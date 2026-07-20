"""外部矩形改派、结构化保存失败和最近使用标签的真实回归。"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from PIL import Image
from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QDialog

from datumdock.domain.models import AnnotationDocument, ReviewStatus, new_id
from datumdock.i18n.catalog import LocaleService
from datumdock.services.annotations import (
    AnnotationAutosaveService,
    AnnotationEditKind,
    AnnotationSaveFailureKind,
    AnnotationSaveRequest,
)
from datumdock.services.dataset_library import DatasetLibraryService
from datumdock.services.managed_interop import (
    XAnyImportCommitRequest,
    XAnyImportPreflightRequest,
    XAnyLabelingInteropService,
)
from datumdock.services.managed_labels import LabelSetService
from datumdock.services.recent_labels import RecentLabelTracker
from datumdock.services.tasks import TaskState
from datumdock.ui.annotation_workspace import AnnotationWorkspace
from datumdock.ui.application_shell import ApplicationShell
from datumdock.ui.managed_gateway import ManagedDatasetGateway
from datumdock.ui.preview_canvas import CanvasTool
from datumdock.ui.prototype_pages import RouteId
from datumdock.ui.quick_label_dialog import QuickLabelSelectorDialog


def _wait_task(gateway: ManagedDatasetGateway, task_id: str) -> object:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        snapshot = gateway.task_snapshot(task_id)
        if snapshot.state not in {TaskState.QUEUED, TaskState.RUNNING}:
            assert snapshot.state == TaskState.COMPLETED
            return gateway.task_result(task_id)
        time.sleep(0.01)
    raise AssertionError("后台任务未按时完成")


def test_imported_rectangle_double_click_reassigns_and_next_box_uses_recent_label(
    qtbot, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """test1→test2 成功保存后，下一框立即使用 test2 且重启仍可编辑。"""

    library_root = tmp_path / "library"
    library = DatasetLibraryService(library_root)
    dataset = library.create_dataset("改派闭环").dataset
    labels = LabelSetService(library).add_label(
        dataset.id, class_id=0, name="test1", alias="测试一"
    )
    first = labels.labels[-1]
    labels = LabelSetService(library).add_label(
        dataset.id, class_id=1, name="test2", alias="测试二"
    )
    second = labels.labels[-1]
    source = tmp_path / "external"
    source.mkdir()
    image = source / "sample.png"
    Image.new("RGB", (320, 180), (110, 140, 170)).save(image)
    payload = {
        "version": "5.5.0",
        "flags": {},
        "shapes": [
            {
                "label": "test1",
                "points": [[30, 25], [150, 120]],
                "shape_type": "rectangle",
            }
        ],
        "imagePath": image.name,
        "imageData": None,
        "imageHeight": 180,
        "imageWidth": 320,
    }
    (source / "sample.json").write_text(json.dumps(payload), encoding="utf-8")
    interop = XAnyLabelingInteropService(library, dataset.id)
    preflight = interop.preflight_import(XAnyImportPreflightRequest(dataset.id, source))
    imported = interop.commit_import(XAnyImportCommitRequest(dataset.id, preflight, {}))
    sample_id = imported.imported_sample_ids[0]

    gateway = ManagedDatasetGateway(library)
    window = ApplicationShell(LocaleService(), gateway)
    qtbot.addWidget(window)
    window.resize(1440, 900)
    window.show()
    window.navigate(f"annotation_workspace:{dataset.id}")
    workspace = window.navigation.pages[RouteId.ANNOTATION_WORKSPACE]
    assert isinstance(workspace, AnnotationWorkspace)
    qtbot.waitUntil(lambda: len(workspace.canvas.annotations) == 1, timeout=5000)

    def choose_test2(dialog: QuickLabelSelectorDialog) -> QDialog.DialogCode:
        dialog.selected_label_id = second.id
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(QuickLabelSelectorDialog, "exec", choose_test2)
    queued_requests = []
    original_queue = gateway.queue_annotation_save

    def record_save(request):
        queued_requests.append(request)
        return original_queue(request)

    monkeypatch.setattr(gateway, "queue_annotation_save", record_save)
    initial_version = gateway.load_annotation(dataset.id, sample_id).document.document_version
    original = workspace.canvas.annotations[0]
    rect = workspace.canvas._annotation_rect(original)
    qtbot.mouseDClick(
        workspace.canvas,
        Qt.MouseButton.LeftButton,
        pos=rect.center().toPoint(),
    )
    qtbot.waitUntil(
        lambda: gateway.annotation_save_state(dataset.id)[0].value == "saved",
        timeout=5000,
    )
    qtbot.waitUntil(lambda: workspace.canvas.current_label_id == second.id, timeout=3000)
    # Windows 在模态小窗关闭后会补发双击序列的 release；它不能再形成一次虚假移动。
    qtbot.mouseRelease(
        workspace.canvas,
        Qt.MouseButton.LeftButton,
        pos=rect.center().toPoint(),
    )
    loaded = gateway.load_annotation(dataset.id, sample_id)
    assert loaded.document is not None
    assert loaded.document.rectangles[0].label_id == second.id
    assert loaded.document.document_version == initial_version + 1
    assert len(queued_requests) == 1
    assert queued_requests[0].edit_kind.value == "reassign"
    assert loaded.review_status == ReviewStatus.COMPLETED

    workspace.canvas.set_tool(CanvasTool.RECTANGLE)
    image_rect = workspace.canvas._image_rect()
    start = QPoint(round(image_rect.left() + 20), round(image_rect.top() + 20))
    end = QPoint(round(image_rect.left() + 90), round(image_rect.top() + 80))
    qtbot.mousePress(workspace.canvas, Qt.MouseButton.LeftButton, pos=start)
    qtbot.mouseMove(workspace.canvas, end)
    qtbot.mouseRelease(workspace.canvas, Qt.MouseButton.LeftButton, pos=end)
    qtbot.waitUntil(lambda: len(workspace.canvas.annotations) == 2, timeout=3000)
    assert workspace.canvas.annotations[-1].label_id == second.id
    assert first.id != second.id

    restarted = DatasetLibraryService(library_root)
    reopened_gateway = ManagedDatasetGateway(restarted)
    reopened = reopened_gateway.load_annotation(dataset.id, sample_id)
    assert reopened.document is not None
    assert all(shape.label_id == second.id for shape in reopened.document.rectangles)
    reopened_gateway.close()
    gateway.close()


def test_recent_label_tracker_is_dataset_scoped_and_invalidates_without_fallback() -> None:
    """会话记录不跨数据集，标签失效后明确返回空值。"""

    tracker = RecentLabelTracker()
    first_dataset = new_id()
    second_dataset = new_id()
    label_id = new_id()
    tracker.remember(first_dataset, label_id)
    assert tracker.resolve(first_dataset, {label_id}) == (True, label_id)
    assert tracker.resolve(second_dataset, {label_id}) == (False, None)
    assert tracker.resolve(first_dataset, set()) == (True, None)
    assert tracker.resolve(first_dataset, {label_id}) == (False, None)


def test_autosave_classifies_permission_failure_without_guessing_other_causes() -> None:
    """只有真实 PermissionError 才产生权限类别，原异常文本保留供诊断。"""

    class FailingService:
        def save(self, _request):
            raise PermissionError("access denied")

    sample_id = new_id()
    autosave = AnnotationAutosaveService(FailingService())
    request = AnnotationSaveRequest(
        new_id(),
        sample_id,
        1,
        "",
        AnnotationDocument(
            sample_id=sample_id,
            image_filename="sample.png",
            image_width=10,
            image_height=10,
            document_version=1,
        ),
        edit_kind=AnnotationEditKind.REASSIGN,
    )
    future = autosave.submit(request)
    with pytest.raises(PermissionError):
        future.result(timeout=3)
    failure = autosave.failure
    assert failure is not None
    assert failure.kind == AnnotationSaveFailureKind.PERMISSION
    assert "access denied" in failure.message
    assert failure.request_id == request.request_id
    assert failure.sample_id == sample_id
    assert failure.edit_kind == AnnotationEditKind.REASSIGN.value
    assert failure.retryable is True
    assert failure.exception_chain == ("PermissionError: access denied",)
    autosave.close()
