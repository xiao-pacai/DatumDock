"""普通模式真实 YOLO Detection 导出向导。"""

from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (
    QCheckBox,
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
    QSpinBox,
    QTableView,
    QVBoxLayout,
)

from datumdock.i18n.catalog import LocaleService, tr
from datumdock.services.managed_yolo import (
    ExportIssueSeverity,
    ExportScope,
    SplitRatios,
    YoloExportPreflight,
    YoloExportReport,
    YoloExportRequest,
)
from datumdock.services.tasks import TaskState
from datumdock.ui.components import GhostButton, PrimaryButton


class ManagedYoloExportDialog(QDialog):
    """只提交一次性请求；路径、方案和报告关闭后不持久化。"""

    def __init__(
        self,
        locale: LocaleService,
        gateway,
        dataset_id: str,
        *,
        filtered_sample_ids: tuple[str, ...] = (),
        selected_sample_ids: tuple[str, ...] = (),
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.locale = locale
        self.gateway = gateway
        self.dataset_id = dataset_id
        self.filtered_sample_ids = filtered_sample_ids
        self.selected_sample_ids = selected_sample_ids
        self.parent_directory: Path | None = None
        self.preflight_task_id: str | None = None
        self.task_id: str | None = None
        self.preflight: YoloExportPreflight | None = None
        self.report: YoloExportReport | None = None
        self.phase = "configure"
        self.setModal(True)
        self.resize(900, 680)
        self._build_ui()
        self._apply_defaults()
        self.retranslate_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        self.title = QLabel()
        self.title.setObjectName("pageTitle")
        root.addWidget(self.title)

        form = QFormLayout()
        self.scope = QComboBox()
        self.scope.addItem("", ExportScope.ALL)
        if self.filtered_sample_ids:
            self.scope.addItem("", ExportScope.FILTERED)
        if self.selected_sample_ids:
            self.scope.addItem("", ExportScope.SELECTED)
        form.addRow(self._label("scope_label"), self.scope)

        self.completed_negatives = QCheckBox()
        self.unreviewed_negatives = QCheckBox()
        self.pending_review = QCheckBox()
        option_box = QVBoxLayout()
        option_box.addWidget(self.completed_negatives)
        option_box.addWidget(self.unreviewed_negatives)
        option_box.addWidget(self.pending_review)
        form.addRow(self._label("options_label"), option_box)

        ratio_row = QHBoxLayout()
        self.train_ratio = QSpinBox()
        self.val_ratio = QSpinBox()
        self.test_ratio = QSpinBox()
        for field in (self.train_ratio, self.val_ratio, self.test_ratio):
            field.setRange(0, 100)
            ratio_row.addWidget(field)
        self.train_ratio.setMinimum(1)
        form.addRow(self._label("ratios_label"), ratio_row)

        self.seed = QSpinBox()
        self.seed.setRange(-2_147_483_648, 2_147_483_647)
        self.seed.setValue(42)
        form.addRow(self._label("seed_label"), self.seed)

        parent_row = QHBoxLayout()
        self.parent_path = QLineEdit()
        self.parent_path.setReadOnly(True)
        self.choose_parent = QPushButton()
        self.choose_parent.clicked.connect(self._choose_parent)
        parent_row.addWidget(self.parent_path, 1)
        parent_row.addWidget(self.choose_parent)
        form.addRow(self._label("parent_label"), parent_row)
        self.target_name = QLineEdit("datumdock-yolo")
        form.addRow(self._label("name_label"), self.target_name)
        root.addLayout(form)

        self.summary = QLabel()
        self.summary.setObjectName("mutedText")
        self.summary.setWordWrap(True)
        root.addWidget(self.summary)
        self.distribution_model = QStandardItemModel(0, 4, self)
        self.distribution_table = QTableView()
        self.distribution_table.setModel(self.distribution_model)
        self.distribution_table.verticalHeader().setVisible(False)
        distribution_header = self.distribution_table.horizontalHeader()
        distribution_header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in range(1, 4):
            distribution_header.setSectionResizeMode(
                column,
                QHeaderView.ResizeMode.ResizeToContents,
            )
        self.distribution_table.setMaximumHeight(170)
        root.addWidget(self.distribution_table)
        self.issue_model = QStandardItemModel(0, 3, self)
        self.issue_table = QTableView()
        self.issue_table.setModel(self.issue_model)
        self.issue_table.verticalHeader().setVisible(False)
        header = self.issue_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        root.addWidget(self.issue_table, 1)

        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        root.addWidget(self.progress)
        self.status = QLabel()
        self.status.setWordWrap(True)
        root.addWidget(self.status)

        buttons = QHBoxLayout()
        self.preflight_button = PrimaryButton()
        self.preflight_button.clicked.connect(self._start_preflight)
        self.export_button = PrimaryButton()
        self.export_button.setEnabled(False)
        self.export_button.clicked.connect(self._start_export)
        self.cancel_task_button = GhostButton()
        self.cancel_task_button.clicked.connect(self._cancel_task)
        self.cancel_task_button.setEnabled(False)
        self.open_output_button = GhostButton()
        self.open_output_button.clicked.connect(self._open_output)
        self.open_output_button.hide()
        self.close_button = GhostButton()
        self.close_button.clicked.connect(self.reject)
        buttons.addWidget(self.preflight_button)
        buttons.addWidget(self.export_button)
        buttons.addWidget(self.cancel_task_button)
        buttons.addWidget(self.open_output_button)
        buttons.addStretch()
        buttons.addWidget(self.close_button)
        root.addLayout(buttons)

        self.timer = QTimer(self)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self._poll_task)

    def _label(self, name: str) -> QLabel:
        label = QLabel()
        setattr(self, name, label)
        return label

    def _apply_defaults(self) -> None:
        train, val, test = self.gateway.settings.default_split
        self.train_ratio.setValue(train)
        self.val_ratio.setValue(val)
        self.test_ratio.setValue(test)

    def _choose_parent(self) -> None:
        selected = QFileDialog.getExistingDirectory(
            self,
            tr(self.locale, "dialog.yolo.choose_parent"),
        )
        if selected:
            self.parent_directory = Path(selected)
            self.parent_path.setText(selected)
            self.preflight = None
            self.export_button.setEnabled(False)

    def _request(self) -> YoloExportRequest | None:
        values = (
            self.train_ratio.value(),
            self.val_ratio.value(),
            self.test_ratio.value(),
        )
        if sum(values) != 100 or values[0] <= 0:
            self.status.setText(tr(self.locale, "dialog.yolo.ratio_error"))
            return None
        name = self.target_name.text().strip()
        if (
            self.parent_directory is None
            or not name
            or Path(name).name != name
            or name in {".", ".."}
        ):
            self.status.setText(tr(self.locale, "dialog.yolo.target_error"))
            return None
        scope = ExportScope(self.scope.currentData())
        identifiers = {
            ExportScope.ALL: (),
            ExportScope.FILTERED: self.filtered_sample_ids,
            ExportScope.SELECTED: self.selected_sample_ids,
        }[scope]
        return YoloExportRequest(
            self.dataset_id,
            self.parent_directory / name,
            scope,
            identifiers,
            SplitRatios(*values),
            self.seed.value(),
            self.completed_negatives.isChecked(),
            self.unreviewed_negatives.isChecked(),
            self.pending_review.isChecked(),
        )

    def _start_preflight(self) -> None:
        request = self._request()
        if request is None:
            return
        if request.include_unreviewed_negatives:
            choice = QMessageBox.question(
                self,
                tr(self.locale, "dialog.export.title"),
                tr(self.locale, "dialog.yolo.unreviewed_confirm"),
            )
            if choice != QMessageBox.StandardButton.Yes:
                return
        try:
            self.preflight_task_id = self.gateway.start_yolo_export_preflight(request)
        except Exception as error:
            self.status.setText(f"{tr(self.locale, 'dialog.yolo.failed')}: {error}")
            return
        self.task_id = self.preflight_task_id
        self.phase = "preflight"
        self.preflight = None
        self.issue_model.removeRows(0, self.issue_model.rowCount())
        self.export_button.setEnabled(False)
        self._set_running(True)
        self.status.setText(tr(self.locale, "dialog.yolo.preflighting"))
        self.timer.start()

    def _start_export(self) -> None:
        if self.preflight is None or self.preflight_task_id is None:
            return
        try:
            self.task_id = self.gateway.start_yolo_export_commit(
                self.dataset_id,
                self.preflight_task_id,
            )
        except Exception as error:
            self.status.setText(f"{tr(self.locale, 'dialog.yolo.failed')}: {error}")
            return
        self.phase = "export"
        self._set_running(True)
        self.status.setText(tr(self.locale, "dialog.yolo.exporting"))
        self.timer.start()

    def _poll_task(self) -> None:
        if self.task_id is None:
            self.timer.stop()
            return
        snapshot = self.gateway.task_snapshot(self.task_id)
        self.progress.setRange(0, max(1, snapshot.total))
        self.progress.setValue(min(snapshot.completed, max(1, snapshot.total)))
        if snapshot.state in {TaskState.QUEUED, TaskState.RUNNING}:
            return
        self.timer.stop()
        self._set_running(False)
        if snapshot.state == TaskState.FAILED:
            detail = "\n".join(snapshot.errors)
            self.status.setText(f"{tr(self.locale, 'dialog.yolo.failed')}: {detail}")
            return
        result = self.gateway.task_result(self.task_id)
        if snapshot.state == TaskState.CANCELLED:
            self.status.setText(tr(self.locale, "dialog.yolo.cancelled"))
            return
        if self.phase == "preflight" and isinstance(result, YoloExportPreflight):
            self.preflight = result
            self._show_preflight(result)
            return
        if self.phase == "export" and isinstance(result, YoloExportReport):
            self.report = result
            if result.cancelled or snapshot.state == TaskState.CANCELLED:
                self.status.setText(tr(self.locale, "dialog.yolo.cancelled"))
                return
            self.status.setText(
                "\n".join(
                    (
                        tr(self.locale, "dialog.yolo.finished"),
                        tr(self.locale, "dialog.yolo.finished_summary").format(
                            images=result.image_count,
                            labels=result.label_file_count,
                            boxes=result.rectangle_count,
                        ),
                    )
                )
            )
            self.preflight_button.setEnabled(False)
            self.export_button.setEnabled(False)
            self.preflight_button.hide()
            self.export_button.hide()
            self.cancel_task_button.hide()
            self.open_output_button.show()

    def _show_preflight(self, preflight: YoloExportPreflight) -> None:
        rectangle_count = sum(len(candidate.rectangles) for candidate in preflight.candidates)
        group_count = len(preflight.plan.groups) if preflight.plan else 0
        lines = [
            tr(self.locale, "dialog.yolo.summary").format(
                samples=len(preflight.candidates),
                boxes=rectangle_count,
                groups=group_count,
            )
        ]
        if preflight.statistics:
            for bucket in preflight.statistics.buckets:
                lines.append(
                    tr(self.locale, "dialog.yolo.split_summary").format(
                        name=bucket.name,
                        samples=bucket.sample_count,
                        boxes=bucket.rectangle_count,
                        negatives=bucket.negative_count,
                    )
                )
            lines.extend(preflight.statistics.warnings)
        self.summary.setText("\n".join(lines))
        self.issue_model.removeRows(0, self.issue_model.rowCount())
        for issue in preflight.issues:
            severity = tr(
                self.locale,
                "dialog.yolo.severity_error"
                if issue.severity == ExportIssueSeverity.ERROR
                else "dialog.yolo.severity_warning",
            )
            self.issue_model.appendRow(
                [
                    QStandardItem(severity),
                    QStandardItem(issue.filename),
                    QStandardItem(self._localized_issue(issue.code, issue.message)),
                ]
            )
        self._show_distribution(preflight)
        self.export_button.setEnabled(preflight.can_export)
        self.status.setText(
            tr(
                self.locale,
                "dialog.yolo.ready" if preflight.can_export else "dialog.yolo.blocked",
            )
        )

    def _show_distribution(self, preflight: YoloExportPreflight) -> None:
        """按类别 ID 展示各集合图片数，模型视图可承载大标签集。"""

        self.distribution_model.removeRows(0, self.distribution_model.rowCount())
        if preflight.statistics is None:
            return
        label_set = self.gateway.get_label_set(self.dataset_id)
        by_split = {
            bucket.name: dict(bucket.class_image_counts) for bucket in preflight.statistics.buckets
        }
        for label in sorted(label_set.labels, key=lambda item: item.class_id):
            self.distribution_model.appendRow(
                [
                    QStandardItem(f"{label.class_id} · {label.name}"),
                    QStandardItem(str(by_split.get("train", {}).get(label.class_id, 0))),
                    QStandardItem(str(by_split.get("val", {}).get(label.class_id, 0))),
                    QStandardItem(str(by_split.get("test", {}).get(label.class_id, 0))),
                ]
            )

    def _localized_issue(self, code: str, detail: str) -> str:
        key = f"dialog.yolo.issue_{code}"
        translated = tr(self.locale, key)
        if translated == key:
            return detail
        return translated.format(detail=detail)

    def _cancel_task(self) -> None:
        if self.task_id:
            self.gateway.cancel_task(self.task_id)

    def _set_running(self, running: bool) -> None:
        self.preflight_button.setEnabled(not running)
        self.export_button.setEnabled(
            not running and bool(self.preflight and self.preflight.can_export)
        )
        self.cancel_task_button.setEnabled(running)
        self.close_button.setEnabled(not running)

    def _open_output(self) -> None:
        if self.report and self.report.output_directory.is_dir():
            QDesktopServices.openUrl(QUrl.fromLocalFile(os.fspath(self.report.output_directory)))

    def reject(self) -> None:
        if self.task_id:
            snapshot = self.gateway.task_snapshot(self.task_id)
            if snapshot.state in {TaskState.QUEUED, TaskState.RUNNING}:
                self.gateway.cancel_task(self.task_id)
                return
        super().reject()

    def retranslate_ui(self) -> None:
        self.setWindowTitle(tr(self.locale, "dialog.export.title"))
        self.title.setText(tr(self.locale, "dialog.export.title"))
        scope_value = self.scope.currentData()
        for index in range(self.scope.count()):
            value = self.scope.itemData(index)
            key = {
                ExportScope.ALL: "dialog.yolo.scope_all",
                ExportScope.FILTERED: "dialog.yolo.scope_filtered",
                ExportScope.SELECTED: "dialog.yolo.scope_selected",
            }[value]
            self.scope.setItemText(index, tr(self.locale, key))
        for index in range(self.scope.count()):
            if self.scope.itemData(index) == scope_value:
                self.scope.setCurrentIndex(index)
                break
        self.scope_label.setText(tr(self.locale, "dialog.yolo.scope"))
        self.options_label.setText("")
        self.completed_negatives.setText(tr(self.locale, "dialog.yolo.completed_negative"))
        self.unreviewed_negatives.setText(tr(self.locale, "dialog.yolo.unreviewed_negative"))
        self.pending_review.setText(tr(self.locale, "dialog.yolo.pending_review"))
        self.ratios_label.setText(tr(self.locale, "dialog.yolo.ratios"))
        self.seed_label.setText(tr(self.locale, "dialog.yolo.seed"))
        self.parent_label.setText(tr(self.locale, "dialog.yolo.target_parent"))
        self.name_label.setText(tr(self.locale, "dialog.yolo.target_name"))
        self.choose_parent.setText(tr(self.locale, "dialog.yolo.choose_parent"))
        self.preflight_button.setText(tr(self.locale, "dialog.yolo.preflight"))
        self.export_button.setText(tr(self.locale, "dialog.yolo.export"))
        self.cancel_task_button.setText(tr(self.locale, "dialog.yolo.cancel_task"))
        self.close_button.setText(tr(self.locale, "dialog.yolo.close"))
        self.open_output_button.setText(tr(self.locale, "dialog.yolo.open_output"))
        self.issue_model.setHorizontalHeaderLabels(
            [
                tr(self.locale, "dialog.yolo.issue_level"),
                tr(self.locale, "dialog.yolo.issue_file"),
                tr(self.locale, "dialog.yolo.issue_message"),
            ]
        )
        self.distribution_model.setHorizontalHeaderLabels(
            [tr(self.locale, "dialog.yolo.class"), "train", "val", "test"]
        )
