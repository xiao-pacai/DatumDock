"""不依赖 pytest-qt 插件的 Qt 离屏界面回归。"""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PySide6.QtWidgets import QApplication

from datumdock.domain.models import Label
from datumdock.i18n.catalog import LocaleService
from datumdock.services.dataset import DatasetPoolService
from datumdock.services.labels import LabelService
from datumdock.services.workspace import WorkspaceService
from datumdock.ui.main_window import MainWindow


def test_main_window_loads_dataset_and_switches_locale_without_project_mutation(
    tmp_path: Path,
) -> None:
    """主窗口应加载受管样本，且中英切换不得修改用户标签别名。"""

    application = QApplication.instance() or QApplication([])
    service = WorkspaceService()
    root = tmp_path / "workspace"
    workspace = service.create_workspace(root)
    project = service.create_project(root, workspace, "界面测试")
    dataset = service.create_dataset(root, project, "数据池")
    label = Label(class_id=0, name="part", alias="零件", color="#78978C")
    LabelService().add_label(project.label_set, label)
    service.save_project(root, project)
    source = tmp_path / "source.png"
    Image.new("RGB", (80, 40), (80, 120, 160)).save(source)
    DatasetPoolService().import_images(root, project, dataset, [source])

    locale = LocaleService()
    window = MainWindow(locale, service)
    window.workspace_root = root
    window.workspace = workspace
    window.current_project = project
    window.current_dataset = dataset
    window.refresh_tree()
    window.refresh_context()
    assert window.sample_browser.list_widget.count() == 1
    window.sample_browser.list_widget.setCurrentRow(0)
    application.processEvents()
    assert window.current_sample is not None
    assert window.canvas.document is not None

    locale.set_locale("en_US")
    assert window.file_menu.title() == "File"
    assert project.label_set.labels[0].alias == "零件"
    window.close()
