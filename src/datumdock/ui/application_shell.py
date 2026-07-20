"""DatumDock 新应用外壳、页面导航与原型安全边界。"""

from __future__ import annotations

import logging

from PySide6.QtCore import QThreadPool, QTimer
from PySide6.QtGui import QCloseEvent, QIcon, QResizeEvent
from PySide6.QtWidgets import QDialog, QMainWindow, QMessageBox, QStackedWidget, QWidget

from datumdock.domain.models import AppSettings
from datumdock.i18n.catalog import LocaleService, tr
from datumdock.resources import application_icon_path
from datumdock.services.shortcuts import ActionRegistry, ShortcutProfileService
from datumdock.ui.annotation_workspace import AnnotationWorkspace
from datumdock.ui.components import ToastOverlay
from datumdock.ui.dataset_deletion_dialog import ManagedDatasetDeletionDialog
from datumdock.ui.managed_gateway import ManagedDatasetGateway
from datumdock.ui.managed_governance_pages import ManagedGovernancePage
from datumdock.ui.managed_interop_dialogs import (
    ManagedRectangleRepairDialog,
    ManagedXAnyExportDialog,
    ManagedXAnyImportDialog,
)
from datumdock.ui.managed_label_pages import ManagedLabelInspectionPage, ManagedLabelPage
from datumdock.ui.managed_media_dialogs import (
    ManagedImageImportDialog,
    ManagedRenameDialog,
    ManagedTaskCenterDialog,
)
from datumdock.ui.prototype_dialogs import DialogId, DialogRegistry
from datumdock.ui.prototype_gateway import PreviewGateway, UnavailableGateway
from datumdock.ui.prototype_models import (
    CommandStatus,
    UiCommand,
    UiCommandResult,
    UiGateway,
    WorkspaceNavigationTarget,
)
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
        # 主窗口显式绑定 DD 图标，避免嵌入式启动器覆盖 QApplication 的继承图标。
        self.setWindowIcon(QIcon(str(application_icon_path())))
        self.locale_service = locale_service
        self.gateway = gateway
        self.managed_settings = getattr(gateway, "settings", None)
        if (
            self.managed_settings is not None
            and self.managed_settings.ui_locale != locale_service.locale
        ):
            locale_service.set_locale(self.managed_settings.ui_locale)
        settings = self.managed_settings or AppSettings(ui_locale=locale_service.locale)
        self.action_registry = ActionRegistry(parent=self)
        self.shortcut_profiles = ShortcutProfileService(
            self.action_registry,
            settings,
            getattr(gateway, "settings_repository", None) if not gateway.preview_mode else None,
        )
        self.dialog_registry = DialogRegistry(locale_service, gateway.preview_mode)
        self._active_dialogs: list[QDialog] = []
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
        self.startup_timer = QTimer(self)
        self.startup_timer.setSingleShot(True)
        self.startup_timer.timeout.connect(self._finish_startup)
        self.startup_timer.start(360)
        initial_error_key = getattr(gateway, "initial_error_key", "")
        self.initial_error_timer = QTimer(self)
        self.initial_error_timer.setSingleShot(True)
        if initial_error_key:
            self.initial_error_timer.timeout.connect(lambda: self.show_message(initial_error_key))
            self.initial_error_timer.start(460)

    def _finish_startup(self) -> None:
        """窗口仍存活时才从启动页进入主页。"""

        if self.navigation.current == RouteId.STARTUP:
            self.navigation.navigate(RouteId.HOME, remember=False)

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
        settings = SettingsPage(
            self.locale_service,
            self.action_registry,
            self.shortcut_profiles,
        )
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
        settings.settings_change_requested.connect(self._change_setting)
        if self.managed_settings is not None:
            settings.apply_settings(self.managed_settings)

    def _register_context_pages(self, dataset_id: str | None = None) -> None:
        snapshot = self.gateway.workspace_snapshot(dataset_id)
        if snapshot is None:
            return
        workspace = AnnotationWorkspace(
            self.locale_service,
            self.gateway.preview_mode,
            snapshot,
            self.gateway,
            self.action_registry,
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
            if (
                not self.gateway.preview_mode
                and kind == "labels"
                and isinstance(self.gateway, ManagedDatasetGateway)
            ):
                page = ManagedLabelPage(
                    self.locale_service,
                    self.gateway,
                    snapshot.dataset.id,
                )
                page.route_requested.connect(self.navigate)
            elif (
                not self.gateway.preview_mode
                and kind == "inspection"
                and isinstance(self.gateway, ManagedDatasetGateway)
            ):
                page = ManagedLabelInspectionPage(
                    self.locale_service,
                    self.gateway,
                    snapshot.dataset.id,
                )
                page.route_requested.connect(self.navigate)
                page.workspace_target_requested.connect(self.navigate_workspace_target)
            elif (
                not self.gateway.preview_mode
                and kind in {"similarity", "trash"}
                and isinstance(self.gateway, ManagedDatasetGateway)
            ):
                page = ManagedGovernancePage(
                    self.locale_service,
                    self.gateway,
                    snapshot.dataset.id,
                    kind,
                )
                page.route_requested.connect(self.navigate)
                page.command_requested.connect(self._dispatch_command)
            else:
                page = ManagementPage(
                    self.locale_service,
                    kind,
                    snapshot,
                    self.gateway.preview_mode,
                )
            self.navigation.register(route, page)
            self._connect_page(page)

    def navigate_workspace_target(self, target: WorkspaceNavigationTarget) -> None:
        """使用结构化稳定 ID 打开跨页标签检查目标。"""

        current = self.navigation.pages.get(RouteId.ANNOTATION_WORKSPACE)
        if isinstance(current, AnnotationWorkspace) and not current.prepare_to_leave():
            return
        self._register_context_pages(target.dataset_id)
        workspace = self.navigation.pages.get(RouteId.ANNOTATION_WORKSPACE)
        if isinstance(workspace, AnnotationWorkspace):
            workspace.open_navigation_target(target)
            self.navigation.navigate(RouteId.ANNOTATION_WORKSPACE)

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
        current_workspace = self.navigation.pages.get(RouteId.ANNOTATION_WORKSPACE)
        if (
            self.navigation.current == RouteId.ANNOTATION_WORKSPACE
            and isinstance(current_workspace, AnnotationWorkspace)
            and (
                route != RouteId.ANNOTATION_WORKSPACE
                or (context and context != current_workspace.snapshot.dataset.id)
            )
            and not current_workspace.prepare_to_leave()
        ):
            return
        if route == RouteId.ANNOTATION_WORKSPACE:
            snapshot = self.gateway.workspace_snapshot(context or None)
            if snapshot is None:
                self.show_message("toast.not_connected")
                return
            self._register_context_pages(context or None)
        if route not in self.navigation.pages:
            self.show_message("toast.not_connected")
            return
        refresh = getattr(self.navigation.pages[route], "refresh", None)
        if callable(refresh):
            refresh()
        self.navigation.navigate(route)

    def open_dialog(self, dialog_spec: str) -> None:
        """打开注册弹窗并连接统一命令边界。"""

        parts = dialog_spec.split(":")
        dialog_text = parts[0]
        dataset_id = parts[1] if len(parts) > 1 else ""
        sample_id = parts[2] if len(parts) > 2 else ""
        allowed_managed_dialogs = {
            DialogId.CREATE_DATASET,
            DialogId.CREATE_FROM_TEMPLATE,
            DialogId.DATASET_DIAGNOSTICS,
            DialogId.RENAME_DATASET,
            DialogId.ARCHIVE_DATASET,
            DialogId.DELETE_DATASET,
            DialogId.IMAGE_IMPORT,
            DialogId.XANY_IMPORT,
            DialogId.XANY_EXPORT,
            DialogId.XANY_REPAIR,
            DialogId.RENAME_SAMPLES,
            DialogId.DELETE_CURRENT,
            DialogId.DELETE_BATCH,
            DialogId.TASK_CENTER,
        }
        try:
            identifier = DialogId(dialog_text)
        except ValueError:
            self.show_message("toast.not_connected")
            return
        if not self.gateway.preview_mode and identifier == DialogId.IMAGE_IMPORT:
            self._open_managed_import(dataset_id)
            return
        if not self.gateway.preview_mode and identifier == DialogId.DELETE_DATASET:
            self._open_dataset_deletion(dataset_id)
            return
        if not self.gateway.preview_mode and identifier == DialogId.XANY_IMPORT:
            self._open_managed_xany_import(dataset_id)
            return
        if not self.gateway.preview_mode and identifier == DialogId.XANY_EXPORT:
            self._open_managed_xany_export(dataset_id)
            return
        if not self.gateway.preview_mode and identifier == DialogId.XANY_REPAIR:
            self._open_managed_rectangle_repair(dataset_id)
            return
        if not self.gateway.preview_mode and identifier == DialogId.RENAME_SAMPLES:
            self._open_managed_rename(dataset_id)
            return
        if not self.gateway.preview_mode and identifier == DialogId.TASK_CENTER:
            self._open_task_center()
            return
        if not self.gateway.preview_mode and identifier in {
            DialogId.DELETE_CURRENT,
            DialogId.DELETE_BATCH,
        }:
            self._confirm_managed_delete(dataset_id, (sample_id,) if sample_id else ())
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
        if action_id.startswith(("sample.", "trash.")):
            workspace = self.navigation.pages.get(RouteId.ANNOTATION_WORKSPACE)
            if isinstance(workspace, AnnotationWorkspace):
                workspace.refresh_managed_samples(select_first=True)

    def _open_managed_import(self, dataset_id: str) -> None:
        if not dataset_id or not isinstance(self.gateway, ManagedDatasetGateway):
            self.show_message("toast.dataset_unavailable")
            return
        dialog = ManagedImageImportDialog(
            self.locale_service,
            self.gateway,
            dataset_id,
            self,
        )
        dialog.import_finished.connect(self._managed_import_finished)
        dialog.finished.connect(lambda: self._forget_dialog(dialog))
        self._active_dialogs.append(dialog)
        dialog.open()

    def _open_dataset_deletion(self, dataset_id: str) -> None:
        """打开整数据集危险确认；成功后清除旧工作台并回到主页。"""

        if not dataset_id or not isinstance(self.gateway, ManagedDatasetGateway):
            self.show_message("toast.dataset_unavailable")
            return
        dialog = ManagedDatasetDeletionDialog(
            self.locale_service,
            self.gateway,
            dataset_id,
            self,
        )
        dialog.deletion_finished.connect(self._dataset_deletion_finished)
        dialog.finished.connect(lambda: self._forget_dialog(dialog))
        self._active_dialogs.append(dialog)
        dialog.open()

    def _dataset_deletion_finished(self, _dataset_id: str) -> None:
        home = self.navigation.pages.get(RouteId.HOME)
        if isinstance(home, HomePage):
            home.update_snapshot(self.gateway.home_snapshot())
        self.navigation.navigate(RouteId.HOME, remember=False)
        self.show_message("toast.dataset_deleted")

    def _managed_import_finished(self, sample_id: str) -> None:
        workspace = self.navigation.pages.get(RouteId.ANNOTATION_WORKSPACE)
        if isinstance(workspace, AnnotationWorkspace):
            workspace.refresh_managed_samples(sample_id=sample_id)
        home = self.navigation.pages.get(RouteId.HOME)
        if isinstance(home, HomePage):
            home.update_snapshot(self.gateway.home_snapshot())

    def _open_managed_xany_import(self, dataset_id: str) -> None:
        """打开正式交换目录导入向导，并在完成后刷新工作台。"""

        if not dataset_id or not isinstance(self.gateway, ManagedDatasetGateway):
            self.show_message("toast.dataset_unavailable")
            return
        dialog = ManagedXAnyImportDialog(
            self.locale_service,
            self.gateway,
            dataset_id,
            self,
        )
        dialog.import_finished.connect(self._managed_import_finished)
        dialog.finished.connect(lambda: self._forget_dialog(dialog))
        self._active_dialogs.append(dialog)
        dialog.open()

    def _open_managed_xany_export(self, dataset_id: str) -> None:
        """导出向导只接收当前数据集 ID，不把受管路径暴露给页面。"""

        if not dataset_id or not isinstance(self.gateway, ManagedDatasetGateway):
            self.show_message("toast.dataset_unavailable")
            return
        dialog = ManagedXAnyExportDialog(
            self.locale_service,
            self.gateway,
            dataset_id,
            parent=self,
        )
        dialog.finished.connect(lambda: self._forget_dialog(dialog))
        self._active_dialogs.append(dialog)
        dialog.open()

    def _open_managed_rectangle_repair(self, dataset_id: str) -> None:
        """显式打开已有四点矩形检查，不在数据集打开时自动改写标注。"""

        if not dataset_id or not isinstance(self.gateway, ManagedDatasetGateway):
            self.show_message("toast.dataset_unavailable")
            return
        dialog = ManagedRectangleRepairDialog(
            self.locale_service,
            self.gateway,
            dataset_id,
            self,
        )
        dialog.repair_finished.connect(self._managed_import_finished)
        dialog.finished.connect(lambda: self._forget_dialog(dialog))
        self._active_dialogs.append(dialog)
        dialog.open()

    def _open_managed_rename(self, dataset_id: str) -> None:
        if not dataset_id or not isinstance(self.gateway, ManagedDatasetGateway):
            self.show_message("toast.dataset_unavailable")
            return
        dialog = ManagedRenameDialog(
            self.locale_service,
            self.gateway,
            dataset_id,
            self,
        )
        dialog.command_ready.connect(self._dispatch_command)
        dialog.finished.connect(lambda: self._forget_dialog(dialog))
        self._active_dialogs.append(dialog)
        dialog.open()

    def _open_task_center(self) -> None:
        if not isinstance(self.gateway, ManagedDatasetGateway):
            self.show_message("toast.not_connected")
            return
        dialog = ManagedTaskCenterDialog(self.locale_service, self.gateway, self)
        dialog.finished.connect(lambda: self._forget_dialog(dialog))
        self._active_dialogs.append(dialog)
        dialog.open()

    def _confirm_managed_delete(
        self,
        dataset_id: str,
        sample_ids: tuple[str, ...],
    ) -> None:
        if not sample_ids or not isinstance(self.gateway, ManagedDatasetGateway):
            self.show_message("toast.no_sample_selected")
            return
        threshold = self.gateway.settings.trash_sample_threshold
        use_trash = len(sample_ids) <= threshold
        message_key = "dialog.delete.trash" if use_trash else "dialog.delete.permanent"
        first = QMessageBox.question(
            self,
            tr(self.locale_service, "dialog.delete.title"),
            tr(self.locale_service, message_key),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if first != QMessageBox.StandardButton.Yes:
            return
        action = "sample.trash" if use_trash else "sample.delete_permanent"
        if not use_trash:
            final = QMessageBox.warning(
                self,
                tr(self.locale_service, "dialog.delete.final_title"),
                tr(self.locale_service, "dialog.delete.final_body"),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if final != QMessageBox.StandardButton.Yes:
                return
        self._dispatch_command(
            action,
            {
                "dataset_id": dataset_id,
                "sample_ids": sample_ids,
                "threshold": threshold,
            },
        )

    def _forget_dialog(self, dialog: QDialog) -> None:
        if dialog in self._active_dialogs:
            self._active_dialogs.remove(dialog)

    def show_message(self, message_key: str) -> None:
        """翻译稳定消息键并显示克制 Toast。"""

        self.toast.show_message(tr(self.locale_service, message_key))

    def change_locale(self, locale: str) -> None:
        """即时切换系统文案，预览数据对象保持不变。"""

        if locale != self.locale_service.locale:
            self.locale_service.set_locale(locale)
            if not self.gateway.preview_mode:
                self._dispatch_command("settings.update", {"ui_locale": locale})

    def _change_setting(self, name: str, value: object) -> None:
        """步骤三只持久化已接入的设置字段。"""

        if self.gateway.preview_mode:
            self.show_message("toast.preview_applied")
            return
        self._dispatch_command("settings.update", {name: value})
        managed = getattr(self.gateway, "settings", None)
        if managed is not None:
            self.shortcut_profiles.settings = managed.model_copy(deep=True)

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
        """关闭时取消后台任务并等待当前单样本原子步骤结束。"""

        workspace = self.navigation.pages.get(RouteId.ANNOTATION_WORKSPACE)
        if isinstance(workspace, AnnotationWorkspace) and not workspace.prepare_to_leave():
            event.ignore()
            return
        self.locale_service.unsubscribe(self.retranslate_ui)
        self.startup_timer.stop()
        self.initial_error_timer.stop()
        close_gateway = getattr(self.gateway, "close", None)
        if callable(close_gateway):
            close_gateway()
        # 缩略图和画布解码使用 Qt 全局读取线程，退出前等待它们释放文件句柄。
        QThreadPool.globalInstance().waitForDone()
        super().closeEvent(event)
