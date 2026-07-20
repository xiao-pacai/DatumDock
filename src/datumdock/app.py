"""DatumDock 应用启动、参数解析与新 UI 外壳组装。"""

from __future__ import annotations

import argparse
import ctypes
import sys
from dataclasses import dataclass

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from datumdock.i18n.catalog import LocaleService
from datumdock.resources import application_icon_path
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

    _set_windows_application_identity()
    application = QApplication.instance()
    if application is None:
        application = QApplication(arguments if arguments is not None else sys.argv)
    application.setApplicationName("DatumDock")
    application.setOrganizationName("DatumDock")
    application.setWindowIcon(QIcon(str(application_icon_path())))
    application.setStyleSheet(application_stylesheet())
    return application


def _set_windows_application_identity() -> None:
    """让源码运行和安装版都按 DatumDock 分组，避免任务栏回退为 Python 图标。"""

    if sys.platform != "win32":
        return
    try:
        shell32 = getattr(getattr(ctypes, "windll", None), "shell32", None)
        if shell32 is None:
            return
        shell32.SetCurrentProcessExplicitAppUserModelID("DatumDock.DatumDock")
    except (AttributeError, OSError):
        # 极少数精简 Windows 环境没有对应 Shell API，窗口自身图标仍可正常使用。
        return


def main(arguments: list[str] | None = None) -> int:
    """启动新应用外壳；普通模式使用内部资料库，预览模式只用内存。"""

    raw_arguments = list(sys.argv[1:] if arguments is None else arguments)
    options, qt_arguments = parse_launch_options(raw_arguments)
    application = create_application([sys.argv[0], *qt_arguments])
    window = ApplicationShell.for_mode(LocaleService(), options.ui_preview)
    window.show()
    return application.exec()
