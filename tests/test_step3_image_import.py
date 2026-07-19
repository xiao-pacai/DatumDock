"""步骤三 PNG 规范化、重复决策与缩略图回归。"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from PIL import Image

from datumdock.domain.models import SimilarityStatus, utc_now
from datumdock.services.dataset_library import DatasetLibraryService
from datumdock.services.image_pool import (
    DuplicateDecision,
    ImageImportPreflightRequest,
    ImageImportService,
    ImagePoolError,
    ThumbnailService,
)
from datumdock.services.sample_repository import DatasetSampleRepository, SampleQuery


def _services(tmp_path: Path):
    library = DatasetLibraryService(tmp_path / "library")
    bundle = library.create_dataset("导入测试")
    paths = library.dataset_repository.paths(bundle.dataset.id)
    repository = DatasetSampleRepository(paths, bundle.dataset.id)
    importer = ImageImportService(paths, bundle.dataset, repository)
    return library, bundle, paths, repository, importer


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize(
    "suffix,format_name",
    [
        (".jpg", "JPEG"),
        (".jpeg", "JPEG"),
        (".png", "PNG"),
        (".bmp", "BMP"),
        (".webp", "WEBP"),
        (".tiff", "TIFF"),
    ],
)
def test_six_static_formats_are_copied_as_managed_png(
    tmp_path: Path, suffix: str, format_name: str
) -> None:
    """所有支持格式都只读取来源，并在 UUID 数据集内发布 PNG。"""

    _library, bundle, paths, repository, importer = _services(tmp_path)
    source = tmp_path / f"source{suffix}"
    Image.new("RGB", (48, 32), (80, 120, 160)).save(source, format_name)
    source_hash = _sha256(source)

    prepared = importer.preflight(ImageImportPreflightRequest(bundle.dataset.id, (source,)))
    report = importer.commit(prepared, {})

    assert len(report.imported_sample_ids) == 1
    assert _sha256(source) == source_hash
    sample = repository.get_sample(report.imported_sample_ids[0])
    assert sample is not None
    assert sample.original_filename == source.name
    assert Path(sample.image_path).is_absolute() is False
    managed = repository.resolve_path(sample.image_path, "pool/images")
    assert managed.parent == paths.images
    assert managed.suffix == ".png"
    with Image.open(managed) as image:
        assert image.format == "PNG"
        assert image.size == (48, 32)


def test_exif_orientation_grayscale_and_alpha_are_preserved_safely(tmp_path: Path) -> None:
    """方向校正改变受管尺寸，但来源字节保持不变。"""

    _library, bundle, _paths, repository, importer = _services(tmp_path)
    oriented = tmp_path / "oriented.jpg"
    exif = Image.Exif()
    exif[274] = 6
    Image.new("RGB", (40, 20), (120, 80, 40)).save(oriented, exif=exif)
    grayscale = tmp_path / "gray.png"
    Image.new("L", (30, 18), 128).save(grayscale)
    alpha = tmp_path / "alpha.png"
    Image.new("RGBA", (24, 16), (10, 20, 30, 80)).save(alpha)
    before = {path: _sha256(path) for path in (oriented, grayscale, alpha)}

    prepared = importer.preflight(
        ImageImportPreflightRequest(bundle.dataset.id, (oriented, grayscale, alpha))
    )
    report = importer.commit(prepared, {})
    samples = {
        sample.original_filename: sample
        for sample in repository.query(SampleQuery(bundle.dataset.id)).items
    }

    assert len(report.imported_sample_ids) == 3
    assert samples["oriented.jpg"].width == 20
    assert samples["oriented.jpg"].height == 40
    assert samples["gray.png"].image_mode == "L"
    assert samples["alpha.png"].image_mode == "RGBA"
    assert {path: _sha256(path) for path in before} == before


def test_unsupported_corrupt_and_multiframe_files_are_reported(tmp_path: Path) -> None:
    """无效输入逐项进入报告，不影响同批有效图片。"""

    _library, bundle, _paths, _repository, importer = _services(tmp_path)
    unsupported = tmp_path / "notes.txt"
    unsupported.write_text("not an image", encoding="utf-8")
    corrupt = tmp_path / "broken.jpg"
    corrupt.write_bytes(b"broken")
    multi = tmp_path / "multi.tiff"
    frames = [Image.new("RGB", (20, 20), color) for color in ("red", "blue")]
    frames[0].save(multi, save_all=True, append_images=frames[1:])

    prepared = importer.preflight(
        ImageImportPreflightRequest(bundle.dataset.id, (unsupported, corrupt, multi))
    )

    assert prepared.items == ()
    assert "不支持" in prepared.failures[str(unsupported)]
    assert str(corrupt) in prepared.failures
    assert "单帧" in prepared.failures[str(multi)]


def test_exact_duplicate_requires_skip_or_keep_decision(tmp_path: Path) -> None:
    """重复图片缺少决定时不能登记，保留后使用独立 UUID。"""

    _library, bundle, _paths, repository, importer = _services(tmp_path)
    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    Image.new("RGB", (32, 20), (40, 80, 120)).save(first)
    second.write_bytes(first.read_bytes())
    first_preflight = importer.preflight(ImageImportPreflightRequest(bundle.dataset.id, (first,)))
    first_report = importer.commit(first_preflight, {})

    skipped_preflight = importer.preflight(
        ImageImportPreflightRequest(bundle.dataset.id, (second,))
    )
    duplicate = skipped_preflight.items[0]
    assert duplicate.requires_duplicate_decision
    with pytest.raises(ImagePoolError, match="决定"):
        importer.commit(skipped_preflight, {})
    skipped = importer.commit(skipped_preflight, {duplicate.id: DuplicateDecision.SKIP})
    assert skipped.skipped_item_ids == [duplicate.id]
    assert repository.count_active() == 1

    kept_preflight = importer.preflight(ImageImportPreflightRequest(bundle.dataset.id, (second,)))
    kept_item = kept_preflight.items[0]
    kept = importer.commit(kept_preflight, {kept_item.id: DuplicateDecision.KEEP})

    assert repository.count_active() == 2
    assert first_report.imported_sample_ids[0] != kept.imported_sample_ids[0]
    page = repository.query(SampleQuery(bundle.dataset.id))
    hashes = {sample.content_hash for sample in page.items}
    assert len(hashes) == 1


def test_same_batch_duplicate_and_recursive_directory_scan(tmp_path: Path) -> None:
    """递归导入同批重复项时，第二项也必须人工决定。"""

    _library, bundle, _paths, repository, importer = _services(tmp_path)
    source_root = tmp_path / "sources"
    nested = source_root / "nested"
    nested.mkdir(parents=True)
    first = source_root / "a.png"
    second = nested / "b.png"
    Image.new("RGB", (22, 18), (90, 100, 110)).save(first)
    second.write_bytes(first.read_bytes())

    prepared = importer.preflight(
        ImageImportPreflightRequest(bundle.dataset.id, (source_root,), recursive=True)
    )

    assert len(prepared.items) == 2
    assert prepared.items[0].requires_duplicate_decision is False
    assert prepared.items[1].requires_duplicate_decision is True
    report = importer.commit(
        prepared,
        {prepared.items[1].id: DuplicateDecision.KEEP},
    )
    assert len(report.imported_sample_ids) == 2
    assert repository.count_active() == 2


def test_thumbnail_cache_rebuild_and_restart_recovery(tmp_path: Path) -> None:
    """缩略图删除后可重建，重新创建 Service 仍能查询图片。"""

    library, bundle, paths, repository, importer = _services(tmp_path)
    source = tmp_path / "source.png"
    Image.new("RGB", (60, 40), (20, 90, 150)).save(source)
    prepared = importer.preflight(ImageImportPreflightRequest(bundle.dataset.id, (source,)))
    report = importer.commit(prepared, {})
    sample_id = report.imported_sample_ids[0]
    thumbnail_service = ThumbnailService(paths, repository)
    first = thumbnail_service.load(sample_id)
    assert first.cache_hit is True
    sample = repository.get_sample(sample_id)
    assert sample is not None
    repository.resolve_path(sample.thumbnail_path, "cache/thumbnails").unlink()
    rebuilt = thumbnail_service.load(sample_id)
    assert rebuilt.cache_hit is False
    assert rebuilt.data.startswith(b"\x89PNG")

    restarted_library = DatasetLibraryService(library.root)
    restarted_bundle = restarted_library.open_dataset(bundle.dataset.id)
    restarted_paths = restarted_library.dataset_repository.paths(bundle.dataset.id)
    restarted_repository = DatasetSampleRepository(restarted_paths, restarted_bundle.dataset.id)
    assert restarted_repository.get_sample(sample_id) is not None


def test_near_duplicate_candidates_require_confirmation_and_survive_restart(
    tmp_path: Path,
) -> None:
    """近似图只形成待确认候选，确认与忽略状态均由人工命令决定。"""

    _library, bundle, paths, repository, importer = _services(tmp_path)
    first = tmp_path / "near-a.png"
    second = tmp_path / "near-b.png"
    base = Image.new("RGB", (96, 64))
    for x in range(base.width):
        for y in range(base.height):
            base.putpixel((x, y), (x * 2, y * 3, 90))
    base.save(first)
    changed = base.copy()
    changed.putpixel((40, 30), (250, 250, 250))
    changed.save(second)

    first_preflight = importer.preflight(ImageImportPreflightRequest(bundle.dataset.id, (first,)))
    importer.commit(first_preflight, {})
    second_preflight = importer.preflight(ImageImportPreflightRequest(bundle.dataset.id, (second,)))
    report = importer.commit(second_preflight, {})

    assert report.similar_sample_ids
    groups = repository.list_similarity_groups()
    assert len(groups) == 1
    assert groups[0].status == SimilarityStatus.PENDING
    repository.set_similarity_status(
        groups[0].id, SimilarityStatus.CONFIRMED, utc_now().isoformat()
    )
    restarted = DatasetSampleRepository(paths, bundle.dataset.id)
    assert restarted.list_similarity_groups()[0].status == SimilarityStatus.CONFIRMED
    assert len(restarted.confirmed_similarity_mapping()) == 2
    restarted.set_similarity_status(groups[0].id, SimilarityStatus.IGNORED, utc_now().isoformat())
    assert restarted.confirmed_similarity_mapping() == {}
