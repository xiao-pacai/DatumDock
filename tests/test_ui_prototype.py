"""全量 GUI 原型的安全边界、路由、弹窗与国际化回归。"""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from datumdock.app import create_application, parse_launch_options
from datumdock.i18n.catalog import CATALOGS, LocaleService, tr
from datumdock.resources import resource_root
from datumdock.ui.annotation_workspace import AnnotationWorkspace
from datumdock.ui.application_shell import ApplicationShell
from datumdock.ui.icons import IconRegistry
from datumdock.ui.prototype_dialogs import TITLE_KEYS, DialogId
from datumdock.ui.prototype_gateway import PreviewGateway, UnavailableGateway
from datumdock.ui.prototype_models import CommandStatus, UiCommand
from datumdock.ui.prototype_pages import HomePage, RouteId, SettingsPage


def _application() -> QApplication:
    return QApplication.instance() or create_application(["datumdock-test"])


def test_launch_options_keep_qt_arguments() -> None:
    """预览参数应与 Qt 自身参数分离。"""

    options, remaining = parse_launch_options(["--ui-preview", "-style", "Fusion"])
    assert options.ui_preview is True
    assert remaining == ["-style", "Fusion"]


def test_normal_gateway_rejects_side_effects() -> None:
    """普通原型模式没有真实后端时必须明确拒绝写操作。"""

    gateway = UnavailableGateway()
    assert gateway.home_snapshot().datasets == ()
    assert gateway.workspace_snapshot() is None
    result = gateway.dispatch(UiCommand("dataset.create", {"name": "不会创建"}))
    assert result.status == CommandStatus.NOT_CONNECTED


def test_preview_gateway_changes_memory_only(tmp_path: Path) -> None:
    """预览命令只能改变会话快照，不得在用户目录生成文件。"""

    gateway = PreviewGateway()
    before = len(gateway.home_snapshot().datasets)
    result = gateway.dispatch(UiCommand("dataset.create", {"name": "内存数据集"}))
    assert result.status == CommandStatus.PREVIEW_APPLIED
    assert len(gateway.home_snapshot().datasets) == before + 1
    assert list(tmp_path.iterdir()) == []


def test_shell_sessions_do_not_write_user_data(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """启动、导航和预览命令都不得创建用户资料库。"""

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path))
    application = _application()
    normal = ApplicationShell.for_mode(LocaleService(), False)
    normal.show()
    application.processEvents()
    normal.close()
    preview = ApplicationShell.for_mode(LocaleService(), True)
    preview.gateway.dispatch(UiCommand("dataset.create", {"name": "临时"}))
    preview.navigate(RouteId.SETTINGS.value)
    application.processEvents()
    preview.close()
    assert list(tmp_path.iterdir()) == []


def test_translation_catalogs_have_identical_keys() -> None:
    """中文与英文资源必须完整对应，避免运行时显示内部键名。"""

    assert set(CATALOGS["zh_CN"]) == set(CATALOGS["en_US"])


def test_preview_shell_registers_and_visits_every_route() -> None:
    """预览模式应能创建、访问并返回全部公开页面。"""

    application = _application()
    window = ApplicationShell.for_mode(LocaleService(), True)
    window.show()
    application.processEvents()
    assert set(window.navigation.pages) == set(RouteId)
    for route in RouteId:
        window.navigation.navigate(route, remember=False)
        application.processEvents()
        assert window.navigation.current == route
    home = window.navigation.pages[RouteId.HOME]
    assert isinstance(home, HomePage)
    assert home.gallery_button.isVisibleTo(home)
    window.close()


def test_normal_shell_is_safe_empty_home() -> None:
    """普通模式不得注册依赖演示数据的管理和标注页面。"""

    application = _application()
    window = ApplicationShell.for_mode(LocaleService(), False)
    window.show()
    application.processEvents()
    assert RouteId.ANNOTATION_WORKSPACE not in window.navigation.pages
    assert RouteId.COMPONENT_GALLERY not in window.navigation.pages
    home = window.navigation.pages[RouteId.HOME]
    assert isinstance(home, HomePage)
    assert home.snapshot.datasets == ()
    assert home.snapshot.quick_start_completed == 0
    window.open_dialog(DialogId.CREATE_DATASET.value)
    application.processEvents()
    assert window._active_dialogs == []
    assert window.toast.isVisible()
    window.close()


