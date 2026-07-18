"""解析开发环境与 PyInstaller 分发目录共用的只读品牌资源路径。"""

from __future__ import annotations

import sys
from pathlib import Path


def resource_root() -> Path:
    """返回包含 ``assets`` 的根目录，冻结应用优先使用 PyInstaller 的资源目录。"""

    bundle_root = getattr(sys, "_MEIPASS", None)
    if bundle_root:
        return Path(str(bundle_root))
    return Path(__file__).resolve().parents[2]
