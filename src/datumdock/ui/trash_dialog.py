"""当前项目受管回收站的样本恢复界面。"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
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

from datumdock.domain.models import Project
from datumdock.i18n.catalog import LocaleService, tr
from datumdock.services.dataset import DatasetPoolService


class TrashDialog(QDialog):
    """恢复时复用数据集池服务，保证图片、JSON 与 SQLite 索引一并回到原位置。"""

    samples_restored = Signal()

    def __init__(
        self,
        locale_service: LocaleService,
        root: Path,
        project: Project,
        pool_service: DatasetPoolService,
        parent: QWidget,
    ) -> None:
        super().__init__(parent)
        self.locale_service = locale_service
        self.root = root
        self.project = project
        self.pool_service = pool_service
        self.setWindowTitle(tr(locale_service, "dialog.trash.title"))
        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        """只展示当前项目回收站的可恢复样本，避免跨项目误恢复。"""

        layout = QVBoxLayout(self)
        self.empty_label = QLabel()
        layout.addWidget(self.empty_label)
        self.table = QTableWidget(0, 2)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        layout.addWidget(self.table)
        controls = QHBoxLayout()
        restore = QPushButton(tr(self.locale_service, "dialog.trash.restore"))
        restore.clicked.connect(self.restore_selected)
        controls.addWidget(restore)
        controls.addStretch()
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(self.accept)
        controls.addWidget(close)
        layout.addLayout(controls)

    def refresh(self) -> None:
        """从受管 manifest 刷新列表，不依赖已删除的 SQLite 样本行。"""

        samples = self.pool_service.list_trashed_samples(self.root, self.project)
        self.empty_label.setText(
            tr(self.locale_service, "dialog.trash.empty") if not samples else ""
        )
        self.table.setRowCount(len(samples))
        self.table.setHorizontalHeaderLabels(["ID", tr(self.locale_service, "panel.samples")])
        for row, sample in enumerate(samples):
            identifier = QTableWidgetItem(sample.id)
            identifier.setData(Qt.ItemDataRole.UserRole, sample.id)
            identifier.setFlags(identifier.flags() & ~Qt.ItemFlag.ItemIsEditable)
            filename = QTableWidgetItem(sample.filename)
            filename.setFlags(filename.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(row, 0, identifier)
            self.table.setItem(row, 1, filename)
        self.table.resizeColumnsToContents()

    def restore_selected(self) -> None:
        """恢复失败时保留回收站内容，以便用户处理命名冲突后重试。"""

        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return
        sample_id = self.table.item(rows[0].row(), 0).data(Qt.ItemDataRole.UserRole)
        try:
            self.pool_service.restore_sample(self.root, self.project, str(sample_id))
        except (OSError, ValueError, KeyError) as error:
            QMessageBox.warning(self, tr(self.locale_service, "dialog.error"), str(error))
            return
        self.samples_restored.emit()
        self.refresh()
