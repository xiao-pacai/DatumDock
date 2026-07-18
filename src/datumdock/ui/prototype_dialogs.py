"""DatumDock UI 原型的集中对话框与向导注册表。"""

from __future__ import annotations

from enum import StrEnum

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from datumdock.i18n.catalog import LocaleService, tr
from datumdock.ui.components import (
    CoverPreview,
    FilterChip,
    GhostButton,
    PrimaryButton,
    SectionCard,
)
from datumdock.ui.theme import THEME


class DialogId(StrEnum):
    """全部原型弹窗的稳定注册标识。"""

    CREATE_DATASET = "create_dataset"
    CREATE_FROM_TEMPLATE = "create_from_template"
    DATASET_DIAGNOSTICS = "dataset_diagnostics"
    RENAME_DATASET = "rename_dataset"
    ARCHIVE_DATASET = "archive_dataset"
    LABEL_EDITOR = "label_editor"
    LABEL_COLOR = "label_color"
    MODEL_IMPORT = "model_import"
    MODEL_INSPECTION = "model_inspection"
    MODEL_MAPPING = "model_mapping"
    AUTO_ANNOTATION = "auto_annotation"
    CPU_FALLBACK = "cpu_fallback"
    GPU_GUIDE = "gpu_guide"
    IMAGE_IMPORT = "image_import"
    DUPLICATE_COMPARE = "duplicate_compare"
    IMPORT_REPORT = "import_report"
    RENAME_SAMPLES = "rename_samples"
    DELETE_CURRENT = "delete_current"
    DELETE_BATCH = "delete_batch"
    YOLO_EXPORT = "yolo_export"
    XANY_EXCHANGE = "xany_exchange"
    BACKUP_EXPORT = "backup_export"
    BACKUP_IMPORT = "backup_import"
    DATASET_TRANSFER = "dataset_transfer"
    TASK_CENTER = "task_center"
    SAVE_ERROR = "save_error"
    JSON_ERROR = "json_error"
    UNSUPPORTED_MODEL = "unsupported_model"


TITLE_KEYS = {
    DialogId.CREATE_DATASET: "dialog.create.title",
    DialogId.CREATE_FROM_TEMPLATE: "dialog.template.title",
    DialogId.DATASET_DIAGNOSTICS: "home.diagnostics",
    DialogId.RENAME_DATASET: "dialog.rename.title",
    DialogId.ARCHIVE_DATASET: "action.archive",
    DialogId.LABEL_EDITOR: "action.add_label",
    DialogId.LABEL_COLOR: "table.color",
    DialogId.MODEL_IMPORT: "dialog.model.title",
    DialogId.MODEL_INSPECTION: "dialog.model.title",
    DialogId.MODEL_MAPPING: "action.configure_mapping",
    DialogId.AUTO_ANNOTATION: "tool.ai",
    DialogId.CPU_FALLBACK: "dialog.model.title",
    DialogId.GPU_GUIDE: "dialog.model.title",
    DialogId.IMAGE_IMPORT: "dialog.import.title",
    DialogId.DUPLICATE_COMPARE: "dialog.duplicate.title",
    DialogId.IMPORT_REPORT: "dialog.import.title",
    DialogId.RENAME_SAMPLES: "dialog.rename.title",
    DialogId.DELETE_CURRENT: "dialog.delete.title",
    DialogId.DELETE_BATCH: "dialog.delete.title",
    DialogId.YOLO_EXPORT: "dialog.export.title",
    DialogId.XANY_EXCHANGE: "dialog.xany.title",
    DialogId.BACKUP_EXPORT: "dialog.backup.title",
    DialogId.BACKUP_IMPORT: "dialog.backup.title",
    DialogId.DATASET_TRANSFER: "dialog.transfer.title",
    DialogId.TASK_CENTER: "dialog.task.title",
    DialogId.SAVE_ERROR: "dialog.error.title",
    DialogId.JSON_ERROR: "dialog.error.title",
    DialogId.UNSUPPORTED_MODEL: "dialog.error.title",
}


