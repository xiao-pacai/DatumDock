"""正式受管数据集的标签仓库、颜色分配与编辑服务。"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from datumdock.domain.models import Label, LabelSet, LabelStatus, new_id, utc_now
from datumdock.services.dataset_library import DatasetLibraryService, DatasetLibraryServiceError
from datumdock.services.library_repository import DatasetRepositoryError
from datumdock.services.sample_repository import DatasetSampleRepository, SampleRepositoryError


class ManagedLabelError(RuntimeError):
    """标签操作无法在不破坏稳定映射的前提下完成。"""


@dataclass(frozen=True, slots=True)
class LabelChangePreview:
    """训练映射变更在确认前展示的真实影响。"""

    dataset_id: str
    label_id: str
    expected_revision: int
    old_training_name: str
    new_training_name: str
    old_class_id: int
    new_class_id: int
    affected_images: int
    affected_shapes: int
    requires_json_migration: bool
    requires_confirmation: bool


@dataclass(frozen=True, slots=True)
class LabelUsage:
    """标签检查页使用的 SQLite 摘要。"""

    label_id: str
    image_count: int
    shape_count: int


class LabelColorService:
    """从可访问调色板分配活动标签唯一颜色。"""

    PALETTE = (
        "#4D8FBF",
        "#E69262",
        "#6AA889",
        "#A479B5",
        "#D6A84B",
        "#5BA7A7",
        "#D4778D",
        "#7C8DC5",
        "#8FA55F",
        "#C78355",
        "#578CB5",
        "#B16F9B",
        "#6E9C72",
        "#CC765E",
        "#7182AC",
        "#AA8B55",
    )

    def allocate(self, label_set: LabelSet) -> str:
        """优先复用调色板中尚未被活动标签占用的颜色。"""

        used = {
            label.color.upper() for label in label_set.labels if label.status == LabelStatus.ACTIVE
        }
        for color in self.PALETTE:
            if color.upper() not in used:
                return color
        # 调色板用尽后使用黄金角生成稳定候选，并继续保证精确唯一。
        index = len(used)
        while True:
            hue = (index * 137.508) % 360
            color = _hsl_to_hex(hue, 0.48, 0.56)
            if color.upper() not in used:
                return color
            index += 1

    def nearby_colors(self, label_set: LabelSet, candidate: str) -> tuple[str, ...]:
        """返回感知距离过近的活动颜色，供 UI 提示而不强制阻断。"""

        target = _hex_rgb(candidate)
        return tuple(
            label.id
            for label in label_set.labels
            if label.status == LabelStatus.ACTIVE
            and label.color.upper() != candidate.upper()
            and _rgb_distance(target, _hex_rgb(label.color)) < 46
        )


class LabelSetRepository:
    """只通过正式数据集仓库读写当前数据集的标签文件。"""

    def __init__(self, library_service: DatasetLibraryService) -> None:
        self.library_service = library_service

    def load(self, dataset_id: str) -> LabelSet:
        """返回独立模型副本，调用方不能直接修改仓库缓存。"""

        try:
            return self.library_service.open_dataset(dataset_id).label_set.model_copy(deep=True)
        except DatasetLibraryServiceError as error:
            raise ManagedLabelError(f"标签集读取失败: {error}") from error

    def save(self, dataset_id: str, label_set: LabelSet) -> LabelSet:
        """完整复验、原子替换并从磁盘重读标签集。"""

        try:
            validated = LabelSet.model_validate(label_set.model_dump(mode="json"))
            self.library_service.update_label_set(dataset_id, validated)
            return self.library_service.open_dataset(dataset_id).label_set.model_copy(deep=True)
        except (ValidationError, DatasetRepositoryError, DatasetLibraryServiceError) as error:
            raise ManagedLabelError(f"标签集保存失败: {error}") from error


class LabelSetService:
    """执行标签新增、展示字段编辑、归档和训练映射确认。"""

    def __init__(self, library_service: DatasetLibraryService) -> None:
        self.library_service = library_service
        self.repository = LabelSetRepository(library_service)
        self.colors = LabelColorService()

    def list_labels(
        self,
        dataset_id: str,
        *,
        search: str = "",
        include_archived: bool = True,
    ) -> tuple[Label, ...]:
        """同时检索训练名、别名、描述和同义词。"""

        label_set = self.repository.load(dataset_id)
        needle = search.strip().casefold()
        labels = [
            label
            for label in label_set.labels
            if (include_archived or label.status == LabelStatus.ACTIVE)
            and (
                not needle
                or needle
                in " ".join(
                    (label.name, label.alias, label.description, *label.synonyms)
                ).casefold()
            )
        ]
        return tuple(sorted(labels, key=lambda item: (item.status.value, item.class_id, item.name)))

    def add_label(
        self,
        dataset_id: str,
        *,
        class_id: int | None,
        name: str,
        alias: str,
        description: str = "",
        synonyms: tuple[str, ...] = (),
        color: str | None = None,
    ) -> LabelSet:
        """新增活动标签并自动分配未占用类别 ID 与颜色。"""

        current = self.repository.load(dataset_id)
        used_ids = {label.class_id for label in current.labels}
        assigned_class_id = class_id
        if assigned_class_id is None:
            assigned_class_id = next(
                index for index in range(len(used_ids) + 1) if index not in used_ids
            )
        timestamp = utc_now()
        label = Label(
            id=new_id(),
            class_id=assigned_class_id,
            name=name,
            alias=alias,
            description=description.strip(),
            synonyms=list(synonyms),
            color=color or self.colors.allocate(current),
            created_at=timestamp,
            modified_at=timestamp,
        )
        updated = current.model_copy(
            deep=True,
            update={
                "labels": [*current.labels, label],
                "revision": current.revision + 1,
                "updated_at": timestamp,
            },
        )
        return self.repository.save(dataset_id, updated)

    def preview_change(
        self,
        dataset_id: str,
        label_id: str,
        *,
        name: str | None = None,
        class_id: int | None = None,
    ) -> LabelChangePreview:
        """从 SQLite 计算训练名或类别 ID 变化的准确影响。"""

        current = self.repository.load(dataset_id)
        try:
            label = current.get_label(label_id)
        except KeyError as error:
            raise ManagedLabelError(str(error)) from error
        paths = self.library_service.dataset_repository.paths(dataset_id)
        try:
            usage = (
                DatasetSampleRepository(paths, dataset_id)
                .label_usage_counts()
                .get(label_id, (0, 0))
            )
        except SampleRepositoryError as error:
            raise ManagedLabelError(f"标签影响查询失败: {error}") from error
        new_name = name.strip() if name is not None else label.name
        new_class_id = class_id if class_id is not None else label.class_id
        return LabelChangePreview(
            dataset_id=dataset_id,
            label_id=label_id,
            expected_revision=current.revision,
            old_training_name=label.name,
            new_training_name=new_name,
            old_class_id=label.class_id,
            new_class_id=new_class_id,
            affected_images=usage[0],
            affected_shapes=usage[1],
            requires_json_migration=new_name.casefold() != label.name.casefold(),
            requires_confirmation=(
                new_name.casefold() != label.name.casefold() or new_class_id != label.class_id
            ),
        )

    def update_display_fields(
        self,
        dataset_id: str,
        label_id: str,
        *,
        alias: str,
        description: str,
        synonyms: tuple[str, ...],
        color: str,
        expected_revision: int,
    ) -> LabelSet:
        """展示字段变化只写标签集，绝不触碰标注 JSON。"""

        return self._replace_label(
            dataset_id,
            label_id,
            expected_revision=expected_revision,
            changes={
                "alias": alias,
                "description": description.strip(),
                "synonyms": list(synonyms),
                "color": color,
            },
        )

    def set_status(
        self,
        dataset_id: str,
        label_id: str,
        status: LabelStatus,
        *,
        expected_revision: int,
    ) -> LabelSet:
        """归档保留稳定 ID 与历史映射；恢复仍需通过活动唯一性校验。"""

        return self._replace_label(
            dataset_id,
            label_id,
            expected_revision=expected_revision,
            changes={"status": status},
        )

    def apply_mapping_change(
        self,
        preview: LabelChangePreview,
        *,
        migration_completed: bool,
    ) -> LabelSet:
        """只接受仍匹配当前修订的已确认训练映射变更。"""

        if preview.requires_json_migration and not migration_completed:
            raise ManagedLabelError("训练名变化尚未完成标注迁移")
        return self._replace_label(
            preview.dataset_id,
            preview.label_id,
            expected_revision=preview.expected_revision,
            changes={
                "name": preview.new_training_name,
                "class_id": preview.new_class_id,
            },
        )

    def _replace_label(
        self,
        dataset_id: str,
        label_id: str,
        *,
        expected_revision: int,
        changes: dict[str, Any],
    ) -> LabelSet:
        current = self.repository.load(dataset_id)
        if current.revision != expected_revision:
            raise ManagedLabelError("标签集已被其他操作修改，请刷新后重试")
        timestamp = utc_now()
        found = False
        labels: list[Label] = []
        for label in current.labels:
            if label.id != label_id:
                labels.append(label.model_copy(deep=True))
                continue
            found = True
            labels.append(label.model_copy(deep=True, update={**changes, "modified_at": timestamp}))
        if not found:
            raise ManagedLabelError("待修改标签不存在")
        updated = current.model_copy(
            deep=True,
            update={
                "labels": labels,
                "revision": current.revision + 1,
                "updated_at": timestamp,
            },
        )
        return self.repository.save(dataset_id, updated)


class LabelInspectionService:
    """使用 SQLite 标签反向索引生成检查集合。"""

    def __init__(self, library_service: DatasetLibraryService) -> None:
        self.library_service = library_service

    def usages(self, dataset_id: str) -> tuple[LabelUsage, ...]:
        """读取全部标签使用量，不同步解析标注文件。"""

        paths = self.library_service.dataset_repository.paths(dataset_id)
        try:
            counts = DatasetSampleRepository(paths, dataset_id).label_usage_counts()
        except SampleRepositoryError as error:
            raise ManagedLabelError(f"标签检查索引读取失败: {error}") from error
        return tuple(
            LabelUsage(label_id, image_count, shape_count)
            for label_id, (image_count, shape_count) in sorted(counts.items())
        )


def _hex_rgb(value: str) -> tuple[int, int, int]:
    normalized = value.removeprefix("#")
    return tuple(int(normalized[index : index + 2], 16) for index in (0, 2, 4))


def _rgb_distance(left: tuple[int, int, int], right: tuple[int, int, int]) -> float:
    return math.sqrt(sum((first - second) ** 2 for first, second in zip(left, right, strict=True)))


def _hsl_to_hex(hue: float, saturation: float, lightness: float) -> str:
    """不依赖 GUI 的 HSL 转换，便于服务层单元测试。"""

    chroma = (1 - abs(2 * lightness - 1)) * saturation
    section = hue / 60
    intermediate = chroma * (1 - abs(section % 2 - 1))
    if section < 1:
        red, green, blue = chroma, intermediate, 0
    elif section < 2:
        red, green, blue = intermediate, chroma, 0
    elif section < 3:
        red, green, blue = 0, chroma, intermediate
    elif section < 4:
        red, green, blue = 0, intermediate, chroma
    elif section < 5:
        red, green, blue = intermediate, 0, chroma
    else:
        red, green, blue = chroma, 0, intermediate
    offset = lightness - chroma / 2
    red_value = round((red + offset) * 255)
    green_value = round((green + offset) * 255)
    blue_value = round((blue + offset) * 255)
    return f"#{red_value:02X}{green_value:02X}{blue_value:02X}"
