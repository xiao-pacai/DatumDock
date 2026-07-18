"""DatumDock 应用启动、主题安装与依赖组装。"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from datumdock.i18n.catalog import LocaleService
from datumdock.services.workspace import WorkspaceService
from datumdock.ui.main_window import MainWindow
from datumdock.ui.theme import application_stylesheet


def create_application(arguments: list[str] | None = None) -> QApplication:
    """创建单一 QApplication 并在启动前安装全局莫兰迪主题。"""

    application = QApplication(arguments if arguments is not None else sys.argv)
    application.setApplicationName("DatumDock")
    application.setOrganizationName("DatumDock")
    icon_path = Path(__file__).resolve().parents[2] / "assets" / "brand" / "datumdock-app-icon.ico"
    application.setWindowIcon(QIcon(str(icon_path)))
    application.setStyleSheet(application_stylesheet())
    return application


def main(arguments: list[str] | None = None) -> int:
    """启动主窗口；所有受管文件操作由窗口注入的服务层处理。"""

    application = create_application(arguments)
    window = MainWindow(LocaleService(), WorkspaceService())
    window.show()
    return application.exec()
