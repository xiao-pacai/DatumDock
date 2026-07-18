"""X-AnyLabeling/LabelMe 目录导入服务。"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from datumdock.domain.models import Dataset, Label, Project
from datumdock.services.dataset import (
    SUPPORTED_IMAGE_SUFFIXES,
    DatasetPoolService,
    DuplicateCandidate,
)
from datumdock.services.labels import LabelService
from datumdock.services.storage import ProjectIndexRepository
from datumdock.services.workspace import WorkspaceService


@dataclass
class InteropImportReport:
    """导入前后均可展示的文件级互操作报告。"""

    imported_sample_ids: list[str] = field(default_factory=list)
    missing_json_images: list[Path] = field(default_factory=list)
    invalid_json: dict[Path, str] = field(default_factory=dict)
    unsupported_shape_counts: dict[str, int] = field(default_factory=dict)


class XAnyLabelingInteropService:
    """导入外部目录时绝不修改源图片或源 JSON。"""

    def __init__(self, dataset_pool: DatasetPoolService | None = None) -> None:
        self.dataset_pool = dataset_pool or DatasetPoolService()
        self.label_service = LabelService()

    def import_directory(
        self,
        root: Path,
        project: Project,
        dataset: Dataset,
        source_directory: Path,
        *,
        keep_duplicate: Callable[[DuplicateCandidate], bool] | None = None,
    ) -> InteropImportReport:
        """递归扫描图片与同名 JSON；矩形变为可编辑标注，其余 shape 保留。"""

        report = InteropImportReport()
        image_paths = sorted(
            path
            for path in source_directory.rglob("*")
            if path.is_file() and path.suffix.lower() in SUPPORTED_IMAGE_SUFFIXES
        )
        payloads: dict[Path, dict[str, Any]] = {}
        for image_path in image_paths:
            json_path = image_path.with_suffix(".json")
            if not json_path.is_file():
                report.missing_json_images.append(image_path)
                continue
            try:
                payload = json.loads(json_path.read_text(encoding="utf-8"))
                if not isinstance(payload, dict):
                    raise ValueError("JSON 根节点必须是对象")
                payloads[image_path] = payload
            except (OSError, ValueError, json.JSONDecodeError) as error:
                report.invalid_json[json_path] = str(error)
        self._ensure_labels(project, payloads.values())
        WorkspaceService().save_project(root, project)
        for image_path, payload in payloads.items():
            pool_report = self.dataset_pool.import_images(
                root,
                project,
                dataset,
                [image_path],
                keep_duplicate=keep_duplicate,
            )
            if not pool_report.imported_sample_ids:
                continue
            sample_id = pool_report.imported_sample_ids[0]
            index = ProjectIndexRepository(
                WorkspaceService.project_path(root, project.id) / "project-index.sqlite"
            )
            sample = index.get_sample(sample_id)
            if sample is None:
                raise RuntimeError("导入后找不到受管样本索引")
            document = self.dataset_pool.labelme_repository.from_payload(
                payload,
                sample.id,
                project.label_set,
                sample.filename,
                (sample.width, sample.height),
            )
            document.image_filename = sample.filename
            self.dataset_pool.save_document(root, project, sample, document)
            report.imported_sample_ids.append(sample.id)
            for shape in document.unsupported_shapes:
                shape_type = str(shape.get("shape_type", "unknown"))
                report.unsupported_shape_counts[shape_type] = (
                    report.unsupported_shape_counts.get(shape_type, 0) + 1
                )
        return report

    def _ensure_labels(self, project: Project, payloads: object) -> None:
        """外部英文标签精确匹配已有项目标签，未知标签安全创建为待完善别名。"""

        existing_names = {label.name for label in project.label_set.labels}
        next_class_id = max((label.class_id for label in project.label_set.labels), default=-1) + 1
        for payload in payloads:
            if not isinstance(payload, dict):
                continue
            for shape in payload.get("shapes", []):
                name = str(shape.get("label", "")).strip()
                if not name or name in existing_names:
                    continue
                label = Label(
                    class_id=next_class_id,
                    name=name,
                    alias=name,
                    description="从 X-AnyLabeling 导入，建议在标签管理页补充中文说明。",
                    color=self.label_service.next_color(project.label_set),
                )
                self.label_service.add_label(project.label_set, label)
                existing_names.add(name)
                next_class_id += 1
