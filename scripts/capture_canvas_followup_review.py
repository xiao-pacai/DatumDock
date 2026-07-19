"""在隔离资料库中生成 A0.5～A0.7 画布视觉复验证据。"""

from __future__ import annotations

import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory

from capture_step4_review import _prepare_library
from PySide6.QtCore import QPoint, QPointF, Qt, QThreadPool
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
    """进入真实工作台并等待当前图片后台解码完成。"""

    window.navigate(f"{RouteId.ANNOTATION_WORKSPACE.value}:{dataset_id}")
    QTest.qWait(350)
    if not QThreadPool.globalInstance().waitForDone(5000):
        raise RuntimeError("A0.5～A0.7 工作台后台加载超时")
    QApplication.processEvents()
    if window.navigation.current != RouteId.ANNOTATION_WORKSPACE:
        raise RuntimeError("A0.5～A0.7 截图路由错误")
    return window.navigation.pages[RouteId.ANNOTATION_WORKSPACE]


def _place_pointer_inside_image(workspace) -> None:
    """在选择模式把真实鼠标移到图片内，显示持续辅助线。"""

    canvas = workspace.canvas
    canvas.set_tool(CanvasTool.SELECT)
    point = canvas._image_rect().center().toPoint() + QPoint(46, 24)
    QTest.mouseMove(canvas, point)
    QApplication.processEvents()


def _prepare_edge_clamped_draft(workspace) -> None:
    """从左上底板锚定并移动到右下底板，形成跨图片吸附草稿。"""

    canvas = workspace.canvas
    canvas.fit_image()
    image_rect = canvas._image_rect()
    first = QPoint(round(image_rect.left() - 18), round(image_rect.top() - 16))
    second = QPoint(round(image_rect.right() + 18), round(image_rect.bottom() + 16))
    canvas.set_tool(CanvasTool.RECTANGLE)
    QTest.mouseClick(canvas, Qt.MouseButton.LeftButton, pos=first)
    QTest.mouseMove(canvas, second)
    QApplication.processEvents()
    if canvas._draft.anchor is None or canvas._snap_projection is None:
        raise RuntimeError("底板吸附草稿没有进入预期状态")


def capture(output_root: Path) -> int:
    """生成双语三尺寸矩阵以及 1440×900 的高倍率和空状态证据。"""

    application = QApplication.instance() or create_application(["datumdock-canvas-review"])
    count = 0
    with TemporaryDirectory(prefix="datumdock-canvas-review-") as temporary:
        temporary_root = Path(temporary)
        library_root = temporary_root / "library"
        first, _label, _sample_id, _shape_id = _prepare_library(
            library_root,
            temporary_root / "sources",
        )
        service = DatasetLibraryService(library_root)
        empty_dataset = next(
            item.bundle.dataset
            for item in service.list_datasets()
            if item.bundle is not None and item.bundle.dataset.id != first.dataset.id
        )
        for locale_name in LOCALES:
            for width, height in SIZES:
                locale = LocaleService(locale_name)
                gateway = ManagedDatasetGateway(service)
                gateway.dispatch(UiCommand("settings.update", {"ui_locale": locale_name}))
                window = ApplicationShell(locale, gateway)
                window.resize(width, height)
                window.show()
                QTest.qWait(250)
                workspace = _wait_workspace(window, first.dataset.id)
                size_root = output_root / locale_name / f"{width}x{height}"

                _place_pointer_inside_image(workspace)
                crosshair_hash = _save_widget(window, size_root / "persistent-crosshair.png")

                _prepare_edge_clamped_draft(workspace)
                clamp_hash = _save_widget(window, size_root / "edge-clamped-draft.png")
                workspace.canvas.cancel_current_operation()
                if crosshair_hash == clamp_hash:
                    raise RuntimeError(f"核心截图重复: {locale_name} {width}x{height}")
                count += 2

                if (width, height) == (1440, 900):
                    workspace.canvas.set_zoom_percent(6400)
                    selected = workspace.canvas.annotations[0]
                    selected_rect = workspace.canvas._annotation_rect(selected)
                    center = QPointF(workspace.canvas.rect().center())
                    workspace.canvas.pan_offset += center - selected_rect.topLeft()
                    workspace.canvas._clamp_pan_offset()
                    handle = workspace.canvas._handle_points(
                        workspace.canvas._annotation_rect(selected)
                    )["bottom_right"]
                    QTest.mouseMove(workspace.canvas, handle.toPoint())
                    workspace.canvas.update()
                    QTest.qWait(80)
                    _save_widget(window, size_root / "canvas-6400-percent.png")

                    _wait_workspace(window, empty_dataset.id)
                    _save_widget(window, size_root / "empty-light-backplate.png")
                    count += 2

                window.close()
                application.processEvents()
    return count


def main() -> int:
    """命令行入口只向 Git 忽略的复验目录写入图片。"""

    output_root = Path("build/ui-review/a0.5-a0.7").resolve()
    count = capture(output_root)
    print(f"已生成 {count} 张 A0.5～A0.7 画布截图: {output_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
