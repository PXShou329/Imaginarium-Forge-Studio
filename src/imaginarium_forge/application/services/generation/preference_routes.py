"""Backward-compatible resolution for persisted AI Settings task categories."""

from __future__ import annotations

from imaginarium_forge.application.services.generation.contracts import GenerationTask
from imaginarium_forge.domain.generation.preferences import (
    AIRuntimePreferences,
    TaskRoutePreference,
)

_DEFAULT_CATEGORY_BY_TASK: dict[GenerationTask, str] = {
    GenerationTask.GENERAL: "general_draft",
    GenerationTask.STORY_FRAGMENT: "general_draft",
    GenerationTask.CHARACTER_DRAFT: "general_draft",
    GenerationTask.WORLD_SEED: "general_draft",
    GenerationTask.PROMPT_SCRATCH: "image_prompt",
    GenerationTask.STORY_CONTINUATION: "long_form",
    GenerationTask.LONG_FORM: "long_form",
}
_INSPIRATION_ACTIONS = frozenset({"creative_assist", "creative_automation"})
_IMAGE_PROMPT_ACTIONS = frozenset({"prompt_scene_parse"})
_REFINABLE_DRAFT_TASKS = frozenset(
    {
        GenerationTask.STORY_FRAGMENT,
        GenerationTask.CHARACTER_DRAFT,
        GenerationTask.WORLD_SEED,
    }
)


def task_route_preference(
    preferences: AIRuntimePreferences,
    task: GenerationTask,
    *,
    action_id: str = "",
    category: str = "",
) -> TaskRoutePreference | None:
    """Resolve an exact stable task key before a persisted UI category key.

    Exact keys remain supported for integrations and take precedence. The
    category fallback keeps the five task groups already written by AI Settings
    functional without renaming or migrating persisted JSON.
    """

    exact = preferences.task_routes.get(task.value)
    if exact is not None:
        return exact
    resolved_category = category.strip().casefold()
    if not resolved_category:
        if task is GenerationTask.GENERAL and action_id in _INSPIRATION_ACTIONS:
            resolved_category = "inspiration"
        elif task is GenerationTask.GENERAL and action_id in _IMAGE_PROMPT_ACTIONS:
            resolved_category = "image_prompt"
        elif task in _REFINABLE_DRAFT_TASKS and (
            action_id == "refine" or action_id.startswith("refine.")
        ):
            resolved_category = "refinement"
        else:
            resolved_category = _DEFAULT_CATEGORY_BY_TASK.get(task, "")
    if not resolved_category:
        return None
    return preferences.task_routes.get(resolved_category)


def refinement_route_preference(
    preferences: AIRuntimePreferences,
    task: GenerationTask,
) -> TaskRoutePreference | None:
    """Resolve universal refinement through its intentional UI category."""

    category = (
        "image_prompt"
        if task is GenerationTask.PROMPT_SCRATCH
        else "long_form"
        if task is GenerationTask.LONG_FORM
        else "refinement"
    )
    return task_route_preference(preferences, task, category=category)


__all__ = ["refinement_route_preference", "task_route_preference"]
