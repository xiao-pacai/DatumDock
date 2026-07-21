"""把真实内部资料库转换为步骤一 UI 所需的只读快照。"""

from __future__ import annotations

import logging
from pathlib import Path
from uuid import UUID

from datumdock.domain.models import (
    AnnotationState,
    AppSettings,
    LabelStatus,
    NamingPolicy,
    ReviewStatus,
    SampleSort,
    SimilarityStatus,
    utc_now,
)
from datumdock.services.annotations import (
    AnnotationAutosaveService,
    AnnotationLoadResult,
    AnnotationSaveRequest,
    AnnotationService,
    AutosaveState,
)
from datumdock.services.dataset_deletion import (
    DatasetDeletionPreflight,
    DatasetDeletionRequest,
    DatasetDeletionService,
)
from datumdock.services.dataset_library import (
    DatasetLibraryService,
    DatasetLibraryServiceError,
    DatasetNotFoundError,
    DatasetUnavailableError,
    DuplicateDatasetNameError,
    InvalidDatasetNameError,
    ManagedDatasetRecord,
    format_modified_time,
)
from datumdock.services.image_pool import (
    DuplicateDecision,
    ImageAsset,
    ImageImportPreflight,
    ImageImportPreflightRequest,
    ImageImportService,
    ImagePoolError,
    ManagedImageService,
    ThumbnailAsset,
    ThumbnailService,
)
from datumdock.services.library_repository import resolve_data_root
from datumdock.services.managed_interop import (
    ManagedRectangleRepairPreflight,
    XAnyExportPreflight,
    XAnyExportRequest,
    XAnyImportCommitRequest,
    XAnyImportPreflight,
    XAnyImportPreflightRequest,
    XAnyLabelingInteropService,
)
from datumdock.services.managed_labels import (
    LabelChangePreview,
    LabelInspectionService,
    LabelSetService,
    ManagedLabelError,
    ManagedLabelMigrationService,
)
from datumdock.services.recent_labels import RecentLabelTracker
from datumdock.services.sample_governance import (
    RenamePlan,
    SampleGovernanceError,
    SampleRenameService,
    TrashService,
)
from datumdock.services.sample_repository import (
    DatasetSampleRepository,
    SamplePage,
    SampleQuery,
    SampleRepositoryError,
    SimilarityGroup,
    TrashItem,
)
from datumdock.services.settings import AppSettingsError, AppSettingsRepository
from datumdock.services.tasks import BackgroundTaskService, TaskSnapshot, TaskState
from datumdock.ui.prototype_models import (
    CommandStatus,
    DatasetCardViewData,
    DatasetHealth,
    HomeSnapshot,
    LabelViewData,
    UiCommand,
    UiCommandResult,
    WorkspaceSnapshot,
)

logger = logging.getLogger(__name__)


