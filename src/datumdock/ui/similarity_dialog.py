"""近似图片候选组的确认界面。"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from datumdock.i18n.catalog import LocaleService, tr
from datumdock.services.storage import ProjectIndexRepository


class SimilarityDialog(QDialog):
    """用户可明确确认或取消近似组，未确认候选永远不强制影响导出。"""

    def __init__(
        self,
        locale_service: LocaleService,
        index: ProjectIndexRepository,
        parent: QWidget,
    ) -> None:
        super().__init__(parent)
        self.locale_service = locale_service
        self.index = index
        self.setWindowTitle(tr(locale_service, "dialog.similarity.title"))
        self.resize(720, 420)
        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        """表格按相似组呈现候选图片和是否参与导出绑定。"""

        layout = QVBoxLayout(self)
        explanation = QLabel(tr(self.locale_service, "dialog.similarity.help"))
        explanation.setWordWrap(True)
        layout.addWidget(explanation)
        self.table = QTableWidget(0, 3)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        layout.addWidget(self.table)
        actions = QHBoxLayout()
        confirm = QPushButton(tr(self.locale_service, "dialog.similarity.confirm"))
        unconfirm = QPushButton(tr(self.locale_service, "dialog.similarity.unconfirm"))
        confirm.clicked.connect(lambda: self.set_confirmed(True))
        unconfirm.clicked.connect(lambda: self.set_confirmed(False))
        actions.addWidget(confirm)
        actions.addWidget(unconfirm)
        actions.addStretch()
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(self.accept)
        actions.addWidget(close)
        layout.addLayout(actions)

    def refresh(self) -> None:
        """只读取索引中的候选组，不重新计算哈希或扫描原始图片。"""

        self.table.setHorizontalHeaderLabels(
            [
                tr(self.locale_service, "dialog.similarity.group"),
                tr(self.locale_service, "dialog.similarity.status"),
                tr(self.locale_service, "dialog.similarity.samples"),
            ]
        )
        groups = self.index.list_similarity_groups()
        self.table.setRowCount(len(groups))
        for row, (group_id, (confirmed, samples)) in enumerate(groups.items()):
            group_item = QTableWidgetItem(group_id)
            group_item.setData(Qt.ItemDataRole.UserRole, group_id)
            status_key = "dialog.similarity.confirmed" if confirmed else "dialog.similarity.pending"
            status_item = QTableWidgetItem(tr(self.locale_service, status_key))
            files_item = QTableWidgetItem("\n".join(sample.filename for sample in samples))
            self.table.setItem(row, 0, group_item)
            self.table.setItem(row, 1, status_item)
            self.table.setItem(row, 2, files_item)
        self.table.resizeColumnsToContents()

    def set_confirmed(self, confirmed: bool) -> None:
        """将当前组的确认状态原子写入索引，导出时立即生效。"""

        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return
        group_id = self.table.item(rows[0].row(), 0).data(Qt.ItemDataRole.UserRole)
        try:
            self.index.set_similarity_group_confirmed(str(group_id), confirmed)
        except (OSError, KeyError) as error:
            QMessageBox.warning(self, tr(self.locale_service, "dialog.error"), str(error))
            return
        self.refresh()
