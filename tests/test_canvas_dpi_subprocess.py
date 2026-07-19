"""在独立 Qt 进程中验证 A0.5～A0.7 的 DPI 逻辑坐标。"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("scale_factor", ("1", "1.25", "1.5"))
def test_canvas_hit_testing_and_projection_are_dpi_independent(scale_factor: str) -> None:
    """不同系统缩放下，逻辑控制点和底板投影必须保持同一语义。"""

    project_root = Path(__file__).resolve().parents[1]
    source_root = project_root / "src"
    program = r"""
from PySide6.QtCore import QPointF, QSizeF, Qt
from PySide6.QtWidgets import QApplication
from datumdock.ui.preview_canvas import CanvasTool, PreviewAnnotationCanvas
from datumdock.ui.prototype_models import (
    AnnotationItemViewData,
    ImageItemViewData,
    ImageStatus,
    LabelViewData,
)

application = QApplication([])
canvas = PreviewAnnotationCanvas()
canvas.resize(760, 520)
label = LabelViewData("label-1", 0, "part", "零件", "DPI 验证", (), "#5B83E6", 1)
annotation = AnnotationItemViewData("shape-1", label.id, 80.0, 45.0, 240.0, 135.0)
canvas.load_preview(
    ImageItemViewData(
        "image-1", "sample.png", ImageStatus.COMPLETED, 320, 180, 1, 1
    ),
    (label,),
    (annotation,),
)
canvas.show()
application.processEvents()
canvas.set_zoom_percent(6400)
image_rect = canvas._image_rect()
source = QPointF(80.0, 45.0)
target = QPointF(
    image_rect.left() + source.x() * image_rect.width() / canvas.image.width,
    image_rect.top() + source.y() * image_rect.height() / canvas.image.height,
)
canvas.pan_offset += QPointF(canvas.rect().center()) - target
canvas._clamp_pan_offset()
handle = canvas._handle_points(canvas._annotation_rect(annotation))["top_left"]
restored = canvas._canvas_to_image(handle, canvas._image_rect())
assert abs(restored.x() - source.x()) < 1e-9
assert abs(restored.y() - source.y()) < 1e-9
assert canvas._cursor_shape(handle) == Qt.CursorShape.SizeFDiagCursor
canvas.set_tool(CanvasTool.RECTANGLE)
outside = QPointF(canvas._image_rect().left() - 25, canvas._image_rect().top() - 25)
projection = canvas._project_canvas_point(outside)
assert projection.image_point == QPointF(0, 0)
assert len(projection.clamped_edges) == 2
assert canvas._cursor_shape(outside) == Qt.CursorShape.CrossCursor
canvas.close()
"""
    environment = os.environ.copy()
    environment["QT_QPA_PLATFORM"] = "offscreen"
    environment["QT_SCALE_FACTOR"] = scale_factor
    environment["PYTHONPATH"] = str(source_root)
    result = subprocess.run(
        [sys.executable, "-c", program],
        cwd=project_root,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
