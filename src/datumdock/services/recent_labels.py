"""按数据集隔离的会话级最近成功标签记录。"""

from __future__ import annotations

import threading


class RecentLabelTracker:
    """只记录已成功保存的创建或改派标签，关闭应用后自然清空。"""

    def __init__(self) -> None:
        self._labels: dict[str, str] = {}
        self._lock = threading.RLock()

    def remember(self, dataset_id: str, label_id: str) -> None:
        with self._lock:
            self._labels[dataset_id] = label_id

    def resolve(self, dataset_id: str, active_label_ids: set[str]) -> tuple[bool, str | None]:
        """返回是否曾记录；失效标签会被清除且不偷偷回退第一项。"""

        with self._lock:
            if dataset_id not in self._labels:
                return False, None
            label_id = self._labels[dataset_id]
            if label_id in active_label_ids:
                return True, label_id
            self._labels.pop(dataset_id, None)
            return True, None

    def clear(self, dataset_id: str) -> None:
        with self._lock:
            self._labels.pop(dataset_id, None)
