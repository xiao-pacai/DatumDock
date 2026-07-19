"""受管图片批量重命名、回收站恢复与永久删除事务。"""

from __future__ import annotations

import json
import os
import shutil
from collections.abc import Iterable
from contextlib import suppress
from dataclasses import asdict, dataclass
from pathlib import Path

from datumdock.domain.models import DatasetSample, ManagedDataset, NamingPolicy, new_id, utc_now
from datumdock.services.library_repository import DatasetPaths
from datumdock.services.sample_repository import (
    DatasetSampleRepository,
    ManagedOperation,
    SampleQuery,
    SampleRepositoryError,
    TrashItem,
)
from datumdock.services.storage import write_json_atomic


class SampleGovernanceError(RuntimeError):
    """文件和索引无法保持一致时，治理操作必须显式失败。"""


@dataclass(frozen=True, slots=True)
class RenamePlanItem:
    """重命名预览的一行，稳定样本 ID 在整个操作中保持不变。"""

    sample_id: str
    old_filename: str
    new_filename: str
    old_image_path: str
    new_image_path: str
    old_annotation_path: str = ""
    new_annotation_path: str = ""
    conflict: str = ""


@dataclass(frozen=True, slots=True)
class RenamePlan:
    """界面确认前不会修改任何文件或配置。"""

    items: tuple[RenamePlanItem, ...]
    naming_policy: NamingPolicy

    @property
    def valid(self) -> bool:
        return bool(self.items) and not any(item.conflict for item in self.items)


@dataclass(frozen=True, slots=True)
class DeletionImpact:
    """删除确认必须明确列出内部受影响对象，外部来源永不在清单中。"""

    sample_ids: tuple[str, ...]
    image_count: int
    annotation_count: int
    thumbnail_count: int
    use_trash: bool