def test_every_registered_dialog_opens_and_retranslates() -> None:
    """集中注册的弹窗都应能构造、切换语言并安全关闭。"""

    application = _application()
    locale = LocaleService()
    window = ApplicationShell.for_mode(locale, True)
    for dialog_id in DialogId:
        dialog = window.dialog_registry.create(dialog_id, window)
        dialog.show()
        application.processEvents()
        assert dialog.windowTitle()
        locale.set_locale("en_US")
        dialog.retranslate_ui()
        assert dialog.windowTitle() == tr(locale, TITLE_KEYS[dialog_id])
        dialog.close()
        locale.set_locale("zh_CN")
    window.close()


def test_language_switch_preserves_demo_dataset_content() -> None:
    """界面语言变化不得翻译或改写数据集、文件名和标签训练名。"""

    application = _application()
    locale = LocaleService()
    window = ApplicationShell.for_mode(locale, True)
    home = window.navigation.pages[RouteId.HOME]
    settings = window.navigation.pages[RouteId.SETTINGS]
    assert isinstance(home, HomePage)
    assert isinstance(settings, SettingsPage)
    names_before = tuple(item.name for item in home.snapshot.datasets)
    window.change_locale("en_US")
    application.processEvents()
    assert tuple(item.name for item in home.snapshot.datasets) == names_before
    assert settings.back.text() == "Back"
    window.close()


def test_shortcut_recorder_detects_conflict_and_restores_defaults() -> None:
    """快捷键录入器应显示冲突，并能恢复内存默认值。"""

    application = _application()
    window = ApplicationShell.for_mode(LocaleService(), True)
    settings = window.navigation.pages[RouteId.SETTINGS]
    assert isinstance(settings, SettingsPage)
    recorder = settings.shortcut_recorders[1]
    QTest.mouseClick(recorder, Qt.MouseButton.LeftButton)
    QTest.keyClick(recorder, Qt.Key.Key_O, Qt.KeyboardModifier.ControlModifier)
    application.processEvents()
    assert recorder.sequence == "Ctrl+O"
    assert not settings.shortcut_conflict.isHidden()
    settings._restore_shortcut_defaults()
    assert recorder.sequence == "Ctrl+E"
    assert not settings.shortcut_conflict.isVisible()
    window.close()


def test_annotation_canvas_draw_undo_redo_and_list_sync() -> None:
    """矩形创建与撤销重做应只更新工作台内存，并同步右侧列表。"""

    application = _application()
    window = ApplicationShell.for_mode(LocaleService(), True)
    workspace = window.navigation.pages[RouteId.ANNOTATION_WORKSPACE]
    assert isinstance(workspace, AnnotationWorkspace)
    window.show()
    window.navigation.navigate(RouteId.ANNOTATION_WORKSPACE, remember=False)
    application.processEvents()
    canvas = workspace.canvas
    before = len(canvas.annotations)
    workspace.tool_buttons["rectangle"].click()
    image_rect = canvas._image_rect()
    start = QPoint(round(image_rect.left() + 30), round(image_rect.top() + 30))
    end = QPoint(round(image_rect.left() + 150), round(image_rect.top() + 110))
    QTest.mousePress(canvas, Qt.MouseButton.LeftButton, pos=start)
    QTest.mouseMove(canvas, end, delay=10)
    QTest.mouseRelease(canvas, Qt.MouseButton.LeftButton, pos=end)
    application.processEvents()
    assert len(canvas.annotations) == before + 1
    assert workspace.annotation_list.count() == before + 1
    canvas.undo()
    assert len(canvas.annotations) == before
    canvas.redo()
    assert len(canvas.annotations) == before + 1
    window.close()


def test_icon_registry_renders_all_prototype_icons() -> None:
    """工作台与主页所需的自有 SVG 必须存在并可渲染。"""

    _application()
    registry = IconRegistry(resource_root())
    names = {
        "home",
        "add",
        "back",
        "search",
        "select",
        "rectangle",
        "auto_annotate",
        "pan",
        "zoom_in",
        "zoom_out",
        "fit",
        "undo",
        "redo",
        "more",
        "list",
        "grid",
        "help",
        "warning",
        "success",
        "error",
        "restore",
        "edit",
        "archive",
        "tutorial",
        "info",
    }
    for name in names:
        assert registry.exists(name)
        assert not registry.icon(name).isNull()
        assert not registry.icon(name, "disabled").isNull()


def test_responsive_window_sizes_keep_valid_central_geometry() -> None:
    """三种基准分辨率下中央页面都应保持可用尺寸。"""

    application = _application()
    window = ApplicationShell.for_mode(LocaleService(), True)
    window.show()
    for width, height in ((1366, 768), (1440, 900), (1920, 1080)):
        window.resize(width, height)
        application.processEvents()
        assert window.centralWidget().width() >= 900
        assert window.centralWidget().height() >= 540
    window.close()
