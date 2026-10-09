"""Undo-only Story Scene adapter using persistent revisions and immutable receipts.

This adapter restores the working pointer, including NULL. It never creates a
fake draft, changes the accepted pointer, or owns a transaction. The completed
candidate action records the new verifiable Scene revision in that transaction.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.orm import Session

from imaginarium_forge.application.services.generation.candidate_application import (
    CandidateActionNotUndoableError,
    TargetSnapshotRequest,
    TargetVersionConflict,
    TargetVersionRequest,
    VersionedTargetSnapshot,
)
from imaginarium_forge.domain.common.ids import utc_now_iso
from imaginarium_forge.domain.generation.durable import (
    CandidateAction,
    CandidateActionKind,
    CandidateActionStatus,
    sha256_json,
    sha256_text,
)
from imaginarium_forge.infrastructure.db.repositories.story_repos import (
    GenerationRunRecord,
    GenerationRunRepository,
    SceneDraftRepository,
    SceneRecord,
    StorySceneRepository,
)


class _SceneState(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid", hide_input_in_errors=True)

    id: str = Field(min_length=1)
    story_chapter_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    scene_number: int
    title: str
    working_head_card_version_id: str | None
    accepted_card_version_id: str | None
    working_draft_id: str | None
    accepted_draft_id: str | None
    summary_text: str
    created_at: str
    updated_at: str


class _UndoSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid", hide_input_in_errors=True)

    undo_schema_version: Literal["story-scene-undo-v1"]
    project_id: str = Field(min_length=1)
    story_scene_id: str = Field(min_length=1)
    scene_card_version_id: str = Field(min_length=1)
    working_draft_id: str | None
    accepted_draft_id: str | None
    source_candidate_id: str = Field(min_length=1)
    source_generation_run_id: str = Field(min_length=1)
    scene_revision: int = Field(ge=1)
    scene_state: _SceneState

    @model_validator(mode="after")
    def _consistent(self) -> _UndoSnapshot:
        state = self.scene_state
        if (
            self.project_id != state.project_id
            or self.story_scene_id != state.id
            or self.working_draft_id != state.working_draft_id
            or self.accepted_draft_id != state.accepted_draft_id
            or self.scene_card_version_id
            not in {state.working_head_card_version_id, state.accepted_card_version_id}
        ):
            raise ValueError("Inconsistent Story Undo snapshot")
        return self


def story_undo_snapshot(
    scene: SceneRecord,
    *,
    revision: int,
    scene_card_version_id: str,
    candidate_id: str,
    generation_run_id: str,
) -> dict[str, object]:
    """Build a strict after-state envelope within the import/Undo transaction."""
    return _UndoSnapshot.model_validate(
        {
            "undo_schema_version": "story-scene-undo-v1",
            "project_id": scene.project_id,
            "story_scene_id": scene.id,
            "scene_card_version_id": scene_card_version_id,
            "working_draft_id": scene.working_draft_id,
            "accepted_draft_id": scene.accepted_draft_id,
            "source_candidate_id": candidate_id,
            "source_generation_run_id": generation_run_id,
            "scene_revision": revision,
            "scene_state": asdict(scene),
        }
    ).model_dump(mode="json")


def _receipt(run: GenerationRunRecord) -> dict[str, object]:
    payload = json.loads(run.input_snapshot_json)
    if sha256_text(run.input_snapshot_json) != run.input_snapshot_sha256:
        raise ValueError("Story run snapshot hash mismatch")
    raw = payload["durable_candidate_receipt_json"]
    if not isinstance(raw, str) or sha256_text(raw) != payload["durable_candidate_receipt_sha256"]:
        raise ValueError("Story receipt hash mismatch")
    result = json.loads(raw)
    if not isinstance(result, dict):
        raise ValueError("Story receipt is unavailable")
    return result


def imported_story_undo_snapshot(run: GenerationRunRecord) -> dict[str, object] | None:
    """Return the recorded import-time envelope; legacy receipts remain legacy."""
    value = _receipt(run).get("target_after")
    if value is None:
        return None
    return _UndoSnapshot.model_validate(value).model_dump(mode="json")


def supports_story_undo(action: CandidateAction) -> bool:
    """Check receipt capability only; freshness is rechecked inside Undo's CAS."""
    if (
        action.target_type != "story_scene"
        or action.target_id is None
        or action.status is not CandidateActionStatus.COMPLETED
        or action.action_kind
        not in {
            CandidateActionKind.APPLY_ALL,
            CandidateActionKind.APPLY_SELECTION,
            CandidateActionKind.SAVE_AS_NEW,
        }
        or action.after_snapshot is None
    ):
        return False
    try:
        snapshot = _UndoSnapshot.model_validate(action.after_snapshot)
        before = action.before_snapshot
        return (
            snapshot.story_scene_id == action.target_id
            and snapshot.source_candidate_id == action.candidate_id
            and snapshot.working_draft_id is not None
            and snapshot.working_draft_id == action.result_version_ref
            and sha256_json(action.after_snapshot) == action.after_snapshot_sha256
            and before.get("project_id") == snapshot.project_id
            and before.get("story_scene_id") == snapshot.story_scene_id
            and before.get("scene_card_version_id") == snapshot.scene_card_version_id
            and "working_draft_id" in before
            and "accepted_draft_id" in before
            and before["accepted_draft_id"] == snapshot.accepted_draft_id
            and action.base_version_ref == before["working_draft_id"]
        )
    except (ValueError, TypeError):
        return False


