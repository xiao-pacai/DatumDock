"""项目模型库、类别映射与自动标注范围选择界面。"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from datumdock.domain.models import ModelEntry, Project
from datumdock.i18n.catalog import LocaleService, tr
from datumdock.services.models import ModelImportService


class ModelMappingDialog(QDialog):
    """把模型类别显式映射到当前项目标签，未映射类别不会生成框。"""

    def __init__(
        self,
        locale_service: LocaleService,
        project: Project,
        entry: ModelEntry,
        parent: QWidget,
    ) -> None:
        super().__init__(parent)
        self.locale_service = locale_service
        self.project = project
        self.entry = entry
        self.combos: dict[str, QComboBox] = {}
        self.setWindowTitle(tr(locale_service, "dialog.models.mapping"))
        self._build_ui()

    def _build_ui(self) -> None:
        """每个模型类别一行，选择器显示别名、训练名和描述。"""

        layout = QVBoxLayout(self)
        self.table = QTableWidget(len(self.entry.model_classes), 2)
        self.table.setHorizontalHeaderLabels(
            [
                tr(self.locale_service, "dialog.models.mapping.class"),
                tr(self.locale_service, "dialog.models.mapping.label"),
            ]
        )
        labels = [label for label in self.project.label_set.labels if label.status == "active"]
        for row, model_class in enumerate(self.entry.model_classes):
            class_item = QTableWidgetItem(model_class)
            class_item.setFlags(class_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(row, 0, class_item)
            combo = QComboBox()
            combo.addItem(tr(self.locale_service, "dialog.models.mapping.none"), None)
            for label in labels:
                combo.addItem(f"{label.alias} · {label.name} — {label.description}", label.id)
            mapped_id = self.entry.label_mapping.get(str(row))
            for index in range(combo.count()):
                if combo.itemData(index) == mapped_id:
                    combo.setCurrentIndex(index)
                    break
            self.table.setCellWidget(row, 1, combo)
            self.combos[str(row)] = combo
        self.table.resizeColumnsToContents()
        layout.addWidget(self.table)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def mapping(self) -> dict[str, str]:
        """收集非空映射，空项代表用户明确跳过该模型类别。"""

        return {
            model_class: str(combo.currentData())
            for model_class, combo in self.combos.items()
            if combo.currentData() is not None
        }


class ModelManagerDialog(QDialog):
    """以项目为隔离边界管理模型，并把自动标注请求交给主窗口执行。"""

    auto_annotation_requested = Signal(object, str)

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
        self.model_service = ModelImportService()
        self.entries: dict[str, ModelEntry] = {}
        self.setWindowTitle(tr(locale_service, "dialog.models.title"))
        self.resize(820, 450)
        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        """模型表与导入、映射、删除、三种自动标注范围操作共用同一选择。"""

        layout = QVBoxLayout(self)
        self.table = QTableWidget(0, 4)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        layout.addWidget(self.table)
        management = QHBoxLayout()
        import_button = QPushButton(tr(self.locale_service, "dialog.models.import"))
        update_button = QPushButton(tr(self.locale_service, "dialog.models.update"))
        mapping_button = QPushButton(tr(self.locale_service, "dialog.models.mapping"))
        delete_button = QPushButton(tr(self.locale_service, "dialog.models.delete"))
        import_button.clicked.connect(self.import_model)
        update_button.clicked.connect(self.update_model)
        mapping_button.clicked.connect(self.configure_mapping)
        delete_button.clicked.connect(self.delete_model)
        management.addWidget(import_button)
        management.addWidget(update_button)
        management.addWidget(mapping_button)
        management.addWidget(delete_button)
        management.addStretch()
        layout.addLayout(management)
        annotation = QHBoxLayout()
        current = QPushButton(tr(self.locale_service, "dialog.models.current"))
        all_samples = QPushButton(tr(self.locale_service, "dialog.models.all"))
        unannotated = QPushButton(tr(self.locale_service, "dialog.models.unannotated"))
        current.clicked.connect(lambda: self.request_auto_annotation("current"))
        all_samples.clicked.connect(lambda: self.request_auto_annotation("all"))
        unannotated.clicked.connect(lambda: self.request_auto_annotation("unannotated"))
        annotation.addWidget(current)
        annotation.addWidget(all_samples)
        annotation.addWidget(unannotated)
        annotation.addStretch()
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(self.accept)
        annotation.addWidget(close)
        layout.addLayout(annotation)

    def refresh(self) -> None:
        """从当前项目 models 目录读取元数据，二进制不离开受管目录。"""

        entries = self.model_service.list_models(self.root, self.project)
        self.entries = {entry.id: entry for entry in entries}
        self.table.setHorizontalHeaderLabels(
            [
                tr(self.locale_service, "dialog.models.name"),
                tr(self.locale_service, "dialog.models.format"),
                tr(self.locale_service, "dialog.models.classes"),
                tr(self.locale_service, "dialog.models.status"),
            ]
        )
        self.table.setRowCount(len(entries))
        for row, entry in enumerate(entries):
            name = QTableWidgetItem(entry.display_name)
            name.setData(Qt.ItemDataRole.UserRole, entry.id)
            name.setFlags(name.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(row, 0, name)
            self.table.setItem(row, 1, QTableWidgetItem(entry.format.upper()))
            self.table.setItem(row, 2, QTableWidgetItem(str(len(entry.model_classes))))
            self.table.setItem(row, 3, QTableWidgetItem(entry.status))
        self.table.resizeColumnsToContents()

    def import_model(self) -> None:
        """先验证模型格式再复制到当前项目模型目录，源文件绝不被移动。"""

        path, _ = QFileDialog.getOpenFileName(
            self,
            tr(self.locale_service, "dialog.models.import"),
            "",
            tr(self.locale_service, "dialog.models.filter"),
        )
        if not path:
            return
        try:
            self.model_service.import_model(self.root, self.project, Path(path))
        except (OSError, ValueError, RuntimeError) as error:
            QMessageBox.warning(self, tr(self.locale_service, "dialog.error"), str(error))
            return
        self.refresh()

    def configure_mapping(self) -> None:
        """保存已验证模型的类别映射；映射变更不需要重新复制模型。"""

        entry = self.selected_entry()
        if entry is None:
            return
        dialog = ModelMappingDialog(self.locale_service, self.project, entry, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        entry.label_mapping = dialog.mapping()
        try:
            self.model_service.save_model(self.root, self.project, entry)
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, tr(self.locale_service, "dialog.error"), str(error))
            return
        self.refresh()

    def update_model(self) -> None:
        """验证新文件成功后替换当前模型，类别映射需要由用户重新确认。"""

        entry = self.selected_entry()
        if entry is None:
            return
        path, _ = QFileDialog.getOpenFileName(
            self,
            tr(self.locale_service, "dialog.models.update"),
            "",
            tr(self.locale_service, "dialog.models.filter"),
        )
        if not path:
            return
        try:
            self.model_service.replace_model(self.root, self.project, entry, Path(path))
        except (OSError, ValueError, RuntimeError) as error:
            QMessageBox.warning(self, tr(self.locale_service, "dialog.error"), str(error))
            return
        self.refresh()

    def delete_model(self) -> None:
        """模型删除仅作用于当前项目，且会在用户确认后永久移除二进制。"""

        entry = self.selected_entry()
        if entry is None:
            return
        confirmed = QMessageBox.question(
            self,
            tr(self.locale_service, "dialog.models.delete"),
            tr(self.locale_service, "dialog.models.delete_confirm"),
        )
        if confirmed != QMessageBox.StandardButton.Yes:
            return
        try:
            self.model_service.delete_model(self.root, self.project, entry)
        except OSError as error:
            QMessageBox.warning(self, tr(self.locale_service, "dialog.error"), str(error))
            return
        self.refresh()

    def request_auto_annotation(self, scope: str) -> None:
        """只发出请求，耗时推理由主窗口决定范围并执行。"""

        entry = self.selected_entry()
        if entry is not None:
            self.auto_annotation_requested.emit(entry, scope)

    def selected_entry(self) -> ModelEntry | None:
        """从当前行的稳定模型 ID 取回元数据，不依赖可编辑显示名称。"""

        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return None
        entry_id = self.table.item(rows[0].row(), 0).data(Qt.ItemDataRole.UserRole)
        return self.entries.get(str(entry_id))