class SampleRenameService:
    """两段临时名称解决交换，并以恢复日志记录跨介质事务。"""

    def __init__(
        self,
        paths: DatasetPaths,
        dataset: ManagedDataset,
        repository: DatasetSampleRepository,
    ) -> None:
        self.paths = paths
        self.dataset = dataset
        self.repository = repository

    def preview(
        self,
        policy: NamingPolicy,
        *,
        sample_ids: Iterable[str] | None = None,
        query: SampleQuery | None = None,
    ) -> RenamePlan:
        """为全部、指定或当前筛选结果建立稳定顺序的冲突预览。"""

        samples = self._selected_samples(sample_ids, query)
        planned_names: set[str] = set()
        items: list[RenamePlanItem] = []
        for offset, sample in enumerate(samples):
            new_filename = policy.filename_for(policy.start_index + offset)
            new_key = new_filename.casefold()
            conflict = ""
            if new_key in planned_names:
                conflict = "命名规则产生重复文件名"
            elif self.repository.sample_filename_exists(
                new_filename, excluding_id=sample.id
            ) and not any(
                other.id != sample.id and other.filename.casefold() == new_key for other in samples
            ):
                conflict = "目标文件名已被未选中样本使用"
            planned_names.add(new_key)
            old_annotation = self._annotation_relative(sample)
            new_annotation = (
                f"pool/annotations/{Path(new_filename).stem}.json" if old_annotation else ""
            )
            items.append(
                RenamePlanItem(
                    sample.id,
                    sample.filename,
                    new_filename,
                    sample.image_path,
                    f"pool/images/{new_filename}",
                    old_annotation,
                    new_annotation,
                    conflict,
                )
            )
        # 交换名称仅允许发生在本次完整选择集合内。
        selected_names = {sample.filename.casefold(): sample.id for sample in samples}
        repaired: list[RenamePlanItem] = []
        for item in items:
            if item.conflict and item.new_filename.casefold() in selected_names:
                repaired.append(
                    RenamePlanItem(
                        item.sample_id,
                        item.old_filename,
                        item.new_filename,
                        item.old_image_path,
                        item.new_image_path,
                        item.old_annotation_path,
                        item.new_annotation_path,
                        "",
                    )
                )
            else:
                repaired.append(item)
        return RenamePlan(tuple(repaired), policy.model_copy(deep=True))

    def apply(self, plan: RenamePlan) -> tuple[str, ...]:
        """先全部移入内部暂存区，再发布新名称和原子更新 SQLite。"""

        if not plan.valid:
            raise SampleGovernanceError("重命名预览包含冲突或没有样本")
        operation_id = new_id()
        staging = self.paths.root / "cache" / "rename-staging" / operation_id
        if staging.exists() or staging.is_symlink():
            raise SampleGovernanceError("重命名暂存目录不安全")
        staging.mkdir(parents=True)
        payload = {
            "items": [asdict(item) for item in plan.items],
            "naming_policy": plan.naming_policy.model_dump(mode="json"),
        }
        timestamp = utc_now().isoformat()
        self.repository.register_operation(
            ManagedOperation(
                operation_id,
                self.dataset.id,
                "rename",
                "prepared",
                payload,
                timestamp,
                timestamp,
            )
        )
        moved_to_staging: list[tuple[Path, Path]] = []
        published: list[tuple[Path, Path]] = []
        try:
            for item in plan.items:
                old_image = self.repository.resolve_path(item.old_image_path, "pool/images")
                staged_image = staging / f"{item.sample_id}.png"
                if not old_image.is_file() or old_image.is_symlink():
                    raise SampleGovernanceError(f"受管图片不存在: {item.old_filename}")
                os.replace(old_image, staged_image)
                moved_to_staging.append((staged_image, old_image))
                if item.old_annotation_path:
                    old_annotation = self.repository.resolve_path(
                        item.old_annotation_path, "pool/annotations"
                    )
                    if old_annotation.is_file() and not old_annotation.is_symlink():
                        staged_annotation = staging / f"{item.sample_id}.json"
                        os.replace(old_annotation, staged_annotation)
                        moved_to_staging.append((staged_annotation, old_annotation))
            self.repository.update_operation(
                operation_id, "staged", payload, updated_at=utc_now().isoformat()
            )
            for item in plan.items:
                staged_image = staging / f"{item.sample_id}.png"
                new_image = self.repository.resolve_path(item.new_image_path, "pool/images")
                os.replace(staged_image, new_image)
                published.append((new_image, staged_image))
                staged_annotation = staging / f"{item.sample_id}.json"
                if item.new_annotation_path and staged_annotation.is_file():
                    self._update_annotation_image_path(staged_annotation, item.new_filename)
                    new_annotation = self.repository.resolve_path(
                        item.new_annotation_path, "pool/annotations"
                    )
                    os.replace(staged_annotation, new_annotation)
                    published.append((new_annotation, staged_annotation))
            self.repository.rename_sample_paths(
                tuple(
                    (
                        item.sample_id,
                        item.new_filename,
                        item.new_image_path,
                        item.new_annotation_path,
                    )
                    for item in plan.items
                )
            )
            self.repository.finish_operation(operation_id)
            shutil.rmtree(staging, ignore_errors=True)
            return tuple(item.sample_id for item in plan.items)
        except Exception as error:
            rollback_errors = self._rollback_rename(plan, staging, published, moved_to_staging)
            if not rollback_errors:
                try:
                    self.repository.finish_operation(operation_id)
                except SampleRepositoryError as rollback_error:
                    rollback_errors.append(str(rollback_error))
            suffix = f"；恢复失败: {'；'.join(rollback_errors)}" if rollback_errors else ""
            raise SampleGovernanceError(f"批量重命名失败: {error}{suffix}") from error

    def _selected_samples(
        self,
        sample_ids: Iterable[str] | None,
        query: SampleQuery | None,
    ) -> tuple[DatasetSample, ...]:
        if sample_ids is not None:
            result: list[DatasetSample] = []
            for sample_id in sample_ids:
                sample = self.repository.get_sample(sample_id)
                if sample is None:
                    raise SampleGovernanceError("重命名选择中包含不存在的样本")
                result.append(sample)
            return tuple(sorted(result, key=lambda item: (item.filename.casefold(), item.id)))
        base = query or SampleQuery(self.dataset.id)
        offset = 0
        result = []
        while True:
            page = self.repository.query(
                SampleQuery(
                    dataset_id=base.dataset_id,
                    offset=offset,
                    limit=500,
                    search=base.search,
                    review_status=base.review_status,
                    label_id=base.label_id,
                    sort=base.sort,
                )
            )
            result.extend(page.items)
            offset += len(page.items)
            if offset >= page.total or not page.items:
                break
        return tuple(result)

    def _annotation_relative(self, sample: DatasetSample) -> str:
        if sample.annotation_path:
            return sample.annotation_path
        candidate = self.paths.annotations / f"{Path(sample.filename).stem}.json"
        return candidate.relative_to(self.paths.root).as_posix() if candidate.is_file() else ""

    @staticmethod
    def _update_annotation_image_path(path: Path, filename: str) -> None:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise SampleGovernanceError(f"关联标注文件无法安全更新: {error}") from error
        if not isinstance(payload, dict):
            raise SampleGovernanceError("关联标注文件根节点不是对象")
        payload["imagePath"] = filename
        write_json_atomic(path, payload)

    def _rollback_rename(
        self,
        plan: RenamePlan,
        staging: Path,
        published: list[tuple[Path, Path]],
        moved_to_staging: list[tuple[Path, Path]],
    ) -> list[str]:
        errors: list[str] = []
        for target, staged in reversed(published):
            if target.exists() and not staged.exists():
                try:
                    os.replace(target, staged)
                except OSError as error:
                    errors.append(str(error))
        for staged, original in reversed(moved_to_staging):
            if staged.exists() and not original.exists():
                try:
                    os.replace(staged, original)
                except OSError as error:
                    errors.append(str(error))
        try:
            self.repository.rename_sample_paths(
                tuple(
                    (
                        item.sample_id,
                        item.old_filename,
                        item.old_image_path,
                        item.old_annotation_path,
                    )
                    for item in plan.items
                )
            )
        except SampleRepositoryError as error:
            errors.append(str(error))
        if not errors:
            shutil.rmtree(staging, ignore_errors=True)
        return errors


