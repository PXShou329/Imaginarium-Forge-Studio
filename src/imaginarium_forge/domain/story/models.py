"""Story Studio domain models (spec §34–§37).

Pure data + validation; no I/O, no provider calls, no DB. Every model is
frozen so a version snapshot cannot be mutated after it is created.

These models carry *narrative planning* state only. ``entry_state`` and
``exit_state`` remain author-facing notes; a separate durable story-memory
workflow may derive a proposal from the exact accepted draft, but nothing is
applied until the author reviews and accepts that proposal.
"""

from __future__ import annotations

import hashlib
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from imaginarium_forge.canonical import canonical_json
from imaginarium_forge.domain.prompt.content_mode import ContentMode

# Bounds mirror the parser discipline of A2-10: nothing unbounded reaches
# storage or a provider prompt.
MAX_ITEMS = 32
MAX_BEATS = 24
MAX_SHORT = 200
MAX_MEDIUM = 1000
MAX_LONG = 4000

_Short = Annotated[str, StringConstraints(max_length=MAX_SHORT)]
_Medium = Annotated[str, StringConstraints(max_length=MAX_MEDIUM)]
_Long = Annotated[str, StringConstraints(max_length=MAX_LONG)]


def _fingerprint(model: BaseModel) -> str:
    return hashlib.sha256(canonical_json(model.model_dump(mode="json")).encode("utf-8")).hexdigest()


# ============================================================ requirement
class NarrativePov(StrEnum):
    FIRST = "first"
    SECOND = "second"
    THIRD_LIMITED = "third_limited"
    THIRD_OMNISCIENT = "third_omniscient"


class NarrativeTense(StrEnum):
    PAST = "past"
    PRESENT = "present"


class IntensityLevel(StrEnum):
    """Author-declared intensity for a content dimension (§34).

    These are *craft* dials, not permissions: `explicit` violence still obeys
    every Canon rule, and sexual content additionally requires an adult
    content mode plus a verified adult character version.
    """

    NONE = "none"
    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"
    EXPLICIT = "explicit"


class StoryRequirement(BaseModel):
    """Quick fields + detailed fields (§34). Only `concept` is mandatory."""

    model_config = ConfigDict(frozen=True)

    # ---- quick ------------------------------------------------------
    concept: _Long = ""
    logline: _Medium = ""
    synopsis: _Long = ""
    opening_hook: _Medium = ""
    genre: _Short = ""
    target_length: _Short = ""
    tone: _Short = ""
    # ---- detailed ---------------------------------------------------
    audience: _Short = ""
    setting: _Medium = ""
    time_period: _Short = ""
    protagonist_notes: _Medium = ""
    antagonist_notes: _Medium = ""
    central_conflict: _Medium = ""
    themes: tuple[_Short, ...] = Field(default=(), max_length=MAX_ITEMS)
    must_include: tuple[_Short, ...] = Field(default=(), max_length=MAX_ITEMS)
    must_avoid: tuple[_Short, ...] = Field(default=(), max_length=MAX_ITEMS)
    pov: NarrativePov = NarrativePov.THIRD_LIMITED
    tense: NarrativeTense = NarrativeTense.PAST
    pacing: _Short = ""
    prose_style_notes: _Medium = ""
    dialogue_density: _Short = ""
    violence_intensity: IntensityLevel = IntensityLevel.NONE
    horror_intensity: IntensityLevel = IntensityLevel.NONE
    intimacy_intensity: IntensityLevel = IntensityLevel.NONE
    ending_preference: _Medium = ""
    content_mode: ContentMode = ContentMode.GENERAL
    inspiration_notes: _Medium = ""

    def fingerprint(self) -> str:
        return _fingerprint(self)


# ================================================================= bible
class BibleCharacterEntry(BaseModel):
    """A Story Bible character slot.

    ``canon_character_id``/``canon_character_version_id`` link to Phase 1
    Canon when the character is a Canon character; a Bible-only character
    (a walk-on, a narrator) may leave them empty. The Bible NEVER redefines
    Canon: it references it.
    """

    model_config = ConfigDict(frozen=True)

    name: _Short
    role: _Short = ""
    canon_character_id: str = ""
    canon_character_version_id: str = ""
    biography: _Long = ""
    goal: _Medium = ""
    motivation: _Medium = ""
    fear: _Medium = ""
    secret: _Medium = ""
    internal_conflict: _Medium = ""
    voice_notes: _Medium = ""
    relationships: tuple[_Short, ...] = Field(default=(), max_length=MAX_ITEMS)
    arc_start: _Medium = ""
    arc_turning_points: tuple[_Medium, ...] = Field(
        default=(), max_length=MAX_ITEMS
    )
    arc_end: _Medium = ""

    @model_validator(mode="after")
    def _canon_link_is_a_complete_pair(self) -> BibleCharacterEntry:
        """A3-15: a Canon link is the PAIR (character, version).

        Half a link is worse than none: a bare version ID cannot be checked
        against the character it claims to belong to, and a bare character ID
        silently re-resolves to whatever version happens to be current.
        """
        has_character = bool(self.canon_character_id)
        has_version = bool(self.canon_character_version_id)
        if has_character != has_version:
            raise ValueError(
                "Canon 連結必須成對提供 canon_character_id 與 "
                "canon_character_version_id（或兩者皆留空）"
            )
        return self


