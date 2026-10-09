"""Dedicated unit receipt validation, retaining the unchanged whole-book validator."""

from __future__ import annotations

from imaginarium_forge.application.errors import ConflictError
from imaginarium_forge.application.services.durable_generation.executor import (
    DurableCreativeWorkflowService,
)
from imaginarium_forge.application.services.durable_generation.unit_author_policy import (
    effective_spec,
)
from imaginarium_forge.application.services.durable_generation.unit_resolution import (
    UNIT_RESOLUTION_KEY,
    unit_identity,
)
from imaginarium_forge.application.services.story_orchestration_service import (
    ImportDurableStoryCandidateRequest,
    StoryGenerationOrchestrationService,
)
from imaginarium_forge.domain.generation.durable import (
    ArtifactKind,
    CandidateActionKind,
    CandidateStatus,
    GenerationStep,
    StepStatus,
    sha256_json,
    sha256_text,
)
from imaginarium_forge.domain.generation.unit_author_decision import UnitAuthorDecision


class UnitStoryCandidateImporter(StoryGenerationOrchestrationService):
    """Reuse only atomic draft/run/CAS/undo mechanics, with a unit-specific proof."""

    @staticmethod
    def _validate_durable_candidate_import(request: ImportDurableStoryCandidateRequest) -> None:
        candidate, job, current_step = request.candidate, request.job, request.step
        raw = candidate.quality_report.get(UNIT_RESOLUTION_KEY)
        if not isinstance(raw, dict):
            raise ConflictError("單章候選缺少作者確認收據。")
        receipt = dict(raw)
        saved_hash = receipt.pop("receipt_sha256", None)
        if saved_hash != sha256_json(receipt):
            raise ConflictError("單章作者確認收據 hash 不一致。")
        if (
            receipt.get("version") != "unit-author-accept-v1"
            or receipt.get("action") != "accept_current"
            or receipt.get("whole_job_complete") is not False
            or candidate.artifact_kind is not ArtifactKind.CHAPTER
            or candidate.status is not CandidateStatus.PROPOSED
            or candidate.job_id != job.id
            or candidate.producing_step_id != current_step.id
            or current_step.job_id != job.id
            or candidate.provenance.job_id != job.id
            or candidate.provenance.step_id != current_step.id
            or receipt.get("original_job_source_sha256") != job.source_snapshot_sha256
            or receipt.get("original_length_spec_sha256") != sha256_json(job.length_spec)
        ):
            raise ConflictError("單章作者確認收據不屬於原始工作。")
        raw_step = receipt.get("producing_step_snapshot")
        if receipt.get("producing_step_snapshot_sha256") != sha256_json(raw_step):
            raise ConflictError("單章保存步驟的 hash 不一致。")
        step = GenerationStep.model_validate(raw_step)
        if (
            step.id != current_step.id
            or step.job_id != job.id
            or step.status is not StepStatus.NEEDS_AUTHOR_INPUT
            or not step.partial
            or step.partial_text != candidate.content_text
            or receipt.get("partial_sha256") != sha256_text(candidate.content_text)
            or candidate.provenance.output_sha256 != candidate.content_sha256
            or candidate.provenance.input_snapshot_sha256 != step.input_snapshot_sha256
            or candidate.provenance.provider != step.provider
            or candidate.provenance.model != step.model
            or candidate.provenance.attempt_number != step.attempts
        ):
            raise ConflictError("單章保存正文與 provider provenance 不一致。")
        unit_id, _, scene_id = unit_identity(step)
        if unit_identity(current_step) != unit_identity(step):
            raise ConflictError("單章的保存場景對應已變更。")
        raw_decisions = receipt.get("author_decisions", [])
        if not isinstance(raw_decisions, list):
            raise ConflictError("單章作者調整證據無效。")
        decisions = tuple(UnitAuthorDecision.model_validate(item) for item in raw_decisions)
        spec = effective_spec(job, unit_id, step.id, decisions)
        state = receipt.get("assurance_state")
        if receipt.get("assurance_state_sha256") != sha256_json(state):
            raise ConflictError("單章長度驗證收據 hash 不一致。")
        validated = DurableCreativeWorkflowService._validated_assurance_state(
            state,
            spec=spec,
            step_id=step.id,
        )
        if (
            validated.get("next_action") != "save_partial"
            or receipt.get("unit_id") != unit_id
            or receipt.get("generation_length_spec_sha256") != spec.sha256
            or receipt.get("generation_length_spec") != spec.model_dump(mode="json")
        ):
            raise ConflictError("單章確認範圍與保存合約不一致。")
        payload = candidate.content_json
        if (
            not isinstance(payload, dict)
            or payload.get("scope") != "unit"
            or payload.get("units")
            != [{"id": unit_id, "title": unit_identity(step)[1], "text": candidate.content_text}]
            or request.action_kind
            not in {
                CandidateActionKind.APPLY_SELECTION,
                CandidateActionKind.SAVE_AS_NEW,
            }
            or (
                request.action_kind is CandidateActionKind.APPLY_SELECTION
                and request.selection != {"unit_id": unit_id}
            )
            or request.selected_prose != candidate.content_text
        ):
            raise ConflictError("此候選只能套用確認的單章，不能視為整本。")
        if (
            candidate.target_type != "story_scene"
            or candidate.target_id != scene_id
            or request.expected_scene_id != scene_id
            or request.expected_project_id != job.source_snapshot.get("project_id")
            or candidate.base_snapshot != request.before_snapshot
            or receipt.get("target_snapshot_sha256") != sha256_json(request.before_snapshot)
            or request.before_snapshot
            != {
                "project_id": request.expected_project_id,
                "story_scene_id": request.expected_scene_id,
                "scene_card_version_id": request.expected_scene_card_version_id,
                "working_draft_id": request.expected_working_draft_id,
                "accepted_draft_id": request.expected_accepted_draft_id,
                "scene_revision": request.expected_scene_revision,
            }
            or isinstance(request.expected_scene_revision, bool)
            or not isinstance(request.expected_scene_revision, int)
        ):
            raise ConflictError("單章目標場景 pin 不一致。")
        if (
            job.context_snapshot.get("content_mode", "general") != request.content_mode.value
            or payload.get("content_mode") != request.content_mode.value
        ):
            raise ConflictError("單章內容不符合公開展示的創作契約。")
