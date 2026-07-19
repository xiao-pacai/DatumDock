"""DatumDock 现代视觉 v2 的语义令牌与全局 Qt 样式。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ThemeTokens:
    """集中保存颜色与布局常量，页面不得自行维护第二套主题。"""

    app_background: str = "#F4F7FC"
    surface: str = "#FFFFFF"
    surface_subtle: str = "#EDF2F8"
    surface_hover: str = "#E8F0FF"
    brand_primary: str = "#5B83E6"
    brand_hover: str = "#4B73D2"
    brand_soft: str = "#E7EEFF"
    brand_orange: str = "#F2A36F"
    brand_cyan: str = "#73B9D2"
    canvas_background: str = "#252B36"
    canvas_surface: str = "#303846"
    text_primary: str = "#253047"
    text_secondary: str = "#6D778A"
    text_muted: str = "#929BAD"
    text_disabled: str = "#B7C0CD"
    icon: str = "#667188"
    border: str = "#DCE3EE"
    focus_ring: str = "#8EB0FF"
    success: str = "#4FA47A"
    warning: str = "#E0A447"
    danger: str = "#D96565"
    info: str = "#4E9CCB"
    annotation_border_alpha: int = 235
    annotation_fill_alpha: int = 38
    canvas_crosshair_light: str = "#FFFFFF"
    canvas_crosshair_dark: str = "#253047"
    canvas_crosshair_underlay_alpha: int = 185
    canvas_crosshair_alpha: int = 150
    canvas_crosshair_underlay_width: int = 2
    canvas_crosshair_width: int = 1
    annotation_line_width: int = 2
    annotation_selected_line_width: int = 3
    annotation_handle_size: int = 8


class ThemeService:
    """把视觉令牌转换为应用级 QSS，并提供统一间距和圆角。"""

    def __init__(self, tokens: ThemeTokens | None = None) -> None:
        self.tokens = tokens or ThemeTokens()

    @staticmethod
    def spacing(name: str) -> int:
        """返回 4px 网格上的常用间距。"""

        return {"xs": 4, "sm": 8, "md": 12, "lg": 16, "xl": 24, "xxl": 32}[name]

    @staticmethod
    def radius(name: str) -> int:
        """返回组件圆角，主页卡片与紧凑工具使用不同层级。"""

        return {"tool": 8, "control": 10, "card": 16}[name]

    def stylesheet(self) -> str:
        """生成只引用语义令牌的全局样式表。"""

        t = self.tokens
        return f"""
        * {{
            font-family: "Segoe UI Variable", "Microsoft YaHei UI", "Segoe UI", sans-serif;
            font-size: 14px;
            color: {t.text_primary};
        }}
        QMainWindow, QDialog, QWidget#appRoot {{ background: {t.app_background}; }}
        QToolTip {{
            background: {t.text_primary}; color: white; border: 0; border-radius: 6px;
            padding: 6px 8px;
        }}
        QLabel#pageTitle {{ font-size: 26px; font-weight: 700; }}
        QLabel#sectionTitle {{ font-size: 17px; font-weight: 650; }}
        QLabel#mutedText, QLabel[muted="true"] {{ color: {t.text_secondary}; }}
        QLabel#previewBanner {{
            background: {t.brand_soft}; color: {t.brand_hover}; border: 1px solid {t.focus_ring};
            border-radius: 8px; padding: 7px 12px; font-weight: 600;
        }}
        QFrame#topBar, QFrame#surface, QFrame#sectionCard, QFrame#rightPanel,
        QFrame#toolRail, QFrame#statusBarSurface {{
            background: {t.surface}; border: 1px solid {t.border}; border-radius: 12px;
        }}
        QFrame#topBar {{ border-radius: 0; border-width: 0 0 1px 0; }}
        QFrame#sectionCard {{ border-radius: 16px; }}
        QFrame#toolRail {{ border-radius: 10px; }}
        QPushButton {{
            min-height: 34px; padding: 0 14px; border: 1px solid {t.border};
            border-radius: 9px; background: {t.surface}; font-weight: 600;
        }}
        QPushButton:hover {{ background: {t.surface_hover}; border-color: {t.focus_ring}; }}
        QPushButton:pressed {{ background: {t.brand_soft}; }}
        QPushButton:focus {{ border: 2px solid {t.focus_ring}; }}
        QPushButton:disabled {{ color: {t.text_muted}; background: {t.surface_subtle}; }}
        QPushButton[role="primary"] {{
            background: {t.brand_primary}; color: white; border-color: {t.brand_primary};
        }}
        QPushButton[role="primary"]:hover {{
            background: {t.brand_hover}; border-color: {t.brand_hover};
        }}
        QPushButton[role="ghost"] {{ background: transparent; border-color: transparent; }}
        QPushButton[role="danger"] {{
            color: white; background: {t.danger}; border-color: {t.danger};
        }}
        QPushButton[role="tool"] {{
            min-width: 42px; max-width: 42px; min-height: 42px; padding: 0;
        }}
        QPushButton[role="tool"]:checked {{
            background: {t.brand_primary}; color: white; border-color: {t.brand_primary};
        }}
        QPushButton[chip="true"] {{
            min-height: 28px; padding: 0 12px; border-radius: 14px; font-weight: 500;
        }}
        QPushButton[chip="true"]:checked {{
            color: {t.brand_hover}; background: {t.brand_soft}; border-color: {t.focus_ring};
        }}
        QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QTextEdit, QPlainTextEdit {{
            min-height: 36px; background: {t.surface}; border: 1px solid {t.border};
            border-radius: 9px; padding: 0 10px; selection-background-color: {t.brand_primary};
        }}
        QTextEdit, QPlainTextEdit {{ padding: 10px; }}
        QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus,
        QTextEdit:focus, QPlainTextEdit:focus {{ border: 2px solid {t.focus_ring}; }}
        QComboBox::drop-down {{ border: 0; width: 28px; }}
        QMenu {{
            background: {t.surface}; border: 1px solid {t.border}; border-radius: 10px;
            padding: 6px;
        }}
        QMenu::item {{ padding: 8px 28px 8px 10px; border-radius: 7px; }}
        QMenu::item:selected {{ background: {t.surface_hover}; }}
        QTableWidget, QTableView, QListWidget, QTreeWidget {{
            background: {t.surface}; alternate-background-color: {t.surface_subtle};
            border: 1px solid {t.border}; border-radius: 10px; gridline-color: {t.border};
            outline: 0;
        }}
        QHeaderView::section {{
            background: {t.surface_subtle}; border: 0; border-bottom: 1px solid {t.border};
            padding: 9px; font-weight: 650;
        }}
        QTableWidget::item, QListWidget::item, QTreeWidget::item {{ padding: 7px; }}
        QTableWidget::item:selected, QListWidget::item:selected, QTreeWidget::item:selected {{
            color: {t.text_primary}; background: {t.brand_soft};
        }}
        QTabWidget::pane {{
            border: 1px solid {t.border}; border-radius: 10px; background: {t.surface};
        }}
        QTabBar::tab {{
            min-height: 34px; padding: 0 16px; color: {t.text_secondary};
            background: transparent; border: 0;
        }}
        QTabBar::tab:selected {{
            color: {t.brand_hover}; border-bottom: 2px solid {t.brand_primary};
        }}
        QProgressBar {{
            min-height: 10px; max-height: 10px; border: 0; border-radius: 5px;
            background: {t.surface_subtle}; text-align: center;
        }}
        QProgressBar::chunk {{ background: {t.brand_primary}; border-radius: 5px; }}
        QScrollArea {{ border: 0; background: transparent; }}
        QScrollBar:vertical {{ width: 10px; background: transparent; margin: 2px; }}
        QScrollBar::handle:vertical {{
            background: {t.border}; min-height: 32px; border-radius: 4px;
        }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
        QSplitter::handle {{ background: {t.border}; }}
        QSplitter::handle:hover {{ background: {t.focus_ring}; }}
        QStatusBar {{ background: {t.surface}; border-top: 1px solid {t.border}; }}
        QWizard {{ background: {t.app_background}; }}
        """


THEME = ThemeService()
TOKENS = {
    "app_background": THEME.tokens.app_background,
    "surface": THEME.tokens.surface,
    "surface_subtle": THEME.tokens.surface_subtle,
    "panel": THEME.tokens.border,
    "accent": THEME.tokens.brand_primary,
    "accent_soft": THEME.tokens.brand_soft,
    "rose_soft": THEME.tokens.brand_orange,
    "text_primary": THEME.tokens.text_primary,
    "text_secondary": THEME.tokens.text_secondary,
    "danger": THEME.tokens.danger,
}


def application_stylesheet() -> str:
    """兼容旧入口，统一返回现代视觉 v2 样式。"""

    return THEME.stylesheet()
