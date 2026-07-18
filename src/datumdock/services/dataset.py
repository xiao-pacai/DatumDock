"""受管数据集池的导入、自动保存、重命名与删除服务。"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from PIL import Image

from datumdock.domain.models import (
    AnnotationDocument,
    Dataset,
    DatasetSample,
    Project,
    ReviewStatus,
)
from datumdock.services.labelme import LabelMeRepository
from datumdock.services.storage import ProjectIndexRepository, write_json_atomic
from datumdock.services.workspace import WorkspaceService

SUPPORTED_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff"}


@dataclass(frozen=True)
class DuplicateCandidate:
    """导入前交给 UI 展示的完全重复图片候选。"""

    source_path: Path
    existing_samples: tuple[DatasetSample, ...]


@dataclass
class ImportReport:
    """导入任务的成功、跳过与失败信息，供 UI 显示而非静默吞掉。"""

    imported_sample_ids: list[str] = field(default_factory=list)
    duplicates: list[DuplicateCandidate] = field(default_factory=list)
    similar_sample_ids: dict[str, list[str]] = field(default_factory=dict)
    skipped_paths: list[Path] = field(default_factory=list)
    failures: dict[Path, str] = field(default_factory=dict)


class DatasetPoolService:
    """确保所有图片复制进目标数据集池，外部来源永远不受写入和删除影响。"""

    def __init__(self, labelme_repository: LabelMeRepository | None = None) -> None:
        self.labelme_repository = labelme_repository or LabelMeRepository()

    def import_images(
        self,
        root: Path,
        project: Project,
        dataset: Dataset,
        source_paths: Iterable[Path],
        *,
        keep_duplicate: Callable[[DuplicateCandidate], bool] | None = None,
    ) -> ImportReport:
        """复制并转码图片；重复项由回调明确决定是否保留为新稳定样本。"""

        report = ImportReport()
        project_root = WorkspaceService.project_path(root, project.id)
        index = ProjectIndexRepository(project_root / "project-index.sqlite")
        dataset_root = WorkspaceService.dataset_path(root, project.id, dataset.id)
        images_root = dataset_root / "pool" / "images"
        annotations_root = dataset_root / "pool" / "annotations"
        existing_count = index.count_samples(dataset.id)
        for offset, source_path in enumerate(source_paths, start=1):
            if source_path.suffix.lower() not in SUPPORTED_IMAGE_SUFFIXES:
                report.failures[source_path] = "不支持的图片格式"
                continue
            try:
                normalized = self._normalize_to_temp(source_path, images_root)
                temporary_path, width, height, content_hash, perceptual_hash = normalized
                duplicates = tuple(index.find_by_hash(content_hash))
                candidate = DuplicateCandidate(source_path=source_path, existing_samples=duplicates)
                if duplicates:
                    report.duplicates.append(candidate)
                    if keep_duplicate is None or not keep_duplicate(candidate):
                        temporary_path.unlink(missing_ok=True)
                        report.skipped_paths.append(source_path)
                        continue
                filename = self._next_filename(images_root, dataset, existing_count + offset)
                target_image = images_root / filename
                target_annotation = annotations_root / f"{Path(filename).stem}.json"
                os.replace(temporary_path, target_image)
                sample = DatasetSample(
                    dataset_id=dataset.id,
                    filename=filename,
                    image_path=str(target_image),
                    annotation_path=str(target_annotation),
                    width=width,
                    height=height,
                    content_hash=content_hash,
                    perceptual_hash=perceptual_hash,
                    imported_at=datetime.now(UTC).isoformat(),
                )
                document = AnnotationDocument(
                    sample_id=sample.id,
                    image_filename=filename,
                    image_width=width,
                    image_height=height,
                )
                self.labelme_repository.save(target_annotation, document, project.label_set)
                index.upsert_sample(sample, [])
                near_samples = index.register_similarity_candidates(
                    sample.id, sample.perceptual_hash
                )
                if near_samples:
                    report.similar_sample_ids[sample.id] = [item.id for item in near_samples]
                report.imported_sample_ids.append(sample.id)
            except (OSError, ValueError) as error:
                report.failures[source_path] = str(error)
        return report

    def load_document(self, sample: DatasetSample, project: Project) -> AnnotationDocument:
        """读取受管标注；异常必须保留原 JSON 供用户诊断或恢复。"""

        return self.labelme_repository.load(
            Path(sample.annotation_path),
            sample.id,
            project.label_set,
            sample.filename,
            (sample.width, sample.height),
        )

    def save_document(
        self,
        root: Path,
        project: Project,
        sample: DatasetSample,
        document: AnnotationDocument,
        review_status: ReviewStatus | None = None,
    ) -> None:
        """立即原子保存标注并同步 SQLite 标签关联和图片级复核状态。"""

        self.labelme_repository.save(Path(sample.annotation_path), document, project.label_set)
        index = ProjectIndexRepository(
            WorkspaceService.project_path(root, project.id) / "project-index.sqlite"
        )
        if review_status is not None:
            sample.review_status = review_status
        index.upsert_sample(
            sample,
            ((rectangle.label_id, rectangle.id) for rectangle in document.rectangles),
        )

    def rename_samples(
        self,
        root: Path,
        project: Project,
        dataset: Dataset,
        sample_ids: Iterable[str],
    ) -> list[tuple[str, str]]:
        """通过两段临时名重命名图片与 JSON，避免序号交换造成覆盖。"""

        index = ProjectIndexRepository(
            WorkspaceService.project_path(root, project.id) / "project-index.sqlite"
        )
        selected = [index.get_sample(sample_id) for sample_id in sample_ids]
        samples = [sample for sample in selected if sample is not None]
        if len(samples) != len(selected):
            raise KeyError("存在找不到的样本，已停止批量重命名")
        dataset_root = WorkspaceService.dataset_path(root, project.id, dataset.id)
        images_root = dataset_root / "pool" / "images"
        annotations_root = dataset_root / "pool" / "annotations"
        plan = [
            (sample, dataset.naming_policy.filename_for(dataset.naming_policy.start_index + number))
            for number, sample in enumerate(sorted(samples, key=lambda item: item.filename))
        ]
        final_names = [filename for _, filename in plan]
        if len(final_names) != len(set(final_names)):
            raise ValueError("命名规则产生重复文件名")
        temporary_pairs: list[tuple[DatasetSample, Path, Path, str]] = []
        try:
            for sample, filename in plan:
                old_image = Path(sample.image_path)
                old_annotation = Path(sample.annotation_path)
                temp_image = images_root / f".{sample.id}.rename.png"
                temp_annotation = annotations_root / f".{sample.id}.rename.json"
                os.replace(old_image, temp_image)
                os.replace(old_annotation, temp_annotation)
                temporary_pairs.append((sample, temp_image, temp_annotation, filename))
            results: list[tuple[str, str]] = []
            for sample, temp_image, temp_annotation, filename in temporary_pairs:
                new_image = images_root / filename
                new_annotation = annotations_root / f"{Path(filename).stem}.json"
                os.replace(temp_image, new_image)
                os.replace(temp_annotation, new_annotation)
                sample.filename = filename
                sample.image_path = str(new_image)
                sample.annotation_path = str(new_annotation)
                document = self.load_document(sample, project)
                document.image_filename = filename
                self.save_document(root, project, sample, document)
                results.append((sample.id, filename))
            return results
        except Exception:
            for sample, temp_image, temp_annotation, _ in temporary_pairs:
                if temp_image.exists():
                    os.replace(temp_image, Path(sample.image_path))
                if temp_annotation.exists():
                    os.replace(temp_annotation, Path(sample.annotation_path))
            raise

    def delete_sample(
        self,
        root: Path,
        project: Project,
        sample: DatasetSample,
        *,
        move_to_trash: bool,
    ) -> None:
        """只删除受管图片与派生信息；回收站模式可恢复完整样本包。"""

        project_root = WorkspaceService.project_path(root, project.id)
        image_path = Path(sample.image_path)
        annotation_path = Path(sample.annotation_path)
        if move_to_trash:
            trash_root = project_root / "trash" / sample.id
            trash_root.mkdir(parents=True, exist_ok=False)
            image_target = trash_root / image_path.name
            annotation_target = trash_root / annotation_path.name
            shutil.move(str(image_path), image_target)
            shutil.move(str(annotation_path), annotation_target)
            write_json_atomic(
                trash_root / "manifest.json",
                {
                    "sample": sample.model_dump(mode="json"),
                    "image_filename": image_path.name,
                    "annotation_filename": annotation_path.name,
                },
            )
        else:
            image_path.unlink(missing_ok=False)
            annotation_path.unlink(missing_ok=False)
        thumbnail = (
            WorkspaceService.dataset_path(root, project.id, sample.dataset_id)
            / "cache"
            / "thumbnails"
            / f"{sample.id}.png"
        )
        thumbnail.unlink(missing_ok=True)
        ProjectIndexRepository(project_root / "project-index.sqlite").delete_sample(sample.id)

    def restore_sample(self, root: Path, project: Project, sample_id: str) -> DatasetSample:
        """从受管回收站恢复完整样本，原目标冲突时停止恢复而不覆盖现有文件。"""

        project_root = WorkspaceService.project_path(root, project.id)
        trash_root = project_root / "trash" / sample_id
        manifest_path = trash_root / "manifest.json"
        if not manifest_path.exists():
            raise FileNotFoundError("回收站中不存在该样本")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        sample = DatasetSample.model_validate(manifest["sample"])
        if Path(sample.image_path).exists() or Path(sample.annotation_path).exists():
            raise FileExistsError("恢复目标已存在同名文件")
        shutil.move(str(trash_root / manifest["image_filename"]), sample.image_path)
        shutil.move(str(trash_root / manifest["annotation_filename"]), sample.annotation_path)
        shutil.rmtree(trash_root)
        document = self.load_document(sample, project)
        self.save_document(root, project, sample, document)
        return sample

    def list_trashed_samples(self, root: Path, project: Project) -> list[DatasetSample]:
        """列出当前项目可恢复样本，损坏回收站清单不会影响其他项目数据。"""

        trash_root = WorkspaceService.project_path(root, project.id) / "trash"
        samples: list[DatasetSample] = []
        for manifest_path in trash_root.glob("*/manifest.json"):
            try:
                payload = json.loads(manifest_path.read_text(encoding="utf-8"))
                samples.append(DatasetSample.model_validate(payload["sample"]))
            except (OSError, ValueError, KeyError, json.JSONDecodeError):
                continue
        return sorted(samples, key=lambda item: item.filename)

    @staticmethod
    def _next_filename(images_root: Path, dataset: Dataset, index: int) -> str:
        filename = dataset.naming_policy.filename_for(dataset.naming_policy.start_index + index - 1)
        if not (images_root / filename).exists():
            return filename
        counter = index
        while True:
            next_index = dataset.naming_policy.start_index + counter
            filename = dataset.naming_policy.filename_for(next_index)
            if not (images_root / filename).exists():
                return filename
            counter += 1

    @staticmethod
    def _normalize_to_temp(
        source_path: Path,
        destination_root: Path,
    ) -> tuple[Path, int, int, str, str]:
        """在目标池同分区创建临时 PNG，校验完成后才允许原子移动。"""

        destination_root.mkdir(parents=True, exist_ok=True)
        with Image.open(source_path) as image:
            image.load()
            normalized = image.convert("RGBA") if "A" in image.getbands() else image.convert("RGB")
            width, height = normalized.size
            file_handle, temporary_name = tempfile.mkstemp(
                prefix=".import-",
                suffix=".png",
                dir=destination_root,
            )
            os.close(file_handle)
            temporary_path = Path(temporary_name)
            try:
                normalized.save(temporary_path, "PNG")
                content_hash = hashlib.sha256(temporary_path.read_bytes()).hexdigest()
                perceptual_hash = DatasetPoolService._perceptual_hash(normalized)
            except Exception:
                temporary_path.unlink(missing_ok=True)
                raise
        return temporary_path, width, height, content_hash, perceptual_hash

    @staticmethod
    def _perceptual_hash(image: Image.Image) -> str:
        """组合差异哈希与平均色，避免纯色或低纹理图片产生大量误候选。"""

        grayscale = image.convert("L").resize((9, 8))
        pixels = list(grayscale.getdata())
        bits = [
            "1" if pixels[row * 9 + column] > pixels[row * 9 + column + 1] else "0"
            for row in range(8)
            for column in range(8)
        ]
        red, green, blue = image.convert("RGB").resize((1, 1)).getpixel((0, 0))
        return f"{int(''.join(bits), 2):016x}{red:02x}{green:02x}{blue:02x}"