class NarrativeContract(BaseModel):
    """Author-declared narrative rules the generator must obey (§35)."""

    model_config = ConfigDict(frozen=True)

    prohibited_phrases: tuple[_Short, ...] = Field(default=(), max_length=MAX_ITEMS)
    forbidden_reveals: tuple[_Medium, ...] = Field(default=(), max_length=MAX_ITEMS)
    tone_rules: tuple[_Short, ...] = Field(default=(), max_length=MAX_ITEMS)
    pov_rules: tuple[_Short, ...] = Field(default=(), max_length=MAX_ITEMS)
    style_rules: tuple[_Short, ...] = Field(default=(), max_length=MAX_ITEMS)


class StoryBible(BaseModel):
    """Identity / world / characters / contract / promise (§35)."""

    model_config = ConfigDict(frozen=True)

    # identity
    title: _Short = ""
    logline: _Medium = ""
    genre: _Short = ""
    tone: _Short = ""
    # world
    world_rules: tuple[_Medium, ...] = Field(default=(), max_length=MAX_ITEMS)
    locations: tuple[_Short, ...] = Field(default=(), max_length=MAX_ITEMS)
    social_context: _Medium = ""
    technology_or_magic: _Medium = ""
    # characters
    characters: tuple[BibleCharacterEntry, ...] = Field(default=(), max_length=MAX_ITEMS)
    # contract + promise
    narrative_contract: NarrativeContract = NarrativeContract()
    story_promise: _Medium = ""
    reader_experience_goal: _Medium = ""

    def fingerprint(self) -> str:
        return _fingerprint(self)

    def canon_character_version_ids(self) -> tuple[str, ...]:
        return tuple(
            c.canon_character_version_id for c in self.characters if c.canon_character_version_id
        )


# =============================================================== outline
class StructureProfile(StrEnum):
    """Seven offered structures (§36). None is forced; `freeform` opts out."""

    THREE_ACT = "three_act"
    FOUR_ACT = "four_act"
    HEROS_JOURNEY = "heros_journey"
    SAVE_THE_CAT = "save_the_cat"
    KISHOTENKETSU = "kishotenketsu"
    SEVEN_POINT = "seven_point"
    FREEFORM = "freeform"


class OutlineAct(BaseModel):
    model_config = ConfigDict(frozen=True)

    # A3-13: ordinals are 1-based; 0 and negatives are not "empty", they are
    # invalid, and silently accepting them corrupts every downstream ordering.
    act_number: int = Field(default=1, ge=1)
    name: _Short
    purpose: _Medium = ""
    goal: _Medium = ""
    turning_point: _Medium = ""
    chapter_numbers: tuple[Annotated[int, Field(ge=1)], ...] = Field(
        default=(), max_length=MAX_ITEMS
    )

    @model_validator(mode="after")
    def _chapter_numbers_unique(self) -> OutlineAct:
        if len(set(self.chapter_numbers)) != len(self.chapter_numbers):
            raise ValueError("章節編號不可重複")
        return self


class StoryOutline(BaseModel):
    model_config = ConfigDict(frozen=True)

    structure_profile: StructureProfile = StructureProfile.THREE_ACT
    acts: tuple[OutlineAct, ...] = Field(default=(), max_length=MAX_ITEMS)
    reveals: tuple[_Medium, ...] = Field(default=(), max_length=MAX_ITEMS)
    withheld: tuple[_Medium, ...] = Field(default=(), max_length=MAX_ITEMS)
    arc_beats: tuple[_Medium, ...] = Field(default=(), max_length=MAX_ITEMS)
    foreshadowing: tuple[_Medium, ...] = Field(default=(), max_length=MAX_ITEMS)
    ending_state: _Medium = ""

    def fingerprint(self) -> str:
        return _fingerprint(self)


class ChapterPlan(BaseModel):
    """Per-chapter plan (§36)."""

    model_config = ConfigDict(frozen=True)

    chapter_number: int = Field(default=1, ge=1)
    title: _Short = ""
    purpose: _Medium = ""
    pov_character_id: str = ""
    goal: _Medium = ""
    conflict: _Medium = ""
    outcome: _Medium = ""
    scene_intentions: tuple[_Medium, ...] = Field(default=(), max_length=MAX_ITEMS)
    reveals: tuple[_Medium, ...] = Field(default=(), max_length=MAX_ITEMS)
    forbidden_reveals: tuple[_Medium, ...] = Field(default=(), max_length=MAX_ITEMS)
    target_word_count: int = Field(default=0, ge=0)

    def fingerprint(self) -> str:
        return _fingerprint(self)


