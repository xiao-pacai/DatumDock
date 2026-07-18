"""DatumDock 莫兰迪主题令牌与 Qt 样式表。"""

from __future__ import annotations

TOKENS = {
    "app_background": "#F7F6F2",
    "surface": "#FFFFFF",
    "surface_subtle": "#EEF1EE",
    "panel": "#DFE7E4",
    "accent": "#78978C",
    "accent_soft": "#DDE9E1",
    "rose_soft": "#D8B5AE",
    "text_primary": "#35403C",
    "text_secondary": "#68736E",
    "danger": "#B36F68",
}


def application_stylesheet() -> str:
    """返回只消费语义设计令牌的统一 QSS。"""

    return f"""
    QMainWindow, QDialog {{
        background: {TOKENS["app_background"]};
        color: {TOKENS["text_primary"]};
        font-family: "Microsoft YaHei UI", "Segoe UI", sans-serif;
        font-size: 14px;
    }}
    QWidget {{ color: {TOKENS["text_primary"]}; }}
    QToolBar, QStatusBar {{
        background: {TOKENS["surface"]};
        border: 0;
        border-bottom: 1px solid {TOKENS["panel"]};
        spacing: 6px;
        padding: 4px;
    }}
    QStatusBar {{ border-top: 1px solid {TOKENS["panel"]}; border-bottom: 0; }}
    QFrame#sidebar, QFrame#rightPanel {{
        background: {TOKENS["surface"]};
        border: 1px solid {TOKENS["panel"]};
        border-radius: 10px;
    }}
    QLabel#panelTitle {{ font-size: 16px; font-weight: 600; padding: 8px 10px; }}
    QLabel#helpHint {{
        background: {TOKENS["accent_soft"]};
        border-radius: 10px;
        color: {TOKENS["text_secondary"]};
        font-weight: 600;
    }}
    QPushButton {{
        background: {TOKENS["surface"]};
        border: 1px solid {TOKENS["panel"]};
        border-radius: 8px;
        padding: 8px 12px;
    }}
    QPushButton:hover {{ background: {TOKENS["surface_subtle"]}; }}
    QPushButton:focus {{ border: 2px solid {TOKENS["accent"]}; }}
    QPushButton#primaryButton {{ background: {TOKENS["accent"]}; color: white; border: 0; }}
    QPushButton#primaryButton:hover {{ background: #66867B; }}
    QTreeWidget, QListWidget, QLineEdit, QComboBox, QSpinBox {{
        background: {TOKENS["surface"]};
        border: 1px solid {TOKENS["panel"]};
        border-radius: 8px;
        padding: 6px;
    }}
    QTreeWidget::item:selected, QListWidget::item:selected {{
        background: {TOKENS["accent_soft"]};
        color: {TOKENS["text_primary"]};
    }}
    QSplitter::handle {{ background: {TOKENS["panel"]}; width: 1px; }}
    """
