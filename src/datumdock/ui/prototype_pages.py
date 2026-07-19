"""DatumDock UI 原型的主页、教程、管理和设置页面。"""

from __future__ import annotations

from enum import StrEnum

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPixmap, QResizeEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QListWidget,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from datumdock.domain.models import AppSettings
from datumdock.i18n.catalog import LocaleService, tr
from datumdock.services.settings import AppSettingsError
from datumdock.services.shortcuts import (
    ActionGroup,
    ActionRegistry,
    ShortcutProfileService,
)
from datumdock.ui.components import (
    BrandLockup,
    DangerButton,
    DatasetCard,
    EmptyState,
    FilterChip,
    GhostButton,
    HelpButton,
    PageHeader,
    PreviewBanner,
    PrimaryButton,
    SearchBox,
    SectionCard,
    ShortcutRecorder,
    StatCard,
    StatusBadge,
    TutorialCard,
    brand_asset_path,
    clear_layout,
)
from datumdock.ui.prototype_models import HomeSnapshot, ImageStatus, WorkspaceSnapshot
from datumdock.ui.theme import THEME


class RouteId(StrEnum):
    """所有可访问页面的稳定路由标识。"""

    STARTUP = "startup"
    HOME = "home"
    LEARNING_CENTER = "learning_center"
    TUTORIAL_READER = "tutorial_reader"
    RELEASE_NOTES = "release_notes"
    ABOUT = "about"
    ANNOTATION_WORKSPACE = "annotation_workspace"
    LABEL_MANAGER = "label_manager"
    LABEL_INSPECTION = "label_inspection"
    LABEL_COMPARISON = "label_comparison"
    MODEL_MANAGER = "model_manager"
    SIMILARITY_REVIEW = "similarity_review"
    TRASH = "trash"
    DATASET_OVERVIEW = "dataset_overview"
    SETTINGS = "settings"
    COMPONENT_GALLERY = "component_gallery"


class BasePage(QWidget):
    """为页面提供统一导航和重翻译信号。"""

    route_requested = Signal(str)
    dialog_requested = Signal(str)
    command_requested = Signal(str, dict)
    message_requested = Signal(str)

    def retranslate_ui(self) -> None:
        """子类覆盖后刷新当前可见系统文案。"""


