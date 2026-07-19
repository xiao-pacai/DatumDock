"""步骤二受管数据集稳定标识与关系约束回归。"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from datumdock.domain.models import DatasetStatistics, Label, LabelSet, LabelStatus, new_id
from datumdock.services.dataset_library import DatasetLibraryService
from datumdock.services.library_repository import DatasetRepositoryError


def _label(
    *,
    identifier: str | None = None,
    class_id: int = 0,
    name: str = "part",
    color: str = "#73B9D2",
    status: LabelStatus = LabelStatus.ACTIVE,
) -> Label:
    """构造只改变当前断言字段的合法标签。"""

    values = {
        "class_id": class_id,
        "name": name,
        "alias": name,
        "color": color,
        "status": status,
    }
    if identifier is not None:
        values["id"] = identifier
    return Label(**values)


@pytest.mark.parametrize("model", [Label, LabelSet])
def test_stable_label_objects_reject_non_uuid_ids(model) -> None:
    """标签及标签集稳定 ID 都必须使用规范 UUID。"""

    payload = {"id": "not-a-uuid"}
    if model is Label:
        payload.update(class_id=0, name="part", alias="零件", color="#73B9D2")
    with pytest.raises(ValidationError, match="UUID"):
        model(**payload)


@pytest.mark.parametrize(
    ("labels", "message"),
    [
        (lambda: [_label(identifier=new_id()), _label(identifier=new_id(), class_id=1)], "标签 ID"),
        (lambda: [_label(), _label(class_id=0, name="other", color="#F2A36F")], "类别 ID"),
        (lambda: [_label(), _label(class_id=1, name="PART", color="#F2A36F")], "训练名"),
        (lambda: [_label(), _label(class_id=1, name="other", color="#73b9d2")], "颜色"),
    ],
)
def test_label_set_rejects_duplicate_active_mappings(labels, message: str) -> None:
    """活动标签不能共享稳定 ID、类别 ID、训练名或显示颜色。"""

    items = labels()
    if message == "标签 ID":
        items[1].id = items[0].id
    with pytest.raises(ValidationError, match=message):
        LabelSet(labels=items)


def test_archived_labels_may_preserve_historical_name_and_color() -> None:
    """归档标签可保留历史展示信息，但类别 ID 仍保持唯一。"""

    label_set = LabelSet(
        labels=[
            _label(),
            _label(
                class_id=1,
                name="part",
                color="#73B9D2",
                status=LabelStatus.ARCHIVED,
            ),
        ]
    )
    assert len(label_set.labels) == 2


def test_reviewed_count_cannot_exceed_image_count() -> None:
    """主页复核比例不得接受超过图片总数的损坏统计。"""

    with pytest.raises(ValidationError, match="复核数量"):
        DatasetStatistics(image_count=1, reviewed_count=2)


def test_repository_revalidates_mutated_label_set_before_save(tmp_path: Path) -> None:
    """可变模型在构造后被破坏时，Repository 保存前仍必须完整复验。"""

    service = DatasetLibraryService(tmp_path)
    created = service.create_dataset("保存前复验")
    label_set = created.label_set.model_copy(deep=True)
    first = _label()
    second = _label(class_id=1, name="other", color="#F2A36F")
    label_set.labels.extend((first, second))
    label_set.labels[1].class_id = 0

    with pytest.raises(DatasetRepositoryError, match="标签集验证失败"):
        service.dataset_repository.save_label_set(created.dataset.id, label_set)
