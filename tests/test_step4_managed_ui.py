"""步骤四正式标签页、矩形画布与自动保存的 pytest-qt 回归。"""

from __future__ import annotations

import time
from pathlib import Path

from PIL import Image
from PySide6.QtCore import QPoint, Qt

from datumdock.domain.models import ReviewStatus
from datumdock.i18n.catalog import LocaleService
from datumdock.services.annotations import AutosaveState
from datumdock.services.dataset_library import DatasetLibraryService
from datumdock.services.managed_labels import LabelSetService
from datumdock.services.tasks import TaskState
from datumdock.ui.annotation_workspace import AnnotationWorkspace
from datumdock.ui.application_shell import ApplicationShell
from datumdock.ui.managed_gateway import ManagedDatasetGateway
from datumdock.ui.managed_label_pages import ManagedLabelInspectionPage, ManagedLabelPage
from datumdock.ui.preview_canvas import CanvasTool
from datumdock.ui.prototype_pages import RouteId


def _wait_task(gateway: ManagedDatasetGateway, task_id: str, timeout: float = 10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        snapshot = gateway.task_snapshot(task_id)
        if snapshot.state not in {TaskState.QUEUED, TaskState.RUNNING}:
            return snapshot
        time.sleep(0.01)
    raise AssertionError("后台任务未在预期时间内完成")


def _import_image(
    gateway: ManagedDatasetGateway,
    dataset_id: str,
    source: Path,
) -> str:
    preflight_task = gateway.start_import_preflight(dataset_id, (source,))
    assert _wait_task(gateway, preflight_task).state == TaskState.COMPLETED
    preflight = gateway.task_result(preflight_task)
    commit_task = gateway.start_import_commit(dataset_id, preflight.session_id, {})
    assert _wait_task(gateway, commit_task).state == TaskState.COMPLETED
    return gateway.task_result(commit_task).imported_sample_ids[0]


def _workspace_with_label(qtbot, tmp_path: Path):
    library = DatasetLibraryService(tmp_path / "library")
    dataset = library.create_dataset("矩形标注").dataset
    label_set = LabelSetService(library).add_label(
        dataset.id,
        class_id=0,
        name="metal_part",
        alias="金属零件",
        description="需要框选的 target assembly 零件",
    )
    gateway = ManagedDatasetGateway(library)
    source = tmp_path / "source.png"
    Image.new("RGB", (320, 180), (110, 140, 170)).save(source)
    sample_id = _import_image(gateway, dataset.id, source)
    window = ApplicationShell(LocaleService(), gateway)
    qtbot.addWidget(window)
    window.resize(1440, 900)
    window.show()
    window.navigate(f"annotation_workspace:{dataset.id}")
    workspace = window.navigation.pages[RouteId.ANNOTATION_WORKSPACE]
    assert isinstance(workspace, AnnotationWorkspace)
    qtbot.waitUntil(lambda: not workspace.canvas.managed_pixmap.isNull(), timeout=5000)
    return library, gateway, window, workspace, dataset.id, sample_id, label_set.labels[0]


def _draw_rectangle(qtbot, workspace: AnnotationWorkspace) -> None:
    workspace.canvas.set_tool(CanvasTool.RECTANGLE)
    image_rect = workspace.canvas._image_rect()
    start = QPoint(round(image_rect.left() + 60), round(image_rect.top() + 45))
    end = QPoint(round(image_rect.left() + 220), round(image_rect.top() + 130))
    qtbot.mousePress(workspace.canvas, Qt.MouseButton.LeftButton, pos=start)
    qtbot.mouseMove(workspace.canvas, end)
    qtbot.mouseRelease(workspace.canvas, Qt.MouseButton.LeftButton, pos=end)


def test_real_canvas_draws_moves_undoes_and_autosaves(qtbot, tmp_path: Path) -> None:
    """真实鼠标手势创建矩形，右侧列表同步，自动保存后重启仍可恢复。"""

    _library, gateway, _window, workspace, dataset_id, sample_id, label = _workspace_with_label(
        qtbot, tmp_path
    )
    assert workspace.tool_buttons["rectangle"].isEnabled() is True
    line_edit = workspace.label_combo.lineEdit()
    assert line_edit is not None
    line_edit.clear()
    qtbot.keyClicks(line_edit, "target")
    qtbot.waitUntil(lambda: workspace.label_combo.count() == 1, timeout=2000)
    assert workspace.label_combo.itemData(0) == label.id
    workspace.label_combo.setCurrentIndex(0)

    _draw_rectangle(qtbot, workspace)

    qtbot.waitUntil(lambda: len(workspace.canvas.annotations) == 1, timeout=3000)
    qtbot.waitUntil(
        lambda: gateway.annotation_save_state(dataset_id)[0] == AutosaveState.SAVED,
        timeout=5000,
    )
    assert workspace.annotation_list.count() == 1
    assert workspace.canvas.annotations[0].label_id == label.id
    saved = gateway.load_annotation(dataset_id, sample_id)
    assert saved.document is not None
    assert len(saved.document.rectangles) == 1
    assert saved.document.review_status == ReviewStatus.PENDING_REVIEW

    original = workspace.canvas.annotations[0]
    workspace.canvas.undo()
    qtbot.waitUntil(lambda: not workspace.canvas.annotations, timeout=3000)
    workspace.canvas.redo()
    qtbot.waitUntil(lambda: len(workspace.canvas.annotations) == 1, timeout=3000)
    assert workspace.canvas.annotations[0].id == original.id
    qtbot.waitUntil(
        lambda: gateway.annotation_save_state(dataset_id)[0] == AutosaveState.SAVED,
        timeout=5000,
    )

    restarted = ManagedDatasetGateway(DatasetLibraryService(tmp_path / "library"))
    reopened = restarted.load_annotation(dataset_id, sample_id)
    assert reopened.document is not None
    assert reopened.document.rectangles[0].id == original.id
    restarted.close()


def test_canvas_eight_handle_resize_label_change_delete_and_review(qtbot, tmp_path: Path) -> None:
    """控制柄缩放、标签改派、删除与图片级复核状态形成同一持久化闭环。"""

    library, gateway, _window, workspace, dataset_id, sample_id, first = _workspace_with_label(
        qtbot, tmp_path
    )
    second_set = LabelSetService(library).add_label(
        dataset_id,
        class_id=1,
        name="connector",
        alias="连接器",
    )
    second = second_set.labels[-1]
    # 页面快照需要在新增标签后重新建立，以验证真实标签选择器。
    _window.navigate(f"annotation_workspace:{dataset_id}")
    workspace = _window.navigation.pages[RouteId.ANNOTATION_WORKSPACE]
    assert isinstance(workspace, AnnotationWorkspace)
    qtbot.waitUntil(lambda: not workspace.canvas.managed_pixmap.isNull(), timeout=5000)
    _draw_rectangle(qtbot, workspace)
    qtbot.waitUntil(lambda: len(workspace.canvas.annotations) == 1, timeout=3000)

    shape = workspace.canvas.annotations[0]
    workspace.canvas.select_shape(shape.id)
    rect = workspace.canvas._annotation_rect(shape)
    handle = QPoint(round(rect.right()), round(rect.bottom()))
    target = QPoint(handle.x() + 24, handle.y() + 18)
    workspace.canvas.set_tool(CanvasTool.SELECT)
    qtbot.mousePress(workspace.canvas, Qt.MouseButton.LeftButton, pos=handle)
    qtbot.mouseMove(workspace.canvas, target)
    qtbot.mouseRelease(workspace.canvas, Qt.MouseButton.LeftButton, pos=target)
    resized = workspace.canvas.annotations[0]
    assert resized.x2 > shape.x2
    assert resized.y2 > shape.y2

    workspace.canvas.select_shape(resized.id)
    workspace.canvas.change_selected_label(second.id)
    qtbot.waitUntil(
        lambda: workspace.canvas.annotations[0].label_id == second.id,
        timeout=3000,
    )
    completed_index = workspace.review_combo.findData(ReviewStatus.COMPLETED)
    assert completed_index >= 0
    workspace.review_combo.setCurrentIndex(completed_index)
    assert workspace._annotation_document is not None
    assert workspace._annotation_document.review_status == ReviewStatus.COMPLETED
    qtbot.waitUntil(
        lambda: gateway.annotation_save_state(dataset_id)[0] == AutosaveState.SAVED,
        timeout=5000,
    )
    loaded = gateway.load_annotation(dataset_id, sample_id)
    assert loaded.document is not None
    assert loaded.document.review_status == ReviewStatus.COMPLETED
    assert loaded.document.rectangles[0].label_id == second.id
    assert first.id != second.id

    workspace.canvas.delete_selected()
    qtbot.waitUntil(lambda: not workspace.canvas.annotations, timeout=3000)
    qtbot.waitUntil(
        lambda: gateway.annotation_save_state(dataset_id)[0] == AutosaveState.SAVED,
        timeout=5000,
    )
    after_delete = gateway.load_annotation(dataset_id, sample_id)
    assert after_delete.document is not None
    assert after_delete.document.review_status == ReviewStatus.PENDING_REVIEW
    assert after_delete.document.rectangles == []

    negative_index = workspace.review_combo.findData(ReviewStatus.COMPLETED_NEGATIVE)
    workspace.review_combo.setCurrentIndex(negative_index)
    qtbot.waitUntil(
        lambda: gateway.annotation_save_state(dataset_id)[0] == AutosaveState.SAVED,
        timeout=5000,
    )
    negative = gateway.load_annotation(dataset_id, sample_id)
    assert negative.document is not None
    assert negative.document.review_status == ReviewStatus.COMPLETED_NEGATIVE


def test_managed_label_and_inspection_pages_use_real_sqlite(qtbot, tmp_path: Path) -> None:
    """标签管理页展示真实标签，检查页通过 SQLite 标签索引找到图片。"""

    _library, gateway, window, workspace, dataset_id, _sample_id, label = _workspace_with_label(
        qtbot, tmp_path
    )
    _draw_rectangle(qtbot, workspace)
    qtbot.waitUntil(
        lambda: gateway.annotation_save_state(dataset_id)[0] == AutosaveState.SAVED,
        timeout=5000,
    )

    window.navigate(RouteId.LABEL_MANAGER.value)
    label_page = window.navigation.pages[RouteId.LABEL_MANAGER]
    assert isinstance(label_page, ManagedLabelPage)
    label_page.refresh()
    assert label_page.table.rowCount() == 1
    assert label_page.table.item(0, 1).text() == label.name
    assert label_page.table.item(0, 4).text() == "1"

    window.navigate(RouteId.LABEL_INSPECTION.value)
    inspection = window.navigation.pages[RouteId.LABEL_INSPECTION]
    assert isinstance(inspection, ManagedLabelInspectionPage)
    inspection.refresh()
    assert inspection.table.rowCount() == 1
    assert inspection.table.item(0, 1).text() == "1"
