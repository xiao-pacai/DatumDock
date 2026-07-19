"""在隔离资料库中生成步骤四标签与矩形标注复验截图。"""

from __future__ import annotations

import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image, ImageDraw
from PySide6.QtCore import QThreadPool
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox, QWidget

from datumdock.app import create_application
from datumdock.domain.models import AnnotationDocument, RectangleShape, ReviewStatus
from datumdock.i18n.catalog import LocaleService, tr
from datumdock.services.annotations import AnnotationSaveRequest, AnnotationService
from datumdock.services.dataset_library import DatasetLibraryService
from datumdock.services.image_pool import ImageImportPreflightRequest, ImageImportService
from datumdock.services.managed_labels import LabelSetService
from datumdock.services.sample_repository import DatasetSampleRepository
from datumdock.ui.application_shell import ApplicationShell
from datumdock.ui.managed_gateway import ManagedDatasetGateway
from datumdock.ui.managed_label_pages import ManagedLabelEditDialog
from datumdock.ui.prototype_models import UiCommand
from datumdock.ui.prototype_pages import RouteId

SIZES = ((1366, 768), (1440, 900), (1920, 1080))
LOCALES = ("zh_CN", "en_US")


def _save_widget(widget: QWidget, target: Path) -> str:
    """保存当前控件并返回文件哈希，空截图立即终止复验。"""

    target.parent.mkdir(parents=True, exist_ok=True)
    pixmap = widget.grab()
    if pixmap.isNull() or not pixmap.save(str(target), "PNG"):
        raise RuntimeError(f"截图保存失败: {target}")
    return hashlib.sha256(target.read_bytes()).hexdigest()


def _prepare_library(root: Path, source_root: Path):
    """创建两个真实数据集，并为首个数据集准备标签、图片和标注。"""

    service = DatasetLibraryService(root)
    first = service.create_dataset("工厂零件", "步骤四隔离截图数据")
    service.create_dataset("仓库安全检查", "用于验证多数据集切换")
    labels = LabelSetService(service)
    current = labels.add_label(
        first.dataset.id,
        class_id=0,
        name="metal_part",
        alias="金属零件",
        description="设备上的银蓝色主体零件",
        synonyms=("工件", "part"),
    )
    current = labels.add_label(
        first.dataset.id,
        class_id=1,
        name="connector",
        alias="连接器",
        description="与主体连接的橙色接口",
    )
    current = labels.add_label(
        first.dataset.id,
        class_id=2,
        name="fastener",
        alias="紧固件",
        description="需要检查位置的小型紧固件",
    )
    label_by_name = {label.name: label for label in current.labels}
    source_root.mkdir(parents=True, exist_ok=True)
    paths = service.dataset_repository.paths(first.dataset.id)
    repository = DatasetSampleRepository(paths, first.dataset.id)
    sources: list[Path] = []
    for index in range(3):
        source = source_root / f"assembly-{index}.jpg"
        image = Image.new("RGB", (640, 420), (230, 235, 238))
        painter = ImageDraw.Draw(image)
        painter.rounded_rectangle(
            (115, 85, 485, 335),
            radius=34,
            fill=(111, 151, 184),
            outline=(68, 87, 104),
            width=8,
        )
        painter.rounded_rectangle(
            (440, 155, 560, 270),
            radius=18,
            fill=(221, 154, 107),
            outline=(110, 86, 70),
            width=6,
        )
        painter.ellipse((255, 160, 350, 255), fill=(246, 242, 232))
        painter.rectangle((24, 24, 38, 38), fill=(120 + index * 35, 105, 145))
        image.save(source, quality=95)
        sources.append(source)
    importer = ImageImportService(paths, first.dataset, repository)
    prepared = importer.preflight(ImageImportPreflightRequest(first.dataset.id, tuple(sources)))
    report = importer.commit(prepared, {})
    if len(report.imported_sample_ids) != 3:
        raise RuntimeError("截图资料库图片导入失败")
    annotations = AnnotationService(service, first.dataset.id)
    statuses = (
        ReviewStatus.COMPLETED,
        ReviewStatus.PENDING_REVIEW,
        ReviewStatus.COMPLETED_NEGATIVE,
    )
    for index, (sample_id, status) in enumerate(
        zip(report.imported_sample_ids, statuses, strict=True)
    ):
        sample = repository.get_sample(sample_id)
        if sample is None:
            raise RuntimeError("截图样本索引丢失")
        rectangles = []
        if status != ReviewStatus.COMPLETED_NEGATIVE:
            rectangles = [
                RectangleShape(
                    label_id=label_by_name["metal_part"].id,
                    x1=115,
                    y1=85,
                    x2=485,
                    y2=335,
                ),
                RectangleShape(
                    label_id=label_by_name["connector"].id,
                    x1=440,
                    y1=155,
                    x2=560,
                    y2=270,
                    confidence=0.94 if index == 1 else None,
                ),
            ]
        document = AnnotationDocument(
            sample_id=sample.id,
            image_filename=sample.filename,
            image_width=sample.width,
            image_height=sample.height,
            rectangles=rectangles,
            review_status=status,
        )
        annotations.save(AnnotationSaveRequest(first.dataset.id, sample.id, 1, "", document))
    service.synchronize_statistics(first.dataset.id)
    return first, label_by_name["metal_part"]


