"""DatumDock 新应用外壳、页面导航与原型安全边界。"""

from __future__ import annotations

import logging

from PySide6.QtCore import QTimer
from PySide6.QtGui import QCloseEvent, QResizeEvent
from PySide6.QtWidgets import QMainWindow, QStackedWidget, QWidget

from datumdock.i18n.catalog import LocaleService, tr
from datumdock.ui.annotation_workspace import AnnotationWorkspace
from datumdock.ui.components import ToastOverlay
from datumdock.ui.managed_gateway import ManagedDatasetGateway
from datumdock.ui.prototype_dialogs import DialogId, DialogRegistry, PreviewFlowDialog
from datumdock.ui.prototype_gateway import PreviewGateway, UnavailableGateway
from datumdock.ui.prototype_models import CommandStatus, UiCommand, UiCommandResult, UiGateway
from datumdock.ui.prototype_pages import (
    BasePage,
    ComponentGalleryPage,
    HomePage,
    InfoPage,
    LearningCenterPage,
    ManagementPage,
    RouteId,
    SettingsPage,
    StartupPage,
    TutorialReaderPage,
)

logger = logging.getLogger(__name__)


class NavigationController:
    """维护稳定页面实例和返回栈，页面不直接操作主窗口堆栈。"""

    def __init__(self, stack: QStackedWidget) -> None:
        self.stack = stack
        self.pages: dict[RouteId, QWidget] = {}
        self.history: list[RouteId] = []
        self.current: RouteId | None = None

    def register(self, route: RouteId, page: QWidget) -> None:
        """注册页面并确保同一路由只有一个实例。"""

        if route in self.pages:
            old_page = self.pages[route]
            self.stack.removeWidget(old_page)
            old_page.deleteLater()
        self.pages[route] = page
        self.stack.addWidget(page)

    def navigate(self, route: RouteId, remember: bool = True) -> None:
        """切换到已注册页面并记录可返回路径。"""

        page = self.pages[route]
        if remember and self.current is not None and self.current != route:
            self.history.append(self.current)
        self.current = route
        self.stack.setCurrentWidget(page)

    def back(self, fallback: RouteId = RouteId.HOME) -> None:
        """返回上一页面，历史为空时回到安全主页。"""

        route = self.history.pop() if self.history else fallback
        self.navigate(route, remember=False)


