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


def test_missing_library_index_rebuilds_valid_dataset_registration(tmp_path: Path) -> None:
    """library.json 丢失时从有效 UUID 数据集目录恢复主页登记。"""

    service = DatasetLibraryService(tmp_path)
    created = service.create_dataset("断电后恢复")
    (tmp_path / "library.json").unlink()

    restarted = DatasetLibraryService(tmp_path)

    assert [item.id for item in restarted.library.datasets] == [created.dataset.id]
    assert restarted.open_dataset(created.dataset.id).dataset.name == "断电后恢复"
    assert restarted.recovery_report.recovered_dataset_ids == (created.dataset.id,)


def test_unregistered_published_directory_is_reconciled_on_restart(tmp_path: Path) -> None:
    """发布目录后若登记被中断，下次启动应重新登记而不是隐藏数据。"""

    service = DatasetLibraryService(tmp_path)
    created = service.create_dataset("发布后中断")
    empty_library = service.library.model_copy(deep=True)
    empty_library.datasets.clear()
    service.library_repository.save(empty_library)

    restarted = DatasetLibraryService(tmp_path)

    assert restarted.library.datasets[0].id == created.dataset.id
    assert restarted.recovery_report.recovered_dataset_ids == (created.dataset.id,)


def test_damaged_unregistered_directory_becomes_diagnostic_record(tmp_path: Path) -> None:
    """损坏孤儿目录必须保留并显示诊断卡片，不能被删除或静默忽略。"""

    service = DatasetLibraryService(tmp_path)
    created = service.create_dataset("损坏孤儿")
    empty_library = service.library.model_copy(deep=True)
    empty_library.datasets.clear()
    service.library_repository.save(empty_library)
    paths = service.dataset_repository.paths(created.dataset.id)
    original = b"{broken-dataset"
    paths.metadata.write_bytes(original)

    restarted = DatasetLibraryService(tmp_path)
    record = restarted.list_datasets()[0]

    assert record.entry.id == created.dataset.id
    assert record.bundle is None
    assert record.diagnostic
    assert paths.metadata.read_bytes() == original
    assert restarted.recovery_report.damaged_dataset_ids == (created.dataset.id,)


def test_damaged_orphan_uses_valid_metadata_for_home_summary(tmp_path: Path) -> None:
    """标签或索引损坏时仍以有效 dataset.json 恢复真实名称和描述。"""

    service = DatasetLibraryService(tmp_path)
    created = service.create_dataset("可识别的损坏项", "保留真实摘要")
    empty_library = service.library.model_copy(deep=True)
    empty_library.datasets.clear()
    service.library_repository.save(empty_library)
    paths = service.dataset_repository.paths(created.dataset.id)
    paths.index.write_bytes(b"not-a-sqlite-database")

    restarted = DatasetLibraryService(tmp_path)
    record = restarted.list_datasets()[0]

    assert record.entry.name == "可识别的损坏项"
    assert record.entry.description == "保留真实摘要"
    assert record.bundle is None
    assert record.diagnostic


def test_registered_damaged_dataset_refreshes_summary_from_valid_metadata(tmp_path: Path) -> None:
    """已登记目录的索引损坏时，过期摘要仍应由有效 dataset.json 修正。"""

    service = DatasetLibraryService(tmp_path)
    created = service.create_dataset("损坏但可识别", "真实诊断摘要")
    stale = service.library.model_copy(deep=True)
    stale.datasets[0].name = "错误旧名称"
    stale.datasets[0].description = "错误旧描述"
    service.library_repository.save(stale)
    service.dataset_repository.paths(created.dataset.id).index.write_bytes(b"broken-index")

    restarted = DatasetLibraryService(tmp_path)
    record = restarted.list_datasets()[0]

    assert record.entry.name == "损坏但可识别"
    assert record.entry.description == "真实诊断摘要"
    assert record.bundle is None
    assert restarted.recovery_report.refreshed_dataset_ids == (created.dataset.id,)


def test_reconciliation_refreshes_stale_library_summary(tmp_path: Path) -> None:
    """dataset.json 是摘要事实来源，启动对账应原子刷新过期主页索引。"""

    service = DatasetLibraryService(tmp_path)
    created = service.create_dataset("真实名称", "真实描述")
    stale = service.library.model_copy(deep=True)
    stale.datasets[0].name = "过期摘要"
    stale.datasets[0].description = "错误描述"
    service.library_repository.save(stale)

    restarted = DatasetLibraryService(tmp_path)

    assert restarted.library.datasets[0].name == "真实名称"
    assert restarted.library.datasets[0].description == "真实描述"
    assert restarted.recovery_report.refreshed_dataset_ids == (created.dataset.id,)