class StartupPage(BasePage):
    """短时启动加载状态，避免使用空白窗口。"""

    def __init__(self, locale: LocaleService, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.locale = locale
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(BrandLockup(False), 0, Qt.AlignmentFlag.AlignCenter)
        self.title = QLabel("DatumDock")
        self.title.setObjectName("pageTitle")
        self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.subtitle = QLabel()
        self.subtitle.setObjectName("mutedText")
        self.subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        progress = QProgressBar()
        progress.setRange(0, 0)
        progress.setFixedWidth(260)
        layout.addSpacing(22)
        layout.addWidget(self.title)
        layout.addWidget(self.subtitle)
        layout.addSpacing(14)
        layout.addWidget(progress, 0, Qt.AlignmentFlag.AlignCenter)
        self.retranslate_ui()

    def retranslate_ui(self) -> None:
        self.subtitle.setText(tr(self.locale, "home.loading"))


class HomePage(BasePage):
    """游戏存档式数据集主页，教程不会遮挡数据集主入口。"""

    def __init__(
        self,
        locale: LocaleService,
        preview_mode: bool,
        snapshot: HomeSnapshot,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.locale = locale
        self.preview_mode = preview_mode
        self.snapshot = snapshot
        self._column_count = 3
        self._build_ui()
        self.retranslate_ui()
        self._rebuild_dataset_cards()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        top = QFrame()
        top.setObjectName("topBar")
        top_layout = QHBoxLayout(top)
        top_layout.setContentsMargins(28, 8, 28, 8)
        top_layout.addWidget(BrandLockup(False))
        top_layout.addStretch()
        self.learning_button = GhostButton()
        self.learning_button.clicked.connect(
            lambda: self.route_requested.emit(RouteId.LEARNING_CENTER.value)
        )
        self.release_button = GhostButton()
        self.release_button.clicked.connect(
            lambda: self.route_requested.emit(RouteId.RELEASE_NOTES.value)
        )
        self.settings_button = GhostButton()
        self.settings_button.clicked.connect(
            lambda: self.route_requested.emit(RouteId.SETTINGS.value)
        )
        self.about_button = GhostButton()
        self.about_button.clicked.connect(lambda: self.route_requested.emit(RouteId.ABOUT.value))
        self.gallery_button = GhostButton()
        self.gallery_button.setVisible(self.preview_mode)
        self.gallery_button.clicked.connect(
            lambda: self.route_requested.emit(RouteId.COMPONENT_GALLERY.value)
        )
        for button in (
            self.learning_button,
            self.release_button,
            self.gallery_button,
            self.settings_button,
            self.about_button,
        ):
            top_layout.addWidget(button)
        root.addWidget(top)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        self.content_layout = QVBoxLayout(content)
        self.content_layout.setContentsMargins(30, 22, 30, 36)
        self.content_layout.setSpacing(24)
        banner_row = QHBoxLayout()
        self.banner = PreviewBanner(self.locale, self.preview_mode)
        banner_row.addWidget(self.banner)
        banner_row.addStretch()
        self.content_layout.addLayout(banner_row)
        self.content_layout.addWidget(self._build_hero())
        self.content_layout.addWidget(self._build_quick_start())
        self.content_layout.addLayout(self._build_dataset_section())
        self.content_layout.addLayout(self._build_learning_section())
        scroll.setWidget(content)
        root.addWidget(scroll, 1)

    def _build_hero(self) -> QWidget:
        hero = SectionCard()
        hero.setStyleSheet(
            f"QFrame#sectionCard {{ background:{THEME.tokens.brand_soft}; "
            f"border:1px solid {THEME.tokens.focus_ring}; border-radius:18px; }}"
        )
        self.hero_eyebrow = QLabel()
        self.hero_eyebrow.setStyleSheet(
            f"color:{THEME.tokens.brand_hover}; font-weight:700; letter-spacing:1px;"
        )
        self.hero_title = QLabel()
        self.hero_title.setObjectName("pageTitle")
        self.hero_subtitle = QLabel()
        self.hero_subtitle.setObjectName("mutedText")
        self.hero_subtitle.setWordWrap(True)
        self.hero_new = PrimaryButton()
        self.hero_new.clicked.connect(lambda: self.dialog_requested.emit("create_dataset"))
        self.hero_template = GhostButton()
        self.hero_template.clicked.connect(
            lambda: self.dialog_requested.emit("create_from_template")
        )
        hero.body.addWidget(self.hero_eyebrow)
        hero.body.addWidget(self.hero_title)
        hero.body.addWidget(self.hero_subtitle)
        actions = QHBoxLayout()
        actions.addWidget(self.hero_new)
        actions.addWidget(self.hero_template)
        actions.addStretch()
        hero.body.addLayout(actions)
        return hero

    def _build_quick_start(self) -> QWidget:
        card = SectionCard()
        heading = QHBoxLayout()
        copy = QVBoxLayout()
        self.quick_title = QLabel()
        self.quick_title.setObjectName("sectionTitle")
        self.quick_subtitle = QLabel()
        self.quick_subtitle.setObjectName("mutedText")
        copy.addWidget(self.quick_title)
        copy.addWidget(self.quick_subtitle)
        heading.addLayout(copy, 1)
        progress = QLabel(f"{self.snapshot.quick_start_completed} / 5")
        progress.setStyleSheet(
            f"background:{THEME.tokens.brand_soft}; color:{THEME.tokens.brand_hover}; "
            "border-radius:14px; padding:5px 12px; font-weight:700;"
        )
        heading.addWidget(progress)
        card.body.addLayout(heading)
        self.quick_row = QHBoxLayout()
        self.quick_labels: list[QLabel] = []
        for index in range(5):
            step = QFrame()
            step.setObjectName("surface")
            step_layout = QVBoxLayout(step)
            number = QLabel("✓" if index < self.snapshot.quick_start_completed else str(index + 1))
            number.setAlignment(Qt.AlignmentFlag.AlignCenter)
            number.setFixedSize(30, 30)
            background = (
                THEME.tokens.brand_primary
                if index < self.snapshot.quick_start_completed
                else THEME.tokens.surface_subtle
            )
            foreground = (
                "white"
                if index < self.snapshot.quick_start_completed
                else THEME.tokens.text_secondary
            )
            number.setStyleSheet(
                f"background:{background}; color:{foreground}; border-radius:15px; font-weight:700;"
            )
            label = QLabel()
            label.setWordWrap(True)
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.quick_labels.append(label)
            step_layout.addWidget(number, 0, Qt.AlignmentFlag.AlignCenter)
            step_layout.addWidget(label)
            self.quick_row.addWidget(step, 1)
        card.body.addLayout(self.quick_row)
        return card

    def _build_dataset_section(self) -> QVBoxLayout:
        section = QVBoxLayout()
        title_row = QHBoxLayout()
        self.datasets_title = QLabel()
        self.datasets_title.setObjectName("pageTitle")
        title_row.addWidget(self.datasets_title)
        title_row.addStretch()
        self.dataset_search = SearchBox()
        self.dataset_search.setMaximumWidth(320)
        self.dataset_search.textChanged.connect(self._rebuild_dataset_cards)
        self.dataset_sort = QComboBox()
        self.dataset_sort.currentIndexChanged.connect(self._rebuild_dataset_cards)
        self.archive_filter = QComboBox()
        self.archive_filter.currentIndexChanged.connect(self._rebuild_dataset_cards)
        self.template_button = GhostButton()
        self.template_button.clicked.connect(
            lambda: self.dialog_requested.emit("create_from_template")
        )
        self.new_button = PrimaryButton()
        self.new_button.clicked.connect(lambda: self.dialog_requested.emit("create_dataset"))
        title_row.addWidget(self.dataset_search)
        title_row.addWidget(self.dataset_sort)
        title_row.addWidget(self.archive_filter)
        title_row.addWidget(self.template_button)
        title_row.addWidget(self.new_button)
        section.addLayout(title_row)
        self.dataset_grid = QGridLayout()
        self.dataset_grid.setSpacing(16)
        self.dataset_grid.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        section.addLayout(self.dataset_grid)
        return section

    def _build_learning_section(self) -> QVBoxLayout:
        section = QVBoxLayout()
        title_row = QHBoxLayout()
        copy = QVBoxLayout()
        self.learning_title = QLabel()
        self.learning_title.setObjectName("pageTitle")
        self.learning_subtitle = QLabel()
        self.learning_subtitle.setObjectName("mutedText")
        copy.addWidget(self.learning_title)
        copy.addWidget(self.learning_subtitle)
        title_row.addLayout(copy, 1)
        self.learning_all = GhostButton()
        self.learning_all.clicked.connect(
            lambda: self.route_requested.emit(RouteId.LEARNING_CENTER.value)
        )
        title_row.addWidget(self.learning_all)
        section.addLayout(title_row)
        cards = QHBoxLayout()
        for tutorial_id, title_key, summary_key, accent in (
            (
                "datumdock",
                "tutorial.datumdock",
                "tutorial.datumdock.summary",
                THEME.tokens.brand_primary,
            ),
            ("yolo", "tutorial.yolo", "tutorial.yolo.summary", THEME.tokens.brand_cyan),
            ("leakage", "tutorial.leakage", "tutorial.leakage.summary", THEME.tokens.brand_orange),
        ):
            card = TutorialCard(self.locale, tutorial_id, title_key, summary_key, accent)
            card.activated.connect(
                lambda identifier: self.route_requested.emit(RouteId.TUTORIAL_READER.value)
            )
            cards.addWidget(card, 1)
        section.addLayout(cards)
        return section

    def update_snapshot(self, snapshot: HomeSnapshot) -> None:
        """预览创建数据集后只刷新内存卡片。"""

        self.snapshot = snapshot
        self._rebuild_dataset_cards()

    def retranslate_ui(self) -> None:
        self.banner.retranslate_ui()
        self.learning_button.setText(tr(self.locale, "nav.learning"))
        self.release_button.setText(tr(self.locale, "nav.release_notes"))
        self.gallery_button.setText(tr(self.locale, "nav.components"))
        self.settings_button.setText(tr(self.locale, "nav.settings"))
        self.about_button.setText(tr(self.locale, "nav.about"))
        self.hero_eyebrow.setText(tr(self.locale, "home.eyebrow"))
        self.hero_title.setText(tr(self.locale, "home.title"))
        self.hero_subtitle.setText(tr(self.locale, "home.subtitle"))
        self.hero_new.setText(tr(self.locale, "home.new_dataset"))
        self.hero_template.setText(tr(self.locale, "home.from_template"))
        self.quick_title.setText(tr(self.locale, "home.quick_start"))
        self.quick_subtitle.setText(tr(self.locale, "home.quick_subtitle"))
        for label, key in zip(
            self.quick_labels,
            ("quick.create", "quick.labels", "quick.import", "quick.annotate", "quick.export"),
            strict=True,
        ):
            label.setText(tr(self.locale, key))
        self.datasets_title.setText(tr(self.locale, "home.datasets"))
        self.dataset_search.setPlaceholderText(tr(self.locale, "home.search"))
        current_sort = self.dataset_sort.currentData()
        self.dataset_sort.clear()
        for key, value in (
            ("home.sort.modified", "modified"),
            ("home.sort.created", "created"),
            ("home.sort.name", "name"),
        ):
            self.dataset_sort.addItem(tr(self.locale, key), value)
        for index in range(self.dataset_sort.count()):
            if self.dataset_sort.itemData(index) == current_sort:
                self.dataset_sort.setCurrentIndex(index)
        current_filter = self.archive_filter.currentData()
        self.archive_filter.clear()
        for key, value in (
            ("home.filter.active", "active"),
            ("home.filter.archived", "archived"),
            ("home.filter.all", "all"),
        ):
            self.archive_filter.addItem(tr(self.locale, key), value)
        for index in range(self.archive_filter.count()):
            if self.archive_filter.itemData(index) == current_filter:
                self.archive_filter.setCurrentIndex(index)
                break
        self.template_button.setText(tr(self.locale, "home.from_template"))
        self.new_button.setText("＋  " + tr(self.locale, "home.new_dataset"))
        has_template_source = any(
            not item.archived and item.health.value == "ready" for item in self.snapshot.datasets
        )
        self.hero_template.setEnabled(has_template_source)
        self.template_button.setEnabled(has_template_source)
        template_tip = (
            tr(self.locale, "home.template_help")
            if has_template_source
            else tr(self.locale, "home.template_empty")
        )
        self.hero_template.setToolTip(template_tip)
        self.template_button.setToolTip(template_tip)
        self.learning_title.setText(tr(self.locale, "home.learning"))
        self.learning_subtitle.setText(tr(self.locale, "home.learning_subtitle"))
        self.learning_all.setText(tr(self.locale, "home.view_all"))
        for card in self.findChildren(TutorialCard):
            card.retranslate_ui()
        self._rebuild_dataset_cards()

    def _rebuild_dataset_cards(self) -> None:
        clear_layout(self.dataset_grid)
        query = self.dataset_search.text().strip().casefold()
        datasets = [item for item in self.snapshot.datasets if query in item.name.casefold()]
        archive_filter = self.archive_filter.currentData() or "active"
        if archive_filter == "active":
            datasets = [item for item in datasets if not item.archived]
        elif archive_filter == "archived":
            datasets = [item for item in datasets if item.archived]
        sort_mode = self.dataset_sort.currentData() or "modified"
        if sort_mode == "name":
            datasets.sort(key=lambda item: item.name.casefold())
        elif sort_mode == "created":
            datasets.sort(key=lambda item: item.created_sort, reverse=True)
        else:
            datasets.sort(key=lambda item: item.modified_sort, reverse=True)
        if not datasets:
            empty = EmptyState(
                tr(self.locale, "home.empty.title"),
                tr(self.locale, "home.empty.body"),
                tr(self.locale, "home.empty.action"),
            )
            empty.action_requested.connect(lambda: self.dialog_requested.emit("create_dataset"))
            self.dataset_grid.addWidget(empty, 0, 0, 1, self._column_count)
            return
        for position, dataset in enumerate(datasets):
            card = DatasetCard(self.locale, dataset)
            card.opened.connect(
                lambda dataset_id: self.route_requested.emit(
                    f"{RouteId.ANNOTATION_WORKSPACE.value}:{dataset_id}"
                )
            )
            card.diagnostics_requested.connect(
                lambda dataset_id: self.dialog_requested.emit(f"dataset_diagnostics:{dataset_id}")
            )
            card.rename_requested.connect(
                lambda dataset_id: self.dialog_requested.emit(f"rename_dataset:{dataset_id}")
            )
            card.archive_requested.connect(
                lambda dataset_id: self.dialog_requested.emit(f"archive_dataset:{dataset_id}")
            )
            card.restore_requested.connect(
                lambda dataset_id: self.command_requested.emit(
                    "dataset.restore", {"dataset_id": dataset_id}
                )
            )
            self.dataset_grid.addWidget(
                card,
                position // self._column_count,
                position % self._column_count,
            )

    def resizeEvent(self, event: QResizeEvent) -> None:
        """在常见 Windows 窗口宽度下自动调整数据集卡片列数。"""

        columns = 1 if self.width() < 920 else 2 if self.width() < 1280 else 3
        if columns != self._column_count:
            self._column_count = columns
            self._rebuild_dataset_cards()
        super().resizeEvent(event)


class LearningCenterPage(BasePage):
    """全部离线教程入口的响应式卡片页。"""

    def __init__(self, locale: LocaleService, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.locale = locale
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 28)
        self.header = PageHeader(locale, "learning.title", "learning.subtitle")
        back = GhostButton()
        back.clicked.connect(lambda: self.route_requested.emit(RouteId.HOME.value))
        self.back_button = back
        self.header.add_action(back)
        layout.addWidget(self.header)
        grid = QGridLayout()
        grid.setSpacing(16)
        tutorials = (
            (
                "datumdock",
                "tutorial.datumdock",
                "tutorial.datumdock.summary",
                THEME.tokens.brand_primary,
            ),
            ("yolo", "tutorial.yolo", "tutorial.yolo.summary", THEME.tokens.brand_cyan),
            ("review", "tutorial.review", "tutorial.review.summary", THEME.tokens.success),
            ("leakage", "tutorial.leakage", "tutorial.leakage.summary", THEME.tokens.brand_orange),
            ("backup", "tutorial.backup", "tutorial.backup.summary", "#9A83C8"),
        )
        for index, spec in enumerate(tutorials):
            card = TutorialCard(locale, *spec)
            card.activated.connect(
                lambda identifier: self.route_requested.emit(RouteId.TUTORIAL_READER.value)
            )
            grid.addWidget(card, index // 3, index % 3)
        layout.addLayout(grid)
        layout.addStretch()
        self.retranslate_ui()

    def retranslate_ui(self) -> None:
        self.header.retranslate_ui()
        self.back_button.setText(tr(self.locale, "nav.back"))
        for card in self.findChildren(TutorialCard):
            card.retranslate_ui()


class TutorialReaderPage(BasePage):
    """带目录、章节进度和双语正文的应用内教程阅读器。"""

    def __init__(self, locale: LocaleService, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.locale = locale
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 24)
        self.header = PageHeader(locale, "tutorial.datumdock", "tutorial.reader.reading_time")
        back = GhostButton()
        back.clicked.connect(lambda: self.route_requested.emit(RouteId.LEARNING_CENTER.value))
        self.back_button = back
        self.header.add_action(back)
        layout.addWidget(self.header)
        body = QHBoxLayout()
        contents_card = SectionCard()
        contents_card.setFixedWidth(250)
        self.contents_title = QLabel()
        self.contents_title.setObjectName("sectionTitle")
        contents_card.body.addWidget(self.contents_title)
        self.contents = QListWidget()
        self.section_names = ("create", "labels", "import", "annotate", "export")
        for index, section_name in enumerate(self.section_names, 1):
            section_title = tr(locale, f"quick.{section_name}")
            self.contents.addItem(f"{index}.  {section_title}")
        self.contents.setCurrentRow(0)
        contents_card.body.addWidget(self.contents)
        body.addWidget(contents_card)
        article_card = SectionCard()
        self.article = QTextBrowser()
        self.article.setOpenExternalLinks(False)
        article_card.body.addWidget(self.article, 1)
        controls = QHBoxLayout()
        self.previous_button = GhostButton()
        self.next_button = PrimaryButton()
        self.complete_button = GhostButton()
        controls.addWidget(self.previous_button)
        controls.addStretch()
        controls.addWidget(self.complete_button)
        controls.addWidget(self.next_button)
        article_card.body.addLayout(controls)
        body.addWidget(article_card, 1)
        layout.addLayout(body, 1)
        self.retranslate_ui()

    def retranslate_ui(self) -> None:
        self.header.retranslate_ui()
        self.back_button.setText(tr(self.locale, "nav.back"))
        self.contents_title.setText(tr(self.locale, "tutorial.reader.contents"))
        self.previous_button.setText(tr(self.locale, "tutorial.reader.previous"))
        self.next_button.setText(tr(self.locale, "tutorial.reader.next"))
        self.complete_button.setText(tr(self.locale, "tutorial.reader.complete"))
        current_section = max(0, self.contents.currentRow())
        self.contents.clear()
        for index, section_name in enumerate(self.section_names, 1):
            self.contents.addItem(f"{index}.  {tr(self.locale, f'quick.{section_name}')}")
        self.contents.setCurrentRow(current_section)
        self.article.setHtml(
            f"<h1>{tr(self.locale, 'tutorial.reader.heading')}</h1>"
            f"<p>{tr(self.locale, 'tutorial.reader.body')}</p>"
            f"<h2>1. {tr(self.locale, 'quick.create')}</h2>"
            f"<p>{tr(self.locale, 'dialog.template.scope')}</p>"
            f"<h2>2. {tr(self.locale, 'quick.labels')}</h2>"
            f"<p>{tr(self.locale, 'page.labels.subtitle')}</p>"
        )


class InfoPage(BasePage):
    """关于页和版本说明共用的品牌信息布局。"""

    def __init__(
        self,
        locale: LocaleService,
        title_key: str,
        subtitle_key: str,
        body_key: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.locale = locale
        self.title_key = title_key
        self.subtitle_key = subtitle_key
        self.body_key = body_key
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 28, 32, 32)
        self.back = GhostButton()
        self.back.clicked.connect(lambda: self.route_requested.emit(RouteId.HOME.value))
        layout.addWidget(self.back, 0, Qt.AlignmentFlag.AlignLeft)
        card = SectionCard()
        card.setMaximumWidth(820)
        card.body.setContentsMargins(36, 34, 36, 34)
        logo = QLabel()
        pixmap = QPixmap(str(brand_asset_path("datumdock-wordmark-v3.png")))
        logo.setPixmap(pixmap.scaledToWidth(360, Qt.TransformationMode.SmoothTransformation))
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title = QLabel()
        self.title.setObjectName("pageTitle")
        self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.subtitle = QLabel()
        self.subtitle.setObjectName("mutedText")
        self.subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.subtitle.setWordWrap(True)
        self.body = QLabel()
        self.body.setWordWrap(True)
        self.body.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.meta = QLabel()
        self.meta.setObjectName("mutedText")
        self.meta.setAlignment(Qt.AlignmentFlag.AlignCenter)
        card.body.addWidget(logo)
        card.body.addWidget(self.title)
        card.body.addWidget(self.subtitle)
        card.body.addSpacing(10)
        card.body.addWidget(self.body)
        card.body.addSpacing(10)
        card.body.addWidget(self.meta)
        layout.addStretch()
        layout.addWidget(card, 0, Qt.AlignmentFlag.AlignCenter)
        layout.addStretch()
        self.retranslate_ui()

    def retranslate_ui(self) -> None:
        self.back.setText(tr(self.locale, "nav.back"))
        self.title.setText(tr(self.locale, self.title_key))
        self.subtitle.setText(tr(self.locale, self.subtitle_key))
        self.body.setText(tr(self.locale, self.body_key))
        self.meta.setText(
            f"{tr(self.locale, 'about.version')}   ·   {tr(self.locale, 'about.license')}"
        )


class ManagementPage(BasePage):
    """为标签、模型、相似图和回收站提供一致但内容明确的管理界面。"""

    def __init__(
        self,
        locale: LocaleService,
        kind: str,
        workspace: WorkspaceSnapshot,
        preview_mode: bool = True,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.locale = locale
        self.kind = kind
        self.workspace = workspace
        self.preview_mode = preview_mode
        self._build_ui()
        self.retranslate_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 24)
        root.setSpacing(16)
        title_key, subtitle_key = {
            "labels": ("page.labels.title", "page.labels.subtitle"),
            "inspection": ("page.inspection.title", "page.inspection.subtitle"),
            "comparison": ("page.comparison.title", "page.comparison.subtitle"),
            "models": ("page.models.title", "page.models.subtitle"),
            "similarity": ("page.similarity.title", "page.similarity.subtitle"),
            "trash": ("page.trash.title", "page.trash.subtitle"),
            "overview": ("page.overview.title", "page.overview.subtitle"),
        }[self.kind]
        self.header = PageHeader(self.locale, title_key, subtitle_key)
        self.back_button = GhostButton()
        self.back_button.clicked.connect(
            lambda: self.route_requested.emit(RouteId.ANNOTATION_WORKSPACE.value)
        )
        self.header.add_action(self.back_button)
        root.addWidget(self.header)
        if self.kind == "overview":
            root.addLayout(self._build_stats())
        toolbar = QHBoxLayout()
        self.search = SearchBox()
        toolbar.addWidget(self.search, 1)
        self.primary_action = PrimaryButton()
        self.secondary_action = QPushButton()
        toolbar.addWidget(self.secondary_action)
        toolbar.addWidget(self.primary_action)
        root.addLayout(toolbar)
        self.table = QTableWidget()
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().setVisible(False)
        root.addWidget(self.table, 1)
        self.details = SectionCard()
        self.details_title = QLabel()
        self.details_title.setObjectName("sectionTitle")
        self.details_body = QLabel()
        self.details_body.setObjectName("mutedText")
        self.details_body.setWordWrap(True)
        self.details.body.addWidget(self.details_title)
        self.details.body.addWidget(self.details_body)
        root.addWidget(self.details)
        self._configure_actions()
        self._populate_table()

    def _build_stats(self) -> QHBoxLayout:
        row = QHBoxLayout()
        self.stat_cards: list[tuple[StatCard, str]] = []
        for value, label_key, color in (
            (f"{self.workspace.dataset.image_count:,}", "stats.images", THEME.tokens.brand_primary),
            (str(self.workspace.dataset.label_count), "stats.labels", THEME.tokens.brand_cyan),
            (
                f"{self.workspace.dataset.reviewed_percent}%",
                "stats.completed",
                THEME.tokens.success,
            ),
            (
                "12" if self.preview_mode else "0",
                "stats.attention",
                THEME.tokens.warning,
            ),
        ):
            card = StatCard(value, tr(self.locale, label_key), color)
            self.stat_cards.append((card, label_key))
            row.addWidget(card, 1)
        return row

    def _configure_actions(self) -> None:
        if self.kind == "labels":
            self.primary_action.clicked.connect(lambda: self.dialog_requested.emit("label_editor"))
            self.secondary_action.clicked.connect(
                lambda: self.route_requested.emit(RouteId.LABEL_COMPARISON.value)
            )
        elif self.kind == "models":
            self.primary_action.clicked.connect(lambda: self.dialog_requested.emit("model_import"))
            self.secondary_action.clicked.connect(
                lambda: self.dialog_requested.emit("model_mapping")
            )
        elif self.kind == "similarity":
            self.primary_action.clicked.connect(
                lambda: self.message_requested.emit(
                    "toast.preview_applied" if self.preview_mode else "toast.not_connected"
                )
            )
        elif self.kind == "trash":
            self.primary_action.clicked.connect(
                lambda: self.message_requested.emit(
                    "toast.preview_applied" if self.preview_mode else "toast.not_connected"
                )
            )
            self.secondary_action.clicked.connect(
                lambda: self.dialog_requested.emit("delete_batch")
            )
        else:
            self.primary_action.clicked.connect(
                lambda: self.message_requested.emit(
                    "toast.preview_applied" if self.preview_mode else "toast.not_connected"
                )
            )

    def _populate_table(self) -> None:
        if self.kind in {"labels", "inspection", "comparison"}:
            headers = (
                "table.color",
                "table.alias",
                "table.name",
                "table.description",
                "table.class_id",
                "table.usage",
                "table.status",
            )
            rows = [
                (
                    label.color,
                    label.alias,
                    label.name,
                    label.description,
                    str(label.class_id),
                    str(label.usage_count),
                    "value.active",
                )
                for label in self.workspace.labels
            ]
        elif self.kind == "models":
            headers = (
                "table.model",
                "table.format",
                "table.input",
                "table.classes",
                "table.backend",
                "table.status",
            )
            rows = [
                (
                    model.name,
                    model.format,
                    model.input_size,
                    str(model.class_count),
                    model.backend,
                    model.status,
                )
                for model in self.workspace.models
            ]
        elif self.kind == "similarity":
            headers = ("table.file", "table.file", "table.similarity", "table.status")
            rows = (
                []
                if not self.preview_mode
                else [
                    (
                        "factory_part_000231.png",
                        "factory_part_000232.png",
                        "96.8%",
                        "value.pending_confirmation",
                    ),
                    (
                        "factory_part_000244.png",
                        "factory_part_000245.png",
                        "94.2%",
                        "value.confirmed",
                    ),
                    (
                        "factory_part_000301.png",
                        "factory_part_000302.png",
                        "91.6%",
                        "value.pending_confirmation",
                    ),
                ]
            )
        elif self.kind == "trash":
            headers = ("table.file", "table.review", "table.status")
            rows = (
                []
                if not self.preview_mode
                else [
                    ("factory_part_000119.png", "4", "value.restorable"),
                    ("factory_part_000087.png", "0", "value.restorable"),
                ]
            )
        else:
            headers = ("table.file", "table.review", "table.usage", "table.status")
            rows = [
                (
                    image.filename,
                    f"status.{image.status.value}",
                    str(image.annotation_count),
                    "PNG",
                )
                for image in self.workspace.images
            ]
        self._header_keys = headers
        self.table.setColumnCount(len(headers))
        self.table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            for column, value in enumerate(row):
                item = QTableWidgetItem(value)
                if value.startswith(("value.", "preview.", "status.")):
                    item.setData(Qt.ItemDataRole.UserRole, value)
                if column == 0 and self.kind in {"labels", "inspection", "comparison"}:
                    item.setBackground(QColor(value))
                    item.setText("")
                self.table.setItem(row_index, column, item)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(
            len(headers) - 1, QHeaderView.ResizeMode.Stretch
        )

    def retranslate_ui(self) -> None:
        self.header.retranslate_ui()
        self.back_button.setText(tr(self.locale, "nav.back"))
        for card, key in getattr(self, "stat_cards", []):
            card.set_label(tr(self.locale, key))
        self.search.setPlaceholderText(tr(self.locale, "label.search"))
        action_map = {
            "labels": ("action.add_label", "action.compare"),
            "inspection": ("action.open_workspace", "workspace.list"),
            "comparison": ("action.continue", "action.details"),
            "models": ("action.import_model", "action.configure_mapping"),
            "similarity": ("action.confirm_group", "action.ignore"),
            "trash": ("action.restore", "action.empty_trash"),
            "overview": ("action.open_workspace", "action.details"),
        }
        primary, secondary = action_map[self.kind]
        self.primary_action.setText(tr(self.locale, primary))
        self.secondary_action.setText(tr(self.locale, secondary))
        if hasattr(self, "_header_keys"):
            self.table.setHorizontalHeaderLabels(
                [tr(self.locale, key) for key in self._header_keys]
            )
        for row in range(self.table.rowCount()):
            for column in range(self.table.columnCount()):
                item = self.table.item(row, column)
                key = item.data(Qt.ItemDataRole.UserRole)
                if key:
                    item.setText(tr(self.locale, key))
        self.details_title.setText(self.workspace.dataset.name)
        self.details_body.setText(
            tr(
                self.locale,
                {
                    "labels": "page.labels.subtitle",
                    "inspection": "page.inspection.subtitle",
                    "comparison": "page.comparison.subtitle",
                    "models": "page.models.subtitle",
                    "similarity": "page.similarity.subtitle",
                    "trash": "page.trash.subtitle",
                    "overview": "page.overview.subtitle",
                }[self.kind],
            )
        )


class SettingsPage(BasePage):
    """设置页展示全部分区、即时语言和快捷键冲突状态。"""

    locale_change_requested = Signal(str)
    settings_change_requested = Signal(str, object)

    def __init__(
        self,
        locale: LocaleService,
        action_registry: ActionRegistry | None = None,
        shortcut_profiles: ShortcutProfileService | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.locale = locale
        self.action_registry = action_registry or ActionRegistry(parent=self)
        self.shortcut_profiles = shortcut_profiles or ShortcutProfileService(
            self.action_registry,
            AppSettings(ui_locale=locale.locale),
            None,
        )
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 24)
        self.header = PageHeader(locale, "page.settings.title", "page.settings.subtitle")
        self.back = GhostButton()
        self.back.clicked.connect(lambda: self.route_requested.emit(RouteId.HOME.value))
        self.header.add_action(self.back)
        root.addWidget(self.header)
        content = QHBoxLayout()
        self.sections = QListWidget()
        self.sections.setFixedWidth(230)
        self.sections.currentRowChanged.connect(self._show_section)
        self.pages = QStackedWidget()
        content.addWidget(self.sections)
        content.addWidget(self.pages, 1)
        root.addLayout(content, 1)
        self._build_sections()
        self.retranslate_ui()
        self.sections.setCurrentRow(0)

    def _build_sections(self) -> None:
        self.general_page = self._form_card()
        form = QFormLayout()
        self.language_combo = QComboBox()
        self.language_combo.addItem("简体中文", "zh_CN")
        self.language_combo.addItem("English", "en_US")
        self.language_combo.currentIndexChanged.connect(
            lambda: self.locale_change_requested.emit(str(self.language_combo.currentData()))
        )
        self.language_help = QLabel()
        self.language_help.setObjectName("mutedText")
        self.language_help.setWordWrap(True)
        self.language_label = QLabel()
        form.addRow(self.language_label, self.language_combo)
        form.addRow(self.language_help)
        self.general_page.body.addLayout(form)
        self.pages.addWidget(self.general_page)

        self.shortcut_page = self._form_card()
        self.shortcut_search = SearchBox()
        self.shortcut_page.body.addWidget(self.shortcut_search)
        definitions = self.action_registry.definitions
        self.shortcut_table = QTableWidget(len(definitions), 4)
        self.shortcut_recorders: dict[str, ShortcutRecorder] = {}
        self.shortcut_action_buttons: dict[str, tuple[QPushButton, QPushButton]] = {}
        for row, definition in enumerate(definitions):
            action_item = QTableWidgetItem(definition.name_key)
            action_item.setData(Qt.ItemDataRole.UserRole, definition.name_key)
            action_item.setData(Qt.ItemDataRole.UserRole + 1, definition.id)
            action_item.setToolTip(definition.description_key)
            self.shortcut_table.setItem(row, 0, action_item)
            recorder = ShortcutRecorder(self.action_registry.sequence(definition.id))
            recorder.recorded.connect(
                lambda sequence, action_id=definition.id: self._shortcut_changed(
                    action_id, sequence
                )
            )
            recorder.recording_changed.connect(self.action_registry.set_recording)
            self.shortcut_recorders[definition.id] = recorder
            self.shortcut_table.setCellWidget(row, 1, recorder)
            self.shortcut_table.setItem(
                row,
                2,
                QTableWidgetItem(definition.default_shortcut or "—"),
            )
            buttons = QWidget()
            button_layout = QHBoxLayout(buttons)
            button_layout.setContentsMargins(0, 0, 0, 0)
            clear = GhostButton()
            restore = GhostButton()
            clear.clicked.connect(
                lambda _checked=False, action_id=definition.id: self._clear_shortcut(action_id)
            )
            restore.clicked.connect(
                lambda _checked=False, action_id=definition.id: self._restore_shortcut(action_id)
            )
            button_layout.addWidget(clear)
            button_layout.addWidget(restore)
            self.shortcut_action_buttons[definition.id] = (clear, restore)
            self.shortcut_table.setCellWidget(row, 3, buttons)
        self.shortcut_table.horizontalHeader().setStretchLastSection(True)
        self.shortcut_page.body.addWidget(self.shortcut_table)
        self.shortcut_conflict = QLabel()
        self.shortcut_conflict.setStyleSheet(
            f"color:{THEME.tokens.danger}; background:#FCE8E8; border-radius:8px; padding:9px;"
        )
        self.shortcut_conflict.hide()
        self.shortcut_page.body.addWidget(self.shortcut_conflict)
        restore_row = QHBoxLayout()
        self.restore_group_combo = QComboBox()
        for group in ActionGroup:
            self.restore_group_combo.addItem(group.value, group)
        self.restore_group_shortcuts = GhostButton()
        self.restore_group_shortcuts.clicked.connect(self._restore_shortcut_group)
        self.restore_shortcuts = QPushButton()
        self.restore_shortcuts.clicked.connect(self._restore_shortcut_defaults)
        restore_row.addWidget(self.restore_group_combo)
        restore_row.addWidget(self.restore_group_shortcuts)
        restore_row.addStretch()
        restore_row.addWidget(self.restore_shortcuts)
        self.shortcut_page.body.addLayout(restore_row)
        self.pages.addWidget(self.shortcut_page)
        self.shortcut_search.textChanged.connect(self._filter_shortcuts)
        self.action_registry.changed.connect(self._refresh_shortcut_values)

        self.data_page = self._form_card()
        self.trash_threshold = QSpinBox()
        self.trash_threshold.setRange(0, 10000)
        self.trash_threshold.setValue(50)
        self.trash_threshold.valueChanged.connect(
            lambda value: self.settings_change_requested.emit("trash_sample_threshold", value)
        )
        self.trash_help_button = HelpButton()
        self.trash_help = QLabel()
        self.trash_help.setObjectName("mutedText")
        self.trash_help.setWordWrap(True)
        self.split_combo = QComboBox()
        self.split_combo.addItems(["80 / 10 / 10", "70 / 20 / 10", "60 / 20 / 20"])
        self.data_form = QFormLayout()
        self.trash_threshold_label = QLabel()
        self.default_split_label = QLabel()
        threshold_field = QWidget()
        threshold_layout = QHBoxLayout(threshold_field)
        threshold_layout.setContentsMargins(0, 0, 0, 0)
        threshold_layout.addWidget(self.trash_threshold)
        threshold_layout.addWidget(self.trash_help_button)
        threshold_layout.addStretch()
        self.data_form.addRow(self.trash_threshold_label, threshold_field)
        self.data_form.addRow(self.trash_help)
        self.data_form.addRow(self.default_split_label, self.split_combo)
        self.data_page.body.addLayout(self.data_form)
        self.pages.addWidget(self.data_page)

        self.display_page = self._form_card()
        self.preview_check = QCheckBox()
        self.preview_check.setChecked(True)
        self.label_display = QComboBox()
        self.label_display.addItems(["中文别名 · english_name", "中文别名", "english_name"])
        self.display_page.body.addWidget(self.preview_check)
        self.display_page.body.addWidget(self.label_display)
        self.pages.addWidget(self.display_page)

        self.tutorial_page = self._form_card()
        self.quick_check = QCheckBox()
        self.quick_check.setChecked(True)
        self.tutorial_page.body.addWidget(self.quick_check)
        tutorial_progress = QProgressBar()
        tutorial_progress.setValue(35)
        tutorial_progress.setTextVisible(False)
        self.tutorial_page.body.addWidget(tutorial_progress)
        self.pages.addWidget(self.tutorial_page)

        self.about_page = self._form_card()
        self.about_page.body.addWidget(BrandLockup(False), 0, Qt.AlignmentFlag.AlignLeft)
        self.runtime_label = QLabel("DatumDock 0.1.0 · Python · PySide6 · Windows x64\nMIT License")
        self.runtime_label.setObjectName("mutedText")
        self.about_page.body.addWidget(self.runtime_label)
        self.pages.addWidget(self.about_page)

    def apply_settings(self, settings: AppSettings) -> None:
        """只把已接入字段同步到控件，避免初始化过程反向写盘。"""

        self.trash_threshold.blockSignals(True)
        self.trash_threshold.setValue(settings.trash_sample_threshold)
        self.trash_threshold.blockSignals(False)
        for index in range(self.language_combo.count()):
            if self.language_combo.itemData(index) == settings.ui_locale:
                self.language_combo.blockSignals(True)
                self.language_combo.setCurrentIndex(index)
                self.language_combo.blockSignals(False)
                break

    @staticmethod
    def _form_card() -> SectionCard:
        card = SectionCard()
        card.body.setContentsMargins(24, 24, 24, 24)
        return card

    def _show_section(self, index: int) -> None:
        if 0 <= index < self.pages.count():
            self.pages.setCurrentIndex(index)

    def _shortcut_changed(self, action_id: str, sequence: str) -> None:
        """保存新绑定；冲突时由用户明确决定是否替换旧动作。"""

        try:
            self.shortcut_profiles.set_binding(action_id, sequence)
        except (AppSettingsError, ValueError) as error:
            conflict = self.action_registry.conflict_owner(action_id, sequence)
            if (
                conflict
                and QMessageBox.question(
                    self,
                    tr(self.locale, "settings.shortcut_conflict"),
                    f"{error}\n{tr(self.locale, 'settings.shortcut_replace_confirm')}",
                )
                == QMessageBox.StandardButton.Yes
            ):
                try:
                    self.shortcut_profiles.set_binding(
                        action_id,
                        sequence,
                        replace_conflict=True,
                    )
                except (AppSettingsError, ValueError) as replace_error:
                    self._show_shortcut_error(str(replace_error))
                    self._refresh_shortcut_values()
                    return
            else:
                self._show_shortcut_error(str(error))
                self._refresh_shortcut_values()
                return
        self.shortcut_conflict.hide()
        self.message_requested.emit("toast.preview_applied")

    def _show_shortcut_error(self, message: str) -> None:
        self.shortcut_conflict.setText("⚠  " + message)
        self.shortcut_conflict.show()

    def _clear_shortcut(self, action_id: str) -> None:
        try:
            self.shortcut_profiles.set_binding(action_id, "")
        except (AppSettingsError, ValueError) as error:
            self._show_shortcut_error(str(error))

    def _restore_shortcut(self, action_id: str) -> None:
        try:
            self.shortcut_profiles.restore_action(action_id)
        except (AppSettingsError, ValueError) as error:
            self._show_shortcut_error(str(error))

    def _restore_shortcut_group(self) -> None:
        group = self.restore_group_combo.currentData()
        if not isinstance(group, ActionGroup):
            return
        try:
            self.shortcut_profiles.restore_group(group)
        except (AppSettingsError, ValueError) as error:
            self._show_shortcut_error(str(error))

    def _restore_shortcut_defaults(self) -> None:
        """显示影响数量，确认后只原子清除快捷键覆盖。"""

        count = len(self.action_registry.overrides)
        if (
            count
            and QMessageBox.question(
                self,
                tr(self.locale, "settings.restore_defaults"),
                tr(self.locale, "settings.restore_all_confirm").format(count=count),
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        try:
            self.shortcut_profiles.restore_all()
        except (AppSettingsError, ValueError) as error:
            self._show_shortcut_error(str(error))
            return
        self.shortcut_conflict.hide()
        self.message_requested.emit("toast.preview_applied")

    def _refresh_shortcut_values(self) -> None:
        for action_id, recorder in self.shortcut_recorders.items():
            recorder.sequence = self.action_registry.sequence(action_id)
            if not recorder.recording:
                recorder.setText(recorder.sequence or "—")

    def _filter_shortcuts(self, text: str) -> None:
        needle = text.strip().casefold()
        for row in range(self.shortcut_table.rowCount()):
            item = self.shortcut_table.item(row, 0)
            action_id = str(item.data(Qt.ItemDataRole.UserRole + 1))
            definition = self.action_registry.definition(action_id)
            haystack = " ".join(
                (
                    action_id,
                    tr(self.locale, definition.name_key),
                    tr(self.locale, definition.description_key),
                )
            ).casefold()
            self.shortcut_table.setRowHidden(row, bool(needle and needle not in haystack))

    def retranslate_ui(self) -> None:
        self.header.retranslate_ui()
        self.back.setText(tr(self.locale, "nav.back"))
        current = self.sections.currentRow()
        self.sections.clear()
        for key in (
            "settings.general",
            "settings.shortcuts",
            "settings.data",
            "settings.display",
            "settings.tutorials",
            "settings.about",
        ):
            self.sections.addItem(tr(self.locale, key))
        self.sections.setCurrentRow(max(0, current))
        self.language_help.setText(tr(self.locale, "settings.language.help"))
        self.language_label.setText(tr(self.locale, "settings.language"))
        self.shortcut_search.setPlaceholderText(tr(self.locale, "settings.shortcut_search"))
        self.shortcut_table.setHorizontalHeaderLabels(
            [
                tr(self.locale, "table.actions"),
                tr(self.locale, "settings.current"),
                tr(self.locale, "settings.default"),
                tr(self.locale, "settings.actions"),
            ]
        )
        for row in range(self.shortcut_table.rowCount()):
            item = self.shortcut_table.item(row, 0)
            item.setText(tr(self.locale, item.data(Qt.ItemDataRole.UserRole)))
            action_id = str(item.data(Qt.ItemDataRole.UserRole + 1))
            definition = self.action_registry.definition(action_id)
            item.setToolTip(tr(self.locale, definition.description_key))
        self.shortcut_conflict.setText("⚠  " + tr(self.locale, "settings.shortcut_conflict"))
        for recorder in self.shortcut_recorders.values():
            recorder.set_prompt(tr(self.locale, "settings.press_shortcut"))
        for clear, restore in self.shortcut_action_buttons.values():
            clear.setText(tr(self.locale, "settings.clear_shortcut"))
            restore.setText(tr(self.locale, "settings.restore_one"))
        current_group = self.restore_group_combo.currentData()
        self.restore_group_combo.blockSignals(True)
        self.restore_group_combo.clear()
        for group in ActionGroup:
            self.restore_group_combo.addItem(
                tr(self.locale, f"settings.shortcut_group.{group.value}"),
                group,
            )
        for index in range(self.restore_group_combo.count()):
            if self.restore_group_combo.itemData(index) == current_group:
                self.restore_group_combo.setCurrentIndex(index)
                break
        self.restore_group_combo.blockSignals(False)
        self.restore_group_shortcuts.setText(tr(self.locale, "settings.restore_group"))
        self.restore_shortcuts.setText(tr(self.locale, "settings.restore_defaults"))
        self.trash_threshold.setToolTip(tr(self.locale, "settings.trash_help"))
        self.trash_help_button.setToolTip(tr(self.locale, "settings.trash_help"))
        self.trash_help_button.setAccessibleName(tr(self.locale, "settings.trash_help"))
        self.trash_threshold_label.setText(tr(self.locale, "settings.trash_threshold"))
        self.default_split_label.setText(tr(self.locale, "settings.default_split"))
        self.trash_help.setText("ⓘ  " + tr(self.locale, "settings.trash_help"))
        self.preview_check.setText(tr(self.locale, "settings.preview_boxes"))
        self.quick_check.setText(tr(self.locale, "settings.show_quick_start"))
        for index in range(self.language_combo.count()):
            if self.language_combo.itemData(index) == self.locale.locale:
                self.language_combo.blockSignals(True)
                self.language_combo.setCurrentIndex(index)
                self.language_combo.blockSignals(False)


class ComponentGalleryPage(BasePage):
    """预览模式集中展示组件状态，便于视觉审核。"""

    def __init__(self, locale: LocaleService, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.locale = locale
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 24)
        self.header = PageHeader(locale, "page.gallery.title", "page.gallery.subtitle")
        self.back = GhostButton()
        self.back.clicked.connect(lambda: self.route_requested.emit(RouteId.HOME.value))
        self.header.add_action(self.back)
        layout.addWidget(self.header)
        grid = QGridLayout()
        buttons = SectionCard()
        buttons.body.addWidget(QLabel("Buttons / 按钮"))
        row = QHBoxLayout()
        row.addWidget(PrimaryButton("Primary"))
        row.addWidget(QPushButton("Secondary"))
        row.addWidget(GhostButton("Ghost"))
        row.addWidget(DangerButton("Danger"))
        disabled = QPushButton("Disabled")
        disabled.setEnabled(False)
        row.addWidget(disabled)
        buttons.body.addLayout(row)
        grid.addWidget(buttons, 0, 0)
        statuses = SectionCard()
        statuses.body.addWidget(QLabel("Image Status / 图片状态"))
        status_row = QHBoxLayout()
        for status in ImageStatus:
            status_row.addWidget(StatusBadge(locale, status))
        statuses.body.addLayout(status_row)
        grid.addWidget(statuses, 1, 0)
        inputs = SectionCard()
        inputs.body.addWidget(QLabel("Inputs & Filters / 输入与筛选"))
        inputs.body.addWidget(SearchBox("Search"))
        chips = QHBoxLayout()
        for text in ("Normal", "Selected", "Disabled"):
            chip = FilterChip(text)
            chip.setChecked(text == "Selected")
            chip.setEnabled(text != "Disabled")
            chips.addWidget(chip)
        inputs.body.addLayout(chips)
        grid.addWidget(inputs, 2, 0)
        layout.addLayout(grid)
        layout.addStretch()
        self.retranslate_ui()

    def retranslate_ui(self) -> None:
        self.header.retranslate_ui()
        self.back.setText(tr(self.locale, "nav.back"))
