"""UI 原型使用的只读视图模型与无副作用网关协议。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol


class ImageStatus(StrEnum):
    """原型中展示的整图状态，与矩形框数量保持独立。"""

    UNLABELED = "unlabeled"
    PENDING = "pending"
    COMPLETED = "completed"
    NEGATIVE = "negative"
    ISSUE = "issue"
    ERROR = "error"


class DatasetHealth(StrEnum):
    """主页卡片健康状态。"""

    READY = "ready"
    LOADING = "loading"
    DAMAGED = "damaged"


class CommandStatus(StrEnum):
    """界面意图的处理结论，禁止用成功结果冒充真实业务。"""

    PREVIEW_APPLIED = "preview_applied"
    APPLIED = "applied"
    NOT_CONNECTED = "not_connected"
    INVALID = "invalid"
    ERROR = "error"
    PARTIAL = "partial"


@dataclass(frozen=True, slots=True)
class DatasetCardViewData:
    """主页单张数据集卡片所需的完整快照。"""

    id: str
    name: str
    description: str
    image_count: int
    label_count: int
    reviewed_percent: int
    modified_text: str
    cover_seed: int
    health: DatasetHealth = DatasetHealth.READY
    created_sort: str = ""
    modified_sort: str = ""
    archived: bool = False
    diagnostic: str = ""


@dataclass(frozen=True, slots=True)
class LabelViewData:
    """标签知识与训练映射的展示快照。"""

    id: str
    class_id: int
    name: str
    alias: str
    description: str
    synonyms: tuple[str, ...]
    color: str
    usage_count: int
    archived: bool = False


@dataclass(frozen=True, slots=True)
class AnnotationItemViewData:
    """当前图片单个矩形的展示数据。"""

    id: str
    label_id: str
    x1: float
    y1: float
    x2: float
    y2: float
    confidence: float | None = None


@dataclass(frozen=True, slots=True)
class ImageItemViewData:
    """右侧图片列表和预览画布共享的样本快照。"""

    id: str
    filename: str
    status: ImageStatus
    width: int
    height: int
    scene_seed: int
    annotation_count: int
    labels: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ModelViewData:
    """模型管理表的一行演示数据。"""

    id: str
    name: str
    format: str
    input_size: str
    class_count: int
    backend: str
    status: str


@dataclass(frozen=True, slots=True)
class HomeSnapshot:
    """数据集主页一次渲染所需的全部数据。"""

    datasets: tuple[DatasetCardViewData, ...]
    quick_start_completed: int = 2


@dataclass(frozen=True, slots=True)
class WorkspaceSnapshot:
    """标注工作台一次渲染所需的全部内存数据。"""

    dataset: DatasetCardViewData
    labels: tuple[LabelViewData, ...]
    images: tuple[ImageItemViewData, ...]
    annotations_by_image: dict[str, tuple[AnnotationItemViewData, ...]]
    models: tuple[ModelViewData, ...]
    available_datasets: tuple[DatasetCardViewData, ...] = ()


@dataclass(frozen=True, slots=True)
class UiCommand:
    """页面向后端边界发出的稳定操作意图。"""

    action_id: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class UiCommandResult:
    """命令结果只说明原型会话或待接入状态，不代表文件操作成功。"""

    status: CommandStatus
    message_key: str
    affected_id: str | None = None
    task_id: str | None = None
    data: dict[str, Any] = field(default_factory=dict)


class UiGateway(Protocol):
    """真实业务后续接入时必须实现的最小 UI 数据边界。"""

    @property
    def preview_mode(self) -> bool:
        """返回当前是否使用一次性内存演示数据。"""

    def home_snapshot(self) -> HomeSnapshot:
        """返回主页快照。"""

    def workspace_snapshot(self, dataset_id: str | None = None) -> WorkspaceSnapshot | None:
        """返回目标数据集工作台快照。"""

    def dispatch(self, command: UiCommand) -> UiCommandResult:
        """处理操作意图；原型阶段不得访问真实用户数据。"""

    def query_samples(self, dataset_id: str, **filters: Any) -> Any:
        """普通模式按页查询 SQLite；预览页面不会调用此接口。"""

    def load_image(self, dataset_id: str, sample_id: str) -> Any:
        """返回已验证图片资产，不向页面暴露受管路径。"""

    def load_thumbnail(self, dataset_id: str, sample_id: str) -> Any:
        """按需返回或重建缩略图资产。"""

    def task_snapshot(self, task_id: str) -> Any:
        """返回后台任务的不可变进度快照。"""

    def cancel_task(self, task_id: str) -> bool:
        """请求在下一个单样本原子边界取消任务。"""
