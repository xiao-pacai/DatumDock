"""A0.5～A0.7 画布后续交互的独立回归。"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QPoint, QPointF, Qt

from datumdock.ui.preview_canvas import CanvasTool, PreviewAnnotationCanvas
from datumdock.ui.prototype_models import ImageItemViewData, ImageStatus


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
