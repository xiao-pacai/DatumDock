"""受管数据集的 LabelMe 标注加载、原子保存、恢复与串行自动保存。"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from datumdock.domain.models import (
    AnnotationDocument,
    AnnotationState,
    LabelSet,
    ReviewStatus,
    new_id,
    utc_now,
)
from datumdock.services.dataset_library import DatasetLibraryService, DatasetLibraryServiceError
from datumdock.services.labelme import (
    AnnotationRepository,
    LabelMeError,
    LabelMeRepository,
    UnknownLabelReferenceError,
)
from datumdock.services.sample_repository import DatasetSampleRepository, SampleRepositoryError
from datumdock.services.storage import write_json_atomic


class AnnotationServiceError(RuntimeError):
    """标注无法在保证文件与索引一致的前提下完成。"""


class AnnotationConflictError(AnnotationServiceError):
    """磁盘文档已被其他操作修改，禁止静默覆盖。"""


class AutosaveState(StrEnum):
    """供正式状态栏显示的自动保存状态。"""

    IDLE = "idle"
    SAVING = "saving"
    SAVED = "saved"
    FAILED = "failed"
    RECOVERING = "recovering"


class AnnotationSaveFailureKind(StrEnum):
    """用户提示按真实失败类型区分，不能把所有错误都归为磁盘权限。"""

    VERSION_CONFLICT = "version_conflict"
    EXTERNAL_MODIFICATION = "external_modification"
    PERMISSION = "permission"
    DISK_SPACE = "disk_space"
    VALIDATION = "validation"
    SQLITE = "sqlite"
    RECOVERY_REQUIRED = "recovery_required"
    UNKNOWN = "unknown"


class AnnotationEditOrigin(StrEnum):
    """区分人工、模型和兼容维护写入，复核状态只能由该来源驱动。"""

    MANUAL = "manual"
    MODEL = "model"
    MAINTENANCE = "maintenance"


class AnnotationEditKind(StrEnum):
    """标注内容发生变化的稳定操作类型。"""

    CREATE = "create"
    MOVE = "move"
    RESIZE = "resize"
    DELETE = "delete"
    REASSIGN = "reassign"
    UNDO = "undo"
    REDO = "redo"
    MODEL_WRITE = "model_write"
    COMPATIBILITY_REPAIR = "compatibility_repair"


class ReviewStateMachine:
    """统一计算双状态，页面和模型服务不得自行拼装状态。"""

    @staticmethod
    def after_edit(
        current: ReviewStatus | None,
        origin: AnnotationEditOrigin,
        *,
        changed: bool,
    ) -> ReviewStatus | None:
        if not changed:
            return current
        if origin == AnnotationEditOrigin.MAINTENANCE:
            return current
        if origin == AnnotationEditOrigin.MODEL:
            return ReviewStatus.PENDING_REVIEW
        return ReviewStatus.COMPLETED


@dataclass(frozen=True, slots=True)
class AnnotationSaveRequest:
    """一次不可变保存快照及其并发前提。"""

    dataset_id: str
    sample_id: str
    document_version: int
    expected_disk_sha256: str
    document: AnnotationDocument
    edit_origin: AnnotationEditOrigin = AnnotationEditOrigin.MANUAL
    edit_kind: AnnotationEditKind = AnnotationEditKind.CREATE
    base_document_version: int | None = None
    request_id: str = field(default_factory=new_id)
    shape_id: str | None = None
    label_set_revision: int = 0


@dataclass(frozen=True, slots=True)
class AnnotationSaveResult:
    """标注文件和 SQLite 摘要提交后的结果。"""

    sample_id: str
    saved_version: int
    json_sha256: str
    sqlite_synced: bool
    recovery_required: bool
    warnings: tuple[str, ...] = ()
    review_status: ReviewStatus | None = None


@dataclass(frozen=True, slots=True)
class AnnotationSaveCheckpoint:
    """加载时建立的磁盘与索引检查点，后续版本由串行队列自动衔接。"""

    sample_id: str
    document_version: int
    disk_sha256: str
    label_set_revision: int


@dataclass(frozen=True, slots=True)
class AnnotationSaveFailure:
    """状态栏和诊断详情消费的结构化失败，不丢失原始异常文本。"""

    kind: AnnotationSaveFailureKind
    message: str
    request_id: str = ""
    dataset_id: str = ""
    sample_id: str = ""
    shape_id: str | None = None
    edit_kind: str = ""
    requested_version: int = 0
    base_version: int | None = None
    current_version: int | None = None
    expected_disk_sha256: str = ""
    current_disk_sha256: str = ""
    label_set_revision: int = 0
    recovery_required: bool = False
    retryable: bool = False
    exception_type: str = ""
    exception_chain: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class AnnotationLoadResult:
    """加载结果显式区分可编辑、损坏与未知标签。"""

    sample_id: str
    document: AnnotationDocument | None
    label_set: LabelSet
    diagnostics: tuple[str, ...]
    disk_sha256: str
    editable: bool
    annotation_state: AnnotationState
    review_status: ReviewStatus | None = None
    checkpoint: AnnotationSaveCheckpoint | None = None


@dataclass(frozen=True, slots=True)
class AnnotationMigrationReport:
    """恢复或标签迁移的逐样本结果摘要。"""

    examined: int = 0
    updated: int = 0
    recovered: int = 0
    failed_sample_ids: tuple[str, ...] = ()
    diagnostics: tuple[str, ...] = ()


class AnnotationService:
    """在文件仓库与 SQLite 索引之间维持可恢复的一致性边界。"""

    def __init__(
        self,
        library_service: DatasetLibraryService,
        dataset_id: str,
        annotation_repository: AnnotationRepository | None = None,
    ) -> None:
        self.library_service = library_service
        self.dataset_id = dataset_id
        self.paths = library_service.dataset_repository.paths(dataset_id)
        self.samples = DatasetSampleRepository(self.paths, dataset_id)
        self.repository = annotation_repository or LabelMeRepository()
        self.recovery_directory = (
            library_service.root / "recovery" / "annotation-operations" / dataset_id
        )

    def load(self, sample_id: str) -> AnnotationLoadResult:
        """按需读取当前图片 JSON；缺失文件不会触发全数据集扫描。"""

        sample = self.samples.get_sample(sample_id)
        if sample is None:
            raise AnnotationServiceError("待加载标注的样本不存在")
        try:
            label_set = self.library_service.open_dataset(self.dataset_id).label_set
        except DatasetLibraryServiceError as error:
            raise AnnotationServiceError(f"标签集读取失败: {error}") from error
        path = self._annotation_path(sample.filename, sample.annotation_path)
        if not path.exists():
            document = AnnotationDocument(
                sample_id=sample.id,
                image_filename=sample.filename,
                image_width=sample.width,
                image_height=sample.height,
                document_version=sample.annotation_version,
            )
            return AnnotationLoadResult(
                sample.id,
                document,
                label_set.model_copy(deep=True),
                (),
                "",
                sample.health.value == "ready",
                AnnotationState.MISSING,
                sample.review_status,
                AnnotationSaveCheckpoint(
                    sample.id,
                    document.document_version,
                    "",
                    label_set.revision,
                ),
            )
        try:
            digest = _sha256(path)
            document = self.repository.load(
                path,
                sample.id,
                label_set,
                sample.filename,
                (sample.width, sample.height),
            )
            warnings = self._validate_document(document, label_set, allow_archived=True)
            archived = {label.id for label in label_set.labels if label.status.value == "archived"}
            if any(rectangle.label_id in archived for rectangle in document.rectangles):
                warnings = (*warnings, "标注包含已归档标签，可移动、删除或改派")
            if (
                sample.annotation_sha256 != digest
                or sample.annotation_version != document.document_version
                or sample.annotation_count != len(document.rectangles)
            ):
                try:
                    self.samples.update_annotation_index(
                        sample.id,
                        annotation_path=sample.annotation_path,
                        annotation_count=len(document.rectangles),
                        annotation_state=AnnotationState.READY,
                        annotation_version=document.document_version,
                        annotation_sha256=digest,
                        annotation_updated_at=utc_now().isoformat(),
                        review_status=sample.review_status,
                        shape_labels=tuple(
                            (shape.id, shape.label_id) for shape in document.rectangles
                        ),
                    )
                    warnings = (*warnings, "标注索引已按磁盘 JSON 事实完成对账")
                except SampleRepositoryError as error:
                    return AnnotationLoadResult(
                        sample.id,
                        document,
                        label_set.model_copy(deep=True),
                        (*warnings, f"标注索引对账失败: {error}"),
                        digest,
                        False,
                        AnnotationState.RECOVERY_REQUIRED,
                        sample.review_status,
                    )
            return AnnotationLoadResult(
                sample.id,
                document,
                label_set.model_copy(deep=True),
                warnings,
                digest,
                sample.health.value == "ready",
                AnnotationState.READY,
                sample.review_status,
                AnnotationSaveCheckpoint(
                    sample.id,
                    document.document_version,
                    digest,
                    label_set.revision,
                ),
            )
        except UnknownLabelReferenceError as error:
            self._mark_diagnostic(sample.id, AnnotationState.UNKNOWN_LABEL)
            return AnnotationLoadResult(
                sample.id,
                None,
                label_set.model_copy(deep=True),
                (str(error),),
                _safe_sha256(path),
                False,
                AnnotationState.UNKNOWN_LABEL,
                sample.review_status,
            )
        except (LabelMeError, OSError, ValueError) as error:
            self._mark_diagnostic(sample.id, AnnotationState.CORRUPT)
            return AnnotationLoadResult(
                sample.id,
                None,
                label_set.model_copy(deep=True),
                (f"标注 JSON 损坏，原文件未被覆盖: {error}",),
                _safe_sha256(path),
                False,
                AnnotationState.CORRUPT,
                sample.review_status,
            )

    def save(self, request: AnnotationSaveRequest) -> AnnotationSaveResult:
        """先发布验证过的 JSON，再以恢复标记保护 SQLite 摘要提交。"""

        if request.dataset_id != self.dataset_id:
            raise AnnotationServiceError("保存请求不属于当前数据集")
        sample = self.samples.get_sample(request.sample_id)
        if sample is None:
            raise AnnotationServiceError("待保存标注的样本不存在")
        if (
            request.base_document_version is not None
            and request.base_document_version != sample.annotation_version
        ):
            raise AnnotationConflictError("标注文档基准版本已变化，请重新加载后再保存")
        document = request.document.model_copy(
            deep=True,
            update={"document_version": request.document_version},
        )
        if document.sample_id != sample.id:
            raise AnnotationServiceError("标注文档与样本 ID 不一致")
        if document.image_filename != sample.filename:
            raise AnnotationServiceError("标注文档图片名与受管样本不一致")
        if (document.image_width, document.image_height) != (sample.width, sample.height):
            raise AnnotationServiceError("标注文档尺寸与受管图片不一致")
        try:
            label_set = self.library_service.open_dataset(self.dataset_id).label_set
        except DatasetLibraryServiceError as error:
            raise AnnotationServiceError(f"标签集读取失败: {error}") from error
        if request.label_set_revision and request.label_set_revision != label_set.revision:
            raise AnnotationConflictError("标签集修订已变化，请重新加载后再保存")
        warnings = self._validate_document(document, label_set, allow_archived=True)
        path = self._annotation_path(sample.filename, sample.annotation_path)
        current_digest = _safe_sha256(path)
        if current_digest != request.expected_disk_sha256:
            raise AnnotationConflictError("标注文件已被其他操作修改，请重新加载后再保存")
        existing_document: AnnotationDocument | None = None
        if path.exists():
            try:
                existing_document = self.repository.load(
                    path,
                    sample.id,
                    label_set,
                    sample.filename,
                    (sample.width, sample.height),
                )
            except (LabelMeError, OSError, ValueError) as error:
                self._mark_diagnostic(sample.id, AnnotationState.CORRUPT)
                raise AnnotationServiceError(
                    f"现有标注 JSON 无法验证，原文件未被覆盖: {error}"
                ) from error

        changed = _document_content_changed(existing_document, document)
        review_status = ReviewStateMachine.after_edit(
            sample.review_status,
            request.edit_origin,
            changed=changed,
        )

        # 新图片没有框时不创建无意义 JSON；明确完成人工复核使用 mark_review_completed。
        if not path.exists() and not document.rectangles:
            self.samples.update_annotation_index(
                sample.id,
                annotation_path="",
                annotation_count=0,
                annotation_state=AnnotationState.MISSING,
                annotation_version=request.document_version,
                annotation_sha256="",
                annotation_updated_at=datetime.now(UTC).isoformat(),
                review_status=review_status,
                shape_labels=(),
            )
            return AnnotationSaveResult(
                sample.id,
                request.document_version,
                "",
                True,
                False,
                warnings,
                review_status,
            )

        relative_path = (
            self.paths.annotations.joinpath(path.name).relative_to(self.paths.root).as_posix()
        )
        operation_id = new_id()
        operation_directory = self._operation_directory(operation_id)
        manifest = operation_directory / "manifest.json"
        candidate = operation_directory / "candidate.json"
        original = operation_directory / "original.json"
        marker_payload: dict[str, Any] = {
            "operation_id": operation_id,
            "dataset_id": self.dataset_id,
            "sample_id": sample.id,
            "annotation_path": relative_path,
            "expected_sha256": request.expected_disk_sha256,
            "document_version": request.document_version,
            "old_annotation_version": sample.annotation_version,
            "old_annotation_sha256": sample.annotation_sha256,
            "old_review_status": sample.review_status.value if sample.review_status else None,
            "target_review_status": review_status.value if review_status else None,
            "edit_origin": request.edit_origin.value,
            "edit_kind": request.edit_kind.value,
            "original_existed": path.exists(),
            "phase": "prepared",
            "created_at": datetime.now(UTC).isoformat(),
        }
        try:
            self._prepare_operation_directory(operation_directory)
            if path.exists():
                _copy_file_durable(path, original)
            self._write_marker(manifest, marker_payload)
            digest = self.repository.save(candidate, document, label_set)
            marker_payload.update(phase="candidate_ready", json_sha256=digest)
            self._write_marker(manifest, marker_payload)
            _replace_file_from_bytes(candidate, path)
            marker_payload.update(phase="json_committed", json_sha256=digest)
            self._write_marker(manifest, marker_payload)
            self.samples.update_annotation_index(
                sample.id,
                annotation_path=relative_path,
                annotation_count=len(document.rectangles),
                annotation_state=AnnotationState.READY,
                annotation_version=request.document_version,
                annotation_sha256=digest,
                annotation_updated_at=datetime.now(UTC).isoformat(),
                review_status=review_status,
                shape_labels=tuple(
                    (rectangle.id, rectangle.label_id) for rectangle in document.rectangles
                ),
            )
            marker_payload.update(phase="committed")
            self._write_marker(manifest, marker_payload)
            self._remove_operation_directory(operation_directory)
            return AnnotationSaveResult(
                sample.id,
                request.document_version,
                digest,
                True,
                False,
                warnings,
                review_status,
            )
        except (LabelMeError, SampleRepositoryError, OSError) as error:
            if marker_payload.get("phase") == "json_committed":
                try:
                    if bool(marker_payload["original_existed"]):
                        _replace_file_from_bytes(original, path)
                    else:
                        path.unlink(missing_ok=True)
                        _fsync_directory(path.parent)
                    marker_payload.update(phase="rolled_back", error=str(error))
                    self._write_marker(manifest, marker_payload)
                except OSError as rollback_error:
                    marker_payload.update(
                        phase="recovery_required",
                        error=str(error),
                        rollback_error=str(rollback_error),
                    )
                    self._write_marker(manifest, marker_payload)
                    self._mark_diagnostic(sample.id, AnnotationState.RECOVERY_REQUIRED)
                    raise AnnotationServiceError(
                        f"标注保存失败且原文件恢复失败: {rollback_error}"
                    ) from error
            raise AnnotationServiceError(f"标注保存失败: {error}") from error

    def mark_review_completed(self, sample_id: str) -> ReviewStatus:
        """无需制造空 JSON 即可确认整张图片已完成人工复核。"""

        sample = self.samples.get_sample(sample_id)
        if sample is None:
            raise AnnotationServiceError("待确认复核的样本不存在")
        if sample.annotation_state in {
            AnnotationState.CORRUPT,
            AnnotationState.UNKNOWN_LABEL,
            AnnotationState.RECOVERY_REQUIRED,
        }:
            raise AnnotationServiceError("样本存在标注诊断，修复前不能确认完成")
        try:
            self.samples.update_review_status(sample_id, ReviewStatus.COMPLETED)
        except SampleRepositoryError as error:
            raise AnnotationServiceError(f"确认复核完成失败: {error}") from error
        return ReviewStatus.COMPLETED

    def recover_pending(self) -> AnnotationMigrationReport:
        """只回放有限恢复标记，不扫描数据集中的全部 JSON。"""

        if not self.recovery_directory.exists():
            return AnnotationMigrationReport()
        examined = 0
        recovered = 0
        failed: list[str] = []
        diagnostics: list[str] = []
        operation_directories = sorted(
            path for path in self.recovery_directory.iterdir() if path.is_dir()
        )
        for operation_directory in operation_directories:
            examined += 1
            manifest = operation_directory / "manifest.json"
            payload: dict[str, Any] = {}
            try:
                if operation_directory.is_symlink() or manifest.is_symlink():
                    raise AnnotationServiceError("恢复目录不能是符号链接")
                payload = json.loads(manifest.read_text(encoding="utf-8"))
                if payload.get("dataset_id") != self.dataset_id:
                    raise AnnotationServiceError("恢复清单数据集 ID 不匹配")
                sample_id = str(payload["sample_id"])
                sample = self.samples.get_sample(sample_id)
                if sample is None:
                    raise AnnotationServiceError("恢复清单引用的样本不存在")
                phase = str(payload.get("phase"))
                if phase == "committed":
                    self._remove_operation_directory(operation_directory)
                    recovered += 1
                    continue
                if phase in {"json_committed", "recovery_required"}:
                    path = self.samples.resolve_path(
                        str(payload["annotation_path"]), "pool/annotations"
                    )
                    if bool(payload.get("original_existed")):
                        _replace_file_from_bytes(operation_directory / "original.json", path)
                    else:
                        path.unlink(missing_ok=True)
                        _fsync_directory(path.parent)
                    payload.update(phase="rolled_back")
                    self._write_marker(manifest, payload)
                    self.samples.update_annotation_diagnostic(
                        sample_id,
                        AnnotationState.READY
                        if sample.annotation_path
                        else AnnotationState.MISSING,
                    )
                    recovered += 1
                    continue
                if phase == "rolled_back":
                    diagnostics.append(f"保留可重试候选: {operation_directory.name}")
                    continue
                diagnostics.append(f"保留未发布候选: {operation_directory.name}")
            except Exception as error:
                failed.append(str(payload.get("sample_id", operation_directory.name)))
                diagnostics.append(f"恢复操作 {operation_directory.name} 处理失败: {error}")

        try:
            label_set = self.library_service.open_dataset(self.dataset_id).label_set
        except DatasetLibraryServiceError as error:
            raise AnnotationServiceError(f"恢复时无法读取标签集: {error}") from error
        for marker in sorted(self.recovery_directory.glob("*.json")):
            examined += 1
            payload: dict[str, Any] = {}
            if marker.is_symlink():
                diagnostics.append(f"拒绝跟随恢复标记符号链接: {marker.name}")
                continue
            try:
                payload = json.loads(marker.read_text(encoding="utf-8"))
                sample_id = str(payload["sample_id"])
                if payload.get("dataset_id") != self.dataset_id:
                    raise AnnotationServiceError("恢复标记数据集 ID 不匹配")
                if payload.get("phase") != "json_committed":
                    diagnostics.append(f"保留未提交恢复标记: {marker.name}")
                    failed.append(sample_id)
                    continue
                sample = self.samples.get_sample(sample_id)
                if sample is None:
                    raise AnnotationServiceError("恢复标记引用的样本不存在")
                path = self.samples.resolve_path(
                    str(payload["annotation_path"]), "pool/annotations"
                )
                digest = _sha256(path)
                if digest != payload.get("json_sha256"):
                    raise AnnotationConflictError("恢复标记摘要与标注文件不一致")
                document = self.repository.load(
                    path,
                    sample.id,
                    label_set,
                    sample.filename,
                    (sample.width, sample.height),
                )
                self._validate_document(document, label_set, allow_archived=True)
                self.samples.update_annotation_index(
                    sample.id,
                    annotation_path=str(payload["annotation_path"]),
                    annotation_count=len(document.rectangles),
                    annotation_state=AnnotationState.READY,
                    annotation_version=int(payload["document_version"]),
                    annotation_sha256=digest,
                    annotation_updated_at=datetime.now(UTC).isoformat(),
                    review_status=sample.review_status,
                    shape_labels=tuple(
                        (rectangle.id, rectangle.label_id) for rectangle in document.rectangles
                    ),
                )
                marker.unlink(missing_ok=True)
                recovered += 1
            except Exception as error:
                failed.append(str(payload.get("sample_id", marker.stem)))
                diagnostics.append(f"恢复标记 {marker.name} 处理失败: {error}")
        return AnnotationMigrationReport(
            examined=examined,
            recovered=recovered,
            failed_sample_ids=tuple(dict.fromkeys(failed)),
            diagnostics=tuple(diagnostics),
        )

    def _validate_document(
        self,
        document: AnnotationDocument,
        label_set: LabelSet,
        *,
        allow_archived: bool,
    ) -> tuple[str, ...]:
        labels = {label.id: label for label in label_set.labels}
        warnings: list[str] = []
        for rectangle in document.rectangles:
            label = labels.get(rectangle.label_id)
            if label is None:
                raise AnnotationServiceError(f"矩形引用未知标签: {rectangle.label_id}")
            if label.status.value == "archived" and not allow_archived:
                raise AnnotationServiceError("不能使用已归档标签创建新框")
            if rectangle.width <= 0 or rectangle.height <= 0:
                raise AnnotationServiceError("零面积矩形不能保存")
            if (
                rectangle.x1 < 0
                or rectangle.y1 < 0
                or rectangle.x2 > document.image_width
                or rectangle.y2 > document.image_height
            ):
                raise AnnotationServiceError("矩形坐标越过图片边界")
            if (
                rectangle.width < 2
                or rectangle.height < 2
                or rectangle.width * rectangle.height < 4
            ):
                warnings.append(f"矩形 {rectangle.id} 尺寸很小，请人工确认")
        return tuple(warnings)

    def _annotation_path(self, filename: str, relative_path: str) -> Path:
        if relative_path:
            return self.samples.resolve_path(relative_path, "pool/annotations")
        return self.paths.annotations / f"{Path(filename).stem}.json"

    def _marker_path(self, sample_id: str) -> Path:
        return self.recovery_directory / f"{sample_id}.json"

    def _operation_directory(self, operation_id: str) -> Path:
        return self.recovery_directory / operation_id

    def _prepare_operation_directory(self, directory: Path) -> None:
        if self.recovery_directory.exists() and self.recovery_directory.is_symlink():
            raise AnnotationServiceError("标注恢复目录不能是符号链接")
        self.recovery_directory.mkdir(parents=True, exist_ok=True)
        try:
            directory.resolve(strict=False).relative_to(self.recovery_directory.resolve())
        except ValueError as error:
            raise AnnotationServiceError("标注恢复操作越过受管目录") from error
        directory.mkdir(parents=False, exist_ok=False)

    def _remove_operation_directory(self, directory: Path) -> None:
        try:
            directory.resolve(strict=True).relative_to(self.recovery_directory.resolve())
        except (OSError, ValueError) as error:
            raise AnnotationServiceError("拒绝清理越界的标注恢复目录") from error
        if directory.is_symlink():
            raise AnnotationServiceError("拒绝清理符号链接恢复目录")
        shutil.rmtree(directory)
        _fsync_directory(self.recovery_directory)

    def _write_marker(self, marker: Path, payload: dict[str, Any]) -> None:
        if self.recovery_directory.exists() and self.recovery_directory.is_symlink():
            raise AnnotationServiceError("标注恢复目录不能是符号链接")
        self.recovery_directory.mkdir(parents=True, exist_ok=True)
        write_json_atomic(marker, payload)

    def _mark_diagnostic(self, sample_id: str, state: AnnotationState) -> None:
        try:
            self.samples.update_annotation_diagnostic(sample_id, state)
        except SampleRepositoryError as error:
            raise AnnotationServiceError(f"标注异常状态写入失败: {error}") from error


class AnnotationHistory:
    """保存当前图片至多 100 个用户操作节点。"""

    def __init__(self, document: AnnotationDocument, limit: int = 100) -> None:
        if limit < 1:
            raise ValueError("历史节点上限必须大于零")
        self.limit = limit
        self._undo: list[AnnotationDocument] = [document.model_copy(deep=True)]
        self._redo: list[AnnotationDocument] = []

    @property
    def can_undo(self) -> bool:
        return len(self._undo) > 1

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    def record(self, document: AnnotationDocument) -> None:
        """一次完整手势只追加一个不可变快照。"""

        snapshot = document.model_copy(deep=True)
        if snapshot.model_dump(mode="json") == self._undo[-1].model_dump(mode="json"):
            return
        self._undo.append(snapshot)
        if len(self._undo) > self.limit + 1:
            self._undo.pop(0)
        self._redo.clear()

    def undo(self) -> AnnotationDocument:
        if not self.can_undo:
            return self._undo[-1].model_copy(deep=True)
        self._redo.append(self._undo.pop())
        return self._undo[-1].model_copy(deep=True)

    def redo(self) -> AnnotationDocument:
        if not self._redo:
            return self._undo[-1].model_copy(deep=True)
        restored = self._redo.pop()
        self._undo.append(restored)
        return restored.model_copy(deep=True)


class AnnotationAutosaveService:
    """使用单线程串行保存不可变文档版本，旧结果不会覆盖新状态。"""

    def __init__(self, service: AnnotationService) -> None:
        self.service = service
        self._executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="datumdock-annotation"
        )
        self._lock = threading.Lock()
        self._latest_request: AnnotationSaveRequest | None = None
        self._latest_future: Future[AnnotationSaveResult] | None = None
        self._saved_by_sample: dict[str, tuple[int, str]] = {}
        self._state = AutosaveState.IDLE
        self._error: str = ""
        self._failure: AnnotationSaveFailure | None = None

    @property
    def state(self) -> AutosaveState:
        with self._lock:
            return self._state

    @property
    def error(self) -> str:
        with self._lock:
            return self._error

    @property
    def failure(self) -> AnnotationSaveFailure | None:
        with self._lock:
            return self._failure

    def seed_checkpoint(self, checkpoint: AnnotationSaveCheckpoint) -> None:
        """图片加载时登记事实检查点，防止首个改派依赖页面中的陈旧摘要。"""

        with self._lock:
            previous = self._saved_by_sample.get(checkpoint.sample_id, (-1, ""))
            if checkpoint.document_version >= previous[0]:
                self._saved_by_sample[checkpoint.sample_id] = (
                    checkpoint.document_version,
                    checkpoint.disk_sha256,
                )

    def submit(self, request: AnnotationSaveRequest) -> Future[AnnotationSaveResult]:
        """排入最新快照；执行顺序与用户操作顺序完全一致。"""

        immutable = AnnotationSaveRequest(
            request.dataset_id,
            request.sample_id,
            request.document_version,
            request.expected_disk_sha256,
            request.document.model_copy(deep=True),
            request.edit_origin,
            request.edit_kind,
            request.base_document_version,
            request.request_id,
            request.shape_id,
            request.label_set_revision,
        )
        with self._lock:
            self._latest_request = immutable
            self._state = AutosaveState.SAVING
            self._error = ""
            self._failure = None
            future = self._executor.submit(self._save_with_rebase, immutable)
            self._latest_future = future
        future.add_done_callback(lambda completed: self._finish(immutable, completed))
        return future

    def wait_latest(self, timeout: float | None = None) -> AnnotationSaveResult | None:
        """切图、切数据集和关闭前等待最新版本落盘。"""

        with self._lock:
            future = self._latest_future
        return future.result(timeout=timeout) if future else None

    def retry_latest(self) -> Future[AnnotationSaveResult]:
        """重试最近失败的内存快照，不构造更旧版本。"""

        with self._lock:
            request = self._latest_request
            failure = self._failure
        if request is None:
            raise AnnotationServiceError("没有可重试的标注版本")
        if failure is not None and not failure.retryable:
            raise AnnotationConflictError("该失败需要重新加载或修复数据，不能直接覆盖重试")
        self.service.recover_pending()
        loaded = self.service.load(request.sample_id)
        rebased = AnnotationSaveRequest(
            request.dataset_id,
            request.sample_id,
            request.document_version,
            loaded.disk_sha256,
            request.document.model_copy(deep=True),
            request.edit_origin,
            request.edit_kind,
            loaded.document.document_version if loaded.document else None,
            request.request_id,
            request.shape_id,
            request.label_set_revision,
        )
        return self.submit(rebased)

    def close(self) -> None:
        self._executor.shutdown(wait=True, cancel_futures=False)

    def _finish(
        self,
        request: AnnotationSaveRequest,
        future: Future[AnnotationSaveResult],
    ) -> None:
        with self._lock:
            try:
                result = future.result()
            except Exception as error:
                if self._is_latest_request(request):
                    self._state = AutosaveState.FAILED
                    self._error = str(error)
                    self._failure = _classify_save_failure(error, request, self.service)
                return
            previous = self._saved_by_sample.get(request.sample_id, (-1, ""))
            if request.document_version >= previous[0]:
                self._saved_by_sample[request.sample_id] = (
                    request.document_version,
                    result.json_sha256,
                )
            if self._is_latest_request(request):
                self._state = AutosaveState.SAVED
                self._error = ""
                self._failure = None

    def _save_with_rebase(self, request: AnnotationSaveRequest) -> AnnotationSaveResult:
        """同一队列的后续版本以上一个成功摘要为并发前提。"""

        with self._lock:
            previous_version, previous_digest = self._saved_by_sample.get(
                request.sample_id,
                (-1, ""),
            )
        if previous_version >= 0 and request.document_version > previous_version:
            request = AnnotationSaveRequest(
                request.dataset_id,
                request.sample_id,
                request.document_version,
                previous_digest,
                request.document,
                request.edit_origin,
                request.edit_kind,
                previous_version,
                request.request_id,
                request.shape_id,
                request.label_set_revision,
            )
        result = self.service.save(request)
        # 必须在线程取出下一项之前登记检查点；仅依赖 Future 回调会留下极短竞争窗口。
        with self._lock:
            previous = self._saved_by_sample.get(request.sample_id, (-1, ""))
            if request.document_version >= previous[0]:
                self._saved_by_sample[request.sample_id] = (
                    request.document_version,
                    result.json_sha256,
                )
        return result

    def _is_latest_request(self, request: AnnotationSaveRequest) -> bool:
        """样本 ID 与版本都一致时，完成结果才可更新当前状态栏。"""

        return bool(
            self._latest_request
            and self._latest_request.sample_id == request.sample_id
            and self._latest_request.document_version == request.document_version
        )


def review_status_after_edit(current: ReviewStatus | None) -> ReviewStatus:
    """兼容旧调用点；人工有效编辑统一转为已完成。"""

    return (
        ReviewStateMachine.after_edit(
            current,
            AnnotationEditOrigin.MANUAL,
            changed=True,
        )
        or ReviewStatus.COMPLETED
    )


def _classify_save_failure(
    error: Exception,
    request: AnnotationSaveRequest | None = None,
    service: AnnotationService | None = None,
) -> AnnotationSaveFailure:
    """沿异常链识别真实原因；无法证明时使用未知错误而不是权限提示。"""

    chain: list[BaseException] = []
    current: BaseException | None = error
    while current is not None and current not in chain:
        chain.append(current)
        current = current.__cause__ or current.__context__
    text = "；".join(str(item) for item in chain if str(item)) or error.__class__.__name__
    lowered = text.casefold()
    if any(isinstance(item, PermissionError) for item in chain):
        kind = AnnotationSaveFailureKind.PERMISSION
    elif any(isinstance(item, OSError) and getattr(item, "errno", None) == 28 for item in chain):
        kind = AnnotationSaveFailureKind.DISK_SPACE
    elif "摘要" in text or "外部" in text or "sha" in lowered:
        kind = AnnotationSaveFailureKind.EXTERNAL_MODIFICATION
    elif "版本" in text or "version" in lowered:
        kind = AnnotationSaveFailureKind.VERSION_CONFLICT
    elif "sqlite" in lowered or "索引" in text:
        kind = AnnotationSaveFailureKind.SQLITE
    elif "恢复" in text:
        kind = AnnotationSaveFailureKind.RECOVERY_REQUIRED
    elif "验证" in text or "坐标" in text or "标签" in text:
        kind = AnnotationSaveFailureKind.VALIDATION
    else:
        kind = AnnotationSaveFailureKind.UNKNOWN
    sample = None
    current_digest = ""
    if request is not None and service is not None:
        try:
            sample = service.samples.get_sample(request.sample_id)
            if sample is not None and sample.annotation_path:
                path = service.samples.resolve_path(
                    sample.annotation_path,
                    "pool/annotations",
                )
                current_digest = _safe_sha256(path)
        except Exception:
            # 诊断采集绝不能掩盖原始保存异常；测试替身也可能不提供完整 Repository。
            current_digest = ""
    retryable = kind in {
        AnnotationSaveFailureKind.PERMISSION,
        AnnotationSaveFailureKind.DISK_SPACE,
        AnnotationSaveFailureKind.SQLITE,
        AnnotationSaveFailureKind.RECOVERY_REQUIRED,
        AnnotationSaveFailureKind.UNKNOWN,
    }
    return AnnotationSaveFailure(
        kind,
        text,
        request_id=request.request_id if request else "",
        dataset_id=request.dataset_id if request else "",
        sample_id=request.sample_id if request else "",
        shape_id=request.shape_id if request else None,
        edit_kind=request.edit_kind.value if request else "",
        requested_version=request.document_version if request else 0,
        base_version=request.base_document_version if request else None,
        current_version=sample.annotation_version if sample else None,
        expected_disk_sha256=request.expected_disk_sha256 if request else "",
        current_disk_sha256=current_digest,
        label_set_revision=request.label_set_revision if request else 0,
        recovery_required=bool(
            sample and sample.annotation_state == AnnotationState.RECOVERY_REQUIRED
        ),
        retryable=retryable,
        exception_type=error.__class__.__name__,
        exception_chain=tuple(f"{item.__class__.__name__}: {item}" for item in chain),
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _safe_sha256(path: Path) -> str:
    try:
        return _sha256(path) if path.is_file() else ""
    except OSError:
        return ""


def _document_content_changed(
    previous: AnnotationDocument | None,
    current: AnnotationDocument,
) -> bool:
    if previous is None:
        return bool(current.rectangles or current.unsupported_shapes)
    return previous.model_dump(exclude={"document_version"}) != current.model_dump(
        exclude={"document_version"}
    )


def _copy_file_durable(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with source.open("rb") as source_stream, target.open("wb") as target_stream:
        shutil.copyfileobj(source_stream, target_stream)
        target_stream.flush()
        os.fsync(target_stream.fileno())
    _fsync_directory(target.parent)


def _replace_file_from_bytes(source: Path, target: Path) -> None:
    data = source.read_bytes()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{new_id()}.tmp")
    try:
        with temporary.open("wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
        _fsync_directory(target.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _fsync_directory(directory: Path) -> None:
    """Windows 不支持目录 fsync；可用的平台上尽力刷新目录项。"""

    if os.name == "nt":
        return
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
