"""使用临时资料库生成步骤五中英文互操作向导截图。"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path

from PIL import Image
from PySide6.QtWidgets import QApplication

from datumdock.app import create_application
from datumdock.i18n.catalog import LocaleService, tr
from datumdock.services.dataset_library import DatasetLibraryService
from datumdock.services.managed_interop import (
    ExternalLabelResolution,
    XAnyExportRequest,
    XAnyImportCommitRequest,
    XAnyImportPreflightRequest,
    XAnyLabelingInteropService,
)
from datumdock.services.managed_labels import LabelSetService
from datumdock.ui.managed_gateway import ManagedDatasetGateway
from datumdock.ui.managed_interop_dialogs import (
    ManagedXAnyExportDialog,
    ManagedXAnyImportDialog,
)


def _source_directory(root: Path) -> Path:
    source = root / "xany-source"
    source.mkdir()
    image = source / "connector.png"
    Image.new("RGB", (640, 360), (154, 177, 196)).save(image)
    payload = {
        "version": "5.5.0",
        "flags": {"reviewed": True},
        "imagePath": image.name,
        "imageData": None,
        "imageWidth": 640,
        "imageHeight": 360,
        "shapes": [
            {
                "label": "connector",
                "points": [[120, 80], [430, 280]],
                "shape_type": "rectangle",
                "description": "演示矩形",
            },
            {
                "label": "legacy-outline",
                "points": [[30, 30], [80, 25], [70, 90]],
                "shape_type": "polygon",
                "attributes": {"preserved": True},
            },
        ],
    }
    (source / "connector.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    Image.new("RGB", (320, 240), (218, 203, 185)).save(source / "unannotated.png")
    (source / "orphan.json").write_text("{}", encoding="utf-8")
    return source


def _capture(widget, path: Path) -> None:
    widget.show()
    QApplication.processEvents()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not widget.grab().save(str(path), "PNG"):
        raise RuntimeError(f"截图保存失败: {path}")
    widget.hide()


def capture(output_root: Path) -> int:
    with tempfile.TemporaryDirectory(prefix="datumdock-step5-review-") as temporary:
        root = Path(temporary)
        os.environ["DATUMDOCK_DATA_DIR"] = str((root / "library").resolve())
        library = DatasetLibraryService(root / "library")
        dataset = library.create_dataset("连接器检查").dataset
        label_set = LabelSetService(library).add_label(
            dataset.id,
            class_id=0,
            name="connector",
            alias="连接器",
            description="线束连接器",
        )
        label = label_set.labels[0]
        source = _source_directory(root)
        service = XAnyLabelingInteropService(library, dataset.id)
        preflight = service.preflight_import(XAnyImportPreflightRequest(dataset.id, source))
        gateway = ManagedDatasetGateway(library)
        count = 0
        try:
            for locale_name in ("zh_CN", "en_US"):
                locale = LocaleService(locale_name)
                for width, height in ((1366, 768), (1440, 900), (1920, 1080)):
                    dialog = ManagedXAnyImportDialog(locale, gateway, dataset.id)
                    dialog.preflight = preflight
                    dialog._show_preflight()
                    dialog.resize(min(width - 120, 1120), min(height - 100, 760))
                    _capture(
                        dialog,
                        output_root
                        / locale_name
                        / f"{width}x{height}"
                        / "xany-import-preflight.png",
                    )
                    dialog.mapping_table.setCurrentCell(1, 2)
                    _capture(
                        dialog,
                        output_root / locale_name / f"{width}x{height}" / "xany-label-mapping.png",
                    )
                    dialog.deleteLater()
                    count += 2
            service.commit_import(
                XAnyImportCommitRequest(
                    dataset.id,
                    preflight,
                    {},
                    (
                        ExternalLabelResolution("connector", label.id),
                        ExternalLabelResolution("legacy-outline", None),
                    ),
                )
            )
            for locale_name in ("zh_CN", "en_US"):
                locale = LocaleService(locale_name)
                for width, height in ((1366, 768), (1440, 900), (1920, 1080)):
                    dialog = ManagedXAnyExportDialog(locale, gateway, dataset.id)
                    dialog.parent_directory = root
                    dialog.parent_path.setText(str(root))
                    dialog.phase = "review"
                    dialog.start.setText(tr(locale, "dialog.xany.start_export"))
                    dialog.status.setText(
                        tr(locale, "dialog.xany.export_preflight_summary").format(
                            images=2,
                            annotated=1,
                            empty=1,
                        )
                    )
                    dialog.resize(min(width - 180, 900), min(height - 160, 620))
                    _capture(
                        dialog,
                        output_root
                        / locale_name
                        / f"{width}x{height}"
                        / "xany-export-preflight.png",
                    )
                    export_target = root / f"export-{locale_name}-{width}x{height}"
                    export_preflight = service.preflight_export(
                        XAnyExportRequest(dataset.id, export_target)
                    )
                    export_report = service.export(export_preflight)
                    dialog.phase = "done"
                    dialog.start.setText(tr(locale, "action.close"))
                    dialog.status.setText(
                        tr(locale, "dialog.xany.export_report").format(
                            images=export_report.image_count,
                            rectangles=export_report.rectangle_count,
                            empty=export_report.empty_annotation_count,
                        )
                    )
                    _capture(
                        dialog,
                        output_root / locale_name / f"{width}x{height}" / "xany-export-result.png",
                    )
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
        default=Path("build/ui-review/step5-xany"),
    )
    args = parser.parse_args()
    app = QApplication.instance() or create_application(["datumdock-step5-review"])
    count = capture(args.output.resolve())
    print(f"已生成 {count} 张步骤五截图。")
    app.processEvents()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
