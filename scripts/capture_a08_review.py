"""在隔离的真实受管资料库中生成 A0.8 用户实机可用性截图。"""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
from tempfile import TemporaryDirectory

from capture_step4_review import _prepare_library
from PySide6.QtCore import QPoint, QPointF, Qt, QThreadPool
from PySide6.QtGui import QCursor, QRegion, QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from datumdock.app import create_application
from datumdock.i18n.catalog import LocaleService
from datumdock.services.dataset_library import DatasetLibraryService
from datumdock.ui.application_shell import ApplicationShell
from datumdock.ui.managed_gateway import ManagedDatasetGateway
from datumdock.ui.preview_canvas import CanvasTool
from datumdock.ui.prototype_models import UiCommand
from datumdock.ui.prototype_pages import RouteId

SIZES = ((1366, 768), (1440, 900), (1920, 1080))
LOCALES = ("zh_CN", "en_US")


def _save_widget(widget: QWidget, target: Path) -> str:
    """保存 Qt 原生控件截图，并拒绝空文件。"""

    target.parent.mkdir(parents=True, exist_ok=True)
    pixmap = widget.grab()
    if pixmap.isNull() or not pixmap.save(str(target), "PNG"):
        raise RuntimeError(f"截图保存失败: {target}")
    return hashlib.sha256(target.read_bytes()).hexdigest()


def _wait_workspace(window: ApplicationShell, dataset_id: str):
    """进入正式工作台并等待真实图片与标注后台加载完成。"""

    window.navigate(f"{RouteId.ANNOTATION_WORKSPACE.value}:{dataset_id}")
    QTest.qWait(300)
    if not QThreadPool.globalInstance().waitForDone(5000):
        raise RuntimeError("A0.8 工作台后台加载超时")
    QApplication.processEvents()
    if window.navigation.current != RouteId.ANNOTATION_WORKSPACE:
        raise RuntimeError("A0.8 截图路由错误")
    return window.navigation.pages[RouteId.ANNOTATION_WORKSPACE]


def _assert_responsive_brand(workspace) -> None:
    """按实际逻辑宽度验证完整字标或窄屏 DD 标记。"""

    pixmap = workspace.workbench_brand.pixmap()
    if pixmap is None:
        raise RuntimeError("工作台品牌图片没有加载")
    bounds = QRegion(pixmap.mask()).boundingRect()
    ratio = pixmap.devicePixelRatio()
    width = bounds.width() / ratio
    height = bounds.height() / ratio
    if workspace.width() >= 1180:
        valid = 160 <= width <= 180 and height >= 24
    else:
        valid = 28 <= width <= 42 and height >= 24
    if not valid:
        raise RuntimeError(
            f"工作台品牌响应尺寸不合格: 窗口 {workspace.width()}px，品牌 {width:.1f}×{height:.1f}"
        )


def _move_pointer_to_image(workspace) -> QPoint:
    """在选择模式中通过真实鼠标事件显示持续辅助线。"""

    canvas = workspace.canvas
    canvas.set_tool(CanvasTool.SELECT)
    point = canvas._image_rect().center().toPoint() + QPoint(64, 34)
    workspace.window().raise_()
    workspace.window().activateWindow()
    QCursor.setPos(canvas.mapToGlobal(QPoint(4, 4)))
    QTest.qWait(30)
    QTest.mouseMove(canvas, QPoint(4, 4))
    QCursor.setPos(canvas.mapToGlobal(point))
    QTest.qWait(30)
    QTest.mouseMove(canvas, point)
    QApplication.processEvents()
    if canvas._crosshair_geometry() is None:
        raise RuntimeError("选择模式辅助线没有显示")
    return point


def _ctrl_wheel_zoom(workspace, point: QPoint) -> None:
    """发送真实 Ctrl+滚轮事件并验证指针锚点未漂移。"""

    canvas = workspace.canvas
    canvas.set_zoom_percent(400)
    before_zoom = canvas.zoom
    before_source = canvas._canvas_to_image(QPointF(point), canvas._image_rect())
    event = QWheelEvent(
        QPointF(point),
        QPointF(canvas.mapToGlobal(point)),
        QPoint(),
        QPoint(0, 120),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.ControlModifier,
        Qt.ScrollPhase.ScrollUpdate,
        False,
    )
    QApplication.sendEvent(canvas, event)
    after_source = canvas._canvas_to_image(QPointF(point), canvas._image_rect())
    if not event.isAccepted() or canvas.zoom <= before_zoom:
        raise RuntimeError("Ctrl+滚轮没有执行放大")
    if (
        abs(after_source.x() - before_source.x()) > 1e-9
        or abs(after_source.y() - before_source.y()) > 1e-9
    ):
        raise RuntimeError("Ctrl+滚轮指针锚点发生漂移")
    QApplication.processEvents()


def capture(output_root: Path) -> int:
    """生成双语、三尺寸及当前 DPI 子进程对应的正式工作台证据。"""

    application = QApplication.instance() or create_application(["datumdock-a08-review"])
    scale = os.environ.get("QT_SCALE_FACTOR", "system").replace(".", "_")
    scale_root = output_root / f"dpi-{scale}"
    count = 0
    with TemporaryDirectory(prefix="datumdock-a08-review-") as temporary:
        temporary_root = Path(temporary)
        first, _label, _sample_id, _shape_id = _prepare_library(
            temporary_root / "library",
            temporary_root / "sources",
        )
        service = DatasetLibraryService(temporary_root / "library")
        for locale_name in LOCALES:
            for width, height in SIZES:
                locale = LocaleService(locale_name)
                gateway = ManagedDatasetGateway(service)
                gateway.dispatch(UiCommand("settings.update", {"ui_locale": locale_name}))
                window = ApplicationShell(locale, gateway)
                window.resize(width, height)
                window.show()
                QTest.qWait(200)
                workspace = _wait_workspace(window, first.dataset.id)
                _assert_responsive_brand(workspace)
                point = _move_pointer_to_image(workspace)
                size_root = scale_root / locale_name / f"{width}x{height}"
                first_hash = _save_widget(window, size_root / "select-crosshair-and-brand.png")

                _ctrl_wheel_zoom(workspace, point)
                second_hash = _save_widget(window, size_root / "ctrl-wheel-pointer-zoom.png")
                if first_hash == second_hash:
                    raise RuntimeError(f"A0.8 两张核心截图重复: {locale_name} {width}x{height}")
                count += 2
                window.close()
                application.processEvents()
    return count


def main() -> int:
    parser = argparse.ArgumentParser(description="生成 DatumDock A0.8 GUI 复验截图")
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("build/ui-review/a0.8"),
    )
    arguments = parser.parse_args()
    output_root = arguments.output_root.resolve()
    count = capture(output_root)
    print(f"已生成 {count} 张 A0.8 截图: {output_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
