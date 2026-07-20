"""DatumDock 全量动作注册、快捷键偏好与 Qt 运行时绑定。"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QAction, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QComboBox,
    QLineEdit,
    QPlainTextEdit,
    QTextEdit,
    QWidget,
)

from datumdock.domain.models import AppSettings
from datumdock.services.settings import AppSettingsRepository


class ActionGroup(StrEnum):
    """设置页使用的稳定动作分组。"""

    DATASET = "dataset"
    SAMPLE = "sample"
    CANVAS = "canvas"
    ANNOTATION = "annotation"
    REVIEW = "review"
    APPLICATION = "application"


class ActionScope(StrEnum):
    """快捷键生效范围。"""

    APPLICATION = "application"
    WORKSPACE = "workspace"
    DIALOG = "dialog"


@dataclass(frozen=True, slots=True)
class ActionDefinition:
    """一个可配置动作的稳定元数据。"""

    id: str
    name_key: str
    description_key: str
    group: ActionGroup
    default_shortcut: str = ""
    scope: ActionScope = ActionScope.WORKSPACE
    suppress_in_text_input: bool = True


ACTION_DEFINITIONS: tuple[ActionDefinition, ...] = (
    ActionDefinition(
        "dataset.import_images",
        "action.dataset.import_images",
        "action.dataset.import_images.help",
        ActionGroup.DATASET,
        "Ctrl+O",
    ),
    ActionDefinition(
        "dataset.export",
        "action.dataset.export",
        "action.dataset.export.help",
        ActionGroup.DATASET,
        "Ctrl+E",
    ),
    ActionDefinition(
        "dataset.import_xany",
        "action.dataset.import_xany",
        "action.dataset.import_xany.help",
        ActionGroup.DATASET,
    ),
    ActionDefinition(
        "dataset.export_xany",
        "action.dataset.export_xany",
        "action.dataset.export_xany.help",
        ActionGroup.DATASET,
    ),
    ActionDefinition(
        "dataset.switch",
        "action.dataset.switch",
        "action.dataset.switch.help",
        ActionGroup.DATASET,
    ),
    ActionDefinition(
        "sample.previous",
        "action.sample.previous",
        "action.sample.previous.help",
        ActionGroup.SAMPLE,
        "A",
    ),
    ActionDefinition(
        "sample.next",
        "action.sample.next",
        "action.sample.next.help",
        ActionGroup.SAMPLE,
        "D",
    ),
    ActionDefinition(
        "sample.delete_current",
        "action.sample.delete_current",
        "action.sample.delete_current.help",
        ActionGroup.SAMPLE,
        "Ctrl+Shift+Delete",
    ),
    ActionDefinition(
        "canvas.select",
        "action.canvas.select",
        "action.canvas.select.help",
        ActionGroup.CANVAS,
    ),
    ActionDefinition(
        "canvas.rectangle",
        "action.canvas.rectangle",
        "action.canvas.rectangle.help",
        ActionGroup.CANVAS,
        "R",
    ),
    ActionDefinition(
        "canvas.pan",
        "action.canvas.pan",
        "action.canvas.pan.help",
        ActionGroup.CANVAS,
    ),
    ActionDefinition(
        "canvas.cancel",
        "action.canvas.cancel",
        "action.canvas.cancel.help",
        ActionGroup.CANVAS,
        "Esc",
    ),
    ActionDefinition(
        "canvas.fit",
        "action.canvas.fit",
        "action.canvas.fit.help",
        ActionGroup.CANVAS,
        "F",
    ),
    ActionDefinition(
        "canvas.zoom_in",
        "action.canvas.zoom_in",
        "action.canvas.zoom_in.help",
        ActionGroup.CANVAS,
        "Ctrl++",
    ),
    ActionDefinition(
        "canvas.zoom_out",
        "action.canvas.zoom_out",
        "action.canvas.zoom_out.help",
        ActionGroup.CANVAS,
        "Ctrl+-",
    ),
    ActionDefinition(
        "canvas.zoom_100",
        "action.canvas.zoom_100",
        "action.canvas.zoom_100.help",
        ActionGroup.CANVAS,
    ),
    ActionDefinition(
        "annotation.delete_selected",
        "action.annotation.delete_selected",
        "action.annotation.delete_selected.help",
        ActionGroup.ANNOTATION,
        "Delete",
    ),
    ActionDefinition(
        "annotation.undo",
        "action.annotation.undo",
        "action.annotation.undo.help",
        ActionGroup.ANNOTATION,
        "Ctrl+Z",
    ),
    ActionDefinition(
        "annotation.redo",
        "action.annotation.redo",
        "action.annotation.redo.help",
        ActionGroup.ANNOTATION,
        "Ctrl+Y",
    ),
    ActionDefinition(
        "annotation.retry_save",
        "action.annotation.retry_save",
        "action.annotation.retry_save.help",
        ActionGroup.ANNOTATION,
        "Ctrl+S",
    ),
    ActionDefinition(
        "annotation.change_label",
        "action.annotation.change_label",
        "action.annotation.change_label.help",
        ActionGroup.ANNOTATION,
    ),
    ActionDefinition(
        "review.mark_completed",
        "action.review.mark_completed",
        "action.review.mark_completed.help",
        ActionGroup.REVIEW,
        "S",
    ),
    ActionDefinition(
        "app.focus_search",
        "action.app.focus_search",
        "action.app.focus_search.help",
        ActionGroup.APPLICATION,
        "Ctrl+F",
        ActionScope.APPLICATION,
    ),
    ActionDefinition(
        "app.open_settings",
        "action.app.open_settings",
        "action.app.open_settings.help",
        ActionGroup.APPLICATION,
        scope=ActionScope.APPLICATION,
    ),
    ActionDefinition(
        "app.manage_labels",
        "action.app.manage_labels",
        "action.app.manage_labels.help",
        ActionGroup.APPLICATION,
        scope=ActionScope.APPLICATION,
    ),
    ActionDefinition(
        "app.manage_models",
        "action.app.manage_models",
        "action.app.manage_models.help",
        ActionGroup.APPLICATION,
        scope=ActionScope.APPLICATION,
    ),
)

DEFAULT_SHORTCUTS = {item.id: item.default_shortcut for item in ACTION_DEFINITIONS}


class WindowsReservedShortcutPolicy:
    """拒绝 Windows 保留组合，避免保存一个永远无法触发的绑定。"""

    _RESERVED: ClassVar[set[str]] = {
        "Alt+Tab",
        "Alt+Esc",
        "Ctrl+Esc",
        "Alt+F4",
        "Ctrl+Alt+Delete",
    }

    @classmethod
    def validate(cls, normalized: str) -> None:
        if not normalized:
            return
        if normalized in cls._RESERVED or normalized.startswith("Meta+"):
            raise ValueError(f"Windows 系统保留快捷键不可绑定: {normalized}")


class ActionRegistry(QObject):
    """注册表是动作元数据和当前有效绑定的唯一事实来源。"""

    changed = Signal()
    recording_changed = Signal(bool)

    def __init__(
        self,
        definitions: tuple[ActionDefinition, ...] = ACTION_DEFINITIONS,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._definitions = {item.id: item for item in definitions}
        if len(self._definitions) != len(definitions):
            raise ValueError("动作注册表包含重复 ID")
        self._overrides: dict[str, str] = {}
        self._recording = False

    @property
    def definitions(self) -> tuple[ActionDefinition, ...]:
        return tuple(self._definitions.values())

    @property
    def overrides(self) -> dict[str, str]:
        return dict(self._overrides)

    @property
    def recording(self) -> bool:
        return self._recording

    def definition(self, action_id: str) -> ActionDefinition:
        try:
            return self._definitions[action_id]
        except KeyError as error:
            raise ValueError(f"未知动作: {action_id}") from error

    def sequence(self, action_id: str) -> str:
        definition = self.definition(action_id)
        return self._overrides.get(action_id, definition.default_shortcut)

    def effective(self, overrides: Mapping[str, str] | None = None) -> dict[str, str]:
        values = dict(self._overrides if overrides is None else overrides)
        return {
            action_id: values.get(action_id, definition.default_shortcut)
            for action_id, definition in self._definitions.items()
        }

    def set_overrides(self, overrides: Mapping[str, str]) -> None:
        unknown = sorted(set(overrides) - set(self._definitions))
        if unknown:
            raise ValueError(f"快捷键配置包含未知动作: {', '.join(unknown)}")
        normalized = {key: normalize_shortcut(value) for key, value in overrides.items()}
        self.validate(self.effective(normalized))
        self._overrides = normalized
        self.changed.emit()

    def set_recording(self, recording: bool) -> None:
        if self._recording == recording:
            return
        self._recording = recording
        self.recording_changed.emit(recording)

    @staticmethod
    def validate(shortcuts: Mapping[str, str]) -> None:
        owners: dict[str, str] = {}
        for action_id, value in shortcuts.items():
            normalized = normalize_shortcut(value)
            WindowsReservedShortcutPolicy.validate(normalized)
            if normalized and normalized in owners:
                raise ValueError(f"快捷键冲突: {owners[normalized]} 与 {action_id}")
            if normalized:
                owners[normalized] = action_id

    def conflict_owner(
        self,
        action_id: str,
        sequence: str,
        overrides: Mapping[str, str] | None = None,
    ) -> str | None:
        normalized = normalize_shortcut(sequence)
        if not normalized:
            return None
        for owner, value in self.effective(overrides).items():
            if owner != action_id and normalize_shortcut(value) == normalized:
                return owner
        return None


class ShortcutProfileService:
    """先原子保存完整目标配置，再更新当前运行时绑定。"""

    def __init__(
        self,
        registry: ActionRegistry,
        settings: AppSettings,
        repository: AppSettingsRepository | None,
    ) -> None:
        self.registry = registry
        self.settings = settings.model_copy(deep=True)
        self.repository = repository
        known = {
            key: value
            for key, value in settings.shortcut_overrides.items()
            if key in {item.id for item in registry.definitions}
        }
        self.registry.set_overrides(known)

    def set_binding(self, action_id: str, sequence: str, *, replace_conflict: bool = False) -> None:
        self.registry.definition(action_id)
        normalized = normalize_shortcut(sequence)
        WindowsReservedShortcutPolicy.validate(normalized)
        target = self.registry.overrides
        conflict = self.registry.conflict_owner(action_id, normalized, target)
        if conflict and not replace_conflict:
            raise ValueError(f"快捷键已由 {conflict} 使用")
        if conflict:
            target[conflict] = ""
        target[action_id] = normalized
        self._commit(target)

    def restore_action(self, action_id: str) -> None:
        definition = self.registry.definition(action_id)
        target = self.registry.overrides
        target.pop(action_id, None)
        self._clear_conflicts_for_defaults(target, (definition,))
        self._commit(target)

    def restore_group(self, group: ActionGroup) -> int:
        action_ids = {item.id for item in self.registry.definitions if item.group == group}
        target = self.registry.overrides
        changed = sum(action_id in target for action_id in action_ids)
        for action_id in action_ids:
            target.pop(action_id, None)
        definitions = tuple(item for item in self.registry.definitions if item.id in action_ids)
        self._clear_conflicts_for_defaults(target, definitions)
        self._commit(target)
        return changed

    def restore_all(self) -> int:
        changed = len(self.registry.overrides)
        self._commit({})
        return changed

    def _commit(self, overrides: Mapping[str, str]) -> None:
        target = dict(overrides)
        self.registry.validate(self.registry.effective(target))
        base = self.repository.load() if self.repository is not None else self.settings
        updated = base.model_copy(update={"shortcut_overrides": target})
        updated = AppSettings.model_validate(updated.model_dump(mode="json"))
        if self.repository is not None:
            self.repository.save(updated)
        self.settings = updated
        self.registry.set_overrides(target)

    def _clear_conflicts_for_defaults(
        self,
        target: dict[str, str],
        restored: tuple[ActionDefinition, ...],
    ) -> None:
        """恢复默认值时清空占用目标默认键的自定义绑定。"""

        restored_ids = {item.id for item in restored}
        defaults = {
            normalize_shortcut(item.default_shortcut): item.id
            for item in restored
            if item.default_shortcut
        }
        for owner, sequence in self.registry.effective(target).items():
            normalized = normalize_shortcut(sequence)
            if owner not in restored_ids and normalized in defaults:
                target[owner] = ""


class ActionBindingManager(QObject):
    """根据注册表为一个窗口生成运行时 QShortcut，并集中执行焦点保护。"""

    def __init__(self, registry: ActionRegistry, parent_widget: QWidget) -> None:
        super().__init__(parent_widget)
        self.registry = registry
        self.parent_widget = parent_widget
        self._bindings: dict[
            str,
            tuple[QShortcut, Callable[[], None], Callable[[QWidget | None], bool] | None],
        ] = {}
        self.registry.changed.connect(self.refresh)
        self.registry.recording_changed.connect(lambda _recording: self.refresh())

    def bind(
        self,
        action_id: str,
        callback: Callable[[], None],
        *,
        focus_predicate: Callable[[QWidget | None], bool] | None = None,
    ) -> QShortcut:
        definition = self.registry.definition(action_id)
        shortcut = QShortcut(QKeySequence(), self.parent_widget)
        shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        shortcut.activated.connect(lambda current=action_id: self._activate(current))
        self._bindings[action_id] = (shortcut, callback, focus_predicate)
        shortcut.setKey(QKeySequence(self.registry.sequence(definition.id)))
        return shortcut

    def refresh(self) -> None:
        for action_id, (shortcut, _callback, _predicate) in self._bindings.items():
            shortcut.setKey(QKeySequence(self.registry.sequence(action_id)))
            shortcut.setEnabled(not self.registry.recording)

    def _activate(self, action_id: str) -> None:
        shortcut, callback, predicate = self._bindings[action_id]
        if not shortcut.isEnabled() or self.registry.recording:
            return
        focus = QApplication.focusWidget()
        definition = self.registry.definition(action_id)
        if definition.suppress_in_text_input and is_text_input(focus):
            return
        if predicate is not None and not predicate(focus):
            return
        callback()


class ShortcutService:
    """兼容旧测试与旧窗口的轻量 QAction 应用适配器。"""

    @staticmethod
    def merged(overrides: Mapping[str, str]) -> dict[str, str]:
        return {key: overrides.get(key, value) for key, value in DEFAULT_SHORTCUTS.items()}

    @staticmethod
    def validate(shortcuts: Mapping[str, str]) -> None:
        ActionRegistry.validate(shortcuts)

    def apply(self, actions: Mapping[str, QAction], overrides: Mapping[str, str]) -> None:
        shortcuts = self.merged(overrides)
        self.validate(shortcuts)
        for action_id, action in actions.items():
            if action_id in shortcuts:
                action.setShortcut(QKeySequence(shortcuts[action_id]))


def normalize_shortcut(value: str) -> str:
    """统一使用 Qt PortableText，空字符串表示用户主动清空。"""

    if not value:
        return ""
    sequence = QKeySequence(value)
    normalized = sequence.toString(QKeySequence.SequenceFormat.PortableText)
    if not normalized:
        raise ValueError(f"快捷键无效: {value}")
    return normalized


def is_text_input(widget: QWidget | None) -> bool:
    """文本与数值编辑控件优先消费单字母和 Delete。"""

    if widget is None:
        return False
    if isinstance(widget, (QLineEdit, QTextEdit, QPlainTextEdit, QAbstractSpinBox)):
        return True
    return isinstance(widget, QComboBox) and widget.isEditable()
