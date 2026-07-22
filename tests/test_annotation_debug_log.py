"""临时标注诊断日志与连续第三张图片保存回归。"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from datumdock.domain.models import DatasetSample, RectangleShape, ReviewStatus
from datumdock.services.annotation_debug import AnnotationDebugLog
from datumdock.services.annotations import (
    AnnotationEditKind,
    AnnotationSaveRequest,
    AutosaveState,
)
from datumdock.services.dataset_library import DatasetLibraryService
from datumdock.services.managed_labels import LabelSetService
from datumdock.services.sample_repository import DatasetSampleRepository
from datumdock.ui.managed_gateway import ManagedDatasetGateway


def _sample(dataset_id: str, number: int) -> DatasetSample:
    return DatasetSample(
        dataset_id=dataset_id,
        filename=f"image_{number:06d}.png",
        original_filename=f"source-{number}.png",
        image_path=f"pool/images/image_{number:06d}.png",
        width=160,
        height=90,
        content_hash=f"{number:064x}",
        file_hash=f"{number + 1:064x}",
        perceptual_hash=f"{number:016x}102030",
        imported_at=datetime.now(UTC).isoformat(),
    )


def test_annotation_debug_log_is_lazy_limited_and_can_be_disabled(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """日志首次事件才创建，限制容量，并允许临时环境开关关闭。"""

    monkeypatch.delenv("DATUMDOCK_ANNOTATION_DEBUG", raising=False)
    debug = AnnotationDebugLog(tmp_path)
    assert not debug.path.exists()
    debug.record("测试诊断事件", sample_id="sample-1", document_version=3)
    assert debug.path.is_file()
    payload = json.loads(debug.path.read_text(encoding="utf-8").splitlines()[-1])
    assert payload["event"] == "测试诊断事件"
    assert payload["sample_id"] == "sample-1"
    assert payload["document_version"] == 3
    assert debug._handler is not None
    assert debug._handler.maxBytes == 2 * 1024 * 1024
    assert debug._handler.backupCount == 3
    debug.close()

    disabled_root = tmp_path / "disabled"
    monkeypatch.setenv("DATUMDOCK_ANNOTATION_DEBUG", "0")
    disabled = AnnotationDebugLog(disabled_root)
    disabled.record("不应写入")
    assert not disabled.path.exists()


def test_three_consecutive_samples_save_and_emit_correlated_debug_events(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """连续处理到第三张图片仍能保存，并在日志中串起加载、请求与提交。"""

    monkeypatch.delenv("DATUMDOCK_ANNOTATION_DEBUG", raising=False)
    root = tmp_path / "library"
    library = DatasetLibraryService(root)
    dataset = library.create_dataset("三图日志回归").dataset
    label_set = LabelSetService(library).add_label(
        dataset.id,
        class_id=0,
        name="part",
        alias="零件",
    )
    label = label_set.labels[0]
    repository = DatasetSampleRepository(
        library.dataset_repository.paths(dataset.id),
        dataset.id,
    )
    samples = tuple(_sample(dataset.id, number) for number in range(1, 4))
    for sample in samples:
        repository.add_sample(sample)

    gateway = ManagedDatasetGateway(library)
    try:
        for sample in samples:
            loaded = gateway.load_annotation(dataset.id, sample.id)
            assert loaded.document is not None
            assert loaded.checkpoint is not None
            document = loaded.document.model_copy(deep=True)
            document.rectangles.append(RectangleShape(label_id=label.id, x1=8, y1=9, x2=70, y2=55))
            request = AnnotationSaveRequest(
                dataset.id,
                sample.id,
                loaded.document.document_version + 1,
                loaded.disk_sha256,
                document,
                edit_kind=AnnotationEditKind.CREATE,
                base_document_version=loaded.document.document_version,
                label_set_revision=loaded.checkpoint.label_set_revision,
            )
            result = gateway.queue_annotation_save(request).result(timeout=10)
            assert result.sample_id == sample.id
            assert result.review_status == ReviewStatus.COMPLETED
            assert gateway.annotation_save_state(dataset.id)[0] == AutosaveState.SAVED

        for sample in samples:
            reopened = gateway.load_annotation(dataset.id, sample.id)
            assert reopened.document is not None
            assert len(reopened.document.rectangles) == 1
            assert reopened.review_status == ReviewStatus.COMPLETED
    finally:
        gateway.close()

    lines = [
        json.loads(line)
        for line in (root / "logs" / "annotation-debug.log")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    events = {line["event"] for line in lines}
    assert "Gateway 标注加载完成" in events
    assert "自动保存请求已入队" in events
    assert "标注 JSON 已发布" in events
    assert "标注 SQLite 已提交" in events
    assert "自动保存请求完成" in events
    logged_samples = {line.get("sample_id") for line in lines}
    assert {sample.id for sample in samples} <= logged_samples
    assert all("imageData" not in line for line in lines)
