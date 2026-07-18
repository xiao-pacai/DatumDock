"""支持矩形绘制、选择、移动、八点缩放与自动保存回调的标注画布。"""

from __future__ import annotations

import copy
from collections.abc import Callable

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPen, QPixmap, QWheelEvent
from PySide6.QtWidgets import QGraphicsPixmapItem, QGraphicsRectItem, QGraphicsScene, QGraphicsView

from datumdock.domain.models import AnnotationDocument, LabelSet, RectangleShape


class RectangleItem(QGraphicsRectItem):
    """画布中的单个矩形，支持移动和八个缩放把手。"""

    HANDLE_SIZE = 8.0

    def __init__(
        self,
        shape_id: str,
        rectangle: QRectF,
        color: str,
        on_started: Callable[[str], None],
        on_changed: Callable[[str], None],
        on_activated: Callable[[str], None],
    ) -> None:
        super().__init__(rectangle)
        self.shape_id = shape_id
        self._on_started = on_started
        self._on_changed = on_changed
        self._on_activated = on_activated
        self._active_handle: str | None = None
        self._changed = False
        self.setBrush(Qt.BrushStyle.NoBrush)
        self.setPen(QPen(QColor(color), 2))
        self.setFlags(
            QGraphicsRectItem.GraphicsItemFlag.ItemIsMovable
            | QGraphicsRectItem.GraphicsItemFlag.ItemIsSelectable
            | QGraphicsRectItem.GraphicsItemFlag.ItemSendsGeometryChanges
        )

    def set_color(self, color: str) -> None:
        """标签颜色变化后同步画布边框，不改变几何数据。"""

        self.setPen(QPen(QColor(color), 2))
        self.update()

    def paint(self, painter: QPainter, option: object, widget: object | None = None) -> None:
        """在选中状态绘制八个把手，未选中时保持低干扰的彩色边框。"""

        super().paint(painter, option, widget)
        if not self.isSelected():
            return
        painter.setPen(QPen(QColor("#35403C"), 1))
        painter.setBrush(QColor("#F7F6F2"))
        for point in self._handle_points().values():
            painter.drawRect(self._handle_rect(point))

    def mousePressEvent(self, event: QMouseEvent) -> None:
        """优先命中缩放把手；普通拖拽交给图元自身的移动逻辑。"""

        self._on_started(self.shape_id)
        self._changed = False
        self._active_handle = self._handle_at(event.pos())
        if self._active_handle is not None:
            self._changed = True
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        """根据把手位置实时更新矩形，允许拖过对侧并自动标准化。"""

        if self._active_handle is None:
            self._changed = True
            super().mouseMoveEvent(event)
            return
        rectangle = QRectF(self.rect())
        point = event.pos()
        handle = self._active_handle
        if "left" in handle:
            rectangle.setLeft(point.x())
        if "right" in handle:
            rectangle.setRight(point.x())
        if "top" in handle:
            rectangle.setTop(point.y())
        if "bottom" in handle:
            rectangle.setBottom(point.y())
        normalized = rectangle.normalized()
        if normalized.width() >= 2 and normalized.height() >= 2:
            self.setRect(normalized)
        event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        """手势结束后只发出一次同步通知，避免拖动过程中频繁落盘。"""

        was_resizing = self._active_handle is not None
        self._active_handle = None
        if was_resizing:
            event.accept()
        else:
            super().mouseReleaseEvent(event)
        if self._changed:
            self._on_changed(self.shape_id)
        self._changed = False

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        """双击矩形交给主界面选择新标签，契合常见标注软件习惯。"""

        self._on_activated(self.shape_id)
        event.accept()

    def _handle_points(self) -> dict[str, QPointF]:
        rectangle = self.rect()
        return {
            "top_left": rectangle.topLeft(),
            "top": QPointF(rectangle.center().x(), rectangle.top()),
            "top_right": rectangle.topRight(),
            "right": QPointF(rectangle.right(), rectangle.center().y()),
            "bottom_right": rectangle.bottomRight(),
            "bottom": QPointF(rectangle.center().x(), rectangle.bottom()),
            "bottom_left": rectangle.bottomLeft(),
            "left": QPointF(rectangle.left(), rectangle.center().y()),
        }

    def _handle_rect(self, point: QPointF) -> QRectF:
        return QRectF(
            point.x() - self.HANDLE_SIZE / 2,
            point.y() - self.HANDLE_SIZE / 2,
            self.HANDLE_SIZE,
            self.HANDLE_SIZE,
        )

    def _handle_at(self, point: QPointF) -> str | None:
        if not self.isSelected():
            return None
        for name, handle_point in self._handle_points().items():
            if self._handle_rect(handle_point).contains(point):
                return name
        return None


