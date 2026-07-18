"""项目级标签集的表格管理界面，兼顾别名、描述、颜色与训练名迁移。"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QColorDialog,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from datumdock.domain.models import Label, LabelStatus, Project
from datumdock.i18n.catalog import LocaleService, tr
from datumdock.services.labelme import LabelMeRepository
from datumdock.services.labels import (
    LabelMigrationService,
    LabelService,
    LabelSetCompatibilityService,
)
from datumdock.services.storage import ProjectIndexRepository
from datumdock.services.workspace import WorkspaceService


class LabelEditorDialog(QDialog):
    """编辑单个标签的中文别名、英文训练名、描述、颜色和类别 ID。"""

    def __init__(
        self,
        locale_service: LocaleService,
        label: Label | None,
        default_class_id: int,
        default_color: str,
        parent: QWidget,
    ) -> None:
        super().__init__(parent)
        self.locale_service = locale_service
        self.original = label
        self.color = label.color if label else default_color
        self.name_input = QLineEdit(label.name if label else "")
        self.alias_input = QLineEdit(label.alias if label else "")
        self.description_input = QLineEdit(label.description if label else "")
        self.class_id_input = QSpinBox()
        self.class_id_input.setRange(0, 99_999)
        self.class_id_input.setValue(label.class_id if label else default_class_id)
        self.color_button = QPushButton()
        self.color_button.clicked.connect(self.choose_color)
        self._build_ui()
        self._update_color_button()

    def _build_ui(self) -> None:
        """建立不依赖具体项目的标签表单。"""

        self.setWindowTitle(
            tr(self.locale_service, "dialog.labels.edit")
            if self.original
            else tr(self.locale_service, "dialog.labels.add")
        )
        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.addRow(tr(self.locale_service, "dialog.labels.name"), self.name_input)
        form.addRow(tr(self.locale_service, "dialog.labels.alias"), self.alias_input)
        form.addRow(tr(self.locale_service, "dialog.labels.description"), self.description_input)
        form.addRow(tr(self.locale_service, "dialog.labels.class_id"), self.class_id_input)
        form.addRow(tr(self.locale_service, "dialog.labels.color"), self.color_button)
        layout.addLayout(form)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def choose_color(self) -> None:
        """颜色选择使用系统对话框，最终写入稳定的十六进制值。"""

        selected = QColorDialog.getColor(QColor(self.color), self)
        if selected.isValid():
            self.color = selected.name().upper()
            self._update_color_button()

    def build_label(self) -> Label:
        """仅在提交时构造领域模型，让 Pydantic 负责训练名格式校验。"""

        values = {
            "class_id": self.class_id_input.value(),
            "name": self.name_input.text().strip(),
            "alias": self.alias_input.text().strip(),
            "description": self.description_input.text().strip(),
            "color": self.color,
        }
        if self.original is not None:
            values["id"] = self.original.id
            values["synonyms"] = self.original.synonyms
            values["status"] = self.original.status
        return Label(**values)

    def _update_color_button(self) -> None:
        """让用户在点击保存前直观看到最终标签颜色。"""

        self.color_button.setText(self.color)
        self.color_button.setStyleSheet(f"background: {self.color}; color: #35403C;")


class LabelInspectionDialog(QDialog):
    """按页展示项目内包含目标标签的样本，点击后由主窗口跳转回原始画布。"""

    sample_open_requested = Signal(str, str, str)
    PAGE_SIZE = 100

    def __init__(
        self,
        locale_service: LocaleService,
        index: ProjectIndexRepository,
        project: Project,
        label: Label,
        parent: QWidget,
    ) -> None:
        super().__init__(parent)
        self.locale_service = locale_service
        self.index = index
        self.project = project
        self.label = label
        self.page = 0
        self.dataset_names = {dataset.id: dataset.name for dataset in project.datasets}
        self.setWindowTitle(
            tr(locale_service, "dialog.labels.inspect_title").format(label=label.alias)
        )
        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        """创建轻量表格和分页控件，不会为了万级项目预加载所有检查结果。"""

        layout = QVBoxLayout(self)
        self.empty_label = QLabel()
        self.empty_label.setWordWrap(True)
        layout.addWidget(self.empty_label)
        self.table = QTableWidget(0, 4)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.cellDoubleClicked.connect(lambda *_: self.open_selected())
        layout.addWidget(self.table)
        controls = QHBoxLayout()
        self.previous_button = QPushButton(tr(self.locale_service, "browser.previous_page"))
        self.previous_button.clicked.connect(self.previous_page)
        self.page_label = QLabel()
        self.page_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.next_button = QPushButton(tr(self.locale_service, "browser.next_page"))
        self.next_button.clicked.connect(self.next_page)
        self.open_button = QPushButton(tr(self.locale_service, "dialog.labels.inspect_open"))
        self.open_button.clicked.connect(self.open_selected)
        controls.addWidget(self.previous_button)
        controls.addWidget(self.page_label, 1)
        controls.addWidget(self.next_button)
        controls.addWidget(self.open_button)
        layout.addLayout(controls)

    def refresh(self) -> None:
        """从项目索引读取当前页，样本稳定 ID 放在不可编辑表格项的用户数据中。"""

        dataset_ids = [dataset.id for dataset in self.project.datasets]
        total = self.index.count_project_label_samples(dataset_ids, self.label.id)
        page_total = max(1, (total + self.PAGE_SIZE - 1) // self.PAGE_SIZE)
        self.page = min(self.page, page_total - 1)
        rows = self.index.list_project_label_samples(
            dataset_ids,
            self.label.id,
            offset=self.page * self.PAGE_SIZE,
            limit=self.PAGE_SIZE,
        )
        self.table.setHorizontalHeaderLabels(
            [
                tr(self.locale_service, "dialog.labels.inspect_dataset"),
                tr(self.locale_service, "dialog.labels.inspect_filename"),
                tr(self.locale_service, "dialog.labels.inspect_shapes"),
                tr(self.locale_service, "dialog.labels.inspect_review"),
            ]
        )
        self.table.setRowCount(len(rows))
        for row, (sample, shape_count) in enumerate(rows):
            values = [
                self.dataset_names.get(sample.dataset_id, sample.dataset_id),
                sample.filename,
                str(shape_count),
                tr(self.locale_service, f"review.{sample.review_status.value}"),
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, (sample.dataset_id, sample.id))
                self.table.setItem(row, column, item)
        self.empty_label.setText(
            tr(self.locale_service, "dialog.labels.inspect_empty") if not rows else ""
        )
        self.previous_button.setEnabled(self.page > 0)
        self.next_button.setEnabled(self.page + 1 < page_total)
        self.open_button.setEnabled(bool(rows))
        self.page_label.setText(
            tr(self.locale_service, "browser.page").format(
                page=self.page + 1,
                total=page_total,
                count=total,
            )
        )
        self.table.resizeColumnsToContents()

    def previous_page(self) -> None:
        """返回上一页标签检查结果。"""

        if self.page > 0:
            self.page -= 1
            self.refresh()

    def next_page(self) -> None:
        """读取下一页标签检查结果。"""

        self.page += 1
        self.refresh()

    def open_selected(self) -> None:
        """将选中样本的稳定 ID 发给主窗口，检查窗口不直接修改项目上下文。"""

        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return
        value = self.table.item(rows[0].row(), 0).data(Qt.ItemDataRole.UserRole)
        if not isinstance(value, tuple) or len(value) != 2:
            return
        self.sample_open_requested.emit(str(value[0]), str(value[1]), self.label.id)
        self.accept()


class LabelManagerDialog(QDialog):
    """集中管理当前项目标签，并在训练名变更时执行可确认的迁移。"""

    labels_changed = Signal()
    sample_inspection_requested = Signal(str, str, str)

    def __init__(
        self,
        locale_service: LocaleService,
        root: Path,
        project: Project,
        parent: QWidget,
    ) -> None:
        super().__init__(parent)
        self.locale_service = locale_service
        self.root = root
        self.project = project
        self.label_service = LabelService()
        self.migration_service = LabelMigrationService(LabelMeRepository())
        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        """上方表格是项目标签的唯一集中编辑入口。"""

        self.setWindowTitle(tr(self.locale_service, "dialog.labels.title"))
        self.resize(820, 460)
        layout = QVBoxLayout(self)
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr(self.locale_service, "label.search"))
        self.search.textChanged.connect(self.refresh)
        layout.addWidget(self.search)
        self.table = QTableWidget(0, 7)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.cellDoubleClicked.connect(lambda *_: self.edit_selected())
        layout.addWidget(self.table)
        buttons_layout = QHBoxLayout()
        self.add_button = QPushButton(tr(self.locale_service, "dialog.labels.add"))
        self.edit_button = QPushButton(tr(self.locale_service, "dialog.labels.edit"))
        self.archive_button = QPushButton(tr(self.locale_service, "dialog.labels.archive"))
        self.merge_button = QPushButton(tr(self.locale_service, "dialog.labels.merge"))
        self.inspect_button = QPushButton(tr(self.locale_service, "dialog.labels.inspect"))
        close_button = QPushButton(tr(self.locale_service, "settings.close"))
        self.add_button.clicked.connect(self.add_label)
        self.edit_button.clicked.connect(self.edit_selected)
        self.archive_button.clicked.connect(self.archive_selected)
        self.merge_button.clicked.connect(self.merge_label_set)
        self.inspect_button.clicked.connect(self.inspect_selected)
        close_button.clicked.connect(self.accept)
        buttons_layout.addWidget(self.add_button)
        buttons_layout.addWidget(self.edit_button)
        buttons_layout.addWidget(self.archive_button)
        buttons_layout.addWidget(self.merge_button)
        buttons_layout.addWidget(self.inspect_button)
        buttons_layout.addStretch()
        buttons_layout.addWidget(close_button)
        layout.addLayout(buttons_layout)

    def refresh(self) -> None:
        """用稳定标签 ID 存在表格隐藏角色中，展示文本可以安全变化。"""

        headers = [
            tr(self.locale_service, "dialog.labels.alias"),
            tr(self.locale_service, "dialog.labels.name"),
            tr(self.locale_service, "dialog.labels.description"),
            tr(self.locale_service, "dialog.labels.color"),
            tr(self.locale_service, "dialog.labels.class_id"),
            tr(self.locale_service, "label.status"),
            tr(self.locale_service, "label.usage"),
        ]
        self.table.setHorizontalHeaderLabels(headers)
        labels = sorted(
            self.label_service.search(self.project.label_set, self.search.text()),
            key=lambda item: item.class_id,
        )
        usage_counts = ProjectIndexRepository(
            WorkspaceService.project_path(self.root, self.project.id) / "project-index.sqlite"
        ).label_usage_counts()
        self.table.setRowCount(len(labels))
        for row, label in enumerate(labels):
            values = [
                label.alias,
                label.name,
                label.description,
                label.color,
                str(label.class_id),
                tr(self.locale_service, f"label.status.{label.status.value}"),
                str(usage_counts.get(label.id, 0)),
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, label.id)
                if column == 3:
                    item.setBackground(QColor(label.color))
                self.table.setItem(row, column, item)
        self.table.resizeColumnsToContents()

    def add_label(self) -> None:
        """新增标签时自动给出下一个类别 ID 和未占用的莫兰迪色。"""

        next_class_id = (
            max((item.class_id for item in self.project.label_set.labels), default=-1) + 1
        )
        dialog = LabelEditorDialog(
            self.locale_service,
            None,
            next_class_id,
            self.label_service.next_color(self.project.label_set),
            self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            self.label_service.add_label(self.project.label_set, dialog.build_label())
            WorkspaceService().save_project(self.root, self.project)
        except (ValueError, OSError, KeyError) as error:
            QMessageBox.warning(self, tr(self.locale_service, "dialog.error"), str(error))
            return
        self.refresh()
        self.labels_changed.emit()

    def edit_selected(self) -> None:
        """编辑标签；训练名变化先展示影响范围，再原子改写关联 JSON。"""

        label = self._selected_label()
        if label is None:
            return
        dialog = LabelEditorDialog(
            self.locale_service,
            label,
            label.class_id,
            label.color,
            self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        original = label.model_copy(deep=True)
        try:
            candidate = dialog.build_label()
            self.label_service.validate_label(self.project.label_set, candidate)
            if candidate.name != label.name:
                index = ProjectIndexRepository(
                    WorkspaceService.project_path(self.root, self.project.id)
                    / "project-index.sqlite"
                )
                preview = self.label_service.preview_name_migration(
                    index,
                    (dataset.id for dataset in self.project.datasets),
                    label.id,
                )
                body = tr(self.locale_service, "dialog.labels.migrate").format(
                    count=preview.sample_count
                )
                confirmed = QMessageBox.question(
                    self,
                    tr(self.locale_service, "dialog.labels.edit"),
                    body,
                )
                if confirmed != QMessageBox.StandardButton.Yes:
                    return
                label.alias = candidate.alias
                label.description = candidate.description
                label.color = candidate.color
                label.class_id = candidate.class_id
                self.migration_service.migrate_training_name(
                    self.root,
                    self.project,
                    label.id,
                    candidate.name,
                )
            else:
                label.alias = candidate.alias
                label.description = candidate.description
                label.color = candidate.color
                label.class_id = candidate.class_id
                WorkspaceService().save_project(self.root, self.project)
        except (ValueError, OSError, KeyError) as error:
            label.id = original.id
            label.class_id = original.class_id
            label.name = original.name
            label.alias = original.alias
            label.description = original.description
            label.synonyms = original.synonyms
            label.color = original.color
            label.status = original.status
            QMessageBox.warning(self, tr(self.locale_service, "dialog.error"), str(error))
            return
        self.refresh()
        self.labels_changed.emit()

    def archive_selected(self) -> None:
        """归档只禁止新建框，不删除历史标签或改写已有标注。"""

        label = self._selected_label()
        if label is None:
            return
        label.status = LabelStatus.ARCHIVED
        try:
            WorkspaceService().save_project(self.root, self.project)
        except OSError as error:
            QMessageBox.warning(self, tr(self.locale_service, "dialog.error"), str(error))
            return
        self.refresh()
        self.labels_changed.emit()

    def merge_label_set(self) -> None:
        """从同一工作区选择来源项目；冲突时保持当前项目标签集完全不变。"""

        workspace = WorkspaceService().open_workspace(self.root)
        candidates = [item for item in workspace.projects if item.id != self.project.id]
        if not candidates:
            QMessageBox.information(
                self,
                tr(self.locale_service, "dialog.labels.merge"),
                tr(self.locale_service, "dialog.labels.merge_no_source"),
            )
            return
        display_names = [f"{item.name} ({item.id[:8]})" for item in candidates]
        choice, accepted = QInputDialog.getItem(
            self,
            tr(self.locale_service, "dialog.labels.merge"),
            tr(self.locale_service, "dialog.labels.merge_choose"),
            display_names,
            editable=False,
        )
        if not accepted:
            return
        selected_index = display_names.index(choice)
        source_project = WorkspaceService().open_project(
            self.root,
            candidates[selected_index].id,
        )
        target_keys = {(label.class_id, label.name) for label in self.project.label_set.labels}
        addition_count = sum(
            (label.class_id, label.name) not in target_keys
            for label in source_project.label_set.labels
        )
        confirmed = QMessageBox.question(
            self,
            tr(self.locale_service, "dialog.labels.merge"),
            tr(self.locale_service, "dialog.labels.merge_preview").format(count=addition_count),
        )
        if confirmed != QMessageBox.StandardButton.Yes:
            return
        original = self.project.label_set.model_copy(deep=True)
        try:
            LabelSetCompatibilityService().merge_into(
                self.project.label_set,
                source_project.label_set,
            )
            WorkspaceService().save_project(self.root, self.project)
        except (OSError, ValueError, KeyError) as error:
            self.project.label_set = original
            QMessageBox.warning(self, tr(self.locale_service, "dialog.error"), str(error))
            return
        self.refresh()
        self.labels_changed.emit()

    def inspect_selected(self) -> None:
        """打开选中标签的项目级检查集合，检索结果始终来自索引而非复制的图片目录。"""

        label = self._selected_label()
        if label is None:
            return
        index = ProjectIndexRepository(
            WorkspaceService.project_path(self.root, self.project.id) / "project-index.sqlite"
        )
        dialog = LabelInspectionDialog(
            self.locale_service,
            index,
            self.project,
            label,
            self,
        )
        dialog.sample_open_requested.connect(self.sample_inspection_requested)
        dialog.exec()

    def _selected_label(self) -> Label | None:
        """从选中行读取稳定 ID，避免相同别名或显示名造成误编辑。"""

        selected_rows = self.table.selectionModel().selectedRows()
        if not selected_rows:
            return None
        label_id = self.table.item(selected_rows[0].row(), 0).data(Qt.ItemDataRole.UserRole)
        return self.project.label_set.get_label(str(label_id))
