"""Resolve pinned author unit targets without applying whole-book bounds to a chapter."""

from __future__ import annotations

from collections.abc import Mapping

from imaginarium_forge.domain.generation.durable import StepKind
from imaginarium_forge.domain.generation.length import (
    GenerationLengthSpec,
    LengthStrictness,
    LengthUnit,
)

UNIT_PROSE_KINDS = frozenset(
    {StepKind.PROSE_CHUNK, StepKind.TARGETED_REVISION, StepKind.LENGTH_ASSURANCE}
)


def build_unit_length_spec(
    whole: GenerationLengthSpec,
    target: int,
) -> GenerationLengthSpec:
    """A confirmed visible-character unit target is its own minimum, not the book minimum."""
    if whole.unit is not LengthUnit.VISIBLE_CHARS:
        raise ValueError("unit target contract requires visible characters")
    return GenerationLengthSpec(
        unit=whole.unit,
        minimum=target,
        target=target,
        maximum=None,
        segment_target=min(target, whole.segment_target or target),
        max_continuations=whole.max_continuations,
        strictness=LengthStrictness.MINIMUM_REQUIRED,
    )


def resolve_unit_length_spec(
    payload: Mapping[str, object],
    unit_id: str,
) -> GenerationLengthSpec | None:
    """Validate explicit unit pins, or derive old factory jobs from their saved exact targets.

    Legacy/direct API jobs without per-unit visible-character contracts retain
    their original final-only contract. No mutable preferences are consulted.
    """
    explicit = payload.get("unit_length_contracts")
    targets = payload.get("unit_target_visible_chars")
    if explicit is None and targets is None:
        return None
    whole = GenerationLengthSpec.model_validate(payload.get("generation_length_spec"))
    if payload.get("generation_length_spec_sha256") != whole.sha256:
        raise ValueError("whole length contract hash mismatch")
    units = payload.get("units")
    if not isinstance(units, list) or not isinstance(targets, list) or len(units) != len(targets):
        raise ValueError("unit targets do not match saved units")
    matched: list[GenerationLengthSpec] = []
    for unit, target in zip(units, targets, strict=True):
        if not isinstance(unit, Mapping) or not isinstance(unit.get("id"), str):
            raise ValueError("invalid saved unit")
        if isinstance(target, bool) or not isinstance(target, int) or target <= 0:
            raise ValueError("invalid saved unit target")
        if unit.get("target_words") != target:
            raise ValueError("unit target differs from confirmed target")
        expected = build_unit_length_spec(whole, target)
        if explicit is not None:
            if not isinstance(explicit, Mapping):
                raise ValueError("invalid unit length contracts")
            saved = explicit.get(unit["id"])
            if not isinstance(saved, Mapping):
                raise ValueError("missing unit length contract")
            actual = GenerationLengthSpec.model_validate(saved.get("spec"))
            if saved.get("sha256") != actual.sha256 or actual != expected:
                raise ValueError("unit length contract does not match confirmed target")
        if unit["id"] == unit_id:
            matched.append(expected)
    if len(matched) != 1:
        raise ValueError("unit length contract must identify one saved unit")
    return matched[0]
