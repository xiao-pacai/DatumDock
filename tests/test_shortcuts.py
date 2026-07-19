"""统一动作注册表与快捷键偏好的回归测试。"""

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QLineEdit, QVBoxLayout, QWidget

from datumdock.domain.models import AppSettings
from datumdock.services.settings import AppSettingsError, AppSettingsRepository
from datumdock.services.shortcuts import (
    ActionBindingManager,
    ActionGroup,
    ActionRegistry,
    ShortcutProfileService,
    WindowsReservedShortcutPolicy,
)


def test_registry_exposes_defaults_and_rejects_conflicts() -> None:
    """注册表公开完整默认键，重复组合键不能被静默接受。"""

    registry = ActionRegistry()
    assert registry.sequence("sample.previous") == "A"
    assert registry.sequence("sample.next") == "D"
    assert registry.sequence("canvas.rectangle") == "R"
    assert registry.sequence("review.mark_completed") == "S"
    with pytest.raises(ValueError, match="冲突"):
        registry.set_overrides({"dataset.import_images": "Ctrl+E"})


def test_profile_can_replace_conflict_and_restore_scopes() -> None:
    """替换冲突和三级恢复必须基于完整目标配置原子更新。"""

    registry = ActionRegistry()
    profile = ShortcutProfileService(registry, AppSettings(), None)
    profile.set_binding("dataset.import_images", "Ctrl+E", replace_conflict=True)
    assert registry.sequence("dataset.import_images") == "Ctrl+E"
    assert registry.sequence("dataset.export") == ""

    profile.restore_action("dataset.export")
    assert registry.sequence("dataset.export") == "Ctrl+E"
    profile.set_binding("sample.previous", "Left")
    assert profile.restore_group(ActionGroup.SAMPLE) == 1
    assert registry.sequence("sample.previous") == "A"

    profile.set_binding("canvas.rectangle", "B")
    assert profile.restore_all() == 2
    assert registry.sequence("canvas.rectangle") == "R"


@pytest.mark.parametrize(
    "sequence",
    ["Alt+Tab", "Alt+Esc", "Ctrl+Esc", "Alt+F4", "Ctrl+Alt+Delete", "Meta+R"],
)
def test_windows_reserved_shortcuts_are_rejected(sequence: str) -> None:
    """Windows 保留组合不得进入用户配置。"""

    with pytest.raises(ValueError, match="Windows"):
        WindowsReservedShortcutPolicy.validate(sequence)


def test_restore_all_preserves_non_shortcut_settings(tmp_path) -> None:
    """恢复全部快捷键只清除覆盖，不得改写任何其他全局设置。"""

    repository = AppSettingsRepository(tmp_path / "settings.json")
    original = AppSettings(
        ui_locale="en_US",
        trash_sample_threshold=73,
        quick_label_dialog_size=(920, 680),
        shortcut_overrides={"canvas.rectangle": "B"},
    )
    repository.save(original)
    registry = ActionRegistry()
    profile = ShortcutProfileService(registry, original, repository)
    profile.restore_all()

    saved = repository.load()
    assert saved.shortcut_overrides == {}
    assert saved.ui_locale == "en_US"
    assert saved.trash_sample_threshold == 73
    assert saved.quick_label_dialog_size == (920, 680)


def test_save_failure_keeps_runtime_bindings_unchanged(monkeypatch, tmp_path) -> None:
    """磁盘保存失败时，文件配置和运行时快捷键都保持原值。"""

    repository = AppSettingsRepository(tmp_path / "settings.json")
    settings = AppSettings()
    repository.save(settings)
    registry = ActionRegistry()
    profile = ShortcutProfileService(registry, settings, repository)

    def fail_save(_settings: AppSettings) -> None:
        raise AppSettingsError("模拟写盘失败")

    monkeypatch.setattr(repository, "save", fail_save)
    with pytest.raises(AppSettingsError, match="模拟写盘失败"):
        profile.set_binding("canvas.rectangle", "B")
    assert registry.sequence("canvas.rectangle") == "R"
    assert repository.load().shortcut_overrides == {}


def test_single_letter_actions_do_not_penetrate_text_input(qtbot, monkeypatch) -> None:
    """文本框输入 A/D/R/S/Delete 时，应用动作不得被误触发。"""

    host = QWidget()
    layout = QVBoxLayout(host)
    editor = QLineEdit()
    canvas_focus = QWidget()
    canvas_focus.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
    layout.addWidget(editor)
    layout.addWidget(canvas_focus)
    registry = ActionRegistry()
    manager = ActionBindingManager(registry, host)
    activations: list[str] = []
    for action_id in (
        "sample.previous",
        "sample.next",
        "canvas.rectangle",
        "review.mark_completed",
        "annotation.delete_selected",
    ):
        manager.bind(action_id, lambda current=action_id: activations.append(current))
    qtbot.addWidget(host)
    host.show()

    editor.setFocus()
    qtbot.keyClicks(editor, "adrs")
    qtbot.keyClick(editor, Qt.Key.Key_Delete)
    activations.clear()
    monkeypatch.setattr(QApplication, "focusWidget", staticmethod(lambda: editor))
    for action_id in (
        "sample.previous",
        "sample.next",
        "canvas.rectangle",
        "review.mark_completed",
        "annotation.delete_selected",
    ):
        manager._activate(action_id)
    assert activations == []
    assert editor.text() == "adrs"

    canvas_focus.setFocus()
    monkeypatch.setattr(QApplication, "focusWidget", staticmethod(lambda: canvas_focus))
    manager._activate("canvas.rectangle")
    assert activations == ["canvas.rectangle"]