def test_reconciliation_reports_unknown_directory_without_modifying_it(tmp_path: Path) -> None:
    """非 UUID 目录只进入恢复报告，不删除、不移动也不登记。"""

    DatasetLibraryService(tmp_path)
    unknown = tmp_path / "datasets" / "manual-folder"
    unknown.mkdir()
    marker = unknown / "keep.txt"
    marker.write_text("必须保留", encoding="utf-8")

    restarted = DatasetLibraryService(tmp_path)

    assert restarted.library.datasets == []
    assert marker.read_text(encoding="utf-8") == "必须保留"
    assert "manual-folder" in restarted.recovery_report.ignored_entries


def test_dataset_metadata_write_failure_is_wrapped_by_service(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """底层数据集写入失败只能以业务异常离开 Service。"""

    service = DatasetLibraryService(tmp_path)
    created = service.create_dataset("写盘失败")

    def fail_save(_dataset) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(service.dataset_repository, "save_dataset", fail_save)
    with pytest.raises(DatasetLibraryServiceError, match="元数据写入失败"):
        service.rename_dataset(created.dataset.id, "不会生效")
    assert service.library.datasets[0].name == "写盘失败"


def test_registration_and_recovery_failure_preserves_both_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """登记与恢复同时失败时不得用清理异常覆盖原始写盘原因。"""

    service = DatasetLibraryService(tmp_path)

    def fail_library_save(_library) -> None:
        raise OSError("library disk full")

    def fail_recovery(_dataset_id: str) -> None:
        raise OSError("recovery locked")

    monkeypatch.setattr(service.library_repository, "save", fail_library_save)
    monkeypatch.setattr(service.dataset_repository, "move_unregistered_to_recovery", fail_recovery)

    with pytest.raises(DatasetLibraryServiceError) as captured:
        service.create_dataset("保留现场")

    message = str(captured.value)
    assert "library disk full" in message
    assert "recovery locked" in message
    assert any(path.is_dir() for path in (tmp_path / "datasets").iterdir())


def test_initial_dataset_write_failure_leaves_no_registered_or_staging_data(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """首次写入失败应保持空索引，并清理本轮已经建立的临时目录。"""

    service = DatasetLibraryService(tmp_path)
    original = service.dataset_repository.create_in_staging

    def fail_after_creating(staging, dataset, label_set) -> None:
        original(staging, dataset, label_set)
        raise OSError("metadata flush failed")

    monkeypatch.setattr(service.dataset_repository, "create_in_staging", fail_after_creating)

    with pytest.raises(DatasetLibraryServiceError, match="metadata flush failed"):
        service.create_dataset("首次写入失败")

    assert service.library.datasets == []
    assert list((tmp_path / "datasets").iterdir()) == []


def test_metadata_rollback_failure_is_explicit_and_reconciles_after_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """摘要写入与元数据回滚同时失败时保留现场，并可由下次启动对账。"""

    service = DatasetLibraryService(tmp_path)
    created = service.create_dataset("回滚前名称")
    real_save_dataset = service.dataset_repository.save_dataset
    calls = 0

    def fail_second_dataset_save(dataset) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("rollback locked")
        real_save_dataset(dataset)

    def fail_library_save(_library) -> None:
        raise OSError("library locked")

    monkeypatch.setattr(service.dataset_repository, "save_dataset", fail_second_dataset_save)
    monkeypatch.setattr(service.library_repository, "save", fail_library_save)

    with pytest.raises(DatasetLibraryServiceError) as captured:
        service.rename_dataset(created.dataset.id, "磁盘现场名称")

    assert "library locked" in str(captured.value)
    assert "rollback locked" in str(captured.value)
    monkeypatch.undo()
    restarted = DatasetLibraryService(tmp_path)
    assert restarted.library.datasets[0].name == "磁盘现场名称"
    assert restarted.recovery_report.refreshed_dataset_ids == (created.dataset.id,)


def test_uuid_symlink_is_reported_without_being_followed_or_registered(tmp_path: Path) -> None:
    """UUID 名称的符号链接仍属于未知入口，启动对账不得跟随。"""

    DatasetLibraryService(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    marker = outside / "keep.txt"
    marker.write_text("外部内容", encoding="utf-8")
    link_name = "8cb9ff2f-c6f1-4e7c-8ce4-5fcc59b9cdfa"
    link = tmp_path / "datasets" / link_name
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"当前 Windows 环境不允许创建测试符号链接: {error}")

    restarted = DatasetLibraryService(tmp_path)

    assert restarted.library.datasets == []
    assert link_name in restarted.recovery_report.ignored_entries
    assert marker.read_text(encoding="utf-8") == "外部内容"
