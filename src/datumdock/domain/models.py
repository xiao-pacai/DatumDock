"""DatumDock 的稳定领域对象与文件格式模型。"""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def new_id() -> str:
    """生成不依赖文件名和排序顺序的稳定对象标识。"""

    return str(uuid4())


class ReviewStatus(StrEnum):
    """以整张图片为单位保存的复核状态。"""

    UNREVIEWED = "unreviewed"
    AUTO_PENDING_REVIEW = "auto_pending_review"
    REVIEWED = "reviewed"
    ISSUE = "issue"


class LabelStatus(StrEnum):
    """归档标签仍可读取历史标注，但不能创建新标注。"""

    ACTIVE = "active"
    ARCHIVED = "archived"


class RectangleShape(BaseModel):
    """以原图像素坐标保存的可编辑矩形框。"""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=new_id)
    label_id: str
    x1: float
    y1: float
    x2: float
    y2: float
    source_model_id: str | None = None
    confidence: float | None = None
    compatibility_payload: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def normalize_corners(self) -> RectangleShape:
        """统一矩形两角顺序，避免反向拖拽写入无效框。"""

        self.x1, self.x2 = sorted((self.x1, self.x2))
        self.y1, self.y2 = sorted((self.y1, self.y2))
        return self

    @property
    def width(self) -> float:
        """返回原图像素宽度。"""

        return self.x2 - self.x1

    @property
    def height(self) -> float:
        """返回原图像素高度。"""

        return self.y2 - self.y1


class AnnotationDocument(BaseModel):
    """一个受管样本的可编辑标注及不可编辑兼容负载。"""

    model_config = ConfigDict(extra="forbid")

    sample_id: str
    image_filename: str
    image_width: int
    image_height: int
    rectangles: list[RectangleShape] = Field(default_factory=list)
    image_flags: dict[str, Any] = Field(default_factory=dict)
    unsupported_shapes: list[dict[str, Any]] = Field(default_factory=list)
    root_payload: dict[str, Any] = Field(default_factory=dict)


class Label(BaseModel):
    """项目级标签；稳定 ID、训练类别 ID 与英文名受迁移保护。"""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=new_id)
    class_id: int = Field(ge=0)
    name: str = Field(min_length=1, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    alias: str = Field(min_length=1)
    description: str = ""
    synonyms: list[str] = Field(default_factory=list)
    color: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")
    status: LabelStatus = LabelStatus.ACTIVE

    @field_validator("synonyms")
    @classmethod
    def remove_empty_synonyms(cls, value: list[str]) -> list[str]:
        """搜索索引不应保存空白同义词或重复项。"""

        return list(dict.fromkeys(item.strip() for item in value if item.strip()))


class LabelSet(BaseModel):
    """一个项目唯一的标签集合。"""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=new_id)
    labels: list[Label] = Field(default_factory=list)

    def training_signature(self) -> str:
        """计算影响标注迁移和数据集合并的稳定训练映射签名。"""

        payload = [
            (label.id, label.class_id, label.name, label.status.value)
            for label in sorted(self.labels, key=lambda item: item.id)
        ]
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()

    def display_signature(self) -> str:
        """计算仅用于提示展示差异的非训练映射签名。"""

        payload = [
            (label.id, label.alias, label.description, label.synonyms, label.color)
            for label in sorted(self.labels, key=lambda item: item.id)
        ]
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()

    def get_label(self, label_id: str) -> Label:
        """按稳定 ID 获取标签，未知引用必须显式失败而不是猜测。"""

        for label in self.labels:
            if label.id == label_id:
                return label
        raise KeyError(f"未知标签 ID: {label_id}")


class NamingPolicy(BaseModel):
    """数据集池内文件名规则，不影响导入前的外部文件。"""

    prefix: str = "image"
    start_index: int = Field(default=1, ge=0)
    padding: int = Field(default=6, ge=1, le=12)

    def filename_for(self, index: int) -> str:
        """基于规则产生统一 PNG 文件名。"""

        return f"{self.prefix}_{index:0{self.padding}d}.png"


class Dataset(BaseModel):
    """项目内隔离的数据集元数据。"""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=new_id)
    name: str = Field(min_length=1)
    naming_policy: NamingPolicy = Field(default_factory=NamingPolicy)
    show_annotation_preview: bool = True
    archived: bool = False


class Project(BaseModel):
    """项目元数据，标签集与模型库只属于这个项目。"""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=new_id)
    name: str = Field(min_length=1)
    description: str = ""
    label_set: LabelSet = Field(default_factory=LabelSet)
    datasets: list[Dataset] = Field(default_factory=list)


class WorkspaceProjectRef(BaseModel):
    """工作区登记项目时仅保存可恢复的元数据和相对路径。"""

    id: str
    name: str
    relative_path: str


class Workspace(BaseModel):
    """本地工作区入口及全局项目登记表。"""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=new_id)
    projects: list[WorkspaceProjectRef] = Field(default_factory=list)
    recent_project_id: str | None = None


class AppSettings(BaseModel):
    """不随项目备份传播的全局应用偏好。"""

    model_config = ConfigDict(extra="forbid")

    ui_locale: str = "zh_CN"
    default_split: tuple[int, int, int] = (80, 10, 10)
    trash_sample_threshold: int = Field(default=30, ge=0)
    shortcut_overrides: dict[str, str] = Field(default_factory=dict)

    @field_validator("default_split")
    @classmethod
    def validate_default_split(cls, value: tuple[int, int, int]) -> tuple[int, int, int]:
        """训练、验证、测试比例必须完整覆盖一次导出。"""

        if sum(value) != 100:
            raise ValueError("训练、验证、测试比例之和必须为 100")
        return value


class DatasetSample(BaseModel):
    """SQLite 索引中的受管样本记录。"""

    id: str = Field(default_factory=new_id)
    dataset_id: str
    filename: str
    image_path: str
    annotation_path: str
    width: int = Field(ge=1)
    height: int = Field(ge=1)
    content_hash: str
    perceptual_hash: str
    review_status: ReviewStatus = ReviewStatus.UNREVIEWED
    imported_at: str


class ModelEntry(BaseModel):
    """项目模型配置；模型二进制不进入项目备份。"""

    id: str = Field(default_factory=new_id)
    display_name: str
    filename: str
    format: str
    task_type: str = "detection"
    runtime_config: dict[str, Any] = Field(default_factory=dict)
    model_classes: list[str] = Field(default_factory=list)
    label_mapping: dict[str, str] = Field(default_factory=dict)
    status: str = "pending"


class ExportRequest(BaseModel):
    """只在当前导出流程存活的参数，禁止持久化到项目。"""

    dataset_id: str
    output_directory: str
    split: tuple[int, int, int] = (80, 10, 10)
    seed: int = 42
    include_unannotated: bool = False
    format_name: str = "yolo_detection"

    @field_validator("split")
    @classmethod
    def validate_split(cls, value: tuple[int, int, int]) -> tuple[int, int, int]:
        """避免生成缺失或重复比例的训练目录。"""

        if sum(value) != 100:
            raise ValueError("导出比例之和必须为 100")
        return value
