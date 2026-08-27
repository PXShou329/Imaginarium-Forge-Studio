"""Generation input snapshot (Gate A, A3-01 §7.4).

The original defect: ``SceneGenerationService.generate()`` accepted a scene
ID, a Scene Card version ID, a ContextPackage, and an eligibility fingerprint
as four independent arguments, and never proved they belonged together. A
direct call could pair Scene 1 with Scene 2's card, invent planning version
IDs, and store the result as a completed run.

The fix is to make "these inputs belong together" a *thing* rather than an
assumption. The orchestration service resolves every input from persisted
data, freezes it here, and hashes it. The low-level provider adapter accepts
a snapshot, not loose arguments, so there is no longer a call shape that can
express a mismatched pair.

This mirrors the Phase 2 ``ResolutionInputSnapshot`` (A2-02) deliberately:
the same discipline that binds prompt compilation to its resolved inputs now
binds story generation to its resolved inputs.
"""

from __future__ import annotations

import hashlib
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from imaginarium_forge.canonical import canonical_json
from imaginarium_forge.domain.prompt.content_mode import ContentMode
from imaginarium_forge.domain.story.context import (
    ContextBudgetSnapshot,
    ContextSourceRef,
)
from imaginarium_forge.domain.story.short_story import (
    CompleteStoryBrief,
    StoryGenerationPurpose,
)

#: bumped when the snapshot FIELD SET changes (not when values change)
#: v3 added typed context sources; v4 added the exact output contract used by
#: structured adult generation; v5 records the exact per-section context
#: budget; v6 pins the accepted story-memory projection; v7 records the prose
#: unit purpose and the complete bounded story brief needed for raw-off replay;
#: v8 records typed per-run author prohibitions for byte-faithful replay.
#: All travel inside
#: the existing hashed JSON, so databases need no generation-run column change.
GENERATION_SNAPSHOT_SCHEMA_VERSION = "phase6-generation-input-v8"

#: bumped when the cancellation contract changes
CANCELLATION_CONTRACT_VERSION = "cooperative-event-v1"


class PlanningMode(StrEnum):
    """A3-R12: which planning material a run was permitted to use.

    Replaces the former ``allow_unaccepted_planning`` boolean, which was
    declared on the request object and never read by anything — so a caller
    could not actually restrict generation to accepted planning material, and
    could not tell afterwards which kind had been used.
    """

    ACCEPTED = "accepted"
    PREVIEW = "preview"


class ProviderOption(BaseModel):
    """One normalized provider-specific option, as an immutable pair."""

    model_config = ConfigDict(frozen=True)

    key: str
    value: str


