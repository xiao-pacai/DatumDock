"""矩形标注和受管样本的可重复结构质量检查。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from datumdock.domain.models import AnnotationDocument, DatasetSample, LabelSet


@dataclass(frozen=True)
class AnnotationQualityIssue:
    """不直接绑定界面语言的质量问题；界面使用代码映射为本地化说明。"""

    code: str
    shape_id: str | None = None


class AnnotationQualityService:
    """检测空标签、未知标签、无效框、越界框和受管文件缺失，不修改任何数据。"""

    def inspect(
        self,
        sample: DatasetSample,
        document: AnnotationDocument,
        label_set: LabelSet,
    ) -> list[AnnotationQualityIssue]:
        """返回稳定排序的问题列表，便于测试、提示和后续批量质量报告复用。"""

        issues: list[AnnotationQualityIssue] = []
        if not Path(sample.image_path).is_file():
            issues.append(AnnotationQualityIssue("missing_image"))
        if not Path(sample.annotation_path).is_file():
            issues.append(AnnotationQualityIssue("missing_annotation"))
        known_labels = {label.id for label in label_set.labels}
        for rectangle in document.rectangles:
            if not rectangle.label_id:
                issues.append(AnnotationQualityIssue("empty_label", rectangle.id))
            elif rectangle.label_id not in known_labels:
                issues.append(AnnotationQualityIssue("unknown_label", rectangle.id))
            if rectangle.width <= 0 or rectangle.height <= 0:
                issues.append(AnnotationQualityIssue("invalid_area", rectangle.id))
            if (
                rectangle.x1 < 0
                or rectangle.y1 < 0
                or rectangle.x2 > document.image_width
                or rectangle.y2 > document.image_height
            ):
                issues.append(AnnotationQualityIssue("out_of_bounds", rectangle.id))
        return sorted(issues, key=lambda item: (item.code, item.shape_id or ""))
