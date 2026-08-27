"""Recover persistent generation runs orphaned by a stopped process (S8.2)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from imaginarium_forge.application.services.base import ServiceBase, SessionProvider
from imaginarium_forge.application.services.scene_generation_service import RunStatus
from imaginarium_forge.application.services.story_generation_job_manager import (
    StoryGenerationJobManager,
)
from imaginarium_forge.application.services.story_orchestration_service import (
    ReasonCode,
)
from imaginarium_forge.infrastructure.db.repositories.story_repos import (
    GenerationRunRepository,
)


class StaleGenerationRunRecoveryService(ServiceBase):
    """Finalize stale RUNNING rows that have no matching in-memory worker.

    Generation runs predate the in-memory job manager and therefore do not
    persist its ephemeral job ID. The manager permits only one active job, so
    matching by the immutable scene ID is the conservative safe boundary: an
    older orphan for that scene may wait for the next entry, but a legitimate
    worker is never finalized underneath its provider call.
    """

    def __init__(
        self,
        session_factory: SessionProvider,
        *,
        stale_threshold_s: float,
    ) -> None:
        super().__init__(session_factory)
        if stale_threshold_s <= 0:
            raise ValueError("stale_threshold_s must be greater than zero")
        self._stale_threshold = timedelta(seconds=stale_threshold_s)

    def recover_stale_running_runs(
        self,
        *,
        job_manager: StoryGenerationJobManager | None = None,
        now: datetime | None = None,
    ) -> tuple[str, ...]:
        """Return IDs finalized during this invocation, oldest first.

        ``now`` is injectable so the threshold boundary is deterministic in
        direct tests. Re-running recovery is naturally idempotent because the
        repository only returns RUNNING rows and finalization re-checks that
        state before writing.
        """

        recovered_at = _as_utc(now or datetime.now(UTC))
        cutoff = recovered_at - self._stale_threshold
        active_scene_ids = _active_scene_ids(job_manager)
        recovered_ids: list[str] = []

        with self._transaction() as session:
            runs = GenerationRunRepository(session)
            for run in runs.list_running():
                started_at = _parse_started_at(run.started_at)
                if started_at is None or started_at >= cutoff:
                    continue
                if run.story_scene_id in active_scene_ids:
                    continue
                if runs.finalize_recovered_run(
                    run.id,
                    status=RunStatus.FAILED.value,
                    reason_code=ReasonCode.PROCESS_INTERRUPTED,
                    completed_at=recovered_at.isoformat(),
                    # The process was absent for an unknown part of this
                    # interval. Calling wall-clock downtime provider latency
                    # would turn recovery evidence into a fabricated metric.
                    latency_ms=0,
                ):
                    recovered_ids.append(run.id)

        return tuple(recovered_ids)


def _active_scene_ids(
    job_manager: StoryGenerationJobManager | None,
) -> frozenset[str]:
    # Constructing/passing the current session's manager remains explicit at
    # the UI boundary, while the process registry also protects a legitimate
    # worker owned by another browser tab. The argument is intentionally read
    # so tests and non-Streamlit callers cannot accidentally skip registration.
    if job_manager is not None:
        job_manager.active_job()
    return frozenset(
        state.scene_id
        for state in StoryGenerationJobManager.active_jobs_in_process()
    )


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    return value.astimezone(UTC)


def _parse_started_at(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)
