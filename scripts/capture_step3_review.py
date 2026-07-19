"""在隔离资料库中生成步骤三受管图片池复验截图。"""

from __future__ import annotations

import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image, ImageDraw
from PySide6.QtCore import QThreadPool
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from datumdock.app import create_application
from datumdock.i18n.catalog import LocaleService
from datumdock.services.dataset_library import DatasetLibraryService
from datumdock.services.image_pool import ImageImportPreflightRequest, ImageImportService
from datumdock.services.sample_governance import TrashService
from datumdock.services.sample_repository import DatasetSampleRepository
from datumdock.ui.application_shell import ApplicationShell
from datumdock.ui.managed_gateway import ManagedDatasetGateway
from datumdock.ui.prototype_dialogs import DialogId
from datumdock.ui.prototype_models import UiCommand
from datumdock.ui.prototype_pages import RouteId

SIZES = ((1366, 768), (1440, 900), (1920, 1080))
LOCALES = ("zh_CN", "en_US")


def _save_widget(widget: QWidget, target: Path) -> str:
    """保存当前控件并返回文件哈希，任何空截图都终止复验。"""

    target.parent.mkdir(parents=True, exist_ok=True)
    pixmap = widget.grab()
    if pixmap.isNull() or not pixmap.save(str(target), "PNG"):
        raise RuntimeError(f"截图保存失败: {target}")
    return hashlib.sha256(target.read_bytes()).hexdigest()


def _prepare_library(root: Path, source_root: Path):
    """创建两个真实数据集，并在首个数据集导入可辨识图片。"""

    service = DatasetLibraryService(root)
    first = service.create_dataset("工厂零件", "步骤三隔离截图数据")
    service.create_dataset("仓库安全检查", "用于验证多数据集切换")
    source_root.mkdir(parents=True, exist_ok=True)
    paths = service.dataset_repository.paths(first.dataset.id)
    repository = DatasetSampleRepository(paths, first.dataset.id)
    sources: list[Path] = []
    for index, offset in enumerate((0, 2, 28)):
        source = source_root / f"part-{index}.jpg"
        image = Image.new("RGB", (640, 420), (224, 231, 235))
        painter = ImageDraw.Draw(image)
        painter.rounded_rectangle(
            (130 + offset, 90, 470 + offset, 330),
            radius=34,
            fill=(117, 154, 187) if index < 2 else (218, 157, 116),
            outline=(70, 92, 112),
            width=8,
        )
        painter.ellipse((275, 165, 365, 255), fill=(245, 241, 232))
        image.save(source, quality=94)
        sources.append(source)
    importer = ImageImportService(paths, first.dataset, repository)
    prepared = importer.preflight(ImageImportPreflightRequest(first.dataset.id, tuple(sources)))
    report = importer.commit(prepared, {})
    if len(report.imported_sample_ids) != 3:
        raise RuntimeError("截图资料库图片导入失败")
    trash = TrashService(paths, first.dataset, repository)
    trash.delete(trash.preview((report.imported_sample_ids[-1],), threshold=10))
    service.synchronize_statistics(first.dataset.id)
    return first


def capture(output_root: Path) -> int:
    """生成双语三尺寸核心页与步骤三治理页证据。"""

    application = QApplication.instance() or create_application(["datumdock-step3-review"])
    screenshot_count = 0
    with TemporaryDirectory(prefix="datumdock-step3-review-") as temporary:
        temporary_root = Path(temporary)
        library_root = temporary_root / "library"
        first = _prepare_library(library_root, temporary_root / "sources")
        restarted = DatasetLibraryService(library_root)
        assert len(restarted.list_datasets()) == 2
        for locale_name in LOCALES:
            for width, height in SIZES:
                locale = LocaleService(locale_name)
                gateway = ManagedDatasetGateway(restarted)
                gateway.dispatch(UiCommand("settings.update", {"ui_locale": locale_name}))
                window = ApplicationShell(locale, gateway)
                window.resize(width, height)
                window.show()
                QTest.qWait(450)
                size_root = output_root / locale_name / f"{width}x{height}"

                window.navigate(RouteId.HOME.value)
                application.processEvents()
                assert window.navigation.current == RouteId.HOME
                home_hash = _save_widget(window, size_root / "home.png")

                window.navigate(f"{RouteId.ANNOTATION_WORKSPACE.value}:{first.dataset.id}")
                QTest.qWait(350)
                if not QThreadPool.globalInstance().waitForDone(5000):
                    raise RuntimeError("缩略图复验超时")
                application.processEvents()
                assert window.navigation.current == RouteId.ANNOTATION_WORKSPACE
                workspace_hash = _save_widget(window, size_root / "image-workspace.png")
                if home_hash == workspace_hash:
                    raise RuntimeError(f"主页与图片工作台截图重复: {locale_name} {width}x{height}")
                screenshot_count += 2

                if (width, height) == (1440, 900):
                    for route, filename in (
                        (RouteId.SIMILARITY_REVIEW, "similarity-review.png"),
                        (RouteId.TRASH, "trash.png"),
                        (RouteId.SETTINGS, "settings.png"),
                    ):
                        window.navigate(route.value)
                        QTest.qWait(120)
                        assert window.navigation.current == route
                        _save_widget(window, size_root / filename)
                        screenshot_count += 1

                    window.navigate(f"{RouteId.ANNOTATION_WORKSPACE.value}:{first.dataset.id}")
                    window.open_dialog(f"{DialogId.IMAGE_IMPORT.value}:{first.dataset.id}")
                    QTest.qWait(120)
                    dialog = window._active_dialogs[-1]
                    assert dialog.isVisible()
                    _save_widget(dialog, size_root / "image-import.png")
                    screenshot_count += 1
                    dialog.close()

                window.close()
                application.processEvents()
    return screenshot_count


def main() -> int:
    """命令行入口只写入 Git 忽略的视觉复验目录。"""

    output_root = Path("build/ui-review/step3-image-pool").resolve()
    count = capture(output_root)
    print(f"已生成 {count} 张步骤三复验截图: {output_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
