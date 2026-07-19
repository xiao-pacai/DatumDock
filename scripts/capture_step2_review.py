"""在隔离资料库中生成步骤二主页与空工作台复验截图。"""

from __future__ import annotations

import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from datumdock.app import create_application
from datumdock.i18n.catalog import LocaleService
from datumdock.services.dataset_library import DatasetLibraryService
from datumdock.ui.application_shell import ApplicationShell
from datumdock.ui.managed_gateway import ManagedDatasetGateway
from datumdock.ui.prototype_pages import RouteId

SIZES = ((1366, 768), (1440, 900), (1920, 1080))
LOCALES = ("zh_CN", "en_US")


def _save_window(window: ApplicationShell, target: Path) -> str:
    """保存当前窗口并返回文件哈希，截图失败时立即终止复验。"""

    target.parent.mkdir(parents=True, exist_ok=True)
    if not window.grab().save(str(target), "PNG"):
        raise RuntimeError(f"截图保存失败: {target}")
    return hashlib.sha256(target.read_bytes()).hexdigest()


def capture(output_root: Path) -> int:
    """生成十二张路由已断言的双语截图，并验证页面哈希不同。"""

    application = QApplication.instance() or create_application(["datumdock-step2-review"])
    screenshot_count = 0
    with TemporaryDirectory(prefix="datumdock-step2-review-") as temporary:
        root = Path(temporary)
        service = DatasetLibraryService(root)
        first = service.create_dataset("工厂零件", "用于验证真实内部资料库页面")
        service.create_dataset("仓库安全检查", "第二个独立数据集")

        restarted = DatasetLibraryService(root)
        assert len(restarted.list_datasets()) == 2
        for locale_name in LOCALES:
            for width, height in SIZES:
                locale = LocaleService(locale_name)
                window = ApplicationShell(locale, ManagedDatasetGateway(restarted))
                window.show()
                QTest.qWait(420)
                size_root = output_root / locale_name / f"{width}x{height}"
                window.resize(width, height)
                window.navigate(RouteId.HOME.value)
                application.processEvents()
                assert window.navigation.current == RouteId.HOME
                home_hash = _save_window(window, size_root / "home.png")

                window.navigate(f"{RouteId.ANNOTATION_WORKSPACE.value}:{first.dataset.id}")
                application.processEvents()
                assert window.navigation.current == RouteId.ANNOTATION_WORKSPACE
                workspace_hash = _save_window(window, size_root / "empty-workspace.png")
                if home_hash == workspace_hash:
                    raise RuntimeError(f"主页与工作台截图重复: {locale_name} {width}x{height}")
                screenshot_count += 2
                window.close()
                application.processEvents()
    return screenshot_count


def main() -> int:
    """命令行入口固定写入被 Git 忽略的视觉复验目录。"""

    output_root = Path("build/ui-review/step2-revalidation").resolve()
    count = capture(output_root)
    print(f"已生成 {count} 张步骤二复验截图: {output_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
