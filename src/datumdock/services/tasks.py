"""与 Qt 解耦的可取消后台任务执行器。"""

from __future__ import annotations

import threading
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from datumdock.domain.models import new_id


class TaskState(StrEnum):
    """任务生命周期只在单样本原子边界响应取消。"""

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIAL = "partial"
    CANCELLED = "cancelled"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class TaskSnapshot:
    """UI 定时轮询的不可变任务快照。"""

    id: str
    dataset_id: str
    kind: str
    state: TaskState
    phase: str
    completed: int
    total: int
    current_item: str
    errors: tuple[str, ...]
    summary: dict[str, Any] = field(default_factory=dict)


class TaskContext:
    """后台函数通过上下文报告进度，不依赖 Qt 信号。"""

    def __init__(self, task: _TaskRecord) -> None:
        self._task = task

    def cancelled(self) -> bool:
        return self._task.cancel_event.is_set()

    def progress(self, completed: int, total: int, current_item: str = "") -> None:
        with self._task.lock:
            self._task.completed = max(0, completed)
            self._task.total = max(0, total)
            self._task.current_item = current_item

    def phase(self, value: str) -> None:
        with self._task.lock:
            self._task.phase = value

    def add_error(self, message: str) -> None:
        with self._task.lock:
            self._task.errors.append(message)


@dataclass(slots=True)
class _TaskRecord:
    id: str
    dataset_id: str
    kind: str
    state: TaskState = TaskState.QUEUED
    phase: str = "queued"
    completed: int = 0
    total: int = 0
    current_item: str = ""
    errors: list[str] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)
    result: Any = None
    cancel_event: threading.Event = field(default_factory=threading.Event)
    lock: threading.RLock = field(default_factory=threading.RLock)
    future: Future[Any] | None = None


class BackgroundTaskService:
    """管理跨数据集任务；每项始终绑定创建时的数据集 UUID。"""

    def __init__(self, *, max_workers: int = 4) -> None:
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="datumdock-task",
        )
        self._tasks: dict[str, _TaskRecord] = {}
        self._lock = threading.RLock()

    def start(
        self,
        dataset_id: str,
        kind: str,
        work: Callable[[TaskContext], Any],
    ) -> str:
        """登记后再提交线程，确保极快任务也能被 UI 查询。"""

        task_id = new_id()
        record = _TaskRecord(task_id, dataset_id, kind)
        with self._lock:
            self._tasks[task_id] = record
        record.future = self._executor.submit(self._run, record, work)
        return task_id

    def snapshot(self, task_id: str) -> TaskSnapshot:
        record = self._record(task_id)
        with record.lock:
            return TaskSnapshot(
                record.id,
                record.dataset_id,
                record.kind,
                record.state,
                record.phase,
                record.completed,
                record.total,
                record.current_item,
                tuple(record.errors),
                dict(record.summary),
            )

    def result(self, task_id: str) -> Any:
        """仅在完成或部分完成后返回结果，失败任务保留错误明细。"""

        record = self._record(task_id)
        with record.lock:
            if record.state not in {TaskState.COMPLETED, TaskState.PARTIAL, TaskState.CANCELLED}:
                return None
            return record.result

    def cancel(self, task_id: str) -> bool:
        record = self._record(task_id)
        record.cancel_event.set()
        return record.state in {TaskState.QUEUED, TaskState.RUNNING}

    def active_snapshots(self) -> tuple[TaskSnapshot, ...]:
        with self._lock:
            identifiers = tuple(self._tasks)
        return tuple(
            snapshot
            for identifier in identifiers
            if (snapshot := self.snapshot(identifier)).state
            in {TaskState.QUEUED, TaskState.RUNNING}
        )

    def snapshots(self) -> tuple[TaskSnapshot, ...]:
        """任务中心可查看本次应用会话中的活动与已完成任务。"""

        with self._lock:
            identifiers = tuple(self._tasks)
        return tuple(self.snapshot(identifier) for identifier in identifiers)

    def shutdown(self, *, wait: bool = True) -> None:
        """关闭前请求全部活动任务取消，并等待当前原子步骤结束。"""

        for snapshot in self.active_snapshots():
            self.cancel(snapshot.id)
        self._executor.shutdown(wait=wait, cancel_futures=False)

    def _run(self, record: _TaskRecord, work: Callable[[TaskContext], Any]) -> None:
        context = TaskContext(record)
        with record.lock:
            record.state = TaskState.RUNNING
            record.phase = "running"
        try:
            result = work(context)
            with record.lock:
                record.result = result
                if record.cancel_event.is_set():
                    record.state = TaskState.CANCELLED
                    record.phase = "cancelled"
                elif record.errors:
                    record.state = TaskState.PARTIAL
                    record.phase = "partial"
                else:
                    record.state = TaskState.COMPLETED
                    record.phase = "completed"
        except Exception as error:
            with record.lock:
                record.errors.append(str(error))
                record.state = TaskState.FAILED
                record.phase = "failed"

    def _record(self, task_id: str) -> _TaskRecord:
        with self._lock:
            try:
                return self._tasks[task_id]
            except KeyError as error:
                raise KeyError(f"未知后台任务: {task_id}") from error
