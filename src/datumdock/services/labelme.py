"""LabelMe JSON 与 DatumDock 内部有序矩形标注之间的安全转换。"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import tempfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, ClassVar, Protocol
from uuid import UUID

from datumdock.domain.models import (
    AnnotationDocument,
    LabelSet,
    RectangleShape,
    new_id,
)


class LabelMeError(RuntimeError):
    """LabelMe 文档损坏、未知标签或无法安全保存。"""


class UnknownLabelReferenceError(LabelMeError):
    """可编辑矩形引用了当前数据集不存在的标签。"""


class RectanglePointKind(StrEnum):
    """外部 rectangle 的坐标表示，用于让预检和正式导入共享同一判断。"""

    TWO_POINT = "two_point"
    FOUR_POINT_AXIS_ALIGNED = "four_point_axis_aligned"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True, slots=True)
class RectanglePointParseResult:
    """可编辑矩形返回规范坐标；其余表示必须作为兼容负载保留。"""

    kind: RectanglePointKind
    coordinates: tuple[float, float, float, float] | None = None
    reason: str = ""

    @property
    def editable(self) -> bool:
        return self.coordinates is not None


def parse_rectangle_points(
    points: object,
    image_size: tuple[int, int],
) -> RectanglePointParseResult:
    """识别 LabelMe 两点矩形和 X-AnyLabeling 4.x 的轴对齐四点矩形。"""

    if not isinstance(points, list) or len(points) not in {2, 4}:
        return RectanglePointParseResult(
            RectanglePointKind.UNSUPPORTED,
            reason="矩形必须使用两个对角点或四个轴对齐角点",
        )
    parsed: list[tuple[float, float]] = []
    for point in points:
        if not isinstance(point, list) or len(point) != 2:
            return RectanglePointParseResult(
                RectanglePointKind.UNSUPPORTED,
                reason="矩形点必须是包含两个数值的数组",
            )
        x, y = point
        if (
            isinstance(x, bool)
            or isinstance(y, bool)
            or not isinstance(x, int | float)
            or not isinstance(y, int | float)
            or not math.isfinite(float(x))
            or not math.isfinite(float(y))
        ):
            return RectanglePointParseResult(
                RectanglePointKind.UNSUPPORTED,
                reason="矩形点包含非有限或非数值坐标",
            )
        parsed.append((float(x), float(y)))

    tolerance = max(1e-6, max(image_size, default=1) * 1e-9)
    if len(parsed) == 2:
        x1, x2 = sorted((parsed[0][0], parsed[1][0]))
        y1, y2 = sorted((parsed[0][1], parsed[1][1]))
        if x2 - x1 <= tolerance or y2 - y1 <= tolerance:
            return RectanglePointParseResult(
                RectanglePointKind.UNSUPPORTED,
                reason="矩形面积为零",
            )
        return RectanglePointParseResult(
            RectanglePointKind.TWO_POINT,
            (x1, y1, x2, y2),
        )

    xs = _cluster_coordinates((point[0] for point in parsed), tolerance)
    ys = _cluster_coordinates((point[1] for point in parsed), tolerance)
    if len(xs) != 2 or len(ys) != 2:
        return RectanglePointParseResult(
            RectanglePointKind.UNSUPPORTED,
            reason="四点矩形不是轴对齐四角表示",
        )
    x1, x2 = xs
    y1, y2 = ys
    expected = ((x1, y1), (x1, y2), (x2, y1), (x2, y2))
    if not all(
        any(
            abs(expected_x - actual_x) <= tolerance and abs(expected_y - actual_y) <= tolerance
            for actual_x, actual_y in parsed
        )
        for expected_x, expected_y in expected
    ):
        return RectanglePointParseResult(
            RectanglePointKind.UNSUPPORTED,
            reason="四点矩形缺少完整的轴对齐角点",
        )
    return RectanglePointParseResult(
        RectanglePointKind.FOUR_POINT_AXIS_ALIGNED,
        (x1, y1, x2, y2),
    )


def _cluster_coordinates(values: Iterable[float], tolerance: float) -> tuple[float, ...]:
    """在极小的图片相关容差内合并同一条矩形边，避免浮点序列化噪声。"""

    clusters: list[list[float]] = []
    for value in sorted(float(item) for item in values):
        if not clusters or abs(value - clusters[-1][-1]) > tolerance:
            clusters.append([value])
        else:
            clusters[-1].append(value)
    return tuple(sum(cluster) / len(cluster) for cluster in clusters)


class AnnotationRepository(Protocol):
    """标注存储协议，UI 与服务不依赖具体交换格式。"""

    def load(
        self,
        path: Path,
        sample_id: str,
        label_set: LabelSet,
        fallback_filename: str,
        fallback_size: tuple[int, int],
    ) -> AnnotationDocument: ...

    def save(self, path: Path, document: AnnotationDocument, label_set: LabelSet) -> str: ...


class LabelMeRepository:
    """读写交换 JSON，同时保留 shape 顺序和当前版本不能编辑的字段。"""

    ROOT_KEYS: ClassVar[set[str]] = {
        "version",
        "flags",
        "shapes",
        "imagePath",
        "imageData",
        "imageHeight",
        "imageWidth",
        "datumdock_document_version",
        "datumdock_review_status",
    }

    def load(
        self,
        path: Path,
        sample_id: str,
        label_set: LabelSet,
        fallback_filename: str,
        fallback_size: tuple[int, int],
    ) -> AnnotationDocument:
        """读取 JSON；损坏或未知标签由上层显示错误，原字节保持不变。"""

        try:
            with path.open(encoding="utf-8") as stream:
                payload = json.load(stream)
        except (OSError, json.JSONDecodeError) as error:
            raise LabelMeError(f"标注 JSON 无法读取: {error}") from error
        if not isinstance(payload, dict):
            raise LabelMeError("标注 JSON 根节点必须是对象")
        return self.from_payload(payload, sample_id, label_set, fallback_filename, fallback_size)

    def from_payload(
        self,
        payload: dict[str, Any],
        sample_id: str,
        label_set: LabelSet,
        fallback_filename: str,
        fallback_size: tuple[int, int],
        *,
        external_label_ids: Mapping[str, str | None] | None = None,
        preserve_unknown_rectangles: bool = False,
    ) -> AnnotationDocument:
        """仅把已知标签的标准 rectangle 转为可编辑框，其余 shape 原样保留。"""

        try:
            width = int(payload.get("imageWidth") or fallback_size[0])
            height = int(payload.get("imageHeight") or fallback_size[1])
            document_version = int(payload.get("datumdock_document_version") or 0)
        except (TypeError, ValueError) as error:
            raise LabelMeError(f"LabelMe 图片尺寸或文档版本无效: {error}") from error
        by_name = {label.name.casefold(): label for label in label_set.labels}
        by_id = {label.id: label for label in label_set.labels}
        rectangles: list[RectangleShape] = []
        unsupported_shapes: list[dict[str, Any]] = []
        shape_order: list[str] = []
        raw_shapes = payload.get("shapes", [])
        if not isinstance(raw_shapes, list):
            raise LabelMeError("LabelMe shapes 必须是数组")
        for raw_shape in raw_shapes:
            if not isinstance(raw_shape, dict):
                raise LabelMeError("LabelMe shape 必须是对象")
            shape = copy.deepcopy(raw_shape)
            shape_id = _stable_private_id(shape.get("datumdock_shape_id"))
            shape["datumdock_shape_id"] = shape_id
            shape_order.append(shape_id)
            points = shape.get("points")
            private_label_id = str(shape.get("datumdock_label_id") or "")
            external_name = str(shape.get("label", "")).strip()
            mapped_id = None
            if external_label_ids is not None:
                mapped_id = external_label_ids.get(external_name.casefold())
            label = (
                by_id.get(mapped_id or "")
                or by_id.get(private_label_id)
                or by_name.get(external_name.casefold())
            )
            is_rectangle = shape.get("shape_type") == "rectangle"
            read_only_compat = bool(shape.get("datumdock_read_only_compat"))
            point_result = (
                parse_rectangle_points(points, (width, height))
                if is_rectangle
                else RectanglePointParseResult(RectanglePointKind.UNSUPPORTED)
            )
            if (
                is_rectangle
                and label is None
                and not preserve_unknown_rectangles
                and not read_only_compat
            ):
                raise UnknownLabelReferenceError(
                    f"矩形引用了当前标签集中不存在的标签: {shape.get('label', '')}"
                )
            if is_rectangle and point_result.editable and label is not None:
                x1, y1, x2, y2 = point_result.coordinates or (0.0, 0.0, 0.0, 0.0)
                shape.pop("datumdock_read_only_compat", None)
                rectangles.append(
                    RectangleShape(
                        id=shape_id,
                        label_id=label.id,
                        x1=x1,
                        y1=y1,
                        x2=x2,
                        y2=y2,
                        source_model_id=shape.get("datumdock_model_id"),
                        confidence=shape.get("score"),
                        compatibility_payload=shape,
                    )
                )
            else:
                if is_rectangle and label is None and preserve_unknown_rectangles:
                    shape["datumdock_read_only_compat"] = True
                unsupported_shapes.append(shape)
        root_payload = {
            key: copy.deepcopy(value) for key, value in payload.items() if key not in self.ROOT_KEYS
        }
        try:
            return AnnotationDocument(
                sample_id=sample_id,
                image_filename=str(payload.get("imagePath") or fallback_filename),
                image_width=width,
                image_height=height,
                labelme_version=str(payload.get("version") or "5.4.1"),
                image_data=payload.get("imageData"),
                document_version=document_version,
                rectangles=rectangles,
                image_flags=copy.deepcopy(payload.get("flags") or {}),
                unsupported_shapes=unsupported_shapes,
                shape_order=shape_order,
                root_payload=root_payload,
            )
        except ValueError as error:
            raise LabelMeError(f"LabelMe 文档验证失败: {error}") from error

    def from_external_payload(
        self,
        payload: dict[str, Any],
        sample_id: str,
        label_set: LabelSet,
        fallback_filename: str,
        fallback_size: tuple[int, int],
        label_resolutions: Mapping[str, str | None],
    ) -> AnnotationDocument:
        """按用户确认的映射导入外部文档，未映射矩形只读保留而不丢失。"""

        normalized = {name.casefold(): label_id for name, label_id in label_resolutions.items()}
        return self.from_payload(
            payload,
            sample_id,
            label_set,
            fallback_filename,
            fallback_size,
            external_label_ids=normalized,
            preserve_unknown_rectangles=True,
        )

    def save(self, path: Path, document: AnnotationDocument, label_set: LabelSet) -> str:
        """原子写入受管 JSON，并在替换前后重新解析验证。"""

        payload = self.to_payload(document, label_set, for_export=False)
        path.parent.mkdir(parents=True, exist_ok=True)
        handle, temporary_name = tempfile.mkstemp(
            prefix=f".{path.stem}-",
            suffix=".tmp",
            dir=path.parent,
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
                json.dump(payload, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            self.load(
                temporary_path,
                document.sample_id,
                label_set,
                document.image_filename,
                (document.image_width, document.image_height),
            )
            os.replace(temporary_path, path)
            self.load(
                path,
                document.sample_id,
                label_set,
                document.image_filename,
                (document.image_width, document.image_height),
            )
            return hashlib.sha256(path.read_bytes()).hexdigest()
        except Exception as error:
            temporary_path.unlink(missing_ok=True)
            if isinstance(error, LabelMeError):
                raise
            raise LabelMeError(f"标注 JSON 原子保存失败: {error}") from error

    def to_payload(
        self,
        document: AnnotationDocument,
        label_set: LabelSet,
        *,
        for_export: bool,
    ) -> dict[str, Any]:
        """按原有 shape 顺序构造受管或外部交换负载。"""

        by_id = {label.id: label for label in label_set.labels}
        shapes_by_id: dict[str, dict[str, Any]] = {}
        generated_order: list[str] = []
        for raw_shape in document.unsupported_shapes:
            shape = copy.deepcopy(raw_shape)
            shape_id = _stable_private_id(shape.get("datumdock_shape_id"))
            shape["datumdock_shape_id"] = shape_id
            shapes_by_id[shape_id] = shape
            generated_order.append(shape_id)
        for rectangle in document.rectangles:
            label = by_id.get(rectangle.label_id)
            if label is None:
                raise UnknownLabelReferenceError(f"标注引用了不存在的标签: {rectangle.label_id}")
            shape = {
                **copy.deepcopy(rectangle.compatibility_payload),
                "label": label.name,
                "points": [[rectangle.x1, rectangle.y1], [rectangle.x2, rectangle.y2]],
                "shape_type": "rectangle",
                "datumdock_shape_id": rectangle.id,
                "datumdock_label_id": rectangle.label_id,
            }
            shape.setdefault("group_id", None)
            shape.setdefault("description", "")
            shape.setdefault("flags", {})
            shape.setdefault("mask", None)
            if rectangle.confidence is not None:
                shape["score"] = rectangle.confidence
            else:
                shape.pop("score", None)
            if rectangle.source_model_id is not None:
                shape["datumdock_model_id"] = rectangle.source_model_id
            else:
                shape.pop("datumdock_model_id", None)
            shapes_by_id[rectangle.id] = shape
            generated_order.append(rectangle.id)
        order = [identifier for identifier in document.shape_order if identifier in shapes_by_id]
        order.extend(identifier for identifier in generated_order if identifier not in order)
        shapes = [shapes_by_id[identifier] for identifier in order]
        payload: dict[str, Any] = {
            **copy.deepcopy(document.root_payload),
            "version": document.labelme_version,
            "flags": copy.deepcopy(document.image_flags),
            "shapes": shapes,
            "imagePath": document.image_filename,
            "imageData": document.image_data,
            "imageHeight": document.image_height,
            "imageWidth": document.image_width,
            "datumdock_document_version": document.document_version,
        }
        if for_export:
            return _strip_private_fields(payload)
        return payload

    def export_payload(self, document: AnnotationDocument, label_set: LabelSet) -> dict[str, Any]:
        """生成交换 JSON，并递归剔除全部 DatumDock 私有字段。"""

        return self.to_payload(document, label_set, for_export=True)


def _stable_private_id(value: object) -> str:
    try:
        canonical = str(UUID(str(value)))
        if canonical == value:
            return canonical
    except (TypeError, ValueError):
        pass
    return new_id()


def _strip_private_fields(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _strip_private_fields(item)
            for key, item in value.items()
            if not key.startswith("datumdock_")
        }
    if isinstance(value, list):
        return [_strip_private_fields(item) for item in value]
    return copy.deepcopy(value)
