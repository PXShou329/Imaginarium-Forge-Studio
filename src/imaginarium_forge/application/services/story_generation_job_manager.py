"""Background job manager for scene generation and revision (A3-R09 §14).

The defect this closes: generation ran on the Streamlit script thread, so the
whole page froze for the duration of a model call, the cancel button could not
be clicked while the work it was meant to stop was running, and a rerun during
generation had no defined behaviour.

Design constraints, all of them load-bearing:

* **No Streamlit.** This module must not import ``streamlit``, and workers
  must never touch Streamlit session state. A background thread has no
  script run context, so any such call is either a silent no-op or an
  error, and the resulting state would be invisible to the page anyway.
* **No Session crosses a thread.** The worker builds its own orchestration
  service from a thread-safe lifecycle reference, inside the worker.
* **Nothing unpicklable-in-spirit goes on the queue.** Events carry strings,
  enums and IDs — never a provider, Session, or Streamlit object — so the UI
  can consume them without reaching back into worker-owned state.
* **One active job.** A second start is rejected rather than silently
  overwriting the first, because overwriting loses the only handle to a thread
  that is still writing to the database.
* **Terminal exactly once.** Both emission (worker side) and consumption (UI
  side) are guarded, so a rerun cannot duplicate a draft refresh or a success
  message.

The feasibility spike (``R09_FEASIBILITY_SPIKE_REPORT.md``) established that
this boundary is fully testable with deterministic gates, while Streamlit's
timed rerun is not observable under AppTest. The split here follows that
finding: everything correctness-critical lives in this module.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from queue import Empty, Queue
from threading import Event, RLock, Thread
from weakref import WeakSet

from imaginarium_forge.application.errors import ApplicationError
from imaginarium_forge.application.services.scene_generation_service import RunStatus
from imaginarium_forge.application.services.story_orchestration_service import (
    GenerateSceneRequest,
    OrchestratedOutcome,
    ReasonCode,
    ReviseSceneRequest,
)


class StoryGenerationJobKind(StrEnum):
    GENERATION = "generation"
    REVISION = "revision"


class StoryGenerationJobStatus(StrEnum):
    """UI-facing job status.

    ``CANCEL_REQUESTED`` exists so the UI can never claim a run was cancelled
    merely because the button was pressed: only the worker, after the
    orchestration returns a terminal outcome, may move the job to ``CANCELLED``.
    """

    RUNNING = "running"
    CANCEL_REQUESTED = "cancel_requested"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"
    TIMEOUT = "timeout"


class StoryGenerationJobEventKind(StrEnum):
    STARTED = "started"
    CHUNK = "chunk"
    PARTIAL = "partial"
    TERMINAL = "terminal"


TERMINAL_STATUSES = frozenset(
    {
        StoryGenerationJobStatus.COMPLETED,
        StoryGenerationJobStatus.CANCELLED,
        StoryGenerationJobStatus.FAILED,
        StoryGenerationJobStatus.TIMEOUT,
    }
)

_RUN_STATUS_TO_JOB_STATUS = {
    RunStatus.COMPLETED: StoryGenerationJobStatus.COMPLETED,
    RunStatus.CANCELLED: StoryGenerationJobStatus.CANCELLED,
    RunStatus.FAILED: StoryGenerationJobStatus.FAILED,
    RunStatus.TIMEOUT: StoryGenerationJobStatus.TIMEOUT,
}

# Streamlit creates one manager per session, but stale-run recovery can be
# entered from a different tab. A weak process registry lets recovery observe
# every live manager without extending any session's lifetime.
_MANAGER_REGISTRY: WeakSet[StoryGenerationJobManager] = WeakSet()
_MANAGER_REGISTRY_LOCK = RLock()


@dataclass(frozen=True, slots=True)
class StoryGenerationJobEvent:
    """One immutable thing the worker reports.

    A single frozen type rather than a union keeps the queue contract obvious;
    ``kind`` says which fields are meaningful. Everything here is a string, an
    enum or an ID — deliberately nothing that owns a connection or a lock.
    """

    kind: StoryGenerationJobEventKind
    job_id: str
    scene_id: str
    job_kind: StoryGenerationJobKind
    sequence: int = 0
    text: str = ""
    #: terminal only
    status: StoryGenerationJobStatus | None = None
    run_id: str = ""
    run_status: RunStatus | None = None
    reason_code: str = ReasonCode.NONE
    error_reason: str = ""
    draft_id: str = ""
    partial_draft_id: str = ""


@dataclass(frozen=True, slots=True)
class StoryGenerationJobState:
    """Immutable snapshot handed to callers.

    Returning the live job would let the UI mutate worker-owned state; every
    public method therefore returns one of these instead.
    """

    job_id: str
    scene_id: str
    kind: StoryGenerationJobKind
    status: StoryGenerationJobStatus
    started_at: str
    start_count: int
    terminal_emitted: bool
    terminal_consumed: bool
    is_alive: bool

    @property
    def is_active(self) -> bool:
        return self.status not in TERMINAL_STATUSES


@dataclass(slots=True)
class _Job:
    """Internal, mutable. Never handed out; see ``StoryGenerationJobState``."""

    job_id: str
    scene_id: str
    kind: StoryGenerationJobKind
    started_at: str
    cancel_event: Event
    events: Queue[StoryGenerationJobEvent]
    #: set once the worker has emitted its terminal event, so callers can
    #: wait on an event instead of busy-polling
    terminal_signal: Event = field(default_factory=Event)
    status: StoryGenerationJobStatus = StoryGenerationJobStatus.RUNNING
    thread: Thread | None = None
    start_count: int = 0
    terminal_emitted: bool = False
    terminal_consumed: bool = False
    chunk_sequence: int = 0
    _lock: RLock = field(default_factory=RLock)


GenerationRunner = Callable[
    [GenerateSceneRequest, Event, Callable[[str], None]], OrchestratedOutcome
]
RevisionRunner = Callable[
    [ReviseSceneRequest, Event, Callable[[str], None]], OrchestratedOutcome
]


class DuplicateJobError(ApplicationError):
    """A second job was requested while one is still active.

    Rejecting is the safe direction: the running worker still holds the only
    reference to a thread that is writing generation rows, so replacing it
    would lose the ability to cancel or observe that work.
    """


class StoryGenerationJobManager:
    """Owns background generation/revision jobs. Streamlit-free by contract.

    Runners are injected rather than constructed here so that the manager can
    be tested without a provider, a database or a Streamlit session — and so
    the production runner can build its orchestration service *inside* the
    worker thread, which is the only place it may be built.
    """

    def __init__(
        self,
        *,
        generation_runner: GenerationRunner,
        revision_runner: RevisionRunner,
    ) -> None:
        self._generation_runner = generation_runner
        self._revision_runner = revision_runner
        self._jobs: dict[str, _Job] = {}
        self._active_job_id: str | None = None
        # A dict write is atomic under CPython today, but "the GIL protects
        # us" is not a contract. The check-then-register sequence below must
        # be atomic regardless of interpreter.
        self._lock = RLock()
        with _MANAGER_REGISTRY_LOCK:
            _MANAGER_REGISTRY.add(self)

    # ------------------------------------------------------------- public
    def start_generation(
        self, request: GenerateSceneRequest
    ) -> StoryGenerationJobState:
        return self._start(
            kind=StoryGenerationJobKind.GENERATION,
            scene_id=request.scene_id,
            invoke=lambda job: self._generation_runner(
                request, job.cancel_event, _chunk_emitter(job)
            ),
        )

    def start_revision(
        self, request: ReviseSceneRequest, *, scene_id: str
    ) -> StoryGenerationJobState:
        """``scene_id`` is passed explicitly: a revision request names a draft,
        and resolving it to a scene would need a database read the manager is
        not allowed to perform."""
        return self._start(
            kind=StoryGenerationJobKind.REVISION,
            scene_id=scene_id,
            invoke=lambda job: self._revision_runner(
                request, job.cancel_event, _chunk_emitter(job)
            ),
        )

    def poll(self, job_id: str) -> tuple[StoryGenerationJobEvent, ...]:
        """Drain everything queued so far. Events are never replayed."""
        job = self._require(job_id)
        drained: list[StoryGenerationJobEvent] = []
        while True:
            try:
                drained.append(job.events.get_nowait())
            except Empty:
                break
        for event in drained:
            if event.kind is StoryGenerationJobEventKind.TERMINAL:
                with job._lock:
                    job.terminal_consumed = True
                    if event.status is not None:
                        job.status = event.status
                with self._lock:
                    if self._active_job_id == job.job_id:
                        self._active_job_id = None
        return tuple(drained)

    def cancel(self, job_id: str) -> StoryGenerationJobState:
        """Request cancellation. This does NOT make the job cancelled.

        Only the worker, once the orchestration has returned a terminal
        outcome, may report ``CANCELLED`` — otherwise the UI would claim a run
        stopped while the provider call was still in flight and the database
        row still RUNNING.
        """
        job = self._require(job_id)
        with job._lock:
            if job.status in TERMINAL_STATUSES:
                return _snapshot(job)
            job.cancel_event.set()
            job.status = StoryGenerationJobStatus.CANCEL_REQUESTED
            return _snapshot(job)

    def get(self, job_id: str) -> StoryGenerationJobState:
        return _snapshot(self._require(job_id))

    def wait_for_terminal(self, job_id: str, *, timeout: float) -> bool:
        """Block until the worker has emitted its terminal event.

        For callers that legitimately block — deterministic tests, a future
        CLI. The Streamlit page must NOT call this: blocking inside a script
        run is exactly the frozen-UI defect R09 exists to remove.
        """
        job = self._require(job_id)
        return job.terminal_signal.wait(timeout=timeout)

    def active_job(self) -> StoryGenerationJobState | None:
        with self._lock:
            job_id = self._active_job_id
            job = self._jobs.get(job_id) if job_id else None
        return _snapshot(job) if job is not None else None

    @classmethod
    def active_jobs_in_process(cls) -> tuple[StoryGenerationJobState, ...]:
        """Return active snapshots from every live Streamlit session.

        The weak registry is process-local by design: after a process restart
        it is empty, which is exactly when persistent stale RUNNING rows need
        recovery. Snapshots preserve the manager's no-live-state-leak rule.
        """

        with _MANAGER_REGISTRY_LOCK:
            managers = tuple(_MANAGER_REGISTRY)
        jobs = tuple(
            state
            for manager in managers
            if (state := manager.active_job()) is not None and state.is_active
        )
        return tuple(sorted(jobs, key=lambda state: state.job_id))

    def cleanup_terminal_jobs(self) -> tuple[str, ...]:
        """Forget jobs whose terminal event the UI has already consumed.

        An active job is never removed: dropping it would orphan a live thread
        and leave the UI unable to cancel or observe it.
        """
        removed: list[str] = []
        with self._lock:
            for job_id, job in list(self._jobs.items()):
                with job._lock:
                    finished = (
                        job.status in TERMINAL_STATUSES and job.terminal_consumed
                    )
                if finished and job_id != self._active_job_id:
                    del self._jobs[job_id]
                    removed.append(job_id)
        return tuple(removed)

    # ------------------------------------------------------------ internal
    def _require(self, job_id: str) -> _Job:
        with self._lock:
            job = self._jobs.get(job_id)
        if job is None:
            raise ApplicationError(f"找不到背景工作：{job_id}")
        return job

    def _start(
        self,
        *,
        kind: StoryGenerationJobKind,
        scene_id: str,
        invoke: Callable[[_Job], OrchestratedOutcome],
    ) -> StoryGenerationJobState:
        with self._lock:
            active = (
                self._jobs.get(self._active_job_id) if self._active_job_id else None
            )
            if active is not None and active.status not in TERMINAL_STATUSES:
                raise DuplicateJobError(
                    "已有背景工作進行中"
                    f"（{active.kind.value}，場景 {active.scene_id[:8]}…）。"
                    "請等待其結束或先取消，再開始新的工作。"
                )
            job = _Job(
                job_id=str(uuid.uuid4()),
                scene_id=scene_id,
                kind=kind,
                started_at=datetime.now(UTC).isoformat(),
                cancel_event=Event(),
                events=Queue(),
            )
            self._jobs[job.job_id] = job
            self._active_job_id = job.job_id

        thread = Thread(
            target=self._run,
            args=(job, invoke),
            daemon=True,
            name=f"story-job-{job.job_id}",
        )
        job.thread = thread
        thread.start()
        return _snapshot(job)

    def _run(self, job: _Job, invoke: Callable[[_Job], OrchestratedOutcome]) -> None:
        with job._lock:
            job.start_count += 1
        job.events.put(
            StoryGenerationJobEvent(
                kind=StoryGenerationJobEventKind.STARTED,
                job_id=job.job_id,
                scene_id=job.scene_id,
                job_kind=job.kind,
            )
        )
        try:
            outcome = invoke(job)
        except ApplicationError as exc:
            # A pre-run validation failure (no accepted card, ineligible
            # participant…) never created a run row, so there is no run ID to
            # report and none is invented.
            self._emit_terminal(
                job,
                status=StoryGenerationJobStatus.FAILED,
                reason_code=ReasonCode.NONE,
                error_reason=str(exc),
            )
            return
        except Exception as exc:
            # The worker crashed somewhere the orchestration did not handle.
            # The UI is told the job failed so it does not display "running"
            # forever, but the manager does NOT touch the database: if the
            # orchestration left a RUNNING row, resolving it belongs to the
            # stale-RUNNING recovery slice, not to a guess made here.
            self._emit_terminal(
                job,
                status=StoryGenerationJobStatus.FAILED,
                reason_code=ReasonCode.PROVIDER_ERROR,
                error_reason=f"背景工作發生未預期錯誤：{type(exc).__name__}: {exc}",
            )
            return

        if outcome.partial_draft is not None:
            job.events.put(
                StoryGenerationJobEvent(
                    kind=StoryGenerationJobEventKind.PARTIAL,
                    job_id=job.job_id,
                    scene_id=job.scene_id,
                    job_kind=job.kind,
                    partial_draft_id=outcome.partial_draft.id,
                    text=outcome.partial_draft.prose_text,
                )
            )
        self._emit_terminal(
            job,
            status=_RUN_STATUS_TO_JOB_STATUS.get(
                outcome.status, StoryGenerationJobStatus.FAILED
            ),
            run_id=outcome.run_id,
            run_status=outcome.status,
            reason_code=outcome.reason_code,
            error_reason=outcome.error_reason,
            draft_id=outcome.draft.id if outcome.draft else "",
            partial_draft_id=(
                outcome.partial_draft.id if outcome.partial_draft else ""
            ),
        )

    def _emit_terminal(
        self,
        job: _Job,
        *,
        status: StoryGenerationJobStatus,
        run_id: str = "",
        run_status: RunStatus | None = None,
        reason_code: str = ReasonCode.NONE,
        error_reason: str = "",
        draft_id: str = "",
        partial_draft_id: str = "",
    ) -> None:
        with job._lock:
            if job.terminal_emitted:
                return
            job.terminal_emitted = True
            job.status = status
            sequence = job.chunk_sequence + 1
        job.events.put(
            StoryGenerationJobEvent(
                kind=StoryGenerationJobEventKind.TERMINAL,
                job_id=job.job_id,
                scene_id=job.scene_id,
                job_kind=job.kind,
                sequence=sequence,
                status=status,
                run_id=run_id,
                run_status=run_status,
                reason_code=reason_code,
                error_reason=error_reason,
                draft_id=draft_id,
                partial_draft_id=partial_draft_id,
            )
        )
        job.terminal_signal.set()


def _chunk_emitter(job: _Job) -> Callable[[str], None]:
    """Worker-side ``on_chunk``: enqueue only, never touch Streamlit."""

    def emit(text: str) -> None:
        with job._lock:
            job.chunk_sequence += 1
            sequence = job.chunk_sequence
        job.events.put(
            StoryGenerationJobEvent(
                kind=StoryGenerationJobEventKind.CHUNK,
                job_id=job.job_id,
                scene_id=job.scene_id,
                job_kind=job.kind,
                sequence=sequence,
                text=text,
            )
        )

    return emit


def _snapshot(job: _Job) -> StoryGenerationJobState:
    with job._lock:
        return StoryGenerationJobState(
            job_id=job.job_id,
            scene_id=job.scene_id,
            kind=job.kind,
            status=job.status,
            started_at=job.started_at,
            start_count=job.start_count,
            terminal_emitted=job.terminal_emitted,
            terminal_consumed=job.terminal_consumed,
            is_alive=bool(job.thread and job.thread.is_alive()),
        )
