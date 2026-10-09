"""Exact, persisted output measurement for the length contract."""

from __future__ import annotations

import hashlib
import math
import re
from enum import StrEnum
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from imaginarium_forge.canonical import canonical_json as _canonical_json
from imaginarium_forge.domain.generation.length import (
    GenerationLengthSpec,
    LengthStrictness,
    LengthUnit,
)

SCREENPLAY_WORDS_PER_PAGE: Final[Literal[250]] = 250
SCREENPLAY_PAGE_POLICY_VERSION: Final[Literal["screenplay-words-per-page-v1"]] = (
    "screenplay-words-per-page-v1"
)

_WORD_PATTERN = re.compile(r"[^\W_]+(?:['’‐‑-][^\W_]+)*", flags=re.UNICODE)


class LengthBand(StrEnum):
    """Inclusive comparison of actual output with optional author bounds."""

    BELOW = "below"
    WITHIN = "within"
    ABOVE = "above"


class ScreenplayPagePolicy(BaseModel):
    """Versioned internal estimate policy where R2 leaves the coefficient open."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    method: Literal["words_per_page"] = "words_per_page"
    version: Literal["screenplay-words-per-page-v1"] = SCREENPLAY_PAGE_POLICY_VERSION
    words_per_page: Literal[250] = SCREENPLAY_WORDS_PER_PAGE


DEFAULT_SCREENPLAY_PAGE_POLICY = ScreenplayPagePolicy()


class OutputLengthAssessment(BaseModel):
    """A domain comparison; provider finish reasons are intentionally absent."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    unit: LengthUnit
    actual: int | float = Field(ge=0)
    minimum: int | None = Field(ge=0)
    target: int = Field(gt=0)
    maximum: int | None = Field(ge=0)
    strictness: LengthStrictness
    band: LengthBand
    satisfied: bool


class OutputMeasurement(BaseModel):
    """All raw counts plus the exact selected-unit value and page policy."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    unit: LengthUnit
    actual: int | float = Field(ge=0)
    visible_chars: int = Field(ge=0)
    words: int = Field(ge=0)
    screenplay_pages_estimate: float = Field(ge=0, allow_inf_nan=False)
    screenplay_page_policy: ScreenplayPagePolicy = DEFAULT_SCREENPLAY_PAGE_POLICY

    @model_validator(mode="after")
    def _internally_consistent_counts(self) -> OutputMeasurement:
        expected_actual: int | float
        if self.unit is LengthUnit.VISIBLE_CHARS:
            expected_actual = self.visible_chars
        elif self.unit is LengthUnit.WORDS:
            expected_actual = self.words
        else:
            expected_actual = self.screenplay_pages_estimate
        if self.actual != expected_actual:
            raise ValueError("actual does not match the selected-unit raw count")

        expected_pages = self.words / self.screenplay_page_policy.words_per_page
        if not math.isclose(
            self.screenplay_pages_estimate,
            expected_pages,
            rel_tol=0.0,
            abs_tol=1e-12,
        ):
            raise ValueError("screenplay pages estimate does not match saved words and policy")
        return self

    @property
    def canonical_json(self) -> str:
        """Return a stable snapshot containing policy, raw counts, and selected value."""

        return _canonical_json(self.model_dump(mode="json"))

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.canonical_json.encode("utf-8")).hexdigest()

    def assess(self, spec: GenerationLengthSpec) -> OutputLengthAssessment:
        """Compare with the exact contract, independently of any provider finish reason."""

        if self.unit is not spec.unit:
            raise ValueError("measurement unit must match length spec unit")

        if spec.minimum is not None and self.actual < spec.minimum:
            band = LengthBand.BELOW
        elif spec.maximum is not None and self.actual > spec.maximum:
            band = LengthBand.ABOVE
        else:
            band = LengthBand.WITHIN

        if spec.strictness is LengthStrictness.ADVISORY:
            satisfied = True
        elif spec.strictness is LengthStrictness.MINIMUM_REQUIRED:
            satisfied = band is not LengthBand.BELOW
        else:
            satisfied = band is LengthBand.WITHIN

        return OutputLengthAssessment(
            unit=self.unit,
            actual=self.actual,
            minimum=spec.minimum,
            target=spec.target,
            maximum=spec.maximum,
            strictness=spec.strictness,
            band=band,
            satisfied=satisfied,
        )


def count_visible_characters(text: str) -> int:
    """Count Unicode code points excluding whitespace, matching existing UI language."""

    return sum(1 for character in text if not character.isspace())


def count_words(text: str) -> int:
    """Count deterministic Unicode word runs, retaining apostrophe/hyphen joiners."""

    return len(_WORD_PATTERN.findall(text))


def measure_output(
    text: str,
    *,
    unit: LengthUnit,
    screenplay_page_policy: ScreenplayPagePolicy = DEFAULT_SCREENPLAY_PAGE_POLICY,
) -> OutputMeasurement:
    """Measure complete text without truncation or language-based unit switching."""

    visible_chars = count_visible_characters(text)
    words = count_words(text)
    screenplay_pages = words / screenplay_page_policy.words_per_page
    if unit is LengthUnit.VISIBLE_CHARS:
        actual: int | float = visible_chars
    elif unit is LengthUnit.WORDS:
        actual = words
    else:
        actual = screenplay_pages
    return OutputMeasurement(
        unit=unit,
        actual=actual,
        visible_chars=visible_chars,
        words=words,
        screenplay_pages_estimate=screenplay_pages,
        screenplay_page_policy=screenplay_page_policy,
    )


__all__ = [
    "DEFAULT_SCREENPLAY_PAGE_POLICY",
    "SCREENPLAY_PAGE_POLICY_VERSION",
    "SCREENPLAY_WORDS_PER_PAGE",
    "LengthBand",
    "OutputLengthAssessment",
    "OutputMeasurement",
    "ScreenplayPagePolicy",
    "count_visible_characters",
    "count_words",
    "measure_output",
]
