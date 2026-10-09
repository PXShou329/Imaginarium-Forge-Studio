"""Typed, reproducible output-length contracts."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType

from pydantic import BaseModel, ConfigDict, Field, model_validator

from imaginarium_forge.canonical import canonical_json as _canonical_json
from imaginarium_forge.domain.generation.intent import GenerationIntent


class LengthUnit(StrEnum):
    """An author-selected measurement unit; mixed text never changes it implicitly."""

    VISIBLE_CHARS = "visible_chars"
    WORDS = "words"
    SCREENPLAY_PAGES = "screenplay_pages"


class LengthStrictness(StrEnum):
    """How a later generation pipeline should enforce the requested bounds."""

    ADVISORY = "advisory"
    MINIMUM_REQUIRED = "minimum_required"
    BOUNDED = "bounded"


class GenerationLengthSpec(BaseModel):
    """The exact author-visible length request carried through generation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    unit: LengthUnit
    minimum: int | None = Field(ge=0)
    target: int = Field(gt=0)
    maximum: int | None = Field(ge=0)
    segment_target: int | None = Field(gt=0)
    max_continuations: int = Field(default=3, ge=0, le=5)
    strictness: LengthStrictness

    @model_validator(mode="after")
    def _ordered_bounds(self) -> GenerationLengthSpec:
        if self.minimum is not None and self.minimum > self.target:
            raise ValueError("minimum must not exceed target")
        if self.maximum is not None and self.target > self.maximum:
            raise ValueError("target must not exceed maximum")
        return self

    @property
    def canonical_json(self) -> str:
        """Return the stable JSON snapshot used by persistence and provenance."""

        return _canonical_json(self.model_dump(mode="json"))

    @property
    def sha256(self) -> str:
        """Fingerprint the exact canonical length request."""

        return hashlib.sha256(self.canonical_json.encode("utf-8")).hexdigest()


class DefaultGenerationWorkflow(StrEnum):
    """R2 preset workflow labels; none of them selects a provider."""

    SINGLE = "single"
    SINGLE_OR_ONE_CONTINUATION = "single_or_one_continuation"
    SINGLE_WITH_LENGTH_VALIDATION = "single_with_length_validation"
    SEGMENTED_OR_BOUNDED_CONTINUATIONS = "segmented_or_bounded_continuations"
    PLAN_THEN_PROSE = "plan_then_prose"
    DURABLE_UNIT = "durable_unit"
    DURABLE_MULTI_UNIT = "durable_multi_unit"
    DURABLE_STRUCTURED_PROSE = "durable_structured_prose"
    DURABLE_MULTI_SCENE = "durable_multi_scene"


class GenerationIntentPreset(BaseModel):
    """One editable UI starting point from the R2 intent-preset table."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    unit: LengthUnit
    suggested_target: int | None = Field(ge=1)
    workflow: DefaultGenerationWorkflow


INTENT_LENGTH_PRESETS: Mapping[GenerationIntent, GenerationIntentPreset] = MappingProxyType(
    {
        GenerationIntent.IDEA: GenerationIntentPreset(
            unit=LengthUnit.VISIBLE_CHARS,
            suggested_target=200,
            workflow=DefaultGenerationWorkflow.SINGLE,
        ),
        GenerationIntent.PASSAGE: GenerationIntentPreset(
            unit=LengthUnit.VISIBLE_CHARS,
            suggested_target=700,
            workflow=DefaultGenerationWorkflow.SINGLE_OR_ONE_CONTINUATION,
        ),
        GenerationIntent.SCENE: GenerationIntentPreset(
            unit=LengthUnit.VISIBLE_CHARS,
            suggested_target=1_800,
            workflow=DefaultGenerationWorkflow.SINGLE_WITH_LENGTH_VALIDATION,
        ),
        GenerationIntent.SHORT_STORY: GenerationIntentPreset(
            unit=LengthUnit.VISIBLE_CHARS,
            suggested_target=5_000,
            workflow=DefaultGenerationWorkflow.SEGMENTED_OR_BOUNDED_CONTINUATIONS,
        ),
        GenerationIntent.CHAPTER: GenerationIntentPreset(
            unit=LengthUnit.VISIBLE_CHARS,
            suggested_target=5_000,
            workflow=DefaultGenerationWorkflow.DURABLE_UNIT,
        ),
        GenerationIntent.LONG_STORY: GenerationIntentPreset(
            unit=LengthUnit.VISIBLE_CHARS,
            suggested_target=None,
            workflow=DefaultGenerationWorkflow.DURABLE_MULTI_UNIT,
        ),
        GenerationIntent.CHARACTER_BACKSTORY: GenerationIntentPreset(
            unit=LengthUnit.VISIBLE_CHARS,
            suggested_target=2_500,
            workflow=DefaultGenerationWorkflow.PLAN_THEN_PROSE,
        ),
        GenerationIntent.SCRIPT_SCENE: GenerationIntentPreset(
            unit=LengthUnit.SCREENPLAY_PAGES,
            suggested_target=3,
            workflow=DefaultGenerationWorkflow.DURABLE_STRUCTURED_PROSE,
        ),
        GenerationIntent.SCREENPLAY: GenerationIntentPreset(
            unit=LengthUnit.SCREENPLAY_PAGES,
            suggested_target=None,
            workflow=DefaultGenerationWorkflow.DURABLE_MULTI_SCENE,
        ),
    }
)


def preset_for_intent(intent: GenerationIntent | str) -> GenerationIntentPreset:
    """Resolve a persisted intent value to its provider-neutral R2 preset."""

    return INTENT_LENGTH_PRESETS[GenerationIntent(intent)]


__all__ = [
    "INTENT_LENGTH_PRESETS",
    "DefaultGenerationWorkflow",
    "GenerationIntentPreset",
    "GenerationLengthSpec",
    "LengthStrictness",
    "LengthUnit",
    "preset_for_intent",
]
