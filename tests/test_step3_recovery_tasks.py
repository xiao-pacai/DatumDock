"""步骤三启动对账、操作恢复、设置保护与任务取消回归。"""

from __future__ import annotations

import time
from pathlib import Path

from PIL import Image

from datumdock.domain.models import AppSettings, new_id, utc_now
from datumdock.services.dataset_library import DatasetLibraryService
from datumdock.services.image_pool import ImageImportPreflightRequest, ImageImportService
from datumdock.services.sample_governance import TrashService
from datumdock.services.sample_repository import (
    DatasetSampleRepository,
    ManagedOperation,
)
from datumdock.services.settings import AppSettingsError, AppSettingsRepository
from datumdock.services.tasks import BackgroundTaskService, TaskState
from datumdock.ui.managed_gateway import ManagedDatasetGateway
from datumdock.ui.prototype_models import CommandStatus, UiCommand


def _import_two(tmp_path: Path):
    root = tmp_path / "library"
    library = DatasetLibraryService(root)
    bundle = library.create_dataset("对账")
    paths = library.dataset_repository.paths(bundle.dataset.id)
    repository = DatasetSampleRepository(paths, bundle.dataset.id)
    importer = ImageImportService(paths, bundle.dataset, repository)
    sources = []
    for index in range(2):
        source = tmp_path / f"source-{index}.png"
        Image.new("RGB", (44, 30), (60 + index * 50, 100, 140)).save(source)
        sources.append(source)
    prepared = importer.preflight(ImageImportPreflightRequest(bundle.dataset.id, tuple(sources)))
    report = importer.commit(prepared, {})
    library.synchronize_statistics(bundle.dataset.id)
    return root, bundle, paths, repository, report.imported_sample_ids


def _register_operation(
    repository: DatasetSampleRepository,
    dataset_id: str,
    operation_type: str,
    payload: dict,
) -> str:
    """注册一条未完成操作，用于模拟进程在文件移动后突然终止。"""

    operation_id = new_id()
    timestamp = utc_now().isoformat()
    repository.register_operation(
        ManagedOperation(
            operation_id,
            dataset_id,
            operation_type,
            "prepared",
            payload,
            timestamp,
            timestamp,
        )
    )
    return operation_id


def test_restart_reconciliation_marks_missing_corrupt_and_reports_untracked(
    tmp_path: Path,
) -> None:
    """单张异常不阻止数据集打开，未知 PNG 只报告且不删除。"""

    root, bundle, paths, repository, sample_ids = _import_two(tmp_path)
    missing = repository.get_sample(sample_ids[0])
    corrupt = repository.get_sample(sample_ids[1])
    assert missing is not None and corrupt is not None
    repository.resolve_path(missing.image_path, "pool/images").unlink()
    repository.resolve_path(corrupt.image_path, "pool/images").write_bytes(b"not-a-png")
    untracked = paths.images / "unknown.png"
    Image.new("RGB", (10, 10), "white").save(untracked)

    restarted = DatasetLibraryService(root)
    report = restarted.sample_reconciliation_reports[bundle.dataset.id]

    assert report.missing_sample_ids == (missing.id,)
    assert report.corrupt_sample_ids == (corrupt.id,)
    assert report.untracked_pngs == ("pool/images/unknown.png",)
    assert untracked.is_file()
    record = restarted.list_datasets()[0]
    assert record.healthy is True
    assert "缺失图片 1 张" in record.diagnostic
    assert restarted.open_dataset(bundle.dataset.id).dataset.statistics.image_count == 2


