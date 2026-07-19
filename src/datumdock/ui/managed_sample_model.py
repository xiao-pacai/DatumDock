"""正式工作台的 SQLite 分页图片模型与按需缩略图委托。"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass

from PySide6.QtCore import QAbstractListModel, QModelIndex, QObject, QRunnable, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFontMetrics, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QStyle, QStyledItemDelegate, QStyleOptionViewItem, QWidget

from datumdock.domain.models import DatasetSample, ReviewStatus, SampleHealth, SampleSort
from datumdock.i18n.catalog import LocaleService, tr
from datumdock.services.image_pool import ThumbnailAsset
from datumdock.services.sample_repository import SamplePage
from datumdock.ui.theme import THEME


class _ThumbnailSignals(QObject):
    completed = Signal(int, str, object)
    failed = Signal(int, str)


class _ThumbnailJob(QRunnable):
    """后台只调用网关读取图片，永远不把受管路径交给页面。"""

    def __init__(self, gateway, dataset_id: str, sample_id: str, generation: int) -> None:
        super().__init__()
        self.gateway = gateway
        self.dataset_id = dataset_id
        self.sample_id = sample_id
        self.generation = generation
        self.signals = _ThumbnailSignals()

    def run(self) -> None:
        try:
            asset = self.gateway.load_thumbnail(self.dataset_id, self.sample_id)
            self.signals.completed.emit(self.generation, self.sample_id, asset)
        except Exception:
            self.signals.failed.emit(self.generation, self.sample_id)


class ManagedSampleListModel(QAbstractListModel):
    """每次最多持有 200 个领域对象，缩略图缓存具有明确上限。"""

    SampleIdRole = Qt.ItemDataRole.UserRole + 1
    FilenameRole = Qt.ItemDataRole.UserRole + 2
    StatusRole = Qt.ItemDataRole.UserRole + 3
    HealthRole = Qt.ItemDataRole.UserRole + 4
    SizeRole = Qt.ItemDataRole.UserRole + 5
    PAGE_SIZE = 200
    THUMBNAIL_CACHE_LIMIT = 256

    page_changed = Signal(int, int, int)

    def __init__(
        self,
        gateway,
        dataset_id: str,
        locale: LocaleService,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.gateway = gateway
        self.dataset_id = dataset_id
        self.locale = locale
        self.items: tuple[DatasetSample, ...] = ()
        self.total = 0
        self.offset = 0
        self.search = ""
        self.review_status: ReviewStatus | None = None
        self.sort = SampleSort.FILENAME_ASC
        self._generation = 0
        self._thumbnail_cache: OrderedDict[str, QPixmap] = OrderedDict()
        self._loading: set[str] = set()
        self._failed: set[str] = set()
        self._jobs: set[_ThumbnailJob] = set()

    def rowCount(self, parent: QModelIndex | None = None) -> int:
        return 0 if parent is not None and parent.isValid() else len(self.items)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self.items):
            return None
        sample = self.items[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            return sample.filename
        if role == Qt.ItemDataRole.DecorationRole:
            cached = self._thumbnail_cache.get(sample.id)
            if cached is not None:
                self._thumbnail_cache.move_to_end(sample.id)
                return QIcon(cached)
            if sample.id not in self._loading and sample.id not in self._failed:
                self._request_thumbnail(sample.id)
            return QIcon(self._placeholder(sample.health))
        if role == self.SampleIdRole:
            return sample.id
        if role == self.FilenameRole:
            return sample.filename
        if role == self.StatusRole:
            return sample.review_status.value
        if role == self.HealthRole:
            return sample.health.value
        if role == self.SizeRole:
            return (sample.width, sample.height)
        if role == Qt.ItemDataRole.ToolTipRole:
            return f"{sample.filename}\n{sample.width} × {sample.height}"
        return None

    def refresh(
        self,
        *,
        reset_page: bool = False,
        search: str | None = None,
        review_status: ReviewStatus | None | object = ...,
        sort: SampleSort | None = None,
    ) -> None:
        """筛选变化回到第一页；普通刷新保留当前分页位置。"""

        if search is not None:
            self.search = search.strip()
        if review_status is not ...:
            self.review_status = review_status
        if sort is not None:
            self.sort = sort
        if reset_page:
            self.offset = 0
        page: SamplePage = self.gateway.query_samples(
            self.dataset_id,
            offset=self.offset,
            limit=self.PAGE_SIZE,
            search=self.search,
            review_status=self.review_status,
            sort=self.sort,
        )
        if page.total and self.offset >= page.total:
            self.offset = max(0, ((page.total - 1) // self.PAGE_SIZE) * self.PAGE_SIZE)
            page = self.gateway.query_samples(
                self.dataset_id,
                offset=self.offset,
                limit=self.PAGE_SIZE,
                search=self.search,
                review_status=self.review_status,
                sort=self.sort,
            )
        self.beginResetModel()
        self.items = page.items
        self.total = page.total
        self._generation += 1
        self._loading.clear()
        self._failed.clear()
        self.endResetModel()
        self.page_changed.emit(self.page_number, self.page_count, self.total)

    @property
    def page_number(self) -> int:
        return self.offset // self.PAGE_SIZE + 1

    @property
    def page_count(self) -> int:
        return max(1, (self.total + self.PAGE_SIZE - 1) // self.PAGE_SIZE)

    def next_page(self) -> None:
        if self.offset + self.PAGE_SIZE < self.total:
            self.offset += self.PAGE_SIZE
            self.refresh()

    def previous_page(self) -> None:
        if self.offset > 0:
            self.offset = max(0, self.offset - self.PAGE_SIZE)
            self.refresh()

    def sample_at(self, row: int) -> DatasetSample | None:
        return self.items[row] if 0 <= row < len(self.items) else None

    def row_for_id(self, sample_id: str) -> int:
        return next(
            (index for index, sample in enumerate(self.items) if sample.id == sample_id),
            -1,
        )

    def clear_caches(self) -> None:
        """切换数据集时通过代号丢弃迟到结果并释放缩略图内存。"""

        self._generation += 1
        self._thumbnail_cache.clear()
        self._loading.clear()
        self._failed.clear()

    def _request_thumbnail(self, sample_id: str) -> None:
        self._loading.add(sample_id)
        job = _ThumbnailJob(self.gateway, self.dataset_id, sample_id, self._generation)
        self._jobs.add(job)
        job.signals.completed.connect(self._thumbnail_ready)
        job.signals.failed.connect(self._thumbnail_failed)
        job.signals.completed.connect(lambda *_args, current=job: self._jobs.discard(current))
        job.signals.failed.connect(lambda *_args, current=job: self._jobs.discard(current))
        from PySide6.QtCore import QThreadPool

        QThreadPool.globalInstance().start(job)

    def _thumbnail_ready(
        self,
        generation: int,
        sample_id: str,
        asset: ThumbnailAsset,
    ) -> None:
        if generation != self._generation or asset.sample_id != sample_id:
            return
        pixmap = QPixmap()
        if not pixmap.loadFromData(asset.data, "PNG"):
            self._thumbnail_failed(generation, sample_id)
            return
        self._loading.discard(sample_id)
        self._thumbnail_cache[sample_id] = pixmap
        self._thumbnail_cache.move_to_end(sample_id)
        while len(self._thumbnail_cache) > self.THUMBNAIL_CACHE_LIMIT:
            self._thumbnail_cache.popitem(last=False)
        row = self.row_for_id(sample_id)
        if row >= 0:
            index = self.index(row)
            self.dataChanged.emit(index, index, [Qt.ItemDataRole.DecorationRole])

    def _thumbnail_failed(self, generation: int, sample_id: str) -> None:
        if generation != self._generation:
            return
        self._loading.discard(sample_id)
        self._failed.add(sample_id)
        row = self.row_for_id(sample_id)
        if row >= 0:
            index = self.index(row)
            self.dataChanged.emit(index, index, [Qt.ItemDataRole.DecorationRole])

    @staticmethod
    def _placeholder(health: SampleHealth) -> QPixmap:
        pixmap = QPixmap(128, 84)
        pixmap.fill(QColor("#BAC2CC" if health == SampleHealth.READY else "#D7A2A2"))
        return pixmap


@dataclass(frozen=True, slots=True)
class _DelegateLayout:
    thumbnail: QSize
    item: QSize


class ManagedSampleDelegate(QStyledItemDelegate):
    """列表和网格共享同一模型，只更换轻量绘制布局。"""

    def __init__(
        self,
        locale: LocaleService,
        *,
        grid: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.locale = locale
        self.grid = grid

    def set_grid(self, enabled: bool) -> None:
        self.grid = enabled

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:
        return QSize(148, 126) if self.grid else QSize(max(280, option.rect.width()), 64)

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index: QModelIndex,
    ) -> None:
        painter.save()
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(
            option.rect,
            QColor(THEME.tokens.brand_soft if selected else THEME.tokens.surface),
        )
        icon = index.data(Qt.ItemDataRole.DecorationRole)
        pixmap = icon.pixmap(128, 84) if isinstance(icon, QIcon) else QPixmap()
        filename = str(index.data(ManagedSampleListModel.FilenameRole) or "")
        status = str(index.data(ManagedSampleListModel.StatusRole) or "unreviewed")
        health = str(index.data(ManagedSampleListModel.HealthRole) or "ready")
        status_key = f"review.{status}" if health == "ready" else "status.error"
        status_text = tr(self.locale, status_key)
        if self.grid:
            image_rect = option.rect.adjusted(10, 8, -10, -34)
            painter.drawPixmap(image_rect, pixmap)
            text_rect = option.rect.adjusted(8, option.rect.height() - 33, -8, -4)
            text = QFontMetrics(option.font).elidedText(
                filename, Qt.TextElideMode.ElideMiddle, text_rect.width()
            )
            painter.setPen(QColor(THEME.tokens.text_primary))
            painter.drawText(text_rect, Qt.AlignmentFlag.AlignTop, text)
        else:
            image_rect = option.rect.adjusted(8, 7, -option.rect.width() + 86, -7)
            painter.drawPixmap(image_rect, pixmap)
            copy_rect = option.rect.adjusted(96, 8, -8, -8)
            painter.setPen(QColor(THEME.tokens.text_primary))
            painter.drawText(copy_rect, Qt.AlignmentFlag.AlignTop, filename)
            painter.setPen(QColor(THEME.tokens.text_muted))
            painter.drawText(copy_rect, Qt.AlignmentFlag.AlignBottom, status_text)
        painter.restore()
