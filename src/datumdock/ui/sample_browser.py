"""按页加载的受管样本浏览器，避免万张图片一次性进入内存。"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from datumdock.domain.models import Dataset, DatasetSample, Project, ReviewStatus
from datumdock.i18n.catalog import LocaleService, tr
from datumdock.services.labelme import LabelMeRepository
from datumdock.services.storage import ProjectIndexRepository
from datumdock.services.workspace import WorkspaceService


class SampleBrowser(QWidget):
    """每页最多加载固定数量缩略图，筛选条件始终由 SQLite 执行。"""

    sample_selected = Signal(object)
    preview_changed = Signal(bool)

    PAGE_SIZE = 100

    def __init__(self, locale_service: LocaleService, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.locale_service = locale_service
        self.root: Path | None = None
        self.project: Project | None = None
        self.dataset: Dataset | None = None
        self.page = 0
        self._sample_by_id: dict[str, DatasetSample] = {}
        self._current_sample_id: str | None = None
        self._labelme_repository = LabelMeRepository()
        self._build_ui()
        self.locale_service.subscribe(self.retranslate_ui)
        self.retranslate_ui()

    def _build_ui(self) -> None:
        """创建固定控件；翻译与数据刷新在切换语言或数据集时执行。"""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        filters = QHBoxLayout()
        self.search = QLineEdit()
        self.search.textChanged.connect(self._reset_page_and_refresh)
        self.status_filter = QComboBox()
        self.status_filter.currentIndexChanged.connect(self._reset_page_and_refresh)
        self.label_filter = QComboBox()
        self.label_filter.currentIndexChanged.connect(self._reset_page_and_refresh)
        self.preview_toggle = QCheckBox()
        self.preview_toggle.toggled.connect(self._on_preview_toggled)
        filters.addWidget(self.search, 2)
        filters.addWidget(self.status_filter)
        filters.addWidget(self.label_filter)
        filters.addWidget(self.preview_toggle)
        layout.addLayout(filters)
        self.list_widget = QListWidget()
        self.list_widget.setViewMode(QListWidget.ViewMode.IconMode)
        self.list_widget.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.list_widget.setMovement(QListWidget.Movement.Static)
        self.list_widget.setIconSize(QPixmap(132, 88).size())
        self.list_widget.setGridSize(QPixmap(160, 126).size())
        self.list_widget.itemSelectionChanged.connect(self._emit_selected)
        layout.addWidget(self.list_widget, 1)
        pagination = QHBoxLayout()
        self.previous_button = QPushButton()
        self.previous_button.clicked.connect(self.previous_page)
        self.page_label = QLabel()
        self.page_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.next_button = QPushButton()
        self.next_button.clicked.connect(self.next_page)
        pagination.addWidget(self.previous_button)
        pagination.addWidget(self.page_label, 1)
        pagination.addWidget(self.next_button)
        layout.addLayout(pagination)

    def retranslate_ui(self) -> None:
        """保留筛选数据，仅替换系统文字与状态显示名。"""

        current_status = self.status_filter.currentData()
        current_label = self.label_filter.currentData()
        self.search.setPlaceholderText(tr(self.locale_service, "browser.search"))
        self.preview_toggle.setText(tr(self.locale_service, "browser.preview"))
        self.previous_button.setText(tr(self.locale_service, "browser.previous_page"))
        self.next_button.setText(tr(self.locale_service, "browser.next_page"))
        self.status_filter.blockSignals(True)
        self.status_filter.clear()
        self.status_filter.addItem(tr(self.locale_service, "browser.all_status"), None)
        for status in ReviewStatus:
            self.status_filter.addItem(tr(self.locale_service, f"review.{status.value}"), status)
        self._restore_combo_data(self.status_filter, current_status)
        self.status_filter.blockSignals(False)
        self._rebuild_label_filter(current_label)
        self.refresh()

    def set_context(self, root: Path, project: Project, dataset: Dataset) -> None:
        """切换项目或数据集后重置分页和选中项，不串用旧样本。"""

        self.root = root
        self.project = project
        self.dataset = dataset
        self.page = 0
        self._current_sample_id = None
        self.preview_toggle.blockSignals(True)
        self.preview_toggle.setChecked(dataset.show_annotation_preview)
        self.preview_toggle.blockSignals(False)
        self._rebuild_label_filter()
        self.refresh()

    def clear_context(self) -> None:
        """未选数据集时释放页面样本引用，避免错误地显示上一项目图片。"""

        self.root = None
        self.project = None
        self.dataset = None
        self._current_sample_id = None
        self._sample_by_id.clear()
        self.list_widget.clear()
        self.page_label.clear()

    def refresh(self) -> None:
        """只查询当前页面的样本和缩略图；一万张图片不会全量加载。"""

        if self.root is None or self.project is None or self.dataset is None:
            return
        index = ProjectIndexRepository(
            WorkspaceService.project_path(self.root, self.project.id) / "project-index.sqlite"
        )
        total = index.count_filtered_samples(
            self.dataset.id,
            search=self.search.text().strip(),
            review_status=self.status_filter.currentData(),
            label_id=self.label_filter.currentData(),
        )
        page_total = max(1, (total + self.PAGE_SIZE - 1) // self.PAGE_SIZE)
        self.page = min(self.page, page_total - 1)
        samples = index.list_samples(
            self.dataset.id,
            offset=self.page * self.PAGE_SIZE,
            limit=self.PAGE_SIZE,
            search=self.search.text().strip(),
            review_status=self.status_filter.currentData(),
            label_id=self.label_filter.currentData(),
        )
        self._sample_by_id = {sample.id: sample for sample in samples}
        self.list_widget.blockSignals(True)
        self.list_widget.clear()
        for sample in samples:
            item = QListWidgetItem(self._thumbnail_icon(sample), self._sample_caption(sample))
            item.setData(Qt.ItemDataRole.UserRole, sample.id)
            item.setToolTip(sample.filename)
            self.list_widget.addItem(item)
            if sample.id == self._current_sample_id:
                item.setSelected(True)
        self.list_widget.blockSignals(False)
        self.previous_button.setEnabled(self.page > 0)
        self.next_button.setEnabled(self.page + 1 < page_total)
        self.page_label.setText(
            tr(self.locale_service, "browser.page").format(
                page=self.page + 1,
                total=page_total,
                count=total,
            )
        )

    def current_sample(self) -> DatasetSample | None:
        """返回当前页面选中的受管样本，而非从界面文字解析文件名。"""

        return self._sample_by_id.get(self._current_sample_id or "")

    def select_sample_id(self, sample_id: str | None) -> None:
        """在当前页恢复或更新选中项，目标不在页内时由刷新流程选择首项。"""

        self._current_sample_id = sample_id
        for row in range(self.list_widget.count()):
            item = self.list_widget.item(row)
            if item.data(Qt.ItemDataRole.UserRole) == sample_id:
                self.list_widget.setCurrentItem(item)
                return

    def previous_page(self) -> None:
        """翻到上一页并取消旧选中，避免跨页自动保存到错误样本。"""

        if self.page <= 0:
            return
        self.page -= 1
        self._current_sample_id = None
        self.refresh()

    def next_page(self) -> None:
        """翻到下一页并取消旧选中，仍只加载一个页面的缩略图。"""

        self.page += 1
        self._current_sample_id = None
        self.refresh()

    def _on_preview_toggled(self, enabled: bool) -> None:
        """预览开关只改变当前数据集展示偏好，由主窗口持久化项目设置。"""

        if self.dataset is not None:
            self.dataset.show_annotation_preview = enabled
        self.preview_changed.emit(enabled)
        self.refresh()

    def _reset_page_and_refresh(self) -> None:
        """筛选条件变化后回到第一页，避免落在超出范围的旧页码。"""

        self.page = 0
        self._current_sample_id = None
        self.refresh()

    def _rebuild_label_filter(self, selected_label_id: str | None = None) -> None:
        """根据当前项目标签集刷新标签筛选项，显示别名和训练名以减轻记忆负担。"""

        if selected_label_id is None:
            selected_label_id = self.label_filter.currentData()
        self.label_filter.blockSignals(True)
        self.label_filter.clear()
        self.label_filter.addItem(tr(self.locale_service, "browser.all_status"), None)
        if self.project is not None:
            for label in self.project.label_set.labels:
                self.label_filter.addItem(f"{label.alias} · {label.name}", label.id)
        self._restore_combo_data(self.label_filter, selected_label_id)
        self.label_filter.blockSignals(False)

    def _thumbnail_icon(self, sample: DatasetSample) -> QIcon:
        """按需生成单张缩略图；开启预览时叠加矩形，关闭时复用磁盘缓存。"""

        if self.root is None or self.project is None or self.dataset is None:
            return QIcon()
        cache_path = (
            WorkspaceService.dataset_path(self.root, self.project.id, self.dataset.id)
            / "cache"
            / "thumbnails"
            / f"{sample.id}.png"
        )
        if not self.dataset.show_annotation_preview and cache_path.is_file():
            return QIcon(str(cache_path))
        pixmap = QPixmap(sample.image_path).scaled(
            132,
            88,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        if self.dataset.show_annotation_preview:
            self._paint_preview(pixmap, sample)
        elif not pixmap.isNull():
            pixmap.save(str(cache_path), "PNG")
        return QIcon(pixmap)

    def _paint_preview(self, pixmap: QPixmap, sample: DatasetSample) -> None:
        """在缩略图上绘制可见标签框，不改变缓存的原始图片缩略图。"""

        if pixmap.isNull() or self.project is None:
            return
        try:
            document = self._labelme_repository.load(
                Path(sample.annotation_path),
                sample.id,
                self.project.label_set,
                sample.filename,
                (sample.width, sample.height),
            )
        except (OSError, ValueError):
            return
        labels = {label.id: label for label in self.project.label_set.labels}
        scale_x = pixmap.width() / sample.width
        scale_y = pixmap.height() / sample.height
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        for rectangle in document.rectangles:
            label = labels.get(rectangle.label_id)
            if label is None:
                continue
            painter.setPen(QPen(QColor(label.color), 2))
            painter.drawRect(
                rectangle.x1 * scale_x,
                rectangle.y1 * scale_y,
                rectangle.width * scale_x,
                rectangle.height * scale_y,
            )
        painter.end()

    def _sample_caption(self, sample: DatasetSample) -> str:
        """文件名下展示整图复核状态，且不将项目内容交给翻译服务改写。"""

        status_key = (
            f"review.{sample.review_status.value}"
            if sample.review_status is not None
            else "review.none"
        )
        return f"● {tr(self.locale_service, status_key)}\n{sample.filename}"

    def _emit_selected(self) -> None:
        """把 SQLite 样本对象传给主窗口加载标注，不通过列表序号推断。"""

        items = self.list_widget.selectedItems()
        if not items:
            return
        sample_id = str(items[0].data(Qt.ItemDataRole.UserRole))
        sample = self._sample_by_id.get(sample_id)
        if sample is not None:
            self._current_sample_id = sample_id
            self.sample_selected.emit(sample)

    @staticmethod
    def _restore_combo_data(combo: QComboBox, target: object) -> None:
        """翻译后按稳定数据恢复组合框选择，而不是依赖变化的显示文本。"""

        for index in range(combo.count()):
            if combo.itemData(index) == target:
                combo.setCurrentIndex(index)
                return
