"""使用临时资料库生成步骤六 YOLO 导出向导的中英文验收截图。"""

from __future__ import annotations

import argparse
import hashlib
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from PIL import Image
from PySide6.QtWidgets import QApplication

from datumdock.app import create_application
from datumdock.domain.models import (
    AnnotationDocument,
    DatasetSample,
    RectangleShape,
)
from datumdock.i18n.catalog import LocaleService, tr
from datumdock.services.annotations import AnnotationSaveRequest, AnnotationService
from datumdock.services.dataset_library import DatasetLibraryService
from datumdock.services.managed_labels import LabelSetService
from datumdock.services.managed_yolo import (
    SplitRatios,
    YoloExportRequest,
    YoloExportService,
)
from datumdock.services.sample_repository import DatasetSampleRepository
from datumdock.ui.managed_gateway import ManagedDatasetGateway
from datumdock.ui.managed_yolo_dialog import ManagedYoloExportDialog


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _prepare_dataset(root: Path) -> tuple[DatasetLibraryService, str]:
    library = DatasetLibraryService(root / "library")
    dataset = library.create_dataset("YOLO 导出视觉验收").dataset
    label_set = LabelSetService(library).add_label(
        dataset.id,
        class_id=0,
        name="connector",
        alias="连接器",
        description="演示用连接器标签",
    )
    label = label_set.labels[0]
    paths = library.dataset_repository.paths(dataset.id)
    repository = DatasetSampleRepository(paths, dataset.id)
    annotations = AnnotationService(library, dataset.id)
    for number in range(1, 13):
        filename = f"connector_{number:06d}.png"
        image_path = paths.images / filename
        Image.new("RGB", (640, 360), (90 + number * 4, 135, 165)).save(image_path)
        sample = DatasetSample(
            dataset_id=dataset.id,
            filename=filename,
            original_filename=filename,
            image_path=f"pool/images/{filename}",
            width=640,
            height=360,
            content_hash=f"{number:064x}",
            file_hash=_digest(image_path),
            perceptual_hash=f"{number:016x}789abc",
            imported_at=datetime.now(UTC).isoformat(),
        )
        repository.add_sample(sample)
        document = AnnotationDocument(
            sample_id=sample.id,
            image_filename=filename,
            image_width=640,
            image_height=360,
            document_version=1,
            rectangles=[
                RectangleShape(
                    label_id=label.id,
                    x1=120,
                    y1=80,
                    x2=430,
                    y2=280,
                )
            ],
        )
        annotations.save(AnnotationSaveRequest(dataset.id, sample.id, 1, "", document))
    return library, dataset.id


def _capture(widget, path: Path) -> None:
    widget.show()
    QApplication.processEvents()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not widget.grab().save(str(path), "PNG"):
        raise RuntimeError(f"截图保存失败: {path}")
    widget.hide()


def capture(output_root: Path) -> int:
    with tempfile.TemporaryDirectory(prefix="datumdock-step6-review-") as temporary:
        root = Path(temporary)
        os.environ["DATUMDOCK_DATA_DIR"] = str((root / "library").resolve())
        library, dataset_id = _prepare_dataset(root)
        service = YoloExportService(library, dataset_id)
        target = root / "yolo-output"
        preflight = service.preflight(
            YoloExportRequest(
                dataset_id,
                target,
                ratios=SplitRatios(80, 10, 10),
                seed=42,
            )
        )
        report = service.exporter().export(preflight)
        gateway = ManagedDatasetGateway(library)
        count = 0
        try:
            for locale_name in ("zh_CN", "en_US"):
                locale = LocaleService(locale_name)
                for width, height in ((1366, 768), (1440, 900), (1920, 1080)):
                    dialog = ManagedYoloExportDialog(locale, gateway, dataset_id)
                    dialog.preflight = preflight
                    dialog.parent_directory = target.parent
                    dialog.parent_path.setText(str(target.parent))
                    dialog.target_name.setText(target.name)
                    dialog._show_preflight(preflight)
                    dialog.resize(min(width - 140, 1000), min(height - 100, 760))
                    destination = output_root / locale_name / f"{width}x{height}"
                    _capture(dialog, destination / "yolo-preflight.png")
                    dialog.report = report
                    dialog.status.setText(
                        "\n".join(
                            (
                                tr(locale, "dialog.yolo.finished"),
                                tr(locale, "dialog.yolo.finished_summary").format(
                                    images=report.image_count,
                                    labels=report.label_file_count,
                                    boxes=report.rectangle_count,
                                ),
                            )
                        )
                    )
                    dialog.progress.setRange(0, report.image_count)
                    dialog.progress.setValue(report.image_count)
                    dialog.preflight_button.setEnabled(False)
                    dialog.export_button.setEnabled(False)
                    dialog.preflight_button.hide()
                    dialog.export_button.hide()
                    dialog.cancel_task_button.hide()
                    dialog.open_output_button.show()
                    _capture(dialog, destination / "yolo-success.png")
                    if (width, height) == (1440, 900):
                        dialog.preflight_button.show()
                        dialog.export_button.show()
                        dialog.cancel_task_button.show()
                        dialog.open_output_button.hide()
                        dialog.status.setText(tr(locale, "dialog.yolo.exporting"))
                        dialog.progress.setRange(0, 12)
                        dialog.progress.setValue(7)
                        _capture(dialog, destination / "yolo-progress.png")
                        count += 1
                    dialog.deleteLater()
                    count += 2
        finally:
            gateway.close()
    return count


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("build/ui-review/step6-yolo"),
    )
    args = parser.parse_args()
    app = QApplication.instance() or create_application(["datumdock-step6-review"])
    count = capture(args.output.resolve())
    print(f"已生成 {count} 张步骤六截图。")
    app.processEvents()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
