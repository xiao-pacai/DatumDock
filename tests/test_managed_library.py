"""步骤二内部受管数据集资料库的事务与安全回归。"""

from __future__ import annotations

from pathlib import Path

import pytest

from datumdock.domain.models import Label, ManagedDatasetConfiguration, NamingPolicy
from datumdock.services.dataset_library import (
    DatasetLibraryService,
    DatasetLibraryServiceError,
    DuplicateDatasetNameError,
    InvalidDatasetNameError,
)
from datumdock.services.library_repository import (
    CorruptLibraryError,
    DatasetLibraryRepository,
    DatasetRepositoryError,
    resolve_data_root,
)


def test_first_start_creates_empty_internal_library(tmp_path: Path) -> None:
    """首次启动只建立固定内部结构，不要求用户选择工作区。"""

    service = DatasetLibraryService(tmp_path)

    assert service.library.datasets == []
    assert (tmp_path / "library.json").is_file()
    assert (tmp_path / "datasets").is_dir()
    assert (tmp_path / "recovery").is_dir()


def test_environment_override_is_exact_development_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """开发覆盖目录直接作为资料库根，普通用户无需配置该变量。"""

    monkeypatch.setenv("DATUMDOCK_DATA_DIR", str(tmp_path))
    assert resolve_data_root() == tmp_path
    monkeypatch.setenv("DATUMDOCK_DATA_DIR", "relative-library")
    with pytest.raises(Exception, match="绝对路径"):
        resolve_data_root()


def test_create_and_restart_restore_dataset(tmp_path: Path) -> None:
    """空数据集创建后拥有完整结构，并能由新服务实例恢复。"""

    service = DatasetLibraryService(tmp_path)
    created = service.create_dataset("  工厂零件  ", "零件检测")
    paths = service.dataset_repository.paths(created.dataset.id)

    assert created.dataset.name == "工厂零件"
    assert paths.root.name == created.dataset.id
    for path in (
        paths.metadata,
        paths.label_set,
        paths.index,
        paths.images,
        paths.annotations,
        paths.thumbnails,
        paths.models,
        paths.trash,
    ):
        assert path.exists()

    restarted = DatasetLibraryService(tmp_path)
    reopened = restarted.open_dataset(created.dataset.id)
    assert reopened.dataset.name == "工厂零件"
    assert reopened.dataset.statistics.image_count == 0


@pytest.mark.parametrize(
    "name",
    ("", "   ", "../escape", r"C:\outside", "bad/name", "CON", "ends."),
)
def test_invalid_dataset_names_never_become_paths(tmp_path: Path, name: str) -> None:
    """非法显示名称在创建任何 UUID 目录前即被拒绝。"""

    service = DatasetLibraryService(tmp_path)
    with pytest.raises(InvalidDatasetNameError):
        service.create_dataset(name)
    assert list((tmp_path / "datasets").iterdir()) == []


def test_active_names_are_trimmed_case_insensitive_unique(tmp_path: Path) -> None:
    """活动数据集名称去除首尾空格后按大小写不敏感方式判重。"""

    service = DatasetLibraryService(tmp_path)
    service.create_dataset("Factory")
    with pytest.raises(DuplicateDatasetNameError):
        service.create_dataset("  factory  ")


def test_rename_archive_restore_keep_uuid_directory(tmp_path: Path) -> None:
    """重命名和归档只改元数据，受管 UUID 目录始终稳定。"""

    service = DatasetLibraryService(tmp_path)
    created = service.create_dataset("旧名称")
    original_directory = service.dataset_directory(created.dataset.id)

    renamed = service.rename_dataset(created.dataset.id, "新名称")
    archived = service.archive_dataset(created.dataset.id)
    assert renamed.dataset.name == "新名称"
    assert archived.dataset.archived is True
    assert original_directory == service.dataset_directory(created.dataset.id)
    assert original_directory.is_dir()
    assert all(path.exists() for path in original_directory.iterdir())

    restored = service.restore_dataset(created.dataset.id)
    assert restored.dataset.archived is False
    assert service.open_dataset(created.dataset.id).dataset.name == "新名称"


def test_restore_blocks_new_active_name_collision(tmp_path: Path) -> None:
    """归档名称可以被新存档使用，但恢复时必须重新通过活动名称校验。"""

    service = DatasetLibraryService(tmp_path)
    archived = service.create_dataset("共享名称")
    service.archive_dataset(archived.dataset.id)
    service.create_dataset("共享名称")

    with pytest.raises(DuplicateDatasetNameError):
        service.restore_dataset(archived.dataset.id)


