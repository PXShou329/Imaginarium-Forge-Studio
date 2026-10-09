"""Replay explicit author amendments without changing the original job contract."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from imaginarium_forge.application.services.durable_generation.unit_length_contracts import (
    build_unit_length_spec,
    resolve_unit_length_spec,
)
from imaginarium_forge.domain.generation.durable import GenerationJob, GenerationStep, sha256_json
from imaginarium_forge.domain.generation.length import GenerationLengthSpec
from imaginarium_forge.domain.generation.unit_author_decision import UnitAuthorDecision

AUTHOR_POLICY_KEY = "unit_author_policy"


def step_unit_id(step: GenerationStep) -> str:
    planned = step.input_snapshot.get("planned")
    unit = planned.get("unit") if isinstance(planned, dict) else None
    identity = unit.get("id") if isinstance(unit, dict) else None
    if not isinstance(identity, str) or not identity:
        raise ValueError("unit author policy requires a saved unit")
    return identity


def validate_decisions(job: GenerationJob, decisions: Sequence[UnitAuthorDecision]) -> None:
    previous = ""
    for sequence, decision in enumerate(decisions, 1):
        payload = decision.payload
        if (
            decision.job_id != job.id
            or decision.sequence != sequence
            or payload.get("previous_decision_sha256") != previous
            or payload.get("original_length_spec_sha256") != sha256_json(job.length_spec)
            or payload.get("original_source_sha256") != job.source_snapshot_sha256
        ):
            raise ValueError("unit author decision chain does not match the original job")
        previous = decision.payload_sha256


def effective_spec(
    job: GenerationJob,
    unit_id: str,
    step_id: str,
    decisions: Sequence[UnitAuthorDecision],
) -> GenerationLengthSpec:
    validate_decisions(job, decisions)
    original = resolve_unit_length_spec(job.length_spec, unit_id)
    if original is None:
        raise ValueError("unit author policy requires a typed unit contract")
    target, continuations = original.target, original.max_continuations
    for decision in decisions:
        if decision.unit_id != unit_id:
            continue
        if decision.kind == "adjust_target":
            value = decision.payload.get("target_visible_chars")
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 1_000_000:
                raise ValueError("invalid explicit author target")
            target = value
        elif decision.step_id == step_id:
            value = decision.payload.get("max_continuations")
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 5:
                raise ValueError("invalid explicit author continuation bound")
            continuations = value
    spec = build_unit_length_spec(original, target)
    return GenerationLengthSpec.model_validate(
        {
            **spec.model_dump(mode="json"),
            "max_continuations": continuations,
        }
    )


def granted_attempt_limit(step_id: str, decisions: Sequence[UnitAuthorDecision]) -> int | None:
    result = None
    for decision in decisions:
        if decision.kind == "grant_continuation" and decision.step_id == step_id:
            value = decision.payload.get("max_total_attempts")
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 10:
                raise ValueError("invalid explicit author attempt bound")
            result = value
    return result


def projection(
    job: GenerationJob,
    step: GenerationStep,
    decisions: Sequence[UnitAuthorDecision],
) -> dict[str, object]:
    unit_id = step_unit_id(step)
    spec = effective_spec(job, unit_id, step.id, decisions)
    payload: dict[str, object] = {
        "job_id": job.id,
        "step_id": step.id,
        "unit_id": unit_id,
        "original_length_spec_sha256": sha256_json(job.length_spec),
        "decision_head_sha256": decisions[-1].payload_sha256 if decisions else "",
        "generation_length_spec": spec.model_dump(mode="json"),
        "generation_length_spec_sha256": spec.sha256,
    }
    return {**payload, "sha256": sha256_json(payload)}


def projected_spec(
    value: object,
    *,
    length_payload: Mapping[str, object],
    step_id: str,
    unit_id: str,
) -> GenerationLengthSpec:
    if not isinstance(value, dict):
        raise ValueError("invalid author policy projection")
    payload = dict(value)
    saved_hash = payload.pop("sha256", None)
    spec = GenerationLengthSpec.model_validate(payload.get("generation_length_spec"))
    if (
        saved_hash != sha256_json(payload)
        or payload.get("step_id") != step_id
        or payload.get("unit_id") != unit_id
        or payload.get("original_length_spec_sha256") != sha256_json(dict(length_payload))
        or payload.get("generation_length_spec_sha256") != spec.sha256
    ):
        raise ValueError("author policy projection pin mismatch")
    return spec
