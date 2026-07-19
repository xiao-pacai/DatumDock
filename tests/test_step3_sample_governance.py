"""步骤三重命名、回收站与永久删除的一致性回归。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from PIL import Image

from datumdock.domain.models import AnnotationState, NamingPolicy
from datumdock.services.dataset_library import DatasetLibraryService
from datumdock.services.image_pool import ImageImportPreflightRequest, ImageImportService
from datumdock.services.sample_governance import (
    RenamePlan,
    RenamePlanItem,
    SampleGovernanceError,
    SampleRenameService,
    TrashService,
)
from datumdock.services.sample_repository import DatasetSampleRepository


def _pool(tmp_path: Path, count: int = 2):
    library = DatasetLibraryService(tmp_path / "library")
    bundle = library.create_dataset("治理测试")
    paths = library.dataset_repository.paths(bundle.dataset.id)
    repository = DatasetSampleRepository(paths, bundle.dataset.id)
    importer = ImageImportService(paths, bundle.dataset, repository)
    sources: list[Path] = []
    for index in range(count):
        source = tmp_path / f"source-{index}.jpg"
        Image.new("RGB", (32 + index, 24), (30 + index * 40, 80, 120)).save(source)
        sources.append(source)
    prepared = importer.preflight(ImageImportPreflightRequest(bundle.dataset.id, tuple(sources)))
    report = importer.commit(prepared, {})
    assert len(report.imported_sample_ids) == count
    return library, bundle, paths, repository, report.imported_sample_ids


def test_batch_rename_keeps_uuid_thumbnail_and_updates_future_labelme(tmp_path: Path) -> None:
    """重命名只改变受管名，未来同名标注的 imagePath 必须同步。"""

    library, bundle, paths, repository, sample_ids = _pool(tmp_path)
    first = repository.get_sample(sample_ids[0])
    assert first is not None
    annotation = paths.annotations / f"{Path(first.filename).stem}.json"
    annotation.write_text(
        json.dumps({"version": "5.0", "imagePath": first.filename, "shapes": []}),
        encoding="utf-8",
    )
    thumbnail_before = first.thumbnail_path
    policy = NamingPolicy(prefix="part", start_index=10, padding=4)
    service = SampleRenameService(paths, bundle.dataset, repository)
    plan = service.preview(policy)

    renamed_ids = service.apply(plan)

    assert renamed_ids == tuple(item.sample_id for item in plan.items)
    renamed = repository.get_sample(first.id)
    assert renamed is not None
    assert renamed.id == first.id
    assert renamed.filename == "part_0010.png"
    assert renamed.thumbnail_path == thumbnail_before
    new_annotation = paths.annotations / "part_0010.json"
    assert json.loads(new_annotation.read_text(encoding="utf-8"))["imagePath"] == renamed.filename
    assert renamed.annotation_sha256 == hashlib.sha256(new_annotation.read_bytes()).hexdigest()
    assert renamed.annotation_updated_at
    assert not annotation.exists()
    # 成功后才能由资料库服务保存命名策略。
    configuration = bundle.dataset.configuration.model_copy(update={"naming_policy": policy})
    library.update_configuration(bundle.dataset.id, configuration)
    assert library.open_dataset(bundle.dataset.id).dataset.configuration.naming_policy == policy


def test_two_stage_rename_supports_filename_exchange(tmp_path: Path) -> None:
    """文件名互换时不依赖操作系统对覆盖语义的猜测。"""

    _library, bundle, paths, repository, sample_ids = _pool(tmp_path)
    left = repository.get_sample(sample_ids[0])
    right = repository.get_sample(sample_ids[1])
    assert left is not None and right is not None
    left_bytes = repository.resolve_path(left.image_path, "pool/images").read_bytes()
    plan = RenamePlan(
        (
            RenamePlanItem(
                left.id,
                left.filename,
                right.filename,
                left.image_path,
                right.image_path,
            ),
            RenamePlanItem(
                right.id,
                right.filename,
                left.filename,
                right.image_path,
                left.image_path,
            ),
        ),
        bundle.dataset.configuration.naming_policy,
    )

    SampleRenameService(paths, bundle.dataset, repository).apply(plan)

    updated_left = repository.get_sample(left.id)
    assert updated_left is not None
    assert updated_left.filename == right.filename
    assert (
        repository.resolve_path(updated_left.image_path, "pool/images").read_bytes() == left_bytes
    )


def test_trash_restore_and_permanent_delete_keep_index_and_files_consistent(
    tmp_path: Path,
) -> None:
    """少量删除进入回收站，恢复后 UUID 不变，永久删除清除全部关联项。"""

    _library, bundle, paths, repository, sample_ids = _pool(tmp_path)
    service = TrashService(paths, bundle.dataset, repository)
    first = repository.get_sample(sample_ids[0])
    assert first is not None
    annotation = paths.annotations / f"{Path(first.filename).stem}.json"
    annotation.write_text(json.dumps({"imagePath": first.filename, "shapes": []}), encoding="utf-8")
    impact = service.preview((first.id,), threshold=30)
    assert impact.use_trash is True
    assert impact.annotation_count == 1

    service.delete(impact)

    assert repository.get_sample(first.id) is None
    trashed = repository.get_sample(first.id, include_trashed=True)
    assert trashed is not None and trashed.is_trashed
    assert not repository.resolve_path(first.image_path, "pool/images").exists()
    assert len(service.list_items()) == 1

    restored = service.restore(first.id)
    assert restored.id == first.id
    assert repository.resolve_path(restored.image_path, "pool/images").is_file()
    restored_annotation = repository.resolve_path(
        restored.annotation_path,
        "pool/annotations",
    )
    assert (
        restored.annotation_sha256 == hashlib.sha256(restored_annotation.read_bytes()).hexdigest()
    )
    assert (
        json.loads(restored_annotation.read_text(encoding="utf-8"))["imagePath"]
        == restored.filename
    )
    assert len(service.list_items()) == 0

    service.permanently_delete(restored.id)
    assert repository.get_sample(restored.id, include_trashed=True) is None
    assert not repository.resolve_path(restored.image_path, "pool/images").exists()
    assert not (paths.annotations / f"{Path(restored.filename).stem}.json").exists()


def test_restore_name_conflict_allocates_safe_new_name(tmp_path: Path) -> None:
    """回收站原名被占用时保留样本 UUID，并按当前规则分配新名称。"""

    _library, bundle, paths, repository, sample_ids = _pool(tmp_path)
    service = TrashService(paths, bundle.dataset, repository)
    first = repository.get_sample(sample_ids[0])
    second = repository.get_sample(sample_ids[1])
    assert first is not None and second is not None
    service.delete(service.preview((first.id,), threshold=30))
    second_path = repository.resolve_path(second.image_path, "pool/images")
    occupied = paths.images / first.filename
    second_path.replace(occupied)
    repository.update_sample_paths(
        second.id,
        filename=first.filename,
        image_path=occupied.relative_to(paths.root).as_posix(),
        annotation_path="",
    )

    restored = service.restore(first.id)

    assert restored.id == first.id
    assert restored.filename != first.filename
    assert occupied.is_file()
    assert repository.resolve_path(restored.image_path, "pool/images").is_file()


def test_large_delete_defaults_to_direct_permanent_removal(tmp_path: Path) -> None:
    """是否进入回收站只由本次删除数量和阈值决定。"""

    _library, bundle, paths, repository, sample_ids = _pool(tmp_path, count=3)
    service = TrashService(paths, bundle.dataset, repository)
    impact = service.preview(sample_ids, threshold=2)
    assert impact.use_trash is False

    service.delete(impact)

    assert repository.count_active() == 0
    assert service.list_items() == ()
    assert list(paths.images.glob("*.png")) == []


def test_rename_sqlite_failure_restores_annotation_bytes_and_digest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SQLite 提交失败时，文件名、JSON 字节和摘要必须一起回到旧版本。"""

    _library, bundle, paths, repository, sample_ids = _pool(tmp_path, count=1)
    sample = repository.get_sample(sample_ids[0])
    assert sample is not None
    annotation = paths.annotations / f"{Path(sample.filename).stem}.json"
    annotation.write_text(
        json.dumps({"imagePath": sample.filename, "shapes": []}, ensure_ascii=False),
        encoding="utf-8",
    )
    original_bytes = annotation.read_bytes()
    repository.update_annotation_index(
        sample.id,
        annotation_path=annotation.relative_to(paths.root).as_posix(),
        annotation_count=0,
        annotation_state=AnnotationState.READY,
        annotation_version=1,
        annotation_sha256=hashlib.sha256(original_bytes).hexdigest(),
        annotation_updated_at="2026-07-19T00:00:00+00:00",
        review_status=None,
        shape_labels=(),
    )
    original_rename = repository.rename_sample_paths
    calls = 0

    def fail_once(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("模拟 SQLite 写入失败")
        return original_rename(*args, **kwargs)

    monkeypatch.setattr(repository, "rename_sample_paths", fail_once)
    service = SampleRenameService(paths, bundle.dataset, repository)
    plan = service.preview(NamingPolicy(prefix="renamed", start_index=1, padding=4))

    with pytest.raises(SampleGovernanceError, match="批量重命名失败"):
        service.apply(plan)

    restored = repository.get_sample(sample.id)
    assert restored is not None
    assert restored.filename == sample.filename
    assert restored.annotation_sha256 == hashlib.sha256(original_bytes).hexdigest()
    assert annotation.read_bytes() == original_bytes
    assert repository.list_operations() == ()
