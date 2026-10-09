"""Explicit author acceptance of a preserved unit, never whole-job completion."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from imaginarium_forge.application.errors import ConflictError
from imaginarium_forge.application.services.base import ServiceBase
from imaginarium_forge.application.services.durable_generation.contracts import (
    LENGTH_ASSURANCE_STATE_KEY,
)
from imaginarium_forge.application.services.durable_generation.executor import (
    DurableCreativeWorkflowService,
)
from imaginarium_forge.application.services.durable_generation.unit_author_policy import (
    effective_spec,
    granted_attempt_limit,
    step_unit_id,
)
from imaginarium_forge.application.services.durable_generation.unit_length_contracts import (
    UNIT_PROSE_KINDS,
    build_unit_length_spec,
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
from imaginarium_forge.application.services.generation.output_measurement import measure_output
from imaginarium_forge.domain.common.ids import utc_now_iso
from imaginarium_forge.domain.generation.durable import (
    ArtifactKind,
    AttemptStatus,
    CandidateProvenance,
    GenerationCandidate,
    GenerationJob,
    GenerationStep,
    GenerationStepAttempt,
    JobStatus,
    StepStatus,
    sha256_content,
    sha256_json,
    sha256_text,
)
from imaginarium_forge.domain.generation.length import GenerationLengthSpec
from imaginarium_forge.domain.generation.unit_author_decision import UnitAuthorDecision
from imaginarium_forge.domain.prompt.content_mode import (
    ContentMode,
)
from imaginarium_forge.infrastructure.db.repositories.creative_candidates import (
    CreativeCandidateRepository,
)
from imaginarium_forge.infrastructure.db.repositories.creative_generation import (
    CreativeGenerationRepository,
)
from imaginarium_forge.infrastructure.db.repositories.story_repos import StorySceneRepository
from imaginarium_forge.infrastructure.db.repositories.unit_author_decisions import (
    UnitAuthorDecisionRepository,
)

UNIT_RESOLUTION_KEY = "unit_author_resolution"


class UnitResolutionConflict(ConflictError):
    """The reviewed partial or its author target changed; nothing was applied."""


class UnitRecoveryCommand(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    job_id: str = Field(min_length=1, max_length=200)
    step_id: str = Field(min_length=1, max_length=200)
    expected_job_version: int = Field(ge=0)
    expected_step_version: int = Field(ge=0)
    expected_partial_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    idempotency_key: str = Field(min_length=1, max_length=200)


class GrantUnitContinuationCommand(UnitRecoveryCommand):
    """Authorize precisely one additional application attempt, never start it."""


class AdjustUnitTargetCommand(UnitRecoveryCommand):
    target_visible_chars: int = Field(ge=1, le=1_000_000, strict=True)


class AcceptUnitPartialCommand(UnitRecoveryCommand):
    before_snapshot: dict[str, object]


def unit_identity(step: GenerationStep) -> tuple[str, str, str]:
    planned = step.input_snapshot.get("planned")
    unit = planned.get("unit") if isinstance(planned, dict) else None
    metadata = unit.get("metadata") if isinstance(unit, dict) else None
    if not isinstance(unit, dict) or not isinstance(metadata, dict):
        raise UnitResolutionConflict("此單章缺少保存的場景對應，沒有建立候選。")
    values = (unit.get("id"), unit.get("title"), metadata.get("anchor_story_scene_id"))
    if not all(isinstance(value, str) and value for value in values):
        raise UnitResolutionConflict("此單章的場景對應無效，沒有建立候選。")
    return str(values[0]), str(values[1]), str(values[2])


class UnitResolutionService(ServiceBase):
    """Persist an immutable unit receipt; author text remains untouched until apply."""

    @staticmethod
    def _required(
        session: Session,
        job_id: str,
        step_id: str,
    ) -> tuple[GenerationJob, GenerationStep]:
        repository = CreativeGenerationRepository(session)
        job, step = repository.get_job(job_id), repository.get_step(step_id)
        if job is None or step is None or step.job_id != job.id:
            raise UnitResolutionConflict("找不到對應的已保存單章。")
        return job, step

    @staticmethod
    def _target_snapshot(
        session: Session,
        job: GenerationJob,
        step: GenerationStep,
    ) -> dict[str, object]:
        _, _, scene_id = unit_identity(step)
        repository = StorySceneRepository(session)
        scene = repository.get(scene_id)
        if scene is None or scene.project_id != job.source_snapshot.get("project_id"):
            raise UnitResolutionConflict("此單章的目標場景不存在或不屬於原專案。")
        card = scene.working_head_card_version_id or scene.accepted_card_version_id
        if card is None:
            raise UnitResolutionConflict("目標場景沒有可確認的場景卡。")
        return {
            "project_id": scene.project_id,
            "story_scene_id": scene.id,
            "scene_card_version_id": card,
            "working_draft_id": scene.working_draft_id,
            "accepted_draft_id": scene.accepted_draft_id,
            "scene_revision": repository.get_revision(scene.id),
        }

    def target_snapshot(self, job_id: str, step_id: str) -> dict[str, object]:
        """Return a preview pin; the explicit command must return the same snapshot."""

        def read(session: Session) -> dict[str, object]:
            job, step = self._required(session, job_id, step_id)
            return self._target_snapshot(session, job, step)

        return self._read_only(read)

    def effective_unit_spec(self, job_id: str, unit_id: str) -> GenerationLengthSpec:
        def read(session: Session) -> GenerationLengthSpec:
            job = CreativeGenerationRepository(session).get_job(job_id)
            if job is None:
                raise UnitResolutionConflict("找不到原始工作。")
            return effective_spec(
                job,
                unit_id,
                "",
                UnitAuthorDecisionRepository(session).list_for_job(job.id),
            )

        return self._read_only(read)

    def list_decisions(self, job_id: str) -> tuple[UnitAuthorDecision, ...]:
        return self._read_only(
            lambda session: UnitAuthorDecisionRepository(session).list_for_job(job_id)
        )

    def grant_continuation(self, command: GrantUnitContinuationCommand) -> UnitAuthorDecision:
        return self._recover(command)

    def adjust_target(self, command: AdjustUnitTargetCommand) -> UnitAuthorDecision:
        return self._recover(command)

    def _recover(
        self,
        command: GrantUnitContinuationCommand | AdjustUnitTargetCommand,
    ) -> UnitAuthorDecision:
        fingerprint = sha256_json(command.model_dump(mode="json"))
        decision_id = "unit-decision:" + sha256_text(command.idempotency_key)[:40]
        kind = (
            "adjust_target"
            if isinstance(command, AdjustUnitTargetCommand)
            else "grant_continuation"
        )
        with self._transaction() as session:
            journal = UnitAuthorDecisionRepository(session)
            existing = journal.get(decision_id)
            if existing is not None:
                if existing.command_sha256 != fingerprint or existing.kind != kind:
                    raise UnitResolutionConflict("此作者決策識別碼已用於其他操作。")
                return existing
            repository = CreativeGenerationRepository(session)
            job, step = self._required(session, command.job_id, command.step_id)
            first = next(
                (
                    item
                    for item in repository.list_steps(job.id)
                    if item.status is not StepStatus.COMPLETED
                ),
                None,
            )
            if (
                job.status is not JobStatus.NEEDS_AUTHOR_INPUT
                or job.state_version != command.expected_job_version
                or step.state_version != command.expected_step_version
                or first is None
                or first.id != step.id
                or step.step_kind not in UNIT_PROSE_KINDS
                or step.status is not StepStatus.NEEDS_AUTHOR_INPUT
                or not step.partial
                or step.partial_sha256 != command.expected_partial_sha256
            ):
                raise UnitResolutionConflict("保存內容或工作狀態已變更，請重新檢視單章。")
            mode = ContentMode(str(job.context_snapshot.get("content_mode", "general")))
            decisions = journal.list_for_job(job.id)
            unit_id = step_unit_id(step)
            spec = effective_spec(job, unit_id, step.id, decisions)
            state = DurableCreativeWorkflowService._validated_assurance_state(
                (step.output_json or {}).get(LENGTH_ASSURANCE_STATE_KEY, {}),
                spec=spec,
                step_id=step.id,
            )
            if state.get("next_action") != AssuranceAction.SAVE_PARTIAL.value:
                raise UnitResolutionConflict("此單章沒有等待作者決策的保存片段。")
            ledger = state.get("attempt_ledger")
            if not isinstance(ledger, list) or not ledger or not isinstance(ledger[-1], dict):
                raise UnitResolutionConflict("此單章缺少可驗證的 provider 結束證據。")
            last = ledger[-1]
            if last.get("accumulated_output_sha256") != step.partial_sha256:
                raise UnitResolutionConflict("保存片段與長度計量紀錄不一致。")
            progress = AssuranceProgress.model_validate(state.get("progress"))
            if progress.provider_attempts != step.attempts:
                raise UnitResolutionConflict("保存的已消耗次數不一致。")
            limits = DurableCreativeWorkflowService._assurance_limits(job, step)
            prior_bound = granted_attempt_limit(step.id, decisions)
            if prior_bound is not None:
                limits = AssuranceLimits.model_validate(
                    {
                        **limits.model_dump(mode="json"),
                        "max_total_attempts": min(step.max_attempts, prior_bound),
                    }
                )
            termination = ProviderTermination(str(last.get("provider_termination")))
            payload: dict[str, object] = {
                "version": "unit-author-decision-v1",
                "previous_decision_sha256": decisions[-1].payload_sha256 if decisions else "",
                "original_length_spec_sha256": sha256_json(job.length_spec),
                "original_source_sha256": job.source_snapshot_sha256,
                "before_step": step.model_dump(mode="json"),
                "before_step_sha256": sha256_json(step.model_dump(mode="json")),
                "before_assurance_state": state,
                "before_spec": spec.model_dump(mode="json"),
                "before_spec_sha256": spec.sha256,
                "provider_io_performed": False,
            }
            now = utc_now_iso()
            next_state = dict(state)
            changes: dict[str, object] = {
                "state_version": step.state_version + 1,
                "updated_at": now,
                "checkpoint_index": step.checkpoint_index + 1,
                "checkpoint_at": now,
            }
            complete = False
            if isinstance(command, GrantUnitContinuationCommand):
                if step.requested_provider == "offline":
                    raise UnitResolutionConflict(
                        "此舊離線工作不能轉送其他 provider；請保留片段並建立新工作。"
                    )
                if step.attempts >= 10 or progress.continuations_used >= 5:
                    raise UnitResolutionConflict("已達單步硬性上限；不能再授權額外呼叫。")
                spec = GenerationLengthSpec.model_validate(
                    {
                        **spec.model_dump(mode="json"),
                        "max_continuations": progress.continuations_used + 1,
                    }
                )
                limits = AssuranceLimits.model_validate(
                    {
                        **limits.model_dump(mode="json"),
                        "max_total_attempts": step.attempts + 1,
                    }
                )
                if (
                    not LengthAssuranceService()
                    .budget_state(spec=spec, progress=progress, limits=limits)
                    .can_issue_continuation
                ):
                    raise UnitResolutionConflict("原成本上限不足；未增加限額或送出呼叫。")
                # Preserve all prior prose, including an incomplete final sentence.
                plan = ContinuationPlanner().plan(
                    existing_text=step.partial_text,
                    spec=spec,
                    continuations_used=progress.continuations_used,
                    rolling_summary=DurableCreativeWorkflowService._rolling_summary(job),
                    protected_facts=(),
                    provider_incomplete=False,
                )
                next_state.update(
                    {
                        "next_action": AssuranceAction.CONTINUE.value,
                        "continuation_plan": plan.model_dump(mode="json"),
                    }
                )
                changes.update(
                    {
                        "status": StepStatus.PAUSED,
                        "max_attempts": step.attempts + 1,
                        "stable_boundary_offset": plan.stable_boundary_offset,
                    }
                )
                payload.update(
                    {
                        "max_continuations": spec.max_continuations,
                        "max_total_attempts": step.attempts + 1,
                        "runtime_provider_attempt_ceiling": last.get(
                            "runtime_provider_attempt_ceiling"
                        ),
                    }
                )
            else:
                original_spec = DurableCreativeWorkflowService._typed_length_spec(job, step=step)
                if original_spec is None:
                    raise UnitResolutionConflict("單章原始長度合約不存在。")
                revised = build_unit_length_spec(original_spec, command.target_visible_chars)
                spec = GenerationLengthSpec.model_validate(
                    {
                        **revised.model_dump(mode="json"),
                        "max_continuations": spec.max_continuations,
                    }
                )
                reassessment = LengthAssuranceService().evaluate(
                    text=step.partial_text,
                    spec=spec,
                    provider_termination=termination,
                    provider_finish_reason=str(last.get("finish_reason", "")),
                    progress=progress,
                    limits=limits,
                )
                complete = (
                    reassessment.action is AssuranceAction.COMPLETE
                    and termination is ProviderTermination.NORMAL_STOP
                    and not state.get("domain_completion_blocked_by")
                    and last.get("accepted_into_candidate") is True
                )
                # Target editing is never authority for another call.
                next_state.update(
                    {
                        "next_action": AssuranceAction.COMPLETE.value
                        if complete
                        else AssuranceAction.SAVE_PARTIAL.value,
                        "continuation_plan": None,
                        "final_decision": reassessment.model_dump(mode="json")
                        if complete
                        else state["final_decision"],
                        "local_target_reassessment": reassessment.model_dump(mode="json"),
                        "application_status": reassessment.status.value
                        if complete
                        else "needs_author_input",
                        "application_reason": reassessment.reason.value
                        if complete
                        else state.get("application_reason", "author_resolution_required"),
                    }
                )
                payload.update(
                    {
                        "target_visible_chars": command.target_visible_chars,
                        "local_reassessment": reassessment.model_dump(mode="json"),
                        "locally_completed_application_step": complete,
                    }
                )
                if complete:
                    # This closes the application-length gate after an actual normal provider stop.
                    # The immutable receipt retains the original interrupted attempt, unchanged.
                    attempt = GenerationStepAttempt.model_validate(
                        {
                            **step.attempt_history[-1].model_dump(mode="json"),
                            "status": AttemptStatus.COMPLETED,
                        }
                    )
                    changes.update(
                        {
                            "status": StepStatus.COMPLETED,
                            "completed_at": now,
                            "interrupted_at": None,
                            "output_text": step.partial_text,
                            "partial": False,
                            "partial_text": "",
                            "partial_sha256": "",
                            "stable_boundary_offset": 0,
                            "attempt_history": (*step.attempt_history[:-1], attempt),
                            "finish_reason": step.attempt_history[-1].finish_reason,
                        }
                    )
            next_state.update(
                {
                    "generation_length_spec": spec.model_dump(mode="json"),
                    "generation_length_spec_sha256": spec.sha256,
                    "limits": limits.model_dump(mode="json"),
                    "author_decision_id": decision_id,
                }
            )
            next_state = DurableCreativeWorkflowService._hash_assurance_state(next_state)
            output = dict(step.output_json or {})
            output[LENGTH_ASSURANCE_STATE_KEY] = next_state
            if complete:
                output["_cwe_2_1_length_assurance"] = (
                    DurableCreativeWorkflowService._assurance_report(next_state)
                )
            changes.update(
                {
                    "output_json": output,
                    "output_sha256": sha256_content(
                        text=step.partial_text if complete else "",
                        structured=output,
                    ),
                }
            )
            updated_step = GenerationStep.model_validate(
                {**step.model_dump(mode="json"), **changes}
            )
            payload.update(
                {
                    "after_spec": spec.model_dump(mode="json"),
                    "after_spec_sha256": spec.sha256,
                    "after_step_sha256": sha256_json(updated_step.model_dump(mode="json")),
                }
            )
            decision = UnitAuthorDecision.model_validate(
                {
                    "id": decision_id,
                    "job_id": job.id,
                    "step_id": step.id,
                    "unit_id": unit_id,
                    "sequence": len(decisions) + 1,
                    "kind": kind,
                    "command_sha256": fingerprint,
                    "payload": payload,
                    "payload_sha256": sha256_json(payload),
                    "created_at": now,
                }
            )
            # Validate replay before committing either row; failures roll back the entire command.
            if effective_spec(job, unit_id, step.id, (*decisions, decision)) != spec:
                raise UnitResolutionConflict("作者決策與有效合約不一致。")
            updated_job = GenerationJob.model_validate(
                {
                    **job.model_dump(mode="json"),
                    "status": JobStatus.PAUSED
                    if complete or kind == "grant_continuation"
                    else JobStatus.NEEDS_AUTHOR_INPUT,
                    "pause_requested": complete or kind == "grant_continuation",
                    "state_version": job.state_version + 1,
                    "updated_at": now,
                }
            )
            journal.add(decision)
            if not repository.authorize_unit_step_cas(
                updated_step, expected_state_version=step.state_version
            ):
                raise UnitResolutionConflict("單章已在其他視窗變更，沒有保存決策。")
            if not repository.update_job_cas(updated_job, expected_state_version=job.state_version):
                raise UnitResolutionConflict("工作已在其他視窗變更，沒有保存決策。")
            return decision

    def accept_current(self, command: AcceptUnitPartialCommand) -> GenerationCandidate:
        fingerprint = sha256_json(command.model_dump(mode="json"))
        candidate_id = "unit-accept:" + sha256_text(command.idempotency_key)[:40]
        with self._transaction() as session:
            candidates = CreativeCandidateRepository(session)
            existing = candidates.get_candidate(candidate_id)
            if existing is not None:
                prior_receipt = existing.quality_report.get(UNIT_RESOLUTION_KEY)
                if (
                    not isinstance(prior_receipt, dict)
                    or prior_receipt.get("command_sha256") != fingerprint
                ):
                    raise UnitResolutionConflict("此確認識別碼已用於其他內容，沒有覆寫候選。")
                return existing
            job, step = self._required(session, command.job_id, command.step_id)
            repository = CreativeGenerationRepository(session)
            first = next(
                (
                    item
                    for item in repository.list_steps(job.id)
                    if item.status is not StepStatus.COMPLETED
                ),
                None,
            )
            if (
                job.status is not JobStatus.NEEDS_AUTHOR_INPUT
                or job.state_version != command.expected_job_version
                or step.state_version != command.expected_step_version
                or first is None
                or first.id != step.id
                or step.step_kind not in UNIT_PROSE_KINDS
                or step.status is not StepStatus.NEEDS_AUTHOR_INPUT
                or not step.partial
                or step.partial_sha256 != command.expected_partial_sha256
            ):
                raise UnitResolutionConflict("保存內容或工作狀態已變更，請重新檢視單章。")
            original_spec = DurableCreativeWorkflowService._typed_length_spec(job, step=step)
            decisions = UnitAuthorDecisionRepository(session).list_for_job(job.id)
            spec = effective_spec(job, step_unit_id(step), step.id, decisions)
            if spec is None:
                raise UnitResolutionConflict("此單章缺少可驗證的長度合約。")
            state = DurableCreativeWorkflowService._validated_assurance_state(
                (step.output_json or {}).get(LENGTH_ASSURANCE_STATE_KEY, {}),
                spec=spec,
                step_id=step.id,
            )
            if state.get("next_action") != "save_partial":
                raise UnitResolutionConflict("此單章不是等待作者確認的保存片段。")
            mode = ContentMode(str(job.context_snapshot.get("content_mode", "general")))
            snapshot = self._target_snapshot(session, job, step)
            # The pin is reviewed now, not silently reconstructed during apply.
            if snapshot != command.before_snapshot:
                raise UnitResolutionConflict("目標場景已變更，請重新檢視後再確認。")
            unit_id, title, scene_id = unit_identity(step)
            now = utc_now_iso()
            body: dict[str, object] = {
                "scope": "unit",
                "content_mode": mode.value,
                "units": [{"id": unit_id, "title": title, "text": step.partial_text}],
            }
            content_hash = sha256_content(text=step.partial_text, structured=body)
            receipt: dict[str, object] = {
                "version": "unit-author-accept-v1",
                "action": "accept_current",
                "command_sha256": fingerprint,
                "unit_id": unit_id,
                "partial_sha256": step.partial_sha256,
                "original_job_source_sha256": job.source_snapshot_sha256,
                "original_length_spec_sha256": sha256_json(job.length_spec),
                "original_contract_satisfied": measure_output(step.partial_text, unit=spec.unit)
                .assess(original_spec or spec)
                .satisfied,
                "author_decisions": [item.model_dump(mode="json") for item in decisions],
                "generation_length_spec": spec.model_dump(mode="json"),
                "generation_length_spec_sha256": spec.sha256,
                "producing_step_snapshot": step.model_dump(mode="json"),
                "producing_step_snapshot_sha256": sha256_json(step.model_dump(mode="json")),
                "assurance_state": state,
                "assurance_state_sha256": sha256_json(state),
                "target_snapshot_sha256": sha256_json(snapshot),
                "whole_job_complete": False,
                "accepted_at": now,
            }
            receipt["receipt_sha256"] = sha256_json(receipt)
            version = (
                max(
                    (item.candidate_version for item in candidates.list_candidates(job_id=job.id)),
                    default=0,
                )
                + 1
            )
            candidate = GenerationCandidate(
                id=candidate_id,
                job_id=job.id,
                producing_step_id=step.id,
                artifact_kind=ArtifactKind.CHAPTER,
                target_type="story_scene",
                target_id=scene_id,
                candidate_version=version,
                base_snapshot=snapshot,
                base_snapshot_sha256=sha256_json(snapshot),
                content_text=step.partial_text,
                content_json=body,
                content_sha256=content_hash,
                quality_report={UNIT_RESOLUTION_KEY: receipt},
                provenance=CandidateProvenance(
                    job_id=job.id,
                    step_id=step.id,
                    workflow_version=job.workflow_version,
                    template_id=step.template_id,
                    template_version=step.template_version,
                    requested_provider=step.requested_provider,
                    requested_model=step.requested_model,
                    provider=step.provider,
                    model=step.model,
                    fallback_used=step.fallback_used,
                    attempt_number=step.attempts,
                    input_snapshot_sha256=step.input_snapshot_sha256,
                    output_sha256=content_hash,
                ),
                created_at=now,
            )
            candidates.add_candidate(candidate)
            updated_job = GenerationJob.model_validate(
                {
                    **job.model_dump(mode="json"),
                    "state_version": job.state_version + 1,
                    "updated_at": now,
                }
            )
            if not repository.update_job_cas(updated_job, expected_state_version=job.state_version):
                raise UnitResolutionConflict("工作已在其他視窗變更，沒有建立候選。")
            return candidate
