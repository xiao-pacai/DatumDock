"""步骤四真实标签管理、编辑迁移与标签图片检查页面。"""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from datumdock.domain.models import Label, LabelStatus
from datumdock.i18n.catalog import LocaleService, tr
from datumdock.services.managed_labels import ManagedLabelError
from datumdock.ui.components import DangerButton, GhostButton, PageHeader, PrimaryButton, SearchBox
from datumdock.ui.prototype_pages import RouteId


class ManagedLabelEditDialog(QDialog):
    """新增或编辑真实标签；训练映射变化必须先显示影响预览。"""

    def __init__(
        self,
        locale: LocaleService,
        gateway,
        dataset_id: str,
        label: Label | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.locale = locale
        self.gateway = gateway
        self.dataset_id = dataset_id
        self.label = label
        self.saved_label_id: str | None = label.id if label else None
        self.setMinimumWidth(520)
        root = QVBoxLayout(self)
        form = QFormLayout()
        self.name_edit = QLineEdit(label.name if label else "")
        self.alias_edit = QLineEdit(label.alias if label else "")
        self.class_id = QSpinBox()
        self.class_id.setRange(0, 999_999)
        self.class_id.setValue(label.class_id if label else self._next_class_id())
        self.description_edit = QTextEdit(label.description if label else "")
        self.description_edit.setMaximumHeight(90)
        self.synonyms_edit = QLineEdit(", ".join(label.synonyms) if label else "")
        color_row = QWidget()
        color_layout = QHBoxLayout(color_row)
        color_layout.setContentsMargins(0, 0, 0, 0)
        self.color_edit = QLineEdit(label.color if label else "")
        self.color_button = QPushButton()
        self.color_button.clicked.connect(self._choose_color)
        color_layout.addWidget(self.color_edit, 1)
        color_layout.addWidget(self.color_button)
        form.addRow(tr(locale, "label.name"), self.name_edit)
        form.addRow(tr(locale, "label.alias"), self.alias_edit)
        form.addRow(tr(locale, "label.class_id"), self.class_id)
        form.addRow(tr(locale, "label.description"), self.description_edit)
        form.addRow(tr(locale, "label.synonyms"), self.synonyms_edit)
        form.addRow(tr(locale, "label.color"), color_row)
        root.addLayout(form)
        self.error_label = QLabel()
        self.error_label.setObjectName("errorText")
        self.error_label.setWordWrap(True)
        root.addWidget(self.error_label)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        self.setWindowTitle(tr(locale, "label.dialog.edit" if label else "label.dialog.add"))
        self.color_button.setText(tr(locale, "label.choose_color"))

    def _next_class_id(self) -> int:
        labels = self.gateway.list_labels(self.dataset_id)
        used = {label.class_id for label in labels}
        return next(index for index in range(len(used) + 1) if index not in used)

    def _choose_color(self) -> None:
        color = QColorDialog.getColor(QColor(self.color_edit.text() or "#4D8FBF"), self)
        if color.isValid():
            self.color_edit.setText(color.name().upper())

    def _save(self) -> None:
        try:
            synonyms = tuple(
                item.strip()
                for item in self.synonyms_edit.text().replace("，", ",").split(",")
                if item.strip()
            )
            if self.label is None:
                created = self.gateway.add_label(
                    self.dataset_id,
                    class_id=self.class_id.value(),
                    name=self.name_edit.text(),
                    alias=self.alias_edit.text(),
                    description=self.description_edit.toPlainText(),
                    synonyms=synonyms,
                    color=self.color_edit.text().strip() or None,
                )
                self.saved_label_id = created.labels[-1].id
                self.accept()
                return
            label_set = self.gateway.get_label_set(self.dataset_id)
            name = self.name_edit.text().strip()
            class_id = self.class_id.value()
            if name != self.label.name or class_id != self.label.class_id:
                preview = self.gateway.preview_label_change(
                    self.dataset_id,
                    self.label.id,
                    name=name,
                    class_id=class_id,
                )
                message = tr(self.locale, "label.migration.confirm").format(
                    images=preview.affected_images,
                    shapes=preview.affected_shapes,
                )
                answer = QMessageBox.question(
                    self,
                    tr(self.locale, "label.migration.title"),
                    message,
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if answer != QMessageBox.StandardButton.Yes:
                    return
                label_set = self.gateway.apply_label_change(preview).label_set
            self.gateway.update_label_display(
                self.dataset_id,
                self.label.id,
                alias=self.alias_edit.text(),
                description=self.description_edit.toPlainText(),
                synonyms=synonyms,
                color=self.color_edit.text().strip(),
                expected_revision=label_set.revision,
            )
            self.accept()
        except Exception as error:
            self.error_label.setText(str(error))


class ManagedLabelPage(QWidget):
    """数据集级标签的真实表格管理页面。"""

    route_requested = Signal(str)

    def __init__(
        self,
        locale: LocaleService,
        gateway,
        dataset_id: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.locale = locale
        self.gateway = gateway
        self.dataset_id = dataset_id
        self.labels: tuple[Label, ...] = ()
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 24)
        self.header = PageHeader(locale, "page.labels.title", "page.labels.subtitle")
        back = GhostButton(tr(locale, "nav.back"))
        back.clicked.connect(
            lambda: self.route_requested.emit(f"{RouteId.ANNOTATION_WORKSPACE.value}:{dataset_id}")
        )
        self.header.add_action(back)
        root.addWidget(self.header)
        actions = QHBoxLayout()
        self.search = SearchBox()
        self.search.textChanged.connect(self.refresh)
        self.add_button = PrimaryButton(tr(locale, "label.add"))
        self.edit_button = QPushButton(tr(locale, "label.edit"))
        self.status_button = DangerButton(tr(locale, "label.archive"))
        self.inspect_button = GhostButton(tr(locale, "label.inspect"))
        self.add_button.clicked.connect(self._add)
        self.edit_button.clicked.connect(self._edit)
        self.status_button.clicked.connect(self._toggle_status)
        self.inspect_button.clicked.connect(
            lambda: self.route_requested.emit(RouteId.LABEL_INSPECTION.value)
        )
        actions.addWidget(self.search, 1)
        actions.addWidget(self.add_button)
        actions.addWidget(self.edit_button)
        actions.addWidget(self.status_button)
        actions.addWidget(self.inspect_button)
        root.addLayout(actions)
        self.table = QTableWidget()
        self.table.setColumnCount(7)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        for column in (0, 1, 2, 4, 5, 6):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table.cellDoubleClicked.connect(lambda _row, _column: self._edit())
        root.addWidget(self.table, 1)
        self.retranslate_ui()
        self.refresh()

    def selected_label(self) -> Label | None:
        row = self.table.currentRow()
        return self.labels[row] if 0 <= row < len(self.labels) else None

    def refresh(self) -> None:
        self.labels = self.gateway.list_labels(
            self.dataset_id,
            self.search.text(),
            include_archived=True,
        )
        usages = {usage.label_id: usage for usage in self.gateway.label_usages(self.dataset_id)}
        self.table.setRowCount(len(self.labels))
        for row, label in enumerate(self.labels):
            usage = usages.get(label.id)
            values = (
                str(label.class_id),
                label.name,
                label.alias,
                label.description,
                str(usage.image_count if usage else 0),
                label.color,
                tr(self.locale, f"label.status.{label.status.value}"),
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 5:
                    item.setBackground(QColor(label.color))
                self.table.setItem(row, column, item)
        if self.labels:
            self.table.selectRow(0)

    def retranslate_ui(self) -> None:
        self.header.retranslate_ui()
        self.search.setPlaceholderText(tr(self.locale, "label.search"))
        self.table.setHorizontalHeaderLabels(
            [
                tr(self.locale, "label.class_id"),
                tr(self.locale, "label.name"),
                tr(self.locale, "label.alias"),
                tr(self.locale, "label.description"),
                tr(self.locale, "label.usage"),
                tr(self.locale, "label.color"),
                tr(self.locale, "label.status"),
            ]
        )

    def _add(self) -> None:
        dialog = ManagedLabelEditDialog(
            self.locale,
            self.gateway,
            self.dataset_id,
            parent=self,
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.refresh()

    def _edit(self) -> None:
        label = self.selected_label()
        if label is None:
            return
        dialog = ManagedLabelEditDialog(
            self.locale,
            self.gateway,
            self.dataset_id,
            label,
            self,
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.refresh()

    def _toggle_status(self) -> None:
        label = self.selected_label()
        if label is None:
            return
        status = (
            LabelStatus.ACTIVE if label.status == LabelStatus.ARCHIVED else LabelStatus.ARCHIVED
        )
        try:
            label_set = self.gateway.get_label_set(self.dataset_id)
            self.gateway.set_label_status(
                self.dataset_id,
                label.id,
                status,
                expected_revision=label_set.revision,
            )
            self.refresh()
        except ManagedLabelError as error:
            QMessageBox.warning(self, tr(self.locale, "page.labels.title"), str(error))


class ManagedLabelInspectionPage(QWidget):
    """按标签从 SQLite 分页提取图片检查集合。"""

    route_requested = Signal(str)
    workspace_target_requested = Signal(object)

    def __init__(
        self,
        locale: LocaleService,
        gateway,
        dataset_id: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.locale = locale
        self.gateway = gateway
        self.dataset_id = dataset_id
        self.sample_ids: list[str] = []
        self.offset = 0
        self.total = 0
        self.page_size = 200
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 24)
        self.header = PageHeader(locale, "page.inspection.title", "page.inspection.subtitle")
        back = GhostButton(tr(locale, "nav.back"))
        back.clicked.connect(lambda: self.route_requested.emit(RouteId.LABEL_MANAGER.value))
        self.header.add_action(back)
        root.addWidget(self.header)
        self.label_combo = QComboBox()
        self.label_combo.currentIndexChanged.connect(self._label_changed)
        root.addWidget(self.label_combo)
        self.table = QTableWidget()
        self.table.setColumnCount(4)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        inspection_header = self.table.horizontalHeader()
        inspection_header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in (1, 2, 3):
            inspection_header.setSectionResizeMode(
                column,
                QHeaderView.ResizeMode.ResizeToContents,
            )
        self.table.cellDoubleClicked.connect(self._open_sample)
        root.addWidget(self.table, 1)
        pagination = QHBoxLayout()
        self.previous_button = GhostButton("‹")
        self.next_button = GhostButton("›")
        self.page_label = QLabel()
        self.page_label.setObjectName("mutedText")
        self.previous_button.clicked.connect(self._previous_page)
        self.next_button.clicked.connect(self._next_page)
        pagination.addWidget(self.previous_button)
        pagination.addWidget(self.page_label, 1)
        pagination.addWidget(self.next_button)
        root.addLayout(pagination)
        self.retranslate_ui()
        self.refresh()

    def refresh(self) -> None:
        labels = self.gateway.list_labels(self.dataset_id)
        current = self.label_combo.currentData()
        self.label_combo.blockSignals(True)
        self.label_combo.clear()
        for label in labels:
            self.label_combo.addItem(f"{label.alias} · {label.name}", label.id)
        for index in range(self.label_combo.count()):
            if self.label_combo.itemData(index) == current:
                self.label_combo.setCurrentIndex(index)
                break
        self.label_combo.blockSignals(False)
        self._refresh_samples()

    def _refresh_samples(self) -> None:
        label_id = self.label_combo.currentData()
        page = (
            self.gateway.query_samples(
                self.dataset_id,
                offset=self.offset,
                limit=self.page_size,
                label_id=label_id,
            )
            if label_id is not None
            else None
        )
        items = page.items if page is not None else ()
        self.total = page.total if page is not None else 0
        if self.total and self.offset >= self.total:
            self.offset = max(0, ((self.total - 1) // self.page_size) * self.page_size)
            self._refresh_samples()
            return
        self.sample_ids = [sample.id for sample in items]
        self.table.setRowCount(len(items))
        for row, sample in enumerate(items):
            values = (
                sample.filename,
                str(sample.annotation_count),
                tr(
                    self.locale,
                    f"review.{sample.review_status.value}"
                    if sample.review_status is not None
                    else "review.none",
                ),
                _display_timestamp(sample.annotation_updated_at),
            )
            for column, value in enumerate(values):
                self.table.setItem(row, column, QTableWidgetItem(value))
        page_count = max(1, (self.total + self.page_size - 1) // self.page_size)
        current_page = self.offset // self.page_size + 1
        self.page_label.setText(
            tr(self.locale, "browser.page").format(
                page=current_page,
                total=page_count,
                count=self.total,
            )
        )
        self.previous_button.setEnabled(self.offset > 0)
        self.next_button.setEnabled(self.offset + self.page_size < self.total)

    def retranslate_ui(self) -> None:
        self.header.retranslate_ui()
        self.table.setHorizontalHeaderLabels(
            [
                tr(self.locale, "table.file"),
                tr(self.locale, "value.boxes"),
                tr(self.locale, "label.status"),
                tr(self.locale, "label.updated_at"),
            ]
        )
        self.previous_button.setToolTip(tr(self.locale, "browser.previous_page"))
        self.next_button.setToolTip(tr(self.locale, "browser.next_page"))

    def _label_changed(self, _index: int) -> None:
        self.offset = 0
        self._refresh_samples()

    def _previous_page(self) -> None:
        self.offset = max(0, self.offset - self.page_size)
        self._refresh_samples()

    def _next_page(self) -> None:
        if self.offset + self.page_size < self.total:
            self.offset += self.page_size
            self._refresh_samples()

    def _open_sample(self, row: int, _column: int) -> None:
        if 0 <= row < len(self.sample_ids):
            from datumdock.ui.prototype_models import WorkspaceNavigationTarget

            self.workspace_target_requested.emit(
                WorkspaceNavigationTarget(
                    dataset_id=self.dataset_id,
                    sample_id=self.sample_ids[row],
                    focus_label_id=self.label_combo.currentData(),
                    shape_id=None,
                )
            )


def _display_timestamp(value: str) -> str:
    """表格使用本地分钟精度，避免把原始 ISO 字符串挤压其他列。"""

    if not value:
        return "—"
    try:
        return datetime.fromisoformat(value).astimezone().strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return value
