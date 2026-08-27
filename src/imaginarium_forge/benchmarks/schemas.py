"""Named output schemas used by Phase 0 benchmarks and the parser spike.

`MinimalSceneParse` is deliberately NOT the final Prompt AST (proposal §16). It is
a flat, defaults-everywhere spike schema so that candidate models can be compared
cheaply. Promotion criteria: docs/design/spike-scene-parser-criteria.md.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class MinimalSceneParse(BaseModel):
    """Flat minimal scene representation for the Phase 0 parser spike."""

    subject_count: int | None = None
    subject_hint: str | None = None
    pose: list[str] = Field(default_factory=list)
    expression_visible: list[str] = Field(default_factory=list)
    expression_implied: list[str] = Field(default_factory=list)
    camera_shot: str | None = None
    camera_angle: str | None = None
    location: str | None = None
    time_of_day: str | None = None
    weather: str | None = None
    lighting: str | None = None
    style_intent: list[str] = Field(default_factory=list)
    negative_intent: list[str] = Field(default_factory=list)


class CharacterCardV0(BaseModel):
    """Tiny structured-output probe schema (not the Phase 1 character model)."""

    name: str = "unnamed"
    age: int | None = None
    traits: list[str] = Field(default_factory=list)
    one_line_bio: str = ""


SCHEMA_REGISTRY: dict[str, type[BaseModel]] = {
    "minimal_scene": MinimalSceneParse,
    "character_card_v0": CharacterCardV0,
}

DEFAULT_SCHEMA_BY_CATEGORY: dict[str, str] = {
    "scene_parsing": "minimal_scene",
    "structured_output": "character_card_v0",
    "repair_behavior": "minimal_scene",
}
