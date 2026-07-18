"""受管数据集、LabelMe 与 YOLO 导出的核心回归测试。"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from datumdock.domain.models import ExportRequest, Label, RectangleShape
from datumdock.services.dataset import DatasetPoolService
from datumdock.services.exporting import SplitPlanner, XAnyLabelingExporter, YoloDetectionExporter
from datumdock.services.labels import LabelService
from datumdock.services.storage import ProjectIndexRepository
from datumdock.services.workspace import WorkspaceService


def create_source_image(path: Path, color: tuple[int, int, int]) -> None:
    """创建小型测试图片，避免测试依赖仓库外的真实数据集。"""

    Image.new("RGB", (80, 40), color).save(path)


def create_project_with_dataset(tmp_path: Path):
    """构造最小受管工作区、项目、数据集与单标签。"""

    workspace_service = WorkspaceService()
    workspace = workspace_service.create_workspace(tmp_path / "workspace")
    root = tmp_path / "workspace"
    project = workspace_service.create_project(root, workspace, "零件检测")
    dataset = workspace_service.create_dataset(root, project, "训练池")
    label = Label(
        class_id=0,
        name="part",
        alias="零件",
        description="测试零件",
        color="#78978C",
    )
    LabelService().add_label(project.label_set, label)
    workspace_service.save_project(root, project)
    return root, project, dataset, label


def test_import_annotation_and_yolo_export_are_deterministic(tmp_path: Path) -> None:
    """验证受管导入不改写源图片，自动保存标注且 YOLO 划分可重复。"""

    root, project, dataset, label = create_project_with_dataset(tmp_path)
    source_one = tmp_path / "source-one.jpg"
    source_two = tmp_path / "source-two.png"
    create_source_image(source_one, (200, 120, 80))
    create_source_image(source_two, (80, 120, 200))
    pool = DatasetPoolService()
    report = pool.import_images(root, project, dataset, [source_one, source_two])
    assert len(report.imported_sample_ids) == 2
    assert source_one.is_file()
    assert source_two.is_file()

    index = ProjectIndexRepository(root / "projects" / project.id / "project-index.sqlite")
    samples = index.list_samples(dataset.id, limit=10)
    for sample in samples:
        document = pool.load_document(sample, project)
        document.rectangles.append(RectangleShape(label_id=label.id, x1=4, y1=5, x2=44, y2=25))
        pool.save_document(root, project, sample, document)

    samples = index.list_samples(dataset.id, limit=10)
    planner = SplitPlanner()
    first_split = planner.plan(samples, (80, 10, 10), seed=7)
    second_split = planner.plan(samples, (80, 10, 10), seed=7)
    assert first_split == second_split
    output = tmp_path / "yolo-export"
    request = ExportRequest(dataset_id=dataset.id, output_directory=str(output), seed=7)
    YoloDetectionExporter().export(request, project.label_set, first_split)
    assert (output / "data.yaml").is_file()
    label_files = list((output / "labels").rglob("*.txt"))
    assert len(label_files) == 2
    assert all(item.read_text(encoding="utf-8").startswith("0 ") for item in label_files)


def test_xanylabeling_export_preserves_unsupported_shape(tmp_path: Path) -> None:
    """验证交换导出不会丢弃受管 JSON 中暂不支持的 shape 负载。"""

    root, project, dataset, label = create_project_with_dataset(tmp_path)
    source = tmp_path / "source.jpg"
    create_source_image(source, (100, 100, 100))
    pool = DatasetPoolService()
    report = pool.import_images(root, project, dataset, [source])
    index = ProjectIndexRepository(root / "projects" / project.id / "project-index.sqlite")
    sample = index.get_sample(report.imported_sample_ids[0])
    assert sample is not None
    document = pool.load_document(sample, project)
    document.rectangles.append(RectangleShape(label_id=label.id, x1=1, y1=1, x2=30, y2=30))
    document.unsupported_shapes.append(
        {
            "label": "legacy_polygon",
            "points": [[1, 1], [2, 2], [3, 1]],
            "shape_type": "polygon",
            "attributes": {"source": "legacy"},
        }
    )
    pool.save_document(root, project, sample, document)
    exchange = tmp_path / "xanylabeling-export"
    XAnyLabelingExporter().export(exchange, [sample], project.label_set)
    payload = (exchange / f"{Path(sample.filename).stem}.json").read_text(encoding="utf-8")
    assert "legacy_polygon" in payload
    assert '"shape_type": "rectangle"' in payload


def test_confirmed_similar_images_are_not_split_across_yolo_partitions(tmp_path: Path) -> None:
    """确认的近似图组必须整体进入同一划分，避免验证集泄露训练集近似样本。"""

    root, project, dataset, _ = create_project_with_dataset(tmp_path)
    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    create_source_image(first, (100, 100, 100))
    create_source_image(second, (102, 100, 100))
    pool = DatasetPoolService()
    report = pool.import_images(root, project, dataset, [first, second])
    assert len(report.similar_sample_ids) == 1
    index = ProjectIndexRepository(root / "projects" / project.id / "project-index.sqlite")
    groups = index.list_similarity_groups()
    assert len(groups) == 1
    group_id = next(iter(groups))
    index.set_similarity_group_confirmed(group_id, True)
    samples = index.list_samples(dataset.id, limit=10)
    result = SplitPlanner().plan(
        samples,
        (50, 50, 0),
        seed=9,
        similarity_groups=index.confirmed_similarity_mapping(),
    )
    partitions = [
        set(sample.id for sample in partition)
        for partition in (result.train, result.val, result.test)
    ]
    assert any(set(report.imported_sample_ids).issubset(partition) for partition in partitions)


def test_small_dataset_deletion_can_be_restored_from_managed_trash(tmp_path: Path) -> None:
    """回收站恢复必须同时还原图片、标注与 SQLite 样本索引。"""

    root, project, dataset, _ = create_project_with_dataset(tmp_path)
    source = tmp_path / "source.png"
    create_source_image(source, (80, 110, 150))
    pool = DatasetPoolService()
    report = pool.import_images(root, project, dataset, [source])
    index = ProjectIndexRepository(root / "projects" / project.id / "project-index.sqlite")
    sample = index.get_sample(report.imported_sample_ids[0])
    assert sample is not None
    pool.delete_sample(root, project, sample, move_to_trash=True)
    assert index.get_sample(sample.id) is None
    assert [item.id for item in pool.list_trashed_samples(root, project)] == [sample.id]

    restored = pool.restore_sample(root, project, sample.id)
    assert restored.id == sample.id
    assert Path(restored.image_path).is_file()
    assert index.get_sample(sample.id) is not None
