"""不访问文件系统的 UI 预览标注画布。"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QWheelEvent,
)
from PySide6.QtWidgets import QApplication, QWidget

from datumdock.domain.models import new_id
from datumdock.ui.prototype_models import AnnotationItemViewData, ImageItemViewData, LabelViewData
from datumdock.ui.theme import THEME


class CanvasTool(StrEnum):
    """原型画布支持的直接交互模式。"""

    SELECT = "select"
    RECTANGLE = "rectangle"
    PAN = "pan"


@dataclass(slots=True)
class RectangleDraft:
    """一次性矩形的屏幕坐标草稿，不进入撤销或持久化。"""

    anchor: QPointF | None = None
    press_point: QPointF | None = None
    current_point: QPointF | None = None


class PreviewAnnotationCanvas(QWidget):
    """在内存中绘制图片与矩形，验证标注工作台布局和交互。"""

    shape_selected = Signal(str)
    shape_double_clicked = Signal(str)
    document_changed = Signal()
    edit_committed = Signal(str)
    tool_changed = Signal(str)
    zoom_changed = Signal(int)

    HANDLE_SIZE = float(THEME.tokens.annotation_handle_size)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setMinimumSize(360, 300)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.image: ImageItemViewData | None = None
        self.labels: dict[str, LabelViewData] = {}
        self.annotations: list[AnnotationItemViewData] = []
        self.selected_id: str | None = None
        self.tool = CanvasTool.SELECT
        self.zoom = 1.0
        self._drag_origin: QPointF | None = None
        self._drag_snapshot: AnnotationItemViewData | None = None
        self._active_handle: str | None = None
        self._temporary: QRectF | None = None
        self._undo: list[list[AnnotationItemViewData]] = []
        self._redo: list[list[AnnotationItemViewData]] = []
        self.empty_title = "DatumDock"
        self.empty_subtitle = ""
        self.managed_pixmap = QPixmap()
        self.managed_read_only = False
        self.current_label_id: str | None = None
        self.pan_offset = QPointF()
        self._pan_start = QPointF()
        self._middle_panning = False
        self._left_panning = False
        self._draft = RectangleDraft()
        self._hover_point: QPointF | None = None

    def set_empty_message(self, title: str, subtitle: str = "") -> None:
        """由工作台提供本地化空状态，画布不自行读取翻译资源。"""

        self.empty_title = title
        self.empty_subtitle = subtitle
        self.update()

    def load_preview(
        self,
        image: ImageItemViewData,
        labels: tuple[LabelViewData, ...],
        annotations: tuple[AnnotationItemViewData, ...],
    ) -> None:
        """加载纯内存快照，切换图片不会触发任何持久化。"""

        self.image = image
        self.managed_pixmap = QPixmap()
        self.managed_read_only = False
        self.pan_offset = QPointF()
        self.labels = {label.id: label for label in labels}
        self.current_label_id = next(iter(self.labels), None)
        self.annotations = list(annotations)
        self.selected_id = self.annotations[0].id if self.annotations else None
        self._undo.clear()
        self._redo.clear()
        self._draft = RectangleDraft()
        self.zoom = self._fit_zoom()
        self.update()
        if self.selected_id:
            self.shape_selected.emit(self.selected_id)

    def clear_preview(self) -> None:
        """显示无图片空画布。"""

        self.image = None
        self.annotations.clear()
        self.selected_id = None
        self.managed_pixmap = QPixmap()
        self.managed_read_only = False
        self.pan_offset = QPointF()
        self._draft = RectangleDraft()
        self._hover_point = None
        self.update()

    def load_managed_image(
        self,
        image: ImageItemViewData,
        data: bytes,
        labels: tuple[LabelViewData, ...] = (),
        annotations: tuple[AnnotationItemViewData, ...] = (),
        *,
        editable: bool = False,
    ) -> bool:
        """普通模式只接收网关字节和视图快照，不接触受管路径。"""

        pixmap = QPixmap()
        if not pixmap.loadFromData(data, "PNG"):
            self.clear_preview()
            return False
        self.image = image
        self.managed_pixmap = pixmap
        self.managed_read_only = not editable
        self.labels = {label.id: label for label in labels}
        self.current_label_id = next(
            (label.id for label in labels if not label.archived),
            None,
        )
        self.annotations = list(annotations)
        self.selected_id = self.annotations[0].id if self.annotations else None
        self._undo.clear()
        self._redo.clear()
        self._draft = RectangleDraft()
        self.zoom = self._fit_zoom()
        self.pan_offset = QPointF()
        self.update()
        return True

    def set_current_label(self, label_id: str | None) -> None:
        """设置新建矩形使用的活动标签，归档标签由外层过滤。"""

        self.current_label_id = label_id if label_id in self.labels else None

    def delete_selected(self) -> None:
        """删除当前矩形并形成一个可撤销操作。"""

        if self.managed_read_only or not self.selected_id:
            return
        remaining = [item for item in self.annotations if item.id != self.selected_id]
        if len(remaining) == len(self.annotations):
            return
        self._push_undo()
        self.annotations = remaining
        self.selected_id = None
        self._emit_change("delete")
        self.update()

    def change_selected_label(self, label_id: str) -> None:
        """通过统一标签选择器改派所选矩形，并形成一次历史操作。"""

        if self.managed_read_only or label_id not in self.labels or not self.selected_id:
            return
        current = next(
            (item for item in self.annotations if item.id == self.selected_id),
            None,
        )
        if current is None or current.label_id == label_id:
            return
        self._push_undo()
        self._replace_annotation(replace(current, label_id=label_id))
        self._emit_change("reassign")
        self.update()

    def set_tool(self, tool: CanvasTool) -> None:
        """切换画布工具并更新鼠标形态。"""

        if tool != self.tool:
            self._draft = RectangleDraft()
            self._temporary = None
        self.tool = tool
        cursor = (
            Qt.CursorShape.OpenHandCursor if tool == CanvasTool.PAN else Qt.CursorShape.ArrowCursor
        )
        self.setCursor(cursor)
        self.tool_changed.emit(tool.value)
        self.update()

    def cancel_current_operation(self) -> None:
        """Esc 取消未完成草稿并回到选择模式，不产生历史节点。"""

        self._draft = RectangleDraft()
        self._temporary = None
        self._drag_origin = None
        self._drag_snapshot = None
        self._active_handle = None
        self.set_tool(CanvasTool.SELECT)

    def select_shape(self, shape_id: str) -> None:
        """右侧标注列表选择后同步高亮画布矩形。"""

        if any(item.id == shape_id for item in self.annotations):
            self.selected_id = shape_id
            self.shape_selected.emit(shape_id)
            self.update()

    def undo(self) -> None:
        """撤销当前图片内最近一次演示编辑。"""

        if not self._undo:
            return
        self._redo.append(list(self.annotations))
        self.annotations = self._undo.pop()
        self._emit_change("undo")
        self.update()

    def redo(self) -> None:
        """重做当前图片内最近一次演示编辑。"""

        if not self._redo:
            return
        self._undo.append(list(self.annotations))
        self.annotations = self._redo.pop()
        self._emit_change("redo")
        self.update()

    def zoom_in(self) -> None:
        """以画布中心放大演示图片。"""

        self._set_zoom(min(64.0, self.zoom * 1.25))

    def zoom_out(self) -> None:
        """缩小演示图片并保持最小可辨识尺寸。"""

        self._set_zoom(max(0.01, self.zoom / 1.25))

    def zoom_100(self) -> None:
        """恢复原图一个像素对应一个逻辑像素。"""

        self._set_zoom(1.0)

    def set_zoom_percent(self, percent: int) -> None:
        """由状态栏比例输入设置手动倍率。"""

        self._set_zoom(percent / 100.0)

    def fit_image(self) -> None:
        """恢复适配窗口比例。"""

        self.zoom = self._fit_zoom()
        self.pan_offset = QPointF()
        self.zoom_changed.emit(round(self.zoom * 100))
        self.update()

    def _set_zoom(self, value: float) -> None:
        if self.image is None:
            return
        target = max(0.01, min(64.0, float(value)))
        viewport_center = QRectF(self.rect()).center()
        old_rect = self._image_rect()
        image_center = self._canvas_to_image(viewport_center, old_rect)
        self.zoom = target
        new_rect = self._image_rect()
        projected = QPointF(
            new_rect.left() + image_center.x() * new_rect.width() / self.image.width,
            new_rect.top() + image_center.y() * new_rect.height() / self.image.height,
        )
        self.pan_offset += viewport_center - projected
        self._clamp_pan_offset()
        self.zoom_changed.emit(round(self.zoom * 100))
        self.update()

    def _fit_zoom(self) -> float:
        if self.image is None:
            return 1.0
        available = QRectF(self.rect()).adjusted(32, 28, -32, -28)
        if available.width() <= 0 or available.height() <= 0:
            return 1.0
        return min(
            available.width() / self.image.width,
            available.height() / self.image.height,
        )

    def paintEvent(self, event: object) -> None:
        """绘制深色画布、内存图片、标签框、置信度和控制柄。"""

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor(THEME.tokens.canvas_background))
        if self.image is None:
            self._paint_empty(painter)
            return
        image_rect = self._image_rect()
        if not self.managed_pixmap.isNull():
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, self.zoom < 8.0)
            visible = image_rect.intersected(QRectF(self.rect()))
            if not visible.isEmpty():
                source = QRectF(
                    (visible.left() - image_rect.left())
                    * self.managed_pixmap.width()
                    / image_rect.width(),
                    (visible.top() - image_rect.top())
                    * self.managed_pixmap.height()
                    / image_rect.height(),
                    visible.width() * self.managed_pixmap.width() / image_rect.width(),
                    visible.height() * self.managed_pixmap.height() / image_rect.height(),
                )
                painter.drawPixmap(visible, self.managed_pixmap, source)
        else:
            self._paint_demo_image(painter, image_rect, self.image.scene_seed)
        for annotation in self.annotations:
            self._paint_annotation(painter, annotation, annotation.id == self.selected_id)
        if self._temporary is not None:
            painter.setBrush(QColor(91, 131, 230, THEME.tokens.annotation_fill_alpha))
            painter.setPen(QPen(QColor(THEME.tokens.brand_primary), 2, Qt.PenStyle.DashLine))
            painter.drawRect(self._temporary.normalized())
            self._paint_draft_size(painter, self._temporary.normalized())
        if self._draft.anchor is not None:
            painter.setBrush(QColor(THEME.tokens.brand_primary))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(self._draft.anchor, 4, 4)
        self._paint_crosshair(painter)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        """矩形工具开始绘制，选择工具命中框或八点控制柄。"""

        if self.image is None:
            return super().mousePressEvent(event)
        point = event.position()
        if event.button() == Qt.MouseButton.MiddleButton:
            if self._image_rect().contains(point):
                self._middle_panning = True
                self._drag_origin = point
                self._pan_start = QPointF(self.pan_offset)
                self._hover_point = None
                self.setCursor(Qt.CursorShape.ClosedHandCursor)
                event.accept()
                self.update()
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(event)
        if not self._image_rect().contains(point):
            return
        if self.tool == CanvasTool.PAN:
            self._left_panning = True
            self._drag_origin = point
            self._pan_start = QPointF(self.pan_offset)
            self._hover_point = None
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            return
        if self.managed_read_only:
            return
        if self.tool == CanvasTool.RECTANGLE:
            if self.current_label_id is None:
                return
            self._draft.press_point = point
            self._draft.current_point = point
            start = self._draft.anchor or point
            self._temporary = QRectF(start, point)
            self.update()
            return
        hit = self._shape_at(point)
        if hit is None:
            self.selected_id = None
            self.update()
            return
        self.selected_id = hit.id
        self._drag_snapshot = hit
        self._drag_origin = point
        self._active_handle = self._handle_at(point, hit)
        self.shape_selected.emit(hit.id)
        self.update()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        """实时预览新框、移动或缩放，但只在释放时形成一次撤销记录。"""

        point = event.position()
        image_rect = self._image_rect()
        self._hover_point = point if image_rect.contains(point) else None
        if self._middle_panning or self._left_panning:
            if self._drag_origin is not None:
                self.pan_offset = self._pan_start + point - self._drag_origin
                self._clamp_pan_offset()
            self._hover_point = None
            self.update()
            return
        if self.managed_read_only:
            self.update()
            return
        if self.tool == CanvasTool.RECTANGLE and (
            self._draft.anchor is not None or self._draft.press_point is not None
        ):
            bounded = _bounded_point(point, image_rect)
            self._draft.current_point = bounded
            start = self._draft.anchor or self._draft.press_point
            self._temporary = QRectF(start, bounded)
            self.update()
            return
        if self._drag_snapshot is None or self._drag_origin is None:
            return
        delta = self._canvas_delta_to_image(point - self._drag_origin)
        original = self._drag_snapshot
        if self._active_handle:
            changed = self._resize_shape(original, delta, self._active_handle)
        else:
            changed = replace(
                original,
                x1=original.x1 + max(-original.x1, min(delta.x(), self.image.width - original.x2)),
                y1=original.y1 + max(-original.y1, min(delta.y(), self.image.height - original.y2)),
                x2=original.x2 + max(-original.x1, min(delta.x(), self.image.width - original.x2)),
                y2=original.y2 + max(-original.y1, min(delta.y(), self.image.height - original.y2)),
            )
        self._replace_annotation(changed)
        self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        """有效手势完成后只更新内存文档并通知外层刷新列表。"""

        if event.button() == Qt.MouseButton.MiddleButton:
            self._middle_panning = False
            self._drag_origin = None
            self.setCursor(
                Qt.CursorShape.OpenHandCursor
                if self.tool == CanvasTool.PAN
                else Qt.CursorShape.ArrowCursor
            )
            self.update()
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        if self._left_panning:
            self._left_panning = False
            self._drag_origin = None
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            return
        if self.managed_read_only:
            return
        if self.tool == CanvasTool.RECTANGLE and self._draft.press_point is not None:
            point = _bounded_point(event.position(), self._image_rect())
            anchor = self._draft.anchor
            press = self._draft.press_point
            distance = abs(point.x() - press.x()) + abs(point.y() - press.y())
            if anchor is not None:
                self._commit_rectangle(anchor, point)
            elif distance >= QApplication.startDragDistance():
                self._commit_rectangle(press, point)
            else:
                self._draft.anchor = press
                self._draft.current_point = point
                self._draft.press_point = None
                self._temporary = QRectF(press, point)
                self.update()
            return
        elif self._drag_snapshot is not None:
            current = next(
                (item for item in self.annotations if item.id == self._drag_snapshot.id), None
            )
            if current is not None and current != self._drag_snapshot:
                snapshot = list(self.annotations)
                snapshot[snapshot.index(current)] = self._drag_snapshot
                self._undo.append(snapshot)
                self._redo.clear()
                self._emit_change("resize" if self._active_handle else "move")
        self._drag_origin = None
        self._drag_snapshot = None
        self._active_handle = None
        self.update()

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        """双击矩形请求外层打开统一标签选择器。"""

        if event.button() == Qt.MouseButton.LeftButton and not self.managed_read_only:
            hit = self._shape_at(event.position())
            if hit is not None:
                self._draft = RectangleDraft()
                self._temporary = None
                self.set_tool(CanvasTool.SELECT)
                self.select_shape(hit.id)
                self.shape_double_clicked.emit(hit.id)
                return
        super().mouseDoubleClickEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        """应用快捷键由 ActionRegistry 管理，画布只保留原生事件链。"""

        super().keyPressEvent(event)

    def wheelEvent(self, event: QWheelEvent) -> None:
        """滚轮纵向滚动，Alt+滚轮横向滚动；Ctrl 本轮不绑定。"""

        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            event.ignore()
            return
        delta = event.angleDelta().y() or event.pixelDelta().y()
        if event.modifiers() & Qt.KeyboardModifier.AltModifier:
            self.pan_offset.setX(self.pan_offset.x() + delta)
        else:
            self.pan_offset.setY(self.pan_offset.y() + delta)
        self._clamp_pan_offset()
        event.accept()
        self.update()

    def leaveEvent(self, event: object) -> None:
        self._hover_point = None
        self.update()
        super().leaveEvent(event)

    def _image_rect(self) -> QRectF:
        """计算保持原始比例的居中显示区域。"""

        if self.image is None:
            return QRectF()
        available = QRectF(self.rect()).adjusted(32, 28, -32, -28)
        width = self.image.width * self.zoom
        height = self.image.height * self.zoom
        center = available.center()
        return QRectF(
            center.x() - width / 2 + self.pan_offset.x(),
            center.y() - height / 2 + self.pan_offset.y(),
            width,
            height,
        )

    def _clamp_pan_offset(self) -> None:
        """把滚动限制在图片四边，禁止图片无限漂离可视区域。"""

        if self.image is None:
            self.pan_offset = QPointF()
            return
        available = QRectF(self.rect()).adjusted(32, 28, -32, -28)
        width = self.image.width * self.zoom
        height = self.image.height * self.zoom
        if width <= available.width():
            x = 0.0
        else:
            limit = (width - available.width()) / 2
            x = max(-limit, min(limit, self.pan_offset.x()))
        if height <= available.height():
            y = 0.0
        else:
            limit = (height - available.height()) / 2
            y = max(-limit, min(limit, self.pan_offset.y()))
        self.pan_offset = QPointF(x, y)

    def _paint_demo_image(self, painter: QPainter, rect: QRectF, seed: int) -> None:
        """绘制工业零件风格的自有抽象演示图片。"""

        painter.save()
        painter.setClipRect(rect)
        painter.fillRect(rect, QColor("#D4D8DB" if seed % 2 else "#C9CED2"))
        painter.setPen(QPen(QColor(255, 255, 255, 38), 1))
        step = max(18.0, rect.width() / 24)
        x = rect.left()
        while x < rect.right():
            painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
            x += step
        part = rect.adjusted(
            rect.width() * 0.13, rect.height() * 0.17, -rect.width() * 0.13, -rect.height() * 0.17
        )
        path = QPainterPath()
        path.addRoundedRect(part, 18, 18)
        painter.fillPath(path, QColor("#687685"))
        painter.setPen(QPen(QColor("#8E9AA6"), 5))
        painter.drawRoundedRect(part.adjusted(6, 6, -6, -6), 14, 14)
        painter.setBrush(QColor("#C6CCD1"))
        painter.setPen(QPen(QColor("#56616C"), 4))
        for fraction in (0.26, 0.72):
            center = QPointF(part.left() + part.width() * fraction, part.center().y() - 20)
            painter.drawEllipse(center, part.width() * 0.07, part.width() * 0.07)
        painter.setPen(QPen(QColor("#AFB9C2"), 7))
        painter.drawLine(
            QPointF(part.left() + part.width() * 0.42, part.top() + part.height() * 0.57),
            QPointF(part.left() + part.width() * 0.60, part.top() + part.height() * 0.64),
        )
        painter.restore()

    def _paint_annotation(
        self,
        painter: QPainter,
        annotation: AnnotationItemViewData,
        selected: bool,
    ) -> None:
        """绘制标签色矩形、名称浮层、置信度和选中控制柄。"""

        label = self.labels.get(annotation.label_id)
        if label is None:
            return
        rect = self._annotation_rect(annotation)
        color = QColor(label.color)
        fill = QColor(color)
        fill.setAlpha(THEME.tokens.annotation_fill_alpha)
        border = QColor(color)
        border.setAlpha(THEME.tokens.annotation_border_alpha)
        painter.setBrush(fill)
        painter.setPen(
            QPen(
                border,
                THEME.tokens.annotation_selected_line_width
                if selected
                else THEME.tokens.annotation_line_width,
            )
        )
        painter.drawRect(rect)
        caption = f"{label.alias} · {label.name}"
        if annotation.confidence is not None:
            caption += f"  {annotation.confidence:.0%}"
        metrics = painter.fontMetrics()
        label_rect = QRectF(
            rect.left(),
            max(self._image_rect().top(), rect.top() - 27),
            metrics.horizontalAdvance(caption) + 18,
            25,
        )
        painter.fillRect(label_rect, color)
        painter.setPen(QColor("#FFFFFF"))
        painter.drawText(label_rect.adjusted(8, 0, -6, 0), Qt.AlignmentFlag.AlignVCenter, caption)
        if selected:
            painter.setBrush(QColor("#FFFFFF"))
            painter.setPen(QPen(QColor(THEME.tokens.brand_primary), 1))
            for point in self._handle_points(rect).values():
                painter.drawRect(self._handle_rect(point))

    def _paint_empty(self, painter: QPainter) -> None:
        """无图片时保持画布专业且给出明确入口提示。"""

        painter.setPen(QColor("#AEB8C7"))
        font = painter.font()
        font.setPixelSize(17)
        font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font)
        center = self.rect().center()
        title_rect = self.rect().adjusted(24, 0, -24, 0)
        title_rect.setBottom(center.y())
        painter.drawText(
            title_rect,
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom,
            self.empty_title,
        )
        if self.empty_subtitle:
            painter.setPen(QColor("#7F8B9D"))
            font.setPixelSize(13)
            font.setWeight(QFont.Weight.Normal)
            painter.setFont(font)
            subtitle_rect = self.rect().adjusted(40, center.y() + 10, -40, -20)
            painter.drawText(
                subtitle_rect,
                Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
                self.empty_subtitle,
            )

    def _paint_crosshair(self, painter: QPainter) -> None:
        point = self._hover_point
        if point is None or self.image is None or self._middle_panning or self._left_panning:
            return
        visible = self._image_rect().intersected(QRectF(self.rect()))
        if not visible.contains(point):
            return
        color = QColor("#FFFFFF")
        color.setAlpha(THEME.tokens.canvas_crosshair_alpha)
        painter.save()
        painter.setClipRect(visible)
        painter.setPen(QPen(color, 1, Qt.PenStyle.DashLine))
        painter.drawLine(QPointF(visible.left(), point.y()), QPointF(visible.right(), point.y()))
        painter.drawLine(QPointF(point.x(), visible.top()), QPointF(point.x(), visible.bottom()))
        painter.restore()

    def _paint_draft_size(self, painter: QPainter, rect: QRectF) -> None:
        if self.image is None or rect.isEmpty():
            return
        top_left = self._canvas_to_image(rect.topLeft(), self._image_rect())
        bottom_right = self._canvas_to_image(rect.bottomRight(), self._image_rect())
        width = abs(bottom_right.x() - top_left.x())
        height = abs(bottom_right.y() - top_left.y())
        caption = f"{width:.1f} × {height:.1f}"
        metrics = painter.fontMetrics()
        box = QRectF(
            rect.left(),
            min(self.height() - 25, rect.bottom() + 5),
            metrics.horizontalAdvance(caption) + 12,
            22,
        )
        painter.fillRect(box, QColor(37, 43, 54, 220))
        painter.setPen(QColor("#FFFFFF"))
        painter.drawText(box.adjusted(6, 0, -4, 0), Qt.AlignmentFlag.AlignVCenter, caption)

    def _commit_rectangle(self, first: QPointF, second: QPointF) -> bool:
        """提交拖拽或两次单击的共同结果；零面积保留第一角锚点。"""

        if self.image is None or self.current_label_id is None:
            return False
        rect = QRectF(first, second).normalized()
        top_left = self._canvas_to_image(rect.topLeft(), self._image_rect())
        bottom_right = self._canvas_to_image(rect.bottomRight(), self._image_rect())
        if top_left.x() == bottom_right.x() or top_left.y() == bottom_right.y():
            self._draft = RectangleDraft(anchor=first, current_point=second)
            self._temporary = QRectF(first, second)
            self.update()
            return False
        self._push_undo()
        item = AnnotationItemViewData(
            new_id(),
            self.current_label_id,
            top_left.x(),
            top_left.y(),
            bottom_right.x(),
            bottom_right.y(),
        )
        self.annotations.append(item)
        self.selected_id = item.id
        self._draft = RectangleDraft()
        self._temporary = None
        self.shape_selected.emit(item.id)
        self._emit_change("create")
        self.set_tool(CanvasTool.SELECT)
        return True

    def _annotation_rect(self, item: AnnotationItemViewData) -> QRectF:
        image_rect = self._image_rect()
        if self.image is None:
            return QRectF()
        scale_x = image_rect.width() / self.image.width
        scale_y = image_rect.height() / self.image.height
        return QRectF(
            image_rect.left() + item.x1 * scale_x,
            image_rect.top() + item.y1 * scale_y,
            (item.x2 - item.x1) * scale_x,
            (item.y2 - item.y1) * scale_y,
        )

    def _shape_at(self, point: QPointF) -> AnnotationItemViewData | None:
        for item in reversed(self.annotations):
            if self._annotation_rect(item).adjusted(-4, -4, 4, 4).contains(point):
                return item
        return None

    def _handle_points(self, rect: QRectF) -> dict[str, QPointF]:
        return {
            "top_left": rect.topLeft(),
            "top": QPointF(rect.center().x(), rect.top()),
            "top_right": rect.topRight(),
            "right": QPointF(rect.right(), rect.center().y()),
            "bottom_right": rect.bottomRight(),
            "bottom": QPointF(rect.center().x(), rect.bottom()),
            "bottom_left": rect.bottomLeft(),
            "left": QPointF(rect.left(), rect.center().y()),
        }

    def _handle_rect(self, point: QPointF) -> QRectF:
        return QRectF(
            point.x() - self.HANDLE_SIZE / 2,
            point.y() - self.HANDLE_SIZE / 2,
            self.HANDLE_SIZE,
            self.HANDLE_SIZE,
        )

    def _handle_at(self, point: QPointF, item: AnnotationItemViewData) -> str | None:
        if item.id != self.selected_id:
            return None
        for name, handle_point in self._handle_points(self._annotation_rect(item)).items():
            if self._handle_rect(handle_point).adjusted(-3, -3, 3, 3).contains(point):
                return name
        return None

    def _canvas_delta_to_image(self, delta: QPointF) -> QPointF:
        if self.image is None:
            return QPointF()
        rect = self._image_rect()
        return QPointF(
            delta.x() * self.image.width / rect.width(),
            delta.y() * self.image.height / rect.height(),
        )

    def _canvas_to_image(self, point: QPointF, rect: QRectF) -> QPointF:
        if self.image is None:
            return QPointF()
        return QPointF(
            (point.x() - rect.left()) * self.image.width / rect.width(),
            (point.y() - rect.top()) * self.image.height / rect.height(),
        )

    def _resize_shape(
        self,
        original: AnnotationItemViewData,
        delta: QPointF,
        handle: str,
    ) -> AnnotationItemViewData:
        x1, y1, x2, y2 = original.x1, original.y1, original.x2, original.y2
        if "left" in handle:
            x1 += delta.x()
        if "right" in handle:
            x2 += delta.x()
        if "top" in handle:
            y1 += delta.y()
        if "bottom" in handle:
            y2 += delta.y()
        if self.image is None:
            return original
        x1 = max(0.0, min(x1, self.image.width - 2.0))
        y1 = max(0.0, min(y1, self.image.height - 2.0))
        x2 = max(2.0, min(x2, float(self.image.width)))
        y2 = max(2.0, min(y2, float(self.image.height)))
        return replace(
            original,
            x1=min(x1, x2 - 2),
            y1=min(y1, y2 - 2),
            x2=max(x2, x1 + 2),
            y2=max(y2, y1 + 2),
        )

    def _replace_annotation(self, changed: AnnotationItemViewData) -> None:
        for index, item in enumerate(self.annotations):
            if item.id == changed.id:
                self.annotations[index] = changed
                return

    def _push_undo(self) -> None:
        self._undo.append(list(self.annotations))
        del self._undo[:-100]
        self._redo.clear()

    def _emit_change(self, kind: str) -> None:
        self.document_changed.emit()
        self.edit_committed.emit(kind)


def _bounded_point(point: QPointF, bounds: QRectF) -> QPointF:
    return QPointF(
        max(bounds.left(), min(point.x(), bounds.right())),
        max(bounds.top(), min(point.y(), bounds.bottom())),
    )
