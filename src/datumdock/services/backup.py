"""项目备份校验导入与标签兼容的数据集转移服务。"""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import tempfile
import zipfile
from pathlib import Path

from datumdock.domain.models import (
    Dataset,
    DatasetSample,
    Project,
    Workspace,
    WorkspaceProjectRef,
    new_id,
)
from datumdock.services.storage import ProjectIndexRepository, read_json_model, write_json_atomic
from datumdock.services.workspace import WorkspaceService

MODEL_BINARY_SUFFIXES = {".onnx", ".pt", ".pth", ".engine", ".weights", ".ckpt"}


class ProjectBackupService:
    """生成可校验备份包，并在完整验证后才把项目引入工作区。"""

    FORMAT_VERSION = 1

    def export_backup(self, root: Path, project: Project, output_path: Path) -> Path:
        """写入清单与校验和；模型二进制会被排除而配置文件会保留。"""

        project_root = WorkspaceService.project_path(root, project.id)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if output_path.exists():
            raise FileExistsError("备份目标已存在，拒绝无提示覆盖")
        manifest: dict[str, object] = {"format_version": self.FORMAT_VERSION, "files": []}
        with zipfile.ZipFile(output_path, "x", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(project_root.rglob("*")):
                if not path.is_file() or path.suffix.lower() in MODEL_BINARY_SUFFIXES:
                    continue
                relative_path = path.relative_to(project_root).as_posix()
                archive_name = f"project/{relative_path}"
                data = path.read_bytes()
                archive.writestr(archive_name, data)
                manifest["files"].append(
                    {
                        "path": archive_name,
                        "sha256": hashlib.sha256(data).hexdigest(),
                        "size": len(data),
                    }
                )
            archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        return output_path

    def import_backup(self, root: Path, workspace: Workspace, backup_path: Path) -> Project:
        """先在临时目录验证 zip 路径和文件哈希，成功后才登记新项目。"""

        with tempfile.TemporaryDirectory(prefix="datumdock-backup-") as temporary_name:
            temporary_root = Path(temporary_name)
            with zipfile.ZipFile(backup_path) as archive:
                self._validate_archive(archive)
                archive.extractall(temporary_root)
            staged_project = temporary_root / "project"
            project = read_json_model(staged_project / "project.json", Project)
            project.id = new_id()
            target_root = WorkspaceService.project_path(root, project.id)
            if target_root.exists():
                raise FileExistsError("备份导入目标冲突")
            try:
                shutil.copytree(staged_project, target_root)
                write_json_atomic(target_root / "project.json", project)
                self._rewrite_index_paths(target_root, project)
                self._mark_missing_model_binaries(target_root)
                workspace.projects.append(
                    WorkspaceProjectRef(
                        id=project.id,
                        name=project.name,
                        relative_path=f"projects/{project.id}",
                    )
                )
                workspace.recent_project_id = project.id
                write_json_atomic(root / "workspace.json", workspace)
            except Exception:
                shutil.rmtree(target_root, ignore_errors=True)
                raise
            return project

    def _validate_archive(self, archive: zipfile.ZipFile) -> None:
        """拒绝路径穿越、缺失清单和被篡改的项目备份。"""

        names = archive.namelist()
        if "manifest.json" not in names:
            raise ValueError("备份缺少完整性清单")
        if len(names) != len(set(names)):
            raise ValueError("备份包含重复的归档路径")
        if any(Path(name).is_absolute() or ".." in Path(name).parts for name in names):
            raise ValueError("备份包含不安全路径")
        manifest = json.loads(archive.read("manifest.json"))
        if manifest.get("format_version") != self.FORMAT_VERSION:
            raise ValueError("不支持的备份格式版本")
        manifest_files = manifest.get("files", [])
        if not isinstance(manifest_files, list):
            raise ValueError("备份完整性清单格式错误")
        expected_paths = {"manifest.json"}
        for item in manifest_files:
            if not isinstance(item, dict) or not isinstance(item.get("path"), str):
                raise ValueError("备份完整性清单包含无效条目")
            path = item["path"]
            if not path.startswith("project/"):
                raise ValueError("备份完整性清单包含越界文件")
            expected_paths.add(path)
            data = archive.read(path)
            if len(data) != item["size"] or hashlib.sha256(data).hexdigest() != item["sha256"]:
                raise ValueError(f"备份文件校验失败: {path}")
        actual_files = {item.filename for item in archive.infolist() if not item.is_dir()}
        if actual_files != expected_paths:
            raise ValueError("备份包含未登记或缺失的文件")
        if "project/project.json" not in names:
            raise ValueError("备份缺少项目元数据")

    @staticmethod
    def _rewrite_index_paths(project_root: Path, project: Project) -> None:
        """备份可在另一台机器恢复，因此 SQLite 内绝不保留源机器绝对路径。"""

        database_path = project_root / "project-index.sqlite"
        connection = sqlite3.connect(database_path)
        try:
            rows = connection.execute("SELECT id, dataset_id, filename FROM samples").fetchall()
            for sample_id, dataset_id, filename in rows:
                dataset_root = project_root / "datasets" / dataset_id / "pool"
                annotation_name = f"{Path(filename).stem}.json"
                connection.execute(
                    "UPDATE samples SET image_path = ?, annotation_path = ? WHERE id = ?",
                    (
                        str(dataset_root / "images" / filename),
                        str(dataset_root / "annotations" / annotation_name),
                        sample_id,
                    ),
                )
            connection.commit()
        finally:
            connection.close()

    @staticmethod
    def _mark_missing_model_binaries(project_root: Path) -> None:
        """备份刻意排除模型二进制，恢复后明确要求用户重新导入模型。"""

        for metadata_path in (project_root / "models").glob("*/model.json"):
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            metadata["status"] = "binary_missing"
            write_json_atomic(metadata_path, metadata)


class DatasetTransferService:
    """只有训练映射完全一致时才在数据集之间复制或移动样本。"""

    def transfer(
        self,
        root: Path,
        project: Project,
        source: Dataset,
        target: Dataset,
        sample_ids: list[str],
        *,
        move: bool,
    ) -> list[str]:
        """先复制并校验目标，再在移动模式下清理源样本，避免半移动数据。"""

        if source.id == target.id:
            raise ValueError("源数据集与目标数据集不能相同")
        index = ProjectIndexRepository(
            WorkspaceService.project_path(root, project.id) / "project-index.sqlite"
        )
        target_root = WorkspaceService.dataset_path(root, project.id, target.id)
        new_ids: list[str] = []
        copied: list[tuple[DatasetSample, DatasetSample, list[tuple[str, str]]]] = []
        removed_sources: list[tuple[DatasetSample, DatasetSample, list[tuple[str, str]]]] = []
        try:
            for sample_id in sample_ids:
                source_sample = index.get_sample(sample_id)
                if source_sample is None or source_sample.dataset_id != source.id:
                    raise KeyError("存在不属于源数据集的样本")
                if (
                    not Path(source_sample.image_path).is_file()
                    or not Path(source_sample.annotation_path).is_file()
                ):
                    raise FileNotFoundError("源样本图片或标注文件不存在")
                new_sample = source_sample.model_copy(
                    update={
                        "id": new_id(),
                        "dataset_id": target.id,
                        "image_path": str(target_root / "pool" / "images" / source_sample.filename),
                        "annotation_path": str(
                            target_root
                            / "pool"
                            / "annotations"
                            / f"{Path(source_sample.filename).stem}.json"
                        ),
                    }
                )
                if (
                    Path(new_sample.image_path).exists()
                    or Path(new_sample.annotation_path).exists()
                ):
                    raise FileExistsError(f"目标数据集已存在文件: {new_sample.filename}")
                label_rows = index.get_label_rows(source_sample.id)
                shutil.copy2(source_sample.image_path, new_sample.image_path)
                shutil.copy2(source_sample.annotation_path, new_sample.annotation_path)
                index.upsert_sample(new_sample, label_rows)
                copied.append((source_sample, new_sample, label_rows))
                new_ids.append(new_sample.id)
            if move:
                for source_sample, new_sample, label_rows in copied:
                    Path(source_sample.image_path).unlink()
                    Path(source_sample.annotation_path).unlink()
                    index.delete_sample(source_sample.id)
                    removed_sources.append((source_sample, new_sample, label_rows))
        except Exception:
            for source_sample, new_sample, label_rows in reversed(removed_sources):
                if (
                    not Path(source_sample.image_path).exists()
                    and Path(new_sample.image_path).is_file()
                ):
                    shutil.copy2(new_sample.image_path, source_sample.image_path)
                if (
                    not Path(source_sample.annotation_path).exists()
                    and Path(new_sample.annotation_path).is_file()
                ):
                    shutil.copy2(new_sample.annotation_path, source_sample.annotation_path)
                index.upsert_sample(source_sample, label_rows)
            for _, new_sample, _ in copied:
                Path(new_sample.image_path).unlink(missing_ok=True)
                Path(new_sample.annotation_path).unlink(missing_ok=True)
                index.delete_sample(new_sample.id)
            raise
        return new_ids
