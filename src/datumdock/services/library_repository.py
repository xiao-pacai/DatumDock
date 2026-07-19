"""内部资料库与单个受管数据集的安全文件仓库。"""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

from pydantic import ValidationError

from datumdock.domain.models import AppLibrary, LabelSet, ManagedDataset
from datumdock.services.storage import (
    ProjectIndexRepository,
    read_json_model,
    write_json_atomic,
    write_json_model_atomic_verified,
)


class LibraryRepositoryError(RuntimeError):
    """内部资料库文件无法安全读取或写入。"""


class CorruptLibraryError(LibraryRepositoryError):
    """资料库索引损坏，原文件必须保持不变。"""


class DatasetRepositoryError(RuntimeError):
    """单个受管数据集缺失、损坏或越过安全边界。"""


class CorruptDatasetError(DatasetRepositoryError):
    """单个数据集结构或元数据无法验证。"""


@dataclass(frozen=True, slots=True)
class DatasetPaths:
    """集中暴露一个数据集的固定内部路径，UI 不直接使用此对象。"""

    root: Path
    metadata: Path
    label_set: Path
    index: Path
    images: Path
    annotations: Path
    thumbnails: Path
    models: Path
    trash: Path


def resolve_data_root() -> Path:
    """解析 Windows 内部数据目录；环境变量只用于开发和测试覆盖。"""

    override = os.environ.get("DATUMDOCK_DATA_DIR")
    if override:
        root = Path(override)
        if not root.is_absolute():
            raise LibraryRepositoryError("DATUMDOCK_DATA_DIR 必须是绝对路径")
        return root
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "DatumDock"
    return Path.home() / "AppData" / "Local" / "DatumDock"


def _canonical_uuid(value: str) -> str:
    """校验数据集 ID，防止路径分隔符或相对路径进入目录拼接。"""

    try:
        canonical = str(UUID(value))
    except (TypeError, ValueError) as error:
        raise DatasetRepositoryError("数据集 ID 不是有效 UUID") from error
    if value != canonical:
        raise DatasetRepositoryError("数据集 ID 必须使用规范 UUID 格式")
    return canonical


def _reject_symlink(path: Path, description: str) -> None:
    """受管结构不跟随符号链接，避免操作越过资料库根目录。"""

    if path.is_symlink():
        raise DatasetRepositoryError(f"{description}不能是符号链接")


class DatasetLibraryRepository:
    """以原子 JSON 维护软件内部资料库索引。"""

    FILE_NAME = "library.json"

    def __init__(self, root: Path) -> None:
        self.root = root
        self.path = root / self.FILE_NAME
        self.datasets_root = root / "datasets"
        self.recovery_root = root / "recovery"

    def initialize(self) -> AppLibrary:
        """首次启动创建空索引，已有索引只读取且绝不猜测覆盖。"""

        try:
            self._ensure_roots()
            if self.path.exists():
                return self.load()
            library = AppLibrary()
            self.save(library)
            return library
        except LibraryRepositoryError:
            raise
        except (OSError, DatasetRepositoryError) as error:
            raise LibraryRepositoryError(f"资料库初始化失败: {error}") from error

    def load(self) -> AppLibrary:
        """读取资料库索引；损坏时保留原始字节供诊断和恢复。"""

        _reject_symlink(self.path, "资料库索引")
        try:
            return read_json_model(self.path, AppLibrary)
        except (OSError, json.JSONDecodeError, ValidationError) as error:
            raise CorruptLibraryError(f"资料库索引无法读取: {error}") from error

    def save(self, library: AppLibrary) -> None:
        """完整验证后使用同目录临时文件原子替换资料库索引。"""

        try:
            self._ensure_roots()
            validated = AppLibrary.model_validate(library.model_dump(mode="json"))
            write_json_atomic(self.path, validated)
        except (OSError, ValidationError, DatasetRepositoryError) as error:
            raise LibraryRepositoryError(f"资料库索引写入失败: {error}") from error

    def _ensure_roots(self) -> None:
        """创建固定内部目录，并拒绝资料库关键目录上的符号链接。"""

        if self.root.exists():
            _reject_symlink(self.root, "资料库根目录")
        self.root.mkdir(parents=True, exist_ok=True)
        for path, description in (
            (self.datasets_root, "数据集目录"),
            (self.recovery_root, "恢复目录"),
        ):
            if path.exists():
                _reject_symlink(path, description)
            path.mkdir(exist_ok=True)


