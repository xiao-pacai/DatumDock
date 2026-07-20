"""步骤三真实相似图片与回收站管理页面。"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from datumdock.i18n.catalog import LocaleService, tr
from datumdock.resources import resource_root
from datumdock.ui.components import DangerButton, GhostButton, PageHeader, PrimaryButton
from datumdock.ui.icons import IconRegistry
from datumdock.ui.prototype_pages import RouteId


class ManagedGovernancePage(QWidget):
    """管理页只消费网关快照，所有修改继续走统一命令边界。"""

    route_requested = Signal(str)
    command_requested = Signal(str, dict)

    def __init__(
        self,
        locale: LocaleService,
        gateway,
        dataset_id: str,
        kind: str,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.locale = locale
        self.gateway = gateway
        self.dataset_id = dataset_id
        self.kind = kind
        self.icons = IconRegistry(resource_root())
        self.row_ids: list[str] = []
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 24)
        title_key = "page.similarity.title" if kind == "similarity" else "page.trash.title"
        subtitle_key = "page.similarity.subtitle" if kind == "similarity" else "page.trash.subtitle"
        self.header = PageHeader(locale, title_key, subtitle_key)
        back = GhostButton(tr(locale, "nav.back"))
        back.setIcon(self.icons.icon("back"))
        back.clicked.connect(
            lambda: self.route_requested.emit(f"{RouteId.ANNOTATION_WORKSPACE.value}:{dataset_id}")
        )
        self.header.add_action(back)
        root.addWidget(self.header)
        actions = QHBoxLayout()
        self.primary = PrimaryButton()
        self.secondary = QPushButton()
        self.danger = DangerButton()
        if kind == "similarity":
            self.primary.setIcon(self.icons.icon("success"))
            self.secondary.setIcon(self.icons.icon("archive"))
        else:
            self.primary.setIcon(self.icons.icon("restore"))
            self.secondary.setIcon(self.icons.icon("delete_image"))
            self.danger.setIcon(self.icons.icon("trash"))
        actions.addWidget(self.primary)
        actions.addWidget(self.secondary)
        actions.addStretch()
        actions.addWidget(self.danger)
        root.addLayout(actions)
        self.table = QTableWidget()
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        root.addWidget(self.table, 1)
        self.empty = QLabel()
        self.empty.setObjectName("mutedText")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self.empty)
        self._connect_actions()
        self.retranslate_ui()
        self.refresh()

    def _connect_actions(self) -> None:
        if self.kind == "similarity":
            self.primary.clicked.connect(lambda: self._change_similarity("similarity.confirm"))
            self.secondary.clicked.connect(lambda: self._change_similarity("similarity.ignore"))
            self.danger.hide()
        else:
            self.primary.clicked.connect(self._restore_trash)
            self.secondary.clicked.connect(self._delete_trash_item)
            self.danger.clicked.connect(self._empty_trash)

    def refresh(self) -> None:
        self.row_ids.clear()
        if self.kind == "similarity":
            groups = self.gateway.list_similarity_groups(self.dataset_id)
            self.table.setColumnCount(5)
            self.table.setRowCount(len(groups))
            for row, group in enumerate(groups):
                self.row_ids.append(group.id)
                samples = [
                    sample
                    for sample_id in group.sample_ids
                    if (sample := self.gateway.get_sample(self.dataset_id, sample_id)) is not None
                ]
                values = (
                    group.id[:8],
                    str(len(group.sample_ids)),
                    " · ".join(sample.filename for sample in samples[:3]),
                    f"{group.score:.1%}",
                    tr(self.locale, f"similarity.{group.status.value}"),
                )
                for column, value in enumerate(values):
                    item = QTableWidgetItem(value)
                    if column == 2 and samples:
                        try:
                            asset = self.gateway.load_thumbnail(self.dataset_id, samples[0].id)
                            pixmap = QPixmap()
                            if pixmap.loadFromData(asset.data, "PNG"):
                                item.setIcon(QIcon(pixmap))
                        except Exception:
                            pass
                    self.table.setItem(row, column, item)
                self.table.setRowHeight(row, 58)
            self.table.setIconSize(QSize(72, 48))
        else:
            items = self.gateway.list_trash_items(self.dataset_id)
            self.table.setColumnCount(3)
            self.table.setRowCount(len(items))
            for row, item in enumerate(items):
                self.row_ids.append(item.sample.id)
                values = (
                    item.display_filename or item.sample.original_filename,
                    item.trashed_at,
                    tr(self.locale, "value.restorable"),
                )
                for column, value in enumerate(values):
                    self.table.setItem(row, column, QTableWidgetItem(value))
        self.empty.setVisible(not self.row_ids)
        self.table.setVisible(bool(self.row_ids))
        if self.row_ids:
            self.table.selectRow(0)
        self.retranslate_ui()

    def retranslate_ui(self) -> None:
        self.header.retranslate_ui()
        if self.kind == "similarity":
            self.primary.setText(tr(self.locale, "action.confirm_group"))
            self.secondary.setText(tr(self.locale, "action.ignore"))
            self.table.setHorizontalHeaderLabels(
                [
                    tr(self.locale, "table.group"),
                    tr(self.locale, "stats.images"),
                    tr(self.locale, "table.file"),
                    tr(self.locale, "table.similarity"),
                    tr(self.locale, "table.status"),
                ]
            )
            self.empty.setText(tr(self.locale, "similarity.empty"))
        else:
            self.primary.setText(tr(self.locale, "action.restore"))
            self.secondary.setText(tr(self.locale, "dialog.delete.title"))
            self.danger.setText(tr(self.locale, "action.empty_trash"))
            self.table.setHorizontalHeaderLabels(
                [
                    tr(self.locale, "table.file"),
                    tr(self.locale, "table.deleted_at"),
                    tr(self.locale, "table.status"),
                ]
            )
            self.empty.setText(tr(self.locale, "trash.empty"))
        for column in range(max(0, self.table.columnCount() - 1)):
            self.table.horizontalHeader().setSectionResizeMode(
                column, QHeaderView.ResizeMode.ResizeToContents
            )

    def _selected_id(self) -> str | None:
        row = self.table.currentRow()
        return self.row_ids[row] if 0 <= row < len(self.row_ids) else None

    def _change_similarity(self, action: str) -> None:
        group_id = self._selected_id()
        if group_id is None:
            return
        self.command_requested.emit(
            action,
            {"dataset_id": self.dataset_id, "group_id": group_id},
        )
        self.refresh()

    def _restore_trash(self) -> None:
        sample_id = self._selected_id()
        if sample_id is None:
            return
        self.command_requested.emit(
            "trash.restore",
            {"dataset_id": self.dataset_id, "sample_id": sample_id},
        )
        self.refresh()

    def _delete_trash_item(self) -> None:
        sample_id = self._selected_id()
        if sample_id is None:
            return
        if not self._confirm_permanent():
            return
        self.command_requested.emit(
            "trash.delete_permanent",
            {"dataset_id": self.dataset_id, "sample_id": sample_id},
        )
        self.refresh()

    def _empty_trash(self) -> None:
        if not self.row_ids or not self._confirm_permanent():
            return
        for sample_id in tuple(self.row_ids):
            self.command_requested.emit(
                "trash.delete_permanent",
                {"dataset_id": self.dataset_id, "sample_id": sample_id},
            )
        self.refresh()

    def _confirm_permanent(self) -> bool:
        result = QMessageBox.warning(
            self,
            tr(self.locale, "dialog.delete.final_title"),
            tr(self.locale, "dialog.delete.final_body"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return result == QMessageBox.StandardButton.Yes
