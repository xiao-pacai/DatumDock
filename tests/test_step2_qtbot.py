"""使用 pytest-qt 验证步骤二真实控件交互与错误边界。"""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt

from datumdock.i18n.catalog import LocaleService, tr
from datumdock.services.dataset_library import DatasetLibraryService
from datumdock.ui.annotation_workspace import AnnotationWorkspace
from datumdock.ui.application_shell import ApplicationShell
from datumdock.ui.managed_gateway import ManagedDatasetGateway
from datumdock.ui.prototype_dialogs import DialogId
from datumdock.ui.prototype_pages import RouteId


def _complete_dialog(qtbot, dialog) -> None:
    """按用户可见的下一步按钮完成三步受管向导。"""

    qtbot.waitUntil(dialog.isVisible)
    step_count = dialog.pages.count()
    for _ in range(step_count):
        qtbot.mouseClick(dialog.next_button, Qt.MouseButton.LeftButton)
        qtbot.wait(20)


def test_qtbot_creates_dataset_and_switches_real_workspace(qtbot, tmp_path: Path) -> None:
    """真实点击创建向导后进入空工作台，并可从顶部切换另一个数据集。"""

    service = DatasetLibraryService(tmp_path)
    first = service.create_dataset("已有数据集")
    window = ApplicationShell(LocaleService(), ManagedDatasetGateway(service))
    qtbot.addWidget(window)
    window.show()

    window.open_dialog(DialogId.CREATE_DATASET.value)
    dialog = window._active_dialogs[0]
    qtbot.keyClicks(dialog.name_input, "QtBot Dataset")
    _complete_dialog(qtbot, dialog)
    qtbot.waitUntil(lambda: len(service.list_datasets()) == 2)

    assert window.navigation.current == RouteId.ANNOTATION_WORKSPACE
    workspace = window.navigation.pages[RouteId.ANNOTATION_WORKSPACE]
    assert isinstance(workspace, AnnotationWorkspace)
    assert workspace.snapshot.dataset.name == "QtBot Dataset"
    first_index = workspace.dataset_combo.findData(first.dataset.id)
    workspace.dataset_combo.setCurrentIndex(first_index)
    qtbot.waitUntil(
        lambda: (
            isinstance(window.navigation.pages[RouteId.ANNOTATION_WORKSPACE], AnnotationWorkspace)
            and window.navigation.pages[RouteId.ANNOTATION_WORKSPACE].snapshot.dataset.id
            == first.dataset.id
        )
    )


def test_qtbot_write_failure_shows_error_without_closing_application(
    qtbot,
    tmp_path: Path,
    monkeypatch,
) -> None:
    """写盘失败通过窗口命令信号显示 Toast，主窗口与原数据仍可用。"""

    locale = LocaleService()
    service = DatasetLibraryService(tmp_path)
    created = service.create_dataset("原名称")
    window = ApplicationShell(locale, ManagedDatasetGateway(service))
    qtbot.addWidget(window)
    window.show()

    def fail_save(_dataset) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(service.dataset_repository, "save_dataset", fail_save)
    window._dispatch_command(
        "dataset.rename",
        {"dataset_id": created.dataset.id, "name": "不会写入"},
    )
    qtbot.waitUntil(window.toast.isVisible)

    assert window.toast.text() == tr(locale, "toast.library_operation_failed")
    assert window.isVisible()
    assert service.library.datasets[0].name == "原名称"


def test_qtbot_language_switch_preserves_dataset_content(qtbot, tmp_path: Path) -> None:
    """即时切换英文会保存应用偏好，但数据集内容保持不变。"""

    service = DatasetLibraryService(tmp_path)
    service.create_dataset("不可翻译名称", "不可翻译描述")
    before = {
        path.relative_to(tmp_path): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file() and path.name != "settings.json"
    }
    window = ApplicationShell(LocaleService(), ManagedDatasetGateway(service))
    qtbot.addWidget(window)
    window.show()
    window.change_locale("en_US")
    qtbot.waitUntil(lambda: window.windowTitle() == "DatumDock")
    after = {
        path.relative_to(tmp_path): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file() and path.name != "settings.json"
    }

    assert window.gateway.home_snapshot().datasets[0].name == "不可翻译名称"
    assert before == after
    assert window.gateway.settings.ui_locale == "en_US"
