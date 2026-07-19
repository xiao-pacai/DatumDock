"""A0.8 工作台品牌、持续辅助线与滚轮缩放的实机事件回归。"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QRegion, QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from datumdock.i18n.catalog import LocaleService
from datumdock.ui.annotation_workspace import AnnotationWorkspace
from datumdock.ui.preview_canvas import CanvasTool, PreviewAnnotationCanvas
from datumdock.ui.prototype_gateway import PreviewGateway
from datumdock.ui.prototype_models import ImageItemViewData, ImageStatus


class _PaintTrackingCanvas(PreviewAnnotationCanvas):
    """记录重绘请求，避免测试只检查内部悬停坐标。"""

    def __init__(self) -> None:
        self.update_count = 0
        super().__init__()

    def update(self, *args: object) -> None:
        self.update_count += 1
        super().update(*args)


def _loaded_canvas(qtbot, *, tracking: bool = False) -> PreviewAnnotationCanvas:
    canvas = _PaintTrackingCanvas() if tracking else PreviewAnnotationCanvas()
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
    qtbot.waitExposed(canvas)
    return canvas


def _send_wheel(
    canvas: PreviewAnnotationCanvas,
    position: QPoint,
    delta: int,
    modifiers: Qt.KeyboardModifier = Qt.KeyboardModifier.NoModifier,
) -> QWheelEvent:
    """通过 Qt 事件分发链发送真实滚轮事件。"""

    event = QWheelEvent(
        QPointF(position),
        QPointF(canvas.mapToGlobal(position)),
        QPoint(),
        QPoint(0, delta),
        Qt.MouseButton.NoButton,
        modifiers,
        Qt.ScrollPhase.ScrollUpdate,
        False,
    )
    QApplication.sendEvent(canvas, event)
    return event


def _visible_icon_bounds(workspace: AnnotationWorkspace):
    pixmap = workspace.workbench_brand.pixmap()
    assert pixmap is not None
    bounds = QRegion(pixmap.mask()).boundingRect()
    ratio = pixmap.devicePixelRatio()
    return bounds.width() / ratio, bounds.height() / ratio


def test_workbench_brand_has_real_visible_size_and_compact_fallback(qtbot) -> None:
    """透明留白不得让完整字标缩水，窄窗口仍切换为紧凑 DD 标记。"""

    gateway = PreviewGateway()
    snapshot = gateway.workspace_snapshot()
    assert snapshot is not None
    workspace = AnnotationWorkspace(LocaleService(), True, snapshot, gateway)
    qtbot.addWidget(workspace)
    workspace.resize(1366, 768)
    workspace.show()
    qtbot.waitExposed(workspace)

    brand_size = workspace.workbench_brand.size()
    visible_width, visible_height = _visible_icon_bounds(workspace)
    assert brand_size.width() == 170
    assert brand_size.height() == 48
    assert 160 <= visible_width <= 180
    assert visible_height >= 24
    assert workspace.workbench_brand.accessibleName() == "DatumDock"
    assert workspace.back_button.toolTip()

    home_requests: list[bool] = []
    workspace.home_requested.connect(lambda: home_requests.append(True))
    QTest.mouseClick(workspace.workbench_brand, Qt.MouseButton.LeftButton)
    assert home_requests == []
    QTest.mouseClick(workspace.back_button, Qt.MouseButton.LeftButton)
    assert home_requests == [True]

    workspace.resize(1100, 768)
    QApplication.processEvents()
    compact_width, _compact_height = _visible_icon_bounds(workspace)
    assert workspace.workbench_brand.width() < 70
    assert 28 <= compact_width <= 42

    workspace.resize(1366, 768)
    QApplication.processEvents()
    assert _visible_icon_bounds(workspace)[0] >= 160


def test_select_mode_real_mouse_move_requests_crosshair_repaint(qtbot) -> None:
    """默认选择模式没有选框时，真实鼠标移动也必须立即重绘辅助线。"""

    canvas = _loaded_canvas(qtbot, tracking=True)
    assert isinstance(canvas, _PaintTrackingCanvas)
    canvas.set_tool(CanvasTool.SELECT)
    QApplication.processEvents()
    canvas.update_count = 0
    point = canvas._image_rect().center().toPoint() + QPoint(42, 24)

    QTest.mouseMove(canvas, point)
    assert canvas.update_count > 0

    geometry = canvas._crosshair_geometry()
    assert geometry is not None
    visible, pointer = geometry
    assert pointer == QPointF(point)
    assert visible.contains(pointer)
    assert canvas.annotations == []
    assert canvas._undo == []


def test_ctrl_wheel_zooms_around_pointer_with_modifier_priority(qtbot) -> None:
    """Ctrl 优先执行指针锚点缩放，Ctrl+Alt 不得退化成水平滚动。"""

    canvas = _loaded_canvas(qtbot)
    canvas.set_zoom_percent(400)
    point = canvas.rect().center() + QPoint(90, 55)
    before_zoom = canvas.zoom
    before_source = canvas._canvas_to_image(QPointF(point), canvas._image_rect())
    changed: list[str] = []
    canvas.edit_committed.connect(changed.append)

    event = _send_wheel(canvas, point, 120, Qt.KeyboardModifier.ControlModifier)
    after_source = canvas._canvas_to_image(QPointF(point), canvas._image_rect())
    assert event.isAccepted()
    assert canvas.zoom > before_zoom
    assert after_source.x() == pytest.approx(before_source.x(), abs=1e-9)
    assert after_source.y() == pytest.approx(before_source.y(), abs=1e-9)

    zoom_after_ctrl = canvas.zoom
    _send_wheel(
        canvas,
        point,
        120,
        Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier,
    )
    assert canvas.zoom > zoom_after_ctrl

    _send_wheel(canvas, point, -120, Qt.KeyboardModifier.ControlModifier)
    assert canvas.zoom < 64.0
    assert changed == []
    assert canvas._undo == []


def test_wheel_mapping_keeps_plain_vertical_and_alt_horizontal(qtbot) -> None:
    """新增缩放不能破坏普通滚轮纵向和 Alt 滚轮横向滚动。"""

    canvas = _loaded_canvas(qtbot)
    canvas.set_zoom_percent(400)
    point = canvas.rect().center()
    before = QPointF(canvas.pan_offset)

    _send_wheel(canvas, point, 120)
    assert canvas.pan_offset.y() != before.y()
    x_before = canvas.pan_offset.x()
    _send_wheel(canvas, point, 120, Qt.KeyboardModifier.AltModifier)
    assert canvas.pan_offset.x() != x_before
