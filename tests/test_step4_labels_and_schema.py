"""步骤四标签体系与 SQLite v2 迁移回归。"""

from __future__ import annotations

import hashlib
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from datumdock.domain.models import (
    AnnotationState,
    DatasetSample,
    LabelStatus,
    ReviewStatus,
)
from datumdock.services.dataset_library import DatasetLibraryService
from datumdock.services.managed_labels import LabelSetService, ManagedLabelError
from datumdock.services.sample_repository import DatasetSampleRepository


def _sample(dataset_id: str, number: int = 1) -> DatasetSample:
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
        imported_at=datetime.now(UTC).isoformat(),
    )


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_label_crud_persists_revision_and_search(tmp_path: Path) -> None:
    """真实标签编辑重启后保留，并支持别名、描述和同义词检索。"""

    library = DatasetLibraryService(tmp_path / "library")
    dataset = library.create_dataset("标签数据集").dataset
    labels = LabelSetService(library)

    created = labels.add_label(
        dataset.id,
        class_id=None,
        name="metal_part",
        alias="金属零件",
        description="设备上的银色零件",
        synonyms=("工件", "part"),
    )

    assert created.revision == 1
    assert labels.list_labels(dataset.id, search="银色")[0].alias == "金属零件"
    restarted = DatasetLibraryService(tmp_path / "library")
    restored = restarted.open_dataset(dataset.id).label_set
    assert restored.revision == 1
    assert restored.labels[0].class_id == 0


def test_display_change_never_rewrites_annotation_json(tmp_path: Path) -> None:
    """别名、描述、同义词和颜色变化不能触碰任何 LabelMe 文件。"""

    library = DatasetLibraryService(tmp_path / "library")
    bundle = library.create_dataset("展示字段")
    labels = LabelSetService(library)
    label_set = labels.add_label(
        bundle.dataset.id,
        class_id=0,
        name="part",
        alias="零件",
    )
    annotation = library.dataset_repository.paths(bundle.dataset.id).annotations / "image.json"
    annotation.write_text('{"version":"5.4.1","shapes":[]}\n', encoding="utf-8")
    before = _hash(annotation)

    labels.update_display_fields(
        bundle.dataset.id,
        label_set.labels[0].id,
        alias="金属零件",
        description="新的说明",
        synonyms=("工件",),
        color="#E69262",
        expected_revision=label_set.revision,
    )

    assert _hash(annotation) == before


def test_archived_class_id_is_not_reused_and_restore_revalidates_color(tmp_path: Path) -> None:
    """归档标签仍占用类别 ID，恢复时重新检查活动颜色唯一性。"""

    library = DatasetLibraryService(tmp_path / "library")
    dataset = library.create_dataset("归档规则").dataset
    labels = LabelSetService(library)
    first = labels.add_label(
        dataset.id,
        class_id=0,
        name="first",
        alias="第一个",
        color="#4D8FBF",
    )
    archived = labels.set_status(
        dataset.id,
        first.labels[0].id,
        LabelStatus.ARCHIVED,
        expected_revision=first.revision,
    )
    second = labels.add_label(
        dataset.id,
        class_id=None,
        name="second",
        alias="第二个",
        color="#4D8FBF",
    )
    assert second.labels[1].class_id == 1

    with pytest.raises(ManagedLabelError, match="颜色"):
        labels.set_status(
            dataset.id,
            archived.labels[0].id,
            LabelStatus.ACTIVE,
            expected_revision=second.revision,
        )


def test_v1_schema_migrates_status_and_annotation_summary(tmp_path: Path) -> None:
    """步骤三 v1 索引升级后旧复核状态被规范化，标注摘要可原子提交。"""

    library = DatasetLibraryService(tmp_path / "library")
    bundle = library.create_dataset("索引迁移")
    paths = library.dataset_repository.paths(bundle.dataset.id)
    paths.index.unlink()
    connection = sqlite3.connect(paths.index)
    DatasetSampleRepository._create_v1_schema(connection)
    connection.execute("PRAGMA user_version = 1")
    sample = _sample(bundle.dataset.id)
    values = (
        sample.id,
        sample.dataset_id,
        sample.filename,
        sample.original_filename,
        sample.image_path,
        "",
        sample.width,
        sample.height,
        sample.image_mode,
        sample.managed_format,
        sample.content_hash,
        sample.file_hash,
        sample.perceptual_hash,
        sample.perceptual_hash_version,
        ReviewStatus.AUTO_PENDING_REVIEW.value,
        sample.health.value,
        sample.thumbnail_state.value,
        "",
        0,
        None,
        None,
        sample.imported_at,
    )
    connection.execute(
        "INSERT INTO samples VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", values
    )
    connection.commit()
    connection.close()

    repository = DatasetSampleRepository(paths, bundle.dataset.id)
    migrated = repository.get_sample(sample.id)
    assert migrated is not None
    assert migrated.review_status == ReviewStatus.PENDING_REVIEW
    assert migrated.annotation_state == AnnotationState.MISSING

    label_set = LabelSetService(library).add_label(
        bundle.dataset.id,
        class_id=0,
        name="part",
        alias="零件",
    )
    shape_id = label_set.id
    repository.update_annotation_index(
        sample.id,
        annotation_path="pool/annotations/image_000001.json",
        annotation_count=1,
        annotation_state=AnnotationState.READY,
        annotation_version=1,
        annotation_sha256="a" * 64,
        annotation_updated_at=datetime.now(UTC).isoformat(),
        review_status=ReviewStatus.PENDING_REVIEW,
        shape_labels=((shape_id, label_set.labels[0].id),),
    )
    updated = repository.get_sample(sample.id)
    assert updated is not None
    assert updated.annotation_count == 1
    assert repository.label_usage_counts()[label_set.labels[0].id] == (1, 1)


def test_label_revision_blocks_stale_update(tmp_path: Path) -> None:
    """并发标签编辑必须通过修订号检查，不能覆盖较新的修改。"""

    library = DatasetLibraryService(tmp_path / "library")
    dataset = library.create_dataset("修订号").dataset
    labels = LabelSetService(library)
    current = labels.add_label(
        dataset.id,
        class_id=0,
        name="part",
        alias="零件",
    )
    labels.update_display_fields(
        dataset.id,
        current.labels[0].id,
        alias="零件一",
        description="",
        synonyms=(),
        color=current.labels[0].color,
        expected_revision=current.revision,
    )

    with pytest.raises(ManagedLabelError, match="刷新"):
        labels.update_display_fields(
            dataset.id,
            current.labels[0].id,
            alias="过期修改",
            description="",
            synonyms=(),
            color=current.labels[0].color,
            expected_revision=current.revision,
        )
