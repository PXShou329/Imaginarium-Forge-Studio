"""Prepare durable Story requests without exposing credentials to feature pages.

Preparation is not a provider action: it must inspect the active credential even
for an offline route, but must never consume a one-action key or create a lease.
Only a validated, secret-free workflow request crosses back to the page.
"""

from __future__ import annotations

from typing import Protocol

from imaginarium_forge.application.services.durable_generation.author_workspace import (
    LongFormPlanDraft,
    long_form_job_id,
)
from imaginarium_forge.application.services.durable_generation.author_workspace_anchors import (
    AuthorWorkspacePlanningServices,
    ensure_long_form_story_anchors,
)
from imaginarium_forge.application.services.durable_generation.contracts import (
    CreateDurableWorkflow,
)
from imaginarium_forge.application.services.durable_generation.story_workflow import (
    CreateStoryDurableWorkflowCommand,
    StoryDurableWorkflowFactory,
    validated_story_protected_facts,
)
from imaginarium_forge.domain.generation.ollama_context import (
    OllamaContextProfile,
    resolve_ollama_context_profile,
)
from imaginarium_forge.ui.openai_session import (
    SessionStateStore,
    read_openai_session_settings,
)


class DurableStoryPreparationServices(Protocol):
    @property
    def story_durable_workflows(self) -> StoryDurableWorkflowFactory: ...


class LongFormPreparationServices(
    DurableStoryPreparationServices, AuthorWorkspacePlanningServices, Protocol
):
    pass


def prepare_story_durable_workflow(
    services: DurableStoryPreparationServices,
    state: SessionStateStore,
    *,
    project_id: str,
    scene_id: str,
    author_brief: str,
    unit_titles: tuple[str, ...],
    target_words_per_unit: int,
    max_attempts_per_step: int,
) -> CreateDurableWorkflow:
    """Validate an existing scene's request without using or retaining a key."""

    command = CreateStoryDurableWorkflowCommand(
        project_id=project_id,
        scene_id=scene_id,
        author_brief=author_brief,
        unit_titles=unit_titles,
        target_words_per_unit=target_words_per_unit,
        max_attempts_per_step=max_attempts_per_step,
    )
    session_secret = read_openai_session_settings(state).api_key
    try:
        return services.story_durable_workflows.build(
            command, session_override=session_secret
        )
    finally:
        session_secret = None


def prepare_confirmed_long_form_workflow(
    services: LongFormPreparationServices,
    state: SessionStateStore,
    *,
    project_id: str,
    plan: LongFormPlanDraft,
    max_attempts: int,
    context_profile: OllamaContextProfile,
    custom_num_ctx: int | None,
) -> CreateDurableWorkflow:
    """Guard all author text before creating any idempotent planning anchors."""

    # Preserve every hard fact; invalid facts must fail before anchor writes.
    protected_facts = validated_story_protected_facts(
        tuple(
            line
            for source in (plan.character_world_constraints, plan.author_constraints)
            for line in source.splitlines()
            if line.strip()
        )
    )
    session_secret = read_openai_session_settings(state).api_key
    try:
        def content_preflight(texts: tuple[str, ...]) -> None:
            services.story_durable_workflows.assert_author_content_safe(
                *texts, session_override=session_secret
            )
            services.story_durable_workflows.resolve_long_form_route()

        anchors = ensure_long_form_story_anchors(
            services,
            project_id=project_id,
            plan=plan,
            content_preflight=content_preflight,
        )
        command = CreateStoryDurableWorkflowCommand(
            project_id=project_id,
            scene_id=anchors.primary_scene_id,
            author_brief=plan.author_idea,
            unit_titles=tuple(unit.title for unit in plan.units),
            target_words_per_unit=max(
                100, plan.total_target_visible_chars // len(plan.units)
            ),
            max_attempts_per_step=max_attempts,
            protected_facts=protected_facts,
            workspace_mode=plan.mode,
            workspace_plan=plan,
            unit_scene_ids=anchors.scene_ids,
            job_id=long_form_job_id(project_id, plan),
            ollama_context_profile=resolve_ollama_context_profile(
                context_profile, custom_num_ctx=custom_num_ctx
            ),
        )
        return services.story_durable_workflows.build(
            command, session_override=session_secret
        )
    finally:
        session_secret = None


__all__ = [
    "DurableStoryPreparationServices",
    "LongFormPreparationServices",
    "prepare_confirmed_long_form_workflow",
    "prepare_story_durable_workflow",
]
