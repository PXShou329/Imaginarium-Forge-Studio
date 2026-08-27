"""UI service container — builds application services over one engine.

The UI layer only ever talks to these services (never raw SQL, never sessions).
IMF_DATA_DIR controls where the database lives, which lets Streamlit AppTest
point at a temporary directory.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import SecretStr

from imaginarium_forge.application.services.adult_output_review_service import (
    AdultOutputReviewService,
)
from imaginarium_forge.application.services.backup_service import BackupAppService
from imaginarium_forge.application.services.character_biography_draft_service import (
    CharacterBiographyDraftService,
)
from imaginarium_forge.application.services.character_service import (
    CharacterService,
    CharacterVersionService,
)
from imaginarium_forge.application.services.checkpoint_registry_service import (
    CheckpointRegistryService,
)
from imaginarium_forge.application.services.creative_assist_service import (
    CreativeAssistService,
)
from imaginarium_forge.application.services.creative_automation_service import (
    CreativeAutomationService,
)
from imaginarium_forge.application.services.creative_launch_service import (
    CreativeLaunchService,
)
from imaginarium_forge.application.services.creative_story_bootstrap_service import (
    CreativeStoryBootstrapService,
)
from imaginarium_forge.application.services.eligibility_service import EligibilityService
from imaginarium_forge.application.services.free_creation_inbox_service import (
    FreeCreationInboxService,
)
from imaginarium_forge.application.services.project_service import ProjectService
from imaginarium_forge.application.services.prompt_experiment_service import (
    PromptExperimentService,
)
from imaginarium_forge.application.services.prompt_export_service import (
    PromptExportService,
)
from imaginarium_forge.application.services.prompt_orchestration_service import (
    PromptOrchestrationService,
)
from imaginarium_forge.application.services.prompt_profile_service import (
    PromptProfileService,
)
from imaginarium_forge.application.services.prompt_scratch_autosave_service import (
    PromptScratchAutosaveService,
)
from imaginarium_forge.application.services.prompt_scratch_draft_service import (
    PromptScratchDraftService,
)
from imaginarium_forge.application.services.prompt_studio_service import (
    PromptCompilationService,
    PromptProjectService,
    PromptResolutionService,
)
from imaginarium_forge.application.services.scene_generation_service import (
    SceneDraftService,
)
from imaginarium_forge.application.services.screenplay_adaptation_service import (
    ScreenplayAdaptationService,
)
from imaginarium_forge.application.services.story_context_service import (
    StoryContextService,
)
from imaginarium_forge.application.services.story_export_service import (
    StoryExportService,
)
from imaginarium_forge.application.services.story_fragment_draft_service import (
    StoryFragmentDraftService,
)
from imaginarium_forge.application.services.story_generation_recovery_service import (
    StaleGenerationRunRecoveryService,
)
from imaginarium_forge.application.services.story_memory_service import StoryMemoryService
from imaginarium_forge.application.services.story_planning_service import (
    ChapterPlanService,
    SceneCardService,
    StoryBibleService,
    StoryOutlineService,
    StoryRequirementService,
)
from imaginarium_forge.application.services.style_service import OutfitService, StyleProfileService
from imaginarium_forge.application.services.video_prompt_bundle_service import (
    VideoPromptBundleService,
)
from imaginarium_forge.application.services.world_seed_draft_service import (
    WorldSeedDraftService,
)
from imaginarium_forge.config.settings import AppSettings, load_settings
from imaginarium_forge.infrastructure.db.lifecycle import DatabaseLifecycleManager
from imaginarium_forge.infrastructure.db.session import initialize_database
from imaginarium_forge.infrastructure.profiles.loader import ProfileLoader
from imaginarium_forge.providers.ollama import OllamaProvider
from imaginarium_forge.providers.openai import OpenAIProvider

SERVICES_SCHEMA_REVISION = "phase5-author-workflows-v12"
CreativeProviderName = Literal["ollama", "openai"]


def build_creative_provider(
    settings: AppSettings,
    *,
    provider_name: CreativeProviderName = "ollama",
    api_key: str | SecretStr | None = None,
) -> OllamaProvider | OpenAIProvider:
    """Build a text provider without performing I/O or persisting credentials.

    ``ollama`` stays the default.  The optional OpenAI key is passed directly
    into a runtime-only ``SecretStr``; if omitted, ``OpenAIProvider`` resolves
    ``OPENAI_API_KEY`` lazily from the process environment.
    """

    if provider_name == "openai":
        return OpenAIProvider(settings, api_key=api_key)
    return OllamaProvider(settings)


@dataclass
class Services:
    settings: AppSettings
    lifecycle: DatabaseLifecycleManager
    projects: ProjectService
    character_biography_drafts: CharacterBiographyDraftService
    characters: CharacterService
    versions: CharacterVersionService
    styles: StyleProfileService
    outfits: OutfitService
    eligibility: EligibilityService
    backups: BackupAppService
    prompt_profiles: PromptProfileService
    prompt_scratch_drafts: PromptScratchDraftService
    prompt_scratch_autosave: PromptScratchAutosaveService
    world_seed_drafts: WorldSeedDraftService
    story_fragment_drafts: StoryFragmentDraftService
    free_creation_inbox: FreeCreationInboxService
    prompt_projects: PromptProjectService
    prompt_resolution: PromptResolutionService
    prompt_compilation: PromptCompilationService
    prompt_orchestration: PromptOrchestrationService
    prompt_export: PromptExportService
    story_requirements: StoryRequirementService
    story_bibles: StoryBibleService
    story_outlines: StoryOutlineService
    chapter_plans: ChapterPlanService
    scene_cards: SceneCardService
    story_exports: StoryExportService
    story_context: StoryContextService
    story_memory: StoryMemoryService
    story_drafts: SceneDraftService
    screenplay_adaptations: ScreenplayAdaptationService
    story_run_recovery: StaleGenerationRunRecoveryService
    adult_output_reviews: AdultOutputReviewService
    creative_automation: CreativeAutomationService
    creative_launch: CreativeLaunchService
    creative_story_bootstrap: CreativeStoryBootstrapService
    creative_assist: CreativeAssistService
    video_prompts: VideoPromptBundleService
    experiments: PromptExperimentService
    checkpoints: CheckpointRegistryService
    schema_revision: str = SERVICES_SCHEMA_REVISION


def build_services(settings: AppSettings | None = None) -> Services:
    settings = settings or load_settings()
    initialize_database(settings.database_path)
    # One lifecycle manager owns the engine; it is the session provider for every
    # service, so restore can block/dispose/rebuild without touching page code.
    lifecycle = DatabaseLifecycleManager(settings.database_path)
    projects = ProjectService(lifecycle)
    characters = CharacterService(lifecycle)
    versions = CharacterVersionService(lifecycle)
    styles = StyleProfileService(lifecycle)
    outfits = OutfitService(lifecycle)
    eligibility = EligibilityService(lifecycle)
    profiles = PromptProfileService(ProfileLoader(settings.prompt_profiles_dir))
    prompt_resolution = PromptResolutionService(
        lifecycle,
        characters=characters,
        versions=versions,
        outfits=outfits,
        styles=styles,
        eligibility=eligibility,
    )
    prompt_compilation = PromptCompilationService(lifecycle, profiles)
    prompt_projects = PromptProjectService(lifecycle)
    story_requirements = StoryRequirementService(lifecycle)
    story_bibles = StoryBibleService(lifecycle)
    story_outlines = StoryOutlineService(lifecycle)
    creative_provider = build_creative_provider(settings)
    creative_launch = CreativeLaunchService(
        session_factory=lifecycle,
        projects=projects,
        characters=characters,
        versions=versions,
        eligibility=eligibility,
        prompt_projects=prompt_projects,
    )
    character_biography_drafts = CharacterBiographyDraftService(lifecycle)
    prompt_scratch_drafts = PromptScratchDraftService(lifecycle)
    world_seed_drafts = WorldSeedDraftService(lifecycle)
    story_fragment_drafts = StoryFragmentDraftService(lifecycle)
    return Services(
        settings=settings,
        lifecycle=lifecycle,
        projects=projects,
        character_biography_drafts=character_biography_drafts,
        characters=characters,
        versions=versions,
        styles=styles,
        outfits=outfits,
        eligibility=eligibility,
        backups=BackupAppService(settings, lifecycle),
        prompt_profiles=profiles,
        prompt_scratch_drafts=prompt_scratch_drafts,
        prompt_scratch_autosave=PromptScratchAutosaveService(lifecycle),
        world_seed_drafts=world_seed_drafts,
        story_fragment_drafts=story_fragment_drafts,
        free_creation_inbox=FreeCreationInboxService(
            character_drafts=character_biography_drafts,
            prompt_drafts=prompt_scratch_drafts,
            world_drafts=world_seed_drafts,
            story_fragment_drafts=story_fragment_drafts,
        ),
        prompt_projects=prompt_projects,
        prompt_resolution=prompt_resolution,
        prompt_compilation=prompt_compilation,
        prompt_export=PromptExportService(lifecycle),
        story_requirements=story_requirements,
        story_bibles=story_bibles,
        story_outlines=story_outlines,
        chapter_plans=ChapterPlanService(lifecycle),
        scene_cards=SceneCardService(lifecycle, eligibility=eligibility),
        story_exports=StoryExportService(lifecycle),
        story_context=StoryContextService(lifecycle),
        story_memory=StoryMemoryService(lifecycle),
        story_drafts=SceneDraftService(lifecycle),
        screenplay_adaptations=ScreenplayAdaptationService(
            lifecycle,
            eligibility=eligibility,
        ),
        story_run_recovery=StaleGenerationRunRecoveryService(
            lifecycle,
            stale_threshold_s=settings.stale_running_run_threshold_s,
        ),
        adult_output_reviews=AdultOutputReviewService(lifecycle, eligibility=eligibility),
        creative_automation=CreativeAutomationService(creative_provider),
        creative_launch=creative_launch,
        creative_story_bootstrap=CreativeStoryBootstrapService(lifecycle, eligibility=eligibility),
        creative_assist=CreativeAssistService(creative_provider),
        video_prompts=VideoPromptBundleService(lifecycle, eligibility=eligibility),
        prompt_orchestration=PromptOrchestrationService(
            lifecycle,
            resolution=prompt_resolution,
            compilation=prompt_compilation,
        ),
        experiments=PromptExperimentService(lifecycle),
        checkpoints=CheckpointRegistryService(lifecycle),
    )


def get_services() -> Services:
    """Cached per Streamlit session."""
    import streamlit as st

    cached = st.session_state.get("services")
    if (
        cached is None
        or not hasattr(cached, "video_prompts")
        or not hasattr(cached, "creative_automation")
        or not hasattr(cached, "story_memory")
        or not hasattr(cached, "character_biography_drafts")
        or not hasattr(cached, "prompt_scratch_drafts")
        or not hasattr(cached, "prompt_scratch_autosave")
        or not hasattr(cached, "world_seed_drafts")
        or not hasattr(cached, "story_fragment_drafts")
        or not hasattr(cached, "free_creation_inbox")
        or not hasattr(cached, "screenplay_adaptations")
        or getattr(cached, "schema_revision", "") != SERVICES_SCHEMA_REVISION
    ):
        # Streamlit keeps session_state across source hot reloads.  A session
        # holding the pre-Phase-4.3 container must receive the new dependency
        # graph atomically; rebuilding the old lifecycle alone cannot add a
        # missing service field.
        if cached is not None and hasattr(cached, "lifecycle"):
            cached.lifecycle.dispose()
        settings = cached.settings if cached is not None and hasattr(cached, "settings") else None
        st.session_state["services"] = build_services(settings)
    return st.session_state["services"]  # type: ignore[no-any-return]


def selected_project_id() -> str | None:
    import streamlit as st

    return st.session_state.get("selected_project_id")
