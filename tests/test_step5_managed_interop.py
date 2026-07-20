"""步骤五受管 X-AnyLabeling/LabelMe 目录双向互操作回归。"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import pytest
from PIL import Image

from datumdock.domain.models import Label, new_id, utc_now
from datumdock.i18n.catalog import LocaleService
from datumdock.services.dataset_library import DatasetLibraryService
from datumdock.services.managed_interop import (
    ExternalLabelResolution,
    InteropError,
    InteropIssueSeverity,
    XAnyExportRequest,
    XAnyImportCommitRequest,
    XAnyImportPreflightRequest,
    XAnyLabelingInteropService,
)
from datumdock.services.managed_labels import LabelSetService
from datumdock.services.sample_repository import DatasetSampleRepository, SampleRepositoryError
from datumdock.services.tasks import TaskState
from datumdock.ui.application_shell import ApplicationShell
from datumdock.ui.managed_gateway import ManagedDatasetGateway
from datumdock.ui.managed_interop_dialogs import (
    ManagedXAnyExportDialog,
    ManagedXAnyImportDialog,
)


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _payload(filename: str, *, label: str = "part") -> dict:
    return {
        "version": "5.5.0",
        "flags": {"external": True},
        "customRoot": {"source": "x-anylabeling"},
        "imagePath": filename,
        "imageData": None,
        "imageWidth": 80,
        "imageHeight": 50,
        "shapes": [
            {
                "label": "outline",
                "points": [[1, 1], [30, 1], [20, 20]],
                "shape_type": "polygon",
                "attributes": {"keep": True},
                "group_id": 7,
            },
            {
                "label": label,
                "points": [[10, 8], [45, 35]],
                "shape_type": "rectangle",
                "description": "外部矩形",
                "score": 0.91,
            },
        ],
    }


def _new_label(name: str = "part", class_id: int = 0) -> Label:
    timestamp = utc_now()
    return Label(
        id=new_id(),
        class_id=class_id,
        name=name,
        alias="零件",
        description="从互操作导入",
        color="#4D8FBF",
        created_at=timestamp,
        modified_at=timestamp,
    )


def test_preflight_pairs_recursively_and_never_modifies_source(tmp_path: Path) -> None:
    """递归配对、缺失 JSON 和孤立 JSON 都只产生报告。"""

    source = tmp_path / "external"
    nested = source / "nested"
    nested.mkdir(parents=True)
    image = nested / "sample.png"
    Image.new("RGB", (80, 50), (90, 120, 150)).save(image)
    annotation = nested / "sample.json"
    annotation.write_text(json.dumps(_payload(image.name)), encoding="utf-8")
    bare = source / "bare.jpg"
    Image.new("RGB", (80, 50), (120, 100, 80)).save(bare)
    orphan = source / "orphan.json"
    orphan.write_text(json.dumps(_payload("orphan.png")), encoding="utf-8")
    before = {path: _hash(path) for path in (image, annotation, bare, orphan)}

    library = DatasetLibraryService(tmp_path / "library")
    dataset = library.create_dataset("互操作").dataset
    service = XAnyLabelingInteropService(library, dataset.id)
    preflight = service.preflight_import(XAnyImportPreflightRequest(dataset.id, source))

    assert preflight.discovered_image_count == 2
    assert len(preflight.items) == 2
    assert preflight.external_labels[0].matched_label_id is None
    assert {issue.code for issue in preflight.issues} >= {
        "missing_annotation",
        "orphan_annotation",
    }
    assert all(issue.severity != InteropIssueSeverity.ERROR for issue in preflight.issues)
    assert {path: _hash(path) for path in before} == before
    service.discard_import_preflight(preflight)


def test_import_preserves_mixed_shapes_and_export_strips_private_fields(tmp_path: Path) -> None:
    """图片、可编辑矩形与只读兼容 shape 完成受管导入和独立导出。"""

    source = tmp_path / "external"
    source.mkdir()
    image = source / "sample.png"
    Image.new("RGB", (80, 50), (90, 120, 150)).save(image)
    annotation = source / "sample.json"
    annotation.write_text(json.dumps(_payload(image.name)), encoding="utf-8")
    source_hashes = {path: _hash(path) for path in (image, annotation)}

    library = DatasetLibraryService(tmp_path / "library")
    dataset = library.create_dataset("互操作").dataset
    service = XAnyLabelingInteropService(library, dataset.id)
    preflight = service.preflight_import(XAnyImportPreflightRequest(dataset.id, source))
    label = _new_label()
    report = service.commit_import(
        XAnyImportCommitRequest(
            dataset.id,
            preflight,
            {},
            (ExternalLabelResolution("part", label.id),),
            (label,),
        )
    )

    assert len(report.imported_sample_ids) == 1
    assert report.compatibility_shape_count == 1
    assert not report.failures
    assert {path: _hash(path) for path in source_hashes} == source_hashes
    paths = library.dataset_repository.paths(dataset.id)
    repository = DatasetSampleRepository(paths, dataset.id)
    sample = repository.get_sample(report.imported_sample_ids[0])
    assert sample is not None
    assert sample.annotation_count == 1
    managed_payload = json.loads(
        repository.resolve_path(sample.annotation_path, "pool/annotations").read_text(
            encoding="utf-8"
        )
    )
    assert [shape["shape_type"] for shape in managed_payload["shapes"]] == [
        "polygon",
        "rectangle",
    ]
    assert managed_payload["customRoot"] == {"source": "x-anylabeling"}
    assert managed_payload["shapes"][0]["attributes"] == {"keep": True}

    target = tmp_path / "xany-export"
    export_preflight = service.preflight_export(XAnyExportRequest(dataset.id, target))
    export_report = service.export(export_preflight)
    exported = json.loads((target / f"{Path(sample.filename).stem}.json").read_text("utf-8"))

    assert export_report.image_count == 1
    assert export_report.rectangle_count == 1
    assert exported["imagePath"] == sample.filename
    assert "datumdock_" not in json.dumps(exported)
    assert exported["shapes"][0]["attributes"] == {"keep": True}
    assert (target / "labels.txt").read_text("utf-8").splitlines() == ["part", "outline"]


def test_unknown_rectangle_is_read_only_preserved_and_roundtrips(tmp_path: Path) -> None:
    """用户未映射的未知矩形仍以只读兼容负载导入和导出。"""

    source = tmp_path / "external"
    source.mkdir()
    image = source / "unknown.png"
    Image.new("RGB", (80, 50), (40, 80, 120)).save(image)
    payload = _payload(image.name, label="external_only")
    (source / "unknown.json").write_text(json.dumps(payload), encoding="utf-8")
    library = DatasetLibraryService(tmp_path / "library")
    dataset = library.create_dataset("未知标签").dataset
    service = XAnyLabelingInteropService(library, dataset.id)
    preflight = service.preflight_import(XAnyImportPreflightRequest(dataset.id, source))

    report = service.commit_import(
        XAnyImportCommitRequest(
            dataset.id,
            preflight,
            {},
            (ExternalLabelResolution("external_only", None),),
        )
    )
    assert len(report.imported_sample_ids) == 1
    sample = service.samples.get_sample(report.imported_sample_ids[0])
    assert sample is not None
    assert sample.annotation_count == 0

    target = tmp_path / "roundtrip"
    service.export(service.preflight_export(XAnyExportRequest(dataset.id, target)))
    exported = json.loads((target / f"{Path(sample.filename).stem}.json").read_text("utf-8"))
    rectangle = next(shape for shape in exported["shapes"] if shape["shape_type"] == "rectangle")
    assert rectangle["label"] == "external_only"
    assert rectangle["description"] == "外部矩形"
    assert "datumdock_" not in json.dumps(rectangle)


def test_source_change_and_path_escape_are_blocked(tmp_path: Path) -> None:
    """来源变化要求重新预检，危险 imagePath 不进入受管池。"""

    source = tmp_path / "external"
    source.mkdir()
    image = source / "sample.png"
    Image.new("RGB", (80, 50), (50, 70, 90)).save(image)
    payload = _payload("../sample.png")
    annotation = source / "sample.json"
    annotation.write_text(json.dumps(payload), encoding="utf-8")
    library = DatasetLibraryService(tmp_path / "library")
    dataset = library.create_dataset("路径安全").dataset
    service = XAnyLabelingInteropService(library, dataset.id)
    blocked = service.preflight_import(XAnyImportPreflightRequest(dataset.id, source))
    assert blocked.blocking_count == 1
    report = service.commit_import(XAnyImportCommitRequest(dataset.id, blocked, {}))
    assert report.failures
    assert service.samples.count_active() == 0

    annotation.write_text(json.dumps(_payload(image.name)), encoding="utf-8")
    valid = service.preflight_import(XAnyImportPreflightRequest(dataset.id, source))
    Image.new("RGB", (80, 50), (1, 2, 3)).save(image)
    label = _new_label()
    changed = service.commit_import(
        XAnyImportCommitRequest(
            dataset.id,
            valid,
            {},
            (ExternalLabelResolution("part", label.id),),
            (label,),
        )
    )
    assert any("发生变化" in message for message in changed.failures.values())
    assert service.samples.count_active() == 0


def test_unannotated_image_exports_standard_empty_json(tmp_path: Path) -> None:
    """没有 JSON 的图片导入后可导出为空 shapes 的标准交换文档。"""

    source = tmp_path / "external"
    source.mkdir()
    image = source / "negative.png"
    Image.new("L", (80, 50), 128).save(image)
    library = DatasetLibraryService(tmp_path / "library")
    dataset = library.create_dataset("负样本").dataset
    service = XAnyLabelingInteropService(library, dataset.id)
    preflight = service.preflight_import(XAnyImportPreflightRequest(dataset.id, source))
    imported = service.commit_import(XAnyImportCommitRequest(dataset.id, preflight, {}))
    assert imported.unannotated_sample_ids == imported.imported_sample_ids

    target = tmp_path / "empty-export"
    report = service.export(service.preflight_export(XAnyExportRequest(dataset.id, target)))
    exported = json.loads(next(target.glob("*.json")).read_text("utf-8"))
    assert report.empty_annotation_count == 1
    assert exported["shapes"] == []
    assert exported["imageData"] is None
    assert exported["imageWidth"] == 80
    assert exported["imageHeight"] == 50


def test_export_rejects_existing_target(tmp_path: Path) -> None:
    """即使目标目录为空也不能覆盖，避免与用户文件混合。"""

    library = DatasetLibraryService(tmp_path / "library")
    dataset = library.create_dataset("目标安全").dataset
    service = XAnyLabelingInteropService(library, dataset.id)
    target = tmp_path / "existing"
    target.mkdir()
    with pytest.raises(InteropError, match="尚不存在"):
        service.preflight_export(XAnyExportRequest(dataset.id, target))


def _wait_task(gateway: ManagedDatasetGateway, task_id: str):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        snapshot = gateway.task_snapshot(task_id)
        if snapshot.state not in {TaskState.QUEUED, TaskState.RUNNING}:
            return snapshot, gateway.task_result(task_id)
        time.sleep(0.02)
    raise AssertionError("后台互操作任务未在时限内完成")


def test_managed_gateway_runs_import_and_export_as_dataset_bound_tasks(tmp_path: Path) -> None:
    """正式 Gateway 返回稳定任务 ID，结果始终绑定创建任务的数据集。"""

    source = tmp_path / "external"
    source.mkdir()
    image = source / "negative.png"
    Image.new("RGB", (80, 50), (20, 40, 60)).save(image)
    library = DatasetLibraryService(tmp_path / "library")
    first = library.create_dataset("第一个").dataset
    second = library.create_dataset("第二个").dataset
    gateway = ManagedDatasetGateway(library)
    try:
        preflight_task = gateway.start_xany_import_preflight(first.id, source)
        snapshot, preflight = _wait_task(gateway, preflight_task)
        assert snapshot.dataset_id == first.id
        assert snapshot.state == TaskState.COMPLETED
        commit_task = gateway.start_xany_import_commit(
            XAnyImportCommitRequest(first.id, preflight, {})
        )
        snapshot, report = _wait_task(gateway, commit_task)
        assert snapshot.dataset_id == first.id
        assert len(report.imported_sample_ids) == 1
        assert gateway.query_samples(first.id).total == 1
        assert gateway.query_samples(second.id).total == 0

        target = tmp_path / "gateway-export"
        export_preflight_task = gateway.start_xany_export_preflight(
            XAnyExportRequest(first.id, target)
        )
        _snapshot, export_preflight = _wait_task(gateway, export_preflight_task)
        assert export_preflight.can_export
        export_task = gateway.start_xany_export_commit(first.id, export_preflight_task)
        snapshot, export_report = _wait_task(gateway, export_task)
        assert snapshot.dataset_id == first.id
        assert export_report.image_count == 1
        assert target.is_dir()
    finally:
        gateway.close()


def test_normal_shell_opens_real_xany_dialogs(qtbot, tmp_path: Path) -> None:
    """普通模式两个入口创建真实向导，而不是步骤一通用预览弹窗。"""

    library = DatasetLibraryService(tmp_path / "library")
    dataset = library.create_dataset("界面互操作").dataset
    gateway = ManagedDatasetGateway(library)
    window = ApplicationShell(LocaleService(), gateway)
    qtbot.addWidget(window)
    window.show()
    window.open_dialog(f"xany_import:{dataset.id}")
    assert isinstance(window._active_dialogs[-1], ManagedXAnyImportDialog)
    window._active_dialogs[-1].reject()
    qtbot.waitUntil(lambda: not window._active_dialogs)

    window.open_dialog(f"xany_export:{dataset.id}")
    assert isinstance(window._active_dialogs[-1], ManagedXAnyExportDialog)
    window._active_dialogs[-1].reject()
    qtbot.waitUntil(lambda: not window._active_dialogs)
    window.close()


def test_import_issue_table_localizes_system_messages(qtbot, tmp_path: Path) -> None:
    """英文界面翻译系统诊断，但不改写外部文件名和数据集标签内容。"""

    library = DatasetLibraryService(tmp_path / "library")
    dataset = library.create_dataset("本地化互操作").dataset
    service = XAnyLabelingInteropService(library, dataset.id)
    source = tmp_path / "localized-source"
    source.mkdir()
    Image.new("RGB", (32, 24), "white").save(source / "plain.png")
    preflight = service.preflight_import(XAnyImportPreflightRequest(dataset.id, source))
    gateway = ManagedDatasetGateway(library)
    dialog = ManagedXAnyImportDialog(LocaleService("en_US"), gateway, dataset.id)
    qtbot.addWidget(dialog)
    dialog.preflight = preflight
    dialog._show_preflight()

    assert dialog.issue_table.item(0, 0).text() == "Warning"
    assert dialog.issue_table.item(0, 2).text().startswith("The image has no same-stem JSON")
    gateway.close()


def test_one_hundred_images_import_restart_and_export(tmp_path: Path) -> None:
    """100 张图片可连续导入、重启服务、分页读取并完整导出。"""

    source = tmp_path / "hundred"
    source.mkdir()
    for index in range(100):
        Image.new(
            "RGB",
            (24, 16),
            (index, (index * 3) % 256, (index * 7) % 256),
        ).save(source / f"sample_{index:03d}.png")
    library_root = tmp_path / "library"
    library = DatasetLibraryService(library_root)
    dataset = library.create_dataset("百图闭环").dataset
    service = XAnyLabelingInteropService(library, dataset.id)
    preflight = service.preflight_import(XAnyImportPreflightRequest(dataset.id, source))
    report = service.commit_import(XAnyImportCommitRequest(dataset.id, preflight, {}))
    assert len(report.imported_sample_ids) == 100
    assert not report.failures

    restarted = DatasetLibraryService(library_root)
    gateway = ManagedDatasetGateway(restarted)
    try:
        first_page = gateway.query_samples(dataset.id, limit=37)
        assert first_page.total == 100
        assert len(first_page.items) == 37
    finally:
        gateway.close()
    export_service = XAnyLabelingInteropService(restarted, dataset.id)
    target = tmp_path / "hundred-export"
    exported = export_service.export(
        export_service.preflight_export(XAnyExportRequest(dataset.id, target))
    )
    assert exported.image_count == 100
    assert exported.json_count == 100
    assert len(tuple(target.glob("*.png"))) == 100
    assert len(tuple(target.glob("*.json"))) == 100


def test_sqlite_failure_rolls_back_all_published_interop_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """索引失败时图片、缩略图和 JSON 必须全部退回暂存并清理。"""

    source = tmp_path / "external"
    source.mkdir()
    image = source / "sample.png"
    Image.new("RGB", (80, 50), (20, 60, 100)).save(image)
    annotation = source / "sample.json"
    annotation.write_text(json.dumps(_payload(image.name)), encoding="utf-8")
    before = {path: _hash(path) for path in (image, annotation)}
    library = DatasetLibraryService(tmp_path / "library")
    dataset = library.create_dataset("故障回滚").dataset
    service = XAnyLabelingInteropService(library, dataset.id)
    preflight = service.preflight_import(XAnyImportPreflightRequest(dataset.id, source))
    label = _new_label()

    def fail_index(*_args, **_kwargs) -> None:
        raise SampleRepositoryError("模拟 SQLite 失败")

    monkeypatch.setattr(service.samples, "add_sample_with_annotation", fail_index)
    report = service.commit_import(
        XAnyImportCommitRequest(
            dataset.id,
            preflight,
            {},
            (ExternalLabelResolution("part", label.id),),
            (label,),
        )
    )

    paths = library.dataset_repository.paths(dataset.id)
    assert report.failures
    assert service.samples.count_active() == 0
    assert not tuple(paths.images.glob("*.png"))
    assert not tuple(paths.annotations.glob("*.json"))
    assert not tuple(paths.thumbnails.glob("*.png"))
    assert {path: _hash(path) for path in before} == before


def test_label_revision_change_invalidates_import_preflight(tmp_path: Path) -> None:
    """映射期间标签集变化必须要求重新预检，不能套用陈旧稳定 ID。"""

    source = tmp_path / "external"
    source.mkdir()
    image = source / "sample.png"
    Image.new("RGB", (80, 50), (20, 60, 100)).save(image)
    (source / "sample.json").write_text(json.dumps(_payload(image.name)), encoding="utf-8")
    library = DatasetLibraryService(tmp_path / "library")
    dataset = library.create_dataset("修订冲突").dataset
    service = XAnyLabelingInteropService(library, dataset.id)
    preflight = service.preflight_import(XAnyImportPreflightRequest(dataset.id, source))
    LabelSetService(library).add_label(
        dataset.id,
        class_id=0,
        name="other",
        alias="其他",
    )

    with pytest.raises(InteropError, match="重新执行导入预检"):
        service.commit_import(XAnyImportCommitRequest(dataset.id, preflight, {}))
    service.discard_import_preflight(preflight)
    assert service.samples.count_active() == 0


def test_export_failure_never_publishes_incomplete_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """临时目录写入失败时最终目标必须保持不存在。"""

    source = tmp_path / "external"
    source.mkdir()
    Image.new("RGB", (80, 50), (10, 20, 30)).save(source / "sample.png")
    library = DatasetLibraryService(tmp_path / "library")
    dataset = library.create_dataset("导出故障").dataset
    service = XAnyLabelingInteropService(library, dataset.id)
    preflight = service.preflight_import(XAnyImportPreflightRequest(dataset.id, source))
    service.commit_import(XAnyImportCommitRequest(dataset.id, preflight, {}))
    target = tmp_path / "never-published"
    export_preflight = service.preflight_export(XAnyExportRequest(dataset.id, target))

    def fail_copy(*_args, **_kwargs) -> None:
        raise OSError("模拟磁盘写入失败")

    monkeypatch.setattr("datumdock.services.managed_interop.shutil.copy2", fail_copy)
    with pytest.raises(OSError, match="模拟磁盘"):
        service.export(export_preflight)
    assert not target.exists()
