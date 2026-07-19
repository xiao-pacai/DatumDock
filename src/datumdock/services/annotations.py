"""受管数据集的 LabelMe 标注加载、原子保存、恢复与串行自动保存。"""

from __future__ import annotations

import hashlib
import json
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from datumdock.domain.models import (
    AnnotationDocument,
    AnnotationState,
    LabelSet,
    ReviewStatus,
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


@dataclass(frozen=True, slots=True)
class AnnotationSaveRequest:
    """一次不可变保存快照及其并发前提。"""

    dataset_id: str
    sample_id: str
    document_version: int
    expected_disk_sha256: str
    document: AnnotationDocument


@dataclass(frozen=True, slots=True)
class AnnotationSaveResult:
    """标注文件和 SQLite 摘要提交后的结果。"""

    sample_id: str
    saved_version: int
    json_sha256: str
    sqlite_synced: bool
    recovery_required: bool
    warnings: tuple[str, ...] = ()


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
                review_status=_formal_review_status(sample.review_status),
            )
            return AnnotationLoadResult(
                sample.id,
                document,
                label_set.model_copy(deep=True),
                (),
                "",
                sample.health.value == "ready",
                AnnotationState.MISSING,
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
            return AnnotationLoadResult(
                sample.id,
                document,
                label_set.model_copy(deep=True),
                warnings,
                digest,
                sample.health.value == "ready",
                AnnotationState.READY,
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
            )

    def save(self, request: AnnotationSaveRequest) -> AnnotationSaveResult:
        """先发布验证过的 JSON，再以恢复标记保护 SQLite 摘要提交。"""

        if request.dataset_id != self.dataset_id:
            raise AnnotationServiceError("保存请求不属于当前数据集")
        sample = self.samples.get_sample(request.sample_id)
        if sample is None:
            raise AnnotationServiceError("待保存标注的样本不存在")
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
        warnings = self._validate_document(document, label_set, allow_archived=True)
        review_status = _formal_review_status(document.review_status)
        self._validate_review_status(review_status, len(document.rectangles))
        path = self._annotation_path(sample.filename, sample.annotation_path)
        current_digest = _safe_sha256(path)
        if current_digest != request.expected_disk_sha256:
            raise AnnotationConflictError("标注文件已被其他操作修改，请重新加载后再保存")
        if path.exists():
            try:
                self.repository.load(
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

        # 未产生框且未确认负样本时只更新 SQLite 状态，不创建空 JSON。
        if (
            not path.exists()
            and not document.rectangles
            and review_status != ReviewStatus.COMPLETED_NEGATIVE
        ):
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
            )

        relative_path = (
            self.paths.annotations.joinpath(path.name).relative_to(self.paths.root).as_posix()
        )
        marker = self._marker_path(sample.id)
        marker_payload: dict[str, Any] = {
            "dataset_id": self.dataset_id,
            "sample_id": sample.id,
            "annotation_path": relative_path,
            "expected_sha256": request.expected_disk_sha256,
            "document_version": request.document_version,
            "phase": "prepared",
            "created_at": datetime.now(UTC).isoformat(),
        }
        try:
            self._write_marker(marker, marker_payload)
            digest = self.repository.save(path, document, label_set)
            marker_payload.update(phase="json_committed", json_sha256=digest)
            self._write_marker(marker, marker_payload)
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
            marker.unlink(missing_ok=True)
            return AnnotationSaveResult(
                sample.id,
                request.document_version,
                digest,
                True,
                False,
                warnings,
            )
        except (LabelMeError, SampleRepositoryError, OSError) as error:
            if path.exists() and marker_payload.get("phase") == "json_committed":
                self._mark_diagnostic(sample.id, AnnotationState.RECOVERY_REQUIRED)
            raise AnnotationServiceError(f"标注保存失败: {error}") from error

    def recover_pending(self) -> AnnotationMigrationReport:
        """只回放有限恢复标记，不扫描数据集中的全部 JSON。"""

        if not self.recovery_directory.exists():
            return AnnotationMigrationReport()
        examined = 0
        recovered = 0
        failed: list[str] = []
        diagnostics: list[str] = []
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
                    review_status=_formal_review_status(document.review_status),
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

    @staticmethod
    def _validate_review_status(status: ReviewStatus, rectangle_count: int) -> None:
        if status == ReviewStatus.COMPLETED and rectangle_count == 0:
            raise AnnotationServiceError("已完成状态至少需要一个有效矩形")
        if status == ReviewStatus.COMPLETED_NEGATIVE and rectangle_count != 0:
            raise AnnotationServiceError("已完成（无目标）状态不能包含矩形")

    def _annotation_path(self, filename: str, relative_path: str) -> Path:
        if relative_path:
            return self.samples.resolve_path(relative_path, "pool/annotations")
        return self.paths.annotations / f"{Path(filename).stem}.json"

    def _marker_path(self, sample_id: str) -> Path:
        return self.recovery_directory / f"{sample_id}.json"

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

    @property
    def state(self) -> AutosaveState:
        with self._lock:
            return self._state

    @property
    def error(self) -> str:
        with self._lock:
            return self._error

    def submit(self, request: AnnotationSaveRequest) -> Future[AnnotationSaveResult]:
        """排入最新快照；执行顺序与用户操作顺序完全一致。"""

        immutable = AnnotationSaveRequest(
            request.dataset_id,
            request.sample_id,
            request.document_version,
            request.expected_disk_sha256,
            request.document.model_copy(deep=True),
        )
        with self._lock:
            self._latest_request = immutable
            self._state = AutosaveState.SAVING
            self._error = ""
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
        if request is None:
            raise AnnotationServiceError("没有可重试的标注版本")
        self.service.recover_pending()
        loaded = self.service.load(request.sample_id)
        rebased = AnnotationSaveRequest(
            request.dataset_id,
            request.sample_id,
            request.document_version,
            loaded.disk_sha256,
            request.document.model_copy(deep=True),
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
            )
        return self.service.save(request)

    def _is_latest_request(self, request: AnnotationSaveRequest) -> bool:
        """样本 ID 与版本都一致时，完成结果才可更新当前状态栏。"""

        return bool(
            self._latest_request
            and self._latest_request.sample_id == request.sample_id
            and self._latest_request.document_version == request.document_version
        )


def review_status_after_edit(current: ReviewStatus) -> ReviewStatus:
    """按产品规则计算任意框编辑后的图片级复核状态。"""

    normalized = _formal_review_status(current)
    if normalized == ReviewStatus.ISSUE:
        return ReviewStatus.ISSUE
    return ReviewStatus.PENDING_REVIEW


def _formal_review_status(value: ReviewStatus) -> ReviewStatus:
    return {
        ReviewStatus.AUTO_PENDING_REVIEW: ReviewStatus.PENDING_REVIEW,
        ReviewStatus.REVIEWED: ReviewStatus.COMPLETED,
    }.get(value, value)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _safe_sha256(path: Path) -> str:
    try:
        return _sha256(path) if path.is_file() else ""
    except OSError:
        return ""
