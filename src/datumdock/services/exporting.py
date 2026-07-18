"""确定性划分、YOLO Detection 与 X-AnyLabeling 交换目录导出。"""

from __future__ import annotations

import json
import os
import random
import shutil
import tempfile
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from datumdock.domain.models import DatasetSample, ExportRequest, LabelSet
from datumdock.services.labelme import LabelMeRepository


@dataclass(frozen=True)
class SplitResult:
    """导出预览与写入共用的稳定样本划分结果。"""

    train: tuple[DatasetSample, ...]
    val: tuple[DatasetSample, ...]
    test: tuple[DatasetSample, ...]


class SplitPlanner:
    """以重复内容和已确认近似组为不可拆分单元进行确定性划分。"""

    def plan(
        self,
        samples: Iterable[DatasetSample],
        split: tuple[int, int, int],
        seed: int,
        similarity_groups: dict[str, str] | None = None,
    ) -> SplitResult:
        """固定 ID 排序后打乱组顺序，保证同一输入和种子得到同一结果。"""

        if sum(split) != 100:
            raise ValueError("导出比例之和必须为 100")
        sample_list = sorted(samples, key=lambda item: item.id)
        groups: dict[str, list[DatasetSample]] = defaultdict(list)
        for sample in sample_list:
            group_id = (similarity_groups or {}).get(sample.id, f"hash:{sample.content_hash}")
            groups[group_id].append(sample)
        grouped = list(groups.values())
        random.Random(seed).shuffle(grouped)
        targets = [len(sample_list) * ratio / 100 for ratio in split]
        buckets: list[list[DatasetSample]] = [[], [], []]
        for group in grouped:
            # 优先放入相对目标缺口最大的集合，组约束导致偏差时保留真实偏差。
            target_index = max(
                range(3),
                key=lambda index: (targets[index] - len(buckets[index]), -len(buckets[index])),
            )
            buckets[target_index].extend(group)
        return SplitResult(
            train=tuple(buckets[0]),
            val=tuple(buckets[1]),
            test=tuple(buckets[2]),
        )


