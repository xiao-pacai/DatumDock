"""整数据集永久删除的影响预检、恢复清单与同卷暂存事务。"""

from __future__ import annotations

import hashlib
import json
import os
import stat
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any
from uuid import UUID

from datumdock.domain.models import LabelSet, new_id, utc_now
from datumdock.services.sample_repository import DatasetSampleRepository, SampleRepositoryError
from datumdock.services.storage import read_json_model, write_json_atomic

if TYPE_CHECKING:
    from datumdock.services.dataset_library import DatasetLibraryService


class DatasetDeletionError(RuntimeError):
    """整数据集删除无法在已验证的受管边界内安全完成。"""


class DatasetDeletionStatus(StrEnum):
    """清理未结束时不能把资料库登记移除误报为完整成功。"""

    COMPLETED = "completed"
    PENDING_CLEANUP = "pending_cleanup"


@dataclass(frozen=True, slots=True)
class DatasetDeletionImpact:
    """危险确认页展示的真实文件和领域对象统计。"""

    image_count: int
    image_bytes: int
    annotation_file_count: int
    rectangle_count: int
    label_count: int
    model_file_count: int
    index_file_count: int
    thumbnail_file_count: int
    cache_file_count: int
    trash_file_count: int
    recovery_file_count: int
    total_file_count: int
    total_bytes: int


@dataclass(frozen=True, slots=True)
class DatasetDeletionPreflight:
    """删除确认绑定到数据集、资料库和目录清单的不可变快照。"""

    id: str
    dataset_id: str
    dataset_name: str
    library_sha256: str
    tree_sha256: str
    impact: DatasetDeletionImpact
    managed_entries: tuple[str, ...]
    recovery_entries: tuple[str, ...]
    blockers: tuple[str, ...]

    @property
    def can_delete(self) -> bool:
        return not self.blockers


@dataclass(frozen=True, slots=True)
class DatasetDeletionRequest:
    """提交必须携带原预检、精确名称和最终确认标记。"""

    preflight: DatasetDeletionPreflight
    typed_name: str
    final_confirmed: bool


@dataclass(frozen=True, slots=True)
class DatasetDeletionReport:
    """完整清理与待恢复现场使用不同状态。"""

    dataset_id: str
    status: DatasetDeletionStatus
    operation_id: str
    diagnostic: str = ""


@dataclass(frozen=True, slots=True)
class DatasetDeletionRecoveryManifest:
    """启动恢复只依据清单和资料库登记，不猜测未知目录。"""

    format_version: int
    operation_id: str
    dataset_id: str
    dataset_name: str
    library_sha256: str
    tree_sha256: str
    managed_entries: tuple[str, ...]
    recovery_entries: tuple[str, ...]
    phase: str
    created_at: str


@dataclass(frozen=True, slots=True)
class DatasetDeletionRecoveryReport:
    """启动时恢复或继续清理的结果，不隐藏无法证明一致的现场。"""

    restored_dataset_ids: tuple[str, ...] = ()
    cleaned_dataset_ids: tuple[str, ...] = ()
    diagnostics: tuple[str, ...] = ()


