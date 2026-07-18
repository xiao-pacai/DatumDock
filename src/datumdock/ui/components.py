"""DatumDock 现代视觉 v2 的可复用 PySide6 组件。"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QKeyEvent,
    QKeySequence,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from datumdock.i18n.catalog import LocaleService, tr
from datumdock.resources import resource_root
from datumdock.ui.prototype_models import DatasetCardViewData, DatasetHealth, ImageStatus
from datumdock.ui.theme import THEME


class BrandLockup(QLabel):
    """按可用空间显示完整字标或 DD 标记。"""

    def __init__(self, compact: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.compact = compact
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setAccessibleName("DatumDock")
        self.refresh()

    def refresh(self) -> None:
        """加载仓库自有品牌资产，缺失时保留可读文字。"""

        filename = "datumdock-app-icon.png" if self.compact else "datumdock-wordmark-v3.png"
        path = resource_root() / "assets" / "brand" / filename
        pixmap = QPixmap(str(path))
        if pixmap.isNull():
            self.setText("DD" if self.compact else "DatumDock")
            return
        width = 38 if self.compact else 170
        self.setPixmap(pixmap.scaledToWidth(width, Qt.TransformationMode.SmoothTransformation))
        self.setFixedHeight(42 if self.compact else 48)


class PreviewBanner(QFrame):
    """持续标明预览边界，避免演示内容被误认为真实数据。"""

    def __init__(self, locale: LocaleService, preview_mode: bool, parent: QWidget | None = None):
        super().__init__(parent)
        self.locale = locale
        self.preview_mode = preview_mode
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.label = QLabel()
        self.label.setObjectName("previewBanner")
        layout.addWidget(self.label)
        self.retranslate_ui()

    def retranslate_ui(self) -> None:
        """切换语言时保持同一预览状态。"""

        key = "prototype.banner.preview" if self.preview_mode else "prototype.banner.normal"
        self.label.setText(tr(self.locale, key))


class PageHeader(QWidget):
    """统一页面标题、说明与右侧操作区域。"""

    def __init__(
        self,
        locale: LocaleService,
        title_key: str,
        subtitle_key: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.locale = locale
        self.title_key = title_key
        self.subtitle_key = subtitle_key
        self.layout = QHBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 0)
        copy = QVBoxLayout()
        copy.setSpacing(4)
        self.title = QLabel()
        self.title.setObjectName("pageTitle")
        self.subtitle = QLabel()
        self.subtitle.setObjectName("mutedText")
        self.subtitle.setWordWrap(True)
        copy.addWidget(self.title)
        copy.addWidget(self.subtitle)
        self.layout.addLayout(copy, 1)
        self.retranslate_ui()

    def add_action(self, button: QPushButton) -> None:
        """把页面级操作放到一致的标题右侧。"""

        self.layout.addWidget(button, 0, Qt.AlignmentFlag.AlignBottom)

    def retranslate_ui(self) -> None:
        """刷新页面标题和说明。"""

        self.title.setText(tr(self.locale, self.title_key))
        self.subtitle.setText(tr(self.locale, self.subtitle_key))


class SectionCard(QFrame):
    """用留白和轻边框组织内容，替代旧式 QGroupBox。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("sectionCard")
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(18, 18, 18, 18)
        self.body.setSpacing(12)


class PrimaryButton(QPushButton):
    """页面唯一主操作按钮。"""

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setProperty("role", "primary")
        self.setCursor(Qt.CursorShape.PointingHandCursor)


class GhostButton(QPushButton):
    """用于低频或导航操作的无边框按钮。"""

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setProperty("role", "ghost")
        self.setCursor(Qt.CursorShape.PointingHandCursor)


class DangerButton(QPushButton):
    """只在明确确认区域出现的危险操作按钮。"""

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setProperty("role", "danger")
        self.setCursor(Qt.CursorShape.PointingHandCursor)


class ToolButton(QPushButton):
    """标注工具栏使用的可切换方形按钮。"""

    def __init__(self, tooltip: str, icon_text: str, parent: QWidget | None = None) -> None:
        super().__init__(icon_text, parent)
        self.setProperty("role", "tool")
        self.setCheckable(True)
        self.setToolTip(tooltip)
        self.setAccessibleName(tooltip)
        font = QFont(self.font())
        font.setPixelSize(17)
        self.setFont(font)
        self.setCursor(Qt.CursorShape.PointingHandCursor)


