"""步骤五正式 X-AnyLabeling/LabelMe 导入与导出向导。"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from datumdock.i18n.catalog import LocaleService, tr
from datumdock.services.image_pool import DuplicateDecision
from datumdock.services.managed_interop import (
    ExternalLabelAction,
    ExternalLabelDecision,
    InteropIssue,
    InteropIssueSeverity,
    XAnyExportRequest,
    XAnyExportScope,
    XAnyImportCommitRequest,
    XAnyImportPreflight,
)
from datumdock.services.tasks import TaskState
from datumdock.ui.components import GhostButton, PrimaryButton
from datumdock.ui.managed_media_dialogs import DuplicateDecisionDialog


def _localized_issue(locale: LocaleService, issue: InteropIssue) -> str:
    """按稳定问题代码本地化系统说明，外部文件名和标签内容保持原样。"""

    key = f"dialog.xany.issue_{issue.code}"
    message = tr(locale, key)
    if message == key:
        return issue.message
    if "{detail}" in message:
        return message.format(detail=issue.message)
    return message


class ManagedXAnyImportDialog(QDialog):
    """在正式模式中完成目录预检、标签映射、重复决定和提交。"""

    import_finished = Signal(str)

    def __init__(self, locale: LocaleService, gateway, dataset_id: str, parent=None) -> None:
        super().__init__(parent)
        self.locale = locale
        self.gateway = gateway
        self.dataset_id = dataset_id
        self.source_directory: Path | None = None
        self.task_id: str | None = None
        self.preflight: XAnyImportPreflight | None = None
        self.phase = "configure"
        self.mapping_combos: dict[str, QComboBox] = {}
        self.setWindowTitle(tr(locale, "dialog.xany.import_title"))
        self.setModal(True)
        self.resize(920, 680)
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        title = QLabel(tr(self.locale, "dialog.xany.import_title"))
        title.setObjectName("pageTitle")
        root.addWidget(title)
        notice = QLabel(tr(self.locale, "dialog.xany.readonly_notice"))
        notice.setWordWrap(True)
        notice.setObjectName("previewBanner")
        root.addWidget(notice)
        source_row = QHBoxLayout()
        self.source = QLineEdit()
        self.source.setReadOnly(True)
        choose = QPushButton(tr(self.locale, "dialog.xany.choose_source"))
        choose.clicked.connect(self._choose_source)
        source_row.addWidget(self.source, 1)
        source_row.addWidget(choose)
        root.addLayout(source_row)
        self.issue_table = QTableWidget(0, 3)
        self.issue_table.setHorizontalHeaderLabels(
            [
                tr(self.locale, "dialog.xany.issue_level"),
                tr(self.locale, "dialog.xany.issue_file"),
                tr(self.locale, "dialog.xany.issue_detail"),
            ]
        )
        self.issue_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        root.addWidget(self.issue_table, 1)
        self.mapping_table = QTableWidget(0, 3)
        self.mapping_table.setHorizontalHeaderLabels(
            [
                tr(self.locale, "dialog.xany.external_label"),
                tr(self.locale, "dialog.xany.shape_count"),
                tr(self.locale, "dialog.xany.mapping"),
            ]
        )
        self.mapping_table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.Stretch
        )
        self.mapping_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents
        )
        self.mapping_table.verticalHeader().setDefaultSectionSize(44)
        self.mapping_table.setMinimumHeight(220)
        self.mapping_table.hide()
        root.addWidget(self.mapping_table, 1)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        root.addWidget(self.progress)
        self.status = QLabel(tr(self.locale, "dialog.xany.choose_source_hint"))
        self.status.setWordWrap(True)
        self.status.setObjectName("mutedText")
        root.addWidget(self.status)
        controls = QHBoxLayout()
        self.cancel_button = GhostButton(tr(self.locale, "action.cancel"))
        self.start_button = PrimaryButton(tr(self.locale, "dialog.xany.preflight"))
        self.start_button.setEnabled(False)
        self.cancel_button.clicked.connect(self._cancel_or_close)
        self.start_button.clicked.connect(self._advance)
        controls.addWidget(self.cancel_button)
        controls.addStretch()
        controls.addWidget(self.start_button)
        root.addLayout(controls)
        self.poll_timer = QTimer(self)
        self.poll_timer.setInterval(80)
        self.poll_timer.timeout.connect(self._poll)

    def _choose_source(self) -> None:
        value = QFileDialog.getExistingDirectory(
            self,
            tr(self.locale, "dialog.xany.choose_source"),
        )
        if value:
            self.source_directory = Path(value)
            self.source.setText(value)
            self.start_button.setEnabled(True)

    def _advance(self) -> None:
        if self.phase == "configure":
            self._start_preflight()
        elif self.phase == "review":
            self._start_commit()
        else:
            self.accept()

    def _start_preflight(self) -> None:
        if self.source_directory is None:
            return
        try:
            self.task_id = self.gateway.start_xany_import_preflight(
                self.dataset_id,
                self.source_directory,
            )
        except Exception as error:
            self.status.setText(str(error))
            return
        self.phase = "preflight"
        self.start_button.setEnabled(False)
        self.progress.setRange(0, 0)
        self.status.setText(tr(self.locale, "dialog.xany.preflighting"))
        self.poll_timer.start()

    def _poll(self) -> None:
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
        if self.phase == "preflight":
            self.preflight = result
            self._show_preflight()
        else:
            self._show_report(result)

    def _show_preflight(self) -> None:
        if self.preflight is None:
            return
        self.issue_table.setRowCount(len(self.preflight.issues))
        for row, issue in enumerate(self.preflight.issues):
            self.issue_table.setItem(
                row,
                0,
                QTableWidgetItem(
                    tr(self.locale, f"dialog.xany.issue_severity_{issue.severity.value}")
                ),
            )
            self.issue_table.setItem(row, 1, QTableWidgetItem(issue.relative_path))
            self.issue_table.setItem(row, 2, QTableWidgetItem(_localized_issue(self.locale, issue)))
        labels = self.gateway.list_labels(self.dataset_id, include_archived=False)
        self.mapping_table.setRowCount(len(self.preflight.external_labels))
        self.mapping_combos.clear()
        for row, reference in enumerate(self.preflight.external_labels):
            self.mapping_table.setItem(row, 0, QTableWidgetItem(reference.name))
            self.mapping_table.setItem(row, 1, QTableWidgetItem(str(reference.shape_count)))
            combo = QComboBox()
            combo.setMinimumHeight(34)
            combo.setSizeAdjustPolicy(
                QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
            )
            combo.setMinimumContentsLength(28)
            combo.addItem(tr(self.locale, "dialog.xany.preserve_readonly"), None)
            proposed = reference.proposed_training_name or reference.name
            combo.addItem(
                f"{tr(self.locale, 'dialog.xany.create_label')} · {proposed}",
                "__create__",
            )
            for label in labels:
                combo.addItem(f"{label.alias} · {label.name}", label.id)
            if reference.matched_label_id:
                index = combo.findData(reference.matched_label_id)
                combo.setCurrentIndex(index if index >= 0 else 0)
            elif reference.archived_label_id:
                combo.setCurrentIndex(0)
                combo.setToolTip(tr(self.locale, "dialog.xany.archived_conflict"))
            else:
                # 未知外部标签默认新建，避免导入后矩形变成不可编辑兼容负载。
                combo.setCurrentIndex(1)
            self.mapping_table.setCellWidget(row, 2, combo)
            self.mapping_combos[reference.name] = combo
        self.mapping_table.show()
        self.phase = "review"
        self.progress.setRange(0, 1)
        self.progress.setValue(1)
        self.start_button.setText(tr(self.locale, "dialog.xany.start_import"))
        self.start_button.setEnabled(bool(self.preflight.items))
        errors = sum(
            issue.severity == InteropIssueSeverity.ERROR for issue in self.preflight.issues
        )
        self.status.setText(
            tr(self.locale, "dialog.xany.preflight_summary").format(
                images=self.preflight.discovered_image_count,
                errors=errors,
            )
        )

    def _start_commit(self) -> None:
        if self.preflight is None:
            return
        duplicate_decisions: dict[str, DuplicateDecision] = {}
        items_by_id = {item.image.id: item for item in self.preflight.items}
        for item in self.preflight.items:
            if not item.image.requires_duplicate_decision or item.blocking_issues:
                continue
            existing: list[tuple[bytes, str]] = []
            for sample_id in item.image.existing_duplicate_ids:
                asset = self.gateway.load_image(self.dataset_id, sample_id)
                sample = self.gateway.get_sample(self.dataset_id, sample_id)
                existing.append((asset.data, sample.filename if sample else sample_id))
            for prepared_id in item.image.batch_duplicate_ids:
                existing.append(
                    (
                        self.gateway.load_xany_prepared_image(
                            self.dataset_id,
                            self.preflight.session_id,
                            prepared_id,
                        ),
                        items_by_id[prepared_id].image.original_filename,
                    )
                )
            dialog = DuplicateDecisionDialog(
                self.locale,
                self.gateway.load_xany_prepared_image(
                    self.dataset_id,
                    self.preflight.session_id,
                    item.image.id,
                ),
                item.image.original_filename,
                tuple(existing),
                self,
            )
            if dialog.exec() != QDialog.DialogCode.Accepted or dialog.decision is None:
                return
            duplicate_decisions[item.image.id] = dialog.decision
        try:
            label_decisions = self._collect_label_decisions()
            request = XAnyImportCommitRequest(
                dataset_id=self.dataset_id,
                preflight=self.preflight,
                duplicate_decisions=duplicate_decisions,
                label_decisions=label_decisions,
            )
            self.task_id = self.gateway.start_xany_import_commit(request)
        except Exception as error:
            self.status.setText(str(error))
            return
        self.phase = "commit"
        self.start_button.setEnabled(False)
        self.progress.setRange(0, max(1, len(self.preflight.items)))
        self.status.setText(tr(self.locale, "dialog.xany.importing"))
        self.poll_timer.start()

    def _collect_label_decisions(self) -> tuple[ExternalLabelDecision, ...]:
        """Qt 只收集用户意图；稳定 ID、类别 ID、名称和颜色全部由 Service 生成。"""

        decisions: list[ExternalLabelDecision] = []
        for external_name, combo in self.mapping_combos.items():
            target = combo.currentData()
            if target == "__create__":
                decisions.append(ExternalLabelDecision(external_name, ExternalLabelAction.CREATE))
            elif target is None:
                decisions.append(
                    ExternalLabelDecision(external_name, ExternalLabelAction.PRESERVE_READONLY)
                )
            else:
                decisions.append(
                    ExternalLabelDecision(external_name, ExternalLabelAction.MAP, str(target))
                )
        return tuple(decisions)

    def _show_report(self, report) -> None:
        self.phase = "done"
        self.start_button.setText(tr(self.locale, "action.close"))
        self.start_button.setEnabled(True)
        self.cancel_button.hide()
        self.status.setText(
            tr(self.locale, "dialog.xany.import_report").format(
                imported=len(report.imported_sample_ids),
                skipped=len(report.skipped_item_ids),
                failed=len(report.failures),
                compatibility=report.compatibility_shape_count,
            )
        )
        if report.imported_sample_ids:
            self.import_finished.emit(report.imported_sample_ids[0])

    def _cancel_or_close(self) -> None:
        if self.task_id:
            snapshot = self.gateway.task_snapshot(self.task_id)
            if snapshot.state in {TaskState.QUEUED, TaskState.RUNNING}:
                self.gateway.cancel_task(self.task_id)
                self.status.setText(tr(self.locale, "dialog.xany.cancelling"))
                return
        if self.preflight is not None and self.phase in {"review", "preflight"}:
            self.gateway.discard_xany_import_preflight(
                self.dataset_id,
                self.preflight.session_id,
            )
        self.reject()

    def retranslate_ui(self) -> None:
        """重开向导时使用新语言；任务中的领域内容不被翻译。"""

        self.setWindowTitle(tr(self.locale, "dialog.xany.import_title"))


class ManagedXAnyExportDialog(QDialog):
    """生成新交换目录，并在完整复验成功后一次发布。"""

    def __init__(
        self,
        locale: LocaleService,
        gateway,
        dataset_id: str,
        sample_ids: tuple[str, ...] = (),
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.locale = locale
        self.gateway = gateway
        self.dataset_id = dataset_id
        self.sample_ids = sample_ids
        self.parent_directory: Path | None = None
        self.task_id: str | None = None
        self.preflight_task_id: str | None = None
        self.phase = "configure"
        self.setWindowTitle(tr(locale, "dialog.xany.export_title"))
        self.setModal(True)
        self.resize(760, 520)
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        title = QLabel(tr(self.locale, "dialog.xany.export_title"))
        title.setObjectName("pageTitle")
        root.addWidget(title)
        form = QFormLayout()
        parent_row = QHBoxLayout()
        self.parent_path = QLineEdit()
        self.parent_path.setReadOnly(True)
        choose = QPushButton(tr(self.locale, "dialog.xany.choose_target_parent"))
        choose.clicked.connect(self._choose_parent)
        parent_row.addWidget(self.parent_path, 1)
        parent_row.addWidget(choose)
        form.addRow(tr(self.locale, "dialog.xany.target_parent"), parent_row)
        self.folder_name = QLineEdit("DatumDock-XAny")
        form.addRow(tr(self.locale, "dialog.xany.folder_name"), self.folder_name)
        self.scope = QComboBox()
        self.scope.addItem(tr(self.locale, "dialog.xany.scope_all"), "all")
        if self.sample_ids:
            self.scope.addItem(
                tr(self.locale, "dialog.xany.scope_selected").format(count=len(self.sample_ids)),
                "selected",
            )
        form.addRow(tr(self.locale, "dialog.xany.scope"), self.scope)
        root.addLayout(form)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        root.addWidget(self.progress)
        self.status = QLabel(tr(self.locale, "dialog.xany.export_hint"))
        self.status.setWordWrap(True)
        self.status.setObjectName("mutedText")
        root.addWidget(self.status, 1)
        controls = QHBoxLayout()
        cancel = GhostButton(tr(self.locale, "action.cancel"))
        self.start = PrimaryButton(tr(self.locale, "dialog.xany.preflight"))
        cancel.clicked.connect(self._cancel_or_close)
        self.start.clicked.connect(self._advance)
        controls.addWidget(cancel)
        controls.addStretch()
        controls.addWidget(self.start)
        root.addLayout(controls)
        self.poll_timer = QTimer(self)
        self.poll_timer.setInterval(80)
        self.poll_timer.timeout.connect(self._poll)

    def _choose_parent(self) -> None:
        value = QFileDialog.getExistingDirectory(
            self,
            tr(self.locale, "dialog.xany.choose_target_parent"),
        )
        if value:
            self.parent_directory = Path(value)
            self.parent_path.setText(value)

    def _advance(self) -> None:
        if self.phase == "configure":
            self._start_preflight()
        elif self.phase == "review":
            self._start_export()
        else:
            self.accept()

    def _target(self) -> Path:
        if self.parent_directory is None:
            raise ValueError(tr(self.locale, "dialog.xany.choose_target_parent"))
        name = self.folder_name.text().strip()
        if not name or Path(name).name != name or name in {".", ".."}:
            raise ValueError(tr(self.locale, "dialog.xany.invalid_folder_name"))
        return self.parent_directory / name

    def _start_preflight(self) -> None:
        try:
            selected = self.scope.currentData() == "selected"
            request = XAnyExportRequest(
                self.dataset_id,
                self._target(),
                sample_ids=self.sample_ids if selected else (),
                scope=XAnyExportScope.SELECTED if selected else XAnyExportScope.ALL,
            )
            self.task_id = self.gateway.start_xany_export_preflight(request)
            self.preflight_task_id = self.task_id
        except Exception as error:
            self.status.setText(str(error))
            return
        self.phase = "preflight"
        self.start.setEnabled(False)
        self.progress.setRange(0, 0)
        self.poll_timer.start()

    def _poll(self) -> None:
        if self.task_id is None:
            return
        snapshot = self.gateway.task_snapshot(self.task_id)
        if snapshot.total:
            self.progress.setRange(0, snapshot.total)
            self.progress.setValue(snapshot.completed)
        if snapshot.state in {TaskState.QUEUED, TaskState.RUNNING}:
            return
        self.poll_timer.stop()
        result = self.gateway.task_result(self.task_id)
        if snapshot.state == TaskState.FAILED or result is None:
            self.status.setText("\n".join(snapshot.errors) or tr(self.locale, "dialog.error.title"))
            return
        if self.phase == "preflight":
            if not result.can_export:
                self.status.setText("\n".join(issue.message for issue in result.issues))
                return
            self.phase = "review"
            self.start.setText(tr(self.locale, "dialog.xany.start_export"))
            self.start.setEnabled(True)
            self.status.setText(
                tr(self.locale, "dialog.xany.export_preflight_summary").format(
                    images=len(result.sample_ids),
                    annotated=result.annotated_count,
                    empty=result.empty_count,
                )
            )
        else:
            self.phase = "done"
            self.start.setText(tr(self.locale, "action.close"))
            self.start.setEnabled(True)
            self.status.setText(
                tr(self.locale, "dialog.xany.export_report").format(
                    images=result.image_count,
                    rectangles=result.rectangle_count,
                    empty=result.empty_annotation_count,
                )
            )

    def _start_export(self) -> None:
        if self.preflight_task_id is None:
            return
        try:
            self.task_id = self.gateway.start_xany_export_commit(
                self.dataset_id,
                self.preflight_task_id,
            )
        except Exception as error:
            self.status.setText(str(error))
            return
        self.phase = "export"
        self.start.setEnabled(False)
        self.status.setText(tr(self.locale, "dialog.xany.exporting"))
        self.poll_timer.start()

    def _cancel_or_close(self) -> None:
        if self.task_id:
            snapshot = self.gateway.task_snapshot(self.task_id)
            if snapshot.state in {TaskState.QUEUED, TaskState.RUNNING}:
                answer = QMessageBox.question(
                    self,
                    tr(self.locale, "dialog.xany.cancel_title"),
                    tr(self.locale, "dialog.xany.cancel_body"),
                )
                if answer == QMessageBox.StandardButton.Yes:
                    self.gateway.cancel_task(self.task_id)
                return
        self.reject()

    def retranslate_ui(self) -> None:
        self.setWindowTitle(tr(self.locale, "dialog.xany.export_title"))
