"""整数据集永久删除的预检、事务和启动恢复回归。"""

from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path

import pytest

from datumdock.services.dataset_deletion import (
    DatasetDeletionError,
    DatasetDeletionRequest,
    DatasetDeletionService,
    DatasetDeletionStatus,
)
from datumdock.services.dataset_library import DatasetLibraryService, DatasetNotFoundError


def _tree_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def test_dataset_deletion_preflight_and_commit_are_isolated(tmp_path: Path) -> None:
    """确认名称和二次确认通过后，只删除目标 UUID 目录。"""

    service = DatasetLibraryService(tmp_path / "library")
    target = service.create_dataset("待删除")
    survivor = service.create_dataset("保留")
    target_paths = service.dataset_repository.paths(target.dataset.id)
    survivor_root = service.dataset_repository.paths(survivor.dataset.id).root
    annotation = {
        "version": "5.5.0",
        "flags": {},
        "shapes": [
            {
                "label": "part",
                "points": [[1, 2], [10, 20]],
                "shape_type": "rectangle",
            }
        ],
        "imagePath": "sample.png",
        "imageData": None,
        "imageHeight": 32,
        "imageWidth": 32,
    }
    (target_paths.annotations / "sample.json").write_text(json.dumps(annotation), encoding="utf-8")
    external = tmp_path / "external.txt"
    external.write_text("不可修改", encoding="utf-8")
    survivor_before = _tree_bytes(survivor_root)

    deletion = DatasetDeletionService(service)
    preflight = deletion.preflight(target.dataset.id)
    assert preflight.dataset_name == "待删除"
    assert preflight.impact.annotation_file_count == 1
    assert preflight.impact.rectangle_count == 1
    assert preflight.can_delete

    report = deletion.delete(DatasetDeletionRequest(preflight, "待删除", True))

    assert report.status == DatasetDeletionStatus.COMPLETED
    assert not target_paths.root.exists()
    assert not service.is_registered(target.dataset.id)
    assert service.is_registered(survivor.dataset.id)
    assert _tree_bytes(survivor_root) == survivor_before
    assert external.read_text(encoding="utf-8") == "不可修改"


def test_dataset_deletion_requires_exact_name_and_fresh_snapshot(tmp_path: Path) -> None:
    """名称大小写或目录清单发生变化后，旧确认一律失效。"""

    service = DatasetLibraryService(tmp_path)
    created = service.create_dataset("Factory")
    deletion = DatasetDeletionService(service)
    preflight = deletion.preflight(created.dataset.id)

    with pytest.raises(DatasetDeletionError, match="不完全匹配"):
        deletion.delete(DatasetDeletionRequest(preflight, "factory", True))
    assert service.is_registered(created.dataset.id)

    paths = service.dataset_repository.paths(created.dataset.id)
    (paths.models / "changed.bin").write_bytes(b"changed")
    with pytest.raises(DatasetDeletionError, match="发生变化"):
        deletion.delete(DatasetDeletionRequest(preflight, "Factory", True))
    assert paths.root.is_dir()


def test_dataset_deletion_blocks_pending_managed_operation(tmp_path: Path) -> None:
    """未完成文件事务存在时，只提供影响报告而不允许提交。"""

    service = DatasetLibraryService(tmp_path)
    created = service.create_dataset("被阻塞")
    paths = service.dataset_repository.paths(created.dataset.id)
    with __import__("sqlite3").connect(paths.index) as connection:
        connection.execute(
            "INSERT INTO managed_operations VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("op", created.dataset.id, "import", "prepared", "{}", "now", "now"),
        )

    preflight = DatasetDeletionService(service).preflight(created.dataset.id)
    assert not preflight.can_delete
    assert any("未完成" in item for item in preflight.blockers)


def test_dataset_deletion_startup_restores_staged_registered_dataset(tmp_path: Path) -> None:
    """进程在移除登记前中断时，重启根据登记事实恢复原 UUID 目录。"""

    service = DatasetLibraryService(tmp_path)
    created = service.create_dataset("恢复")
    deletion = DatasetDeletionService(service)
    preflight = deletion.preflight(created.dataset.id)
    operation = deletion.operations_root / "00000000-0000-4000-8000-000000000001"
    payload = operation / "payload"
    payload.mkdir(parents=True)
    manifest = {
        "format_version": 1,
        "operation_id": operation.name,
        "dataset_id": created.dataset.id,
        "dataset_name": created.dataset.name,
        "library_sha256": preflight.library_sha256,
        "tree_sha256": preflight.tree_sha256,
        "managed_entries": list(preflight.managed_entries),
        "recovery_entries": [],
        "phase": "staged",
        "created_at": "2026-01-01T00:00:00+00:00",
    }
    (operation / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    original = service.dataset_repository.paths(created.dataset.id).root
    os.replace(original, payload / "dataset")

    restarted = DatasetLibraryService(tmp_path)
    assert restarted.open_dataset(created.dataset.id).dataset.name == "恢复"
    assert created.dataset.id in restarted.dataset_deletion_recovery_report.restored_dataset_ids
    assert not operation.exists()


def test_dataset_deletion_cleanup_failure_is_not_reported_as_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """登记已移除但暂存清理失败时返回待处理状态，重启再继续清理。"""

    service = DatasetLibraryService(tmp_path)
    created = service.create_dataset("待清理")
    deletion = DatasetDeletionService(service)
    preflight = deletion.preflight(created.dataset.id)
    from datumdock.services import dataset_deletion as module

    original_remove = module._remove_verified_operation
    monkeypatch.setattr(
        module,
        "_remove_verified_operation",
        lambda _path: (_ for _ in ()).throw(OSError("locked")),
    )
    report = deletion.delete(DatasetDeletionRequest(preflight, "待清理", True))
    assert report.status == DatasetDeletionStatus.PENDING_CLEANUP
    assert not service.is_registered(created.dataset.id)

    monkeypatch.setattr(module, "_remove_verified_operation", original_remove)
    restarted = DatasetLibraryService(tmp_path)
    assert created.dataset.id in restarted.dataset_deletion_recovery_report.cleaned_dataset_ids
    assert not service.dataset_repository.paths(created.dataset.id).root.exists()


def test_preflight_with_runtime_blocker_cannot_be_committed(tmp_path: Path) -> None:
    """Gateway 提供的自动保存或后台任务阻塞也会绑定到预检。"""

    service = DatasetLibraryService(tmp_path)
    created = service.create_dataset("忙碌")
    deletion = DatasetDeletionService(service)
    preflight = deletion.preflight(created.dataset.id, runtime_blockers=("正在保存",))
    assert not preflight.can_delete
    with pytest.raises(DatasetDeletionError, match="阻塞"):
        deletion.delete(DatasetDeletionRequest(preflight, "忙碌", True))


def test_tampered_preflight_dataset_id_cannot_escape_uuid_boundary(tmp_path: Path) -> None:
    """即使请求对象被程序错误篡改，路径也只能由规范 UUID 生成。"""

    service = DatasetLibraryService(tmp_path)
    created = service.create_dataset("安全")
    deletion = DatasetDeletionService(service)
    preflight = deletion.preflight(created.dataset.id)
    tampered = replace(preflight, dataset_id="../outside")
    with pytest.raises(DatasetNotFoundError):
        deletion.delete(DatasetDeletionRequest(tampered, "安全", True))
    assert service.is_registered(created.dataset.id)
