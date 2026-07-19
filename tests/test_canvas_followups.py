"""A0.5～A0.7 画布后续交互的独立回归。"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QFocusEvent

from datumdock.ui.preview_canvas import CanvasTool, PreviewAnnotationCanvas
from datumdock.ui.prototype_models import (
    AnnotationItemViewData,
    ImageItemViewData,
    ImageStatus,
    LabelViewData,
)


def _canvas(qtbot) -> PreviewAnnotationCanvas:
    """创建不访问文件系统的共享画布，供普通与预览行为共同验证。"""

    canvas = PreviewAnnotationCanvas()
    qtbot.addWidget(canvas)
    canvas.resize(760, 520)
    canvas.load_preview(
        ImageItemViewData(
            "image-1",
            "sample.png",
            ImageStatus.COMPLETED,
            320,
            180,
            1,
            0,
        ),
        (),
        (),
    )
    canvas.show()
    return canvas


def _canvas_with_annotation(qtbot) -> PreviewAnnotationCanvas:
    """创建含一个已选中可编辑矩形的画布。"""

    canvas = PreviewAnnotationCanvas()
    qtbot.addWidget(canvas)
    canvas.resize(760, 520)
    label = LabelViewData(
        "label-1",
        0,
        "metal_part",
        "金属零件",
        "用于鼠标指针命中测试",
        (),
        "#5B83E6",
        1,
    )
    annotation = AnnotationItemViewData(
        "shape-1",
        label.id,
        80.0,
        45.0,
        240.0,
        135.0,
    )
    canvas.load_preview(
        ImageItemViewData(
            "image-1",
            "sample.png",
            ImageStatus.COMPLETED,
            320,
            180,
            1,
            1,
        ),
        (label,),
        (annotation,),
    )
    canvas.show()
    return canvas


def test_crosshair_persists_during_tools_and_middle_pan_without_side_effects(qtbot) -> None:
    """辅助线必须独立于工具和平移，并且只产生视图刷新。"""

    canvas = _canvas(qtbot)
    canvas.set_zoom_percent(400)
    changed: list[str] = []
    canvas.edit_committed.connect(changed.append)
    image_rect = canvas._image_rect()
    point = image_rect.center().toPoint()

    for tool in (CanvasTool.SELECT, CanvasTool.RECTANGLE, CanvasTool.PAN):
        canvas.set_tool(tool)
        qtbot.mouseMove(canvas, point)
        geometry = canvas._crosshair_geometry()
        assert geometry is not None
        visible, pointer = geometry
        assert visible.contains(pointer)

    canvas.set_tool(CanvasTool.SELECT)
    qtbot.mousePress(canvas, Qt.MouseButton.MiddleButton, pos=point)
    moved = point + QPoint(24, 16)
    qtbot.mouseMove(canvas, moved)
    assert canvas._middle_panning is True
    geometry = canvas._crosshair_geometry()
    assert geometry is not None
    visible, pointer = geometry
    assert pointer == QPointF(moved)
    assert visible == canvas._image_rect().intersected(canvas.rect())
    qtbot.mouseRelease(canvas, Qt.MouseButton.MiddleButton, pos=moved)

    assert canvas._crosshair_geometry() is not None
    assert changed == []
    assert canvas._undo == []


@pytest.mark.parametrize("percent", (100, 800, 3200, 6400))
def test_crosshair_uses_current_double_precision_transform(percent: int, qtbot) -> None:
    """各目标倍率下辅助线位置必须映射回同一个原图像素。"""

    canvas = _canvas(qtbot)
    canvas.set_zoom_percent(percent)
    source = QPointF(160.0, 90.0)
    image_rect = canvas._image_rect()
    pointer = QPointF(
        image_rect.left() + source.x() * image_rect.width() / canvas.image.width,
        image_rect.top() + source.y() * image_rect.height() / canvas.image.height,
    )
    canvas._hover_point = pointer

    geometry = canvas._crosshair_geometry()
    assert geometry is not None
    _visible, actual_pointer = geometry
    restored = canvas._canvas_to_image(actual_pointer, canvas._image_rect())
    assert restored.x() == pytest.approx(source.x(), abs=1e-9)
    assert restored.y() == pytest.approx(source.y(), abs=1e-9)


def test_crosshair_hides_without_a_loaded_image(qtbot) -> None:
    """空画布与加载失败后的清空状态都不能残留辅助线。"""

    canvas = _canvas(qtbot)
    canvas._hover_point = canvas._image_rect().center()
    assert canvas._crosshair_geometry() is not None
    canvas.clear_preview()
    assert canvas._crosshair_geometry() is None


def test_cursor_maps_shape_body_and_all_eight_handles(qtbot) -> None:
    """框内和八个控制点必须显示与实际操作方向一致的系统指针。"""

    canvas = _canvas_with_annotation(qtbot)
    annotation = canvas.annotations[0]
    rect = canvas._annotation_rect(annotation)
    expected = {
        "top_left": Qt.CursorShape.SizeFDiagCursor,
        "top": Qt.CursorShape.SizeVerCursor,
        "top_right": Qt.CursorShape.SizeBDiagCursor,
        "right": Qt.CursorShape.SizeHorCursor,
        "bottom_right": Qt.CursorShape.SizeFDiagCursor,
        "bottom": Qt.CursorShape.SizeVerCursor,
        "bottom_left": Qt.CursorShape.SizeBDiagCursor,
        "left": Qt.CursorShape.SizeHorCursor,
    }

    for name, point in canvas._handle_points(rect).items():
        qtbot.mouseMove(canvas, point.toPoint())
        assert canvas.cursor().shape() == expected[name]

    qtbot.mouseMove(canvas, rect.center().toPoint())
    assert canvas.cursor().shape() == Qt.CursorShape.SizeAllCursor


def test_cursor_priority_drag_lock_read_only_and_focus_reset(qtbot) -> None:
    """工具优先级、拖动锁定、只读保护和失焦恢复必须集中生效。"""

    canvas = _canvas_with_annotation(qtbot)
    annotation = canvas.annotations[0]
    rect = canvas._annotation_rect(annotation)
    bottom_right = canvas._handle_points(rect)["bottom_right"].toPoint()

    canvas.set_tool(CanvasTool.SELECT)
    qtbot.mouseMove(canvas, bottom_right)
    assert canvas.cursor().shape() == Qt.CursorShape.SizeFDiagCursor
    qtbot.mousePress(canvas, Qt.MouseButton.LeftButton, pos=bottom_right)
    qtbot.mouseMove(canvas, bottom_right + QPoint(30, 20))
    assert canvas.cursor().shape() == Qt.CursorShape.SizeFDiagCursor
    qtbot.mouseRelease(canvas, Qt.MouseButton.LeftButton, pos=bottom_right + QPoint(30, 20))

    canvas.set_tool(CanvasTool.RECTANGLE)
    qtbot.mouseMove(canvas, canvas._image_rect().center().toPoint())
    assert canvas.cursor().shape() == Qt.CursorShape.CrossCursor
    qtbot.mousePress(
        canvas, Qt.MouseButton.MiddleButton, pos=canvas._image_rect().center().toPoint()
    )
    assert canvas.cursor().shape() == Qt.CursorShape.ClosedHandCursor
    qtbot.mouseRelease(
        canvas,
        Qt.MouseButton.MiddleButton,
        pos=canvas._image_rect().center().toPoint(),
    )
    assert canvas.cursor().shape() == Qt.CursorShape.CrossCursor

    canvas.set_tool(CanvasTool.SELECT)
    canvas.managed_read_only = True
    qtbot.mouseMove(canvas, canvas._annotation_rect(canvas.annotations[0]).center().toPoint())
    assert canvas.cursor().shape() == Qt.CursorShape.ArrowCursor

    canvas.managed_read_only = False
    qtbot.mouseMove(
        canvas,
        canvas._annotation_rect(canvas.annotations[0]).center().toPoint() + QPoint(1, 0),
    )
    assert canvas.cursor().shape() == Qt.CursorShape.SizeAllCursor
    canvas.focusOutEvent(QFocusEvent(QEvent.Type.FocusOut))
    assert canvas.cursor().shape() == Qt.CursorShape.ArrowCursor


def test_cursor_only_hover_does_not_change_document_or_history(qtbot) -> None:
    """鼠标指针反馈不得伪装成标注编辑。"""

    canvas = _canvas_with_annotation(qtbot)
    changed: list[str] = []
    canvas.edit_committed.connect(changed.append)
    before = tuple(canvas.annotations)
    rect = canvas._annotation_rect(canvas.annotations[0])

    for point in (*canvas._handle_points(rect).values(), rect.center()):
        qtbot.mouseMove(canvas, point.toPoint())

    assert tuple(canvas.annotations) == before
    assert canvas._undo == []
    assert changed == []
