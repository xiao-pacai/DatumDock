"""把真实内部资料库转换为步骤一 UI 所需的只读快照。"""

from __future__ import annotations

from uuid import UUID

from datumdock.domain.models import LabelStatus
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
from datumdock.services.library_repository import resolve_data_root
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


class ManagedDatasetGateway:
    """普通模式的唯一 UI 业务入口，页面不直接访问磁盘或 Repository。"""

    preview_mode = False

    def __init__(self, service: DatasetLibraryService) -> None:
        self.service = service

    @classmethod
    def from_default_root(cls) -> ManagedDatasetGateway:
        """从当前 Windows 用户内部目录装配真实资料库服务。"""

        return cls(DatasetLibraryService(resolve_data_root()))

    def home_snapshot(self) -> HomeSnapshot:
        """返回包含健康、归档和损坏项的真实主页快照。"""

        records = self.service.list_datasets(include_archived=True)
        return HomeSnapshot(tuple(self._card(record) for record in records), 0)

    def workspace_snapshot(self, dataset_id: str | None = None) -> WorkspaceSnapshot | None:
        """打开真实空数据集上下文，不用演示图片填充普通模式。"""

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
        labels = tuple(
            LabelViewData(
                id=label.id,
                class_id=label.class_id,
                name=label.name,
                alias=label.alias,
                description=label.description,
                synonyms=tuple(label.synonyms),
                color=label.color,
                usage_count=0,
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
        )

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
        except DuplicateDatasetNameError:
            return UiCommandResult(CommandStatus.INVALID, "toast.duplicate_dataset_name")
        except InvalidDatasetNameError:
            return UiCommandResult(CommandStatus.INVALID, "toast.invalid_name")
        except (DatasetNotFoundError, DatasetUnavailableError):
            return UiCommandResult(CommandStatus.ERROR, "toast.dataset_unavailable")
        except DatasetLibraryServiceError:
            return UiCommandResult(CommandStatus.ERROR, "toast.library_operation_failed")
        return UiCommandResult(CommandStatus.NOT_CONNECTED, "toast.not_connected")

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