class AnnotationCanvas(QGraphicsView):
    """只负责标注交互与像素坐标，持久化由外层数据集池服务完成。"""

    document_changed = Signal()
    rectangle_activated = Signal(str)
    message = Signal(str)

    def __init__(self, parent: object | None = None) -> None:
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self._pixmap_item: QGraphicsPixmapItem | None = None
        self.document: AnnotationDocument | None = None
        self.label_set: LabelSet | None = None
        self.current_label_id: str | None = None
        self._items: dict[str, RectangleItem] = {}
        self._drawing_origin: QPointF | None = None
        self._temporary_item: QGraphicsRectItem | None = None
        self._undo_stack: list[AnnotationDocument] = []
        self._redo_stack: list[AnnotationDocument] = []
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setBackgroundBrush(QColor("#EEECE6"))
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)

    def set_document(
        self,
        image_path: str,
        document: AnnotationDocument,
        label_set: LabelSet,
    ) -> None:
        """切换样本时重新建立图元，撤销栈严格限制在当前图片。"""

        pixmap = QPixmap(image_path)
        if pixmap.isNull():
            raise ValueError("无法读取受管图片")
        self._scene.clear()
        self._items.clear()
        self._pixmap_item = self._scene.addPixmap(pixmap)
        self._pixmap_item.setZValue(0)
        self.document = copy.deepcopy(document)
        self.label_set = label_set
        self._undo_stack.clear()
        self._redo_stack.clear()
        self._scene.setSceneRect(QRectF(pixmap.rect()))
        self._rebuild_rectangles()
        self.fit_image()

    def clear_document(self) -> None:
        """在没有选中图片时清空画布，避免误把旧标注显示到新上下文。"""

        self._scene.clear()
        self._items.clear()
        self._pixmap_item = None
        self.document = None
        self.label_set = None
        self._undo_stack.clear()
        self._redo_stack.clear()

    def set_current_label(self, label_id: str | None) -> None:
        """由标签面板设置下一次画框使用的稳定标签 ID。"""

        self.current_label_id = label_id

    def delete_selected(self) -> None:
        """删除选中矩形并发出自动保存通知，未选中时不影响图片文件。"""

        if self.document is None:
            return
        selected = [item for item in self._items.values() if item.isSelected()]
        if not selected:
            return
        self._push_undo()
        selected_ids = {item.shape_id for item in selected}
        self.document.rectangles = [
            rectangle for rectangle in self.document.rectangles if rectangle.id not in selected_ids
        ]
        self._rebuild_rectangles()
        self.document_changed.emit()

    def reassign_label(self, shape_id: str, label_id: str) -> None:
        """为双击选择的矩形切换标签，使用稳定 ID 而非易变训练名。"""

        if self.document is None or self.label_set is None:
            return
        self.label_set.get_label(label_id)
        rectangle = self._shape_by_id(shape_id)
        if rectangle.label_id == label_id:
            return
        self._push_undo()
        rectangle.label_id = label_id
        self._rebuild_rectangles()
        self.document_changed.emit()

    def undo(self) -> None:
        """撤销最近一次画框、删除、移动、缩放或换标签，并自动保存结果。"""

        if self.document is None or not self._undo_stack:
            return
        self._redo_stack.append(copy.deepcopy(self.document))
        self.document = self._undo_stack.pop()
        self._rebuild_rectangles()
        self.document_changed.emit()

    def redo(self) -> None:
        """恢复被撤销的一次标注编辑，并自动保存结果。"""

        if self.document is None or not self._redo_stack:
            return
        self._undo_stack.append(copy.deepcopy(self.document))
        self.document = self._redo_stack.pop()
        self._rebuild_rectangles()
        self.document_changed.emit()

    def fit_image(self) -> None:
        """按当前可见区域适配整张图片，适用于切图和 F 快捷键。"""

        if self._pixmap_item is not None:
            self.resetTransform()
            self.fitInView(self._pixmap_item, Qt.AspectRatioMode.KeepAspectRatio)

    def zoom_in(self) -> None:
        """平滑放大当前鼠标附近的图像区域。"""

        self.scale(1.2, 1.2)

    def zoom_out(self) -> None:
        """平滑缩小当前图像区域。"""

        self.scale(1 / 1.2, 1 / 1.2)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        """空白图像区域按住左键开始创建矩形，图元交互仍优先处理。"""

        if (
            event.button() == Qt.MouseButton.LeftButton
            and self.document is not None
            and self.current_label_id is not None
            and self.itemAt(event.position().toPoint()) is self._pixmap_item
        ):
            self._drawing_origin = self.mapToScene(event.position().toPoint())
            self._temporary_item = self._scene.addRect(
                QRectF(self._drawing_origin, self._drawing_origin),
                QPen(QColor("#78978C"), 2, Qt.PenStyle.DashLine),
            )
            self._temporary_item.setZValue(2)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        """绘制过程中显示临时矩形，释放鼠标前不修改领域文档。"""

        if self._drawing_origin is not None and self._temporary_item is not None:
            current = self.mapToScene(event.position().toPoint())
            self._temporary_item.setRect(QRectF(self._drawing_origin, current).normalized())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        """有效新矩形写入内存文档后立即通知外层执行原子保存。"""

        if self._drawing_origin is not None and self._temporary_item is not None:
            rectangle = self._temporary_item.rect().normalized()
            self._scene.removeItem(self._temporary_item)
            self._temporary_item = None
            self._drawing_origin = None
            if rectangle.width() >= 2 and rectangle.height() >= 2 and self.document is not None:
                self._push_undo()
                self.document.rectangles.append(
                    RectangleShape(
                        label_id=self.current_label_id,
                        x1=rectangle.left(),
                        y1=rectangle.top(),
                        x2=rectangle.right(),
                        y2=rectangle.bottom(),
                    )
                )
                self._rebuild_rectangles()
                self.document_changed.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def wheelEvent(self, event: QWheelEvent) -> None:
        """滚轮缩放；用户仍可用中键拖动或滚动条平移。"""

        if event.angleDelta().y() > 0:
            self.zoom_in()
        else:
            self.zoom_out()
        event.accept()

    def _rebuild_rectangles(self) -> None:
        """用文档的真值重建矩形图元，避免交互缓存成为第二事实来源。"""

        for item in self._items.values():
            self._scene.removeItem(item)
        self._items.clear()
        if self.document is None or self.label_set is None:
            return
        by_label_id = {label.id: label for label in self.label_set.labels}
        for rectangle in self.document.rectangles:
            label = by_label_id.get(rectangle.label_id)
            if label is None:
                continue
            item = RectangleItem(
                rectangle.id,
                QRectF(rectangle.x1, rectangle.y1, rectangle.width, rectangle.height),
                label.color,
                self._begin_rectangle_change,
                self._finish_rectangle_change,
                self.rectangle_activated.emit,
            )
            item.setZValue(1)
            self._scene.addItem(item)
            self._items[rectangle.id] = item

    def _begin_rectangle_change(self, _: str) -> None:
        """在矩形移动或缩放手势真正开始前保存撤销快照。"""

        self._push_undo()

    def _finish_rectangle_change(self, shape_id: str) -> None:
        """把图元的场景几何同步回像素坐标领域对象。"""

        if self.document is None:
            return
        item = self._items.get(shape_id)
        if item is None:
            return
        rectangle = item.mapRectToScene(item.rect()).boundingRect().normalized()
        shape = self._shape_by_id(shape_id)
        shape.x1 = rectangle.left()
        shape.y1 = rectangle.top()
        shape.x2 = rectangle.right()
        shape.y2 = rectangle.bottom()
        self.document_changed.emit()

    def _push_undo(self) -> None:
        """保留有限深度的当前图片历史，避免长时间标注占用无上限内存。"""

        if self.document is None:
            return
        self._undo_stack.append(copy.deepcopy(self.document))
        del self._undo_stack[:-100]
        self._redo_stack.clear()

    def _shape_by_id(self, shape_id: str) -> RectangleShape:
        """按稳定形状 ID 查找，内部不依赖图元序号。"""

        if self.document is None:
            raise RuntimeError("当前没有标注文档")
        for shape in self.document.rectangles:
            if shape.id == shape_id:
                return shape
        raise KeyError(f"找不到矩形标注: {shape_id}")