def test_cancelled_commit_keeps_completed_items_and_cleans_staging(tmp_path: Path) -> None:
    """取消只在单样本边界生效，未提交的规范化临时文件不会残留。"""

    root = tmp_path / "library"
    library = DatasetLibraryService(root)
    bundle = library.create_dataset("取消导入")
    paths = library.dataset_repository.paths(bundle.dataset.id)
    repository = DatasetSampleRepository(paths, bundle.dataset.id)
    importer = ImageImportService(paths, bundle.dataset, repository)
    sources = []
    for index in range(4):
        source = tmp_path / f"cancel-{index}.png"
        Image.new("RGB", (30 + index, 20), (index * 30, 80, 100)).save(source)
        sources.append(source)
    prepared = importer.preflight(ImageImportPreflightRequest(bundle.dataset.id, tuple(sources)))
    checks = 0

    def cancelled() -> bool:
        nonlocal checks
        checks += 1
        return checks > 1

    report = importer.commit(prepared, {}, cancelled=cancelled)

    assert report.cancelled is True
    assert len(report.imported_sample_ids) == 1
    assert repository.count_active() == 1
    assert not (paths.root / "cache" / "import-staging" / prepared.session_id).exists()


def test_restart_rolls_back_interrupted_rename(tmp_path: Path) -> None:
    """重命名只完成第一段移动时，重启必须恢复原名和原索引。"""

    root, bundle, paths, repository, sample_ids = _import_two(tmp_path)
    sample = repository.get_sample(sample_ids[0])
    assert sample is not None
    new_filename = "renamed.png"
    operation_id = new_id()
    staging = paths.root / "cache" / "rename-staging" / operation_id
    staging.mkdir(parents=True)
    new_image_path = f"pool/images/{new_filename}"
    payload = {
        "items": [
            {
                "sample_id": sample.id,
                "old_filename": sample.filename,
                "new_filename": new_filename,
                "old_image_path": sample.image_path,
                "new_image_path": new_image_path,
                "old_annotation_path": "",
                "new_annotation_path": "",
                "conflict": "",
            }
        ],
        "naming_policy": bundle.dataset.configuration.naming_policy.model_dump(mode="json"),
    }
    timestamp = utc_now().isoformat()
    repository.register_operation(
        ManagedOperation(
            operation_id,
            bundle.dataset.id,
            "rename",
            "prepared",
            payload,
            timestamp,
            timestamp,
        )
    )
    old_image = repository.resolve_path(sample.image_path, "pool/images")
    old_image.replace(staging / f"{sample.id}.png")

    DatasetLibraryService(root)
    recovered = DatasetSampleRepository(paths, bundle.dataset.id)

    assert recovered.resolve_path(sample.image_path, "pool/images").is_file()
    assert recovered.get_sample(sample.id).filename == sample.filename
    assert recovered.list_operations() == ()


def test_restart_rolls_back_interrupted_move_to_trash(tmp_path: Path) -> None:
    """回收站文件已移动但索引未提交时，重启恢复活动图片。"""

    root, bundle, paths, repository, sample_ids = _import_two(tmp_path)
    sample = repository.get_sample(sample_ids[0])
    assert sample is not None
    item_root = paths.trash / sample.id
    item_root.mkdir(parents=True)
    _register_operation(
        repository,
        bundle.dataset.id,
        "move_to_trash",
        {
            "sample": sample.model_dump(mode="json"),
            "item_root": item_root.relative_to(paths.root).as_posix(),
            "image_path": sample.image_path,
            "annotation_path": "",
            "thumbnail_path": "",
        },
    )
    image_path = repository.resolve_path(sample.image_path, "pool/images")
    image_path.replace(item_root / "image.png")

    DatasetLibraryService(root)
    recovered = DatasetSampleRepository(paths, bundle.dataset.id)

    assert recovered.get_sample(sample.id).is_trashed is False
    assert image_path.is_file()
    assert not item_root.exists()
    assert recovered.list_operations() == ()


def test_restart_rolls_back_interrupted_active_permanent_delete(tmp_path: Path) -> None:
    """永久删除的索引仍在时，重启不得让图片凭空消失。"""

    root, bundle, paths, repository, sample_ids = _import_two(tmp_path)
    sample = repository.get_sample(sample_ids[0])
    assert sample is not None
    operation_id = new_id()
    deleting = paths.trash / ".deleting" / operation_id
    deleting.mkdir(parents=True)
    _register_operation(
        repository,
        bundle.dataset.id,
        "delete_permanent",
        {
            "mode": "active",
            "sample": sample.model_dump(mode="json"),
            "deleting_path": deleting.relative_to(paths.root).as_posix(),
        },
    )
    image_path = repository.resolve_path(sample.image_path, "pool/images")
    image_path.replace(deleting / "image.png")

    DatasetLibraryService(root)
    recovered = DatasetSampleRepository(paths, bundle.dataset.id)

    assert recovered.get_sample(sample.id) is not None
    assert image_path.is_file()
    assert not deleting.exists()
    assert recovered.list_operations() == ()


