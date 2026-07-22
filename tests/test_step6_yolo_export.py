"""步骤六受管 YOLO Detection 划分、原子输出和隔离回归。"""

from __future__ import annotations

import hashlib
import shutil
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml
from PIL import Image

from datumdock.domain.models import (
    AnnotationDocument,
    DatasetSample,
    Label,
    LabelSet,
    RectangleShape,
    ReviewStatus,
    new_id,
)
from datumdock.i18n.catalog import LocaleService
from datumdock.services.annotations import AnnotationSaveRequest, AnnotationService
from datumdock.services.dataset_library import DatasetLibraryService
from datumdock.services.managed_labels import LabelSetService
from datumdock.services.managed_yolo import (
    DeterministicSplitPlanner,
    ExportCandidateSnapshot,
    ExportRectangleSnapshot,
    ExportScope,
    SplitRatios,
    YoloExportError,
    YoloExportRequest,
    YoloExportService,
)
from datumdock.services.sample_repository import DatasetSampleRepository
from datumdock.services.tasks import TaskState
from datumdock.ui.managed_gateway import ManagedDatasetGateway
from datumdock.ui.managed_yolo_dialog import ManagedYoloExportDialog
from datumdock.ui.prototype_models import CommandStatus, UiCommand


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _candidate(label: Label, number: int, *, content_hash: str | None = None):
    sample_id = new_id()
    rectangle = ExportRectangleSnapshot(new_id(), label.id, 5, 5, 25, 25)
    return ExportCandidateSnapshot(
        sample_id,
        f"image_{number:06d}.png",
        40,
        30,
        content_hash or f"{number:064x}",
        f"{number + 100:064x}",
        100,
        f"{number + 200:064x}",
        1,
        ReviewStatus.COMPLETED,
        (rectangle,),
    )


def test_split_planner_merges_exact_and_overlapping_confirmed_groups() -> None:
    """完全重复与重叠近似组按传递闭包进入同一集合。"""

    label = Label(class_id=0, name="part", alias="零件", color="#4D8FBF")
    label_set = LabelSet(labels=[label])
    first = _candidate(label, 1, content_hash="a" * 64)
    second = _candidate(label, 2, content_hash="a" * 64)
    third = _candidate(label, 3)
    fourth = _candidate(label, 4)
    fifth = _candidate(label, 5)
    sixth = _candidate(label, 6)
    groups = (
        (new_id(), (second.sample_id, third.sample_id)),
        (new_id(), (third.sample_id, fourth.sample_id)),
    )

    planner = DeterministicSplitPlanner()
    plan, statistics = planner.plan(
        (fifth, third, first, fourth, second, sixth),
        label_set,
        SplitRatios(60, 20, 20),
        17,
        groups,
    )
    repeated, _ = planner.plan(
        (second, fourth, first, fifth, sixth, third),
        label_set,
        SplitRatios(60, 20, 20),
        17,
        groups,
    )

    assigned = {
        sample_id: split_name
        for split_name in ("train", "val", "test")
        for sample_id in plan.sample_ids(split_name)
    }
    assert len({assigned[item.sample_id] for item in (first, second, third, fourth)}) == 1
    assert plan.fingerprint == repeated.fingerprint
    assert statistics.largest_group_size == 4


def test_split_planner_handles_ten_thousand_snapshots_without_order_dependency() -> None:
    """万级划分只处理轻量快照，并在稳定输入下保持可重复。"""

    label = Label(class_id=0, name="part", alias="零件", color="#4D8FBF")
    label_set = LabelSet(labels=[label])
    candidates = tuple(_candidate(label, number) for number in range(1, 10_001))
    planner = DeterministicSplitPlanner()
    started = time.perf_counter()
    first, _statistics = planner.plan(
        candidates,
        label_set,
        SplitRatios(),
        42,
        (),
    )
    second, _ = planner.plan(
        tuple(reversed(candidates)),
        label_set,
        SplitRatios(),
        42,
        (),
    )

    assert len(first.train) + len(first.val) + len(first.test) == 10_000
    assert first.fingerprint == second.fingerprint
    assert time.perf_counter() - started < 12


