"""普通安全空模式与界面预览模式的数据网关。"""

from __future__ import annotations

from dataclasses import replace

from datumdock.ui.prototype_models import (
    AnnotationItemViewData,
    CommandStatus,
    DatasetCardViewData,
    DatasetHealth,
    HomeSnapshot,
    ImageItemViewData,
    ImageStatus,
    LabelViewData,
    ModelViewData,
    UiCommand,
    UiCommandResult,
    WorkspaceSnapshot,
)


class UnavailableGateway:
    """资料库初始化失败时提供安全空主页并拒绝全部副作用。"""

    preview_mode = False

    def __init__(self, error_key: str = "toast.library_unavailable") -> None:
        self.initial_error_key = error_key

    def home_snapshot(self) -> HomeSnapshot:
        """返回没有演示内容的主页。"""

        return HomeSnapshot((), quick_start_completed=0)

    def workspace_snapshot(self, dataset_id: str | None = None) -> WorkspaceSnapshot | None:
        """资料库不可用时没有可安全打开的工作台数据。"""

        return None

    def dispatch(self, command: UiCommand) -> UiCommandResult:
        """统一拒绝副作用，避免原型按钮误改用户数据。"""

        return UiCommandResult(CommandStatus.NOT_CONNECTED, "toast.not_connected")


class PreviewGateway:
    """只在内存中维护演示数据，进程退出后自然丢弃。"""

    preview_mode = True

    def __init__(self) -> None:
        self._datasets = list(_demo_datasets())
        self._workspace = _demo_workspace(self._datasets[0], tuple(self._datasets))

    def home_snapshot(self) -> HomeSnapshot:
        """返回当前预览会话的数据集卡片。"""

        return HomeSnapshot(tuple(self._datasets), quick_start_completed=2)

    def workspace_snapshot(self, dataset_id: str | None = None) -> WorkspaceSnapshot | None:
        """切换演示数据集时复用同一套丰富工作台数据。"""

        if dataset_id:
            dataset = next((item for item in self._datasets if item.id == dataset_id), None)
            if dataset is not None:
                return replace(
                    self._workspace,
                    dataset=dataset,
                    available_datasets=tuple(item for item in self._datasets if not item.archived),
                )
        return replace(
            self._workspace,
            available_datasets=tuple(item for item in self._datasets if not item.archived),
        )

    def dispatch(self, command: UiCommand) -> UiCommandResult:
        """仅处理适合视觉检查的轻量内存动作。"""

        if command.action_id in {"dataset.create", "dataset.create_from_template"}:
            name = str(command.payload.get("name", "")).strip()
            if not name:
                return UiCommandResult(CommandStatus.INVALID, "toast.invalid_name")
            duplicate = any(
                item.name.casefold() == name.casefold() and not item.archived
                for item in self._datasets
            )
            if duplicate:
                return UiCommandResult(CommandStatus.INVALID, "toast.duplicate_dataset_name")
            identifier = f"preview-{len(self._datasets) + 1}"
            self._datasets.insert(
                0,
                DatasetCardViewData(
                    identifier,
                    name,
                    str(command.payload.get("description", "")),
                    0,
                    0,
                    0,
                    "刚刚",
                    len(self._datasets) + 7,
                ),
            )
            return UiCommandResult(
                CommandStatus.PREVIEW_APPLIED,
                "toast.preview_applied",
                affected_id=identifier,
            )
        dataset_id = str(command.payload.get("dataset_id", ""))
        index = next(
            (position for position, item in enumerate(self._datasets) if item.id == dataset_id),
            None,
        )
        if index is not None and command.action_id == "dataset.rename":
            name = str(command.payload.get("name", "")).strip()
            if not name:
                return UiCommandResult(CommandStatus.INVALID, "toast.invalid_name")
            self._datasets[index] = replace(self._datasets[index], name=name)
            return UiCommandResult(
                CommandStatus.PREVIEW_APPLIED,
                "toast.preview_applied",
                affected_id=dataset_id,
            )
        if index is not None and command.action_id in {"dataset.archive", "dataset.restore"}:
            archived = command.action_id == "dataset.archive"
            self._datasets[index] = replace(self._datasets[index], archived=archived)
            return UiCommandResult(
                CommandStatus.PREVIEW_APPLIED,
                "toast.preview_applied",
                affected_id=dataset_id,
            )
        return UiCommandResult(CommandStatus.PREVIEW_APPLIED, "toast.preview_applied")


