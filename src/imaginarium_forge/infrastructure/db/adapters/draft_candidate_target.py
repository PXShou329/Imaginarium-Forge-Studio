"""Versioned candidate adapter for the four existing standalone draft tables.

The draft row remains the only formal aggregate.  Immutable before/after
versions live in ``creative_candidate_actions`` in the same transaction; this
adapter only validates the existing domain model and advances its optimistic
``updated_at`` revision token.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import cast

from pydantic import BaseModel
from sqlalchemy import update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from imaginarium_forge.application.services.generation.candidate_application import (
    TargetSnapshotRequest,
    TargetVersionConflict,
    TargetVersionRequest,
    VersionedTargetSnapshot,
)
from imaginarium_forge.canonical import canonical_json
from imaginarium_forge.domain.character.biography_draft import CharacterBiographyDraft
from imaginarium_forge.domain.common.enums import RecordStatus
from imaginarium_forge.domain.creative.world_seed_draft import WorldSeedDraft
from imaginarium_forge.domain.generation.durable import (
    CandidateActionKind,
    sha256_json,
    sha256_text,
)
from imaginarium_forge.domain.prompt.scratch_draft import PromptScratchDraft
from imaginarium_forge.domain.story.fragment_draft import StoryFragmentDraft
from imaginarium_forge.infrastructure.db.models.orm import (
    CharacterBiographyDraftRow,
    PromptScratchDraftRow,
    StoryFragmentDraftRow,
    WorldSeedDraftRow,
)
from imaginarium_forge.infrastructure.db.repositories.character_biography_drafts import (
    CharacterBiographyDraftRepository,
)
from imaginarium_forge.infrastructure.db.repositories.prompt_scratch_drafts import (
    PromptScratchDraftRepository,
)
from imaginarium_forge.infrastructure.db.repositories.story_fragment_drafts import (
    StoryFragmentDraftRepository,
)
from imaginarium_forge.infrastructure.db.repositories.world_seed_drafts import (
    WorldSeedDraftRepository,
)

STORY_FRAGMENT_DRAFT_TARGET = "story_fragment_draft"
CHARACTER_BIOGRAPHY_DRAFT_TARGET = "character_biography_draft"
WORLD_SEED_DRAFT_TARGET = "world_seed_draft"
PROMPT_SCRATCH_DRAFT_TARGET = "prompt_scratch_draft"

SUPPORTED_DRAFT_TARGETS = frozenset(
    {
        STORY_FRAGMENT_DRAFT_TARGET,
        CHARACTER_BIOGRAPHY_DRAFT_TARGET,
        WORLD_SEED_DRAFT_TARGET,
        PROMPT_SCRATCH_DRAFT_TARGET,
    }
)

type _Draft = (
    StoryFragmentDraft
    | CharacterBiographyDraft
    | WorldSeedDraft
    | PromptScratchDraft
)
Clock = Callable[[], str]


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _next_revision(previous: str, clock: Clock) -> str:
    candidate = clock()
    try:
        base = datetime.fromisoformat(previous)
        current = datetime.fromisoformat(candidate)
    except ValueError:
        return candidate if candidate != previous else f"{previous}:next"
    return (base + timedelta(microseconds=1)).isoformat() if current <= base else candidate


def _version_ref(snapshot: dict[str, object]) -> str:
    return f"draft-version:{sha256_json(snapshot)}"


def _copy_id(idempotency_key: str) -> str:
    return f"refinement-copy-{sha256_text(idempotency_key)[:32]}"


def _copy_title(value: object) -> str:
    title = str(value).strip()
    return (f"{title}（精修副本）" if title else "精修副本")[:200]


class DraftCandidateTargetAdapter:
    """Apply a reviewed candidate through the real draft aggregate schema."""

    def __init__(self, *, clock: Clock = _utc_now) -> None:
        self._clock = clock

    def read_snapshot(
        self,
        session: Session,
        request: TargetSnapshotRequest,
    ) -> VersionedTargetSnapshot:
        if request.target_type not in SUPPORTED_DRAFT_TARGETS or request.target_id is None:
            raise TargetVersionConflict
        draft = self._get(session, request.target_type, request.target_id)
        if draft is None or draft.status is not RecordStatus.ACTIVE:
            raise TargetVersionConflict
        snapshot = self._snapshot(draft)
        return VersionedTargetSnapshot(
            target_type=request.target_type,
            target_id=draft.id,
            snapshot=snapshot,
            version_ref=_version_ref(snapshot),
        )

    def create_version(
        self,
        session: Session,
        request: TargetVersionRequest,
    ) -> VersionedTargetSnapshot:
        if request.target_type not in SUPPORTED_DRAFT_TARGETS or request.target_id is None:
            raise TargetVersionConflict
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
            or current.version_ref != request.expected_version_ref
        ):
            raise TargetVersionConflict

        candidate_snapshot = self._candidate_snapshot(request)
        if request.action_kind is CandidateActionKind.SAVE_AS_NEW:
            result = self._save_as_new(session, request, candidate_snapshot)
        else:
            result = self._advance_existing(
                session,
                request,
                current.snapshot,
                candidate_snapshot,
            )
        session.flush()
        snapshot = self._snapshot(result)
        return VersionedTargetSnapshot(
            target_type=request.target_type,
            target_id=result.id,
            snapshot=snapshot,
            version_ref=_version_ref(snapshot),
        )

    def candidate_snapshot(
        self,
        base_snapshot: dict[str, object],
        *,
        target_type: str,
        field_name: str,
        candidate_text: str,
    ) -> dict[str, object]:
        """Return a domain-valid full snapshot containing one candidate field."""

        if target_type not in SUPPORTED_DRAFT_TARGETS:
            raise TargetVersionConflict
        mutable = self._mutable_fields(target_type)
        if field_name not in mutable:
            raise TargetVersionConflict
        result = dict(base_snapshot)
        if target_type == WORLD_SEED_DRAFT_TARGET and field_name in {
            "world_rules",
            "locations",
            "themes",
        }:
            result[field_name] = [
                line.strip() for line in candidate_text.splitlines() if line.strip()
            ]
        else:
            result[field_name] = candidate_text
        if "generation_mode" in result:
            result["generation_mode"] = "manual"
            result["generation_seed"] = ""
        self._validate(target_type, result)
        return result

    @staticmethod
    def field_text(snapshot: dict[str, object], *, field_name: str) -> str:
        value = snapshot.get(field_name)
        if isinstance(value, list):
            if not all(isinstance(item, str) for item in value):
                raise TargetVersionConflict
            return "\n".join(value)
        if not isinstance(value, str):
            raise TargetVersionConflict
        return value

    def _advance_existing(
        self,
        session: Session,
        request: TargetVersionRequest,
        current: dict[str, object],
        candidate: dict[str, object],
    ) -> _Draft:
        if request.action_kind is CandidateActionKind.UNDO:
            if request.restore_snapshot is None:
                raise TargetVersionConflict
            desired = dict(request.restore_snapshot)
        elif request.action_kind is CandidateActionKind.APPLY_ALL:
            desired = candidate
        elif request.action_kind in {
            CandidateActionKind.APPLY_FIELD,
            CandidateActionKind.APPLY_SELECTION,
        }:
            field_name = request.selection.get("field")
            if not isinstance(field_name, str) or field_name not in self._mutable_fields(
                request.target_type
            ):
                raise TargetVersionConflict
            desired = dict(current)
            desired[field_name] = candidate[field_name]
        else:
            raise TargetVersionConflict

        desired["id"] = current["id"]
        desired["created_at"] = current["created_at"]
        desired["status"] = current["status"]
        desired["updated_at"] = _next_revision(str(current["updated_at"]), self._clock)
        if "generation_mode" in desired:
            desired["generation_mode"] = "manual"
            desired["generation_seed"] = ""
        revised = self._validate(request.target_type, desired)
        if not self._update_cas(
            session,
            request.target_type,
            revised,
            expected_updated_at=str(current["updated_at"]),
        ):
            raise TargetVersionConflict
        return revised

    def _save_as_new(
        self,
        session: Session,
        request: TargetVersionRequest,
        candidate: dict[str, object],
    ) -> _Draft:
        now = self._clock()
        desired = dict(candidate)
        desired.update(
            {
                "id": _copy_id(request.idempotency_key),
                "title": _copy_title(candidate.get("title", "")),
                "status": RecordStatus.ACTIVE.value,
                "created_at": now,
                "updated_at": now,
            }
        )
        if "generation_mode" in desired:
            desired["generation_mode"] = "manual"
            desired["generation_seed"] = ""
        copy = self._validate(request.target_type, desired)
        self._add(session, request.target_type, copy)
        return copy

    @staticmethod
    def _snapshot(draft: _Draft) -> dict[str, object]:
        return cast(dict[str, object], draft.model_dump(mode="json"))

    @staticmethod
    def _get(session: Session, target_type: str, target_id: str) -> _Draft | None:
        if target_type == STORY_FRAGMENT_DRAFT_TARGET:
            return StoryFragmentDraftRepository(session).get(target_id)
        if target_type == CHARACTER_BIOGRAPHY_DRAFT_TARGET:
            return CharacterBiographyDraftRepository(session).get(target_id)
        if target_type == WORLD_SEED_DRAFT_TARGET:
            return WorldSeedDraftRepository(session).get(target_id)
        if target_type == PROMPT_SCRATCH_DRAFT_TARGET:
            return PromptScratchDraftRepository(session).get(target_id)
        raise TargetVersionConflict

    @staticmethod
    def _validate(target_type: str, snapshot: dict[str, object]) -> _Draft:
        model: type[BaseModel]
        if target_type == STORY_FRAGMENT_DRAFT_TARGET:
            model = StoryFragmentDraft
        elif target_type == CHARACTER_BIOGRAPHY_DRAFT_TARGET:
            model = CharacterBiographyDraft
        elif target_type == WORLD_SEED_DRAFT_TARGET:
            model = WorldSeedDraft
        elif target_type == PROMPT_SCRATCH_DRAFT_TARGET:
            model = PromptScratchDraft
        else:
            raise TargetVersionConflict
        try:
            return cast(_Draft, model.model_validate(snapshot))
        except Exception:
            raise TargetVersionConflict from None

    @staticmethod
    def _add(session: Session, target_type: str, draft: _Draft) -> None:
        if target_type == STORY_FRAGMENT_DRAFT_TARGET:
            StoryFragmentDraftRepository(session).add(cast(StoryFragmentDraft, draft))
        elif target_type == CHARACTER_BIOGRAPHY_DRAFT_TARGET:
            CharacterBiographyDraftRepository(session).add(
                cast(CharacterBiographyDraft, draft)
            )
        elif target_type == WORLD_SEED_DRAFT_TARGET:
            WorldSeedDraftRepository(session).add(cast(WorldSeedDraft, draft))
        elif target_type == PROMPT_SCRATCH_DRAFT_TARGET:
            PromptScratchDraftRepository(session).add(cast(PromptScratchDraft, draft))
        else:
            raise TargetVersionConflict

    @staticmethod
    def _mutable_fields(target_type: str) -> frozenset[str]:
        fields: dict[str, frozenset[str]] = {
            STORY_FRAGMENT_DRAFT_TARGET: frozenset(
                {"title", "fragment_text", "context_notes", "tags"}
            ),
            CHARACTER_BIOGRAPHY_DRAFT_TARGET: frozenset(
                {
                    "title",
                    "character_name",
                    "character_details",
                    "character_image_prompt_en",
                    "personal_story",
                    "background_image_prompt_en",
                    "notes",
                }
            ),
            WORLD_SEED_DRAFT_TARGET: frozenset(
                {
                    "title",
                    "setting",
                    "time_period",
                    "world_rules",
                    "locations",
                    "social_context",
                    "technology_or_magic",
                    "central_conflict",
                    "themes",
                }
            ),
            PROMPT_SCRATCH_DRAFT_TARGET: frozenset(
                {
                    "title",
                    "character_name",
                    "character_image_prompt_en",
                    "background_image_prompt_en",
                    "notes",
                }
            ),
        }
        try:
            return fields[target_type]
        except KeyError:
            raise TargetVersionConflict from None

    @staticmethod
    def _candidate_snapshot(request: TargetVersionRequest) -> dict[str, object]:
        if request.candidate_json is None:
            raise TargetVersionConflict
        return dict(request.candidate_json)

    @staticmethod
    def _update_cas(
        session: Session,
        target_type: str,
        draft: _Draft,
        *,
        expected_updated_at: str,
    ) -> bool:
        values = DraftCandidateTargetAdapter._row_values(target_type, draft)
        row_type: type[
            StoryFragmentDraftRow
            | CharacterBiographyDraftRow
            | WorldSeedDraftRow
            | PromptScratchDraftRow
        ]
        if target_type == STORY_FRAGMENT_DRAFT_TARGET:
            row_type = StoryFragmentDraftRow
        elif target_type == CHARACTER_BIOGRAPHY_DRAFT_TARGET:
            row_type = CharacterBiographyDraftRow
        elif target_type == WORLD_SEED_DRAFT_TARGET:
            row_type = WorldSeedDraftRow
        elif target_type == PROMPT_SCRATCH_DRAFT_TARGET:
            row_type = PromptScratchDraftRow
        else:
            raise TargetVersionConflict
        result = cast(
            CursorResult[object],
            session.execute(
                update(row_type)
                .where(
                    row_type.id == draft.id,
                    row_type.status == RecordStatus.ACTIVE.value,
                    row_type.updated_at == expected_updated_at,
                )
                .values(**values)
            ),
        )
        return result.rowcount == 1

    @staticmethod
    def _row_values(target_type: str, draft: _Draft) -> dict[str, object]:
        payload = draft.model_dump(mode="json")
        for immutable in ("id", "created_at"):
            payload.pop(immutable, None)
        if target_type == STORY_FRAGMENT_DRAFT_TARGET:
            payload["tags_json"] = canonical_json(payload.pop("tags"))
        elif target_type == WORLD_SEED_DRAFT_TARGET:
            for field_name in ("world_rules", "locations", "themes"):
                payload[f"{field_name}_json"] = canonical_json(payload.pop(field_name))
        return cast(dict[str, object], payload)


__all__ = [
    "CHARACTER_BIOGRAPHY_DRAFT_TARGET",
    "PROMPT_SCRATCH_DRAFT_TARGET",
    "STORY_FRAGMENT_DRAFT_TARGET",
    "SUPPORTED_DRAFT_TARGETS",
    "WORLD_SEED_DRAFT_TARGET",
    "DraftCandidateTargetAdapter",
]
