"""不访问文件系统的 UI 预览标注画布。"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from math import isfinite

from PySide6.QtCore import QPointF, QRectF, QSizeF, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QCursor,
    QFocusEvent,
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


class ImageEdge(StrEnum):
    """底板输入被投影到的图片边，用于绘制轻量吸附反馈。"""

    LEFT = "left"
    TOP = "top"
    RIGHT = "right"
    BOTTOM = "bottom"


@dataclass(frozen=True, slots=True)
class CanvasProjection:
    """画布输入到合法图片像素坐标的一次纯函数投影结果。"""

    source_point: QPointF
    canvas_point: QPointF
    image_point: QPointF
    inside_image: bool
    clamped_edges: frozenset[ImageEdge]


def project_point_to_image_bounds(
    point: QPointF,
    image_rect: QRectF,
    image_size: QSizeF,
) -> CanvasProjection:
    """把中央底板点逐轴钳制到图片边界，并换算为原图像素坐标。"""

    values = (
        point.x(),
        point.y(),
        image_rect.left(),
        image_rect.top(),
        image_rect.width(),
        image_rect.height(),
        image_size.width(),
        image_size.height(),
    )
    if not all(isfinite(value) for value in values):
        raise ValueError("画布投影拒绝非有限坐标")
    if image_rect.width() <= 0 or image_rect.height() <= 0:
        raise ValueError("画布投影需要有效图片显示区域")
    if image_size.width() <= 0 or image_size.height() <= 0:
        raise ValueError("画布投影需要有效原图尺寸")

    x = max(image_rect.left(), min(point.x(), image_rect.right()))
    y = max(image_rect.top(), min(point.y(), image_rect.bottom()))
    edges: set[ImageEdge] = set()
    if point.x() < image_rect.left():
        edges.add(ImageEdge.LEFT)
    elif point.x() > image_rect.right():
        edges.add(ImageEdge.RIGHT)
    if point.y() < image_rect.top():
        edges.add(ImageEdge.TOP)
    elif point.y() > image_rect.bottom():
        edges.add(ImageEdge.BOTTOM)
    canvas_point = QPointF(x, y)
    image_point = QPointF(
        (x - image_rect.left()) * image_size.width() / image_rect.width(),
        (y - image_rect.top()) * image_size.height() / image_rect.height(),
    )
    return CanvasProjection(
        QPointF(point),
        canvas_point,
        image_point,
        not edges,
        frozenset(edges),
    )


@dataclass(slots=True)
class RectangleDraft:
    """一次性矩形的屏幕坐标草稿，不进入撤销或持久化。"""

    anchor: QPointF | None = None
    press_point: QPointF | None = None
    press_raw_point: QPointF | None = None
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
        self._suppress_left_release = False
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
        self._snap_projection: CanvasProjection | None = None

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
        self._temporary = None
        self._drag_origin = None
        self._drag_snapshot = None
        self._active_handle = None
        self._suppress_left_release = False
        self._middle_panning = False
        self._left_panning = False
        self._hover_point = None
        self._snap_projection = None
        self.zoom = self._fit_zoom()
        self._refresh_cursor(reset=True)
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
        self._temporary = None
        self._drag_origin = None
        self._drag_snapshot = None
        self._active_handle = None
        self._suppress_left_release = False
        self._middle_panning = False
        self._left_panning = False
        self._hover_point = None
        self._snap_projection = None
        self._refresh_cursor(reset=True)
        self.update()

    def begin_managed_load(self) -> None:
        """清除上一张受管图片并锁定交互，直到新图片与标注一起加载完成。"""

        self.clear_preview()
        self.managed_read_only = True
        self._refresh_cursor(reset=True)
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
        self._temporary = None
        self._drag_origin = None
        self._drag_snapshot = None
        self._active_handle = None
        self._suppress_left_release = False
        self._middle_panning = False
        self._left_panning = False
        self._hover_point = None
        self._snap_projection = None
        self.zoom = self._fit_zoom()
        self.pan_offset = QPointF()
        self._refresh_cursor(reset=True)
        self.update()
        return True

    def set_current_label(self, label_id: str | None) -> None:
        """设置新建矩形使用的活动标签，归档标签由外层过滤。"""

        self.current_label_id = label_id if label_id in self.labels else None
        self._refresh_snap_projection()
        self._refresh_cursor()

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
        self._refresh_cursor()
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
            self._snap_projection = None
        self.tool = tool
        self._refresh_snap_projection()
        self._refresh_cursor()
        self.tool_changed.emit(tool.value)
        self.update()

    def cancel_current_operation(self) -> None:
        """Esc 取消未完成草稿并回到选择模式，不产生历史节点。"""

        self._draft = RectangleDraft()
        self._temporary = None
        self._snap_projection = None
        self._drag_origin = None
        self._drag_snapshot = None
        self._active_handle = None
        self._suppress_left_release = False
        self.set_tool(CanvasTool.SELECT)

    def select_shape(self, shape_id: str) -> None:
        """右侧标注列表选择后同步高亮画布矩形。"""

        if any(item.id == shape_id for item in self.annotations):
            self.selected_id = shape_id
            self._refresh_cursor()
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
        self._refresh_snap_projection()
        self.update()

    def _set_zoom(self, value: float, anchor: QPointF | None = None) -> None:
        """设置倍率，并尽量保持锚点对应的原图像素停留在指针下方。"""

        if self.image is None:
            return
        target = max(0.01, min(64.0, float(value)))
        viewport_center = QRectF(self.rect()).center()
        old_rect = self._image_rect()
        anchor_point = (
            QPointF(anchor) if anchor is not None and old_rect.contains(anchor) else viewport_center
        )
        image_anchor = self._canvas_to_image(anchor_point, old_rect)
        self.zoom = target
        new_rect = self._image_rect()
        projected = QPointF(
            new_rect.left() + image_anchor.x() * new_rect.width() / self.image.width,
            new_rect.top() + image_anchor.y() * new_rect.height() / self.image.height,
        )
        self.pan_offset += anchor_point - projected
        self._clamp_pan_offset()
        self.zoom_changed.emit(round(self.zoom * 100))
        self._refresh_snap_projection()
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
        """绘制浅色底板、图片边界、标签框、辅助线和吸附反馈。"""

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor(THEME.tokens.canvas_backplate))
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
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(
            QPen(
                QColor(THEME.tokens.canvas_image_boundary),
                THEME.tokens.canvas_image_boundary_width,
            )
        )
        painter.drawRect(image_rect)
        hovered_id = self._hovered_annotation_id()
        for annotation in self.annotations:
            self._paint_annotation(
                painter,
                annotation,
                annotation.id == self.selected_id,
                annotation.id == hovered_id,
            )
        if self._temporary is not None:
            draft_fill = QColor(THEME.tokens.brand_primary)
            draft_fill.setAlpha(THEME.tokens.annotation_fill_alpha)
            painter.setBrush(draft_fill)
            painter.setPen(QPen(QColor(THEME.tokens.brand_primary), 2, Qt.PenStyle.DashLine))
            painter.drawRect(self._temporary.normalized())
            self._paint_draft_size(painter, self._temporary.normalized())
        if self._draft.anchor is not None:
            painter.setBrush(QColor(THEME.tokens.brand_primary))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(self._draft.anchor, 4, 4)
        self._paint_crosshair(painter)
        self._paint_snap_feedback(painter)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        """矩形工具开始绘制，选择工具命中框或八点控制柄。"""

        if self.image is None:
            return super().mousePressEvent(event)
        point = event.position()
        self._hover_point = point
        if event.button() == Qt.MouseButton.MiddleButton:
            if self._image_rect().contains(point):
                self._middle_panning = True
                self._drag_origin = point
                self._pan_start = QPointF(self.pan_offset)
                self._refresh_cursor(point)
                event.accept()
                self.update()
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(event)
        if self.tool == CanvasTool.PAN:
            if self._image_rect().contains(point):
                self._left_panning = True
                self._drag_origin = point
                self._pan_start = QPointF(self.pan_offset)
                self._refresh_cursor(point)
            return
        if self.managed_read_only:
            return
        if self.tool == CanvasTool.RECTANGLE:
            if self.current_label_id is None:
                return
            projection = self._project_canvas_point(point)
            self._snap_projection = projection if not projection.inside_image else None
            self._draft.press_raw_point = QPointF(point)
            self._draft.press_point = projection.canvas_point
            self._draft.current_point = projection.canvas_point
            start = self._draft.anchor or projection.canvas_point
            self._temporary = QRectF(start, projection.canvas_point)
            self.update()
            return
        if not self._image_rect().contains(point):
            self.selected_id = None
            self._refresh_cursor(point)
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
        self._refresh_cursor(point)
        self.shape_selected.emit(hit.id)
        self.update()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        """实时预览新框、移动或缩放，但只在释放时形成一次撤销记录。"""

        point = event.position()
        self._hover_point = point
        if self._middle_panning or self._left_panning:
            self._snap_projection = None
            if self._drag_origin is not None:
                self.pan_offset = self._pan_start + point - self._drag_origin
                self._clamp_pan_offset()
            # 平移会改变图片在画布中的位置，绘制阶段会使用新图片范围重新裁切辅助线。
            self._refresh_cursor(point)
            self.update()
            return
        if self.managed_read_only:
            self._snap_projection = None
            self._refresh_cursor(point)
            self.update()
            return
        if self.tool == CanvasTool.RECTANGLE and self.current_label_id is not None:
            projection = self._project_canvas_point(point)
            self._snap_projection = projection if not projection.inside_image else None
            if self._draft.anchor is not None or self._draft.press_point is not None:
                self._draft.current_point = projection.canvas_point
                start = self._draft.anchor or self._draft.press_point
                self._temporary = QRectF(start, projection.canvas_point)
            self._refresh_cursor(point)
            self.update()
            return
        self._snap_projection = None
        if self._drag_snapshot is None or self._drag_origin is None:
            self._refresh_cursor(point)
            # 默认选择模式也必须立即刷新，不能等到切换工具后才显示最新辅助线。
            self.update()
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
        self._refresh_cursor(point)
        self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        """有效手势完成后只更新内存文档并通知外层刷新列表。"""

        if event.button() == Qt.MouseButton.MiddleButton:
            self._middle_panning = False
            self._drag_origin = None
            self._hover_point = event.position()
            self._refresh_snap_projection()
            self._refresh_cursor(event.position())
            self.update()
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        if self._suppress_left_release:
            # Qt 的双击序列在模态标签窗口关闭后仍会补发一次 release；该事件不属于拖动。
            self._suppress_left_release = False
            self._drag_origin = None
            self._drag_snapshot = None
            self._active_handle = None
            self._hover_point = event.position()
            self._refresh_snap_projection()
            self._refresh_cursor(event.position())
            self.update()
            event.accept()
            return
        if self._left_panning:
            self._left_panning = False
            self._drag_origin = None
            self._hover_point = event.position()
            self._refresh_snap_projection()
            self._refresh_cursor(event.position())
            self.update()
            return
        if self.managed_read_only:
            return
        if self.tool == CanvasTool.RECTANGLE and self._draft.press_point is not None:
            projection = self._project_canvas_point(event.position())
            point = projection.canvas_point
            self._snap_projection = projection if not projection.inside_image else None
            anchor = self._draft.anchor
            press = self._draft.press_point
            raw_press = self._draft.press_raw_point or press
            distance = abs(event.position().x() - raw_press.x()) + abs(
                event.position().y() - raw_press.y()
            )
            if anchor is not None:
                self._commit_rectangle(anchor, point)
            elif distance >= QApplication.startDragDistance():
                self._commit_rectangle(press, point)
            else:
                self._draft.anchor = press
                self._draft.current_point = point
                self._draft.press_point = None
                self._draft.press_raw_point = None
                self._temporary = QRectF(press, point)
                self.update()
            return
        elif self._drag_snapshot is not None:
            current = next(
                (item for item in self.annotations if item.id == self._drag_snapshot.id), None
            )
            if current is not None and _annotation_geometry_changed(
                current,
                self._drag_snapshot,
            ):
                snapshot = list(self.annotations)
                snapshot[snapshot.index(current)] = self._drag_snapshot
                self._undo.append(snapshot)
                self._redo.clear()
                self._emit_change("resize" if self._active_handle else "move")
        self._drag_origin = None
        self._drag_snapshot = None
        self._active_handle = None
        self._hover_point = event.position()
        self._refresh_snap_projection()
        self._refresh_cursor(event.position())
        self.update()

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        """双击矩形请求外层打开统一标签选择器。"""

        if event.button() == Qt.MouseButton.LeftButton and not self.managed_read_only:
            hit = self._shape_at(event.position())
            if hit is not None:
                # 双击只表示改派标签，必须在打开模态窗口前终止第二次按下创建的拖动状态。
                self._drag_origin = None
                self._drag_snapshot = None
                self._active_handle = None
                self._suppress_left_release = True
                self._draft = RectangleDraft()
                self._temporary = None
                self._snap_projection = None
                self.set_tool(CanvasTool.SELECT)
                self.select_shape(hit.id)
                self.shape_double_clicked.emit(hit.id)
                return
        super().mouseDoubleClickEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        """应用快捷键由 ActionRegistry 管理，画布只保留原生事件链。"""

        super().keyPressEvent(event)

    def wheelEvent(self, event: QWheelEvent) -> None:
        """Ctrl 缩放优先于 Alt 横移；普通滚轮继续纵向滚动。"""

        self._hover_point = event.position()
        angle_delta = event.angleDelta().y()
        pixel_delta = event.pixelDelta().y()
        delta = angle_delta or pixel_delta
        if not delta:
            event.ignore()
            return
        modifiers = event.modifiers()
        if modifiers & Qt.KeyboardModifier.ControlModifier:
            steps = delta / (120.0 if angle_delta else 240.0)
            self._set_zoom(self.zoom * (1.2**steps), event.position())
        elif modifiers & Qt.KeyboardModifier.AltModifier:
            self.pan_offset.setX(self.pan_offset.x() + delta)
        else:
            self.pan_offset.setY(self.pan_offset.y() + delta)
        if not (modifiers & Qt.KeyboardModifier.ControlModifier):
            self._clamp_pan_offset()
            self._refresh_snap_projection()
            self.update()
        event.accept()

    def leaveEvent(self, event: object) -> None:
        self._hover_point = None
        self._snap_projection = None
        self._refresh_cursor(reset=True)
        self.update()
        super().leaveEvent(event)

    def focusOutEvent(self, event: QFocusEvent) -> None:
        """失去焦点时清除可能锁定的操作指针，避免切窗后残留。"""

        self._refresh_cursor(reset=True)
        super().focusOutEvent(event)

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
        hovered: bool,
    ) -> None:
        """绘制标签框；悬停只增强视觉，不修改任何标注状态。"""

        label = self.labels.get(annotation.label_id)
        if label is None:
            return
        rect = self._annotation_rect(annotation)
        color = QColor(label.color)
        fill = QColor(color)
        if selected and hovered:
            fill_alpha = THEME.tokens.annotation_selected_hover_fill_alpha
        elif hovered:
            fill_alpha = THEME.tokens.annotation_hover_fill_alpha
        elif selected:
            fill_alpha = THEME.tokens.annotation_selected_fill_alpha
        else:
            fill_alpha = THEME.tokens.annotation_fill_alpha
        fill.setAlpha(fill_alpha)
        border = QColor(color)
        border.setAlpha(
            THEME.tokens.annotation_hover_border_alpha
            if hovered
            else THEME.tokens.annotation_border_alpha
        )
        painter.setBrush(fill)
        painter.setPen(
            QPen(
                border,
                THEME.tokens.annotation_selected_line_width
                if selected
                else THEME.tokens.annotation_hover_line_width
                if hovered
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

        painter.setPen(QColor(THEME.tokens.text_secondary))
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
            painter.setPen(QColor(THEME.tokens.text_muted))
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
        geometry = self._crosshair_geometry()
        if geometry is None:
            return
        visible, point = geometry
        underlay = QColor(THEME.tokens.canvas_crosshair_light)
        underlay.setAlpha(THEME.tokens.canvas_crosshair_underlay_alpha)
        foreground = QColor(THEME.tokens.canvas_crosshair_dark)
        foreground.setAlpha(THEME.tokens.canvas_crosshair_alpha)
        painter.save()
        painter.setClipRect(visible)
        painter.setPen(QPen(underlay, THEME.tokens.canvas_crosshair_underlay_width))
        painter.drawLine(QPointF(visible.left(), point.y()), QPointF(visible.right(), point.y()))
        painter.drawLine(QPointF(point.x(), visible.top()), QPointF(point.x(), visible.bottom()))
        painter.setPen(
            QPen(
                foreground,
                THEME.tokens.canvas_crosshair_width,
                Qt.PenStyle.DashLine,
            )
        )
        painter.drawLine(QPointF(visible.left(), point.y()), QPointF(visible.right(), point.y()))
        painter.drawLine(QPointF(point.x(), visible.top()), QPointF(point.x(), visible.bottom()))
        painter.restore()

    def _paint_snap_feedback(self, painter: QPainter) -> None:
        """在底板输入被钳制的位置绘制空心圆点和短边缘刻度。"""

        projection = self._snap_projection
        if projection is None or self.image is None or not projection.clamped_edges:
            return
        image_rect = self._image_rect()
        visible = image_rect.intersected(QRectF(self.rect()))
        if visible.isEmpty() or not visible.contains(projection.canvas_point):
            return
        color = QColor(THEME.tokens.canvas_snap_feedback)
        color.setAlpha(THEME.tokens.canvas_snap_feedback_alpha)
        pen = QPen(
            color,
            THEME.tokens.canvas_snap_feedback_width,
            Qt.PenStyle.SolidLine,
        )
        point = projection.canvas_point
        radius = THEME.tokens.canvas_snap_marker_size / 2
        tick = THEME.tokens.canvas_snap_tick_length / 2
        painter.save()
        painter.setClipRect(visible.adjusted(-2, -2, 2, 2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(pen)
        painter.drawEllipse(point, radius, radius)
        if (
            ImageEdge.LEFT in projection.clamped_edges
            or ImageEdge.RIGHT in projection.clamped_edges
        ):
            painter.drawLine(
                QPointF(point.x(), max(visible.top(), point.y() - tick)),
                QPointF(point.x(), min(visible.bottom(), point.y() + tick)),
            )
        if (
            ImageEdge.TOP in projection.clamped_edges
            or ImageEdge.BOTTOM in projection.clamped_edges
        ):
            painter.drawLine(
                QPointF(max(visible.left(), point.x() - tick), point.y()),
                QPointF(min(visible.right(), point.x() + tick), point.y()),
            )
        painter.restore()

    def _crosshair_geometry(self) -> tuple[QRectF, QPointF] | None:
        """按当前指针和图片变换计算辅助线，避免平移后沿用旧坐标。"""

        point = self._hover_point
        if point is None:
            pointer = self.mapFromGlobal(QCursor.pos())
            point = QPointF(pointer) if self.rect().contains(pointer) else None
        if point is None or self.image is None:
            return None
        visible = self._image_rect().intersected(QRectF(self.rect()))
        if visible.isEmpty() or not visible.contains(point):
            return None
        return visible, QPointF(point)

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
        background = QColor(THEME.tokens.text_primary)
        background.setAlpha(220)
        painter.fillRect(box, background)
        painter.setPen(QColor(THEME.tokens.surface))
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
        self._snap_projection = None
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

    def _hovered_annotation_id(self) -> str | None:
        """返回指针下可见矩形；查询过程没有领域数据副作用。"""

        if self._hover_point is None or self.image is None:
            return None
        if self._middle_panning or self._left_panning:
            return None
        if not self._image_rect().contains(self._hover_point):
            return None
        hovered = self._shape_at(self._hover_point)
        return hovered.id if hovered is not None else None

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

    @staticmethod
    def _handle_cursor(handle: str) -> Qt.CursorShape:
        """把八控制点映射为与屏幕拖动方向一致的 Qt 系统指针。"""

        if handle in {"left", "right"}:
            return Qt.CursorShape.SizeHorCursor
        if handle in {"top", "bottom"}:
            return Qt.CursorShape.SizeVerCursor
        if handle in {"top_left", "bottom_right"}:
            return Qt.CursorShape.SizeFDiagCursor
        return Qt.CursorShape.SizeBDiagCursor

    def _cursor_shape(self, point: QPointF | None = None) -> Qt.CursorShape:
        """按单一优先级解析系统指针，避免事件处理器各自维护状态。"""

        if self._middle_panning or self._left_panning:
            return Qt.CursorShape.ClosedHandCursor
        if self.image is None:
            return Qt.CursorShape.ArrowCursor
        if self.tool == CanvasTool.PAN:
            return Qt.CursorShape.OpenHandCursor
        if self.tool == CanvasTool.RECTANGLE:
            if not self.managed_read_only and self.current_label_id is not None:
                return Qt.CursorShape.CrossCursor
            return Qt.CursorShape.ArrowCursor
        if self.managed_read_only:
            return Qt.CursorShape.ArrowCursor
        if self._drag_snapshot is not None:
            if self._active_handle is not None:
                return self._handle_cursor(self._active_handle)
            return Qt.CursorShape.SizeAllCursor
        target = point if point is not None else self._hover_point
        if target is None:
            return Qt.CursorShape.ArrowCursor
        selected = next(
            (item for item in self.annotations if item.id == self.selected_id),
            None,
        )
        if selected is not None:
            handle = self._handle_at(target, selected)
            if handle is not None:
                return self._handle_cursor(handle)
        if self._shape_at(target) is not None:
            return Qt.CursorShape.SizeAllCursor
        return Qt.CursorShape.ArrowCursor

    def _refresh_cursor(self, point: QPointF | None = None, *, reset: bool = False) -> None:
        """画布设置系统指针的唯一入口；视觉变化不得触发标注命令。"""

        shape = Qt.CursorShape.ArrowCursor if reset else self._cursor_shape(point)
        if self.cursor().shape() != shape:
            self.setCursor(shape)

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

    def _project_canvas_point(self, point: QPointF) -> CanvasProjection:
        """把事件点投影到当前图片；调用方必须先确认图片已经加载。"""

        if self.image is None:
            raise ValueError("无图片时不能投影画布点")
        return project_point_to_image_bounds(
            point,
            self._image_rect(),
            QSizeF(self.image.width, self.image.height),
        )

    def _refresh_snap_projection(self) -> None:
        """视图变化后按当前鼠标位置重新计算底板吸附反馈。"""

        if (
            self.image is None
            or self._hover_point is None
            or self.tool != CanvasTool.RECTANGLE
            or self.managed_read_only
            or self.current_label_id is None
            or self._middle_panning
            or self._left_panning
        ):
            self._snap_projection = None
            return
        projection = self._project_canvas_point(self._hover_point)
        self._snap_projection = projection if not projection.inside_image else None

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


def _annotation_geometry_changed(
    current: AnnotationItemViewData,
    original: AnnotationItemViewData,
) -> bool:
    """释放手势只比较几何，标签改派或兼容元数据变化不能伪装成移动。"""

    return any(
        abs(current_value - original_value) > 1e-9
        for current_value, original_value in zip(
            (current.x1, current.y1, current.x2, current.y2),
            (original.x1, original.y1, original.x2, original.y2),
            strict=True,
        )
    )
