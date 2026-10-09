"""Session-owned, memory-only background work for local translation and install.

Workers never access Streamlit state. Exceptions are converted to fixed codes;
neither exception objects nor their messages, author text, or tracebacks are logged.
This module deliberately has no generation provider or credential dependency.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from threading import Event, RLock, Thread
from uuid import uuid4


class LocalTaskStatus(StrEnum):
    RUNNING = "running"
    CANCEL_REQUESTED = "cancel_requested"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class LocalTaskProgress:
    completed: int = 0
    total: int = 0
    partial_text: str = field(default="", repr=False)
    stage: str = "loading"


@dataclass(frozen=True, slots=True)
class LocalTaskResult[T]:
    value: T = field(repr=False)
    cancelled: bool = False


@dataclass(frozen=True, slots=True)
class LocalTaskSnapshot[T]:
    task_id: str
    status: LocalTaskStatus
    progress: LocalTaskProgress
    result: T | None = field(default=None, repr=False)
    incident_id: str = ""

    @property
    def active(self) -> bool:
        return self.status in {LocalTaskStatus.RUNNING, LocalTaskStatus.CANCEL_REQUESTED}


type LocalTaskRunner[T] = Callable[[Event, Callable[[LocalTaskProgress], None]], LocalTaskResult[T]]


class LocalTaskManager[T]:
    """Own a single result until the UI explicitly consumes it.

    Keeping the terminal handle prevents a fast completion from being overwritten
    by a second start before the script has received its partial/result. Managers
    are session-scoped and never placed in a global Streamlit resource cache.
    """

    def __init__(self) -> None:
        self._lock = RLock()
        self._cancel = Event()
        self._finished = Event()
        self._snapshot: LocalTaskSnapshot[T] | None = None

    def start(self, runner: LocalTaskRunner[T]) -> str:
        with self._lock:
            if self._snapshot is not None:
                raise RuntimeError("local task result has not been consumed")
            task_id = uuid4().hex
            self._cancel = Event()
            self._finished = Event()
            self._snapshot = LocalTaskSnapshot(
                task_id=task_id,
                status=LocalTaskStatus.RUNNING,
                progress=LocalTaskProgress(),
            )
            worker = Thread(
                target=self._run,
                args=(task_id, runner),
                name=f"local-translation-{task_id}",
                daemon=True,
            )
            try:
                worker.start()
            except Exception:
                self._snapshot = LocalTaskSnapshot(
                    task_id=task_id,
                    status=LocalTaskStatus.FAILED,
                    progress=LocalTaskProgress(),
                    incident_id=uuid4().hex[:12],
                )
                self._finished.set()
            return task_id

    def snapshot(self) -> LocalTaskSnapshot[T] | None:
        with self._lock:
            return self._snapshot

    def cancel(self) -> None:
        with self._lock:
            current = self._snapshot
            if current is None or not current.active:
                return
            self._cancel.set()
            self._snapshot = LocalTaskSnapshot(
                task_id=current.task_id,
                status=LocalTaskStatus.CANCEL_REQUESTED,
                progress=current.progress,
            )

    def consume(self) -> LocalTaskSnapshot[T] | None:
        with self._lock:
            current = self._snapshot
            if current is None or current.active:
                return None
            self._snapshot = None
            return current

    def wait(self, timeout: float) -> bool:
        """A bounded synchronization hook for deterministic worker tests."""
        return self._finished.wait(timeout=max(0.0, min(timeout, 30.0)))

    def _report(self, task_id: str, progress: LocalTaskProgress) -> None:
        if not 0 <= progress.completed <= progress.total:
            raise ValueError("invalid local progress counts")
        if progress.stage not in {"loading", "translating", "installing", "verifying"}:
            raise ValueError("invalid local progress stage")
        with self._lock:
            current = self._snapshot
            if current is None or current.task_id != task_id or not current.active:
                return
            self._snapshot = LocalTaskSnapshot(
                task_id=task_id,
                status=current.status,
                progress=progress,
            )

    def _run(self, task_id: str, runner: LocalTaskRunner[T]) -> None:
        try:
            result = runner(self._cancel, lambda progress: self._report(task_id, progress))
            status = LocalTaskStatus.CANCELLED if result.cancelled else LocalTaskStatus.COMPLETED
            value: T | None = result.value
            incident_id = ""
        except Exception:
            # Do not inspect exception text; native libraries can include source
            # documents, file paths or process environment values in failures.
            status = LocalTaskStatus.FAILED
            value = None
            incident_id = uuid4().hex[:12]
        with self._lock:
            current = self._snapshot
            if current is not None and current.task_id == task_id:
                self._snapshot = LocalTaskSnapshot(
                    task_id=task_id,
                    status=status,
                    progress=current.progress,
                    result=value,
                    incident_id=incident_id,
                )
            self._finished.set()
