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
from datumdock.services.dataset_library import DatasetLibraryService
from datumdock.ui.annotation_workspace import AnnotationWorkspace
from datumdock.ui.application_shell import ApplicationShell
from datumdock.ui.components import DatasetCard
from datumdock.ui.icons import IconRegistry
from datumdock.ui.managed_gateway import ManagedDatasetGateway
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
    """普通模式初始化内部资料库，随后预览模式不得读取或修改它。"""

    managed_root = tmp_path / "managed"
    monkeypatch.setenv("DATUMDOCK_DATA_DIR", str(managed_root))
    application = _application()
    normal = ApplicationShell.for_mode(LocaleService(), False)
    normal.show()
    application.processEvents()
    normal.close()
    before = {
        path.relative_to(managed_root): path.read_bytes()
        for path in managed_root.rglob("*")
        if path.is_file()
    }
    preview = ApplicationShell.for_mode(LocaleService(), True)
    preview.gateway.dispatch(UiCommand("dataset.create", {"name": "临时"}))
    preview.navigate(RouteId.SETTINGS.value)
    application.processEvents()
    preview.close()
    after = {
        path.relative_to(managed_root): path.read_bytes()
        for path in managed_root.rglob("*")
        if path.is_file()
    }
    assert before == after


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


def test_normal_shell_is_real_empty_home(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """普通首次启动显示真实空主页，并允许打开受管数据集创建向导。"""

    monkeypatch.setenv("DATUMDOCK_DATA_DIR", str(tmp_path / "library"))
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
    assert len(window._active_dialogs) == 1
    window._active_dialogs[0].close()
    window.close()


def test_managed_shell_creates_opens_and_switches_real_empty_datasets(tmp_path: Path) -> None:
    """创建后直接进入空工作台，顶部下拉可切换两个真实数据集。"""

    application = _application()
    service = DatasetLibraryService(tmp_path)
    gateway = ManagedDatasetGateway(service)
    window = ApplicationShell(LocaleService(), gateway)
    window.show()
    window._dispatch_command("dataset.create", {"name": "数据集一", "description": ""})
    application.processEvents()
    assert window.navigation.current == RouteId.ANNOTATION_WORKSPACE
    workspace = window.navigation.pages[RouteId.ANNOTATION_WORKSPACE]
    assert isinstance(workspace, AnnotationWorkspace)
    assert workspace.snapshot.dataset.name == "数据集一"
    assert workspace.snapshot.images == ()
    assert workspace.image_stack.currentIndex() == 1

    window._dispatch_command("dataset.create", {"name": "数据集二", "description": ""})
    application.processEvents()
    workspace = window.navigation.pages[RouteId.ANNOTATION_WORKSPACE]
    assert isinstance(workspace, AnnotationWorkspace)
    assert workspace.dataset_combo.count() == 2
    first_id = next(item.id for item in gateway.home_snapshot().datasets if item.name == "数据集一")
    window.navigate(f"{RouteId.ANNOTATION_WORKSPACE.value}:{first_id}")
    application.processEvents()
    switched = window.navigation.pages[RouteId.ANNOTATION_WORKSPACE]
    assert isinstance(switched, AnnotationWorkspace)
    assert switched.snapshot.dataset.name == "数据集一"
    assert switched.snapshot.images == ()
    window.close()


def test_real_create_dialog_finishes_in_new_empty_workspace(tmp_path: Path) -> None:
    """主页创建向导经过确认后真实创建，并直接进入新数据集工作台。"""

    application = _application()
    service = DatasetLibraryService(tmp_path)
    window = ApplicationShell(LocaleService(), ManagedDatasetGateway(service))
    window.show()
    window.open_dialog(DialogId.CREATE_DATASET.value)
    dialog = window._active_dialogs[0]
    dialog.name_input.setText("向导创建数据集")
    dialog.description_input.setPlainText("由真实 GUI 向导创建")
    dialog.next_step()
    dialog.next_step()
    dialog.next_step()
    application.processEvents()

    records = service.list_datasets()
    assert len(records) == 1
    assert records[0].entry.name == "向导创建数据集"
    assert window.navigation.current == RouteId.ANNOTATION_WORKSPACE
    workspace = window.navigation.pages[RouteId.ANNOTATION_WORKSPACE]
    assert isinstance(workspace, AnnotationWorkspace)
    assert workspace.snapshot.dataset.name == "向导创建数据集"
    assert workspace.image_stack.currentIndex() == 1
    window.close()


def test_unconnected_normal_action_has_no_file_side_effect(tmp_path: Path) -> None:
    """普通模式的图片导入等未接入入口只提示，不产生任何文件副作用。"""

    service = DatasetLibraryService(tmp_path)
    dataset = service.create_dataset("安全数据集")
    gateway = ManagedDatasetGateway(service)
    before = {
        path.relative_to(tmp_path): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }
    result = gateway.dispatch(UiCommand("image.import", {"dataset_id": dataset.dataset.id}))
    after = {
        path.relative_to(tmp_path): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }
    assert result.status == CommandStatus.NOT_CONNECTED
    assert before == after


def test_managed_gateway_rename_archive_restore_and_home_snapshot(tmp_path: Path) -> None:
    """主页元数据命令真实生效，归档和恢复不删除 UUID 目录。"""

    service = DatasetLibraryService(tmp_path)
    gateway = ManagedDatasetGateway(service)
    created = gateway.dispatch(UiCommand("dataset.create", {"name": "初始名称"}))
    assert created.status == CommandStatus.APPLIED
    assert created.affected_id
    dataset_id = created.affected_id
    directory = service.dataset_directory(dataset_id)

    renamed = gateway.dispatch(
        UiCommand("dataset.rename", {"dataset_id": dataset_id, "name": "更新名称"})
    )
    assert renamed.status == CommandStatus.APPLIED
    assert gateway.home_snapshot().datasets[0].name == "更新名称"

    archived = gateway.dispatch(UiCommand("dataset.archive", {"dataset_id": dataset_id}))
    assert archived.status == CommandStatus.APPLIED
    assert gateway.home_snapshot().datasets[0].archived is True
    assert directory.is_dir()

    restored = gateway.dispatch(UiCommand("dataset.restore", {"dataset_id": dataset_id}))
    assert restored.status == CommandStatus.APPLIED
    assert gateway.home_snapshot().datasets[0].archived is False
    assert service.dataset_directory(dataset_id) == directory


def test_home_search_sort_and_archive_filter_use_real_snapshot(tmp_path: Path) -> None:
    """主页搜索、排序和归档筛选直接作用于真实资料库快照。"""

    application = _application()
    service = DatasetLibraryService(tmp_path)
    first = service.create_dataset("Alpha")
    service.create_dataset("Beta")
    service.archive_dataset(first.dataset.id)
    window = ApplicationShell(LocaleService(), ManagedDatasetGateway(service))
    home = window.navigation.pages[RouteId.HOME]
    assert isinstance(home, HomePage)
    window.show()
    window.navigation.navigate(RouteId.HOME, remember=False)

    home.archive_filter.setCurrentIndex(home.archive_filter.findData("active"))
    application.processEvents()
    active_cards = home.findChildren(DatasetCard)
    assert [card.data.name for card in active_cards if card.isVisibleTo(home)] == ["Beta"]

    home.archive_filter.setCurrentIndex(home.archive_filter.findData("archived"))
    home.dataset_search.setText("alp")
    application.processEvents()
    archived_cards = home.findChildren(DatasetCard)
    assert [card.data.name for card in archived_cards if card.isVisibleTo(home)] == ["Alpha"]
    assert next(card for card in archived_cards if card.isVisibleTo(home)).data.archived is True
    window.close()


def test_preview_mode_ignores_even_a_corrupt_real_library(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """预览模式不实例化真实 Service，因此损坏索引也不会被读取或改写。"""

    root = tmp_path / "real-library"
    root.mkdir()
    library = root / "library.json"
    original = b"{broken-real-library"
    library.write_bytes(original)
    monkeypatch.setenv("DATUMDOCK_DATA_DIR", str(root))

    application = _application()
    window = ApplicationShell.for_mode(LocaleService(), True)
    window.show()
    application.processEvents()
    assert window.gateway.preview_mode is True
    assert window.gateway.home_snapshot().datasets
    window.close()
    assert library.read_bytes() == original


def test_language_switch_does_not_modify_real_dataset_content(tmp_path: Path) -> None:
    """中英文切换只刷新系统文案，真实名称、描述和资料库字节保持不变。"""

    service = DatasetLibraryService(tmp_path)
    dataset = service.create_dataset("原始名称", "原始描述")
    gateway = ManagedDatasetGateway(service)
    before = {
        path.relative_to(tmp_path): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }
    application = _application()
    window = ApplicationShell(LocaleService(), gateway)
    window.change_locale("en_US")
    application.processEvents()
    window.close()
    after = {
        path.relative_to(tmp_path): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }

    assert gateway.home_snapshot().datasets[0].name == "原始名称"
    assert service.open_dataset(dataset.dataset.id).dataset.description == "原始描述"
    assert before == after


def test_corrupt_dataset_becomes_diagnostic_home_card(tmp_path: Path) -> None:
    """损坏数据集保留在主页并带诊断，不影响健康数据集工作台。"""

    service = DatasetLibraryService(tmp_path)
    damaged = service.create_dataset("损坏数据集")
    healthy = service.create_dataset("健康数据集")
    service.dataset_repository.paths(damaged.dataset.id).metadata.write_text(
        "{broken",
        encoding="utf-8",
    )
    gateway = ManagedDatasetGateway(service)
    cards = {item.id: item for item in gateway.home_snapshot().datasets}

    assert cards[damaged.dataset.id].health.value == "damaged"
    assert cards[damaged.dataset.id].diagnostic
    assert gateway.workspace_snapshot(damaged.dataset.id) is None
    assert gateway.workspace_snapshot(healthy.dataset.id) is not None


def test_real_empty_home_and_workspace_fit_reference_sizes(tmp_path: Path) -> None:
    """三个基准窗口尺寸下真实主页与空工作台不发生核心区域裁切。"""

    application = _application()
    service = DatasetLibraryService(tmp_path)
    dataset = service.create_dataset("响应式数据集")
    window = ApplicationShell(LocaleService(), ManagedDatasetGateway(service))
    window.show()
    for width, height in ((1366, 768), (1440, 900), (1920, 1080)):
        window.resize(width, height)
        window.navigate(RouteId.HOME.value)
        application.processEvents()
        assert window.centralWidget().width() >= 900
        window.navigate(f"{RouteId.ANNOTATION_WORKSPACE.value}:{dataset.dataset.id}")
        application.processEvents()
        workspace = window.navigation.pages[RouteId.ANNOTATION_WORKSPACE]
        assert isinstance(workspace, AnnotationWorkspace)
        assert workspace.canvas.width() >= 360
        assert workspace.right_panel.width() >= 300
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