class TrashService:
    """删除只触碰受管数据集内部文件，并以稳定样本 UUID 支持恢复。"""

    def __init__(
        self,
        paths: DatasetPaths,
        dataset: ManagedDataset,
        repository: DatasetSampleRepository,
    ) -> None:
        self.paths = paths
        self.dataset = dataset
        self.repository = repository

    def preview(self, sample_ids: Iterable[str], *, threshold: int) -> DeletionImpact:
        samples = self._samples(sample_ids)
        annotations = sum(bool(self._existing_annotation(sample)) for sample in samples)
        thumbnails = sum(bool(self._existing_thumbnail(sample)) for sample in samples)
        return DeletionImpact(
            tuple(sample.id for sample in samples),
            len(samples),
            annotations,
            thumbnails,
            len(samples) <= threshold,
        )

    def delete(self, impact: DeletionImpact, *, permanent: bool = False) -> tuple[str, ...]:
        if not impact.sample_ids:
            return ()
        if permanent or not impact.use_trash:
            for sample_id in impact.sample_ids:
                self._delete_active_permanently(sample_id)
        else:
            for sample_id in impact.sample_ids:
                self._move_one_to_trash(sample_id)
        return impact.sample_ids

    def list_items(self) -> tuple[TrashItem, ...]:
        result: list[TrashItem] = []
        for item in self.repository.list_trash():
            display_filename = item.sample.filename
            try:
                manifest = self.repository.resolve_path(item.manifest_path, "trash")
                payload = json.loads(manifest.read_text(encoding="utf-8"))
                display_filename = str(payload.get("filename", display_filename))
            except (OSError, json.JSONDecodeError, SampleRepositoryError):
                pass
            result.append(
                TrashItem(
                    item.sample,
                    item.trashed_at,
                    item.manifest_path,
                    display_filename,
                )
            )
        return tuple(result)

    def restore(self, sample_id: str) -> DatasetSample:
        sample = self.repository.get_sample(sample_id, include_trashed=True)
        if sample is None or not sample.is_trashed:
            raise SampleGovernanceError("回收站样本不存在")
        item = next(
            (row for row in self.repository.list_trash() if row.sample.id == sample_id),
            None,
        )
        if item is None:
            raise SampleGovernanceError("回收站索引不完整")
        manifest_path = self.repository.resolve_path(item.manifest_path, "trash")
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise SampleGovernanceError(f"回收站恢复清单损坏: {error}") from error
        filename = str(manifest.get("filename", sample.filename))
        if (
            self.repository.sample_filename_exists(filename)
            or (self.paths.images / filename).exists()
        ):
            sequence = self.repository.allocate_sequence(
                1, start_at=self.dataset.configuration.naming_policy.start_index
            )
            filename = self.dataset.configuration.naming_policy.filename_for(sequence)
            while (
                self.repository.sample_filename_exists(filename)
                or (self.paths.images / filename).exists()
            ):
                sequence = self.repository.allocate_sequence(1, start_at=sequence + 1)
                filename = self.dataset.configuration.naming_policy.filename_for(sequence)
        image_target = self.paths.images / filename
        annotation_target = self.paths.annotations / f"{Path(filename).stem}.json"
        thumbnail_target = (
            self.repository.resolve_path(sample.thumbnail_path, "cache/thumbnails")
            if sample.thumbnail_path
            else None
        )
        item_root = manifest_path.parent
        image_source = item_root / str(manifest.get("image_file", "image.png"))
        annotation_source = item_root / "annotation.json"
        thumbnail_source = item_root / "thumbnail.png"
        published: list[tuple[Path, Path]] = []
        operation_id = new_id()
        timestamp = utc_now().isoformat()
        operation_payload = {
            "sample": sample.model_dump(mode="json"),
            "item_root": item_root.relative_to(self.paths.root).as_posix(),
            "image_target": image_target.relative_to(self.paths.root).as_posix(),
            "annotation_target": (
                annotation_target.relative_to(self.paths.root).as_posix()
                if annotation_source.is_file()
                else ""
            ),
            "thumbnail_target": (
                thumbnail_target.relative_to(self.paths.root).as_posix()
                if thumbnail_target is not None and thumbnail_source.is_file()
                else ""
            ),
        }
        self.repository.register_operation(
            ManagedOperation(
                operation_id,
                self.dataset.id,
                "restore_from_trash",
                "prepared",
                operation_payload,
                timestamp,
                timestamp,
            )
        )
        try:
            os.replace(image_source, image_target)
            published.append((image_target, image_source))
            annotation_relative = ""
            if annotation_source.is_file():
                SampleRenameService._update_annotation_image_path(annotation_source, filename)
                os.replace(annotation_source, annotation_target)
                published.append((annotation_target, annotation_source))
                annotation_relative = annotation_target.relative_to(self.paths.root).as_posix()
            if thumbnail_target is not None and thumbnail_source.is_file():
                thumbnail_target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(thumbnail_source, thumbnail_target)
                published.append((thumbnail_target, thumbnail_source))
            self.repository.restore_from_trash(
                sample_id,
                filename=filename,
                image_path=image_target.relative_to(self.paths.root).as_posix(),
                annotation_path=annotation_relative,
            )
            restored = self.repository.get_sample(sample_id)
            if restored is None:
                raise SampleGovernanceError("回收站恢复后索引校验失败")
            self.repository.finish_operation(operation_id)
            shutil.rmtree(item_root, ignore_errors=True)
            return restored
        except Exception as error:
            indexed = self.repository.get_sample(sample_id, include_trashed=True)
            if indexed is None or indexed.is_trashed:
                for target, source in reversed(published):
                    if target.exists() and not source.exists():
                        with suppress(OSError):
                            os.replace(target, source)
                with suppress(SampleRepositoryError):
                    self.repository.finish_operation(operation_id)
            raise SampleGovernanceError(f"回收站恢复失败: {error}") from error

    def permanently_delete(self, sample_id: str) -> None:
        sample = self.repository.get_sample(sample_id, include_trashed=True)
        if sample is None:
            raise SampleGovernanceError("待永久删除样本不存在")
        if sample.is_trashed:
            item = next(
                (row for row in self.repository.list_trash() if row.sample.id == sample_id), None
            )
            if item is None:
                raise SampleGovernanceError("回收站索引不完整")
            manifest_path = self.repository.resolve_path(item.manifest_path, "trash")
            source_root = manifest_path.parent
            operation_id = new_id()
            deleting = self.paths.trash / ".deleting" / operation_id
            deleting.parent.mkdir(parents=True, exist_ok=True)
            timestamp = utc_now().isoformat()
            payload = {
                "mode": "trash",
                "sample": sample.model_dump(mode="json"),
                "source_root": source_root.relative_to(self.paths.root).as_posix(),
                "deleting_path": deleting.relative_to(self.paths.root).as_posix(),
            }
            self.repository.register_operation(
                ManagedOperation(
                    operation_id,
                    self.dataset.id,
                    "delete_permanent",
                    "prepared",
                    payload,
                    timestamp,
                    timestamp,
                )
            )
            try:
                os.replace(source_root, deleting)
                self.repository.delete_sample_record(sample_id)
                self.repository.finish_operation(operation_id)
            except Exception as error:
                indexed = self.repository.get_sample(sample_id, include_trashed=True)
                if indexed is not None:
                    if deleting.exists() and not source_root.exists():
                        with suppress(OSError):
                            os.replace(deleting, source_root)
                    with suppress(SampleRepositoryError):
                        self.repository.finish_operation(operation_id)
                raise SampleGovernanceError(f"永久删除索引失败: {error}") from error
            shutil.rmtree(deleting, ignore_errors=True)
            return
        self._delete_active_permanently(sample_id)

    def empty(self) -> int:
        items = self.repository.list_trash()
        for item in items:
            self.permanently_delete(item.sample.id)
        return len(items)

    def _move_one_to_trash(self, sample_id: str) -> None:
        sample = self.repository.get_sample(sample_id)
        if sample is None:
            raise SampleGovernanceError("待删除样本不存在")
        item_root = self.paths.trash / sample.id
        if item_root.exists() or item_root.is_symlink():
            raise SampleGovernanceError("回收站目标已存在或不安全")
        item_root.mkdir(parents=True)
        image = self.repository.resolve_path(sample.image_path, "pool/images")
        annotation = self._existing_annotation(sample)
        thumbnail = self._existing_thumbnail(sample)
        moved: list[tuple[Path, Path]] = []
        manifest = {
            "format_version": 1,
            "sample_id": sample.id,
            "filename": sample.filename,
            "image_file": "image.png",
            "annotation_file": "annotation.json" if annotation else "",
            "thumbnail_file": "thumbnail.png" if thumbnail else "",
        }
        manifest_path = item_root / "manifest.json"
        operation_id = new_id()
        timestamp = utc_now().isoformat()
        operation_payload = {
            "sample": sample.model_dump(mode="json"),
            "item_root": item_root.relative_to(self.paths.root).as_posix(),
            "image_path": sample.image_path,
            "annotation_path": (
                annotation.relative_to(self.paths.root).as_posix() if annotation else ""
            ),
            "thumbnail_path": (
                thumbnail.relative_to(self.paths.root).as_posix() if thumbnail else ""
            ),
        }
        self.repository.register_operation(
            ManagedOperation(
                operation_id,
                self.dataset.id,
                "move_to_trash",
                "prepared",
                operation_payload,
                timestamp,
                timestamp,
            )
        )
        try:
            image_target = item_root / "image.png"
            os.replace(image, image_target)
            moved.append((image_target, image))
            if annotation is not None:
                target = item_root / "annotation.json"
                os.replace(annotation, target)
                moved.append((target, annotation))
            if thumbnail is not None:
                target = item_root / "thumbnail.png"
                os.replace(thumbnail, target)
                moved.append((target, thumbnail))
            write_json_atomic(manifest_path, manifest)
            relative_manifest = manifest_path.relative_to(self.paths.root).as_posix()
            self.repository.move_to_trash(sample.id, utc_now().isoformat(), relative_manifest)
            self.repository.finish_operation(operation_id)
        except Exception as error:
            indexed = self.repository.get_sample(sample.id, include_trashed=True)
            if indexed is not None and not indexed.is_trashed:
                for target, original in reversed(moved):
                    if target.exists() and not original.exists():
                        with suppress(OSError):
                            os.replace(target, original)
                shutil.rmtree(item_root, ignore_errors=True)
                with suppress(SampleRepositoryError):
                    self.repository.finish_operation(operation_id)
            raise SampleGovernanceError(f"移入回收站失败: {error}") from error

    def _delete_active_permanently(self, sample_id: str) -> None:
        sample = self.repository.get_sample(sample_id)
        if sample is None:
            raise SampleGovernanceError("待永久删除样本不存在")
        operation_id = new_id()
        deleting = self.paths.trash / ".deleting" / operation_id
        deleting.mkdir(parents=True, exist_ok=False)
        sources = (
            (
                "image.png",
                self.repository.resolve_path(sample.image_path, "pool/images"),
            ),
            ("annotation.json", self._existing_annotation(sample)),
            ("thumbnail.png", self._existing_thumbnail(sample)),
        )
        moved: list[tuple[Path, Path]] = []
        timestamp = utc_now().isoformat()
        payload = {
            "mode": "active",
            "sample": sample.model_dump(mode="json"),
            "deleting_path": deleting.relative_to(self.paths.root).as_posix(),
        }
        self.repository.register_operation(
            ManagedOperation(
                operation_id,
                self.dataset.id,
                "delete_permanent",
                "prepared",
                payload,
                timestamp,
                timestamp,
            )
        )
        try:
            for stored_name, source in sources:
                if source is None or not source.exists():
                    continue
                target = deleting / stored_name
                os.replace(source, target)
                moved.append((target, source))
            self.repository.update_operation(
                operation_id, "staged", payload, updated_at=utc_now().isoformat()
            )
            self.repository.delete_sample_record(sample.id)
            self.repository.finish_operation(operation_id)
        except Exception as error:
            indexed = self.repository.get_sample(sample.id, include_trashed=True)
            if indexed is not None:
                for target, source in reversed(moved):
                    if target.exists() and not source.exists():
                        with suppress(OSError):
                            os.replace(target, source)
                with suppress(SampleRepositoryError):
                    self.repository.finish_operation(operation_id)
            raise SampleGovernanceError(f"永久删除失败: {error}") from error
        shutil.rmtree(deleting, ignore_errors=True)

    def _samples(self, sample_ids: Iterable[str]) -> tuple[DatasetSample, ...]:
        result: list[DatasetSample] = []
        seen: set[str] = set()
        for sample_id in sample_ids:
            if sample_id in seen:
                continue
            seen.add(sample_id)
            sample = self.repository.get_sample(sample_id)
            if sample is None:
                raise SampleGovernanceError("删除选择中包含不存在的样本")
            result.append(sample)
        return tuple(result)

    def _existing_annotation(self, sample: DatasetSample) -> Path | None:
        if sample.annotation_path:
            path = self.repository.resolve_path(sample.annotation_path, "pool/annotations")
        else:
            path = self.paths.annotations / f"{Path(sample.filename).stem}.json"
        return path if path.is_file() and not path.is_symlink() else None

    def _existing_thumbnail(self, sample: DatasetSample) -> Path | None:
        if not sample.thumbnail_path:
            return None
        path = self.repository.resolve_path(sample.thumbnail_path, "cache/thumbnails")
        return path if path.is_file() and not path.is_symlink() else None