def test_template_copies_only_independent_configuration(tmp_path: Path) -> None:
    """模板保留标签稳定映射和配置，但不复制任何样本或模型内容。"""

    service = DatasetLibraryService(tmp_path)
    source = service.create_dataset("源数据集")
    label_set = source.label_set.model_copy(deep=True)
    label_set.labels.append(
        Label(
            class_id=0,
            name="metal_part",
            alias="金属零件",
            description="主要检测物",
            synonyms=["零件"],
            color="#73B9D2",
        )
    )
    service.update_label_set(source.dataset.id, label_set)
    source_configuration = ManagedDatasetConfiguration(
        naming_policy=NamingPolicy(prefix="part", start_index=8, padding=5),
        default_split=(70, 20, 10),
    )
    service.update_configuration(source.dataset.id, source_configuration)
    source_paths = service.dataset_repository.paths(source.dataset.id)
    (source_paths.images / "source.png").write_bytes(b"image")
    (source_paths.annotations / "source.json").write_text("{}", encoding="utf-8")
    (source_paths.models / "model.pt").write_bytes(b"model")
    (source_paths.trash / "deleted.bin").write_bytes(b"trash")
    (source_paths.thumbnails / "source.png").write_bytes(b"thumb")

    target = service.create_dataset(
        "目标数据集",
        source_dataset_id=source.dataset.id,
    )
    target_paths = service.dataset_repository.paths(target.dataset.id)

    assert target.label_set.id == label_set.id
    assert target.label_set.labels[0].id == label_set.labels[0].id
    assert target.dataset.configuration == source_configuration
    for path in (
        target_paths.images,
        target_paths.annotations,
        target_paths.models,
        target_paths.trash,
        target_paths.thumbnails,
    ):
        assert list(path.iterdir()) == []

    changed = target.dataset.configuration.model_copy(deep=True)
    changed.naming_policy.prefix = "target"
    service.update_configuration(target.dataset.id, changed)
    assert service.open_dataset(source.dataset.id).dataset.configuration.naming_policy.prefix == (
        "part"
    )


def test_two_datasets_keep_independent_metadata(tmp_path: Path) -> None:
    """快速打开两个数据集时名称、标签和统计不会串联。"""

    service = DatasetLibraryService(tmp_path)
    first = service.create_dataset("数据集一")
    second = service.create_dataset("数据集二")

    assert service.open_dataset(first.dataset.id).dataset.name == "数据集一"
    assert service.open_dataset(second.dataset.id).dataset.name == "数据集二"
    assert service.dataset_directory(first.dataset.id) != service.dataset_directory(
        second.dataset.id
    )


def test_corrupt_library_is_never_overwritten(tmp_path: Path) -> None:
    """资料库索引损坏时初始化失败，原始诊断字节保持不变。"""

    DatasetLibraryService(tmp_path)
    path = tmp_path / "library.json"
    corrupt = b'{"datasets": [broken'
    path.write_bytes(corrupt)

    with pytest.raises(CorruptLibraryError):
        DatasetLibraryService(tmp_path)
    assert path.read_bytes() == corrupt


def test_single_corrupt_dataset_does_not_hide_healthy_dataset(tmp_path: Path) -> None:
    """单个 dataset.json 损坏只产生一张诊断卡片。"""

    service = DatasetLibraryService(tmp_path)
    damaged = service.create_dataset("损坏项")
    healthy = service.create_dataset("健康项")
    service.dataset_repository.paths(damaged.dataset.id).metadata.write_text(
        "{broken",
        encoding="utf-8",
    )

    records = {record.entry.id: record for record in service.list_datasets()}
    assert records[damaged.dataset.id].healthy is False
    assert records[damaged.dataset.id].diagnostic
    assert records[healthy.dataset.id].healthy is True


def test_registration_failure_leaves_no_half_registered_dataset(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """资料库登记失败时活动目录移入恢复区，索引不出现半成品。"""

    service = DatasetLibraryService(tmp_path)

    def fail_save(library) -> None:
        raise OSError("模拟索引写入失败")

    monkeypatch.setattr(service.library_repository, "save", fail_save)
    with pytest.raises(DatasetLibraryServiceError):
        service.create_dataset("不会半登记")

    assert service.library.datasets == []
    active_directories = [
        path for path in (tmp_path / "datasets").iterdir() if not path.name.startswith(".")
    ]
    assert active_directories == []
    assert any(path.name.startswith("unregistered-") for path in (tmp_path / "recovery").iterdir())


def test_repository_rejects_non_uuid_dataset_paths(tmp_path: Path) -> None:
    """Repository 不接受相对路径、绝对路径或任意字符串作为数据集 ID。"""

    repository = DatasetLibraryRepository(tmp_path)
    repository.initialize()
    service = DatasetLibraryService(tmp_path)
    for identifier in ("../outside", "dataset", "C:/outside"):
        with pytest.raises(DatasetRepositoryError):
            service.dataset_repository.paths(identifier)
