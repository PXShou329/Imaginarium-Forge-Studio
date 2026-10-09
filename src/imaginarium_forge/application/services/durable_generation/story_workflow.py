"""Build project-bound durable jobs from existing Story Studio contracts."""

from __future__ import annotations

from typing import Protocol

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    field_validator,
    model_validator,
)

from imaginarium_forge.application.errors import ConflictError, ValidationFailedError
from imaginarium_forge.application.ports.credentials import (
    OPENAI_CREDENTIAL_ID,
    CredentialResolver,
)
from imaginarium_forge.application.services.durable_generation.author_workspace import (
    LongFormPlanDraft,
    LongFormWorkspaceMode,
    long_form_plan_author_texts,
)
from imaginarium_forge.application.services.durable_generation.contracts import (
    CreateDurableWorkflow,
    DurableWorkflowRoute,
    WorkflowUnit,
)
from imaginarium_forge.application.services.durable_generation.runtime_step_runner import (
    durable_route_from_preferences,
)
from imaginarium_forge.application.services.durable_generation.unit_length_contracts import (
    build_unit_length_spec,
)
from imaginarium_forge.application.services.generation.context_compiler import (
    ContextCompiler,
)
from imaginarium_forge.application.services.generation.credential_content_guard import (
    assert_active_credential_not_in_content,
)
from imaginarium_forge.application.services.story_context_service import (
    BuildStoryContextRequest,
    ResolvedStoryContext,
)
from imaginarium_forge.domain.common.ids import new_id, utc_now_iso
from imaginarium_forge.domain.generation.durable import ArtifactKind, sha256_json
from imaginarium_forge.domain.generation.intent import GenerationIntent
from imaginarium_forge.domain.generation.length import (
    GenerationLengthSpec,
    LengthStrictness,
    LengthUnit,
)
from imaginarium_forge.domain.generation.preferences import (
    AIQualityProfile,
    AIRuntimePreferences,
)
from imaginarium_forge.infrastructure.db.repositories.story_repos import SceneRecord
from imaginarium_forge.providers.errors import ProviderConfigurationError
from imaginarium_forge.providers.ollama import (
    OllamaContextProfile,
    OllamaContextProfileDecision,
    resolve_ollama_context_profile,
)

MAX_STORY_PROTECTED_FACTS = 100
MAX_STORY_PROTECTED_FACT_CHARS = 1_000


def validated_story_protected_facts(values: tuple[str, ...]) -> tuple[str, ...]:
    """Normalize the explicit hard-fact contract without truncating content."""

    normalized = tuple(value.strip() for value in values)
    if any(not value for value in normalized):
        raise ValueError("作者鎖定事實不可留白")
    if len(normalized) > MAX_STORY_PROTECTED_FACTS:
        raise ValueError(
            f"作者鎖定事實最多 {MAX_STORY_PROTECTED_FACTS} 項；"
            "請合併或刪除項目後再建立正文工作，內容尚未被截短或保存。"
        )
    if any(len(value) > MAX_STORY_PROTECTED_FACT_CHARS for value in normalized):
        raise ValueError(
            f"每項作者鎖定事實最多 {MAX_STORY_PROTECTED_FACT_CHARS:,} 個字元；"
            "請自行拆分後再建立正文工作，內容尚未被截短或保存。"
        )
    folded = tuple(value.casefold() for value in normalized)
    if len(folded) != len(set(folded)):
        raise ValueError("作者鎖定事實不可重複")
    return normalized


class _PreferenceReader(Protocol):
    def get_preferences(self) -> AIRuntimePreferences: ...


class _StoryContextReader(Protocol):
    def build_for_scene(
        self,
        request: BuildStoryContextRequest,
    ) -> ResolvedStoryContext: ...


class _SceneReader(Protocol):
    def get_scene(self, scene_id: str) -> SceneRecord: ...