class DatasetDeletionService:
    """删除范围固定为一个已登记 UUID 目录及其已知恢复目录。"""

    RECOVERY_KINDS = ("annotation-operations", "label-migrations")

    def __init__(self, library: DatasetLibraryService) -> None:
        self.library = library
        self.operations_root = library.root / "recovery" / "dataset-deletions"

    def preflight(
        self,
        dataset_id: str,
        *,
        runtime_blockers: tuple[str, ...] = (),
    ) -> DatasetDeletionPreflight:
        """枚举普通文件并拒绝重解析点；统计失败时不产生可提交预检。"""

        entry = self.library.registered_entry(dataset_id)
        paths = self.library.dataset_repository.paths(dataset_id)
        _require_safe_directory(paths.root, self.library.root / "datasets")
        managed = _scan_tree(paths.root, prefix="dataset")
        recovery_roots = self._recovery_roots(dataset_id)
        recovery_entries: list[_TreeEntry] = []
        for kind, root in recovery_roots:
            if root.exists():
                _require_safe_directory(root, self.library.root / "recovery" / kind)
                recovery_entries.extend(_scan_tree(root, prefix=f"recovery/{kind}"))

        blockers = list(runtime_blockers)
        try:
            repository = DatasetSampleRepository(paths, dataset_id)
            operations = repository.list_operations()
        except SampleRepositoryError as error:
            raise DatasetDeletionError(f"数据集索引无法可靠统计: {error}") from error
        if operations:
            blockers.append(f"数据集仍有 {len(operations)} 个未完成受管操作")
        if recovery_entries:
            blockers.append("数据集仍有未完成恢复记录")

        try:
            label_set = read_json_model(paths.label_set, LabelSet)
            rectangle_count = _count_rectangles(paths.annotations)
        except (OSError, ValueError, json.JSONDecodeError) as error:
            raise DatasetDeletionError(f"标签或标注影响统计失败: {error}") from error

        all_entries = [*managed, *recovery_entries]
        impact = _impact(all_entries, len(label_set.labels), rectangle_count)
        return DatasetDeletionPreflight(
            id=new_id(),
            dataset_id=dataset_id,
            dataset_name=entry.name,
            library_sha256=self.library.library_repository.digest(),
            tree_sha256=_tree_digest(all_entries),
            impact=impact,
            managed_entries=tuple(item.serialized for item in managed),
            recovery_entries=tuple(item.serialized for item in recovery_entries),
            blockers=tuple(dict.fromkeys(blockers)),
        )

    def delete(self, request: DatasetDeletionRequest) -> DatasetDeletionReport:
        """暂存成功后移除登记；最终清理失败时保留可恢复清单。"""

        preflight = request.preflight
        if request.typed_name != preflight.dataset_name:
            raise DatasetDeletionError("输入的数据集名称不完全匹配")
        if not request.final_confirmed:
            raise DatasetDeletionError("尚未完成最终永久删除确认")
        if preflight.blockers:
            raise DatasetDeletionError("删除预检仍存在阻塞项，请重新检查")

        current = self.preflight(preflight.dataset_id)
        if current.blockers:
            raise DatasetDeletionError("删除条件已变化: " + "；".join(current.blockers))
        if (
            current.dataset_name != preflight.dataset_name
            or current.library_sha256 != preflight.library_sha256
            or current.tree_sha256 != preflight.tree_sha256
        ):
            raise DatasetDeletionError("资料库或数据集内容在确认后发生变化，请重新预检")

        operation_id = new_id()
        operation_root = self.operations_root / operation_id
        payload_root = operation_root / "payload"
        staged_dataset = payload_root / "dataset"
        manifest_path = operation_root / "manifest.json"
        manifest = DatasetDeletionRecoveryManifest(
            format_version=1,
            operation_id=operation_id,
            dataset_id=preflight.dataset_id,
            dataset_name=preflight.dataset_name,
            library_sha256=preflight.library_sha256,
            tree_sha256=preflight.tree_sha256,
            managed_entries=preflight.managed_entries,
            recovery_entries=preflight.recovery_entries,
            phase="prepared",
            created_at=utc_now().isoformat(),
        )
        moved: list[tuple[Path, Path]] = []
        try:
            operation_root.mkdir(parents=True, exist_ok=False)
            payload_root.mkdir()
            write_json_atomic(manifest_path, asdict(manifest))
            source_dataset = self.library.dataset_repository.paths(preflight.dataset_id).root
            os.replace(source_dataset, staged_dataset)
            moved.append((staged_dataset, source_dataset))
            for kind, source in self._recovery_roots(preflight.dataset_id):
                if not source.exists():
                    continue
                target = payload_root / "recovery" / kind
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(source, target)
                moved.append((target, source))
            manifest = _manifest_with_phase(manifest, "staged")
            write_json_atomic(manifest_path, asdict(manifest))
            self.library.remove_registration_for_deletion(
                preflight.dataset_id,
                expected_library_sha256=preflight.library_sha256,
            )
            manifest = _manifest_with_phase(manifest, "unregistered")
            write_json_atomic(manifest_path, asdict(manifest))
        except Exception as error:
            rollback_errors: list[str] = []
            if self.library.is_registered(preflight.dataset_id):
                for staged, source in reversed(moved):
                    try:
                        source.parent.mkdir(parents=True, exist_ok=True)
                        if staged.exists() and not source.exists():
                            os.replace(staged, source)
                    except OSError as rollback_error:
                        rollback_errors.append(str(rollback_error))
            suffix = f"；恢复失败: {'；'.join(rollback_errors)}" if rollback_errors else ""
            raise DatasetDeletionError(f"永久删除提交失败: {error}{suffix}") from error

        try:
            _remove_verified_operation(operation_root)
        except (OSError, DatasetDeletionError) as error:
            return DatasetDeletionReport(
                preflight.dataset_id,
                DatasetDeletionStatus.PENDING_CLEANUP,
                operation_id,
                f"资料库登记已移除，但内部清理尚未完成: {error}",
            )
        return DatasetDeletionReport(
            preflight.dataset_id,
            DatasetDeletionStatus.COMPLETED,
            operation_id,
        )

    def _recovery_roots(self, dataset_id: str) -> tuple[tuple[str, Path], ...]:
        return tuple(
            (
                kind,
                self.library.root / "recovery" / kind / dataset_id,
            )
            for kind in self.RECOVERY_KINDS
        )


