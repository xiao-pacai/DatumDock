"""DatumDock 全局设置的原子持久化边界。"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import ValidationError

from datumdock.domain.models import AppSettings
from datumdock.services.storage import write_json_atomic


class AppSettingsError(RuntimeError):
    """设置文件损坏或无法安全保存。"""


class AppSettingsRepository:
    """设置只存于应用资料库根目录，不写入安装目录或数据集目录。"""

    def __init__(self, data_root: Path) -> None:
        self.path = data_root / "settings.json"

    def load(self) -> AppSettings:
        """首次使用创建默认设置，损坏文件保持原样并显式失败。"""

        if not self.path.exists():
            settings = AppSettings()
            self.save(settings)
            return settings
        if self.path.is_symlink():
            raise AppSettingsError("设置文件不能是符号链接")
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            return AppSettings.model_validate(payload)
        except (OSError, json.JSONDecodeError, ValidationError) as error:
            raise AppSettingsError(f"设置文件无法读取，原文件未被覆盖: {error}") from error

    def save(self, settings: AppSettings) -> None:
        """保存前重新验证完整模型，防止可变对象绕过初始校验。"""

        try:
            validated = AppSettings.model_validate(settings.model_dump(mode="json"))
            write_json_atomic(self.path, validated)
        except (OSError, ValidationError) as error:
            raise AppSettingsError(f"设置保存失败: {error}") from error
