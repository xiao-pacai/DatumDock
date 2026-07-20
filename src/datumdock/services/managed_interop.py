"""受管数据集与 X-AnyLabeling/LabelMe 目录之间的安全双向互操作。"""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import os
import re
import shutil
import tempfile
import unicodedata
from collections.abc import Callable, Iterable, Mapping
from contextlib import suppress
from dataclasses import dataclass, field
from enum import StrEnum
from io import BytesIO
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from PIL import Image, ImageOps

from datumdock.domain.models import (
    AnnotationDocument,
    AnnotationState,
    DatasetSample,
    Label,
    LabelSet,
    LabelStatus,
    ReviewStatus,
    SampleHealth,
    ThumbnailState,
    new_id,
    utc_now,
)
from datumdock.services.dataset_library import DatasetLibraryService
from datumdock.services.image_pool import (
    DuplicateDecision,
    ImageImportPreflight,
    ImageImportPreflightRequest,
    ImageImportService,
    ImagePoolError,
    PreparedImportItem,
)
from datumdock.services.labelme import LabelMeError, LabelMeRepository
from datumdock.services.managed_labels import LabelSetService, ManagedLabelError
from datumdock.services.sample_repository import (
    DatasetSampleRepository,
    ManagedOperation,
    SampleRepositoryError,
)


class InteropError(RuntimeError):
    """互操作无法在不损坏来源或受管数据的前提下继续。"""


class InteropIssueSeverity(StrEnum):
    """预检问题的严重程度决定能否提交对应文件。"""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class XAnyExportScope(StrEnum):
    """导出范围仅在本次向导会话内存在。"""

    ALL = "all"
    FILTERED = "filtered"
    SELECTED = "selected"


class ExternalLabelAction(StrEnum):
    """未知外部标签必须显式映射、新建或只读保留。"""

    MAP = "map"
    CREATE = "create"
    PRESERVE_READONLY = "preserve_readonly"


@dataclass(frozen=True, slots=True)
class InteropIssue:
    """可定位到单个相对文件的结构化互操作问题。"""

    severity: InteropIssueSeverity
    code: str
    message: str
    relative_path: str = ""


@dataclass(frozen=True, slots=True)
class SourceFingerprint:
    """提交前复核来源，避免预检后文件被替换。"""

    relative_path: str
    size: int
    modified_ns: int
    sha256: str


@dataclass(frozen=True, slots=True)
class ExternalLabelReference:
    """外部训练名及其引用次数。"""

    name: str
    shape_count: int
    matched_label_id: str | None = None
    proposed_training_name: str | None = None
    archived_label_id: str | None = None


@dataclass(frozen=True, slots=True)
class ProposedImportLabel:
    """Service 根据外部文本生成的可复验标签提案。"""

    external_name: str
    training_name: str
    alias: str


@dataclass(frozen=True, slots=True)
class ExternalLabelDecision:
    """UI 只提交选择，不在 Qt 层构造领域标签对象。"""

    external_name: str
    action: ExternalLabelAction
    target_label_id: str | None = None


@dataclass(frozen=True, slots=True)
class ExternalLabelResolution:
    """目标标签 ID 为空表示以只读兼容 shape 保留。"""

    external_name: str
    target_label_id: str | None


@dataclass(frozen=True, slots=True)
class XAnyImportPreflightRequest:
    """目录路径只存在于当前导入会话，不写入受管资料库。"""

    dataset_id: str
    source_directory: Path


@dataclass(frozen=True, slots=True)
class PreparedInteropItem:
    """图片已规范化，外部 JSON 已解析但尚未发布。"""

    image: PreparedImportItem
    relative_image_path: str
    annotation_path: Path | None
    annotation_payload: dict[str, Any] | None
    image_fingerprint: SourceFingerprint
    annotation_fingerprint: SourceFingerprint | None
    external_labels: tuple[str, ...]
    compatibility_shape_count: int
    blocking_issues: tuple[InteropIssue, ...] = ()


@dataclass(frozen=True, slots=True)
class XAnyImportPreflight:
    """导入预检快照；标签修订变化后必须重新执行。"""

    session_id: str
    source_directory: Path
    image_preflight: ImageImportPreflight
    items: tuple[PreparedInteropItem, ...]
    issues: tuple[InteropIssue, ...]
    external_labels: tuple[ExternalLabelReference, ...]
    label_set_revision: int
    discovered_image_count: int
    labels_fingerprint: SourceFingerprint | None = None

    @property
    def blocking_count(self) -> int:
        return sum(issue.severity == InteropIssueSeverity.ERROR for issue in self.issues)


@dataclass(frozen=True, slots=True)
class XAnyImportCommitRequest:
    """用户确认的重复决定和标签映射。"""

    dataset_id: str
    preflight: XAnyImportPreflight
    duplicate_decisions: Mapping[str, DuplicateDecision]
    label_resolutions: tuple[ExternalLabelResolution, ...] = ()
    new_labels: tuple[Label, ...] = ()
    label_decisions: tuple[ExternalLabelDecision, ...] = ()