def test_split_requires_contiguous_class_ids_and_enough_components(tmp_path: Path) -> None:
    """类别空洞和不可拆分组不足都必须在写文件前明确阻断。"""

    label = Label(class_id=2, name="part", alias="零件", color="#4D8FBF")
    label_set = LabelSet(labels=[label])
    candidate = _candidate(label, 1)
    with pytest.raises(YoloExportError, match="不可拆分组数量不足"):
        DeterministicSplitPlanner().plan(
            (candidate,),
            label_set,
            SplitRatios(),
            1,
            (),
        )

    library = DatasetLibraryService(tmp_path / "library-gap")
    dataset = library.create_dataset("类别空洞").dataset
    library.update_label_set(dataset.id, label_set.model_copy(update={"id": dataset.label_set_id}))
    target = tmp_path / "gap-yolo"
    preflight = YoloExportService(library, dataset.id).preflight(
        YoloExportRequest(dataset.id, target, ratios=SplitRatios(100, 0, 0))
    )
    assert any(issue.code == "non_contiguous_class_ids" for issue in preflight.issues)
    assert not preflight.can_export


def _managed_dataset(
    tmp_path: Path,
    *,
    count: int = 6,
) -> tuple[DatasetLibraryService, str, Label, tuple[str, ...]]:
    library = DatasetLibraryService(tmp_path / "library")
    dataset = library.create_dataset("YOLO 导出").dataset
    label_set = LabelSetService(library).add_label(
        dataset.id,
        class_id=0,
        name="part",
        alias="零件",
    )
    label = label_set.labels[0]
    paths = library.dataset_repository.paths(dataset.id)
    repository = DatasetSampleRepository(paths, dataset.id)
    identifiers: list[str] = []
    for number in range(1, count + 1):
        filename = f"image_{number:06d}.png"
        image_path = paths.images / filename
        color_number = 1 if number == 2 else number
        Image.new("RGB", (80, 50), (color_number * 20, 80, 120)).save(image_path)
        file_hash = _digest(image_path)
        sample = DatasetSample(
            dataset_id=dataset.id,
            filename=filename,
            original_filename=filename,
            image_path=f"pool/images/{filename}",
            width=80,
            height=50,
            content_hash=("f" * 64 if number in {1, 2} else f"{number:064x}"),
            file_hash=file_hash,
            perceptual_hash=f"{number:016x}102030",
            imported_at=datetime.now(UTC).isoformat(),
        )
        repository.add_sample(sample)
        document = AnnotationDocument(
            sample_id=sample.id,
            image_filename=filename,
            image_width=80,
            image_height=50,
            document_version=1,
            rectangles=[RectangleShape(label_id=label.id, x1=10, y1=5, x2=50, y2=35)],
        )
        AnnotationService(library, dataset.id).save(
            AnnotationSaveRequest(dataset.id, sample.id, 1, "", document)
        )
        identifiers.append(sample.id)
    return library, dataset.id, label, tuple(identifiers)


def test_managed_yolo_export_is_atomic_portable_and_does_not_modify_pool(
    tmp_path: Path,
) -> None:
    """正式服务输出相对路径、配对标签，并保持受管数据集字节不变。"""

    library, dataset_id, _label, identifiers = _managed_dataset(tmp_path)
    service = YoloExportService(library, dataset_id)
    target = tmp_path / "exports" / "detector"
    target.parent.mkdir()
    request = YoloExportRequest(
        dataset_id,
        target,
        ExportScope.ALL,
        ratios=SplitRatios(60, 20, 20),
        seed=23,
    )
    dataset_root = library.dataset_repository.paths(dataset_id).root
    before = _tree_digest(dataset_root)

    preflight = service.preflight(request)
    assert preflight.can_export
    assert preflight.plan is not None
    report = service.exporter().export(preflight)

    assert report.image_count == len(identifiers)
    assert target.is_dir()
    assert _tree_digest(dataset_root) == before
    data = yaml.safe_load((target / "data.yaml").read_text(encoding="utf-8"))
    assert data == {
        "train": "images/train",
        "val": "images/val",
        "test": "images/test",
        "nc": 1,
        "names": {0: "part"},
    }
    assigned = {
        path.stem: split
        for split in ("train", "val", "test")
        for path in (target / "images" / split).glob("*.png")
    }
    repository = DatasetSampleRepository(library.dataset_repository.paths(dataset_id), dataset_id)
    samples = {sample.id: sample for sample in repository.all_active_samples()}
    assert (
        assigned[Path(samples[identifiers[0]].filename).stem]
        == assigned[Path(samples[identifiers[1]].filename).stem]
    )
    for split in ("train", "val", "test"):
        images = sorted((target / "images" / split).glob("*.png"))
        labels = sorted((target / "labels" / split).glob("*.txt"))
        assert [path.stem for path in images] == [path.stem for path in labels]


