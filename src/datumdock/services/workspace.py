"""工作区、项目与数据集的受管文件系统服务。"""

from __future__ import annotations

from pathlib import Path

from datumdock.domain.models import Dataset, Project, Workspace, WorkspaceProjectRef, new_id
from datumdock.services.storage import ProjectIndexRepository, read_json_model, write_json_atomic


class WorkspaceService:
    """创建、打开与切换工作区；所有项目路径都限制在受管工作区内。"""

    WORKSPACE_FILENAME = "workspace.json"
    SETTINGS_FILENAME = "settings.json"

    def create_workspace(self, root: Path) -> Workspace:
        """创建空工作区，已存在的工作区不会被无提示覆盖。"""

        metadata_path = root / self.WORKSPACE_FILENAME
        if metadata_path.exists():
            raise FileExistsError(f"工作区已存在: {root}")
        root.mkdir(parents=True, exist_ok=True)
        (root / "projects").mkdir(exist_ok=True)
        workspace = Workspace()
        write_json_atomic(metadata_path, workspace)
        return workspace

    def open_workspace(self, root: Path) -> Workspace:
        """读取已有工作区；缺少元数据时显式提示而非猜测目录用途。"""

        return read_json_model(root / self.WORKSPACE_FILENAME, Workspace)

    def create_project(
        self,
        root: Path,
        workspace: Workspace,
        name: str,
        *,
        template: Project | None = None,
    ) -> Project:
        """创建项目、默认数据集池目录和 SQLite 索引，并更新工作区登记表。"""

        project = (
            Project(name=name, label_set=template.label_set.model_copy(deep=True))
            if template is not None
            else Project(name=name)
        )
        project_path = root / "projects" / project.id
        project_path.mkdir(parents=True)
        (project_path / "datasets").mkdir()
        (project_path / "models").mkdir()
        (project_path / "trash").mkdir()
        write_json_atomic(project_path / "project.json", project)
        ProjectIndexRepository(project_path / "project-index.sqlite")
        workspace.projects.append(
            WorkspaceProjectRef(
                id=project.id,
                name=project.name,
                relative_path=f"projects/{project.id}",
            )
        )
        workspace.recent_project_id = project.id
        write_json_atomic(root / self.WORKSPACE_FILENAME, workspace)
        return project

    def open_project(self, root: Path, project_id: str) -> Project:
        """只允许打开工作区登记过的项目，避免 UI 任意越界读取路径。"""

        workspace = self.open_workspace(root)
        if not any(item.id == project_id for item in workspace.projects):
            raise KeyError(f"工作区未登记项目: {project_id}")
        return read_json_model(root / "projects" / project_id / "project.json", Project)

    def save_project(self, root: Path, project: Project) -> None:
        """将项目标签集和数据集元数据作为单个原子事实来源保存。"""

        write_json_atomic(root / "projects" / project.id / "project.json", project)

    def create_dataset(
        self,
        root: Path,
        project: Project,
        name: str,
        *,
        template: Dataset | None = None,
    ) -> Dataset:
        """创建与其他数据集隔离的图片、标注和缩略图目录。"""

        dataset = (
            template.model_copy(
                deep=True,
                update={"id": new_id(), "name": name, "archived": False},
            )
            if template is not None
            else Dataset(name=name)
        )
        dataset_root = root / "projects" / project.id / "datasets" / dataset.id
        (dataset_root / "pool" / "images").mkdir(parents=True)
        (dataset_root / "pool" / "annotations").mkdir()
        (dataset_root / "cache" / "thumbnails").mkdir(parents=True)
        write_json_atomic(dataset_root / "dataset.json", dataset)
        project.datasets.append(dataset)
        self.save_project(root, project)
        return dataset

    @staticmethod
    def project_path(root: Path, project_id: str) -> Path:
        """集中构造项目路径，调用方不应拼接用户提供的文件系统路径。"""

        return root / "projects" / project_id

    @classmethod
    def dataset_path(cls, root: Path, project_id: str, dataset_id: str) -> Path:
        """集中构造数据集路径，保持所有池操作有明确边界。"""

        return cls.project_path(root, project_id) / "datasets" / dataset_id
