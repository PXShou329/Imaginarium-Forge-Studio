"""Process-local background jobs for the three short-form authoring pages.

The manager is deliberately Streamlit-free. A worker receives only an
explicit cancellation event and a text-delta callback; it never reads or
writes UI session state. Partial prose remains process-local until the UI
polls a frozen snapshot, and only a terminal typed facade response may become
an applyable candidate envelope.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from threading import Event, RLock, Thread

from imaginarium_forge.application.services.generation.authoring_facade import (
    AuthoringCandidateStatus,
    CreativeAuthoringResponse,
)
from imaginarium_forge.application.services.generation.contracts import (
    GenerationErrorCategory,
    GenerationExecutionStatus,
)
from imaginarium_forge.application.services.generation.facade import (
    CreativeCandidateResponse,
)
from imaginarium_forge.application.services.generation.length_assurance import (
    AssuranceReason,
    ProviderTermination,
)
from imaginarium_forge.application.services.generation.runtime import TextDeltaCallback

type CandidateJobResponse = CreativeCandidateResponse | CreativeAuthoringResponse
type CandidateGenerationRunner = Callable[
    [Event, TextDeltaCallback],
    CandidateJobResponse,
]


class CandidateGenerationJobStatus(StrEnum):
    """Truthful UI status; requesting cancellation is not terminal cancellation."""

    RUNNING = "running"
    CANCEL_REQUESTED = "cancel_requested"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


TERMINAL_CANDIDATE_JOB_STATUSES = frozenset(
    {
        CandidateGenerationJobStatus.COMPLETED,
        CandidateGenerationJobStatus.CANCELLED,
        CandidateGenerationJobStatus.FAILED,
    }
)


class DuplicateCandidateGenerationJobError(RuntimeError):
    """A second worker cannot replace the only handle to an active request."""


class CandidateGenerationJobNotFoundError(RuntimeError):
    """The requested process-local job is no longer available."""


@dataclass(frozen=True, slots=True)
class CandidateGenerationJobSnapshot:
    """Immutable, non-secret state exposed to the polling UI."""

    job_id: str
    status: CandidateGenerationJobStatus
    started_at: str
    delta_count: int
    partial_text: str = field(default="", repr=False)
    cancel_was_requested: bool = False
    terminal_consumed: bool = False
    is_alive: bool = False

    @property
    def is_active(self) -> bool:
        return self.status not in TERMINAL_CANDIDATE_JOB_STATUSES


@dataclass(frozen=True, slots=True)
class CandidateGenerationJobTerminal:
    """Exactly-once terminal value consumed by the Streamlit script thread."""

    job_id: str
    status: CandidateGenerationJobStatus
    cancel_was_requested: bool
    response: CandidateJobResponse | None = field(default=None, repr=False)
    safe_error: str = ""


@dataclass(slots=True)
class _CandidateJob:
    job_id: str
    started_at: str
    cancel_event: Event
    terminal_signal: Event = field(default_factory=Event)
    status: CandidateGenerationJobStatus = CandidateGenerationJobStatus.RUNNING
    partial_chunks: list[str] = field(default_factory=list, repr=False)
    delta_count: int = 0
    cancel_was_requested: bool = False
    terminal_consumed: bool = False
    response: CandidateJobResponse | None = field(default=None, repr=False)
    safe_error: str = ""
    thread: Thread | None = field(default=None, repr=False)
    lock: RLock = field(default_factory=RLock, repr=False)


class CandidateGenerationJobManager:
    """Own one session-scoped background candidate operation at a time."""

    def __init__(self) -> None:
        self._jobs: dict[str, _CandidateJob] = {}
        self._active_job_id: str | None = None
        self._lock = RLock()

    def start(self, runner: CandidateGenerationRunner) -> CandidateGenerationJobSnapshot:
        """Start one daemon worker without blocking the caller's UI script run."""

        with self._lock:
            active = self._jobs.get(self._active_job_id) if self._active_job_id else None
            if active is not None:
                # A terminal value remains the active handle until the UI script
                # consumes it. Replacing that handle here would silently lose a
                # completed or cancelled partial candidate between reruns.
                raise DuplicateCandidateGenerationJobError(
                    "已有 AI 候選背景工作尚未由頁面收取；請等待目前結果完成同步。"
                )
            job = _CandidateJob(
                job_id=str(uuid.uuid4()),
                started_at=datetime.now(UTC).isoformat(),
                cancel_event=Event(),
            )
            self._jobs[job.job_id] = job
            self._active_job_id = job.job_id

        thread = Thread(
            target=self._run,
            args=(job, runner),
            daemon=True,
            name=f"candidate-job-{job.job_id}",
        )
        job.thread = thread
        thread.start()
        return _snapshot(job)

    def get(self, job_id: str) -> CandidateGenerationJobSnapshot:
        return _snapshot(self._require(job_id))

    def active_job(self) -> CandidateGenerationJobSnapshot | None:
        with self._lock:
            job = self._jobs.get(self._active_job_id) if self._active_job_id else None
        return _snapshot(job) if job is not None else None

    def cancel(self, job_id: str) -> CandidateGenerationJobSnapshot:
        """Set the real Event but do not claim cancellation before worker evidence."""

        job = self._require(job_id)
        with job.lock:
            if job.status in TERMINAL_CANDIDATE_JOB_STATUSES:
                return _snapshot(job)
            job.cancel_was_requested = True
            job.cancel_event.set()
            job.status = CandidateGenerationJobStatus.CANCEL_REQUESTED
            return _snapshot(job)

    def wait_for_terminal(self, job_id: str, *, timeout: float) -> bool:
        """Bounded helper for deterministic tests and a tiny fast-result UI path."""

        return self._require(job_id).terminal_signal.wait(timeout=timeout)

    def consume_terminal(self, job_id: str) -> CandidateGenerationJobTerminal | None:
        """Return a terminal facade response once; active jobs return ``None``."""

        job = self._require(job_id)
        with job.lock:
            if job.status not in TERMINAL_CANDIDATE_JOB_STATUSES:
                return None
            if job.terminal_consumed:
                return None
            job.terminal_consumed = True
            terminal = CandidateGenerationJobTerminal(
                job_id=job.job_id,
                status=job.status,
                cancel_was_requested=job.cancel_was_requested,
                response=job.response,
                safe_error=job.safe_error,
            )
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
                        job.status in TERMINAL_CANDIDATE_JOB_STATUSES
                        and job.terminal_consumed
                        and not bool(job.thread and job.thread.is_alive())
                    )
                if removable:
                    del self._jobs[job_id]
                    removed.append(job_id)
        return tuple(removed)

    def _require(self, job_id: str) -> _CandidateJob:
        with self._lock:
            job = self._jobs.get(job_id)
        if job is None:
            raise CandidateGenerationJobNotFoundError(
                f"找不到 AI 候選背景工作：{job_id}"
            )
        return job

    @staticmethod
    def _run(job: _CandidateJob, runner: CandidateGenerationRunner) -> None:
        def emit(delta: str) -> None:
            if not isinstance(delta, str):
                raise TypeError("candidate prose delta must be text")
            if not delta:
                return
            with job.lock:
                if job.status in TERMINAL_CANDIDATE_JOB_STATUSES:
                    return
                job.partial_chunks.append(delta)
                job.delta_count += 1

        response: CandidateJobResponse | None = None
        try:
            completed_response = runner(job.cancel_event, emit)
            status = _terminal_status(completed_response)
            response = completed_response
            safe_error = ""
        except Exception:
            status = CandidateGenerationJobStatus.FAILED
            safe_error = "AI 候選背景工作發生未預期錯誤；原稿完全沒有變更。"

        with job.lock:
            job.response = response
            job.safe_error = safe_error
            job.status = status
        job.terminal_signal.set()