class PreviewFlowDialog(QDialog):
    """用统一视觉组织表单、预览和结果，不执行任何真实业务。"""

    command_ready = Signal(str, dict)

    def __init__(
        self,
        locale: LocaleService,
        dialog_id: DialogId,
        preview_mode: bool,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.locale = locale
        self.dialog_id = dialog_id
        self.preview_mode = preview_mode
        self.comparison_labels: list[tuple[QLabel, str]] = []
        self.setModal(True)
        self.resize(760, 570)
        self._build_ui()
        self.retranslate_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(14)
        self.title = QLabel()
        self.title.setObjectName("pageTitle")
        root.addWidget(self.title)
        self.notice = QLabel()
        self.notice.setObjectName("previewBanner")
        self.notice.setWordWrap(True)
        root.addWidget(self.notice)
        self.steps = QHBoxLayout()
        self.step_chips: list[FilterChip] = []
        for _ in range(3):
            chip = FilterChip("")
            chip.setEnabled(False)
            self.steps.addWidget(chip)
            self.step_chips.append(chip)
        self.steps.addStretch()
        root.addLayout(self.steps)
        self.pages = QStackedWidget()
        self.pages.addWidget(self._build_configure_page())
        self.pages.addWidget(self._build_preview_page())
        self.pages.addWidget(self._build_result_page())
        root.addWidget(self.pages, 1)
        controls = QHBoxLayout()
        self.cancel_button = GhostButton()
        self.cancel_button.clicked.connect(self.reject)
        self.previous_button = QPushButton()
        self.previous_button.clicked.connect(self.previous_step)
        self.next_button = PrimaryButton()
        self.next_button.clicked.connect(self.next_step)
        controls.addWidget(self.cancel_button)
        controls.addStretch()
        controls.addWidget(self.previous_button)
        controls.addWidget(self.next_button)
        root.addLayout(controls)
        self._refresh_step_state()

    def _build_configure_page(self) -> QWidget:
        page = SectionCard()
        self.form = QFormLayout()
        self.form_labels: dict[str, QLabel] = {}
        self.form.setHorizontalSpacing(18)
        self.name_input = QLineEdit()
        self.description_input = QTextEdit()
        self.description_input.setMaximumHeight(84)
        self.source_combo = QComboBox()
        self.source_combo.addItems(["工厂零件检测", "可回收物分类", "仓库安全检查"])
        self.path_input = QLineEdit("C:\\DatumDock-Preview\\output")
        self.seed_input = QSpinBox()
        self.seed_input.setRange(0, 999999)
        self.seed_input.setValue(42)
        self.threshold_input = QDoubleSpinBox()
        self.threshold_input.setRange(0.05, 0.99)
        self.threshold_input.setSingleStep(0.05)
        self.threshold_input.setValue(0.45)
        self.scope_combo = QComboBox()
        self.scope_combo.addItems(["当前图片", "全部图片", "全部未标注图片"])
        self.format_combo = QComboBox()
        self.format_combo.addItems(
            ["YOLO Detection", "X-AnyLabeling / LabelMe", "DatumDock Backup"]
        )
        self._configure_fields()
        page.body.addLayout(self.form)
        if self.dialog_id == DialogId.DUPLICATE_COMPARE:
            compare = QHBoxLayout()
            compare.addWidget(self._comparison_card("compare.pending", 5))
            compare.addWidget(self._comparison_card("compare.existing", 1))
            page.body.addLayout(compare)
        if self.dialog_id == DialogId.LABEL_COLOR:
            palette = QHBoxLayout()
            for color in ("#73B9D2", "#F2A36F", "#7BBF9A", "#C28CC8", "#D9B65D"):
                swatch = QPushButton()
                swatch.setFixedSize(48, 42)
                swatch.setStyleSheet(f"background:{color}; border-radius:10px;")
                palette.addWidget(swatch)
            page.body.addLayout(palette)
        return page

    def _add_form_row(self, key: str, field: QWidget) -> None:
        """保存表单标签引用，确保打开的向导可即时切换语言。"""

        label = QLabel()
        self.form_labels[key] = label
        self.form.addRow(label, field)

    def _configure_fields(self) -> None:
        if self.dialog_id in {
            DialogId.CREATE_DATASET,
            DialogId.RENAME_DATASET,
            DialogId.LABEL_EDITOR,
        }:
            self._add_form_row("form.name", self.name_input)
            self._add_form_row("form.description", self.description_input)
        elif self.dialog_id == DialogId.CREATE_FROM_TEMPLATE:
            self.copy_check = QCheckBox()
            self._add_form_row("form.source_dataset", self.source_combo)
            self._add_form_row("form.new_dataset", self.name_input)
            self._add_form_row("form.copy", self.copy_check)
        elif self.dialog_id == DialogId.AUTO_ANNOTATION:
            self.backend_combo = QComboBox()
            self._add_form_row("form.model", self.source_combo)
            self._add_form_row("form.scope", self.scope_combo)
            self._add_form_row("form.confidence", self.threshold_input)
            self._add_form_row("form.backend", self.backend_combo)
        elif self.dialog_id in {DialogId.YOLO_EXPORT, DialogId.XANY_EXCHANGE}:
            self._add_form_row("form.format", self.format_combo)
            self._add_form_row("form.output", self.path_input)
            self._add_form_row("form.random_seed", self.seed_input)
            self._add_form_row("form.split", QLabel("Train 80% · Val 10% · Test 10%"))
        elif self.dialog_id in {DialogId.BACKUP_EXPORT, DialogId.BACKUP_IMPORT}:
            self.validation_value = QLabel()
            self._add_form_row("form.backup", self.path_input)
            self._add_form_row("form.dataset", self.name_input)
            self._add_form_row("form.validation", self.validation_value)
        elif self.dialog_id == DialogId.DATASET_TRANSFER:
            self.target_combo = QComboBox()
            self.target_combo.addItems(["可回收物分类", "仓库安全检查"])
            self.mode_combo = QComboBox()
            self.compatibility_value = QLabel()
            self._add_form_row("form.source", self.source_combo)
            self._add_form_row("form.target", self.target_combo)
            self._add_form_row("form.mode", self.mode_combo)
            self._add_form_row("form.label_signature", self.compatibility_value)
        elif self.dialog_id in {
            DialogId.MODEL_IMPORT,
            DialogId.MODEL_INSPECTION,
            DialogId.MODEL_MAPPING,
            DialogId.CPU_FALLBACK,
            DialogId.GPU_GUIDE,
            DialogId.UNSUPPORTED_MODEL,
        }:
            self.task_value = QLabel()
            self.runtime_value = QLabel()
            self._add_form_row("form.model_file", QLineEdit("parts-yolo11.onnx"))
            self._add_form_row("form.task", self.task_value)
            self._add_form_row("form.input", QLabel("1 × 3 × 640 × 640"))
            self._add_form_row("form.runtime", self.runtime_value)
        elif self.dialog_id == DialogId.RENAME_SAMPLES:
            self._add_form_row("form.prefix", QLineEdit("factory_part"))
            self._add_form_row("form.start", self.seed_input)
            self._add_form_row("form.padding", QSpinBox())
            self._add_form_row("form.preview", QLabel("IMG_231.jpg  →  factory_part_000231.png"))
        elif self.dialog_id in {DialogId.IMAGE_IMPORT, DialogId.IMPORT_REPORT}:
            self.duplicate_check = QCheckBox()
            self._add_form_row("form.source_path", QLineEdit("D:\\Images\\Factory"))
            self._add_form_row("form.formats", QLabel("JPG · JPEG · PNG · BMP · WebP · TIFF"))
            self._add_form_row("form.managed_format", QLabel("PNG"))
            self._add_form_row("form.duplicate_check", self.duplicate_check)
        elif self.dialog_id in {
            DialogId.DELETE_CURRENT,
            DialogId.DELETE_BATCH,
            DialogId.ARCHIVE_DATASET,
        }:
            self.delete_warning = QLabel()
            self.delete_warning.setStyleSheet(
                f"color:{THEME.tokens.danger}; background:#FCE8E8; border-radius:8px; padding:10px;"
            )
            self.form.addRow(self.delete_warning)
            self.delete_mode_combo = QComboBox()
            self._add_form_row("form.mode", self.delete_mode_combo)
        else:
            self.status_value = QLabel()
            self._add_form_row("form.status", self.status_value)
            self._add_form_row("form.details", self.description_input)

    def _build_preview_page(self) -> QWidget:
        page = SectionCard()
        self.preview_title = QLabel()
        self.preview_title.setObjectName("sectionTitle")
        page.body.addWidget(self.preview_title)
        self.preview_table = QTableWidget(5, 4)
        self.preview_header_keys = (
            "preview.item",
            "preview.before",
            "preview.after",
            "preview.status",
        )
        rows = (
            ("preview.images", "1,864", "1,864", "preview.ready"),
            ("preview.annotations", "3,208", "3,208", "preview.ready"),
            ("preview.labels", "34", "34", "preview.compatible"),
            ("preview.duplicates", "preview.groups", "preview.grouped", "preview.review"),
            ("preview.output", "—", "preview.new_directory", "preview.preview"),
        )
        for row, values in enumerate(rows):
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if value.startswith("preview."):
                    item.setData(Qt.ItemDataRole.UserRole, value)
                self.preview_table.setItem(row, column, item)
        self.preview_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        page.body.addWidget(self.preview_table)
        self.preview_explanation = QLabel()
        self.preview_explanation.setObjectName("mutedText")
        self.preview_explanation.setWordWrap(True)
        page.body.addWidget(self.preview_explanation)
        return page

    def _build_result_page(self) -> QWidget:
        page = SectionCard()
        page.body.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon = QLabel("✓")
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setFixedSize(64, 64)
        icon.setStyleSheet(
            f"background:#E4F4EB; color:{THEME.tokens.success}; border-radius:32px; "
            "font-size:28px; font-weight:700;"
        )
        self.result_title = QLabel()
        self.result_title.setObjectName("sectionTitle")
        self.result_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.result_body = QLabel()
        self.result_body.setObjectName("mutedText")
        self.result_body.setWordWrap(True)
        self.result_body.setAlignment(Qt.AlignmentFlag.AlignCenter)
        progress = QProgressBar()
        progress.setValue(100)
        progress.setTextVisible(False)
        page.body.addStretch()
        page.body.addWidget(icon, 0, Qt.AlignmentFlag.AlignCenter)
        page.body.addWidget(self.result_title)
        page.body.addWidget(self.result_body)
        page.body.addWidget(progress)
        page.body.addStretch()
        return page

    def _comparison_card(self, title_key: str, seed: int) -> SectionCard:
        card = SectionCard()
        card.body.addWidget(CoverPreview(seed))
        label = QLabel()
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        card.body.addWidget(label)
        self.comparison_labels.append((label, title_key))
        return card

    def previous_step(self) -> None:
        self.pages.setCurrentIndex(max(0, self.pages.currentIndex() - 1))
        self._refresh_step_state()

    def next_step(self) -> None:
        requires_name = self.dialog_id in {
            DialogId.CREATE_DATASET,
            DialogId.CREATE_FROM_TEMPLATE,
            DialogId.RENAME_DATASET,
        }
        if self.pages.currentIndex() == 0 and requires_name and not self.name_input.text().strip():
            self.name_input.setFocus()
            self.name_input.setStyleSheet(f"border:2px solid {THEME.tokens.danger};")
            return
        if self.pages.currentIndex() < self.pages.count() - 1:
            self.pages.setCurrentIndex(self.pages.currentIndex() + 1)
            self._refresh_step_state()
            return
        payload = {
            "name": self.name_input.text().strip(),
            "description": self.description_input.toPlainText().strip(),
        }
        action_id = (
            "dataset.create"
            if self.dialog_id
            in {
                DialogId.CREATE_DATASET,
                DialogId.CREATE_FROM_TEMPLATE,
            }
            else f"preview.{self.dialog_id.value}"
        )
        self.command_ready.emit(action_id, payload)
        self.accept()

    def _refresh_step_state(self) -> None:
        current = self.pages.currentIndex()
        destructive = self.dialog_id in {
            DialogId.DELETE_CURRENT,
            DialogId.DELETE_BATCH,
            DialogId.ARCHIVE_DATASET,
        }
        self.next_button.setProperty(
            "role",
            "danger" if destructive and current == self.pages.count() - 1 else "primary",
        )
        self.next_button.style().unpolish(self.next_button)
        self.next_button.style().polish(self.next_button)
        for index, chip in enumerate(self.step_chips):
            chip.setChecked(index == current)
        self.previous_button.setEnabled(current > 0)
        self.retranslate_ui()

    def retranslate_ui(self) -> None:
        title = tr(self.locale, TITLE_KEYS[self.dialog_id])
        self.setWindowTitle(title)
        self.title.setText(title)
        self.notice.setText(tr(self.locale, "dialog.preview_only"))
        for chip, key in zip(
            self.step_chips,
            ("dialog.step.configure", "dialog.step.preview", "dialog.step.result"),
            strict=True,
        ):
            chip.setText(tr(self.locale, key))
        self.cancel_button.setText(tr(self.locale, "action.cancel"))
        self.previous_button.setText(tr(self.locale, "action.previous"))
        self.next_button.setText(
            tr(
                self.locale,
                "action.finish_preview"
                if self.pages.currentIndex() == self.pages.count() - 1
                else "action.next",
            )
        )
        self.preview_title.setText(tr(self.locale, "dialog.step.preview"))
        for key, label in self.form_labels.items():
            label.setText(tr(self.locale, key))
        for label, key in self.comparison_labels:
            label.setText(tr(self.locale, key))
        self.preview_table.setHorizontalHeaderLabels(
            [tr(self.locale, key) for key in self.preview_header_keys]
        )
        for row in range(self.preview_table.rowCount()):
            for column in range(self.preview_table.columnCount()):
                item = self.preview_table.item(row, column)
                key = item.data(Qt.ItemDataRole.UserRole)
                if key:
                    item.setText(tr(self.locale, key))
        self._retranslate_options()
        if self.dialog_id == DialogId.YOLO_EXPORT:
            explanation_key = "dialog.ratio"
        elif self.dialog_id in {DialogId.BACKUP_EXPORT, DialogId.BACKUP_IMPORT}:
            explanation_key = "dialog.models_excluded"
        else:
            explanation_key = "dialog.integrity"
        self.preview_explanation.setText(tr(self.locale, explanation_key))
        self.result_title.setText(tr(self.locale, "dialog.step.result"))
        self.result_body.setText(tr(self.locale, "dialog.success_preview"))

    def _retranslate_options(self) -> None:
        """刷新表单值和组合框选项，同时保留当前选择。"""

        self.scope_combo.clear()
        for key, value in (
            ("option.current_image", "current"),
            ("option.all_images", "all"),
            ("option.unlabeled_images", "unlabeled"),
        ):
            self.scope_combo.addItem(tr(self.locale, key), value)
        if hasattr(self, "copy_check"):
            self.copy_check.setText(tr(self.locale, "option.copy_config"))
        if hasattr(self, "backend_combo"):
            self.backend_combo.clear()
            self.backend_combo.addItem(tr(self.locale, "option.gpu_preferred"), "auto")
            self.backend_combo.addItem("CPU", "cpu")
        if hasattr(self, "mode_combo"):
            self.mode_combo.clear()
            for key, value in (
                ("option.copy", "copy"),
                ("option.move", "move"),
                ("option.merge", "merge"),
            ):
                self.mode_combo.addItem(tr(self.locale, key), value)
            self.compatibility_value.setText("✓ " + tr(self.locale, "option.compatible"))
        if hasattr(self, "delete_mode_combo"):
            self.delete_mode_combo.clear()
            self.delete_mode_combo.addItem(tr(self.locale, "option.trash"), "trash")
            self.delete_mode_combo.addItem(tr(self.locale, "option.permanent"), "permanent")
        if hasattr(self, "duplicate_check"):
            self.duplicate_check.setText(tr(self.locale, "option.exact_match"))
        if hasattr(self, "task_value"):
            self.task_value.setText(tr(self.locale, "option.object_detection"))
            self.runtime_value.setText(tr(self.locale, "option.gpu_preferred"))
        if hasattr(self, "validation_value"):
            self.validation_value.setText(tr(self.locale, "dialog.integrity"))
        if hasattr(self, "status_value"):
            self.status_value.setText(tr(self.locale, "preview.ready"))
        if hasattr(self, "delete_warning"):
            self.delete_warning.setText("⚠  " + tr(self.locale, "dialog.delete.scope"))


class DialogRegistry:
    """集中创建全部对话框，便于页面覆盖测试遍历。"""

    def __init__(self, locale: LocaleService, preview_mode: bool) -> None:
        self.locale = locale
        self.preview_mode = preview_mode

    def create(self, dialog_id: DialogId | str, parent: QWidget | None = None) -> PreviewFlowDialog:
        """按稳定标识创建完整对话框实例。"""

        identifier = DialogId(dialog_id)
        return PreviewFlowDialog(self.locale, identifier, self.preview_mode, parent)