@dataclass(slots=True)
class XAnyImportReport:
    """导入报告区分完整成功、跳过、兼容保留和失败。"""

    imported_sample_ids: list[str] = field(default_factory=list)
    unannotated_sample_ids: list[str] = field(default_factory=list)
    skipped_item_ids: list[str] = field(default_factory=list)
    duplicate_item_ids: list[str] = field(default_factory=list)
    compatibility_shape_count: int = 0
    created_label_ids: list[str] = field(default_factory=list)
    mapped_label_count: int = 0
    readonly_label_count: int = 0
    generated_training_names: dict[str, str] = field(default_factory=dict)
    failures: dict[str, str] = field(default_factory=dict)
    cancelled: bool = False


@dataclass(frozen=True, slots=True)
class XAnyExportRequest:
    """一次性导出请求，不保存在数据集配置或历史中。"""

    dataset_id: str
    output_directory: Path
    scope: XAnyExportScope = XAnyExportScope.ALL
    sample_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class XAnyExportPreflight:
    """导出前固定样本 ID，并展示阻断项和预估空间。"""

    request: XAnyExportRequest
    sample_ids: tuple[str, ...]
    issues: tuple[InteropIssue, ...]
    estimated_bytes: int
    annotated_count: int
    empty_count: int

    @property
    def can_export(self) -> bool:
        return not any(issue.severity == InteropIssueSeverity.ERROR for issue in self.issues)


@dataclass(slots=True)
class XAnyExportReport:
    """完成后可展示的交换目录统计；空标注计数表示未生成 JSON 的图片。"""

    output_directory: Path
    image_count: int = 0
    json_count: int = 0
    rectangle_count: int = 0
    empty_annotation_count: int = 0
    compatibility_shapes: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    cancelled: bool = False


