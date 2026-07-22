"""响应式快速标签选择窗口。"""

from __future__ import annotations

from PySide6.QtCore import (
    QAbstractListModel,
    QEvent,
    QModelIndex,
    QPoint,
    QSize,
    QSortFilterProxyModel,
    Qt,
    Signal,
)
from PySide6.QtGui import QColor, QFontMetrics, QKeyEvent, QPainter, QPen
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QListView,
    QPushButton,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QVBoxLayout,
    QWidget,
)

from datumdock.domain.models import Label
from datumdock.i18n.catalog import LocaleService, tr
from datumdock.services.shortcuts import ActionBindingManager, ActionRegistry
from datumdock.ui.components import PrimaryButton, SearchBox
from datumdock.ui.managed_label_pages import ManagedLabelEditDialog
from datumdock.ui.prototype_models import CommandStatus, UiCommand
from datumdock.ui.theme import THEME


class QuickLabelListModel(QAbstractListModel):
    """只保存标签快照，不为每个标签创建 QWidget。"""

    LabelRole = Qt.ItemDataRole.UserRole + 1
    SearchRole = Qt.ItemDataRole.UserRole + 2

    def __init__(self, labels: tuple[Label, ...], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.labels = labels

    def rowCount(self, parent: QModelIndex | None = None) -> int:
        return 0 if parent is not None and parent.isValid() else len(self.labels)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self.labels):
            return None
        label = self.labels[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            return f"{label.alias} · {label.name}"
        if role == Qt.ItemDataRole.ToolTipRole:
            return label.description or f"{label.alias} · {label.name}"
        if role == self.LabelRole:
            return label
        if role == self.SearchRole:
            return " ".join(
                (label.alias, label.name, label.description, *label.synonyms)
            ).casefold()
        return None

    def replace_labels(self, labels: tuple[Label, ...]) -> None:
        self.beginResetModel()
        self.labels = labels
        self.endResetModel()


class QuickLabelFilterModel(QSortFilterProxyModel):
    """搜索别名、训练名、描述和同义词。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._needles: tuple[str, ...] = ()

    def set_search_text(self, text: str) -> None:
        """按空白拆分搜索词，并把特殊字符当作普通文本处理。"""

        self._needles = tuple(part for part in text.casefold().split() if part)
        self.invalidate()

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:
        if not self._needles:
            return True
        source = self.sourceModel().index(source_row, 0, source_parent)
        haystack = str(source.data(QuickLabelListModel.SearchRole))
        return all(needle in haystack for needle in self._needles)


def _quick_label_card_style(state: QStyle.StateFlag) -> tuple[QColor, QColor, int]:
    """按交互状态返回卡片样式，确保悬停、焦点和选中层级一致。"""

    selected = bool(state & QStyle.StateFlag.State_Selected)
    hovered = bool(state & QStyle.StateFlag.State_MouseOver)
    focused = bool(state & QStyle.StateFlag.State_HasFocus)
    background = QColor(
        THEME.tokens.brand_soft
        if selected
        else THEME.tokens.surface_hover
        if hovered or focused
        else THEME.tokens.surface
    )
    border = QColor(
        THEME.tokens.brand_primary
        if selected
        else THEME.tokens.focus_ring
        if hovered or focused
        else THEME.tokens.border
    )
    return background, border, 2 if selected or focused else 1


class QuickLabelDelegate(QStyledItemDelegate):
    """绘制带颜色、文字和非颜色选中标记的响应式卡片。"""

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index: QModelIndex,
    ) -> None:
        label = index.data(QuickLabelListModel.LabelRole)
        if not isinstance(label, Label):
            return
        rect = option.rect.adjusted(5, 5, -5, -5)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        background, border_color, border_width = _quick_label_card_style(option.state)
        painter.setBrush(background)
        painter.setPen(QPen(border_color, border_width))
        painter.drawRoundedRect(rect, 10, 10)
        if selected:
            painter.setPen(QPen(QColor(THEME.tokens.brand_primary), 3))
            painter.drawLine(rect.topLeft() + QPoint(3, 10), rect.bottomLeft() + QPoint(3, -10))
        swatch = rect.adjusted(12, 13, -rect.width() + 34, -rect.height() + 35)
        painter.setBrush(QColor(label.color))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(swatch, 5, 5)
        text_rect = rect.adjusted(48, 9, -12, -9)
        painter.setPen(QColor(THEME.tokens.text_primary))
        font = painter.font()
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(
            text_rect,
            Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextSingleLine,
            label.alias,
        )
        font.setBold(False)
        painter.setFont(font)
        painter.setPen(QColor(THEME.tokens.text_secondary))
        training_rect = text_rect.adjusted(0, 24, 0, 0)
        painter.drawText(
            training_rect,
            Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextSingleLine,
            label.name,
        )
        if option.rect.height() >= 96 and label.description:
            painter.setPen(QColor(THEME.tokens.text_muted))
            description_rect = text_rect.adjusted(0, 48, 0, 0)
            description = QFontMetrics(painter.font()).elidedText(
                label.description,
                Qt.TextElideMode.ElideRight,
                max(1, description_rect.width()),
            )
            painter.drawText(
                description_rect,
                Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextSingleLine,
                description,
            )
        if selected:
            painter.setPen(QPen(QColor(THEME.tokens.brand_primary), 2))
            painter.drawText(
                rect.adjusted(0, 0, -10, -8),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom,
                "✓",
            )
        painter.restore()

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:
        return QSize(220, 106)


class QuickLabelSelectorDialog(QDialog):
    """双击矩形后确认改派；取消永远不会改变标注文档。"""

    size_save_failed = Signal()

    def __init__(
        self,
        locale: LocaleService,
        gateway,
        action_registry: ActionRegistry,
        dataset_id: str,
        sample_id: str,
        shape_id: str,
        current_label_id: str,
        document_version: int,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.locale = locale
        self.gateway = gateway
        self.dataset_id = dataset_id
        self.sample_id = sample_id
        self.shape_id = shape_id
        self.current_label_id = current_label_id
        self.document_version = document_version
        self.selected_label_id: str | None = None
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setMinimumSize(560, 420)
        size = getattr(gateway, "settings", None)
        width, height = getattr(size, "quick_label_dialog_size", (760, 520))
        screen = (
            QApplication.screenAt(parent.mapToGlobal(parent.rect().center())) if parent else None
        )
        screen = screen or QApplication.primaryScreen()
        if screen is not None:
            available = screen.availableGeometry().adjusted(20, 20, -20, -20)
            width = min(width, available.width())
            height = min(height, available.height())
        self.resize(max(560, width), max(420, height))
        self._build_ui()
        self.bindings = ActionBindingManager(action_registry, self)
        self.bindings.bind("app.focus_search", self.search.setFocus)
        self._reload_labels(current_label_id)
        self.retranslate_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 14)
        root.setSpacing(12)
        top = QHBoxLayout()
        self.search = SearchBox()
        self.search.textChanged.connect(self._filter)
        self.search.returnPressed.connect(self.accept)
        self.search.installEventFilter(self)
        self.create_label = QPushButton()
        self.create_label.clicked.connect(self._create_label)
        top.addWidget(self.search, 1)
        top.addWidget(self.create_label)
        root.addLayout(top)
        self.source_model = QuickLabelListModel(())
        self.proxy_model = QuickLabelFilterModel(self)
        self.proxy_model.setSourceModel(self.source_model)
        self.label_view = QListView()
        self.label_view.setMouseTracking(True)
        self.label_view.setModel(self.proxy_model)
        self.label_view.setItemDelegate(QuickLabelDelegate(self.label_view))
        self.label_view.setViewMode(QListView.ViewMode.IconMode)
        self.label_view.setResizeMode(QListView.ResizeMode.Adjust)
        self.label_view.setMovement(QListView.Movement.Static)
        self.label_view.setWrapping(True)
        self.label_view.setUniformItemSizes(True)
        self.label_view.doubleClicked.connect(lambda _index: self.accept())
        self.label_view.activated.connect(lambda _index: self.accept())
        root.addWidget(self.label_view, 1)
        self.empty_label = QLabel()
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_label.setObjectName("mutedText")
        self.empty_label.hide()
        root.addWidget(self.empty_label)
        self.buttons = QDialogButtonBox()
        self.confirm_button = PrimaryButton()
        self.confirm_button.setDefault(True)
        self.cancel_button = QPushButton()
        self.confirm_button.clicked.connect(self.accept)
        self.cancel_button.clicked.connect(self.reject)
        self.buttons.addButton(self.confirm_button, QDialogButtonBox.ButtonRole.AcceptRole)
        self.buttons.addButton(self.cancel_button, QDialogButtonBox.ButtonRole.RejectRole)
        root.addWidget(self.buttons)

    def _reload_labels(self, select_label_id: str | None = None) -> None:
        labels = tuple(
            label
            for label in self.gateway.list_labels(
                self.dataset_id,
                include_archived=False,
            )
        )
        self.source_model.replace_labels(labels)
        self._update_grid()
        if select_label_id:
            for row, label in enumerate(labels):
                if label.id == select_label_id:
                    source = self.source_model.index(row, 0)
                    proxy = self.proxy_model.mapFromSource(source)
                    self.label_view.setCurrentIndex(proxy)
                    break
        if not self.label_view.currentIndex().isValid() and self.proxy_model.rowCount():
            self.label_view.setCurrentIndex(self.proxy_model.index(0, 0))

    def _filter(self, text: str) -> None:
        self.proxy_model.set_search_text(text)
        self.empty_label.setVisible(self.proxy_model.rowCount() == 0)
        if self.proxy_model.rowCount() and not self.label_view.currentIndex().isValid():
            self.label_view.setCurrentIndex(self.proxy_model.index(0, 0))

    def _create_label(self) -> None:
        dialog = ManagedLabelEditDialog(
            self.locale,
            self.gateway,
            self.dataset_id,
            parent=self,
        )
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.saved_label_id:
            self.search.clear()
            self._reload_labels(dialog.saved_label_id)

    def accept(self) -> None:
        index = self.label_view.currentIndex()
        label = index.data(QuickLabelListModel.LabelRole) if index.isValid() else None
        if not isinstance(label, Label):
            return
        fresh = {item.id: item for item in self.gateway.list_labels(self.dataset_id)}
        if label.id not in fresh or fresh[label.id].status.value != "active":
            self._reload_labels()
            return
        self.selected_label_id = label.id
        super().accept()

    def done(self, result: int) -> None:
        command = UiCommand(
            "settings.update",
            {"quick_label_dialog_size": (self.width(), self.height())},
        )
        response = self.gateway.dispatch(command)
        if response.status not in {CommandStatus.APPLIED, CommandStatus.PREVIEW_APPLIED}:
            self.size_save_failed.emit()
        super().done(result)

    def resizeEvent(self, event) -> None:
        self._update_grid()
        super().resizeEvent(event)

    def eventFilter(self, watched, event) -> bool:
        """搜索框中的上下键进入候选区，避免用户必须先点击卡片。"""

        if watched is self.search and event.type() == QEvent.Type.KeyPress:
            key_event = event if isinstance(event, QKeyEvent) else None
            if key_event is not None and key_event.key() in {Qt.Key.Key_Down, Qt.Key.Key_Up}:
                count = self.proxy_model.rowCount()
                if count:
                    row = 0 if key_event.key() == Qt.Key.Key_Down else count - 1
                    self.label_view.setCurrentIndex(self.proxy_model.index(row, 0))
                    self.label_view.setFocus()
                return True
        return super().eventFilter(watched, event)

    def _update_grid(self) -> None:
        if not hasattr(self, "label_view"):
            return
        width = max(1, self.label_view.viewport().width())
        columns = max(1, width // 230)
        card_width = max(180, (width - 12 * columns) // columns)
        self.label_view.setGridSize(QSize(card_width, 116))

    def retranslate_ui(self) -> None:
        self.setWindowTitle(tr(self.locale, "quick_label.title"))
        self.search.setPlaceholderText(tr(self.locale, "quick_label.search"))
        self.create_label.setText(tr(self.locale, "quick_label.create"))
        self.empty_label.setText(tr(self.locale, "quick_label.empty"))
        self.confirm_button.setText(tr(self.locale, "action.confirm"))
        self.cancel_button.setText(tr(self.locale, "action.cancel"))
