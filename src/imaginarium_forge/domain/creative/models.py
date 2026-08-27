"""Pure contracts for starting a creation from one guided brief.

The Launchpad deliberately composes the existing Canon, Story and Prompt
aggregates.  These models describe author intent; they never grant mature
content eligibility and they contain no database or provider behaviour.
"""

from __future__ import annotations

import hashlib
import re
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationInfo,
    field_validator,
    model_validator,
)

from imaginarium_forge.canonical import canonical_json
from imaginarium_forge.domain.character.gender import (
    CharacterGender,
    strip_gender_prompt_tokens,
)
from imaginarium_forge.domain.character.presentation_cues import detect_presentation_cues
from imaginarium_forge.domain.policy import AGE_POLICY
from imaginarium_forge.domain.prompt.content_mode import (
    ContentMode,
    derives_adult,
    preflight_content_mode,
)
from imaginarium_forge.domain.story.models import (
    IntensityLevel,
    NarrativePov,
    NarrativeTense,
    StructureProfile,
)

_Short = Annotated[str, StringConstraints(max_length=200)]
_Medium = Annotated[str, StringConstraints(max_length=1000)]
_Long = Annotated[str, StringConstraints(max_length=4000)]

_LEGACY_PRIMARY_SLOT_ID = "legacy-primary"


class CreationMode(StrEnum):
    """The four author-facing starting routes (not content ratings)."""

    SERIES_STORY = "series_story"
    CHARACTER_STORY = "character_story"
    CHARACTER_ONLY = "character_only"
    WORLD_ONLY = "world_only"


class GenreFamily(StrEnum):
    FANTASY = "fantasy"
    SCIENCE_FICTION = "science_fiction"
    MYSTERY_CRIME = "mystery_crime"
    THRILLER_SUSPENSE = "thriller_suspense"
    ROMANCE = "romance"
    HORROR = "horror"
    ACTION_ADVENTURE = "action_adventure"
    DRAMA_LITERARY = "drama_literary"
    HISTORICAL = "historical"
    COMEDY_SATIRE = "comedy_satire"
    SLICE_OF_LIFE = "slice_of_life"
    HYBRID_CUSTOM = "hybrid_custom"


class CreativeParticipantSource(StrEnum):
    """Where one Launchpad participant gets its Canon identity."""

    NEW_BLUEPRINT = "new_blueprint"
    EXISTING_CANON = "existing_canon"


