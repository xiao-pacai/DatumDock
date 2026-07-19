"""测试进程的全局数据安全边界。"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolate_managed_data_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """强制每个测试使用独立绝对资料库，禁止回落到真实用户目录。"""

    managed_root = (tmp_path / "datumdock-app-data").resolve()
    monkeypatch.setenv("DATUMDOCK_DATA_DIR", str(managed_root))
