"""Parser draft schema and draft→AST mapping (spec §5).

The LLM's structured-output target is `ParsedSceneDraft` — deliberately
SMALLER than the full PromptAST:

- no Canon identifiers (character/version/outfit selection is the UI's job;
  the parser must never choose or modify Canon, §5.1);
- no metadata (the service stamps provider/model/time deterministically);
- every section the LLM fills has safe defaults, so a minimal valid response
  is `{}`.

`field_states` uses dotted paths (e.g. `"camera.angle"`) with the four states
from §5.3. The mapping into PromptAST is pure and deterministic.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from imaginarium_forge.domain.prompt.ast import (
    AstMetadata,
    CameraNode,
    EnvironmentNode,
    LightingNode,
    NegativeNode,
    PromptAST,
    StyleNode,
    SubjectNode,
    UncertaintyState,
    UserIntent,
)

#: A2-10 (review §4.5) — hard structural bounds on parser output. A model
#: (or an injected instruction) must not be able to return 10,000 subjects or
#: megabyte-long strings and have them flow into the AST unchecked.
MAX_SUBJECTS = 8
MAX_LIST_ITEMS = 32
MAX_SHORT_TEXT = 200
MAX_LONG_TEXT = 500
MAX_FIELD_STATE_KEYS = 64

_Short = Annotated[str, StringConstraints(max_length=MAX_SHORT_TEXT)]
_Long = Annotated[str, StringConstraints(max_length=MAX_LONG_TEXT)]


class DraftSubject(BaseModel):
    """Visual description of one subject — never identity selection."""

    model_config = ConfigDict(frozen=True)

    presentation: _Short = ""
    appearance_overrides: tuple[_Short, ...] = Field(
        default=(), max_length=MAX_LIST_ITEMS
    )
    outfit_notes: tuple[_Short, ...] = Field(default=(), max_length=MAX_LIST_ITEMS)
    pose: _Short = ""
    expression: _Short = ""
    emotional_subtext: _Long = ""
    gaze: _Short = ""
    motion_cues: tuple[_Short, ...] = Field(default=(), max_length=MAX_LIST_ITEMS)
    preserve_identity: bool = False
    outfit_override_only: bool = False


#: bumped whenever ParsedSceneDraft fields change (A-11 §14.5 metadata)
PARSED_SCENE_DRAFT_SCHEMA_VERSION = "1.0"


class ParsedSceneDraft(BaseModel):
    """Structured-output contract for the visual scene parser LLM call."""

    model_config = ConfigDict(frozen=True)

    subjects: tuple[DraftSubject, ...] = Field(default=(), max_length=MAX_SUBJECTS)
    camera_shot: _Short = ""
    camera_angle: _Short = ""
    camera_lens_intent: _Short = ""
    camera_framing: _Short = ""
    environment_location: _Short = ""
    environment_time_of_day: _Short = ""
    environment_weather: _Short = ""
    environment_background: tuple[_Short, ...] = Field(
        default=(), max_length=MAX_LIST_ITEMS
    )
    environment_atmosphere: _Long = ""
    lighting_key: _Short = ""
    lighting_color_temperature: _Short = ""
    style_mood: tuple[_Short, ...] = Field(default=(), max_length=MAX_LIST_ITEMS)
    negative_semantic: tuple[_Short, ...] = Field(
        default=(), max_length=MAX_LIST_ITEMS
    )
    negative_style: tuple[_Short, ...] = Field(default=(), max_length=MAX_LIST_ITEMS)
    must_include: tuple[_Short, ...] = Field(default=(), max_length=MAX_LIST_ITEMS)
    must_avoid: tuple[_Short, ...] = Field(default=(), max_length=MAX_LIST_ITEMS)
    field_states: dict[str, UncertaintyState] = Field(default_factory=dict)


def draft_to_ast(
    draft: ParsedSceneDraft,
    *,
    source_text: str,
    metadata: AstMetadata,
) -> PromptAST:
    """Deterministically lift the LLM draft into a full editable PromptAST.

    Canon identifiers stay empty — selection happens in the Studio UI.
    """
    subjects = tuple(
        SubjectNode(
            presentation=s.presentation,
            scene_overrides=s.appearance_overrides + s.outfit_notes,
            pose=s.pose,
            expression=s.expression,
            emotional_subtext=s.emotional_subtext,
            gaze=s.gaze,
            motion_cues=s.motion_cues,
            preserve_identity=s.preserve_identity,
            outfit_override_only=s.outfit_override_only,
        )
        for s in draft.subjects
    )
    return PromptAST(
        subjects=subjects,
        camera=CameraNode(
            shot=draft.camera_shot,
            angle=draft.camera_angle,
            lens_intent=draft.camera_lens_intent,
            framing=draft.camera_framing,
        ),
        environment=EnvironmentNode(
            location=draft.environment_location,
            time_of_day=draft.environment_time_of_day,
            weather=draft.environment_weather,
            background_elements=draft.environment_background,
            atmosphere=draft.environment_atmosphere,
        ),
        lighting=LightingNode(
            key=draft.lighting_key,
            color_temperature=draft.lighting_color_temperature,
        ),
        style=StyleNode(scene_mood_overrides=draft.style_mood),
        negative=NegativeNode(
            semantic=draft.negative_semantic,
            style=draft.negative_style,
        ),
        user_intent=UserIntent(
            source_text=source_text,
            must_include=draft.must_include,
            must_avoid=draft.must_avoid,
        ),
        field_states=dict(draft.field_states),
        metadata=metadata,
    )
