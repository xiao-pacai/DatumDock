"""DatumDock 四区标注工作台的可交互视觉原型。"""

from __future__ import annotations

from PySide6.QtCore import QModelIndex, QObject, QRunnable, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QIcon,
    QKeySequence,
    QPainter,
    QPen,
    QPixmap,
    QResizeEvent,
    QShortcut,
)
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListView,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from datumdock.domain.models import (
    AnnotationDocument,
    DatasetSample,
    RectangleShape,
    ReviewStatus,
    SampleHealth,
    SampleSort,
)
from datumdock.i18n.catalog import LocaleService, tr
from datumdock.resources import resource_root
from datumdock.services.annotations import (
    AnnotationLoadResult,
    AnnotationSaveRequest,
    AutosaveState,
    review_status_after_edit,
)
from datumdock.ui.components import (
    BrandLockup,
    FilterChip,
    GhostButton,
    PreviewBanner,
    PrimaryButton,
    SearchBox,
    StatusBadge,
    ToolButton,
    brand_asset_path,
)
from datumdock.ui.icons import IconRegistry
from datumdock.ui.managed_sample_model import ManagedSampleDelegate, ManagedSampleListModel
from datumdock.ui.preview_canvas import CanvasTool, PreviewAnnotationCanvas
from datumdock.ui.prototype_models import (
    AnnotationItemViewData,
    ImageItemViewData,
    ImageStatus,
    WorkspaceNavigationTarget,
    WorkspaceSnapshot,
)
from datumdock.ui.theme import THEME


class _ImageLoadSignals(QObject):
    completed = Signal(int, str, object)
    failed = Signal(int, str)


class _ImageLoadJob(QRunnable):
    """原图解码通过网关在后台执行，迟到结果由工作台代号丢弃。"""

    def __init__(self, gateway, dataset_id: str, sample_id: str, generation: int) -> None:
        super().__init__()
        self.gateway = gateway
        self.dataset_id = dataset_id
        self.sample_id = sample_id
        self.generation = generation
        self.signals = _ImageLoadSignals()

    def run(self) -> None:
        try:
            asset = self.gateway.load_image(self.dataset_id, self.sample_id)
            annotation = self.gateway.load_annotation(self.dataset_id, self.sample_id)
            self.signals.completed.emit(
                self.generation,
                self.sample_id,
                (asset, annotation),
            )
        except Exception:
            self.signals.failed.emit(self.generation, self.sample_id)


