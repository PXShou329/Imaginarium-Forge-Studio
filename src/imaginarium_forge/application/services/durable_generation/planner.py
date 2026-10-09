"""Deterministic planner for persisted long-form creative workflows."""

from __future__ import annotations

from dataclasses import dataclass

from imaginarium_forge.application.services.durable_generation.contracts import (
    CreateDurableWorkflow,
    WorkflowUnit,
)
from imaginarium_forge.domain.generation.durable import (
    GenerationJob,
    GenerationStep,
    JobStatus,
    StepKind,
    sha256_json,
)
from imaginarium_forge.domain.generation.length import GenerationLengthSpec

_TEMPLATES: dict[StepKind, tuple[str, str]] = {
    StepKind.BRIEF_NORMALIZATION: ("durable.brief-normalization", "1"),
    StepKind.STORY_PLAN: ("durable.story-plan", "1"),
    StepKind.CHAPTER_OR_SCENE_PLAN: ("durable.unit-plan", "1"),
    StepKind.PROSE_CHUNK: ("durable.prose-chunk", "1"),
    StepKind.ROLLING_SUMMARY: ("durable.rolling-summary", "1"),
    StepKind.CONTINUITY_CHECK: ("durable.continuity-check", "1"),
    StepKind.TARGETED_REVISION: ("durable.targeted-revision", "1"),
    StepKind.LENGTH_ASSURANCE: ("durable.length-assurance-continuation", "1"),
    StepKind.FINAL_ASSEMBLY: ("durable.final-assembly", "1"),
    StepKind.FINAL_QUALITY_CHECK: ("durable.final-quality-check", "1"),
}


@dataclass(frozen=True)
class DurableWorkflowPlan:
    job: GenerationJob
    steps: tuple[GenerationStep, ...]


@dataclass(frozen=True)
class _StepSpec:
    kind: StepKind
    unit: WorkflowUnit | None
    dependencies: tuple[str, ...]


class LongFormWorkflowPlanner:
    """Build one stable graph; workflow units are references, not Story Scenes."""

    def plan(self, request: CreateDurableWorkflow) -> DurableWorkflowPlan:
        specs: list[_StepSpec] = []
        step_ids: list[str] = []

        def add(
            kind: StepKind,
            *,
            unit: WorkflowUnit | None = None,
            dependencies: tuple[str, ...] = (),
        ) -> str:
            step_id = f"{request.job_id}:step:{len(specs):04d}"
            specs.append(_StepSpec(kind=kind, unit=unit, dependencies=dependencies))
            step_ids.append(step_id)
            return step_id

        normalize_id = add(StepKind.BRIEF_NORMALIZATION)
        story_plan_id = add(StepKind.STORY_PLAN, dependencies=(normalize_id,))
        prior_summary_id: str | None = None
        unit_outputs: list[str] = []
        for unit in request.units:
            plan_dependencies: tuple[str, ...] = (story_plan_id,)
            if prior_summary_id is not None:
                plan_dependencies += (prior_summary_id,)
            unit_plan_id = add(
                StepKind.CHAPTER_OR_SCENE_PLAN,
                unit=unit,
                dependencies=plan_dependencies,
            )
            prose_id = add(
                StepKind.PROSE_CHUNK,
                unit=unit,
                dependencies=(unit_plan_id,),
            )
            summary_id = add(
                StepKind.ROLLING_SUMMARY,
                unit=unit,
                dependencies=(prose_id,),
            )
            continuity_id = add(
                StepKind.CONTINUITY_CHECK,
                unit=unit,
                dependencies=(prose_id, summary_id),
            )
            revision_id = add(
                StepKind.TARGETED_REVISION,
                unit=unit,
                dependencies=(prose_id, continuity_id),
            )
            length_id = add(
                StepKind.LENGTH_ASSURANCE,
                unit=unit,
                dependencies=(revision_id,),
            )
            prior_summary_id = summary_id
            unit_outputs.extend((revision_id, length_id, summary_id))

        assembly_id = add(
            StepKind.FINAL_ASSEMBLY,
            dependencies=tuple(unit_outputs),
        )
        add(StepKind.FINAL_QUALITY_CHECK, dependencies=(assembly_id,))

        max_attempts = request.length_spec.get("max_attempts_per_step")
        if max_attempts is None:
            raw_generation_spec = request.length_spec.get("generation_length_spec")
            if raw_generation_spec is None:
                max_attempts = 2
            else:
                generation_spec = GenerationLengthSpec.model_validate(raw_generation_spec)
                if (
                    request.length_spec.get("generation_length_spec_sha256")
                    != generation_spec.sha256
                ):
                    raise ValueError("generation length spec hash mismatch")
                repetition_retries = request.length_spec.get(
                    "max_repetition_retries",
                    2,
                )
                if (
                    isinstance(repetition_retries, bool)
                    or not isinstance(repetition_retries, int)
                    or not 0 <= repetition_retries <= 5
                ):
                    raise ValueError("max_repetition_retries must be an integer from 0 to 5")
                # The step attempt budget covers one deterministic/provider
                # result, logical continuations, bounded repetition retries,
                # and (when a maximum exists) one separate condensation call.
                # GenerationStep itself is the final hard cap at ten attempts.
                condensation_reserve = 1 if generation_spec.maximum is not None else 0
                max_attempts = min(
                    10,
                    1
                    + generation_spec.max_continuations
                    + repetition_retries
                    + condensation_reserve,
                )
        if not isinstance(max_attempts, int) or not 1 <= max_attempts <= 10:
            raise ValueError("max_attempts_per_step must be an integer from 1 to 10")

        steps: list[GenerationStep] = []
        for ordinal, (step_id, spec) in enumerate(zip(step_ids, specs, strict=True)):
            template_id, template_version = _TEMPLATES[spec.kind]
            planned_input: dict[str, object] = {
                "planned": {
                    "job_id": request.job_id,
                    "step_kind": spec.kind.value,
                    "unit": (spec.unit.model_dump(mode="json") if spec.unit is not None else None),
                    "dependency_step_ids": list(spec.dependencies),
                }
            }
            steps.append(
                GenerationStep(
                    id=step_id,
                    job_id=request.job_id,
                    ordinal=ordinal,
                    step_kind=spec.kind,
                    idempotency_key=f"{request.job_id}:workflow:{ordinal:04d}",
                    template_id=template_id,
                    template_version=template_version,
                    requested_provider=request.route.provider,
                    requested_model=request.route.model,
                    input_snapshot=planned_input,
                    input_snapshot_sha256=sha256_json(planned_input),
                    max_attempts=max_attempts,
                    created_at=request.created_at,
                    updated_at=request.created_at,
                )
            )

        job = GenerationJob(
            id=request.job_id,
            artifact_kind=request.artifact_kind,
            operation=request.operation,
            target_type=request.target_type,
            target_id=request.target_id,
            status=JobStatus.QUEUED,
            route=request.route.model_dump(mode="json"),
            quality_profile=request.quality_profile,
            length_spec={
                **request.length_spec,
                "units": [unit.model_dump(mode="json") for unit in request.units],
            },
            source_snapshot=request.source_snapshot,
            source_snapshot_sha256=sha256_json(request.source_snapshot),
            context_snapshot=request.context_snapshot,
            context_snapshot_sha256=sha256_json(request.context_snapshot),
            workflow_version=request.workflow_version,
            total_steps_hint=len(steps),
            created_at=request.created_at,
            updated_at=request.created_at,
        )
        return DurableWorkflowPlan(job=job, steps=tuple(steps))


__all__ = ["DurableWorkflowPlan", "LongFormWorkflowPlanner"]
