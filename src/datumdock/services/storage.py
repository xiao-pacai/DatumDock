"""原子 JSON 与 SQLite 索引的基础设施。"""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel

from datumdock.domain.models import DatasetSample, ReviewStatus, new_id

ModelT = TypeVar("ModelT", bound=BaseModel)


def write_json_atomic(path: Path, payload: BaseModel | dict[str, Any]) -> None:
    """以同目录临时文件原子替换 JSON，避免断电留下半写入元数据。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    content = payload.model_dump(mode="json") if isinstance(payload, BaseModel) else payload
    handle, temporary_name = tempfile.mkstemp(
        prefix=f".{path.stem}-",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(content, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise


def read_json_model(path: Path, model_type: type[ModelT]) -> ModelT:
    """读取受控 JSON；调用方可将异常转换为可读的 UI 错误。"""

    with path.open(encoding="utf-8") as stream:
        return model_type.model_validate(json.load(stream))


class ProjectIndexRepository:
    """维护万级样本查询索引，不把图片列表加载到内存。"""

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        """创建可向后扩展的最小索引表与必要查询索引。"""

        with self._connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS samples (
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
                CREATE INDEX IF NOT EXISTS idx_samples_dataset_filename
                    ON samples(dataset_id, filename);
                CREATE INDEX IF NOT EXISTS idx_samples_dataset_status
                    ON samples(dataset_id, review_status);
                CREATE INDEX IF NOT EXISTS idx_samples_content_hash
                    ON samples(content_hash);
                CREATE TABLE IF NOT EXISTS sample_labels (
                    sample_id TEXT NOT NULL,
                    label_id TEXT NOT NULL,
                    shape_id TEXT NOT NULL,
                    PRIMARY KEY(sample_id, shape_id),
                    FOREIGN KEY(sample_id) REFERENCES samples(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_sample_labels_label
                    ON sample_labels(label_id, sample_id);
                CREATE TABLE IF NOT EXISTS similarity_members (
                    group_id TEXT NOT NULL,
                    sample_id TEXT NOT NULL,
                    confirmed INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(group_id, sample_id),
                    FOREIGN KEY(sample_id) REFERENCES samples(id) ON DELETE CASCADE
                );
                """
            )

    def upsert_sample(self, sample: DatasetSample, label_rows: Iterable[tuple[str, str]]) -> None:
        """在同一事务内更新样本和标签查询关联，防止检查集合读到旧数据。"""

        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO samples VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    filename=excluded.filename,
                    image_path=excluded.image_path,
                    annotation_path=excluded.annotation_path,
                    width=excluded.width,
                    height=excluded.height,
                    content_hash=excluded.content_hash,
                    perceptual_hash=excluded.perceptual_hash,
                    review_status=excluded.review_status
                """,
                (
                    sample.id,
                    sample.dataset_id,
                    sample.filename,
                    sample.image_path,
                    sample.annotation_path,
                    sample.width,
                    sample.height,
                    sample.content_hash,
                    sample.perceptual_hash,
                    sample.review_status.value,
                    sample.imported_at,
                ),
            )
            connection.execute("DELETE FROM sample_labels WHERE sample_id = ?", (sample.id,))
            connection.executemany(
                "INSERT INTO sample_labels(sample_id, label_id, shape_id) VALUES (?, ?, ?)",
                ((sample.id, label_id, shape_id) for label_id, shape_id in label_rows),
            )

    def get_sample(self, sample_id: str) -> DatasetSample | None:
        """按稳定 ID 读取样本，不依赖会变化的受管文件名。"""

        with self._connection() as connection:
            row = connection.execute("SELECT * FROM samples WHERE id = ?", (sample_id,)).fetchone()
        return self._sample_from_row(row) if row else None

    def find_by_hash(self, content_hash: str) -> list[DatasetSample]:
        """为重复图确认对话框返回所有已有样本。"""

        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM samples WHERE content_hash = ? ORDER BY imported_at", (content_hash,)
            ).fetchall()
        return [self._sample_from_row(row) for row in rows]

    def get_label_rows(self, sample_id: str) -> list[tuple[str, str]]:
        """读取样本的标签索引行，供受管复制等事务复用。"""

        with self._connection() as connection:
            rows = connection.execute(
                "SELECT label_id, shape_id FROM sample_labels WHERE sample_id = ?", (sample_id,)
            ).fetchall()
        return [(str(row["label_id"]), str(row["shape_id"])) for row in rows]

    def register_similarity_candidates(
        self,
        sample_id: str,
        perceptual_hash: str,
        *,
        max_distance: int = 6,
    ) -> list[DatasetSample]:
        """登记近似图候选组但不自动确认，用户确认后才参与导出分组。"""

        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM samples WHERE id != ?", (sample_id,)
            ).fetchall()
            candidates = [
                self._sample_from_row(row)
                for row in rows
                if _perceptual_distance(perceptual_hash, str(row["perceptual_hash"]))
                <= max_distance
            ]
            if not candidates:
                return []
            candidate_ids = [candidate.id for candidate in candidates]
            placeholders = ",".join("?" for _ in candidate_ids)
            group_rows = connection.execute(
                "SELECT DISTINCT group_id FROM similarity_members "
                f"WHERE sample_id IN ({placeholders})",
                candidate_ids,
            ).fetchall()
            existing_groups = [str(row["group_id"]) for row in group_rows]
            group_id = existing_groups[0] if existing_groups else new_id()
            for old_group_id in existing_groups[1:]:
                connection.execute(
                    "INSERT OR IGNORE INTO similarity_members(group_id, sample_id, confirmed) "
                    "SELECT ?, sample_id, confirmed FROM similarity_members WHERE group_id = ?",
                    (group_id, old_group_id),
                )
                connection.execute(
                    "DELETE FROM similarity_members WHERE group_id = ?", (old_group_id,)
                )
            for member_id in [sample_id, *candidate_ids]:
                connection.execute(
                    "INSERT OR IGNORE INTO similarity_members(group_id, sample_id, confirmed) "
                    "VALUES (?, ?, 0)",
                    (group_id, member_id),
                )
        return candidates

    def list_similarity_groups(
        self, *, confirmed: bool | None = None
    ) -> dict[str, tuple[bool, list[DatasetSample]]]:
        """返回项目内近似图组及其确认状态，供检查界面按组呈现。"""

        clauses = []
        parameters: list[Any] = []
        if confirmed is not None:
            clauses.append("similarity_members.confirmed = ?")
            parameters.append(int(confirmed))
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        query = (
            "SELECT similarity_members.group_id, similarity_members.confirmed, samples.* "
            "FROM similarity_members JOIN samples ON samples.id = similarity_members.sample_id "
            f"{where} ORDER BY similarity_members.group_id, samples.filename"
        )
        result: dict[str, tuple[bool, list[DatasetSample]]] = {}
        with self._connection() as connection:
            rows = connection.execute(query, parameters).fetchall()
        for row in rows:
            group_id = str(row["group_id"])
            is_confirmed = bool(row["confirmed"])
            if group_id not in result:
                result[group_id] = (is_confirmed, [])
            result[group_id][1].append(self._sample_from_row(row))
        return result

    def set_similarity_group_confirmed(self, group_id: str, confirmed: bool) -> None:
        """用户确认近似关系后，导出器才会把该组视作不可拆分整体。"""

        with self._connection() as connection:
            cursor = connection.execute(
                "UPDATE similarity_members SET confirmed = ? WHERE group_id = ?",
                (int(confirmed), group_id),
            )
            if cursor.rowcount == 0:
                raise KeyError("近似图片组不存在")

    def confirmed_similarity_mapping(self) -> dict[str, str]:
        """生成已确认近似组的样本映射，用于避免 train/val/test 数据泄露。"""

        with self._connection() as connection:
            rows = connection.execute(
                "SELECT group_id, sample_id FROM similarity_members WHERE confirmed = 1"
            ).fetchall()
        return {str(row["sample_id"]): str(row["group_id"]) for row in rows}

    def list_samples(
        self,
        dataset_id: str,
        *,
        offset: int = 0,
        limit: int = 100,
        search: str = "",
        review_status: ReviewStatus | None = None,
        label_id: str | None = None,
        annotated: bool | None = None,
    ) -> list[DatasetSample]:
        """按页查询样本，保证 UI 不会为万张图片创建完整列表。"""

        clauses = ["samples.dataset_id = ?"]
        parameters: list[Any] = [dataset_id]
        join = ""
        if search:
            clauses.append("samples.filename LIKE ?")
            parameters.append(f"%{search}%")
        if review_status:
            clauses.append("samples.review_status = ?")
            parameters.append(review_status.value)
        if label_id:
            join = "JOIN sample_labels ON sample_labels.sample_id = samples.id"
            clauses.append("sample_labels.label_id = ?")
            parameters.append(label_id)
        if annotated is True:
            clauses.append(
                "EXISTS (SELECT 1 FROM sample_labels AS annotation_labels "
                "WHERE annotation_labels.sample_id = samples.id)"
            )
        if annotated is False:
            clauses.append(
                "NOT EXISTS (SELECT 1 FROM sample_labels AS annotation_labels "
                "WHERE annotation_labels.sample_id = samples.id)"
            )
        parameters.extend([limit, offset])
        query = (
            "SELECT DISTINCT samples.* FROM samples "
            f"{join} WHERE {' AND '.join(clauses)} "
            "ORDER BY samples.filename LIMIT ? OFFSET ?"
        )
        with self._connection() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [self._sample_from_row(row) for row in rows]

    def count_samples(self, dataset_id: str) -> int:
        """返回当前数据集统计，不扫描受管图片目录。"""

        with self._connection() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS count FROM samples WHERE dataset_id = ?", (dataset_id,)
            ).fetchone()
        return int(row["count"])

    def count_filtered_samples(
        self,
        dataset_id: str,
        *,
        search: str = "",
        review_status: ReviewStatus | None = None,
        label_id: str | None = None,
    ) -> int:
        """返回与分页筛选完全一致的总数，避免 UI 为统计而加载所有样本。"""

        clauses = ["samples.dataset_id = ?"]
        parameters: list[Any] = [dataset_id]
        join = ""
        if search:
            clauses.append("samples.filename LIKE ?")
            parameters.append(f"%{search}%")
        if review_status:
            clauses.append("samples.review_status = ?")
            parameters.append(review_status.value)
        if label_id:
            join = "JOIN sample_labels ON sample_labels.sample_id = samples.id"
            clauses.append("sample_labels.label_id = ?")
            parameters.append(label_id)
        query = (
            "SELECT COUNT(DISTINCT samples.id) AS count FROM samples "
            f"{join} WHERE {' AND '.join(clauses)}"
        )
        with self._connection() as connection:
            row = connection.execute(query, parameters).fetchone()
        return int(row["count"])

    def label_usage_counts(self) -> dict[str, int]:
        """返回每个稳定标签 ID 的矩形数量，供项目级标签表格展示。"""

        with self._connection() as connection:
            rows = connection.execute(
                "SELECT label_id, COUNT(*) AS count FROM sample_labels GROUP BY label_id"
            ).fetchall()
        return {str(row["label_id"]): int(row["count"]) for row in rows}

    def update_review_status(self, sample_id: str, status: ReviewStatus) -> None:
        """问题状态与已复核的互斥性由单字段状态自然保证。"""

        with self._connection() as connection:
            connection.execute(
                "UPDATE samples SET review_status = ? WHERE id = ?", (status.value, sample_id)
            )

    def delete_sample(self, sample_id: str) -> None:
        """先由删除服务移动文件，成功后再删除索引。"""

        with self._connection() as connection:
            connection.execute("DELETE FROM samples WHERE id = ?", (sample_id,))

    @staticmethod
    def _sample_from_row(row: sqlite3.Row) -> DatasetSample:
        return DatasetSample(
            id=row["id"],
            dataset_id=row["dataset_id"],
            filename=row["filename"],
            image_path=row["image_path"],
            annotation_path=row["annotation_path"],
            width=row["width"],
            height=row["height"],
            content_hash=row["content_hash"],
            perceptual_hash=row["perceptual_hash"],
            review_status=ReviewStatus(row["review_status"]),
            imported_at=row["imported_at"],
        )


def _perceptual_distance(left: str, right: str) -> int:
    """计算差异哈希位差并约束平均色差，格式异常视为不可比较。"""

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