def recover_dataset_deletions(library: DatasetLibraryService) -> DatasetDeletionRecoveryReport:
    """资料库仍登记则恢复完整目录，登记已移除则继续清理暂存树。"""

    operations_root = library.root / "recovery" / "dataset-deletions"
    if not operations_root.exists():
        operations_root.mkdir(parents=True, exist_ok=True)
        return DatasetDeletionRecoveryReport()
    if _is_reparse_point(operations_root):
        return DatasetDeletionRecoveryReport(diagnostics=("整数据集删除恢复目录是重解析点",))

    restored: list[str] = []
    cleaned: list[str] = []
    diagnostics: list[str] = []
    for operation_root in sorted(operations_root.iterdir(), key=lambda item: item.name):
        if not operation_root.is_dir() or _is_reparse_point(operation_root):
            diagnostics.append(f"忽略未知删除恢复项: {operation_root.name}")
            continue
        manifest_path = operation_root / "manifest.json"
        try:
            manifest = _read_manifest(manifest_path)
            staged_dataset = operation_root / "payload" / "dataset"
            target_dataset = library.dataset_repository.paths(manifest.dataset_id).root
            if library.is_registered(manifest.dataset_id):
                if staged_dataset.exists():
                    if target_dataset.exists():
                        raise DatasetDeletionError("原目录和暂存目录同时存在")
                    os.replace(staged_dataset, target_dataset)
                if not target_dataset.is_dir():
                    raise DatasetDeletionError("资料库仍登记但无法恢复完整数据集目录")
                for kind in DatasetDeletionService.RECOVERY_KINDS:
                    staged = operation_root / "payload" / "recovery" / kind
                    target = library.root / "recovery" / kind / manifest.dataset_id
                    if staged.exists():
                        if target.exists():
                            raise DatasetDeletionError(f"{kind} 恢复目标已存在")
                        target.parent.mkdir(parents=True, exist_ok=True)
                        os.replace(staged, target)
                _remove_verified_operation(operation_root)
                restored.append(manifest.dataset_id)
            else:
                _remove_verified_operation(operation_root)
                cleaned.append(manifest.dataset_id)
        except Exception as error:
            diagnostics.append(f"{operation_root.name}: {error}")
    return DatasetDeletionRecoveryReport(tuple(restored), tuple(cleaned), tuple(diagnostics))


@dataclass(frozen=True, slots=True)
class _TreeEntry:
    relative_path: str
    size: int
    modified_ns: int

    @property
    def serialized(self) -> str:
        return f"{self.relative_path}\t{self.size}\t{self.modified_ns}"


def _scan_tree(root: Path, *, prefix: str) -> list[_TreeEntry]:
    entries: list[_TreeEntry] = []

    def visit(directory: Path) -> None:
        for child in sorted(directory.iterdir(), key=lambda item: item.name.casefold()):
            if _is_reparse_point(child):
                raise DatasetDeletionError(f"受管目录包含重解析点: {child.name}")
            relative = child.relative_to(root).as_posix()
            if child.is_dir():
                visit(child)
            elif child.is_file():
                details = child.stat()
                entries.append(
                    _TreeEntry(f"{prefix}/{relative}", details.st_size, details.st_mtime_ns)
                )
            else:
                raise DatasetDeletionError(f"受管目录包含未知文件类型: {relative}")

    visit(root)
    return entries


