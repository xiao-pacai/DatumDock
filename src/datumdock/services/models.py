"""本地 ONNX 与受支持 Ultralytics YOLO 模型的受管导入和预标注服务。"""

from __future__ import annotations

import importlib
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from datumdock.domain.models import DatasetSample, ModelEntry, Project, RectangleShape, ReviewStatus
from datumdock.services.dataset import DatasetPoolService
from datumdock.services.storage import read_json_model, write_json_atomic
from datumdock.services.workspace import WorkspaceService


@dataclass(frozen=True)
class BackendSelection:
    """推理后端选择结果及首次 CPU 回退时需要展示的解释。"""

    device: str
    using_gpu: bool
    reason: str


class InferenceBackendSelector:
    """优先选择经验证的 CUDA，缺失时明确回退 CPU 而不是静默降级。"""

    def select(self) -> BackendSelection:
        """检测 PyTorch CUDA；未安装推理依赖时也向 UI 返回可解释结果。"""

        try:
            torch = importlib.import_module("torch")
        except ModuleNotFoundError:
            return BackendSelection("cpu", False, "未安装 PyTorch，自动标注暂不可用")
        if bool(torch.cuda.is_available()):
            return BackendSelection("0", True, "检测到可用 NVIDIA GPU")
        return BackendSelection("cpu", False, "未检测到可用 GPU，将使用 CPU 推理")


class ModelImportService:
    """只在模型探测与本地校验成功后把二进制移入项目模型目录。"""

    def import_model(self, root: Path, project: Project, source_path: Path) -> ModelEntry:
        """根据后缀路由受支持适配器；未知 PT 绝不猜测结构。"""

        suffix = source_path.suffix.lower()
        if suffix == ".onnx":
            inspection = self._inspect_onnx(source_path)
        elif suffix == ".pt":
            inspection = self._inspect_ultralytics(source_path)
        else:
            raise ValueError("仅支持导入 ONNX 或经验证的 Ultralytics YOLO PT 模型")
        entry = ModelEntry(
            display_name=source_path.stem,
            filename=source_path.name,
            format=suffix.removeprefix("."),
            runtime_config=inspection["runtime_config"],
            model_classes=inspection["model_classes"],
            status="verified",
        )
        target_directory = WorkspaceService.project_path(root, project.id) / "models" / entry.id
        target_directory.mkdir(parents=True, exist_ok=False)
        try:
            shutil.copy2(source_path, target_directory / source_path.name)
            write_json_atomic(target_directory / "model.json", entry)
        except Exception:
            shutil.rmtree(target_directory, ignore_errors=True)
            raise
        return entry

    def list_models(self, root: Path, project: Project) -> list[ModelEntry]:
        """模型配置按项目目录枚举，其他项目的模型绝不参与当前列表。"""

        models_root = WorkspaceService.project_path(root, project.id) / "models"
        entries: list[ModelEntry] = []
        for metadata_path in models_root.glob("*/model.json"):
            entries.append(read_json_model(metadata_path, ModelEntry))
        return sorted(entries, key=lambda item: item.display_name.casefold())

    def save_model(self, root: Path, project: Project, entry: ModelEntry) -> None:
        """保存类别映射等模型元数据，不触碰已验证的模型二进制。"""

        metadata_path = (
            WorkspaceService.project_path(root, project.id) / "models" / entry.id / "model.json"
        )
        if not metadata_path.is_file():
            raise FileNotFoundError("项目中找不到该模型配置")
        write_json_atomic(metadata_path, entry)

    def delete_model(self, root: Path, project: Project, entry: ModelEntry) -> None:
        """仅删除当前项目模型目录，项目数据集池和其他项目模型不会受影响。"""

        model_directory = WorkspaceService.project_path(root, project.id) / "models" / entry.id
        if not model_directory.is_dir():
            raise FileNotFoundError("项目中找不到该模型目录")
        shutil.rmtree(model_directory)

    @staticmethod
    def _inspect_onnx(source_path: Path) -> dict[str, Any]:
        """读取 ONNX Runtime 输入输出与 metadata，不依赖不安全的模型反序列化。"""

        try:
            onnxruntime = importlib.import_module("onnxruntime")
        except ModuleNotFoundError as error:
            raise RuntimeError("缺少 ONNX Runtime，请安装 inference 依赖后再导入模型") from error
        session = onnxruntime.InferenceSession(str(source_path), providers=["CPUExecutionProvider"])
        inputs = [
            {"name": value.name, "shape": list(value.shape), "type": value.type}
            for value in session.get_inputs()
        ]
        outputs = [
            {"name": value.name, "shape": list(value.shape), "type": value.type}
            for value in session.get_outputs()
        ]
        metadata = session.get_modelmeta().custom_metadata_map
        classes = _parse_model_classes(metadata)
        return {
            "runtime_config": {
                "inputs": inputs,
                "outputs": outputs,
                "metadata": metadata,
                "decoder": "ultralytics_yolo_required",
            },
            "model_classes": classes,
        }

    @staticmethod
    def _inspect_ultralytics(source_path: Path) -> dict[str, Any]:
        """仅利用 Ultralytics 官方加载器验证 PT，失败即判定为当前不支持。"""

        try:
            ultralytics = importlib.import_module("ultralytics")
        except ModuleNotFoundError as error:
            raise RuntimeError("缺少 Ultralytics，请安装 inference 依赖后再导入模型") from error
        try:
            model = ultralytics.YOLO(str(source_path))
            names = model.names
        except Exception as error:
            raise ValueError("该 PT 文件不是当前可安全识别的 Ultralytics YOLO 检测模型") from error
        classes = (
            [str(names[index]) for index in sorted(names)]
            if isinstance(names, dict)
            else list(names)
        )
        return {
            "runtime_config": {"decoder": "ultralytics_yolo", "task": str(model.task)},
            "model_classes": classes,
        }


