"""DatumDock 自有 SVG 图标的统一加载入口。"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from datumdock.ui.theme import THEME


class IconRegistry:
    """集中定位品牌资产和工具栏图标，避免界面散落硬编码文件路径。"""

    def __init__(self, root: Path) -> None:
        self.root = root
        self._cache: dict[tuple[str, str, int], QIcon] = {}

    def icon(self, name: str, state: str = "normal", size: int = 24) -> QIcon:
        """按语义和状态渲染 SVG，缺失资源时返回空图标。"""

        key = (name, state, size)
        if key in self._cache:
            return self._cache[key]
        path = self.root / "assets" / "icons" / f"{name}.svg"
        if not path.is_file():
            return QIcon()
        colors = {
            "normal": THEME.tokens.icon,
            "hover": THEME.tokens.brand_primary,
            "active": THEME.tokens.brand_hover,
            "disabled": THEME.tokens.text_disabled,
        }
        color = colors.get(state, colors["normal"])
        source = path.read_text(encoding="utf-8")
        source = source.replace("#5B83E6", color).replace("#78978C", color)
        renderer = QSvgRenderer(QByteArray(source.encode("utf-8")))
        if not renderer.isValid():
            return QIcon()
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        renderer.render(painter, QRectF(0, 0, size, size))
        painter.end()
        icon = QIcon(pixmap)
        self._cache[key] = icon
        return icon

    def exists(self, name: str) -> bool:
        """供测试和组件样例页验证语义图标是否存在。"""

        return (self.root / "assets" / "icons" / f"{name}.svg").is_file()