def test_hundred_image_export_is_complete_and_repeatable(tmp_path: Path) -> None:
    """百图导出必须完整，并在相同快照与种子下得到同一划分指纹。"""

    library, dataset_id, _label, identifiers = _managed_dataset(tmp_path, count=100)
    first_target = tmp_path / "hundred-yolo"
    service = YoloExportService(library, dataset_id)
    request = YoloExportRequest(
        dataset_id,
        first_target,
        ratios=SplitRatios(80, 10, 10),
        seed=20260723,
    )

    first_preflight = service.preflight(request)
    first_report = service.exporter().export(first_preflight)
    second_preflight = service.preflight(
        YoloExportRequest(
            dataset_id,
            tmp_path / "hundred-yolo-repeat",
            ratios=request.ratios,
            seed=request.seed,
        )
    )

    assert first_preflight.plan is not None
    assert second_preflight.plan is not None
    assert first_preflight.plan.fingerprint == second_preflight.plan.fingerprint
    assert first_report.image_count == len(identifiers) == 100
    actual_counts = tuple(
        len(list((first_target / "images" / split_name).glob("*.png")))
        for split_name in ("train", "val", "test")
    )
    assert sum(actual_counts) == 100
    assert all(
        abs(actual - target) <= 2
        for actual, target in zip(actual_counts, (80, 10, 10), strict=True)
    )


def test_zero_ratio_and_explicit_negative_sample(tmp_path: Path) -> None:
    """零比例集合写 null，明确完成的零框图片输出空标签文件。"""

    library, dataset_id, _label, _identifiers = _managed_dataset(tmp_path, count=2)
    paths = library.dataset_repository.paths(dataset_id)
    repository = DatasetSampleRepository(paths, dataset_id)
    filename = "image_000003.png"
    image_path = paths.images / filename
    Image.new("RGB", (80, 50), (15, 25, 35)).save(image_path)
    negative = DatasetSample(
        dataset_id=dataset_id,
        filename=filename,
        original_filename=filename,
        image_path=f"pool/images/{filename}",
        width=80,
        height=50,
        content_hash="e" * 64,
        file_hash=_digest(image_path),
        perceptual_hash="0000000000000003102030",
        review_status=ReviewStatus.COMPLETED,
        imported_at=datetime.now(UTC).isoformat(),
    )
    repository.add_sample(negative)
    target = tmp_path / "negative-yolo"
    request = YoloExportRequest(
        dataset_id,
        target,
        ratios=SplitRatios(100, 0, 0),
        include_completed_negatives=True,
    )

    preflight = YoloExportService(library, dataset_id).preflight(request)
    report = YoloExportService(library, dataset_id).exporter().export(preflight)

    assert report.negative_count == 1
    assert (target / "labels" / "train" / "image_000003.txt").read_bytes() == b""
    data = yaml.safe_load((target / "data.yaml").read_text(encoding="utf-8"))
    assert data["val"] is None
    assert data["test"] is None


