"""步骤三网关后台任务、正式分页工作台与真实设置回归。"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from pathlib import Path

from PIL import Image
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox, QWidget

from datumdock.domain.models import DatasetSample
from datumdock.i18n.catalog import LocaleService
from datumdock.services.dataset_library import DatasetLibraryService
from datumdock.services.sample_repository import DatasetSampleRepository
from datumdock.services.tasks import TaskState
from datumdock.ui.annotation_workspace import AnnotationWorkspace
from datumdock.ui.application_shell import ApplicationShell
from datumdock.ui.managed_gateway import ManagedDatasetGateway
from datumdock.ui.managed_governance_pages import ManagedGovernancePage
from datumdock.ui.managed_media_dialogs import (
    DuplicateDecisionDialog,
    ManagedImageImportDialog,
)
from datumdock.ui.prototype_pages import RouteId


def _wait_task(gateway: ManagedDatasetGateway, task_id: str, timeout: float = 10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        snapshot = gateway.task_snapshot(task_id)
        if snapshot.state not in {TaskState.QUEUED, TaskState.RUNNING}:
            return snapshot
        time.sleep(0.01)
    raise AssertionError("后台任务未在预期时间内完成")


def _import_images(
    gateway: ManagedDatasetGateway,
    dataset_id: str,
    sources: tuple[Path, ...],
) -> tuple[str, ...]:
    preflight_task = gateway.start_import_preflight(dataset_id, sources)
    assert _wait_task(gateway, preflight_task).state == TaskState.COMPLETED
    preflight = gateway.task_result(preflight_task)
    commit_task = gateway.start_import_commit(dataset_id, preflight.session_id, {})
    snapshot = _wait_task(gateway, commit_task)
    assert snapshot.state == TaskState.COMPLETED
    return tuple(gateway.task_result(commit_task).imported_sample_ids)


def test_gateway_background_import_updates_real_home_and_keeps_sources(
    tmp_path: Path,
) -> None:
    """后台导入完成后 SQLite 和主页统计同步，外部文件字节不变。"""

    service = DatasetLibraryService(tmp_path / "library")
    bundle = service.create_dataset("后台导入")
    gateway = ManagedDatasetGateway(service)
    sources = []
    before: dict[Path, bytes] = {}
    for index in range(3):
        path = tmp_path / f"source-{index}.jpg"
        Image.new("RGB", (50 + index, 30), (index * 50, 90, 130)).save(path)
        sources.append(path)
        before[path] = path.read_bytes()

    imported_ids = _import_images(gateway, bundle.dataset.id, tuple(sources))

    assert len(imported_ids) == 3
    assert gateway.query_samples(bundle.dataset.id).total == 3
    assert gateway.home_snapshot().datasets[0].image_count == 3
    assert all(path.read_bytes() == before[path] for path in sources)
    gateway.close()


def test_managed_workspace_uses_page_model_and_loads_real_canvas(qtbot, tmp_path: Path) -> None:
    """普通工作台不再使用演示图，选择 SQLite 样本后显示真实 PNG。"""

    service = DatasetLibraryService(tmp_path / "library")
    bundle = service.create_dataset("真实工作台")
    gateway = ManagedDatasetGateway(service)
    source = tmp_path / "actual.png"
    Image.new("RGBA", (96, 64), (12, 88, 160, 120)).save(source)
    imported_id = _import_images(gateway, bundle.dataset.id, (source,))[0]
    window = ApplicationShell(LocaleService(), gateway)
    qtbot.addWidget(window)
    window.show()
    window.navigate(f"annotation_workspace:{bundle.dataset.id}")
    workspace = window.navigation.pages[RouteId.ANNOTATION_WORKSPACE]
    assert isinstance(workspace, AnnotationWorkspace)
    assert workspace.sample_model is not None
    assert workspace.sample_model.rowCount() == 1
    assert workspace.sample_model.total == 1
    workspace.sample_model.data(workspace.sample_model.index(0), Qt.ItemDataRole.DecorationRole)
    qtbot.waitUntil(
        lambda: imported_id in workspace.sample_model._thumbnail_cache,
        timeout=5000,
    )
    assert workspace.current_image_id == imported_id
    qtbot.waitUntil(lambda: not workspace.canvas.managed_pixmap.isNull(), timeout=5000)
    assert workspace.canvas.managed_pixmap.size().width() == 96
    assert workspace.canvas.managed_read_only is False
    assert workspace.tool_buttons["rectangle"].isEnabled() is False

    workspace.grid_toggle.click()
    assert workspace.image_list.viewMode() == workspace.image_list.ViewMode.IconMode
    workspace.list_toggle.click()
    assert workspace.image_list.viewMode() == workspace.image_list.ViewMode.ListMode
    window.close()


def test_two_dataset_switch_drops_old_canvas_context(qtbot, tmp_path: Path) -> None:
    """切换数据集后分页模型、缩略图和当前画布不串用旧数据。"""

    service = DatasetLibraryService(tmp_path / "library")
    first = service.create_dataset("红色")
    second = service.create_dataset("蓝色")
    gateway = ManagedDatasetGateway(service)
    red = tmp_path / "red.png"
    blue = tmp_path / "blue.png"
    Image.new("RGB", (40, 20), "red").save(red)
    Image.new("RGB", (70, 30), "blue").save(blue)
    _import_images(gateway, first.dataset.id, (red,))
    blue_id = _import_images(gateway, second.dataset.id, (blue,))[0]
    window = ApplicationShell(LocaleService(), gateway)
    qtbot.addWidget(window)
    window.navigate(f"annotation_workspace:{first.dataset.id}")
    window.navigate(f"annotation_workspace:{second.dataset.id}")
    workspace = window.navigation.pages[RouteId.ANNOTATION_WORKSPACE]
    assert isinstance(workspace, AnnotationWorkspace)
    assert workspace.snapshot.dataset.id == second.dataset.id
    assert workspace.current_image_id == blue_id
    qtbot.waitUntil(lambda: not workspace.canvas.managed_pixmap.isNull(), timeout=5000)
    assert workspace.canvas.managed_pixmap.width() == 70
    window.close()


def test_real_import_dialog_runs_from_selected_sources(qtbot, tmp_path: Path) -> None:
    """真实导入向导通过按钮启动后台流程，完成后发出首张稳定 ID。"""

    service = DatasetLibraryService(tmp_path / "library")
    bundle = service.create_dataset("导入向导")
    gateway = ManagedDatasetGateway(service)
    source = tmp_path / "dialog.bmp"
    Image.new("L", (36, 24), 100).save(source)
    dialog = ManagedImageImportDialog(LocaleService(), gateway, bundle.dataset.id)
    qtbot.addWidget(dialog)
    completed: list[str] = []
    dialog.import_finished.connect(completed.append)
    dialog._add_sources((source,))
    dialog.show()

    qtbot.mouseClick(dialog.start_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: bool(completed), timeout=10000)

    assert gateway.query_samples(bundle.dataset.id).total == 1
    assert dialog.status.text().startswith("已导入 1 张")
    dialog.close()
    # 同一项回归继续覆盖多个已有匹配的逐项查看与无默认决定。
    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    Image.new("RGB", (20, 20), "red").save(first)
    Image.new("RGB", (20, 20), "blue").save(second)
    dialog = DuplicateDecisionDialog(
        LocaleService(),
        first.read_bytes(),
        "pending.png",
        (
            (first.read_bytes(), "existing-a.png"),
            (second.read_bytes(), "existing-b.png"),
        ),
    )
    qtbot.addWidget(dialog)
    dialog.show()

    assert dialog.decision is None
    assert "existing-a.png" in dialog.match_position.text()
    qtbot.mouseClick(dialog.next_match, Qt.MouseButton.LeftButton)
    assert "existing-b.png" in dialog.match_position.text()
    assert dialog.decision is None
    dialog.reject()
    gateway.close()


def test_settings_language_and_trash_threshold_are_persisted(qtbot, tmp_path: Path) -> None:
    """步骤三已接入设置持久化，数据集内容不会被翻译。"""

    service = DatasetLibraryService(tmp_path / "library")
    dataset = service.create_dataset("不翻译数据集", "不翻译描述")
    gateway = ManagedDatasetGateway(service)
    window = ApplicationShell(LocaleService(), gateway)
    qtbot.addWidget(window)
    settings_page = window.navigation.pages[RouteId.SETTINGS]
    settings_page.trash_threshold.setValue(12)
    window.change_locale("en_US")
    qtbot.waitUntil(lambda: gateway.settings.trash_sample_threshold == 12)
    window.close()

    reopened = ManagedDatasetGateway(DatasetLibraryService(tmp_path / "library"))
    assert reopened.settings.ui_locale == "en_US"
    assert reopened.settings.trash_sample_threshold == 12
    assert reopened.service.open_dataset(dataset.dataset.id).dataset.name == "不翻译数据集"
    reopened.close()


def test_real_delete_confirmation_trash_page_and_restore(
    qtbot,
    tmp_path: Path,
    monkeypatch,
) -> None:
    """正式删除确认会移入真实回收站，管理页可用稳定 ID 恢复。"""

    service = DatasetLibraryService(tmp_path / "library")
    bundle = service.create_dataset("回收站页面")
    gateway = ManagedDatasetGateway(service)
    source = tmp_path / "delete.png"
    Image.new("RGB", (42, 28), "green").save(source)
    sample_id = _import_images(gateway, bundle.dataset.id, (source,))[0]
    window = ApplicationShell(LocaleService(), gateway)
    qtbot.addWidget(window)
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes,
    )

    window._confirm_managed_delete(bundle.dataset.id, (sample_id,))
    assert gateway.query_samples(bundle.dataset.id).total == 0
    window.navigate(f"trash:{bundle.dataset.id}")
    page = window.navigation.pages[RouteId.TRASH]
    assert isinstance(page, ManagedGovernancePage)
    assert page.table.rowCount() == 1

    qtbot.mouseClick(page.primary, Qt.MouseButton.LeftButton)

    assert gateway.query_samples(bundle.dataset.id).total == 1
    assert gateway.get_sample(bundle.dataset.id, sample_id) is not None
    window.close()


def test_ten_thousand_index_uses_only_one_page_and_bounded_widgets(
    qtbot,
    tmp_path: Path,
) -> None:
    """正式工作台面对万条索引仍只实例化一个页面和固定数量控件。"""

    service = DatasetLibraryService(tmp_path / "library")
    bundle = service.create_dataset("万图压力")
    paths = service.dataset_repository.paths(bundle.dataset.id)
    repository = DatasetSampleRepository(paths, bundle.dataset.id)
    with repository._connection() as connection:
        for number in range(10_000):
            sample = DatasetSample(
                dataset_id=bundle.dataset.id,
                filename=f"image_{number:06d}.png",
                original_filename=f"source-{number}.jpg",
                image_path=f"pool/images/image_{number:06d}.png",
                width=80,
                height=40,
                content_hash=f"{number:064x}",
                file_hash=f"{number + 1:064x}",
                perceptual_hash=f"{number:016x}102030",
                imported_at=datetime.now(UTC).isoformat(),
            )
            repository._insert_sample(connection, sample)
    gateway = ManagedDatasetGateway(service)
    window = ApplicationShell(LocaleService(), gateway)
    qtbot.addWidget(window)
    window.navigate(f"annotation_workspace:{bundle.dataset.id}")
    workspace = window.navigation.pages[RouteId.ANNOTATION_WORKSPACE]
    assert isinstance(workspace, AnnotationWorkspace)

    assert workspace.sample_model is not None
    assert workspace.sample_model.total == 10_000
    assert workspace.sample_model.rowCount() == 200
    assert len(workspace.findChildren(QWidget)) < 500
    window.close()
