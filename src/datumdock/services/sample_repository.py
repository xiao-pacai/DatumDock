"""受管数据集图片的 SQLite v3 索引、分页查询与标注摘要。"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import UUID

from datumdock.domain.models import (
    AnnotationState,
    DatasetSample,
    ReviewStatus,
    SampleHealth,
    SampleSort,
    SimilarityStatus,
    ThumbnailState,
    new_id,
)
from datumdock.services.library_repository import DatasetPaths


class SampleRepositoryError(RuntimeError):
    """样本索引无法安全迁移、查询或提交。"""


@dataclass(frozen=True, slots=True)
class SampleQuery:
    """分页条件由 UI 传入，但具体 SQL 始终由 Repository 生成。"""

    dataset_id: str
    offset: int = 0
    limit: int = 200
    search: str = ""
    review_status: ReviewStatus | None = None
    label_id: str | None = None
    sort: SampleSort = SampleSort.FILENAME_ASC
    include_trashed: bool = False
    annotation_state: AnnotationState | None = None
    has_annotations: bool | None = None


@dataclass(frozen=True, slots=True)
class SamplePage:
    """一次分页结果不持有数据库连接，也不包含原图数据。"""

    items: tuple[DatasetSample, ...]
    total: int
    offset: int
    limit: int


@dataclass(frozen=True, slots=True)
class SimilarityGroup:
    """近似组只保存稳定样本 ID，缩略图由 UI 按需请求。"""

    id: str
    status: SimilarityStatus
    score: float
    sample_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TrashItem:
    """回收站索引中的恢复信息，真实文件仍受数据集路径边界保护。"""

    sample: DatasetSample
    trashed_at: str
    manifest_path: str
    display_filename: str = ""


@dataclass(frozen=True, slots=True)
class ManagedOperation:
    """跨文件系统与 SQLite 的操作日志，用于启动恢复。"""

    id: str
    dataset_id: str
    operation_type: str
    phase: str
    payload: dict[str, Any]
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class SampleReconciliationReport:
    """启动对账只报告或标记异常，不猜测删除未登记文件。"""

    missing_sample_ids: tuple[str, ...] = ()
    corrupt_sample_ids: tuple[str, ...] = ()
    untracked_pngs: tuple[str, ...] = ()
    pending_operation_ids: tuple[str, ...] = ()
    temporary_files: tuple[str, ...] = ()
    migration_issues: tuple[str, ...] = ()


class DatasetSampleRepository:
    """单个受管数据集的图片查询事实来源。"""

    SCHEMA_VERSION = 3

    def __init__(self, paths: DatasetPaths, dataset_id: str) -> None:
        self.paths = paths
        self.dataset_id = _canonical_uuid(dataset_id)
        if paths.root.name != self.dataset_id:
            raise SampleRepositoryError("样本索引 UUID 与数据集目录不一致")
        self._validate_database_path()
        self._initialize()

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.paths.index, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _validate_database_path(self) -> None:
        """拒绝索引或数据集根目录上的符号链接，避免 SQLite 越界写入。"""

        for path, label in ((self.paths.root, "数据集目录"), (self.paths.index, "样本索引")):
            if path.is_symlink():
                raise SampleRepositoryError(f"{label}不能是符号链接")
        try:
            self.paths.index.resolve(strict=False).relative_to(
                self.paths.root.resolve(strict=False)
            )
        except ValueError as error:
            raise SampleRepositoryError("样本索引越过数据集目录") from error

    def _initialize(self) -> None:
        """逐级升级索引；任一步失败都由 SQLite 完整回滚。"""

        connection = sqlite3.connect(self.paths.index, timeout=30)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA foreign_keys = OFF")
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if version > self.SCHEMA_VERSION:
                raise SampleRepositoryError("数据集索引版本高于当前应用支持范围")
            if version == self.SCHEMA_VERSION:
                self._validate_v3_schema(connection)
                violations = connection.execute("PRAGMA foreign_key_check").fetchall()
                if violations:
                    raise SampleRepositoryError("数据集索引外键校验失败")
                return
            connection.execute("BEGIN IMMEDIATE")
            if version == 0:
                self._migrate_v0(connection)
                self._create_v1_schema(connection)
                self._migrate_v1_to_v2(connection)
                self._migrate_v2_to_v3(connection)
            elif version == 1:
                self._validate_v1_schema(connection)
                self._migrate_v1_to_v2(connection)
                self._migrate_v2_to_v3(connection)
            elif version == 2:
                self._validate_v2_schema(connection)
                self._migrate_v2_to_v3(connection)
            connection.execute("PRAGMA user_version = 3")
            violations = connection.execute("PRAGMA foreign_key_check").fetchall()
            if violations:
                raise SampleRepositoryError("数据集索引外键校验失败")
            connection.commit()
        except SampleRepositoryError:
            connection.rollback()
            raise
        except sqlite3.Error as error:
            connection.rollback()
            raise SampleRepositoryError(f"数据集索引初始化失败: {error}") from error
        finally:
            connection.close()

    def _migrate_v0(self, connection: sqlite3.Connection) -> None:
        """迁移步骤二旧表；无法验证的行保留在 legacy 表和诊断表。"""

        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        if "samples" not in tables:
            return
        columns = {
            str(row[1]) for row in connection.execute("PRAGMA table_info(samples)").fetchall()
        }
        if "original_filename" in columns:
            return

        connection.execute("ALTER TABLE samples RENAME TO legacy_samples_v0")
        if "sample_labels" in tables:
            connection.execute("ALTER TABLE sample_labels RENAME TO legacy_sample_labels_v0")
        if "similarity_members" in tables:
            connection.execute(
                "ALTER TABLE similarity_members RENAME TO legacy_similarity_members_v0"
            )
        self._create_v1_schema(connection)
        self._migrate_v1_to_v2(connection)
        rows = connection.execute("SELECT * FROM legacy_samples_v0").fetchall()
        migrated_ids: set[str] = set()
        for row in rows:
            try:
                sample = self._legacy_sample(row)
                self._insert_sample(connection, sample)
                migrated_ids.add(sample.id)
            except (SampleRepositoryError, ValueError) as error:
                connection.execute(
                    "INSERT INTO migration_issues(kind, reference_id, details) VALUES (?, ?, ?)",
                    ("sample_v0", str(row["id"]), str(error)),
                )
        if "legacy_sample_labels_v0" in {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }:
            for row in connection.execute("SELECT * FROM legacy_sample_labels_v0").fetchall():
                if str(row["sample_id"]) in migrated_ids:
                    connection.execute(
                        "INSERT OR IGNORE INTO sample_labels(sample_id, label_id, shape_id) "
                        "VALUES (?, ?, ?)",
                        (str(row["sample_id"]), str(row["label_id"]), str(row["shape_id"])),
                    )
        legacy_tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        if "legacy_similarity_members_v0" in legacy_tables:
            grouped: dict[str, list[sqlite3.Row]] = {}
            for row in connection.execute("SELECT * FROM legacy_similarity_members_v0").fetchall():
                grouped.setdefault(str(row["group_id"]), []).append(row)
            timestamp = datetime.now(UTC).isoformat()
            for group_id, members in grouped.items():
                try:
                    _canonical_uuid(group_id)
                    member_ids = [str(row["sample_id"]) for row in members]
                    if any(member_id not in migrated_ids for member_id in member_ids):
                        raise SampleRepositoryError("旧相似组包含未迁移样本")
                    status = (
                        SimilarityStatus.CONFIRMED
                        if any(bool(row["confirmed"]) for row in members)
                        else SimilarityStatus.PENDING
                    )
                    connection.execute(
                        "INSERT INTO similarity_groups VALUES (?, ?, ?, ?, ?)",
                        (group_id, status.value, 1.0, timestamp, timestamp),
                    )
                    connection.executemany(
                        "INSERT INTO similarity_members(group_id, sample_id) VALUES (?, ?)",
                        ((group_id, member_id) for member_id in member_ids),
                    )
                except (SampleRepositoryError, sqlite3.Error) as error:
                    connection.execute(
                        "INSERT INTO migration_issues(kind, reference_id, details) "
                        "VALUES (?, ?, ?)",
                        ("similarity_v0", group_id, str(error)),
                    )

    def _legacy_sample(self, row: sqlite3.Row) -> DatasetSample:
        """旧绝对路径只有位于当前受管目录时才能转换为相对路径。"""

        if str(row["dataset_id"]) != self.dataset_id:
            raise SampleRepositoryError("旧样本属于其他数据集")
        image_path = self._relative_from_legacy(str(row["image_path"]), "pool/images")
        annotation_path = ""
        legacy_annotation = str(row["annotation_path"])
        if legacy_annotation:
            annotation_path = self._relative_from_legacy(
                legacy_annotation, "pool/annotations", require_exists=False
            )
        content_hash = str(row["content_hash"])
        perceptual_hash = str(row["perceptual_hash"])
        if len(content_hash) != 64 or len(perceptual_hash) != 22:
            raise SampleRepositoryError("旧样本哈希格式无效")
        return DatasetSample(
            id=str(row["id"]),
            dataset_id=self.dataset_id,
            filename=str(row["filename"]),
            original_filename=str(row["filename"]),
            image_path=image_path,
            annotation_path=annotation_path,
            width=int(row["width"]),
            height=int(row["height"]),
            content_hash=content_hash,
            file_hash=content_hash,
            perceptual_hash=perceptual_hash,
            review_status=_review_status_from_storage(row["review_status"]),
            health=SampleHealth.READY,
            imported_at=str(row["imported_at"]),
        )

    def _relative_from_legacy(
        self,
        value: str,
        required_prefix: str,
        *,
        require_exists: bool = True,
    ) -> str:
        path = Path(value)
        if not path.is_absolute():
            return self.validate_relative_path(value, required_prefix)
        try:
            relative = path.resolve(strict=require_exists).relative_to(self.paths.root.resolve())
        except (OSError, ValueError) as error:
            raise SampleRepositoryError("旧样本路径不在受管数据集内") from error
        return self.validate_relative_path(relative.as_posix(), required_prefix)

    @staticmethod
    def _create_v1_schema(connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS samples (
                id TEXT PRIMARY KEY,
                dataset_id TEXT NOT NULL,
                filename TEXT NOT NULL,
                original_filename TEXT NOT NULL,
                image_path TEXT NOT NULL,
                annotation_path TEXT NOT NULL DEFAULT '',
                width INTEGER NOT NULL CHECK(width > 0),
                height INTEGER NOT NULL CHECK(height > 0),
                image_mode TEXT NOT NULL,
                managed_format TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                file_hash TEXT NOT NULL,
                perceptual_hash TEXT NOT NULL,
                perceptual_hash_version TEXT NOT NULL,
                review_status TEXT NOT NULL,
                health TEXT NOT NULL,
                thumbnail_state TEXT NOT NULL,
                thumbnail_path TEXT NOT NULL DEFAULT '',
                is_trashed INTEGER NOT NULL DEFAULT 0,
                duplicate_group_id TEXT,
                similarity_group_id TEXT,
                imported_at TEXT NOT NULL,
                UNIQUE(dataset_id, filename)
            );
            CREATE INDEX IF NOT EXISTS idx_samples_dataset_filename
                ON samples(dataset_id, is_trashed, filename COLLATE NOCASE);
            CREATE INDEX IF NOT EXISTS idx_samples_dataset_status
                ON samples(dataset_id, is_trashed, review_status);
            CREATE INDEX IF NOT EXISTS idx_samples_content_hash
                ON samples(dataset_id, content_hash, is_trashed);
            CREATE INDEX IF NOT EXISTS idx_samples_imported
                ON samples(dataset_id, is_trashed, imported_at);

            CREATE TABLE IF NOT EXISTS sample_labels (
                sample_id TEXT NOT NULL,
                label_id TEXT NOT NULL,
                shape_id TEXT NOT NULL,
                PRIMARY KEY(sample_id, shape_id),
                FOREIGN KEY(sample_id) REFERENCES samples(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_sample_labels_label
                ON sample_labels(label_id, sample_id);

            CREATE TABLE IF NOT EXISTS perceptual_hash_bands (
                sample_id TEXT NOT NULL,
                band_index INTEGER NOT NULL,
                band_value TEXT NOT NULL,
                PRIMARY KEY(sample_id, band_index),
                FOREIGN KEY(sample_id) REFERENCES samples(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_perceptual_band
                ON perceptual_hash_bands(band_index, band_value);

            CREATE TABLE IF NOT EXISTS similarity_groups (
                id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                score REAL NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS similarity_members (
                group_id TEXT NOT NULL,
                sample_id TEXT NOT NULL,
                PRIMARY KEY(group_id, sample_id),
                FOREIGN KEY(group_id) REFERENCES similarity_groups(id) ON DELETE CASCADE,
                FOREIGN KEY(sample_id) REFERENCES samples(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_similarity_sample
                ON similarity_members(sample_id, group_id);

            CREATE TABLE IF NOT EXISTS trash_items (
                sample_id TEXT PRIMARY KEY,
                trashed_at TEXT NOT NULL,
                manifest_path TEXT NOT NULL,
                FOREIGN KEY(sample_id) REFERENCES samples(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS managed_operations (
                id TEXT PRIMARY KEY,
                dataset_id TEXT NOT NULL,
                operation_type TEXT NOT NULL,
                phase TEXT NOT NULL,
                payload TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_operations_dataset
                ON managed_operations(dataset_id, operation_type, phase);

            CREATE TABLE IF NOT EXISTS naming_counters (
                name TEXT PRIMARY KEY,
                next_value INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS migration_issues (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL,
                reference_id TEXT NOT NULL,
                details TEXT NOT NULL
            );
            """
        )

    @staticmethod
    def _validate_v1_schema(connection: sqlite3.Connection) -> None:
        """已是 v1 时只读验证，避免每次分页查询都改写 SQLite 文件。"""

        required = {
            "samples",
            "sample_labels",
            "perceptual_hash_bands",
            "similarity_groups",
            "similarity_members",
            "trash_items",
            "managed_operations",
            "naming_counters",
            "migration_issues",
        }
        present = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        missing = sorted(required - present)
        if missing:
            raise SampleRepositoryError(f"数据集 v1 索引缺少表: {', '.join(missing)}")

    @classmethod
    def _migrate_v1_to_v2(cls, connection: sqlite3.Connection) -> None:
        """增加标注摘要列，并规范化步骤三使用的旧复核状态。"""

        cls._validate_v1_schema(connection)
        columns = {
            str(row[1]) for row in connection.execute("PRAGMA table_info(samples)").fetchall()
        }
        additions = {
            "annotation_count": "INTEGER NOT NULL DEFAULT 0 CHECK(annotation_count >= 0)",
            "annotation_state": "TEXT NOT NULL DEFAULT 'missing'",
            "annotation_version": "INTEGER NOT NULL DEFAULT 0 CHECK(annotation_version >= 0)",
            "annotation_sha256": "TEXT NOT NULL DEFAULT ''",
            "annotation_updated_at": "TEXT NOT NULL DEFAULT ''",
        }
        for name, declaration in additions.items():
            if name not in columns:
                connection.execute(f"ALTER TABLE samples ADD COLUMN {name} {declaration}")
        connection.execute(
            "UPDATE samples SET review_status = ? WHERE review_status = ?",
            (ReviewStatus.PENDING_REVIEW.value, "auto_pending_review"),
        )
        connection.execute(
            "UPDATE samples SET review_status = ? WHERE review_status = ?",
            (ReviewStatus.COMPLETED.value, "reviewed"),
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_samples_annotation_state "
            "ON samples(dataset_id, is_trashed, annotation_state)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_samples_annotation_updated "
            "ON samples(dataset_id, annotation_updated_at)"
        )

    @classmethod
    def _validate_v2_schema(cls, connection: sqlite3.Connection) -> None:
        """验证 v2 表和标注摘要列，绝不在普通打开时暗中修补。"""

        cls._validate_v1_schema(connection)
        columns = {
            str(row[1]) for row in connection.execute("PRAGMA table_info(samples)").fetchall()
        }
        required = {
            "annotation_count",
            "annotation_state",
            "annotation_version",
            "annotation_sha256",
            "annotation_updated_at",
        }
        missing = sorted(required - columns)
        if missing:
            raise SampleRepositoryError(f"数据集 v2 索引缺少列: {', '.join(missing)}")

    @classmethod
    def _migrate_v2_to_v3(cls, connection: sqlite3.Connection) -> None:
        """重建样本表，将旧五状态收敛为可空的双状态字段。"""

        cls._validate_v2_schema(connection)
        values = {
            row[0]
            for row in connection.execute("SELECT DISTINCT review_status FROM samples").fetchall()
        }
        supported = {
            None,
            "",
            "unreviewed",
            "pending_review",
            "completed",
            "completed_negative",
            "issue",
            "auto_pending_review",
            "reviewed",
        }
        unknown = sorted(str(value) for value in values - supported)
        if unknown:
            raise SampleRepositoryError(f"数据集包含未知复核状态: {', '.join(unknown)}")
        for legacy in ("issue", "auto_pending_review", "reviewed"):
            count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM samples WHERE review_status = ?", (legacy,)
                ).fetchone()[0]
            )
            if count:
                connection.execute(
                    "INSERT INTO migration_issues(kind, reference_id, details) VALUES (?, ?, ?)",
                    ("review_status_v3", legacy, f"迁移 {count} 个样本到双状态模型"),
                )
        connection.execute("DROP TABLE IF EXISTS samples_v3")
        connection.execute(
            """
            CREATE TABLE samples_v3 (
                id TEXT PRIMARY KEY,
                dataset_id TEXT NOT NULL,
                filename TEXT NOT NULL,
                original_filename TEXT NOT NULL,
                image_path TEXT NOT NULL,
                annotation_path TEXT NOT NULL DEFAULT '',
                width INTEGER NOT NULL CHECK(width > 0),
                height INTEGER NOT NULL CHECK(height > 0),
                image_mode TEXT NOT NULL,
                managed_format TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                file_hash TEXT NOT NULL,
                perceptual_hash TEXT NOT NULL,
                perceptual_hash_version TEXT NOT NULL,
                review_status TEXT CHECK(
                    review_status IS NULL OR review_status IN ('pending_review', 'completed')
                ),
                annotation_count INTEGER NOT NULL DEFAULT 0 CHECK(annotation_count >= 0),
                annotation_state TEXT NOT NULL DEFAULT 'missing',
                annotation_version INTEGER NOT NULL DEFAULT 0 CHECK(annotation_version >= 0),
                annotation_sha256 TEXT NOT NULL DEFAULT '',
                annotation_updated_at TEXT NOT NULL DEFAULT '',
                health TEXT NOT NULL,
                thumbnail_state TEXT NOT NULL,
                thumbnail_path TEXT NOT NULL DEFAULT '',
                is_trashed INTEGER NOT NULL DEFAULT 0,
                duplicate_group_id TEXT,
                similarity_group_id TEXT,
                imported_at TEXT NOT NULL,
                UNIQUE(dataset_id, filename)
            )
            """
        )
        columns = (
            "id, dataset_id, filename, original_filename, image_path, annotation_path, "
            "width, height, image_mode, managed_format, content_hash, file_hash, "
            "perceptual_hash, perceptual_hash_version, review_status, annotation_count, "
            "annotation_state, annotation_version, annotation_sha256, annotation_updated_at, "
            "health, thumbnail_state, thumbnail_path, is_trashed, duplicate_group_id, "
            "similarity_group_id, imported_at"
        )
        connection.execute(
            f"INSERT INTO samples_v3({columns}) SELECT "
            "id, dataset_id, filename, original_filename, image_path, annotation_path, "
            "width, height, image_mode, managed_format, content_hash, file_hash, "
            "perceptual_hash, perceptual_hash_version, "
            "CASE review_status "
            "WHEN 'pending_review' THEN 'pending_review' "
            "WHEN 'issue' THEN 'pending_review' "
            "WHEN 'auto_pending_review' THEN 'pending_review' "
            "WHEN 'completed' THEN 'completed' "
            "WHEN 'completed_negative' THEN 'completed' "
            "WHEN 'reviewed' THEN 'completed' ELSE NULL END, "
            "annotation_count, annotation_state, annotation_version, annotation_sha256, "
            "annotation_updated_at, health, thumbnail_state, thumbnail_path, is_trashed, "
            "duplicate_group_id, similarity_group_id, imported_at FROM samples"
        )
        old_count = int(connection.execute("SELECT COUNT(*) FROM samples").fetchone()[0])
        new_count = int(connection.execute("SELECT COUNT(*) FROM samples_v3").fetchone()[0])
        if old_count != new_count:
            raise SampleRepositoryError("v3 迁移样本计数不一致")
        connection.execute("DROP TABLE samples")
        connection.execute("ALTER TABLE samples_v3 RENAME TO samples")
        cls._create_sample_indexes(connection)

    @classmethod
    def _validate_v3_schema(cls, connection: sqlite3.Connection) -> None:
        """只读验证 v3 结构及双状态约束。"""

        cls._validate_v2_schema(connection)
        sql_row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'samples'"
        ).fetchone()
        sql = str(sql_row[0] if sql_row else "").lower()
        if "review_status is null" not in sql or "pending_review" not in sql:
            raise SampleRepositoryError("数据集 v3 索引缺少双状态约束")
        invalid = connection.execute(
            "SELECT COUNT(*) FROM samples WHERE review_status IS NOT NULL "
            "AND review_status NOT IN ('pending_review', 'completed')"
        ).fetchone()[0]
        if invalid:
            raise SampleRepositoryError("数据集 v3 索引包含非法复核状态")

    @staticmethod
    def _create_sample_indexes(connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
            CREATE INDEX IF NOT EXISTS idx_samples_dataset_filename
                ON samples(dataset_id, is_trashed, filename COLLATE NOCASE);
            CREATE INDEX IF NOT EXISTS idx_samples_dataset_status
                ON samples(dataset_id, is_trashed, review_status);
            CREATE INDEX IF NOT EXISTS idx_samples_content_hash
                ON samples(dataset_id, content_hash, is_trashed);
            CREATE INDEX IF NOT EXISTS idx_samples_imported
                ON samples(dataset_id, is_trashed, imported_at);
            CREATE INDEX IF NOT EXISTS idx_samples_annotation_state
                ON samples(dataset_id, is_trashed, annotation_state);
            CREATE INDEX IF NOT EXISTS idx_samples_annotation_updated
                ON samples(dataset_id, annotation_updated_at);
            CREATE INDEX IF NOT EXISTS idx_samples_annotation_presence
                ON samples(dataset_id, is_trashed, annotation_count);
            """
        )

    def validate_relative_path(self, value: str, required_prefix: str) -> str:
        """只接受固定目录下的 POSIX 相对路径，拒绝符号链接与逃逸。"""

        normalized = value.replace("\\", "/")
        pure = PurePosixPath(normalized)
        prefix = PurePosixPath(required_prefix)
        invalid_prefix = pure.parts[: len(prefix.parts)] != prefix.parts
        if pure.is_absolute() or ".." in pure.parts or invalid_prefix:
            raise SampleRepositoryError("样本路径越过受管目录")
        target = self.paths.root.joinpath(*pure.parts)
        current = self.paths.root
        for part in pure.parts:
            current = current / part
            if current.exists() and current.is_symlink():
                raise SampleRepositoryError("样本路径不能经过符号链接")
        try:
            target.resolve(strict=False).relative_to(self.paths.root.resolve())
        except ValueError as error:
            raise SampleRepositoryError("样本路径越过数据集根目录") from error
        return pure.as_posix()

    def resolve_path(self, relative_path: str, required_prefix: str) -> Path:
        """将索引相对路径解析为受管路径；外部路径永远不能成为操作目标。"""

        validated = self.validate_relative_path(relative_path, required_prefix)
        return self.paths.root.joinpath(*PurePosixPath(validated).parts)

    def add_sample(self, sample: DatasetSample) -> None:
        """在同一事务登记样本及感知哈希分桶。"""

        self._validate_sample(sample)
        try:
            with self._connection() as connection:
                self._insert_sample(connection, sample)
        except (sqlite3.Error, ValueError) as error:
            raise SampleRepositoryError(f"样本登记失败: {error}") from error

    def _insert_sample(self, connection: sqlite3.Connection, sample: DatasetSample) -> None:
        review_value: str | None = (
            sample.review_status.value if sample.review_status is not None else None
        )
        if review_value is None:
            table_sql = str(
                connection.execute(
                    "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'samples'"
                ).fetchone()[0]
            ).lower()
            if "review_status is null" not in table_sql:
                review_value = "unreviewed"
        values = (
            sample.id,
            sample.dataset_id,
            sample.filename,
            sample.original_filename or sample.filename,
            sample.image_path,
            sample.annotation_path,
            sample.width,
            sample.height,
            sample.image_mode,
            sample.managed_format,
            sample.content_hash,
            sample.file_hash,
            sample.perceptual_hash,
            sample.perceptual_hash_version,
            review_value,
            sample.annotation_count,
            sample.annotation_state.value,
            sample.annotation_version,
            sample.annotation_sha256,
            sample.annotation_updated_at,
            sample.health.value,
            sample.thumbnail_state.value,
            sample.thumbnail_path,
            int(sample.is_trashed),
            sample.duplicate_group_id,
            sample.similarity_group_id,
            sample.imported_at,
        )
        connection.execute(
            "INSERT INTO samples("
            "id, dataset_id, filename, original_filename, image_path, annotation_path, "
            "width, height, image_mode, managed_format, content_hash, file_hash, "
            "perceptual_hash, perceptual_hash_version, review_status, annotation_count, "
            "annotation_state, annotation_version, annotation_sha256, annotation_updated_at, "
            "health, thumbnail_state, thumbnail_path, is_trashed, duplicate_group_id, "
            "similarity_group_id, imported_at) VALUES ("
            "?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            values,
        )
        connection.executemany(
            "INSERT INTO perceptual_hash_bands(sample_id, band_index, band_value) VALUES (?, ?, ?)",
            (
                (sample.id, index, value)
                for index, value in enumerate(_hash_bands(sample.perceptual_hash))
            ),
        )

    def _validate_sample(self, sample: DatasetSample) -> None:
        if sample.dataset_id != self.dataset_id:
            raise SampleRepositoryError("样本不属于当前数据集")
        self.validate_relative_path(sample.image_path, "pool/images")
        if sample.annotation_path:
            self.validate_relative_path(sample.annotation_path, "pool/annotations")
        if sample.thumbnail_path:
            self.validate_relative_path(sample.thumbnail_path, "cache/thumbnails")

    def get_sample(self, sample_id: str, *, include_trashed: bool = False) -> DatasetSample | None:
        _canonical_uuid(sample_id)
        clause = "" if include_trashed else " AND is_trashed = 0"
        with self._connection() as connection:
            row = connection.execute(
                f"SELECT * FROM samples WHERE id = ? AND dataset_id = ?{clause}",
                (sample_id, self.dataset_id),
            ).fetchone()
        return self._sample_from_row(row) if row else None

    def find_by_content_hash(self, content_hash: str) -> tuple[DatasetSample, ...]:
        """完全重复查询包含当前活动池，不把回收站图片当作活动冲突。"""

        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM samples WHERE dataset_id = ? AND content_hash = ? "
                "AND is_trashed = 0 ORDER BY imported_at, id",
                (self.dataset_id, content_hash),
            ).fetchall()
        return tuple(self._sample_from_row(row) for row in rows)

    def query(self, query: SampleQuery) -> SamplePage:
        """分页和统计使用相同条件，禁止先加载全部样本。"""

        if query.dataset_id != self.dataset_id:
            raise SampleRepositoryError("分页条件不属于当前数据集")
        if query.offset < 0 or not 1 <= query.limit <= 500:
            raise SampleRepositoryError("分页范围无效")
        where, parameters, join = self._query_parts(query)
        order = {
            SampleSort.FILENAME_ASC: "samples.filename COLLATE NOCASE ASC, samples.id ASC",
            SampleSort.FILENAME_DESC: "samples.filename COLLATE NOCASE DESC, samples.id ASC",
            SampleSort.IMPORTED_NEWEST: "samples.imported_at DESC, samples.id ASC",
            SampleSort.IMPORTED_OLDEST: "samples.imported_at ASC, samples.id ASC",
        }[query.sort]
        try:
            with self._connection() as connection:
                total_row = connection.execute(
                    f"SELECT COUNT(DISTINCT samples.id) FROM samples {join} WHERE {where}",
                    parameters,
                ).fetchone()
                rows = connection.execute(
                    f"SELECT DISTINCT samples.* FROM samples {join} WHERE {where} "
                    f"ORDER BY {order} LIMIT ? OFFSET ?",
                    [*parameters, query.limit, query.offset],
                ).fetchall()
        except sqlite3.Error as error:
            raise SampleRepositoryError(f"样本分页查询失败: {error}") from error
        return SamplePage(
            tuple(self._sample_from_row(row) for row in rows),
            int(total_row[0]),
            query.offset,
            query.limit,
        )

    def _query_parts(self, query: SampleQuery) -> tuple[str, list[Any], str]:
        clauses = ["samples.dataset_id = ?", "samples.is_trashed = ?"]
        parameters: list[Any] = [self.dataset_id, int(query.include_trashed)]
        join = ""
        if query.search.strip():
            clauses.append(
                "(samples.filename LIKE ? ESCAPE '\\' OR "
                "samples.original_filename LIKE ? ESCAPE '\\')"
            )
            escaped = _escape_like(query.search.strip())
            parameters.extend([f"%{escaped}%", f"%{escaped}%"])
        if query.review_status is not None:
            clauses.append("samples.review_status = ?")
            parameters.append(query.review_status.value)
        if query.annotation_state is not None:
            clauses.append("samples.annotation_state = ?")
            parameters.append(query.annotation_state.value)
        if query.has_annotations is not None:
            clauses.append(
                "samples.annotation_count > 0"
                if query.has_annotations
                else "samples.annotation_count = 0"
            )
        if query.label_id:
            join = "JOIN sample_labels ON sample_labels.sample_id = samples.id"
            clauses.append("sample_labels.label_id = ?")
            parameters.append(query.label_id)
        return " AND ".join(clauses), parameters, join

    def count_active(self) -> int:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT COUNT(*) FROM samples WHERE dataset_id = ? AND is_trashed = 0",
                (self.dataset_id,),
            ).fetchone()
        return int(row[0])

    def count_reviewed(self) -> int:
        """返回整图复核已完成数量，供数据集摘要与主页对账。"""

        with self._connection() as connection:
            row = connection.execute(
                "SELECT COUNT(*) FROM samples WHERE dataset_id = ? AND is_trashed = 0 "
                "AND review_status = ?",
                (self.dataset_id, ReviewStatus.COMPLETED.value),
            ).fetchone()
        return int(row[0])

    def update_annotation_index(
        self,
        sample_id: str,
        *,
        annotation_path: str,
        annotation_count: int,
        annotation_state: AnnotationState,
        annotation_version: int,
        annotation_sha256: str,
        annotation_updated_at: str,
        review_status: ReviewStatus | None,
        shape_labels: Sequence[tuple[str, str]],
    ) -> None:
        """在单个 SQLite 事务内提交标注摘要与框到标签的反向索引。"""

        _canonical_uuid(sample_id)
        if annotation_path:
            self.validate_relative_path(annotation_path, "pool/annotations")
        if annotation_count != len(shape_labels) or annotation_count < 0:
            raise SampleRepositoryError("标注框数量与标签索引不一致")
        if annotation_sha256 and len(annotation_sha256) != 64:
            raise SampleRepositoryError("标注摘要格式无效")
        try:
            with self._connection() as connection:
                cursor = connection.execute(
                    "UPDATE samples SET annotation_path = ?, annotation_count = ?, "
                    "annotation_state = ?, annotation_version = ?, annotation_sha256 = ?, "
                    "annotation_updated_at = ?, review_status = ? "
                    "WHERE id = ? AND dataset_id = ? AND is_trashed = 0",
                    (
                        annotation_path,
                        annotation_count,
                        annotation_state.value,
                        annotation_version,
                        annotation_sha256,
                        annotation_updated_at,
                        review_status.value if review_status is not None else None,
                        sample_id,
                        self.dataset_id,
                    ),
                )
                if cursor.rowcount != 1:
                    raise SampleRepositoryError("待更新标注摘要的样本不存在")
                connection.execute("DELETE FROM sample_labels WHERE sample_id = ?", (sample_id,))
                connection.executemany(
                    "INSERT INTO sample_labels(sample_id, label_id, shape_id) VALUES (?, ?, ?)",
                    ((sample_id, label_id, shape_id) for shape_id, label_id in shape_labels),
                )
        except SampleRepositoryError:
            raise
        except sqlite3.Error as error:
            raise SampleRepositoryError(f"标注索引提交失败: {error}") from error

    def update_review_status(self, sample_id: str, review_status: ReviewStatus) -> None:
        """不改写 LabelMe JSON，仅原子更新图片级复核结论。"""

        _canonical_uuid(sample_id)
        try:
            with self._connection() as connection:
                cursor = connection.execute(
                    "UPDATE samples SET review_status = ? WHERE id = ? AND dataset_id = ? "
                    "AND is_trashed = 0",
                    (review_status.value, sample_id, self.dataset_id),
                )
                if cursor.rowcount != 1:
                    raise SampleRepositoryError("待更新复核状态的样本不存在")
        except SampleRepositoryError:
            raise
        except sqlite3.Error as error:
            raise SampleRepositoryError(f"复核状态提交失败: {error}") from error

    def label_usage_counts(self) -> dict[str, tuple[int, int]]:
        """返回每个标签的图片数和矩形数，不解析任何 JSON。"""

        with self._connection() as connection:
            rows = connection.execute(
                "SELECT sample_labels.label_id, COUNT(DISTINCT sample_labels.sample_id), "
                "COUNT(*) FROM sample_labels JOIN samples ON samples.id = sample_labels.sample_id "
                "WHERE samples.dataset_id = ? AND samples.is_trashed = 0 "
                "GROUP BY sample_labels.label_id",
                (self.dataset_id,),
            ).fetchall()
        return {str(row[0]): (int(row[1]), int(row[2])) for row in rows}

    def sample_ids_for_label(self, label_id: str) -> tuple[str, ...]:
        """返回标签检查和迁移需要的稳定样本 ID，不读取标注 JSON。"""

        _canonical_uuid(label_id)
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT DISTINCT samples.id FROM sample_labels "
                "JOIN samples ON samples.id = sample_labels.sample_id "
                "WHERE samples.dataset_id = ? AND samples.is_trashed = 0 "
                "AND sample_labels.label_id = ? ORDER BY samples.id",
                (self.dataset_id, label_id),
            ).fetchall()
        return tuple(str(row[0]) for row in rows)

    def update_annotation_diagnostic(
        self,
        sample_id: str,
        state: AnnotationState,
    ) -> None:
        """加载失败时只更新派生健康状态，不覆盖摘要或原 JSON。"""

        if state not in {
            AnnotationState.CORRUPT,
            AnnotationState.UNKNOWN_LABEL,
            AnnotationState.RECOVERY_REQUIRED,
        }:
            raise SampleRepositoryError("该接口只接受标注异常状态")
        with self._connection() as connection:
            cursor = connection.execute(
                "UPDATE samples SET annotation_state = ? "
                "WHERE id = ? AND dataset_id = ? AND is_trashed = 0",
                (state.value, sample_id, self.dataset_id),
            )
            if cursor.rowcount != 1:
                raise SampleRepositoryError("待标记标注异常的样本不存在")

    def locate_sample(self, sample_id: str, query: SampleQuery) -> int | None:
        """按当前筛选和排序返回样本零基位置，供跨页高亮跳转。"""

        page = self.query(
            SampleQuery(
                dataset_id=query.dataset_id,
                offset=0,
                limit=500,
                search=query.search,
                review_status=query.review_status,
                annotation_state=query.annotation_state,
                label_id=query.label_id,
                sort=query.sort,
                include_trashed=query.include_trashed,
            )
        )
        # 数据库每页上限为 500；大集合使用窗口函数避免加载所有样本。
        if page.total <= 500:
            return next(
                (index for index, item in enumerate(page.items) if item.id == sample_id), None
            )
        where, parameters, join = self._query_parts(query)
        order = {
            SampleSort.FILENAME_ASC: "samples.filename COLLATE NOCASE ASC, samples.id ASC",
            SampleSort.FILENAME_DESC: "samples.filename COLLATE NOCASE DESC, samples.id ASC",
            SampleSort.IMPORTED_NEWEST: "samples.imported_at DESC, samples.id ASC",
            SampleSort.IMPORTED_OLDEST: "samples.imported_at ASC, samples.id ASC",
        }[query.sort]
        with self._connection() as connection:
            row = connection.execute(
                "SELECT position FROM (SELECT samples.id, ROW_NUMBER() OVER (ORDER BY "
                f"{order}) - 1 AS position FROM samples {join} WHERE {where}) WHERE id = ?",
                [*parameters, sample_id],
            ).fetchone()
        return int(row[0]) if row else None

    def all_active_samples(self) -> tuple[DatasetSample, ...]:
        """仅供启动对账逐项验证文件；普通页面仍必须使用分页查询。"""

        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM samples WHERE dataset_id = ? AND is_trashed = 0 ORDER BY id",
                (self.dataset_id,),
            ).fetchall()
        return tuple(self._sample_from_row(row) for row in rows)

    def update_health(self, sample_id: str, health: SampleHealth) -> None:
        """对账只改变健康标记，不删除或替换用户的受管图片。"""

        with self._connection() as connection:
            cursor = connection.execute(
                "UPDATE samples SET health = ? WHERE id = ? AND dataset_id = ?",
                (health.value, sample_id, self.dataset_id),
            )
            if cursor.rowcount != 1:
                raise SampleRepositoryError("待标记健康状态的样本不存在")

    def sample_filename_exists(self, filename: str, *, excluding_id: str | None = None) -> bool:
        """使用 SQLite 的不区分大小写规则检查活动受管名称冲突。"""

        query = (
            "SELECT 1 FROM samples WHERE dataset_id = ? AND is_trashed = 0 "
            "AND filename = ? COLLATE NOCASE"
        )
        parameters: list[Any] = [self.dataset_id, filename]
        if excluding_id:
            query += " AND id != ?"
            parameters.append(excluding_id)
        with self._connection() as connection:
            return connection.execute(query, parameters).fetchone() is not None

    def allocate_sequence(self, count: int, *, start_at: int) -> int:
        """原子预留命名序号；取消造成的间隔可以接受，绝不复用已发布名称。"""

        if count < 1 or start_at < 0:
            raise SampleRepositoryError("命名序号范围无效")
        with self._connection() as connection:
            row = connection.execute(
                "SELECT next_value FROM naming_counters WHERE name = 'managed_image'"
            ).fetchone()
            first = max(start_at, int(row[0]) if row else start_at)
            connection.execute(
                "INSERT INTO naming_counters(name, next_value) VALUES('managed_image', ?) "
                "ON CONFLICT(name) DO UPDATE SET next_value = excluded.next_value",
                (first + count,),
            )
        return first

    def update_sample_paths(
        self,
        sample_id: str,
        *,
        filename: str,
        image_path: str,
        annotation_path: str,
    ) -> None:
        """文件成功发布后再切换索引路径，失败由外层文件事务恢复。"""

        self.validate_relative_path(image_path, "pool/images")
        if annotation_path:
            self.validate_relative_path(annotation_path, "pool/annotations")
        with self._connection() as connection:
            cursor = connection.execute(
                "UPDATE samples SET filename = ?, image_path = ?, annotation_path = ? "
                "WHERE id = ? AND dataset_id = ?",
                (filename, image_path, annotation_path, sample_id, self.dataset_id),
            )
            if cursor.rowcount != 1:
                raise SampleRepositoryError("待更新样本不存在")

    def rename_sample_paths(
        self,
        changes: Sequence[tuple[str, str, str, str]],
        *,
        annotation_metadata: Sequence[tuple[str, str, str]] = (),
    ) -> None:
        """在一个 SQLite 事务内用临时名完成批量交换，避免唯一键冲突。"""

        if not changes:
            return
        validated: list[tuple[str, str, str, str]] = []
        for sample_id, filename, image_path, annotation_path in changes:
            _canonical_uuid(sample_id)
            self.validate_relative_path(image_path, "pool/images")
            if annotation_path:
                self.validate_relative_path(annotation_path, "pool/annotations")
            validated.append((sample_id, filename, image_path, annotation_path))
        valid_ids = {item[0] for item in validated}
        for sample_id, digest, _updated_at in annotation_metadata:
            if sample_id not in valid_ids or (digest and len(digest) != 64):
                raise SampleRepositoryError("重命名标注摘要不属于当前操作或格式无效")
        try:
            with self._connection() as connection:
                for sample_id, _filename, _image_path, _annotation_path in validated:
                    temporary_name = f".__rename__{sample_id}.png"
                    cursor = connection.execute(
                        "UPDATE samples SET filename = ? WHERE id = ? AND dataset_id = ?",
                        (temporary_name, sample_id, self.dataset_id),
                    )
                    if cursor.rowcount != 1:
                        raise SampleRepositoryError("待批量重命名样本不存在")
                for sample_id, filename, image_path, annotation_path in validated:
                    connection.execute(
                        "UPDATE samples SET filename = ?, image_path = ?, annotation_path = ? "
                        "WHERE id = ? AND dataset_id = ?",
                        (filename, image_path, annotation_path, sample_id, self.dataset_id),
                    )
                for sample_id, digest, updated_at in annotation_metadata:
                    cursor = connection.execute(
                        "UPDATE samples SET annotation_sha256 = ?, annotation_updated_at = ? "
                        "WHERE id = ? AND dataset_id = ?",
                        (digest, updated_at, sample_id, self.dataset_id),
                    )
                    if cursor.rowcount != 1:
                        raise SampleRepositoryError("待更新标注摘要的重命名样本不存在")
        except SampleRepositoryError:
            raise
        except sqlite3.Error as error:
            raise SampleRepositoryError(f"批量重命名索引失败: {error}") from error

    def update_thumbnail(
        self,
        sample_id: str,
        state: ThumbnailState,
        relative_path: str = "",
    ) -> None:
        if relative_path:
            self.validate_relative_path(relative_path, "cache/thumbnails")
        with self._connection() as connection:
            connection.execute(
                "UPDATE samples SET thumbnail_state = ?, thumbnail_path = ? WHERE id = ?",
                (state.value, relative_path, sample_id),
            )

    def perceptual_candidates(
        self,
        perceptual_hash: str,
        *,
        exclude_id: str | None = None,
        max_distance: int = 6,
    ) -> tuple[tuple[DatasetSample, int], ...]:
        """先使用 8 个分桶缩小候选，再计算完整位差和平均色差。"""

        bands = _hash_bands(perceptual_hash)
        conditions = " OR ".join("(band_index = ? AND band_value = ?)" for _ in bands)
        parameters: list[Any] = []
        for index, value in enumerate(bands):
            parameters.extend([index, value])
        query = (
            "SELECT DISTINCT samples.* FROM perceptual_hash_bands "
            "JOIN samples ON samples.id = perceptual_hash_bands.sample_id "
            f"WHERE ({conditions}) AND samples.dataset_id = ? AND samples.is_trashed = 0"
        )
        parameters.append(self.dataset_id)
        if exclude_id:
            query += " AND samples.id != ?"
            parameters.append(exclude_id)
        with self._connection() as connection:
            rows = connection.execute(query, parameters).fetchall()
        candidates: list[tuple[DatasetSample, int]] = []
        for row in rows:
            distance = perceptual_distance(perceptual_hash, str(row["perceptual_hash"]))
            if distance <= max_distance:
                candidates.append((self._sample_from_row(row), distance))
        return tuple(sorted(candidates, key=lambda item: (item[1], item[0].filename)))

    def register_similarity_candidates(
        self,
        sample_id: str,
        candidates: Sequence[tuple[DatasetSample, int]],
        *,
        timestamp: str,
    ) -> str | None:
        """将候选合并到一个待确认组，不自动改变任何人工状态。"""

        if not candidates:
            return None
        member_ids = {sample_id, *(sample.id for sample, _distance in candidates)}
        with self._connection() as connection:
            placeholders = ",".join("?" for _ in member_ids)
            existing = connection.execute(
                "SELECT DISTINCT similarity_members.group_id FROM similarity_members "
                "JOIN similarity_groups ON similarity_groups.id = similarity_members.group_id "
                f"WHERE sample_id IN ({placeholders}) AND similarity_groups.status = ?",
                [*member_ids, SimilarityStatus.PENDING.value],
            ).fetchall()
            group_ids = [str(row[0]) for row in existing]
            group_id = group_ids[0] if group_ids else new_id()
            score = max(0.0, 1.0 - min(distance for _sample, distance in candidates) / 64)
            connection.execute(
                "INSERT INTO similarity_groups(id, status, score, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET "
                "score = MAX(score, excluded.score), updated_at = excluded.updated_at",
                (group_id, SimilarityStatus.PENDING.value, score, timestamp, timestamp),
            )
            for old_group in group_ids[1:]:
                connection.execute(
                    "INSERT OR IGNORE INTO similarity_members(group_id, sample_id) "
                    "SELECT ?, sample_id FROM similarity_members WHERE group_id = ?",
                    (group_id, old_group),
                )
                connection.execute("DELETE FROM similarity_groups WHERE id = ?", (old_group,))
            connection.executemany(
                "INSERT OR IGNORE INTO similarity_members(group_id, sample_id) VALUES (?, ?)",
                ((group_id, member_id) for member_id in member_ids),
            )
            connection.execute(
                f"UPDATE samples SET similarity_group_id = ? WHERE id IN ({placeholders})",
                [group_id, *member_ids],
            )
        return group_id

    def list_similarity_groups(
        self, status: SimilarityStatus | None = None
    ) -> tuple[SimilarityGroup, ...]:
        parameters: list[Any] = [self.dataset_id]
        status_clause = ""
        if status is not None:
            status_clause = " AND similarity_groups.status = ?"
            parameters.append(status.value)
        query = (
            "SELECT similarity_groups.id, similarity_groups.status, similarity_groups.score, "
            "similarity_members.sample_id FROM similarity_groups "
            "JOIN similarity_members ON similarity_members.group_id = similarity_groups.id "
            "JOIN samples ON samples.id = similarity_members.sample_id "
            f"WHERE samples.dataset_id = ?{status_clause} "
            "ORDER BY similarity_groups.updated_at DESC, similarity_members.sample_id"
        )
        with self._connection() as connection:
            rows = connection.execute(query, parameters).fetchall()
        grouped: dict[str, tuple[SimilarityStatus, float, list[str]]] = {}
        for row in rows:
            identifier = str(row["id"])
            if identifier not in grouped:
                grouped[identifier] = (
                    SimilarityStatus(str(row["status"])),
                    float(row["score"]),
                    [],
                )
            grouped[identifier][2].append(str(row["sample_id"]))
        return tuple(
            SimilarityGroup(identifier, state, score, tuple(sample_ids))
            for identifier, (state, score, sample_ids) in grouped.items()
        )

    def set_similarity_status(
        self, group_id: str, status: SimilarityStatus, timestamp: str
    ) -> None:
        _canonical_uuid(group_id)
        with self._connection() as connection:
            cursor = connection.execute(
                "UPDATE similarity_groups SET status = ?, updated_at = ? WHERE id = ?",
                (status.value, timestamp, group_id),
            )
            if cursor.rowcount != 1:
                raise SampleRepositoryError("近似图片组不存在")

    def confirmed_similarity_mapping(self) -> dict[str, str]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT similarity_members.sample_id, similarity_members.group_id "
                "FROM similarity_members JOIN similarity_groups "
                "ON similarity_groups.id = similarity_members.group_id "
                "WHERE similarity_groups.status = ?",
                (SimilarityStatus.CONFIRMED.value,),
            ).fetchall()
        return {str(row["sample_id"]): str(row["group_id"]) for row in rows}

    def move_to_trash(self, sample_id: str, trashed_at: str, manifest_path: str) -> None:
        self.validate_relative_path(manifest_path, "trash")
        with self._connection() as connection:
            cursor = connection.execute(
                "UPDATE samples SET is_trashed = 1, filename = ? "
                "WHERE id = ? AND dataset_id = ? "
                "AND is_trashed = 0",
                (f".__trash__{sample_id}.png", sample_id, self.dataset_id),
            )
            if cursor.rowcount != 1:
                raise SampleRepositoryError("待删除样本不存在或已在回收站")
            connection.execute(
                "INSERT INTO trash_items(sample_id, trashed_at, manifest_path) VALUES (?, ?, ?)",
                (sample_id, trashed_at, manifest_path),
            )

    def restore_from_trash(
        self,
        sample_id: str,
        *,
        filename: str,
        image_path: str,
        annotation_path: str,
        annotation_sha256: str = "",
        annotation_updated_at: str = "",
    ) -> None:
        self.validate_relative_path(image_path, "pool/images")
        if annotation_path:
            self.validate_relative_path(annotation_path, "pool/annotations")
        if annotation_sha256 and len(annotation_sha256) != 64:
            raise SampleRepositoryError("回收站标注摘要格式无效")
        with self._connection() as connection:
            cursor = connection.execute(
                "UPDATE samples SET is_trashed = 0, filename = ?, image_path = ?, "
                "annotation_path = ?, annotation_sha256 = ?, annotation_updated_at = ? "
                "WHERE id = ? AND dataset_id = ? AND is_trashed = 1",
                (
                    filename,
                    image_path,
                    annotation_path,
                    annotation_sha256,
                    annotation_updated_at,
                    sample_id,
                    self.dataset_id,
                ),
            )
            if cursor.rowcount != 1:
                raise SampleRepositoryError("回收站样本不存在")
            connection.execute("DELETE FROM trash_items WHERE sample_id = ?", (sample_id,))

    def list_trash(self) -> tuple[TrashItem, ...]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT samples.*, trash_items.trashed_at, trash_items.manifest_path "
                "FROM trash_items JOIN samples ON samples.id = trash_items.sample_id "
                "WHERE samples.dataset_id = ? ORDER BY trash_items.trashed_at DESC",
                (self.dataset_id,),
            ).fetchall()
        return tuple(
            TrashItem(
                self._sample_from_row(row),
                str(row["trashed_at"]),
                str(row["manifest_path"]),
            )
            for row in rows
        )

    def delete_sample_record(self, sample_id: str) -> None:
        with self._connection() as connection:
            cursor = connection.execute(
                "DELETE FROM samples WHERE id = ? AND dataset_id = ?",
                (sample_id, self.dataset_id),
            )
            if cursor.rowcount != 1:
                raise SampleRepositoryError("待永久删除样本不存在")

    def register_operation(self, operation: ManagedOperation) -> None:
        if operation.dataset_id != self.dataset_id:
            raise SampleRepositoryError("恢复日志不属于当前数据集")
        with self._connection() as connection:
            connection.execute(
                "INSERT INTO managed_operations VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    operation.id,
                    operation.dataset_id,
                    operation.operation_type,
                    operation.phase,
                    json.dumps(operation.payload, ensure_ascii=False, separators=(",", ":")),
                    operation.created_at,
                    operation.updated_at,
                ),
            )

    def update_operation(
        self,
        operation_id: str,
        phase: str,
        payload: dict[str, Any],
        *,
        updated_at: str | None = None,
    ) -> None:
        with self._connection() as connection:
            cursor = connection.execute(
                "UPDATE managed_operations SET phase = ?, payload = ?, updated_at = COALESCE(?, "
                "updated_at) WHERE id = ?",
                (
                    phase,
                    json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                    updated_at,
                    operation_id,
                ),
            )
            if cursor.rowcount != 1:
                raise SampleRepositoryError("受管操作日志不存在")

    def finish_operation(self, operation_id: str) -> None:
        with self._connection() as connection:
            connection.execute("DELETE FROM managed_operations WHERE id = ?", (operation_id,))

    def list_operations(self) -> tuple[ManagedOperation, ...]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM managed_operations WHERE dataset_id = ? ORDER BY created_at",
                (self.dataset_id,),
            ).fetchall()
        return tuple(
            ManagedOperation(
                id=str(row["id"]),
                dataset_id=str(row["dataset_id"]),
                operation_type=str(row["operation_type"]),
                phase=str(row["phase"]),
                payload=json.loads(str(row["payload"])),
                created_at=str(row["created_at"]),
                updated_at=str(row["updated_at"]),
            )
            for row in rows
        )

    def migration_issues(self) -> tuple[str, ...]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT kind, reference_id, details FROM migration_issues ORDER BY id"
            ).fetchall()
        return tuple(f"{row['kind']}:{row['reference_id']}: {row['details']}" for row in rows)

    @staticmethod
    def _sample_from_row(row: sqlite3.Row) -> DatasetSample:
        keys = set(row.keys())
        review_status = _review_status_from_storage(row["review_status"])
        return DatasetSample(
            id=str(row["id"]),
            dataset_id=str(row["dataset_id"]),
            filename=str(row["filename"]),
            original_filename=str(row["original_filename"]),
            image_path=str(row["image_path"]),
            annotation_path=str(row["annotation_path"]),
            width=int(row["width"]),
            height=int(row["height"]),
            image_mode=str(row["image_mode"]),
            managed_format=str(row["managed_format"]),
            content_hash=str(row["content_hash"]),
            file_hash=str(row["file_hash"]),
            perceptual_hash=str(row["perceptual_hash"]),
            perceptual_hash_version=str(row["perceptual_hash_version"]),
            review_status=review_status,
            annotation_count=int(row["annotation_count"]) if "annotation_count" in keys else 0,
            annotation_state=(
                AnnotationState(str(row["annotation_state"]))
                if "annotation_state" in keys
                else AnnotationState.MISSING
            ),
            annotation_version=(
                int(row["annotation_version"]) if "annotation_version" in keys else 0
            ),
            annotation_sha256=(
                str(row["annotation_sha256"]) if "annotation_sha256" in keys else ""
            ),
            annotation_updated_at=(
                str(row["annotation_updated_at"]) if "annotation_updated_at" in keys else ""
            ),
            health=SampleHealth(str(row["health"])),
            thumbnail_state=ThumbnailState(str(row["thumbnail_state"])),
            thumbnail_path=str(row["thumbnail_path"]),
            is_trashed=bool(row["is_trashed"]),
            duplicate_group_id=row["duplicate_group_id"],
            similarity_group_id=row["similarity_group_id"],
            imported_at=str(row["imported_at"]),
        )


def _canonical_uuid(value: str) -> str:
    try:
        parsed = str(UUID(value))
    except (TypeError, ValueError) as error:
        raise SampleRepositoryError("稳定标识必须是 UUID") from error
    if parsed != value:
        raise SampleRepositoryError("稳定标识必须使用规范 UUID")
    return value


def _review_status_from_storage(value: object) -> ReviewStatus | None:
    """兼容读取旧索引；v3 写入端只接受双状态或空值。"""

    if value is None or str(value) in {"", "unreviewed"}:
        return None
    normalized = {
        "auto_pending_review": ReviewStatus.PENDING_REVIEW,
        "issue": ReviewStatus.PENDING_REVIEW,
        "reviewed": ReviewStatus.COMPLETED,
        "completed_negative": ReviewStatus.COMPLETED,
    }.get(str(value))
    return normalized or ReviewStatus(str(value))


def _hash_bands(perceptual_hash: str) -> tuple[str, ...]:
    if len(perceptual_hash) != 22:
        raise SampleRepositoryError("感知哈希格式无效")
    return tuple(perceptual_hash[index : index + 2] for index in range(0, 16, 2))


def perceptual_distance(left: str, right: str) -> int:
    """计算 dHash 位差，并以平均色约束纯色和低纹理误报。"""

    try:
        if len(left) != 22 or len(right) != 22:
            return 64
        hash_distance = (int(left[:16], 16) ^ int(right[:16], 16)).bit_count()
        left_color = tuple(int(left[index : index + 2], 16) for index in (16, 18, 20))
        right_color = tuple(int(right[index : index + 2], 16) for index in (16, 18, 20))
        color_distance = sum(
            (first - second) ** 2 for first, second in zip(left_color, right_color, strict=True)
        )
        return hash_distance if color_distance <= 1600 else 64
    except ValueError:
        return 64


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
