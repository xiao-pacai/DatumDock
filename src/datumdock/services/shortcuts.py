"""全局快捷键的默认值、冲突校验与 QAction 应用服务。"""

from __future__ import annotations

from collections.abc import Mapping

from PySide6.QtGui import QAction, QKeySequence

DEFAULT_SHORTCUTS = {
    "import_images": "Ctrl+I",
    "labels": "Ctrl+L",
    "export": "Ctrl+E",
    "delete_sample": "Delete",
    "previous_sample": "Left",
    "next_sample": "Right",
    "undo": "Ctrl+Z",
    "redo": "Ctrl+Y",
    "fit_image": "F",
    "zoom_in": "Ctrl++",
    "zoom_out": "Ctrl+-",
}


class ShortcutService:
    """不把快捷键散落在 UI 中，并在保存前拒绝会互相遮蔽的配置。"""

    @staticmethod
    def merged(overrides: Mapping[str, str]) -> dict[str, str]:
        """返回默认值叠加用户覆盖后的可应用快捷键字典。"""

        return {key: overrides.get(key, value) for key, value in DEFAULT_SHORTCUTS.items()}

    @staticmethod
    def validate(shortcuts: Mapping[str, str]) -> None:
        """验证序列语法与重复项，空值表示用户主动禁用该操作。"""

        owners: dict[str, str] = {}
        for action_id, sequence_text in shortcuts.items():
            sequence = QKeySequence(sequence_text)
            normalized = sequence.toString(QKeySequence.SequenceFormat.PortableText)
            if sequence_text and not normalized:
                raise ValueError(f"快捷键无效: {sequence_text}")
            if normalized and normalized in owners:
                raise ValueError(f"快捷键冲突: {owners[normalized]} 与 {action_id}")
            if normalized:
                owners[normalized] = action_id

    def apply(self, actions: Mapping[str, QAction], overrides: Mapping[str, str]) -> None:
        """在通过校验后将快捷键应用到已有 QAction，不重建业务回调。"""

        shortcuts = self.merged(overrides)
        self.validate(shortcuts)
        for action_id, action in actions.items():
            if action_id in shortcuts:
                action.setShortcut(QKeySequence(shortcuts[action_id]))