def test_existing_target_cancel_and_stale_snapshot_never_publish(tmp_path: Path) -> None:
    """目录冲突、取消和预检后变化均不得产生最终成功目录。"""

    library, dataset_id, _label, identifiers = _managed_dataset(tmp_path, count=3)
    existing = tmp_path / "existing"
    existing.mkdir()
    with pytest.raises(YoloExportError, match="尚不存在"):
        YoloExportService(library, dataset_id).preflight(
            YoloExportRequest(dataset_id, existing, ratios=SplitRatios(100, 0, 0))
        )

    cancelled_target = tmp_path / "cancelled"
    service = YoloExportService(library, dataset_id)
    cancelled_preflight = service.preflight(
        YoloExportRequest(dataset_id, cancelled_target, ratios=SplitRatios(100, 0, 0))
    )
    cancelled_report = service.exporter().export(
        cancelled_preflight,
        cancelled=lambda: True,
    )
    assert cancelled_report.cancelled is True
    assert not cancelled_target.exists()

    stale_target = tmp_path / "stale"
    stale_preflight = service.preflight(
        YoloExportRequest(dataset_id, stale_target, ratios=SplitRatios(100, 0, 0))
    )
    repository = DatasetSampleRepository(library.dataset_repository.paths(dataset_id), dataset_id)
    sample = repository.get_sample(identifiers[0])
    assert sample is not None
    image_path = repository.resolve_path(sample.image_path, "pool/images")
    Image.new("RGB", (80, 50), (1, 2, 3)).save(image_path)
    with pytest.raises(YoloExportError, match="发生变化"):
        service.exporter().export(stale_preflight)
    assert not stale_target.exists()


def test_copy_failure_is_structured_and_leaves_no_final_or_temporary_directory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """复制失败必须转换为业务错误，并完整清理临时发布目录。"""

    library, dataset_id, _label, _identifiers = _managed_dataset(tmp_path, count=3)
    target = tmp_path / "copy-failed"
    service = YoloExportService(library, dataset_id)
    preflight = service.preflight(
        YoloExportRequest(dataset_id, target, ratios=SplitRatios(100, 0, 0))
    )

    def fail_copy(_source: Path, _target: Path) -> None:
        raise OSError("simulated copy failure")

    monkeypatch.setattr(shutil, "copy2", fail_copy)
    with pytest.raises(YoloExportError, match="YOLO 导出失败"):
        service.exporter().export(preflight)

    assert not target.exists()
    assert not tuple(tmp_path.glob(".datumdock-yolo-copy-failed-*"))


def test_default_split_persists_and_initializes_real_export_dialog(qtbot, tmp_path: Path) -> None:
    """默认比例只写入全局设置，并在下一次真实导出向导中恢复。"""

    library, dataset_id, _label, _identifiers = _managed_dataset(tmp_path, count=3)
    gateway = ManagedDatasetGateway(library)
    result = gateway.dispatch(UiCommand("settings.update", {"default_split": (70, 20, 10)}))
    assert result.status == CommandStatus.APPLIED
    gateway.close()

    restored = ManagedDatasetGateway(library)
    assert restored.settings.default_split == (70, 20, 10)
    dialog = ManagedYoloExportDialog(LocaleService(), restored, dataset_id)
    qtbot.addWidget(dialog)
    assert (
        dialog.train_ratio.value(),
        dialog.val_ratio.value(),
        dialog.test_ratio.value(),
    ) == (70, 20, 10)
    dialog.close()
    restored.close()


def test_managed_yolo_dialog_runs_real_preflight_and_export(qtbot, tmp_path: Path) -> None:
    """普通模式向导通过 Gateway 完成真实预检和后台导出。"""

    library, dataset_id, _label, _identifiers = _managed_dataset(tmp_path, count=3)
    gateway = ManagedDatasetGateway(library)
    dialog = ManagedYoloExportDialog(LocaleService(), gateway, dataset_id)
    qtbot.addWidget(dialog)
    dialog.parent_directory = tmp_path
    dialog.parent_path.setText(str(tmp_path))
    dialog.target_name.setText("qt-yolo")
    dialog.train_ratio.setValue(100)
    dialog.val_ratio.setValue(0)
    dialog.test_ratio.setValue(0)

    dialog.preflight_button.click()
    qtbot.waitUntil(
        lambda: (
            dialog.task_id is not None
            and gateway.task_snapshot(dialog.task_id).state
            not in {TaskState.QUEUED, TaskState.RUNNING}
        ),
        timeout=10000,
    )
    snapshot = gateway.task_snapshot(dialog.task_id)
    assert snapshot.errors == ()
    qtbot.waitUntil(lambda: dialog.preflight is not None, timeout=2000)
    assert dialog.preflight is not None and dialog.preflight.can_export
    assert dialog.export_button.isEnabled()

    dialog.export_button.click()
    qtbot.waitUntil(lambda: dialog.report is not None, timeout=10000)
    assert dialog.report is not None
    assert (tmp_path / "qt-yolo" / "data.yaml").is_file()
    dialog.close()
    gateway.close()
