"""A0.5～A0.7 画布后续交互的独立回归。"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, QRectF, QSizeF, Qt
from PySide6.QtGui import QFocusEvent

from datumdock.ui.preview_canvas import (
    CanvasTool,
    ImageEdge,
    PreviewAnnotationCanvas,
    project_point_to_image_bounds,
)
from datumdock.ui.prototype_models import (
    AnnotationItemViewData,
    ImageItemViewData,
    ImageStatus,
    LabelViewData,
)
from datumdock.ui.theme import THEME


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


@pytest.mark.parametrize(
    ("point", "expected_canvas", "expected_image", "expected_edges"),
    (
        (QPointF(5, 80), QPointF(10, 80), QPointF(0, 90), {ImageEdge.LEFT}),
        (QPointF(115, 80), QPointF(110, 80), QPointF(320, 90), {ImageEdge.RIGHT}),
        (QPointF(60, 10), QPointF(60, 20), QPointF(160, 0), {ImageEdge.TOP}),
        (QPointF(60, 150), QPointF(60, 140), QPointF(160, 180), {ImageEdge.BOTTOM}),
        (
            QPointF(5, 10),
            QPointF(10, 20),
            QPointF(0, 0),
            {ImageEdge.LEFT, ImageEdge.TOP},
        ),
        (
            QPointF(115, 10),
            QPointF(110, 20),
            QPointF(320, 0),
            {ImageEdge.RIGHT, ImageEdge.TOP},
        ),
        (
            QPointF(5, 150),
            QPointF(10, 140),
            QPointF(0, 180),
            {ImageEdge.LEFT, ImageEdge.BOTTOM},
        ),
        (
            QPointF(115, 150),
            QPointF(110, 140),
            QPointF(320, 180),
            {ImageEdge.RIGHT, ImageEdge.BOTTOM},
        ),
        (QPointF(35, 50), QPointF(35, 50), QPointF(80, 45), set()),
    ),
)
def test_projection_clamps_each_axis_to_image_edges(
    point: QPointF,
    expected_canvas: QPointF,
    expected_image: QPointF,
    expected_edges: set[ImageEdge],
) -> None:
    """四边、四角和图片内点必须由同一个纯函数逐轴投影。"""

    projection = project_point_to_image_bounds(
        point,
        QRectF(10, 20, 100, 120),
        QSizeF(320, 180),
    )
    assert projection.canvas_point == expected_canvas
    assert projection.image_point == expected_image
    assert projection.clamped_edges == frozenset(expected_edges)
    assert projection.inside_image is (not expected_edges)


def test_light_backplate_tokens_are_centralized() -> None:
    """浅色底板与图片细边界必须来自语义主题令牌。"""

    assert THEME.tokens.canvas_backplate == "#E9EEF4"
    assert THEME.tokens.canvas_image_boundary == "#C5D0DC"
    assert THEME.tokens.canvas_background == THEME.tokens.canvas_backplate


@pytest.mark.parametrize("percent", (100, 800, 3200, 6400))
def test_projection_and_handle_cursor_stay_accurate_at_high_zoom(percent: int, qtbot) -> None:
    """高倍率下投影、控制点命中和系统指针不能产生倍率相关漂移。"""

    canvas = _canvas_with_annotation(qtbot)
    canvas.set_zoom_percent(percent)
    target_source = QPointF(80.0, 45.0)
    image_rect = canvas._image_rect()
    target_canvas = QPointF(
        image_rect.left() + target_source.x() * image_rect.width() / canvas.image.width,
        image_rect.top() + target_source.y() * image_rect.height() / canvas.image.height,
    )
    canvas.pan_offset += QPointF(canvas.rect().center()) - target_canvas
    canvas._clamp_pan_offset()
    top_left = canvas._handle_points(canvas._annotation_rect(canvas.annotations[0]))["top_left"]
    restored = canvas._canvas_to_image(top_left, canvas._image_rect())
    assert restored.x() == pytest.approx(target_source.x(), abs=1e-9)
    assert restored.y() == pytest.approx(target_source.y(), abs=1e-9)
    qtbot.mouseMove(canvas, QPoint(10, 10))
    qtbot.mouseMove(canvas, top_left.toPoint())
    assert canvas.cursor().shape() == Qt.CursorShape.SizeFDiagCursor


def test_drag_and_two_click_backplate_inputs_share_clamped_coordinates(qtbot) -> None:
    """拖拽与两点创建从底板跨过图片时必须得到同一贴边矩形。"""

    drag_canvas = _canvas_with_annotation(qtbot)
    drag_rect = drag_canvas._image_rect()
    start = QPoint(
        round(drag_rect.left() - 18),
        round(drag_rect.top() + drag_rect.height() * 0.25),
    )
    end = QPoint(
        round(drag_rect.right() + 18),
        round(drag_rect.bottom() - drag_rect.height() * 0.25),
    )
    before = len(drag_canvas.annotations)
    drag_canvas.set_tool(CanvasTool.RECTANGLE)
    qtbot.mousePress(drag_canvas, Qt.MouseButton.LeftButton, pos=start)
    qtbot.mouseMove(drag_canvas, end)
    assert drag_canvas._snap_projection is not None
    qtbot.mouseRelease(drag_canvas, Qt.MouseButton.LeftButton, pos=end)
    assert len(drag_canvas.annotations) == before + 1
    dragged = drag_canvas.annotations[-1]

    click_canvas = _canvas_with_annotation(qtbot)
    click_rect = click_canvas._image_rect()
    click_start = QPoint(
        round(click_rect.left() - 18),
        round(click_rect.top() + click_rect.height() * 0.25),
    )
    click_end = QPoint(
        round(click_rect.right() + 18),
        round(click_rect.bottom() - click_rect.height() * 0.25),
    )
    click_canvas.set_tool(CanvasTool.RECTANGLE)
    qtbot.mouseClick(click_canvas, Qt.MouseButton.LeftButton, pos=click_start)
    assert click_canvas._draft.anchor is not None
    qtbot.mouseClick(click_canvas, Qt.MouseButton.LeftButton, pos=click_end)
    clicked = click_canvas.annotations[-1]

    assert (dragged.x1, dragged.y1, dragged.x2, dragged.y2) == pytest.approx(
        (clicked.x1, clicked.y1, clicked.x2, clicked.y2),
        abs=1e-9,
    )
    assert dragged.x1 == pytest.approx(0.0)
    assert dragged.x2 == pytest.approx(320.0)


def test_clamped_zero_area_retries_and_select_click_on_backplate_only_clears(qtbot) -> None:
    """零面积不造假框，选择工具点击底板只执行空白选择语义。"""

    canvas = _canvas_with_annotation(qtbot)
    image_rect = canvas._image_rect()
    outside = QPoint(round(image_rect.left() - 12), round(image_rect.top() + 50))
    before = tuple(canvas.annotations)
    canvas.set_tool(CanvasTool.RECTANGLE)
    qtbot.mouseClick(canvas, Qt.MouseButton.LeftButton, pos=outside)
    assert canvas._draft.anchor is not None
    qtbot.mouseClick(canvas, Qt.MouseButton.LeftButton, pos=outside + QPoint(4, 0))
    assert tuple(canvas.annotations) == before
    assert canvas._draft.anchor is not None

    canvas.cancel_current_operation()
    canvas.select_shape(before[0].id)
    assert canvas.selected_id == before[0].id
    qtbot.mouseClick(canvas, Qt.MouseButton.LeftButton, pos=QPoint(4, 4))
    assert canvas.selected_id is None
    assert tuple(canvas.annotations) == before


def test_backplate_clamping_requires_editable_rectangle_mode_and_middle_pan_wins(qtbot) -> None:
    """吸附只属于可编辑矩形工具，中键在底板上不能创建或平移。"""

    canvas = _canvas_with_annotation(qtbot)
    image_rect = canvas._image_rect()
    outside = QPoint(round(image_rect.left() - 15), round(image_rect.center().y()))
    before = tuple(canvas.annotations)

    canvas.set_tool(CanvasTool.RECTANGLE)
    qtbot.mouseMove(canvas, outside)
    assert canvas._snap_projection is not None
    qtbot.mousePress(canvas, Qt.MouseButton.MiddleButton, pos=outside)
    qtbot.mouseMove(canvas, outside + QPoint(20, 12))
    qtbot.mouseRelease(canvas, Qt.MouseButton.MiddleButton, pos=outside + QPoint(20, 12))
    assert tuple(canvas.annotations) == before
    assert canvas._middle_panning is False
    assert canvas._draft.anchor is None

    canvas.managed_read_only = True
    canvas._refresh_snap_projection()
    assert canvas._snap_projection is None
    qtbot.mouseClick(canvas, Qt.MouseButton.LeftButton, pos=outside)
    assert tuple(canvas.annotations) == before


def test_crosshair_cursor_and_snap_feedback_have_no_business_side_effects(qtbot) -> None:
    """辅助线、系统指针和吸附提示只刷新视图，不得伪造编辑或历史。"""

    canvas = _canvas_with_annotation(qtbot)
    changed: list[str] = []
    document_changes: list[bool] = []
    canvas.edit_committed.connect(changed.append)
    canvas.document_changed.connect(lambda: document_changes.append(True))
    before = tuple(canvas.annotations)
    image_rect = canvas._image_rect()

    canvas.set_tool(CanvasTool.RECTANGLE)
    for point in (
        image_rect.center().toPoint(),
        QPoint(round(image_rect.left() - 16), round(image_rect.center().y())),
        QPoint(round(image_rect.right() + 16), round(image_rect.bottom() + 16)),
    ):
        qtbot.mouseMove(canvas, point)

    assert tuple(canvas.annotations) == before
    assert canvas._undo == []
    assert canvas._redo == []
    assert changed == []
    assert document_changes == []