def _save_failure_dialog(locale: LocaleService, parent: QWidget) -> QMessageBox:
    """构造与离开保护一致的保存失败选择框，供原生视觉验收。"""

    box = QMessageBox(parent)
    box.setWindowTitle(tr(locale, "canvas.leave_failed_title"))
    box.setText(tr(locale, "canvas.leave_failed_body"))
    box.addButton(tr(locale, "canvas.retry"), QMessageBox.ButtonRole.AcceptRole)
    box.addButton(tr(locale, "canvas.discard"), QMessageBox.ButtonRole.DestructiveRole)
    box.addButton(tr(locale, "action.cancel"), QMessageBox.ButtonRole.RejectRole)
    return box


def capture(output_root: Path) -> int:
    """生成双语三尺寸真实工作台、标签页和关键对话框证据。"""

    application = QApplication.instance() or create_application(["datumdock-step4-review"])
    screenshot_count = 0
    with TemporaryDirectory(prefix="datumdock-step4-review-") as temporary:
        temporary_root = Path(temporary)
        library_root = temporary_root / "library"
        first, editable_label = _prepare_library(library_root, temporary_root / "sources")
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
                QTest.qWait(300)
                size_root = output_root / locale_name / f"{width}x{height}"

                window.navigate(f"{RouteId.ANNOTATION_WORKSPACE.value}:{first.dataset.id}")
                QTest.qWait(400)
                if not QThreadPool.globalInstance().waitForDone(5000):
                    raise RuntimeError("步骤四工作台后台加载超时")
                application.processEvents()
                assert window.navigation.current == RouteId.ANNOTATION_WORKSPACE
                workspace_hash = _save_widget(window, size_root / "annotation-workspace.png")

                window.navigate(RouteId.LABEL_MANAGER.value)
                QTest.qWait(120)
                assert window.navigation.current == RouteId.LABEL_MANAGER
                labels_hash = _save_widget(window, size_root / "label-manager.png")

                window.navigate(RouteId.LABEL_INSPECTION.value)
                QTest.qWait(120)
                assert window.navigation.current == RouteId.LABEL_INSPECTION
                inspection_hash = _save_widget(window, size_root / "label-inspection.png")
                if len({workspace_hash, labels_hash, inspection_hash}) != 3:
                    raise RuntimeError(f"步骤四核心截图重复: {locale_name} {width}x{height}")
                screenshot_count += 3

                if (width, height) == (1440, 900):
                    edit = ManagedLabelEditDialog(
                        locale,
                        gateway,
                        first.dataset.id,
                        editable_label,
                        window,
                    )
                    edit.show()
                    QTest.qWait(100)
                    _save_widget(edit, size_root / "label-migration-edit.png")
                    edit.close()

                    failure = _save_failure_dialog(locale, window)
                    failure.show()
                    QTest.qWait(100)
                    _save_widget(failure, size_root / "save-failure.png")
                    failure.close()
                    screenshot_count += 2

                window.close()
                application.processEvents()
    return screenshot_count


def main() -> int:
    """命令行入口只写入 Git 忽略的视觉复验目录。"""

    output_root = Path("build/ui-review/step4-annotation").resolve()
    count = capture(output_root)
    print(f"已生成 {count} 张步骤四复验截图: {output_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
