"""DatumDock 的稳定领域对象与文件格式模型。"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def new_id() -> str:
    """生成不依赖文件名和排序顺序的稳定对象标识。"""

    return str(uuid4())


def utc_now() -> datetime:
    """生成带时区的 UTC 时间，避免本地时区变化破坏排序。"""

    return datetime.now(UTC)


def _validate_uuid(value: str) -> str:
    """只接受规范 UUID 文本，阻止把路径片段伪装成稳定标识。"""

    try:
        parsed = UUID(value)
    except (TypeError, ValueError) as error:
        raise ValueError("标识必须是有效 UUID") from error
    canonical = str(parsed)
    if value != canonical:
        raise ValueError("标识必须使用规范 UUID 格式")
    return value


class ReviewStatus(StrEnum):
    """用户可见的图片级双状态；空值表示尚无人工或模型结论。"""

    PENDING_REVIEW = "pending_review"
    COMPLETED = "completed"


class AnnotationState(StrEnum):
    """标注文件的独立健康状态，异常状态不由用户手工设置。"""

    MISSING = "missing"
    READY = "ready"
    CORRUPT = "corrupt"
    UNKNOWN_LABEL = "unknown_label"
    RECOVERY_REQUIRED = "recovery_required"


class SampleHealth(StrEnum):
    """样本文件健康状态与人工复核状态相互独立。"""

    READY = "ready"
    MISSING = "missing"
    CORRUPT = "corrupt"


class ThumbnailState(StrEnum):
    """缩略图是可重建缓存，状态不改变原图事实。"""

    MISSING = "missing"
    READY = "ready"
    STALE = "stale"
    ERROR = "error"


class SimilarityStatus(StrEnum):
    """近似候选只有人工确认后才约束后续数据划分。"""

    PENDING = "pending"
    CONFIRMED = "confirmed"
    IGNORED = "ignored"


class SampleSort(StrEnum):
    """SQLite 可直接执行的稳定样本排序。"""

    FILENAME_ASC = "filename_asc"
    FILENAME_DESC = "filename_desc"
    IMPORTED_NEWEST = "imported_newest"
    IMPORTED_OLDEST = "imported_oldest"


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

    _validate_id = field_validator("id")(_validate_uuid)

    @field_validator("x1", "y1", "x2", "y2")
    @classmethod
    def validate_finite_coordinate(cls, value: float) -> float:
        """拒绝无法可靠序列化和绘制的非有限坐标。"""

        if not math.isfinite(value):
            raise ValueError("矩形坐标必须是有限数值")
        return value

    @field_validator("confidence")
    @classmethod
    def validate_confidence(cls, value: float | None) -> float | None:
        """模型置信度存在时必须落在标准概率范围。"""

        if value is not None and (not math.isfinite(value) or not 0 <= value <= 1):
            raise ValueError("置信度必须位于 0 到 1 之间")
        return value

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
    labelme_version: str = "5.4.1"
    image_data: str | None = None
    document_version: int = Field(default=0, ge=0)
    rectangles: list[RectangleShape] = Field(default_factory=list)
    image_flags: dict[str, Any] = Field(default_factory=dict)
    unsupported_shapes: list[dict[str, Any]] = Field(default_factory=list)
    shape_order: list[str] = Field(default_factory=list)
    root_payload: dict[str, Any] = Field(default_factory=dict)

    _validate_sample_id = field_validator("sample_id")(_validate_uuid)

    @model_validator(mode="after")
    def validate_document(self) -> AnnotationDocument:
        """确保图片尺寸有效，且形状顺序不包含重复引用。"""

        if self.image_width < 1 or self.image_height < 1:
            raise ValueError("标注图片尺寸必须大于零")
        if len(self.shape_order) != len(set(self.shape_order)):
            raise ValueError("标注形状顺序包含重复引用")
        return self


class Label(BaseModel):
    """数据集级标签；稳定 ID、训练类别 ID 与英文名受迁移保护。"""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=new_id)
    class_id: int = Field(ge=0)
    name: str = Field(min_length=1, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    alias: str = Field(min_length=1)
    description: str = ""
    synonyms: list[str] = Field(default_factory=list)
    color: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")
    status: LabelStatus = LabelStatus.ACTIVE
    created_at: datetime = Field(default_factory=utc_now)
    modified_at: datetime = Field(default_factory=utc_now)

    _validate_id = field_validator("id")(_validate_uuid)

    @field_validator("name", "alias")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        """训练名和别名不得仅由空白组成。"""

        stripped = value.strip()
        if not stripped:
            raise ValueError("标签训练名和别名不能为空")
        return stripped

    @model_validator(mode="after")
    def validate_timestamps(self) -> Label:
        """标签修改时间不能早于创建时间。"""

        if self.modified_at < self.created_at:
            raise ValueError("标签修改时间不能早于创建时间")
        return self

    @field_validator("synonyms")
    @classmethod
    def remove_empty_synonyms(cls, value: list[str]) -> list[str]:
        """搜索索引不应保存空白同义词或重复项。"""

        return list(dict.fromkeys(item.strip() for item in value if item.strip()))


class LabelSet(BaseModel):
    """一个数据集独立持有的标签集合。"""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=new_id)
    format_version: int = Field(default=2, ge=1)
    revision: int = Field(default=0, ge=0)
    updated_at: datetime = Field(default_factory=utc_now)
    labels: list[Label] = Field(default_factory=list)

    _validate_id = field_validator("id")(_validate_uuid)

    @model_validator(mode="after")
    def validate_unique_mappings(self) -> LabelSet:
        """保护稳定映射，并让活动标签颜色与训练名保持可辨识。"""

        identifiers = [label.id for label in self.labels]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("标签集包含重复的标签 ID")

        class_ids = [label.class_id for label in self.labels]
        if len(class_ids) != len(set(class_ids)):
            raise ValueError("标签集包含重复的类别 ID")

        active = [label for label in self.labels if label.status == LabelStatus.ACTIVE]
        training_names = [label.name.casefold() for label in active]
        if len(training_names) != len(set(training_names)):
            raise ValueError("活动标签包含重复的训练名")

        colors = [label.color.upper() for label in active]
        if len(colors) != len(set(colors)):
            raise ValueError("活动标签包含重复的颜色")
        return self

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


class DatasetDisplaySettings(BaseModel):
    """单个数据集的显示偏好，复制配置时生成独立副本。"""

    model_config = ConfigDict(extra="forbid")

    show_annotation_preview: bool = True
    image_list_mode: str = Field(default="list", pattern=r"^(list|grid)$")


class DatasetImportSettings(BaseModel):
    """本阶段只记录未来图片导入会采用的受管格式策略。"""

    model_config = ConfigDict(extra="forbid")

    managed_format: str = Field(default="PNG", pattern=r"^PNG$")
    keep_source_trace: bool = True


class ManagedDatasetConfiguration(BaseModel):
    """可从其他数据集复制、但不与源对象共享引用的配置。"""

    model_config = ConfigDict(extra="forbid")

    naming_policy: NamingPolicy = Field(default_factory=NamingPolicy)
    import_settings: DatasetImportSettings = Field(default_factory=DatasetImportSettings)
    display_settings: DatasetDisplaySettings = Field(default_factory=DatasetDisplaySettings)
    default_split: tuple[int, int, int] = (80, 10, 10)

    @field_validator("default_split")
    @classmethod
    def validate_default_split(cls, value: tuple[int, int, int]) -> tuple[int, int, int]:
        """训练、验证、测试比例必须完整覆盖一次导出。"""

        if any(item < 0 for item in value) or sum(value) != 100:
            raise ValueError("训练、验证、测试比例之和必须为 100")
        return value


class DatasetStatistics(BaseModel):
    """主页使用的数据集摘要，不替代未来 SQLite 样本事实。"""

    model_config = ConfigDict(extra="forbid")

    image_count: int = Field(default=0, ge=0)
    label_count: int = Field(default=0, ge=0)
    reviewed_count: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_reviewed_count(self) -> DatasetStatistics:
        """复核数量不能超过当前数据集的图片总数。"""

        if self.reviewed_count > self.image_count:
            raise ValueError("复核数量不能超过图片总数")
        return self

    @property
    def reviewed_percent(self) -> int:
        """空数据集复核进度为零，避免产生除零错误。"""

        if self.image_count == 0:
            return 0
        return round(self.reviewed_count * 100 / self.image_count)


class ManagedDataset(BaseModel):
    """软件内部独立受管的数据集存档元数据。"""

    model_config = ConfigDict(extra="forbid")

    format_version: int = Field(default=1, ge=1)
    id: str = Field(default_factory=new_id)
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)
    created_at: datetime = Field(default_factory=utc_now)
    modified_at: datetime = Field(default_factory=utc_now)
    archived: bool = False
    label_set_id: str
    configuration: ManagedDatasetConfiguration = Field(default_factory=ManagedDatasetConfiguration)
    statistics: DatasetStatistics = Field(default_factory=DatasetStatistics)

    _validate_id = field_validator("id")(_validate_uuid)
    _validate_label_set_id = field_validator("label_set_id")(_validate_uuid)

    @field_validator("name")
    @classmethod
    def trim_name(cls, value: str) -> str:
        """元数据中不保存数据集名称两端的无意义空白。"""

        trimmed = value.strip()
        if not trimmed:
            raise ValueError("数据集名称不能为空")
        return trimmed


class DatasetLibraryEntry(BaseModel):
    """资料库首页索引中的轻量数据集登记项。"""

    model_config = ConfigDict(extra="forbid")

    id: str
    relative_path: str
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)
    created_at: datetime
    modified_at: datetime
    archived: bool = False
    statistics: DatasetStatistics = Field(default_factory=DatasetStatistics)

    _validate_id = field_validator("id")(_validate_uuid)

    @model_validator(mode="after")
    def validate_managed_path(self) -> DatasetLibraryEntry:
        """登记路径只能是当前 UUID 对应的固定内部相对目录。"""

        path = PurePosixPath(self.relative_path)
        if path.is_absolute() or path.parts != ("datasets", self.id):
            raise ValueError("数据集登记路径必须指向固定 UUID 目录")
        return self


class AppLibrary(BaseModel):
    """DatumDock 当前 Windows 用户的软件内部资料库索引。"""

    model_config = ConfigDict(extra="forbid")

    format_version: int = Field(default=1, ge=1)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    datasets: list[DatasetLibraryEntry] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_unique_dataset_ids(self) -> AppLibrary:
        """资料库不能把同一个稳定 ID 登记到多个位置。"""

        identifiers = [item.id for item in self.datasets]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("资料库包含重复的数据集 UUID")
        return self


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
    quick_label_dialog_size: tuple[int, int] = (760, 520)

    @field_validator("default_split")
    @classmethod
    def validate_default_split(cls, value: tuple[int, int, int]) -> tuple[int, int, int]:
        """训练、验证、测试比例必须完整覆盖一次导出。"""

        if sum(value) != 100:
            raise ValueError("训练、验证、测试比例之和必须为 100")
        return value

    @field_validator("quick_label_dialog_size")
    @classmethod
    def validate_quick_label_dialog_size(cls, value: tuple[int, int]) -> tuple[int, int]:
        """限制持久化窗口尺寸，避免损坏设置把对话框放到屏幕之外。"""

        width, height = value
        if not 560 <= width <= 4096 or not 420 <= height <= 2160:
            raise ValueError("快速标签窗口尺寸超出安全范围")
        return value


class DatasetSample(BaseModel):
    """SQLite 中的受管图片事实；路径只允许由 Repository 解析。"""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    id: str = Field(default_factory=new_id)
    dataset_id: str
    filename: str = Field(min_length=1, max_length=255)
    original_filename: str = Field(default="", max_length=255)
    image_path: str
    annotation_path: str = ""
    width: int = Field(ge=1)
    height: int = Field(ge=1)
    image_mode: str = Field(default="RGB", pattern=r"^(L|LA|RGB|RGBA)$")
    managed_format: str = Field(default="PNG", pattern=r"^PNG$")
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    file_hash: str = Field(default="", pattern=r"^$|^[0-9a-f]{64}$")
    perceptual_hash: str = Field(pattern=r"^[0-9a-f]{22}$")
    perceptual_hash_version: str = Field(default="dhash64-rgb-v1", pattern=r"^dhash64-rgb-v1$")
    review_status: ReviewStatus | None = None
    annotation_count: int = Field(default=0, ge=0)
    annotation_state: AnnotationState = AnnotationState.MISSING
    annotation_version: int = Field(default=0, ge=0)
    annotation_sha256: str = Field(default="", pattern=r"^$|^[0-9a-f]{64}$")
    annotation_updated_at: str = ""
    health: SampleHealth = SampleHealth.READY
    thumbnail_state: ThumbnailState = ThumbnailState.MISSING
    thumbnail_path: str = ""
    is_trashed: bool = False
    duplicate_group_id: str | None = None
    similarity_group_id: str | None = None
    imported_at: str

    _validate_id = field_validator("id")(_validate_uuid)
    _validate_dataset_id = field_validator("dataset_id")(_validate_uuid)

    @field_validator("filename", "original_filename")
    @classmethod
    def validate_sample_filename(cls, value: str) -> str:
        """文件名不得携带目录片段，避免展示值成为文件操作目标。"""

        if not value:
            return value
        path = PurePosixPath(value.replace("\\", "/"))
        if path.name != value or value in {".", ".."}:
            raise ValueError("样本文件名不能包含目录")
        return value


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
