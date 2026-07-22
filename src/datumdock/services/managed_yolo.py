"""受管数据集的确定性 YOLO Detection 预检、划分、导出与回读验证。"""

from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import stat
import tempfile
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Protocol

import yaml
from PIL import Image

from datumdock.domain.models import (
    AnnotationDocument,
    AnnotationState,
    DatasetSample,
    Label,
    LabelSet,
    LabelStatus,
    RectangleShape,
    ReviewStatus,
    SampleHealth,
    SimilarityStatus,
)
from datumdock.services.dataset_library import DatasetLibraryService
from datumdock.services.labelme import LabelMeError, LabelMeRepository
from datumdock.services.sample_repository import DatasetSampleRepository


class YoloExportError(RuntimeError):
    """导出无法在保持数据含义和文件一致性的前提下继续。"""


class ExportScope(StrEnum):
    """本次请求的候选范围；筛选和显式选择均由稳定样本 ID 快照表示。"""

    ALL = "all"
    FILTERED = "filtered"
    SELECTED = "selected"


class ExportIssueSeverity(StrEnum):
    """错误阻止导出，警告只要求用户理解风险。"""

    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class SplitRatios:
    """train 必须存在；val/test 为零时仍创建空目录。"""

    train: int = 80
    val: int = 10
    test: int = 10

    def __post_init__(self) -> None:
        values = (self.train, self.val, self.test)
        if any(value < 0 for value in values) or sum(values) != 100:
            raise ValueError("训练、验证、测试比例必须为非负整数且总和为 100")
        if self.train <= 0:
            raise ValueError("训练集比例必须大于 0")

    def as_tuple(self) -> tuple[int, int, int]:
        return self.train, self.val, self.test


@dataclass(frozen=True, slots=True)
class YoloExportRequest:
    """仅在当前导出会话存活的一次性请求。"""

    dataset_id: str
    output_directory: Path
    scope: ExportScope = ExportScope.ALL
    sample_ids: tuple[str, ...] = ()
    ratios: SplitRatios = field(default_factory=SplitRatios)
    seed: int = 42
    include_completed_negatives: bool = False
    include_unreviewed_negatives: bool = False
    include_pending_review: bool = False


@dataclass(frozen=True, slots=True)
class ExportRectangleSnapshot:
    """预检时固定的矩形事实，输出阶段不重新猜测标签映射。"""

    shape_id: str
    label_id: str
    x1: float
    y1: float
    x2: float
    y2: float


@dataclass(frozen=True, slots=True)
class ExportCandidateSnapshot:
    """不暴露绝对受管路径的不可变样本快照。"""

    sample_id: str
    filename: str
    width: int
    height: int
    content_hash: str
    image_sha256: str
    image_size: int
    annotation_sha256: str
    annotation_version: int
    review_status: ReviewStatus | None
    rectangles: tuple[ExportRectangleSnapshot, ...]

    @property
    def negative(self) -> bool:
        return not self.rectangles


@dataclass(frozen=True, slots=True)
class ExportValidationIssue:
    """预检问题可定位到样本；领域文本在语言切换时不被改写。"""

    severity: ExportIssueSeverity
    code: str
    message: str
    sample_id: str = ""
    filename: str = ""


@dataclass(frozen=True, slots=True)
class SplitGroup:
    """完全重复和确认相似关系合并后的最小划分单位。"""

    id: str
    sample_ids: tuple[str, ...]
    class_image_counts: tuple[tuple[int, int], ...]
    class_box_counts: tuple[tuple[int, int], ...]
    negative_count: int


@dataclass(frozen=True, slots=True)
class SplitBucketStatistics:
    """一个集合的目标和实际分布。"""

    name: str
    target_samples: int
    sample_count: int
    rectangle_count: int
    negative_count: int
    pending_review_count: int
    class_image_counts: tuple[tuple[int, int], ...]
    class_box_counts: tuple[tuple[int, int], ...]


@dataclass(frozen=True, slots=True)
class SplitStatistics:
    """向导展示的划分数量、标签分布和风险摘要。"""

    buckets: tuple[SplitBucketStatistics, ...]
    exact_duplicate_group_count: int
    confirmed_similarity_group_count: int
    pending_similarity_group_count: int
    largest_group_size: int
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SplitPlan:
    """稳定计划不保存到数据集，只由预检任务持有。"""

    train: tuple[str, ...]
    val: tuple[str, ...]
    test: tuple[str, ...]
    groups: tuple[SplitGroup, ...]
    fingerprint: str
    algorithm_version: str = "group-stratified-v1"

    def sample_ids(self, split_name: str) -> tuple[str, ...]:
        return {
            "train": self.train,
            "val": self.val,
            "test": self.test,
        }[split_name]


@dataclass(frozen=True, slots=True)
class YoloExportPreflight:
    """完整预检；候选和计划发生变化后不得继续提交。"""

    request: YoloExportRequest
    candidates: tuple[ExportCandidateSnapshot, ...]
    issues: tuple[ExportValidationIssue, ...]
    plan: SplitPlan | None
    statistics: SplitStatistics | None
    label_set_revision: int
    label_training_signature: str
    snapshot_fingerprint: str
    estimated_bytes: int
    excluded_counts: tuple[tuple[str, int], ...] = ()

    @property
    def can_export(self) -> bool:
        return self.plan is not None and not any(
            issue.severity == ExportIssueSeverity.ERROR for issue in self.issues
        )


@dataclass(slots=True)
class YoloExportReport:
    """任务报告只在进程内存中存在，不写入数据集或输出目录。"""

    output_directory: Path
    image_count: int = 0
    label_file_count: int = 0
    rectangle_count: int = 0
    negative_count: int = 0
    split_counts: dict[str, int] = field(default_factory=dict)
    plan_fingerprint: str = ""
    directory_sha256: str = ""
    cancelled: bool = False
    warnings: list[str] = field(default_factory=list)
    cleanup_path: str = ""