class StoryCandidateUndoTargetAdapter:
    """Restore only an exact reviewed Story import, with no provider or commit."""

    def read_snapshot(
        self, session: Session, request: TargetSnapshotRequest
    ) -> VersionedTargetSnapshot:
        if (
            request.target_type != "story_scene"
            or not request.target_id
            or not request.version_ref_hint
        ):
            raise ValueError("Story Undo target is unavailable")
        draft = SceneDraftRepository(session).get(request.version_ref_hint)
        if (
            draft is None
            or draft.story_scene_id != request.target_id
            or not draft.generation_run_id
        ):
            raise ValueError("Story import draft is unavailable")
        run = GenerationRunRepository(session).get(draft.generation_run_id)
        if run is None:
            raise ValueError("Story import receipt is unavailable")
        recorded = imported_story_undo_snapshot(run)
        if recorded is None:
            raise ValueError("Legacy Story receipt has no verifiable revision")
        envelope = _UndoSnapshot.model_validate(recorded)
        if (
            envelope.working_draft_id != draft.id
            or envelope.source_generation_run_id != run.id
            or envelope.story_scene_id != request.target_id
            or run.story_scene_id != request.target_id
            or envelope.project_id != draft.project_id
            or run.project_id != draft.project_id
        ):
            raise ValueError("Story import binding mismatch")
        scenes = StorySceneRepository(session)
        scene = scenes.get(request.target_id)
        if scene is None:
            raise TargetVersionConflict
        # A stale card may no longer satisfy the envelope shape: classify it as
        # a conflict instead of rebuilding history from the current card.
        if (
            scene != SceneRecord(**envelope.scene_state.model_dump())
            or scenes.get_revision(scene.id) != envelope.scene_revision
        ):
            raise TargetVersionConflict
        return VersionedTargetSnapshot(
            target_type="story_scene",
            target_id=scene.id,
            snapshot=recorded,
            version_ref=draft.id,
        )

    def create_version(
        self, session: Session, request: TargetVersionRequest
    ) -> VersionedTargetSnapshot:
        if request.action_kind is not CandidateActionKind.UNDO or request.restore_snapshot is None:
            raise CandidateActionNotUndoableError
        current = self.read_snapshot(
            session,
            TargetSnapshotRequest(
                target_type=request.target_type,
                target_id=request.target_id,
                version_ref_hint=request.expected_version_ref,
            ),
        )
        if (
            sha256_json(current.snapshot) != request.expected_snapshot_sha256
            or current.snapshot != request.before_snapshot
        ):
            raise TargetVersionConflict
        envelope = _UndoSnapshot.model_validate(current.snapshot)
        if envelope.source_candidate_id != request.candidate_id:
            raise CandidateActionNotUndoableError
        run = GenerationRunRepository(session).get(envelope.source_generation_run_id)
        assert run is not None
        if _receipt(run).get("target_before") != request.restore_snapshot:
            raise CandidateActionNotUndoableError
        restore = request.restore_snapshot.get("working_draft_id")
        if restore is not None and (not isinstance(restore, str) or not restore):
            raise CandidateActionNotUndoableError
        drafts = SceneDraftRepository(session)
        imported = drafts.get(str(envelope.working_draft_id))
        if imported is None or imported.parent_draft_id != restore:
            raise CandidateActionNotUndoableError
        if restore is not None:
            previous = drafts.get(restore)
            if (
                previous is None
                or previous.story_scene_id != envelope.story_scene_id
                or previous.project_id != envelope.project_id
            ):
                raise CandidateActionNotUndoableError
        scenes = StorySceneRepository(session)
        if not scenes.advance_working_draft_cas(
            envelope.story_scene_id,
            expected_working_draft_id=envelope.working_draft_id,
            expected_accepted_draft_id=envelope.accepted_draft_id,
            working_draft_id=restore,
            updated_at=utc_now_iso(),
            expected_revision=envelope.scene_revision,
        ):
            raise TargetVersionConflict
        session.expire_all()
        scene = scenes.get(envelope.story_scene_id)
        assert scene is not None
        revision = scenes.get_revision(scene.id)
        if revision != envelope.scene_revision + 1:
            raise TargetVersionConflict
        return VersionedTargetSnapshot(
            target_type="story_scene",
            target_id=scene.id,
            snapshot=story_undo_snapshot(
                scene,
                revision=revision,
                scene_card_version_id=envelope.scene_card_version_id,
                candidate_id=envelope.source_candidate_id,
                generation_run_id=envelope.source_generation_run_id,
            ),
            version_ref=f"story-scene-revision:{scene.id}:{revision}",
        )


__all__ = ["StoryCandidateUndoTargetAdapter", "supports_story_undo"]
