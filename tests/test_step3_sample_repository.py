"""受管样本 SQLite v3 的迁移、分页和安全边界回归。"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from uuid import uuid4

import pytest

from datumdock.domain.models import DatasetSample, ReviewStatus, SampleSort
from datumdock.services.dataset_library import DatasetLibraryService
from datumdock.services.sample_repository import (
    DatasetSampleRepository,
    SampleQuery,
    SampleRepositoryError,
)


def _repository(tmp_path: Path) -> tuple[DatasetLibraryService, DatasetSampleRepository, str]:
    service = DatasetLibraryService(tmp_path / "library")
    bundle = service.create_dataset("图片池")
    paths = service.dataset_repository.paths(bundle.dataset.id)
    return service, DatasetSampleRepository(paths, bundle.dataset.id), bundle.dataset.id


def _sample(dataset_id: str, number: int, *, status: ReviewStatus | None = None):
    return DatasetSample(
        dataset_id=dataset_id,
        filename=f"image_{number:06d}.png",
        original_filename=f"source-{number}.jpg",
        image_path=f"pool/images/image_{number:06d}.png",
        width=80,
        height=40,
        content_hash=f"{number:064x}",
        file_hash=f"{number + 1:064x}",
        perceptual_hash=f"{number:016x}102030",
        review_status=status,
        imported_at=datetime.now(UTC).isoformat(),
    )


def _replace_with_v0_index(path: Path) -> None:
    path.unlink()
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE samples (
            id TEXT PRIMARY KEY,
            dataset_id TEXT NOT NULL,
            filename TEXT NOT NULL,
            image_path TEXT NOT NULL,
            annotation_path TEXT NOT NULL,
            width INTEGER NOT NULL,
            height INTEGER NOT NULL,
            content_hash TEXT NOT NULL,
            perceptual_hash TEXT NOT NULL,
            review_status TEXT NOT NULL,
            imported_at TEXT NOT NULL
        );
        CREATE TABLE sample_labels (
            sample_id TEXT NOT NULL,
            label_id TEXT NOT NULL,
            shape_id TEXT NOT NULL,
            PRIMARY KEY(sample_id, shape_id)
        );
        CREATE TABLE similarity_members (
            group_id TEXT NOT NULL,
            sample_id TEXT NOT NULL,
            confirmed INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(group_id, sample_id)
        );
        PRAGMA user_version = 0;
        """
    )
    connection.commit()
    connection.close()


def test_empty_step2_index_migrates_to_latest_schema(tmp_path: Path) -> None:
    """步骤二创建的 user_version=0 空索引应无损升级到当前版本。"""

    service = DatasetLibraryService(tmp_path / "library")
    bundle = service.create_dataset("迁移")
    paths = service.dataset_repository.paths(bundle.dataset.id)
    _replace_with_v0_index(paths.index)
    before = sqlite3.connect(paths.index)
    assert before.execute("PRAGMA user_version").fetchone()[0] == 0
    before.close()

    repository = DatasetSampleRepository(paths, bundle.dataset.id)

    connection = sqlite3.connect(paths.index)
    assert connection.execute("PRAGMA user_version").fetchone()[0] == 3
    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    connection.close()
    assert {"samples", "similarity_groups", "trash_items", "managed_operations"} <= tables
    assert repository.count_active() == 0