def _impact(
    entries: list[_TreeEntry],
    label_count: int,
    rectangle_count: int,
) -> DatasetDeletionImpact:
    def selected(prefix: str, suffix: str | None = None) -> list[_TreeEntry]:
        return [
            item
            for item in entries
            if item.relative_path.startswith(prefix)
            and (suffix is None or item.relative_path.lower().endswith(suffix))
        ]

    images = selected("dataset/pool/images/", ".png")
    annotations = selected("dataset/pool/annotations/", ".json")
    models = selected("dataset/models/")
    indexes = [item for item in entries if item.relative_path == "dataset/index.sqlite"]
    thumbnails = selected("dataset/cache/thumbnails/")
    caches = [
        item
        for item in entries
        if item.relative_path.startswith("dataset/cache/")
        and not item.relative_path.startswith("dataset/cache/thumbnails/")
    ]
    trash = selected("dataset/trash/")
    recovery = selected("recovery/")
    return DatasetDeletionImpact(
        image_count=len(images),
        image_bytes=sum(item.size for item in images),
        annotation_file_count=len(annotations),
        rectangle_count=rectangle_count,
        label_count=label_count,
        model_file_count=len(models),
        index_file_count=len(indexes),
        thumbnail_file_count=len(thumbnails),
        cache_file_count=len(caches),
        trash_file_count=len(trash),
        recovery_file_count=len(recovery),
        total_file_count=len(entries),
        total_bytes=sum(item.size for item in entries),
    )


def _count_rectangles(annotations: Path) -> int:
    total = 0
    for path in sorted(annotations.glob("*.json"), key=lambda item: item.name.casefold()):
        if _is_reparse_point(path):
            raise DatasetDeletionError(f"标注文件是重解析点: {path.name}")
        payload = json.loads(path.read_text(encoding="utf-8"))
        shapes = payload.get("shapes", [])
        if not isinstance(shapes, list):
            raise DatasetDeletionError(f"标注 shapes 不是数组: {path.name}")
        total += sum(
            isinstance(shape, dict) and shape.get("shape_type") == "rectangle" for shape in shapes
        )
    return total


def _tree_digest(entries: list[_TreeEntry]) -> str:
    digest = hashlib.sha256()
    for entry in sorted(entries, key=lambda item: item.relative_path.casefold()):
        digest.update(entry.serialized.encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def _manifest_with_phase(
    manifest: DatasetDeletionRecoveryManifest,
    phase: str,
) -> DatasetDeletionRecoveryManifest:
    payload = asdict(manifest)
    payload["phase"] = phase
    return DatasetDeletionRecoveryManifest(**payload)


def _read_manifest(path: Path) -> DatasetDeletionRecoveryManifest:
    if _is_reparse_point(path):
        raise DatasetDeletionError("删除恢复清单是重解析点")
    payload: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    manifest = DatasetDeletionRecoveryManifest(**payload)
    if PurePosixPath(manifest.dataset_id).parts != (manifest.dataset_id,):
        raise DatasetDeletionError("删除恢复清单包含非法数据集 ID")
    library_id = str(UUID(manifest.dataset_id))
    if library_id != manifest.dataset_id:
        raise DatasetDeletionError("删除恢复清单数据集 ID 不是规范 UUID")
    return manifest


def _remove_verified_operation(operation_root: Path) -> None:
    """删除前逐项拒绝重解析点，避免 shutil.rmtree 跟随未知目标。"""

    if _is_reparse_point(operation_root):
        raise DatasetDeletionError("删除暂存目录是重解析点")

    def remove(directory: Path) -> None:
        for child in list(directory.iterdir()):
            if _is_reparse_point(child):
                raise DatasetDeletionError(f"删除暂存区包含重解析点: {child.name}")
            if child.is_dir():
                remove(child)
                child.rmdir()
            elif child.is_file():
                child.unlink()
            else:
                raise DatasetDeletionError(f"删除暂存区包含未知文件类型: {child.name}")

    remove(operation_root)
    operation_root.rmdir()


def _require_safe_directory(path: Path, expected_parent: Path) -> None:
    if not path.is_dir() or _is_reparse_point(path):
        raise DatasetDeletionError("目标受管目录缺失或属于重解析点")
    try:
        path.resolve(strict=True).relative_to(expected_parent.resolve(strict=True))
    except ValueError as error:
        raise DatasetDeletionError("目标受管目录越过资料库边界") from error


def _is_reparse_point(path: Path) -> bool:
    try:
        details = os.lstat(path)
    except OSError:
        return False
    attributes = getattr(details, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return path.is_symlink() or bool(attributes & reparse_flag)