class AnnotationWorkspace(QWidget):
    """组合顶部、左侧、画布和右侧面板，只消费网关提供的只读快照。"""

    home_requested = Signal()
    route_requested = Signal(str)
    dialog_requested = Signal(str)
    message_requested = Signal(str)

    def __init__(
        self,
        locale: LocaleService,
        preview_mode: bool,
        snapshot: WorkspaceSnapshot,
        gateway=None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.locale = locale
        self.preview_mode = preview_mode
        self.snapshot = snapshot
        self.gateway = gateway
        self.managed_mode = (
            not preview_mode and gateway is not None and hasattr(gateway, "query_samples")
        )
        self.icons = IconRegistry(resource_root())
        self.current_image_id = snapshot.images[0].id if snapshot.images else None
        self.selected_shape_id: str | None = None
        self._compact = False
        self.sample_model: ManagedSampleListModel | None = None
        self.sample_delegate: ManagedSampleDelegate | None = None
        self._image_generation = 0
        self._image_jobs: set[_ImageLoadJob] = set()
        self._managed_sample: DatasetSample | None = None
        self._annotation_load: AnnotationLoadResult | None = None
        self._annotation_document: AnnotationDocument | None = None
        self._annotation_disk_sha256 = ""
        self._save_future = None
        self._reassign_shape_id: str | None = None
        self._pending_navigation_target: WorkspaceNavigationTarget | None = None
        self._build_ui()
        self.save_poll_timer = QTimer(self)
        self.save_poll_timer.setInterval(80)
        self.save_poll_timer.timeout.connect(self._poll_annotation_save)
        self.save_shortcut = QShortcut(QKeySequence.StandardKey.Save, self)
        self.save_shortcut.activated.connect(self._retry_save)
        self.retranslate_ui()
        if self.managed_mode:
            self._initialize_managed_browser()
        else:
            self._load_current_image()

    def _build_ui(self) -> None:
        """建立画布优先的固定四区结构。"""

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_top_bar())
        banner_wrap = QWidget()
        banner_layout = QHBoxLayout(banner_wrap)
        banner_layout.setContentsMargins(12, 8, 12, 0)
        self.banner = PreviewBanner(self.locale, self.preview_mode)
        banner_layout.addWidget(self.banner)
        banner_layout.addStretch()
        banner_wrap.setVisible(self.preview_mode)
        root.addWidget(banner_wrap)

        body = QWidget()
        body_layout = QHBoxLayout(body)
        body_layout.setContentsMargins(10, 8, 10, 8)
        body_layout.setSpacing(8)
        self.canvas = PreviewAnnotationCanvas()
        self.canvas.shape_selected.connect(self._on_canvas_shape_selected)
        self.canvas.shape_double_clicked.connect(self._request_shape_reassignment)
        self.canvas.document_changed.connect(self._on_canvas_changed)
        self.canvas.zoom_changed.connect(self._set_zoom)
        self.tool_rail = self._build_tool_rail()
        body_layout.addWidget(self.tool_rail)
        body_layout.addWidget(self.canvas, 1)
        self.right_panel = self._build_right_panel()
        body_layout.addWidget(self.right_panel)
        root.addWidget(body, 1)
        root.addWidget(self._build_status_bar())

    def _build_top_bar(self) -> QFrame:
        bar = QFrame()
        bar.setObjectName("topBar")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(12, 7, 12, 7)
        layout.setSpacing(8)
        self.home_button = GhostButton()
        self.home_button.setIcon(QIcon(str(brand_asset_path("datumdock-wordmark-v3.png"))))
        self.home_button.setIconSize(QSize(160, 36))
        self.home_button.setFixedWidth(178)
        self.home_button.clicked.connect(self.home_requested)
        layout.addWidget(self.home_button)
        self.dataset_combo = QComboBox()
        self.dataset_combo.setMinimumWidth(210)
        available = self.snapshot.available_datasets or (self.snapshot.dataset,)
        for dataset in available:
            self.dataset_combo.addItem(dataset.name, dataset.id)
        for index in range(self.dataset_combo.count()):
            if self.dataset_combo.itemData(index) == self.snapshot.dataset.id:
                self.dataset_combo.setCurrentIndex(index)
                break
        self.dataset_combo.currentIndexChanged.connect(self._on_dataset_changed)
        layout.addWidget(self.dataset_combo)
        layout.addStretch()
        self.import_button = QPushButton()
        self.import_button.clicked.connect(
            lambda: self.dialog_requested.emit(f"image_import:{self.snapshot.dataset.id}")
        )
        self.export_button = QPushButton()
        self.export_menu = QMenu(self.export_button)
        self.export_button.setMenu(self.export_menu)
        self.labels_button = QPushButton()
        self.labels_button.clicked.connect(lambda: self.route_requested.emit("label_manager"))
        self.models_button = QPushButton()
        self.models_button.clicked.connect(lambda: self.route_requested.emit("model_manager"))
        self.settings_button = GhostButton("⚙")
        self.settings_button.setIcon(self.icons.icon("settings"))
        self.settings_button.setText("")
        self.settings_button.clicked.connect(lambda: self.route_requested.emit("settings"))
        self.more_button = QToolButton()
        self.more_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.more_menu = QMenu(self.more_button)
        self.more_button.setMenu(self.more_menu)
        self.more_button.setIcon(self.icons.icon("more"))
        for button in (
            self.import_button,
            self.export_button,
            self.labels_button,
            self.models_button,
            self.settings_button,
            self.more_button,
        ):
            layout.addWidget(button)
        self.top_layout = layout
        return bar

    def _build_tool_rail(self) -> QFrame:
        rail = QFrame()
        rail.setObjectName("toolRail")
        rail.setFixedWidth(58)
        layout = QVBoxLayout(rail)
        layout.setContentsMargins(7, 8, 7, 8)
        layout.setSpacing(6)
        layout.addWidget(BrandLockup(True))
        self.tool_group = QButtonGroup(self)
        self.tool_group.setExclusive(True)
        self.tool_buttons: dict[str, ToolButton] = {}
        specs = (
            ("select", "select", CanvasTool.SELECT),
            ("rectangle", "rectangle", CanvasTool.RECTANGLE),
            ("ai", "auto_annotate", None),
            ("pan", "pan", CanvasTool.PAN),
            ("zoom_in", "zoom_in", None),
            ("zoom_out", "zoom_out", None),
            ("fit", "fit", None),
            ("undo", "undo", None),
            ("redo", "redo", None),
        )
        for name, icon_name, tool in specs:
            button = ToolButton("", "")
            button.setIcon(self.icons.icon(icon_name))
            button.setIconSize(QSize(22, 22))
            self.tool_buttons[name] = button
            layout.addWidget(button)
            if tool is not None:
                self.tool_group.addButton(button)
                button.clicked.connect(lambda checked, value=tool: self.canvas.set_tool(value))
            elif name == "zoom_in":
                button.setCheckable(False)
                button.clicked.connect(self.canvas.zoom_in)
            elif name == "zoom_out":
                button.setCheckable(False)
                button.clicked.connect(self.canvas.zoom_out)
            elif name == "fit":
                button.setCheckable(False)
                button.clicked.connect(self.canvas.fit_image)
            elif name == "undo":
                button.setCheckable(False)
                button.clicked.connect(self.canvas.undo)
            elif name == "redo":
                button.setCheckable(False)
                button.clicked.connect(self.canvas.redo)
            elif name == "ai":
                button.setCheckable(False)
                button.clicked.connect(lambda: self.dialog_requested.emit("auto_annotation"))
        self.tool_buttons["select"].setChecked(True)
        layout.addStretch()
        return rail

    def _build_right_panel(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("rightPanel")
        panel.setMinimumWidth(300)
        panel.setMaximumWidth(400)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(self._build_annotation_panel())
        splitter.addWidget(self._build_image_panel())
        splitter.setSizes([280, 430])
        layout.addWidget(splitter)
        return panel

    def _panel_heading(self, title: QLabel, toggle: QPushButton) -> QWidget:
        wrapper = QWidget()
        layout = QHBoxLayout(wrapper)
        layout.setContentsMargins(12, 9, 8, 7)
        title.setObjectName("sectionTitle")
        layout.addWidget(title, 1)
        toggle.setProperty("role", "ghost")
        toggle.setFixedWidth(34)
        layout.addWidget(toggle)
        return wrapper

    def _build_annotation_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self.annotation_title = QLabel()
        self.annotation_collapse = QPushButton("⌃")
        layout.addWidget(self._panel_heading(self.annotation_title, self.annotation_collapse))
        self.annotation_list = QListWidget()
        self.annotation_list.setAlternatingRowColors(True)
        self.annotation_list.currentItemChanged.connect(self._on_annotation_selected)
        self.annotation_list.itemDoubleClicked.connect(self._on_annotation_double_clicked)
        layout.addWidget(self.annotation_list, 1)
        self.label_combo = QComboBox()
        self.label_combo.setEditable(True)
        self.label_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.label_combo.setMaxVisibleItems(16)
        self.label_combo.currentIndexChanged.connect(self._on_label_combo_changed)
        layout.addWidget(self.label_combo)
        action_row = QHBoxLayout()
        self.review_combo = QComboBox()
        self.review_combo.currentIndexChanged.connect(self._on_review_status_changed)
        action_row.addWidget(self.review_combo, 1)
        self.delete_annotation_button = GhostButton()
        self.delete_annotation_button.clicked.connect(self.canvas.delete_selected)
        action_row.addWidget(self.delete_annotation_button)
        layout.addLayout(action_row)
        self.annotation_collapse.clicked.connect(
            lambda: self.annotation_list.setVisible(not self.annotation_list.isVisible())
        )
        return panel

    def _build_image_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(8, 0, 8, 8)
        layout.setSpacing(7)
        self.image_title = QLabel()
        self.image_collapse = QPushButton("⌃")
        layout.addWidget(self._panel_heading(self.image_title, self.image_collapse))
        self.image_search = SearchBox()
        self.image_search.textChanged.connect(self._on_image_filter_changed)
        layout.addWidget(self.image_search)
        filter_row = QHBoxLayout()
        self.status_combo = QComboBox()
        self.status_combo.currentIndexChanged.connect(self._on_image_filter_changed)
        filter_row.addWidget(self.status_combo, 1)
        self.sort_combo = QComboBox()
        self.sort_combo.currentIndexChanged.connect(self._on_image_filter_changed)
        self.sort_combo.setVisible(self.managed_mode)
        filter_row.addWidget(self.sort_combo, 1)
        self.list_toggle = FilterChip("")
        self.grid_toggle = FilterChip("")
        self.list_toggle.setChecked(True)
        self.list_toggle.clicked.connect(lambda: self._set_image_mode(False))
        self.grid_toggle.clicked.connect(lambda: self._set_image_mode(True))
        filter_row.addWidget(self.list_toggle)
        filter_row.addWidget(self.grid_toggle)
        layout.addLayout(filter_row)
        self.label_filter_combo = QComboBox()
        self.label_filter_combo.currentIndexChanged.connect(self._on_image_filter_changed)
        layout.addWidget(self.label_filter_combo)
        if self.managed_mode:
            self.image_list = QListView()
            self.image_list.setSpacing(4)
            self.image_list.setUniformItemSizes(True)
        else:
            self.image_list = QListWidget()
            self.image_list.setSpacing(4)
            self.image_list.currentItemChanged.connect(self._on_image_selected)
        self.image_stack = QStackedWidget()
        self.image_stack.addWidget(self.image_list)
        empty = QWidget()
        empty_layout = QVBoxLayout(empty)
        empty_layout.setContentsMargins(16, 18, 16, 18)
        empty_layout.addStretch()
        self.image_empty_title = QLabel()
        self.image_empty_title.setObjectName("sectionTitle")
        self.image_empty_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_empty_body = QLabel()
        self.image_empty_body.setObjectName("mutedText")
        self.image_empty_body.setWordWrap(True)
        self.image_empty_body.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_empty_import = PrimaryButton()
        self.image_empty_import.clicked.connect(
            lambda: self.dialog_requested.emit(f"image_import:{self.snapshot.dataset.id}")
        )
        self.image_empty_labels = GhostButton()
        self.image_empty_labels.clicked.connect(lambda: self.route_requested.emit("label_manager"))
        empty_layout.addWidget(self.image_empty_title)
        empty_layout.addWidget(self.image_empty_body)
        empty_layout.addWidget(self.image_empty_import)
        empty_layout.addWidget(self.image_empty_labels)
        empty_layout.addStretch()
        self.image_stack.addWidget(empty)
        layout.addWidget(self.image_stack, 1)
        self.pagination = QWidget()
        pagination_layout = QHBoxLayout(self.pagination)
        pagination_layout.setContentsMargins(0, 0, 0, 0)
        self.previous_page_button = GhostButton("‹")
        self.next_page_button = GhostButton("›")
        self.page_label = QLabel()
        self.page_label.setObjectName("mutedText")
        self.page_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pagination_layout.addWidget(self.previous_page_button)
        pagination_layout.addWidget(self.page_label, 1)
        pagination_layout.addWidget(self.next_page_button)
        self.pagination.setVisible(self.managed_mode)
        layout.addWidget(self.pagination)
        self.preview_toggle = FilterChip("")
        self.preview_toggle.setChecked(True)
        layout.addWidget(self.preview_toggle)
        self.image_collapse.clicked.connect(
            lambda: self.image_stack.setVisible(not self.image_stack.isVisible())
        )
        return panel

    def _build_status_bar(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("statusBarSurface")
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(12, 5, 12, 5)
        self.index_label = QLabel()
        self.resolution_label = QLabel()
        self.zoom_label = QLabel()
        self.save_label = QLabel()
        self.save_label.setStyleSheet(f"color:{THEME.tokens.success}; font-weight:600;")
        layout.addWidget(self.index_label)
        layout.addWidget(self.resolution_label)
        layout.addWidget(self.zoom_label)
        layout.addStretch()
        layout.addWidget(self.save_label)
        return frame

    def retranslate_ui(self) -> None:
        """即时刷新系统文案，演示文件名和标签内容保持原样。"""

        self.banner.retranslate_ui()
        self.home_button.setToolTip(tr(self.locale, "workspace.back_home"))
        self.dataset_combo.setToolTip(tr(self.locale, "workspace.switch"))
        self.import_button.setText(tr(self.locale, "workspace.import"))
        self.export_button.setText(tr(self.locale, "workspace.export"))
        self.labels_button.setText(tr(self.locale, "workspace.labels"))
        self.models_button.setText(tr(self.locale, "workspace.models"))
        self.settings_button.setToolTip(tr(self.locale, "nav.settings"))
        self.more_button.setText(tr(self.locale, "nav.more"))
        self.annotation_title.setText(tr(self.locale, "workspace.annotations"))
        self.image_title.setText(tr(self.locale, "workspace.images"))
        self.image_search.setPlaceholderText(tr(self.locale, "workspace.search_images"))
        self.list_toggle.setText(tr(self.locale, "workspace.list"))
        self.list_toggle.setIcon(self.icons.icon("list"))
        self.grid_toggle.setText(tr(self.locale, "workspace.grid"))
        self.grid_toggle.setIcon(self.icons.icon("grid"))
        self.preview_toggle.setText(tr(self.locale, "workspace.preview_boxes"))
        self.image_empty_title.setText(tr(self.locale, "workspace.empty_images_title"))
        self.image_empty_body.setText(tr(self.locale, "workspace.empty_images_body"))
        self.image_empty_import.setText(tr(self.locale, "workspace.import"))
        self.image_empty_labels.setText(tr(self.locale, "workspace.labels"))
        self.previous_page_button.setToolTip(tr(self.locale, "browser.previous_page"))
        self.next_page_button.setToolTip(tr(self.locale, "browser.next_page"))
        self.canvas.set_empty_message(
            tr(self.locale, "workspace.empty_canvas_title"),
            tr(self.locale, "workspace.empty_canvas_body"),
        )
        current_status = self.status_combo.currentData()
        self.status_combo.clear()
        self.status_combo.addItem(tr(self.locale, "workspace.all_status"), None)
        statuses = (
            (
                ReviewStatus.UNREVIEWED,
                ReviewStatus.PENDING_REVIEW,
                ReviewStatus.COMPLETED,
                ReviewStatus.COMPLETED_NEGATIVE,
                ReviewStatus.ISSUE,
            )
            if self.managed_mode
            else ImageStatus
        )
        for status in statuses:
            prefix = "review" if self.managed_mode else "status"
            self.status_combo.addItem(tr(self.locale, f"{prefix}.{status.value}"), status)
        for index in range(self.status_combo.count()):
            if self.status_combo.itemData(index) == current_status:
                self.status_combo.setCurrentIndex(index)
                break
        current_sort = self.sort_combo.currentData()
        self.sort_combo.blockSignals(True)
        self.sort_combo.clear()
        for key, value in (
            ("workspace.sort_name_asc", SampleSort.FILENAME_ASC),
            ("workspace.sort_name_desc", SampleSort.FILENAME_DESC),
            ("workspace.sort_newest", SampleSort.IMPORTED_NEWEST),
            ("workspace.sort_oldest", SampleSort.IMPORTED_OLDEST),
        ):
            self.sort_combo.addItem(tr(self.locale, key), value)
        for index in range(self.sort_combo.count()):
            if self.sort_combo.itemData(index) == current_sort:
                self.sort_combo.setCurrentIndex(index)
                break
        self.sort_combo.blockSignals(False)
        current_label = self.label_combo.currentData()
        self.label_combo.blockSignals(True)
        self.label_combo.clear()
        for label in self.snapshot.labels:
            if not label.archived:
                self.label_combo.addItem(f"{label.alias} · {label.name}", label.id)
                self.label_combo.setItemData(
                    self.label_combo.count() - 1,
                    label.description,
                    Qt.ItemDataRole.ToolTipRole,
                )
        for index in range(self.label_combo.count()):
            if self.label_combo.itemData(index) == current_label:
                self.label_combo.setCurrentIndex(index)
                break
        self.label_combo.setPlaceholderText(tr(self.locale, "canvas.label"))
        self.label_combo.blockSignals(False)
        self.canvas.set_current_label(self.label_combo.currentData())
        current_filter = self.label_filter_combo.currentData()
        self.label_filter_combo.blockSignals(True)
        self.label_filter_combo.clear()
        self.label_filter_combo.addItem(tr(self.locale, "label.filter_all"), None)
        for label in self.snapshot.labels:
            self.label_filter_combo.addItem(f"{label.alias} · {label.name}", label.id)
        for index in range(self.label_filter_combo.count()):
            if self.label_filter_combo.itemData(index) == current_filter:
                self.label_filter_combo.setCurrentIndex(index)
                break
        self.label_filter_combo.blockSignals(False)
        current_review = self.review_combo.currentData()
        self.review_combo.blockSignals(True)
        self.review_combo.clear()
        for status in (
            ReviewStatus.UNREVIEWED,
            ReviewStatus.PENDING_REVIEW,
            ReviewStatus.COMPLETED,
            ReviewStatus.COMPLETED_NEGATIVE,
            ReviewStatus.ISSUE,
        ):
            self.review_combo.addItem(tr(self.locale, f"review.{status.value}"), status)
        for index in range(self.review_combo.count()):
            if self.review_combo.itemData(index) == current_review:
                self.review_combo.setCurrentIndex(index)
                break
        self.review_combo.blockSignals(False)
        self.delete_annotation_button.setText(tr(self.locale, "annotation.delete_selected"))
        for name, button in self.tool_buttons.items():
            button.setToolTip(tr(self.locale, f"tool.{name}"))
            button.setAccessibleName(button.toolTip())
        self.export_menu.clear()
        yolo = self.export_menu.addAction("YOLO Detection")
        yolo.triggered.connect(lambda: self.dialog_requested.emit("yolo_export"))
        xany = self.export_menu.addAction("X-AnyLabeling / LabelMe")
        xany.triggered.connect(lambda: self.dialog_requested.emit("xany_exchange"))
        backup = self.export_menu.addAction(tr(self.locale, "dialog.backup.title"))
        backup.triggered.connect(lambda: self.dialog_requested.emit("backup_export"))
        self.more_menu.clear()
        for text_key, target in (
            ("page.similarity.title", "similarity_review"),
            ("page.overview.title", "dataset_overview"),
            ("page.trash.title", "trash"),
        ):
            action = self.more_menu.addAction(tr(self.locale, text_key))
            action.triggered.connect(
                lambda checked=False, route=target: self.route_requested.emit(route)
            )
        if self.managed_mode:
            self.more_menu.addSeparator()
            rename = self.more_menu.addAction(tr(self.locale, "dialog.rename.title"))
            rename.triggered.connect(
                lambda: self.dialog_requested.emit(f"rename_samples:{self.snapshot.dataset.id}")
            )
            tasks = self.more_menu.addAction(tr(self.locale, "dialog.task.title"))
            tasks.triggered.connect(
                lambda: self.dialog_requested.emit(f"task_center:{self.snapshot.dataset.id}")
            )
            self.delete_action = self.more_menu.addAction(tr(self.locale, "dialog.delete.title"))
            self.delete_action.setEnabled(self.current_image_id is not None)
            self.delete_action.triggered.connect(self._request_current_delete)
        self._rebuild_annotations()
        self._rebuild_images()
        self._update_status_bar()

    def _initialize_managed_browser(self) -> None:
        """普通模式装配分页模型，并启用真实矩形标注工具。"""

        self.sample_model = ManagedSampleListModel(
            self.gateway,
            self.snapshot.dataset.id,
            self.locale,
            self,
        )
        self.sample_delegate = ManagedSampleDelegate(self.locale, parent=self.image_list)
        self.image_list.setModel(self.sample_model)
        self.image_list.setItemDelegate(self.sample_delegate)
        self.image_list.selectionModel().currentChanged.connect(self._on_managed_image_selected)
        self.sample_model.page_changed.connect(self._on_page_changed)
        self.previous_page_button.clicked.connect(self._previous_page)
        self.next_page_button.clicked.connect(self._next_page)
        self.preview_toggle.toggled.connect(self.sample_model.set_annotation_preview)
        self.sample_model.set_annotation_preview(self.preview_toggle.isChecked())
        has_active_labels = any(not label.archived for label in self.snapshot.labels)
        for name in ("select", "undo", "redo"):
            self.tool_buttons[name].setEnabled(True)
        self.tool_buttons["rectangle"].setEnabled(has_active_labels)
        self.tool_buttons["ai"].setEnabled(False)
        self.tool_buttons["select"].setChecked(True)
        self.canvas.set_tool(CanvasTool.SELECT)
        if not has_active_labels:
            self.tool_buttons["rectangle"].setToolTip(tr(self.locale, "canvas.no_label"))
        self.refresh_managed_samples(select_first=True)

    def refresh_managed_samples(
        self,
        *,
        select_first: bool = False,
        sample_id: str | None = None,
    ) -> None:
        """导入、删除或切换筛选后仅刷新当前页。"""

        if self.sample_model is None:
            return
        self.sample_model.refresh()
        self.image_stack.setCurrentIndex(0 if self.sample_model.rowCount() else 1)
        target_row = self.sample_model.row_for_id(sample_id or "")
        if target_row < 0 and select_first and self.sample_model.rowCount():
            target_row = 0
        if target_row >= 0:
            index = self.sample_model.index(target_row)
            self.image_list.setCurrentIndex(index)
        elif not self.sample_model.rowCount():
            self._managed_sample = None
            self.current_image_id = None
            self.canvas.clear_preview()
            self._update_status_bar()

    def open_navigation_target(self, target: WorkspaceNavigationTarget) -> None:
        """定位标签检查目标，加载完成后再高亮具体矩形。"""

        if not self.managed_mode or target.dataset_id != self.snapshot.dataset.id:
            return
        self._pending_navigation_target = target
        if target.sample_id and self.sample_model is not None:
            row = self.sample_model.load_page_for_sample(target.sample_id)
            if row >= 0:
                self.image_list.setCurrentIndex(self.sample_model.index(row))

    def _on_page_changed(self, current: int, total: int, count: int) -> None:
        self.page_label.setText(
            tr(self.locale, "browser.page").format(page=current, total=total, count=count)
        )
        self.previous_page_button.setEnabled(current > 1)
        self.next_page_button.setEnabled(current < total)

    def _previous_page(self) -> None:
        if self.sample_model is None:
            return
        self.sample_model.previous_page()
        if self.sample_model.rowCount():
            self.image_list.setCurrentIndex(self.sample_model.index(0))

    def _next_page(self) -> None:
        if self.sample_model is None:
            return
        self.sample_model.next_page()
        if self.sample_model.rowCount():
            self.image_list.setCurrentIndex(self.sample_model.index(0))

    def _on_image_filter_changed(self) -> None:
        if not self.managed_mode:
            self._rebuild_images()
            return
        if self.sample_model is None:
            return
        self.sample_model.refresh(
            reset_page=True,
            search=self.image_search.text(),
            review_status=self.status_combo.currentData(),
            label_id=self.label_filter_combo.currentData(),
            sort=self.sort_combo.currentData() or SampleSort.FILENAME_ASC,
        )
        self.image_stack.setCurrentIndex(0 if self.sample_model.rowCount() else 1)
        if self.sample_model.rowCount():
            self.image_list.setCurrentIndex(self.sample_model.index(0))
        else:
            self.canvas.clear_preview()

    def _on_managed_image_selected(
        self,
        current: QModelIndex,
        previous: QModelIndex,
    ) -> None:
        if self.sample_model is None or not current.isValid():
            return
        sample = self.sample_model.sample_at(current.row())
        if sample is None:
            return
        if (
            previous.isValid()
            and self.current_image_id is not None
            and sample.id != self.current_image_id
            and not self.prepare_to_leave()
        ):
            self.image_list.setCurrentIndex(previous)
            return
        self._managed_sample = sample
        self.current_image_id = sample.id
        if hasattr(self, "delete_action"):
            self.delete_action.setEnabled(True)
        self._start_managed_image_load(sample)
        self._rebuild_annotations()
        self._update_status_bar()

    def _start_managed_image_load(self, sample: DatasetSample) -> None:
        self._image_generation += 1
        job = _ImageLoadJob(
            self.gateway,
            self.snapshot.dataset.id,
            sample.id,
            self._image_generation,
        )
        self._image_jobs.add(job)
        job.signals.completed.connect(self._managed_image_ready)
        job.signals.failed.connect(self._managed_image_failed)
        job.signals.completed.connect(lambda *_args, current=job: self._image_jobs.discard(current))
        job.signals.failed.connect(lambda *_args, current=job: self._image_jobs.discard(current))
        from PySide6.QtCore import QThreadPool

        QThreadPool.globalInstance().start(job)

    def _managed_image_ready(self, generation: int, sample_id: str, payload) -> None:
        if generation != self._image_generation or sample_id != self.current_image_id:
            return
        sample = self._managed_sample
        if sample is None:
            return
        asset, annotation_load = payload
        self._annotation_load = annotation_load
        self._annotation_document = (
            annotation_load.document.model_copy(deep=True)
            if annotation_load.document is not None
            else None
        )
        self._annotation_disk_sha256 = annotation_load.disk_sha256
        view = self._managed_view_data(sample)
        annotations = (
            tuple(self._annotation_view(item) for item in self._annotation_document.rectangles)
            if self._annotation_document is not None
            else ()
        )
        if not self.canvas.load_managed_image(
            view,
            asset.data,
            self.snapshot.labels,
            annotations,
            editable=annotation_load.editable,
        ):
            self.message_requested.emit("toast.image_load_failed")
            return
        self._set_review_combo(
            self._annotation_document.review_status
            if self._annotation_document is not None
            else sample.review_status
        )
        self._rebuild_annotations()
        self._update_status_bar()
        if annotation_load.diagnostics:
            self.message_requested.emit("toast.annotation_warning")
        target = self._pending_navigation_target
        if target and target.sample_id == sample_id:
            if target.shape_id:
                self.canvas.select_shape(target.shape_id)
            elif target.focus_label_id:
                matching = next(
                    (
                        item.id
                        for item in self.canvas.annotations
                        if item.label_id == target.focus_label_id
                    ),
                    None,
                )
                if matching:
                    self.canvas.select_shape(matching)
            self._pending_navigation_target = None

    def _managed_image_failed(self, generation: int, sample_id: str) -> None:
        if generation != self._image_generation or sample_id != self.current_image_id:
            return
        self.canvas.clear_preview()
        self._annotation_load = None
        self._annotation_document = None
        self.message_requested.emit("toast.image_load_failed")

    @staticmethod
    def _managed_view_data(sample: DatasetSample) -> ImageItemViewData:
        status = {
            ReviewStatus.UNREVIEWED: ImageStatus.UNLABELED,
            ReviewStatus.PENDING_REVIEW: ImageStatus.PENDING,
            ReviewStatus.COMPLETED: ImageStatus.COMPLETED,
            ReviewStatus.COMPLETED_NEGATIVE: ImageStatus.NEGATIVE,
            ReviewStatus.AUTO_PENDING_REVIEW: ImageStatus.PENDING,
            ReviewStatus.REVIEWED: ImageStatus.COMPLETED,
            ReviewStatus.ISSUE: ImageStatus.ISSUE,
        }[sample.review_status]
        if sample.health != SampleHealth.READY or sample.annotation_state.value in {
            "corrupt",
            "unknown_label",
            "recovery_required",
        }:
            status = ImageStatus.ERROR
        return ImageItemViewData(
            sample.id,
            sample.filename,
            status,
            sample.width,
            sample.height,
            0,
            sample.annotation_count,
        )

    @staticmethod
    def _annotation_view(rectangle: RectangleShape) -> AnnotationItemViewData:
        return AnnotationItemViewData(
            rectangle.id,
            rectangle.label_id,
            rectangle.x1,
            rectangle.y1,
            rectangle.x2,
            rectangle.y2,
            rectangle.confidence,
        )

    def resizeEvent(self, event: QResizeEvent) -> None:
        """窄窗口收起低频顶部操作，同时保留导入、导出和画布工具。"""

        compact = self.width() < 1180
        if compact != self._compact:
            self._compact = compact
            self.labels_button.setVisible(not compact)
            self.models_button.setVisible(not compact)
            self.settings_button.setVisible(not compact)
            self.right_panel.setMaximumWidth(330 if compact else 400)
        super().resizeEvent(event)

    def _load_current_image(self) -> None:
        image = self._current_image()
        if image is None:
            self.canvas.clear_preview()
            self._rebuild_annotations()
            self._rebuild_images()
            self._update_status_bar()
            return
        annotations = self.snapshot.annotations_by_image.get(image.id, ())
        self.canvas.load_preview(image, self.snapshot.labels, annotations)
        self._rebuild_annotations()
        self._update_status_bar()

    def _current_image(self) -> ImageItemViewData | None:
        if self.managed_mode:
            return (
                self._managed_view_data(self._managed_sample)
                if self._managed_sample is not None
                else None
            )
        return next(
            (item for item in self.snapshot.images if item.id == self.current_image_id), None
        )

    def _rebuild_annotations(self) -> None:
        current = self._current_image()
        if self.managed_mode:
            annotations = tuple(self.canvas.annotations) if current is not None else ()
        else:
            annotations = (
                () if current is None else self.snapshot.annotations_by_image.get(current.id, ())
            )
        if not self.managed_mode and self.canvas.annotations and current is not None:
            annotations = tuple(self.canvas.annotations)
        self.annotation_list.blockSignals(True)
        self.annotation_list.clear()
        label_by_id = {label.id: label for label in self.snapshot.labels}
        for annotation in annotations:
            label = label_by_id.get(annotation.label_id)
            if label is None:
                continue
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, annotation.id)
            item.setSizeHint(QSize(260, 48))
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(9, 4, 8, 4)
            color = QLabel()
            color.setFixedSize(12, 30)
            color.setStyleSheet(f"background:{label.color}; border-radius:6px;")
            copy = QVBoxLayout()
            copy.setSpacing(0)
            alias_text = label.alias + (
                f" · {tr(self.locale, 'label.archived_hint')}" if label.archived else ""
            )
            alias = QLabel(alias_text)
            alias.setStyleSheet("font-weight:650;")
            name = QLabel(label.name)
            name.setObjectName("mutedText")
            copy.addWidget(alias)
            copy.addWidget(name)
            row_layout.addWidget(color)
            row_layout.addLayout(copy, 1)
            if annotation.confidence is not None:
                confidence = QLabel(f"{annotation.confidence:.0%}")
                confidence.setObjectName("mutedText")
                row_layout.addWidget(confidence)
            self.annotation_list.addItem(item)
            self.annotation_list.setItemWidget(item, row)
            if annotation.id == self.selected_shape_id:
                item.setSelected(True)
        self.annotation_list.blockSignals(False)

    def _rebuild_images(self) -> None:
        if self.managed_mode:
            if self.sample_model is not None:
                self.sample_model.refresh()
                self.image_stack.setCurrentIndex(0 if self.sample_model.rowCount() else 1)
            return
        query = self.image_search.text().strip().casefold()
        status = self.status_combo.currentData()
        grid = self.image_list.viewMode() == QListView.ViewMode.IconMode
        self.image_list.blockSignals(True)
        self.image_list.clear()
        for image in self.snapshot.images:
            if query and query not in image.filename.casefold():
                continue
            if status is not None and image.status != status:
                continue
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, image.id)
            if grid:
                item.setIcon(self._thumbnail(image))
                item.setText(f"{image.filename}\n{tr(self.locale, f'status.{image.status.value}')}")
                item.setSizeHint(QSize(132, 118))
            else:
                item.setSizeHint(QSize(280, 60))
            self.image_list.addItem(item)
            if not grid:
                self.image_list.setItemWidget(item, self._image_row(image))
            if image.id == self.current_image_id:
                self.image_list.setCurrentItem(item)
        self.image_list.blockSignals(False)
        self.image_stack.setCurrentIndex(0 if self.image_list.count() else 1)

    def _on_dataset_changed(self, index: int) -> None:
        """快速切换只发送稳定 ID，主窗口重新向网关请求完整上下文。"""

        dataset_id = self.dataset_combo.itemData(index)
        if dataset_id and dataset_id != self.snapshot.dataset.id:
            self.route_requested.emit(f"annotation_workspace:{dataset_id}")

    def _request_current_delete(self) -> None:
        if self.current_image_id:
            self.dialog_requested.emit(
                f"delete_current:{self.snapshot.dataset.id}:{self.current_image_id}"
            )

    def _image_row(self, image: ImageItemViewData) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(6, 4, 6, 4)
        preview = QLabel()
        preview.setPixmap(self._thumbnail(image).pixmap(58, 42))
        preview.setFixedSize(62, 46)
        copy = QVBoxLayout()
        copy.setSpacing(1)
        filename = QLabel(image.filename)
        filename.setStyleSheet("font-weight:600;")
        count = QLabel(f"{image.annotation_count} {tr(self.locale, 'value.boxes')}")
        count.setObjectName("mutedText")
        copy.addWidget(filename)
        copy.addWidget(count)
        layout.addWidget(preview)
        layout.addLayout(copy, 1)
        layout.addWidget(StatusBadge(self.locale, image.status))
        return row

    def _thumbnail(self, image: ImageItemViewData):
        pixmap = QPixmap(120, 80)
        pixmap.fill(QColor("#C9CED2"))
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(QColor("#687685"))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(16, 14, 88, 52, 8, 8)
        if self.preview_toggle.isChecked() and image.annotation_count:
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(THEME.tokens.brand_primary), 2))
            painter.drawRect(11, 9, 98, 62)
        painter.end()
        return QIcon(pixmap)

    def _set_image_mode(self, grid: bool) -> None:
        self.grid_toggle.setChecked(grid)
        self.list_toggle.setChecked(not grid)
        self.image_list.setViewMode(
            QListView.ViewMode.IconMode if grid else QListView.ViewMode.ListMode
        )
        self.image_list.setIconSize(QSize(120, 80))
        self.image_list.setResizeMode(QListView.ResizeMode.Adjust)
        if self.managed_mode:
            if self.sample_delegate is not None:
                self.sample_delegate.set_grid(grid)
            self.image_list.setGridSize(QSize(148, 126) if grid else QSize())
            self.image_list.viewport().update()
            return
        self._rebuild_images()

    def _on_image_selected(self, current: QListWidgetItem | None) -> None:
        if current is None:
            return
        image_id = str(current.data(Qt.ItemDataRole.UserRole))
        if image_id == self.current_image_id:
            return
        self.current_image_id = image_id
        self.selected_shape_id = None
        self._load_current_image()

    def _on_annotation_selected(self, current: QListWidgetItem | None) -> None:
        if current is None:
            return
        shape_id = str(current.data(Qt.ItemDataRole.UserRole))
        self.selected_shape_id = shape_id
        self.canvas.select_shape(shape_id)

    def _on_annotation_double_clicked(self, item: QListWidgetItem) -> None:
        self._request_shape_reassignment(str(item.data(Qt.ItemDataRole.UserRole)))

    def _on_canvas_shape_selected(self, shape_id: str) -> None:
        self.selected_shape_id = shape_id
        for index in range(self.annotation_list.count()):
            item = self.annotation_list.item(index)
            if item.data(Qt.ItemDataRole.UserRole) == shape_id:
                self.annotation_list.blockSignals(True)
                self.annotation_list.setCurrentItem(item)
                self.annotation_list.blockSignals(False)
                break

    def _on_canvas_changed(self) -> None:
        self._rebuild_annotations()
        if not self.managed_mode or self._annotation_document is None:
            self.message_requested.emit("workspace.saved")
            return
        previous = self._annotation_document
        old_rectangles = {rectangle.id: rectangle for rectangle in previous.rectangles}
        rectangles = [
            RectangleShape(
                id=item.id,
                label_id=item.label_id,
                x1=item.x1,
                y1=item.y1,
                x2=item.x2,
                y2=item.y2,
                source_model_id=(
                    old_rectangles[item.id].source_model_id if item.id in old_rectangles else None
                ),
                confidence=item.confidence,
                compatibility_payload=(
                    old_rectangles[item.id].compatibility_payload
                    if item.id in old_rectangles
                    else {}
                ),
            )
            for item in self.canvas.annotations
        ]
        old_ids = set(old_rectangles)
        new_ids = {rectangle.id for rectangle in rectangles}
        shape_order = [
            identifier
            for identifier in previous.shape_order
            if identifier not in old_ids or identifier in new_ids
        ]
        shape_order.extend(
            rectangle.id for rectangle in rectangles if rectangle.id not in shape_order
        )
        updated = previous.model_copy(
            deep=True,
            update={
                "rectangles": rectangles,
                "shape_order": shape_order,
                "review_status": review_status_after_edit(previous.review_status),
                "document_version": previous.document_version + 1,
            },
        )
        self._annotation_document = updated
        self._set_review_combo(updated.review_status)
        self._queue_annotation_save(updated)

    def _queue_annotation_save(self, document: AnnotationDocument) -> None:
        """通过 Gateway 排入不可变快照，页面不直接执行磁盘 I/O。"""

        if not self.managed_mode or self.current_image_id is None:
            return
        request = AnnotationSaveRequest(
            self.snapshot.dataset.id,
            self.current_image_id,
            document.document_version,
            self._annotation_disk_sha256,
            document.model_copy(deep=True),
        )
        self._save_future = self.gateway.queue_annotation_save(request)
        self.save_label.setText(tr(self.locale, "canvas.saving"))
        self.save_poll_timer.start()

    def _poll_annotation_save(self) -> None:
        future = self._save_future
        if future is None or not future.done():
            state, _error = self.gateway.annotation_save_state(self.snapshot.dataset.id)
            if state == AutosaveState.SAVING:
                self.save_label.setText(tr(self.locale, "canvas.saving"))
            return
        self.save_poll_timer.stop()
        try:
            result = future.result()
        except Exception:
            self.save_label.setText(tr(self.locale, "canvas.save_failed"))
            self.save_label.setStyleSheet(f"color:{THEME.tokens.danger}; font-weight:600;")
            self.message_requested.emit("canvas.save_failed")
            return
        self._annotation_disk_sha256 = result.json_sha256
        self.save_label.setText("●  " + tr(self.locale, "canvas.autosaved"))
        self.save_label.setStyleSheet(f"color:{THEME.tokens.success}; font-weight:600;")
        if self.sample_model is not None and self.current_image_id:
            sample = self.gateway.get_sample(
                self.snapshot.dataset.id,
                self.current_image_id,
            )
            if sample is not None:
                self._managed_sample = sample
                self.sample_model.replace_sample(sample)
        self._update_status_bar()

    def _retry_save(self) -> None:
        if not self.managed_mode:
            return
        state, _error = self.gateway.annotation_save_state(self.snapshot.dataset.id)
        if state != AutosaveState.FAILED:
            return
        self._save_future = self.gateway.retry_annotation_save(self.snapshot.dataset.id)
        self.save_label.setText(tr(self.locale, "canvas.saving"))
        self.save_poll_timer.start()

    def _on_label_combo_changed(self, index: int) -> None:
        label_id = self.label_combo.itemData(index) if index >= 0 else None
        self.canvas.set_current_label(label_id)
        if self._reassign_shape_id and label_id:
            self.canvas.select_shape(self._reassign_shape_id)
            self.canvas.change_selected_label(label_id)
            self._reassign_shape_id = None

    def _request_shape_reassignment(self, shape_id: str) -> None:
        if not self.managed_mode:
            return
        self._reassign_shape_id = shape_id
        self.canvas.select_shape(shape_id)
        current = next(
            (item for item in self.canvas.annotations if item.id == shape_id),
            None,
        )
        if current is not None:
            for index in range(self.label_combo.count()):
                if self.label_combo.itemData(index) == current.label_id:
                    self.label_combo.setCurrentIndex(index)
                    break
        self.label_combo.showPopup()

    def _on_review_status_changed(self, index: int) -> None:
        if (
            not self.managed_mode
            or self._annotation_document is None
            or index < 0
            or self.review_combo.signalsBlocked()
        ):
            return
        try:
            status = ReviewStatus(str(self.review_combo.itemData(index)))
        except ValueError:
            return
        if status == self._annotation_document.review_status:
            return
        count = len(self._annotation_document.rectangles)
        if status == ReviewStatus.COMPLETED and count == 0:
            self.message_requested.emit("toast.review_requires_box")
            self._set_review_combo(self._annotation_document.review_status)
            return
        if status == ReviewStatus.COMPLETED_NEGATIVE and count != 0:
            self.message_requested.emit("toast.negative_requires_empty")
            self._set_review_combo(self._annotation_document.review_status)
            return
        updated = self._annotation_document.model_copy(
            deep=True,
            update={
                "review_status": status,
                "document_version": self._annotation_document.document_version + 1,
            },
        )
        self._annotation_document = updated
        self._queue_annotation_save(updated)

    def _set_review_combo(self, status: ReviewStatus) -> None:
        normalized = {
            ReviewStatus.AUTO_PENDING_REVIEW: ReviewStatus.PENDING_REVIEW,
            ReviewStatus.REVIEWED: ReviewStatus.COMPLETED,
        }.get(status, status)
        self.review_combo.blockSignals(True)
        for index in range(self.review_combo.count()):
            if self.review_combo.itemData(index) == normalized:
                self.review_combo.setCurrentIndex(index)
                break
        self.review_combo.blockSignals(False)

    def prepare_to_leave(self) -> bool:
        """离开当前图片前等待保存；失败时由用户明确重试、放弃或取消。"""

        if not self.managed_mode:
            return True
        state, _error = self.gateway.annotation_save_state(self.snapshot.dataset.id)
        if state == AutosaveState.SAVING:
            try:
                self.gateway.wait_annotation_save(self.snapshot.dataset.id)
            except Exception:
                state = AutosaveState.FAILED
            else:
                state = AutosaveState.SAVED
        if state != AutosaveState.FAILED:
            return True
        box = QMessageBox(self)
        box.setWindowTitle(tr(self.locale, "canvas.leave_failed_title"))
        box.setText(tr(self.locale, "canvas.leave_failed_body"))
        retry = box.addButton(tr(self.locale, "canvas.retry"), QMessageBox.ButtonRole.AcceptRole)
        discard = box.addButton(
            tr(self.locale, "canvas.discard"),
            QMessageBox.ButtonRole.DestructiveRole,
        )
        cancel = box.addButton(tr(self.locale, "action.cancel"), QMessageBox.ButtonRole.RejectRole)
        box.exec()
        clicked = box.clickedButton()
        if clicked is retry:
            try:
                self.gateway.retry_annotation_save(self.snapshot.dataset.id).result()
                return True
            except Exception:
                self.message_requested.emit("canvas.save_failed")
                return False
        if clicked is discard:
            self._annotation_document = None
            self._annotation_load = None
            return True
        if clicked is cancel:
            return False
        return False

    def _set_zoom(self, zoom: int) -> None:
        self.zoom_label.setText(tr(self.locale, "workspace.zoom").format(zoom=zoom))

    def _update_status_bar(self) -> None:
        image = self._current_image()
        if image is None:
            self.index_label.setText(
                tr(self.locale, "workspace.image_index").format(current=0, total=0)
            )
            self.resolution_label.setText(tr(self.locale, "workspace.no_image"))
            self.zoom_label.setText(tr(self.locale, "workspace.zoom").format(zoom=100))
            self.save_label.setText(tr(self.locale, "workspace.waiting_for_import"))
            return
        if self.managed_mode and self.sample_model is not None:
            row = self.sample_model.row_for_id(image.id)
            index = self.sample_model.offset + max(0, row) + 1
            total = self.sample_model.total
        else:
            index = next(
                (
                    position
                    for position, item in enumerate(self.snapshot.images, 1)
                    if item.id == image.id
                ),
                1,
            )
            total = len(self.snapshot.images)
        self.index_label.setText(
            tr(self.locale, "workspace.image_index").format(current=index, total=total)
        )
        self.resolution_label.setText(
            tr(self.locale, "workspace.resolution").format(width=image.width, height=image.height)
        )
        self.zoom_label.setText(tr(self.locale, "workspace.zoom").format(zoom=100))
        if self.managed_mode:
            state, _error = self.gateway.annotation_save_state(self.snapshot.dataset.id)
            state_key = {
                AutosaveState.IDLE: "canvas.ready",
                AutosaveState.SAVING: "canvas.saving",
                AutosaveState.SAVED: "canvas.autosaved",
                AutosaveState.FAILED: "canvas.save_failed",
                AutosaveState.RECOVERING: "canvas.recovering",
            }[state]
            self.save_label.setText("●  " + tr(self.locale, state_key))
        else:
            self.save_label.setText("●  " + tr(self.locale, "workspace.saved"))
