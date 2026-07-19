"""步骤四正式标签页、矩形画布与自动保存的 pytest-qt 回归。"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from PIL import Image
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication, QDialog

from datumdock.domain.models import ReviewStatus
from datumdock.i18n.catalog import LocaleService
from datumdock.services.annotations import AutosaveState
from datumdock.services.dataset_library import DatasetLibraryService
from datumdock.services.managed_labels import LabelSetService
from datumdock.services.sample_repository import DatasetSampleRepository
from datumdock.services.tasks import TaskState
from datumdock.ui.annotation_workspace import AnnotationWorkspace
from datumdock.ui.application_shell import ApplicationShell
from datumdock.ui.managed_gateway import ManagedDatasetGateway
from datumdock.ui.managed_label_pages import ManagedLabelInspectionPage, ManagedLabelPage
from datumdock.ui.preview_canvas import CanvasTool
from datumdock.ui.prototype_models import CommandStatus, UiCommand
from datumdock.ui.prototype_pages import RouteId
from datumdock.ui.quick_label_dialog import QuickLabelSelectorDialog


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
    assert workspace.canvas.tool == CanvasTool.SELECT
    saved = gateway.load_annotation(dataset_id, sample_id)
    assert saved.document is not None
    assert len(saved.document.rectangles) == 1
    assert saved.review_status == ReviewStatus.COMPLETED

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


def test_two_click_rectangle_zero_area_retry_and_high_zoom_navigation(
    qtbot, tmp_path: Path
) -> None:
    """两次单击、零面积重试、中键平移和 6400% 坐标必须共同可用。"""

    _library, _gateway, window, workspace, _dataset_id, _sample_id, _label = _workspace_with_label(
        qtbot, tmp_path
    )
    canvas = workspace.canvas
    window.activateWindow()
    canvas.setFocus()
    qtbot.waitUntil(lambda: QApplication.focusWidget() is canvas, timeout=1000)
    qtbot.keyClick(canvas, Qt.Key.Key_R)
    qtbot.waitUntil(lambda: canvas.tool == CanvasTool.RECTANGLE, timeout=1000)
    image_rect = canvas._image_rect()
    first = QPoint(round(image_rect.left() + 70), round(image_rect.top() + 50))
    second = QPoint(round(image_rect.left() + 210), round(image_rect.top() + 125))

    qtbot.mouseClick(canvas, Qt.MouseButton.LeftButton, pos=first)
    assert canvas._draft.anchor is not None
    assert canvas.annotations == []
    qtbot.mouseClick(canvas, Qt.MouseButton.LeftButton, pos=first)
    assert canvas._draft.anchor is not None
    assert canvas.annotations == []
    qtbot.mouseClick(canvas, Qt.MouseButton.LeftButton, pos=second)
    assert len(canvas.annotations) == 1
    assert canvas.tool == CanvasTool.SELECT
    assert canvas._draft.anchor is None

    canvas.set_zoom_percent(6400)
    assert canvas.zoom == 64.0
    source_point = QPointF(123.25, 81.5)
    zoomed_rect = canvas._image_rect()
    projected = QPointF(
        zoomed_rect.left() + source_point.x() * zoomed_rect.width() / canvas.image.width,
        zoomed_rect.top() + source_point.y() * zoomed_rect.height() / canvas.image.height,
    )
    restored = canvas._canvas_to_image(projected, zoomed_rect)
    assert restored.x() == pytest.approx(source_point.x(), abs=1e-9)
    assert restored.y() == pytest.approx(source_point.y(), abs=1e-9)

    center = canvas.rect().center()
    before_pan = QPointF(canvas.pan_offset)
    qtbot.mousePress(canvas, Qt.MouseButton.MiddleButton, pos=center)
    qtbot.mouseMove(canvas, center + QPoint(35, 22))
    qtbot.mouseRelease(canvas, Qt.MouseButton.MiddleButton, pos=center + QPoint(35, 22))
    assert canvas.pan_offset != before_pan
    assert canvas._hover_point is None

    before_scroll = QPointF(canvas.pan_offset)
    vertical = QWheelEvent(
        QPointF(center),
        QPointF(canvas.mapToGlobal(center)),
        QPoint(),
        QPoint(0, 120),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.ScrollUpdate,
        False,
    )
    canvas.wheelEvent(vertical)
    assert canvas.pan_offset.y() != before_scroll.y()
    horizontal = QWheelEvent(
        QPointF(center),
        QPointF(canvas.mapToGlobal(center)),
        QPoint(),
        QPoint(0, 120),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.AltModifier,
        Qt.ScrollPhase.ScrollUpdate,
        False,
    )
    old_x = canvas.pan_offset.x()
    canvas.wheelEvent(horizontal)
    assert canvas.pan_offset.x() != old_x


def test_quick_label_dialog_searches_resizes_and_remembers_size(
    qtbot, monkeypatch, tmp_path: Path
) -> None:
    """快速标签窗使用真实标签、响应式网格，并在取消时只记忆窗口尺寸。"""

    library, gateway, _window, workspace, dataset_id, sample_id, first = _workspace_with_label(
        qtbot, tmp_path
    )
    labels = LabelSetService(library)
    for number in range(1, 12):
        labels.add_label(
            dataset_id,
            class_id=number,
            name=f"connector_{number}",
            alias=f"连接器{number}",
            description=f"第 {number} 个蓝色连接部件",
        )
    dialog = QuickLabelSelectorDialog(
        workspace.locale,
        gateway,
        workspace.action_registry,
        dataset_id,
        sample_id,
        "shape-for-layout-test",
        first.id,
        0,
        workspace,
    )
    qtbot.addWidget(dialog)
    dialog.show()
    assert dialog.label_view.currentIndex().data(dialog.source_model.LabelRole).id == first.id

    dialog.resize(580, 440)
    qtbot.wait(20)
    narrow_columns = max(
        1, dialog.label_view.viewport().width() // dialog.label_view.gridSize().width()
    )
    dialog.resize(1120, 700)
    qtbot.wait(20)
    wide_columns = max(
        1, dialog.label_view.viewport().width() // dialog.label_view.gridSize().width()
    )
    assert wide_columns > narrow_columns

    dialog.search.setText("蓝色 7")
    assert dialog.proxy_model.rowCount() == 1
    selected = dialog.proxy_model.index(0, 0).data(dialog.source_model.LabelRole)
    assert selected.name == "connector_7"

    def create_quick_label(edit_dialog) -> QDialog.DialogCode:
        result = gateway.dispatch(
            UiCommand(
                "label.add",
                {
                    "dataset_id": dataset_id,
                    "class_id": 99,
                    "name": "quick_created",
                    "alias": "快速新建",
                    "description": "在快速标签窗中创建",
                },
            )
        )
        assert result.status == CommandStatus.APPLIED
        edit_dialog.saved_label_id = result.affected_id
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(
        "datumdock.ui.quick_label_dialog.ManagedLabelEditDialog.exec",
        create_quick_label,
    )
    dialog._create_label()
    created = dialog.label_view.currentIndex().data(dialog.source_model.LabelRole)
    assert created.name == "quick_created"
    before_annotations = tuple(workspace.canvas.annotations)
    dialog.reject()
    assert dialog.selected_label_id is None
    assert tuple(workspace.canvas.annotations) == before_annotations
    assert any(label.id == created.id for label in gateway.list_labels(dataset_id))
    assert gateway.settings.quick_label_dialog_size == (1120, 700)

    confirm_dialog = QuickLabelSelectorDialog(
        workspace.locale,
        gateway,
        workspace.action_registry,
        dataset_id,
        sample_id,
        "shape-for-confirm-test",
        first.id,
        0,
        workspace,
    )
    qtbot.addWidget(confirm_dialog)
    confirm_dialog.show()
    confirm_dialog.activateWindow()
    confirm_dialog.search.setFocus()
    qtbot.waitUntil(lambda: QApplication.focusWidget() is confirm_dialog.search, timeout=1000)
    confirm_dialog.search.setText("connector_7")
    qtbot.keyClick(confirm_dialog.search, Qt.Key.Key_Down)
    assert QApplication.focusWidget() is confirm_dialog.label_view
    qtbot.keyClick(confirm_dialog.label_view, Qt.Key.Key_Enter)
    assert confirm_dialog.result() == QDialog.DialogCode.Accepted
    assert confirm_dialog.selected_label_id == selected.id


def test_canvas_eight_handle_resize_label_change_delete_and_review(qtbot, tmp_path: Path) -> None:
    """控制柄缩放、标签改派、删除与图片级复核状态形成同一持久化闭环。"""

    library, gateway, window, workspace, dataset_id, sample_id, first = _workspace_with_label(
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
    window.navigate(f"annotation_workspace:{dataset_id}")
    workspace = window.navigation.pages[RouteId.ANNOTATION_WORKSPACE]
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
    workspace._mark_review_completed()
    qtbot.waitUntil(
        lambda: gateway.annotation_save_state(dataset_id)[0] == AutosaveState.SAVED,
        timeout=5000,
    )
    loaded = gateway.load_annotation(dataset_id, sample_id)
    assert loaded.document is not None
    assert loaded.review_status == ReviewStatus.COMPLETED
    assert loaded.document.rectangles[0].label_id == second.id
    assert first.id != second.id

    window.activateWindow()
    workspace.annotation_list.setFocus()
    qtbot.waitUntil(lambda: QApplication.focusWidget() is workspace.annotation_list, timeout=1000)
    qtbot.keyClick(workspace.annotation_list, Qt.Key.Key_Delete)
    qtbot.waitUntil(lambda: not workspace.canvas.annotations, timeout=3000)
    workspace.canvas.setFocus()
    qtbot.keyClick(workspace.canvas, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    qtbot.waitUntil(lambda: len(workspace.canvas.annotations) == 1, timeout=3000)
    workspace.annotation_list.setFocus()
    qtbot.keyClick(workspace.annotation_list, Qt.Key.Key_Delete)
    qtbot.waitUntil(lambda: not workspace.canvas.annotations, timeout=3000)
    qtbot.waitUntil(
        lambda: gateway.annotation_save_state(dataset_id)[0] == AutosaveState.SAVED,
        timeout=5000,
    )
    after_delete = gateway.load_annotation(dataset_id, sample_id)
    assert after_delete.document is not None
    assert after_delete.review_status == ReviewStatus.COMPLETED
    assert after_delete.document.rectangles == []

    workspace._mark_review_completed()
    negative = gateway.load_annotation(dataset_id, sample_id)
    assert negative.document is not None
    assert negative.review_status == ReviewStatus.COMPLETED


def test_a_d_navigation_and_s_review_shortcut_use_real_managed_state(qtbot, tmp_path: Path) -> None:
    """A/D 切图与 S 确认完成必须经过正式快捷键和真实 SQLite 状态。"""

    library, gateway, window, workspace, dataset_id, first_id, _label = _workspace_with_label(
        qtbot, tmp_path
    )
    second_source = tmp_path / "second.png"
    Image.new("RGB", (320, 180), (150, 115, 95)).save(second_source)
    second_id = _import_image(gateway, dataset_id, second_source)
    workspace.refresh_managed_samples(sample_id=first_id)
    qtbot.waitUntil(lambda: workspace.current_image_id == first_id, timeout=3000)
    assert workspace.status_combo.count() == 3
    assert workspace.annotation_filter_combo.count() == 3
    assert workspace.sample_model is not None
    assert workspace.sample_model.index(0).data(workspace.sample_model.StatusRole) == ""

    window.activateWindow()
    workspace.canvas.setFocus()
    qtbot.waitUntil(lambda: QApplication.focusWidget() is workspace.canvas, timeout=1000)
    qtbot.keyClick(workspace.canvas, Qt.Key.Key_D)
    qtbot.waitUntil(lambda: workspace.current_image_id == second_id, timeout=3000)
    qtbot.keyClick(workspace.canvas, Qt.Key.Key_A)
    qtbot.waitUntil(lambda: workspace.current_image_id == first_id, timeout=3000)

    repository = DatasetSampleRepository(library.dataset_repository.paths(dataset_id), dataset_id)
    repository.update_review_status(first_id, ReviewStatus.PENDING_REVIEW)
    workspace.refresh_managed_samples(sample_id=first_id)
    qtbot.waitUntil(
        lambda: (
            workspace._managed_sample is not None
            and workspace._managed_sample.review_status == ReviewStatus.PENDING_REVIEW
        )
    )
    workspace.canvas.setFocus()
    qtbot.keyClick(workspace.canvas, Qt.Key.Key_S)
    qtbot.waitUntil(
        lambda: (
            gateway.load_annotation(dataset_id, first_id).review_status == ReviewStatus.COMPLETED
        ),
        timeout=3000,
    )
    assert library.open_dataset(dataset_id).dataset.statistics.image_count == 2


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
