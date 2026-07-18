"""项目级标签管理、颜色唯一性与训练映射迁移服务。"""

from __future__ import annotations

import colorsys
import os
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from datumdock.domain.models import AnnotationDocument, Label, LabelSet, Project
from datumdock.services.labelme import LabelMeRepository
from datumdock.services.storage import ProjectIndexRepository
from datumdock.services.workspace import WorkspaceService

LABEL_PALETTE = [
    "#78978C",
    "#C48E7A",
    "#819DB5",
    "#B58A9D",
    "#A49A70",
    "#8EA59A",
    "#B58F63",
    "#857FA7",
    "#7BA5A3",
    "#B27775",
]


@dataclass(frozen=True)
class LabelMigrationPreview:
    """标签英文训练名迁移前展示给用户的受影响范围。"""

    sample_count: int
    shape_count: int


class LabelService:
    """保持标签训练映射稳定，并保证活动标签颜色不重复。"""

    def add_label(self, label_set: LabelSet, label: Label) -> Label:
        """验证名称、类别 ID 和颜色后添加标签，不允许静默冲突。"""

        self._validate_unique(label_set, label)
        label_set.labels.append(label)
        return label

    def validate_label(self, label_set: LabelSet, label: Label) -> None:
        """验证新增或编辑后的标签，不修改标签集本身。"""

        self._validate_unique(label_set, label)

    def next_color(self, label_set: LabelSet) -> str:
        """从固定调色板选择未被活动标签占用的颜色。"""

        occupied = {item.color.lower() for item in label_set.labels if item.status == "active"}
        for color in LABEL_PALETTE:
            if color.lower() not in occupied:
                return color
        for index in range(1, 2048):
            hue = (index * 0.61803398875) % 1
            red, green, blue = colorsys.hsv_to_rgb(hue, 0.34, 0.76)
            color = f"#{round(red * 255):02X}{round(green * 255):02X}{round(blue * 255):02X}"
            if color.lower() not in occupied:
                return color
        raise ValueError("无法为标签分配唯一颜色")

    def search(self, label_set: LabelSet, query: str) -> list[Label]:
        """以标签面向人的所有字段搜索，中文别名和英文名优先排序。"""

        normalized = query.casefold().strip()
        if not normalized:
            return list(label_set.labels)

        def score(label: Label) -> tuple[int, str]:
            alias_match = normalized in label.alias.casefold()
            name_match = normalized in label.name.casefold()
            return (0 if alias_match or name_match else 1, label.alias)

        return [
            label
            for label in sorted(label_set.labels, key=score)
            if normalized
            in " ".join([label.name, label.alias, label.description, *label.synonyms]).casefold()
        ]

    def preview_name_migration(
        self,
        index: ProjectIndexRepository,
        dataset_ids: Iterable[str],
        label_id: str,
    ) -> LabelMigrationPreview:
        """基于索引预览迁移影响，避免确认后才发现大范围改写。"""

        sample_ids: set[str] = set()
        shapes = 0
        for dataset_id in dataset_ids:
            samples = index.list_samples(dataset_id, label_id=label_id, limit=100_000)
            sample_ids.update(sample.id for sample in samples)
            shapes += len(samples)
        return LabelMigrationPreview(sample_count=len(sample_ids), shape_count=shapes)

    def _validate_unique(self, label_set: LabelSet, candidate: Label) -> None:
        other_labels = [label for label in label_set.labels if label.id != candidate.id]
        active = [label for label in other_labels if label.status == "active"]
        if any(label.name == candidate.name for label in other_labels):
            raise ValueError("英文训练名必须在项目内唯一")
        if any(label.class_id == candidate.class_id for label in other_labels):
            raise ValueError("训练类别 ID 必须在项目内唯一")
        if candidate.status == "active" and any(
            label.color.lower() == candidate.color.lower() for label in active
        ):
            raise ValueError("活动标签颜色不能重复")


class LabelMigrationService:
    """将英文训练名变更原子地同步到所有相关 LabelMe 标注文件。"""

    def __init__(self, repository: LabelMeRepository) -> None:
        self.repository = repository

    def migrate_training_name(
        self,
        root: Path,
        project: Project,
        label_id: str,
        new_name: str,
    ) -> LabelMigrationPreview:
        """先完整加载受影响文件再写回，读取失败时不修改标签定义。"""

        target = project.label_set.get_label(label_id)
        if target.name == new_name:
            return LabelMigrationPreview(sample_count=0, shape_count=0)
        previous_name = target.name
        previous_label_set = project.label_set.model_copy(deep=True)
        index = ProjectIndexRepository(root / "projects" / project.id / "project-index.sqlite")
        affected_documents: list[tuple[Path, AnnotationDocument, bytes]] = []
        shape_count = 0
        try:
            for dataset in project.datasets:
                for sample in index.list_samples(dataset.id, label_id=label_id, limit=100_000):
                    document = self.repository.load(
                        Path(sample.annotation_path),
                        sample.id,
                        previous_label_set,
                        sample.filename,
                        (sample.width, sample.height),
                    )
                    shape_count += sum(shape.label_id == label_id for shape in document.rectangles)
                    affected_documents.append(
                        (
                            Path(sample.annotation_path),
                            document,
                            Path(sample.annotation_path).read_bytes(),
                        )
                    )
        except Exception:
            raise
        target.name = new_name
        try:
            for path, document, _ in affected_documents:
                self.repository.save(path, document, project.label_set)
            WorkspaceService().save_project(root, project)
        except Exception:
            for path, _, original_bytes in affected_documents:
                self._restore_original(path, original_bytes)
            target.name = previous_name
            raise
        return LabelMigrationPreview(len(affected_documents), shape_count)

    @staticmethod
    def _restore_original(path: Path, original_bytes: bytes) -> None:
        """迁移写入失败时原子恢复已写过的标注文件，避免训练名与 JSON 脱节。"""

        handle, temporary_name = tempfile.mkstemp(
            prefix=f".{path.stem}-",
            suffix=".rollback",
            dir=path.parent,
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(handle, "wb") as stream:
                stream.write(original_bytes)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_path, path)
        except Exception:
            temporary_path.unlink(missing_ok=True)
            raise