class GenerationOptionsSnapshot(BaseModel):
    """A3-R01/A3-R13: every behaviour-affecting provider option, frozen.

    The previous version kept these in a plain ``dict`` inside a frozen model.
    Pydantic's ``frozen=True`` blocks attribute assignment but does NOT stop
    ``snapshot.generation_options["temperature"] = 0.2`` — which was
    reproduced, succeeded, and silently changed the snapshot hash. A frozen
    model containing a mutable container is not an immutable snapshot.

    Provider extras are a canonically-sorted tuple of pairs rather than a
    mapping, so key insertion order cannot affect the hash either.
    """

    model_config = ConfigDict(frozen=True)

    temperature: float
    timeout_s: float
    stream: bool = False
    seed: int | None = None
    max_output_tokens: int | None = None
    top_p: float | None = None
    stop_sequences: tuple[str, ...] = ()
    structured_mode: bool = False
    cancellation_contract_version: str = CANCELLATION_CONTRACT_VERSION
    provider_options: tuple[ProviderOption, ...] = ()

    @field_validator("provider_options")
    @classmethod
    def _canonical_order(
        cls, value: tuple[ProviderOption, ...]
    ) -> tuple[ProviderOption, ...]:
        """Option order must never affect the hash."""
        return tuple(sorted(value, key=lambda option: (option.key, option.value)))

    @property
    def sha256(self) -> str:
        payload = canonical_json(self.model_dump(mode="json"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class ParticipantPin(BaseModel):
    """One participant, pinned to the exact Character Version used."""

    model_config = ConfigDict(frozen=True)

    character_id: str
    character_version_id: str
    role: str = ""
    is_pov: bool = False


class GenerationInputSnapshot(BaseModel):
    """Every resolved input for one generation or revision, frozen together.

    Two snapshots with the same ``sha256`` describe the same generation
    inputs; any difference at all — a different card version, a different
    participant version, a different content mode, a different model —
    produces a different hash.
    """

    model_config = ConfigDict(frozen=True)

    #: A3-S8.1 §15: the exact typed sources this context was assembled
    #: from. Stored inside the existing immutable snapshot JSON, so the
    #: run's own SHA-256 covers provenance without a schema migration.
    context_source_refs: tuple[ContextSourceRef, ...] = ()
    schema_version: str = GENERATION_SNAPSHOT_SCHEMA_VERSION
    generation_purpose: StoryGenerationPurpose = StoryGenerationPurpose.SCENE
    complete_story_brief: CompleteStoryBrief | None = None
    complete_story_brief_sha256: str = ""
    structured_must_avoid: tuple[str, ...] = ()

    # ---- ownership chain -------------------------------------------
    project_id: str
    story_scene_id: str
    scene_card_version_id: str

    # ---- planning versions (true VERSION ids, never parent IDs) -----
    requirement_version_id: str | None = None
    bible_version_id: str | None = None
    outline_version_id: str | None = None
    chapter_plan_version_id: str | None = None

    # ---- canon ------------------------------------------------------
    participants: tuple[ParticipantPin, ...] = ()
    pov_character_version_id: str | None = None

    # ---- policy -----------------------------------------------------
    content_mode: ContentMode = ContentMode.GENERAL
    eligibility_evaluation_ids: tuple[str, ...] = ()
    eligibility_fingerprint: str = ""

    # ---- context ----------------------------------------------------
    context_fingerprint: str = ""
    context_schema_version: str = ""
    context_contract_version: str = ""
    context_budget_policy_version: str = ""
    #: Exact author-selected context space.  The nested schema is independently
    #: versioned, canonical and immutable, so a historical run can reproduce
    #: the same section trimming without relying on current UI defaults.
    context_budget_snapshot: ContextBudgetSnapshot = ContextBudgetSnapshot()
    #: Immutable IDs plus fingerprint make accepted story memory reproducible
    #: even after a scene switches its accepted draft. Defaults keep v1-v5
    #: snapshots readable and represent "story memory was not part of input".
    memory_projection_fingerprint: str = ""
    memory_proposal_ids: tuple[str, ...] = ()
    memory_entry_ids: tuple[str, ...] = ()
    memory_gap_scene_ids: tuple[str, ...] = ()
    stale_memory_proposal_ids: tuple[str, ...] = ()

    # ---- output contract -------------------------------------------
    output_contract_version: str = ""

    # ---- provider ---------------------------------------------------
    provider: str = ""
    model: str = ""
    options: GenerationOptionsSnapshot

    # ---- planning policy (A3-R02 / A3-R12) --------------------------
    planning_mode: PlanningMode = PlanningMode.ACCEPTED
    #: proves the six planning links formed ONE coherent chain
    planning_chain_fingerprint: str = ""

    # ---- rendering (A3-R10) ----------------------------------------
    renderer_version: str = ""
    system_message_sha256: str = ""
    user_message_sha256: str = ""

    # ---- revision provenance (A3-02) --------------------------------
    run_kind: str = "generation"
    parent_draft_id: str | None = None
    revision_request_json: str = "{}"
    revision_request_fingerprint: str = ""
    #: A3-R03: an explicit rebase names the run whose chain it replaced
    rebased_from_generation_run_id: str | None = None
    is_recovery_rebase: bool = False
    missing_source_version_ids: tuple[str, ...] = ()

    @field_validator("structured_must_avoid")
    @classmethod
    def _bounded_structured_must_avoid(
        cls, values: tuple[str, ...]
    ) -> tuple[str, ...]:
        if len(values) > 32:
            raise ValueError("structured_must_avoid must contain at most 32 items")
        if any(
            not value
            or value != " ".join(value.strip().split())
            or len(value) > 120
            for value in values
        ):
            raise ValueError(
                "structured_must_avoid items must be normalized nonblank text "
                "of at most 120 characters"
            )
        if len({value.casefold() for value in values}) != len(values):
            raise ValueError("structured_must_avoid items must not repeat")
        return values

    @model_validator(mode="after")
    def _purpose_matches_brief(self) -> GenerationInputSnapshot:
        if self.generation_purpose is StoryGenerationPurpose.SCENE:
            if self.complete_story_brief is not None:
                raise ValueError("scene snapshot must not carry complete-story brief")
            if self.complete_story_brief_sha256:
                raise ValueError("scene snapshot must not carry complete-story brief hash")
            return self
        if self.complete_story_brief is None:
            raise ValueError("complete-story snapshot requires the exact brief")
        if self.complete_story_brief_sha256 != self.complete_story_brief.sha256:
            raise ValueError("complete-story brief SHA-256 does not match snapshot")
        return self

    def canonical_payload(self) -> str:
        """Deterministic serialization — the hash input."""
        return canonical_json(self.model_dump(mode="json"))

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.canonical_payload().encode("utf-8")).hexdigest()

    @property
    def character_version_ids(self) -> tuple[str, ...]:
        return tuple(p.character_version_id for p in self.participants)

    def matches(self, other: GenerationInputSnapshot) -> bool:
        return self.sha256 == other.sha256