class HelpButton(QPushButton):
    """设置表单中的圆形问号帮助入口。"""

    def __init__(self, help_text: str = "", parent: QWidget | None = None) -> None:
        super().__init__("?", parent)
        self.setProperty("role", "ghost")
        self.setFixedSize(26, 26)
        self.setToolTip(help_text)
        self.setAccessibleName(help_text)
        self.setStyleSheet("border-radius:13px; font-weight:700;")


class ShortcutRecorder(QPushButton):
    """点击后捕获下一组按键，只更新当前 UI 预览会话。"""

    recorded = Signal(str)

    def __init__(self, sequence: str, parent: QWidget | None = None) -> None:
        super().__init__(sequence, parent)
        self.sequence = sequence
        self.prompt = "…"
        self.recording = False
        self.clicked.connect(self.start_recording)

    def start_recording(self) -> None:
        """进入录入状态并等待键盘事件。"""

        self.recording = True
        self.setText(self.prompt)
        self.setFocus(Qt.FocusReason.MouseFocusReason)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        """把 Qt 按键组合转成平台原生可读文本。"""

        if not self.recording:
            return super().keyPressEvent(event)
        if event.key() == Qt.Key.Key_Escape:
            self.recording = False
            self.setText(self.sequence)
            return
        if event.key() in {
            Qt.Key.Key_Control,
            Qt.Key.Key_Shift,
            Qt.Key.Key_Alt,
            Qt.Key.Key_Meta,
        }:
            return
        sequence = QKeySequence(event.keyCombination()).toString(
            QKeySequence.SequenceFormat.NativeText
        )
        if not sequence:
            return
        self.sequence = sequence
        self.recording = False
        self.setText(sequence)
        self.recorded.emit(sequence)

    def set_prompt(self, prompt: str) -> None:
        """刷新录入提示，非录入状态不改变当前快捷键。"""

        self.prompt = prompt
        if self.recording:
            self.setText(prompt)


class FilterChip(QPushButton):
    """可组合的圆角筛选条件。"""

    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setProperty("chip", True)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)


class SearchBox(QLineEdit):
    """带清除能力的统一搜索输入。"""

    def __init__(self, placeholder: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setPlaceholderText(placeholder)
        self.setClearButtonEnabled(True)
        self.setAccessibleName(placeholder)


class StatusBadge(QLabel):
    """同时使用文字、符号和颜色表达图片级状态。"""

    def __init__(
        self,
        locale: LocaleService,
        status: ImageStatus,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.locale = locale
        self.status = status
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumHeight(26)
        self.retranslate_ui()

    def retranslate_ui(self) -> None:
        """状态文案翻译后仍保留稳定状态值。"""

        colors = {
            ImageStatus.UNLABELED: (THEME.tokens.text_secondary, THEME.tokens.surface_subtle, "○"),
            ImageStatus.PENDING: ("#9B681E", "#FFF3D8", "◷"),
            ImageStatus.COMPLETED: ("#2F7A56", "#E4F4EB", "✓"),
            ImageStatus.NEGATIVE: ("#39705C", "#E6F2ED", "–"),
            ImageStatus.ISSUE: ("#9B681E", "#FFF0DA", "!"),
            ImageStatus.ERROR: ("#A94343", "#FCE8E8", "×"),
        }
        foreground, background, icon = colors[self.status]
        self.setText(f"{icon}  {tr(self.locale, f'status.{self.status.value}')}")
        self.setStyleSheet(
            f"color:{foreground}; background:{background}; border-radius:13px; "
            "padding:3px 9px; font-weight:600;"
        )


class CoverPreview(QWidget):
    """在内存中绘制抽象数据集封面，不使用第三方照片或磁盘文件。"""

    def __init__(self, seed: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.seed = seed
        self.setMinimumHeight(118)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def paintEvent(self, event: object) -> None:
        """绘制克制的图片、框和标签抽象图形。"""

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0, 0, -1, -1)
        path = QPainterPath()
        path.addRoundedRect(rect, 12, 12)
        painter.setClipPath(path)
        gradient = QLinearGradient(rect.topLeft(), rect.bottomRight())
        palettes = [
            ("#DDEAFF", "#F7DCC9"),
            ("#D9F0EC", "#DDE6FA"),
            ("#E9E0F5", "#DCECF4"),
            ("#F4DEDE", "#E1E7F1"),
        ]
        start, end = palettes[self.seed % len(palettes)]
        gradient.setColorAt(0, QColor(start))
        gradient.setColorAt(1, QColor(end))
        painter.fillRect(rect, gradient)
        painter.setBrush(QColor(255, 255, 255, 170))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(rect.adjusted(28, 24, -35, -25), 16, 16)
        painter.setBrush(QColor("#9DAFC8"))
        painter.drawRoundedRect(rect.adjusted(65, 47, -70, -47), 8, 8)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(THEME.tokens.brand_primary), 3))
        painter.drawRoundedRect(rect.adjusted(50, 35, -56, -34), 6, 6)