class YoloDetectionExporter:
    """把受管 PNG 和矩形标注复制到一次性 YOLO Detection 导出目录。"""

    def __init__(self, labelme_repository: LabelMeRepository | None = None) -> None:
        self.labelme_repository = labelme_repository or LabelMeRepository()

    def export(
        self,
        request: ExportRequest,
        label_set: LabelSet,
        split_result: SplitResult,
    ) -> Path:
        """先写临时目录并校验，再原子替换新的用户指定输出目录。"""

        output_root = Path(request.output_directory)
        if output_root.exists() and any(output_root.iterdir()):
            raise FileExistsError("导出目录已存在且非空，请选择新的空目录")
        parent = output_root.parent
        parent.mkdir(parents=True, exist_ok=True)
        temporary_root = Path(tempfile.mkdtemp(prefix=f".{output_root.name}-", dir=parent))
        try:
            for partition in ("train", "val", "test"):
                (temporary_root / "images" / partition).mkdir(parents=True)
                (temporary_root / "labels" / partition).mkdir(parents=True)
            for partition, samples in (
                ("train", split_result.train),
                ("val", split_result.val),
                ("test", split_result.test),
            ):
                for sample in samples:
                    self._write_sample(temporary_root, partition, sample, label_set)
            self._write_data_yaml(temporary_root, label_set)
            self._validate_export(temporary_root)
            if output_root.exists():
                output_root.rmdir()
            os.replace(temporary_root, output_root)
        except Exception:
            shutil.rmtree(temporary_root, ignore_errors=True)
            raise
        return output_root

    def _write_sample(
        self,
        root: Path,
        partition: str,
        sample: DatasetSample,
        label_set: LabelSet,
    ) -> None:
        """复制图片并将原图像素矩形转换为 YOLO 归一化坐标。"""

        image_destination = root / "images" / partition / sample.filename
        label_destination = root / "labels" / partition / f"{Path(sample.filename).stem}.txt"
        shutil.copy2(sample.image_path, image_destination)
        document = self.labelme_repository.load(
            Path(sample.annotation_path),
            sample.id,
            label_set,
            sample.filename,
            (sample.width, sample.height),
        )
        by_label = {label.id: label for label in label_set.labels}
        lines: list[str] = []
        for rectangle in document.rectangles:
            label = by_label.get(rectangle.label_id)
            if label is None:
                raise KeyError(f"样本 {sample.filename} 使用未知标签")
            x_center = (rectangle.x1 + rectangle.x2) / 2 / sample.width
            y_center = (rectangle.y1 + rectangle.y2) / 2 / sample.height
            width = rectangle.width / sample.width
            height = rectangle.height / sample.height
            values = (x_center, y_center, width, height)
            if not all(0 <= value <= 1 for value in values) or width <= 0 or height <= 0:
                raise ValueError(f"样本 {sample.filename} 含越界或无效面积标注")
            lines.append(f"{label.class_id} {x_center:.6f} {y_center:.6f} {width:.6f} {height:.6f}")
        label_destination.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")

    @staticmethod
    def _write_data_yaml(root: Path, label_set: LabelSet) -> None:
        """写入 Ultralytics 可直接读取的最小 data.yaml。"""

        sorted_labels = sorted(label_set.labels, key=lambda item: item.class_id)
        names = {label.class_id: label.name for label in sorted_labels}
        lines = [
            f"path: {root.as_posix()}",
            "train: images/train",
            "val: images/val",
            "test: images/test",
            "names:",
        ]
        lines.extend(f"  {class_id}: {name}" for class_id, name in names.items())
        (root / "data.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")

    @staticmethod
    def _validate_export(root: Path) -> None:
        """在暴露目标目录前验证所有图片和同名标签文件均已配对。"""

        for partition in ("train", "val", "test"):
            images = root / "images" / partition
            labels = root / "labels" / partition
            for image_path in images.glob("*.png"):
                label_path = labels / f"{image_path.stem}.txt"
                if not label_path.is_file():
                    raise RuntimeError(f"导出缺少标签文件: {image_path.name}")


class XAnyLabelingExporter:
    """生成可被 X-AnyLabeling 直接打开的 PNG + 同名 LabelMe JSON 目录。"""

    def __init__(self, labelme_repository: LabelMeRepository | None = None) -> None:
        self.labelme_repository = labelme_repository or LabelMeRepository()

    def export(
        self,
        output_directory: Path,
        samples: Iterable[DatasetSample],
        label_set: LabelSet,
    ) -> Path:
        """导出独立副本，不修改受管数据集池和内部模型元数据。"""

        if output_directory.exists() and any(output_directory.iterdir()):
            raise FileExistsError("交换目录已存在且非空")
        output_directory.parent.mkdir(parents=True, exist_ok=True)
        temporary_root = Path(
            tempfile.mkdtemp(prefix=f".{output_directory.name}-", dir=output_directory.parent)
        )
        try:
            for sample in samples:
                image_destination = temporary_root / sample.filename
                json_destination = temporary_root / f"{Path(sample.filename).stem}.json"
                shutil.copy2(sample.image_path, image_destination)
                document = self.labelme_repository.load(
                    Path(sample.annotation_path),
                    sample.id,
                    label_set,
                    sample.filename,
                    (sample.width, sample.height),
                )
                document.image_filename = sample.filename
                payload = self.labelme_repository.export_payload(document, label_set)
                json_destination.write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
            sorted_labels = sorted(label_set.labels, key=lambda item: item.class_id)
            labels = "\n".join(label.name for label in sorted_labels)
            (temporary_root / "labels.txt").write_text(
                labels + ("\n" if labels else ""),
                encoding="utf-8",
            )
            if output_directory.exists():
                output_directory.rmdir()
            os.replace(temporary_root, output_directory)
        except Exception:
            shutil.rmtree(temporary_root, ignore_errors=True)
            raise
        return output_directory