class DatasetExporter(Protocol):
    """后续格式复用候选快照和 SplitPlan，不改动数据集池。"""

    def export(
        self,
        preflight: YoloExportPreflight,
        *,
        progress: Callable[[int, int, str], None] | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> YoloExportReport: ...


class ExporterRegistry:
    """按稳定格式 ID 注册导出器。"""

    def __init__(self) -> None:
        self._exporters: dict[str, DatasetExporter] = {}

    def register(self, format_id: str, exporter: DatasetExporter) -> None:
        if format_id in self._exporters:
            raise ValueError(f"导出格式已经注册: {format_id}")
        self._exporters[format_id] = exporter

    def get(self, format_id: str) -> DatasetExporter:
        try:
            return self._exporters[format_id]
        except KeyError as error:
            raise KeyError(f"未知导出格式: {format_id}") from error


class _DisjointSet:
    """合并重叠关系，避免单样本映射覆盖间接相似约束。"""

    def __init__(self, identifiers: Iterable[str]) -> None:
        self.parent = {identifier: identifier for identifier in identifiers}

    def find(self, identifier: str) -> str:
        parent = self.parent[identifier]
        if parent != identifier:
            self.parent[identifier] = self.find(parent)
        return self.parent[identifier]

    def union(self, left: str, right: str) -> None:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root == right_root:
            return
        first, second = sorted((left_root, right_root))
        self.parent[second] = first


class DeterministicSplitPlanner:
    """以连通分量为原子单位执行版本化的弱多标签分层。"""

    ALGORITHM_VERSION = "group-stratified-v1"
    SPLIT_NAMES = ("train", "val", "test")

    def plan(
        self,
        candidates: Sequence[ExportCandidateSnapshot],
        label_set: LabelSet,
        ratios: SplitRatios,
        seed: int,
        similarity_groups: Sequence[tuple[str, Sequence[str]]],
    ) -> tuple[SplitPlan, SplitStatistics]:
        if not candidates:
            raise YoloExportError("没有符合条件的导出样本")
        by_id = {candidate.sample_id: candidate for candidate in candidates}
        if len(by_id) != len(candidates):
            raise YoloExportError("导出候选包含重复样本 ID")
        labels = {label.id: label for label in label_set.labels}
        disjoint = _DisjointSet(by_id)
        exact_groups: dict[str, list[str]] = defaultdict(list)
        for candidate in candidates:
            exact_groups[candidate.content_hash].append(candidate.sample_id)
        for members in exact_groups.values():
            self._union_members(disjoint, members)
        confirmed_count = 0
        for _group_id, members in similarity_groups:
            scoped = sorted({member for member in members if member in by_id})
            if len(scoped) > 1:
                confirmed_count += 1
                self._union_members(disjoint, scoped)
        components: dict[str, list[str]] = defaultdict(list)
        for sample_id in sorted(by_id):
            components[disjoint.find(sample_id)].append(sample_id)
        groups = tuple(
            self._build_group(member_ids, by_id, labels)
            for member_ids in sorted(components.values(), key=lambda value: tuple(value))
        )
        positive_splits = [index for index, ratio in enumerate(ratios.as_tuple()) if ratio > 0]
        if len(groups) < len(positive_splits):
            raise YoloExportError(
                "不可拆分组数量不足以填充所有正比例集合，请把 val/test 设为 0 或增加样本"
            )
        targets = self._largest_remainder_targets(len(candidates), ratios.as_tuple())
        assignments = self._assign(groups, ratios, targets, seed)
        split_ids = tuple(
            tuple(sample_id for group in assignments[index] for sample_id in group.sample_ids)
            for index in range(3)
        )
        for index in positive_splits:
            if not split_ids[index]:
                raise YoloExportError("正比例集合没有获得样本，请调整比例或相似组")
        fingerprint = self._fingerprint(split_ids, groups, ratios, seed)
        plan = SplitPlan(
            train=split_ids[0],
            val=split_ids[1],
            test=split_ids[2],
            groups=groups,
            fingerprint=fingerprint,
        )
        statistics = self._statistics(
            plan,
            by_id,
            labels,
            targets,
            exact_groups,
            confirmed_count,
        )
        return plan, statistics

    @staticmethod
    def _union_members(disjoint: _DisjointSet, members: Sequence[str]) -> None:
        if not members:
            return
        first = members[0]
        for member in members[1:]:
            disjoint.union(first, member)

    @staticmethod
    def _build_group(
        member_ids: Sequence[str],
        candidates: Mapping[str, ExportCandidateSnapshot],
        labels: Mapping[str, Label],
    ) -> SplitGroup:
        class_images: Counter[int] = Counter()
        class_boxes: Counter[int] = Counter()
        negative_count = 0
        for sample_id in member_ids:
            candidate = candidates[sample_id]
            if candidate.negative:
                negative_count += 1
            image_classes: set[int] = set()
            for rectangle in candidate.rectangles:
                label = labels.get(rectangle.label_id)
                if label is None:
                    raise YoloExportError(f"候选引用未知标签: {rectangle.label_id}")
                class_id = label.class_id
                class_boxes[class_id] += 1
                image_classes.add(class_id)
            class_images.update(image_classes)
        ordered = tuple(sorted(member_ids))
        identifier = hashlib.sha256("\n".join(ordered).encode()).hexdigest()
        return SplitGroup(
            identifier,
            ordered,
            tuple(sorted(class_images.items())),
            tuple(sorted(class_boxes.items())),
            negative_count,
        )

    @staticmethod
    def _largest_remainder_targets(
        total: int,
        ratios: tuple[int, int, int],
    ) -> tuple[int, int, int]:
        raw = [total * ratio / 100 for ratio in ratios]
        base = [math.floor(value) for value in raw]
        remaining = total - sum(base)
        order = sorted(range(3), key=lambda index: (-(raw[index] - base[index]), index))
        for index in order[:remaining]:
            base[index] += 1
        return base[0], base[1], base[2]

    def _assign(
        self,
        groups: Sequence[SplitGroup],
        ratios: SplitRatios,
        targets: tuple[int, int, int],
        seed: int,
    ) -> list[list[SplitGroup]]:
        global_images: Counter[int] = Counter()
        global_boxes: Counter[int] = Counter()
        global_negatives = 0
        for group in groups:
            global_images.update(dict(group.class_image_counts))
            global_boxes.update(dict(group.class_box_counts))
            global_negatives += group.negative_count

        def order_key(group: SplitGroup) -> tuple[int, int, str]:
            present = dict(group.class_image_counts)
            rarity = min((global_images[class_id] for class_id in present), default=10**9)
            digest = hashlib.sha256(
                f"{seed}:{self.ALGORITHM_VERSION}:{group.id}".encode()
            ).hexdigest()
            return rarity, -len(group.sample_ids), digest

        ordered_groups = sorted(groups, key=order_key)
        buckets: list[list[SplitGroup]] = [[], [], []]
        sample_counts = [0, 0, 0]
        image_counts = [Counter(), Counter(), Counter()]
        box_counts = [Counter(), Counter(), Counter()]
        negative_counts = [0, 0, 0]
        positive = [index for index, ratio in enumerate(ratios.as_tuple()) if ratio > 0]
        initial_order = sorted(positive, key=lambda index: (-targets[index], index))
        for group, split_index in zip(
            ordered_groups[: len(initial_order)],
            initial_order,
            strict=True,
        ):
            self._place(
                group,
                split_index,
                buckets,
                sample_counts,
                image_counts,
                box_counts,
                negative_counts,
            )
        for group in ordered_groups[len(initial_order) :]:
            choices: list[tuple[float, str, int]] = []
            for split_index in positive:
                cost = self._projected_cost(
                    group,
                    split_index,
                    sample_counts,
                    image_counts,
                    box_counts,
                    negative_counts,
                    targets,
                    global_images,
                    global_boxes,
                    global_negatives,
                    ratios.as_tuple(),
                )
                tie = hashlib.sha256(
                    f"{seed}:{group.id}:{self.SPLIT_NAMES[split_index]}".encode()
                ).hexdigest()
                choices.append((cost, tie, split_index))
            split_index = min(choices)[2]
            self._place(
                group,
                split_index,
                buckets,
                sample_counts,
                image_counts,
                box_counts,
                negative_counts,
            )
        return buckets

    @staticmethod
    def _place(
        group: SplitGroup,
        split_index: int,
        buckets: list[list[SplitGroup]],
        sample_counts: list[int],
        image_counts: list[Counter[int]],
        box_counts: list[Counter[int]],
        negative_counts: list[int],
    ) -> None:
        buckets[split_index].append(group)
        sample_counts[split_index] += len(group.sample_ids)
        image_counts[split_index].update(dict(group.class_image_counts))
        box_counts[split_index].update(dict(group.class_box_counts))
        negative_counts[split_index] += group.negative_count

    @staticmethod
    def _projected_cost(
        group: SplitGroup,
        split_index: int,
        sample_counts: Sequence[int],
        image_counts: Sequence[Counter[int]],
        box_counts: Sequence[Counter[int]],
        negative_counts: Sequence[int],
        targets: Sequence[int],
        global_images: Counter[int],
        global_boxes: Counter[int],
        global_negatives: int,
        ratios: Sequence[int],
    ) -> float:
        group_images = dict(group.class_image_counts)
        group_boxes = dict(group.class_box_counts)
        projected_samples = list(sample_counts)
        projected_samples[split_index] += len(group.sample_ids)
        sample_cost = sum(
            abs(projected_samples[index] - targets[index]) / max(1, targets[index])
            for index in range(3)
            if ratios[index] > 0
        )
        image_cost = 0.0
        for class_id, total in global_images.items():
            for index in range(3):
                if ratios[index] <= 0:
                    continue
                actual = image_counts[index][class_id]
                if index == split_index:
                    actual += group_images.get(class_id, 0)
                target = total * ratios[index] / 100
                image_cost += abs(actual - target) / max(1, total)
        box_cost = 0.0
        for class_id, total in global_boxes.items():
            for index in range(3):
                if ratios[index] <= 0:
                    continue
                actual = box_counts[index][class_id]
                if index == split_index:
                    actual += group_boxes.get(class_id, 0)
                target = total * ratios[index] / 100
                box_cost += abs(actual - target) / max(1, total)
        negative_cost = 0.0
        for index in range(3):
            if ratios[index] <= 0:
                continue
            actual = negative_counts[index]
            if index == split_index:
                actual += group.negative_count
            target = global_negatives * ratios[index] / 100
            negative_cost += abs(actual - target) / max(1, global_negatives)
        return sample_cost * 8 + image_cost * 2 + box_cost + negative_cost

    def _fingerprint(
        self,
        split_ids: Sequence[Sequence[str]],
        groups: Sequence[SplitGroup],
        ratios: SplitRatios,
        seed: int,
    ) -> str:
        payload = {
            "version": self.ALGORITHM_VERSION,
            "seed": seed,
            "ratios": ratios.as_tuple(),
            "splits": {name: list(split_ids[index]) for index, name in enumerate(self.SPLIT_NAMES)},
            "groups": [(group.id, group.sample_ids) for group in groups],
        }
        return _json_digest(payload)

    def _statistics(
        self,
        plan: SplitPlan,
        candidates: Mapping[str, ExportCandidateSnapshot],
        labels: Mapping[str, Label],
        targets: tuple[int, int, int],
        exact_groups: Mapping[str, Sequence[str]],
        confirmed_count: int,
    ) -> SplitStatistics:
        buckets: list[SplitBucketStatistics] = []
        warnings: list[str] = []
        global_images: Counter[int] = Counter()
        for index, name in enumerate(self.SPLIT_NAMES):
            class_images: Counter[int] = Counter()
            class_boxes: Counter[int] = Counter()
            rectangle_count = 0
            negative_count = 0
            pending_count = 0
            for sample_id in plan.sample_ids(name):
                candidate = candidates[sample_id]
                if candidate.negative:
                    negative_count += 1
                if candidate.review_status == ReviewStatus.PENDING_REVIEW:
                    pending_count += 1
                image_classes: set[int] = set()
                for rectangle in candidate.rectangles:
                    label = labels[rectangle.label_id]
                    class_id = label.class_id
                    class_boxes[class_id] += 1
                    image_classes.add(class_id)
                    rectangle_count += 1
                class_images.update(image_classes)
            global_images.update(class_images)
            actual = len(plan.sample_ids(name))
            total_samples = sum(len(group.sample_ids) for group in plan.groups)
            if abs(actual - targets[index]) > max(1, total_samples * 0.05):
                warnings.append(f"{name} 实际数量与目标比例存在明显偏差")
            buckets.append(
                SplitBucketStatistics(
                    name,
                    targets[index],
                    actual,
                    rectangle_count,
                    negative_count,
                    pending_count,
                    tuple(sorted(class_images.items())),
                    tuple(sorted(class_boxes.items())),
                )
            )
        train_images = dict(buckets[0].class_image_counts)
        for label in labels.values():
            class_id = label.class_id
            if global_images[class_id] and not train_images.get(class_id):
                warnings.append(f"训练集缺少类别 {label.name}")
            if 0 < global_images[class_id] < 3:
                warnings.append(f"类别 {label.name} 仅出现在少量图片中")
        return SplitStatistics(
            tuple(buckets),
            sum(len(members) > 1 for members in exact_groups.values()),
            confirmed_count,
            0,
            max((len(group.sample_ids) for group in plan.groups), default=0),
            tuple(dict.fromkeys(warnings)),
        )


class YoloExportValidator:
    """对临时输出执行完整回读，验证成功前不暴露最终目录。"""

    SPLIT_NAMES = ("train", "val", "test")

    def validate(
        self,
        root: Path,
        preflight: YoloExportPreflight,
        label_set: LabelSet,
    ) -> None:
        if preflight.plan is None:
            raise YoloExportError("导出计划不存在")
        yaml_path = root / "data.yaml"
        try:
            data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as error:
            raise YoloExportError(f"data.yaml 无法读取: {error}") from error
        if not isinstance(data, dict):
            raise YoloExportError("data.yaml 根节点必须是对象")
        expected_paths = {
            "train": "images/train",
            "val": "images/val" if preflight.request.ratios.val else None,
            "test": "images/test" if preflight.request.ratios.test else None,
        }
        if any(data.get(name) != value for name, value in expected_paths.items()):
            raise YoloExportError("data.yaml 划分路径与本次比例不一致")
        if set(path.name for path in root.iterdir()) != {"data.yaml", "images", "labels"}:
            raise YoloExportError("导出根目录包含未声明文件")
        for kind in ("images", "labels"):
            directory = root / kind
            if not directory.is_dir() or {path.name for path in directory.iterdir()} != set(
                self.SPLIT_NAMES
            ):
                raise YoloExportError(f"{kind} 目录的划分结构不完整")
        expected_names = {
            label.class_id: label.name
            for label in sorted(label_set.labels, key=lambda item: item.class_id)
        }
        if data.get("nc") != len(expected_names) or data.get("names") != expected_names:
            raise YoloExportError("data.yaml 类别映射与标签集不一致")
        candidates = {candidate.sample_id: candidate for candidate in preflight.candidates}
        assigned: dict[str, str] = {}
        for split_name in self.SPLIT_NAMES:
            expected_ids = preflight.plan.sample_ids(split_name)
            expected_names_in_split = {candidates[sample_id].filename for sample_id in expected_ids}
            image_directory = root / "images" / split_name
            label_directory = root / "labels" / split_name
            if not image_directory.is_dir() or not label_directory.is_dir():
                raise YoloExportError(f"{split_name} 目录结构不完整")
            if any(path.is_dir() for path in image_directory.iterdir()) or any(
                path.is_dir() for path in label_directory.iterdir()
            ):
                raise YoloExportError(f"{split_name} 包含意外子目录")
            if any(path.suffix.casefold() != ".png" for path in image_directory.iterdir()):
                raise YoloExportError(f"{split_name} 图片目录包含非 PNG 文件")
            if any(path.suffix.casefold() != ".txt" for path in label_directory.iterdir()):
                raise YoloExportError(f"{split_name} 标签目录包含非 TXT 文件")
            actual_images = {path.name for path in image_directory.glob("*.png")}
            actual_labels = {path.stem for path in label_directory.glob("*.txt")}
            if actual_images != expected_names_in_split:
                raise YoloExportError(f"{split_name} 图片集合与划分计划不一致")
            if actual_labels != {Path(name).stem for name in expected_names_in_split}:
                raise YoloExportError(f"{split_name} 标签文件与图片未一一配对")
            for sample_id in expected_ids:
                if sample_id in assigned:
                    raise YoloExportError("同一样本被分配到多个集合")
                assigned[sample_id] = split_name
                candidate = candidates[sample_id]
                image_path = image_directory / candidate.filename
                with Image.open(image_path) as opened:
                    opened.load()
                    if opened.size != (candidate.width, candidate.height):
                        raise YoloExportError(f"导出图片尺寸发生变化: {candidate.filename}")
                label_path = label_directory / f"{Path(candidate.filename).stem}.txt"
                lines = [
                    line for line in label_path.read_text(encoding="utf-8").splitlines() if line
                ]
                if len(lines) != len(candidate.rectangles):
                    raise YoloExportError(f"标签行数不一致: {candidate.filename}")
                for line in lines:
                    self._validate_label_line(line, expected_names)
        if set(assigned) != set(candidates):
            raise YoloExportError("划分计划遗漏候选样本")
        for group in preflight.plan.groups:
            split_names = {assigned[sample_id] for sample_id in group.sample_ids}
            if len(split_names) != 1:
                raise YoloExportError("重复或确认相似组被拆分到不同集合")

    @staticmethod
    def _validate_label_line(line: str, names: Mapping[int, str]) -> None:
        parts = line.split()
        if len(parts) != 5:
            raise YoloExportError("YOLO 标签行必须包含类别和四个坐标")
        try:
            class_id = int(parts[0])
            values = tuple(float(value) for value in parts[1:])
        except ValueError as error:
            raise YoloExportError("YOLO 标签行包含非数字内容") from error
        if class_id not in names:
            raise YoloExportError(f"YOLO 标签引用未知类别: {class_id}")
        if not all(math.isfinite(value) and 0 <= value <= 1 for value in values):
            raise YoloExportError("YOLO 归一化坐标不在 [0, 1] 范围内")
        if values[2] <= 0 or values[3] <= 0:
            raise YoloExportError("YOLO 标签宽高必须大于 0")


class YoloDetectionExporter:
    """把已复验快照写入同卷临时目录，并在完整验证后一次发布。"""

    def __init__(
        self,
        service: YoloExportService,
        validator: YoloExportValidator | None = None,
    ) -> None:
        self.service = service
        self.validator = validator or YoloExportValidator()

    def export(
        self,
        preflight: YoloExportPreflight,
        *,
        progress: Callable[[int, int, str], None] | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> YoloExportReport:
        if not preflight.can_export or preflight.plan is None:
            raise YoloExportError("导出预检包含阻断问题")
        if cancelled and cancelled():
            return YoloExportReport(
                preflight.request.output_directory,
                plan_fingerprint=preflight.plan.fingerprint,
                cancelled=True,
            )
        refreshed = self.service.preflight(
            preflight.request,
            cancelled=cancelled,
        )
        if cancelled and cancelled():
            return YoloExportReport(
                preflight.request.output_directory,
                plan_fingerprint=preflight.plan.fingerprint,
                cancelled=True,
            )
        if (
            not refreshed.can_export
            or refreshed.snapshot_fingerprint != preflight.snapshot_fingerprint
            or refreshed.plan is None
            or refreshed.plan.fingerprint != preflight.plan.fingerprint
        ):
            raise YoloExportError("数据集在预检后发生变化，请重新生成导出预览")
        target = preflight.request.output_directory
        if target.exists():
            raise YoloExportError("目标目录已在预检后出现，请选择新的目录")
        temporary = Path(
            tempfile.mkdtemp(prefix=f".datumdock-yolo-{target.name}-", dir=target.parent)
        )
        report = YoloExportReport(target, plan_fingerprint=preflight.plan.fingerprint)
        try:
            marker = temporary / ".datumdock-exporting"
            _write_bytes_fsync(marker, b"temporary yolo export\n")
            for split_name in ("train", "val", "test"):
                (temporary / "images" / split_name).mkdir(parents=True)
                (temporary / "labels" / split_name).mkdir(parents=True)
            by_id = {candidate.sample_id: candidate for candidate in refreshed.candidates}
            label_set = self.service.library.open_dataset(self.service.dataset_id).label_set
            labels = {label.id: label for label in label_set.labels}
            total = len(refreshed.candidates)
            completed = 0
            for split_name in ("train", "val", "test"):
                split_ids = refreshed.plan.sample_ids(split_name)
                report.split_counts[split_name] = len(split_ids)
                for sample_id in split_ids:
                    if cancelled and cancelled():
                        report.cancelled = True
                        return report
                    candidate = by_id[sample_id]
                    if progress:
                        progress(completed, total, candidate.filename)
                    self._write_candidate(temporary, split_name, candidate, labels)
                    report.image_count += 1
                    report.label_file_count += 1
                    report.rectangle_count += len(candidate.rectangles)
                    report.negative_count += int(candidate.negative)
                    completed += 1
                    if progress:
                        progress(completed, total, candidate.filename)
            self._write_yaml(temporary, label_set, refreshed.request.ratios)
            marker.unlink()
            self.validator.validate(temporary, refreshed, label_set)
            report.directory_sha256 = _directory_digest(temporary)
            os.replace(temporary, target)
            return report
        except Exception as error:
            if temporary.exists():
                try:
                    shutil.rmtree(temporary)
                except OSError as cleanup_error:
                    raise YoloExportError(
                        f"{error}；临时目录清理失败: {cleanup_error}；保留位置: {temporary}"
                    ) from error
            if isinstance(error, YoloExportError):
                raise
            raise YoloExportError(f"YOLO 导出失败: {error}") from error
        finally:
            if temporary.exists():
                try:
                    shutil.rmtree(temporary)
                except OSError as error:
                    report.cleanup_path = str(temporary)
                    report.warnings.append(f"临时目录清理失败: {error}")

    def _write_candidate(
        self,
        root: Path,
        split_name: str,
        candidate: ExportCandidateSnapshot,
        labels: Mapping[str, Label],
    ) -> None:
        sample = self.service.samples.get_sample(candidate.sample_id)
        if sample is None:
            raise YoloExportError(f"导出过程中样本消失: {candidate.sample_id}")
        source = self.service.samples.resolve_path(sample.image_path, "pool/images")
        target = root / "images" / split_name / candidate.filename
        shutil.copy2(source, target)
        _fsync_existing_file(target)
        if _sha256(target) != candidate.image_sha256:
            raise YoloExportError(f"图片复制期间发生变化: {candidate.filename}")
        lines: list[str] = []
        for rectangle in candidate.rectangles:
            label = labels.get(rectangle.label_id)
            if label is None:
                raise YoloExportError(f"矩形引用未知标签: {rectangle.label_id}")
            x_center = (rectangle.x1 + rectangle.x2) / 2 / candidate.width
            y_center = (rectangle.y1 + rectangle.y2) / 2 / candidate.height
            width = (rectangle.x2 - rectangle.x1) / candidate.width
            height = (rectangle.y2 - rectangle.y1) / candidate.height
            values = (x_center, y_center, width, height)
            if not all(math.isfinite(value) and 0 <= value <= 1 for value in values):
                raise YoloExportError(f"矩形坐标不能转换为 YOLO: {candidate.filename}")
            if width <= 0 or height <= 0:
                raise YoloExportError(f"矩形面积无效: {candidate.filename}")
            lines.append(
                f"{label.class_id} " + " ".join(_format_coordinate(value) for value in values)
            )
        payload = ("\n".join(lines) + ("\n" if lines else "")).encode("utf-8")
        label_target = root / "labels" / split_name / f"{Path(candidate.filename).stem}.txt"
        _write_bytes_fsync(label_target, payload)

    @staticmethod
    def _write_yaml(root: Path, label_set: LabelSet, ratios: SplitRatios) -> None:
        names = {
            label.class_id: label.name
            for label in sorted(label_set.labels, key=lambda item: item.class_id)
        }
        payload = {
            "train": "images/train",
            "val": "images/val" if ratios.val else None,
            "test": "images/test" if ratios.test else None,
            "nc": len(names),
            "names": names,
        }
        encoded = yaml.safe_dump(
            payload,
            allow_unicode=True,
            sort_keys=False,
            default_flow_style=False,
        ).encode("utf-8")
        _write_bytes_fsync(root / "data.yaml", encoded)


class YoloExportService:
    """受管资料库和正式导出器之间的只读业务边界。"""

    def __init__(self, library: DatasetLibraryService, dataset_id: str) -> None:
        self.library = library
        self.dataset_id = dataset_id
        self.paths = library.dataset_repository.paths(dataset_id)
        self.samples = DatasetSampleRepository(self.paths, dataset_id)
        self.labelme = LabelMeRepository()

    def preflight(
        self,
        request: YoloExportRequest,
        *,
        progress: Callable[[int, int, str], None] | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> YoloExportPreflight:
        if request.dataset_id != self.dataset_id:
            raise YoloExportError("导出请求不属于当前数据集")
        self._validate_target(request.output_directory)
        bundle = self.library.open_dataset(self.dataset_id)
        label_set = bundle.label_set
        issues = list(self._validate_label_set(label_set))
        selected_samples = self._selected_samples(request)
        candidates: list[ExportCandidateSnapshot] = []
        excluded: Counter[str] = Counter()
        estimated_bytes = 0
        total_samples = len(selected_samples)
        for index, sample in enumerate(selected_samples, start=1):
            if cancelled and cancelled():
                issues.append(
                    ExportValidationIssue(
                        ExportIssueSeverity.ERROR,
                        "cancelled",
                        "导出预检已取消",
                    )
                )
                break
            if progress:
                progress(index - 1, total_samples, sample.filename)
            include, reason = self._include_sample(sample, request)
            if not include:
                excluded[reason] += 1
                if progress:
                    progress(index, total_samples, sample.filename)
                continue
            try:
                candidate = self._candidate(sample, label_set)
                candidates.append(candidate)
                estimated_bytes += candidate.image_size + max(64, len(candidate.rectangles) * 64)
            except YoloExportError as error:
                issues.append(
                    ExportValidationIssue(
                        ExportIssueSeverity.ERROR,
                        "sample_not_exportable",
                        str(error),
                        sample.id,
                        sample.filename,
                    )
                )
            if progress:
                progress(index, total_samples, sample.filename)
        if not candidates:
            issues.append(
                ExportValidationIssue(
                    ExportIssueSeverity.ERROR,
                    "empty_candidates",
                    "没有符合当前范围和复核规则的导出样本",
                )
            )
        confirmed_groups = [
            (group.id, group.sample_ids)
            for group in self.samples.list_similarity_groups(SimilarityStatus.CONFIRMED)
        ]
        pending_groups = self.samples.list_similarity_groups(SimilarityStatus.PENDING)
        candidate_ids = {candidate.sample_id for candidate in candidates}
        outside_confirmed_count = sum(
            bool(candidate_ids.intersection(group.sample_ids))
            and bool(set(group.sample_ids) - candidate_ids)
            for group in self.samples.list_similarity_groups(SimilarityStatus.CONFIRMED)
        )
        if outside_confirmed_count:
            issues.append(
                ExportValidationIssue(
                    ExportIssueSeverity.WARNING,
                    "confirmed_group_outside_scope",
                    f"有 {outside_confirmed_count} 个确认相似组包含本次范围外图片；"
                    "范围不会自动扩大",
                )
            )
        pending_count = sum(
            len(candidate_ids.intersection(group.sample_ids)) > 1 for group in pending_groups
        )
        if pending_count:
            issues.append(
                ExportValidationIssue(
                    ExportIssueSeverity.WARNING,
                    "pending_similarity",
                    f"有 {pending_count} 个未确认近似组可能跨集合，请先检查相似图片",
                )
            )
        if any(candidate.review_status == ReviewStatus.PENDING_REVIEW for candidate in candidates):
            issues.append(
                ExportValidationIssue(
                    ExportIssueSeverity.WARNING,
                    "pending_review_included",
                    "本次候选包含尚未人工确认的待复核图片",
                )
            )
        if request.include_unreviewed_negatives and any(
            candidate.negative and candidate.review_status is None for candidate in candidates
        ):
            issues.append(
                ExportValidationIssue(
                    ExportIssueSeverity.WARNING,
                    "unreviewed_negative_included",
                    "本次候选包含尚未人工确认的零框图片",
                )
            )
        used_label_ids = {
            rectangle.label_id for candidate in candidates for rectangle in candidate.rectangles
        }
        archived_names = [
            label.name
            for label in label_set.labels
            if label.id in used_label_ids and label.status == LabelStatus.ARCHIVED
        ]
        if archived_names:
            issues.append(
                ExportValidationIssue(
                    ExportIssueSeverity.WARNING,
                    "archived_labels_referenced",
                    "历史标注仍引用归档标签: " + ", ".join(sorted(archived_names)),
                )
            )
        plan = None
        statistics = None
        if candidates and not any(issue.severity == ExportIssueSeverity.ERROR for issue in issues):
            try:
                plan, statistics = DeterministicSplitPlanner().plan(
                    tuple(candidates),
                    label_set,
                    request.ratios,
                    request.seed,
                    confirmed_groups,
                )
                statistics = SplitStatistics(
                    statistics.buckets,
                    statistics.exact_duplicate_group_count,
                    statistics.confirmed_similarity_group_count,
                    pending_count,
                    statistics.largest_group_size,
                    statistics.warnings,
                )
            except YoloExportError as error:
                issues.append(
                    ExportValidationIssue(
                        ExportIssueSeverity.ERROR,
                        "split_not_possible",
                        str(error),
                    )
                )
        snapshot_fingerprint = self._snapshot_fingerprint(
            request,
            candidates,
            label_set,
            confirmed_groups,
        )
        free = shutil.disk_usage(request.output_directory.parent).free
        required = estimated_bytes + 16 * 1024 * 1024
        if free < required:
            issues.append(
                ExportValidationIssue(
                    ExportIssueSeverity.ERROR,
                    "insufficient_space",
                    f"目标磁盘空间不足，至少需要约 {required} 字节",
                )
            )
        return YoloExportPreflight(
            request,
            tuple(sorted(candidates, key=lambda item: item.sample_id)),
            tuple(issues),
            plan,
            statistics,
            label_set.revision,
            label_set.training_signature(),
            snapshot_fingerprint,
            estimated_bytes,
            tuple(sorted(excluded.items())),
        )

    def exporter(self) -> YoloDetectionExporter:
        return YoloDetectionExporter(self)

    def _selected_samples(self, request: YoloExportRequest) -> tuple[DatasetSample, ...]:
        if request.scope == ExportScope.ALL:
            return self.samples.all_active_samples()
        if not request.sample_ids:
            raise YoloExportError("当前筛选或显式选择范围为空")
        selected: list[DatasetSample] = []
        for sample_id in dict.fromkeys(request.sample_ids):
            sample = self.samples.get_sample(sample_id)
            if sample is None:
                raise YoloExportError(f"导出样本不存在或已在回收站: {sample_id}")
            selected.append(sample)
        return tuple(selected)

    @staticmethod
    def _include_sample(sample: DatasetSample, request: YoloExportRequest) -> tuple[bool, str]:
        if sample.annotation_count > 0:
            if sample.review_status == ReviewStatus.COMPLETED:
                return True, ""
            if (
                sample.review_status == ReviewStatus.PENDING_REVIEW
                and request.include_pending_review
            ):
                return True, ""
            return False, "review_not_completed"
        if sample.review_status == ReviewStatus.COMPLETED:
            return request.include_completed_negatives, "completed_negative_excluded"
        if sample.review_status is None:
            return request.include_unreviewed_negatives, "unreviewed_negative_excluded"
        if sample.review_status == ReviewStatus.PENDING_REVIEW:
            include = request.include_completed_negatives and request.include_pending_review
            return include, "pending_negative_excluded"
        return False, "not_eligible"

    def _candidate(self, sample: DatasetSample, label_set: LabelSet) -> ExportCandidateSnapshot:
        if sample.health != SampleHealth.READY:
            raise YoloExportError(f"图片健康状态阻止导出: {sample.health.value}")
        if sample.annotation_state in {
            AnnotationState.CORRUPT,
            AnnotationState.UNKNOWN_LABEL,
            AnnotationState.RECOVERY_REQUIRED,
        }:
            raise YoloExportError(f"标注状态阻止导出: {sample.annotation_state.value}")
        image_path = self.samples.resolve_path(sample.image_path, "pool/images")
        if not image_path.is_file() or _is_reparse_point(image_path):
            raise YoloExportError("受管图片缺失或为不安全的重解析点")
        image_sha = _sha256(image_path)
        if sample.file_hash and image_sha != sample.file_hash:
            raise YoloExportError("受管图片摘要与索引不一致")
        try:
            with Image.open(image_path) as opened:
                opened.load()
                if opened.size != (sample.width, sample.height):
                    raise YoloExportError("受管图片尺寸与索引不一致")
        except OSError as error:
            raise YoloExportError(f"受管图片无法解码: {error}") from error
        document = AnnotationDocument(
            sample_id=sample.id,
            image_filename=sample.filename,
            image_width=sample.width,
            image_height=sample.height,
            document_version=sample.annotation_version,
        )
        annotation_sha = ""
        if sample.annotation_path:
            annotation_path = self.samples.resolve_path(sample.annotation_path, "pool/annotations")
            if not annotation_path.is_file() or _is_reparse_point(annotation_path):
                raise YoloExportError("索引引用的标注 JSON 缺失或不安全")
            annotation_sha = _sha256(annotation_path)
            if sample.annotation_sha256 and annotation_sha != sample.annotation_sha256:
                raise YoloExportError("标注 JSON 摘要与索引不一致")
            try:
                document = self.labelme.load(
                    annotation_path,
                    sample.id,
                    label_set,
                    sample.filename,
                    (sample.width, sample.height),
                )
            except (LabelMeError, OSError, ValueError) as error:
                raise YoloExportError(f"标注 JSON 无法读取: {error}") from error
            if document.document_version != sample.annotation_version:
                raise YoloExportError("标注 JSON 版本与 SQLite 索引不一致")
        if document.unsupported_shapes:
            raise YoloExportError("图片包含 YOLO Detection 无法表达的兼容 shape")
        if len(document.rectangles) != sample.annotation_count:
            raise YoloExportError("标注框数量与 SQLite 索引不一致")
        label_ids = {label.id for label in label_set.labels}
        rectangles: list[ExportRectangleSnapshot] = []
        for rectangle in document.rectangles:
            self._validate_rectangle(rectangle, sample, label_ids)
            rectangles.append(
                ExportRectangleSnapshot(
                    rectangle.id,
                    rectangle.label_id,
                    rectangle.x1,
                    rectangle.y1,
                    rectangle.x2,
                    rectangle.y2,
                )
            )
        return ExportCandidateSnapshot(
            sample.id,
            sample.filename,
            sample.width,
            sample.height,
            sample.content_hash,
            image_sha,
            image_path.stat().st_size,
            annotation_sha,
            sample.annotation_version,
            sample.review_status,
            tuple(rectangles),
        )

    @staticmethod
    def _validate_rectangle(
        rectangle: RectangleShape,
        sample: DatasetSample,
        label_ids: set[str],
    ) -> None:
        if rectangle.label_id not in label_ids:
            raise YoloExportError("矩形引用未知标签")
        values = (rectangle.x1, rectangle.y1, rectangle.x2, rectangle.y2)
        if not all(math.isfinite(value) for value in values):
            raise YoloExportError("矩形包含非有限坐标")
        if rectangle.width <= 0 or rectangle.height <= 0:
            raise YoloExportError("矩形面积必须大于 0")
        if (
            rectangle.x1 < 0
            or rectangle.y1 < 0
            or rectangle.x2 > sample.width
            or rectangle.y2 > sample.height
        ):
            raise YoloExportError("矩形坐标越过图片边界")

    @staticmethod
    def _validate_label_set(label_set: LabelSet) -> tuple[ExportValidationIssue, ...]:
        issues: list[ExportValidationIssue] = []
        sorted_labels = sorted(label_set.labels, key=lambda label: label.class_id)
        if not sorted_labels:
            issues.append(
                ExportValidationIssue(
                    ExportIssueSeverity.ERROR,
                    "empty_label_set",
                    "YOLO Detection 导出至少需要一个标签类别",
                )
            )
            return tuple(issues)
        class_ids = [label.class_id for label in sorted_labels]
        if class_ids != list(range(len(sorted_labels))):
            issues.append(
                ExportValidationIssue(
                    ExportIssueSeverity.ERROR,
                    "non_contiguous_class_ids",
                    "类别 ID 必须连续覆盖 0..N-1，请先执行标签迁移",
                )
            )
        names = [label.name.casefold() for label in sorted_labels]
        if len(names) != len(set(names)):
            issues.append(
                ExportValidationIssue(
                    ExportIssueSeverity.ERROR,
                    "duplicate_training_names",
                    "活动和归档标签中存在重复训练名",
                )
            )
        return tuple(issues)

    @staticmethod
    def _validate_target(target: Path) -> None:
        if not target.is_absolute():
            raise YoloExportError("目标目录必须使用绝对路径")
        if target.exists() or target.is_symlink():
            raise YoloExportError("目标目录必须尚不存在")
        if not target.name or target.name in {".", ".."}:
            raise YoloExportError("目标目录名称无效")
        try:
            parent = target.parent.resolve(strict=True)
        except OSError as error:
            raise YoloExportError(f"目标父目录不存在: {error}") from error
        if not parent.is_dir() or _is_reparse_point(parent):
            raise YoloExportError("目标父目录不可用或为重解析点")

    @staticmethod
    def _snapshot_fingerprint(
        request: YoloExportRequest,
        candidates: Sequence[ExportCandidateSnapshot],
        label_set: LabelSet,
        similarity_groups: Sequence[tuple[str, Sequence[str]]],
    ) -> str:
        payload = {
            "dataset_id": request.dataset_id,
            "scope": request.scope.value,
            "ratios": request.ratios.as_tuple(),
            "seed": request.seed,
            "options": (
                request.include_completed_negatives,
                request.include_unreviewed_negatives,
                request.include_pending_review,
            ),
            "label_revision": label_set.revision,
            "training_signature": label_set.training_signature(),
            "candidates": [
                (
                    candidate.sample_id,
                    candidate.image_sha256,
                    candidate.annotation_sha256,
                    candidate.annotation_version,
                    candidate.review_status.value if candidate.review_status else None,
                    [
                        (
                            rectangle.shape_id,
                            rectangle.label_id,
                            rectangle.x1,
                            rectangle.y1,
                            rectangle.x2,
                            rectangle.y2,
                        )
                        for rectangle in candidate.rectangles
                    ],
                )
                for candidate in sorted(candidates, key=lambda item: item.sample_id)
            ],
            "confirmed_similarity": [
                (group_id, sorted(members))
                for group_id, members in sorted(similarity_groups, key=lambda item: item[0])
            ],
        }
        return _json_digest(payload)


def _format_coordinate(value: float) -> str:
    """八位小数兼顾确定性与小框精度，并移除无意义尾零。"""

    return f"{value:.8f}".rstrip("0").rstrip(".") or "0"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_digest(payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _write_bytes_fsync(path: Path, payload: bytes) -> None:
    with path.open("wb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


def _fsync_existing_file(path: Path) -> None:
    """复制完成后刷新目标文件，避免验证通过但数据仍停留在用户态缓存。"""

    with path.open("r+b") as stream:
        stream.flush()
        os.fsync(stream.fileno())


def _directory_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(bytes.fromhex(_sha256(path)))
    return digest.hexdigest()


def _is_reparse_point(path: Path) -> bool:
    try:
        attributes = path.lstat().st_file_attributes
    except AttributeError:
        attributes = 0
    except OSError:
        return True
    flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return path.is_symlink() or bool(attributes & flag)
