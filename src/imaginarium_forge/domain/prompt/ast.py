"""Prompt AST — the model-independent scene representation (spec §24).

Design rules honored here:
- model-independent: nothing in this module knows about tags, weights, or any
  checkpoint syntax; that belongs to profiles + the compiler;
- serializable/diffable: `canonical_dump()` gives a stable JSON string;
- versioned: `prompt_ast_version` travels with every instance;
- uncertainty: parser-produced fields carry an explicit state in `field_states`
  keyed by dotted path (e.g. "camera.shot"); absence of a key means the field
  was human-entered/confirmed. The compiler ignores uncertainty (deterministic);
  the UI highlights inferred/uncertain fields.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator

from imaginarium_forge.canonical import canonical_json

PROMPT_AST_VERSION = "1.0"


class UncertaintyState(StrEnum):
    CONFIRMED = "confirmed"
    INFERRED = "inferred"
    UNCERTAIN = "uncertain"
    MISSING = "missing"


class SubjectNode(BaseModel):
    """One subject. Internal Alpha blocking support: exactly one primary subject."""

    model_config = ConfigDict(frozen=True)

    subject_id: str = "subject-1"
    character_id: str = ""
    character_version_id: str = ""
    origin: str = ""  # original | existing (informational echo of Canon)
    presentation: str = ""  # e.g. "adult"
    canonical_features: tuple[str, ...] = ()
    scene_overrides: tuple[str, ...] = ()
    outfit_id: str = ""
    pose: str = ""
    expression: str = ""
    #: parser-distinguished emotional subtext (e.g. "holding_back_tears") — the
    #: visible expression stays in `expression`; subtext never overwrites it.
    emotional_subtext: str = ""
    gaze: str = ""
    motion_cues: tuple[str, ...] = ()
    #: §5.4 identity preservation: "只換衣服，不改角色"
    preserve_identity: bool = False
    outfit_override_only: bool = False


class CameraNode(BaseModel):
    model_config = ConfigDict(frozen=True)

    shot: str = ""
    angle: str = ""
    lens_intent: str = ""
    framing: str = ""
    subject_placement: str = ""
    depth_of_field: str = ""


class EnvironmentNode(BaseModel):
    model_config = ConfigDict(frozen=True)

    location: str = ""
    time_of_day: str = ""
    weather: str = ""
    background_elements: tuple[str, ...] = ()
    atmosphere: str = ""


class LightingNode(BaseModel):
    model_config = ConfigDict(frozen=True)

    key: str = ""
    fill: str = ""
    rim: str = ""
    contrast: str = ""
    color_temperature: str = ""


class StyleNode(BaseModel):
    model_config = ConfigDict(frozen=True)

    style_profile_id: str = ""
    style_version_id: str = ""
    scene_mood_overrides: tuple[str, ...] = ()


class NegativeNode(BaseModel):
    """Negative intent by category (model-independent semantics, not tags)."""

    model_config = ConfigDict(frozen=True)

    semantic: tuple[str, ...] = ()
    anatomy: tuple[str, ...] = ()
    identity: tuple[str, ...] = ()
    style: tuple[str, ...] = ()
    composition: tuple[str, ...] = ()


class UserIntent(BaseModel):
    model_config = ConfigDict(frozen=True)

    source_language: str = "zh-TW"
    source_text: str = ""
    must_include: tuple[str, ...] = ()
    must_avoid: tuple[str, ...] = ()


class AstMetadata(BaseModel):
    model_config = ConfigDict(frozen=True)

    parser_provider: str = ""
    parser_model: str = ""
    created_at: str = ""
    # Gate A A-11 §14.5 — parser provenance
    parser_contract_version: str = ""
    schema_version: str = ""
    structured_mode: bool = True
    repair_attempts: int = 0
    total_latency_ms: int = 0


class PromptAST(BaseModel):
    prompt_ast_version: str = PROMPT_AST_VERSION
    subjects: tuple[SubjectNode, ...] = ()
    camera: CameraNode = Field(default_factory=CameraNode)
    environment: EnvironmentNode = Field(default_factory=EnvironmentNode)
    lighting: LightingNode = Field(default_factory=LightingNode)
    style: StyleNode = Field(default_factory=StyleNode)
    negative: NegativeNode = Field(default_factory=NegativeNode)
    user_intent: UserIntent = Field(default_factory=UserIntent)
    metadata: AstMetadata = Field(default_factory=AstMetadata)
    #: dotted-path → uncertainty state, for parser-produced fields only
    field_states: dict[str, UncertaintyState] = Field(default_factory=dict)

    @field_validator("prompt_ast_version")
    @classmethod
    def _known_version(cls, value: str) -> str:
        if value != PROMPT_AST_VERSION:
            raise ValueError(f"不支援的 Prompt AST 版本：{value}（目前：{PROMPT_AST_VERSION}）")
        return value

    @property
    def primary_subject(self) -> SubjectNode | None:
        return self.subjects[0] if self.subjects else None

    def canonical_dump(self) -> str:
        """Stable serialization for hashing/diffing (dict key order canonicalized)."""
        return canonical_json(self.model_dump(mode="json"))

    def uncertain_paths(self) -> tuple[str, ...]:
        """Paths the UI must highlight (inferred or uncertain)."""
        return tuple(
            sorted(
                path
                for path, state in self.field_states.items()
                if state in (UncertaintyState.INFERRED, UncertaintyState.UNCERTAIN)
            )
        )
