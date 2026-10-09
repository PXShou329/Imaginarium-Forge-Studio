"""SQLite-authoritative durable creative workflow executor.

Every claim/checkpoint/transition uses a short transaction. The injected step
runner and project adapter are invoked only after that transaction commits.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from threading import Event

from pydantic import SecretStr
from sqlalchemy.orm import Session

from imaginarium_forge.application.services.base import ServiceBase, SessionProvider
from imaginarium_forge.application.services.durable_generation.contracts import (
    LENGTH_ASSURANCE_STATE_KEY,
    CandidateApplyCommand,
    CreateDurableWorkflow,
    DurableExecutionResult,
    DurableStepRunner,
    DurableStepRunRequest,
    DurableStepRunResult,
    ExecutionDisposition,
    PartialCheckpoint,
    ProjectApplyRequest,
    ProjectArtifactAdapter,
    StepExecutionError,
)
from imaginarium_forge.application.services.durable_generation.planner import (
    LongFormWorkflowPlanner,
)
from imaginarium_forge.application.services.durable_generation.unit_author_policy import (
    AUTHOR_POLICY_KEY,
    effective_spec,
    granted_attempt_limit,
    projection,
    step_unit_id,
)
from imaginarium_forge.application.services.durable_generation.unit_length_contracts import (
    UNIT_PROSE_KINDS,
    resolve_unit_length_spec,
)
from imaginarium_forge.application.services.generation.continuation_planner import (
    ContinuationPlanner,
)
from imaginarium_forge.application.services.generation.length_assurance import (
    AssuranceAction,
    AssuranceLimits,
    AssuranceProgress,
    LengthAssuranceService,
    ProviderTermination,
)
from imaginarium_forge.application.services.generation.output_measurement import (
    measure_output,
)
from imaginarium_forge.application.services.generation.protected_facts_guard import (
    ProtectedFactsGuard,
)
from imaginarium_forge.application.services.generation.repetition_guard import (
    ContinuationRepetitionGuard,
    RepetitionGuardReport,
)
from imaginarium_forge.domain.generation.durable import (
    ACTIVE_JOB_STATUSES,
    AttemptStatus,
    CandidateAction,
    CandidateActionKind,
    CandidateActionStatus,
    CandidateProvenance,
    CandidateStatus,
    GenerationCandidate,
    GenerationJob,
    GenerationStep,
    GenerationStepAttempt,
    JobStatus,
    StepKind,
    StepStatus,
    sha256_content,
    sha256_json,
    sha256_text,
)
from imaginarium_forge.domain.generation.length import GenerationLengthSpec
from imaginarium_forge.infrastructure.db.repositories.creative_candidates import (
    CreativeCandidateRepository,
)
from imaginarium_forge.infrastructure.db.repositories.creative_generation import (
    CreativeGenerationRepository,
)
from imaginarium_forge.infrastructure.db.repositories.unit_author_decisions import (
    UnitAuthorDecisionRepository,
)


class DurableWorkflowError(RuntimeError):
    pass


class WorkflowNotFoundError(DurableWorkflowError):
    pass


class WorkflowIdempotencyConflict(DurableWorkflowError):
    pass


class ConcurrentWorkflowUpdate(DurableWorkflowError):
    pass


class WorkflowControlError(DurableWorkflowError):
    pass


class _ControlKind(StrEnum):
    PAUSE = "pause"
    CANCEL = "cancel"


class _ControlSignal(BaseException):
    def __init__(self, kind: _ControlKind) -> None:
        super().__init__(kind.value)
        self.kind = kind


@dataclass(frozen=True)
class _ClaimedStep:
    request: DurableStepRunRequest


_ASSURANCE_REPORT_KEY = "_cwe_2_1_length_assurance"
_CANDIDATE_ROLE_KEY = "_cwe_2_1_candidate_role"


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _job_copy(job: GenerationJob, **changes: object) -> GenerationJob:
    return GenerationJob.model_validate({**job.model_dump(mode="json"), **changes})


def _step_copy(step: GenerationStep, **changes: object) -> GenerationStep:
    return GenerationStep.model_validate({**step.model_dump(mode="json"), **changes})


def _attempt_copy(
    attempt: GenerationStepAttempt,
    **changes: object,
) -> GenerationStepAttempt:
    return GenerationStepAttempt.model_validate({**attempt.model_dump(mode="json"), **changes})


def _phase_for_step(kind: StepKind) -> JobStatus:
    if kind is StepKind.BRIEF_NORMALIZATION:
        return JobStatus.NORMALIZING_BRIEF
    if kind is StepKind.STORY_PLAN:
        return JobStatus.PLANNING
    if kind in {
        StepKind.CONTINUITY_CHECK,
        StepKind.FINAL_QUALITY_CHECK,
    }:
        return JobStatus.REVIEWING
    if kind is StepKind.TARGETED_REVISION:
        return JobStatus.REVISING
    if kind is StepKind.FINAL_ASSEMBLY:
        return JobStatus.ASSEMBLING
    return JobStatus.DRAFTING


class DurableCreativeWorkflowService(ServiceBase):
    """Orchestrate durable steps without owning any formal project aggregate."""

    def __init__(
        self,
        *,
        session_factory: SessionProvider,
        step_runner: DurableStepRunner,
        project_adapter: ProjectArtifactAdapter | None = None,
        planner: LongFormWorkflowPlanner | None = None,
        length_assurance: LengthAssuranceService | None = None,
        continuation_planner: ContinuationPlanner | None = None,
        repetition_guard: ContinuationRepetitionGuard | None = None,
        protected_facts_guard: ProtectedFactsGuard | None = None,
        clock: Callable[[], str] = _utc_now,
    ) -> None:
        super().__init__(session_factory)
        self._step_runner = step_runner
        self._project_adapter = project_adapter
        self._planner = planner or LongFormWorkflowPlanner()
        self._length_assurance = length_assurance or LengthAssuranceService()
        self._continuation_planner = continuation_planner or ContinuationPlanner()
        self._repetition_guard = repetition_guard or ContinuationRepetitionGuard()
        self._protected_facts_guard = protected_facts_guard or ProtectedFactsGuard()
        self._clock = clock

    def create_workflow(self, request: CreateDurableWorkflow) -> GenerationJob:
        plan = self._planner.plan(request)
        with self._transaction() as session:
            repository = CreativeGenerationRepository(session)
            existing = repository.get_job(request.job_id)
            if existing is not None:
                if (
                    existing.artifact_kind is not plan.job.artifact_kind
                    or existing.operation is not plan.job.operation
                    or existing.target_type != plan.job.target_type
                    or existing.target_id != plan.job.target_id
                    or existing.route != plan.job.route
                    or existing.quality_profile != plan.job.quality_profile
                    or existing.length_spec != plan.job.length_spec
                    or existing.source_snapshot_sha256 != plan.job.source_snapshot_sha256
                    or existing.context_snapshot_sha256 != plan.job.context_snapshot_sha256
                    or existing.workflow_version != plan.job.workflow_version
                    or existing.total_steps_hint != plan.job.total_steps_hint
                ):
                    raise WorkflowIdempotencyConflict(
                        "job id already exists with a different durable snapshot"
                    )
                return existing
            repository.add_job(plan.job)
            for step in plan.steps:
                repository.add_step(step)
        return plan.job

    def get_job(self, job_id: str) -> GenerationJob:
        job = self._read_only(lambda session: CreativeGenerationRepository(session).get_job(job_id))
        if job is None:
            raise WorkflowNotFoundError(f"durable job not found: {job_id}")
        return job

    def list_jobs(
        self,
        *,
        target_type: str | None = None,
        target_id: str | None = None,
    ) -> tuple[GenerationJob, ...]:
        """Return durable jobs for restart recovery and progress selection."""

        return self._read_only(
            lambda session: tuple(
                CreativeGenerationRepository(session).list_jobs(
                    target_type=target_type,
                    target_id=target_id,
                )
            )
        )

    def get_steps(self, job_id: str) -> tuple[GenerationStep, ...]:
        """Return the durable, ordinal step snapshot used by progress UIs."""

        def read(session: Session) -> tuple[GenerationStep, ...] | None:
            repository = CreativeGenerationRepository(session)
            if repository.get_job(job_id) is None:
                return None
            return tuple(repository.list_steps(job_id))

        steps = self._read_only(read)
        if steps is None:
            raise WorkflowNotFoundError(f"durable job not found: {job_id}")
        return steps

    def get_candidate(self, candidate_id: str) -> GenerationCandidate:
        """Return one durable candidate without exposing persistence details."""

        candidate = self._read_only(
            lambda session: CreativeCandidateRepository(session).get_candidate(candidate_id)
        )
        if candidate is None:
            raise WorkflowNotFoundError(f"candidate not found: {candidate_id}")
        return candidate

    def list_candidates(self, job_id: str) -> tuple[GenerationCandidate, ...]:
        """Return candidates for an existing job in repository-defined order."""

        def read(session: Session) -> tuple[GenerationCandidate, ...] | None:
            if CreativeGenerationRepository(session).get_job(job_id) is None:
                return None
            repository = CreativeCandidateRepository(session)
            return tuple(repository.list_candidates(job_id=job_id))

        candidates = self._read_only(read)
        if candidates is None:
            raise WorkflowNotFoundError(f"durable job not found: {job_id}")
        return candidates

    def run_next(
        self,
        job_id: str,
        *,
        session_override: SecretStr | str | None = None,
        cancel: Event | None = None,
    ) -> DurableExecutionResult:
        claimed = self._claim_next(job_id)
        if isinstance(claimed, DurableExecutionResult):
            return claimed

        request = claimed.request
        try:

            def checkpoint_sink(checkpoint: PartialCheckpoint) -> None:
                self._checkpoint_partial(
                    job_id,
                    request.step_id,
                    checkpoint,
                )

            local_measurement = self._measure_saved_unit(request)
            if local_measurement is not None:
                if cancel is not None and cancel.is_set():
                    raise _ControlSignal(_ControlKind.CANCEL)
                result = local_measurement
            elif cancel is None:
                result = self._step_runner.run(
                    request,
                    checkpoint_sink,
                    session_override=session_override,
                )
            else:
                result = self._step_runner.run(
                    request,
                    checkpoint_sink,
                    session_override=session_override,
                    cancel=cancel,
                )
        except _ControlSignal as signal:
            return self._finish_controlled_step(job_id, request.step_id, signal.kind)
        except StepExecutionError as error:
            return self._fail_step(job_id, request.step_id, error)
        except Exception:
            return self._fail_step(
                job_id,
                request.step_id,
                StepExecutionError(
                    code="runner_exception",
                    safe_message="The step runner failed before producing a safe result.",
                ),
            )
        finally:
            session_override = None
        if request.step_kind is StepKind.FINAL_ASSEMBLY or request.step_kind in UNIT_PROSE_KINDS:
            assured = self._process_final_length_assurance(
                job_id,
                request.step_id,
                request,
                result,
            )
            if isinstance(assured, DurableExecutionResult):
                return assured
            result = assured
        if result.continuation_required:
            return self._interrupt_for_continuation(
                job_id,
                request.step_id,
                result,
            )
        return self._complete_step(job_id, request.step_id, result)

    def _measure_saved_unit(self, request: DurableStepRunRequest) -> DurableStepRunResult | None:
        """Recheck a saved revision, retaining its text provenance without another model call."""
        if request.step_kind is not StepKind.LENGTH_ASSURANCE:
            return None
        state = request.input_snapshot.get(LENGTH_ASSURANCE_STATE_KEY)
        if isinstance(state, dict) and state.get("next_action"):
            return None
        job = self.get_job(request.job_id)
        step = next(item for item in self.get_steps(job.id) if item.id == request.step_id)
        if self._typed_length_spec(job, step=step) is None:
            return None
        dependencies = request.input_snapshot.get("dependencies")
        if not isinstance(dependencies, list):
            raise DurableWorkflowError("unit measurement dependencies are missing")
        revision_ids = [
            item.get("step_id") for item in dependencies
            if isinstance(item, dict) and item.get("step_kind") == StepKind.TARGETED_REVISION.value
        ]
        revisions = [item for item in self.get_steps(job.id) if item.id in revision_ids]
        if len(revisions) != 1 or revisions[0].status is not StepStatus.COMPLETED:
            raise DurableWorkflowError("unit measurement needs one completed revision")
        revision = revisions[0]
        if not revision.output_text.strip():
            raise DurableWorkflowError("unit measurement revision has no prose")
        return DurableStepRunResult(
            output_text=revision.output_text,
            output_json={"deterministic_unit_measurement": {
                "source_step_id": revision.id,
                "source_output_sha256": revision.output_sha256,
                "provider_calls": 0,
                "estimated_cost_microunits": 0,
            }},
            provider=revision.provider or request.requested_provider,
            model=revision.model or request.requested_model,
            fallback_used=revision.fallback_used,
            finish_reason="stop",
        )

    def run_until_blocked(
        self,
        job_id: str,
        *,
        max_steps: int = 10_000,
        session_override: SecretStr | str | None = None,
        cancel: Event | None = None,
    ) -> DurableExecutionResult:
        if max_steps < 1:
            raise ValueError("max_steps must be positive")
        result: DurableExecutionResult | None = None
        for _ in range(max_steps):
            result = self.run_next(
                job_id,
                session_override=session_override,
                cancel=cancel,
            )
            if result.disposition not in {
                ExecutionDisposition.STEP_COMPLETED,
                ExecutionDisposition.CONTINUATION_REQUIRED,
            }:
                return result
        raise DurableWorkflowError("workflow exceeded the bounded step budget")

    def request_pause(self, job_id: str) -> GenerationJob:
        now = self._clock()
        with self._transaction() as session:
            repository = CreativeGenerationRepository(session)
            job = self._required_job(repository, job_id)
            if job.status is JobStatus.PAUSED:
                return job
            if job.status not in ACTIVE_JOB_STATUSES:
                raise WorkflowControlError("only an active job can be paused")
            paused = _job_copy(
                job,
                status=JobStatus.PAUSED,
                resume_phase=job.status,
                pause_requested=True,
                cancel_requested=False,
                resume_requested=False,
                state_version=job.state_version + 1,
                updated_at=now,
            )
            self._require_job_cas(repository, paused, job.state_version)
            return paused

    def request_cancel(self, job_id: str) -> GenerationJob:
        now = self._clock()
        with self._transaction() as session:
            repository = CreativeGenerationRepository(session)
            job = self._required_job(repository, job_id)
            if job.status is JobStatus.CANCELLED:
                return job
            if job.status is JobStatus.COMPLETED:
                raise WorkflowControlError("a completed job cannot be cancelled")
            cancelled = _job_copy(
                job,
                status=JobStatus.CANCELLED,
                resume_phase=None,
                pause_requested=False,
                cancel_requested=True,
                resume_requested=False,
                error_code="",
                safe_error_message="",
                completed_at=now,
                state_version=job.state_version + 1,
                updated_at=now,
            )
            self._require_job_cas(repository, cancelled, job.state_version)
            return cancelled

    def resume(self, job_id: str) -> GenerationJob:
        now = self._clock()
        with self._transaction() as session:
            repository = CreativeGenerationRepository(session)
            job = self._required_job(repository, job_id)
            if job.status not in {
                JobStatus.PAUSED,
                JobStatus.FAILED,
                JobStatus.NEEDS_AUTHOR_INPUT,
            }:
                raise WorkflowControlError("job is not resumable")
            if job.resume_phase not in ACTIVE_JOB_STATUSES:
                raise WorkflowControlError("job has no durable resume phase")
            pending_step = next(
                (
                    step for step in repository.list_steps(job.id)
                    if step.status is not StepStatus.COMPLETED
                ),
                None,
            )
            if pending_step is not None:
                self._require_resolved_unit_partial(job, pending_step)
            resumed = _job_copy(
                job,
                status=job.resume_phase,
                resume_phase=None,
                pause_requested=False,
                cancel_requested=False,
                resume_requested=False,
                error_code="",
                safe_error_message="",
                completed_at=None,
                state_version=job.state_version + 1,
                updated_at=now,
            )
            self._require_job_cas(repository, resumed, job.state_version)
            return resumed

    def retry(self, job_id: str) -> GenerationJob:
        job = self.get_job(job_id)
        if job.status is not JobStatus.FAILED:
            raise WorkflowControlError("only a failed job can be retried")
        return self.resume(job_id)

    def recover_startup(self) -> tuple[str, ...]:
        """Convert stale RUNNING steps to durable INTERRUPTED/CANCELLED rows."""

        recovered: list[str] = []
        jobs = self._read_only(lambda session: CreativeGenerationRepository(session).list_jobs())
        for job_snapshot in jobs:
            with self._transaction() as session:
                repository = CreativeGenerationRepository(session)
                job = repository.get_job(job_snapshot.id)
                if job is None:
                    continue
                running_steps = repository.list_steps(
                    job.id,
                    statuses=(StepStatus.RUNNING,),
                )
                if not running_steps:
                    continue
                now = self._clock()
                for step in running_steps:
                    attempt = step.attempt_history[-1]
                    if job.status is JobStatus.CANCELLED:
                        terminal_attempt = _attempt_copy(
                            attempt,
                            status=AttemptStatus.CANCELLED,
                            finish_reason="cancelled",
                            completed_at=now,
                        )
                        recovered_step = _step_copy(
                            step,
                            status=StepStatus.CANCELLED,
                            attempt_history=(
                                *step.attempt_history[:-1],
                                terminal_attempt,
                            ),
                            finish_reason="cancelled",
                            completed_at=now,
                            state_version=step.state_version + 1,
                            updated_at=now,
                        )
                    else:
                        terminal_attempt = _attempt_copy(
                            attempt,
                            status=AttemptStatus.INTERRUPTED,
                            finish_reason="interrupted",
                            completed_at=now,
                        )
                        recovered_step = _step_copy(
                            step,
                            status=StepStatus.INTERRUPTED,
                            attempt_history=(
                                *step.attempt_history[:-1],
                                terminal_attempt,
                            ),
                            finish_reason="interrupted",
                            interrupted_at=now,
                            completed_at=None,
                            state_version=step.state_version + 1,
                            updated_at=now,
                        )
                    self._require_step_cas(repository, recovered_step, step.state_version)
                    recovered.append(step.id)

                if job.status in ACTIVE_JOB_STATUSES or job.status in {
                    JobStatus.PAUSED,
                    JobStatus.NEEDS_AUTHOR_INPUT,
                }:
                    refreshed = _job_copy(
                        job,
                        recovery_count=job.recovery_count + 1,
                        state_version=job.state_version + 1,
                        updated_at=now,
                    )
                    self._require_job_cas(repository, refreshed, job.state_version)
        return tuple(recovered)

    def apply_candidate(self, command: CandidateApplyCommand) -> CandidateAction:
        if self._project_adapter is None:
            raise WorkflowControlError("no project artifact adapter is configured")

        with self._transaction() as session:
            repository = CreativeCandidateRepository(session)
            candidate = repository.get_candidate(command.candidate_id)
            if candidate is None:
                raise WorkflowNotFoundError(f"candidate not found: {command.candidate_id}")
            generation = CreativeGenerationRepository(session)
            job = generation.get_job(candidate.job_id)
            step = generation.get_step(candidate.producing_step_id)
            if (
                job is None
                or step is None
                or step.job_id != job.id
                or candidate.provenance.job_id != job.id
                or candidate.provenance.step_id != step.id
            ):
                raise DurableWorkflowError("candidate job/step provenance is incomplete")
            existing = repository.get_action_by_idempotency_key(command.idempotency_key)
            if existing is not None and (
                existing.candidate_id != candidate.id
                or existing.action_kind is not command.action_kind
                or existing.selection != command.selection
                or existing.before_snapshot_sha256 != sha256_json(command.before_snapshot)
                or existing.base_version_ref != command.base_version_ref
                or existing.undo_of_action_id != command.undo_of_action_id
            ):
                raise WorkflowIdempotencyConflict(
                    "apply idempotency key was reused with different inputs"
                )
            if existing is not None and existing.status is not CandidateActionStatus.PENDING:
                return existing
            if existing is None:
                action_id = f"apply:{sha256_text(command.idempotency_key)[:32]}"
                pending = CandidateAction(
                    id=action_id,
                    candidate_id=candidate.id,
                    action_kind=command.action_kind,
                    status=CandidateActionStatus.PENDING,
                    idempotency_key=command.idempotency_key,
                    target_type=candidate.target_type,
                    target_id=candidate.target_id,
                    selection=command.selection,
                    before_snapshot=command.before_snapshot,
                    before_snapshot_sha256=sha256_json(command.before_snapshot),
                    base_version_ref=command.base_version_ref,
                    undo_of_action_id=command.undo_of_action_id,
                    created_at=command.created_at,
                )
                repository.add_action(pending)
                action = pending
            else:
                action = existing

        apply_request = ProjectApplyRequest(
            candidate_id=candidate.id,
            action_kind=action.action_kind,
            idempotency_key=command.idempotency_key,
            target_type=candidate.target_type,
            target_id=candidate.target_id,
            selection=action.selection,
            candidate_text=candidate.content_text,
            candidate_json=candidate.content_json,
            before_snapshot=action.before_snapshot,
            base_version_ref=action.base_version_ref,
            undo_of_action_id=action.undo_of_action_id,
            durable_candidate_snapshot=candidate.model_dump(mode="json"),
            durable_job_snapshot=job.model_dump(mode="json"),
            durable_step_snapshot=step.model_dump(mode="json"),
        )
        try:
            applied = self._project_adapter.apply_candidate(apply_request)
        except Exception:
            failed = CandidateAction.model_validate(
                {
                    **action.model_dump(mode="json"),
                    "status": CandidateActionStatus.FAILED,
                    "error_code": "adapter_failed",
                    "safe_error_message": (
                        "The project adapter did not return a confirmed apply receipt."
                    ),
                    "completed_at": self._clock(),
                }
            )
            with self._transaction() as session:
                repository = CreativeCandidateRepository(session)
                if repository.complete_action_once(failed):
                    return failed
                existing = repository.get_action(failed.id)
                if existing is None:
                    raise ConcurrentWorkflowUpdate("candidate action disappeared") from None
                return existing

        completed = CandidateAction.model_validate(
            {
                **action.model_dump(mode="json"),
                "status": CandidateActionStatus.COMPLETED,
                "after_snapshot": applied.after_snapshot,
                "after_snapshot_sha256": sha256_json(applied.after_snapshot),
                "result_version_ref": applied.result_version_ref,
                "completed_at": self._clock(),
            }
        )
        with self._transaction() as session:
            repository = CreativeCandidateRepository(session)
            if not repository.complete_action_once(completed):
                existing = repository.get_action(completed.id)
                if existing is None:
                    raise ConcurrentWorkflowUpdate("candidate action disappeared")
                return existing
            current_candidate = repository.get_candidate(candidate.id)
            if (
                current_candidate is not None
                and current_candidate.status is CandidateStatus.PROPOSED
                and command.action_kind is not CandidateActionKind.UNDO
            ):
                accepted = GenerationCandidate.model_validate(
                    {
                        **current_candidate.model_dump(mode="json"),
                        "status": CandidateStatus.APPLIED,
                        "state_version": current_candidate.state_version + 1,
                        "decided_at": completed.completed_at,
                    }
                )
                if not repository.update_candidate_cas(
                    accepted,
                    expected_state_version=current_candidate.state_version,
                ):
                    raise ConcurrentWorkflowUpdate("candidate status changed concurrently")
        return completed

    def _claim_next(
        self,
        job_id: str,
    ) -> _ClaimedStep | DurableExecutionResult:
        now = self._clock()
        with self._transaction() as session:
            repository = CreativeGenerationRepository(session)
            job = self._required_job(repository, job_id)
            saved_candidates = CreativeCandidateRepository(session).list_candidates(job_id=job.id)
            latest_candidate_id = saved_candidates[0].id if saved_candidates else None
            if job.status is JobStatus.COMPLETED:
                return DurableExecutionResult(
                    job_id=job.id,
                    disposition=ExecutionDisposition.ALREADY_COMPLETE,
                    candidate_id=latest_candidate_id,
                )
            if job.status is JobStatus.CANCELLED:
                return DurableExecutionResult(
                    job_id=job.id,
                    disposition=ExecutionDisposition.CANCELLED,
                    candidate_id=latest_candidate_id,
                )
            if job.status is JobStatus.PAUSED:
                return DurableExecutionResult(
                    job_id=job.id,
                    disposition=ExecutionDisposition.PAUSED,
                )
            if job.status is JobStatus.FAILED:
                return DurableExecutionResult(
                    job_id=job.id,
                    disposition=ExecutionDisposition.FAILED,
                )
            if job.status is JobStatus.NEEDS_AUTHOR_INPUT:
                return DurableExecutionResult(
                    job_id=job.id,
                    disposition=ExecutionDisposition.NEEDS_AUTHOR_INPUT,
                    candidate_id=latest_candidate_id,
                )

            steps = repository.list_steps(job.id)
            step = next(
                (item for item in steps if item.status is not StepStatus.COMPLETED),
                None,
            )
            if step is None:
                quality = next(
                    (
                        item
                        for item in steps
                        if item.step_kind is StepKind.FINAL_QUALITY_CHECK
                        and item.status is StepStatus.COMPLETED
                    ),
                    None,
                )
                if quality is None:
                    raise DurableWorkflowError("final quality output is missing")
                candidate, disposition = self._finalize_job(
                    session=session,
                    job=job,
                    steps=steps,
                    quality_report=quality.output_json or {"text": quality.output_text},
                    now=now,
                )
                return DurableExecutionResult(
                    job_id=job.id,
                    disposition=disposition,
                    candidate_id=candidate.id,
                )
            if step.status is StepStatus.CANCELLED:
                raise DurableWorkflowError("cancelled step belongs to a non-cancelled job")
            if step.status is StepStatus.RUNNING:
                raise DurableWorkflowError(
                    "step is already running; recover stale work before claiming"
                )
            self._require_resolved_unit_partial(job, step)
            if step.attempts >= step.max_attempts:
                exhausted = StepExecutionError(
                    code="attempts_exhausted",
                    safe_message=(
                        "The step exhausted its configured continuation attempts; "
                        "the preserved partial remains available for author review."
                        if step.partial
                        else "The step exhausted its configured attempts."
                    ),
                    needs_author_input=step.partial,
                )
                return self._fail_unclaimed_step(
                    repository,
                    job,
                    step,
                    exhausted,
                    now,
                )

            resolved_input = dict(
                step.input_snapshot if step.attempts > 0 else self._resolve_input(job, step, steps)
            )
            if step.step_kind is StepKind.FINAL_ASSEMBLY or step.step_kind in UNIT_PROSE_KINDS:
                # update_step_cas intentionally does not rewrite the immutable
                # input snapshot. Persist retry state in the existing output
                # JSON column while interrupted, then copy the verified state
                # into the next claimed request (claim_step_cas does persist
                # the newly resolved input). This avoids a schema migration
                # and makes process restarts resume from the durable ledger.
                carrier = step.output_json or {}
                raw_assurance_state = carrier.get(
                    LENGTH_ASSURANCE_STATE_KEY,
                    resolved_input.get(LENGTH_ASSURANCE_STATE_KEY, {}),
                )
                typed_spec = self._effective_length_spec(job, step=step)
                if typed_spec is not None:
                    if step.step_kind in UNIT_PROSE_KINDS:
                        decisions = UnitAuthorDecisionRepository(session).list_for_job(job.id)
                        if decisions:
                            resolved_input[AUTHOR_POLICY_KEY] = projection(job, step, decisions)
                    resolved_input[LENGTH_ASSURANCE_STATE_KEY] = self._validated_assurance_state(
                        raw_assurance_state,
                        spec=typed_spec,
                        step_id=step.id if step.step_kind in UNIT_PROSE_KINDS else None,
                    )
            attempt_number = step.attempts + 1
            attempt = GenerationStepAttempt(
                attempt_number=attempt_number,
                status=AttemptStatus.RUNNING,
                requested_provider=step.requested_provider,
                requested_model=step.requested_model,
                provider=step.requested_provider,
                model=step.requested_model,
                started_at=now,
            )
            claimed_step = _step_copy(
                step,
                status=StepStatus.RUNNING,
                provider=step.requested_provider,
                model=step.requested_model,
                fallback_used=False,
                input_snapshot=resolved_input,
                input_snapshot_sha256=sha256_json(resolved_input),
                attempts=attempt_number,
                attempt_history=(*step.attempt_history, attempt),
                error_code="",
                safe_error_message="",
                started_at=step.started_at or now,
                interrupted_at=None,
                completed_at=None,
                state_version=step.state_version + 1,
                updated_at=now,
            )
            if not repository.claim_step_cas(
                claimed_step,
                expected_state_version=step.state_version,
            ):
                raise ConcurrentWorkflowUpdate("step was claimed concurrently")

            phase = _phase_for_step(step.step_kind)
            active_job = _job_copy(
                job,
                status=phase,
                resume_phase=None,
                started_at=job.started_at or now,
                current_step_index=step.ordinal,
                state_version=job.state_version + 1,
                updated_at=now,
            )
            self._require_job_cas(repository, active_job, job.state_version)
            request = DurableStepRunRequest(
                job_id=job.id,
                step_id=step.id,
                ordinal=step.ordinal,
                step_kind=step.step_kind,
                template_id=step.template_id,
                template_version=step.template_version,
                requested_provider=step.requested_provider or "offline",
                requested_model=step.requested_model or "deterministic",
                fallback_policy=str(job.route.get("fallback_policy", "fail_closed")),
                fallback_provider=(
                    str(job.route["fallback_provider"])
                    if job.route.get("fallback_provider") is not None
                    else None
                ),
                fallback_model=(
                    str(job.route["fallback_model"])
                    if job.route.get("fallback_model") is not None
                    else None
                ),
                attempt_number=attempt_number,
                input_snapshot=resolved_input,
                prior_partial_text=step.partial_text,
                stable_boundary_offset=step.stable_boundary_offset,
            )
            return _ClaimedStep(request=request)

    def _checkpoint_partial(
        self,
        job_id: str,
        step_id: str,
        checkpoint: PartialCheckpoint,
    ) -> None:
        now = self._clock()
        with self._transaction() as session:
            repository = CreativeGenerationRepository(session)
            job = self._required_job(repository, job_id)
            step = repository.get_step(step_id)
            if step is None or step.job_id != job.id:
                raise WorkflowNotFoundError(f"step not found: {step_id}")
            if step.status is not StepStatus.RUNNING:
                raise ConcurrentWorkflowUpdate("checkpoint target is no longer running")
            checkpointed = _step_copy(
                step,
                partial=True,
                partial_text=checkpoint.text,
                partial_sha256=sha256_text(checkpoint.text),
                stable_boundary_offset=checkpoint.stable_boundary_offset,
                finish_reason=checkpoint.finish_reason,
                checkpoint_index=step.checkpoint_index + 1,
                checkpoint_at=now,
                state_version=step.state_version + 1,
                updated_at=now,
            )
            self._require_step_cas(repository, checkpointed, step.state_version)

            checkpointed_job = _job_copy(
                job,
                last_checkpoint_step_id=step.id,
                checkpoint_sequence=job.checkpoint_sequence + 1,
                last_checkpoint_at=now,
                state_version=job.state_version + 1,
                updated_at=now,
            )
            self._require_job_cas(repository, checkpointed_job, job.state_version)
            control_status = job.status

        if control_status is JobStatus.CANCELLED:
            raise _ControlSignal(_ControlKind.CANCEL)
        if control_status is JobStatus.PAUSED:
            raise _ControlSignal(_ControlKind.PAUSE)

    def _interrupt_for_continuation(
        self,
        job_id: str,
        step_id: str,
        result: DurableStepRunResult,
    ) -> DurableExecutionResult:
        """Persist an incomplete provider result without promoting it to output."""

        now = self._clock()
        with self._transaction() as session:
            repository = CreativeGenerationRepository(session)
            job = self._required_job(repository, job_id)
            step = repository.get_step(step_id)
            if step is None or step.status is not StepStatus.RUNNING:
                raise ConcurrentWorkflowUpdate("interrupted step is no longer running")
            if job.status is JobStatus.CANCELLED:
                return self._cancel_running_step(repository, job, step, now)

            fallback_used = result.fallback_used or (
                result.provider != step.requested_provider or result.model != step.requested_model
            )
            interrupted_attempt = _attempt_copy(
                step.attempt_history[-1],
                status=AttemptStatus.INTERRUPTED,
                provider=result.provider,
                model=result.model,
                fallback_used=fallback_used,
                finish_reason=result.finish_reason,
                prompt_tokens=result.prompt_tokens,
                completion_tokens=result.completion_tokens,
                latency_ms=result.latency_ms,
                completed_at=now,
            )
            checkpoint_changed = (
                step.partial_text != result.output_text
                or step.stable_boundary_offset != result.stable_boundary_offset
                or step.finish_reason != result.finish_reason
            )
            suspended_status = (
                StepStatus.PAUSED if job.status is JobStatus.PAUSED else StepStatus.INTERRUPTED
            )
            interrupted_step = _step_copy(
                step,
                status=suspended_status,
                provider=result.provider,
                model=result.model,
                fallback_used=fallback_used,
                partial=True,
                partial_text=result.output_text,
                partial_sha256=sha256_text(result.output_text),
                stable_boundary_offset=result.stable_boundary_offset,
                finish_reason=result.finish_reason,
                checkpoint_index=(step.checkpoint_index + (1 if checkpoint_changed else 0)),
                checkpoint_at=now if checkpoint_changed else step.checkpoint_at,
                prompt_tokens=result.prompt_tokens,
                completion_tokens=result.completion_tokens,
                latency_ms=result.latency_ms,
                attempt_history=(*step.attempt_history[:-1], interrupted_attempt),
                interrupted_at=(None if suspended_status is StepStatus.PAUSED else now),
                completed_at=None,
                state_version=step.state_version + 1,
                updated_at=now,
            )
            self._require_step_cas(repository, interrupted_step, step.state_version)

            if checkpoint_changed:
                checkpointed_job = _job_copy(
                    job,
                    last_checkpoint_step_id=step.id,
                    checkpoint_sequence=job.checkpoint_sequence + 1,
                    last_checkpoint_at=now,
                    state_version=job.state_version + 1,
                    updated_at=now,
                )
                self._require_job_cas(repository, checkpointed_job, job.state_version)

            return DurableExecutionResult(
                job_id=job.id,
                step_id=step.id,
                disposition=(
                    ExecutionDisposition.PAUSED
                    if suspended_status is StepStatus.PAUSED
                    else ExecutionDisposition.CONTINUATION_REQUIRED
                ),
            )

    def _process_final_length_assurance(
        self,
        job_id: str,
        step_id: str,
        request: DurableStepRunRequest,
        result: DurableStepRunResult,
    ) -> DurableStepRunResult | DurableExecutionResult:
        """Apply the scoped author contract after a complete unit or assembly stops.

        Provider completion is only an input. This application boundary owns
        mechanical measurement, bounded continuation, repetition rejection,
        partial preservation, and separate over-maximum candidates.
        """

        snapshot = self._read_only(
            lambda session: (
                CreativeGenerationRepository(session).get_job(job_id),
                CreativeGenerationRepository(session).get_step(step_id),
            )
        )
        job, step = snapshot
        if job is None or step is None:
            raise WorkflowNotFoundError("durable assurance target disappeared")
        spec = self._effective_length_spec(job, step=step)
        if spec is None:
            return result
        if step.status is not StepStatus.RUNNING:
            raise ConcurrentWorkflowUpdate("assurance target is no longer running")
        # Whole-book facts need not occur literally in every chapter. The
        # immutable full context still travels to the provider; the literal
        # completeness gate remains at final assembly only.
        protected_facts = (
            self._protected_facts(job) if step.step_kind is StepKind.FINAL_ASSEMBLY else ()
        )

        raw_state = step.input_snapshot.get(LENGTH_ASSURANCE_STATE_KEY, {})
        state = self._validated_assurance_state(
            raw_state, spec=spec,
            step_id=step.id if step.step_kind in UNIT_PROSE_KINDS else None,
        )
        prior_action = state.get("next_action", "")
        if not isinstance(prior_action, str):
            raise DurableWorkflowError("durable assurance action is invalid")

        continuations_used = self._state_counter(state, "continuations_used")
        repetition_retries_used = self._state_counter(
            state,
            "repetition_retries_used",
        )
        prior_cost = self._state_counter(
            state,
            "estimated_cost_microunits",
        )
        effective_text = result.output_text
        repetition_report: RepetitionGuardReport | None = None
        repetition_rejected = False
        unusable_continuation = False

        if prior_action == AssuranceAction.CONTINUE.value and request.prior_partial_text:
            stable_prefix = request.prior_partial_text[: request.stable_boundary_offset]
            continuation_text = result.output_text
            stripped_stable_echo = False
            if stable_prefix and continuation_text.startswith(stable_prefix):
                continuation_text = continuation_text[len(stable_prefix) :]
                stripped_stable_echo = True
            continuation_plan = state.get("continuation_plan")
            prior_was_incomplete = (
                isinstance(continuation_plan, dict)
                and continuation_plan.get("provider_incomplete") is True
            )
            merge_base = (
                stable_prefix
                if prior_was_incomplete and stable_prefix
                else request.prior_partial_text
            )
            if not continuation_text.strip():
                effective_text = merge_base
                if stripped_stable_echo and result.output_text.strip():
                    # A provider that returns only the supplied stable prefix
                    # produced a duplicate, not an empty transport response.
                    # Count it against the bounded repetition retry budget so
                    # one useful retry can still be issued.
                    repetition_report = self._repetition_guard.merge(
                        existing_text=merge_base,
                        continuation_text=result.output_text,
                    ).report
                    repetition_rejected = True
                else:
                    unusable_continuation = True
            else:
                merge = self._repetition_guard.merge(
                    existing_text=merge_base,
                    continuation_text=continuation_text,
                )
                repetition_report = merge.report
                effective_text = merge.combined_text
                if merge.accepted:
                    continuations_used += 1
                else:
                    repetition_rejected = True
        elif prior_action == AssuranceAction.CREATE_CONDENSED_CANDIDATE.value:
            # Condensation is a separate paid/provider call. It consumes an
            # attempt/cost slot, not the author's logical continuation budget.
            # The original is already immutable candidate material and is
            # never merged over.
            pass

        estimated_cost = prior_cost + result.estimated_cost_microunits
        limits = self._effective_assurance_limits(job, step)
        next_cost = job.length_spec.get(
            "next_attempt_estimated_cost_microunits",
            result.estimated_cost_microunits,
        )
        if not isinstance(next_cost, int) or next_cost < 0:
            raise DurableWorkflowError("durable next-attempt cost estimate is invalid")
        progress = AssuranceProgress(
            provider_attempts=step.attempts,
            continuations_used=continuations_used,
            repetition_retries_used=repetition_retries_used,
            estimated_cost_microunits=estimated_cost,
            next_attempt_estimated_cost_microunits=next_cost,
        )
        termination = self._normalized_provider_termination(result)
        if unusable_continuation:
            termination = ProviderTermination.ERROR
        if repetition_rejected:
            decision = self._length_assurance.repetition_rejected(
                preserved_text=effective_text,
                spec=spec,
                provider_termination=termination,
                progress=progress,
                limits=limits,
                provider_finish_reason="repetition_rejected",
            )
        else:
            decision = self._length_assurance.evaluate(
                text=effective_text,
                spec=spec,
                provider_termination=termination,
                progress=progress,
                limits=limits,
                provider_finish_reason=result.finish_reason,
            )
        if repetition_rejected and decision.action is AssuranceAction.CONTINUE:
            # Reserve a retry only when the assurance service actually grants
            # one. Incrementing before evaluation makes a limit of one reject
            # the very first bounded retry (an off-by-one failure).
            repetition_retries_used += 1

        ledger = state.get("attempt_ledger", [])
        if not isinstance(ledger, list):
            raise DurableWorkflowError("durable assurance ledger is invalid")
        entry: dict[str, object] = {
            "attempt_number": step.attempts,
            "step_attempt_idempotency_key": f"{step.id}:attempt:{step.attempts}",
            "requested_provider": step.requested_provider,
            "requested_model": step.requested_model,
            "provider": result.provider,
            "model": result.model,
            "fallback_used": result.fallback_used,
            "finish_reason": result.finish_reason,
            "provider_termination": termination.value,
            "prompt_tokens": result.prompt_tokens,
            "completion_tokens": result.completion_tokens,
            "latency_ms": result.latency_ms,
            "estimated_cost_microunits": result.estimated_cost_microunits,
            "runtime_provider_attempts": result.runtime_provider_attempts,
            "runtime_provider_attempt_ceiling": result.runtime_provider_attempt_ceiling,
            "provider_output_sha256": sha256_text(result.output_text),
            "accumulated_output_sha256": sha256_text(effective_text),
            "accepted_into_candidate": not repetition_rejected and not unusable_continuation,
            "decision": decision.model_dump(mode="json"),
        }
        if repetition_report is not None:
            entry["repetition_guard"] = repetition_report.model_dump(mode="json")
        updated_ledger = [*ledger, entry]
        next_state: dict[str, object] = {
            "contract_version": "cwe-2.1-durable-length-assurance-v1",
            "generation_length_spec_sha256": spec.sha256,
            "continuations_used": continuations_used,
            "repetition_retries_used": repetition_retries_used,
            "estimated_cost_microunits": estimated_cost,
            "limits": limits.model_dump(mode="json"),
            "progress": progress.model_dump(mode="json"),
            "attempt_ledger": updated_ledger,
            "attempt_ledger_sha256": sha256_json(updated_ledger),
            "final_decision": decision.model_dump(mode="json"),
        }
        if step.step_kind in UNIT_PROSE_KINDS:
            planned = step.input_snapshot.get("planned")
            unit = planned.get("unit") if isinstance(planned, dict) else None
            assert isinstance(unit, dict)
            next_state.update({
                "scope": "unit",
                "step_id": step.id,
                "unit_id": unit["id"],
                "unit_title": unit["title"],
                "generation_length_spec": spec.model_dump(mode="json"),
            })
        preserved_id = state.get("preserved_original_candidate_id")
        if isinstance(preserved_id, str) and preserved_id:
            next_state["preserved_original_candidate_id"] = preserved_id

        if termination is ProviderTermination.CANCELLED:
            next_state.update(
                {
                    "next_action": AssuranceAction.SAVE_PARTIAL.value,
                    "application_status": decision.status.value,
                    "application_reason": decision.reason.value,
                }
            )
            return self._cancel_assured_result(
                job_id,
                step_id,
                result=result.model_copy(update={"output_text": effective_text}),
                assurance_state=self._hash_assurance_state(next_state),
            )

        if decision.action is AssuranceAction.COMPLETE:
            protected_report = self._protected_facts_guard.inspect(
                candidate_text=effective_text,
                protected_facts=protected_facts,
            )
            next_state["protected_facts_report"] = protected_report.model_dump(mode="json")
            if not protected_report.literal_gate_passed:
                next_state.update(
                    {
                        "next_action": AssuranceAction.SAVE_PARTIAL.value,
                        "application_status": "needs_author_input",
                        "application_reason": "protected_facts_unverified",
                        "domain_completion_blocked_by": "protected_facts_literal_gate",
                    }
                )
                return self._suspend_assured_partial(
                    job_id,
                    step_id,
                    result=result.model_copy(update={"output_text": effective_text}),
                    assurance_state=self._hash_assurance_state(next_state),
                    candidate_role="protected_facts_unverified",
                )
            report = self._assurance_report(next_state)
            output_json = dict(result.output_json or {})
            output_json[_ASSURANCE_REPORT_KEY] = report
            return result.model_copy(
                update={
                    "output_text": effective_text,
                    "output_json": output_json,
                    "provider_termination": termination,
                    "continuation_required": False,
                    "stable_boundary_offset": 0,
                }
            )

        preserve_original = decision.original_candidate_preserved
        if decision.action is AssuranceAction.CONTINUE:
            plan = self._continuation_planner.plan(
                existing_text=effective_text,
                spec=spec,
                continuations_used=continuations_used,
                rolling_summary=self._rolling_summary(job),
                protected_facts=protected_facts,
                provider_incomplete=termination is not ProviderTermination.NORMAL_STOP,
            )
            next_state.update(
                {
                    "next_action": AssuranceAction.CONTINUE.value,
                    "continuation_plan": plan.model_dump(mode="json"),
                }
            )
            stable_boundary_offset = plan.stable_boundary_offset
        elif decision.action is AssuranceAction.CREATE_CONDENSED_CANDIDATE:
            next_state.update(
                {
                    "next_action": AssuranceAction.CREATE_CONDENSED_CANDIDATE.value,
                    "continuation_plan": None,
                }
            )
            stable_boundary_offset = 0
        elif decision.action is AssuranceAction.SAVE_PARTIAL:
            next_state.update(
                {
                    "next_action": AssuranceAction.SAVE_PARTIAL.value,
                    "application_status": decision.status.value,
                    "application_reason": decision.reason.value,
                }
            )
            return self._suspend_assured_partial(
                job_id,
                step_id,
                result=result.model_copy(update={"output_text": effective_text}),
                assurance_state=self._hash_assurance_state(next_state),
                candidate_role=(
                    "over_max_original" if preserve_original else "partial_needs_action"
                ),
            )
        elif decision.action is AssuranceAction.CANCEL:
            return self._cancel_assured_result(
                job_id,
                step_id,
                result=result.model_copy(update={"output_text": effective_text}),
                assurance_state=self._hash_assurance_state(next_state),
            )
        else:
            return self._fail_step(
                job_id,
                step_id,
                StepExecutionError(
                    code="length_assurance_failed",
                    safe_message="Length assurance could not preserve usable prose.",
                ),
            )

        return self._interrupt_assured_step(
            job_id,
            step_id,
            result=result.model_copy(
                update={
                    "output_text": effective_text,
                    "provider_termination": termination,
                    "continuation_required": True,
                    "stable_boundary_offset": stable_boundary_offset,
                }
            ),
            assurance_state=self._hash_assurance_state(next_state),
            preserve_original=preserve_original,
        )

    def _interrupt_assured_step(
        self,
        job_id: str,
        step_id: str,
        *,
        result: DurableStepRunResult,
        assurance_state: dict[str, object],
        preserve_original: bool,
    ) -> DurableExecutionResult:
        now = self._clock()
        with self._transaction() as session:
            repository = CreativeGenerationRepository(session)
            job = self._required_job(repository, job_id)
            step = repository.get_step(step_id)
            if step is None or step.status is not StepStatus.RUNNING:
                raise ConcurrentWorkflowUpdate("assured step is no longer running")
            if job.status is JobStatus.CANCELLED:
                return self._cancel_running_step(repository, job, step, now)

            fallback_used = result.fallback_used or (
                result.provider != step.requested_provider or result.model != step.requested_model
            )
            interrupted_attempt = _attempt_copy(
                step.attempt_history[-1],
                status=AttemptStatus.INTERRUPTED,
                provider=result.provider,
                model=result.model,
                fallback_used=fallback_used,
                finish_reason=result.finish_reason,
                prompt_tokens=result.prompt_tokens,
                completion_tokens=result.completion_tokens,
                latency_ms=result.latency_ms,
                completed_at=now,
            )
            candidate: GenerationCandidate | None = None
            if preserve_original:
                candidate = self._preserve_material_candidate(
                    session=session,
                    job=job,
                    step=step,
                    result=result,
                    content_text=result.output_text,
                    content_json=result.output_json,
                    candidate_role="over_max_original",
                    assurance_state=assurance_state,
                    now=now,
                )
                assurance_state = {
                    **assurance_state,
                    "preserved_original_candidate_id": candidate.id,
                }
                assurance_state = self._hash_assurance_state(assurance_state)

            updated_input = {
                **step.input_snapshot,
                LENGTH_ASSURANCE_STATE_KEY: assurance_state,
            }
            checkpoint_output = self._assurance_checkpoint_output(
                result.output_json,
                assurance_state,
            )
            interrupted_step = _step_copy(
                step,
                status=StepStatus.INTERRUPTED,
                provider=result.provider,
                model=result.model,
                fallback_used=fallback_used,
                input_snapshot=updated_input,
                input_snapshot_sha256=sha256_json(updated_input),
                output_text="",
                output_json=checkpoint_output,
                output_sha256=sha256_content(text="", structured=checkpoint_output),
                partial=True,
                partial_text=result.output_text,
                partial_sha256=sha256_text(result.output_text),
                stable_boundary_offset=result.stable_boundary_offset,
                finish_reason=result.finish_reason,
                checkpoint_index=step.checkpoint_index + 1,
                checkpoint_at=now,
                prompt_tokens=result.prompt_tokens,
                completion_tokens=result.completion_tokens,
                latency_ms=result.latency_ms,
                attempt_history=(*step.attempt_history[:-1], interrupted_attempt),
                interrupted_at=now,
                completed_at=None,
                state_version=step.state_version + 1,
                updated_at=now,
            )
            self._require_step_cas(repository, interrupted_step, step.state_version)
            checkpointed_job = _job_copy(
                job,
                last_checkpoint_step_id=step.id,
                checkpoint_sequence=job.checkpoint_sequence + 1,
                last_checkpoint_at=now,
                state_version=job.state_version + 1,
                updated_at=now,
            )
            self._require_job_cas(repository, checkpointed_job, job.state_version)
            return DurableExecutionResult(
                job_id=job.id,
                step_id=step.id,
                disposition=ExecutionDisposition.CONTINUATION_REQUIRED,
                candidate_id=candidate.id if candidate is not None else None,
            )

    def _suspend_assured_partial(
        self,
        job_id: str,
        step_id: str,
        *,
        result: DurableStepRunResult,
        assurance_state: dict[str, object],
        candidate_role: str,
    ) -> DurableExecutionResult:
        now = self._clock()
        with self._transaction() as session:
            repository = CreativeGenerationRepository(session)
            job = self._required_job(repository, job_id)
            step = repository.get_step(step_id)
            if step is None or step.status is not StepStatus.RUNNING:
                raise ConcurrentWorkflowUpdate("assured partial is no longer running")
            if job.status is JobStatus.CANCELLED:
                return self._cancel_running_step(repository, job, step, now)
            fallback_used = result.fallback_used or (
                result.provider != step.requested_provider or result.model != step.requested_model
            )
            interrupted_attempt = _attempt_copy(
                step.attempt_history[-1],
                status=AttemptStatus.INTERRUPTED,
                provider=result.provider,
                model=result.model,
                fallback_used=fallback_used,
                finish_reason=result.finish_reason,
                prompt_tokens=result.prompt_tokens,
                completion_tokens=result.completion_tokens,
                latency_ms=result.latency_ms,
                completed_at=now,
            )
            candidate = None
            if step.step_kind not in UNIT_PROSE_KINDS:
                candidate = self._preserve_material_candidate(
                    session=session,
                    job=job,
                    step=step,
                    result=result,
                    content_text=result.output_text,
                    content_json=result.output_json,
                    candidate_role=candidate_role,
                    assurance_state=assurance_state,
                    now=now,
                )
                assurance_state = {
                    **assurance_state,
                    "preserved_original_candidate_id": candidate.id,
                }
            assurance_state = self._hash_assurance_state(assurance_state)
            updated_input = {
                **step.input_snapshot,
                LENGTH_ASSURANCE_STATE_KEY: assurance_state,
            }
            checkpoint_output = self._assurance_checkpoint_output(
                result.output_json,
                assurance_state,
            )
            suspended_step = _step_copy(
                step,
                status=StepStatus.NEEDS_AUTHOR_INPUT,
                provider=result.provider,
                model=result.model,
                fallback_used=fallback_used,
                input_snapshot=updated_input,
                input_snapshot_sha256=sha256_json(updated_input),
                output_text="",
                output_json=checkpoint_output,
                output_sha256=sha256_content(text="", structured=checkpoint_output),
                partial=True,
                partial_text=result.output_text,
                partial_sha256=sha256_text(result.output_text),
                stable_boundary_offset=result.stable_boundary_offset,
                finish_reason="partial_needs_action",
                checkpoint_index=step.checkpoint_index + 1,
                checkpoint_at=now,
                prompt_tokens=result.prompt_tokens,
                completion_tokens=result.completion_tokens,
                latency_ms=result.latency_ms,
                attempt_history=(*step.attempt_history[:-1], interrupted_attempt),
                interrupted_at=None,
                completed_at=None,
                state_version=step.state_version + 1,
                updated_at=now,
            )
            self._require_step_cas(repository, suspended_step, step.state_version)
            waiting_job = _job_copy(
                job,
                status=JobStatus.NEEDS_AUTHOR_INPUT,
                resume_phase=_phase_for_step(step.step_kind),
                last_checkpoint_step_id=step.id,
                checkpoint_sequence=job.checkpoint_sequence + 1,
                last_checkpoint_at=now,
                completed_at=None,
                state_version=job.state_version + 1,
                updated_at=now,
            )
            self._require_job_cas(repository, waiting_job, job.state_version)
            return DurableExecutionResult(
                job_id=job.id,
                step_id=step.id,
                disposition=ExecutionDisposition.NEEDS_AUTHOR_INPUT,
                candidate_id=candidate.id if candidate is not None else None,
            )

    def _cancel_assured_result(
        self,
        job_id: str,
        step_id: str,
        *,
        result: DurableStepRunResult,
        assurance_state: dict[str, object],
    ) -> DurableExecutionResult:
        now = self._clock()
        with self._transaction() as session:
            repository = CreativeGenerationRepository(session)
            job = self._required_job(repository, job_id)
            step = repository.get_step(step_id)
            if step is None or step.status is not StepStatus.RUNNING:
                raise ConcurrentWorkflowUpdate("cancelled assurance step is no longer running")
            cancelled_attempt = _attempt_copy(
                step.attempt_history[-1],
                status=AttemptStatus.CANCELLED,
                provider=result.provider,
                model=result.model,
                fallback_used=result.fallback_used,
                finish_reason="cancelled",
                prompt_tokens=result.prompt_tokens,
                completion_tokens=result.completion_tokens,
                latency_ms=result.latency_ms,
                completed_at=now,
            )
            updated_input = {
                **step.input_snapshot,
                LENGTH_ASSURANCE_STATE_KEY: assurance_state,
            }
            checkpoint_output = self._assurance_checkpoint_output(
                result.output_json,
                assurance_state,
            )
            cancelled_step = _step_copy(
                step,
                status=StepStatus.CANCELLED,
                provider=result.provider,
                model=result.model,
                fallback_used=result.fallback_used,
                input_snapshot=updated_input,
                input_snapshot_sha256=sha256_json(updated_input),
                output_text="",
                output_json=checkpoint_output,
                output_sha256=sha256_content(text="", structured=checkpoint_output),
                partial=bool(result.output_text),
                partial_text=result.output_text,
                partial_sha256=(sha256_text(result.output_text) if result.output_text else ""),
                stable_boundary_offset=result.stable_boundary_offset,
                finish_reason="cancelled",
                checkpoint_index=step.checkpoint_index + 1,
                checkpoint_at=now,
                attempt_history=(*step.attempt_history[:-1], cancelled_attempt),
                completed_at=now,
                state_version=step.state_version + 1,
                updated_at=now,
            )
            self._require_step_cas(repository, cancelled_step, step.state_version)
            candidate = None
            if step.step_kind not in UNIT_PROSE_KINDS:
                candidate = self._preserve_material_candidate(
                    session=session,
                    job=job,
                    step=step,
                    result=result,
                    content_text=result.output_text,
                    content_json=result.output_json,
                    candidate_role="cancelled_with_partial",
                    assurance_state=assurance_state,
                    now=now,
                )
            cancelled_job = _job_copy(
                job,
                status=JobStatus.CANCELLED,
                resume_phase=None,
                pause_requested=False,
                cancel_requested=True,
                resume_requested=False,
                completed_at=now,
                last_checkpoint_step_id=step.id,
                checkpoint_sequence=job.checkpoint_sequence + 1,
                last_checkpoint_at=now,
                state_version=job.state_version + 1,
                updated_at=now,
            )
            self._require_job_cas(repository, cancelled_job, job.state_version)
            return DurableExecutionResult(
                job_id=job.id,
                step_id=step.id,
                disposition=ExecutionDisposition.CANCELLED,
                candidate_id=candidate.id if candidate is not None else None,
            )

    def _complete_step(
        self,
        job_id: str,
        step_id: str,
        result: DurableStepRunResult,
    ) -> DurableExecutionResult:
        now = self._clock()
        with self._transaction() as session:
            repository = CreativeGenerationRepository(session)
            job = self._required_job(repository, job_id)
            step = repository.get_step(step_id)
            if step is None or step.status is not StepStatus.RUNNING:
                raise ConcurrentWorkflowUpdate("completed step is no longer running")
            if job.status is JobStatus.CANCELLED:
                return self._cancel_running_step(repository, job, step, now)

            fallback_used = result.fallback_used or (
                result.provider != step.requested_provider or result.model != step.requested_model
            )
            completed_attempt = _attempt_copy(
                step.attempt_history[-1],
                status=AttemptStatus.COMPLETED,
                provider=result.provider,
                model=result.model,
                fallback_used=fallback_used,
                finish_reason=result.finish_reason,
                prompt_tokens=result.prompt_tokens,
                completion_tokens=result.completion_tokens,
                latency_ms=result.latency_ms,
                completed_at=now,
            )
            completed_step = _step_copy(
                step,
                status=StepStatus.COMPLETED,
                provider=result.provider,
                model=result.model,
                fallback_used=fallback_used,
                output_text=result.output_text,
                output_json=result.output_json,
                output_sha256=sha256_content(
                    text=result.output_text,
                    structured=result.output_json,
                ),
                partial=False,
                partial_text="",
                partial_sha256="",
                stable_boundary_offset=0,
                finish_reason=result.finish_reason,
                checkpoint_index=step.checkpoint_index + 1,
                prompt_tokens=result.prompt_tokens,
                completion_tokens=result.completion_tokens,
                latency_ms=result.latency_ms,
                attempt_history=(*step.attempt_history[:-1], completed_attempt),
                error_code="",
                safe_error_message="",
                checkpoint_at=now,
                interrupted_at=None,
                completed_at=now,
                state_version=step.state_version + 1,
                updated_at=now,
            )
            self._require_step_cas(repository, completed_step, step.state_version)

            all_steps = [
                completed_step if item.id == step.id else item
                for item in repository.list_steps(job.id)
            ]
            job_updates: dict[str, object] = {
                "current_step_index": step.ordinal + 1,
                "last_checkpoint_step_id": step.id,
                "checkpoint_sequence": job.checkpoint_sequence + 1,
                "last_checkpoint_at": now,
                "state_version": job.state_version + 1,
                "updated_at": now,
            }
            if job.status is JobStatus.PAUSED:
                checkpointed_job = _job_copy(job, **job_updates)
                self._require_job_cas(repository, checkpointed_job, job.state_version)
                return DurableExecutionResult(
                    job_id=job.id,
                    step_id=step.id,
                    disposition=ExecutionDisposition.PAUSED,
                )

            if all(item.status is StepStatus.COMPLETED for item in all_steps):
                quality_report = result.output_json or {"text": result.output_text}
                candidate, disposition = self._finalize_job(
                    session=session,
                    job=job,
                    steps=all_steps,
                    quality_report=quality_report,
                    now=now,
                    job_updates=job_updates,
                )
                return DurableExecutionResult(
                    job_id=job.id,
                    step_id=step.id,
                    disposition=disposition,
                    candidate_id=candidate.id,
                )

            checkpointed_job = _job_copy(job, **job_updates)
            self._require_job_cas(repository, checkpointed_job, job.state_version)
            return DurableExecutionResult(
                job_id=job.id,
                step_id=step.id,
                disposition=ExecutionDisposition.STEP_COMPLETED,
            )

    def _fail_step(
        self,
        job_id: str,
        step_id: str,
        error: StepExecutionError,
    ) -> DurableExecutionResult:
        now = self._clock()
        with self._transaction() as session:
            repository = CreativeGenerationRepository(session)
            job = self._required_job(repository, job_id)
            step = repository.get_step(step_id)
            if step is None or step.status is not StepStatus.RUNNING:
                raise ConcurrentWorkflowUpdate("failed step is no longer running")
            if job.status is JobStatus.CANCELLED:
                return self._cancel_running_step(repository, job, step, now)
            if job.status is JobStatus.PAUSED:
                interrupted_attempt = _attempt_copy(
                    step.attempt_history[-1],
                    status=AttemptStatus.INTERRUPTED,
                    finish_reason="paused",
                    completed_at=now,
                )
                paused_step = _step_copy(
                    step,
                    status=StepStatus.PAUSED,
                    attempt_history=(
                        *step.attempt_history[:-1],
                        interrupted_attempt,
                    ),
                    finish_reason="paused",
                    state_version=step.state_version + 1,
                    updated_at=now,
                )
                self._require_step_cas(repository, paused_step, step.state_version)
                return DurableExecutionResult(
                    job_id=job.id,
                    step_id=step.id,
                    disposition=ExecutionDisposition.PAUSED,
                )
            failed_attempt = _attempt_copy(
                step.attempt_history[-1],
                status=AttemptStatus.FAILED,
                error_code=error.code,
                safe_error_message=error.safe_message,
                completed_at=now,
            )
            if error.needs_author_input:
                suspended_step = _step_copy(
                    step,
                    status=StepStatus.NEEDS_AUTHOR_INPUT,
                    attempt_history=(*step.attempt_history[:-1], failed_attempt),
                    finish_reason="needs_author_input",
                    state_version=step.state_version + 1,
                    updated_at=now,
                )
                self._require_step_cas(repository, suspended_step, step.state_version)
                waiting_job = _job_copy(
                    job,
                    status=JobStatus.NEEDS_AUTHOR_INPUT,
                    resume_phase=_phase_for_step(step.step_kind),
                    state_version=job.state_version + 1,
                    updated_at=now,
                )
                self._require_job_cas(repository, waiting_job, job.state_version)
                return DurableExecutionResult(
                    job_id=job.id,
                    step_id=step.id,
                    disposition=ExecutionDisposition.NEEDS_AUTHOR_INPUT,
                )

            failed_step = _step_copy(
                step,
                status=StepStatus.FAILED,
                attempt_history=(*step.attempt_history[:-1], failed_attempt),
                partial=False if error.discard_partial else step.partial,
                partial_text="" if error.discard_partial else step.partial_text,
                partial_sha256="" if error.discard_partial else step.partial_sha256,
                stable_boundary_offset=(
                    0 if error.discard_partial else step.stable_boundary_offset
                ),
                error_code=error.code,
                safe_error_message=error.safe_message,
                completed_at=now,
                state_version=step.state_version + 1,
                updated_at=now,
            )
            self._require_step_cas(repository, failed_step, step.state_version)
            failed_job = _job_copy(
                job,
                status=JobStatus.FAILED,
                resume_phase=_phase_for_step(step.step_kind),
                error_code=error.code,
                safe_error_message=error.safe_message,
                completed_at=now,
                state_version=job.state_version + 1,
                updated_at=now,
            )
            self._require_job_cas(repository, failed_job, job.state_version)
            return DurableExecutionResult(
                job_id=job.id,
                step_id=step.id,
                disposition=ExecutionDisposition.FAILED,
            )

    def _fail_unclaimed_step(
        self,
        repository: CreativeGenerationRepository,
        job: GenerationJob,
        step: GenerationStep,
        error: StepExecutionError,
        now: str,
    ) -> DurableExecutionResult:
        if error.needs_author_input:
            waiting_step = _step_copy(
                step,
                status=StepStatus.NEEDS_AUTHOR_INPUT,
                finish_reason="attempts_exhausted_partial",
                interrupted_at=None,
                state_version=step.state_version + 1,
                updated_at=now,
            )
            self._require_step_cas(repository, waiting_step, step.state_version)
            waiting_job = _job_copy(
                job,
                status=JobStatus.NEEDS_AUTHOR_INPUT,
                resume_phase=_phase_for_step(step.step_kind),
                completed_at=None,
                state_version=job.state_version + 1,
                updated_at=now,
            )
            self._require_job_cas(repository, waiting_job, job.state_version)
            return DurableExecutionResult(
                job_id=job.id,
                step_id=step.id,
                disposition=ExecutionDisposition.NEEDS_AUTHOR_INPUT,
            )
        failed_job = _job_copy(
            job,
            status=JobStatus.FAILED,
            resume_phase=_phase_for_step(step.step_kind),
            error_code=error.code,
            safe_error_message=error.safe_message,
            completed_at=now,
            state_version=job.state_version + 1,
            updated_at=now,
        )
        self._require_job_cas(repository, failed_job, job.state_version)
        return DurableExecutionResult(
            job_id=job.id,
            step_id=step.id,
            disposition=ExecutionDisposition.FAILED,
        )

    def _finish_controlled_step(
        self,
        job_id: str,
        step_id: str,
        control: _ControlKind,
    ) -> DurableExecutionResult:
        now = self._clock()
        with self._transaction() as session:
            repository = CreativeGenerationRepository(session)
            job = self._required_job(repository, job_id)
            step = repository.get_step(step_id)
            if step is None:
                raise WorkflowNotFoundError(f"step not found: {step_id}")
            if step.status is not StepStatus.RUNNING:
                disposition = (
                    ExecutionDisposition.CANCELLED
                    if control is _ControlKind.CANCEL
                    else ExecutionDisposition.PAUSED
                )
                return DurableExecutionResult(
                    job_id=job.id,
                    step_id=step.id,
                    disposition=disposition,
                )
            if control is _ControlKind.CANCEL:
                return self._cancel_running_step(repository, job, step, now)

            interrupted_attempt = _attempt_copy(
                step.attempt_history[-1],
                status=AttemptStatus.INTERRUPTED,
                finish_reason="paused",
                completed_at=now,
            )
            paused_step = _step_copy(
                step,
                status=StepStatus.PAUSED,
                attempt_history=(*step.attempt_history[:-1], interrupted_attempt),
                finish_reason="paused",
                state_version=step.state_version + 1,
                updated_at=now,
            )
            self._require_step_cas(repository, paused_step, step.state_version)
            return DurableExecutionResult(
                job_id=job.id,
                step_id=step.id,
                disposition=ExecutionDisposition.PAUSED,
            )

    def _cancel_running_step(
        self,
        repository: CreativeGenerationRepository,
        job: GenerationJob,
        step: GenerationStep,
        now: str,
    ) -> DurableExecutionResult:
        cancelled_attempt = _attempt_copy(
            step.attempt_history[-1],
            status=AttemptStatus.CANCELLED,
            finish_reason="cancelled",
            completed_at=now,
        )
        cancelled_step = _step_copy(
            step,
            status=StepStatus.CANCELLED,
            attempt_history=(*step.attempt_history[:-1], cancelled_attempt),
            finish_reason="cancelled",
            completed_at=now,
            state_version=step.state_version + 1,
            updated_at=now,
        )
        self._require_step_cas(repository, cancelled_step, step.state_version)
        return DurableExecutionResult(
            job_id=job.id,
            step_id=step.id,
            disposition=ExecutionDisposition.CANCELLED,
        )

    def _require_resolved_unit_partial(
        self, job: GenerationJob, step: GenerationStep,
    ) -> None:
        """Never turn an exhausted unit into an unapproved provider attempt."""

        if step.step_kind not in UNIT_PROSE_KINDS or not step.partial:
            return
        spec = self._effective_length_spec(job, step=step)
        if spec is None:
            return
        state = self._validated_assurance_state(
            (step.output_json or {}).get(
                LENGTH_ASSURANCE_STATE_KEY,
                step.input_snapshot.get(LENGTH_ASSURANCE_STATE_KEY, {}),
            ),
            spec=spec,
            step_id=step.id,
        )
        if state.get("next_action") == AssuranceAction.SAVE_PARTIAL.value:
            raise WorkflowControlError(
                "This unit requires an explicit author resolution before another attempt; "
                "its saved partial and length contract have not been changed."
            )

    def _effective_length_spec(
        self, job: GenerationJob, *, step: GenerationStep,
    ) -> GenerationLengthSpec | None:
        original = self._typed_length_spec(job, step=step)
        if original is None or step.step_kind not in UNIT_PROSE_KINDS:
            return original
        decisions = self._read_only(
            lambda session: UnitAuthorDecisionRepository(session).list_for_job(job.id)
        )
        return effective_spec(job, step_unit_id(step), step.id, decisions)

    def _effective_assurance_limits(
        self, job: GenerationJob, step: GenerationStep,
    ) -> AssuranceLimits:
        original = self._assurance_limits(job, step)
        if step.step_kind not in UNIT_PROSE_KINDS:
            return original
        decisions = self._read_only(
            lambda session: UnitAuthorDecisionRepository(session).list_for_job(job.id)
        )
        # Validate the same original-contract chain before honoring extra capacity.
        effective_spec(job, step_unit_id(step), step.id, decisions)
        bound = granted_attempt_limit(step.id, decisions)
        if bound is None:
            return original
        return AssuranceLimits.model_validate({
            **original.model_dump(mode="json"), "max_total_attempts": min(step.max_attempts, bound),
        })

    @staticmethod
    def _typed_length_spec(
        job: GenerationJob, *, step: GenerationStep | None = None,
    ) -> GenerationLengthSpec | None:
        raw_spec = job.length_spec.get("generation_length_spec")
        if raw_spec is None:
            return None
        try:
            spec = GenerationLengthSpec.model_validate(raw_spec)
        except ValueError as exc:
            raise DurableWorkflowError("durable length contract is invalid") from exc
        if job.length_spec.get("generation_length_spec_sha256") != spec.sha256:
            raise DurableWorkflowError("durable length contract hash mismatch")
        if step is not None and step.step_kind in UNIT_PROSE_KINDS:
            planned = step.input_snapshot.get("planned")
            unit = planned.get("unit") if isinstance(planned, dict) else None
            unit_id = unit.get("id") if isinstance(unit, dict) else None
            if not isinstance(unit_id, str):
                raise DurableWorkflowError("unit prose step has no saved unit")
            try:
                return resolve_unit_length_spec(job.length_spec, unit_id)
            except ValueError as exc:
                raise DurableWorkflowError("durable unit length contract is invalid") from exc
        return spec

    @staticmethod
    def _state_counter(state: dict[str, object], key: str) -> int:
        value = state.get(key, 0)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise DurableWorkflowError(f"durable assurance {key} is invalid")
        return value

    @staticmethod
    def _validated_assurance_state(
        value: object,
        *,
        spec: GenerationLengthSpec,
        step_id: str | None = None,
    ) -> dict[str, object]:
        """Validate persisted N3 state before it can drive another paid call."""

        if not isinstance(value, dict):
            raise DurableWorkflowError("durable assurance state is invalid")
        if not value:
            return {}
        state = dict(value)
        saved_hash = state.pop("state_sha256", None)
        if not isinstance(saved_hash, str) or saved_hash != sha256_json(state):
            raise DurableWorkflowError("durable assurance state hash mismatch")
        if state.get("generation_length_spec_sha256") != spec.sha256:
            raise DurableWorkflowError("durable assurance state belongs to another length contract")
        if step_id is not None and state.get("step_id") != step_id:
            raise DurableWorkflowError("unit assurance state belongs to another step")
        ledger = state.get("attempt_ledger")
        if not isinstance(ledger, list):
            raise DurableWorkflowError("durable assurance ledger is invalid")
        if state.get("attempt_ledger_sha256") != sha256_json(ledger):
            raise DurableWorkflowError("durable assurance ledger hash mismatch")
        state["state_sha256"] = saved_hash
        return state

    @staticmethod
    def _assurance_limits(
        job: GenerationJob,
        step: GenerationStep,
    ) -> AssuranceLimits:
        configured_attempts = job.length_spec.get(
            "max_total_attempts",
            step.max_attempts,
        )
        if (
            isinstance(configured_attempts, bool)
            or not isinstance(configured_attempts, int)
            or configured_attempts < 1
        ):
            raise DurableWorkflowError("durable assurance attempt limit is invalid")
        repetition_retries = job.length_spec.get("max_repetition_retries", 2)
        if (
            isinstance(repetition_retries, bool)
            or not isinstance(repetition_retries, int)
            or repetition_retries < 0
        ):
            raise DurableWorkflowError("durable repetition retry limit is invalid")
        cost_limit = job.length_spec.get("max_estimated_cost_microunits")
        if cost_limit is not None and (
            isinstance(cost_limit, bool) or not isinstance(cost_limit, int) or cost_limit < 0
        ):
            raise DurableWorkflowError("durable assurance cost limit is invalid")
        try:
            return AssuranceLimits(
                max_total_attempts=min(step.max_attempts, configured_attempts),
                max_repetition_retries=repetition_retries,
                max_estimated_cost_microunits=cost_limit,
            )
        except ValueError as exc:
            raise DurableWorkflowError("durable assurance limits are invalid") from exc

    @staticmethod
    def _normalized_provider_termination(
        result: DurableStepRunResult,
    ) -> ProviderTermination:
        if result.provider_termination is not ProviderTermination.NORMAL_STOP:
            return result.provider_termination
        if not result.continuation_required:
            return ProviderTermination.NORMAL_STOP
        reason = result.finish_reason.strip().casefold()
        if reason in {
            "max_output_tokens",
            "max_tokens",
            "length",
            "num_predict",
            "token_limit",
        }:
            return ProviderTermination.TOKEN_LIMIT
        if reason in {"timeout", "timed_out", "deadline_exceeded"}:
            return ProviderTermination.TIMEOUT
        if reason in {"cancelled", "canceled", "request_cancelled"}:
            return ProviderTermination.CANCELLED
        return ProviderTermination.ERROR

    @staticmethod
    def _hash_assurance_state(state: dict[str, object]) -> dict[str, object]:
        payload = dict(state)
        payload.pop("state_sha256", None)
        payload["state_sha256"] = sha256_json(payload)
        return payload

    @staticmethod
    def _assurance_checkpoint_output(
        provider_output: dict[str, object] | None,
        assurance_state: dict[str, object],
    ) -> dict[str, object]:
        """Build the structured checkpoint stored between final-assembly calls."""

        payload = dict(provider_output or {})
        payload[LENGTH_ASSURANCE_STATE_KEY] = assurance_state
        return payload

    @staticmethod
    def _assurance_report(state: dict[str, object]) -> dict[str, object]:
        report = dict(state)
        report.pop("next_action", None)
        report.pop("continuation_plan", None)
        report["report_sha256"] = sha256_json(report)
        return report

    @staticmethod
    def _rolling_summary(job: GenerationJob) -> str:
        value = job.context_snapshot.get("rolling_summary", "")
        return value[:20_000] if isinstance(value, str) else ""

    @staticmethod
    def _protected_facts(job: GenerationJob) -> tuple[str, ...]:
        value = job.context_snapshot.get("protected_facts", ())
        if not isinstance(value, (list, tuple)):
            return ()
        return tuple(
            item.strip()[:1_000] for item in value[:100] if isinstance(item, str) and item.strip()
        )

    def _preserve_material_candidate(
        self,
        *,
        session: Session,
        job: GenerationJob,
        step: GenerationStep,
        result: DurableStepRunResult,
        content_text: str,
        content_json: dict[str, object] | None,
        candidate_role: str,
        assurance_state: dict[str, object],
        now: str,
    ) -> GenerationCandidate:
        repository = CreativeCandidateRepository(session)
        existing_candidates = repository.list_candidates(job_id=job.id)
        expected_content_sha256 = sha256_content(
            text=content_text,
            structured=content_json,
        )
        for existing in existing_candidates:
            if (
                existing.quality_report.get(_CANDIDATE_ROLE_KEY) == candidate_role
                and existing.producing_step_id == step.id
            ):
                if existing.content_sha256 != expected_content_sha256:
                    raise WorkflowIdempotencyConflict(
                        "candidate role exists with different preserved material"
                    )
                return existing

        candidate_version = (
            max(
                (candidate.candidate_version for candidate in existing_candidates),
                default=0,
            )
            + 1
        )
        candidate_id = f"{job.id}:candidate:{candidate_version:04d}"
        content_sha256 = expected_content_sha256
        fallback_used = result.fallback_used or (
            result.provider != step.requested_provider or result.model != step.requested_model
        )
        provenance = CandidateProvenance(
            job_id=job.id,
            step_id=step.id,
            workflow_version=job.workflow_version,
            template_id=step.template_id,
            template_version=step.template_version,
            requested_provider=step.requested_provider,
            requested_model=step.requested_model,
            provider=result.provider,
            model=result.model,
            fallback_used=fallback_used,
            attempt_number=step.attempts,
            input_snapshot_sha256=step.input_snapshot_sha256,
            output_sha256=content_sha256,
        )
        quality_report: dict[str, object] = {
            _CANDIDATE_ROLE_KEY: candidate_role,
            _ASSURANCE_REPORT_KEY: self._assurance_report(assurance_state),
        }
        length_evidence = self._final_length_evidence(job, content_text, step=step)
        if length_evidence is not None:
            quality_report["_cwe_2_1_length_contract"] = length_evidence
        candidate = GenerationCandidate(
            id=candidate_id,
            job_id=job.id,
            producing_step_id=step.id,
            artifact_kind=job.artifact_kind,
            target_type=job.target_type,
            target_id=job.target_id,
            parent_candidate_id=(existing_candidates[0].id if existing_candidates else None),
            candidate_version=candidate_version,
            base_snapshot=job.source_snapshot,
            base_snapshot_sha256=job.source_snapshot_sha256,
            content_text=content_text,
            content_json=content_json,
            content_sha256=content_sha256,
            quality_report=quality_report,
            provenance=provenance,
            created_at=now,
        )
        repository.add_candidate(candidate)
        return candidate

    def _finalize_job(
        self,
        *,
        session: Session,
        job: GenerationJob,
        steps: list[GenerationStep],
        quality_report: dict[str, object],
        now: str,
        job_updates: dict[str, object] | None = None,
    ) -> tuple[GenerationCandidate, ExecutionDisposition]:
        assembly = next(
            (
                step
                for step in steps
                if step.step_kind is StepKind.FINAL_ASSEMBLY and step.status is StepStatus.COMPLETED
            ),
            None,
        )
        if assembly is None:
            raise DurableWorkflowError("final assembly output is missing")
        final_quality_report = dict(quality_report)
        assurance_report = (
            assembly.output_json.get(_ASSURANCE_REPORT_KEY)
            if assembly.output_json is not None
            else None
        )
        if isinstance(assurance_report, dict):
            final_quality_report[_ASSURANCE_REPORT_KEY] = assurance_report
        length_evidence = self._final_length_evidence(job, assembly.output_text)
        length_satisfied = True
        if length_evidence is not None:
            final_quality_report["_cwe_2_1_length_contract"] = length_evidence
            length_satisfied = bool(length_evidence["satisfied"])
        protected_report = self._protected_facts_guard.inspect(
            candidate_text=assembly.output_text,
            protected_facts=self._protected_facts(job),
        )
        if length_evidence is not None or protected_report.fact_count:
            final_quality_report["_cwe_2_1_protected_facts"] = protected_report.model_dump(
                mode="json"
            )
        if not protected_report.literal_gate_passed:
            length_satisfied = False

        candidate_repository = CreativeCandidateRepository(session)
        prior_candidates = candidate_repository.list_candidates(job_id=job.id)
        typed_contract = self._typed_length_spec(job) is not None
        parent_candidate = prior_candidates[0] if prior_candidates else None
        if typed_contract:
            has_over_max_original = any(
                item.quality_report.get(_CANDIDATE_ROLE_KEY) == "over_max_original"
                for item in prior_candidates
            )
            final_role = "condensed_candidate" if has_over_max_original else "satisfied_candidate"
            final_quality_report[_CANDIDATE_ROLE_KEY] = final_role
            existing = next(
                (
                    item
                    for item in prior_candidates
                    if item.quality_report.get(_CANDIDATE_ROLE_KEY) == final_role
                ),
                None,
            )
            candidate_version = max(
                (item.candidate_version for item in prior_candidates),
                default=0,
            ) + (0 if existing is not None else 1)
            candidate_id = (
                existing.id
                if existing is not None
                else f"{job.id}:candidate:{candidate_version:04d}"
            )
        else:
            existing = candidate_repository.get_candidate(self._candidate_id(job.id))
            candidate_version = 1
            candidate_id = self._candidate_id(job.id)
        if existing is None:
            if assembly.provider is None or assembly.model is None:
                raise DurableWorkflowError("assembly provenance is incomplete")
            provenance = CandidateProvenance(
                job_id=job.id,
                step_id=assembly.id,
                workflow_version=job.workflow_version,
                template_id=assembly.template_id,
                template_version=assembly.template_version,
                requested_provider=assembly.requested_provider,
                requested_model=assembly.requested_model,
                provider=assembly.provider,
                model=assembly.model,
                fallback_used=assembly.fallback_used,
                attempt_number=assembly.attempts,
                input_snapshot_sha256=assembly.input_snapshot_sha256,
                output_sha256=assembly.output_sha256,
            )
            candidate = GenerationCandidate(
                id=candidate_id,
                job_id=job.id,
                producing_step_id=assembly.id,
                artifact_kind=job.artifact_kind,
                target_type=job.target_type,
                target_id=job.target_id,
                parent_candidate_id=(
                    parent_candidate.id if typed_contract and parent_candidate is not None else None
                ),
                candidate_version=candidate_version,
                base_snapshot=job.source_snapshot,
                base_snapshot_sha256=job.source_snapshot_sha256,
                content_text=assembly.output_text,
                content_json=assembly.output_json,
                content_sha256=assembly.output_sha256,
                quality_report=final_quality_report,
                provenance=provenance,
                created_at=now,
            )
            candidate_repository.add_candidate(candidate)
        else:
            if existing.content_sha256 != assembly.output_sha256:
                raise WorkflowIdempotencyConflict(
                    "candidate id exists with a different assembly hash"
                )
            if existing.quality_report != final_quality_report:
                raise WorkflowIdempotencyConflict(
                    "candidate id exists with different final assessment evidence"
                )
            candidate = existing

        updates = dict(job_updates or {})
        disposition = (
            ExecutionDisposition.JOB_COMPLETED
            if length_satisfied
            else ExecutionDisposition.NEEDS_AUTHOR_INPUT
        )
        resume_phase = (
            None
            if length_satisfied
            else job.status
            if job.status in ACTIVE_JOB_STATUSES
            else JobStatus.ASSEMBLING
        )
        updates.update(
            {
                "status": (
                    JobStatus.COMPLETED if length_satisfied else JobStatus.NEEDS_AUTHOR_INPUT
                ),
                "resume_phase": resume_phase,
                "pause_requested": False,
                "cancel_requested": False,
                "resume_requested": False,
                "error_code": "",
                "safe_error_message": "",
                "completed_at": now if length_satisfied else None,
                "state_version": job.state_version + 1,
                "updated_at": now,
            }
        )
        completed_job = _job_copy(job, **updates)
        self._require_job_cas(
            CreativeGenerationRepository(session),
            completed_job,
            job.state_version,
        )
        return candidate, disposition

    @staticmethod
    def _final_length_evidence(
        job: GenerationJob,
        output_text: str,
        *,
        step: GenerationStep | None = None,
    ) -> dict[str, object] | None:
        """Mechanically assess typed N2 jobs; legacy jobs retain prior behavior."""

        spec = DurableCreativeWorkflowService._typed_length_spec(job, step=step)
        if spec is None:
            return None
        measurement = measure_output(output_text, unit=spec.unit)
        assessment = measurement.assess(spec)
        return {
            "contract_version": "cwe-2.1-final-length-assessment-v1",
            "scope": "unit" if step is not None and step.step_kind in UNIT_PROSE_KINDS else "whole",
            "producing_step_id": step.id if step is not None else None,
            "generation_length_spec": spec.model_dump(mode="json"),
            "generation_length_spec_sha256": spec.sha256,
            "output_measurement": measurement.model_dump(mode="json"),
            "output_measurement_sha256": measurement.sha256,
            "final_assessment": assessment.model_dump(mode="json"),
            "final_assessment_sha256": sha256_json(assessment.model_dump(mode="json")),
            "satisfied": assessment.satisfied,
            "status": "satisfied" if assessment.satisfied else "partial_needs_action",
        }

    @staticmethod
    def _candidate_id(job_id: str) -> str:
        return f"{job_id}:candidate:0001"

    @staticmethod
    def _required_job(
        repository: CreativeGenerationRepository,
        job_id: str,
    ) -> GenerationJob:
        job = repository.get_job(job_id)
        if job is None:
            raise WorkflowNotFoundError(f"durable job not found: {job_id}")
        return job

    @staticmethod
    def _resolve_input(
        job: GenerationJob,
        step: GenerationStep,
        steps: list[GenerationStep],
    ) -> dict[str, object]:
        planned = step.input_snapshot.get("planned")
        if not isinstance(planned, dict):
            raise DurableWorkflowError("planned step metadata is missing")
        dependency_ids = planned.get("dependency_step_ids")
        if not isinstance(dependency_ids, list) or not all(
            isinstance(item, str) for item in dependency_ids
        ):
            raise DurableWorkflowError("planned dependency ids are invalid")
        by_id = {item.id: item for item in steps}
        dependencies: list[dict[str, object]] = []
        for dependency_id in dependency_ids:
            dependency = by_id.get(dependency_id)
            if dependency is None or dependency.status is not StepStatus.COMPLETED:
                raise DurableWorkflowError(f"dependency is not complete: {dependency_id}")
            dependency_planned = dependency.input_snapshot.get("planned")
            dependencies.append(
                {
                    "step_id": dependency.id,
                    "step_kind": dependency.step_kind.value,
                    "output_text": dependency.output_text,
                    "output_json": dependency.output_json,
                    "output_sha256": dependency.output_sha256,
                    "unit": (
                        dependency_planned.get("unit")
                        if isinstance(dependency_planned, dict)
                        else None
                    ),
                }
            )
        return {
            "job": {
                "artifact_kind": job.artifact_kind.value,
                "operation": job.operation.value,
                "target_type": job.target_type,
                "target_id": job.target_id,
                "quality_profile": job.quality_profile,
                "route": job.route,
                "length_spec": job.length_spec,
                "source_snapshot": job.source_snapshot,
                "context_snapshot": job.context_snapshot,
                "workflow_version": job.workflow_version,
            },
            "planned": planned,
            "dependencies": dependencies,
        }

    @staticmethod
    def _require_job_cas(
        repository: CreativeGenerationRepository,
        job: GenerationJob,
        expected_state_version: int,
    ) -> None:
        if not repository.update_job_cas(
            job,
            expected_state_version=expected_state_version,
        ):
            raise ConcurrentWorkflowUpdate("job changed concurrently")

    @staticmethod
    def _require_step_cas(
        repository: CreativeGenerationRepository,
        step: GenerationStep,
        expected_state_version: int,
    ) -> None:
        if not repository.update_step_cas(
            step,
            expected_state_version=expected_state_version,
        ):
            raise ConcurrentWorkflowUpdate("step changed concurrently")


__all__ = [
    "ConcurrentWorkflowUpdate",
    "DurableCreativeWorkflowService",
    "DurableWorkflowError",
    "WorkflowControlError",
    "WorkflowIdempotencyConflict",
    "WorkflowNotFoundError",
]
