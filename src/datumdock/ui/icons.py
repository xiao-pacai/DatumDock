"""DatumDock 自有 SVG 图标的统一加载入口。"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QIcon


class IconRegistry:
    """集中定位品牌资产和工具栏图标，避免界面散落硬编码文件路径。"""

    def __init__(self, root: Path) -> None:
        self.root = root

    def icon(self, name: str) -> QIcon:
        """按语义名称加载项目自有 SVG，缺失时返回空图标而不阻塞操作。"""

        return QIcon(str(self.root / "assets" / "icons" / f"{name}.svg"))
