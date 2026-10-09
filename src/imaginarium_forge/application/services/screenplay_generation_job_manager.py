"""Process-local background jobs for screenplay generation.

The manager deliberately has no Streamlit dependency. A worker receives a
real cancellation:class:`threading.Event` and an append-only prose callback;
the UI may poll immutable snapshots without blocking. Only the durable
``ScreenplayGenerationOutcome`` decides the terminal status, so a requested
stop is never presented as a completed cancellation before the service has
persisted its terminal state.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from threading import Event, RLock, Thread

from imaginarium_forge.application.services.screenplay_adaptation_service import (
    ScreenplayGenerationOutcome,
)

type ScreenplayGenerationRunner = Callable[
    [Event, Callable[[str], None]],
    ScreenplayGenerationOutcome,
]
type ScreenplayProviderRelease = Callable[[], None]


class ScreenplayGenerationJobStatus(StrEnum):
    """Truthful process-local state for one screenplay generation job."""

    RUNNING = "running"
    CANCEL_REQUESTED = "cancel_requested"
    COMPLETED = "completed"
    PARTIAL = "partial"
    CANCELLED = "cancelled"
    FAILED = "failed"


TERMINAL_SCREENPLAY_JOB_STATUSES = frozenset(
    {
        ScreenplayGenerationJobStatus.COMPLETED,
        ScreenplayGenerationJobStatus.PARTIAL,
        ScreenplayGenerationJobStatus.CANCELLED,
        ScreenplayGenerationJobStatus.FAILED,
    }
)


class DuplicateScreenplayGenerationJobError(RuntimeError):
    """A second job cannot replace the active or unconsumed terminal handle."""


class ScreenplayGenerationJobNotFoundError(RuntimeError):
    """The requested process-local screenplay job is unavailable."""


@dataclass(frozen=True, slots=True)
class ScreenplayGenerationJobSnapshot:
    """Immutable, secret-free snapshot suitable for repeated UI polling."""

    job_id: str
    status: ScreenplayGenerationJobStatus
    started_at: str
    delta_count: int
    buffered_preview_chars: int = 0
    partial_text: str = field(default="", repr=False)
    cancel_was_requested: bool = False
    terminal_consumed: bool = False
    is_alive: bool = False
    live_preview_redacted: bool = False

    @property
    def is_active(self) -> bool:
        return self.status not in TERMINAL_SCREENPLAY_JOB_STATUSES


@dataclass(frozen=True, slots=True)
class ScreenplayGenerationJobTerminal:
    """Exactly-once terminal outcome transferred to the UI script thread."""

    job_id: str
    status: ScreenplayGenerationJobStatus
    cancel_was_requested: bool
    run_status: str = ""
    run_id: str = ""
    adaptation_id: str = ""
    revision_id: str = ""
    reason_code: str = ""
    outcome: ScreenplayGenerationOutcome | None = field(default=None, repr=False)
    safe_error: str = ""


@dataclass(slots=True)
class _ScreenplayJob:
    job_id: str
    started_at: str
    cancel_event: Event
    expose_live_preview: bool
    terminal_signal: Event = field(default_factory=Event)
    status: ScreenplayGenerationJobStatus = ScreenplayGenerationJobStatus.RUNNING
    partial_chunks: list[str] = field(default_factory=list, repr=False)
    delta_count: int = 0
    cancel_was_requested: bool = False
    terminal_consumed: bool = False
    outcome: ScreenplayGenerationOutcome | None = field(default=None, repr=False)
    safe_error: str = ""
    thread: Thread | None = field(default=None, repr=False)
    lock: RLock = field(default_factory=RLock, repr=False)


class ScreenplayGenerationJobManager:
    """Own at most one session-scoped screenplay worker and terminal handle."""

    def __init__(self) -> None:
        self._jobs: dict[str, _ScreenplayJob] = {}
        self._active_job_id: str | None = None
        self._lock = RLock()

    def start(
        self,
        runner: ScreenplayGenerationRunner,
        *,
        release_provider: ScreenplayProviderRelease | None = None,
        expose_live_preview: bool = True,
    ) -> ScreenplayGenerationJobSnapshot:
        """Start one daemon worker and return without waiting for generation."""

        with self._lock:
            active = self._jobs.get(self._active_job_id) if self._active_job_id else None
            if active is not None:
                raise DuplicateScreenplayGenerationJobError(
                    "已有劇本生成背景工作尚未由頁面收取；請先停止或等待目前結果。"
                )
            job = _ScreenplayJob(
                job_id=str(uuid.uuid4()),
                started_at=datetime.now(UTC).isoformat(),
                cancel_event=Event(),
                expose_live_preview=expose_live_preview,
            )
            self._jobs[job.job_id] = job
            self._active_job_id = job.job_id

        thread = Thread(
            target=self._run,
            args=(job, runner, release_provider),
            daemon=True,
            name=f"screenplay-job-{job.job_id}",
        )
        job.thread = thread
        thread.start()
        return _snapshot(job)

    def get(self, job_id: str) -> ScreenplayGenerationJobSnapshot:
        return _snapshot(self._require(job_id))

    def active_job(self) -> ScreenplayGenerationJobSnapshot | None:
        with self._lock:
            job = self._jobs.get(self._active_job_id) if self._active_job_id else None
        return _snapshot(job) if job is not None else None

    def cancel(self, job_id: str) -> ScreenplayGenerationJobSnapshot:
        """Set the actual Event without inventing a terminal cancellation."""

        job = self._require(job_id)
        with job.lock:
            if job.status in TERMINAL_SCREENPLAY_JOB_STATUSES:
                return _snapshot(job)
            job.cancel_was_requested = True
            job.cancel_event.set()
            job.status = ScreenplayGenerationJobStatus.CANCEL_REQUESTED
            return _snapshot(job)

    def wait_for_terminal(self, job_id: str, *, timeout: float) -> bool:
        """Bounded wait helper for deterministic tests and fast UI polling."""

        return self._require(job_id).terminal_signal.wait(timeout=timeout)

    def consume_terminal(self, job_id: str) -> ScreenplayGenerationJobTerminal | None:
        """Transfer a terminal outcome once; running jobs and repeats return ``None``."""

        job = self._require(job_id)
        with job.lock:
            if job.status not in TERMINAL_SCREENPLAY_JOB_STATUSES:
                return None
            if job.terminal_consumed:
                return None
            outcome = job.outcome
            run = outcome.run if outcome is not None else None
            revision = outcome.revision if outcome is not None else None
            terminal = ScreenplayGenerationJobTerminal(
                job_id=job.job_id,
                status=job.status,
                cancel_was_requested=job.cancel_was_requested,
                run_status=run.status if run is not None else "",
                run_id=run.id if run is not None else "",
                adaptation_id=outcome.adaptation.id if outcome is not None else "",
                revision_id=revision.id if revision is not None else "",
                reason_code=run.reason_code if run is not None else "",
                outcome=outcome,
                safe_error=job.safe_error,
            )
            # The caller now owns the only manager-supplied outcome reference.
            # Release generated prose and provider objects after consumption.
            job.outcome = None
            job.terminal_consumed = True
        with self._lock:
            if self._active_job_id == job_id:
                self._active_job_id = None
        return terminal

    def cleanup_consumed(self) -> tuple[str, ...]:
        removed: list[str] = []
        with self._lock:
            for job_id, job in tuple(self._jobs.items()):
                with job.lock:
                    removable = (
                        job.status in TERMINAL_SCREENPLAY_JOB_STATUSES
                        and job.terminal_consumed
                        and not bool(job.thread and job.thread.is_alive())
                    )
                if removable:
                    del self._jobs[job_id]
                    removed.append(job_id)
        return tuple(removed)

    def _require(self, job_id: str) -> _ScreenplayJob:
        with self._lock:
            job = self._jobs.get(job_id)
        if job is None:
            raise ScreenplayGenerationJobNotFoundError(
                f"找不到劇本生成背景工作：{job_id}"
            )
        return job

    @staticmethod
    def _run(
        job: _ScreenplayJob,
        runner: ScreenplayGenerationRunner,
        release_provider: ScreenplayProviderRelease | None,
    ) -> None:
        def emit(delta: str) -> None:
            if not isinstance(delta, str):
                raise TypeError("screenplay prose delta must be text")
            if not delta:
                return
            with job.lock:
                if job.status in TERMINAL_SCREENPLAY_JOB_STATUSES:
                    return
                # The caller decides whether this job exposes a live preview.
                if job.expose_live_preview:
                    job.partial_chunks.append(delta)
                job.delta_count += 1

        outcome: ScreenplayGenerationOutcome | None = None
        safe_error = ""
        try:
            outcome = runner(job.cancel_event, emit)
            status = _terminal_status(outcome)
        except Exception:
            # Raw exceptions can contain active credentials, prompt bytes, or
            # remote response bodies. Never retain, chain, or display them.
            outcome = None
            status = ScreenplayGenerationJobStatus.FAILED
            safe_error = "劇本生成背景工作發生未預期錯誤；作者原稿完全沒有變更。"
        finally:
            if release_provider is not None:
                with suppress(Exception):
                    release_provider()

        with job.lock:
            job.outcome = outcome
            job.safe_error = safe_error
            job.status = status
        job.terminal_signal.set()


def _terminal_status(
    outcome: ScreenplayGenerationOutcome,
) -> ScreenplayGenerationJobStatus:
    return {
        "succeeded": ScreenplayGenerationJobStatus.COMPLETED,
        "partial": ScreenplayGenerationJobStatus.PARTIAL,
        "cancelled": ScreenplayGenerationJobStatus.CANCELLED,
        "failed": ScreenplayGenerationJobStatus.FAILED,
    }[outcome.run.status]


def _snapshot(job: _ScreenplayJob) -> ScreenplayGenerationJobSnapshot:
    with job.lock:
        return ScreenplayGenerationJobSnapshot(
            job_id=job.job_id,
            status=job.status,
            started_at=job.started_at,
            delta_count=job.delta_count,
            buffered_preview_chars=sum(len(chunk) for chunk in job.partial_chunks),
            partial_text=(
                "".join(job.partial_chunks) if job.expose_live_preview else ""
            ),
            cancel_was_requested=job.cancel_was_requested,
            terminal_consumed=job.terminal_consumed,
            is_alive=bool(job.thread and job.thread.is_alive()),
            live_preview_redacted=not job.expose_live_preview,
        )


__all__ = [
    "TERMINAL_SCREENPLAY_JOB_STATUSES",
    "DuplicateScreenplayGenerationJobError",
    "ScreenplayGenerationJobManager",
    "ScreenplayGenerationJobNotFoundError",
    "ScreenplayGenerationJobSnapshot",
    "ScreenplayGenerationJobStatus",
    "ScreenplayGenerationJobTerminal",
    "ScreenplayGenerationRunner",
    "ScreenplayProviderRelease",
]
