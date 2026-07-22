"""临时标注诊断日志；用于定位连续切图后的保存时序问题。"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import UTC, datetime
from enum import Enum
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

# 临时方案: 用户实机问题定位完成后将默认值改为关闭，并保留环境变量供开发复验。
TEMPORARY_ANNOTATION_DEBUG_ENABLED = True
ANNOTATION_DEBUG_ENV = "DATUMDOCK_ANNOTATION_DEBUG"


class _JsonLineFormatter(logging.Formatter):
    """把一次诊断事件编码为单行 JSON，便于按请求和样本检索。"""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "time": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "event": record.getMessage(),
            "thread": record.threadName,
            **getattr(record, "event_data", {}),
        }
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


class AnnotationDebugLog:
    """惰性写入有限大小的本地日志；日志失败不得影响标注保存。"""

    def __init__(self, data_root: Path) -> None:
        self.path = data_root / "logs" / "annotation-debug.log"
        self.enabled = _debug_enabled()
        self._handler: RotatingFileHandler | None = None
        self._lock = threading.RLock()

    def record(self, event: str, **fields: object) -> None:
        """记录不含图片内容的结构化事件，并在首次事件时创建日志。"""

        if not self.enabled:
            return
        try:
            with self._lock:
                handler = self._ensure_handler()
                record = logging.LogRecord(
                    "datumdock.annotation_debug",
                    logging.DEBUG,
                    "",
                    0,
                    event,
                    (),
                    None,
                )
                record.event_data = {key: _json_value(value) for key, value in fields.items()}
                handler.handle(record)
        except (OSError, ValueError):
            # 诊断日志是旁路能力；磁盘满或目录不可写时不能反过来阻断标注事务。
            return

    def close(self) -> None:
        """刷新并关闭当前日志句柄，不删除已经取得的诊断证据。"""

        with self._lock:
            if self._handler is None:
                return
            self._handler.flush()
            self._handler.close()
            self._handler = None

    def _ensure_handler(self) -> RotatingFileHandler:
        if self._handler is not None:
            return self._handler
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            self.path,
            maxBytes=2 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
            delay=True,
        )
        handler.setFormatter(_JsonLineFormatter())
        self._handler = handler
        return handler


def short_digest(value: str) -> str:
    """日志只保留摘要前缀，足以比对又避免冗长输出。"""

    return value[:16] if value else ""


def _debug_enabled() -> bool:
    override = os.getenv(ANNOTATION_DEBUG_ENV)
    if override is None:
        return TEMPORARY_ANNOTATION_DEBUG_ENABLED
    return override.strip().casefold() not in {"0", "false", "off", "no"}


def _json_value(value: object) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list, set)):
        return [_json_value(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)
