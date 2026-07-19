"""标注质量检查的服务层回归测试。"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from PIL import Image

from datumdock.domain.models import (
    AnnotationDocument,
    DatasetSample,
    Label,
    LabelSet,
    RectangleShape,
)
from datumdock.services.quality import AnnotationQualityService


def test_quality_check_reports_missing_files_and_invalid_rectangles(tmp_path: Path) -> None:
    """质量检查必须识别文件缺失、未知标签、零面积和越界框，且不修改输入。"""

    image_path = tmp_path / "sample.png"
    Image.new("RGB", (40, 20), (100, 120, 140)).save(image_path)
    sample = DatasetSample(
        dataset_id=str(uuid4()),
        filename="sample.png",
        image_path=str(image_path),
        annotation_path=str(tmp_path / "missing.json"),
        width=40,
        height=20,
        content_hash="0" * 64,
        perceptual_hash="0" * 22,
        imported_at="2026-07-18T00:00:00+00:00",
    )
    label_set = LabelSet(labels=[Label(class_id=0, name="part", alias="零件", color="#78978C")])
    document = AnnotationDocument(
        sample_id=sample.id,
        image_filename=sample.filename,
        image_width=40,
        image_height=20,
        rectangles=[
            RectangleShape(label_id="unknown", x1=-1, y1=2, x2=60, y2=2),
        ],
    )

    issues = AnnotationQualityService().inspect(sample, document, label_set)

    assert [issue.code for issue in issues] == [
        "invalid_area",
        "missing_annotation",
        "out_of_bounds",
        "unknown_label",
    ]
    assert document.rectangles[0].label_id == "unknown"
