"""步骤四连续标注、万级筛选与多数据集隔离回归。"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter

from datumdock.domain.models import (
    AnnotationDocument,
    AnnotationState,
    DatasetSample,
    RectangleShape,
    ReviewStatus,
    new_id,
)
from datumdock.services.annotations import AnnotationSaveRequest, AnnotationService
from datumdock.services.dataset_library import DatasetLibraryService
from datumdock.services.managed_labels import LabelSetService
from datumdock.services.sample_repository import DatasetSampleRepository, SampleQuery
from datumdock.ui.managed_gateway import ManagedDatasetGateway


def _sample(dataset_id: str, number: int) -> DatasetSample:
    return DatasetSample(
        dataset_id=dataset_id,
        filename=f"image_{number:06d}.png",
        original_filename=f"source-{number}.jpg",
        image_path=f"pool/images/image_{number:06d}.png",
        width=96,
        height=64,
        content_hash=f"{number + 1:064x}",
        file_hash=f"{number + 2:064x}",
        perceptual_hash=f"{number:016x}102030",
        imported_at=datetime.now(UTC).isoformat(),
    )


def _document(sample: DatasetSample, label_id: str, version: int = 1) -> AnnotationDocument:
    return AnnotationDocument(
        sample_id=sample.id,
        image_filename=sample.filename,
        image_width=sample.width,
        image_height=sample.height,
        rectangles=[RectangleShape(label_id=label_id, x1=4, y1=5, x2=40, y2=32)],
        document_version=version,
    )


def test_one_hundred_annotations_save_reload_without_cross_sample_state(tmp_path: Path) -> None:
    """连续处理一百张图片后，重启仍能按稳定 ID 恢复各自标注。"""

    root = tmp_path / "library"
    library = DatasetLibraryService(root)
    dataset = library.create_dataset("百图连续标注").dataset
    label = (
        LabelSetService(library)
        .add_label(
            dataset.id,
            class_id=0,
            name="part",
            alias="零件",
        )
        .labels[0]
    )
    paths = library.dataset_repository.paths(dataset.id)
    samples = tuple(_sample(dataset.id, number) for number in range(100))
    repository = DatasetSampleRepository(paths, dataset.id)
    with repository._connection() as connection:
        for sample in samples:
            repository._insert_sample(connection, sample)
    service = AnnotationService(library, dataset.id)
    for sample in samples:
        service.save(
            AnnotationSaveRequest(
                dataset.id,
                sample.id,
                1,
                "",
                _document(sample, label.id),
            )
        )

    restarted = DatasetLibraryService(root)
    reopened = AnnotationService(restarted, dataset.id)
    for sample in samples:
        loaded = reopened.load(sample.id)
        assert loaded.document is not None
        assert loaded.document.rectangles[0].label_id == label.id
        assert loaded.document.document_version == 1


def test_ten_thousand_label_status_query_and_location_stay_paged(tmp_path: Path) -> None:
    """万条索引的标签、复核筛选和跨页定位均由 SQLite 完成。"""

    library = DatasetLibraryService(tmp_path / "library")
    dataset = library.create_dataset("万级标注索引").dataset
    label = (
        LabelSetService(library)
        .add_label(
            dataset.id,
            class_id=0,
            name="part",
            alias="零件",
        )
        .labels[0]
    )
    repository = DatasetSampleRepository(
        library.dataset_repository.paths(dataset.id),
        dataset.id,
    )
    samples = tuple(_sample(dataset.id, number) for number in range(10_000))
    selected = samples[:5_000]
    with repository._connection() as connection:
        for sample in samples:
            repository._insert_sample(connection, sample)
        connection.executemany(
            "UPDATE samples SET review_status = ?, annotation_state = ?, "
            "annotation_count = 1 WHERE id = ?",
            (
                (ReviewStatus.COMPLETED.value, AnnotationState.READY.value, sample.id)
                for sample in selected
            ),
        )
        connection.executemany(
            "INSERT INTO sample_labels(sample_id, label_id, shape_id) VALUES (?, ?, ?)",
            ((sample.id, label.id, new_id()) for sample in selected),
        )

    query = SampleQuery(
        dataset.id,
        limit=200,
        review_status=ReviewStatus.COMPLETED,
        label_id=label.id,
    )
    started = perf_counter()
    page = repository.query(query)
    position = repository.locate_sample(selected[-1].id, query)
    elapsed = perf_counter() - started

    assert page.total == 5_000
    assert len(page.items) == 200
    assert position == 4_999
    assert repository.label_usage_counts()[label.id] == (5_000, 5_000)
    assert elapsed < 1.5


def test_two_dataset_autosaves_keep_labels_documents_and_results_isolated(
    tmp_path: Path,
) -> None:
    """两个数据集快速排队保存时，标签、文档和摘要不能互相串联。"""

    library = DatasetLibraryService(tmp_path / "library")
    first_dataset = library.create_dataset("数据集甲").dataset
    second_dataset = library.create_dataset("数据集乙").dataset
    labels = LabelSetService(library)
    first_label = labels.add_label(
        first_dataset.id,
        class_id=0,
        name="part",
        alias="甲零件",
    ).labels[0]
    second_label = labels.add_label(
        second_dataset.id,
        class_id=0,
        name="part",
        alias="乙零件",
    ).labels[0]
    first_sample = _sample(first_dataset.id, 1)
    second_sample = _sample(second_dataset.id, 2)
    first_paths = library.dataset_repository.paths(first_dataset.id)
    second_paths = library.dataset_repository.paths(second_dataset.id)
    DatasetSampleRepository(first_paths, first_dataset.id).add_sample(first_sample)
    DatasetSampleRepository(second_paths, second_dataset.id).add_sample(second_sample)
    gateway = ManagedDatasetGateway(library)

    first_future = gateway.queue_annotation_save(
        AnnotationSaveRequest(
            first_dataset.id,
            first_sample.id,
            1,
            "",
            _document(first_sample, first_label.id),
        )
    )
    second_future = gateway.queue_annotation_save(
        AnnotationSaveRequest(
            second_dataset.id,
            second_sample.id,
            1,
            "",
            _document(second_sample, second_label.id),
        )
    )
    first_result = first_future.result(timeout=5)
    second_result = second_future.result(timeout=5)

    first_loaded = gateway.load_annotation(first_dataset.id, first_sample.id)
    second_loaded = gateway.load_annotation(second_dataset.id, second_sample.id)
    gateway.close()
    assert first_result.json_sha256 != ""
    assert second_result.json_sha256 != ""
    assert first_label.id != second_label.id
    assert first_loaded.document is not None
    assert second_loaded.document is not None
    assert first_loaded.document.rectangles[0].label_id == first_label.id
    assert second_loaded.document.rectangles[0].label_id == second_label.id
    assert not (first_paths.annotations / second_sample.filename.replace(".png", ".json")).exists()
    assert not (second_paths.annotations / first_sample.filename.replace(".png", ".json")).exists()
