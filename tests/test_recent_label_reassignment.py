"""外部矩形改派、结构化保存失败和最近使用标签的真实回归。"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from PIL import Image
from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QDialog
from pytestqt.exceptions import TimeoutError as QtWaitTimeout

from datumdock.domain.models import AnnotationDocument, ReviewStatus, new_id
from datumdock.i18n.catalog import LocaleService
from datumdock.services.annotations import (
    AnnotationAutosaveService,
    AnnotationConflictError,
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


def _wait_annotation_saved(qtbot, gateway, dataset_id: str, context: str) -> None:
    """等待当前串行保存终态，并在失败或卡住时保留结构化诊断。"""

    try:
        qtbot.waitUntil(
            lambda: gateway.annotation_save_state(dataset_id)[0].value in {"saved", "failed"},
            timeout=10000,
        )
    except QtWaitTimeout:
        state, failure = gateway.annotation_save_state(dataset_id)
        detail = gateway.annotation_save_failure(dataset_id)
        pytest.fail(f"{context}未结束: state={state.value}, failure={failure!r}, detail={detail!r}")
    state, failure = gateway.annotation_save_state(dataset_id)
    detail = gateway.annotation_save_failure(dataset_id)
    assert state.value == "saved", f"{context}失败: {failure!r}, detail={detail!r}"


def test_imported_rectangle_double_click_reassigns_and_next_box_uses_recent_label(
    qtbot,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
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
    stale_load = workspace._annotation_load
    assert stale_load is not None and stale_load.document is not None
    assert stale_load.document.document_version == 0
    stale_asset = gateway.load_image(dataset.id, sample_id)

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
    # 先确认本次改派请求确实入队，再等待它完成；否则图片加载阶段残留的
    # SAVED 状态可能让测试过早继续，掩盖真实的串行保存时序。
    qtbot.waitUntil(lambda: len(queued_requests) == 1, timeout=3000)
    _wait_annotation_saved(qtbot, gateway, dataset.id, "标签改派保存")
    assert workspace._annotation_document is not None
    assert workspace._annotation_document.document_version == initial_version + 1
    workspace._managed_image_ready(
        workspace._image_generation,
        sample_id,
        (stale_asset, stale_load),
    )
    assert workspace._annotation_document.document_version == initial_version + 1
    qtbot.waitUntil(lambda: workspace.canvas.current_label_id == second.id, timeout=3000)
    # Windows 在模态小窗关闭后会补发双击序列的 release；它不能再形成一次虚假移动。
    qtbot.mouseRelease(
        workspace.canvas,
        Qt.MouseButton.LeftButton,
        pos=rect.center().toPoint(),
    )
    assert workspace._annotation_document.document_version == initial_version + 1
    loaded = gateway.load_annotation(dataset.id, sample_id)
    assert loaded.document is not None
    assert loaded.document.rectangles[0].label_id == second.id
    assert loaded.document.document_version == initial_version + 1
    assert len(queued_requests) == 1
    assert queued_requests[0].edit_kind.value == "reassign"
    assert loaded.review_status == ReviewStatus.COMPLETED

    workspace.canvas.set_tool(CanvasTool.RECTANGLE)
    assert workspace._annotation_document.document_version == initial_version + 1
    image_rect = workspace.canvas._image_rect()
    start = QPoint(round(image_rect.left() + 20), round(image_rect.top() + 20))
    end = QPoint(round(image_rect.left() + 90), round(image_rect.top() + 80))
    qtbot.mousePress(workspace.canvas, Qt.MouseButton.LeftButton, pos=start)
    qtbot.mouseMove(workspace.canvas, end)
    qtbot.mouseRelease(workspace.canvas, Qt.MouseButton.LeftButton, pos=end)
    qtbot.waitUntil(lambda: len(workspace.canvas.annotations) == 2, timeout=3000)
    assert workspace.canvas.annotations[-1].label_id == second.id
    assert first.id != second.id
    qtbot.waitUntil(lambda: len(queued_requests) == 2, timeout=3000)
    _wait_annotation_saved(qtbot, gateway, dataset.id, "第二个矩形保存")

    restarted = DatasetLibraryService(library_root)
    reopened_gateway = ManagedDatasetGateway(restarted)
    reopened = reopened_gateway.load_annotation(dataset.id, sample_id)
    assert reopened.document is not None
    assert len(reopened.document.rectangles) == 2
    assert all(shape.label_id == second.id for shape in reopened.document.rectangles)
    reopened_gateway.close()
    gateway.close()


def test_reassigning_second_sample_keeps_selection_and_saves_that_sample(
    qtbot,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """刷新标签快照不能重置图片列表，更不能把第二张的改派丢弃。"""

    library = DatasetLibraryService(tmp_path / "library")
    dataset = library.create_dataset("第二张改派").dataset
    labels = LabelSetService(library).add_label(
        dataset.id, class_id=0, name="test1", alias="测试一"
    )
    first_label = labels.labels[-1]
    labels = LabelSetService(library).add_label(
        dataset.id, class_id=1, name="test2", alias="测试二"
    )
    second_label = labels.labels[-1]
    source = tmp_path / "external"
    source.mkdir()
    for index, color in enumerate(((90, 120, 150), (150, 120, 90)), start=1):
        image = source / f"image_{index:06d}.png"
        Image.new("RGB", (320, 180), color).save(image)
        (source / f"image_{index:06d}.json").write_text(
            json.dumps(
                {
                    "version": "5.5.0",
                    "flags": {},
                    "shapes": [
                        {
                            "label": first_label.name,
                            "points": [[30, 25], [150, 120]],
                            "shape_type": "rectangle",
                        }
                    ],
                    "imagePath": image.name,
                    "imageData": None,
                    "imageHeight": 180,
                    "imageWidth": 320,
                }
            ),
            encoding="utf-8",
        )
    interop = XAnyLabelingInteropService(library, dataset.id)
    preflight = interop.preflight_import(XAnyImportPreflightRequest(dataset.id, source))
    imported = interop.commit_import(XAnyImportCommitRequest(dataset.id, preflight, {}))
    assert len(imported.imported_sample_ids) == 2

    gateway = ManagedDatasetGateway(library)
    window = ApplicationShell(LocaleService(), gateway)
    qtbot.addWidget(window)
    window.resize(1440, 900)
    window.show()
    window.navigate(f"annotation_workspace:{dataset.id}")
    workspace = window.navigation.pages[RouteId.ANNOTATION_WORKSPACE]
    assert isinstance(workspace, AnnotationWorkspace)
    qtbot.waitUntil(lambda: workspace.sample_model is not None, timeout=5000)
    assert workspace.sample_model is not None
    second_sample = workspace.sample_model.sample_at(1)
    assert second_sample is not None
    workspace.image_list.setCurrentIndex(workspace.sample_model.index(1))
    qtbot.waitUntil(
        lambda: (
            workspace._annotation_document is not None
            and workspace._annotation_document.sample_id == second_sample.id
            and len(workspace.canvas.annotations) == 1
        ),
        timeout=5000,
    )

    def choose_second_label(dialog: QuickLabelSelectorDialog) -> QDialog.DialogCode:
        dialog.selected_label_id = second_label.id
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(QuickLabelSelectorDialog, "exec", choose_second_label)
    queued_requests = []
    original_queue = gateway.queue_annotation_save

    def record_save(request):
        queued_requests.append(request)
        return original_queue(request)

    monkeypatch.setattr(gateway, "queue_annotation_save", record_save)
    workspace._request_shape_reassignment(workspace.canvas.annotations[0].id)

    assert workspace.current_image_id == second_sample.id
    assert workspace.image_list.currentIndex().row() == 1
    qtbot.waitUntil(lambda: len(queued_requests) == 1, timeout=3000)
    _wait_annotation_saved(qtbot, gateway, dataset.id, "第二张标签改派保存")
    reopened = gateway.load_annotation(dataset.id, second_sample.id)
    assert reopened.document is not None
    assert reopened.document.rectangles[0].label_id == second_label.id
    assert len(queued_requests) == 1
    gateway.close()


def test_quick_created_label_refreshes_revision_before_reassignment(
    qtbot,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """快速新建标签后，外层改派必须携带同一标签快照的新修订号。"""

    library = DatasetLibraryService(tmp_path / "library")
    dataset = library.create_dataset("快速标签修订").dataset
    initial_set = LabelSetService(library).add_label(
        dataset.id,
        class_id=0,
        name="test1",
        alias="测试一",
    )
    first = initial_set.labels[-1]
    source = tmp_path / "external"
    source.mkdir()
    image = source / "sample.png"
    Image.new("RGB", (320, 180), (110, 140, 170)).save(image)
    (source / "sample.json").write_text(
        json.dumps(
            {
                "version": "5.5.0",
                "flags": {},
                "shapes": [
                    {
                        "label": first.name,
                        "points": [[30, 25], [150, 120]],
                        "shape_type": "rectangle",
                    }
                ],
                "imagePath": image.name,
                "imageData": None,
                "imageHeight": 180,
                "imageWidth": 320,
            }
        ),
        encoding="utf-8",
    )
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
    initial_revision = workspace._annotation_label_set_revision
    initial_version = workspace._annotation_document.document_version
    created_label = None

    def create_and_choose(dialog: QuickLabelSelectorDialog) -> QDialog.DialogCode:
        nonlocal created_label
        updated = gateway.add_label(
            dataset.id,
            class_id=1,
            name="test2",
            alias="测试二",
        )
        created_label = updated.labels[-1]
        dialog.selected_label_id = created_label.id
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(QuickLabelSelectorDialog, "exec", create_and_choose)
    workspace._request_shape_reassignment(workspace.canvas.annotations[0].id)

    assert created_label is not None
    assert workspace._annotation_label_set_revision == initial_revision + 1
    qtbot.waitUntil(
        lambda: workspace._annotation_document.document_version == initial_version + 1,
        timeout=3000,
    )
    _wait_annotation_saved(qtbot, gateway, dataset.id, "快速新建标签改派保存")
    loaded = gateway.load_annotation(dataset.id, sample_id)
    assert loaded.document is not None
    assert loaded.document.rectangles[0].label_id == created_label.id
    assert loaded.checkpoint is not None
    assert loaded.checkpoint.label_set_revision == initial_revision + 1
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


def test_autosave_classifies_stale_label_revision_separately() -> None:
    """标签并发修订不是字段校验错误，诊断必须提示刷新标签上下文。"""

    class StaleLabelService:
        def save(self, _request):
            raise AnnotationConflictError("标签集修订已变化，请重新加载后再保存")

    sample_id = new_id()
    autosave = AnnotationAutosaveService(StaleLabelService())
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
        label_set_revision=3,
    )
    future = autosave.submit(request)
    with pytest.raises(AnnotationConflictError):
        future.result(timeout=3)
    failure = autosave.failure
    assert failure is not None
    assert failure.kind == AnnotationSaveFailureKind.LABEL_REVISION_CONFLICT
    assert failure.retryable is False
    assert failure.label_set_revision == 3
    autosave.close()
