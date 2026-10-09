"""Process-local background worker for the durable creative workflow.

The database remains the workflow source of truth. This object owns only a
short-lived thread and its cancellation probe; it deliberately has no session
credential parameter or field, so a Streamlit session secret cannot outlive the
explicit foreground action that supplied it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from threading import Event, RLock, Thread

from imaginarium_forge.application.services.durable_generation.contracts import (
    DurableExecutionResult,
)
from imaginarium_forge.application.services.durable_generation.executor import (
    DurableCreativeWorkflowService,
    DurableWorkflowError,
)


class DurableWorkerBusyError(DurableWorkflowError):
    """Another durable workflow is already using the process worker."""


class DurableWorkerStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    FINISHED = "finished"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class DurableWorkerSnapshot:
    job_id: str
    status: DurableWorkerStatus
    result: DurableExecutionResult | None = None
    error_code: str = ""
    safe_error_message: str = ""
    is_alive: bool = False

    @property
    def is_active(self) -> bool:
        return self.status in {
            DurableWorkerStatus.QUEUED,
            DurableWorkerStatus.RUNNING,
        }


@dataclass(slots=True)
class _Work:
    job_id: str
    max_steps: int
    interrupt: Event = field(default_factory=Event, repr=False)
    status: DurableWorkerStatus = DurableWorkerStatus.QUEUED
    result: DurableExecutionResult | None = None
    error_code: str = ""
    safe_error_message: str = ""
    thread: Thread | None = field(default=None, repr=False)
    lock: RLock = field(default_factory=RLock, repr=False)


class DurableWorkflowWorker:
    """Run at most one durable job in a background thread and expose polling."""

    def __init__(self, service: DurableCreativeWorkflowService) -> None:
        self._service = service
        self._work: dict[str, _Work] = {}
        self._active_job_id: str | None = None
        self._lock = RLock()

    def submit(self, job_id: str, *, max_steps: int) -> DurableWorkerSnapshot:
        """Start one bounded run without accepting or retaining a session secret."""

        if max_steps < 1:
            raise ValueError("max_steps must be positive")
        with self._lock:
            active = self._active_work()
            if active is not None:
                if active.job_id == job_id:
                    return _snapshot(active)
                raise DurableWorkerBusyError("另一個耐久工作流仍在背景執行；請先等待、暫停或取消。")
            work = _Work(job_id=job_id, max_steps=max_steps)
            thread = Thread(
                target=self._run,
                args=(work,),
                name=f"durable-workflow-{job_id[-24:]}",
                daemon=True,
            )
            work.thread = thread
            self._work[job_id] = work
            self._active_job_id = job_id
            thread.start()
            return _snapshot(work)

    def poll(self, job_id: str) -> DurableWorkerSnapshot | None:
        with self._lock:
            work = self._work.get(job_id)
        return None if work is None else _snapshot(work)

    def active(self) -> DurableWorkerSnapshot | None:
        with self._lock:
            work = self._active_work()
        return None if work is None else _snapshot(work)

    def request_pause(self, job_id: str) -> DurableWorkerSnapshot:
        work = self._required_active(job_id)
        self._service.request_pause(job_id)
        work.interrupt.set()
        return _snapshot(work)

    def request_cancel(self, job_id: str) -> DurableWorkerSnapshot:
        work = self._required_active(job_id)
        self._service.request_cancel(job_id)
        work.interrupt.set()
        return _snapshot(work)

    def shutdown(self, *, wait: bool = True) -> None:
        """Join a live worker for deterministic application/test shutdown."""

        with self._lock:
            work = self._active_work()
            thread = None if work is None else work.thread
        if wait and thread is not None:
            thread.join()

    def _run(self, work: _Work) -> None:
        with work.lock:
            work.status = DurableWorkerStatus.RUNNING
        try:
            result = self._service.run_until_blocked(
                work.job_id,
                max_steps=work.max_steps,
                cancel=work.interrupt,
            )
        except Exception:
            with work.lock:
                work.status = DurableWorkerStatus.FAILED
                work.error_code = "worker_exception"
                work.safe_error_message = "背景執行器未能完成這次有界工作；請重新載入持久狀態。"
        else:
            with work.lock:
                work.result = result
                work.status = DurableWorkerStatus.FINISHED
        finally:
            with self._lock:
                if self._active_job_id == work.job_id:
                    self._active_job_id = None

    def _required_active(self, job_id: str) -> _Work:
        with self._lock:
            work = self._active_work()
            if work is None or work.job_id != job_id:
                raise DurableWorkerBusyError("指定的耐久工作流目前不在背景執行。")
            return work

    def _active_work(self) -> _Work | None:
        job_id = self._active_job_id
        if job_id is None:
            return None
        work = self._work.get(job_id)
        if work is None:
            self._active_job_id = None
            return None
        return work


def _snapshot(work: _Work) -> DurableWorkerSnapshot:
    with work.lock:
        thread = work.thread
        return DurableWorkerSnapshot(
            job_id=work.job_id,
            status=work.status,
            result=work.result,
            error_code=work.error_code,
            safe_error_message=work.safe_error_message,
            is_alive=bool(thread is not None and thread.is_alive()),
        )


__all__ = [
    "DurableWorkerBusyError",
    "DurableWorkerSnapshot",
    "DurableWorkerStatus",
    "DurableWorkflowWorker",
]
