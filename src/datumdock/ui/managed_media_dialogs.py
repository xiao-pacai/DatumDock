"""步骤三真实图片导入、重复决策、重命名和删除对话框。"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from datumdock.domain.models import NamingPolicy
from datumdock.i18n.catalog import LocaleService, tr
from datumdock.services.image_pool import DuplicateDecision, ImageImportPreflight
from datumdock.services.tasks import TaskState
from datumdock.ui.components import GhostButton, PrimaryButton


class DuplicateDecisionDialog(QDialog):
    """完全重复图片不提供静默默认值，关闭等同于取消整个决策流程。"""

    def __init__(
        self,
        locale: LocaleService,
        pending_data: bytes,
        pending_name: str,
        existing_items: tuple[tuple[bytes, str], ...],
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.locale = locale
        self.decision: DuplicateDecision | None = None
        self.existing_items = existing_items
        self.existing_index = 0
        self.setWindowTitle(tr(locale, "dialog.duplicate.title"))
        self.setModal(True)
        self.resize(860, 560)
        root = QVBoxLayout(self)
        body = QLabel(tr(locale, "dialog.duplicate.body"))
        body.setWordWrap(True)
        root.addWidget(body)
        compare = QHBoxLayout()
        compare.addWidget(self._image_card(pending_data, pending_name))
        self.existing_preview = self._image_card(*existing_items[0])
        compare.addWidget(self.existing_preview)
        root.addLayout(compare, 1)
        matches = QLabel(tr(locale, "dialog.duplicate.matches").format(count=len(existing_items)))
        matches.setObjectName("mutedText")
        root.addWidget(matches)
        navigation = QHBoxLayout()
        self.previous_match = GhostButton("‹")
        self.next_match = GhostButton("›")
        self.match_position = QLabel()
        self.match_position.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.previous_match.clicked.connect(lambda: self._move_match(-1))
        self.next_match.clicked.connect(lambda: self._move_match(1))
        navigation.addStretch()
        navigation.addWidget(self.previous_match)
        navigation.addWidget(self.match_position)
        navigation.addWidget(self.next_match)
        navigation.addStretch()
        root.addLayout(navigation)
        controls = QHBoxLayout()
        cancel = GhostButton(tr(locale, "action.cancel"))
        skip = QPushButton(tr(locale, "dialog.duplicate.skip"))
        keep = PrimaryButton(tr(locale, "dialog.duplicate.keep"))
        cancel.clicked.connect(self.reject)
        skip.clicked.connect(lambda: self._choose(DuplicateDecision.SKIP))
        keep.clicked.connect(lambda: self._choose(DuplicateDecision.KEEP))
        skip.setAutoDefault(False)
        keep.setAutoDefault(False)
        controls.addWidget(cancel)
        controls.addStretch()
        controls.addWidget(skip)
        controls.addWidget(keep)
        root.addLayout(controls)
        self._refresh_match()

    @staticmethod
    def _image_card(data: bytes, filename: str) -> QLabel:
        pixmap = QPixmap()
        pixmap.loadFromData(data, "PNG")
        label = QLabel()
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setPixmap(
            pixmap.scaled(
                380,
                400,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )
        label.setToolTip(filename)
        return label

    def _choose(self, decision: DuplicateDecision) -> None:
        self.decision = decision
        self.accept()

    def _move_match(self, delta: int) -> None:
        self.existing_index = max(
            0,
            min(len(self.existing_items) - 1, self.existing_index + delta),
        )
        self._refresh_match()

    def _refresh_match(self) -> None:
        data, filename = self.existing_items[self.existing_index]
        pixmap = QPixmap()
        pixmap.loadFromData(data, "PNG")
        self.existing_preview.setPixmap(
            pixmap.scaled(
                380,
                400,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )
        self.existing_preview.setToolTip(filename)
        self.match_position.setText(
            f"{self.existing_index + 1} / {len(self.existing_items)} · {filename}"
        )
        self.previous_match.setEnabled(self.existing_index > 0)
        self.next_match.setEnabled(self.existing_index + 1 < len(self.existing_items))


class ManagedImageImportDialog(QDialog):
    """导入流程只通过网关启动后台任务并轮询不可变状态。"""

    import_finished = Signal(str)

    def __init__(
        self,
        locale: LocaleService,
        gateway,
        dataset_id: str,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.locale = locale
        self.gateway = gateway
        self.dataset_id = dataset_id
        self.sources: list[Path] = []
        self.task_id: str | None = None
        self.preflight: ImageImportPreflight | None = None
        self._phase = "configure"
        self.setWindowTitle(tr(locale, "dialog.import.title"))
        self.setModal(True)
        self.resize(760, 560)
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        title = QLabel(tr(self.locale, "dialog.import.title"))
        title.setObjectName("pageTitle")
        root.addWidget(title)
        notice = QLabel(tr(self.locale, "dialog.import.managed_notice"))
        notice.setWordWrap(True)
        notice.setObjectName("previewBanner")
        root.addWidget(notice)
        add_row = QHBoxLayout()
        self.add_files = QPushButton(tr(self.locale, "dialog.import.add_files"))
        self.add_folder = QPushButton(tr(self.locale, "dialog.import.add_folder"))
        self.add_files.clicked.connect(self._choose_files)
        self.add_folder.clicked.connect(self._choose_folder)
        add_row.addWidget(self.add_files)
        add_row.addWidget(self.add_folder)
        add_row.addStretch()
        root.addLayout(add_row)
        self.source_list = QListWidget()
        root.addWidget(self.source_list, 1)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        root.addWidget(self.progress)
        self.status = QLabel(tr(self.locale, "dialog.import.choose_sources"))
        self.status.setObjectName("mutedText")
        self.status.setWordWrap(True)
        root.addWidget(self.status)
        controls = QHBoxLayout()
        self.cancel_button = GhostButton(tr(self.locale, "action.cancel"))
        self.start_button = PrimaryButton(tr(self.locale, "dialog.import.start"))
        self.start_button.setEnabled(False)
        self.cancel_button.clicked.connect(self._cancel_or_close)
        self.start_button.clicked.connect(self._start_preflight)
        controls.addWidget(self.cancel_button)
        controls.addStretch()
        controls.addWidget(self.start_button)
        root.addLayout(controls)
        self.poll_timer = QTimer(self)
        self.poll_timer.setInterval(80)
        self.poll_timer.timeout.connect(self._poll_task)

    def _choose_files(self) -> None:
        paths, _selected_filter = QFileDialog.getOpenFileNames(
            self,
            tr(self.locale, "dialog.import.add_files"),
            "",
            tr(self.locale, "dialog.import.filter"),
        )
        self._add_sources(Path(path) for path in paths)

    def _choose_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self,
            tr(self.locale, "dialog.import.add_folder"),
        )
        if path:
            self._add_sources((Path(path),))

    def _add_sources(self, paths) -> None:
        known = {str(path.resolve(strict=False)).casefold() for path in self.sources}
        for path in paths:
            key = str(path.resolve(strict=False)).casefold()
            if key in known:
                continue
            known.add(key)
            self.sources.append(path)
            self.source_list.addItem(str(path))
        self.start_button.setEnabled(bool(self.sources))

    def _start_preflight(self) -> None:
        if not self.sources:
            self.status.setText(tr(self.locale, "dialog.import.choose_sources"))
            return
        try:
            self.task_id = self.gateway.start_import_preflight(self.dataset_id, tuple(self.sources))
        except Exception as error:
            self.status.setText(str(error))
            return
        self._phase = "preflight"
        self.start_button.setEnabled(False)
        self.add_files.setEnabled(False)
        self.add_folder.setEnabled(False)
        self.progress.setRange(0, 0)
        self.status.setText(tr(self.locale, "dialog.import.preparing"))
        self.poll_timer.start()

    def _poll_task(self) -> None:
        if self.task_id is None:
            return
        snapshot = self.gateway.task_snapshot(self.task_id)
        if snapshot.total:
            self.progress.setRange(0, snapshot.total)
            self.progress.setValue(snapshot.completed)
        if snapshot.current_item:
            self.status.setText(snapshot.current_item)
        if snapshot.state in {TaskState.QUEUED, TaskState.RUNNING}:
            return
        self.poll_timer.stop()
        result = self.gateway.task_result(self.task_id)
        if snapshot.state == TaskState.FAILED or result is None:
            self.status.setText("\n".join(snapshot.errors) or tr(self.locale, "dialog.error.title"))
            self.cancel_button.setText(tr(self.locale, "action.close"))
            return
        if self._phase == "preflight":
            self.preflight = result
            if snapshot.state == TaskState.CANCELLED:
                self.gateway.discard_import_preflight(self.dataset_id, self.preflight.session_id)
                self.status.setText(tr(self.locale, "dialog.import.cancelled"))
                self.cancel_button.setText(tr(self.locale, "action.close"))
                return
            if not self._collect_duplicate_decisions():
                return
        else:
            self._finish_commit(result)

    def _collect_duplicate_decisions(self) -> bool:
        if self.preflight is None:
            return False
        decisions: dict[str, DuplicateDecision] = {}
        prepared_by_id = {item.id: item for item in self.preflight.items}
        for item in self.preflight.items:
            if not item.requires_duplicate_decision:
                continue
            pending = self.gateway.load_prepared_image(
                self.dataset_id, self.preflight.session_id, item.id
            )
            existing_ids = item.existing_duplicate_ids
            existing_items: list[tuple[bytes, str]] = []
            for existing_id in existing_ids:
                asset = self.gateway.load_image(self.dataset_id, existing_id)
                sample = self.gateway.get_sample(self.dataset_id, existing_id)
                existing_items.append(
                    (asset.data, sample.filename if sample is not None else existing_id)
                )
            for reference_id in item.batch_duplicate_ids:
                existing_items.append(
                    (
                        self.gateway.load_prepared_image(
                            self.dataset_id,
                            self.preflight.session_id,
                            reference_id,
                        ),
                        prepared_by_id[reference_id].original_filename,
                    )
                )
            dialog = DuplicateDecisionDialog(
                self.locale,
                pending,
                item.original_filename,
                tuple(existing_items),
                self,
            )
            if dialog.exec() != QDialog.DialogCode.Accepted or dialog.decision is None:
                self.gateway.discard_import_preflight(self.dataset_id, self.preflight.session_id)
                self.status.setText(tr(self.locale, "dialog.import.cancelled"))
                self.cancel_button.setText(tr(self.locale, "action.close"))
                return False
            decisions[item.id] = dialog.decision
        try:
            self.task_id = self.gateway.start_import_commit(
                self.dataset_id,
                self.preflight.session_id,
                decisions,
            )
        except Exception as error:
            self.status.setText(str(error))
            return False
        self._phase = "commit"
        self.progress.setRange(0, max(1, len(self.preflight.items)))
        self.status.setText(tr(self.locale, "dialog.import.committing"))
        self.poll_timer.start()
        return True

    def _finish_commit(self, report) -> None:
        summary = tr(self.locale, "dialog.import.summary").format(
            imported=len(report.imported_sample_ids),
            skipped=len(report.skipped_item_ids),
            failed=len(report.failures),
        )
        self.status.setText(summary)
        self.progress.setValue(self.progress.maximum())
        self.cancel_button.setText(tr(self.locale, "action.close"))
        self.start_button.hide()
        if report.imported_sample_ids:
            self.import_finished.emit(report.imported_sample_ids[0])

    def _cancel_or_close(self) -> None:
        if self.task_id:
            snapshot = self.gateway.task_snapshot(self.task_id)
            if snapshot.state in {TaskState.QUEUED, TaskState.RUNNING}:
                self.gateway.cancel_task(self.task_id)
                self.status.setText(tr(self.locale, "dialog.import.cancelling"))
                return
        if self.preflight is not None and self._phase == "preflight":
            self.gateway.discard_import_preflight(self.dataset_id, self.preflight.session_id)
        self.reject()


class ManagedRenameDialog(QDialog):
    """先展示真实冲突预览，确认后才发送受管重命名命令。"""

    command_ready = Signal(str, dict)

    def __init__(self, locale: LocaleService, gateway, dataset_id: str, parent=None) -> None:
        super().__init__(parent)
        self.locale = locale
        self.gateway = gateway
        self.dataset_id = dataset_id
        self.setWindowTitle(tr(locale, "dialog.rename.title"))
        self.resize(720, 520)
        root = QVBoxLayout(self)
        form = QFormLayout()
        self.prefix_input = QLineEdit("image")
        self.start = QSpinBox()
        self.start.setRange(0, 999999999)
        self.start.setValue(1)
        self.padding = QSpinBox()
        self.padding.setRange(1, 12)
        self.padding.setValue(6)
        form.addRow(tr(locale, "dialog.rename.prefix"), self.prefix_input)
        form.addRow(tr(locale, "dialog.rename.start"), self.start)
        form.addRow(tr(locale, "dialog.rename.padding"), self.padding)
        root.addLayout(form)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(
            [
                tr(locale, "table.file"),
                tr(locale, "dialog.rename.preview"),
                tr(locale, "table.status"),
            ]
        )
        root.addWidget(self.table, 1)
        controls = QHBoxLayout()
        cancel = GhostButton(tr(locale, "action.cancel"))
        preview = QPushButton(tr(locale, "action.preview"))
        apply_button = PrimaryButton(tr(locale, "action.confirm"))
        cancel.clicked.connect(self.reject)
        preview.clicked.connect(self._preview)
        apply_button.clicked.connect(self._apply)
        controls.addWidget(cancel)
        controls.addStretch()
        controls.addWidget(preview)
        controls.addWidget(apply_button)
        root.addLayout(controls)
        self._plan = None
        self._preview()

    def _policy(self) -> NamingPolicy:
        return NamingPolicy(
            prefix=self.prefix_input.text().strip(),
            start_index=self.start.value(),
            padding=self.padding.value(),
        )

    def _preview(self) -> None:
        try:
            self._plan = self.gateway.preview_rename(self.dataset_id, self._policy())
        except Exception as error:
            QMessageBox.critical(self, tr(self.locale, "dialog.error.title"), str(error))
            return
        self.table.setRowCount(len(self._plan.items))
        for row, item in enumerate(self._plan.items):
            self.table.setItem(row, 0, QTableWidgetItem(item.old_filename))
            self.table.setItem(row, 1, QTableWidgetItem(item.new_filename))
            self.table.setItem(row, 2, QTableWidgetItem(item.conflict or "✓"))

    def _apply(self) -> None:
        self._preview()
        if self._plan is None or not self._plan.valid:
            return
        self.command_ready.emit(
            "sample.rename",
            {
                "dataset_id": self.dataset_id,
                "policy": self._policy().model_dump(mode="json"),
            },
        )
        self.accept()


class ManagedTaskCenterDialog(QDialog):
    """轮询后端任务状态，文件级错误保留在会话内可查看。"""

    def __init__(self, locale: LocaleService, gateway, parent=None) -> None:
        super().__init__(parent)
        self.locale = locale
        self.gateway = gateway
        self.task_ids: list[str] = []
        self.setWindowTitle(tr(locale, "dialog.task.title"))
        self.resize(760, 460)
        root = QVBoxLayout(self)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            [
                tr(locale, "form.task"),
                tr(locale, "form.dataset_id"),
                tr(locale, "form.status"),
                tr(locale, "dialog.progress"),
                tr(locale, "form.details"),
            ]
        )
        root.addWidget(self.table, 1)
        controls = QHBoxLayout()
        self.cancel_task_button = QPushButton(tr(locale, "action.cancel_task"))
        close = PrimaryButton(tr(locale, "action.close"))
        self.cancel_task_button.clicked.connect(self._cancel_selected)
        close.clicked.connect(self.accept)
        controls.addWidget(self.cancel_task_button)
        controls.addStretch()
        controls.addWidget(close)
        root.addLayout(controls)
        self.timer = QTimer(self)
        self.timer.setInterval(250)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()
        self.refresh()

    def refresh(self) -> None:
        snapshots = self.gateway.task_snapshots()
        self.task_ids = [snapshot.id for snapshot in snapshots]
        self.table.setRowCount(len(snapshots))
        for row, snapshot in enumerate(snapshots):
            progress = f"{snapshot.completed} / {snapshot.total}" if snapshot.total else "—"
            values = (
                snapshot.kind,
                snapshot.dataset_id[:8],
                tr(self.locale, f"task.{snapshot.state.value}"),
                progress,
                "\n".join(snapshot.errors),
            )
            for column, value in enumerate(values):
                self.table.setItem(row, column, QTableWidgetItem(value))
        active = any(
            snapshot.state in {TaskState.QUEUED, TaskState.RUNNING} for snapshot in snapshots
        )
        self.cancel_task_button.setEnabled(active and self.table.currentRow() >= 0)

    def _cancel_selected(self) -> None:
        row = self.table.currentRow()
        if 0 <= row < len(self.task_ids):
            self.gateway.cancel_task(self.task_ids[row])
            self.refresh()
