"""ManagedDataset 图片池的 PNG 导入、缩略图和重复决策服务。"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import Path
from uuid import UUID, uuid4

from PIL import Image, ImageOps, UnidentifiedImageError

from datumdock.domain.models import (
    DatasetSample,
    ManagedDataset,
    SampleHealth,
    ThumbnailState,
    new_id,
    utc_now,
)
from datumdock.services.library_repository import DatasetPaths
from datumdock.services.sample_repository import (
    DatasetSampleRepository,
    ManagedOperation,
    SampleReconciliationReport,
    SampleRepositoryError,
)
from datumdock.services.storage import write_json_atomic

SUPPORTED_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff"}


class ImagePoolError(RuntimeError):
    """图片池操作无法安全完成。"""


class DuplicateDecision(StrEnum):
    """完全重复图片必须由用户明确选择。"""

    SKIP = "skip"
    KEEP = "keep"


@dataclass(frozen=True, slots=True)
class ImageImportPreflightRequest:
    """来源路径只在本次会话内使用，不持久化进数据集。"""

    dataset_id: str
    source_paths: tuple[Path, ...]
    recursive: bool = True


@dataclass(frozen=True, slots=True)
class PreparedImportItem:
    """预检后的内部临时 PNG，尚未成为数据集样本。"""

    id: str
    source_path: Path
    original_filename: str
    staged_image_path: Path
    staged_thumbnail_path: Path
    width: int
    height: int
    image_mode: str
    content_hash: str
    file_hash: str
    perceptual_hash: str
    existing_duplicate_ids: tuple[str, ...] = ()
    batch_duplicate_ids: tuple[str, ...] = ()

    @property
    def requires_duplicate_decision(self) -> bool:
        """已有样本或同批先出现的图片都会触发人工决定。"""

        return bool(self.existing_duplicate_ids or self.batch_duplicate_ids)


@dataclass(frozen=True, slots=True)
class ImageImportPreflight:
    """预检结果保留成功准备项和逐文件错误。"""

    session_id: str
    items: tuple[PreparedImportItem, ...]
    failures: dict[str, str]
    discovered_count: int


@dataclass(slots=True)
class ImageImportReport:
    """导入结果区分真实成功、用户跳过、失败和近似候选。"""

    imported_sample_ids: list[str] = field(default_factory=list)
    skipped_item_ids: list[str] = field(default_factory=list)
    duplicate_item_ids: list[str] = field(default_factory=list)
    similar_sample_ids: dict[str, list[str]] = field(default_factory=dict)
    failures: dict[str, str] = field(default_factory=dict)
    cancelled: bool = False


@dataclass(frozen=True, slots=True)
class ImageAsset:
    """UI 只接收已验证图片字节，不接触受管路径。"""

    sample_id: str
    data: bytes
    width: int
    height: int
    format_name: str = "PNG"


@dataclass(frozen=True, slots=True)
class ThumbnailAsset:
    """缩略图可随时重建，不承担图片事实职责。"""

    sample_id: str
    data: bytes
    cache_hit: bool


class ImageImportService:
    """以两阶段流程把外部静态图片导入当前受管数据集。"""

    def __init__(
        self,
        paths: DatasetPaths,
        dataset: ManagedDataset,
        repository: DatasetSampleRepository,
    ) -> None:
        self.paths = paths
        self.dataset = dataset
        self.repository = repository
        self.staging_root = paths.thumbnails.parent / "import-staging"

    def preflight(
        self,
        request: ImageImportPreflightRequest,
        *,
        progress: Callable[[int, int, str], None] | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> ImageImportPreflight:
        """扫描并规范化到内部临时区；此阶段不登记样本。"""

        if request.dataset_id != self.dataset.id:
            raise ImagePoolError("导入请求不属于当前数据集")
        sources, discovery_failures = self._discover_sources(
            request.source_paths, request.recursive
        )
        session_id = str(uuid4())
        session_root = self._session_root(session_id)
        session_root.mkdir(parents=True, exist_ok=False)
        prepared: list[PreparedImportItem] = []
        failures: dict[str, str] = dict(discovery_failures)
        first_by_hash: dict[str, str] = {}
        total_count = len(sources) + len(discovery_failures)
        try:
            for index, source in enumerate(sources, start=1):
                if cancelled and cancelled():
                    break
                if progress:
                    progress(index - 1 + len(discovery_failures), total_count, source.name)
                try:
                    if source.suffix.lower() not in SUPPORTED_IMAGE_SUFFIXES:
                        raise ImagePoolError("不支持的图片格式")
                    item = self._prepare_one(source, session_root)
                    existing = self.repository.find_by_content_hash(item.content_hash)
                    batch_duplicate = first_by_hash.get(item.content_hash)
                    item = replace(
                        item,
                        existing_duplicate_ids=tuple(sample.id for sample in existing),
                        batch_duplicate_ids=(batch_duplicate,) if batch_duplicate else (),
                    )
                    first_by_hash.setdefault(item.content_hash, item.id)
                    prepared.append(item)
                except (OSError, ValueError, ImagePoolError, UnidentifiedImageError) as error:
                    failures[str(source)] = str(error)
            if progress:
                progress(len(prepared) + len(failures), total_count, "")
            return ImageImportPreflight(
                session_id,
                tuple(prepared),
                failures,
                total_count,
            )
        except Exception:
            self.discard_preflight(session_id)
            raise

    def commit(
        self,
        preflight: ImageImportPreflight,
        decisions: dict[str, DuplicateDecision],
        *,
        progress: Callable[[int, int, str], None] | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> ImageImportReport:
        """逐项发布准备结果；每张图结束后都是一致的取消边界。"""

        session_root = self._session_root(preflight.session_id)
        if not session_root.is_dir() or session_root.is_symlink():
            raise ImagePoolError("导入预检会话不存在或不安全")
        missing_decisions = [
            item.id
            for item in preflight.items
            if item.requires_duplicate_decision and item.id not in decisions
        ]
        if missing_decisions:
            raise ImagePoolError("存在尚未作出决定的完全重复图片")

        report = ImageImportReport(failures=dict(preflight.failures))
        keep_items = [
            item
            for item in preflight.items
            if not item.requires_duplicate_decision
            or decisions.get(item.id) == DuplicateDecision.KEEP
        ]
        next_sequence = None
        if keep_items:
            next_sequence = self.repository.allocate_sequence(
                len(keep_items),
                start_at=self.dataset.configuration.naming_policy.start_index,
            )
        keep_position = 0
        for index, item in enumerate(preflight.items, start=1):
            if cancelled and cancelled():
                report.cancelled = True
                break
            if progress:
                progress(index - 1, len(preflight.items), item.original_filename)
            if item.requires_duplicate_decision:
                report.duplicate_item_ids.append(item.id)
                if decisions[item.id] == DuplicateDecision.SKIP:
                    self._discard_item(item)
                    report.skipped_item_ids.append(item.id)
                    continue
            if next_sequence is None:
                raise ImagePoolError("命名序号未初始化")
            try:
                sample = self._commit_one(item, next_sequence + keep_position)
                keep_position += 1
                report.imported_sample_ids.append(sample.id)
                candidates = tuple(
                    candidate
                    for candidate in self.repository.perceptual_candidates(
                        sample.perceptual_hash, exclude_id=sample.id
                    )
                    if candidate[0].content_hash != sample.content_hash
                )
                if candidates:
                    self.repository.register_similarity_candidates(
                        sample.id,
                        candidates,
                        timestamp=utc_now().isoformat(),
                    )
                    report.similar_sample_ids[sample.id] = [
                        candidate.id for candidate, _distance in candidates
                    ]
            except (OSError, ValueError, SampleRepositoryError, ImagePoolError) as error:
                report.failures[str(item.source_path)] = str(error)
        if progress:
            completed = len(report.imported_sample_ids) + len(report.skipped_item_ids)
            progress(completed, len(preflight.items), "")
        # 最终报告已经保存逐文件错误，失败或取消项不应把规范化临时 PNG
        # 长期留在数据集。已发布文件已被移动出会话目录，不受此清理影响。
        if session_root.exists():
            self.discard_preflight(preflight.session_id)
        return report

    def discard_preflight(self, session_id: str) -> None:
        """只清理当前数据集内部的规范 UUID 会话目录。"""

        session_root = self._session_root(session_id)
        if not session_root.exists():
            return
        if session_root.is_symlink():
            raise ImagePoolError("拒绝清理符号链接导入会话")
        shutil.rmtree(session_root)

    def load_prepared_image(self, item: PreparedImportItem) -> bytes:
        """重复对比通过服务读取内部临时 PNG，页面不直接访问路径。"""

        self._validate_staged_path(item.staged_image_path)
        return item.staged_image_path.read_bytes()

    def _discover_sources(
        self,
        roots: Iterable[Path],
        recursive: bool,
    ) -> tuple[tuple[Path, ...], dict[str, str]]:
        discovered: list[Path] = []
        failures: dict[str, str] = {}
        seen: set[str] = set()
        for supplied in roots:
            path = Path(supplied)
            if path.is_symlink():
                failures[str(path)] = "拒绝导入符号链接"
                continue
            if path.is_file():
                candidates = (path,)
            elif path.is_dir():
                candidates = self._walk_directory(path, recursive)
            else:
                failures[str(path)] = "来源文件或目录不存在"
                continue
            for candidate in candidates:
                if candidate.is_symlink():
                    continue
                if path.is_dir() and candidate.suffix.lower() not in SUPPORTED_IMAGE_SUFFIXES:
                    continue
                key = str(candidate.resolve(strict=False)).casefold()
                if key not in seen:
                    seen.add(key)
                    discovered.append(candidate)
        return tuple(sorted(discovered, key=lambda item: str(item).casefold())), failures

    @staticmethod
    def _walk_directory(root: Path, recursive: bool) -> tuple[Path, ...]:
        if not recursive:
            return tuple(path for path in root.iterdir() if path.is_file())
        result: list[Path] = []
        for current_root, directories, files in os.walk(root, followlinks=False):
            current = Path(current_root)
            directories[:] = [name for name in directories if not (current / name).is_symlink()]
            result.extend(current / name for name in files if not (current / name).is_symlink())
        return tuple(result)

    def _prepare_one(self, source: Path, session_root: Path) -> PreparedImportItem:
        identifier = new_id()
        staged_image = session_root / f"{identifier}.png"
        staged_thumbnail = session_root / f"{identifier}.thumb.png"
        with Image.open(source) as opened:
            frame_count = int(getattr(opened, "n_frames", 1))
            if frame_count != 1:
                raise ImagePoolError("仅支持静态单帧图片")
            opened.load()
            normalized = _normalize_image(ImageOps.exif_transpose(opened))
            width, height = normalized.size
            content_hash = _pixel_content_hash(normalized)
            _save_png_atomic(normalized, staged_image)
            with Image.open(staged_image) as verification:
                verification.verify()
            file_hash = _file_sha256(staged_image)
            perceptual_hash = _perceptual_hash(normalized)
            thumbnail = normalized.copy()
            thumbnail.thumbnail((320, 320), Image.Resampling.LANCZOS)
            _save_png_atomic(thumbnail, staged_thumbnail)
        return PreparedImportItem(
            identifier,
            source,
            source.name,
            staged_image,
            staged_thumbnail,
            width,
            height,
            normalized.mode,
            content_hash,
            file_hash,
            perceptual_hash,
        )

    def _commit_one(self, item: PreparedImportItem, sequence: int) -> DatasetSample:
        self._validate_staged_path(item.staged_image_path)
        self._validate_staged_path(item.staged_thumbnail_path)
        filename = self.dataset.configuration.naming_policy.filename_for(sequence)
        target_image = self.paths.images / filename
        while target_image.exists():
            sequence = self.repository.allocate_sequence(1, start_at=sequence + 1)
            filename = self.dataset.configuration.naming_policy.filename_for(sequence)
            target_image = self.paths.images / filename
        thumbnail_name = f"{item.id}-{item.content_hash[:12]}.png"
        target_thumbnail = self.paths.thumbnails / thumbnail_name
        image_relative = target_image.relative_to(self.paths.root).as_posix()
        thumbnail_relative = target_thumbnail.relative_to(self.paths.root).as_posix()
        sample = DatasetSample(
            id=item.id,
            dataset_id=self.dataset.id,
            filename=filename,
            original_filename=item.original_filename,
            image_path=image_relative,
            width=item.width,
            height=item.height,
            image_mode=item.image_mode,
            content_hash=item.content_hash,
            file_hash=item.file_hash,
            perceptual_hash=item.perceptual_hash,
            health=SampleHealth.READY,
            thumbnail_state=ThumbnailState.READY,
            thumbnail_path=thumbnail_relative,
            duplicate_group_id=(item.content_hash if item.requires_duplicate_decision else None),
            imported_at=utc_now().isoformat(),
        )
        operation_id = new_id()
        timestamp = utc_now().isoformat()
        payload = {
            "sample": sample.model_dump(mode="json"),
            "staged_image": item.staged_image_path.relative_to(self.paths.root).as_posix(),
            "staged_thumbnail": item.staged_thumbnail_path.relative_to(self.paths.root).as_posix(),
            "target_image": image_relative,
            "target_thumbnail": thumbnail_relative,
        }
        self.repository.register_operation(
            ManagedOperation(
                operation_id,
                self.dataset.id,
                "import",
                "prepared",
                payload,
                timestamp,
                timestamp,
            )
        )
        image_published = False
        thumbnail_published = False
        try:
            os.replace(item.staged_image_path, target_image)
            image_published = True
            os.replace(item.staged_thumbnail_path, target_thumbnail)
            thumbnail_published = True
            self.repository.update_operation(operation_id, "published", payload)
            self.repository.add_sample(sample)
            self.repository.update_operation(operation_id, "indexed", payload)
            self.repository.finish_operation(operation_id)
            return sample
        except Exception as error:
            rollback_errors: list[str] = []
            for published, target, staged in (
                (thumbnail_published, target_thumbnail, item.staged_thumbnail_path),
                (image_published, target_image, item.staged_image_path),
            ):
                if published and target.exists() and not staged.exists():
                    try:
                        os.replace(target, staged)
                    except OSError as rollback_error:
                        rollback_errors.append(str(rollback_error))
            if not rollback_errors:
                try:
                    self.repository.finish_operation(operation_id)
                except SampleRepositoryError as cleanup_error:
                    rollback_errors.append(str(cleanup_error))
            suffix = f"；恢复失败: {'；'.join(rollback_errors)}" if rollback_errors else ""
            raise ImagePoolError(f"图片发布失败: {error}{suffix}") from error

    def _session_root(self, session_id: str) -> Path:
        try:
            canonical = str(UUID(session_id))
        except ValueError as error:
            raise ImagePoolError("导入会话 ID 无效") from error
        if canonical != session_id:
            raise ImagePoolError("导入会话 ID 必须使用规范 UUID")
        root = self.staging_root / session_id
        try:
            root.resolve(strict=False).relative_to(self.paths.root.resolve())
        except ValueError as error:
            raise ImagePoolError("导入临时目录越过数据集边界") from error
        return root

    def _validate_staged_path(self, path: Path) -> None:
        try:
            path.resolve(strict=True).relative_to(self.staging_root.resolve())
        except (OSError, ValueError) as error:
            raise ImagePoolError("导入临时文件不在受管缓存区") from error
        if path.is_symlink():
            raise ImagePoolError("导入临时文件不能是符号链接")

    @staticmethod
    def _discard_item(item: PreparedImportItem) -> None:
        item.staged_image_path.unlink(missing_ok=True)
        item.staged_thumbnail_path.unlink(missing_ok=True)

    @staticmethod
    def _cleanup_session_if_empty(session_root: Path) -> None:
        if session_root.exists() and not any(session_root.iterdir()):
            session_root.rmdir()


class ThumbnailService:
    """按需读取或重建稳定 UUID 缩略图缓存。"""

    def __init__(self, paths: DatasetPaths, repository: DatasetSampleRepository) -> None:
        self.paths = paths
        self.repository = repository

    def load(self, sample_id: str) -> ThumbnailAsset:
        sample = self.repository.get_sample(sample_id)
        if sample is None:
            raise ImagePoolError("缩略图样本不存在")
        expected_name = f"{sample.id}-{sample.content_hash[:12]}.png"
        expected_path = self.paths.thumbnails / expected_name
        cache_hit = (
            sample.thumbnail_state == ThumbnailState.READY
            and sample.thumbnail_path
            and expected_path.is_file()
            and not expected_path.is_symlink()
        )
        if not cache_hit:
            image_path = self.repository.resolve_path(sample.image_path, "pool/images")
            try:
                with Image.open(image_path) as image:
                    image.load()
                    thumbnail = image.copy()
                    thumbnail.thumbnail((320, 320), Image.Resampling.LANCZOS)
                    _save_png_atomic(thumbnail, expected_path)
                relative = expected_path.relative_to(self.paths.root).as_posix()
                self.repository.update_thumbnail(sample.id, ThumbnailState.READY, relative)
            except (OSError, UnidentifiedImageError) as error:
                self.repository.update_thumbnail(sample.id, ThumbnailState.ERROR)
                raise ImagePoolError(f"缩略图生成失败: {error}") from error
        return ThumbnailAsset(sample.id, expected_path.read_bytes(), cache_hit)

    def invalidate(self, sample_id: str) -> None:
        sample = self.repository.get_sample(sample_id)
        if sample is None:
            return
        if sample.thumbnail_path:
            path = self.repository.resolve_path(sample.thumbnail_path, "cache/thumbnails")
            path.unlink(missing_ok=True)
        self.repository.update_thumbnail(sample.id, ThumbnailState.STALE)


class ManagedImageService:
    """为 UI 加载真实受管 PNG，任何路径异常都停留在服务边界。"""

    def __init__(self, paths: DatasetPaths, repository: DatasetSampleRepository) -> None:
        self.paths = paths
        self.repository = repository

    def load(self, sample_id: str) -> ImageAsset:
        sample = self.repository.get_sample(sample_id)
        if sample is None:
            raise ImagePoolError("图片样本不存在")
        path = self.repository.resolve_path(sample.image_path, "pool/images")
        try:
            data = path.read_bytes()
            with Image.open(path) as image:
                image.verify()
        except (OSError, UnidentifiedImageError) as error:
            raise ImagePoolError(f"受管图片无法读取: {error}") from error
        return ImageAsset(sample.id, data, sample.width, sample.height)


class ImagePoolMaintenanceService:
    """在应用启动时恢复已登记操作并核对图片索引与受管目录。"""

    def __init__(self, paths: DatasetPaths, repository: DatasetSampleRepository) -> None:
        self.paths = paths
        self.repository = repository

    def reconcile(self) -> SampleReconciliationReport:
        """已知操作按日志恢复；未知 PNG 和临时文件只报告、不删除。"""

        self._recover_import_operations()
        self._recover_rename_operations()
        self._recover_trash_operations()
        self._recover_restore_operations()
        self._recover_deletion_operations()
        missing: list[str] = []
        corrupt: list[str] = []
        tracked_paths: set[str] = set()
        for sample in self.repository.all_active_samples():
            tracked_paths.add(sample.image_path)
            try:
                image_path = self.repository.resolve_path(sample.image_path, "pool/images")
                if not image_path.is_file() or image_path.is_symlink():
                    missing.append(sample.id)
                    self.repository.update_health(sample.id, SampleHealth.MISSING)
                    continue
                with Image.open(image_path) as image:
                    image.verify()
                if sample.health != SampleHealth.READY:
                    self.repository.update_health(sample.id, SampleHealth.READY)
            except (OSError, UnidentifiedImageError):
                corrupt.append(sample.id)
                self.repository.update_health(sample.id, SampleHealth.CORRUPT)

        untracked: list[str] = []
        if self.paths.images.is_dir() and not self.paths.images.is_symlink():
            for path in self.paths.images.glob("*.png"):
                if path.is_symlink():
                    continue
                relative = path.relative_to(self.paths.root).as_posix()
                if relative not in tracked_paths:
                    untracked.append(relative)
        pending = tuple(operation.id for operation in self.repository.list_operations())
        temporary = tuple(
            path.relative_to(self.paths.root).as_posix()
            for path in self.paths.root.rglob("*.tmp")
            if not path.is_symlink()
        )
        return SampleReconciliationReport(
            missing_sample_ids=tuple(missing),
            corrupt_sample_ids=tuple(corrupt),
            untracked_pngs=tuple(sorted(untracked)),
            pending_operation_ids=pending,
            temporary_files=tuple(sorted(temporary)),
            migration_issues=self.repository.migration_issues(),
        )

    def _recover_import_operations(self) -> None:
        """导入崩溃恢复以日志阶段和实际文件为共同证据。"""

        for operation in self.repository.list_operations():
            if operation.operation_type != "import":
                continue
            payload = operation.payload
            try:
                sample = DatasetSample.model_validate(payload["sample"])
                staged_image = self.repository.resolve_path(
                    str(payload["staged_image"]), "cache/import-staging"
                )
                staged_thumbnail = self.repository.resolve_path(
                    str(payload["staged_thumbnail"]), "cache/import-staging"
                )
                target_image = self.repository.resolve_path(
                    str(payload["target_image"]), "pool/images"
                )
                target_thumbnail = self.repository.resolve_path(
                    str(payload["target_thumbnail"]), "cache/thumbnails"
                )
            except (KeyError, ValueError, SampleRepositoryError):
                # 日志负载本身损坏时保留原记录，供诊断页面显示。
                continue
            if self.repository.get_sample(sample.id, include_trashed=True) is not None:
                self.repository.finish_operation(operation.id)
                continue
            if target_image.is_file() and target_thumbnail.is_file():
                try:
                    self.repository.add_sample(sample)
                    self.repository.finish_operation(operation.id)
                except SampleRepositoryError:
                    continue
                continue
            published_pairs = (
                (target_thumbnail, staged_thumbnail),
                (target_image, staged_image),
            )
            rollback_failed = False
            for target, staged in published_pairs:
                if target.exists() and not staged.exists():
                    try:
                        staged.parent.mkdir(parents=True, exist_ok=True)
                        os.replace(target, staged)
                    except OSError:
                        rollback_failed = True
            if rollback_failed:
                continue
            staged_image.unlink(missing_ok=True)
            staged_thumbnail.unlink(missing_ok=True)
            self.repository.finish_operation(operation.id)

    def _recover_rename_operations(self) -> None:
        """重命名中断后，已完整提交则收尾，否则恢复全部旧名称。"""

        for operation in self.repository.list_operations():
            if operation.operation_type != "rename":
                continue
            raw_items = operation.payload.get("items")
            if not isinstance(raw_items, list):
                continue
            try:
                items = [dict(item) for item in raw_items]
                committed = all(
                    (
                        (sample := self.repository.get_sample(str(item["sample_id"]))) is not None
                        and sample.filename == str(item["new_filename"])
                        and self.repository.resolve_path(
                            str(item["new_image_path"]), "pool/images"
                        ).is_file()
                    )
                    for item in items
                )
                staging = self.paths.root / "cache" / "rename-staging" / operation.id
                if committed:
                    shutil.rmtree(staging, ignore_errors=True)
                    self.repository.finish_operation(operation.id)
                    continue
                for item in reversed(items):
                    sample_id = str(item["sample_id"])
                    old_image = self.repository.resolve_path(
                        str(item["old_image_path"]), "pool/images"
                    )
                    new_image = self.repository.resolve_path(
                        str(item["new_image_path"]), "pool/images"
                    )
                    staged_image = staging / f"{sample_id}.png"
                    if new_image.exists() and new_image != old_image and not staged_image.exists():
                        os.replace(new_image, staged_image)
                    if staged_image.exists() and not old_image.exists():
                        os.replace(staged_image, old_image)
                    old_annotation_value = str(item.get("old_annotation_path", ""))
                    new_annotation_value = str(item.get("new_annotation_path", ""))
                    staged_annotation = staging / f"{sample_id}.json"
                    if new_annotation_value:
                        new_annotation = self.repository.resolve_path(
                            new_annotation_value, "pool/annotations"
                        )
                        if new_annotation.exists() and not staged_annotation.exists():
                            os.replace(new_annotation, staged_annotation)
                    if old_annotation_value and staged_annotation.exists():
                        old_annotation = self.repository.resolve_path(
                            old_annotation_value, "pool/annotations"
                        )
                        self._rewrite_annotation_image_path(
                            staged_annotation, str(item["old_filename"])
                        )
                        if not old_annotation.exists():
                            os.replace(staged_annotation, old_annotation)
                self.repository.rename_sample_paths(
                    tuple(
                        (
                            str(item["sample_id"]),
                            str(item["old_filename"]),
                            str(item["old_image_path"]),
                            str(item.get("old_annotation_path", "")),
                        )
                        for item in items
                    )
                )
                shutil.rmtree(staging, ignore_errors=True)
                self.repository.finish_operation(operation.id)
            except (KeyError, OSError, SampleRepositoryError, ValueError):
                # 无法证明恢复完成时保留操作日志和所有现存文件。
                continue

    def _recover_deletion_operations(self) -> None:
        """永久删除中断时按索引是否仍存在决定恢复或完成清理。"""

        for operation in self.repository.list_operations():
            if operation.operation_type != "delete_permanent":
                continue
            payload = operation.payload
            try:
                sample = DatasetSample.model_validate(payload["sample"])
                deleting = self.repository.resolve_path(
                    str(payload["deleting_path"]), "trash/.deleting"
                )
                indexed = self.repository.get_sample(sample.id, include_trashed=True)
                if indexed is None:
                    shutil.rmtree(deleting, ignore_errors=True)
                    self.repository.finish_operation(operation.id)
                    continue
                if str(payload.get("mode", "active")) == "trash":
                    source_root = self.repository.resolve_path(str(payload["source_root"]), "trash")
                    if deleting.exists() and source_root.exists():
                        # 两份目录同时存在时无法证明哪份完整，保留日志供诊断。
                        continue
                    if deleting.exists() and not source_root.exists():
                        source_root.parent.mkdir(parents=True, exist_ok=True)
                        os.replace(deleting, source_root)
                    if not source_root.is_dir():
                        continue
                    self.repository.finish_operation(operation.id)
                    continue
                targets = {
                    "image.png": self.repository.resolve_path(sample.image_path, "pool/images"),
                }
                if sample.annotation_path:
                    targets["annotation.json"] = self.repository.resolve_path(
                        sample.annotation_path, "pool/annotations"
                    )
                if sample.thumbnail_path:
                    targets["thumbnail.png"] = self.repository.resolve_path(
                        sample.thumbnail_path, "cache/thumbnails"
                    )
                for stored_name, target in targets.items():
                    stored = deleting / stored_name
                    if stored.is_file() and not target.exists():
                        target.parent.mkdir(parents=True, exist_ok=True)
                        os.replace(stored, target)
                if not targets["image.png"].is_file():
                    continue
                shutil.rmtree(deleting, ignore_errors=True)
                self.repository.finish_operation(operation.id)
            except (KeyError, OSError, SampleRepositoryError, ValueError):
                continue

    def _recover_trash_operations(self) -> None:
        """移入回收站中断时，以索引状态决定完成登记或恢复活动文件。"""

        for operation in self.repository.list_operations():
            if operation.operation_type != "move_to_trash":
                continue
            payload = operation.payload
            try:
                sample = DatasetSample.model_validate(payload["sample"])
                indexed = self.repository.get_sample(sample.id, include_trashed=True)
                if indexed is None:
                    continue
                if indexed.is_trashed:
                    item_root = self.repository.resolve_path(str(payload["item_root"]), "trash")
                    if not (item_root / "image.png").is_file():
                        continue
                    self.repository.finish_operation(operation.id)
                    continue
                item_root = self.repository.resolve_path(str(payload["item_root"]), "trash")
                targets = {
                    "image.png": self.repository.resolve_path(
                        str(payload["image_path"]), "pool/images"
                    )
                }
                annotation_value = str(payload.get("annotation_path", ""))
                thumbnail_value = str(payload.get("thumbnail_path", ""))
                if annotation_value:
                    targets["annotation.json"] = self.repository.resolve_path(
                        annotation_value, "pool/annotations"
                    )
                if thumbnail_value:
                    targets["thumbnail.png"] = self.repository.resolve_path(
                        thumbnail_value, "cache/thumbnails"
                    )
                for stored_name, target in targets.items():
                    stored = item_root / stored_name
                    if stored.is_file() and not target.exists():
                        target.parent.mkdir(parents=True, exist_ok=True)
                        os.replace(stored, target)
                if not targets["image.png"].is_file():
                    continue
                shutil.rmtree(item_root, ignore_errors=True)
                self.repository.finish_operation(operation.id)
            except (KeyError, OSError, SampleRepositoryError, ValueError):
                continue

    def _recover_restore_operations(self) -> None:
        """回收站恢复中断时，活动索引完成收尾，回收站索引恢复原目录。"""

        for operation in self.repository.list_operations():
            if operation.operation_type != "restore_from_trash":
                continue
            payload = operation.payload
            try:
                sample = DatasetSample.model_validate(payload["sample"])
                indexed = self.repository.get_sample(sample.id, include_trashed=True)
                if indexed is None:
                    continue
                item_root = self.repository.resolve_path(str(payload["item_root"]), "trash")
                if not indexed.is_trashed:
                    image_target = self.repository.resolve_path(
                        str(payload["image_target"]), "pool/images"
                    )
                    if not image_target.is_file():
                        continue
                    shutil.rmtree(item_root, ignore_errors=True)
                    self.repository.finish_operation(operation.id)
                    continue
                item_root.mkdir(parents=True, exist_ok=True)
                targets = {
                    "image.png": self.repository.resolve_path(
                        str(payload["image_target"]), "pool/images"
                    )
                }
                annotation_value = str(payload.get("annotation_target", ""))
                thumbnail_value = str(payload.get("thumbnail_target", ""))
                if annotation_value:
                    targets["annotation.json"] = self.repository.resolve_path(
                        annotation_value, "pool/annotations"
                    )
                if thumbnail_value:
                    targets["thumbnail.png"] = self.repository.resolve_path(
                        thumbnail_value, "cache/thumbnails"
                    )
                for stored_name, target in targets.items():
                    stored = item_root / stored_name
                    if target.is_file() and not stored.exists():
                        os.replace(target, stored)
                if not (item_root / "image.png").is_file():
                    continue
                self.repository.finish_operation(operation.id)
            except (KeyError, OSError, SampleRepositoryError, ValueError):
                continue

    @staticmethod
    def _rewrite_annotation_image_path(path: Path, filename: str) -> None:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("标注根节点不是对象")
        payload["imagePath"] = filename
        write_json_atomic(path, payload)


def _normalize_image(image: Image.Image) -> Image.Image:
    """只保留 PNG 能稳定表达且 UI 可预测的颜色模式。"""

    if image.mode in {"L", "LA", "RGB", "RGBA"}:
        return image.copy()
    if image.mode == "P" and "transparency" in image.info:
        return image.convert("RGBA")
    if image.mode == "P":
        return image.convert("RGB")
    return image.convert("RGB")


def _save_png_atomic(image: Image.Image, target: Path) -> None:
    """PNG 先写同目录临时文件、刷新后再原子发布。"""

    target.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(
        # Windows 未启用长路径策略时，测试目录叠加 UUID 后很容易接近
        # MAX_PATH。临时名无需重复目标文件名，使用短前缀仍可保证唯一。
        prefix=".p-",
        suffix=".tmp",
        dir=target.parent,
    )
    os.close(handle)
    temporary = Path(temporary_name)
    try:
        image.save(temporary, "PNG")
        with temporary.open("rb+") as stream:
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _pixel_content_hash(image: Image.Image) -> str:
    """模式、尺寸和像素共同参与哈希，编码元数据不会制造假差异。"""

    digest = hashlib.sha256()
    digest.update(image.mode.encode("ascii"))
    digest.update(f"{image.width}x{image.height}".encode("ascii"))
    digest.update(image.tobytes())
    return digest.hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _perceptual_hash(image: Image.Image) -> str:
    """64 位 dHash 加平均 RGB；不使用 Pillow 即将弃用的 getdata。"""

    grayscale = image.convert("L").resize((9, 8), Image.Resampling.LANCZOS)
    pixels = list(grayscale.get_flattened_data())
    bits = [
        "1" if pixels[row * 9 + column] > pixels[row * 9 + column + 1] else "0"
        for row in range(8)
        for column in range(8)
    ]
    red, green, blue = image.convert("RGB").resize((1, 1)).getpixel((0, 0))
    return f"{int(''.join(bits), 2):016x}{red:02x}{green:02x}{blue:02x}"