class CreateStoryDurableWorkflowCommand(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    project_id: str = Field(min_length=1, max_length=200)
    scene_id: str = Field(min_length=1, max_length=200)
    author_brief: str = Field(min_length=1, max_length=100_000)
    unit_titles: tuple[str, ...] = Field(min_length=1, max_length=100)
    target_words_per_unit: int = Field(default=1_500, ge=100, le=100_000)
    max_attempts_per_step: int = Field(default=3, ge=1, le=10)
    protected_facts: tuple[str, ...] = ()
    workspace_mode: LongFormWorkspaceMode = LongFormWorkspaceMode.ADVANCED
    workspace_plan: LongFormPlanDraft | None = None
    unit_scene_ids: tuple[str, ...] = Field(default=(), max_length=100)
    ollama_context_profile: OllamaContextProfileDecision = Field(
        default_factory=lambda: resolve_ollama_context_profile(OllamaContextProfile.AUTO)
    )
    job_id: str = Field(default_factory=lambda: f"story-long-{new_id()}")
    created_at: str = Field(default_factory=utc_now_iso)

    @field_validator("project_id", "scene_id", "job_id", "created_at")
    @classmethod
    def _identifiers(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("workflow identifiers must not be blank")
        return normalized

    @field_validator("author_brief")
    @classmethod
    def _brief(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("author brief must not be blank")
        return normalized

    @field_validator("unit_titles")
    @classmethod
    def _titles(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(value.strip() for value in values if value.strip())
        if not normalized:
            raise ValueError("at least one unit title is required")
        if len(normalized) != len(set(normalized)):
            raise ValueError("unit titles must be unique")
        return normalized

    @field_validator("protected_facts")
    @classmethod
    def _protected_facts(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return validated_story_protected_facts(values)

    @field_validator("unit_scene_ids")
    @classmethod
    def _unit_scene_ids(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(value.strip() for value in values)
        if any(not value for value in normalized):
            raise ValueError("unit scene IDs must not be blank")
        if len(normalized) != len(set(normalized)):
            raise ValueError("unit scene IDs must be unique")
        return normalized

    @model_validator(mode="after")
    def _workspace_plan_is_confirmed_and_exact(
        self,
    ) -> CreateStoryDurableWorkflowCommand:
        if self.unit_scene_ids and len(self.unit_scene_ids) != len(self.unit_titles):
            raise ValueError("unit scene IDs must match the number of units")
        if self.unit_scene_ids and self.unit_scene_ids[0] != self.scene_id:
            raise ValueError("the workflow anchor must be the first unit scene")
        plan = self.workspace_plan
        if plan is None:
            return self
        if not plan.confirmed:
            raise ValueError("the author must confirm the long-form plan before prose starts")
        if plan.mode is not self.workspace_mode:
            raise ValueError("workspace mode must match the confirmed plan")
        if tuple(unit.title for unit in plan.units) != self.unit_titles:
            raise ValueError("workflow unit titles must match the confirmed plan exactly")
        if len(plan.units) != len(self.unit_titles):
            raise ValueError("confirmed plan units must match workflow units")
        return self


class StoryDurableWorkflowFactory:
    """Snapshot accepted Story context, versions, and memory into one job."""

    def __init__(
        self,
        *,
        preferences: _PreferenceReader,
        context: _StoryContextReader,
        scenes: _SceneReader,
        credential_resolver: CredentialResolver | None = None,
    ) -> None:
        self._preferences = preferences
        self._context = context
        self._scenes = scenes
        self._credential_resolver = credential_resolver

    def build(
        self,
        command: CreateStoryDurableWorkflowCommand,
        *,
        session_override: SecretStr | str | None = None,
    ) -> CreateDurableWorkflow:
        self._assert_command_author_content_safe(
            command,
            session_override=session_override,
        )
        preferences = self._preferences.get_preferences()
        route, quality = self.resolve_long_form_route(preferences)
        scene = self._scenes.get_scene(command.scene_id)
        if scene.project_id != command.project_id:
            raise ConflictError("選取場景不屬於目前 Story 專案")
        resolved = self._context.build_for_scene(
            BuildStoryContextRequest(
                scene_id=command.scene_id,
                user_instruction=command.author_brief,
            )
        )
        if resolved.project_id != command.project_id:
            raise ConflictError("Story context 與目前專案不一致")

        if command.unit_scene_ids:
            for unit_scene_id in command.unit_scene_ids:
                unit_scene = self._scenes.get_scene(unit_scene_id)
                if unit_scene.project_id != command.project_id:
                    raise ConflictError("長篇規劃包含不屬於目前專案的場景")

        plan_units = (
            command.workspace_plan.units if command.workspace_plan is not None else ()
        )
        unit_targets = (
            tuple(unit.target_visible_chars for unit in plan_units)
            if plan_units
            else tuple(command.target_words_per_unit for _ in command.unit_titles)
        )
        unit_summaries = (
            tuple(unit.summary for unit in plan_units)
            if plan_units
            else tuple("" for _ in command.unit_titles)
        )
        unit_scene_ids = command.unit_scene_ids or tuple(
            scene.id for _ in command.unit_titles
        )
        units = tuple(
            WorkflowUnit(
                id=f"unit-{index:03d}",
                title=title,
                target_words=unit_targets[index - 1],
                metadata={
                    "anchor_story_scene_id": unit_scene_ids[index - 1],
                    "ordinal": index,
                    "author_plan_summary": unit_summaries[index - 1],
                },
            )
            for index, title in enumerate(command.unit_titles, start=1)
        )
        # R2 defines LONG_STORY in visible characters, not whitespace-delimited
        # words. The command/WorkflowUnit field names are retained as legacy
        # compatibility surfaces, but the authoritative typed contract and
        # evidence below state the actual counting rule explicitly.
        target_visible_chars_per_unit = command.target_words_per_unit
        total_target_visible_chars = sum(unit_targets)
        provider_attempt_ceiling = 2
        next_attempt_cost_ceiling = 8_000 * provider_attempt_ceiling
        total_cost_ceiling = next_attempt_cost_ceiling * command.max_attempts_per_step
        length_contract = GenerationLengthSpec(
            unit=LengthUnit.VISIBLE_CHARS,
            minimum=total_target_visible_chars,
            target=total_target_visible_chars,
            maximum=None,
            segment_target=max(unit_targets),
            max_continuations=min(3, command.max_attempts_per_step - 1),
            strictness=LengthStrictness.MINIMUM_REQUIRED,
        )
        unit_contracts = {
            unit.id: build_unit_length_spec(length_contract, unit.target_words)
            for unit in units
        }
        story_projection = ContextCompiler().project_story_context(resolved.package)
        context_snapshot = {
            **self._context_snapshot(resolved),
            "protected_facts": list(command.protected_facts),
            "story_context_projection": story_projection.snapshot.model_dump(mode="json"),
            "story_context_projection_sha256": story_projection.snapshot_sha256,
            "story_context_report": story_projection.report.model_dump(mode="json"),
        }
        return CreateDurableWorkflow(
            job_id=command.job_id,
            artifact_kind=ArtifactKind.LONG_STORY,
            target_type="story_scene",
            target_id=scene.id,
            route=route,
            quality_profile=quality.value,
            length_spec={
                "contract_version": "cwe-2.1-generation-length-spec-v1",
                "generation_intent": GenerationIntent.LONG_STORY.value,
                "generation_length_spec": length_contract.model_dump(mode="json"),
                "generation_length_spec_sha256": length_contract.sha256,
                "ollama_context_profile": command.ollama_context_profile.model_dump(mode="json"),
                "ollama_context_profile_sha256": sha256_json(
                    command.ollama_context_profile.model_dump(mode="json")
                ),
                "measurement_unit": LengthUnit.VISIBLE_CHARS.value,
                "target_visible_chars": total_target_visible_chars,
                "target_visible_chars_per_unit": target_visible_chars_per_unit,
                "unit_target_visible_chars": list(unit_targets),
                "unit_length_contracts": {
                    unit_id: {"spec": spec.model_dump(mode="json"), "sha256": spec.sha256}
                    for unit_id, spec in unit_contracts.items()
                },
                # Read-only compatibility aliases for already-created UI/API
                # clients. The typed contract above remains authoritative.
                "target_words": total_target_visible_chars,
                "target_words_per_unit": target_visible_chars_per_unit,
                "max_attempts_per_step": command.max_attempts_per_step,
                "max_total_attempts": command.max_attempts_per_step,
                "max_repetition_retries": 2,
                "provider_attempt_ceiling_per_call": provider_attempt_ceiling,
                "next_attempt_estimated_cost_microunits": next_attempt_cost_ceiling,
                "max_estimated_cost_microunits": total_cost_ceiling,
                "language": preferences.default_language,
            },
            source_snapshot={
                "brief": command.author_brief,
                "project_id": scene.project_id,
                "story_scene_id": scene.id,
                "scene_card_version_id": resolved.scene_card_version_id,
                "working_draft_id": scene.working_draft_id,
                "accepted_draft_id": scene.accepted_draft_id,
                "context_fingerprint": resolved.package.fingerprint,
                "workspace_mode": command.workspace_mode.value,
                "outline_confirmed": bool(
                    command.workspace_plan is not None and command.workspace_plan.confirmed
                ),
                "workspace_plan": (
                    command.workspace_plan.model_dump(mode="json")
                    if command.workspace_plan is not None
                    else None
                ),
                "workspace_plan_fingerprint": (
                    command.workspace_plan.fingerprint
                    if command.workspace_plan is not None
                    else ""
                ),
                "unit_story_scene_ids": list(command.unit_scene_ids),
            },
            context_snapshot=context_snapshot,
            units=units,
            created_at=command.created_at,
        )

    def resolve_long_form_route(
        self,
        preferences: AIRuntimePreferences | None = None,
    ) -> tuple[DurableWorkflowRoute, AIQualityProfile]:
        """Validate the non-secret long-form route before any durable writes."""

        current = preferences or self._preferences.get_preferences()
        return durable_route_from_preferences(current)

    def assert_author_content_safe(
        self,
        *author_texts: str,
        session_override: SecretStr | str | None = None,
    ) -> None:
        """Guard content before *any* planning anchor or durable row is stored."""

        resolver = self._credential_resolver
        if resolver is None:
            return
        try:
            resolved = resolver.resolve(
                OPENAI_CREDENTIAL_ID,
                session_override=session_override,
            )
        except Exception as exc:
            raise ProviderConfigurationError(
                "無法安全檢查目前憑證；長篇工作流尚未建立。"
            ) from exc
        assert_active_credential_not_in_content(resolved.secret, *author_texts)

    def _assert_command_author_content_safe(
        self,
        command: CreateStoryDurableWorkflowCommand,
        *,
        session_override: SecretStr | str | None,
    ) -> None:
        """Block an active key before any durable snapshot or row can exist."""

        self.assert_author_content_safe(
            command.author_brief,
            *command.unit_titles,
            *command.protected_facts,
            *(
                long_form_plan_author_texts(command.workspace_plan)
                if command.workspace_plan is not None
                else ()
            ),
            session_override=session_override,
        )

    @staticmethod
    def _context_snapshot(resolved: ResolvedStoryContext) -> dict[str, object]:
        package = resolved.package
        return {
            "project_id": resolved.project_id,
            "anchor_story_scene_id": resolved.scene_id,
            "content_mode": package.content_mode.value,
            "context_fingerprint": package.fingerprint,
            "context_schema_version": package.schema_version,
            "context_contract_version": package.contract_version,
            "context_renderer_version": package.renderer_version,
            "context_budget_policy_version": package.budget_policy_version,
            "context_budget_snapshot": package.budget_snapshot.model_dump(mode="json"),
            "planning_mode": package.planning_mode,
            "planning_chain_fingerprint": package.planning_chain_fingerprint,
            "system_message": package.system_message,
            "user_message": package.user_message,
            "version_references": [
                {
                    "entity_type": reference.entity_type,
                    "entity_id": reference.entity_id,
                }
                for reference in package.version_references
            ],
            "character_version_ids": list(package.character_version_ids),
            "pov_character_version_id": package.pov_character_version_id,
            "eligibility_fingerprint": package.eligibility_fingerprint,
            "source_refs": [reference.canonical() for reference in package.source_refs],
            "memory_fingerprint": package.memory_fingerprint,
            "memory_proposal_ids": list(package.memory_proposal_ids),
            "memory_entry_ids": list(package.memory_entry_ids),
            "memory_gap_scene_ids": list(package.memory_gap_scene_ids),
            "stale_memory_proposal_ids": list(package.stale_memory_proposal_ids),
            "structured_must_avoid": list(package.structured_must_avoid),
            "memory_projection": resolved.memory_projection.model_dump(mode="json"),
        }


__all__ = [
    "MAX_STORY_PROTECTED_FACTS",
    "MAX_STORY_PROTECTED_FACT_CHARS",
    "CreateStoryDurableWorkflowCommand",
    "StoryDurableWorkflowFactory",
    "validated_story_protected_facts",
]
