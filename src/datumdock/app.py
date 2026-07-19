"""DatumDock 应用启动、参数解析与新 UI 外壳组装。"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from datumdock.i18n.catalog import LocaleService
from datumdock.resources import resource_root
from datumdock.ui.application_shell import ApplicationShell
from datumdock.ui.theme import application_stylesheet


@dataclass(frozen=True, slots=True)
class LaunchOptions:
    """启动参数只控制 UI 模式，不携带或持久化数据集路径。"""

    ui_preview: bool = False


def parse_launch_options(arguments: list[str] | None = None) -> tuple[LaunchOptions, list[str]]:
    """解析 DatumDock 参数，并把未知 Qt 参数继续交给 QApplication。"""

    parser = argparse.ArgumentParser(prog="datumdock", add_help=True)
    parser.add_argument(
        "--ui-preview",
        action="store_true",
        help="使用不会写入用户数据的一次性内存演示界面",
    )
    namespace, qt_arguments = parser.parse_known_args(arguments)
    return LaunchOptions(ui_preview=namespace.ui_preview), qt_arguments


def create_application(arguments: list[str] | None = None) -> QApplication:
    """创建单一 QApplication 并安装现代视觉 v2 主题。"""

    application = QApplication.instance()
    if application is None:
        application = QApplication(arguments if arguments is not None else sys.argv)
    application.setApplicationName("DatumDock")
    application.setOrganizationName("DatumDock")
    icon_path = resource_root() / "assets" / "brand" / "datumdock-app-icon.ico"
    application.setWindowIcon(QIcon(str(icon_path)))
    application.setStyleSheet(application_stylesheet())
    return application


def main(arguments: list[str] | None = None) -> int:
    """启动新应用外壳；普通模式使用内部资料库，预览模式只用内存。"""

    raw_arguments = list(sys.argv[1:] if arguments is None else arguments)
    options, qt_arguments = parse_launch_options(raw_arguments)
    application = create_application([sys.argv[0], *qt_arguments])
    window = ApplicationShell.for_mode(LocaleService(), options.ui_preview)
    window.show()
    return application.exec()
