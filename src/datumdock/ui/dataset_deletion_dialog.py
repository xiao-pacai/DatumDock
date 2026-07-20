"""整数据集永久删除的真实影响预检与双重确认对话框。"""

from __future__ import annotations

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import (
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QTextEdit,
    QVBoxLayout,
)

from datumdock.i18n.catalog import LocaleService, tr
from datumdock.services.dataset_deletion import (
    DatasetDeletionPreflight,
    DatasetDeletionReport,
    DatasetDeletionRequest,
    DatasetDeletionStatus,
)
from datumdock.services.tasks import TaskState
from datumdock.ui.components import DangerButton, GhostButton
from datumdock.ui.managed_gateway import ManagedDatasetGateway


class ManagedDatasetDeletionDialog(QDialog):
    """危险操作只有真实预检完成且名称精确匹配后才允许提交。"""

    deletion_finished = Signal(str)

    def __init__(
        self,
        locale: LocaleService,
        gateway: ManagedDatasetGateway,
        dataset_id: str,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.locale = locale
        self.gateway = gateway
        self.dataset_id = dataset_id
        self.preflight: DatasetDeletionPreflight | None = None
        self.task_id = gateway.start_dataset_deletion_preflight(dataset_id)
        self.commit_started = False
        self.setModal(True)
        self.setWindowTitle(tr(locale, "dialog.dataset_delete.title"))
        self.setMinimumSize(680, 540)
        self.resize(760, 620)

        root = QVBoxLayout(self)
        warning = QLabel(tr(locale, "dialog.dataset_delete.warning"))
        warning.setObjectName("dangerText")
        warning.setWordWrap(True)
        root.addWidget(warning)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        root.addWidget(self.progress)
        self.summary = QLabel(tr(locale, "dialog.dataset_delete.loading"))
        self.summary.setWordWrap(True)
        root.addWidget(self.summary)
        self.impact = QFormLayout()
        root.addLayout(self.impact)
        self.diagnostics = QTextEdit()
        self.diagnostics.setReadOnly(True)
        self.diagnostics.setVisible(False)
        self.diagnostics.setMaximumHeight(120)
        root.addWidget(self.diagnostics)
        self.name_input = QLineEdit()
        self.name_input.setEnabled(False)
        self.name_input.textChanged.connect(self._update_delete_enabled)
        root.addWidget(QLabel(tr(locale, "dialog.dataset_delete.type_name")))
        root.addWidget(self.name_input)
        controls = QHBoxLayout()
        cancel = GhostButton(tr(locale, "action.cancel"))
        cancel.clicked.connect(self.reject)
        self.delete_button = DangerButton(tr(locale, "dialog.dataset_delete.action"))
        self.delete_button.setEnabled(False)
        self.delete_button.clicked.connect(self._confirm_final)
        controls.addWidget(cancel)
        controls.addStretch()
        controls.addWidget(self.delete_button)
        root.addLayout(controls)

        self.timer = QTimer(self)
        self.timer.setInterval(80)
        self.timer.timeout.connect(self._poll)
        self.timer.start()

    def _poll(self) -> None:
        snapshot = self.gateway.task_snapshot(self.task_id)
        if snapshot.state in {TaskState.QUEUED, TaskState.RUNNING}:
            return
        self.timer.stop()
        result = self.gateway.task_result(self.task_id)
        if snapshot.state == TaskState.FAILED or result is None:
            self._show_failure("\n".join(snapshot.errors) or snapshot.phase)
            return
        if self.commit_started:
            if not isinstance(result, DatasetDeletionReport):
                self._show_failure(tr(self.locale, "dialog.dataset_delete.failed"))
                return
            if result.status == DatasetDeletionStatus.COMPLETED:
                self.deletion_finished.emit(result.dataset_id)
                self.accept()
                return
            self._show_failure(result.diagnostic)
            return
        if not isinstance(result, DatasetDeletionPreflight):
            self._show_failure(tr(self.locale, "dialog.dataset_delete.failed"))
            return
        self.preflight = result
        self.progress.setRange(0, 1)
        self.progress.setValue(1)
        self.summary.setText(
            tr(self.locale, "dialog.dataset_delete.summary").format(name=result.dataset_name)
        )
        values = (
            ("dialog.dataset_delete.images", result.impact.image_count),
            ("dialog.dataset_delete.annotations", result.impact.annotation_file_count),
            ("dialog.dataset_delete.rectangles", result.impact.rectangle_count),
            ("dialog.dataset_delete.labels", result.impact.label_count),
            ("dialog.dataset_delete.models", result.impact.model_file_count),
            ("dialog.dataset_delete.files", result.impact.total_file_count),
            (
                "dialog.dataset_delete.space",
                f"{result.impact.total_bytes / 1024 / 1024:.2f} MB",
            ),
        )
        for key, value in values:
            self.impact.addRow(tr(self.locale, key), QLabel(str(value)))
        if result.blockers:
            self.diagnostics.setPlainText("\n".join(result.blockers))
            self.diagnostics.setVisible(True)
        self.name_input.setEnabled(result.can_delete)
        self.name_input.setPlaceholderText(result.dataset_name)
        self._update_delete_enabled()

    def _confirm_final(self) -> None:
        if self.preflight is None or self.name_input.text() != self.preflight.dataset_name:
            return
        answer = QMessageBox.warning(
            self,
            tr(self.locale, "dialog.dataset_delete.final_title"),
            tr(self.locale, "dialog.dataset_delete.final_body").format(
                name=self.preflight.dataset_name
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self.task_id = self.gateway.start_dataset_deletion(
                DatasetDeletionRequest(self.preflight, self.name_input.text(), True)
            )
        except Exception as error:
            self._show_failure(str(error))
            return
        self.commit_started = True
        self.name_input.setEnabled(False)
        self.delete_button.setEnabled(False)
        self.progress.setRange(0, 0)
        self.timer.start()

    def _update_delete_enabled(self) -> None:
        self.delete_button.setEnabled(
            self.preflight is not None
            and self.preflight.can_delete
            and self.name_input.text() == self.preflight.dataset_name
            and not self.commit_started
        )

    def _show_failure(self, message: str) -> None:
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.summary.setText(tr(self.locale, "dialog.dataset_delete.failed"))
        self.diagnostics.setPlainText(message)
        self.diagnostics.setVisible(True)

    def reject(self) -> None:
        """提交阶段不可取消；预检阶段取消只丢弃内存快照。"""

        if self.commit_started:
            return
        if self.preflight is not None:
            self.gateway.discard_dataset_deletion_preflight(self.preflight.id)
        if self.timer.isActive():
            self.gateway.cancel_task(self.task_id)
        super().reject()