def _demo_datasets() -> tuple[DatasetCardViewData, ...]:
    """构造能覆盖正常、加载和损坏状态的主页数据。"""

    return (
        DatasetCardViewData(
            "factory-parts",
            "工厂零件检测",
            "用于零件、缺口和表面瑕疵的矩形框数据集",
            1864,
            34,
            72,
            "今天 14:32",
            1,
        ),
        DatasetCardViewData(
            "recycle-items",
            "可回收物分类",
            "纸杯、塑料瓶和金属罐的现场采集图片",
            928,
            18,
            46,
            "昨天 19:08",
            2,
        ),
        DatasetCardViewData(
            "warehouse",
            "仓库安全检查",
            "正在更新缩略图索引",
            423,
            12,
            31,
            "3 天前",
            3,
            DatasetHealth.LOADING,
        ),
        DatasetCardViewData(
            "damaged-demo",
            "旧实验数据",
            "元数据无法完整读取，可打开诊断页面",
            0,
            0,
            0,
            "2 周前",
            4,
            DatasetHealth.DAMAGED,
        ),
    )


def _demo_workspace(
    dataset: DatasetCardViewData,
    available_datasets: tuple[DatasetCardViewData, ...],
) -> WorkspaceSnapshot:
    """构造包含三十多个标签体验所需信息密度的工作台快照。"""

    labels = (
        LabelViewData(
            "label-part",
            0,
            "metal_part",
            "金属零件",
            "待检测的主要金属构件",
            ("零件",),
            "#73B9D2",
            842,
        ),
        LabelViewData(
            "label-scratch",
            1,
            "surface_scratch",
            "表面划痕",
            "细长或片状的表面损伤",
            ("刮痕",),
            "#F2A36F",
            318,
        ),
        LabelViewData(
            "label-hole",
            2,
            "mounting_hole",
            "安装孔",
            "设计预留的圆形或椭圆孔位",
            ("孔位",),
            "#7BBF9A",
            506,
        ),
        LabelViewData(
            "label-burr",
            3,
            "edge_burr",
            "边缘毛刺",
            "加工边缘残留的突出材料",
            ("飞边",),
            "#C28CC8",
            127,
        ),
        LabelViewData(
            "label-stain",
            4,
            "oil_stain",
            "油污",
            "附着在表面的油性污染区域",
            ("污渍",),
            "#D9B65D",
            74,
        ),
    )
    images = (
        ImageItemViewData(
            "img-1",
            "factory_part_000231.png",
            ImageStatus.PENDING,
            1280,
            853,
            1,
            4,
            ("label-part", "label-hole", "label-scratch"),
        ),
        ImageItemViewData(
            "img-2",
            "factory_part_000232.png",
            ImageStatus.COMPLETED,
            1280,
            853,
            2,
            3,
            ("label-part", "label-hole"),
        ),
        ImageItemViewData(
            "img-3", "factory_part_000233.png", ImageStatus.UNLABELED, 1280, 853, 3, 0
        ),
        ImageItemViewData(
            "img-4",
            "factory_part_000234.png",
            ImageStatus.ISSUE,
            1280,
            853,
            4,
            2,
            ("label-part", "label-burr"),
        ),
        ImageItemViewData(
            "img-5", "factory_part_000235.png", ImageStatus.NEGATIVE, 1280, 853, 5, 0
        ),
        ImageItemViewData("img-6", "factory_part_000236.png", ImageStatus.ERROR, 1280, 853, 6, 0),
    )
    annotations = {
        "img-1": (
            AnnotationItemViewData("shape-1", "label-part", 155, 145, 1005, 710, 0.96),
            AnnotationItemViewData("shape-2", "label-hole", 310, 282, 430, 410, 0.91),
            AnnotationItemViewData("shape-3", "label-hole", 760, 275, 885, 405, 0.89),
            AnnotationItemViewData("shape-4", "label-scratch", 515, 430, 715, 505, 0.78),
        ),
        "img-2": (
            AnnotationItemViewData("shape-5", "label-part", 170, 150, 990, 705),
            AnnotationItemViewData("shape-6", "label-hole", 330, 290, 440, 402),
            AnnotationItemViewData("shape-7", "label-hole", 755, 282, 870, 398),
        ),
        "img-4": (
            AnnotationItemViewData("shape-8", "label-part", 180, 160, 980, 700),
            AnnotationItemViewData("shape-9", "label-burr", 930, 350, 1010, 475),
        ),
    }
    models = (
        ModelViewData("model-1", "Parts YOLO11", "PT", "640 × 640", 34, "CUDA", "preview.ready"),
        ModelViewData(
            "model-2",
            "Defect Detector",
            "ONNX",
            "1024 × 1024",
            8,
            "CPU",
            "preview.review",
        ),
        ModelViewData("model-3", "Legacy Checkpoint", "PT", "—", 0, "—", "value.unsupported"),
    )
    return WorkspaceSnapshot(dataset, labels, images, annotations, models, available_datasets)
