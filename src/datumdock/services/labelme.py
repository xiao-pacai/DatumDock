"""LabelMe JSON 与 DatumDock 内部矩形标注之间的安全转换。"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from datumdock.domain.models import AnnotationDocument, LabelSet, RectangleShape
from datumdock.services.storage import write_json_atomic


class LabelMeRepository:
    """读写交换 JSON，同时保留当前版本无法编辑的 shape 和扩展字段。"""

    def load(
        self,
        path: Path,
        sample_id: str,
        label_set: LabelSet,
        fallback_filename: str,
        fallback_size: tuple[int, int],
    ) -> AnnotationDocument:
        """读取 JSON；损坏或未知标签由调用方显示错误，绝不自动覆盖原文件。"""

        with path.open(encoding="utf-8") as stream:
            payload = json.load(stream)
        return self.from_payload(payload, sample_id, label_set, fallback_filename, fallback_size)

    def from_payload(
        self,
        payload: dict[str, Any],
        sample_id: str,
        label_set: LabelSet,
        fallback_filename: str,
        fallback_size: tuple[int, int],
    ) -> AnnotationDocument:
        """仅将标准 rectangle 转为可编辑框，其他内容按原结构存入兼容负载。"""

        by_name = {label.name: label for label in label_set.labels}
        rectangles: list[RectangleShape] = []
        unsupported_shapes: list[dict[str, Any]] = []
        for raw_shape in payload.get("shapes", []):
            shape = copy.deepcopy(raw_shape)
            points = shape.get("points")
            label = by_name.get(str(shape.get("label", "")))
            if (
                shape.get("shape_type") == "rectangle"
                and label is not None
                and isinstance(points, list)
                and len(points) == 2
                and all(isinstance(point, list) and len(point) == 2 for point in points)
            ):
                rectangles.append(
                    RectangleShape(
                        label_id=label.id,
                        x1=float(points[0][0]),
                        y1=float(points[0][1]),
                        x2=float(points[1][0]),
                        y2=float(points[1][1]),
                        source_model_id=shape.get("datumdock_model_id"),
                        confidence=shape.get("score"),
                        compatibility_payload=shape,
                    )
                )
            else:
                unsupported_shapes.append(shape)
        width = int(payload.get("imageWidth") or fallback_size[0])
        height = int(payload.get("imageHeight") or fallback_size[1])
        root_keys = {
            "version",
            "flags",
            "shapes",
            "imagePath",
            "imageData",
            "imageHeight",
            "imageWidth",
        }
        root_payload = {
            key: copy.deepcopy(value) for key, value in payload.items() if key not in root_keys
        }
        return AnnotationDocument(
            sample_id=sample_id,
            image_filename=str(payload.get("imagePath") or fallback_filename),
            image_width=width,
            image_height=height,
            rectangles=rectangles,
            image_flags=copy.deepcopy(payload.get("flags") or {}),
            unsupported_shapes=unsupported_shapes,
            root_payload=root_payload,
        )

    def save(self, path: Path, document: AnnotationDocument, label_set: LabelSet) -> None:
        """把矩形和保留 shape 合并为标准 LabelMe JSON 并原子替换目标文件。"""

        by_id = {label.id: label for label in label_set.labels}
        shapes = [copy.deepcopy(shape) for shape in document.unsupported_shapes]
        for rectangle in document.rectangles:
            label = by_id.get(rectangle.label_id)
            if label is None:
                raise KeyError(f"标注引用了不存在的标签: {rectangle.label_id}")
            shape: dict[str, Any] = {
                **copy.deepcopy(rectangle.compatibility_payload),
                "label": label.name,
                "points": [[rectangle.x1, rectangle.y1], [rectangle.x2, rectangle.y2]],
                "shape_type": "rectangle",
            }
            shape.setdefault("group_id", None)
            shape.setdefault("description", "")
            shape.setdefault("flags", {})
            shape.setdefault("mask", None)
            if rectangle.confidence is not None:
                shape["score"] = rectangle.confidence
            if rectangle.source_model_id is not None:
                # DatumDock 私有模型来源只保存在受管 JSON，交换导出时会剔除。
                shape["datumdock_model_id"] = rectangle.source_model_id
            shapes.append(shape)
        payload = {
            **document.root_payload,
            "version": "5.4.1",
            "flags": document.image_flags,
            "shapes": shapes,
            "imagePath": document.image_filename,
            "imageData": None,
            "imageHeight": document.image_height,
            "imageWidth": document.image_width,
        }
        write_json_atomic(path, payload)

    def export_payload(self, document: AnnotationDocument, label_set: LabelSet) -> dict[str, Any]:
        """生成交换 JSON，确保 DatumDock 私有模型标识不泄漏给外部项目。"""

        by_id = {label.id: label for label in label_set.labels}
        shapes = [copy.deepcopy(shape) for shape in document.unsupported_shapes]
        for shape in shapes:
            shape.pop("datumdock_model_id", None)
        for rectangle in document.rectangles:
            label = by_id.get(rectangle.label_id)
            if label is None:
                raise KeyError(f"标注引用了不存在的标签: {rectangle.label_id}")
            shape = {
                **copy.deepcopy(rectangle.compatibility_payload),
                "label": label.name,
                "points": [[rectangle.x1, rectangle.y1], [rectangle.x2, rectangle.y2]],
                "shape_type": "rectangle",
            }
            shape.setdefault("group_id", None)
            shape.setdefault("description", "")
            shape.setdefault("flags", {})
            shape.setdefault("mask", None)
            if rectangle.confidence is not None:
                shape["score"] = rectangle.confidence
            shapes.append(shape)
        return {
            **document.root_payload,
            "version": "5.4.1",
            "flags": document.image_flags,
            "shapes": shapes,
            "imagePath": document.image_filename,
            "imageData": None,
            "imageHeight": document.image_height,
            "imageWidth": document.image_width,
        }
