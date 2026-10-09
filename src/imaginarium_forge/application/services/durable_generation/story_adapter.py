"""Adapter from durable candidates to the existing Story Studio draft contract."""

from __future__ import annotations

from collections.abc import Mapping

from pydantic import ValidationError

from imaginarium_forge.application.errors import ConflictError, ValidationFailedError
from imaginarium_forge.application.services.base import SessionProvider
from imaginarium_forge.application.services.durable_generation.contracts import (
    ProjectApplyRequest,
    ProjectApplyResult,
)
from imaginarium_forge.application.services.story_orchestration_service import (
    ImportDurableStoryCandidateRequest,
    StoryGenerationOrchestrationService,
)
from imaginarium_forge.domain.generation.durable import (
    CandidateActionKind,
    GenerationCandidate,
    GenerationJob,
    GenerationStep,
)
from imaginarium_forge.domain.prompt.content_mode import (
    ContentMode,
)


class StorySceneCandidateAdapter:
    """Copy a reviewed general-audience candidate into an existing Scene."""

    def __init__(self, *, session_factory: SessionProvider) -> None:
        self._session_factory = session_factory

    def apply_candidate(self, request: ProjectApplyRequest) -> ProjectApplyResult:
        if request.target_type != "story_scene" or request.target_id is None:
            raise ValidationFailedError("Story 候選只能套用至已存在的 Story Studio 場景。")
        if request.action_kind not in {
            CandidateActionKind.APPLY_ALL,
            CandidateActionKind.APPLY_SELECTION,
            CandidateActionKind.SAVE_AS_NEW,
        }:
            raise ValidationFailedError("此 Story 候選操作尚未支援。")
        prose = self._selected_text(request)
        content_mode = self._content_mode(request.candidate_json)

        candidate, job, step = self._durable_evidence(request)
        expected_project = self._required_snapshot_id(
            request.before_snapshot,
            "project_id",
        )
        expected_scene = self._required_snapshot_id(
            request.before_snapshot,
            "story_scene_id",
        )
        expected_card = self._required_snapshot_id(
            request.before_snapshot,
            "scene_card_version_id",
        )
        expected_working = self._optional_snapshot_id(
            request.before_snapshot,
            "working_draft_id",
        )
        expected_accepted = self._optional_snapshot_id(
            request.before_snapshot,
            "accepted_draft_id",
        )
        if expected_scene != request.target_id:
            raise ConflictError("候選的 Story Scene snapshot 與目標不一致")
        if request.base_version_ref != expected_working:
            raise ConflictError("候選的 base version 與 working draft snapshot 不一致")

        importer = StoryGenerationOrchestrationService
        expected_revision = None
        if "unit_author_resolution" in candidate.quality_report:
            from imaginarium_forge.application.services.durable_generation import (
                unit_story_import,
            )

            importer = unit_story_import.UnitStoryCandidateImporter
            revision = request.before_snapshot.get("scene_revision")
            if isinstance(revision, bool) or not isinstance(revision, int):
                raise ValidationFailedError("單章候選缺少已確認的場景 revision。")
            expected_revision = revision
        imported = importer.import_durable_candidate(
            self._session_factory,
            ImportDurableStoryCandidateRequest(
                candidate=candidate,
                job=job,
                step=step,
                action_kind=request.action_kind,
                idempotency_key=request.idempotency_key,
                selected_prose=prose,
                before_snapshot=request.before_snapshot,
                selection=request.selection,
                content_mode=content_mode,
                expected_project_id=expected_project,
                expected_scene_id=expected_scene,
                expected_scene_card_version_id=expected_card,
                expected_working_draft_id=expected_working,
                expected_accepted_draft_id=expected_accepted,
                expected_scene_revision=expected_revision,
            ),
        )
        return ProjectApplyResult(
            after_snapshot=imported.after_snapshot or {
                "project_id": expected_project,
                "story_scene_id": expected_scene,
                "scene_card_version_id": expected_card,
                "working_draft_id": imported.draft.id,
                "accepted_draft_id": expected_accepted,
                "source_candidate_id": request.candidate_id,
                "source_generation_run_id": imported.run.id,
            },
            result_version_ref=imported.draft.id,
        )

    @staticmethod
    def _durable_evidence(
        request: ProjectApplyRequest,
    ) -> tuple[GenerationCandidate, GenerationJob, GenerationStep]:
        if (
            request.durable_candidate_snapshot is None
            or request.durable_job_snapshot is None
            or request.durable_step_snapshot is None
        ):
            raise ValidationFailedError(
                "Story 候選缺少 durable job/step/candidate provenance；沒有建立草稿。"
            )
        try:
            candidate = GenerationCandidate.model_validate(request.durable_candidate_snapshot)
            job = GenerationJob.model_validate(request.durable_job_snapshot)
            step = GenerationStep.model_validate(request.durable_step_snapshot)
        except ValidationError as exc:
            raise ValidationFailedError(
                "Story 候選的 durable job/step/candidate provenance 無效。"
            ) from exc
        if (
            candidate.id != request.candidate_id
            or candidate.content_text != request.candidate_text
            or candidate.content_json != request.candidate_json
        ):
            raise ConflictError("Story 候選內容與 immutable candidate receipt 不一致")
        return candidate, job, step

    @staticmethod
    def _required_snapshot_id(snapshot: dict[str, object], key: str) -> str:
        value = snapshot.get(key)
        if not isinstance(value, str) or not value:
            raise ValidationFailedError(f"候選 Story snapshot 缺少 {key}")
        return value

    @staticmethod
    def _optional_snapshot_id(
        snapshot: dict[str, object],
        key: str,
    ) -> str | None:
        if key not in snapshot:
            raise ValidationFailedError(f"候選 Story snapshot 缺少 {key}")
        value = snapshot[key]
        if value is not None and (not isinstance(value, str) or not value):
            raise ValidationFailedError(f"候選 Story snapshot 的 {key} 無效")
        return value

    @staticmethod
    def _content_mode(candidate_json: dict[str, object] | None) -> ContentMode:
        value = (
            "general"
            if candidate_json is None
            else candidate_json.get(
                "content_mode",
                "general",
            )
        )
        if not isinstance(value, str):
            raise ValidationFailedError("候選 content mode 無效")
        try:
            return ContentMode(value)
        except ValueError as exc:
            raise ValidationFailedError("候選 content mode 無效") from exc

    @staticmethod
    def _selected_text(request: ProjectApplyRequest) -> str:
        if request.action_kind is not CandidateActionKind.APPLY_SELECTION:
            if not request.candidate_text.strip():
                raise ValidationFailedError("候選正文不可留白")
            return request.candidate_text
        unit_id = request.selection.get("unit_id")
        if not isinstance(unit_id, str) or not unit_id:
            raise ValidationFailedError("套用單元必須指定 unit_id")
        if request.candidate_json is None:
            raise ValidationFailedError("候選缺少單元資料")
        units = request.candidate_json.get("units")
        if not isinstance(units, list):
            raise ValidationFailedError("候選單元資料無效")
        matches = [
            item for item in units if isinstance(item, Mapping) and item.get("id") == unit_id
        ]
        if len(matches) != 1:
            raise ValidationFailedError("候選找不到唯一的指定單元")
        text = matches[0].get("text")
        if not isinstance(text, str) or not text.strip():
            raise ValidationFailedError("指定單元沒有可套用正文")
        return text


__all__ = ["StorySceneCandidateAdapter"]