class AutoAnnotationService:
    """将模型预测作为可审阅建议追加到当前样本，绝不替换人工矩形框。"""

    def __init__(self, pool_service: DatasetPoolService | None = None) -> None:
        self.pool_service = pool_service or DatasetPoolService()
        self.backend_selector = InferenceBackendSelector()

    def annotate_sample(
        self,
        root: Path,
        project: Project,
        sample: DatasetSample,
        model_entry: ModelEntry,
        *,
        confidence: float = 0.25,
        iou: float = 0.7,
    ) -> BackendSelection:
        """使用明确映射的模型类别生成框，并把图片状态设为待人工复核。"""

        if model_entry.status != "verified":
            raise ValueError("模型尚未通过本地校验，不能运行自动标注")
        try:
            ultralytics = importlib.import_module("ultralytics")
        except ModuleNotFoundError as error:
            raise RuntimeError("缺少 Ultralytics，无法运行自动标注") from error
        model_path = (
            WorkspaceService.project_path(root, project.id)
            / "models"
            / model_entry.id
            / model_entry.filename
        )
        if not model_path.is_file():
            raise FileNotFoundError("模型二进制已移除，请重新导入模型")
        backend = self.backend_selector.select()
        model = ultralytics.YOLO(str(model_path))
        results = model.predict(
            source=str(sample.image_path),
            conf=confidence,
            iou=iou,
            device=backend.device,
            verbose=False,
        )
        document = self.pool_service.load_document(sample, project)
        for result in results:
            boxes = getattr(result, "boxes", None)
            if boxes is None:
                continue
            for coordinates, class_index, score in zip(
                boxes.xyxy.tolist(), boxes.cls.tolist(), boxes.conf.tolist(), strict=False
            ):
                model_class = str(int(class_index))
                label_id = model_entry.label_mapping.get(model_class)
                if label_id is None:
                    continue
                document.rectangles.append(
                    RectangleShape(
                        label_id=label_id,
                        x1=float(coordinates[0]),
                        y1=float(coordinates[1]),
                        x2=float(coordinates[2]),
                        y2=float(coordinates[3]),
                        source_model_id=model_entry.id,
                        confidence=float(score),
                    )
                )
        self.pool_service.save_document(
            root,
            project,
            sample,
            document,
            review_status=ReviewStatus.AUTO_PENDING_REVIEW,
        )
        return backend


def _parse_model_classes(metadata: dict[str, str]) -> list[str]:
    """尽量读取 ONNX metadata 中的类别名；缺失时让用户在 UI 配置而非猜测。"""

    for key in ("names", "classes", "class_names"):
        value = metadata.get(key)
        if not value:
            continue
        try:
            parsed = json.loads(value)
        except ValueError:
            return [item.strip() for item in value.split(",") if item.strip()]
        if isinstance(parsed, dict):
            return [str(parsed[index]) for index in sorted(parsed, key=lambda item: int(item))]
        if isinstance(parsed, list):
            return [str(item) for item in parsed]
    return []
