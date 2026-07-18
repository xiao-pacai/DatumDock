"""DatumDock 主窗口：工作区、受管数据集池、标注与导出工作流。"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QColor, QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QStatusBar,
    QTableWidget,
    QTableWidgetItem,
    QToolBar,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from datumdock.domain.models import (
    AppSettings,
    Dataset,
    DatasetSample,
    ExportRequest,
    NamingPolicy,
    Project,
    ReviewStatus,
    Workspace,
)
from datumdock.i18n.catalog import LocaleService, tr
from datumdock.resources import resource_root
from datumdock.services.backup import ProjectBackupService
from datumdock.services.dataset import DatasetPoolService, DuplicateCandidate
from datumdock.services.exporting import SplitPlanner, XAnyLabelingExporter, YoloDetectionExporter
from datumdock.services.interop import XAnyLabelingInteropService
from datumdock.services.models import AutoAnnotationService, InferenceBackendSelector
from datumdock.services.quality import AnnotationQualityService
from datumdock.services.shortcuts import DEFAULT_SHORTCUTS, ShortcutService
from datumdock.services.storage import ProjectIndexRepository, read_json_model, write_json_atomic
from datumdock.services.workspace import WorkspaceService
from datumdock.ui.annotation_canvas import AnnotationCanvas
from datumdock.ui.icons import IconRegistry
from datumdock.ui.label_manager import LabelManagerDialog
from datumdock.ui.model_manager import ModelManagerDialog
from datumdock.ui.sample_browser import SampleBrowser
from datumdock.ui.similarity_dialog import SimilarityDialog
from datumdock.ui.trash_dialog import TrashDialog


class DuplicateDialog(QDialog):
    """以并排图片帮助用户决定是否保留完全重复的导入图片。"""

    def __init__(
        self, locale_service: LocaleService, candidate: DuplicateCandidate, parent: QWidget
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr(locale_service, "dialog.duplicate.title"))
        self.keep = False
        layout = QVBoxLayout(self)
        body = QLabel(tr(locale_service, "dialog.duplicate.body"))
        body.setWordWrap(True)
        layout.addWidget(body)
        images = QHBoxLayout()
        images.addWidget(self._image_label(candidate.source_path, candidate.source_path.name))
        existing = candidate.existing_samples[0]
        images.addWidget(self._image_label(Path(existing.image_path), existing.filename))
        layout.addLayout(images)
        buttons = QDialogButtonBox()
        skip = buttons.addButton(
            tr(locale_service, "dialog.duplicate.skip"),
            QDialogButtonBox.ButtonRole.RejectRole,
        )
        keep = buttons.addButton(
            tr(locale_service, "dialog.duplicate.keep"),
            QDialogButtonBox.ButtonRole.AcceptRole,
        )
        skip.clicked.connect(self.reject)
        keep.clicked.connect(self._accept_keep)
        layout.addWidget(buttons)

    @staticmethod
    def _image_label(path: Path, caption: str) -> QWidget:
        """把缩放后的对比图和来源文件名组合为一个可读的小卡片。"""

        container = QWidget()
        layout = QVBoxLayout(container)
        image = QLabel()
        image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pixmap = QPixmap(str(path))
        image.setPixmap(pixmap.scaled(300, 220, Qt.AspectRatioMode.KeepAspectRatio))
        label = QLabel(caption)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setWordWrap(True)
        layout.addWidget(image)
        layout.addWidget(label)
        return container

    def _accept_keep(self) -> None:
        """明确记录用户选择，回调返回后才允许数据集池继续导入。"""

        self.keep = True
        self.accept()


class ExportDialog(QDialog):
    """当次 YOLO 导出向导；不会把路径、方案或历史写入项目。"""

    def __init__(
        self, locale_service: LocaleService, settings: AppSettings, parent: QWidget
    ) -> None:
        super().__init__(parent)
        self.locale_service = locale_service
        self.setWindowTitle(tr(locale_service, "dialog.export.title"))
        self.directory_input = QLineEdit()
        self.include_negative = QPushButton()
        self.include_negative.setCheckable(True)
        self.include_negative.setText(tr(locale_service, "dialog.export.include_negative"))
        self.train = QSpinBox()
        self.val = QSpinBox()
        self.test = QSpinBox()
        for widget, value in zip(
            (self.train, self.val, self.test), settings.default_split, strict=True
        ):
            widget.setRange(0, 100)
            widget.setValue(value)
        self._build_ui()

    def _build_ui(self) -> None:
        """构建包含输出路径、比例和负样本开关的最小导出表单。"""

        layout = QVBoxLayout(self)
        form = QFormLayout()
        output_row = QHBoxLayout()
        output_row.addWidget(self.directory_input, 1)
        choose = QPushButton(tr(self.locale_service, "dialog.export.choose"))
        choose.clicked.connect(self.choose_directory)
        output_row.addWidget(choose)
        form.addRow(tr(self.locale_service, "dialog.export.directory"), output_row)
        form.addRow(tr(self.locale_service, "dialog.export.train"), self.train)
        form.addRow(tr(self.locale_service, "dialog.export.val"), self.val)
        form.addRow(tr(self.locale_service, "dialog.export.test"), self.test)
        layout.addLayout(form)
        layout.addWidget(self.include_negative)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def choose_directory(self) -> None:
        """用户选择目标目录；导出器仍会拒绝非空目录以防无提示覆盖。"""

        chosen = QFileDialog.getExistingDirectory(
            self, tr(self.locale_service, "dialog.export.directory")
        )
        if chosen:
            self.directory_input.setText(chosen)

    def accept(self) -> None:
        """提交前验证比例与目标，避免创建不完整的临时导出目录。"""

        if not self.directory_input.text().strip():
            QMessageBox.warning(
                self,
                tr(self.locale_service, "dialog.error"),
                tr(self.locale_service, "dialog.required"),
            )
            return
        if self.train.value() + self.val.value() + self.test.value() != 100:
            QMessageBox.warning(
                self,
                tr(self.locale_service, "dialog.error"),
                tr(self.locale_service, "dialog.export.invalid_split"),
            )
            return
        super().accept()

    def build_request(self, dataset_id: str) -> ExportRequest:
        """在点击确认后创建一次性的导出请求，不持久化到任何配置文件。"""

        return ExportRequest(
            dataset_id=dataset_id,
            output_directory=self.directory_input.text().strip(),
            split=(self.train.value(), self.val.value(), self.test.value()),
            include_unannotated=self.include_negative.isChecked(),
        )


class RenameSamplesDialog(QDialog):
    """在真正写入前预览受管图片的新名称；外部导入来源不会出现在操作范围内。"""

    def __init__(
        self,
        locale_service: LocaleService,
        policy: NamingPolicy,
        samples: list[DatasetSample],
        parent: QWidget,
    ) -> None:
        super().__init__(parent)
        self.locale_service = locale_service
        self.samples = samples
        self.setWindowTitle(tr(locale_service, "dialog.rename.title"))
        self.prefix = QLineEdit(policy.prefix)
        self.start = QSpinBox()
        self.start.setRange(0, 9_999_999)
        self.start.setValue(policy.start_index)
        self.padding = QSpinBox()
        self.padding.setRange(1, 12)
        self.padding.setValue(policy.padding)
        self.preview = QLabel()
        self.preview.setWordWrap(True)
        self._build_ui()
        self._refresh_preview()

    def _build_ui(self) -> None:
        """表单仅接收命名策略字段，预览随着输入变化而刷新。"""

        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.addRow(tr(self.locale_service, "dialog.rename.prefix"), self.prefix)
        form.addRow(tr(self.locale_service, "dialog.rename.start"), self.start)
        form.addRow(tr(self.locale_service, "dialog.rename.padding"), self.padding)
        layout.addLayout(form)
        layout.addWidget(self.preview)
        self.prefix.textChanged.connect(self._refresh_preview)
        self.start.valueChanged.connect(self._refresh_preview)
        self.padding.valueChanged.connect(self._refresh_preview)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _refresh_preview(self) -> None:
        """展示按当前文件名顺序计算的首批目标名称，避免执行后才发现规则写错。"""

        policy = self.build_policy()
        shown_samples = sorted(self.samples, key=lambda item: item.filename)[:5]
        examples = [
            f"{sample.filename} → {policy.filename_for(policy.start_index + index)}"
            for index, sample in enumerate(shown_samples)
        ]
        header = tr(self.locale_service, "dialog.rename.preview").format(
            shown=len(shown_samples),
            total=len(self.samples),
        )
        self.preview.setText("\n".join([header, *examples]))

    def build_policy(self) -> NamingPolicy:
        """将当前表单值验证为领域命名规则，非法前缀由确认时统一提示。"""

        return NamingPolicy(
            prefix=self.prefix.text().strip() or "image",
            start_index=self.start.value(),
            padding=self.padding.value(),
        )


class ShortcutDialog(QDialog):
    """集中编辑全局快捷键，并在确认时检查冲突。"""

    def __init__(
        self,
        locale_service: LocaleService,
        settings: AppSettings,
        actions: dict[str, QAction],
        parent: QWidget,
    ) -> None:
        super().__init__(parent)
        self.locale_service = locale_service
        self.settings = settings
        self.actions = actions
        self.inputs: dict[str, QLineEdit] = {}
        self.setWindowTitle(tr(locale_service, "dialog.shortcuts.title"))
        self._build_ui()

    def _build_ui(self) -> None:
        """使用文本序列输入，便于直接粘贴常见的 Ctrl+Shift+X 形式。"""

        layout = QVBoxLayout(self)
        self.table = QTableWidget(len(DEFAULT_SHORTCUTS), 2)
        self.table.setHorizontalHeaderLabels(
            [
                tr(self.locale_service, "menu.project"),
                tr(self.locale_service, "dialog.shortcuts.sequence"),
            ]
        )
        for row, action_id in enumerate(DEFAULT_SHORTCUTS):
            item = QTableWidgetItem(tr(self.locale_service, f"action.{action_id}"))
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(row, 0, item)
            input_widget = QLineEdit(
                self.settings.shortcut_overrides.get(action_id, DEFAULT_SHORTCUTS[action_id])
            )
            self.table.setCellWidget(row, 1, input_widget)
            self.inputs[action_id] = input_widget
        self.table.resizeColumnsToContents()
        layout.addWidget(self.table)
        controls = QHBoxLayout()
        restore = QPushButton(tr(self.locale_service, "dialog.shortcuts.restore"))
        restore.clicked.connect(self.restore_defaults)
        controls.addWidget(restore)
        controls.addStretch()
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        controls.addWidget(buttons)
        layout.addLayout(controls)

    def restore_defaults(self) -> None:
        """恢复默认值只修改对话框内输入，仍需用户确认才写入设置。"""

        for action_id, input_widget in self.inputs.items():
            input_widget.setText(DEFAULT_SHORTCUTS[action_id])

    def accept(self) -> None:
        """校验后只保存和默认值不同的覆盖项，配置保持精简可读。"""

        values = {action_id: widget.text().strip() for action_id, widget in self.inputs.items()}
        try:
            ShortcutService.validate(values)
        except ValueError:
            QMessageBox.warning(
                self,
                tr(self.locale_service, "dialog.error"),
                tr(self.locale_service, "dialog.shortcuts.invalid"),
            )
            return
        self.settings.shortcut_overrides = {
            action_id: value
            for action_id, value in values.items()
            if value != DEFAULT_SHORTCUTS[action_id]
        }
        ShortcutService().apply(self.actions, self.settings.shortcut_overrides)
        super().accept()


class SettingsDialog(QDialog):
    """编辑全局偏好；语言、回收站阈值和快捷键均不修改项目内容。"""

    def __init__(
        self,
        locale_service: LocaleService,
        settings: AppSettings,
        actions: dict[str, QAction],
        parent: QWidget,
    ) -> None:
        super().__init__(parent)
        self.locale_service = locale_service
        self.settings = settings
        self.actions = actions
        self.language_combo = QComboBox()
        self.threshold_input = QComboBox()
        self.threshold_input.addItems(["10", "30", "50", "100"])
        self.threshold_field = QWidget()
        threshold_layout = QHBoxLayout(self.threshold_field)
        threshold_layout.setContentsMargins(0, 0, 0, 0)
        threshold_layout.addWidget(self.threshold_input)
        self.threshold_help = QLabel("?")
        self.threshold_help.setObjectName("helpHint")
        self.threshold_help.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.threshold_help.setFixedWidth(20)
        threshold_layout.addWidget(self.threshold_help)
        threshold_layout.addStretch()
        self.shortcuts_button = QPushButton()
        self.shortcuts_button.clicked.connect(self.open_shortcuts)
        self._build_ui()
        self.retranslate_ui()
        self.locale_service.subscribe(self.retranslate_ui)
        self.finished.connect(lambda _: self.locale_service.unsubscribe(self.retranslate_ui))

    def _build_ui(self) -> None:
        """预先建立表单布局，语言切换时只替换文案和选项。"""

        self.layout_root = QVBoxLayout(self)
        self.form = QFormLayout()
        self.layout_root.addLayout(self.form)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.accept)
        self.layout_root.addWidget(buttons)

    def retranslate_ui(self) -> None:
        """语言变化后不接触项目数据，仅刷新设置对话框可见文案。"""

        self.setWindowTitle(tr(self.locale_service, "settings.title"))
        self.language_combo.blockSignals(True)
        self.language_combo.clear()
        self.language_combo.addItem("简体中文", "zh_CN")
        self.language_combo.addItem("English", "en_US")
        self.language_combo.setCurrentIndex(0 if self.locale_service.locale == "zh_CN" else 1)
        self.language_combo.blockSignals(False)
        self.threshold_input.setCurrentText(str(self.settings.trash_sample_threshold))
        self.threshold_help.setToolTip(tr(self.locale_service, "tooltip.trash_threshold"))
        self.threshold_help.setAccessibleName(tr(self.locale_service, "tooltip.trash_threshold"))
        while self.form.rowCount():
            self.form.removeRow(0)
        self.form.addRow(tr(self.locale_service, "settings.language"), self.language_combo)
        self.form.addRow(tr(self.locale_service, "settings.trash"), self.threshold_field)
        self.shortcuts_button.setText(tr(self.locale_service, "settings.shortcuts"))
        self.form.addRow(tr(self.locale_service, "settings.shortcuts"), self.shortcuts_button)

    def open_shortcuts(self) -> None:
        """打开独立快捷键编辑器，以便用户随时恢复默认值。"""

        ShortcutDialog(self.locale_service, self.settings, self.actions, self).exec()

    def accept(self) -> None:
        """立即应用语言并保存由主窗口负责的全局偏好对象。"""

        self.settings.trash_sample_threshold = int(self.threshold_input.currentText())
        self.locale_service.set_locale(str(self.language_combo.currentData()))
        super().accept()


class MainWindow(QMainWindow):
    """连接持久化服务与 GUI，耗时 I/O 均保持在明确的用户触发入口。"""

    def __init__(self, locale_service: LocaleService, workspace_service: WorkspaceService) -> None:
        super().__init__()
        self.locale_service = locale_service
        self.workspace_service = workspace_service
        self.pool_service = DatasetPoolService()
        self.settings = AppSettings()
        self.workspace_root: Path | None = None
        self.workspace: Workspace | None = None
        self.current_project: Project | None = None
        self.current_dataset: Dataset | None = None
        self.current_sample: DatasetSample | None = None
        self.is_sidebar_collapsed = False
        self._browser_context: tuple[str, str] | None = None
        self._actions: dict[str, QAction] = {}
        self.icon_registry = IconRegistry(resource_root())
        self._build_ui()
        self.locale_service.subscribe(self.retranslate_ui)
        self.retranslate_ui()

    def _build_ui(self) -> None:
        """一次性创建窗口结构，后续通过上下文刷新加载项目内容。"""

        self.setMinimumSize(1180, 720)
        self._create_actions()
        self._create_menu_and_toolbar()
        self._create_content()
        self.setStatusBar(QStatusBar())
        ShortcutService().apply(self._actions, self.settings.shortcut_overrides)

    def _create_actions(self) -> None:
        """将所有用户操作集中注册为 QAction，便于菜单、工具栏和快捷键共用。"""

        action_specs = {
            "new_workspace": self.create_workspace,
            "open_workspace": self.open_workspace_dialog,
            "new_project": self.create_project,
            "new_dataset": self.create_dataset,
            "import_images": self.import_images,
            "import_xany": self.import_xany,
            "labels": self.open_label_manager,
            "models": self.open_model_manager,
            "similarity": self.open_similarity_review,
            "export": self.export_dataset,
            "export_xany": self.export_xany,
            "export_backup": self.export_backup,
            "import_backup": self.import_backup,
            "delete_sample": self.delete_current_sample,
            "trash": self.open_trash,
            "rename_samples": self.rename_dataset_samples,
            "quality_check": self.check_current_annotation_quality,
            "previous_sample": lambda: self.select_adjacent_sample(-1),
            "next_sample": lambda: self.select_adjacent_sample(1),
            "undo": lambda: self.canvas.undo(),
            "redo": lambda: self.canvas.redo(),
            "fit_image": lambda: self.canvas.fit_image(),
            "zoom_in": lambda: self.canvas.zoom_in(),
            "zoom_out": lambda: self.canvas.zoom_out(),
            "settings": self.open_settings,
            "about": self.open_about,
            "collapse_sidebar": self.toggle_sidebar,
        }
        for action_id, callback in action_specs.items():
            action = QAction(self)
            action.triggered.connect(callback)
            self._actions[action_id] = action
        icon_names = {
            "new_workspace": "workspace",
            "open_workspace": "workspace",
            "new_project": "workspace",
            "new_dataset": "dataset",
            "import_images": "import",
            "import_xany": "import",
            "labels": "labels",
            "models": "models",
            "export": "export",
            "export_xany": "export",
            "export_backup": "export",
            "import_backup": "import",
            "delete_sample": "trash",
            "trash": "trash",
            "settings": "settings",
        }
        for action_id, icon_name in icon_names.items():
            self._actions[action_id].setIcon(self.icon_registry.icon(icon_name))

    def _create_menu_and_toolbar(self) -> None:
        """组织常用入口，任何行动仍复用同一个 QAction 回调。"""

        self.file_menu = self.menuBar().addMenu("")
        self.project_menu = self.menuBar().addMenu("")
        self.help_menu = self.menuBar().addMenu("")
        self.file_menu.addActions(
            [
                self._actions["new_workspace"],
                self._actions["open_workspace"],
                self._actions["import_images"],
                self._actions["import_xany"],
                self._actions["export"],
                self._actions["export_xany"],
                self._actions["export_backup"],
                self._actions["import_backup"],
            ]
        )
        self.project_menu.addActions(
            [
                self._actions["new_project"],
                self._actions["new_dataset"],
                self._actions["labels"],
                self._actions["models"],
                self._actions["similarity"],
                self._actions["rename_samples"],
                self._actions["quality_check"],
                self._actions["delete_sample"],
                self._actions["trash"],
            ]
        )
        self.help_menu.addAction(self._actions["about"])
        toolbar = QToolBar(self)
        toolbar.setMovable(False)
        self.addToolBar(toolbar)
        toolbar.addActions(
            [
                self._actions["new_workspace"],
                self._actions["open_workspace"],
                self._actions["new_project"],
                self._actions["new_dataset"],
                self._actions["import_images"],
                self._actions["labels"],
                self._actions["export"],
                self._actions["export_xany"],
                self._actions["export_backup"],
                self._actions["settings"],
            ]
        )

    def _create_content(self) -> None:
        """创建品牌侧栏、中心样本/画布工作区与标签/复核右栏。"""

        self.sidebar = QFrame()
        self.sidebar.setObjectName("sidebar")
        sidebar_layout = QVBoxLayout(self.sidebar)
        sidebar_layout.setContentsMargins(10, 10, 10, 10)
        self.brand_button = QPushButton()
        self.brand_button.setFlat(True)
        self.brand_button.clicked.connect(self.show_welcome)
        self.brand_button.setAccessibleName("DatumDock")
        self.brand_label = QLabel()
        self.brand_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        brand_layout = QVBoxLayout(self.brand_button)
        brand_layout.addWidget(self.brand_label)
        sidebar_layout.addWidget(self.brand_button)
        self.sidebar_title = QLabel()
        self.sidebar_title.setObjectName("panelTitle")
        sidebar_layout.addWidget(self.sidebar_title)
        self.project_tree = QTreeWidget()
        self.project_tree.setHeaderHidden(True)
        self.project_tree.itemSelectionChanged.connect(self.select_tree_item)
        sidebar_layout.addWidget(self.project_tree, 1)
        self.sample_title = QLabel()
        self.sample_title.setObjectName("panelTitle")
        sidebar_layout.addWidget(self.sample_title)
        self.sample_placeholder = QLabel()
        self.sample_placeholder.setWordWrap(True)
        sidebar_layout.addWidget(self.sample_placeholder)

        self.pages = QStackedWidget()
        self.welcome_page = self._build_welcome_page()
        self.canvas_page = self._build_canvas_page()
        self.pages.addWidget(self.welcome_page)
        self.pages.addWidget(self.canvas_page)

        self.right_panel = QFrame()
        self.right_panel.setObjectName("rightPanel")
        right_layout = QVBoxLayout(self.right_panel)
        right_layout.setContentsMargins(10, 10, 10, 10)
        self.review_title = QLabel()
        self.review_title.setObjectName("panelTitle")
        self.review_combo = QComboBox()
        self.review_combo.currentIndexChanged.connect(self.update_review_status)
        self.label_title = QLabel()
        self.label_title.setObjectName("panelTitle")
        self.label_list = QTreeWidget()
        self.label_list.setHeaderHidden(True)
        self.label_list.itemSelectionChanged.connect(self.select_drawing_label)
        self.labels_placeholder = QLabel()
        self.labels_placeholder.setWordWrap(True)
        right_layout.addWidget(self.review_title)
        right_layout.addWidget(self.review_combo)
        right_layout.addWidget(self.label_title)
        right_layout.addWidget(self.label_list, 1)
        right_layout.addWidget(self.labels_placeholder)

        self.splitter = QSplitter()
        self.splitter.addWidget(self.sidebar)
        self.splitter.addWidget(self.pages)
        self.splitter.addWidget(self.right_panel)
        self.splitter.setSizes([270, 760, 250])
        self.setCentralWidget(self.splitter)

    def _build_welcome_page(self) -> QWidget:
        """欢迎页展示完整字标和两个不具破坏性的工作区入口。"""

        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.welcome_logo = QLabel()
        self.welcome_logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.welcome_title = QLabel()
        self.welcome_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.welcome_title.setStyleSheet("font-size: 24px; font-weight: 600;")
        self.welcome_description = QLabel()
        self.welcome_description.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.welcome_description.setWordWrap(True)
        self.welcome_create = QPushButton()
        self.welcome_create.setObjectName("primaryButton")
        self.welcome_create.clicked.connect(self.create_workspace)
        self.welcome_open = QPushButton()
        self.welcome_open.clicked.connect(self.open_workspace_dialog)
        layout.addWidget(self.welcome_logo)
        layout.addSpacing(18)
        layout.addWidget(self.welcome_title)
        layout.addWidget(self.welcome_description)
        layout.addSpacing(12)
        layout.addWidget(self.welcome_create, alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.welcome_open, alignment=Qt.AlignmentFlag.AlignCenter)
        return page

    def _build_canvas_page(self) -> QWidget:
        """将分页样本浏览器与独立标注画布放到垂直工作流中。"""

        page = QWidget()
        layout = QVBoxLayout(page)
        self.sample_browser = SampleBrowser(self.locale_service)
        self.sample_browser.sample_selected.connect(self.load_sample)
        self.sample_browser.preview_changed.connect(self.save_dataset_preference)
        self.canvas = AnnotationCanvas()
        self.canvas.document_changed.connect(self.auto_save_annotation)
        self.canvas.rectangle_activated.connect(self.choose_rectangle_label)
        self.canvas.message.connect(self.statusBar().showMessage)
        workbench = QSplitter(Qt.Orientation.Vertical)
        workbench.addWidget(self.sample_browser)
        workbench.addWidget(self.canvas)
        workbench.setSizes([260, 460])
        layout.addWidget(workbench)
        return page

    def _set_brand_pixmaps(self) -> None:
        """正常侧栏、欢迎页和关于页都使用同一份完整 DatumDock 字标。"""

        logo_path = resource_root() / "assets" / "brand" / "datumdock-wordmark-v3.png"
        pixmap = QPixmap(str(logo_path))
        if pixmap.isNull():
            self.brand_label.setText("DatumDock")
            self.welcome_logo.setText("DatumDock")
            return
        self.brand_label.setPixmap(
            pixmap.scaledToWidth(210, Qt.TransformationMode.SmoothTransformation)
        )
        self.welcome_logo.setPixmap(
            pixmap.scaledToWidth(410, Qt.TransformationMode.SmoothTransformation)
        )

    def retranslate_ui(self) -> None:
        """刷新系统文案；项目名称、标签别名和文件名保持用户原始内容。"""

        self.setWindowTitle(tr(self.locale_service, "app.title"))
        self.file_menu.setTitle(tr(self.locale_service, "menu.file"))
        self.project_menu.setTitle(tr(self.locale_service, "menu.project"))
        self.help_menu.setTitle(tr(self.locale_service, "menu.help"))
        for action_id, action in self._actions.items():
            action.setText(tr(self.locale_service, f"action.{action_id}"))
        self.sidebar_title.setText(tr(self.locale_service, "panel.workspace"))
        self.sample_title.setText(tr(self.locale_service, "panel.samples"))
        self.review_title.setText(tr(self.locale_service, "canvas.review"))
        self.label_title.setText(tr(self.locale_service, "canvas.label"))
        self.welcome_title.setText(tr(self.locale_service, "welcome.title"))
        self.welcome_description.setText(tr(self.locale_service, "welcome.description"))
        self.welcome_create.setText(tr(self.locale_service, "welcome.create"))
        self.welcome_open.setText(tr(self.locale_service, "welcome.open"))
        self.brand_button.setToolTip(tr(self.locale_service, "tooltip.brand"))
        self._set_brand_pixmaps()
        self.refresh_tree()
        self.refresh_context()

    def create_workspace(self) -> None:
        """选择目录创建新工作区，已有工作区不会被无提示覆盖。"""

        chosen = QFileDialog.getExistingDirectory(
            self, tr(self.locale_service, "dialog.workspace.title")
        )
        if not chosen:
            return
        root = Path(chosen)
        try:
            self.workspace = self.workspace_service.create_workspace(root)
            write_json_atomic(root / "settings.json", self.settings)
        except FileExistsError:
            QMessageBox.warning(
                self,
                tr(self.locale_service, "dialog.error"),
                tr(self.locale_service, "dialog.workspace.exists"),
            )
            return
        except OSError as error:
            self.show_error(error)
            return
        self.workspace_root = root
        self.current_project = None
        self.current_dataset = None
        self.current_sample = None
        self._browser_context = None
        self.refresh_tree()
        self.refresh_context()

    def open_workspace_dialog(self) -> None:
        """打开已有工作区并恢复最近项目与全局偏好。"""

        chosen = QFileDialog.getExistingDirectory(
            self, tr(self.locale_service, "action.open_workspace")
        )
        if not chosen:
            return
        root = Path(chosen)
        try:
            workspace = self.workspace_service.open_workspace(root)
            settings_path = root / "settings.json"
            self.settings = (
                read_json_model(settings_path, AppSettings)
                if settings_path.is_file()
                else AppSettings()
            )
            self.locale_service.set_locale(self.settings.ui_locale)
            self.workspace = workspace
            self.workspace_root = root
            self.current_project = None
            self.current_dataset = None
            self.current_sample = None
            self._browser_context = None
            if workspace.recent_project_id:
                self.current_project = self.workspace_service.open_project(
                    root, workspace.recent_project_id
                )
                if self.current_project.datasets:
                    self.current_dataset = self.current_project.datasets[0]
        except (OSError, ValueError, KeyError) as error:
            self.show_error(error)
            return
        ShortcutService().apply(self._actions, self.settings.shortcut_overrides)
        self.refresh_tree()
        self.refresh_context()

    def create_project(self) -> None:
        """在已打开的工作区中创建隔离的项目根目录、模型库和数据集索引。"""

        if not self.require_workspace():
            return
        name, accepted = QInputDialog.getText(
            self,
            tr(self.locale_service, "dialog.project.title"),
            tr(self.locale_service, "dialog.project.name"),
        )
        if not accepted:
            return
        if not name.strip():
            self.show_required()
            return
        template = None
        if self.current_project is not None:
            copy_template = QMessageBox.question(
                self,
                tr(self.locale_service, "dialog.project.title"),
                tr(self.locale_service, "dialog.project.copy_template"),
            )
            if copy_template == QMessageBox.StandardButton.Yes:
                template = self.current_project
        try:
            self.current_project = self.workspace_service.create_project(
                self.workspace_root,
                self.workspace,
                name.strip(),
                template=template,
            )
        except OSError as error:
            self.show_error(error)
            return
        self.current_dataset = None
        self.current_sample = None
        self._browser_context = None
        self.refresh_tree()
        self.refresh_context()

    def create_dataset(self) -> None:
        """在当前项目中新建受管数据集池，项目标签集仍由同一项目共享。"""

        if self.current_project is None or self.workspace_root is None:
            QMessageBox.warning(
                self,
                tr(self.locale_service, "dialog.error"),
                tr(self.locale_service, "panel.no_project"),
            )
            return
        name, accepted = QInputDialog.getText(
            self,
            tr(self.locale_service, "dialog.dataset.title"),
            tr(self.locale_service, "dialog.dataset.name"),
        )
        if not accepted:
            return
        if not name.strip():
            self.show_required()
            return
        template = None
        if self.current_dataset is not None:
            copy_template = QMessageBox.question(
                self,
                tr(self.locale_service, "dialog.dataset.title"),
                tr(self.locale_service, "dialog.dataset.copy_template"),
            )
            if copy_template == QMessageBox.StandardButton.Yes:
                template = self.current_dataset
        try:
            self.current_dataset = self.workspace_service.create_dataset(
                self.workspace_root,
                self.current_project,
                name.strip(),
                template=template,
            )
        except OSError as error:
            self.show_error(error)
            return
        self.current_sample = None
        self._browser_context = None
        self.refresh_tree()
        self.refresh_context()

    def import_images(self) -> None:
        """导入常见图片格式并转为受管 PNG；重复图片必须由用户逐张确认。"""

        if not self.require_dataset():
            return
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            tr(self.locale_service, "dialog.import.title"),
            "",
            tr(self.locale_service, "dialog.import.filter"),
        )
        if not paths:
            return
        report = self.pool_service.import_images(
            self.workspace_root,
            self.current_project,
            self.current_dataset,
            (Path(path) for path in paths),
            keep_duplicate=self.confirm_duplicate,
        )
        self.sample_browser.refresh()
        if report.imported_sample_ids:
            self.sample_browser.select_sample_id(report.imported_sample_ids[0])
        QMessageBox.information(
            self,
            tr(self.locale_service, "dialog.import.title"),
            tr(self.locale_service, "dialog.import.summary").format(
                imported=len(report.imported_sample_ids),
                skipped=len(report.skipped_paths),
                failed=len(report.failures),
            ),
        )

    def confirm_duplicate(self, candidate: DuplicateCandidate) -> bool:
        """在池服务实际写入前显示并排重复图对比，返回用户明确决定。"""

        dialog = DuplicateDialog(self.locale_service, candidate, self)
        dialog.exec()
        return dialog.keep

    def import_xany(self) -> None:
        """递归导入 X-AnyLabeling/LabelMe 图像与同名 JSON，源目录保持只读。"""

        if not self.require_dataset():
            return
        source = QFileDialog.getExistingDirectory(
            self,
            tr(self.locale_service, "dialog.xany.import_title"),
        )
        if not source:
            return
        report = XAnyLabelingInteropService(self.pool_service).import_directory(
            self.workspace_root,
            self.current_project,
            self.current_dataset,
            Path(source),
            keep_duplicate=self.confirm_duplicate,
        )
        self.sample_browser.refresh()
        self.on_labels_changed()
        QMessageBox.information(
            self,
            tr(self.locale_service, "dialog.xany.import_title"),
            tr(self.locale_service, "dialog.xany.import_complete").format(
                imported=len(report.imported_sample_ids),
                missing=len(report.missing_json_images),
                invalid=len(report.invalid_json),
            ),
        )

    def open_label_manager(self) -> None:
        """打开项目级标签管理器；保存后的标签刷新画布和浏览器筛选。"""

        if self.current_project is None or self.workspace_root is None:
            return
        dialog = LabelManagerDialog(
            self.locale_service,
            self.workspace_root,
            self.current_project,
            self,
        )
        dialog.labels_changed.connect(self.on_labels_changed)
        dialog.sample_inspection_requested.connect(self.open_inspected_label_sample)
        dialog.exec()

    def on_labels_changed(self) -> None:
        """标签元数据变化后刷新选择器、预览和当前画布颜色。"""

        self.refresh_label_panel()
        self.sample_browser.retranslate_ui()
        if self.current_sample is not None:
            self.load_sample(self.current_sample)

    def open_inspected_label_sample(
        self,
        dataset_id: str,
        sample_id: str,
        label_id: str,
    ) -> None:
        """从跨数据集标签检查集合切换回原始样本，并高亮第一个对应矩形框。"""

        if self.current_project is None or self.workspace_root is None:
            return
        target_dataset = next(
            (dataset for dataset in self.current_project.datasets if dataset.id == dataset_id),
            None,
        )
        if target_dataset is None:
            return
        sample = self.index_repository().get_sample(sample_id)
        if sample is None or sample.dataset_id != target_dataset.id:
            return
        self.current_dataset = target_dataset
        self.current_sample = None
        self._browser_context = None
        self.refresh_tree()
        self.refresh_context()
        self.load_sample(sample)
        self.canvas.focus_first_label_shape(label_id)

    def open_similarity_review(self) -> None:
        """让用户确认近似图片组；只有确认组会在导出时被强制绑定。"""

        if not self.require_dataset():
            return
        SimilarityDialog(self.locale_service, self.index_repository(), self).exec()

    def open_model_manager(self) -> None:
        """打开当前项目隔离的模型库，模型不会被写入项目备份。"""

        if self.current_project is None or self.workspace_root is None:
            return
        dialog = ModelManagerDialog(
            self.locale_service,
            self.workspace_root,
            self.current_project,
            self,
        )
        dialog.auto_annotation_requested.connect(self.run_auto_annotation)
        dialog.exec()

    def run_auto_annotation(self, entry: object, scope: str) -> None:
        """按当前、全部或未标注范围运行预标注，人工框不会被清空或覆盖。"""

        if not self.require_dataset() or not hasattr(entry, "label_mapping"):
            return
        if not entry.label_mapping:
            QMessageBox.warning(
                self,
                tr(self.locale_service, "dialog.error"),
                tr(self.locale_service, "dialog.models.no_mapping"),
            )
            return
        selection = InferenceBackendSelector().select()
        if not selection.using_gpu:
            choice = QMessageBox(self)
            choice.setWindowTitle(tr(self.locale_service, "dialog.models.cpu.title"))
            choice.setText(tr(self.locale_service, "dialog.models.cpu.body"))
            continue_button = choice.addButton(
                tr(self.locale_service, "dialog.models.cpu.continue"),
                QMessageBox.ButtonRole.AcceptRole,
            )
            guide_button = choice.addButton(
                tr(self.locale_service, "dialog.models.cpu.guide"),
                QMessageBox.ButtonRole.ActionRole,
            )
            choice.addButton(QMessageBox.StandardButton.Cancel)
            choice.exec()
            if choice.clickedButton() is guide_button:
                QMessageBox.information(
                    self,
                    tr(self.locale_service, "dialog.models.cpu.guide"),
                    tr(self.locale_service, "dialog.models.cpu.guide_body"),
                )
                return
            if choice.clickedButton() is not continue_button:
                return
        if scope == "current":
            samples = [self.current_sample] if self.current_sample is not None else []
        elif scope == "unannotated":
            samples = self.index_repository().list_samples(
                self.current_dataset.id,
                limit=100_000,
                annotated=False,
            )
        else:
            samples = self.index_repository().list_samples(self.current_dataset.id, limit=100_000)
        if not samples:
            return
        service = AutoAnnotationService(self.pool_service)
        processed = 0
        try:
            for sample in samples:
                service.annotate_sample(self.workspace_root, self.current_project, sample, entry)
                processed += 1
        except (OSError, ValueError, RuntimeError, KeyError) as error:
            self.show_error(error)
            return
        self.sample_browser.refresh()
        if self.current_sample is not None:
            refreshed = self.index_repository().get_sample(self.current_sample.id)
            if refreshed is not None:
                self.load_sample(refreshed)
        self.statusBar().showMessage(
            tr(self.locale_service, "dialog.models.complete").format(count=processed)
        )

    def export_dataset(self) -> None:
        """按当次向导设置生成 YOLO 目录，不保存方案、路径或导出记录。"""

        if not self.require_dataset():
            return
        if not self.current_project.label_set.labels:
            QMessageBox.warning(
                self,
                tr(self.locale_service, "dialog.error"),
                tr(self.locale_service, "panel.no_labels"),
            )
            return
        dialog = ExportDialog(self.locale_service, self.settings, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        request = dialog.build_request(self.current_dataset.id)
        index = self.index_repository()
        samples = index.list_samples(
            self.current_dataset.id,
            limit=100_000,
            annotated=None if request.include_unannotated else True,
        )
        if not samples:
            QMessageBox.warning(
                self,
                tr(self.locale_service, "dialog.error"),
                tr(self.locale_service, "dialog.export.no_annotated"),
            )
            return
        try:
            split_result = SplitPlanner().plan(
                samples,
                request.split,
                request.seed,
                index.confirmed_similarity_mapping(),
            )
            output = YoloDetectionExporter().export(
                request, self.current_project.label_set, split_result
            )
        except (OSError, ValueError, KeyError, RuntimeError) as error:
            self.show_error(error)
            return
        QMessageBox.information(
            self,
            tr(self.locale_service, "dialog.export.title"),
            f"{tr(self.locale_service, 'dialog.export.complete')}\n{output}",
        )

    def export_backup(self) -> None:
        """按项目生成已校验压缩备份，模型二进制按产品约定不会被打包。"""

        if self.workspace_root is None or self.current_project is None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self,
            tr(self.locale_service, "dialog.backup.export_title"),
            f"{self.current_project.name}.ddbackup",
            tr(self.locale_service, "dialog.backup.filter"),
        )
        if not path:
            return
        output = Path(path)
        if output.suffix.lower() != ".ddbackup":
            output = output.with_suffix(".ddbackup")
        try:
            ProjectBackupService().export_backup(self.workspace_root, self.current_project, output)
        except (OSError, ValueError) as error:
            self.show_error(error)
            return
        QMessageBox.information(
            self,
            tr(self.locale_service, "dialog.backup.export_title"),
            f"{tr(self.locale_service, 'dialog.backup.export_complete')}\n{output}",
        )

    def export_xany(self) -> None:
        """从受管池复制 PNG 与 LabelMe JSON，供 X-AnyLabeling 直接打开和继续编辑。"""

        if not self.require_dataset():
            return
        parent = QFileDialog.getExistingDirectory(
            self,
            tr(self.locale_service, "dialog.xany.export_title"),
        )
        if not parent:
            return
        name, accepted = QInputDialog.getText(
            self,
            tr(self.locale_service, "dialog.xany.export_title"),
            tr(self.locale_service, "dialog.xany.folder_name"),
            text=f"{self.current_dataset.name}-xanylabeling",
        )
        if not accepted or not name.strip():
            return
        samples = self.index_repository().list_samples(self.current_dataset.id, limit=100_000)
        try:
            output = XAnyLabelingExporter().export(
                Path(parent) / name.strip(),
                samples,
                self.current_project.label_set,
            )
        except (OSError, ValueError, KeyError) as error:
            self.show_error(error)
            return
        QMessageBox.information(
            self,
            tr(self.locale_service, "dialog.xany.export_title"),
            f"{tr(self.locale_service, 'dialog.xany.export_complete')}\n{output}",
        )

    def import_backup(self) -> None:
        """验证备份完整性后导入为新项目，失败时不把半成品登记到工作区。"""

        if not self.require_workspace():
            return
        path, _ = QFileDialog.getOpenFileName(
            self,
            tr(self.locale_service, "dialog.backup.import_title"),
            "",
            tr(self.locale_service, "dialog.backup.filter"),
        )
        if not path:
            return
        try:
            self.current_project = ProjectBackupService().import_backup(
                self.workspace_root,
                self.workspace,
                Path(path),
            )
        except (OSError, ValueError, KeyError) as error:
            self.show_error(error)
            return
        self.current_dataset = (
            self.current_project.datasets[0] if self.current_project.datasets else None
        )
        self.current_sample = None
        self._browser_context = None
        self.refresh_tree()
        self.refresh_context()
        QMessageBox.information(
            self,
            tr(self.locale_service, "dialog.backup.import_title"),
            tr(self.locale_service, "dialog.backup.import_complete"),
        )

    def delete_current_sample(self) -> None:
        """根据少量样本阈值选择回收站或永久删除，并始终在操作前二次确认。"""

        if not self.require_dataset() or self.current_sample is None:
            return
        move_to_trash = (
            self.index_repository().count_samples(self.current_dataset.id)
            <= self.settings.trash_sample_threshold
        )
        message_key = "dialog.delete.trash" if move_to_trash else "dialog.delete.permanent"
        confirmed = QMessageBox.question(
            self,
            tr(self.locale_service, "dialog.delete.title"),
            tr(self.locale_service, message_key),
        )
        if confirmed != QMessageBox.StandardButton.Yes:
            return
        try:
            self.pool_service.delete_sample(
                self.workspace_root,
                self.current_project,
                self.current_sample,
                move_to_trash=move_to_trash,
            )
        except OSError as error:
            self.show_error(error)
            return
        self.current_sample = None
        self.canvas.clear_document()
        self.sample_browser.select_sample_id(None)
        self.sample_browser.refresh()

    def open_trash(self) -> None:
        """打开当前项目回收站，恢复后刷新数据集池索引与缩略图列表。"""

        if self.workspace_root is None or self.current_project is None:
            return
        dialog = TrashDialog(
            self.locale_service,
            self.workspace_root,
            self.current_project,
            self.pool_service,
            self,
        )
        dialog.samples_restored.connect(self.after_trash_restored)
        dialog.exec()

    def after_trash_restored(self) -> None:
        """恢复操作不自动切换数据集，只刷新当前可见数据集的样本列表。"""

        if self.current_dataset is not None:
            self.sample_browser.refresh()

    def select_adjacent_sample(self, direction: int) -> None:
        """上一张/下一张在当前分页内快速切换，到边界时自动翻页。"""

        count = self.sample_browser.list_widget.count()
        current_row = self.sample_browser.list_widget.currentRow()
        target_row = current_row + direction
        if 0 <= target_row < count:
            self.sample_browser.list_widget.setCurrentRow(target_row)
            return
        if direction > 0 and self.sample_browser.next_button.isEnabled():
            self.sample_browser.next_page()
            if self.sample_browser.list_widget.count():
                self.sample_browser.list_widget.setCurrentRow(0)
        elif direction < 0 and self.sample_browser.previous_button.isEnabled():
            self.sample_browser.previous_page()
            if self.sample_browser.list_widget.count():
                self.sample_browser.list_widget.setCurrentRow(
                    self.sample_browser.list_widget.count() - 1
                )

    def load_sample(self, sample: DatasetSample) -> None:
        """加载当前样本的受管 LabelMe JSON；损坏文件只报错，绝不自动覆盖。"""

        if self.current_project is None:
            return
        try:
            document = self.pool_service.load_document(sample, self.current_project)
            self.canvas.set_document(sample.image_path, document, self.current_project.label_set)
        except (OSError, ValueError, KeyError) as error:
            self.show_error(error)
            return
        self.current_sample = sample
        self.review_combo.blockSignals(True)
        self._set_review_combo(sample.review_status)
        self.review_combo.blockSignals(False)
        self.refresh_label_panel()

    def auto_save_annotation(self) -> None:
        """每次有效编辑都通过数据集池原子保存，失败时显示明确的可恢复错误。"""

        if (
            self.workspace_root is None
            or self.current_project is None
            or self.current_sample is None
            or self.canvas.document is None
        ):
            return
        try:
            self.pool_service.save_document(
                self.workspace_root,
                self.current_project,
                self.current_sample,
                self.canvas.document,
            )
        except (OSError, ValueError, KeyError) as error:
            self.statusBar().showMessage(tr(self.locale_service, "canvas.save_failed"))
            self.show_error(error)
            return
        self.statusBar().showMessage(tr(self.locale_service, "canvas.autosaved"))
        self.sample_browser.refresh()

    def choose_rectangle_label(self, shape_id: str) -> None:
        """双击矩形后可通过别名、训练名和描述选择新标签。"""

        if self.current_project is None:
            return
        labels = [
            label for label in self.current_project.label_set.labels if label.status == "active"
        ]
        if not labels:
            self.statusBar().showMessage(tr(self.locale_service, "canvas.no_label"))
            return
        choices = [f"{label.alias} · {label.name} — {label.description}" for label in labels]
        choice, accepted = QInputDialog.getItem(
            self,
            tr(self.locale_service, "dialog.choose_label"),
            tr(self.locale_service, "dialog.choose_label.prompt"),
            choices,
            0,
            False,
        )
        if accepted:
            self.canvas.reassign_label(shape_id, labels[choices.index(choice)].id)

    def update_review_status(self) -> None:
        """复核状态按整张图片保存到 SQLite，不影响矩形标注内容。"""

        if self.current_sample is None or self.current_project is None:
            return
        status = self.review_combo.currentData()
        if not isinstance(status, ReviewStatus):
            return
        self.current_sample.review_status = status
        self.index_repository().update_review_status(self.current_sample.id, status)
        self.sample_browser.refresh()

    def select_drawing_label(self) -> None:
        """右侧标签项使用稳定 ID 设置当前画框目标，归档标签不参与新建。"""

        items = self.label_list.selectedItems()
        if not items:
            return
        label_id = items[0].data(0, Qt.ItemDataRole.UserRole)
        self.canvas.set_current_label(str(label_id))

    def save_dataset_preference(self, _: bool) -> None:
        """预览开关是数据集级展示偏好，随项目 JSON 原子保存。"""

        if self.workspace_root is not None and self.current_project is not None:
            self.workspace_service.save_project(self.workspace_root, self.current_project)

    def refresh_tree(self) -> None:
        """仅依据工作区登记和项目元数据重建导航树，不扫描数据集池文件。"""

        self.project_tree.blockSignals(True)
        self.project_tree.clear()
        if self.workspace is None or self.workspace_root is None:
            self.project_tree.blockSignals(False)
            self.sample_placeholder.setText(tr(self.locale_service, "panel.no_workspace"))
            return
        selected_item: QTreeWidgetItem | None = None
        for project_ref in self.workspace.projects:
            project_item = QTreeWidgetItem([project_ref.name])
            project_item.setData(0, Qt.ItemDataRole.UserRole, ("project", project_ref.id))
            self.project_tree.addTopLevelItem(project_item)
            project = self.workspace_service.open_project(self.workspace_root, project_ref.id)
            for dataset in project.datasets:
                dataset_item = QTreeWidgetItem([dataset.name])
                dataset_item.setData(
                    0, Qt.ItemDataRole.UserRole, ("dataset", project.id, dataset.id)
                )
                project_item.addChild(dataset_item)
                if self.current_dataset is not None and dataset.id == self.current_dataset.id:
                    selected_item = dataset_item
            if (
                self.current_project is not None
                and project.id == self.current_project.id
                and selected_item is None
            ):
                selected_item = project_item
            project_item.setExpanded(True)
        if selected_item is not None:
            self.project_tree.setCurrentItem(selected_item)
        self.project_tree.blockSignals(False)

    def select_tree_item(self) -> None:
        """切换时重新打开目标项目，保证项目间标签集和索引严格隔离。"""

        items = self.project_tree.selectedItems()
        if not items or self.workspace_root is None:
            return
        data = items[0].data(0, Qt.ItemDataRole.UserRole)
        if not data:
            return
        try:
            self.current_project = self.workspace_service.open_project(self.workspace_root, data[1])
            if data[0] == "project":
                self.current_dataset = None
            else:
                self.current_dataset = next(
                    dataset for dataset in self.current_project.datasets if dataset.id == data[2]
                )
        except (OSError, ValueError, KeyError) as error:
            self.show_error(error)
            return
        self.current_sample = None
        self._browser_context = None
        self.refresh_context()

    def rename_dataset_samples(self) -> None:
        """以一次明确确认执行当前数据集全量重命名，并在元数据写入失败时回滚规则。"""

        if not self.require_dataset():
            return
        samples = self.index_repository().list_samples(
            self.current_dataset.id,
            limit=100_000,
        )
        if not samples:
            return
        dialog = RenameSamplesDialog(
            self.locale_service,
            self.current_dataset.naming_policy,
            samples,
            self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        confirmed = QMessageBox.question(
            self,
            tr(self.locale_service, "dialog.rename.title"),
            tr(self.locale_service, "dialog.rename.confirm").format(count=len(samples)),
        )
        if confirmed != QMessageBox.StandardButton.Yes:
            return
        original_policy = self.current_dataset.naming_policy.model_copy(deep=True)
        self.current_dataset.naming_policy = dialog.build_policy()
        try:
            self.pool_service.rename_samples(
                self.workspace_root,
                self.current_project,
                self.current_dataset,
                [sample.id for sample in samples],
            )
            self.workspace_service.save_project(self.workspace_root, self.current_project)
        except (OSError, ValueError, KeyError) as error:
            self.current_dataset.naming_policy = original_policy
            self.show_error(error)
            return
        self.sample_browser.refresh()
        self.refresh_context()
        QMessageBox.information(
            self,
            tr(self.locale_service, "dialog.rename.title"),
            tr(self.locale_service, "dialog.rename.complete").format(count=len(samples)),
        )

    def check_current_annotation_quality(self) -> None:
        """仅检查当前保存后的结构质量；结果不会擅自改变用户设置的图片复核状态。"""

        if (
            self.current_sample is None
            or self.current_project is None
            or self.canvas.document is None
        ):
            return
        issues = AnnotationQualityService().inspect(
            self.current_sample,
            self.canvas.document,
            self.current_project.label_set,
        )
        if not issues:
            QMessageBox.information(
                self,
                tr(self.locale_service, "dialog.quality.title"),
                tr(self.locale_service, "dialog.quality.none"),
            )
            return
        descriptions = [f"• {tr(self.locale_service, f'quality.{issue.code}')}" for issue in issues]
        QMessageBox.warning(
            self,
            tr(self.locale_service, "dialog.quality.title"),
            "\n".join(descriptions),
        )

    def refresh_context(self) -> None:
        """根据当前项目/数据集切换欢迎页或真实浏览与标注工作区。"""

        if self.current_project is None:
            self.labels_placeholder.setText(tr(self.locale_service, "panel.no_project"))
            self.review_combo.clear()
            self.label_list.clear()
            self.sample_browser.clear_context()
            self.canvas.clear_document()
            self.pages.setCurrentWidget(self.welcome_page)
            self.statusBar().showMessage(tr(self.locale_service, "status.ready"))
            return
        self.refresh_label_panel()
        if self.current_dataset is None or self.workspace_root is None:
            self.sample_placeholder.setText(tr(self.locale_service, "status.no_dataset"))
            self.sample_browser.clear_context()
            self.canvas.clear_document()
            self.pages.setCurrentWidget(self.welcome_page)
        else:
            self.sample_placeholder.setText(self.current_dataset.name)
            context = (self.current_project.id, self.current_dataset.id)
            if context != self._browser_context:
                self.sample_browser.set_context(
                    self.workspace_root, self.current_project, self.current_dataset
                )
                self._browser_context = context
            else:
                self.sample_browser.refresh()
            self.pages.setCurrentWidget(self.canvas_page)
        self.statusBar().showMessage(tr(self.locale_service, "status.ready"))

    def refresh_label_panel(self) -> None:
        """用别名、英文名和描述展示标签，降低大量英文类别的理解负担。"""

        self.label_list.blockSignals(True)
        current_id = self.canvas.current_label_id
        self.label_list.clear()
        self.review_combo.blockSignals(True)
        self.review_combo.clear()
        for status in ReviewStatus:
            self.review_combo.addItem(tr(self.locale_service, f"review.{status.value}"), status)
        self.review_combo.setEnabled(self.current_sample is not None)
        if self.current_sample is not None:
            self._set_review_combo(self.current_sample.review_status)
        self.review_combo.blockSignals(False)
        if self.current_project is None:
            self.labels_placeholder.setText(tr(self.locale_service, "panel.no_project"))
            self.label_list.blockSignals(False)
            return
        active_items: list[QTreeWidgetItem] = []
        for label in sorted(self.current_project.label_set.labels, key=lambda item: item.class_id):
            item = QTreeWidgetItem([f"{label.alias} · {label.name}"])
            item.setData(0, Qt.ItemDataRole.UserRole, label.id)
            item.setToolTip(0, label.description)
            item.setForeground(0, QColor(label.color))
            if label.status != "active":
                item.setDisabled(True)
            else:
                active_items.append(item)
            self.label_list.addTopLevelItem(item)
            if label.id == current_id:
                self.label_list.setCurrentItem(item)
        if current_id is None and active_items:
            self.label_list.setCurrentItem(active_items[0])
            self.canvas.set_current_label(str(active_items[0].data(0, Qt.ItemDataRole.UserRole)))
        self.labels_placeholder.setText(
            ""
            if self.current_project.label_set.labels
            else tr(self.locale_service, "panel.no_labels")
        )
        self.label_list.blockSignals(False)

    def _set_review_combo(self, status: ReviewStatus) -> None:
        """按枚举值恢复复核组合框，语言切换时不依赖已变化的显示文字。"""

        for index in range(self.review_combo.count()):
            if self.review_combo.itemData(index) == status:
                self.review_combo.setCurrentIndex(index)
                return

    def toggle_sidebar(self) -> None:
        """折叠时显示橙蓝 DD 标记，展开后恢复完整项目字标。"""

        self.is_sidebar_collapsed = not self.is_sidebar_collapsed
        self.sidebar_title.setVisible(not self.is_sidebar_collapsed)
        self.project_tree.setVisible(not self.is_sidebar_collapsed)
        self.sample_title.setVisible(not self.is_sidebar_collapsed)
        self.sample_placeholder.setVisible(not self.is_sidebar_collapsed)
        if self.is_sidebar_collapsed:
            self.brand_label.clear()
            self.brand_label.setText(
                '<span style="color:#E8B58A">D</span><span style="color:#A9C8DF">D</span>'
            )
            self.sidebar.setMaximumWidth(80)
        else:
            self.sidebar.setMaximumWidth(16777215)
            self._set_brand_pixmaps()

    def show_welcome(self) -> None:
        """点击品牌区仅返回欢迎概览，不会修改当前工作区或样本。"""

        self.pages.setCurrentWidget(self.welcome_page)

    def open_settings(self) -> None:
        """保存全局偏好到工作区根目录，项目备份不会携带这些本机设置。"""

        dialog = SettingsDialog(self.locale_service, self.settings, self._actions, self)
        dialog.exec()
        self.settings.ui_locale = self.locale_service.locale
        if self.workspace_root:
            write_json_atomic(self.workspace_root / "settings.json", self.settings)

    def open_about(self) -> None:
        """关于页复用完整 Logo 并说明应用定位。"""

        dialog = QDialog(self)
        dialog.setWindowTitle(tr(self.locale_service, "about.title"))
        layout = QVBoxLayout(dialog)
        logo = QLabel()
        pixmap = QPixmap(str(resource_root() / "assets" / "brand" / "datumdock-wordmark-v3.png"))
        logo.setPixmap(pixmap.scaledToWidth(340, Qt.TransformationMode.SmoothTransformation))
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        body = QLabel(tr(self.locale_service, "about.body"))
        body.setWordWrap(True)
        body.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(logo)
        layout.addWidget(body)
        dialog.exec()

    def require_workspace(self) -> bool:
        """拦截没有工作区时的创建操作，避免把项目隐式写到未知路径。"""

        if self.workspace_root is not None and self.workspace is not None:
            return True
        QMessageBox.warning(
            self,
            tr(self.locale_service, "dialog.error"),
            tr(self.locale_service, "panel.no_workspace"),
        )
        return False

    def require_dataset(self) -> bool:
        """拦截导入、导出、删除等必须具备数据集上下文的操作。"""

        if self.workspace_root and self.current_project and self.current_dataset:
            return True
        QMessageBox.warning(
            self,
            tr(self.locale_service, "dialog.error"),
            tr(self.locale_service, "status.no_dataset"),
        )
        return False

    def index_repository(self) -> ProjectIndexRepository:
        """返回当前项目的唯一 SQLite 索引仓库，调用前必须已通过上下文检查。"""

        if self.workspace_root is None or self.current_project is None:
            raise RuntimeError("当前没有项目索引")
        return ProjectIndexRepository(
            WorkspaceService.project_path(self.workspace_root, self.current_project.id)
            / "project-index.sqlite"
        )

    def show_required(self) -> None:
        """为必填输入提供本地化提示。"""

        QMessageBox.warning(
            self,
            tr(self.locale_service, "dialog.error"),
            tr(self.locale_service, "dialog.required"),
        )

    def show_error(self, error: Exception) -> None:
        """显示底层可诊断错误，避免静默吞掉文件、权限或格式失败。"""

        QMessageBox.critical(self, tr(self.locale_service, "dialog.error"), str(error))