class CharacterBlueprint(BaseModel):
    """Author-confirmed identity and visual design for one original character."""

    model_config = ConfigDict(frozen=True)

    name: _Short
    gender: CharacterGender | None = None
    explicit_age: int | None = Field(default=None, ge=0, le=200)
    user_confirmed_age: bool = False
    adult_presentation_confirmed: bool = False
    biography: _Long = ""
    personality: _Medium = ""
    voice: _Medium = ""
    motivation: _Medium = ""
    fear: _Medium = ""
    secret: _Medium = ""
    internal_conflict: _Medium = ""
    relationship_hooks: tuple[_Medium, ...] = Field(default=(), max_length=12)
    arc_start: _Medium = ""
    arc_turning_points: tuple[_Medium, ...] = Field(default=(), max_length=12)
    arc_end: _Medium = ""
    identity: _Medium = ""
    face: _Medium = ""
    hair: _Medium = ""
    eyes: _Medium = ""
    body: _Medium = ""
    distinguishing_features: tuple[_Short, ...] = Field(default=(), max_length=24)
    prohibited_mutations: tuple[_Short, ...] = Field(default=(), max_length=24)
    action: _Medium = ""
    expression: _Short = ""

    @field_validator("name")
    @classmethod
    def _name_required(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("角色名稱為必填")
        return value.strip()

    @field_validator("distinguishing_features")
    @classmethod
    def _structured_gender_owns_prompt_marker(
        cls,
        value: tuple[str, ...],
        info: ValidationInfo,
    ) -> tuple[str, ...]:
        if info.data.get("gender") is None:
            return value
        return strip_gender_prompt_tokens(value)


class CreativeParticipantDraft(BaseModel):
    """One roster slot before every character has necessarily been saved.

    A new participant carries a :class:`CharacterBlueprint`; an existing
    participant carries an exact Canon ``(character_id, version_id)`` pair.
    The two forms are deliberately exclusive so a mutable "current version"
    can never be substituted for the version the author selected.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    slot_id: _Short
    source: CreativeParticipantSource
    role: _Short = ""
    is_primary: bool = False
    blueprint: CharacterBlueprint | None = None
    character_id: _Short = ""
    character_version_id: _Short = ""

    @field_validator("slot_id")
    @classmethod
    def _slot_required(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("參與角色 slot_id 為必填")
        return value

    @field_validator("role", "character_id", "character_version_id")
    @classmethod
    def _strip_text(cls, value: str) -> str:
        return value.strip()

    @model_validator(mode="after")
    def _source_payload_is_exclusive(self) -> CreativeParticipantDraft:
        has_character = bool(self.character_id)
        has_version = bool(self.character_version_id)
        if has_character != has_version:
            raise ValueError("既有 Canon 角色必須成對提供 character_id 與 character_version_id")
        if self.source is CreativeParticipantSource.NEW_BLUEPRINT:
            if self.blueprint is None:
                raise ValueError("新角色參與者必須提供 CharacterBlueprint")
            if has_character:
                raise ValueError("新角色草稿尚未綁定 Canon ID；請在保存後建立 ParticipantPin")
        else:
            if self.blueprint is not None:
                raise ValueError("既有 Canon 參與者不可同時攜帶新角色藍圖")
            if not has_character:
                raise ValueError("既有 Canon 參與者必須綁定精確角色與版本 ID")
        return self


class ParticipantPin(BaseModel):
    """One saved participant pinned to an exact immutable Canon version."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    slot_id: _Short
    character_id: _Short
    character_version_id: _Short
    role: _Short = ""
    is_primary: bool = False

    @field_validator("slot_id", "character_id", "character_version_id")
    @classmethod
    def _required_ids(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("ParticipantPin 必須提供非空白 slot、角色與版本 ID")
        return value

    @field_validator("role")
    @classmethod
    def _strip_role(cls, value: str) -> str:
        return value.strip()


class ParticipantManifest(BaseModel):
    """The exact multi-character identity used by a save/generation command."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    participants: tuple[ParticipantPin, ...] = Field(min_length=1, max_length=24)

    @model_validator(mode="after")
    def _unique_exact_roster(self) -> ParticipantManifest:
        slot_ids = [participant.slot_id for participant in self.participants]
        if len(slot_ids) != len(set(slot_ids)):
            raise ValueError("ParticipantManifest 的 slot_id 不可重複")
        character_ids = [participant.character_id for participant in self.participants]
        if len(character_ids) != len(set(character_ids)):
            raise ValueError("ParticipantManifest 的 character_id 不可重複")
        if sum(participant.is_primary for participant in self.participants) != 1:
            raise ValueError("ParticipantManifest 必須且只能指定一位 primary 角色")
        return self

    @property
    def primary(self) -> ParticipantPin:
        return next(participant for participant in self.participants if participant.is_primary)

    @property
    def exact_pairs(self) -> tuple[tuple[str, str], ...]:
        return tuple(
            (participant.character_id, participant.character_version_id)
            for participant in self.participants
        )

    @property
    def fingerprint(self) -> str:
        payload = canonical_json(self.model_dump(mode="json"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class CreativeLaunchRequest(BaseModel):
    """One immutable, locally serializable creative brief."""

    model_config = ConfigDict(frozen=True)

    project_id: str
    mode: CreationMode
    title: _Short
    concept: _Long = ""
    story_logline: _Medium = ""
    story_synopsis: _Long = ""
    story_opening_hook: _Medium = ""
    primary_genre: GenreFamily = GenreFamily.FANTASY
    secondary_genres: tuple[GenreFamily, ...] = Field(default=(), max_length=4)
    genre_tags: tuple[_Short, ...] = Field(default=(), max_length=16)
    custom_genre: _Short = ""
    target_length: _Short = ""
    audience: _Short = ""
    tone: _Short = ""
    setting: _Medium = ""
    time_period: _Short = ""
    world_rules: tuple[_Medium, ...] = Field(default=(), max_length=24)
    locations: tuple[_Short, ...] = Field(default=(), max_length=24)
    social_context: _Medium = ""
    technology_or_magic: _Medium = ""
    central_conflict: _Medium = ""
    themes: tuple[_Short, ...] = Field(default=(), max_length=24)
    must_include: tuple[_Short, ...] = Field(default=(), max_length=24)
    must_avoid: tuple[_Short, ...] = Field(default=(), max_length=24)
    pov: NarrativePov = NarrativePov.THIRD_LIMITED
    tense: NarrativeTense = NarrativeTense.PAST
    pacing: _Short = ""
    prose_style_notes: _Medium = ""
    dialogue_density: _Short = ""
    direction: _Medium = ""
    ending_preference: _Medium = ""
    structure_profile: StructureProfile = StructureProfile.THREE_ACT
    violence_intensity: IntensityLevel = IntensityLevel.NONE
    horror_intensity: IntensityLevel = IntensityLevel.NONE
    intimacy_intensity: IntensityLevel = IntensityLevel.NONE
    content_mode: ContentMode = ContentMode.GENERAL
    character: CharacterBlueprint | None = None
    participants: tuple[CreativeParticipantDraft, ...] = Field(default=(), max_length=24)
    english_character_keywords: tuple[_Short, ...] = Field(default=(), max_length=64)
    scene_location: _Short = ""
    scene_time: _Short = ""
    scene_weather: _Short = ""
    scene_atmosphere: _Medium = ""
    scene_lighting: _Medium = ""
    scene_camera: _Medium = ""
    scene_motion: _Medium = ""
    video_duration_seconds: int = Field(default=6, ge=1, le=60)
    video_fps: int = Field(default=24, ge=1, le=120)
    video_aspect: Literal["16:9", "9:16", "1:1", "4:3", "3:4"] = "16:9"
    video_loop: bool = False

    @field_validator("project_id", "title")
    @classmethod
    def _required_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("專案與創作標題為必填")
        return value.strip()

    @field_validator("english_character_keywords", mode="before")
    @classmethod
    def _canonical_english_character_keywords(cls, value: object) -> tuple[str, ...]:
        """Normalize user-authored English prompt tokens without translating.

        The first spelling/casing wins for case-insensitive duplicates.  Space
        runs are collapsed so the stored tuple has one deterministic form.
        Commas are intentionally forbidden because rendering owns separators.
        """
        if value is None:
            return ()
        if isinstance(value, str):
            raise ValueError("英文角色提示詞必須逐項提供，不可提交逗號字串")
        try:
            raw_tokens: tuple[object, ...] = tuple(value)  # type: ignore[arg-type]
        except TypeError as exc:
            raise ValueError("英文角色提示詞必須是字串清單") from exc
        canonical: list[str] = []
        seen: set[str] = set()
        for raw in raw_tokens:
            if not isinstance(raw, str):
                raise ValueError("英文角色提示詞的每一項都必須是字串")
            token = re.sub(r" +", " ", raw.strip(" "))
            if not token:
                raise ValueError("英文角色提示詞不可為空白")
            if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 '/-]*", token) is None:
                raise ValueError(
                    "英文角色提示詞只允許 ASCII 英文字母、數字、空格、"
                    "單引號、斜線與連字號；不得含逗號"
                )
            key = token.casefold()
            if key in seen:
                continue
            seen.add(key)
            canonical.append(token)
        return tuple(canonical)

    @model_validator(mode="after")
    def _route_and_mature_contract(self) -> CreativeLaunchRequest:
        if self.mode is CreationMode.WORLD_ONLY and (
            self.character is not None or self.participants
        ):
            raise ValueError("僅世界觀路線不可提供角色或參與角色名單")
        if self.character is not None and self.participants:
            raise ValueError(
                "character 是 P4.1 單角色相容欄位；使用 participants 多人名單時不可同時提供"
            )
        drafts = self.effective_participant_drafts
        if self.participants:
            slot_ids = [draft.slot_id for draft in self.participants]
            if len(slot_ids) != len(set(slot_ids)):
                raise ValueError("參與角色 slot_id 不可重複")
            existing_character_ids = [
                draft.character_id
                for draft in self.participants
                if draft.source is CreativeParticipantSource.EXISTING_CANON
            ]
            if len(existing_character_ids) != len(set(existing_character_ids)):
                raise ValueError("同一既有 Canon 角色不可在參與名單中重複")
            if sum(draft.is_primary for draft in self.participants) != 1:
                raise ValueError("參與名單必須且只能指定一位 primary 角色")
        if self.mode not in (CreationMode.SERIES_STORY, CreationMode.WORLD_ONLY) and not drafts:
            raise ValueError("此創作路線需要角色設定")
        if self.mode is CreationMode.WORLD_ONLY and self.content_mode not in {
            ContentMode.GENERAL,
            ContentMode.MATURE_NONSEXUAL,
            ContentMode.DARK,
            ContentMode.HORROR,
            ContentMode.VIOLENT,
        }:
            raise ValueError("僅世界觀路線不接受成人暗示或成人露骨內容模式")
        # Every author-controlled text field that can flow into story or prompt
        # output participates in the same fail-closed classification check.  A
        # marker must not be able to bypass reclassification merely because it
        # was entered as a camera action, world rule, or visual trait.
        preflight = preflight_content_mode(self.content_mode, *self.positive_texts)
        if preflight.confirmation_required:
            raise ValueError(
                "創作文字包含成人性內容標記；請明確改選「成人暗示」或「成人露骨」，"
                "或修改文字後再建立藍圖"
            )
        if self.intimacy_intensity is IntensityLevel.EXPLICIT and not derives_adult(
            self.content_mode
        ):
            raise ValueError("親密強度為露骨時，內容模式必須明確選擇成人模式")
        if derives_adult(self.content_mode):
            if not drafts:
                raise ValueError("成人性內容必須綁定已建檔且可驗證的角色")
            floor = AGE_POLICY.minimum_age_for_mature_content
            for draft in drafts:
                # Existing Canon IDs can only be trusted after the application
                # service reloads that exact version and performs a live audit.
                if draft.source is CreativeParticipantSource.EXISTING_CANON:
                    continue
                character = draft.blueprint
                if character is None:  # guarded by CreativeParticipantDraft
                    raise ValueError("新角色參與者缺少 CharacterBlueprint")
                if character.explicit_age is None or character.explicit_age < floor:
                    raise ValueError(
                        f"成人性內容的每位原創角色都必須有明確且不低於 {floor} 歲的年齡"
                    )
                if not character.user_confirmed_age:
                    raise ValueError("成人性內容需要使用者明確確認每位原創角色年齡")
                if not character.adult_presentation_confirmed:
                    raise ValueError("成人性內容需要使用者確認每位原創角色版本為成人呈現")
            cues = detect_presentation_cues(*self.positive_texts)
            if cues.has_conflict:
                raise ValueError("成人性內容不得包含未綁定的未成年期、未成年人物或孩童化呈現")
        return self

    @property
    def effective_participant_drafts(self) -> tuple[CreativeParticipantDraft, ...]:
        """Return P4.2 drafts while preserving the P4.1 singular contract.

        Legacy callers keep serializing only ``character``.  The synthesized
        slot is a read-only view and therefore never changes their stored
        request shape or asks an old caller to invent a roster identifier.
        """
        if self.participants:
            return self.participants
        if self.character is None:
            return ()
        return (
            CreativeParticipantDraft(
                slot_id=_LEGACY_PRIMARY_SLOT_ID,
                source=CreativeParticipantSource.NEW_BLUEPRINT,
                is_primary=True,
                blueprint=self.character,
            ),
        )

    @property
    def positive_texts(self) -> tuple[str, ...]:
        """All author text that may positively influence story/prompt output."""
        values: list[str] = [
            self.title,
            self.concept,
            self.story_logline,
            self.story_synopsis,
            self.story_opening_hook,
            self.custom_genre,
            self.target_length,
            self.audience,
            self.tone,
            self.setting,
            self.time_period,
            self.social_context,
            self.technology_or_magic,
            self.central_conflict,
            self.pacing,
            self.prose_style_notes,
            self.dialogue_density,
            self.direction,
            self.ending_preference,
            self.scene_location,
            self.scene_time,
            self.scene_weather,
            self.scene_atmosphere,
            self.scene_lighting,
            self.scene_camera,
            self.scene_motion,
            *self.genre_tags,
            *self.world_rules,
            *self.locations,
            *self.themes,
            *self.must_include,
            *self.english_character_keywords,
        ]
        for participant in self.effective_participant_drafts:
            if participant.role:
                values.append(participant.role)
            if participant.source is CreativeParticipantSource.EXISTING_CANON:
                continue
            character = participant.blueprint
            if character is not None:
                values.extend(
                    (
                        character.name,
                        character.biography,
                        character.personality,
                        character.voice,
                        character.motivation,
                        character.fear,
                        character.secret,
                        character.internal_conflict,
                        character.arc_start,
                        character.arc_end,
                        character.identity,
                        character.face,
                        character.hair,
                        character.eyes,
                        character.body,
                        character.action,
                        character.expression,
                        *character.distinguishing_features,
                        *character.relationship_hooks,
                        *character.arc_turning_points,
                    )
                )
        return tuple(value for value in values if value.strip())

    @property
    def genre_text(self) -> str:
        values = [self.primary_genre.value]
        values.extend(g.value for g in self.secondary_genres if g != self.primary_genre)
        values.extend(tag.strip() for tag in self.genre_tags if tag.strip())
        if self.custom_genre.strip():
            values.append(self.custom_genre.strip())
        return " / ".join(dict.fromkeys(values))

    @property
    def fingerprint(self) -> str:
        data = self.model_dump(mode="json")

        # Character depth and gender were added after the original Launchpad
        # handoff contract.  Keep an old request's exact fingerprint stable
        # when every additive field is still at its default; real author
        # choices remain fingerprint-relevant.
        additive_blueprint_defaults: dict[str, object] = {
            "gender": None,
            "motivation": "",
            "fear": "",
            "secret": "",
            "internal_conflict": "",
            "relationship_hooks": [],
            "arc_start": "",
            "arc_turning_points": [],
            "arc_end": "",
        }

        def strip_additive_blueprint_defaults(raw: object) -> None:
            if not isinstance(raw, dict):
                return
            for field, default in additive_blueprint_defaults.items():
                if raw.get(field) == default:
                    raw.pop(field, None)

        strip_additive_blueprint_defaults(data.get("character"))
        participants = data.get("participants")
        if isinstance(participants, list):
            for participant in participants:
                if isinstance(participant, dict):
                    strip_additive_blueprint_defaults(participant.get("blueprint"))
        for additive_text_field in (
            "story_logline",
            "story_synopsis",
            "story_opening_hook",
        ):
            if not data.get(additive_text_field):
                data.pop(additive_text_field, None)
        # Preserve the exact P4.1 fingerprint payload for old singular briefs;
        # the additive empty roster must not invalidate their session results.
        if not self.participants:
            data.pop("participants", None)
        # Phase 4.3 temporal controls are additive.  Their defaults must not
        # invalidate pre-4.3 saved request fingerprints, while non-default
        # author choices remain fingerprint-relevant.
        if self.video_fps == 24:
            data.pop("video_fps", None)
        if self.video_aspect == "16:9":
            data.pop("video_aspect", None)
        if not self.video_loop:
            data.pop("video_loop", None)
        if not self.english_character_keywords:
            data.pop("english_character_keywords", None)
        payload = canonical_json(data)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class PromptDraftBundle(BaseModel):
    """Review-only text drafts; no ComfyUI execution is performed."""

    model_config = ConfigDict(frozen=True)

    character_image_prompt: str = ""
    character_prompt_en: str = ""
    character_video_prompt: str = ""
    scene_image_prompt: str = ""
    scene_video_prompt: str = ""
    negative_prompt: str = ""
    status_message: str = ""


class CreativeLaunchPreview(BaseModel):
    model_config = ConfigDict(frozen=True)

    request_fingerprint: str
    route_summary: str
    story_summary: str
    character_summary: str
    prompt_bundle: PromptDraftBundle


class CreativeExpansion(BaseModel):
    """Provider-authored planning suggestions; deliberately no policy flags."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    title: _Short = ""
    concept: _Long = ""
    tone: _Short = ""
    setting: _Medium = ""
    time_period: _Short = ""
    world_rules: tuple[_Medium, ...] = Field(default=(), max_length=24)
    locations: tuple[_Short, ...] = Field(default=(), max_length=24)
    social_context: _Medium = ""
    technology_or_magic: _Medium = ""
    central_conflict: _Medium = ""
    themes: tuple[_Short, ...] = Field(default=(), max_length=24)
    direction: _Medium = ""
    ending_preference: _Medium = ""
    character_biography: _Long = ""
    character_personality: _Medium = ""
    character_voice: _Medium = ""
    character_identity: _Medium = ""
    character_face: _Medium = ""
    character_hair: _Medium = ""
    character_eyes: _Medium = ""
    character_body: _Medium = ""
    distinguishing_features: tuple[_Short, ...] = Field(default=(), max_length=24)


class CharacterLaunchResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    request_fingerprint: str
    character_id: str
    character_version_id: str
    eligibility_evaluation_id: str = ""


class ParticipantLaunchResult(BaseModel):
    """Exact roster produced after every new draft has entered Canon."""

    model_config = ConfigDict(frozen=True)

    request_fingerprint: str
    manifest: ParticipantManifest


class StoryFoundationResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    request_fingerprint: str
    requirement_id: str
    requirement_version_id: str
    bible_id: str
    bible_version_id: str
    outline_id: str
    outline_version_id: str
    eligibility_evaluation_ids: tuple[str, ...] = ()


class WorldFoundationResult(BaseModel):
    """A world-only Requirement/Bible pair; no story structure is implied."""

    model_config = ConfigDict(frozen=True)

    request_fingerprint: str
    requirement_id: str
    requirement_version_id: str
    bible_id: str
    bible_version_id: str


class PromptLaunchResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    request_fingerprint: str
    prompt_project_id: str
    prompt_version_id: str
    eligibility_evaluation_id: str = ""
    image_eligibility_evaluation_ids: tuple[str, ...] = ()
    prompt_bundle: PromptDraftBundle = Field(default_factory=PromptDraftBundle)