class ManagedDatasetGateway:
    """普通模式的唯一 UI 业务入口，页面不直接访问磁盘或 Repository。"""

    preview_mode = False

    def __init__(self, service: DatasetLibraryService) -> None:
        self.service = service
        self.settings_repository = AppSettingsRepository(service.root)
        self.settings = self.settings_repository.load()
        self.tasks = BackgroundTaskService()
        self._preflights: dict[str, ImageImportPreflight] = {}
        self._xany_import_preflights: dict[str, XAnyImportPreflight] = {}
        self._dataset_deletion_preflights: dict[str, DatasetDeletionPreflight] = {}
        self._annotation_autosaves: dict[str, AnnotationAutosaveService] = {}
        self.recent_labels = RecentLabelTracker()

    @classmethod
    def from_default_root(cls) -> ManagedDatasetGateway:
        """从当前 Windows 用户内部目录装配真实资料库服务。"""

        return cls(DatasetLibraryService(resolve_data_root()))

    def home_snapshot(self) -> HomeSnapshot:
        """返回包含健康、归档和损坏项的真实主页快照。"""

        records = self.service.list_datasets(include_archived=True)
        return HomeSnapshot(tuple(self._card(record) for record in records), 0)

    def workspace_snapshot(self, dataset_id: str | None = None) -> WorkspaceSnapshot | None:
        """打开真实数据集上下文；图片内容始终通过分页接口另行请求。"""

        records = self.service.list_datasets(include_archived=False)
        healthy = [record for record in records if record.healthy and record.bundle is not None]
        if dataset_id is None:
            record = healthy[0] if healthy else None
        else:
            record = next((item for item in healthy if item.entry.id == dataset_id), None)
        if record is None or record.bundle is None:
            return None
        available = tuple(self._card(item) for item in healthy)
        bundle = record.bundle
        try:
            usage = DatasetSampleRepository(
                self.service.dataset_repository.paths(bundle.dataset.id),
                bundle.dataset.id,
            ).label_usage_counts()
        except SampleRepositoryError:
            usage = {}
        labels = tuple(
            LabelViewData(
                id=label.id,
                class_id=label.class_id,
                name=label.name,
                alias=label.alias,
                description=label.description,
                synonyms=tuple(label.synonyms),
                color=label.color,
                usage_count=usage.get(label.id, (0, 0))[0],
                archived=label.status == LabelStatus.ARCHIVED,
            )
            for label in bundle.label_set.labels
        )
        return WorkspaceSnapshot(
            dataset=self._card(record),
            labels=labels,
            images=(),
            annotations_by_image={},
            models=(),
            available_datasets=available,
            label_set_revision=bundle.label_set.revision,
        )

    def query_samples(
        self,
        dataset_id: str,
        *,
        offset: int = 0,
        limit: int = 200,
        search: str = "",
        review_status: ReviewStatus | None = None,
        annotation_state: AnnotationState | None = None,
        has_annotations: bool | None = None,
        label_id: str | None = None,
        sort: SampleSort = SampleSort.FILENAME_ASC,
    ) -> SamplePage:
        """正式图片列表只读取 SQLite 当前页，不构造全量 UI 快照。"""

        _bundle, _paths, repository = self._media(dataset_id)
        return repository.query(
            SampleQuery(
                dataset_id=dataset_id,
                offset=offset,
                limit=limit,
                search=search,
                review_status=review_status,
                label_id=label_id,
                sort=sort,
                annotation_state=annotation_state,
                has_annotations=has_annotations,
            )
        )

    def list_labels(self, dataset_id: str, search: str = "", *, include_archived: bool = True):
        """返回当前数据集的真实标签，不读取其他数据集内容。"""

        return LabelSetService(self.service).list_labels(
            dataset_id,
            search=search,
            include_archived=include_archived,
        )

    def get_label_set(self, dataset_id: str):
        """返回独立标签集快照，供编辑对话框执行修订检查。"""

        return self.service.open_dataset(dataset_id).label_set.model_copy(deep=True)

    def add_label(self, dataset_id: str, **values):
        return LabelSetService(self.service).add_label(dataset_id, **values)

    def update_label_display(self, dataset_id: str, label_id: str, **values):
        return LabelSetService(self.service).update_display_fields(
            dataset_id,
            label_id,
            **values,
        )

    def set_label_status(
        self,
        dataset_id: str,
        label_id: str,
        status: LabelStatus,
        *,
        expected_revision: int,
    ):
        return LabelSetService(self.service).set_status(
            dataset_id,
            label_id,
            status,
            expected_revision=expected_revision,
        )

    def label_usages(self, dataset_id: str):
        """标签管理和检查页共享 SQLite 使用量。"""

        return LabelInspectionService(self.service).usages(dataset_id)

    def preview_label_change(
        self,
        dataset_id: str,
        label_id: str,
        *,
        name: str | None = None,
        class_id: int | None = None,
    ) -> LabelChangePreview:
        return LabelSetService(self.service).preview_change(
            dataset_id,
            label_id,
            name=name,
            class_id=class_id,
        )

    def apply_label_change(self, preview: LabelChangePreview):
        """执行已确认训练映射迁移，并返回真实迁移摘要。"""

        return ManagedLabelMigrationService(self.service).apply(preview)

    def load_annotation(self, dataset_id: str, sample_id: str) -> AnnotationLoadResult:
        """按需加载当前图片标注，损坏 JSON 由结果显式标记为只读。"""

        recovery = ManagedLabelMigrationService(self.service).recover_pending(dataset_id)
        if recovery.failed_sample_ids:
            raise ManagedLabelError("标签训练名迁移尚未安全恢复，请先查看数据集诊断")
        service = AnnotationService(self.service, dataset_id)
        service.recover_pending()
        result = service.load(sample_id)
        if result.checkpoint is not None:
            self._annotation_autosave(dataset_id).seed_checkpoint(result.checkpoint)
        return result

    def queue_annotation_save(self, request: AnnotationSaveRequest):
        """把不可变文档快照排入数据集级串行自动保存队列。"""

        return self._annotation_autosave(request.dataset_id).submit(request)

    def _annotation_autosave(self, dataset_id: str) -> AnnotationAutosaveService:
        autosave = self._annotation_autosaves.get(dataset_id)
        if autosave is None:
            autosave = AnnotationAutosaveService(AnnotationService(self.service, dataset_id))
            self._annotation_autosaves[dataset_id] = autosave
        return autosave

    def retry_annotation_save(self, dataset_id: str):
        autosave = self._annotation_autosaves.get(dataset_id)
        if autosave is None:
            raise ManagedLabelError("没有可重试的标注保存")
        return autosave.retry_latest()

    def annotation_save_state(self, dataset_id: str) -> tuple[AutosaveState, str]:
        autosave = self._annotation_autosaves.get(dataset_id)
        return (autosave.state, autosave.error) if autosave else (AutosaveState.IDLE, "")

    def annotation_save_failure(self, dataset_id: str):
        autosave = self._annotation_autosaves.get(dataset_id)
        return autosave.failure if autosave else None

    def remember_recent_label(self, dataset_id: str, label_id: str) -> None:
        active_ids = {label.id for label in self.list_labels(dataset_id, include_archived=False)}
        if label_id not in active_ids:
            self.recent_labels.clear(dataset_id)
            raise ManagedLabelError("最近使用标签已归档、删除或不属于当前数据集")
        self.recent_labels.remember(dataset_id, label_id)

    def recent_label(self, dataset_id: str) -> tuple[bool, str | None]:
        active_ids = {label.id for label in self.list_labels(dataset_id, include_archived=False)}
        return self.recent_labels.resolve(dataset_id, active_ids)

    def wait_annotation_save(self, dataset_id: str):
        autosave = self._annotation_autosaves.get(dataset_id)
        return autosave.wait_latest() if autosave else None

    def mark_review_completed(self, dataset_id: str, sample_id: str) -> ReviewStatus:
        """通过真实服务确认图片完成，不为零框图片创建 JSON。"""

        return AnnotationService(self.service, dataset_id).mark_review_completed(sample_id)

    def locate_sample(
        self,
        dataset_id: str,
        sample_id: str,
        *,
        search: str = "",
        review_status: ReviewStatus | None = None,
        label_id: str | None = None,
        sort: SampleSort = SampleSort.FILENAME_ASC,
    ) -> int | None:
        """返回样本在当前筛选中的位置，供标签检查跨页跳转。"""

        _bundle, _paths, repository = self._media(dataset_id)
        return repository.locate_sample(
            sample_id,
            SampleQuery(
                dataset_id=dataset_id,
                search=search,
                review_status=review_status,
                label_id=label_id,
                sort=sort,
            ),
        )

    def load_image(self, dataset_id: str, sample_id: str) -> ImageAsset:
        _bundle, paths, repository = self._media(dataset_id)
        return ManagedImageService(paths, repository).load(sample_id)

    def load_thumbnail(self, dataset_id: str, sample_id: str) -> ThumbnailAsset:
        _bundle, paths, repository = self._media(dataset_id)
        return ThumbnailService(paths, repository).load(sample_id)

    def get_sample(self, dataset_id: str, sample_id: str, *, include_trashed: bool = False):
        """管理页按稳定 ID 获取轻量样本，不解析文件路径。"""

        _bundle, _paths, repository = self._media(dataset_id)
        return repository.get_sample(sample_id, include_trashed=include_trashed)

    def list_similarity_groups(
        self,
        dataset_id: str,
        status: SimilarityStatus | None = None,
    ) -> tuple[SimilarityGroup, ...]:
        _bundle, _paths, repository = self._media(dataset_id)
        return repository.list_similarity_groups(status)

    def list_trash_items(self, dataset_id: str) -> tuple[TrashItem, ...]:
        bundle, paths, repository = self._media(dataset_id)
        return TrashService(paths, bundle.dataset, repository).list_items()

    def start_import_preflight(self, dataset_id: str, source_paths: tuple[Path, ...]) -> str:
        """转码、哈希和缩略图全部在线程池执行。"""

        bundle, paths, repository = self._media(dataset_id)
        importer = ImageImportService(paths, bundle.dataset, repository)

        def work(context):
            context.phase("preflight")
            result = importer.preflight(
                ImageImportPreflightRequest(dataset_id, source_paths),
                progress=context.progress,
                cancelled=context.cancelled,
            )
            for source, error in result.failures.items():
                context.add_error(f"{source}: {error}")
            self._preflights[result.session_id] = result
            return result

        return self.tasks.start(dataset_id, "image_import_preflight", work)

    def start_import_commit(
        self,
        dataset_id: str,
        session_id: str,
        decisions: dict[str, DuplicateDecision],
    ) -> str:
        """每张图片独立提交；取消后已完成项继续有效。"""

        try:
            preflight = self._preflights.pop(session_id)
        except KeyError as error:
            raise ImagePoolError("导入预检会话不存在") from error
        bundle, paths, repository = self._media(dataset_id)
        importer = ImageImportService(paths, bundle.dataset, repository)

        def work(context):
            context.phase("commit")
            report = importer.commit(
                preflight,
                decisions,
                progress=context.progress,
                cancelled=context.cancelled,
            )
            for source, error in report.failures.items():
                context.add_error(f"{source}: {error}")
            self.service.synchronize_statistics(dataset_id)
            return report

        return self.tasks.start(dataset_id, "image_import_commit", work)

    def load_prepared_image(
        self,
        dataset_id: str,
        session_id: str,
        item_id: str,
    ) -> bytes:
        """重复比较只返回内部临时 PNG 字节，不暴露暂存路径。"""

        preflight = self._preflights.get(session_id)
        if preflight is None:
            raise ImagePoolError("导入预检会话不存在")
        item = next((candidate for candidate in preflight.items if candidate.id == item_id), None)
        if item is None:
            raise ImagePoolError("导入预检项不存在")
        bundle, paths, repository = self._media(dataset_id)
        return ImageImportService(paths, bundle.dataset, repository).load_prepared_image(item)

    def discard_import_preflight(self, dataset_id: str, session_id: str) -> None:
        """用户取消重复决策时清理内部临时转码，不触碰外部来源。"""

        preflight = self._preflights.pop(session_id, None)
        if preflight is None:
            return
        bundle, paths, repository = self._media(dataset_id)
        ImageImportService(paths, bundle.dataset, repository).discard_preflight(session_id)

    def start_xany_import_preflight(
        self,
        dataset_id: str,
        source_directory: Path,
    ) -> str:
        """在线程池中扫描外部交换目录，预检阶段不登记任何样本。"""

        interop = XAnyLabelingInteropService(self.service, dataset_id)

        def work(context):
            context.phase("xany_import_preflight")
            result = interop.preflight_import(
                XAnyImportPreflightRequest(dataset_id, source_directory),
                progress=context.progress,
                cancelled=context.cancelled,
            )
            self._xany_import_preflights[result.session_id] = result
            return result

        return self.tasks.start(dataset_id, "xany_import_preflight", work)

    def start_xany_import_commit(
        self,
        request: XAnyImportCommitRequest,
    ) -> str:
        """按预检会话提交交换图片和标注，并保留文件级部分成功报告。"""

        preflight = self._xany_import_preflights.pop(request.preflight.session_id, None)
        if preflight is None or preflight != request.preflight:
            raise ImagePoolError("X-AnyLabeling 导入预检会话不存在或已变化")
        interop = XAnyLabelingInteropService(self.service, request.dataset_id)

        def work(context):
            context.phase("xany_import_commit")
            report = interop.commit_import(
                request,
                progress=context.progress,
                cancelled=context.cancelled,
            )
            for source, error in report.failures.items():
                context.add_error(f"{source}: {error}")
            return report

        return self.tasks.start(request.dataset_id, "xany_import_commit", work)

    def discard_xany_import_preflight(self, dataset_id: str, session_id: str) -> None:
        """取消交换导入时只清理当前数据集内部暂存文件。"""

        preflight = self._xany_import_preflights.pop(session_id, None)
        if preflight is None:
            return
        XAnyLabelingInteropService(self.service, dataset_id).discard_import_preflight(preflight)

    def load_xany_prepared_image(
        self,
        dataset_id: str,
        session_id: str,
        item_id: str,
    ) -> bytes:
        """重复对比只返回预检 PNG 字节，不向页面公开内部路径。"""

        preflight = self._xany_import_preflights.get(session_id)
        if preflight is None:
            raise ImagePoolError("X-AnyLabeling 导入预检会话不存在")
        item = next((value for value in preflight.items if value.image.id == item_id), None)
        if item is None:
            raise ImagePoolError("X-AnyLabeling 导入预检项不存在")
        bundle, paths, repository = self._media(dataset_id)
        return ImageImportService(paths, bundle.dataset, repository).load_prepared_image(item.image)

    def start_xany_export_preflight(self, request: XAnyExportRequest) -> str:
        """固定导出样本范围并检查受管文件健康状态。"""

        interop = XAnyLabelingInteropService(self.service, request.dataset_id)

        def work(context):
            context.phase("xany_export_preflight")
            result = interop.preflight_export(request)
            context.progress(len(result.sample_ids), len(result.sample_ids), "")
            return result

        return self.tasks.start(request.dataset_id, "xany_export_preflight", work)

    def start_xany_export_commit(
        self,
        dataset_id: str,
        preflight_task_id: str,
    ) -> str:
        """使用预检快照生成并原子发布独立交换目录。"""

        result = self.tasks.result(preflight_task_id)
        if not isinstance(result, XAnyExportPreflight):
            raise ImagePoolError("X-AnyLabeling 导出预检不存在")
        preflight = result
        interop = XAnyLabelingInteropService(self.service, dataset_id)

        def work(context):
            context.phase("xany_export_commit")
            return interop.export(
                preflight,
                progress=context.progress,
                cancelled=context.cancelled,
            )

        return self.tasks.start(dataset_id, "xany_export_commit", work)

    def start_rectangle_repair_preflight(self, dataset_id: str) -> str:
        """后台只读检查当前数据集已导入的 X-AnyLabeling 四点矩形。"""

        interop = XAnyLabelingInteropService(self.service, dataset_id)

        def work(context):
            context.phase("xany_rectangle_repair_preflight")
            return interop.preflight_managed_rectangle_repair(
                progress=context.progress,
                cancelled=context.cancelled,
            )

        return self.tasks.start(dataset_id, "xany_rectangle_repair_preflight", work)

    def start_rectangle_repair_commit(
        self,
        dataset_id: str,
        preflight_task_id: str,
    ) -> str:
        """确认后逐样本执行恢复型修复，保存失败状态会阻止并发维护写入。"""

        preflight = self.tasks.result(preflight_task_id)
        if not isinstance(preflight, ManagedRectangleRepairPreflight):
            raise ImagePoolError("四点矩形修复预检不存在")
        autosave = self._annotation_autosaves.get(dataset_id)
        if autosave is not None and autosave.state in {AutosaveState.SAVING, AutosaveState.FAILED}:
            raise ImagePoolError("当前数据集仍有保存中或保存失败的标注，请先处理")
        blocking_tasks = [
            item
            for item in self.tasks.active_snapshots()
            if item.dataset_id == dataset_id
            and item.kind
            in {
                "image_import_commit",
                "xany_import_commit",
                "sample_rename",
                "sample_delete",
                "dataset_delete",
            }
        ]
        if blocking_tasks:
            raise ImagePoolError("当前数据集存在写入任务，请等待完成后再修复")
        interop = XAnyLabelingInteropService(self.service, dataset_id)

        def work(context):
            context.phase("xany_rectangle_repair_commit")
            report = interop.repair_managed_rectangles(
                preflight,
                progress=context.progress,
                cancelled=context.cancelled,
            )
            for filename, message in report.failures.items():
                context.add_error(f"{filename}: {message}")
            return report

        return self.tasks.start(dataset_id, "xany_rectangle_repair_commit", work)

    def task_snapshot(self, task_id: str) -> TaskSnapshot:
        return self.tasks.snapshot(task_id)

    def task_snapshots(self) -> tuple[TaskSnapshot, ...]:
        return self.tasks.snapshots()

    def task_result(self, task_id: str) -> object:
        return self.tasks.result(task_id)

    def cancel_task(self, task_id: str) -> bool:
        return self.tasks.cancel(task_id)

    def start_dataset_deletion_preflight(self, dataset_id: str) -> str:
        """后台统计删除影响；预检只读且绑定当前运行时阻塞状态。"""

        blockers = self._dataset_deletion_runtime_blockers(dataset_id)
        deletion = DatasetDeletionService(self.service)

        def work(context):
            context.phase("dataset_deletion_preflight")
            result = deletion.preflight(dataset_id, runtime_blockers=blockers)
            self._dataset_deletion_preflights[result.id] = result
            context.progress(result.impact.total_file_count, result.impact.total_file_count)
            return result

        return self.tasks.start(dataset_id, "dataset_deletion_preflight", work)

    def start_dataset_deletion(self, request: DatasetDeletionRequest) -> str:
        """重新检查运行时写入者后提交不可取消的同卷暂存删除。"""

        current = self._dataset_deletion_preflights.pop(request.preflight.id, None)
        if current is None or current != request.preflight:
            raise DatasetLibraryServiceError("整数据集删除预检不存在或已变化")
        blockers = self._dataset_deletion_runtime_blockers(request.preflight.dataset_id)
        if blockers:
            raise DatasetLibraryServiceError("；".join(blockers))
        deletion = DatasetDeletionService(self.service)

        def work(context):
            context.phase("dataset_deletion_commit")
            # 删除提交从移动 UUID 目录开始就是不可取消原子步骤，关闭应用也必须等待。
            return deletion.delete(request)

        return self.tasks.start(request.preflight.dataset_id, "dataset_deletion_commit", work)

    def discard_dataset_deletion_preflight(self, preflight_id: str) -> None:
        """关闭确认页时仅丢弃只读预检快照。"""

        self._dataset_deletion_preflights.pop(preflight_id, None)

    def _dataset_deletion_runtime_blockers(self, dataset_id: str) -> tuple[str, ...]:
        blockers: list[str] = []
        autosave = self._annotation_autosaves.get(dataset_id)
        if autosave is not None and autosave.state in {AutosaveState.SAVING, AutosaveState.FAILED}:
            blockers.append("当前数据集仍有正在保存或保存失败的标注")
        for snapshot in self.tasks.active_snapshots():
            if snapshot.dataset_id != dataset_id:
                continue
            if snapshot.kind == "dataset_deletion_preflight":
                continue
            if snapshot.state in {TaskState.QUEUED, TaskState.RUNNING}:
                blockers.append(f"当前数据集仍有写入型后台任务：{snapshot.kind}")
        return tuple(dict.fromkeys(blockers))

    def preview_rename(
        self,
        dataset_id: str,
        policy: NamingPolicy,
        sample_ids: tuple[str, ...] | None = None,
    ) -> RenamePlan:
        bundle, paths, repository = self._media(dataset_id)
        return SampleRenameService(paths, bundle.dataset, repository).preview(
            policy, sample_ids=sample_ids
        )

    def close(self) -> None:
        """关闭应用时等待当前单样本原子步骤安全结束。"""

        for autosave in self._annotation_autosaves.values():
            autosave.close()
        self._annotation_autosaves.clear()
        self.tasks.shutdown(wait=True)

    def dispatch(self, command: UiCommand) -> UiCommandResult:
        """执行步骤二允许的元数据操作，其余入口明确返回未接入。"""

        try:
            if command.action_id == "dataset.create":
                bundle = self.service.create_dataset(
                    str(command.payload.get("name", "")),
                    str(command.payload.get("description", "")),
                )
                return UiCommandResult(
                    CommandStatus.APPLIED,
                    "toast.dataset_created",
                    affected_id=bundle.dataset.id,
                )
            if command.action_id == "dataset.create_from_template":
                bundle = self.service.create_dataset(
                    str(command.payload.get("name", "")),
                    str(command.payload.get("description", "")),
                    source_dataset_id=str(command.payload.get("source_dataset_id", "")),
                )
                return UiCommandResult(
                    CommandStatus.APPLIED,
                    "toast.dataset_created",
                    affected_id=bundle.dataset.id,
                )
            if command.action_id == "dataset.rename":
                bundle = self.service.rename_dataset(
                    str(command.payload.get("dataset_id", "")),
                    str(command.payload.get("name", "")),
                )
                return UiCommandResult(
                    CommandStatus.APPLIED,
                    "toast.dataset_renamed",
                    affected_id=bundle.dataset.id,
                )
            if command.action_id == "dataset.archive":
                bundle = self.service.archive_dataset(str(command.payload.get("dataset_id", "")))
                return UiCommandResult(
                    CommandStatus.APPLIED,
                    "toast.dataset_archived",
                    affected_id=bundle.dataset.id,
                )
            if command.action_id == "dataset.restore":
                bundle = self.service.restore_dataset(str(command.payload.get("dataset_id", "")))
                return UiCommandResult(
                    CommandStatus.APPLIED,
                    "toast.dataset_restored",
                    affected_id=bundle.dataset.id,
                )
            if command.action_id == "settings.update":
                updates = dict(command.payload)
                settings = self.settings.model_copy(update=updates)
                settings = AppSettings.model_validate(settings.model_dump(mode="json"))
                self.settings_repository.save(settings)
                self.settings = settings
                return UiCommandResult(CommandStatus.APPLIED, "toast.settings_saved")
            if command.action_id == "label.add":
                dataset_id = str(command.payload.get("dataset_id", ""))
                created = LabelSetService(self.service).add_label(
                    dataset_id,
                    class_id=command.payload.get("class_id"),
                    name=str(command.payload.get("name", "")),
                    alias=str(command.payload.get("alias", "")),
                    description=str(command.payload.get("description", "")),
                    synonyms=tuple(command.payload.get("synonyms", ())),
                    color=str(command.payload.get("color") or "") or None,
                )
                return UiCommandResult(
                    CommandStatus.APPLIED,
                    "toast.label_saved",
                    affected_id=created.labels[-1].id,
                )
            if command.action_id == "label.update_display":
                dataset_id = str(command.payload.get("dataset_id", ""))
                updated = LabelSetService(self.service).update_display_fields(
                    dataset_id,
                    str(command.payload.get("label_id", "")),
                    alias=str(command.payload.get("alias", "")),
                    description=str(command.payload.get("description", "")),
                    synonyms=tuple(command.payload.get("synonyms", ())),
                    color=str(command.payload.get("color", "")),
                    expected_revision=int(command.payload.get("expected_revision", -1)),
                )
                return UiCommandResult(
                    CommandStatus.APPLIED,
                    "toast.label_saved",
                    affected_id=updated.id,
                )
            if command.action_id == "label.set_status":
                dataset_id = str(command.payload.get("dataset_id", ""))
                updated = LabelSetService(self.service).set_status(
                    dataset_id,
                    str(command.payload.get("label_id", "")),
                    LabelStatus(str(command.payload.get("status", ""))),
                    expected_revision=int(command.payload.get("expected_revision", -1)),
                )
                return UiCommandResult(
                    CommandStatus.APPLIED,
                    "toast.label_saved",
                    affected_id=updated.id,
                )
            if command.action_id in {"similarity.confirm", "similarity.ignore"}:
                dataset_id = str(command.payload.get("dataset_id", ""))
                group_id = str(command.payload.get("group_id", ""))
                _bundle, _paths, repository = self._media(dataset_id)
                status = (
                    SimilarityStatus.CONFIRMED
                    if command.action_id == "similarity.confirm"
                    else SimilarityStatus.IGNORED
                )
                repository.set_similarity_status(group_id, status, utc_now().isoformat())
                return UiCommandResult(CommandStatus.APPLIED, "toast.operation_complete")
            if command.action_id == "sample.rename":
                dataset_id = str(command.payload.get("dataset_id", ""))
                policy = NamingPolicy.model_validate(command.payload.get("policy", {}))
                sample_ids = tuple(str(item) for item in command.payload.get("sample_ids", ()))
                bundle, paths, repository = self._media(dataset_id)
                service = SampleRenameService(paths, bundle.dataset, repository)
                plan = service.preview(policy, sample_ids=sample_ids or None)
                service.apply(plan)
                configuration = bundle.dataset.configuration.model_copy(
                    update={"naming_policy": policy}
                )
                self.service.update_configuration(dataset_id, configuration)
                return UiCommandResult(CommandStatus.APPLIED, "toast.samples_renamed")
            if command.action_id in {"sample.trash", "sample.delete_permanent"}:
                dataset_id = str(command.payload.get("dataset_id", ""))
                sample_ids = tuple(str(item) for item in command.payload.get("sample_ids", ()))
                bundle, paths, repository = self._media(dataset_id)
                service = TrashService(paths, bundle.dataset, repository)
                threshold = int(
                    command.payload.get("threshold", self.settings.trash_sample_threshold)
                )
                impact = service.preview(sample_ids, threshold=threshold)
                service.delete(
                    impact,
                    permanent=command.action_id == "sample.delete_permanent",
                )
                self.service.synchronize_statistics(dataset_id)
                return UiCommandResult(CommandStatus.APPLIED, "toast.samples_deleted")
            if command.action_id == "trash.restore":
                dataset_id = str(command.payload.get("dataset_id", ""))
                sample_id = str(command.payload.get("sample_id", ""))
                bundle, paths, repository = self._media(dataset_id)
                TrashService(paths, bundle.dataset, repository).restore(sample_id)
                self.service.synchronize_statistics(dataset_id)
                return UiCommandResult(CommandStatus.APPLIED, "toast.sample_restored")
            if command.action_id == "trash.delete_permanent":
                dataset_id = str(command.payload.get("dataset_id", ""))
                sample_id = str(command.payload.get("sample_id", ""))
                bundle, paths, repository = self._media(dataset_id)
                TrashService(paths, bundle.dataset, repository).permanently_delete(sample_id)
                self.service.synchronize_statistics(dataset_id)
                return UiCommandResult(CommandStatus.APPLIED, "toast.samples_deleted")
        except DuplicateDatasetNameError:
            return UiCommandResult(CommandStatus.INVALID, "toast.duplicate_dataset_name")
        except InvalidDatasetNameError:
            return UiCommandResult(CommandStatus.INVALID, "toast.invalid_name")
        except (DatasetNotFoundError, DatasetUnavailableError):
            return UiCommandResult(CommandStatus.ERROR, "toast.dataset_unavailable")
        except (
            AppSettingsError,
            ImagePoolError,
            ManagedLabelError,
            SampleGovernanceError,
            SampleRepositoryError,
        ):
            logger.exception("受管图片或设置操作失败: %s", command.action_id)
            return UiCommandResult(CommandStatus.ERROR, "toast.library_operation_failed")
        except DatasetLibraryServiceError:
            logger.exception("受管数据集业务操作失败: %s", command.action_id)
            return UiCommandResult(CommandStatus.ERROR, "toast.library_operation_failed")
        except Exception:
            logger.exception("受管数据集命令发生未预期异常: %s", command.action_id)
            return UiCommandResult(CommandStatus.ERROR, "toast.library_operation_failed")
        return UiCommandResult(CommandStatus.NOT_CONNECTED, "toast.not_connected")

    def _media(self, dataset_id: str):
        """集中装配数据集级服务，禁止页面自行解析受管路径。"""

        bundle = self.service.open_dataset(dataset_id)
        paths = self.service.dataset_repository.paths(dataset_id)
        repository = DatasetSampleRepository(paths, dataset_id)
        return bundle, paths, repository

    @staticmethod
    def _card(record: ManagedDatasetRecord) -> DatasetCardViewData:
        """损坏项使用 library.json 摘要，其余使用已验证数据集事实。"""

        entry = record.entry
        if record.bundle is None:
            statistics = entry.statistics
            label_count = statistics.label_count
            health = DatasetHealth.DAMAGED
        else:
            dataset = record.bundle.dataset
            statistics = dataset.statistics
            label_count = len(record.bundle.label_set.labels)
            health = DatasetHealth.READY
        return DatasetCardViewData(
            id=entry.id,
            name=entry.name,
            description=entry.description,
            image_count=statistics.image_count,
            label_count=label_count,
            reviewed_percent=statistics.reviewed_percent,
            modified_text=format_modified_time(entry.modified_at),
            cover_seed=int(UUID(entry.id)) % 17,
            health=health,
            created_sort=entry.created_at.isoformat(),
            modified_sort=entry.modified_at.isoformat(),
            archived=entry.archived,
            diagnostic=record.diagnostic,
        )
