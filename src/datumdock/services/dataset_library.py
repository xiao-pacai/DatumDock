"""受管数据集资料库的创建、打开、切换与配置复制服务。"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from datumdock.domain.models import (
    AppLibrary,
    DatasetLibraryEntry,
    DatasetStatistics,
    LabelSet,
    ManagedDataset,
    ManagedDatasetConfiguration,
    new_id,
    utc_now,
)
from datumdock.services.library_repository import (
    CorruptDatasetError,
    DatasetLibraryRepository,
    DatasetRepository,
    DatasetRepositoryError,
)


class DatasetLibraryServiceError(RuntimeError):
    """资料库业务操作无法安全完成。"""


class InvalidDatasetNameError(DatasetLibraryServiceError):
    """数据集名称为空、包含路径字符或不适合 Windows 使用。"""


class DuplicateDatasetNameError(DatasetLibraryServiceError):
    """活动数据集中已存在大小写不敏感的同名项。"""


class DatasetNotFoundError(DatasetLibraryServiceError):
    """资料库中没有登记目标数据集。"""


class DatasetUnavailableError(DatasetLibraryServiceError):
    """目标数据集已归档或结构损坏，不能作为活动工作台打开。"""


@dataclass(frozen=True, slots=True)
class ManagedDatasetBundle:
    """服务层返回的数据集元数据与其独立标签定义。"""

    dataset: ManagedDataset
    label_set: LabelSet


@dataclass(frozen=True, slots=True)
class ManagedDatasetRecord:
    """主页可安全展示的登记记录；单项损坏不会阻断其他数据集。"""

    entry: DatasetLibraryEntry
    bundle: ManagedDatasetBundle | None
    diagnostic: str = ""

    @property
    def healthy(self) -> bool:
        """元数据和结构均通过验证时才允许打开。"""

        return self.bundle is not None


_WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


def normalize_dataset_name(value: str) -> str:
    """规范并验证显示名称；名称即使合法也永远不用于目录路径。"""

    name = value.strip()
    if not name:
        raise InvalidDatasetNameError("数据集名称不能为空")
    if len(name) > 120:
        raise InvalidDatasetNameError("数据集名称不能超过 120 个字符")
    if name in {".", ".."} or re.search(r"[\\/:*?\"<>|]", name):
        raise InvalidDatasetNameError("数据集名称不能包含路径或 Windows 保留字符")
    if any(ord(character) < 32 for character in name):
        raise InvalidDatasetNameError("数据集名称不能包含控制字符")
    if name.endswith((".", " ")):
        raise InvalidDatasetNameError("数据集名称不能以点或空格结尾")
    if name.split(".", 1)[0].upper() in _WINDOWS_RESERVED_NAMES:
        raise InvalidDatasetNameError("数据集名称不能使用 Windows 保留名称")
    return name


class DatasetLibraryService:
    """对 UI 隐藏文件系统细节，并维护资料库与数据集元数据一致性。"""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.library_repository = DatasetLibraryRepository(root)
        self.dataset_repository = DatasetRepository(root)
        self._library = self.library_repository.initialize()

    @property
    def library(self) -> AppLibrary:
        """返回资料库索引的深副本，调用方不能绕过服务修改登记。"""

        return self._library.model_copy(deep=True)

    def list_datasets(self, *, include_archived: bool = True) -> list[ManagedDatasetRecord]:
        """逐项验证并返回主页记录，损坏项转换为诊断记录。"""

        records: list[ManagedDatasetRecord] = []
        for entry in self._library.datasets:
            if entry.archived and not include_archived:
                continue
            try:
                dataset, label_set = self.dataset_repository.load(entry.id)
                if dataset.archived != entry.archived:
                    raise CorruptDatasetError("资料库与数据集的归档状态不一致")
                records.append(
                    ManagedDatasetRecord(
                        entry=entry.model_copy(deep=True),
                        bundle=ManagedDatasetBundle(dataset, label_set),
                    )
                )
            except DatasetRepositoryError as error:
                records.append(
                    ManagedDatasetRecord(
                        entry=entry.model_copy(deep=True),
                        bundle=None,
                        diagnostic=str(error),
                    )
                )
        return records

    def create_dataset(
        self,
        name: str,
        description: str = "",
        *,
        source_dataset_id: str | None = None,
    ) -> ManagedDatasetBundle:
        """以受管事务创建空数据集，可从健康活动数据集复制独立配置。"""

        normalized_name = normalize_dataset_name(name)
        self._ensure_unique_active_name(normalized_name)
        source_bundle: ManagedDatasetBundle | None = None
        if source_dataset_id is not None:
            source_bundle = self.open_dataset(source_dataset_id)
        label_set = (
            source_bundle.label_set.model_copy(deep=True)
            if source_bundle is not None
            else LabelSet()
        )
        configuration = (
            source_bundle.dataset.configuration.model_copy(deep=True)
            if source_bundle is not None
            else ManagedDatasetConfiguration()
        )
        timestamp = utc_now()
        dataset = ManagedDataset(
            id=new_id(),
            name=normalized_name,
            description=description.strip(),
            created_at=timestamp,
            modified_at=timestamp,
            label_set_id=label_set.id,
            configuration=configuration,
            statistics=DatasetStatistics(label_count=len(label_set.labels)),
        )
        entry = self._entry_from_dataset(dataset)
        staging = self.dataset_repository.staging_path(dataset.id)
        published = False
        try:
            self.dataset_repository.create_in_staging(staging, dataset, label_set)
            self.dataset_repository.publish_staging(staging, dataset.id)
            published = True
            new_library = self._library.model_copy(deep=True)
            new_library.datasets.append(entry)
            new_library.updated_at = timestamp
            self.library_repository.save(new_library)
        except Exception as error:
            if staging.exists():
                self.dataset_repository.remove_staging(staging)
            if published:
                self.dataset_repository.move_unregistered_to_recovery(dataset.id)
            if isinstance(error, DatasetLibraryServiceError):
                raise
            raise DatasetLibraryServiceError(f"创建数据集失败: {error}") from error
        self._library = new_library
        return ManagedDatasetBundle(dataset, label_set)

    def open_dataset(self, dataset_id: str) -> ManagedDatasetBundle:
        """打开健康活动数据集；归档项只允许在主页恢复。"""

        entry = self._entry(dataset_id)
        if entry.archived:
            raise DatasetUnavailableError("数据集已归档，请先在主页恢复")
        try:
            dataset, label_set = self.dataset_repository.load(dataset_id)
        except DatasetRepositoryError as error:
            raise DatasetUnavailableError(str(error)) from error
        if dataset.archived:
            raise DatasetUnavailableError("数据集已归档，请先在主页恢复")
        return ManagedDatasetBundle(dataset, label_set)

    def rename_dataset(self, dataset_id: str, new_name: str) -> ManagedDatasetBundle:
        """只修改元数据和资料库摘要，UUID 目录保持不变。"""

        normalized_name = normalize_dataset_name(new_name)
        self._ensure_unique_active_name(normalized_name, excluding_id=dataset_id)
        bundle = self.open_dataset(dataset_id)
        timestamp = utc_now()
        updated = bundle.dataset.model_copy(
            deep=True,
            update={"name": normalized_name, "modified_at": timestamp},
        )
        self._save_dataset_and_entry(bundle.dataset, updated)
        return ManagedDatasetBundle(updated, bundle.label_set)

    def archive_dataset(self, dataset_id: str) -> ManagedDatasetBundle:
        """归档只切换状态，数据集目录内任何内容都不会删除。"""

        bundle = self.open_dataset(dataset_id)
        updated = bundle.dataset.model_copy(
            deep=True,
            update={"archived": True, "modified_at": utc_now()},
        )
        self._save_dataset_and_entry(bundle.dataset, updated)
        return ManagedDatasetBundle(updated, bundle.label_set)

    def restore_dataset(self, dataset_id: str) -> ManagedDatasetBundle:
        """恢复前重新检查活动名称冲突，并保持原 UUID 目录。"""

        entry = self._entry(dataset_id)
        if not entry.archived:
            return self.open_dataset(dataset_id)
        try:
            dataset, label_set = self.dataset_repository.load(dataset_id)
        except DatasetRepositoryError as error:
            raise DatasetUnavailableError(str(error)) from error
        self._ensure_unique_active_name(dataset.name, excluding_id=dataset_id)
        updated = dataset.model_copy(
            deep=True,
            update={"archived": False, "modified_at": utc_now()},
        )
        self._save_dataset_and_entry(dataset, updated)
        return ManagedDatasetBundle(updated, label_set)

    def update_configuration(
        self,
        dataset_id: str,
        configuration: ManagedDatasetConfiguration,
    ) -> ManagedDatasetBundle:
        """为后续设置页提供真实配置边界，并用于验证模板副本相互独立。"""

        bundle = self.open_dataset(dataset_id)
        updated = bundle.dataset.model_copy(
            deep=True,
            update={
                "configuration": configuration.model_copy(deep=True),
                "modified_at": utc_now(),
            },
        )
        self._save_dataset_and_entry(bundle.dataset, updated)
        return ManagedDatasetBundle(updated, bundle.label_set)

    def update_label_set(self, dataset_id: str, label_set: LabelSet) -> ManagedDatasetBundle:
        """保存当前数据集的独立标签副本，不修改同模板创建的其他数据集。"""

        bundle = self.open_dataset(dataset_id)
        if label_set.id != bundle.dataset.label_set_id:
            raise DatasetLibraryServiceError("标签集稳定 ID 与数据集引用不一致")
        old_label_set = bundle.label_set.model_copy(deep=True)
        updated_dataset = bundle.dataset.model_copy(
            deep=True,
            update={
                "modified_at": utc_now(),
                "statistics": bundle.dataset.statistics.model_copy(
                    update={"label_count": len(label_set.labels)}
                ),
            },
        )
        try:
            self.dataset_repository.save_label_set(dataset_id, label_set)
            self._save_dataset_and_entry(bundle.dataset, updated_dataset)
        except Exception:
            self.dataset_repository.save_label_set(dataset_id, old_label_set)
            raise
        return ManagedDatasetBundle(updated_dataset, label_set.model_copy(deep=True))

    def dataset_directory(self, dataset_id: str) -> Path:
        """只为测试、诊断和后续服务返回经过 UUID 验证的内部目录。"""

        self._entry(dataset_id)
        return self.dataset_repository.paths(dataset_id).root

    def _save_dataset_and_entry(
        self,
        original: ManagedDataset,
        updated: ManagedDataset,
    ) -> None:
        """以可回滚顺序同步数据集元数据和资料库摘要。"""

        new_library = self._library.model_copy(deep=True)
        index = self._entry_index(updated.id, library=new_library)
        new_library.datasets[index] = self._entry_from_dataset(updated)
        new_library.updated_at = updated.modified_at
        self.dataset_repository.save_dataset(updated)
        try:
            self.library_repository.save(new_library)
        except Exception as error:
            try:
                self.dataset_repository.save_dataset(original)
            except Exception as rollback_error:
                raise DatasetLibraryServiceError(
                    f"资料库写入失败且元数据回滚失败: {rollback_error}"
                ) from error
            raise DatasetLibraryServiceError(
                f"资料库写入失败，数据集修改已回滚: {error}"
            ) from error
        self._library = new_library

    def _entry(self, dataset_id: str) -> DatasetLibraryEntry:
        return self._library.datasets[self._entry_index(dataset_id)]

    def _entry_index(self, dataset_id: str, *, library: AppLibrary | None = None) -> int:
        target = self._library if library is None else library
        for index, entry in enumerate(target.datasets):
            if entry.id == dataset_id:
                return index
        raise DatasetNotFoundError(f"资料库未登记数据集: {dataset_id}")

    def _ensure_unique_active_name(self, name: str, *, excluding_id: str | None = None) -> None:
        normalized = name.casefold()
        for entry in self._library.datasets:
            if entry.id == excluding_id or entry.archived:
                continue
            if entry.name.strip().casefold() == normalized:
                raise DuplicateDatasetNameError(f"活动数据集中已存在名称: {name}")

    @staticmethod
    def _entry_from_dataset(dataset: ManagedDataset) -> DatasetLibraryEntry:
        return DatasetLibraryEntry(
            id=dataset.id,
            relative_path=f"datasets/{dataset.id}",
            name=dataset.name,
            description=dataset.description,
            created_at=dataset.created_at,
            modified_at=dataset.modified_at,
            archived=dataset.archived,
            statistics=copy.deepcopy(dataset.statistics),
        )


def format_modified_time(value: datetime) -> str:
    """主页显示稳定的本地时间，不把数据集内容交给翻译服务改写。"""

    return value.astimezone().strftime("%Y-%m-%d %H:%M")