# ============================================================ scene card
class SceneGoal(BaseModel):
    model_config = ConfigDict(frozen=True)

    protagonist: _Medium = ""
    narrative: _Medium = ""


class SceneConflict(BaseModel):
    model_config = ConfigDict(frozen=True)

    external: _Medium = ""
    internal: _Medium = ""


class SceneEntryState(BaseModel):
    """Author NOTES about where characters start (§37).

    This value is not a simulation and is never mutated by earlier scenes.
    Accepted story memory is supplied beside it as inspectable context; any
    mismatch therefore remains visible instead of silently rewriting the card.
    """

    model_config = ConfigDict(frozen=True)

    emotion: _Medium = ""
    known_facts: tuple[_Medium, ...] = Field(default=(), max_length=MAX_ITEMS)
    injuries: tuple[_Short, ...] = Field(default=(), max_length=MAX_ITEMS)
    inventory_notes: tuple[_Short, ...] = Field(default=(), max_length=MAX_ITEMS)


class SceneExitState(BaseModel):
    model_config = ConfigDict(frozen=True)

    emotion: _Medium = ""
    new_knowledge: tuple[_Medium, ...] = Field(default=(), max_length=MAX_ITEMS)
    unresolved: tuple[_Medium, ...] = Field(default=(), max_length=MAX_ITEMS)


class SceneParticipant(BaseModel):
    """A3-03: an EXACT Canon character version taking part in a scene.

    Storing only ``character_id`` was the original defect: a later change to
    the character's current version silently altered which character an old
    Scene Card described, and adult eligibility was then re-evaluated against
    a version the author never approved. The version is pinned here.
    """

    model_config = ConfigDict(frozen=True)

    character_id: str = Field(min_length=1, max_length=MAX_SHORT)
    character_version_id: str = Field(min_length=1, max_length=MAX_SHORT)
    role: _Short = ""


class SceneCard(BaseModel):
    """The generation unit (§37) — one scene's complete specification."""

    model_config = ConfigDict(frozen=True)

    pov_character: SceneParticipant | None = None
    participants: tuple[SceneParticipant, ...] = Field(default=(), max_length=MAX_ITEMS)
    location: _Short = ""
    start_time: _Short = ""
    duration_minutes: int = Field(default=0, ge=0)
    scene_goal: SceneGoal = SceneGoal()
    conflict: SceneConflict = SceneConflict()
    entry_state: SceneEntryState = SceneEntryState()
    beats: tuple[_Medium, ...] = Field(default=(), max_length=MAX_BEATS)
    turning_point: _Medium = ""
    exit_state: SceneExitState = SceneExitState()
    forbidden_reveals: tuple[_Medium, ...] = Field(default=(), max_length=MAX_ITEMS)
    must_include: tuple[_Medium, ...] = Field(default=(), max_length=MAX_ITEMS)
    avoid: tuple[_Medium, ...] = Field(default=(), max_length=MAX_ITEMS)
    target_word_count: int = Field(default=0, ge=0)
    content_mode: ContentMode = ContentMode.GENERAL

    @model_validator(mode="after")
    def _participants_are_consistent(self) -> SceneCard:
        """A3-03 §9.3: no duplicate characters; POV must be a participant."""
        ids = [p.character_id for p in self.participants]
        if len(set(ids)) != len(ids):
            raise ValueError("同一角色不可在參與者名單中重複出現")
        pov_is_stranger = (
            self.pov_character is not None
            and bool(self.participants)
            and self.pov_character not in self.participants
        )
        if pov_is_stranger:
            raise ValueError("POV 角色必須是參與者之一（含相同的 character_version_id）")
        return self

    def fingerprint(self) -> str:
        return _fingerprint(self)

    @property
    def participating_character_ids(self) -> tuple[str, ...]:
        """Read-only view for callers that only need identity, not versions."""
        return tuple(p.character_id for p in self.participants)

    @property
    def participating_character_version_ids(self) -> tuple[str, ...]:
        """The EXACT versions this card was written for (A3-03)."""
        return tuple(p.character_version_id for p in self.participants)

    @property
    def pov_character_id(self) -> str:
        return "" if self.pov_character is None else self.pov_character.character_id

    @property
    def pov_character_version_id(self) -> str:
        return "" if self.pov_character is None else self.pov_character.character_version_id

    @property
    def adult_participants_required(self) -> bool:
        """Sexual modes require EVERY participant to be a verified adult
        Canon character version (§33) — checked by the service layer."""
        from imaginarium_forge.domain.prompt.content_mode import derives_adult

        return derives_adult(self.content_mode)
