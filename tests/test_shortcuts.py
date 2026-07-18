"""快捷键覆盖与冲突校验的回归测试。"""

import pytest

from datumdock.services.shortcuts import ShortcutService


def test_shortcut_service_merges_defaults_and_rejects_conflicts() -> None:
    """用户覆盖只替换目标操作，重复组合键不能被静默接受。"""

    merged = ShortcutService.merged({"import_images": "Ctrl+Shift+I"})
    assert merged["import_images"] == "Ctrl+Shift+I"
    assert merged["export"] == "Ctrl+E"
    with pytest.raises(ValueError, match="冲突"):
        ShortcutService.validate({"import_images": "Ctrl+I", "export": "Ctrl+I"})