def _terminal_status(response: CandidateJobResponse) -> CandidateGenerationJobStatus:
    failure = response.failure
    if failure is not None and failure.category is GenerationErrorCategory.CANCELLED:
        return CandidateGenerationJobStatus.CANCELLED

    prose = (
        response.provenance.prose_pipeline
        if isinstance(response, CreativeAuthoringResponse)
        else response.prose_pipeline
    )
    if prose is not None and (
        prose.decision.reason is AssuranceReason.CANCELLED_BY_AUTHOR
        or any(
            call.provider_termination is ProviderTermination.CANCELLED
            for call in prose.calls
        )
    ):
        return CandidateGenerationJobStatus.CANCELLED

    failed = (
        response.status is AuthoringCandidateStatus.FAILED
        if isinstance(response, CreativeAuthoringResponse)
        else response.status is not GenerationExecutionStatus.SUCCEEDED
    )
    return (
        CandidateGenerationJobStatus.FAILED
        if failed
        else CandidateGenerationJobStatus.COMPLETED
    )


def _snapshot(job: _CandidateJob) -> CandidateGenerationJobSnapshot:
    with job.lock:
        return CandidateGenerationJobSnapshot(
            job_id=job.job_id,
            status=job.status,
            started_at=job.started_at,
            delta_count=job.delta_count,
            partial_text="".join(job.partial_chunks),
            cancel_was_requested=job.cancel_was_requested,
            terminal_consumed=job.terminal_consumed,
            is_alive=bool(job.thread and job.thread.is_alive()),
        )


__all__ = [
    "TERMINAL_CANDIDATE_JOB_STATUSES",
    "CandidateGenerationJobManager",
    "CandidateGenerationJobNotFoundError",
    "CandidateGenerationJobSnapshot",
    "CandidateGenerationJobStatus",
    "CandidateGenerationJobTerminal",
    "CandidateGenerationRunner",
    "CandidateJobResponse",
    "DuplicateCandidateGenerationJobError",
]
