"""DatumDock 四区标注工作台的可交互视觉原型。"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap, QResizeEvent
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
    QPushButton,
    QSplitter,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from datumdock.i18n.catalog import LocaleService, tr
from datumdock.resources import resource_root
from datumdock.ui.components import (
    BrandLockup,
    FilterChip,
    GhostButton,
    PreviewBanner,
    SearchBox,
    StatusBadge,
    ToolButton,
    brand_asset_path,
)
from datumdock.ui.icons import IconRegistry
from datumdock.ui.preview_canvas import CanvasTool, PreviewAnnotationCanvas
from datumdock.ui.prototype_models import (
    ImageItemViewData,
    ImageStatus,
    WorkspaceSnapshot,
)
from datumdock.ui.theme import THEME


class AnnotationWorkspace(QWidget):
    """组合顶部、左侧、画布和右侧面板，只处理内存演示状态。"""

    home_requested = Signal()
    route_requested = Signal(str)
    dialog_requested = Signal(str)
    message_requested = Signal(str)

    def __init__(
        self,
        locale: LocaleService,
        preview_mode: bool,
        snapshot: WorkspaceSnapshot,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.locale = locale
        self.preview_mode = preview_mode
        self.snapshot = snapshot
        self.icons = IconRegistry(resource_root())
        self.current_image_id = snapshot.images[0].id if snapshot.images else None
        self.selected_shape_id: str | None = None
        self._compact = False
        self._build_ui()
        self.retranslate_ui()
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
        root.addWidget(banner_wrap)

        body = QWidget()
        body_layout = QHBoxLayout(body)
        body_layout.setContentsMargins(10, 8, 10, 8)
        body_layout.setSpacing(8)
        self.canvas = PreviewAnnotationCanvas()
        self.canvas.shape_selected.connect(self._on_canvas_shape_selected)
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
        self.dataset_combo.addItem(self.snapshot.dataset.name, self.snapshot.dataset.id)
        self.dataset_combo.addItem("可回收物分类", "recycle-items")
        self.dataset_combo.addItem("仓库安全检查", "warehouse")
        layout.addWidget(self.dataset_combo)
        layout.addStretch()
        self.import_button = QPushButton()
        self.import_button.clicked.connect(lambda: self.dialog_requested.emit("image_import"))
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
        layout.addWidget(self.annotation_list, 1)
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
        self.image_search.textChanged.connect(self._rebuild_images)
        layout.addWidget(self.image_search)
        filter_row = QHBoxLayout()
        self.status_combo = QComboBox()
        self.status_combo.currentIndexChanged.connect(self._rebuild_images)
        filter_row.addWidget(self.status_combo, 1)
        self.list_toggle = FilterChip("")
        self.grid_toggle = FilterChip("")
        self.list_toggle.setChecked(True)
        self.list_toggle.clicked.connect(lambda: self._set_image_mode(False))
        self.grid_toggle.clicked.connect(lambda: self._set_image_mode(True))
        filter_row.addWidget(self.list_toggle)
        filter_row.addWidget(self.grid_toggle)
        layout.addLayout(filter_row)
        self.image_list = QListWidget()
        self.image_list.setSpacing(4)
        self.image_list.currentItemChanged.connect(self._on_image_selected)
        layout.addWidget(self.image_list, 1)
        self.preview_toggle = FilterChip("")
        self.preview_toggle.setChecked(True)
        layout.addWidget(self.preview_toggle)
        self.image_collapse.clicked.connect(
            lambda: self.image_list.setVisible(not self.image_list.isVisible())
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
        current_status = self.status_combo.currentData()
        self.status_combo.clear()
        self.status_combo.addItem(tr(self.locale, "workspace.all_status"), None)
        for status in ImageStatus:
            self.status_combo.addItem(tr(self.locale, f"status.{status.value}"), status)
        for index in range(self.status_combo.count()):
            if self.status_combo.itemData(index) == current_status:
                self.status_combo.setCurrentIndex(index)
                break
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
        self._rebuild_annotations()
        self._rebuild_images()
        self._update_status_bar()

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
            return
        annotations = self.snapshot.annotations_by_image.get(image.id, ())
        self.canvas.load_preview(image, self.snapshot.labels, annotations)
        self._rebuild_annotations()
        self._update_status_bar()

    def _current_image(self) -> ImageItemViewData | None:
        return next(
            (item for item in self.snapshot.images if item.id == self.current_image_id), None
        )

    def _rebuild_annotations(self) -> None:
        current = self._current_image()
        annotations = (
            () if current is None else self.snapshot.annotations_by_image.get(current.id, ())
        )
        if self.canvas.annotations and current is not None:
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
            alias = QLabel(label.alias)
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
        self.message_requested.emit(tr(self.locale, "workspace.saved"))

    def _set_zoom(self, zoom: int) -> None:
        self.zoom_label.setText(tr(self.locale, "workspace.zoom").format(zoom=zoom))

    def _update_status_bar(self) -> None:
        image = self._current_image()
        if image is None:
            self.index_label.clear()
            self.resolution_label.clear()
            return
        index = next(
            (
                position
                for position, item in enumerate(self.snapshot.images, 1)
                if item.id == image.id
            ),
            1,
        )
        self.index_label.setText(
            tr(self.locale, "workspace.image_index").format(
                current=index, total=len(self.snapshot.images)
            )
        )
        self.resolution_label.setText(
            tr(self.locale, "workspace.resolution").format(width=image.width, height=image.height)
        )
        self.zoom_label.setText(tr(self.locale, "workspace.zoom").format(zoom=100))
        self.save_label.setText("●  " + tr(self.locale, "workspace.saved"))