class XAnyLabelingInteropService:
    """只操作一个受管数据集，并将外部目录始终视为只读来源。"""

    def __init__(self, library: DatasetLibraryService, dataset_id: str) -> None:
        self.library = library
        self.dataset_id = dataset_id
        bundle = library.open_dataset(dataset_id)
        self.paths = library.dataset_repository.paths(dataset_id)
        self.samples = DatasetSampleRepository(self.paths, dataset_id)
        self.images = ImageImportService(self.paths, bundle.dataset, self.samples)
        self.labelme = LabelMeRepository()
        self.labels = LabelSetService(library)

    def preflight_import(
        self,
        request: XAnyImportPreflightRequest,
        *,
        progress: Callable[[int, int, str], None] | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> XAnyImportPreflight:
        """只读扫描来源，并复用图片池的内部临时 PNG 预检。"""

        if request.dataset_id != self.dataset_id:
            raise InteropError("导入请求不属于当前数据集")
        root = request.source_directory.resolve(strict=True)
        if not root.is_dir() or request.source_directory.is_symlink():
            raise InteropError("X-AnyLabeling 来源必须是非符号链接目录")
        image_paths, json_paths, discovery_issues = _discover_exchange_files(root)
        labels_path = root / "labels.txt"
        labels_fingerprint = None
        declared_labels: tuple[str, ...] = ()
        if labels_path.is_file() and not labels_path.is_symlink():
            labels_fingerprint = _fingerprint(labels_path, root)
            declared_labels = _read_labels_file(labels_path)
        image_preflight = self.images.preflight(
            ImageImportPreflightRequest(self.dataset_id, image_paths, recursive=False),
            progress=progress,
            cancelled=cancelled,
        )
        prepared_by_source = {
            item.source_path.resolve(strict=True): item for item in image_preflight.items
        }
        issues = list(discovery_issues)
        for path, message in image_preflight.failures.items():
            issues.append(
                InteropIssue(
                    InteropIssueSeverity.ERROR,
                    "image_prepare_failed",
                    message,
                    _safe_relative(Path(path), root),
                )
            )
        items: list[PreparedInteropItem] = []
        label_counts: dict[str, tuple[str, int]] = {
            name.casefold(): (name, 0) for name in declared_labels
        }
        paired_json: set[Path] = set()
        for source in image_paths:
            if cancelled and cancelled():
                break
            prepared = prepared_by_source.get(source.resolve(strict=True))
            if prepared is None:
                continue
            relative = source.relative_to(root).as_posix()
            annotation_path = source.with_suffix(".json")
            payload: dict[str, Any] | None = None
            item_issues: list[InteropIssue] = []
            external_names: list[str] = []
            compatibility_count = 0
            annotation_fingerprint = None
            if annotation_path.is_file() and not annotation_path.is_symlink():
                paired_json.add(annotation_path.resolve(strict=True))
                annotation_fingerprint = _fingerprint(annotation_path, root)
                try:
                    payload = _read_external_payload(annotation_path)
                    _validate_external_payload(
                        payload,
                        root,
                        annotation_path,
                        source,
                        (prepared.width, prepared.height),
                    )
                    for shape in payload.get("shapes", []):
                        name = str(shape.get("label", "")).strip()
                        if name:
                            external_names.append(name)
                            key = name.casefold()
                            previous = label_counts.get(key, (name, 0))
                            label_counts[key] = (previous[0], previous[1] + 1)
                        if shape.get("shape_type") != "rectangle":
                            compatibility_count += 1
                except (OSError, ValueError, json.JSONDecodeError, InteropError) as error:
                    issue = InteropIssue(
                        InteropIssueSeverity.ERROR,
                        "invalid_annotation",
                        str(error),
                        annotation_path.relative_to(root).as_posix(),
                    )
                    item_issues.append(issue)
                    issues.append(issue)
                    payload = None
            else:
                issue = InteropIssue(
                    InteropIssueSeverity.WARNING,
                    "missing_annotation",
                    "图片没有同名 JSON，将作为无标注图片导入",
                    relative,
                )
                issues.append(issue)
            items.append(
                PreparedInteropItem(
                    prepared,
                    relative,
                    annotation_path if annotation_path.is_file() else None,
                    payload,
                    _fingerprint(source, root),
                    annotation_fingerprint,
                    tuple(dict.fromkeys(external_names)),
                    compatibility_count,
                    tuple(item_issues),
                )
            )
        for json_path in json_paths:
            if json_path.resolve(strict=True) not in paired_json:
                issues.append(
                    InteropIssue(
                        InteropIssueSeverity.WARNING,
                        "orphan_annotation",
                        "JSON 没有同名图片，已跳过且未修改来源文件",
                        json_path.relative_to(root).as_posix(),
                    )
                )
        label_set = self.library.open_dataset(self.dataset_id).label_set
        active_by_name = {
            label.name.casefold(): label
            for label in label_set.labels
            if label.status == LabelStatus.ACTIVE
        }
        archived_by_name = {
            label.name.casefold(): label
            for label in label_set.labels
            if label.status == LabelStatus.ARCHIVED
        }
        used_names = {label.name.casefold() for label in label_set.labels}
        references_list: list[ExternalLabelReference] = []
        for key, (name, count) in label_counts.items():
            proposed_name = _safe_training_name(name, used_names)
            if key not in active_by_name:
                used_names.add(proposed_name.casefold())
            references_list.append(
                ExternalLabelReference(
                    name,
                    count,
                    active_by_name.get(key).id if key in active_by_name else None,
                    proposed_name,
                    archived_by_name.get(key).id if key in archived_by_name else None,
                )
            )
        references = tuple(references_list)
        return XAnyImportPreflight(
            image_preflight.session_id,
            root,
            image_preflight,
            tuple(items),
            tuple(issues),
            references,
            label_set.revision,
            len(image_paths),
            labels_fingerprint,
        )

    def commit_import(
        self,
        request: XAnyImportCommitRequest,
        *,
        progress: Callable[[int, int, str], None] | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> XAnyImportReport:
        """逐项提交图片、JSON 和索引；取消只发生在样本边界。"""

        if (
            request.dataset_id != self.dataset_id
            or request.preflight.session_id != request.preflight.image_preflight.session_id
        ):
            raise InteropError("互操作提交请求与预检会话不一致")
        current = self.library.open_dataset(self.dataset_id).label_set
        if current.revision != request.preflight.label_set_revision:
            raise InteropError("标签集已发生变化，请重新执行导入预检")
        self._verify_import_preflight_sources(request.preflight)
        generated_names: dict[str, str] = {}
        if request.label_decisions or not (request.label_resolutions or request.new_labels):
            current, resolution_by_name, created_labels, mapped_count, readonly_count = (
                self._apply_label_decisions(current, request)
            )
            generated_names = {
                decision.external_name: next(
                    label.name for label in created_labels if label.alias == decision.external_name
                )
                for decision in request.label_decisions
                if decision.action == ExternalLabelAction.CREATE
            }
        else:
            created_labels = request.new_labels
            resolution_by_name = {
                resolution.external_name.casefold(): resolution.target_label_id
                for resolution in request.label_resolutions
            }
            mapped_count = sum(value is not None for value in resolution_by_name.values())
            readonly_count = sum(value is None for value in resolution_by_name.values())
        if created_labels:
            try:
                current = self.labels.apply_import_labels(
                    self.dataset_id,
                    created_labels,
                    expected_revision=current.revision,
                )
            except ManagedLabelError as error:
                raise InteropError(str(error)) from error
        report = XAnyImportReport(
            created_label_ids=[label.id for label in created_labels],
            mapped_label_count=mapped_count,
            readonly_label_count=readonly_count,
            generated_training_names=generated_names,
        )
        for reference in request.preflight.external_labels:
            if reference.matched_label_id and reference.name.casefold() not in resolution_by_name:
                resolution_by_name[reference.name.casefold()] = reference.matched_label_id
        current_by_name = {
            label.name.casefold(): label.id
            for label in current.labels
            if label.status == LabelStatus.ACTIVE
        }
        for reference in request.preflight.external_labels:
            resolution_by_name.setdefault(
                reference.name.casefold(),
                current_by_name.get(reference.name.casefold()),
            )
        missing_decisions = [
            item.image.id
            for item in request.preflight.items
            if item.image.requires_duplicate_decision
            and item.image.id not in request.duplicate_decisions
        ]
        if missing_decisions:
            raise InteropError("存在尚未决定的完全重复图片")
        keep_items = [
            item
            for item in request.preflight.items
            if not item.blocking_issues
            and (
                not item.image.requires_duplicate_decision
                or request.duplicate_decisions[item.image.id] == DuplicateDecision.KEEP
            )
        ]
        sequence = None
        if keep_items:
            sequence = self.samples.allocate_sequence(
                len(keep_items),
                start_at=self.library.open_dataset(
                    self.dataset_id
                ).dataset.configuration.naming_policy.start_index,
            )
        position = 0
        try:
            for index, item in enumerate(request.preflight.items, start=1):
                if cancelled and cancelled():
                    report.cancelled = True
                    break
                if progress:
                    progress(index - 1, len(request.preflight.items), item.image.original_filename)
                if item.blocking_issues:
                    report.failures[item.relative_image_path] = item.blocking_issues[0].message
                    continue
                if item.image.requires_duplicate_decision:
                    report.duplicate_item_ids.append(item.image.id)
                    if request.duplicate_decisions[item.image.id] == DuplicateDecision.SKIP:
                        report.skipped_item_ids.append(item.image.id)
                        continue
                if sequence is None:
                    raise InteropError("受管命名序号未初始化")
                try:
                    _verify_fingerprint(
                        item.image.source_path,
                        item.image_fingerprint,
                        request.preflight.source_directory,
                    )
                    if item.annotation_path and item.annotation_fingerprint:
                        _verify_fingerprint(
                            item.annotation_path,
                            item.annotation_fingerprint,
                            request.preflight.source_directory,
                        )
                    sample = self._commit_import_item(
                        item,
                        sequence + position,
                        current,
                        resolution_by_name,
                    )
                    position += 1
                    report.imported_sample_ids.append(sample.id)
                    if item.annotation_payload is None:
                        report.unannotated_sample_ids.append(sample.id)
                    report.compatibility_shape_count += item.compatibility_shape_count
                except (
                    OSError,
                    ValueError,
                    InteropError,
                    LabelMeError,
                    SampleRepositoryError,
                ) as error:
                    report.failures[item.relative_image_path] = str(error)
            if progress:
                progress(len(request.preflight.items), len(request.preflight.items), "")
        finally:
            try:
                self.images.discard_preflight(request.preflight.session_id)
            except (OSError, ImagePoolError) as error:
                report.failures["<cleanup>"] = str(error)
        self.library.synchronize_statistics(self.dataset_id)
        return report

    def _verify_import_preflight_sources(self, preflight: XAnyImportPreflight) -> None:
        """创建任何标签前一次性确认全部来源指纹，避免留下无来源标签。"""

        if preflight.labels_fingerprint is not None:
            _verify_fingerprint(
                preflight.source_directory / preflight.labels_fingerprint.relative_path,
                preflight.labels_fingerprint,
                preflight.source_directory,
            )
        for item in preflight.items:
            _verify_fingerprint(
                item.image.source_path,
                item.image_fingerprint,
                preflight.source_directory,
            )
            if item.annotation_path and item.annotation_fingerprint:
                _verify_fingerprint(
                    item.annotation_path,
                    item.annotation_fingerprint,
                    preflight.source_directory,
                )

    def _apply_label_decisions(
        self,
        current: LabelSet,
        request: XAnyImportCommitRequest,
    ) -> tuple[LabelSet, dict[str, str | None], tuple[Label, ...], int, int]:
        """把映射决定转换为一次标签集修订，未知标签默认安全新建。"""

        decisions = {item.external_name.casefold(): item for item in request.label_decisions}
        active_ids = {label.id for label in current.labels if label.status == LabelStatus.ACTIVE}
        used_class_ids = {label.class_id for label in current.labels}
        used_names = {label.name.casefold() for label in current.labels}
        staged = current.model_copy(deep=True)
        new_labels: list[Label] = []
        resolutions: dict[str, str | None] = {}
        mapped = 0
        readonly = 0
        for reference in request.preflight.external_labels:
            decision = decisions.get(reference.name.casefold())
            if decision is None:
                if reference.matched_label_id:
                    decision = ExternalLabelDecision(
                        reference.name, ExternalLabelAction.MAP, reference.matched_label_id
                    )
                else:
                    decision = ExternalLabelDecision(reference.name, ExternalLabelAction.CREATE)
            if decision.action == ExternalLabelAction.MAP:
                if decision.target_label_id not in active_ids:
                    raise InteropError(f"标签映射目标不可用: {reference.name}")
                resolutions[reference.name.casefold()] = decision.target_label_id
                mapped += 1
                continue
            if decision.action == ExternalLabelAction.PRESERVE_READONLY:
                resolutions[reference.name.casefold()] = None
                readonly += 1
                continue
            training_name = _safe_training_name(reference.name, used_names)
            used_names.add(training_name.casefold())
            class_id = next(
                value for value in range(len(used_class_ids) + 1) if value not in used_class_ids
            )
            used_class_ids.add(class_id)
            timestamp = utc_now()
            label = Label(
                id=new_id(),
                class_id=class_id,
                name=training_name,
                alias=reference.name,
                description="从 X-AnyLabeling 标签目录导入",
                color=self.labels.colors.allocate(staged),
                created_at=timestamp,
                modified_at=timestamp,
            )
            new_labels.append(label)
            staged = LabelSet.model_validate(
                staged.model_copy(update={"labels": [*staged.labels, label]})
            )
            active_ids.add(label.id)
            resolutions[reference.name.casefold()] = label.id
        return current, resolutions, tuple(new_labels), mapped, readonly

    def discard_import_preflight(self, preflight: XAnyImportPreflight) -> None:
        """放弃向导时只删除当前数据集内部的预检缓存。"""

        self.images.discard_preflight(preflight.session_id)

    def preflight_export(self, request: XAnyExportRequest) -> XAnyExportPreflight:
        """固定导出样本并检查健康状态；此阶段不创建输出目录。"""

        if request.dataset_id != self.dataset_id:
            raise InteropError("导出请求不属于当前数据集")
        target = request.output_directory
        if target.exists():
            raise InteropError("导出目录必须尚不存在")
        parent = target.parent.resolve(strict=True)
        if not parent.is_dir() or target.is_symlink():
            raise InteropError("导出目标父目录不可用")
        if request.scope == XAnyExportScope.ALL:
            samples = self.samples.all_active_samples()
        else:
            selected: list[DatasetSample] = []
            for sample_id in dict.fromkeys(request.sample_ids):
                sample = self.samples.get_sample(sample_id)
                if sample is None:
                    raise InteropError(f"导出样本不存在: {sample_id}")
                selected.append(sample)
            samples = tuple(selected)
        issues: list[InteropIssue] = []
        estimated = 0
        annotated = 0
        label_set = self.library.open_dataset(self.dataset_id).label_set
        for sample in samples:
            try:
                image_path = self.samples.resolve_path(sample.image_path, "pool/images")
                if not image_path.is_file() or image_path.is_symlink():
                    raise InteropError("受管图片缺失或不安全")
                estimated += image_path.stat().st_size + 4096
                if sample.annotation_state in {
                    AnnotationState.CORRUPT,
                    AnnotationState.UNKNOWN_LABEL,
                    AnnotationState.RECOVERY_REQUIRED,
                }:
                    raise InteropError(f"标注状态阻止导出: {sample.annotation_state.value}")
                if sample.annotation_path:
                    annotation_path = self.samples.resolve_path(
                        sample.annotation_path, "pool/annotations"
                    )
                    if not annotation_path.is_file():
                        raise InteropError("索引引用的标注 JSON 缺失")
                    document = self.labelme.load(
                        annotation_path,
                        sample.id,
                        label_set,
                        sample.filename,
                        (sample.width, sample.height),
                    )
                    if _document_has_exportable_shapes(document):
                        estimated += annotation_path.stat().st_size
                        annotated += 1
            except (LabelMeError, OSError, SampleRepositoryError, InteropError) as error:
                issues.append(
                    InteropIssue(
                        InteropIssueSeverity.ERROR,
                        "sample_not_exportable",
                        str(error),
                        sample.filename,
                    )
                )
        return XAnyExportPreflight(
            request,
            tuple(sample.id for sample in samples),
            tuple(issues),
            estimated,
            annotated,
            len(samples) - annotated,
        )

    def export(
        self,
        preflight: XAnyExportPreflight,
        *,
        progress: Callable[[int, int, str], None] | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> XAnyExportReport:
        """在同卷临时目录完整生成和复验后一次发布。"""

        if not preflight.can_export:
            raise InteropError("导出预检包含阻断问题")
        target = preflight.request.output_directory
        if target.exists():
            raise InteropError("导出目录已在预检后出现，请选择新的目录")
        temporary = Path(tempfile.mkdtemp(prefix=f".{target.name}-", dir=target.parent))
        report = XAnyExportReport(target)
        label_set = self.library.open_dataset(self.dataset_id).label_set
        compatibility_names: set[str] = set()
        try:
            for index, sample_id in enumerate(preflight.sample_ids, start=1):
                if cancelled and cancelled():
                    report.cancelled = True
                    return report
                sample = self.samples.get_sample(sample_id)
                if sample is None:
                    raise InteropError(f"导出过程中样本消失: {sample_id}")
                if progress:
                    progress(index - 1, len(preflight.sample_ids), sample.filename)
                image_source = self.samples.resolve_path(sample.image_path, "pool/images")
                image_target = temporary / sample.filename
                shutil.copy2(image_source, image_target)
                with Image.open(image_target) as opened:
                    opened.load()
                    width, height = opened.size
                document = self._load_export_document(sample, label_set, width, height)
                document.image_filename = sample.filename
                document.image_width = width
                document.image_height = height
                report.image_count += 1
                report.rectangle_count += len(document.rectangles)
                if _document_has_exportable_shapes(document):
                    payload = self.labelme.export_payload(document, label_set)
                    json_target = temporary / f"{Path(sample.filename).stem}.json"
                    _write_json_file(json_target, payload)
                    _validate_export_pair(image_target, json_target, label_set, self.labelme)
                    report.json_count += 1
                else:
                    report.empty_annotation_count += 1
                for shape in document.unsupported_shapes:
                    shape_type = str(shape.get("shape_type") or "unknown")
                    report.compatibility_shapes[shape_type] = (
                        report.compatibility_shapes.get(shape_type, 0) + 1
                    )
                    name = str(shape.get("label", "")).strip()
                    if name:
                        compatibility_names.add(name)
            _write_labels_file(temporary / "labels.txt", label_set, compatibility_names)
            _validate_export_directory(
                temporary,
                expected_image_count=len(preflight.sample_ids),
                expected_json_count=report.json_count,
            )
            if progress:
                progress(len(preflight.sample_ids), len(preflight.sample_ids), "")
            os.replace(temporary, target)
            return report
        except Exception:
            raise
        finally:
            if temporary.exists():
                try:
                    shutil.rmtree(temporary)
                except OSError as error:
                    report.warnings.append(f"临时目录清理失败: {error}")

    def _commit_import_item(
        self,
        item: PreparedInteropItem,
        sequence: int,
        label_set: LabelSet,
        resolutions: Mapping[str, str | None],
    ) -> DatasetSample:
        self.images._validate_staged_path(item.image.staged_image_path)
        self.images._validate_staged_path(item.image.staged_thumbnail_path)
        policy = self.library.open_dataset(self.dataset_id).dataset.configuration.naming_policy
        filename = policy.filename_for(sequence)
        target_image = self.paths.images / filename
        while target_image.exists():
            sequence = self.samples.allocate_sequence(1, start_at=sequence + 1)
            filename = policy.filename_for(sequence)
            target_image = self.paths.images / filename
        thumbnail_name = f"{item.image.id}-{item.image.content_hash[:12]}.png"
        target_thumbnail = self.paths.thumbnails / thumbnail_name
        target_annotation = self.paths.annotations / f"{Path(filename).stem}.json"
        document = None
        staged_annotation = item.image.staged_image_path.with_suffix(".json")
        annotation_sha = ""
        annotation_relative = ""
        review_status = None
        if item.annotation_payload is not None:
            document = self.labelme.from_external_payload(
                copy.deepcopy(item.annotation_payload),
                item.image.id,
                label_set,
                filename,
                (item.image.width, item.image.height),
                resolutions,
            )
            document.image_filename = filename
            document.image_width = item.image.width
            document.image_height = item.image.height
            annotation_sha = self.labelme.save(staged_annotation, document, label_set)
            annotation_relative = target_annotation.relative_to(self.paths.root).as_posix()
            review_status = ReviewStatus.COMPLETED
        sample = DatasetSample(
            id=item.image.id,
            dataset_id=self.dataset_id,
            filename=filename,
            original_filename=item.image.original_filename,
            image_path=target_image.relative_to(self.paths.root).as_posix(),
            annotation_path=annotation_relative,
            width=item.image.width,
            height=item.image.height,
            image_mode=item.image.image_mode,
            content_hash=item.image.content_hash,
            file_hash=item.image.file_hash,
            perceptual_hash=item.image.perceptual_hash,
            review_status=review_status,
            annotation_count=len(document.rectangles) if document else 0,
            annotation_state=AnnotationState.READY if document else AnnotationState.MISSING,
            annotation_version=document.document_version if document else 0,
            annotation_sha256=annotation_sha,
            annotation_updated_at=utc_now().isoformat() if document else "",
            health=SampleHealth.READY,
            thumbnail_state=ThumbnailState.READY,
            thumbnail_path=target_thumbnail.relative_to(self.paths.root).as_posix(),
            duplicate_group_id=(
                item.image.content_hash if item.image.requires_duplicate_decision else None
            ),
            imported_at=utc_now().isoformat(),
        )
        operation_id = new_id()
        timestamp = utc_now().isoformat()
        payload = {
            "sample": sample.model_dump(mode="json"),
            "staged_image": item.image.staged_image_path.relative_to(self.paths.root).as_posix(),
            "staged_thumbnail": item.image.staged_thumbnail_path.relative_to(
                self.paths.root
            ).as_posix(),
            "staged_annotation": (
                staged_annotation.relative_to(self.paths.root).as_posix() if document else ""
            ),
            "target_image": sample.image_path,
            "target_thumbnail": sample.thumbnail_path,
            "target_annotation": sample.annotation_path,
            "shape_labels": [
                [shape.id, shape.label_id] for shape in (document.rectangles if document else [])
            ],
        }
        self.samples.register_operation(
            ManagedOperation(
                operation_id,
                self.dataset_id,
                "xany_import",
                "prepared",
                payload,
                timestamp,
                timestamp,
            )
        )
        published: list[tuple[Path, Path]] = []
        try:
            publish_pairs = [
                (item.image.staged_image_path, target_image),
                (item.image.staged_thumbnail_path, target_thumbnail),
            ]
            if document is not None:
                publish_pairs.append((staged_annotation, target_annotation))
            for staged, target in publish_pairs:
                os.replace(staged, target)
                published.append((target, staged))
            self.samples.update_operation(operation_id, "published", payload)
            shape_labels = tuple(
                (shape.id, shape.label_id) for shape in (document.rectangles if document else [])
            )
            self.samples.add_sample_with_annotation(sample, shape_labels)
            self.samples.update_operation(operation_id, "indexed", payload)
        except Exception as error:
            rollback_errors: list[str] = []
            for target, staged in reversed(published):
                try:
                    if target.exists() and not staged.exists():
                        os.replace(target, staged)
                except OSError as rollback_error:
                    rollback_errors.append(str(rollback_error))
            if not rollback_errors:
                try:
                    self.samples.finish_operation(operation_id)
                except SampleRepositoryError as cleanup_error:
                    rollback_errors.append(str(cleanup_error))
            suffix = f"；恢复失败: {'；'.join(rollback_errors)}" if rollback_errors else ""
            raise InteropError(f"图片与标注提交失败: {error}{suffix}") from error
        with suppress(SampleRepositoryError):
            self.samples.finish_operation(operation_id)
        return sample

    def _load_export_document(
        self,
        sample: DatasetSample,
        label_set: LabelSet,
        width: int,
        height: int,
    ) -> AnnotationDocument:
        if not sample.annotation_path:
            return AnnotationDocument(
                sample_id=sample.id,
                image_filename=sample.filename,
                image_width=width,
                image_height=height,
            )
        path = self.samples.resolve_path(sample.annotation_path, "pool/annotations")
        return self.labelme.load(path, sample.id, label_set, sample.filename, (width, height))


def _discover_exchange_files(
    root: Path,
) -> tuple[tuple[Path, ...], tuple[Path, ...], tuple[InteropIssue, ...]]:
    images: list[Path] = []
    json_files: list[Path] = []
    issues: list[InteropIssue] = []
    supported = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff"}
    for current_root, directories, files in os.walk(root, followlinks=False):
        current = Path(current_root)
        retained: list[str] = []
        for name in directories:
            path = current / name
            if path.is_symlink():
                issues.append(
                    InteropIssue(
                        InteropIssueSeverity.WARNING,
                        "symlink_skipped",
                        "符号链接目录未被跟随",
                        _safe_relative(path, root),
                    )
                )
            else:
                retained.append(name)
        directories[:] = retained
        for name in files:
            path = current / name
            if path.is_symlink():
                issues.append(
                    InteropIssue(
                        InteropIssueSeverity.WARNING,
                        "symlink_skipped",
                        "符号链接文件未被读取",
                        _safe_relative(path, root),
                    )
                )
                continue
            suffix = path.suffix.lower()
            if suffix in supported:
                images.append(path)
            elif suffix == ".json":
                json_files.append(path)

    def key(path: Path) -> str:
        return path.relative_to(root).as_posix().casefold()

    return tuple(sorted(images, key=key)), tuple(sorted(json_files, key=key)), tuple(issues)


def _read_labels_file(path: Path) -> tuple[str, ...]:
    """按声明顺序读取 UTF-8/BOM 标签，空行和大小写重复项不进入映射。"""

    names: list[str] = []
    seen: set[str] = set()
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        name = line.strip()
        if not name or name.casefold() in seen:
            continue
        seen.add(name.casefold())
        names.append(name)
    return tuple(names)


def _safe_training_name(external_name: str, used_casefold_names: set[str]) -> str:
    """保留合法训练名；非法文本生成可重复且不会覆盖既有标签的 ASCII 名称。"""

    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", external_name):
        base = external_name
    else:
        normalized = unicodedata.normalize("NFKD", external_name)
        ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
        base = re.sub(r"[^A-Za-z0-9_.-]+", "_", ascii_text).strip("._-")
        if not base or not base[0].isalnum():
            digest = hashlib.sha256(external_name.encode("utf-8")).hexdigest()
            base = f"label_{digest[:10]}"
    if base.casefold() not in used_casefold_names:
        return base
    digest = hashlib.sha256(external_name.encode("utf-8")).hexdigest()
    candidate = f"{base}_{digest[:6]}"
    if candidate.casefold() not in used_casefold_names:
        return candidate
    suffix = 2
    while f"{candidate}_{suffix}".casefold() in used_casefold_names:
        suffix += 1
    return f"{candidate}_{suffix}"


def _read_external_payload(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise InteropError("LabelMe JSON 根节点必须是对象")
    if not isinstance(payload.get("shapes", []), list):
        raise InteropError("LabelMe shapes 必须是数组")
    return payload


def _validate_external_payload(
    payload: dict[str, Any],
    root: Path,
    annotation_path: Path,
    image_path: Path,
    image_size: tuple[int, int],
) -> None:
    raw_image_path = str(payload.get("imagePath") or image_path.name)
    if "\x00" in raw_image_path:
        raise InteropError("imagePath 包含无效空字节")
    posix = PurePosixPath(raw_image_path.replace("\\", "/"))
    windows = PureWindowsPath(raw_image_path)
    if posix.is_absolute() or windows.is_absolute() or windows.drive or ".." in posix.parts:
        raise InteropError("imagePath 必须是目录内安全相对路径")
    referenced = annotation_path.parent.joinpath(*posix.parts).resolve(strict=False)
    try:
        referenced.relative_to(root)
    except ValueError as error:
        raise InteropError("imagePath 越过所选目录") from error
    if referenced != image_path.resolve(strict=True):
        raise InteropError("imagePath 与同名配对图片不一致")
    width = int(payload.get("imageWidth") or image_size[0])
    height = int(payload.get("imageHeight") or image_size[1])
    if (width, height) != image_size:
        raise InteropError("JSON 图片尺寸与规范化图片尺寸不一致")
    image_data = payload.get("imageData")
    if image_data not in {None, ""}:
        if not isinstance(image_data, str):
            raise InteropError("imageData 必须为空或 Base64 文本")
        encoded = image_data.split(",", 1)[1] if image_data.startswith("data:") else image_data
        try:
            decoded = base64.b64decode(encoded, validate=True)
            with Image.open(BytesIO(decoded)) as embedded:
                embedded.load()
                embedded_size = ImageOps.exif_transpose(embedded).size
        except Exception as error:
            raise InteropError(f"imageData 无法解码: {error}") from error
        if embedded_size != image_size:
            raise InteropError("imageData 尺寸与配对图片不一致")


def _fingerprint(path: Path, root: Path) -> SourceFingerprint:
    if path.is_symlink():
        raise InteropError("来源文件不能是符号链接")
    resolved = path.resolve(strict=True)
    try:
        relative = resolved.relative_to(root).as_posix()
    except ValueError as error:
        raise InteropError("来源文件越过所选目录") from error
    stat = resolved.stat()
    return SourceFingerprint(relative, stat.st_size, stat.st_mtime_ns, _sha256(resolved))


def _verify_fingerprint(path: Path, expected: SourceFingerprint, root: Path) -> None:
    if _fingerprint(path, root) != expected:
        raise InteropError("来源文件在预检后发生变化，请重新预检")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_relative(path: Path, root: Path) -> str:
    try:
        return path.resolve(strict=False).relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.name


def _write_json_file(path: Path, payload: dict[str, Any]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def _validate_export_pair(
    image_path: Path,
    json_path: Path,
    label_set: LabelSet,
    repository: LabelMeRepository,
) -> None:
    with Image.open(image_path) as image:
        image.load()
        size = image.size
    payload = _read_external_payload(json_path)
    if payload.get("imagePath") != image_path.name:
        raise InteropError("导出 JSON 的 imagePath 与图片文件名不一致")
    if (int(payload.get("imageWidth") or 0), int(payload.get("imageHeight") or 0)) != size:
        raise InteropError("导出 JSON 图片尺寸不正确")
    resolutions = {
        label.name.casefold(): label.id
        for label in label_set.labels
        if label.status == LabelStatus.ACTIVE
    }
    repository.from_external_payload(
        payload,
        new_id(),
        label_set,
        image_path.name,
        size,
        resolutions,
    )
    if "datumdock_" in json.dumps(payload, ensure_ascii=False):
        raise InteropError("导出 JSON 泄漏了 DatumDock 私有字段")


def _write_labels_file(path: Path, label_set: LabelSet, compatibility_names: Iterable[str]) -> None:
    names: list[str] = []
    seen: set[str] = set()
    for label in sorted(
        (item for item in label_set.labels if item.status == LabelStatus.ACTIVE),
        key=lambda item: (item.class_id, item.name.casefold()),
    ):
        if label.name.casefold() not in seen:
            seen.add(label.name.casefold())
            names.append(label.name)
    for name in sorted(compatibility_names, key=str.casefold):
        if name.casefold() not in seen:
            seen.add(name.casefold())
            names.append(name)
    path.write_text("\n".join(names) + ("\n" if names else ""), encoding="utf-8")


def _document_has_exportable_shapes(document: AnnotationDocument) -> bool:
    """仅在至少存在一个可编辑或兼容 shape 时生成 LabelMe JSON。"""

    return bool(document.rectangles or document.unsupported_shapes)


def _validate_export_directory(
    root: Path,
    *,
    expected_image_count: int,
    expected_json_count: int,
) -> None:
    images = tuple(root.glob("*.png"))
    json_files = tuple(root.glob("*.json"))
    if len(images) != expected_image_count or len(json_files) != expected_json_count:
        raise InteropError("交换目录图片或标注 JSON 数量不完整")
    if not (root / "labels.txt").is_file():
        raise InteropError("交换目录缺少 labels.txt")
    if not {path.stem for path in json_files} <= {path.stem for path in images}:
        raise InteropError("交换目录存在没有同名图片的 JSON")
