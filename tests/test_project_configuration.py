"""工程骨架的最小配置验证。"""

from pathlib import Path

import pytest

from datumdock.domain.models import Label, LabelSet
from datumdock.services.labels import LabelService, LabelSetCompatibilityService
from datumdock.services.workspace import WorkspaceService


def test_quality_configuration_files_exist() -> None:
    """确保新环境克隆仓库后能找到统一的质量检查配置。"""

    project_root = Path(__file__).resolve().parents[1]

    assert (project_root / "pyproject.toml").is_file()
    assert (project_root / ".pre-commit-config.yaml").is_file()


def test_project_and_dataset_templates_copy_configuration_without_samples(tmp_path: Path) -> None:
    """新建模板对象只复制配置和标签，不复制任何受管图片或标注。"""

    service = WorkspaceService()
    root = tmp_path / "workspace"
    workspace = service.create_workspace(root)
    source_project = service.create_project(root, workspace, "源项目")
    LabelService().add_label(
        source_project.label_set,
        Label(class_id=0, name="part", alias="零件", color="#78978C"),
    )
    service.save_project(root, source_project)
    source_dataset = service.create_dataset(root, source_project, "源数据集")
    source_dataset.naming_policy.prefix = "component"
    source_dataset.show_annotation_preview = False
    service.save_project(root, source_project)

    copied_project = service.create_project(root, workspace, "复制项目", template=source_project)
    copied_dataset = service.create_dataset(
        root,
        source_project,
        "复制数据集",
        template=source_dataset,
    )

    assert (
        copied_project.label_set.training_signature()
        == source_project.label_set.training_signature()
    )
    assert copied_dataset.id != source_dataset.id
    assert copied_dataset.naming_policy.prefix == "component"
    assert copied_dataset.show_annotation_preview is False
    assert not list((root / "projects" / copied_project.id / "datasets").iterdir())


def test_label_set_compatibility_maps_independent_stable_ids() -> None:
    """跨项目标签稳定 ID 不同，只要所有训练和展示信息一致即可安全映射。"""

    source = LabelSet(
        labels=[
            Label(
                class_id=0,
                name="bolt",
                alias="螺栓",
                description="六角螺栓",
                synonyms=["hex bolt", "Bolt"],
                color="#78978C",
            )
        ]
    )
    target = LabelSet(
        labels=[
            Label(
                class_id=0,
                name="bolt",
                alias="螺栓",
                description="六角螺栓",
                synonyms=["bolt", "hex bolt"],
                color="#78978C",
            )
        ]
    )

    comparison = LabelSetCompatibilityService.compare(source, target)

    assert comparison.compatible
    assert comparison.label_id_mapping == {source.labels[0].id: target.labels[0].id}


def test_label_set_merge_rejects_same_training_label_with_different_information() -> None:
    """同一训练标签的中文别名等信息不同必须阻止合并，避免用户误以为一致。"""

    target = LabelSet(labels=[Label(class_id=0, name="bolt", alias="螺栓", color="#78978C")])
    incoming = LabelSet(labels=[Label(class_id=0, name="bolt", alias="螺丝", color="#78978C")])

    with pytest.raises(ValueError, match="标签信息不一致"):
        LabelSetCompatibilityService().merge_into(target, incoming)
    assert len(target.labels) == 1


def test_label_set_merge_adds_non_conflicting_labels_without_reusing_objects() -> None:
    """标签集合并保留来源标签信息，但目标得到独立对象以避免跨项目串改。"""

    target = LabelSet(labels=[Label(class_id=0, name="bolt", alias="螺栓", color="#78978C")])
    incoming = LabelSet(labels=[Label(class_id=1, name="nut", alias="螺母", color="#C48E7A")])

    additions = LabelSetCompatibilityService().merge_into(target, incoming)

    assert [label.name for label in additions] == ["nut"]
    assert [label.name for label in target.labels] == ["bolt", "nut"]
    target.labels[1].alias = "修改后别名"
    assert incoming.labels[0].alias == "螺母"