def test_restart_rolls_back_interrupted_trash_restore(tmp_path: Path) -> None:
    """回收站恢复文件已发布但索引未提交时，重启放回回收站。"""

    root, bundle, paths, repository, sample_ids = _import_two(tmp_path)
    trash = TrashService(paths, bundle.dataset, repository)
    impact = trash.preview((sample_ids[0],), threshold=10)
    trash.delete(impact)
    sample = repository.get_sample(sample_ids[0], include_trashed=True)
    assert sample is not None and sample.is_trashed
    item = repository.list_trash()[0]
    item_root = repository.resolve_path(item.manifest_path, "trash").parent
    image_target = paths.images / "restored.png"
    _register_operation(
        repository,
        bundle.dataset.id,
        "restore_from_trash",
        {
            "sample": sample.model_dump(mode="json"),
            "item_root": item_root.relative_to(paths.root).as_posix(),
            "image_target": image_target.relative_to(paths.root).as_posix(),
            "annotation_target": "",
            "thumbnail_target": "",
        },
    )
    (item_root / "image.png").replace(image_target)

    DatasetLibraryService(root)
    recovered = DatasetSampleRepository(paths, bundle.dataset.id)

    assert recovered.get_sample(sample.id, include_trashed=True).is_trashed is True
    assert (item_root / "image.png").is_file()
    assert not image_target.exists()
    assert recovered.list_operations() == ()


def test_settings_corruption_is_not_overwritten(tmp_path: Path) -> None:
    """损坏设置必须保留原字节，不能以默认值伪装恢复成功。"""

    repository = AppSettingsRepository(tmp_path)
    repository.save(AppSettings(trash_sample_threshold=9))
    repository.path.write_text("{broken", encoding="utf-8")
    before = repository.path.read_bytes()

    try:
        repository.load()
    except AppSettingsError:
        pass
    else:
        raise AssertionError("损坏设置没有显式失败")

    assert repository.path.read_bytes() == before


def test_background_task_cancel_waits_for_atomic_boundary() -> None:
    """业务执行器不依赖 Qt，取消后保留工作函数已经完成的结果。"""

    tasks = BackgroundTaskService(max_workers=1)

    def work(context):
        completed = 0
        for index in range(100):
            if context.cancelled():
                break
            time.sleep(0.002)
            completed += 1
            context.progress(completed, 100, str(index))
        return completed

    task_id = tasks.start("dataset-id", "test", work)
    time.sleep(0.015)
    tasks.cancel(task_id)
    deadline = time.monotonic() + 2
    while tasks.snapshot(task_id).state in {TaskState.QUEUED, TaskState.RUNNING}:
        assert time.monotonic() < deadline
        time.sleep(0.005)

    assert tasks.snapshot(task_id).state == TaskState.CANCELLED
    assert 0 < tasks.result(task_id) < 100
    tasks.shutdown()


def test_gateway_never_leaks_unexpected_media_exception(tmp_path: Path, monkeypatch) -> None:
    """磁盘和权限类异常必须转换成 UiCommandResult，而不是越过 Qt 边界。"""

    service = DatasetLibraryService(tmp_path / "library")
    bundle = service.create_dataset("异常边界")
    gateway = ManagedDatasetGateway(service)

    def fail(_dataset_id: str):
        raise OSError("disk unavailable")

    monkeypatch.setattr(gateway, "_media", fail)
    result = gateway.dispatch(
        UiCommand(
            "sample.trash",
            {"dataset_id": bundle.dataset.id, "sample_ids": ()},
        )
    )

    assert result.status == CommandStatus.ERROR
    assert result.message_key == "toast.library_operation_failed"
    gateway.close()