class DatasetRepository:
    """创建、读取、验证和保存单个 UUID 数据集目录。"""

    REQUIRED_DIRECTORIES = (
        "pool/images",
        "pool/annotations",
        "cache/thumbnails",
        "models",
        "trash",
    )

    def __init__(self, root: Path) -> None:
        self.root = root
        self.datasets_root = root / "datasets"
        self.recovery_root = root / "recovery"

    def paths(self, dataset_id: str) -> DatasetPaths:
        """只按规范 UUID 生成固定路径，并验证最终位置仍在资料库内。"""

        identifier = _canonical_uuid(dataset_id)
        _reject_symlink(self.datasets_root, "数据集目录")
        dataset_root = self.datasets_root / identifier
        resolved_parent = self.datasets_root.resolve(strict=False)
        resolved_candidate = dataset_root.resolve(strict=False)
        try:
            resolved_candidate.relative_to(resolved_parent)
        except ValueError as error:
            raise DatasetRepositoryError("数据集路径越过内部资料库边界") from error
        return self._paths_for_root(dataset_root)

    def staging_path(self, dataset_id: str) -> Path:
        """生成同卷临时目录，使完成后的目录替换保持原子性。"""

        identifier = _canonical_uuid(dataset_id)
        return self.datasets_root / f".creating-{identifier}-{uuid4()}"

    def create_in_staging(
        self,
        staging_root: Path,
        dataset: ManagedDataset,
        label_set: LabelSet,
    ) -> None:
        """在未登记临时目录中写入完整空数据集并执行结构验证。"""

        try:
            validated_dataset = ManagedDataset.model_validate(dataset.model_dump(mode="json"))
            validated_label_set = LabelSet.model_validate(label_set.model_dump(mode="json"))
            if staging_root.parent != self.datasets_root or not staging_root.name.startswith(
                f".creating-{validated_dataset.id}-"
            ):
                raise DatasetRepositoryError("临时数据集目录不在受管创建区域")
            if staging_root.exists():
                raise DatasetRepositoryError("临时数据集目录已存在")
            staging_root.mkdir(parents=True)
            paths = self._paths_for_root(staging_root)
            for relative in self.REQUIRED_DIRECTORIES:
                (staging_root / relative).mkdir(parents=True, exist_ok=False)
            write_json_atomic(paths.metadata, validated_dataset)
            write_json_atomic(paths.label_set, validated_label_set)
            ProjectIndexRepository(paths.index)
            self.validate_at(staging_root, expected_id=validated_dataset.id)
        except DatasetRepositoryError:
            raise
        except ValidationError as error:
            raise DatasetRepositoryError(f"数据集创建前验证失败: {error}") from error
        except OSError as error:
            raise DatasetRepositoryError(f"数据集临时目录写入失败: {error}") from error

    def publish_staging(self, staging_root: Path, dataset_id: str) -> Path:
        """把已验证临时目录一次移动为稳定 UUID 目录。"""

        final_root = self.paths(dataset_id).root
        if final_root.exists():
            raise DatasetRepositoryError("目标数据集 UUID 目录已存在")
        self.validate_at(staging_root, expected_id=dataset_id)
        try:
            os.replace(staging_root, final_root)
        except OSError as error:
            raise DatasetRepositoryError(f"数据集目录发布失败: {error}") from error
        return final_root

    def move_unregistered_to_recovery(self, dataset_id: str) -> Path | None:
        """登记失败时保留完整数据集，避免留下半登记活动存档。"""

        final_root = self.paths(dataset_id).root
        if not final_root.exists():
            return None
        recovery = self.recovery_root / f"unregistered-{dataset_id}-{uuid4()}"
        try:
            os.replace(final_root, recovery)
        except OSError as error:
            raise DatasetRepositoryError(f"未登记数据集移入恢复区失败: {error}") from error
        return recovery

    def remove_staging(self, staging_root: Path) -> None:
        """仅清理本次创建的临时目录，不接受任意调用方路径。"""

        if staging_root.parent != self.datasets_root or not staging_root.name.startswith(
            ".creating-"
        ):
            raise DatasetRepositoryError("拒绝清理非受管临时目录")
        if not staging_root.exists():
            return
        _reject_symlink(staging_root, "临时数据集目录")
        try:
            children = sorted(
                staging_root.rglob("*"),
                key=lambda item: len(item.parts),
                reverse=True,
            )
            for child in children:
                _reject_symlink(child, "临时数据集内容")
                if child.is_file():
                    child.unlink()
                elif child.is_dir():
                    child.rmdir()
            staging_root.rmdir()
        except DatasetRepositoryError:
            raise
        except OSError as error:
            raise DatasetRepositoryError(f"临时数据集清理失败: {error}") from error

    def load(self, dataset_id: str) -> tuple[ManagedDataset, LabelSet]:
        """读取并验证数据集元数据、标签集、目录和 SQLite 索引。"""

        paths = self.paths(dataset_id)
        self.validate_at(paths.root, expected_id=dataset_id)
        try:
            dataset = read_json_model(paths.metadata, ManagedDataset)
            label_set = read_json_model(paths.label_set, LabelSet)
        except (OSError, json.JSONDecodeError, ValidationError) as error:
            raise CorruptDatasetError(f"数据集元数据无法读取: {error}") from error
        if dataset.label_set_id != label_set.id:
            raise CorruptDatasetError("dataset.json 与 label-set.json 的稳定 ID 不一致")
        return dataset, label_set

    def load_metadata_for_recovery(self, dataset_id: str) -> ManagedDataset:
        """只读取可验证的 dataset.json，用于损坏目录的主页摘要恢复。"""

        paths = self.paths(dataset_id)
        if not paths.root.is_dir():
            raise CorruptDatasetError("数据集目录缺失")
        _reject_symlink(paths.root, "数据集目录")
        if not paths.metadata.is_file():
            raise CorruptDatasetError("数据集元数据缺失")
        _reject_symlink(paths.metadata, "数据集元数据")
        try:
            dataset = read_json_model(paths.metadata, ManagedDataset)
        except (OSError, json.JSONDecodeError, ValidationError) as error:
            raise CorruptDatasetError(f"数据集元数据无法读取: {error}") from error
        if dataset.id != dataset_id:
            raise CorruptDatasetError("数据集元数据 UUID 与受管目录不一致")
        return dataset

    def save_dataset(self, dataset: ManagedDataset) -> None:
        """只更新 UUID 目录内的元数据，不修改目录名称。"""

        try:
            validated = ManagedDataset.model_validate(dataset.model_dump(mode="json"))
        except ValidationError as error:
            raise DatasetRepositoryError(f"数据集元数据验证失败: {error}") from error
        paths = self.paths(validated.id)
        if not paths.root.is_dir():
            raise DatasetRepositoryError("数据集目录不存在")
        try:
            write_json_atomic(paths.metadata, validated)
        except OSError as error:
            raise DatasetRepositoryError(f"数据集元数据写入失败: {error}") from error

    def save_label_set(self, dataset_id: str, label_set: LabelSet) -> None:
        """保存数据集独立标签定义，稳定标签集 ID 由调用方校验。"""

        try:
            validated = LabelSet.model_validate(label_set.model_dump(mode="json"))
        except ValidationError as error:
            raise DatasetRepositoryError(f"标签集验证失败: {error}") from error
        paths = self.paths(dataset_id)
        if not paths.root.is_dir():
            raise DatasetRepositoryError("数据集目录不存在")
        try:
            write_json_model_atomic_verified(paths.label_set, validated, LabelSet)
        except OSError as error:
            raise DatasetRepositoryError(f"标签集写入失败: {error}") from error

    def discover_managed_directories(self) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """发现规范 UUID 目录；未知项与符号链接只报告且绝不跟随。"""

        discovered: list[str] = []
        ignored: list[str] = []
        try:
            entries = sorted(self.datasets_root.iterdir(), key=lambda item: item.name.casefold())
        except OSError as error:
            raise DatasetRepositoryError(f"无法扫描受管数据集目录: {error}") from error
        for entry in entries:
            if entry.is_symlink() or not entry.is_dir():
                ignored.append(entry.name)
                continue
            try:
                discovered.append(_canonical_uuid(entry.name))
            except DatasetRepositoryError:
                ignored.append(entry.name)
        return tuple(discovered), tuple(ignored)

    def validate_at(self, dataset_root: Path, *, expected_id: str) -> None:
        """验证固定结构、稳定 ID 和 SQLite 可读性，不扫描任何图片。"""

        _canonical_uuid(expected_id)
        if not dataset_root.is_dir():
            raise CorruptDatasetError("数据集目录缺失")
        _reject_symlink(dataset_root, "数据集目录")
        paths = self._paths_for_root(dataset_root)
        required_paths = [
            paths.metadata,
            paths.label_set,
            paths.index,
            paths.images,
            paths.annotations,
            paths.thumbnails,
            paths.models,
            paths.trash,
        ]
        for path in required_paths:
            if not path.exists():
                raise CorruptDatasetError(f"数据集结构缺失: {path.relative_to(dataset_root)}")
            _reject_symlink(path, "数据集结构")
        try:
            dataset = read_json_model(paths.metadata, ManagedDataset)
            label_set = read_json_model(paths.label_set, LabelSet)
        except (OSError, json.JSONDecodeError, ValidationError) as error:
            raise CorruptDatasetError(f"数据集元数据无法读取: {error}") from error
        if dataset.id != expected_id:
            raise CorruptDatasetError("数据集元数据 UUID 与受管目录不一致")
        if dataset.label_set_id != label_set.id:
            raise CorruptDatasetError("标签集引用与标签文件不一致")
        try:
            connection = sqlite3.connect(f"file:{paths.index.as_posix()}?mode=ro", uri=True)
            connection.execute("PRAGMA schema_version").fetchone()
        except sqlite3.Error as error:
            raise CorruptDatasetError(f"数据集索引无法读取: {error}") from error
        finally:
            if "connection" in locals():
                connection.close()

    @staticmethod
    def _paths_for_root(dataset_root: Path) -> DatasetPaths:
        return DatasetPaths(
            root=dataset_root,
            metadata=dataset_root / "dataset.json",
            label_set=dataset_root / "label-set.json",
            index=dataset_root / "index.sqlite",
            images=dataset_root / "pool" / "images",
            annotations=dataset_root / "pool" / "annotations",
            thumbnails=dataset_root / "cache" / "thumbnails",
            models=dataset_root / "models",
            trash=dataset_root / "trash",
        )
