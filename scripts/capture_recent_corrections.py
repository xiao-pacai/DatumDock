"""使用隔离资料库生成近期集中整改的中英文视觉证据。"""

from __future__ import annotations

import argparse
import os
import tempfile
import time
from pathlib import Path

from PySide6.QtWidgets import QApplication, QMessageBox

from datumdock.app import create_application, show_application_window
from datumdock.i18n.catalog import LocaleService, tr
from datumdock.services.dataset_library import DatasetLibraryService
from datumdock.ui.application_shell import ApplicationShell
from datumdock.ui.dataset_deletion_dialog import ManagedDatasetDeletionDialog
from datumdock.ui.managed_gateway import ManagedDatasetGateway
from datumdock.ui.prototype_pages import RouteId


def _capture(widget, target: Path) -> None:
    """保存非空 Qt 控件截图，拒绝把失败误记为视觉证据。"""

    widget.show()
    QApplication.processEvents()
    target.parent.mkdir(parents=True, exist_ok=True)
    if not widget.grab().save(str(target), "PNG"):
        raise RuntimeError(f"截图保存失败: {target}")


def _wait_for_deletion_preflight(dialog: ManagedDatasetDeletionDialog) -> None:
    """只等待真实只读预检，绝不触发删除提交。"""

    for _ in range(200):
        QApplication.processEvents()
        dialog._poll()
        if dialog.preflight is not None:
            return
        time.sleep(0.01)
    raise RuntimeError("数据集删除预检未在截图时限内完成")


def capture(output_root: Path) -> int:
    """生成最大化工作台、删除确认和精确保存错误三类证据。"""

    count = 0
    with tempfile.TemporaryDirectory(prefix="datumdock-recent-review-") as temporary:
        data_root = Path(temporary) / "library"
        os.environ["DATUMDOCK_DATA_DIR"] = str(data_root.resolve())
        service = DatasetLibraryService(data_root)
        dataset = service.create_dataset("工业零件", "近期整改隔离截图").dataset
        for locale_name in ("zh_CN", "en_US"):
            locale = LocaleService(locale_name)
            gateway = ManagedDatasetGateway(service)
            window = ApplicationShell(locale, gateway)
            try:
                window.navigate(f"{RouteId.ANNOTATION_WORKSPACE.value}:{dataset.id}")
                show_application_window(QApplication.instance(), window)
                QApplication.processEvents()
                _capture(
                    window,
                    output_root / locale_name / "maximized-workspace.png",
                )

                deletion = ManagedDatasetDeletionDialog(locale, gateway, dataset.id, window)
                _wait_for_deletion_preflight(deletion)
                deletion.name_input.setText(dataset.name)
                _capture(
                    deletion,
                    output_root / locale_name / "dataset-deletion-confirmation.png",
                )
                deletion.reject()

                error = QMessageBox(window)
                error.setIcon(QMessageBox.Icon.Critical)
                error.setWindowTitle(tr(locale, "canvas.save_failed"))
                error.setText(tr(locale, "canvas.save_failed_external_modification"))
                error.setDetailedText("annotation_sha256_mismatch")
                _capture(
                    error,
                    output_root / locale_name / "label-reassignment-error.png",
                )
                error.close()
                count += 3
            finally:
                window.close()
                gateway.close()
    return count


def main() -> int:
    parser = argparse.ArgumentParser(description="生成 DatumDock 近期整改 GUI 复验截图")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("build/ui-review/recent-corrections"),
    )
    args = parser.parse_args()
    application = QApplication.instance() or create_application(["datumdock-recent-review"])
    count = capture(args.output.resolve())
    print(f"已生成 {count} 张近期整改截图: {args.output.resolve()}")
    application.processEvents()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
