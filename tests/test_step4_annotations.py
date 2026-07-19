"""步骤四 LabelMe 有序兼容、原子保存、恢复和自动保存回归。"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from datumdock.domain.models import (
    AnnotationDocument,
    AnnotationState,
    DatasetSample,
    Label,
    LabelSet,
    RectangleShape,
    ReviewStatus,
    new_id,
)
from datumdock.services.annotations import (
    AnnotationAutosaveService,
    AnnotationConflictError,
    AnnotationHistory,
    AnnotationSaveRequest,
    AnnotationService,
    AnnotationServiceError,
    AutosaveState,
    review_status_after_edit,
)
from datumdock.services.dataset_library import DatasetLibraryService
from datumdock.services.labelme import LabelMeRepository
from datumdock.services.managed_labels import LabelSetService
from datumdock.services.sample_repository import (
    DatasetSampleRepository,
    SampleRepositoryError,
)


def _sample(dataset_id: str, number: int = 1) -> DatasetSample:
    return DatasetSample(
        dataset_id=dataset_id,
        filename=f"image_{number:06d}.png",
        original_filename=f"source-{number}.jpg",
        image_path=f"pool/images/image_{number:06d}.png",
        width=100,
        height=60,
        content_hash=f"{number:064x}",
        file_hash=f"{number + 1:064x}",
        perceptual_hash=f"{number:016x}102030",
        imported_at=datetime.now(UTC).isoformat(),
    )


def _managed_annotation(
    tmp_path: Path,
) -> tuple[DatasetLibraryService, str, DatasetSample, Label, AnnotationService]:
    library = DatasetLibraryService(tmp_path / "library")
    dataset = library.create_dataset("标注数据集").dataset
    label_set = LabelSetService(library).add_label(
        dataset.id,
        class_id=0,
        name="part",
        alias="零件",
    )
    sample = _sample(dataset.id)
    paths = library.dataset_repository.paths(dataset.id)
    DatasetSampleRepository(paths, dataset.id).add_sample(sample)
    return library, dataset.id, sample, label_set.labels[0], AnnotationService(library, dataset.id)


def test_labelme_preserves_mixed_shape_order_and_export_strips_private() -> None:
    """矩形与兼容 shape 的原顺序不变，交换负载不泄漏私有字段。"""

    label = Label(class_id=0, name="part", alias="零件", color="#4D8FBF")
    label_set = LabelSet(labels=[label])
    payload = {
        "version": "5.5.0",
        "flags": {"reviewed": False},
        "customRoot": {"source": "x-anylabeling"},
        "imagePath": "sample.png",
        "imageData": "preserved-data",
        "imageWidth": 100,
        "imageHeight": 60,
        "shapes": [
            {
                "label": "legacy",
                "points": [[1, 1], [2, 3], [4, 1]],
                "shape_type": "polygon",
                "attributes": {"keep": True},
            },
            {
                "label": "part",
                "points": [[10, 12], [40, 32]],
                "shape_type": "rectangle",
                "score": 0.8,
            },
            {
                "label": "note",
                "points": [[5, 5]],
                "shape_type": "point",
            },
        ],
    }
    repository = LabelMeRepository()
    document = repository.from_payload(payload, new_id(), label_set, "sample.png", (100, 60))
    managed = repository.to_payload(document, label_set, for_export=False)
    exported = repository.export_payload(document, label_set)

    assert [shape["shape_type"] for shape in managed["shapes"]] == [
        "polygon",
        "rectangle",
        "point",
    ]
    assert managed["imageData"] == "preserved-data"
    assert managed["customRoot"] == payload["customRoot"]
    assert managed["shapes"][1]["datumdock_label_id"] == label.id
    assert "datumdock_shape_id" not in json.dumps(exported)
    assert "datumdock_label_id" not in json.dumps(exported)


def test_atomic_save_updates_labelme_and_sqlite_then_reloads(tmp_path: Path) -> None:
    """一次保存同时提交有序 JSON、摘要、框数和标签反向索引。"""

    library, dataset_id, sample, label, service = _managed_annotation(tmp_path)
    loaded = service.load(sample.id)
    assert loaded.document is not None
    document = loaded.document.model_copy(deep=True)
    document.rectangles.append(RectangleShape(label_id=label.id, x1=5, y1=6, x2=45, y2=36))
    document.review_status = ReviewStatus.PENDING_REVIEW

    result = service.save(
        AnnotationSaveRequest(dataset_id, sample.id, 1, loaded.disk_sha256, document)
    )

    paths = library.dataset_repository.paths(dataset_id)
    annotation = paths.annotations / "image_000001.json"
    payload = json.loads(annotation.read_text(encoding="utf-8"))
    indexed = DatasetSampleRepository(paths, dataset_id).get_sample(sample.id)
    assert result.sqlite_synced is True
    assert result.json_sha256 == hashlib.sha256(annotation.read_bytes()).hexdigest()
    assert payload["shapes"][0]["datumdock_label_id"] == label.id
    assert indexed is not None
    assert indexed.annotation_count == 1
    assert indexed.annotation_version == 1
    assert indexed.review_status == ReviewStatus.PENDING_REVIEW
    assert DatasetSampleRepository(paths, dataset_id).label_usage_counts()[label.id] == (1, 1)
    reopened = service.load(sample.id)
    assert reopened.document is not None
    assert reopened.document.rectangles[0].id == document.rectangles[0].id


def test_autosave_digest_rebase_is_isolated_per_sample(tmp_path: Path) -> None:
    """同一数据集连续编辑两张图时，绝不能跨图片复用磁盘摘要。"""

    library, dataset_id, first, label, service = _managed_annotation(tmp_path)
    paths = library.dataset_repository.paths(dataset_id)
    second = _sample(dataset_id, 2)
    DatasetSampleRepository(paths, dataset_id).add_sample(second)
    second_loaded = service.load(second.id)
    assert second_loaded.document is not None
    second_v1 = second_loaded.document.model_copy(deep=True)
    second_v1.rectangles.append(RectangleShape(label_id=label.id, x1=2, y1=2, x2=20, y2=20))
    second_v1.review_status = ReviewStatus.PENDING_REVIEW
    second_saved = service.save(AnnotationSaveRequest(dataset_id, second.id, 1, "", second_v1))

    autosave = AnnotationAutosaveService(service)
    first_loaded = service.load(first.id)
    assert first_loaded.document is not None
    first_document = first_loaded.document.model_copy(deep=True)
    first_document.rectangles.append(RectangleShape(label_id=label.id, x1=4, y1=4, x2=24, y2=24))
    first_document.review_status = ReviewStatus.PENDING_REVIEW
    autosave.submit(AnnotationSaveRequest(dataset_id, first.id, 1, "", first_document)).result(
        timeout=5
    )

    second_v2 = second_v1.model_copy(deep=True)
    second_v2.rectangles[0].x2 = 30
    result = autosave.submit(
        AnnotationSaveRequest(
            dataset_id,
            second.id,
            2,
            second_saved.json_sha256,
            second_v2,
        )
    ).result(timeout=5)
    autosave.close()

    assert result.saved_version == 2
    reloaded = service.load(second.id)
    assert reloaded.document is not None
    assert reloaded.document.rectangles[0].x2 == 30


def test_empty_document_is_lazy_but_confirmed_negative_creates_json(tmp_path: Path) -> None:
    """普通空标注不建文件，确认无目标后才建立可交换的空 JSON。"""

    library, dataset_id, sample, _label, service = _managed_annotation(tmp_path)
    loaded = service.load(sample.id)
    assert loaded.document is not None
    first = loaded.document.model_copy(deep=True)
    first.review_status = ReviewStatus.PENDING_REVIEW
    service.save(AnnotationSaveRequest(dataset_id, sample.id, 1, "", first))
    path = library.dataset_repository.paths(dataset_id).annotations / "image_000001.json"
    assert not path.exists()

    negative = first.model_copy(deep=True)
    negative.review_status = ReviewStatus.COMPLETED_NEGATIVE
    result = service.save(AnnotationSaveRequest(dataset_id, sample.id, 2, "", negative))

    assert path.is_file()
    assert result.json_sha256
    indexed = DatasetSampleRepository(
        library.dataset_repository.paths(dataset_id), dataset_id
    ).get_sample(sample.id)
    assert indexed is not None
    assert indexed.review_status == ReviewStatus.COMPLETED_NEGATIVE


def test_corrupt_json_stays_byte_identical_and_read_only(tmp_path: Path) -> None:
    """损坏 JSON 只进入异常状态，加载和保存都不能覆盖原字节。"""

    library, dataset_id, sample, _label, service = _managed_annotation(tmp_path)
    path = library.dataset_repository.paths(dataset_id).annotations / "image_000001.json"
    original = b'{"shapes": [broken'
    path.write_bytes(original)

    loaded = service.load(sample.id)

    assert loaded.editable is False
    assert loaded.annotation_state == AnnotationState.CORRUPT
    assert path.read_bytes() == original
    document = AnnotationDocument(
        sample_id=sample.id,
        image_filename=sample.filename,
        image_width=sample.width,
        image_height=sample.height,
    )
    with pytest.raises(AnnotationServiceError, match="原文件未被覆盖"):
        service.save(
            AnnotationSaveRequest(
                dataset_id,
                sample.id,
                1,
                hashlib.sha256(original).hexdigest(),
                document,
            )
        )
    assert path.read_bytes() == original


def test_save_rejects_conflict_zero_area_and_out_of_bounds_but_warns_tiny(
    tmp_path: Path,
) -> None:
    """并发摘要、零面积和越界框阻断保存，正面积微小框只给质量警告。"""

    _library, dataset_id, sample, label, service = _managed_annotation(tmp_path)
    loaded = service.load(sample.id)
    assert loaded.document is not None
    conflict = loaded.document.model_copy(deep=True)
    conflict.rectangles.append(RectangleShape(label_id=label.id, x1=1, y1=1, x2=3, y2=3))
    with pytest.raises(AnnotationConflictError):
        service.save(AnnotationSaveRequest(dataset_id, sample.id, 1, "f" * 64, conflict))

    zero = loaded.document.model_copy(deep=True)
    zero.rectangles.append(RectangleShape(label_id=label.id, x1=2, y1=2, x2=2, y2=3))
    with pytest.raises(AnnotationServiceError, match="零面积"):
        service.save(AnnotationSaveRequest(dataset_id, sample.id, 1, "", zero))

    outside = loaded.document.model_copy(deep=True)
    outside.rectangles.append(RectangleShape(label_id=label.id, x1=1, y1=1, x2=101, y2=20))
    with pytest.raises(AnnotationServiceError, match="边界"):
        service.save(AnnotationSaveRequest(dataset_id, sample.id, 1, "", outside))

    tiny = loaded.document.model_copy(deep=True)
    tiny.rectangles.append(RectangleShape(label_id=label.id, x1=1, y1=1, x2=1.5, y2=1.5))
    result = service.save(AnnotationSaveRequest(dataset_id, sample.id, 1, "", tiny))
    assert "尺寸很小" in result.warnings[0]


def test_sqlite_failure_leaves_marker_and_startup_replays_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """JSON 已提交但 SQLite 失败时保留恢复标记，重启只回放该标记。"""

    _library, dataset_id, sample, label, service = _managed_annotation(tmp_path)
    loaded = service.load(sample.id)
    assert loaded.document is not None
    document = loaded.document.model_copy(deep=True)
    document.rectangles.append(RectangleShape(label_id=label.id, x1=5, y1=5, x2=30, y2=25))

    def fail_index(*_args, **_kwargs) -> None:
        raise SampleRepositoryError("模拟 SQLite 写入失败")

    monkeypatch.setattr(service.samples, "update_annotation_index", fail_index)
    with pytest.raises(AnnotationServiceError, match="SQLite"):
        service.save(AnnotationSaveRequest(dataset_id, sample.id, 1, "", document))
    marker = service.recovery_directory / f"{sample.id}.json"
    assert marker.is_file()

    restarted = AnnotationService(DatasetLibraryService(tmp_path / "library"), dataset_id)
    report = restarted.recover_pending()

    assert report.recovered == 1
    assert not marker.exists()
    restored = restarted.samples.get_sample(sample.id)
    assert restored is not None
    assert restored.annotation_count == 1
    assert restored.annotation_state == AnnotationState.READY


def test_history_and_autosave_keep_latest_version(tmp_path: Path) -> None:
    """单手势形成一个历史节点，串行保存最终以最新文档版本为准。"""

    _library, dataset_id, sample, label, service = _managed_annotation(tmp_path)
    loaded = service.load(sample.id)
    assert loaded.document is not None
    first = loaded.document.model_copy(deep=True)
    first.rectangles.append(RectangleShape(label_id=label.id, x1=2, y1=2, x2=20, y2=20))
    first.review_status = review_status_after_edit(first.review_status)
    history = AnnotationHistory(loaded.document)
    history.record(first)
    history.record(first)
    assert history.can_undo is True
    assert history.undo().rectangles == []
    assert history.redo().rectangles[0].x2 == 20

    second = first.model_copy(deep=True)
    second.rectangles[0].x2 = 35
    autosave = AnnotationAutosaveService(service)
    try:
        autosave.submit(AnnotationSaveRequest(dataset_id, sample.id, 1, "", first))
        latest = autosave.submit(AnnotationSaveRequest(dataset_id, sample.id, 2, "", second))
        assert latest.result(timeout=10).saved_version == 2
        assert autosave.state == AutosaveState.SAVED
    finally:
        autosave.close()
    reopened = service.load(sample.id)
    assert reopened.document is not None
    assert reopened.document.rectangles[0].x2 == 35
    assert reopened.document.document_version == 2


@pytest.mark.parametrize(
    ("status", "count", "message"),
    [
        (ReviewStatus.COMPLETED, 0, "至少需要"),
        (ReviewStatus.COMPLETED_NEGATIVE, 1, "不能包含"),
    ],
)
def test_review_completion_rules(
    tmp_path: Path,
    status: ReviewStatus,
    count: int,
    message: str,
) -> None:
    """完成与完成无目标状态均有明确框数约束。"""

    _library, dataset_id, sample, label, service = _managed_annotation(tmp_path)
    loaded = service.load(sample.id)
    assert loaded.document is not None
    document = loaded.document.model_copy(deep=True)
    if count:
        document.rectangles.append(RectangleShape(label_id=label.id, x1=2, y1=2, x2=20, y2=20))
    document.review_status = status
    with pytest.raises(AnnotationServiceError, match=message):
        service.save(AnnotationSaveRequest(dataset_id, sample.id, 1, "", document))


def test_issue_status_stays_issue_after_edit() -> None:
    """有问题图片只有人工重新确认完成后才移除问题状态。"""

    assert review_status_after_edit(ReviewStatus.ISSUE) == ReviewStatus.ISSUE
    assert review_status_after_edit(ReviewStatus.COMPLETED) == ReviewStatus.PENDING_REVIEW
    assert review_status_after_edit(ReviewStatus.COMPLETED_NEGATIVE) == ReviewStatus.PENDING_REVIEW