class ApplicationShell(QMainWindow):
    """正式启动的新外壳；UI 原型不实例化旧工作区服务。"""

    def __init__(
        self,
        locale_service: LocaleService,
        gateway: UiGateway,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.locale_service = locale_service
        self.gateway = gateway
        self.dialog_registry = DialogRegistry(locale_service, gateway.preview_mode)
        self._active_dialogs: list[PreviewFlowDialog] = []
        self.setMinimumSize(900, 540)
        self.resize(1440, 900)
        self.stack = QStackedWidget()
        self.setCentralWidget(self.stack)
        self.navigation = NavigationController(self.stack)
        self.toast = ToastOverlay(self)
        self._register_static_pages()
        self._register_context_pages()
        self.locale_service.subscribe(self.retranslate_ui)
        self.retranslate_ui()
        self.navigation.navigate(RouteId.STARTUP, remember=False)
        QTimer.singleShot(360, lambda: self.navigation.navigate(RouteId.HOME, remember=False))
        initial_error_key = getattr(gateway, "initial_error_key", "")
        if initial_error_key:
            QTimer.singleShot(460, lambda: self.show_message(initial_error_key))

    @classmethod
    def for_mode(
        cls,
        locale_service: LocaleService,
        preview_mode: bool,
        gateway: UiGateway | None = None,
    ) -> ApplicationShell:
        """预览模式只用内存；普通模式初始化真实受管资料库。"""

        if gateway is None:
            if preview_mode:
                gateway = PreviewGateway()
            else:
                try:
                    gateway = ManagedDatasetGateway.from_default_root()
                except Exception:
                    gateway = UnavailableGateway()
        return cls(locale_service, gateway)

    def _register_static_pages(self) -> None:
        startup = StartupPage(self.locale_service)
        home = HomePage(
            self.locale_service,
            self.gateway.preview_mode,
            self.gateway.home_snapshot(),
        )
        learning = LearningCenterPage(self.locale_service)
        tutorial = TutorialReaderPage(self.locale_service)
        release = InfoPage(
            self.locale_service,
            "release.title",
            "release.subtitle",
            "release.body",
        )
        about = InfoPage(
            self.locale_service,
            "about.title",
            "about.subtitle",
            "release.body",
        )
        settings = SettingsPage(self.locale_service)
        pages: tuple[tuple[RouteId, QWidget], ...] = (
            (RouteId.STARTUP, startup),
            (RouteId.HOME, home),
            (RouteId.LEARNING_CENTER, learning),
            (RouteId.TUTORIAL_READER, tutorial),
            (RouteId.RELEASE_NOTES, release),
            (RouteId.ABOUT, about),
            (RouteId.SETTINGS, settings),
        )
        if self.gateway.preview_mode:
            pages += ((RouteId.COMPONENT_GALLERY, ComponentGalleryPage(self.locale_service)),)
        for route, page in pages:
            self.navigation.register(route, page)
            self._connect_page(page)
        settings.locale_change_requested.connect(self.change_locale)

    def _register_context_pages(self, dataset_id: str | None = None) -> None:
        snapshot = self.gateway.workspace_snapshot(dataset_id)
        if snapshot is None:
            return
        workspace = AnnotationWorkspace(
            self.locale_service,
            self.gateway.preview_mode,
            snapshot,
        )
        workspace.home_requested.connect(lambda: self.navigate(RouteId.HOME.value))
        workspace.route_requested.connect(self.navigate)
        workspace.dialog_requested.connect(self.open_dialog)
        workspace.message_requested.connect(self.show_message)
        self.navigation.register(RouteId.ANNOTATION_WORKSPACE, workspace)
        page_specs = (
            (RouteId.LABEL_MANAGER, "labels"),
            (RouteId.LABEL_INSPECTION, "inspection"),
            (RouteId.LABEL_COMPARISON, "comparison"),
            (RouteId.MODEL_MANAGER, "models"),
            (RouteId.SIMILARITY_REVIEW, "similarity"),
            (RouteId.TRASH, "trash"),
            (RouteId.DATASET_OVERVIEW, "overview"),
        )
        for route, kind in page_specs:
            page = ManagementPage(
                self.locale_service,
                kind,
                snapshot,
                self.gateway.preview_mode,
            )
            self.navigation.register(route, page)
            self._connect_page(page)

    def _connect_page(self, page: QWidget) -> None:
        """把页面意图集中转发到导航、弹窗和状态提示。"""

        if not isinstance(page, BasePage):
            return
        page.route_requested.connect(self.navigate)
        page.dialog_requested.connect(self.open_dialog)
        page.command_requested.connect(self._dispatch_command)
        page.message_requested.connect(self.show_message)

    def navigate(self, route_spec: str) -> None:
        """处理普通路由和带数据集 ID 的工作台路由。"""

        route_text, _, context = route_spec.partition(":")
        route = RouteId(route_text)
        if route == RouteId.ANNOTATION_WORKSPACE:
            snapshot = self.gateway.workspace_snapshot(context or None)
            if snapshot is None:
                self.show_message("toast.not_connected")
                return
            self._register_context_pages(context or None)
        if route not in self.navigation.pages:
            self.show_message("toast.not_connected")
            return
        self.navigation.navigate(route)

    def open_dialog(self, dialog_spec: str) -> None:
        """打开注册弹窗并连接统一命令边界。"""

        dialog_text, _, dataset_id = dialog_spec.partition(":")
        allowed_managed_dialogs = {
            DialogId.CREATE_DATASET,
            DialogId.CREATE_FROM_TEMPLATE,
            DialogId.DATASET_DIAGNOSTICS,
            DialogId.RENAME_DATASET,
            DialogId.ARCHIVE_DATASET,
        }
        try:
            identifier = DialogId(dialog_text)
        except ValueError:
            self.show_message("toast.not_connected")
            return
        if not self.gateway.preview_mode and identifier not in allowed_managed_dialogs:
            self.show_message("toast.not_connected")
            return
        home_snapshot = self.gateway.home_snapshot()
        context: dict[str, str] = {}
        if dataset_id:
            dataset = next(
                (item for item in home_snapshot.datasets if item.id == dataset_id),
                None,
            )
            if dataset is None:
                self.show_message("toast.dataset_unavailable")
                return
            context = {
                "dataset_id": dataset.id,
                "name": dataset.name,
                "description": dataset.description,
                "diagnostic": dataset.diagnostic,
            }
        try:
            dialog = self.dialog_registry.create(
                identifier,
                self,
                context=context,
                datasets=home_snapshot.datasets,
            )
        except ValueError:
            self.show_message("toast.not_connected")
            return
        dialog.command_ready.connect(self._dispatch_command)
        dialog.finished.connect(lambda: self._forget_dialog(dialog))
        self._active_dialogs.append(dialog)
        dialog.open()

    def _dispatch_command(self, action_id: str, payload: dict) -> None:
        """所有原型操作只经过网关，窗口回调不伪造业务成功。"""

        try:
            result = self.gateway.dispatch(UiCommand(action_id, payload))
        except Exception:
            logger.exception("UI 网关命令越过安全边界: %s", action_id)
            result = UiCommandResult(CommandStatus.ERROR, "toast.library_operation_failed")
        self.show_message(result.message_key)
        home = self.navigation.pages.get(RouteId.HOME)
        if isinstance(home, HomePage):
            home.update_snapshot(self.gateway.home_snapshot())
        applied = result.status in {CommandStatus.APPLIED, CommandStatus.PREVIEW_APPLIED}
        if result.affected_id and applied:
            if action_id in {"dataset.create", "dataset.create_from_template"}:
                self._register_context_pages(result.affected_id)
                self.navigation.navigate(RouteId.ANNOTATION_WORKSPACE)
            elif action_id == "dataset.rename":
                self._register_context_pages(result.affected_id)

    def _forget_dialog(self, dialog: PreviewFlowDialog) -> None:
        if dialog in self._active_dialogs:
            self._active_dialogs.remove(dialog)

    def show_message(self, message_key: str) -> None:
        """翻译稳定消息键并显示克制 Toast。"""

        self.toast.show_message(tr(self.locale_service, message_key))

    def change_locale(self, locale: str) -> None:
        """即时切换系统文案，预览数据对象保持不变。"""

        if locale != self.locale_service.locale:
            self.locale_service.set_locale(locale)

    def retranslate_ui(self) -> None:
        """刷新全部已创建页面和仍打开的弹窗。"""

        self.setWindowTitle(tr(self.locale_service, "app.title"))
        for page in self.navigation.pages.values():
            retranslate = getattr(page, "retranslate_ui", None)
            if callable(retranslate):
                retranslate()
        for dialog in list(self._active_dialogs):
            dialog.retranslate_ui()

    def resizeEvent(self, event: QResizeEvent) -> None:
        """窗口变化时让 Toast 始终保持在右上安全区域。"""

        if self.toast.isVisible():
            self.toast.move(max(16, self.width() - self.toast.width() - 24), 74)
        super().resizeEvent(event)

    def closeEvent(self, event: QCloseEvent) -> None:
        """关闭时注销语言监听；原型没有需要写入的会话状态。"""

        self.locale_service.unsubscribe(self.retranslate_ui)
        super().closeEvent(event)
