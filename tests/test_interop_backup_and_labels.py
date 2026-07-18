"""标签迁移、X-AnyLabeling 互操作与项目备份的回归测试。"""

from __future__ import annotations

import json
import shutil
import zipfile
from pathlib import Path

import pytest
from test_dataset_workflow import create_project_with_dataset, create_source_image

from datumdock.domain.models import RectangleShape
from datumdock.services.backup import DatasetTransferService, ProjectBackupService
from datumdock.services.dataset import DatasetPoolService
from datumdock.services.exporting import XAnyLabelingExporter
from datumdock.services.interop import XAnyLabelingInteropService
from datumdock.services.labels import LabelMigrationService
from datumdock.services.storage import ProjectIndexRepository
from datumdock.services.workspace import WorkspaceService


def test_xanylabeling_import_preserves_rectangle_compatibility_payload(tmp_path: Path) -> None:
    """导入后编辑矩形仍须保留外部工具定义的扩展字段。"""

    root, project, dataset, _ = create_project_with_dataset(tmp_path)
    source_directory = tmp_path / "external"
    source_directory.mkdir()
    image_path = source_directory / "external.png"
    create_source_image(image_path, (90, 120, 150))
    image_path.with_suffix(".json").write_text(
        json.dumps(
            {
                "version": "5.4.1",
                "flags": {"checked": True},
                "shapes": [
                    {
                        "label": "connector",
                        "points": [[3, 4], [40, 25]],
                        "group_id": 8,
                        "description": "外部描述",
                        "shape_type": "rectangle",
                        "flags": {"difficult": True},
                        "attributes": {"origin": "xanylabeling"},
                        "score": 0.8,
                    }
                ],
                "imagePath": "external.png",
                "imageData": None,
                "imageHeight": 40,
                "imageWidth": 80,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    report = XAnyLabelingInteropService().import_directory(root, project, dataset, source_directory)
    assert len(report.imported_sample_ids) == 1
    connector = next(label for label in project.label_set.labels if label.name == "connector")
    index = ProjectIndexRepository(root / "projects" / project.id / "project-index.sqlite")
    sample = index.get_sample(report.imported_sample_ids[0])
    assert sample is not None
    document = DatasetPoolService().load_document(sample, project)
    assert document.rectangles[0].label_id == connector.id
    assert document.rectangles[0].compatibility_payload["attributes"] == {"origin": "xanylabeling"}

    exchange = tmp_path / "exchange"
    XAnyLabelingExporter().export(exchange, [sample], project.label_set)
    exported_path = exchange / f"{Path(sample.filename).stem}.json"
    exported = json.loads(exported_path.read_text(encoding="utf-8"))
    rectangle = exported["shapes"][0]
    assert rectangle["group_id"] == 8
    assert rectangle["description"] == "外部描述"
    assert rectangle["flags"] == {"difficult": True}
    assert rectangle["attributes"] == {"origin": "xanylabeling"}


def test_label_name_migration_keeps_existing_rectangles_editable(tmp_path: Path) -> None:
    """训练名变更后，历史矩形仍应指向同一个稳定标签 ID。"""

    root, project, dataset, label = create_project_with_dataset(tmp_path)
    source = tmp_path / "source.png"
    create_source_image(source, (120, 100, 80))
    pool = DatasetPoolService()
    imported = pool.import_images(root, project, dataset, [source])
    index = ProjectIndexRepository(root / "projects" / project.id / "project-index.sqlite")
    sample = index.get_sample(imported.imported_sample_ids[0])
    assert sample is not None
    document = pool.load_document(sample, project)
    document.rectangles.append(RectangleShape(label_id=label.id, x1=5, y1=5, x2=30, y2=20))
    pool.save_document(root, project, sample, document)

    preview = LabelMigrationService(pool.labelme_repository).migrate_training_name(
        root, project, label.id, "renamed_part"
    )
    assert preview.sample_count == 1
    reloaded = pool.load_document(sample, project)
    assert reloaded.rectangles[0].label_id == label.id
    assert "renamed_part" in Path(sample.annotation_path).read_text(encoding="utf-8")


def test_backup_validation_and_dataset_transfer_keep_metadata_consistent(tmp_path: Path) -> None:
    """备份篡改必须拒绝，受管转移必须保留标签查询索引。"""

    root, project, dataset, label = create_project_with_dataset(tmp_path)
    source = tmp_path / "source.png"
    create_source_image(source, (40, 140, 120))
    pool = DatasetPoolService()
    imported = pool.import_images(root, project, dataset, [source])
    index = ProjectIndexRepository(root / "projects" / project.id / "project-index.sqlite")
    source_sample = index.get_sample(imported.imported_sample_ids[0])
    assert source_sample is not None
    document = pool.load_document(source_sample, project)
    document.rectangles.append(RectangleShape(label_id=label.id, x1=5, y1=5, x2=20, y2=20))
    pool.save_document(root, project, source_sample, document)

    target = WorkspaceService().create_dataset(root, project, "验证池")
    transferred = DatasetTransferService().transfer(
        root, project, dataset, target, [source_sample.id], move=False
    )
    target_sample = index.get_sample(transferred[0])
    assert target_sample is not None
    assert index.get_label_rows(target_sample.id) == index.get_label_rows(source_sample.id)

    workspace = WorkspaceService().open_workspace(root)
    backup_path = tmp_path / "project.ddbackup"
    backup = ProjectBackupService()
    backup.export_backup(root, project, backup_path)
    restored = backup.import_backup(root, workspace, backup_path)
    assert restored.id != project.id
    assert (root / "projects" / restored.id / "project-index.sqlite").is_file()

    tampered = tmp_path / "tampered.ddbackup"
    shutil.copy2(backup_path, tampered)
    with zipfile.ZipFile(tampered, "a") as archive:
        archive.writestr("project/unregistered.txt", "tampered")
    with pytest.raises(ValueError, match="未登记"):
        backup.import_backup(root, workspace, tampered)
