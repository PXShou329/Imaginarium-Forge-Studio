"""Deterministic, bounded continuation planning for prose."""

from __future__ import annotations

import hashlib
import math
import re
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from imaginarium_forge.application.services.generation.output_measurement import (
    LengthBand,
    measure_output,
)
from imaginarium_forge.domain.generation.length import (
    GenerationLengthSpec,
    LengthStrictness,
    LengthUnit,
)

_PARAGRAPH_BOUNDARY = re.compile(r"(?:\r?\n)[ \t]*(?:\r?\n)+")
_SENTENCE_BOUNDARY = re.compile(r"[.!?。！？…][\"'”’）)]*(?:\s+|$)")


class StableBoundaryKind(StrEnum):
    """The strongest complete prose boundary found in the accumulated text."""

    PARAGRAPH = "paragraph"
    SENTENCE = "sentence"
    FULL_TEXT = "full_text"
    NONE = "none"


class ContinuationPlan(BaseModel):
    """Reproducible local evidence and prompt inputs for one continuation call."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    length_spec_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    existing_text_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    unit: LengthUnit
    continuation_number: int = Field(ge=1, le=5)
    max_continuations: int = Field(ge=1, le=5)
    stable_boundary_offset: int = Field(ge=0)
    stable_boundary_kind: StableBoundaryKind
    stable_prefix_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    tail_excerpt: str
    tail_excerpt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    tail_excerpt_limit: int = Field(ge=128, le=8_000)
    rolling_summary: str = ""
    protected_facts: tuple[str, ...] = ()
    remaining_to_minimum: int | float = Field(ge=0, allow_inf_nan=False)
    remaining_to_target: int | float = Field(ge=0, allow_inf_nan=False)
    requested_additional: int | float = Field(gt=0, allow_inf_nan=False)
    provider_incomplete: bool = False
    instruction: str = Field(min_length=1)

    @model_validator(mode="after")
    def _coherent_plan(self) -> ContinuationPlan:
        if self.continuation_number > self.max_continuations:
            raise ValueError("continuation number exceeds the saved length contract")
        if len(self.tail_excerpt) > self.tail_excerpt_limit:
            raise ValueError("tail excerpt exceeds its explicit saved limit")
        if self.tail_excerpt_sha256 != _sha256_text(self.tail_excerpt):
            raise ValueError("tail excerpt hash does not match")
        return self


class ContinuationPlanner:
    """Build a continuation request from saved prose and the exact length contract."""

    def __init__(self, *, tail_excerpt_limit: int = 1_200) -> None:
        if not 128 <= tail_excerpt_limit <= 8_000:
            raise ValueError("tail_excerpt_limit must be between 128 and 8000")
        self._tail_excerpt_limit = tail_excerpt_limit

    def plan(
        self,
        *,
        existing_text: str,
        spec: GenerationLengthSpec,
        continuations_used: int,
        rolling_summary: str = "",
        protected_facts: tuple[str, ...] = (),
        provider_incomplete: bool = False,
    ) -> ContinuationPlan:
        """Return the next bounded continuation plan without rewriting prior prose."""

        if not existing_text:
            raise ValueError("continuation planning requires preserved existing prose")
        if continuations_used < 0:
            raise ValueError("continuations_used must not be negative")
        if continuations_used >= spec.max_continuations:
            raise ValueError("continuation budget is exhausted")

        measurement = measure_output(existing_text, unit=spec.unit)
        assessment = measurement.assess(spec)
        if assessment.band is LengthBand.ABOVE:
            raise ValueError("over-maximum prose requires a separate condensed candidate")
        if assessment.satisfied and not provider_incomplete:
            raise ValueError("domain-complete prose does not require continuation")

        boundary_offset, boundary_kind = stable_prose_boundary(existing_text)
        stable_prefix = existing_text[:boundary_offset]
        tail_excerpt = existing_text[-self._tail_excerpt_limit :]
        remaining_to_minimum = _positive_difference(spec.minimum, measurement.actual)
        remaining_to_target = _positive_difference(spec.target, measurement.actual)
        requested_additional = _requested_additional(
            spec=spec,
            actual=measurement.actual,
            remaining_to_minimum=remaining_to_minimum,
            remaining_to_target=remaining_to_target,
            provider_incomplete=provider_incomplete,
        )
        continuation_number = continuations_used + 1
        instruction = _continuation_instruction(
            unit=spec.unit,
            requested_additional=requested_additional,
            continuation_number=continuation_number,
            provider_incomplete=provider_incomplete,
        )
        instruction += "\n\n" + _length_progress_instruction(
            spec=spec,
            actual=measurement.actual,
            requested_additional=requested_additional,
            continuations_remaining=spec.max_continuations - continuation_number,
        )

        return ContinuationPlan(
            length_spec_sha256=spec.sha256,
            existing_text_sha256=_sha256_text(existing_text),
            unit=spec.unit,
            continuation_number=continuation_number,
            max_continuations=spec.max_continuations,
            stable_boundary_offset=boundary_offset,
            stable_boundary_kind=boundary_kind,
            stable_prefix_sha256=_sha256_text(stable_prefix),
            tail_excerpt=tail_excerpt,
            tail_excerpt_sha256=_sha256_text(tail_excerpt),
            tail_excerpt_limit=self._tail_excerpt_limit,
            rolling_summary=rolling_summary,
            protected_facts=protected_facts,
            remaining_to_minimum=remaining_to_minimum,
            remaining_to_target=remaining_to_target,
            requested_additional=requested_additional,
            provider_incomplete=provider_incomplete,
            instruction=instruction,
        )


def stable_prose_boundary(text: str) -> tuple[int, StableBoundaryKind]:
    """Return the last complete paragraph or sentence boundary in full input text."""

    if not text:
        return 0, StableBoundaryKind.NONE

    paragraph_matches = tuple(_PARAGRAPH_BOUNDARY.finditer(text))
    sentence_matches = tuple(_SENTENCE_BOUNDARY.finditer(text))
    paragraph_offset = paragraph_matches[-1].end() if paragraph_matches else 0
    sentence_offset = sentence_matches[-1].end() if sentence_matches else 0
    if paragraph_offset or sentence_offset:
        if paragraph_offset >= sentence_offset:
            return paragraph_offset, StableBoundaryKind.PARAGRAPH
        return sentence_offset, StableBoundaryKind.SENTENCE

    if text[-1].isspace():
        return len(text), StableBoundaryKind.FULL_TEXT
    return 0, StableBoundaryKind.NONE


def _positive_difference(bound: int | None, actual: int | float) -> int | float:
    if bound is None:
        return 0
    return max(0, bound - actual)


def _requested_additional(
    *,
    spec: GenerationLengthSpec,
    actual: int | float,
    remaining_to_minimum: int | float,
    remaining_to_target: int | float,
    provider_incomplete: bool,
) -> int | float:
    desired = max(remaining_to_minimum, remaining_to_target)
    if desired == 0 and provider_incomplete:
        desired = spec.segment_target or max(1, math.ceil(spec.target * 0.1))
    if spec.segment_target is not None:
        desired = min(desired, spec.segment_target)
    if spec.maximum is not None:
        desired = min(desired, max(0, spec.maximum - actual))
    if desired <= 0:
        raise ValueError("no positive continuation fits inside the saved maximum")
    return desired


def _continuation_instruction(
    *,
    unit: LengthUnit,
    requested_additional: int | float,
    continuation_number: int,
    provider_incomplete: bool,
) -> str:
    amount = f"{requested_additional:g}"
    incomplete_clause = (
        "Finish the interrupted sentence or paragraph before advancing the scene. "
        if provider_incomplete
        else ""
    )
    return (
        f"Continuation {continuation_number}: continue only after the saved stable boundary. "
        "Do not rewrite, summarize, quote, or repeat existing prose. "
        f"{incomplete_clause}Preserve names, pronouns, chronology, and protected facts. "
        f"Add approximately {amount} {unit.value}; end at a complete sentence or paragraph. "
        "Return raw prose only."
    )


def _length_progress_instruction(
    *,
    spec: GenerationLengthSpec,
    actual: int | float,
    requested_additional: int | float,
    continuations_remaining: int,
    initial: bool = False,
) -> str:
    """Explain existing numeric bounds without changing the saved contract or budget."""

    minimum = "not_set" if spec.minimum is None else str(spec.minimum)
    maximum = "not_set" if spec.maximum is None else str(spec.maximum)
    minimum_gap = (
        "not_set" if spec.minimum is None else f"{_positive_difference(spec.minimum, actual):g}"
    )
    maximum_gap = (
        "not_set" if spec.maximum is None else f"{_positive_difference(spec.maximum, actual):g}"
    )
    clauses = [
        f"Whole-candidate length: unit={spec.unit.value}; strictness={spec.strictness.value}; "
        f"minimum={minimum}; target={spec.target}; maximum={maximum}.",
        f"Measured accumulated length: {actual:g}; remaining to minimum: {minimum_gap}; "
        f"remaining to target: {_positive_difference(spec.target, actual):g}; "
        f"remaining to maximum: {maximum_gap}.",
        f"This-call target: {requested_additional:g} {spec.unit.value}; "
        f"remaining continuations after this call: {continuations_remaining}.",
        "This remaining count describes logical continuation slots if the output is accepted, "
        "not a promise of executable calls; attempt, cost, and repetition guards can stop earlier.",
        "Whole-candidate bounds are not per-segment bounds.",
        "Develop new actions, dialogue, or consequences; do not pad with repeated conclusions.",
    ]
    if initial:
        clauses.append(
            "This call begins the candidate; its segment target is not the whole-candidate target."
        )
        if continuations_remaining == 0:
            clauses.append("No continuation is authorized after this call.")
    elif continuations_remaining == 0:
        clauses.append("Final continuation: no continuation calls remain after this call.")

    if spec.strictness is LengthStrictness.ADVISORY:
        clauses.append("Advisory minimum and target remain non-binding.")
    else:
        if spec.minimum is not None and actual < spec.minimum:
            if not initial and continuations_remaining == 0:
                clauses.append(
                    f"To satisfy the saved minimum, this final call needs at least "
                    f"{_positive_difference(spec.minimum, actual):g} {spec.unit.value}."
                )
            clauses.append(
                "Do not treat a clean segment ending as completion of the whole candidate "
                "while required length remains."
            )
        clauses.append("Unmet required length remains partial; do not claim completion.")
    if spec.maximum is not None:
        clauses.append("A segment target does not authorize exceeding the whole-candidate maximum.")
        clauses.append(
            "Any configured maximum still invokes over-length protection: preserve the original "
            "candidate and use a separate condensation candidate when allowed; "
            "do not claim an over-maximum original complete."
        )
    clauses.append(
        "These figures do not authorize extra calls, retries, or rewriting the saved prose. "
        "Return raw prose only."
    )
    return " ".join(clauses)


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


__all__ = [
    "ContinuationPlan",
    "ContinuationPlanner",
    "StableBoundaryKind",
    "stable_prose_boundary",
]