class DatasetCard(SectionCard):
    """主页数据集卡片，保持主操作清晰并收起低频操作。"""

    opened = Signal(str)
    diagnostics_requested = Signal(str)

    def __init__(
        self,
        locale: LocaleService,
        data: DatasetCardViewData,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.locale = locale
        self.data = data
        self.setMinimumWidth(270)
        self.setMaximumWidth(380)
        self.body.addWidget(CoverPreview(data.cover_seed))
        title_row = QHBoxLayout()
        title = QLabel(data.name)
        title.setObjectName("sectionTitle")
        title_row.addWidget(title, 1)
        more = GhostButton("•••")
        more.setAccessibleName(tr(locale, "nav.more"))
        more.setToolTip(tr(locale, "nav.more"))
        more.setFixedWidth(38)
        title_row.addWidget(more)
        self.body.addLayout(title_row)
        description = QLabel(data.description)
        description.setObjectName("mutedText")
        description.setWordWrap(True)
        description.setMinimumHeight(40)
        self.body.addWidget(description)
        stats = QLabel()
        stats.setObjectName("mutedText")
        stats.setText(
            f"{data.image_count:,} {tr(locale, 'home.images')}   ·   "
            f"{data.label_count} {tr(locale, 'home.labels')}"
        )
        self.body.addWidget(stats)
        progress = QFrame()
        progress.setFixedHeight(6)
        progress.setStyleSheet(
            f"background: qlineargradient(x1:0,y1:0,x2:1,y2:0, "
            f"stop:0 {THEME.tokens.brand_primary}, stop:{data.reviewed_percent / 100:.2f} "
            f"{THEME.tokens.brand_primary}, stop:{data.reviewed_percent / 100:.2f} "
            f"{THEME.tokens.surface_subtle}, stop:1 {THEME.tokens.surface_subtle}); "
            "border-radius:3px;"
        )
        self.body.addWidget(progress)
        footer = QHBoxLayout()
        review = QLabel(tr(locale, "home.reviewed").format(percent=data.reviewed_percent))
        review.setObjectName("mutedText")
        footer.addWidget(review, 1)
        modified = QLabel(data.modified_text)
        modified.setObjectName("mutedText")
        footer.addWidget(modified)
        self.body.addLayout(footer)
        action = PrimaryButton()
        if data.health == DatasetHealth.DAMAGED:
            action.setText(tr(locale, "home.diagnostics"))
            action.clicked.connect(lambda: self.diagnostics_requested.emit(data.id))
        elif data.health == DatasetHealth.LOADING:
            action.setText(tr(locale, "home.loading"))
            action.setEnabled(False)
        else:
            action.setText(tr(locale, "home.open"))
            action.clicked.connect(lambda: self.opened.emit(data.id))
        self.body.addWidget(action)


class TutorialCard(SectionCard):
    """学习中心使用的轻量教程卡片。"""

    activated = Signal(str)

    def __init__(
        self,
        locale: LocaleService,
        tutorial_id: str,
        title_key: str,
        summary_key: str,
        accent: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.locale = locale
        self.tutorial_id = tutorial_id
        self.title_key = title_key
        self.summary_key = summary_key
        icon = QLabel("▣")
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setFixedSize(42, 42)
        icon.setStyleSheet(
            f"background:{accent}; color:white; border-radius:12px; "
            "font-size:20px; font-weight:700;"
        )
        self.body.addWidget(icon)
        self.title = QLabel()
        self.title.setObjectName("sectionTitle")
        self.body.addWidget(self.title)
        self.summary = QLabel()
        self.summary.setObjectName("mutedText")
        self.summary.setWordWrap(True)
        self.body.addWidget(self.summary)
        self.body.addStretch()
        self.button = GhostButton()
        self.button.clicked.connect(lambda: self.activated.emit(tutorial_id))
        self.body.addWidget(self.button)
        self.retranslate_ui()

    def retranslate_ui(self) -> None:
        """语言切换时刷新卡片标题、摘要和操作文案。"""

        self.title.setText(tr(self.locale, self.title_key))
        self.summary.setText(tr(self.locale, self.summary_key))
        self.button.setText(tr(self.locale, "action.continue"))


class StatCard(SectionCard):
    """管理页面使用的紧凑统计摘要。"""

    def __init__(self, value: str, label: str, accent: str, parent: QWidget | None = None):
        super().__init__(parent)
        value_label = QLabel(value)
        value_label.setStyleSheet(f"font-size:25px; font-weight:700; color:{accent};")
        self.label_widget = QLabel(label)
        self.label_widget.setObjectName("mutedText")
        self.body.addWidget(value_label)
        self.body.addWidget(self.label_widget)

    def set_label(self, label: str) -> None:
        """刷新统计卡片标签，不改动业务数值。"""

        self.label_widget.setText(label)


class EmptyState(SectionCard):
    """为无内容页面提供说明和单一下一步，而不是空白表格。"""

    action_requested = Signal()

    def __init__(
        self,
        title: str,
        body: str,
        action: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.body.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon = QLabel("▧")
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setStyleSheet(
            f"font-size:38px; color:{THEME.tokens.brand_primary}; "
            f"background:{THEME.tokens.brand_soft}; "
            "border-radius:18px; padding:18px;"
        )
        title_label = QLabel(title)
        title_label.setObjectName("sectionTitle")
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        body_label = QLabel(body)
        body_label.setObjectName("mutedText")
        body_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        body_label.setWordWrap(True)
        button = PrimaryButton(action)
        button.clicked.connect(self.action_requested)
        self.body.addStretch()
        self.body.addWidget(icon, 0, Qt.AlignmentFlag.AlignCenter)
        self.body.addWidget(title_label)
        self.body.addWidget(body_label)
        self.body.addWidget(button, 0, Qt.AlignmentFlag.AlignCenter)
        self.body.addStretch()


class ToastOverlay(QLabel):
    """在主窗口右上角显示克制的短时状态提示。"""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setWordWrap(True)
        self.setMinimumWidth(320)
        self.setMaximumWidth(420)
        self.setStyleSheet(
            f"background:{THEME.tokens.text_primary}; color:white; border-radius:10px; "
            "padding:12px 16px; font-weight:600;"
        )
        self.hide()

    def show_message(self, message: str, duration_ms: int = 3200) -> None:
        """显示消息并自动隐藏；错误可由调用方延长时间。"""

        self.setText(message)
        self.adjustSize()
        parent = self.parentWidget()
        self.move(max(16, parent.width() - self.width() - 24), 74)
        self.raise_()
        self.show()
        QTimer.singleShot(duration_ms, self.hide)


def clear_layout(layout: QLayout) -> None:
    """安全释放动态卡片，避免主页筛选后残留旧控件。"""

    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        child = item.layout()
        if widget is not None:
            widget.hide()
            widget.setParent(None)
            widget.deleteLater()
        elif child is not None:
            clear_layout(child)


def connect_button(button: QPushButton, callback: Callable[[], None]) -> QPushButton:
    """连接无参数按钮回调并返回按钮，便于声明式组合标题操作。"""

    button.clicked.connect(callback)
    return button


def brand_asset_path(filename: str) -> Path:
    """统一返回品牌资产路径，页面不散落资源定位逻辑。"""

    return resource_root() / "assets" / "brand" / filename