def test_valid_managed_v0_row_is_preserved_during_migration(tmp_path: Path) -> None:
    """旧绝对路径仅在当前 UUID 目录内时转换为相对路径。"""

    service = DatasetLibraryService(tmp_path / "library")
    bundle = service.create_dataset("旧索引")
    paths = service.dataset_repository.paths(bundle.dataset.id)
    _replace_with_v0_index(paths.index)
    image = paths.images / "legacy.png"
    image.write_bytes(b"legacy-placeholder")
    sample = _sample(bundle.dataset.id, 7)
    connection = sqlite3.connect(paths.index)
    connection.execute(
        "INSERT INTO samples VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            sample.id,
            sample.dataset_id,
            "legacy.png",
            str(image),
            "",
            sample.width,
            sample.height,
            sample.content_hash,
            sample.perceptual_hash,
            "unreviewed",
            sample.imported_at,
        ),
    )
    group_id = str(uuid4())
    connection.execute(
        "INSERT INTO similarity_members VALUES (?, ?, ?)",
        (group_id, sample.id, 1),
    )
    connection.commit()
    connection.close()

    repository = DatasetSampleRepository(paths, bundle.dataset.id)
    migrated = repository.get_sample(sample.id)

    assert migrated is not None
    assert migrated.image_path == "pool/images/legacy.png"
    assert repository.migration_issues() == ()
    assert repository.confirmed_similarity_mapping() == {sample.id: group_id}


def test_repository_pages_searches_sorts_and_filters_without_full_load(tmp_path: Path) -> None:
    """分页查询应由 SQLite 限制结果数量并返回独立总数。"""

    _service, repository, dataset_id = _repository(tmp_path)
    for number in range(450):
        status = ReviewStatus.PENDING_REVIEW if number % 20 == 0 else None
        repository.add_sample(_sample(dataset_id, number, status=status))

    page = repository.query(
        SampleQuery(
            dataset_id,
            offset=200,
            limit=200,
            sort=SampleSort.FILENAME_ASC,
        )
    )
    issues = repository.query(
        SampleQuery(dataset_id, review_status=ReviewStatus.PENDING_REVIEW, limit=200)
    )
    searched = repository.query(SampleQuery(dataset_id, search="source-44", limit=200))

    assert page.total == 450
    assert len(page.items) == 200
    assert page.items[0].filename == "image_000200.png"
    assert issues.total == 23
    assert searched.total == 11


def test_repository_rejects_absolute_and_escaping_sample_paths(tmp_path: Path) -> None:
    """外部绝对路径和相对逃逸都不能成为受管操作目标。"""

    _service, repository, dataset_id = _repository(tmp_path)
    absolute = _sample(dataset_id, 1)
    absolute.image_path = str((tmp_path / "outside.png").resolve())
    escaping = _sample(dataset_id, 2)
    escaping.image_path = "pool/images/../../dataset.json"

    with pytest.raises(SampleRepositoryError, match="路径"):
        repository.add_sample(absolute)
    with pytest.raises(SampleRepositoryError, match="路径"):
        repository.add_sample(escaping)


def test_perceptual_candidates_use_indexed_bands(tmp_path: Path) -> None:
    """近似候选返回真实位差，而明显不同颜色不会误入组。"""

    _service, repository, dataset_id = _repository(tmp_path)
    first = _sample(dataset_id, 1)
    first.perceptual_hash = "0000000000000000102030"
    close = _sample(dataset_id, 2)
    close.perceptual_hash = "0000000000000001102031"
    far = _sample(dataset_id, 3)
    far.perceptual_hash = "0000000000000000f0f0f0"
    for sample in (first, close, far):
        repository.add_sample(sample)

    candidates = repository.perceptual_candidates(first.perceptual_hash, exclude_id=first.id)

    assert [(item.id, distance) for item, distance in candidates] == [(close.id, 1)]


def test_ten_thousand_sample_index_returns_only_requested_page(tmp_path: Path) -> None:
    """万条回归只创建索引记录，不生成一万张原图。"""

    _service, repository, dataset_id = _repository(tmp_path)
    samples = [_sample(dataset_id, number) for number in range(10_000)]
    with repository._connection() as connection:
        for sample in samples:
            repository._insert_sample(connection, sample)

    started = perf_counter()
    page = repository.query(SampleQuery(dataset_id, offset=9_800, limit=200))
    elapsed = perf_counter() - started

    assert page.total == 10_000
    assert len(page.items) == 200
    assert page.items[-1].filename == "image_009999.png"
    assert elapsed < 1.0
