"""工程骨架的最小配置验证。"""

from pathlib import Path

from datumdock.domain.models import Label
from datumdock.services.labels import LabelService
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
