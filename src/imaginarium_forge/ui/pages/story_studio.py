"""Story planning, version selection, generation, and editing.

Planning and draft versions are accepted explicitly. Generation uses selected
character versions, supports streaming and cancellation, and preserves the
author's editor when a provider request fails."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, replace
from enum import StrEnum
from threading import Event
from typing import Any

import streamlit as st

from imaginarium_forge.application.error_incidents import (
    map_and_record_user_facing_error,
)
from imaginarium_forge.application.errors import ApplicationError, EmptyStoryMemoryProposalError
from imaginarium_forge.application.services.generation.contracts import GenerationTask
from imaginarium_forge.application.services.generation.output_measurement import (
    measure_output,
)
from imaginarium_forge.application.services.ollama_model_service import (
    ollama_model_ids_match,
)
from imaginarium_forge.application.services.scene_generation_service import (
    RevisionOperation,
    RevisionRequest,
)
from imaginarium_forge.application.services.screenplay_adaptation_service import (
    GenerateScreenplayRequest,
    ScreenplayGenerationService,
    screenplay_length_spec_for_minutes,
)
from imaginarium_forge.application.services.screenplay_generation_job_manager import (
    ScreenplayGenerationJobManager,
    ScreenplayGenerationJobNotFoundError,
    ScreenplayGenerationJobStatus,
    ScreenplayGenerationJobTerminal,
)
from imaginarium_forge.application.services.story_assist_service import (
    StoryAssistService,
)
from imaginarium_forge.application.services.story_context_service import (
    HistoricalContextDisplayMode,
)
from imaginarium_forge.application.services.story_export_service import (
    StoryExportMode,
    StoryExportReproductionStatus,
)
from imaginarium_forge.application.services.story_generation_job_manager import (
    StoryGenerationJobEventKind,
    StoryGenerationJobManager,
    StoryGenerationJobStatus,
)
from imaginarium_forge.application.services.story_orchestration_service import (
    GenerateSceneRequest,
    ReasonCode,
    ReviseSceneRequest,
    StoryGenerationOrchestrationService,
    StoryRevisionOrchestrationService,
)
from imaginarium_forge.application.user_facing_errors import (
    ErrorDataSafety,
    ErrorMappingContext,
    RecoveryAction,
    RecoveryActionKind,
    UserFacingError,
)
from imaginarium_forge.domain.adaptation.models import (
    AdaptationSourceHealth,
    DialogueRetention,
    ScreenplayBrief,
    ScreenplayPacing,
)
from imaginarium_forge.domain.adaptation.random_brief import (
    RandomScreenplayBrief,
    generate_random_screenplay_brief,
)
from imaginarium_forge.domain.generation.length import (
    GenerationLengthSpec,
    LengthStrictness,
    LengthUnit,
)
from imaginarium_forge.domain.generation.ollama_context import (
    OLLAMA_STANDARD_NUM_CTX,
    OllamaContextProfile,
    OllamaContextProfileDecision,
    OllamaModelCapabilitySnapshot,
    resolve_ollama_context_profile,
)
from imaginarium_forge.domain.prompt.content_mode import ContentMode
from imaginarium_forge.domain.story.context import ContextBudgetSnapshot
from imaginarium_forge.domain.story.continuation import (
    ContinuationGoal,
    compose_story_continuation,
)
from imaginarium_forge.domain.story.generation_input import PlanningMode
from imaginarium_forge.domain.story.memory import (
    StoryConsistencySeverity,
    StoryMemoryChange,
    StoryMemoryKind,
    StoryMemoryOperation,
    StoryMemoryProposalOrigin,
    StoryMemoryProposalPayload,
    StoryMemoryProposalStatus,
)
from imaginarium_forge.domain.story.models import (
    ChapterPlan,
    IntensityLevel,
    NarrativePov,
    NarrativeTense,
    SceneCard,
    StoryBible,
    StoryOutline,
    StoryRequirement,
    StructureProfile,
)
from imaginarium_forge.domain.story.short_story import (
    CompleteStoryBrief,
    CompleteStoryBriefMode,
    StoryGenerationPurpose,
    generate_random_complete_story_brief,
)
from imaginarium_forge.providers.contracts import GenerationOptions
from imaginarium_forge.providers.errors import (
    InvalidProviderResponseError,
    ModelNotFoundError,
    ProviderError,
    ProviderRefusalError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from imaginarium_forge.ui.ai_runtime import (
    GenerationMode,
    GenerationRuntimeConfig,
    OpenAIModelPreset,
)
from imaginarium_forge.ui.bootstrap import get_services
from imaginarium_forge.ui.components import page_header
from imaginarium_forge.ui.content_length import assess_text_length
from imaginarium_forge.ui.durable_story_workflow import render_durable_story_workflow
from imaginarium_forge.ui.error_presenter import present_exception, render_error_card
from imaginarium_forge.ui.generation_actions import (
    capture_generation_provider_action,
    consume_openai_action_credential,
    generation_action_readiness,
    openai_action_readiness,
)
from imaginarium_forge.ui.openai_session import (
    apply_openai_session_state_transitions,
)
from imaginarium_forge.ui.project_gateway import ProjectGatewayCopy, render_project_gateway
from imaginarium_forge.ui.provider_diagnostics import (
    diagnose_provider_reason,
)
from imaginarium_forge.ui.writing_request_controls import (
    OLLAMA_CAPABILITY_SNAPSHOT_SESSION_KEY,
)


def _lines(raw: str) -> tuple[str, ...]:
    return tuple(line.strip() for line in raw.splitlines() if line.strip())


def _short(value: str | None) -> str:
    return "" if not value else f"{value[:8]}…"


_CONTINUATION_GOAL_LABELS = {
    ContinuationGoal.FOLLOW_OUTLINE.value: "依照大綱自然接續",
    ContinuationGoal.DEEPEN_CHARACTER.value: "深化角色與關係",
    ContinuationGoal.ADVANCE_CONFLICT.value: "推進主要衝突",
    ContinuationGoal.CLOSE_CURRENT_SCENE.value: "收束目前場景",
    ContinuationGoal.EXPAND_EXCERPT.value: "擴寫我選的片段",
    ContinuationGoal.REWRITE_EXCERPT.value: "重寫我選的片段",
    ContinuationGoal.DIALOGUE_FOCUS.value: "以對話帶動這一段",
    ContinuationGoal.DESCRIPTION_FOCUS.value: "加強場景與感官描寫",
    ContinuationGoal.REPAIR_CONTINUITY.value: "修補前後矛盾",
}

_AI_ACTIVE_MODE_KEY = "story_active_ai_mode"
_AI_ACTIVE_RETENTION_KEY = "story_active_ai_key_retention"
_STORY_SCENE_INPUT_SCOPE_KEY = "story_scene_input_scope"
_STORY_GENERATION_TASK_KEY = "story_generation_task"
_STORY_GENERATION_PREVIOUS_TASK_KEY = "_story_generation_previous_task"
_STORY_JOB_POLL_ERROR_KEY = "story_job_poll_error"
_STORY_PROSE_LOADED_DRAFT_KEY = "story_prose_loaded_draft_id"
_STORY_PROSE_EDITOR_SNAPSHOT_KEY = "_story_prose_editor_snapshot"
_STORY_ACCEPT_DRAFT_MEMORY_ACK_KEY = "story_accept_draft_memory_replacement_ack"
_STORY_ACCEPT_DRAFT_MEMORY_ACK_SCOPE_KEY = "story_accept_draft_memory_replacement_scope"
_SCREENPLAY_JOB_MANAGER_KEY = "story_screenplay_job_manager"
_SCREENPLAY_ACTIVE_JOB_ID_KEY = "story_screenplay_active_job_id"
_SCREENPLAY_ACTIVE_JOB_SCENE_KEY = "story_screenplay_active_job_scene_id"
_SCREENPLAY_ACTIVE_JOB_ADAPTATION_KEY = "story_screenplay_active_job_adaptation_id"
_SCREENPLAY_ACTIVE_JOB_STATUS_KEY = "story_screenplay_active_job_status"
_SCREENPLAY_ACTIVE_PROVIDER_LABEL_KEY = "story_screenplay_active_provider_label"
_SCREENPLAY_JOB_GENERATED_VALUES_KEY = "story_screenplay_job_generated_values"
_SCREENPLAY_LAST_TERMINAL_KEY = "story_screenplay_last_terminal"
_SCREENPLAY_SAFE_PROVIDER_LABELS = frozenset({"OpenAI", "Ollama", "模型服務"})

_SCREENPLAY_TRANSIENT_KEYS = (
    "story_screenplay_manual_text",
    "story_screenplay_editor",
    "story_screenplay_editor_scope",
    "story_screenplay_change_note",
    "story_screenplay_revision_id",
    "story_screenplay_pending_revision_id",
    "story_screenplay_pending_brief",
    "story_screenplay_random_seed",
    "story_screenplay_random_notice",
)

_SCREENPLAY_ROOT_BRIEF_KEYS = (
    "story_screenplay_target_minutes",
    "story_screenplay_pacing",
    "story_screenplay_dialogue_retention",
    "story_screenplay_direction",
    "story_screenplay_must_include",
    "story_screenplay_must_avoid",
)

_STORY_GENERATION_PURPOSE_LABELS = {
    StoryGenerationPurpose.SCENE.value: "單一場景／續寫",
    StoryGenerationPurpose.COMPLETE_SHORT_STORY.value: "完整短篇故事候選",
}

_STORY_OLLAMA_CONTEXT_PROFILE_LABELS = {
    OllamaContextProfile.AUTO: "自動（依模型與硬體）",
    OllamaContextProfile.STANDARD: "標準上下文",
    OllamaContextProfile.LONG: "長上下文",
    OllamaContextProfile.CUSTOM: "自訂（進階）",
}

_COMPLETE_STORY_MODE_LABELS = {
    CompleteStoryBriefMode.GUIDED.value: "引導創作｜我提供素材，也可全部留空",
    CompleteStoryBriefMode.RANDOM.value: "隨機創作｜先取得可編輯靈感",
}

_KEY_RETENTION_SESSION = "session"
_KEY_RETENTION_ONE_ACTION = "one_action"

_GENERATION_RUN_STATUS_LABELS = {
    "queued": "等待中",
    "running": "進行中",
    "completed": "已完成",
    "succeeded": "已完成",
    "partial": "未達長度，保留 partial",
    "failed": "失敗",
    "cancelled": "已取消",
}


def _generation_run_status_label(value: object) -> str:
    """Render stored run states in creator-facing Traditional Chinese."""

    return _GENERATION_RUN_STATUS_LABELS.get(str(value), "狀態未明")


_MEMORY_KIND_LABELS = {
    StoryMemoryKind.FACT: "已確定的事",
    StoryMemoryKind.TIMELINE: "時間與地點",
    StoryMemoryKind.FORESHADOWING: "伏筆與未解線",
    StoryMemoryKind.CHARACTER_STATE: "角色此刻的狀態",
}

_MEMORY_OPERATION_LABELS = {
    StoryMemoryOperation.ASSERT: "新增一項記憶",
    StoryMemoryOperation.SUPERSEDE: "更新既有記憶",
    StoryMemoryOperation.RETRACT: "撤回既有記憶",
}

_MEMORY_STATUS_LABELS = {
    StoryMemoryProposalStatus.PENDING: "等你確認",
    StoryMemoryProposalStatus.ACCEPTED: "已收進故事記憶",
    StoryMemoryProposalStatus.REJECTED: "已略過",
}


class StoryStudioSection(StrEnum):
    """Stable, presentation-independent identifiers for Story Studio sections."""

    REQUIREMENT = "requirement"
    BIBLE = "bible"
    OUTLINE = "outline"
    SCENE_CARD = "scene_card"
    GENERATION = "generation"
    ADAPTATION = "adaptation"
    CONTEXT = "context"
    EXPORT = "export"
    MEMORY = "memory"


_STORY_SECTION_LABELS = {
    StoryStudioSection.REQUIREMENT: "需求",
    StoryStudioSection.BIBLE: "聖經",
    StoryStudioSection.OUTLINE: "大綱／章節",
    StoryStudioSection.SCENE_CARD: "Scene Card",
    StoryStudioSection.GENERATION: "寫故事／完整故事",
    StoryStudioSection.ADAPTATION: "劇本改編",
    StoryStudioSection.CONTEXT: "上下文",
    StoryStudioSection.EXPORT: "匯出",
    StoryStudioSection.MEMORY: "故事記憶",
}
_STORY_SECTION_OPTIONS = tuple(section.value for section in StoryStudioSection)
_STORY_ACTIVE_SECTION_KEY = "story_active_section"
_STORY_ENTRY_NOTICE_KEY = "story_requested_entry_label"
_STORY_ACTIVE_SECTION_DURABLE_KEY = "_story_active_section_durable"
_STORY_STRUCTURE_SELECTION_KEY = "_story_structure_selection"
_STORY_STRUCTURE_WIDGET_KEYS = (
    "story_req_id", "story_bible_id", "story_outline_id", "story_chapter_id", "story_scene_id",
)

# Streamlit deletes widget state when a widget is not mounted. These are only
# author inputs and selectors owned by the nine lazy sections; buttons,
# Preview acknowledgement and job lifecycle keys are deliberately
# excluded. On a section transition we detach the outgoing section's values
# from widget cleanup so an in-session round trip is lossless.
_STORY_SECTION_WIDGET_KEYS: dict[StoryStudioSection, tuple[str, ...]] = {
    StoryStudioSection.REQUIREMENT: (
        "story_concept",
        "story_genre",
        "story_tone",
        "story_pov",
        "story_tense",
        "story_conflict",
        "story_themes",
        "story_must",
        "story_avoid",
        "story_violence",
        "story_horror",
        "story_req_mode",
        "story_free_text",
        "story_req_version_pick",
    ),
    StoryStudioSection.BIBLE: (
        "story_bible_title",
        "story_logline",
        "story_world",
        "story_prohibited",
        "story_forbidden",
        "story_promise",
        "story_bible_char_name",
        "story_bible_link",
        "story_bible_version_pick",
    ),
    StoryStudioSection.OUTLINE: (
        "story_structure",
        "story_reveals",
        "story_withheld",
        "story_ending",
        "story_outline_version_pick",
        "story_ch_purpose",
        "story_ch_goal",
        "story_ch_conflict",
        "story_ch_outcome",
        "story_plan_version_pick",
    ),
    StoryStudioSection.SCENE_CARD: (
        "story_location",
        "story_time",
        "story_goal_p",
        "story_goal_n",
        "story_conf_ext",
        "story_conf_int",
        "story_entry_emotion",
        "story_beats",
        "story_turning",
        "story_exit_emotion",
        "story_card_forbidden",
        "story_card_must",
        "story_card_avoid",
        "story_target_words",
        "story_card_mode",
        "story_participants",
        "story_pov_participant",
        "story_card_version_pick",
    ),
    StoryStudioSection.GENERATION: (
        _STORY_GENERATION_TASK_KEY,
        "story_complete_mode",
        "story_complete_world_premise",
        "story_complete_story_seed",
        "story_complete_structure_outline",
        "story_complete_additional_direction",
        "story_complete_must_include",
        "story_complete_must_avoid",
        "story_complete_min_chars",
        "story_complete_max_chars",
        "story_complete_pacing",
        "story_complete_random_seed",
        "story_complete_random_preserve",
        "story_generation_preview",
        "story_keywords",
        "story_continuation_goal",
        "story_instruction",
        "story_continuation_source_excerpt",
        "story_continuation_must_include",
        "story_continuation_must_avoid",
        "story_continuation_use_target_words",
        "story_continuation_length_mode",
        "story_continuation_target_words",
        "story_continuation_max_chars",
        "story_continuation_pacing",
        "story_context_total_chars",
        "story_context_requirement_chars",
        "story_context_world_chars",
        "story_context_characters_chars",
        "story_context_recent_chars",
        "story_context_author_chars",
        "story_context_memory_chars",
        "story_ollama_context_profile",
        "story_ollama_custom_num_ctx",
        "story_stream",
        "story_manual_prose",
        "story_draft_id",
        "story_prose_view",
        _STORY_PROSE_LOADED_DRAFT_KEY,
        "story_rev_op",
        "story_rev_instruction",
        "story_rev_events",
        "story_rev_order",
        "story_rev_dialogue",
        "story_summary",
    ),
    StoryStudioSection.ADAPTATION: (
        "story_adaptation_id",
        "story_adaptation_title",
        "story_screenplay_target_minutes",
        "story_screenplay_pacing",
        "story_screenplay_dialogue_retention",
        "story_screenplay_direction",
        "story_screenplay_must_include",
        "story_screenplay_must_avoid",
        "story_screenplay_ollama_context_profile",
        "story_screenplay_ollama_custom_num_ctx",
        "story_screenplay_manual_text",
        "story_screenplay_revision_id",
        "story_screenplay_editor",
        "story_screenplay_change_note",
    ),
    StoryStudioSection.CONTEXT: ("story_inspect_run_id",),
    StoryStudioSection.EXPORT: (
        "story_export_mode",
        "story_export_preview",
        "story_rebuild_export_id",
    ),
    StoryStudioSection.MEMORY: (
        "story_memory_manual_operation",
        "story_memory_manual_kind",
        "story_memory_manual_subject",
        "story_memory_manual_attribute",
        "story_memory_manual_target",
        "story_memory_manual_value",
        "story_memory_manual_source",
        "story_memory_proposal_id",
        "story_memory_decision_note",
    ),
}


@dataclass(frozen=True, slots=True)
class _StoryAIRuntimeView:
    """Non-persistent UI projection of one author-selected AI runtime."""

    mode: GenerationMode
    config: GenerationRuntimeConfig | None
    configured: bool
    key_retention: str = _KEY_RETENTION_SESSION
    validation_error: str = ""

    @property
    def can_send(self) -> bool:
        return self.config is not None and self.config.uses_llm and self.configured

    @property
    def model(self) -> str:
        return self.config.model if self.config is not None else ""


@dataclass(frozen=True, slots=True)
class _CapturedProvider:
    provider: Any
    provider_name: str
    owned: bool
    release: Callable[[], None] | None = None


def _clear_screenplay_transient_state() -> None:
    """Drop editor and review acknowledgements when their scope changes."""

    for key in _SCREENPLAY_TRANSIENT_KEYS:
        st.session_state.pop(key, None)


def _clear_screenplay_root_state() -> None:
    """Drop all inputs that belong to one screenplay adaptation root."""

    _clear_screenplay_transient_state()
    for key in _SCREENPLAY_ROOT_BRIEF_KEYS:
        st.session_state.pop(key, None)


def _clear_screenplay_selection_scope() -> None:
    """Clear root-owned inputs before a selectbox rerun."""

    _clear_screenplay_root_state()


def _normalize_story_section(value: object) -> StoryStudioSection:
    """Fail safely to the historical first tab when session state is stale."""

    try:
        return StoryStudioSection(str(value))
    except (TypeError, ValueError):
        return StoryStudioSection.REQUIREMENT


def _story_section_label(value: str) -> str:
    return _STORY_SECTION_LABELS[_normalize_story_section(value)]


def _remember_story_active_section() -> None:
    section = _normalize_story_section(st.session_state.get(_STORY_ACTIVE_SECTION_KEY))
    previous_raw = st.session_state.get(_STORY_ACTIVE_SECTION_DURABLE_KEY)
    if previous_raw is not None and _normalize_story_section(previous_raw) is not section:
        previous = _normalize_story_section(previous_raw)
        if StoryStudioSection.ADAPTATION in {previous, section}:
            _clear_screenplay_transient_state()
    st.session_state[_STORY_ACTIVE_SECTION_DURABLE_KEY] = section.value


def _render_story_active_section() -> StoryStudioSection:
    """Render the single-section navigation and restore it across page routes."""

    raw = st.session_state.get(
        _STORY_ACTIVE_SECTION_KEY,
        st.session_state.get(_STORY_ACTIVE_SECTION_DURABLE_KEY),
    )
    restored = _normalize_story_section(raw)
    # Rebind even an equal value: after the long-form shell unmounts pills,
    # Streamlit otherwise restores only its Python value and sends no selected
    # value to the new browser widget (which has no default selection).
    st.session_state[_STORY_ACTIVE_SECTION_KEY] = restored.value

    selected = st.pills(
        "故事工作區",
        _STORY_SECTION_OPTIONS,
        selection_mode="single",
        required=True,
        format_func=_story_section_label,
        key=_STORY_ACTIVE_SECTION_KEY,
        help="一次只載入目前編輯區；切換不會保存或接受任何正式內容。",
        on_change=_remember_story_active_section,
        width="stretch",
    )
    active = _normalize_story_section(selected)
    st.session_state[_STORY_ACTIVE_SECTION_DURABLE_KEY] = active.value
    st.caption("一次只載入目前編輯區；背景生成仍會持續更新。")
    return active


def _queue_story_generation_entry(purpose: StoryGenerationPurpose) -> None:
    """Stage a writing destination without generating or persisting content."""

    st.session_state[_STORY_ENTRY_NOTICE_KEY] = (
        "開始寫一個場景" if purpose is StoryGenerationPurpose.SCENE else "生成完整短篇"
    )
    pending = st.session_state.get("story_pending")
    staged = dict(pending) if isinstance(pending, dict) else {}
    staged.update(
        {
            _STORY_ACTIVE_SECTION_KEY: StoryStudioSection.GENERATION.value,
            _STORY_GENERATION_TASK_KEY: purpose.value,
        }
    )
    st.session_state["story_pending"] = staged
    st.session_state[_STORY_ACTIVE_SECTION_DURABLE_KEY] = StoryStudioSection.GENERATION.value
    st.session_state[_STORY_GENERATION_PREVIOUS_TASK_KEY] = purpose.value
    st.session_state["story_longform_workspace_open"] = False


def _queue_long_form_entry(intent: str) -> None:
    """Open N4 without requiring a pre-existing Chapter or Scene Card."""

    st.session_state[_STORY_ENTRY_NOTICE_KEY] = (
        "繼續上次工作" if intent == "continue" else "規劃長篇故事"
    )
    pending = st.session_state.get("story_pending")
    staged = dict(pending) if isinstance(pending, dict) else {}
    staged[_STORY_ACTIVE_SECTION_KEY] = StoryStudioSection.GENERATION.value
    st.session_state["story_pending"] = staged
    st.session_state[_STORY_ACTIVE_SECTION_DURABLE_KEY] = StoryStudioSection.GENERATION.value
    st.session_state["story_longform_workspace_open"] = True
    st.session_state["story_longform_entry_intent"] = intent


def _queue_screenplay_entry() -> None:
    st.session_state[_STORY_ENTRY_NOTICE_KEY] = "規劃劇本"
    pending = st.session_state.get("story_pending")
    staged = dict(pending) if isinstance(pending, dict) else {}
    staged[_STORY_ACTIVE_SECTION_KEY] = StoryStudioSection.ADAPTATION.value
    st.session_state["story_pending"] = staged
    st.session_state[_STORY_ACTIVE_SECTION_DURABLE_KEY] = StoryStudioSection.ADAPTATION.value
    st.session_state["story_longform_workspace_open"] = False


def _render_story_generation_shortcuts(*, active: bool) -> None:
    """Expose all five R2 writing destinations without invoking providers."""

    with st.container(key="story_direct_writing_entries", border=True):
        st.markdown("#### 直接開始寫")
        st.caption(
            "先選你現在要做的事。這裡只切換工作區，不會呼叫模型、"
            "儲存草稿或接受正式內容；長篇可直接從一個想法開始。"
        )
        requested_entry = st.session_state.get(_STORY_ENTRY_NOTICE_KEY)
        if not st.session_state.get("selected_project_id") and requested_entry in (
            "開始寫一個場景", "生成完整短篇", "規劃長篇故事", "規劃劇本", "繼續上次工作",
        ):
            st.info(
                f"已選擇「{requested_entry}」。請在下方開啟或建立一本作品，"
                "再回到故事寫作繼續。目前尚未生成或保存任何內容。"
            )
        scene_column, complete_column, long_column = st.columns(3)
        scene_column.button(
            "開始寫一個場景",
            key="story_shortcut_scene",
            type="primary",
            disabled=active,
            width="stretch",
            on_click=_queue_story_generation_entry,
            args=(StoryGenerationPurpose.SCENE,),
        )
        complete_column.button(
            "生成完整短篇｜手寫／隨機／AI",
            key="story_shortcut_complete",
            disabled=active,
            width="stretch",
            on_click=_queue_story_generation_entry,
            args=(StoryGenerationPurpose.COMPLETE_SHORT_STORY,),
        )
        long_column.button(
            "規劃長篇故事",
            key="story_shortcut_long_form",
            width="stretch",
            on_click=_queue_long_form_entry,
            args=("new",),
        )
        screenplay_column, continue_column = st.columns(2)
        screenplay_column.button(
            "規劃劇本",
            key="story_shortcut_screenplay",
            disabled=active,
            width="stretch",
            on_click=_queue_screenplay_entry,
        )
        continue_column.button(
            "繼續上次工作",
            key="story_shortcut_continue_long_form",
            width="stretch",
            on_click=_queue_long_form_entry,
            args=("continue",),
        )


def _render_story_generation_readiness(
    *,
    requirement_id: str,
    bible_id: str,
    outline_id: str,
    chapter_id: str,
    scene_id: str,
    has_card_version: bool,
) -> None:
    """Show an honest structural checklist when generation cannot mount yet."""

    checks = (
        (bool(requirement_id), "已選擇故事需求", "在左側建立並選擇故事需求。"),
        (bool(bible_id), "已選擇故事聖經", "在左側建立並選擇故事聖經。"),
        (bool(outline_id), "已選擇故事大綱", "在左側建立並選擇大綱。"),
        (bool(chapter_id), "已選擇章節", "在左側的大綱下新增並選擇章節。"),
        (bool(scene_id), "已選擇場景", "在左側的章節下新增並選擇場景。"),
        (
            has_card_version,
            "已有 Scene Card 版本",
            "切到「Scene Card」工作區，先儲存一個版本。",
        ),
    )
    with st.container(border=True):
        st.markdown("#### 寫作準備度")
        st.caption("續寫場景與完整故事都會沿用這本作品目前選定的故事設定與角色版本。")
        for ready, label, _next_step in checks:
            st.markdown(f"- {'✓' if ready else '○'} {label}")
        next_step = next((message for ready, _label, message in checks if not ready), "")
        if next_step:
            st.info(f"下一步：{next_step}")


def _preserve_story_section_state() -> None:
    """Keep section inputs alive across a real Streamlit widget remount."""

    # Rebind both outgoing and incoming keys before any section-owned widget
    # is instantiated. Rebinding only the inactive section is insufficient:
    # on the return run Streamlit has detached the old widget identity, and
    # the incoming key must be marked as user-owned before it is remounted.
    for keys in _STORY_SECTION_WIDGET_KEYS.values():
        for key in keys:
            if key in st.session_state:
                st.session_state[key] = st.session_state[key]


def _remember_story_prose_editor() -> None:
    """Keep one selected editor in session memory, independent of widget cleanup."""

    draft_id = st.session_state.get(_STORY_PROSE_LOADED_DRAFT_KEY)
    prose = st.session_state.get("story_prose_view")
    if isinstance(draft_id, str) and isinstance(prose, str):
        st.session_state[_STORY_PROSE_EDITOR_SNAPSHOT_KEY] = (draft_id, prose)


def _restore_story_structure_selection(
    project_id: str, *, pending_keys: frozenset[str] = frozenset(),
) -> None:
    """Rebind selectors within one project, restoring missing values after unmounting."""

    snapshot = st.session_state.get(_STORY_STRUCTURE_SELECTION_KEY)
    if (
        not isinstance(snapshot, tuple)
        or len(snapshot) != 2
        or snapshot[0] != project_id
        or not isinstance(snapshot[1], dict)
    ):
        return
    for key in _STORY_STRUCTURE_WIDGET_KEYS:
        value = st.session_state.get(key)
        # Route cleanup can leave an empty widget value rather than remove its
        # key. An explicit pending navigation still owns even an empty value.
        if key not in pending_keys and (value is None or value == ""):
            value = snapshot[1].get(key)
        if isinstance(value, str):
            st.session_state[key] = value


def _continuation_goal_label(value: str) -> str:
    return _CONTINUATION_GOAL_LABELS[value]


def _apply_story_ai_state_transitions() -> None:
    """Clear short-lived secrets before any related widget is instantiated."""
    apply_openai_session_state_transitions(st.session_state)


def _schedule_ai_action_cleanup(*, mode: GenerationMode, key_retention: str) -> None:
    """Consume an explicitly one-action key through the shared boundary."""
    if mode is not GenerationMode.OPENAI:
        return
    if key_retention == _KEY_RETENTION_ONE_ACTION:
        consume_openai_action_credential(st.session_state)


def _schedule_active_job_ai_cleanup() -> None:
    st.session_state.pop(_AI_ACTIVE_MODE_KEY, None)
    # The provider captured its credential and `_remember_active_runtime`
    # already consumed a one-action key at dispatch. A terminal event may
    # arrive after the author has entered a different key, so it must never
    # consume the current global credential a second time.
    st.session_state.pop(_AI_ACTIVE_RETENTION_KEY, None)


def _render_ai_runtime(services: Any, *, active: bool) -> _StoryAIRuntimeView:
    """Render the live global final-writing route without page overrides."""
    with st.container(border=True):
        st.subheader("寫作引擎（全域）")
        st.caption("provider、模型與 fallback 只由「AI 設定」管理；本頁不另行覆寫。")
        try:
            readiness = generation_action_readiness(
                services,
                st.session_state,
                task=GenerationTask.FINAL_WRITING,
                action_id="story_scene",
            )
        except ProviderError:
            st.info("全域 final-writing route 尚未可用；手寫與離線靈感仍可照常使用。")
            return _StoryAIRuntimeView(
                mode=GenerationMode.NO_LLM,
                config=GenerationRuntimeConfig(mode=GenerationMode.NO_LLM),
                configured=False,
                validation_error="global_route_unavailable",
            )

        provider_id = readiness.route.primary.provider_id
        model = readiness.route.primary.model
        if provider_id == "ollama":
            from imaginarium_forge.ui.pages.creative_launchpad import _endpoint_summary

            endpoint = _endpoint_summary(services.settings.ollama_base_url)
            configured = endpoint.valid and endpoint.is_loopback and not active
            st.caption(f"全域 route：本機 Ollama · {model}")
            if not endpoint.valid:
                st.error(f"模型端點設定無效：{endpoint.redacted_origin}。")
            elif not endpoint.is_loopback:
                st.error(
                    f"Story Studio 只允許 loopback Ollama；目前端點為 "
                    f"{endpoint.redacted_origin}。請到 AI 設定改用本機端點或 OpenAI。"
                )
            return _StoryAIRuntimeView(
                mode=GenerationMode.LOCAL,
                config=GenerationRuntimeConfig(
                    mode=GenerationMode.LOCAL,
                    local_model=model,
                ),
                configured=configured,
                validation_error="" if configured else "local_model_not_ready",
            )

        if provider_id == "openai":
            openai = openai_action_readiness(services, st.session_state)
            configured = readiness.credential_available and openai.remote_enabled and not active
            st.caption(f"全域 route：OpenAI · {model}")
            if not readiness.credential_available:
                st.info(
                    "請到 AI 設定提供本次工作階段 Key、裝置安全保存 Key，"
                    "或環境變數 OPENAI_API_KEY。"
                )
            else:
                st.warning(
                    "全域 AI 設定已啟用 OpenAI；這次生成所需故事內容會送往遠端。"
                    "API Key 不會進入提示詞或作品。"
                )
            return _StoryAIRuntimeView(
                mode=GenerationMode.OPENAI,
                config=GenerationRuntimeConfig(
                    mode=GenerationMode.OPENAI,
                    openai_model_preset=OpenAIModelPreset.CUSTOM,
                    custom_openai_model=model,
                ),
                configured=configured,
                key_retention=openai.key_retention.value,
                validation_error="" if configured else "openai_not_ready",
            )

        st.info("全域 AI provider 為離線；手寫與離線靈感仍可照常使用。")
        return _StoryAIRuntimeView(
            mode=GenerationMode.NO_LLM,
            config=GenerationRuntimeConfig(mode=GenerationMode.NO_LLM),
            configured=False,
            validation_error="global_route_offline",
        )


def _friendly_provider_error(reason_code: str, error_detail: str = "") -> str:
    """Map normalized provider evidence to a useful message without echoing it."""
    reason = str(reason_code or "").casefold()
    detail = str(error_detail or "").casefold()
    if "authentication" in detail or "api key" in detail or "credential" in detail:
        return "OpenAI 驗證失敗：請確認 API Key 有效且具備此模型的使用權限。"
    if any(marker in detail for marker in ("account quota", "billing limit", "insufficient_quota")):
        return "OpenAI 回報帳戶額度或計費限制：請檢查 API 帳戶用量、餘額與付費狀態。"
    if "rate limit" in detail or "429" in detail:
        return "OpenAI 暫時限制請求速率：請稍後重試；若持續發生，再檢查帳戶使用上限。"
    if reason == ReasonCode.MODEL_NOT_FOUND or ("model" in detail and "not found" in detail):
        return "找不到或無權使用所選模型：請改選另一個模型，或確認模型 ID。"
    if reason == ReasonCode.PROVIDER_TIMEOUT or "timed out" in detail:
        return "模型回應逾時：可稍後重試、縮短上下文，或檢查網路狀態。"
    if reason == ReasonCode.PROVIDER_UNAVAILABLE or any(
        marker in detail for marker in ("cannot reach", "connect", "network", "unavailable")
    ):
        return "無法連線到目前的模型服務：請檢查網路、OpenAI 服務或本機 Ollama。"
    return diagnose_provider_reason(reason).message_zh_tw


def _terminal_failure_exception(reason_code: object) -> ProviderError:
    """Map persisted allow-listed evidence to a typed, message-safe failure."""

    reason = str(reason_code or "")
    if reason == ReasonCode.PROVIDER_TIMEOUT:
        return ProviderTimeoutError("story background operation timed out")
    if reason in {ReasonCode.PROVIDER_UNAVAILABLE, ReasonCode.PROCESS_INTERRUPTED}:
        return ProviderUnavailableError("story background operation was interrupted")
    if reason == ReasonCode.MODEL_NOT_FOUND:
        return ModelNotFoundError("story background model is unavailable")
    if reason in {
        ReasonCode.EMPTY_OUTPUT,
    }:
        return InvalidProviderResponseError("story background response was invalid")
    if reason in {
        ReasonCode.PROVIDER_REFUSED,
    }:
        return ProviderRefusalError("story background provider declined the request")
    return ProviderError("story background operation failed")


def _compose_continuation_instruction(
    *,
    keywords: str,
    goal: str,
    additional_instruction: str,
    source_excerpt: str,
    must_include: str,
    must_avoid: str,
    target_words: int | None = None,
    min_chars: int | None = None,
    max_chars: int | None = None,
    pacing: str = "",
) -> str:
    selected_goal = ContinuationGoal(goal or ContinuationGoal.FOLLOW_OUTLINE.value)
    has_author_direction = any(
        (
            keywords.strip(),
            additional_instruction.strip(),
            source_excerpt.strip(),
            must_include.strip(),
            must_avoid.strip(),
            min_chars is not None,
            max_chars is not None,
            target_words is not None,
            pacing.strip(),
            selected_goal is not ContinuationGoal.FOLLOW_OUTLINE,
        )
    )
    if not has_author_direction:
        return ""
    return compose_story_continuation(
        keywords,
        goal=selected_goal,
        additional_instruction=additional_instruction,
        source_excerpt=source_excerpt,
        must_include=_lines(must_include),
        # Prohibitions travel through the typed request field below so they
        # remain a distinct, fail-closed context source instead of arbitrary
        # author instruction text.
        must_avoid=(),
        min_chars=min_chars,
        max_chars=max_chars,
        target_words=target_words,
        pacing=pacing,
    )


def _story_generation_purpose_label(value: str) -> str:
    return _STORY_GENERATION_PURPOSE_LABELS[value]


def _normalize_story_generation_purpose(value: object) -> StoryGenerationPurpose:
    try:
        return StoryGenerationPurpose(str(value))
    except (TypeError, ValueError):
        return StoryGenerationPurpose.SCENE


def _remember_story_generation_purpose() -> None:
    """Remember the exact writing task shown by the current editor."""

    current = _normalize_story_generation_purpose(st.session_state.get(_STORY_GENERATION_TASK_KEY))
    st.session_state[_STORY_GENERATION_PREVIOUS_TASK_KEY] = current.value


def _complete_story_mode_label(value: str) -> str:
    return _COMPLETE_STORY_MODE_LABELS[value]


def _complete_story_pending_values(
    brief: CompleteStoryBrief,
    *,
    preserve_written: bool = False,
) -> dict[str, object]:
    """Map one immutable brief back to the exact editable widget projection."""

    values: dict[str, object] = {
        "story_complete_mode": brief.mode.value,
        "story_complete_world_premise": brief.world_premise,
        "story_complete_story_seed": brief.story_seed,
        "story_complete_structure_outline": brief.structure_outline,
        "story_complete_additional_direction": brief.additional_direction,
        "story_complete_must_include": "\n".join(brief.must_include),
        "story_complete_must_avoid": "\n".join(brief.must_avoid),
        "story_complete_min_chars": brief.min_visible_chars,
        "story_complete_max_chars": brief.max_visible_chars,
        "story_complete_pacing": brief.pacing,
        "story_complete_random_seed": brief.random_seed,
    }
    if preserve_written:
        for key in tuple(values):
            if key in {"story_complete_mode", "story_complete_random_seed"}:
                continue
            current = st.session_state.get(key)
            if current is not None and (not isinstance(current, str) or current.strip()):
                values[key] = current
    return values


def _queue_complete_story_brief(
    brief: CompleteStoryBrief,
    *,
    preserve_written: bool = False,
) -> None:
    """Stage widget values for the next rerun without mutating mounted widgets."""

    pending = st.session_state.get("story_pending")
    staged = dict(pending) if isinstance(pending, dict) else {}
    staged.update(
        _complete_story_pending_values(
            brief,
            preserve_written=preserve_written,
        )
    )
    st.session_state["story_pending"] = staged


def _fill_random_complete_story_brief(preserve_written: bool) -> None:
    """Queue one offline brief in the button callback before the page reruns."""

    _queue_complete_story_brief(
        generate_random_complete_story_brief(),
        preserve_written=preserve_written,
    )
    st.session_state["story_complete_random_notice"] = (
        "已離線填入可編輯靈感；已填內容依你的保留選項維持不變。"
    )


def _render_complete_story_brief(*, active: bool) -> CompleteStoryBrief | None:
    """Render an editable brief and return its validated immutable projection."""

    with st.container(border=True):
        st.subheader("完整短篇故事候選")
        st.caption("輸出只會建立目前場景的一份未接受候選；不會自動接受，也不會覆寫正式正文。")
        random_notice = st.session_state.pop("story_complete_random_notice", None)
        if random_notice:
            st.success(str(random_notice))
        st.session_state.setdefault("story_complete_min_chars", 1_800)
        st.session_state.setdefault("story_complete_max_chars", 5_000)
        st.session_state.setdefault("story_complete_pacing", "均衡推進，在結尾完整收束")
        random_action_slot = st.container()
        mode_raw = st.radio(
            "素材準備方式",
            options=tuple(mode.value for mode in CompleteStoryBriefMode),
            format_func=_complete_story_mode_label,
            key="story_complete_mode",
            horizontal=True,
            disabled=active,
        )
        mode = CompleteStoryBriefMode(mode_raw)
        world_premise = st.text_area(
            "簡易世界觀／世界前提（選填）",
            key="story_complete_world_premise",
            height=120,
            max_chars=4_000,
            disabled=active,
            help="可以只提供幾條世界規則；留空時仍會使用目前作品的正式規劃鏈。",
        )
        story_seed = st.text_area(
            "短故事／故事種子（選填）",
            key="story_complete_story_seed",
            height=120,
            max_chars=4_000,
            disabled=active,
            help="可貼一段短故事、開場、概念或想延伸的事件。",
        )
        structure_outline = st.text_area(
            "故事大綱／架構（選填）",
            key="story_complete_structure_outline",
            height=150,
            max_chars=6_000,
            disabled=active,
            help="可寫起承轉合、三幕結構或自由段落；不必使用固定格式。",
        )
        additional_direction = st.text_area(
            "補充寫作方向（選填）",
            key="story_complete_additional_direction",
            max_chars=2_000,
            disabled=active,
        )
        include_col, avoid_col = st.columns(2)
        must_include = include_col.text_area(
            "一定要出現（每行一項，最多 12 項）",
            key="story_complete_must_include",
            disabled=active,
        )
        must_avoid = avoid_col.text_area(
            "不要出現（每行一項，最多 12 項）",
            key="story_complete_must_avoid",
            disabled=active,
        )
        length_left, length_right = st.columns(2)
        minimum = int(
            length_left.number_input(
                "期望最少字數",
                min_value=500,
                max_value=30_000,
                step=100,
                key="story_complete_min_chars",
                disabled=active,
            )
        )
        maximum = int(
            length_right.number_input(
                "期望最多字數",
                min_value=500,
                max_value=40_000,
                step=100,
                key="story_complete_max_chars",
                disabled=active,
            )
        )
        pacing = st.text_input(
            "完整故事節奏",
            key="story_complete_pacing",
            max_chars=300,
            disabled=active,
        )
        random_seed_raw = st.session_state.get("story_complete_random_seed")
        random_seed = (
            random_seed_raw
            if isinstance(random_seed_raw, int) and not isinstance(random_seed_raw, bool)
            else None
        )
        if random_seed is not None:
            st.caption(
                f"離線靈感種子：`{random_seed}`；欄位若經編輯，正式保存內容仍會保留編輯後全文。"
            )

        with random_action_slot:
            st.session_state.setdefault("story_complete_random_preserve", True)
            random_action, random_preserve = st.columns((2, 3), vertical_alignment="center")
            preserve_complete_story = random_preserve.checkbox(
                "保留已填寫素材與片長／節奏，只補空白文字",
                key="story_complete_random_preserve",
                disabled=active,
            )
            random_action.button(
                "✦ 離線隨機填入可編輯靈感",
                key="story_complete_random_fill",
                disabled=active,
                width="stretch",
                help="只使用內建素材；不寫入資料庫，也不呼叫 Ollama 或 OpenAI。",
                on_click=_fill_random_complete_story_brief,
                args=(preserve_complete_story,),
            )

        if minimum > maximum:
            st.error("完整故事最少字數不可大於最多字數。")
            return None
        if mode is CompleteStoryBriefMode.RANDOM and random_seed is None:
            st.info("請先離線隨機填入靈感，或使用下方的一鍵隨機生成。")
            return None
        try:
            return CompleteStoryBrief(
                mode=mode,
                world_premise=world_premise,
                story_seed=story_seed,
                structure_outline=structure_outline,
                additional_direction=additional_direction,
                must_include=_lines(must_include),
                must_avoid=_lines(must_avoid),
                min_visible_chars=minimum,
                max_visible_chars=maximum,
                pacing=pacing,
                random_seed=(random_seed if mode is CompleteStoryBriefMode.RANDOM else None),
            )
        except ValueError:
            st.error("完整故事素材超出欄位限制；請縮短內容或將清單控制在 12 項內。")
            return None


def _length_output_token_budget(
    minimum: int | None,
    maximum: int | None,
) -> int | None:
    """Give longer requested prose room without bypassing provider-side caps."""

    requested_chars = maximum or minimum
    if requested_chars is None:
        return None
    return max(2_048, min(24_000, requested_chars * 2))


def _story_visible_char_length_spec(
    minimum: int | None,
    maximum: int | None,
    *,
    target_hint: int,
    max_continuations: int,
) -> GenerationLengthSpec:
    """Build the exact enforceable Story prose contract shown by this page."""

    target_hint = max(1, target_hint)
    if minimum is None and maximum is None:
        target = target_hint
        minimum = max(100, round(target * 0.75))
        maximum = max(target, round(target * 1.25))
    elif minimum is None:
        assert maximum is not None
        target = min(target_hint, maximum)
        minimum = min(maximum, max(1, round(target * 0.6)))
    elif maximum is None:
        target = max(minimum, target_hint)
    else:
        target = min(maximum, max(minimum, target_hint))
    return GenerationLengthSpec(
        unit=LengthUnit.VISIBLE_CHARS,
        minimum=minimum,
        target=target,
        maximum=maximum,
        segment_target=None,
        max_continuations=max_continuations,
        strictness=(
            LengthStrictness.BOUNDED if maximum is not None else LengthStrictness.MINIMUM_REQUIRED
        ),
    )


def _resolve_story_ollama_context_selection(
    *,
    provider_name: str | None,
    model: str,
    profile: OllamaContextProfile | str,
    custom_num_ctx: int | None,
    raw_capability_snapshot: object = None,
) -> tuple[OllamaContextProfileDecision | None, str]:
    """Resolve only model-matched Ollama evidence; never reuse it cross-route.

    The notice is a stable UI diagnostic. A missing/invalid/stale snapshot is
    not guessed from model size: the resolver returns its conservative,
    explicitly warned unknown-capability decision. CUSTOM remains exact or
    raises when known limits prove it impossible.
    """

    if provider_name != "ollama":
        return None, "non_ollama"

    capability: OllamaModelCapabilitySnapshot | None = None
    notice = "capability_unknown"
    if raw_capability_snapshot is not None:
        try:
            candidate = OllamaModelCapabilitySnapshot.model_validate(
                raw_capability_snapshot
            )
        except (TypeError, ValueError):
            notice = "capability_invalid"
        else:
            if ollama_model_ids_match(candidate.model, model):
                capability = candidate
                notice = "capability_model_matched"
            else:
                notice = "capability_model_mismatch"

    return (
        resolve_ollama_context_profile(
            profile,
            custom_num_ctx=custom_num_ctx,
            model_context_limit=(
                capability.model_context_limit if capability is not None else None
            ),
            model_output_limit=(
                capability.model_output_limit if capability is not None else None
            ),
            hardware_context_limit=(
                capability.hardware_context_limit if capability is not None else None
            ),
            capability_model=capability.model if capability is not None else None,
            capability_source=capability.source if capability is not None else None,
        ),
        notice,
    )


def _render_story_ollama_context_controls(
    runtime: _StoryAIRuntimeView,
    *,
    active: bool,
    key_prefix: str = "story_ollama",
    heading: str = "Ollama 上下文（進階）",
) -> OllamaContextProfileDecision | None:
    """Render the four R2 profiles only for the active Ollama route."""

    provider_name = runtime.config.provider_name if runtime.config is not None else None
    if provider_name != "ollama":
        return None

    with st.expander(heading, expanded=False):
        profile_options = tuple(OllamaContextProfile)
        selected = st.selectbox(
            "Ollama context profile",
            options=profile_options,
            format_func=_STORY_OLLAMA_CONTEXT_PROFILE_LABELS.__getitem__,
            key=f"{key_prefix}_context_profile",
            disabled=active,
        )
        profile = OllamaContextProfile(selected)
        custom_num_ctx: int | None = None
        if profile is OllamaContextProfile.CUSTOM:
            custom_num_ctx = int(
                st.number_input(
                    "自訂 num_ctx",
                    min_value=1,
                    value=OLLAMA_STANDARD_NUM_CTX,
                    step=1_024,
                    key=f"{key_prefix}_custom_num_ctx",
                    disabled=active,
                )
            )
        try:
            decision, notice = _resolve_story_ollama_context_selection(
                provider_name=provider_name,
                model=runtime.model,
                profile=profile,
                custom_num_ctx=custom_num_ctx,
                raw_capability_snapshot=st.session_state.get(
                    OLLAMA_CAPABILITY_SNAPSHOT_SESSION_KEY
                ),
            )
        except (TypeError, ValueError):
            st.error(
                "Ollama 上下文設定無效；已阻止生成，且不會偷偷降低自訂 num_ctx。"
            )
            return None
        assert decision is not None
        if notice == "capability_model_mismatch":
            st.warning(
                "已保存的 Ollama 能力快照屬於另一個模型，本次不會套用；"
                "請到狀態頁重新檢查目前模型。"
            )
        elif notice == "capability_invalid":
            st.warning("已保存的 Ollama 能力快照無效，本次不會套用。")
        st.caption(
            f"本次實際 num_ctx={decision.num_ctx:,}｜"
            f"num_predict cap={decision.num_predict_cap:,}｜"
            f"rationale={decision.rationale}"
        )
        if decision.capability_model is not None:
            st.caption(
                f"能力快照：{decision.capability_model}｜"
                f"source={decision.capability_source}"
            )
        if decision.warning is not None:
            st.warning(decision.warning)
        else:
            st.caption("能力與資源 warning：無")
        st.caption("選擇只會建立本次 request；不會載入模型或自動開始生成。")
        return decision


def _render_context_budget_controls() -> ContextBudgetSnapshot:
    """Render creator-language controls for the exact context sent this run."""
    for key, default in (
        ("story_context_total_chars", 32_000),
        ("story_context_requirement_chars", 7_000),
        ("story_context_world_chars", 5_000),
        ("story_context_characters_chars", 7_000),
        ("story_context_recent_chars", 4_000),
        ("story_context_author_chars", 4_000),
        ("story_context_memory_chars", 5_000),
    ):
        st.session_state.setdefault(key, default)
    with st.expander("上下文份量", expanded=False):
        st.caption("決定模型這次能回看多少創作資料；單位是字元，不會改變正文輸出長度。")
        total = int(
            st.number_input(
                "整體可回看份量",
                min_value=2_000,
                max_value=200_000,
                step=1_000,
                key="story_context_total_chars",
                help="包含固定保留的場景卡、世界硬規則與系統寫作規則。",
            )
        )
        left, right = st.columns(2)
        story_requirement = int(
            left.number_input(
                "故事核心與章節方向",
                min_value=128,
                max_value=50_000,
                step=500,
                key="story_context_requirement_chars",
                help="故事需求、敘事契約與章節計畫。",
            )
        )
        world = int(
            right.number_input(
                "世界觀",
                min_value=128,
                max_value=50_000,
                step=500,
                key="story_context_world_chars",
                help="地點、社會、文化、科技與魔法；世界硬規則永遠完整保留。",
            )
        )
        characters = int(
            left.number_input(
                "角色與關係",
                min_value=128,
                max_value=50_000,
                step=500,
                key="story_context_characters_chars",
                help="角色聲音、特徵、目標與關係；不可更動的正式設定永遠完整保留。",
            )
        )
        recent_story = int(
            right.number_input(
                "近期正文脈絡",
                min_value=128,
                max_value=50_000,
                step=500,
                key="story_context_recent_chars",
                help=(
                    "取用同一故事大綱中、目前場景之前最近一個已接受草稿的摘要；"
                    "可跨章延續，未接受草稿的工作筆記不會進入上下文。"
                ),
            )
        )
        author_direction = int(
            left.number_input(
                "你的指示與關鍵字",
                min_value=128,
                max_value=50_000,
                step=500,
                key="story_context_author_chars",
                help="本次續寫關鍵字、目標、額外寫作指示與風格範例。",
            )
        )
        memory = int(
            right.number_input(
                "故事記憶與時間線",
                min_value=128,
                max_value=50_000,
                step=500,
                key="story_context_memory_chars",
                help="作者已確認的事實、時間線、伏筆與角色狀態。",
            )
        )
        adjustable = (
            story_requirement + world + characters + recent_story + author_direction + memory
        )
        st.caption(f"六區最多合計 {adjustable:,} 字元；整體上限仍會替最後送出的內容守門。")
        if adjustable > total:
            st.info(
                "各區合計高於整體份量時，模型會先保留不可違反的設定，"
                "再依故事重要性縮減較後面的參考內容。"
            )
    return ContextBudgetSnapshot(
        total_max_chars=total,
        story_requirement_chars=story_requirement,
        world_chars=world,
        characters_chars=characters,
        recent_story_chars=recent_story,
        author_direction_chars=author_direction,
        memory_chars=memory,
    )


def _missing_version_refs(
    referenced: tuple[str, ...], available: dict[str, tuple[str, str]]
) -> tuple[str, ...]:
    """Find saved character versions that are no longer available.

    Loading must fail rather than silently substitute a different version.
    """
    return tuple(vid for vid in referenced if vid and vid not in available)


def _render_loaded_marker(kind: str, *, expected_entity_id: str) -> None:
    """provenance banner for the CURRENT editor content.

    ``loaded version`` (what filled the form), ``working head`` (latest save)
    and ``accepted version`` (generation default) are three different things;
    this banner only ever describes the first and names the second/third
    explicitly so they cannot be confused.

     hardening: the banner must never impersonate a load for the entity
    now selected. If the stored context belongs to a different Requirement /
    Bible / Outline / Chapter / Scene, say so instead of showing provenance
    that does not apply here.
    """
    version_id = st.session_state.get(f"story_loaded_{kind}_version_id")
    context = st.session_state.get(f"story_loaded_{kind}_context")
    if not version_id or not isinstance(context, dict):
        return
    if context.get("entity_id") != expected_entity_id:
        # S7.3.1: the earlier wording offered "or just edit and save", which
        # was untrue — the save handler raises the stale error again. Worse,
        # a newly created entity has no version to reload, so without an
        # explicit way out the author was stuck: correct data, dead workflow.
        st.warning(
            "先前載入的歷史版本屬於另一個項目，不適用於目前選取的項目。"
            "在解除之前，這個項目無法儲存新版本。"
        )
        st.caption(
            "若這個項目本來就有需要保留的欄位，請改為載入「這個項目」的目標版本。"
            "若直接捨棄，表單未顯示的欄位將不再沿用先前載入的內容，"
            "而是採用目前表單值與模型預設。"
        )
        if st.button("捨棄先前載入的版本", key=f"story_discard_loaded_{kind}"):
            # Clearing is the ONLY effect: no version is created, no working
            # head or accepted pointer moves, no stored data is touched.
            _clear_loaded_marker(kind)
            st.rerun()
        return
    st.info(
        f"已載入 v{context['version_number']}（{_short(str(version_id))}）的內容到編輯器。"
        "編輯此表單不會改動該歷史版本；儲存會建立新的工作版本。"
    )
    if context.get("accepted"):
        st.caption("提醒：被載入的版本目前同時是「生成預設」（已接受）。載入本身不會改變這個指標。")
    for warning in context.get("warnings", ()):
        st.warning(warning)


class _StaleLoadedContextError(RuntimeError):
    """the loaded base belongs to a DIFFERENT parent entity.

    Raised instead of returning ``{}`` because both silent outcomes are wrong:
    reusing the base writes another entity's hidden fields into this one, and
    dropping it resets this entity's unshown fields to defaults. The save must
    fail closed and tell the author what to do.
    """


def _loaded_base(kind: str, *, expected_entity_id: str) -> dict[str, Any]:
    """full dump of the loaded historical model (or ``{}``).

    Save handlers merge the visible widget values OVER this dict and then
    re-validate the WHOLE model, so fields the form does not display survive
    a load→edit→save cycle instead of being silently reset to defaults.
    ``model_copy`` is deliberately not used here — it skips validation, the
    exact hole behind the half-Canon-link defect.

     hardening: the base is only valid for the entity it was loaded
    from. Selecting another Requirement/Bible/Outline/Chapter/Scene without
    reloading leaves a stale base in session state, which would otherwise
    carry entity A's hidden fields into a new version of entity B.
    """
    context = st.session_state.get(f"story_loaded_{kind}_context")
    if not isinstance(context, dict):
        return {}
    base = context.get("base")
    if base is None:
        return {}
    if context.get("entity_id") != expected_entity_id:
        raise _StaleLoadedContextError(
            "無法儲存：目前載入的歷史版本屬於另一個項目。"
            "為避免把另一個項目的欄位寫進這個項目，儲存已中止。"
            "請重新載入目前項目的版本後再儲存；本次未建立任何新版本。"
        )
    return dict(base.model_dump())


def _clear_loaded_marker(kind: str) -> None:
    """a successful save makes the editor content the NEW
    working head, so the "loaded from vN" provenance no longer applies."""
    st.session_state.pop(f"story_loaded_{kind}_version_id", None)
    st.session_state.pop(f"story_loaded_{kind}_context", None)


def _capture_provider(runtime: _StoryAIRuntimeView, services: Any) -> _CapturedProvider:
    """Capture provider/model ownership on the UI thread at button press."""
    if runtime.config is None or runtime.config.provider_name is None:
        raise ValueError("目前未啟用 AI 寫作引擎。")
    injected = st.session_state.get("story_provider")
    if injected is not None:
        # Explicit injections retain caller ownership so existing tests and
        # embedding code may reuse the same seam across generation/revision.
        return _CapturedProvider(
            provider=injected,
            provider_name=runtime.config.provider_name,
            owned=False,
        )
    provider_name = runtime.config.provider_name
    assert provider_name is not None
    lease = capture_generation_provider_action(
        services,
        st.session_state,
        task=GenerationTask.FINAL_WRITING,
        action_id="story_scene",
    )
    return _CapturedProvider(
        provider=lease.provider,
        provider_name=lease.provider_name,
        owned=True,
        release=lease.close,
    )


def _close_captured_provider(captured: _CapturedProvider | None) -> None:
    if captured is None or not captured.owned:
        return
    if captured.release is not None:
        captured.release()
        return
    close = getattr(captured.provider, "close", None)
    if not callable(close):
        return
    # Shutdown must not replace the actual generation result with a raw client
    # exception (which could contain request metadata).
    with suppress(Exception):
        close()


def _assistant_for_runtime(
    runtime: _StoryAIRuntimeView,
) -> tuple[StoryAssistService, _CapturedProvider | None]:
    injected = st.session_state.get("story_assistant")
    if injected is not None:
        return injected, None
    services = get_services()
    captured = _capture_provider(runtime, services)
    return (
        StoryAssistService(captured.provider, provider_name=captured.provider_name),
        captured,
    )


def _new_job_manager(
    runtime: _StoryAIRuntimeView,
) -> tuple[StoryGenerationJobManager, Callable[[], None]]:
    """Build runners around a provider captured before their worker exists."""
    existing = st.session_state.get("story_job_manager")
    if existing is not None and existing.active_job() is not None:
        raise ValueError("已有背景生成正在進行，請等待完成或先取消。")

    services = get_services()
    injected_generator = st.session_state.get("story_generator")
    injected_reviser = st.session_state.get("story_reviser")
    captured = (
        _capture_provider(runtime, services)
        if injected_generator is None or injected_reviser is None
        else None
    )
    provider_released = Event()

    def release_provider() -> None:
        if provider_released.is_set():
            return
        provider_released.set()
        _close_captured_provider(captured)

    provider = captured.provider if captured is not None else None
    provider_name = (
        captured.provider_name
        if captured is not None
        else (runtime.config.provider_name if runtime.config is not None else "ollama")
    )

    def run_generation(
        request: GenerateSceneRequest,
        cancel: Event,
        on_chunk: Callable[[str], None],
    ) -> Any:
        try:
            if injected_generator is not None:
                generator = injected_generator
            else:
                if provider is None:  # pragma: no cover - guarded during capture
                    raise RuntimeError("captured generation provider is missing")
                generator = StoryGenerationOrchestrationService(
                    services.lifecycle,
                    provider=provider,
                    provider_name=str(provider_name),
                    eligibility=services.eligibility,
                    settings=services.settings,
                )
            return generator.generate_scene(request, cancel=cancel, on_chunk=on_chunk)
        finally:
            release_provider()

    def run_revision(
        request: ReviseSceneRequest,
        cancel: Event,
        on_chunk: Callable[[str], None],
    ) -> Any:
        try:
            if injected_reviser is not None:
                reviser = injected_reviser
            else:
                if provider is None:  # pragma: no cover - guarded during capture
                    raise RuntimeError("captured revision provider is missing")
                reviser = StoryRevisionOrchestrationService(
                    services.lifecycle,
                    provider=provider,
                    provider_name=str(provider_name),
                    eligibility=services.eligibility,
                    settings=services.settings,
                )
            return reviser.revise_scene(request, cancel=cancel, on_chunk=on_chunk)
        finally:
            release_provider()

    manager = StoryGenerationJobManager(
        generation_runner=run_generation, revision_runner=run_revision
    )
    st.session_state["story_job_manager"] = manager
    return manager, release_provider


def _job_manager_for_runtime(
    runtime: _StoryAIRuntimeView,
) -> tuple[StoryGenerationJobManager, Callable[[], None]]:
    if not runtime.can_send:
        raise ValueError("請先完成全域寫作引擎與 credential 設定。")
    return _new_job_manager(runtime)


def _job_manager() -> StoryGenerationJobManager:
    """Return the manager captured by the button handler for poll/cancel."""
    existing = st.session_state.get("story_job_manager")
    if existing is not None:
        return existing  # type: ignore[no-any-return]
    raise ValueError("找不到作用中的故事生成工作；請重新從目前全域 route 啟動。")


def _start_generation_job(runtime: _StoryAIRuntimeView, request: GenerateSceneRequest) -> Any:
    manager, release_provider = _job_manager_for_runtime(runtime)
    try:
        return manager.start_generation(request)
    except Exception:
        release_provider()
        if st.session_state.get("story_job_manager") is manager:
            st.session_state.pop("story_job_manager", None)
        raise


def _start_revision_job(
    runtime: _StoryAIRuntimeView,
    request: ReviseSceneRequest,
    *,
    scene_id: str,
) -> Any:
    manager, release_provider = _job_manager_for_runtime(runtime)
    try:
        return manager.start_revision(request, scene_id=scene_id)
    except Exception:
        release_provider()
        if st.session_state.get("story_job_manager") is manager:
            st.session_state.pop("story_job_manager", None)
        raise


def _screenplay_job_manager() -> ScreenplayGenerationJobManager:
    """Return the manager captured for the current screenplay action."""

    existing = st.session_state.get(_SCREENPLAY_JOB_MANAGER_KEY)
    if isinstance(existing, ScreenplayGenerationJobManager):
        return existing
    raise ScreenplayGenerationJobNotFoundError(
        "找不到作用中的劇本生成工作；請由目前劇本改編重新啟動。"
    )


def _start_screenplay_generation_job(
    runtime: _StoryAIRuntimeView,
    request: GenerateScreenplayRequest,
    *,
    expose_live_preview: bool,
    generated_values: dict[str, object] | None,
) -> None:
    """Capture one provider and hand the exact request to a background worker."""

    if not runtime.can_send:
        raise ValueError("請先完成全域寫作引擎與 credential 設定。")
    existing = st.session_state.get(_SCREENPLAY_JOB_MANAGER_KEY)
    if isinstance(existing, ScreenplayGenerationJobManager) and existing.active_job() is not None:
        raise ValueError("已有劇本生成背景工作尚未收取；請先停止或等待目前結果。")

    services = get_services()
    captured = _capture_provider(runtime, services)
    generator = ScreenplayGenerationService(
        services.lifecycle,
        provider=captured.provider,
        provider_name=captured.provider_name,
        settings=services.settings,
    )
    manager = ScreenplayGenerationJobManager()

    def runner(
        cancel: Event,
        on_delta: Callable[[str], None],
    ) -> Any:
        return generator.generate(replace(request, cancel=cancel, on_delta=on_delta))

    try:
        started = manager.start(
            runner,
            release_provider=lambda: _close_captured_provider(captured),
            expose_live_preview=expose_live_preview,
        )
    except Exception:
        _close_captured_provider(captured)
        raise

    st.session_state[_SCREENPLAY_JOB_MANAGER_KEY] = manager
    st.session_state[_SCREENPLAY_ACTIVE_JOB_ID_KEY] = started.job_id
    st.session_state[_SCREENPLAY_ACTIVE_JOB_SCENE_KEY] = request.scene_id
    st.session_state[_SCREENPLAY_ACTIVE_JOB_ADAPTATION_KEY] = request.adaptation_id or ""
    st.session_state[_SCREENPLAY_ACTIVE_JOB_STATUS_KEY] = started.status.value
    st.session_state[_SCREENPLAY_ACTIVE_PROVIDER_LABEL_KEY] = (
        "OpenAI" if runtime.mode is GenerationMode.OPENAI else "Ollama"
    )
    st.session_state[_SCREENPLAY_JOB_GENERATED_VALUES_KEY] = (
        dict(generated_values) if generated_values is not None else None
    )
    st.session_state.pop(_SCREENPLAY_LAST_TERMINAL_KEY, None)
    _remember_active_runtime(runtime)


def _finish_screenplay_job_ui(terminal: ScreenplayGenerationJobTerminal) -> None:
    """Project one consumed durable terminal into prose-free Streamlit state."""

    generated_values = st.session_state.pop(_SCREENPLAY_JOB_GENERATED_VALUES_KEY, None)
    captured_provider_label = st.session_state.pop(
        _SCREENPLAY_ACTIVE_PROVIDER_LABEL_KEY,
        "模型服務",
    )
    if captured_provider_label not in _SCREENPLAY_SAFE_PROVIDER_LABELS:
        captured_provider_label = "模型服務"
    outcome = terminal.outcome
    if outcome is not None and isinstance(generated_values, dict):
        st.session_state["story_screenplay_pending_brief"] = dict(generated_values)

    st.session_state[_SCREENPLAY_LAST_TERMINAL_KEY] = {
        "job_id": terminal.job_id,
        "status": terminal.status.value,
        "run_status": terminal.run_status,
        "run_id": terminal.run_id,
        "adaptation_id": terminal.adaptation_id,
        "revision_id": terminal.revision_id,
        "reason_code": terminal.reason_code,
        "cancel_was_requested": terminal.cancel_was_requested,
        "provider_label": captured_provider_label,
        "error_marker": (
            "screenplay_background_failed" if terminal.outcome is None else ""
        ),
    }
    if outcome is None:
        # The raw worker exception is intentionally gone. The prose- and
        # secret-free marker above lets the next section render use the shared
        # typed error presenter exactly once.
        return
    if outcome.revision is not None:
        st.session_state["story_screenplay_pending_revision_id"] = outcome.revision.id
        if outcome.partial_needs_action:
            prefix = "生成已停止；" if terminal.cancel_was_requested else "生成未完整完成；"
            st.session_state["story_screenplay_flash"] = (
                "warning",
                f"{prefix}已保存第 {outcome.revision.version_number} 版 partial；"
                "尚未達成劇本頁數合約，不能接受為正式版本。",
            )
        else:
            st.session_state["story_screenplay_flash"] = (
                "success",
                f"AI 已建立第 {outcome.revision.version_number} 版工作候選；尚未接受。",
            )
        return
    if outcome.cancelled:
        st.session_state["story_screenplay_flash"] = (
            "info",
            "劇本生成已取消；沒有可保存的 partial，也沒有建立候選。",
        )
        return
    st.session_state["story_screenplay_flash"] = (
        "warning",
        "劇本生成沒有建立可審閱候選；原小說與既有版本均未變更。",
    )


def _poll_screenplay_job() -> bool:
    """Poll once without blocking and consume a durable terminal exactly once."""

    raw_job_id = st.session_state.get(_SCREENPLAY_ACTIVE_JOB_ID_KEY)
    if not raw_job_id:
        return False
    job_id = str(raw_job_id)
    try:
        manager = _screenplay_job_manager()
        snapshot = manager.get(job_id)
    except ScreenplayGenerationJobNotFoundError:
        st.session_state[_SCREENPLAY_ACTIVE_JOB_ID_KEY] = ""
        st.session_state.pop(_SCREENPLAY_ACTIVE_PROVIDER_LABEL_KEY, None)
        st.session_state.pop(_SCREENPLAY_JOB_GENERATED_VALUES_KEY, None)
        st.session_state["story_screenplay_flash"] = (
            "warning",
            "劇本背景工作狀態已遺失；為避免重複生成，請先檢查劇本生成紀錄再重試。",
        )
        return False

    st.session_state[_SCREENPLAY_ACTIVE_JOB_STATUS_KEY] = snapshot.status.value
    if snapshot.is_active:
        return False
    terminal = manager.consume_terminal(job_id)
    if terminal is None:
        return False
    _finish_screenplay_job_ui(terminal)
    st.session_state[_SCREENPLAY_ACTIVE_JOB_ID_KEY] = ""
    manager.cleanup_consumed()
    if st.session_state.get(_SCREENPLAY_JOB_MANAGER_KEY) is manager:
        st.session_state.pop(_SCREENPLAY_JOB_MANAGER_KEY, None)
    return True


def _render_screenplay_job_panel(scene_id: str) -> None:
    """Show a read-only live preview and an actual Event-backed Stop action."""

    raw_job_id = st.session_state.get(_SCREENPLAY_ACTIVE_JOB_ID_KEY)
    if not raw_job_id:
        return
    job_id = str(raw_job_id)
    try:
        manager = _screenplay_job_manager()
        snapshot = manager.get(job_id)
    except ScreenplayGenerationJobNotFoundError:
        return

    active_scene = str(st.session_state.get(_SCREENPLAY_ACTIVE_JOB_SCENE_KEY, ""))
    if active_scene == scene_id:
        st.info(f"劇本背景工作進行中｜狀態 {snapshot.status.value}")
    else:
        st.warning(
            f"劇本背景工作屬於另一個場景（{_short(active_scene)}）；"
            "此處不會顯示或套用它的正文。"
        )
    if snapshot.live_preview_redacted:
        st.caption(
            f"即時正文預覽未提供（已接收 {snapshot.delta_count} 個片段）。"
        )
    elif snapshot.partial_text and active_scene == scene_id:
        st.caption("劇本串流預覽（唯讀；尚未完成、保存或接受）")
        st.code(snapshot.partial_text, language=None, wrap_lines=True)
    if snapshot.status is ScreenplayGenerationJobStatus.CANCEL_REQUESTED:
        st.caption("停止要求已送出；等待 service 保存真實 terminal／partial 狀態。")
    if st.button(
        "停止劇本生成",
        key="story_screenplay_stop_generation",
        disabled=snapshot.status is ScreenplayGenerationJobStatus.CANCEL_REQUESTED,
        width="stretch",
    ):
        manager.cancel(job_id)
        st.session_state[_SCREENPLAY_ACTIVE_JOB_STATUS_KEY] = (
            ScreenplayGenerationJobStatus.CANCEL_REQUESTED.value
        )
        st.rerun()


@st.fragment(run_every=0.25)
def _render_screenplay_job_fragment(scene_id: str) -> None:
    """Timed, non-blocking screenplay poll mounted only while a job is active."""

    terminal_consumed = _poll_screenplay_job()
    _render_screenplay_job_panel(scene_id)
    if terminal_consumed:
        st.rerun()


def _remember_active_runtime(runtime: _StoryAIRuntimeView) -> None:
    st.session_state[_AI_ACTIVE_MODE_KEY] = runtime.mode.value
    st.session_state[_AI_ACTIVE_RETENTION_KEY] = runtime.key_retention
    # The worker already owns an isolated provider snapshot. Clear one-send
    # credentials immediately instead of leaving them in shared session state
    # while the job runs.
    _schedule_ai_action_cleanup(
        mode=runtime.mode,
        key_retention=runtime.key_retention,
    )


def _reset_stream_state() -> None:
    st.session_state["story_stream_buffer"] = ""
    st.session_state["story_stream_last_sequence"] = 0
    st.session_state["story_stream_partial_text"] = ""
    st.session_state["story_last_job_terminal"] = None
    st.session_state.pop(_STORY_JOB_POLL_ERROR_KEY, None)


def _job_start_failure_safety() -> ErrorDataSafety:
    """Describe the state that remains owned by the previous/visible run."""

    return ErrorDataSafety(
        message_zh_tw=(
            "新工作尚未建立；先前的串流、partial、terminal、編輯器內容與"
            "導覽位置均保留，也沒有建立或套用新的草稿。"
        ),
        author_input_preserved=True,
        original_preserved=True,
        partial_preserved=True,
        completed_work_preserved=True,
        draft_selection_preserved=True,
    )


def _record_job_poll_error(error: ApplicationError, *, job_id: str) -> None:
    """Persist one safe blocking card while the worker remains authoritative."""

    st.session_state[_STORY_JOB_POLL_ERROR_KEY] = map_and_record_user_facing_error(
        error,
        context=ErrorMappingContext(
            operation_zh_tw="更新背景故事工作狀態",
            data_safety=ErrorDataSafety(
                message_zh_tw=(
                    "背景工作與取消控制仍保留；已收到的串流與 partial、原稿、"
                    "編輯器內容及導覽位置均未變更。請先重新讀取狀態，勿另開重複工作。"
                ),
                author_input_preserved=True,
                original_preserved=True,
                partial_preserved=True,
                completed_work_preserved=True,
                draft_selection_preserved=True,
            ),
            job_id=job_id,
            additional_actions=(
                RecoveryAction(
                    action_id="retry_story_job_poll",
                    label_zh_tw="重新讀取工作狀態",
                    kind=RecoveryActionKind.RELOAD_STATE,
                ),
            ),
        ),
    )


def _remember_active_length_bounds(
    minimum: int | None,
    maximum: int | None,
) -> None:
    """Bind length feedback to the bounds used by this exact background job."""

    st.session_state["story_active_length_min"] = minimum
    st.session_state["story_active_length_max"] = maximum


def _recover_stale_running_runs() -> tuple[str, ...]:
    """Delegate crash recovery to the application layer on every page entry."""
    services = get_services()
    return services.story_run_recovery.recover_stale_running_runs(
        job_manager=st.session_state.get("story_job_manager")
    )


def _poll_active_job() -> bool:
    """Drain the manager queue into UI state. Never blocks.

    Returns ``True`` only when THIS poll consumed the terminal event. The
    caller uses that to trigger exactly one full-app rerun; returning nothing
    (as this used to) meant the timed fragment could not tell a terminal
    transition from an ordinary chunk, and would either never refresh the
    draft list or refresh it on every tick.
    """
    job_id = st.session_state.get("story_active_job_id")
    if not job_id:
        return False
    if isinstance(st.session_state.get(_STORY_JOB_POLL_ERROR_KEY), UserFacingError):
        # Keep the manager and worker authoritative until the author explicitly
        # asks to retry status reads. Repeated fragment ticks must not generate
        # new incidents or make a duplicate job appear startable.
        return False
    manager = _job_manager()
    try:
        events = manager.poll(job_id)
    except ApplicationError as exc:
        _record_job_poll_error(exc, job_id=str(job_id))
        return False
    st.session_state.pop(_STORY_JOB_POLL_ERROR_KEY, None)
    terminal_consumed = False
    for event in events:
        # events belong to the scene the job STARTED on. If the author
        # has since selected another scene, the text must not be attributed to
        # it — that would show one scene's prose as another's.
        if event.scene_id != st.session_state.get("story_active_job_scene_id"):
            if event.kind is StoryGenerationJobEventKind.TERMINAL:
                # Still consume lifecycle state: the provider has finished and
                # closed even though its output must not be shown on this scene.
                st.session_state["story_active_job_id"] = ""
                terminal_consumed = True
                manager.cleanup_terminal_jobs()
                if st.session_state.get("story_job_manager") is manager:
                    st.session_state.pop("story_job_manager", None)
                _schedule_active_job_ai_cleanup()
            continue
        if event.kind is StoryGenerationJobEventKind.CHUNK:
            st.session_state["story_stream_buffer"] += event.text
            st.session_state["story_stream_last_sequence"] = event.sequence
        elif event.kind is StoryGenerationJobEventKind.PARTIAL:
            st.session_state["story_stream_partial_text"] = event.text
        elif event.kind is StoryGenerationJobEventKind.TERMINAL:
            if event.reason_code in {
                ReasonCode.PROVIDER_REFUSED,
            }:
                # Live deltas are provisional. A terminal policy refusal
                # invalidates them, but never the author's editor or drafts.
                st.session_state["story_stream_buffer"] = ""
                st.session_state["story_stream_partial_text"] = ""
            minimum = st.session_state.get("story_active_length_min")
            maximum = st.session_state.get("story_active_length_max")
            length_result: dict[str, object] | None = None
            if event.draft_id and (minimum is not None or maximum is not None):
                with suppress(ApplicationError, RuntimeError, TypeError, ValueError):
                    prose = get_services().story_drafts.get_draft(event.draft_id).prose_text
                    assessment = assess_text_length(
                        prose,
                        minimum=minimum,
                        maximum=maximum,
                    )
                    length_result = {
                        "actual": assessment.actual,
                        "minimum": assessment.minimum,
                        "maximum": assessment.maximum,
                        "state": assessment.state,
                        "message": assessment.message,
                    }
            st.session_state["story_stream_status"] = (
                event.status.value if event.status else "failed"
            )
            st.session_state["story_last_job_terminal"] = {
                "job_id": event.job_id,
                "status": event.status.value if event.status else "failed",
                "reason_code": event.reason_code,
                "draft_id": event.draft_id,
                "partial_draft_id": event.partial_draft_id,
                "scene_id": event.scene_id,
                "kind": event.job_kind.value,
                "length_result": length_result,
            }
            st.session_state.pop("story_active_length_min", None)
            st.session_state.pop("story_active_length_max", None)
            st.session_state["story_active_job_id"] = ""
            # Counts the full-app refreshes the terminal caused, so a test can
            # prove it happens once rather than on every subsequent rerun.
            st.session_state["story_terminal_refresh_count"] = (
                st.session_state.get("story_terminal_refresh_count", 0) + 1
            )
            terminal_consumed = True
            manager.cleanup_terminal_jobs()
            if st.session_state.get("story_job_manager") is manager:
                st.session_state.pop("story_job_manager", None)
            _schedule_active_job_ai_cleanup()
    return terminal_consumed


@st.fragment(run_every=0.25)
def _render_active_job_fragment(scene_id: str) -> None:
    """the ONLY thing that makes a real browser update by itself.

    Without a timed rerun the queue is drained only when the whole page
    happens to re-run, so an author who starts a generation and then stops
    touching the page sees nothing until they interact again. The fragment is
    mounted only while a job is active, so the page is not left polling every
    250 ms forever once the work is done.

    Polling and session-state writes happen here, on the UI thread; the worker
    still never touches Streamlit. There is no sleep and no blocking loop —
    the timer drives this, not a wait.
    """
    terminal_consumed = _poll_active_job()
    _render_job_panel(scene_id)
    if terminal_consumed:
        # One full-app rerun so the draft and run lists pick up the new rows.
        # The active ID is already cleared, so the next full render will not
        # mount this fragment again.
        st.rerun()


def _render_job_panel(scene_id: str) -> None:
    """Live status for the active job, plus the last terminal outcome."""
    active_id = st.session_state.get("story_active_job_id")
    active_scene = st.session_state.get("story_active_job_scene_id", "")
    if active_id:
        kind = st.session_state.get("story_active_job_kind", "")
        status = st.session_state.get("story_stream_status", "running")
        if active_scene == scene_id:
            st.info(f"背景工作進行中：{kind}｜狀態 {status}")
        else:
            # Never imply the current scene is the one being generated.
            st.warning(
                f"背景工作進行中，但屬於另一個場景（{_short(active_scene)}）。"
                "此處不會套用該工作的輸出。"
            )
        if status == StoryGenerationJobStatus.CANCEL_REQUESTED.value:
            st.caption("取消要求已送出，等待 worker 結束。")
        poll_error = st.session_state.get(_STORY_JOB_POLL_ERROR_KEY)
        if isinstance(poll_error, UserFacingError):
            selected_action = render_error_card(
                poll_error,
                key_prefix="story_background_poll",
            )
            if selected_action == "retry_story_job_poll":
                st.session_state.pop(_STORY_JOB_POLL_ERROR_KEY, None)
        buffer = st.session_state.get("story_stream_buffer", "")
        if buffer and active_scene == scene_id:
            # A read-only element with NO widget key. A keyed text_area keeps
            # its own widget state, so on a later rerun it can keep showing the
            # text it was first created with while the new ``value`` is
            # ignored — the author would watch a stream that never advances.
            # This is also display, not input: it must not be editable.
            st.caption("串流輸出（進行中）")
            st.code(buffer, language=None, wrap_lines=True)
        return

    terminal = st.session_state.get("story_last_job_terminal")
    if not isinstance(terminal, dict) or terminal.get("scene_id") != scene_id:
        return
    status = terminal["status"]
    if status == StoryGenerationJobStatus.COMPLETED.value:
        st.success("生成完成。" if terminal["kind"] == "generation" else "已產生修訂版本。")
        length_result = terminal.get("length_result")
        if isinstance(length_result, dict):
            message = str(length_result.get("message", ""))
            if length_result.get("state") == "within":
                st.success(message)
            elif message:
                st.warning(message)
    elif status == StoryGenerationJobStatus.CANCELLED.value:
        if terminal["partial_draft_id"]:
            st.warning("已取消；已收到的內容保留為 partial 草稿。")
        else:
            # No partial in the payload means none was saved. Saying otherwise
            # would send the author looking for a draft that does not exist.
            st.warning("已取消；沒有可保留的內容。")
    else:
        partial_preserved = bool(terminal.get("partial_draft_id"))
        if partial_preserved:
            safety_message = (
                "背景工作未完成；原稿、編輯器內容與既有版本均未被覆寫。"
                "已收到的內容保留為 partial 草稿，未標記為完整，也不會自動套用。"
            )
        else:
            safety_message = (
                "背景工作未完成；原稿、編輯器內容與既有版本均未被覆寫。"
                "這次沒有可保留的 partial 草稿，也沒有建立或套用完整草稿。"
            )
        present_exception(
            _terminal_failure_exception(terminal.get("reason_code")),
            operation_zh_tw="執行背景故事生成",
            key_prefix="story_background_terminal",
            data_safety=ErrorDataSafety(
                message_zh_tw=safety_message,
                author_input_preserved=True,
                original_preserved=True,
                partial_preserved=partial_preserved,
                completed_work_preserved=True,
                draft_selection_preserved=True,
            ),
            provider_label="故事生成服務",
            retry_permitted=not partial_preserved,
            fallback_permitted=False,
            job_id=str(terminal.get("job_id", "")),
        )


def _version_label(record: Any, accepted_id: str | None) -> str:
    """say which version this is and whether it is the default."""
    marks = []
    if record.accepted:
        marks.append("已接受")
    if accepted_id and record.id == accepted_id:
        marks.append("生成預設")
    suffix = f"（{'・'.join(marks)}）" if marks else ""
    return f"v{record.version_number}{suffix}"


def _memory_entry_label(entry: Any) -> str:
    value = entry.value.replace("\n", " ")
    if len(value) > 48:
        value = f"{value[:48]}…"
    return f"{_MEMORY_KIND_LABELS[entry.kind]}｜{entry.subject_id} · {entry.attribute} = {value}"


def _bind_author_inputs_to_scene(
    services: Any,
    *,
    project_id: str,
    scene_id: str,
    generation_purpose_override: StoryGenerationPurpose | None = None,
) -> None:
    """Reset scene-specific author text before its widgets are instantiated.

    Streamlit keeps a widget's previous value when the same key is rendered
    with a different ``value=``. Without an explicit scope transition, an
    unsaved summary or source excerpt from Scene A can therefore be shown—and
    even saved or sent to a provider—as Scene B content.
    """

    scope = f"{project_id}:{scene_id}" if scene_id else f"{project_id}:"
    previous_scope = st.session_state.get(_STORY_SCENE_INPUT_SCOPE_KEY)
    if previous_scope == scope:
        return
    _clear_screenplay_transient_state()
    st.session_state[_STORY_GENERATION_PREVIOUS_TASK_KEY] = StoryGenerationPurpose.SCENE.value
    summary = ""
    if scene_id:
        summary = services.scene_cards.get_scene(scene_id).summary_text
    for key in (
        _STORY_GENERATION_TASK_KEY,
        "story_complete_mode",
        "story_complete_world_premise",
        "story_complete_story_seed",
        "story_complete_structure_outline",
        "story_complete_additional_direction",
        "story_complete_must_include",
        "story_complete_must_avoid",
        "story_complete_min_chars",
        "story_complete_max_chars",
        "story_complete_pacing",
        "story_complete_random_seed",
        "story_draft_id",
        "story_prose_view",
        _STORY_PROSE_LOADED_DRAFT_KEY,
        _STORY_PROSE_EDITOR_SNAPSHOT_KEY,
        "story_manual_prose",
        "story_keywords",
        "story_continuation_goal",
        "story_instruction",
        "story_continuation_source_excerpt",
        "story_continuation_must_include",
        "story_continuation_must_avoid",
        "story_continuation_use_target_words",
        "story_continuation_target_words",
        "story_continuation_length_mode",
        "story_continuation_max_chars",
        "story_continuation_pacing",
        "story_rev_op",
        "story_rev_instruction",
        "story_rev_events",
        "story_rev_order",
        "story_rev_dialogue",
        "story_memory_manual_operation",
        "story_memory_manual_kind",
        "story_memory_manual_subject",
        "story_memory_manual_attribute",
        "story_memory_manual_target",
        "story_memory_manual_value",
        "story_memory_manual_source",
        "story_memory_proposal_id",
        "story_memory_decision_note",
        *_STORY_SECTION_WIDGET_KEYS[StoryStudioSection.ADAPTATION],
        "story_screenplay_pending_adaptation_id",
        "story_screenplay_clear_manual_pending",
        "story_screenplay_flash",
    ):
        st.session_state.pop(key, None)
    if generation_purpose_override is not None:
        # A Launchpad/shortcut destination is an explicit author choice. It
        # survives the required scene-scope cleanup, but still creates no row.
        st.session_state[_STORY_GENERATION_TASK_KEY] = generation_purpose_override.value
        st.session_state[_STORY_GENERATION_PREVIOUS_TASK_KEY] = generation_purpose_override.value
    st.session_state["story_summary"] = summary
    st.session_state[_STORY_SCENE_INPUT_SCOPE_KEY] = scope


def _render_memory_entries(entries: tuple[Any, ...]) -> None:
    if not entries:
        st.caption("目前還沒有已確認的故事記憶；第一幕出現這種狀態很正常。")
        return
    grouped = {kind: [entry for entry in entries if entry.kind is kind] for kind in StoryMemoryKind}
    for kind, kind_entries in grouped.items():
        if not kind_entries:
            continue
        with st.expander(
            f"{_MEMORY_KIND_LABELS[kind]}（{len(kind_entries)}）",
            expanded=kind is StoryMemoryKind.FACT,
        ):
            for entry in kind_entries:
                value = entry.value or "（已撤回）"
                st.markdown(f"- `{entry.subject_id}` · **{entry.attribute}**：{value}")
                if entry.source_excerpt:
                    st.caption(f"來源片段：{entry.source_excerpt}")


def _render_story_memory_tab(services: Any, scene_id: str) -> None:
    """Review scene-derived continuity facts before they affect later prose."""

    if not scene_id:
        st.info("請先於左側選擇場景。")
        return

    flash = st.session_state.pop("story_memory_flash", None)
    if isinstance(flash, tuple) and len(flash) == 2:
        level, message = flash
        if level == "success":
            st.success(str(message))
        else:
            st.info(str(message))

    try:
        projection = services.story_memory.projection_for_scene(scene_id)
        scene = services.scene_cards.get_scene(scene_id)
    except (ApplicationError, ValueError) as exc:
        present_exception(
            exc,
            operation_zh_tw="執行 Story Studio 操作",
            key_prefix="story_studio_error",
        )
        return

    st.subheader("翻到這一幕之前，故事記得什麼")
    st.caption(
        "這裡只顯示你已確認、且發生在目前場景之前的內容。"
        "AI 或 Scene Card 整理出的資訊，未經你接受不會寫進記憶。"
    )
    _render_memory_entries(projection.entries)
    if projection.gap_scene_ids:
        st.warning(
            "前面有場景尚未建立或接受故事記憶提案；後續生成仍可進行，"
            "但可能缺少上下文。缺口場景："
            + "、".join(_short(item) for item in projection.gap_scene_ids)
        )
    if projection.stale_proposal_ids:
        st.warning(
            "前面有提案因更早的故事記憶改變而過期，請回到該場景重新整理："
            + "、".join(_short(item) for item in projection.stale_proposal_ids)
        )
    st.caption(f"目前故事記憶檢查碼：`{projection.fingerprint[:16]}…`")

    st.divider()
    st.subheader("把這一幕寫進故事記憶")
    accepted_draft_id = scene.accepted_draft_id
    if not accepted_draft_id:
        st.info(
            "目前沒有已接受草稿。先在「寫故事／完整故事」接受一份完整草稿，"
            "才能替這一幕整理故事記憶。"
        )
        return

    st.caption(
        f"目前接受草稿：`{_short(accepted_draft_id)}`。"
        "只整理這份草稿綁定的 Scene Card 及已保存摘要，不會從正文推測事實。"
        "可提出時間、地點、角色離場狀態與未解線索；"
        "按下按鈕只會建立待審提案。"
    )
    try:
        proposals = services.story_memory.list_proposals_for_draft(accepted_draft_id)
    except (ApplicationError, ValueError) as exc:
        present_exception(
            exc,
            operation_zh_tw="執行 Story Studio 操作",
            key_prefix="story_studio_error",
        )
        return
    accepted_proposal = next(
        (
            proposal
            for proposal in proposals
            if proposal.status is StoryMemoryProposalStatus.ACCEPTED
        ),
        None,
    )
    draft_memory_finalized = accepted_proposal is not None
    if draft_memory_finalized:
        st.info(
            "這份已接受草稿已有一份正式故事記憶提案。"
            "若要再補充、更新或撤回，請從後續場景或新的已接受草稿提出；"
            "既有待審提案仍可在下方檢視或略過。"
        )
    if st.button(
        "從已接受草稿整理記憶提案",
        key="story_memory_propose",
        disabled=draft_memory_finalized,
        width="stretch",
    ):
        try:
            proposal = services.story_memory.propose_for_accepted_draft(accepted_draft_id)
            st.session_state["story_memory_proposal_id"] = proposal.id
            st.session_state["story_memory_flash"] = (
                "success",
                "已整理成待審提案；內容尚未影響後續故事。",
            )
            st.rerun()
        except EmptyStoryMemoryProposalError:
            st.info(
                "目前已接受草稿綁定的 Scene Card 與已保存摘要沒有可新增或更新的記憶資料。"
                "尚未建立提案；正文、Scene Card 與正式故事記憶都未變更。"
            )
            st.info(
                "若沒有新事實，可保留現有記憶，不必重複建立提案。"
                "你也可以先確認這一幕已發生的事，在「寫故事／完整故事」使用「儲存摘要」；"
                "或在下方「自己補一項、更新或撤回記憶」建立待審提案。"
                "若要使用新版 Scene Card，請先儲存並接受新版卡，"
                "再從「直接手寫正文」另建綁定新版卡的草稿，並明確接受該草稿。"
                "新版 Scene Card 不會自動改綁既有草稿；「存為手動草稿」仍沿用原卡。"
                "整理完成後，仍須由你接受提案才會更新正式故事記憶。"
            )
        except (ApplicationError, ValueError) as exc:
            present_exception(
                exc,
                operation_zh_tw="執行 Story Studio 操作",
                key_prefix="story_studio_error",
            )

    with st.expander("自己補一項、更新或撤回記憶"):
        operation = st.selectbox(
            "這次要做什麼",
            list(StoryMemoryOperation),
            format_func=lambda item: _MEMORY_OPERATION_LABELS[item],
            key="story_memory_manual_operation",
        )
        target = None
        if operation is StoryMemoryOperation.ASSERT:
            st.session_state.pop("story_memory_manual_target", None)
            kind = st.selectbox(
                "放在哪一頁",
                list(StoryMemoryKind),
                format_func=lambda item: _MEMORY_KIND_LABELS[item],
                key="story_memory_manual_kind",
            )
            subject_id = st.text_input(
                "這件事屬於誰或什麼",
                key="story_memory_manual_subject",
                placeholder="例如：character:艾莉、world、thread:失蹤客戶",
            )
            attribute = st.text_input(
                "記憶名稱",
                key="story_memory_manual_attribute",
                placeholder="例如：hair_color、true_identity、status",
            )
        else:
            entry_ids = [entry.id for entry in projection.entries]
            selected_target = st.session_state.get("story_memory_manual_target")
            if selected_target not in entry_ids:
                st.session_state.pop("story_memory_manual_target", None)
            if not entry_ids:
                st.info("目前沒有可更新或撤回的既有記憶。")
                kind = StoryMemoryKind.FACT
                subject_id = ""
                attribute = ""
            else:
                target_id = st.selectbox(
                    "選擇既有記憶",
                    entry_ids,
                    format_func=lambda item_id: _memory_entry_label(
                        next(entry for entry in projection.entries if entry.id == item_id)
                    ),
                    key="story_memory_manual_target",
                )
                target = next(entry for entry in projection.entries if entry.id == target_id)
                kind = target.kind
                subject_id = target.subject_id
                attribute = target.attribute
                st.caption(f"將處理：{_MEMORY_KIND_LABELS[kind]}｜`{subject_id}` · **{attribute}**")

        if operation is StoryMemoryOperation.RETRACT:
            value = ""
            st.caption("撤回後，這項記憶不再提供給後續場景。舊紀錄仍會保留。")
        else:
            value = st.text_area(
                "要記住的內容",
                key="story_memory_manual_value",
                placeholder="請寫成清楚、單一且可持續引用的事實或狀態",
            )
        source_excerpt = st.text_area(
            "依據片段（選填）",
            key="story_memory_manual_source",
            placeholder="貼上支持這項記憶的短句，日後比較容易回看",
        )
        manual_disabled = (
            draft_memory_finalized
            or (operation is StoryMemoryOperation.ASSERT and not (subject_id and attribute))
            or (operation is not StoryMemoryOperation.ASSERT and target is None)
            or (operation is not StoryMemoryOperation.RETRACT and not value.strip())
        )
        if st.button(
            "建立手動提案",
            key="story_memory_manual_submit",
            disabled=manual_disabled,
            width="stretch",
        ):
            try:
                change = StoryMemoryChange(
                    kind=kind,
                    subject_id=subject_id,
                    attribute=attribute,
                    value=value,
                    operation=operation,
                    supersedes_entry_id=None if target is None else target.id,
                    source_excerpt=source_excerpt,
                )
                proposal = services.story_memory.propose_for_accepted_draft(
                    accepted_draft_id,
                    payload=StoryMemoryProposalPayload(changes=(change,)),
                    origin=StoryMemoryProposalOrigin.MANUAL,
                )
                st.session_state["story_memory_proposal_id"] = proposal.id
                st.session_state["story_memory_flash"] = (
                    "success",
                    "手動提案已建立；請在下方檢查後接受或略過。",
                )
                st.rerun()
            except (ApplicationError, ValueError) as exc:
                present_exception(
                    exc,
                    operation_zh_tw="執行 Story Studio 操作",
                    key_prefix="story_studio_error",
                )

    if not proposals:
        st.caption("這份草稿還沒有故事記憶提案。")
        return

    st.divider()
    st.subheader("逐份確認")
    proposal_ids = [proposal.id for proposal in proposals]
    selected_proposal_id = st.session_state.get("story_memory_proposal_id")
    if selected_proposal_id not in proposal_ids:
        st.session_state.pop("story_memory_proposal_id", None)
    proposal_labels = {
        proposal.id: (
            f"{_MEMORY_STATUS_LABELS[proposal.status]}｜"
            f"{proposal.origin.value}｜{proposal.created_at[:19]}｜"
            f"{_short(proposal.id)}"
        )
        for proposal in proposals
    }
    proposal_id = st.selectbox(
        "記憶提案",
        proposal_ids,
        format_func=lambda item_id: proposal_labels[item_id],
        key="story_memory_proposal_id",
    )
    proposal = next(item for item in proposals if item.id == proposal_id)
    st.caption(
        f"狀態：{_MEMORY_STATUS_LABELS[proposal.status]}｜"
        f"內容 SHA-256：`{proposal.proposal_sha256[:16]}…`"
    )
    if proposal.provider or proposal.model:
        st.caption(f"來源模型：{proposal.provider or '未記錄'}／{proposal.model or '未記錄'}")
    for index, change in enumerate(proposal.payload.changes, start=1):
        verb = _MEMORY_OPERATION_LABELS[change.operation]
        body = change.value or "（撤回）"
        st.markdown(
            f"{index}. **{verb}**｜{_MEMORY_KIND_LABELS[change.kind]}｜"
            f"`{change.subject_id}` · **{change.attribute}**：{body}"
        )
        if change.source_excerpt:
            st.caption(f"依據：{change.source_excerpt}")

    has_accepted_alternative = (
        draft_memory_finalized and proposal.status is StoryMemoryProposalStatus.PENDING
    )
    can_accept = (
        proposal.status is StoryMemoryProposalStatus.PENDING and not has_accepted_alternative
    )
    if proposal.status is StoryMemoryProposalStatus.PENDING:
        try:
            report = services.story_memory.consistency_for_proposal(proposal.id)
            if not report.findings:
                st.success("一致性檢查通過，沒有發現與既有記憶衝突。")
            for finding in report.findings:
                message = f"{finding.message_zh_tw} （{finding.subject_id} · {finding.attribute}）"
                if finding.severity is StoryConsistencySeverity.ERROR:
                    st.error(message)
                elif finding.severity is StoryConsistencySeverity.WARNING:
                    st.warning(message)
                else:
                    st.info(message)
            can_accept = not report.has_errors and not has_accepted_alternative
        except (ApplicationError, ValueError) as exc:
            present_exception(
                exc,
                operation_zh_tw="執行 Story Studio 操作",
                key_prefix="story_studio_error",
            )
            can_accept = False
        if has_accepted_alternative:
            st.warning(
                "這份已接受草稿已有另一份正式故事記憶提案；"
                "此待審提案不可再接受，但仍可略過。"
                "若仍需這項變更，請從後續場景或新的已接受草稿提出。"
            )
    elif proposal.decision_note:
        st.caption(f"作者備註：{proposal.decision_note}")

    decision_note = st.text_input(
        "確認備註（選填）",
        key="story_memory_decision_note",
        placeholder="例如：已核對第三章角色狀態",
        disabled=proposal.status is not StoryMemoryProposalStatus.PENDING,
    )
    if proposal.status is not StoryMemoryProposalStatus.PENDING:
        return
    accept_col, reject_col = st.columns(2)
    if accept_col.button(
        "接受，供後續場景使用",
        key="story_memory_accept",
        type="primary",
        disabled=not can_accept,
        width="stretch",
    ):
        try:
            result = services.story_memory.accept_proposal(
                proposal.id,
                expected_sha256=proposal.proposal_sha256,
                decision_note=decision_note,
            )
            st.session_state["story_memory_flash"] = (
                "success",
                f"已收進故事記憶（{len(result.entries)} 項），會從下一幕開始使用。",
            )
            st.rerun()
        except (ApplicationError, ValueError) as exc:
            present_exception(
                exc,
                operation_zh_tw="執行 Story Studio 操作",
                key_prefix="story_studio_error",
            )
    if reject_col.button(
        "略過這份提案",
        key="story_memory_reject",
        width="stretch",
    ):
        try:
            services.story_memory.reject_proposal(
                proposal.id,
                expected_sha256=proposal.proposal_sha256,
                decision_note=decision_note,
            )
            st.session_state["story_memory_flash"] = (
                "info",
                "提案已略過，沒有改動故事記憶。",
            )
            st.rerun()
        except (ApplicationError, ValueError) as exc:
            present_exception(
                exc,
                operation_zh_tw="執行 Story Studio 操作",
                key_prefix="story_studio_error",
            )


def _screenplay_brief_widget_values(
    generated: RandomScreenplayBrief,
) -> dict[str, object]:
    brief = generated.brief
    return {
        "story_screenplay_target_minutes": brief.target_minutes,
        "story_screenplay_pacing": brief.pacing,
        "story_screenplay_dialogue_retention": brief.dialogue_retention,
        "story_screenplay_direction": brief.additional_direction,
        "story_screenplay_must_include": "\n".join(brief.must_include),
        "story_screenplay_must_avoid": "\n".join(brief.must_avoid),
        "story_screenplay_random_seed": generated.seed,
    }


def _merged_screenplay_brief_widget_values(
    generated: RandomScreenplayBrief,
    *,
    preserve_written: bool,
) -> dict[str, object]:
    """Merge a local brief with mounted author fields without side effects."""

    values = _screenplay_brief_widget_values(generated)
    if preserve_written:
        for key, _value in tuple(values.items()):
            current = st.session_state.get(key)
            if key == "story_screenplay_random_seed":
                continue
            if current is not None and (not isinstance(current, str) or current.strip()):
                values[key] = current
    return values


def _queue_screenplay_brief(
    generated: RandomScreenplayBrief,
    *,
    preserve_written: bool,
) -> None:
    """Stage a local-only brief without mutating already-mounted widgets."""

    values = _merged_screenplay_brief_widget_values(
        generated,
        preserve_written=preserve_written,
    )
    st.session_state["story_screenplay_pending_brief"] = values


def _fill_random_screenplay_brief(preserve_written: bool) -> None:
    """Queue one local screenplay brief before the button-triggered rerun."""

    _queue_screenplay_brief(
        generate_random_screenplay_brief(),
        preserve_written=preserve_written,
    )
    st.session_state["story_screenplay_random_notice"] = (
        "已離線填入可編輯的改編方向；已填內容依你的保留選項維持不變。"
    )


def _screenplay_brief_from_widget_values(values: dict[str, object]) -> ScreenplayBrief:
    """Build the exact provider brief represented by merged widget values."""

    target_minutes = values["story_screenplay_target_minutes"]
    if not isinstance(target_minutes, int) or isinstance(target_minutes, bool):
        raise ValueError("screenplay target minutes must be an integer")
    pacing_raw = values["story_screenplay_pacing"]
    dialogue_raw = values["story_screenplay_dialogue_retention"]
    pacing = (
        pacing_raw
        if isinstance(pacing_raw, ScreenplayPacing)
        else ScreenplayPacing(str(pacing_raw))
    )
    dialogue = (
        dialogue_raw
        if isinstance(dialogue_raw, DialogueRetention)
        else DialogueRetention(str(dialogue_raw))
    )
    return ScreenplayBrief(
        target_minutes=target_minutes,
        pacing=pacing,
        dialogue_retention=dialogue,
        additional_direction=str(values["story_screenplay_direction"]),
        must_include=_lines(str(values["story_screenplay_must_include"])),
        must_avoid=_lines(str(values["story_screenplay_must_avoid"])),
    )


def _apply_pending_screenplay_brief() -> None:
    pending = st.session_state.pop("story_screenplay_pending_brief", None)
    if not isinstance(pending, dict):
        return
    for key, value in pending.items():
        st.session_state[str(key)] = value


def _render_screenplay_adaptation_section(
    services: Any,
    *,
    scene_id: str | None,
    ai_runtime: _StoryAIRuntimeView,
    active_job: bool,
) -> None:
    """Render the independent, versioned Scene-to-screenplay journey."""

    _apply_pending_screenplay_brief()

    st.subheader("劇本改編")
    st.caption(
        "改編稿與原小說各自版本化。手寫與 AI 只會建立劇本候選；不會覆寫小說，也不會自動接受。"
    )
    random_notice = st.session_state.pop("story_screenplay_random_notice", None)
    if random_notice:
        st.success(str(random_notice))
    if not scene_id:
        st.info("請先於左側選擇一個場景。")
        return

    flash = st.session_state.pop("story_screenplay_flash", None)
    if isinstance(flash, tuple) and len(flash) == 2:
        level, message = flash
        if level == "success":
            st.success(str(message))
        elif level == "warning":
            st.warning(str(message))
        else:
            st.info(str(message))

    terminal_marker = st.session_state.pop(_SCREENPLAY_LAST_TERMINAL_KEY, None)
    if (
        isinstance(terminal_marker, dict)
        and terminal_marker.get("error_marker") == "screenplay_background_failed"
    ):
        raw_provider_label = terminal_marker.get("provider_label")
        provider_label = (
            raw_provider_label
            if raw_provider_label in _SCREENPLAY_SAFE_PROVIDER_LABELS
            else "模型服務"
        )
        present_exception(
            ProviderError("screenplay background generation failed"),
            operation_zh_tw="產生劇本改編候選",
            key_prefix="story_screenplay_background",
            data_safety=ErrorDataSafety(
                message_zh_tw=(
                    "這次沒有建立、接受或套用劇本候選；原小說、既有劇本版本、"
                    "未儲存的改編設定與目前選擇均保留。"
                ),
                author_input_preserved=True,
                original_preserved=True,
                partial_preserved=True,
                completed_work_preserved=True,
                draft_selection_preserved=True,
            ),
            provider_label=provider_label,
            job_id=str(terminal_marker.get("job_id", "")),
        )

    try:
        views = services.screenplay_adaptations.list_for_scene(scene_id)
    except (ApplicationError, ValueError) as exc:
        present_exception(
            exc,
            operation_zh_tw="執行 Story Studio 操作",
            key_prefix="story_studio_error",
        )
        return

    source = None
    try:
        source = services.screenplay_adaptations.current_source(scene_id)
    except (ApplicationError, ValueError) as exc:
        present_exception(
            exc,
            operation_zh_tw="檢查劇本正式來源",
            key_prefix="story_studio_screenplay_source",
        )

    with st.container(border=True):
        st.markdown("**正式小說來源**")
        if source is None:
            st.caption("只有已完成、非試寫且由你明確接受的場景草稿，才能改編成劇本。")
        else:
            snapshot = source.snapshot
            st.success("正式小說來源已確認。")
            st.caption(
                f"草稿 ID `{snapshot.draft_id}` · 場景卡版本 ID `{snapshot.scene_card_version_id}` "
                f"· 內容模式 `{snapshot.content_mode.value}`"
            )
            st.caption(f"內容 SHA-256 `{snapshot.prose_sha256}` · 快照 SHA-256 `{snapshot.sha256}`")
            with st.expander("查看本次釘選的完整正式小說"):
                st.code(source.prose_text, language=None)

        title = st.text_input(
            "新改編標題（選填）",
            key="story_adaptation_title",
            placeholder="留白時使用場景標題",
        )
        if st.button(
            "建立獨立劇本改編",
            key="story_screenplay_create_root",
            type="primary" if not views else "secondary",
            disabled=source is None,
            width="stretch",
        ):
            try:
                created = services.screenplay_adaptations.create_from_current_source(
                    scene_id,
                    title=title,
                )
                st.session_state["story_screenplay_pending_adaptation_id"] = created.adaptation.id
                st.session_state["story_screenplay_flash"] = (
                    "success",
                    "已建立獨立劇本改編；尚未建立或接受任何劇本版本。",
                )
                st.rerun()
            except (ApplicationError, ValueError) as exc:
                present_exception(
                    exc,
                    operation_zh_tw="執行 Story Studio 操作",
                    key_prefix="story_studio_error",
                )

    if not views:
        st.info("目前場景還沒有劇本改編；可以先建立一份獨立改編。")
        return

    view_by_id = {view.adaptation.id: view for view in views}
    pending_adaptation_id = st.session_state.pop("story_screenplay_pending_adaptation_id", None)
    if (
        pending_adaptation_id in view_by_id
        and st.session_state.get("story_adaptation_id") != pending_adaptation_id
    ):
        _clear_screenplay_root_state()
        st.session_state["story_adaptation_id"] = pending_adaptation_id
        st.rerun()
    if st.session_state.get("story_adaptation_id") not in view_by_id:
        st.session_state.pop("story_adaptation_id", None)

    health_labels = {
        AdaptationSourceHealth.FRESH: "來源最新",
        AdaptationSourceHealth.SOURCE_STALE: "來源已變更",
        AdaptationSourceHealth.SOURCE_MISSING: "來源遺失",
        AdaptationSourceHealth.SOURCE_INTEGRITY_ERROR: "來源完整性異常",
    }

    def adaptation_label(adaptation_id: str) -> str:
        item = view_by_id[adaptation_id]
        record = item.adaptation
        accepted = "已接受" if record.accepted_revision_id else "未接受"
        return (
            f"{record.title}｜{health_labels[item.source_health]}｜{accepted}｜{_short(record.id)}"
        )

    adaptation_id = st.selectbox(
        "劇本改編",
        list(view_by_id),
        format_func=adaptation_label,
        key="story_adaptation_id",
        on_change=_clear_screenplay_selection_scope,
    )
    selected_view = view_by_id[adaptation_id]
    root = selected_view.adaptation
    health = selected_view.source_health
    fresh = health is AdaptationSourceHealth.FRESH and root.lifecycle_status == "active"

    st.caption(
        f"改編 ID `{root.id}` · 來源快照 `{root.source_snapshot_sha256}` "
        f"· 工作版本 `{root.working_revision_id or '無'}` "
        f"· 已接受版本 `{root.accepted_revision_id or '無'}`"
    )
    if root.lifecycle_status != "active":
        st.warning("此劇本改編已封存，只能閱讀。")
    elif health is AdaptationSourceHealth.FRESH:
        st.success("來源仍與目前已接受的正式小說版本一致。")
    elif health is AdaptationSourceHealth.SOURCE_STALE:
        st.warning(
            "此改編釘選的正式小說已不是目前版本。舊劇本仍保留可讀，"
            "但系統不會靜默重綁；新增、生成、複核與接受均已停用。"
        )
        if st.button(
            "從目前正式小說建立新改編",
            key="story_screenplay_create_successor",
            disabled=source is None,
            width="stretch",
        ):
            try:
                created = services.screenplay_adaptations.create_from_current_source(
                    scene_id,
                    title=title,
                    supersedes_adaptation_id=root.id,
                )
                st.session_state["story_screenplay_pending_adaptation_id"] = created.adaptation.id
                st.session_state["story_screenplay_flash"] = (
                    "success",
                    "已從目前正式小說建立新改編；舊改編保持不變。",
                )
                st.rerun()
            except (ApplicationError, ValueError) as exc:
                present_exception(
                    exc,
                    operation_zh_tw="執行 Story Studio 操作",
                    key_prefix="story_studio_error",
                )
    elif health is AdaptationSourceHealth.SOURCE_MISSING:
        st.error("歷史來源已不存在；為避免錯綁，此改編只能閱讀。")
    else:
        st.error("無法確認來源版本或角色版本；此改編只能閱讀。")

    st.divider()
    with st.container(border=True):
        st.markdown("**手寫完整候選（不需 AI）**")
        if st.session_state.pop("story_screenplay_clear_manual_pending", False):
            st.session_state.pop("story_screenplay_manual_text", None)
        manual_text = st.text_area(
            "完整劇本內容",
            key="story_screenplay_manual_text",
            height=240,
            disabled=not fresh,
            placeholder="貼上或手寫一份完整 screenplay candidate。",
        )
        if st.button(
            "另存手寫候選",
            key="story_screenplay_save_manual",
            disabled=not fresh or not manual_text.strip(),
            width="stretch",
        ):
            try:
                revision = services.screenplay_adaptations.add_manual_revision(
                    root.id,
                    screenplay_text=manual_text,
                    expected_working_revision_id=root.working_revision_id,
                    change_note="作者手寫完整候選。",
                )
                st.session_state["story_screenplay_pending_revision_id"] = revision.id
                st.session_state["story_screenplay_clear_manual_pending"] = True
                st.session_state["story_screenplay_flash"] = (
                    "success",
                    f"已建立手寫 v{revision.version_number} 工作候選；尚未接受。",
                )
                st.rerun()
            except (ApplicationError, ValueError) as exc:
                present_exception(
                    exc,
                    operation_zh_tw="執行 Story Studio 操作",
                    key_prefix="story_studio_error",
                )

    with st.container(border=True):
        st.markdown("**OpenAI／Ollama 改編候選**")
        st.caption("只會把這份改編綁定的正式小說與下方設定送給模型；模型輸出不會自動接受。")
        inspiration_col, preserve_col = st.columns((2, 3), vertical_alignment="center")
        preserve_screenplay_brief = preserve_col.checkbox(
            "保留已填寫的改編方向與目前片長／節奏，只補空白文字",
            value=True,
            key="story_screenplay_random_preserve",
        )
        inspiration_col.button(
            "✦ 離線隨機填入改編方向",
            key="story_screenplay_random_fill",
            disabled=not fresh or active_job,
            width="stretch",
            help="只使用內建素材；不呼叫模型、不建立版本，也不改動原小說。",
            on_click=_fill_random_screenplay_brief,
            args=(preserve_screenplay_brief,),
        )
        st.session_state.setdefault("story_screenplay_target_minutes", 8)
        brief_col_1, brief_col_2, brief_col_3 = st.columns(3)
        target_minutes = brief_col_1.number_input(
            "目標分鐘／劇本頁數（估算）",
            min_value=1,
            max_value=180,
            step=1,
            key="story_screenplay_target_minutes",
        )
        displayed_length_spec = screenplay_length_spec_for_minutes(int(target_minutes))
        pacing = brief_col_2.selectbox(
            "節奏",
            list(ScreenplayPacing),
            format_func=lambda value: value.value,
            key="story_screenplay_pacing",
        )
        dialogue_retention = brief_col_3.selectbox(
            "對白保留",
            list(DialogueRetention),
            format_func=lambda value: value.value,
            key="story_screenplay_dialogue_retention",
        )
        st.caption(
            "長度合約：1 分鐘按 1 劇本頁估算；每頁按 250 words 機械量測。"
            f"本次最少 {displayed_length_spec.minimum}、目標 "
            f"{displayed_length_spec.target}、最多 {displayed_length_spec.maximum} 頁；"
            f"最多自動接續 {displayed_length_spec.max_continuations} 次。"
        )
        direction = st.text_area(
            "額外改編方向",
            key="story_screenplay_direction",
            max_chars=4_000,
        )
        must_col, avoid_col = st.columns(2)
        must_include = must_col.text_area(
            "必須包含（每行一項）",
            key="story_screenplay_must_include",
        )
        must_avoid = avoid_col.text_area(
            "禁止出現（每行一項）",
            key="story_screenplay_must_avoid",
        )
        screenplay_seed = st.session_state.get("story_screenplay_random_seed")
        if isinstance(screenplay_seed, int) and not isinstance(screenplay_seed, bool):
            st.caption(f"離線劇本靈感種子：`{screenplay_seed}`；所有欄位都能在送出前自由修改。")
        screenplay_ollama_context = _render_story_ollama_context_controls(
            ai_runtime,
            active=active_job,
            key_prefix="story_screenplay_ollama",
            heading="劇本 Ollama 上下文（進階）",
        )
        screenplay_ollama_context_ready = (
            ai_runtime.config is None
            or ai_runtime.config.provider_name != "ollama"
            or screenplay_ollama_context is not None
        )
        generation_disabled = (
            not fresh
            or active_job
            or not ai_runtime.can_send
            or not screenplay_ollama_context_ready
        )
        if not ai_runtime.can_send:
            st.caption("請到「AI 設定」完成全域 final-writing route 與 credential 設定。")
        generation_columns = st.columns(2)
        guided_generation = generation_columns[0].button(
            "依目前設定產生劇本候選",
            key="story_screenplay_generate",
            type="primary",
            disabled=generation_disabled,
            width="stretch",
        )
        random_generation = generation_columns[1].button(
            "一鍵隨機產生完整劇本候選",
            key="story_screenplay_generate_random",
            disabled=generation_disabled,
            width="stretch",
            help="先在本機建立一組隨機改編設定，再呼叫目前選定的模型；結果仍是未接受候選。",
        )
        if ai_runtime.mode is GenerationMode.OPENAI:
            st.caption(
                "全域 route 為 OpenAI 時，一鍵隨機會在本機建立新方向並立即送出；"
                "會沿用作者已啟用的全域遠端設定，結果仍是未接受候選。"
            )
        if guided_generation or random_generation:
            generated_values: dict[str, object] | None = None
            try:
                if random_generation:
                    generated_brief = generate_random_screenplay_brief()
                    generated_values = _merged_screenplay_brief_widget_values(
                        generated_brief,
                        preserve_written=preserve_screenplay_brief,
                    )
                    brief = _screenplay_brief_from_widget_values(generated_values)
                else:
                    brief = ScreenplayBrief(
                        target_minutes=int(target_minutes),
                        pacing=pacing,
                        dialogue_retention=dialogue_retention,
                        additional_direction=direction,
                        must_include=_lines(must_include),
                        must_avoid=_lines(must_avoid),
                    )
                _start_screenplay_generation_job(
                    ai_runtime,
                    GenerateScreenplayRequest(
                        scene_id=scene_id,
                        model=ai_runtime.model,
                        brief=brief,
                        adaptation_id=root.id,
                        expected_working_revision_id=root.working_revision_id,
                        options=GenerationOptions(
                            num_ctx=(
                                screenplay_ollama_context.num_ctx
                                if screenplay_ollama_context is not None
                                else None
                            ),
                            num_predict=(
                                screenplay_ollama_context.num_predict_cap
                                if screenplay_ollama_context is not None
                                else None
                            ),
                        ),
                        timeout_s=300.0,
                        length_spec=screenplay_length_spec_for_minutes(brief.target_minutes),
                    ),
                    expose_live_preview=True,
                    generated_values=generated_values,
                )
                st.rerun()
            except ProviderError as exc:
                provider_label = "OpenAI" if ai_runtime.mode is GenerationMode.OPENAI else "Ollama"
                present_exception(
                    exc,
                    operation_zh_tw="產生劇本改編候選",
                    key_prefix="story_screenplay_provider",
                    data_safety=ErrorDataSafety(
                        message_zh_tw=(
                            "這次沒有建立、接受或套用劇本候選；原小說、既有劇本版本、"
                            "未儲存的改編設定與目前選擇均保留。"
                        ),
                        author_input_preserved=True,
                        original_preserved=True,
                        partial_preserved=True,
                        completed_work_preserved=True,
                        draft_selection_preserved=True,
                    ),
                    provider_label=provider_label,
                )
            except (ApplicationError, ValueError) as exc:
                present_exception(
                    exc,
                    operation_zh_tw="執行 Story Studio 操作",
                    key_prefix="story_studio_error",
                )

    try:
        revisions = services.screenplay_adaptations.list_revisions(root.id)
        runs = services.screenplay_adaptations.list_runs(root.id)
    except (ApplicationError, ValueError) as exc:
        present_exception(
            exc,
            operation_zh_tw="執行 Story Studio 操作",
            key_prefix="story_studio_error",
        )
        return

    st.divider()
    st.subheader("劇本版本")
    if not revisions:
        st.info("還沒有劇本候選。手寫或 AI 產生後，版本會出現在這裡。")
    else:
        revision_by_id = {revision.id: revision for revision in revisions}
        pending_revision_id = st.session_state.pop("story_screenplay_pending_revision_id", None)
        if pending_revision_id in revision_by_id:
            st.session_state["story_screenplay_revision_id"] = pending_revision_id
        if st.session_state.get("story_screenplay_revision_id") not in revision_by_id:
            st.session_state.pop("story_screenplay_revision_id", None)

        def revision_label(revision_id: str) -> str:
            revision = revision_by_id[revision_id]
            marks = []
            if revision.id == root.working_revision_id:
                marks.append("working")
            if revision.id == root.accepted_revision_id:
                marks.append("已接受")
            suffix = f"｜{'・'.join(marks)}" if marks else ""
            return f"v{revision.version_number}｜{revision.origin}{suffix}｜{_short(revision.id)}"

        revision_id = st.selectbox(
            "版本",
            list(revision_by_id),
            format_func=revision_label,
            key="story_screenplay_revision_id",
        )
        selected_revision = revision_by_id[revision_id]
        editor_scope = f"{root.id}:{selected_revision.id}"
        if st.session_state.get("story_screenplay_editor_scope") != editor_scope:
            st.session_state["story_screenplay_editor"] = selected_revision.screenplay_text
            st.session_state["story_screenplay_editor_scope"] = editor_scope
        else:
            st.session_state.setdefault(
                "story_screenplay_editor", selected_revision.screenplay_text
            )
        edited_text = st.text_area(
            "劇本內容（編輯後只能另存新版本）",
            key="story_screenplay_editor",
            height=420,
            disabled=not fresh,
        )
        selected_measurement = measure_output(
            selected_revision.screenplay_text,
            unit=LengthUnit.SCREENPLAY_PAGES,
        )
        st.caption(
            f"SHA-256 `{selected_revision.screenplay_sha256}` · "
            f"上一版本 ID `{selected_revision.parent_revision_id or '無'}` · "
            f"完成狀態 `{selected_revision.completion_status}` · "
            f"{selected_measurement.screenplay_pages_estimate:.3f} 劇本頁（估算） · "
            f"{selected_measurement.words} words · "
            f"{selected_measurement.visible_chars} 可見字元"
        )
        if selected_revision.completion_status != "complete":
            st.warning("這是未達長度合約的 partial；可繼續編輯，但不能直接接受為正式劇本。")
        change_note = st.text_input(
            "新版本說明（選填）",
            key="story_screenplay_change_note",
            max_chars=1_000,
            disabled=not fresh,
        )
        edit_col, accept_col = st.columns(2)
        if edit_col.button(
            "另存為新版本",
            key="story_screenplay_save_edited",
            disabled=not fresh or not edited_text.strip(),
            width="stretch",
        ):
            try:
                child = services.screenplay_adaptations.add_edited_revision(
                    root.id,
                    parent_revision_id=selected_revision.id,
                    screenplay_text=edited_text,
                    expected_working_revision_id=root.working_revision_id,
                    change_note=change_note,
                )
                st.session_state["story_screenplay_pending_revision_id"] = child.id
                st.session_state["story_screenplay_flash"] = (
                    "success",
                    f"已建立第 {child.version_number} 版工作候選；尚未接受。",
                )
                st.rerun()
            except (ApplicationError, ValueError) as exc:
                present_exception(
                    exc,
                    operation_zh_tw="執行 Story Studio 操作",
                    key_prefix="story_studio_error",
                )
        if accept_col.button(
            "明確接受此劇本版本",
            key="story_screenplay_accept",
            type="primary",
            disabled=(
                not fresh
                or selected_revision.id == root.accepted_revision_id
                or selected_revision.completion_status != "complete"
            ),
            width="stretch",
        ):
            try:
                services.screenplay_adaptations.accept_revision(
                    root.id,
                    selected_revision.id,
                    expected_accepted_revision_id=root.accepted_revision_id,
                )
                st.session_state["story_screenplay_flash"] = (
                    "success",
                    f"已接受劇本第 {selected_revision.version_number} 版。"
                    "只更新目前接受的劇本版本；原小說沒有被覆寫。",
                )
                st.rerun()
            except (ApplicationError, ValueError) as exc:
                present_exception(
                    exc,
                    operation_zh_tw="執行 Story Studio 操作",
                    key_prefix="story_studio_error",
                )


    if runs:
        with st.expander(f"劇本生成紀錄（{len(runs)} 筆）"):
            for run in runs:
                st.caption(
                    f"{run.started_at[:19]}｜"
                    f"{_generation_run_status_label(run.status)}"
                    f"｜{run.provider}｜{run.model}｜{run.latency_ms} ms"
                    f"｜輸入檢查碼 `{run.input_snapshot_sha256[:12]}…`"
                )
                if run.status == "failed":
                    label = "OpenAI" if run.provider == "openai" else "Ollama"
                    diagnostic = diagnose_provider_reason(
                        run.reason_code,
                        provider_label=label,
                    )
                    st.caption(diagnostic.message_zh_tw)


def _close_long_form_workspace() -> None:
    st.session_state["story_longform_workspace_open"] = False


def _render_long_form_header(services: Any) -> None:
    """Keep long-form controls near the manuscript, without duplicate navigation."""

    st.title("故事寫作")
    back, resume = st.columns(2)
    back.button(
        "返回場景／短篇工作區",
        key="story_longform_workspace_close",
        on_click=_close_long_form_workspace,
        width="stretch",
    )
    resume.button(
        "繼續上次工作",
        key="story_shortcut_continue_long_form",
        on_click=_queue_long_form_entry,
        args=("continue",),
        width="stretch",
    )
    try:
        readiness = generation_action_readiness(
            services, st.session_state,
            task=GenerationTask.FINAL_WRITING, action_id="story_scene",
        )
    except ProviderError:
        st.caption("全域寫作引擎尚未就緒；仍可整理本機大綱，原稿不變。")
    else:
        route = readiness.route.primary
        st.caption(
            f"全域寫作引擎：{route.provider_id} · {route.model}｜"
            "大綱先在本機整理，正文只在你確認並開始後執行。"
        )


def render() -> None:
    _apply_story_ai_state_transitions()
    # Streamlit forbids writing a widget key AFTER its widget exists, so newly
    # created IDs are staged here and applied before any widget is built.
    pending_raw = st.session_state.pop("story_pending", {})
    pending_values = pending_raw if isinstance(pending_raw, dict) else {}
    pending_generation_purpose = (
        _normalize_story_generation_purpose(pending_values[_STORY_GENERATION_TASK_KEY])
        if _STORY_GENERATION_TASK_KEY in pending_values
        else None
    )
    for key, value in pending_values.items():
        st.session_state[key] = value
    if pending_generation_purpose is not None:
        st.session_state[_STORY_GENERATION_PREVIOUS_TASK_KEY] = pending_generation_purpose.value

    # drain the background job queue once per run, before any widget
    # reads the state it updates. Non-blocking by contract — the page must
    # never wait for a worker.
    #
    # this handles the race where the terminal arrives while a full
    # rerun is already in flight. It deliberately ignores the return value:
    # this IS a full render, so asking for another one would double the
    # refresh the timed fragment already accounts for.
    _poll_active_job()
    _poll_screenplay_job()
    # A terminal poll may schedule credential cleanup. Apply it before any
    # action can capture a new provider snapshot.
    _apply_story_ai_state_transitions()

    # S8.2: a prior process can leave a persistent RUNNING row without a
    # worker. Recovery is idempotent and the service consults this session's
    # in-memory job registry before finalizing anything.
    _recover_stale_running_runs()

    services = get_services()
    page_has_active_job = bool(
        st.session_state.get("story_active_job_id")
        or st.session_state.get(_SCREENPLAY_ACTIVE_JOB_ID_KEY)
    )
    project_id = st.session_state.get("selected_project_id")
    if project_id:
        st.session_state.pop(_STORY_ENTRY_NOTICE_KEY, None)
        _restore_story_structure_selection(project_id, pending_keys=frozenset(pending_values))
    restored_section = _normalize_story_section(
        st.session_state.get(
            _STORY_ACTIVE_SECTION_KEY,
            st.session_state.get(_STORY_ACTIVE_SECTION_DURABLE_KEY),
        )
    )
    if (
        project_id
        and restored_section is StoryStudioSection.GENERATION
        and bool(st.session_state.get("story_longform_workspace_open"))
    ):
        # Keep section widget values alive even though the full Studio shell
        # is not mounted here. Route intent and job/secret handling above are
        # shared with normal Studio; no hidden duplicate widgets are created.
        st.session_state[_STORY_ACTIVE_SECTION_KEY] = restored_section.value
        st.session_state[_STORY_ACTIVE_SECTION_DURABLE_KEY] = restored_section.value
        _preserve_story_section_state()
        _render_long_form_header(services)
        scene_id = str(st.session_state.get("story_scene_id", ""))
        if st.session_state.get("story_active_job_id"):
            _render_active_job_fragment(scene_id)
        if st.session_state.get(_SCREENPLAY_ACTIVE_JOB_ID_KEY):
            _render_screenplay_job_fragment(scene_id)
        render_durable_story_workflow(
            services, project_id=project_id, scene_id=scene_id,
        )
        return

    page_header(
        "故事寫作",
        "從故事方向、大綱與章節一路寫到場景、完整故事、修訂與匯出。",
        eyebrow="把這本書一頁頁寫下去",
        badges=(
            ("正式版本由你確認", "teal"),
            ("手寫／離線／全域 AI", ""),
        ),
    )
    # R2 direct writing entrances remain usable before a project, chapter, or
    # Scene Card exists. Project selection is requested only after the author
    # has chosen a destination; no provider or persistence action happens here.
    _render_story_generation_shortcuts(active=page_has_active_job)
    if not project_id:
        render_project_gateway(
            key_prefix="story_studio",
            copy=ProjectGatewayCopy(
                feature_name="故事寫作",
                project_reason="章節、大綱、上下文與版本需要一本作品來維持前後連貫。",
                draft_description="若現在只想寫一個人的過去，可以先做獨立角色與個人故事草稿。",
                draft_button_label="先寫角色個人故事",
            ),
        )
        return

    active_section = _render_story_active_section()
    _preserve_story_section_state()

    ai_runtime = _render_ai_runtime(
        services,
        active=page_has_active_job,
    )

    characters = services.characters.list_characters(project_id)
    # Character Version selectors are keyed by the stable version ID.
    # Labels are presentation-only (via format_func) and must never act as
    # identity: two characters may share a name AND a version number, and a
    # label-keyed dict silently overwrote one of them.
    # version_id -> (character_id, character_version_id) for exact pinning
    version_options: dict[str, tuple[str, str]] = {}
    # version_id -> human-readable, collision-resistant display label
    version_labels: dict[str, str] = {}
    for character in characters:
        for version in services.versions.list_versions(character.id):
            version_options[version.id] = (character.id, version.id)
            version_labels[version.id] = (
                f"{character.name} — v{version.version_number}"
                f" — char {_short(character.id)}"
                f" — ver {_short(version.id)}"
            )

    # Session state may still carry older display labels
    # (e.g. "凜　v1"). A stale label must never reach an ID-valued widget, so
    # normalize here — BEFORE any of these widgets exists in this rerun.
    # Values are kept only when they are valid version IDs; there is no fuzzy
    # matching by visible label.
    stale_participants = st.session_state.get("story_participants")
    if isinstance(stale_participants, list):
        cleaned = [v for v in stale_participants if v in version_options]
        if cleaned != stale_participants:
            st.session_state["story_participants"] = cleaned
    stale_link = st.session_state.get("story_bible_link")
    if stale_link and stale_link not in version_options:
        st.session_state["story_bible_link"] = ""

    story_workspace_shell = st.container(key="story_workspace_shell")
    left, center, right = story_workspace_shell.columns([1.0, 2.2, 1.0])

    # ============================================================== LEFT
    with left:
        st.subheader("故事結構")

        requirements = services.story_requirements.list_for_project(project_id)
        req_labels = {r.id: r.title for r in requirements}
        req_id = st.selectbox(
            "故事需求",
            options=list(req_labels) or [""],
            format_func=lambda x: req_labels.get(x, "（尚未建立）"),
            key="story_req_id",
        )
        new_title = st.text_input("新增標題", key="story_new_req")
        if st.button("建立故事需求", key="story_create_req"):
            try:
                created = services.story_requirements.create(project_id=project_id, title=new_title)
                st.session_state["story_pending"] = {"story_req_id": created.id}
                st.rerun()
            except ApplicationError as exc:
                present_exception(
                    exc,
                    operation_zh_tw="執行 Story Studio 操作",
                    key_prefix="story_studio_error",
                )

        bibles = services.story_bibles.list_for_project(project_id)
        bible_labels = {b.id: b.title for b in bibles}
        bible_id = st.selectbox(
            "故事聖經",
            options=list(bible_labels) or [""],
            format_func=lambda x: bible_labels.get(x, "（尚未建立）"),
            key="story_bible_id",
        )
        if st.button("建立故事聖經", key="story_create_bible"):
            try:
                created = services.story_bibles.create(
                    project_id=project_id, title=new_title or "故事聖經"
                )
                st.session_state["story_pending"] = {"story_bible_id": created.id}
                st.rerun()
            except ApplicationError as exc:
                present_exception(
                    exc,
                    operation_zh_tw="執行 Story Studio 操作",
                    key_prefix="story_studio_error",
                )

        outlines = services.story_outlines.list_for_project(project_id)
        outline_labels = {o.id: o.title for o in outlines}
        outline_id = st.selectbox(
            "大綱",
            options=list(outline_labels) or [""],
            format_func=lambda x: outline_labels.get(x, "（尚未建立）"),
            key="story_outline_id",
        )
        if st.button("建立大綱", key="story_create_outline"):
            try:
                created = services.story_outlines.create(
                    project_id=project_id, title=new_title or "主線大綱"
                )
                st.session_state["story_pending"] = {"story_outline_id": created.id}
                st.rerun()
            except ApplicationError as exc:
                present_exception(
                    exc,
                    operation_zh_tw="執行 Story Studio 操作",
                    key_prefix="story_studio_error",
                )

        chapter_id = ""
        scene_id = ""
        if outline_id:
            chapters = services.chapter_plans.list_chapters(outline_id)
            chapter_labels = {c.id: f"第 {c.chapter_number} 章　{c.title}" for c in chapters}
            chapter_id = st.selectbox(
                "章節",
                options=list(chapter_labels) or [""],
                format_func=lambda x: chapter_labels.get(x, "（尚無章節）"),
                key="story_chapter_id",
            )
            new_chapter = st.text_input("新章節標題", key="story_new_chapter")
            if st.button("新增章節", key="story_add_chapter"):
                try:
                    new_chapter_record = services.chapter_plans.create_chapter(
                        outline_id=outline_id, title=new_chapter
                    )
                    st.session_state["story_pending"] = {"story_chapter_id": new_chapter_record.id}
                    st.rerun()
                except ApplicationError as exc:
                    present_exception(
                        exc,
                        operation_zh_tw="執行 Story Studio 操作",
                        key_prefix="story_studio_error",
                    )

            if chapter_id:
                scenes = services.scene_cards.list_scenes(chapter_id)
                scene_labels = {s.id: f"場景 {s.scene_number}　{s.title}" for s in scenes}
                scene_id = st.selectbox(
                    "場景",
                    options=list(scene_labels) or [""],
                    format_func=lambda x: scene_labels.get(x, "（尚無場景）"),
                    key="story_scene_id",
                )
                new_scene = st.text_input("新場景標題", key="story_new_scene")
                if st.button("新增場景", key="story_add_scene"):
                    try:
                        new_scene_record = services.scene_cards.create_scene(
                            chapter_id=chapter_id, title=new_scene
                        )
                        st.session_state["story_pending"] = {"story_scene_id": new_scene_record.id}
                        st.rerun()
                    except ApplicationError as exc:
                        present_exception(
                            exc,
                            operation_zh_tw="執行 Story Studio 操作",
                            key_prefix="story_studio_error",
                        )

    st.session_state[_STORY_STRUCTURE_SELECTION_KEY] = (
        project_id,
        dict(zip(
            _STORY_STRUCTURE_WIDGET_KEYS,
            (req_id, bible_id, outline_id, chapter_id, scene_id),
            strict=True,
        )),
    )

    # Bind free-form author text to the exact project/scene before any of its
    # widgets are created in the center column.
    _bind_author_inputs_to_scene(
        services,
        project_id=project_id,
        scene_id=scene_id,
        generation_purpose_override=pending_generation_purpose,
    )

    # ================================================== loaders
    # Every loader stages editor state through the pending-form mechanism
    # Streamlit forbids writing a widget key after that widget exists
    # in the current rerun, so values are queued in ``story_pending`` and
    # applied at the top of render before any widget is built. Loading
    # mutates nothing and creates no database row; only the normal save
    # action persists anything.

    def _find_version_record(versions: list[Any], version_id: str) -> Any | None:
        return next((v for v in versions if v.id == version_id), None)

    def _load_requirement_version(version_id: str) -> None:
        """restore every persisted StoryRequirement field. Tuples become
        newline text, enums stay enum objects, and the 11 fields the form does
        not display travel in the loaded base for the save-time merge."""
        record = _find_version_record(services.story_requirements.list_versions(req_id), version_id)
        if record is None:
            st.error(f"找不到需求版本：{version_id}")
            return
        requirement = services.story_requirements.load(version_id)
        st.session_state["story_pending"] = {
            "story_concept": requirement.concept,
            "story_genre": requirement.genre,
            "story_tone": requirement.tone,
            "story_pov": requirement.pov,
            "story_tense": requirement.tense,
            "story_conflict": requirement.central_conflict,
            "story_themes": "\n".join(requirement.themes),
            "story_must": "\n".join(requirement.must_include),
            "story_avoid": "\n".join(requirement.must_avoid),
            "story_violence": requirement.violence_intensity,
            "story_horror": requirement.horror_intensity,
            "story_req_mode": requirement.content_mode,
            "story_loaded_requirement_version_id": version_id,
            "story_loaded_requirement_context": {
                "entity_id": req_id,
                "version_number": record.version_number,
                "accepted": record.accepted,
                "warnings": (),
                "base": requirement,
            },
        }
        st.rerun()

    def _load_bible_version(version_id: str) -> None:
        """restore the Bible form. The Canon link keeps the EXACT stored
        Character Version ID — never re-resolved to the current version. With
        more than one character entry, the first is editable and the rest are
        announced and carried through save untouched (never silently dropped).
        """
        record = _find_version_record(services.story_bibles.list_versions(bible_id), version_id)
        if record is None:
            st.error(f"找不到聖經版本：{version_id}")
            return
        bible = services.story_bibles.load(version_id)
        entry = bible.characters[0] if bible.characters else None
        canon_link = entry.canon_character_version_id if entry else ""
        missing = _missing_version_refs((canon_link,), version_options)
        if missing:
            st.error(
                "無法載入：這份故事設定連結的角色版本已不在目前作品中"
                f"（{'、'.join(_short(m) for m in missing)}）。"
                "為避免以其他版本靜默替換，載入已中止。"
            )
            return
        warnings: tuple[str, ...] = ()
        if entry is not None and len(bible.characters) > 1:
            others = "、".join(c.name for c in bible.characters[1:])
            warnings = (
                f"此版本含 {len(bible.characters)} 個角色條目；表單一次僅能編輯"
                f"第一個（{entry.name}）。其餘條目（{others}）不會顯示在表單，"
                "但儲存時會原樣保留到新版本。",
            )
        st.session_state["story_pending"] = {
            "story_bible_title": bible.title,
            "story_logline": bible.logline,
            "story_world": "\n".join(bible.world_rules),
            "story_prohibited": "\n".join(bible.narrative_contract.prohibited_phrases),
            "story_forbidden": "\n".join(bible.narrative_contract.forbidden_reveals),
            "story_promise": bible.story_promise,
            "story_bible_char_name": entry.name if entry else "",
            "story_bible_link": canon_link,
            "story_loaded_bible_version_id": version_id,
            "story_loaded_bible_context": {
                "entity_id": bible_id,
                "version_number": record.version_number,
                "accepted": record.accepted,
                "warnings": warnings,
                "base": bible,
            },
        }
        st.rerun()

    def _load_outline_version(version_id: str) -> None:
        """restore the outline form; acts / arc beats / foreshadowing
        have no widgets and travel in the loaded base for the save merge."""
        record = _find_version_record(services.story_outlines.list_versions(outline_id), version_id)
        if record is None:
            st.error(f"找不到大綱版本：{version_id}")
            return
        outline = services.story_outlines.load(version_id)
        st.session_state["story_pending"] = {
            "story_structure": outline.structure_profile,
            "story_reveals": "\n".join(outline.reveals),
            "story_withheld": "\n".join(outline.withheld),
            "story_ending": outline.ending_state,
            "story_loaded_outline_version_id": version_id,
            "story_loaded_outline_context": {
                "entity_id": outline_id,
                "version_number": record.version_number,
                "accepted": record.accepted,
                "warnings": (),
                "base": outline,
            },
        }
        st.rerun()

    def _load_chapter_plan_version(version_id: str) -> None:
        """restore the chapter-plan form after verifying the plan's
        chapter number matches the currently selected chapter entity."""
        record = _find_version_record(
            services.chapter_plans.list_plan_versions(chapter_id), version_id
        )
        if record is None:
            st.error(f"找不到章節計畫版本：{version_id}")
            return
        plan = services.chapter_plans.load_plan(version_id)
        chapter = services.chapter_plans.get_chapter(chapter_id)
        if plan.chapter_number != chapter.chapter_number:
            st.error(
                f"章節編號不一致：此計畫版本屬於第 {plan.chapter_number} 章，"
                f"目前選擇的是第 {chapter.chapter_number} 章。載入已中止。"
            )
            return
        st.session_state["story_pending"] = {
            "story_ch_purpose": plan.purpose,
            "story_ch_goal": plan.goal,
            "story_ch_conflict": plan.conflict,
            "story_ch_outcome": plan.outcome,
            "story_loaded_chapter_plan_version_id": version_id,
            "story_loaded_chapter_plan_context": {
                "entity_id": chapter_id,
                "version_number": record.version_number,
                "accepted": record.accepted,
                "warnings": (),
                "base": plan,
            },
        }
        st.rerun()

    def _load_scene_card_version(version_id: str) -> None:
        """restore the Scene Card form. Participants and POV use the
        exact stored Character Version IDs (R08 identity), never labels and
        never the characters' current versions."""
        record = _find_version_record(services.scene_cards.list_card_versions(scene_id), version_id)
        if record is None:
            st.error(f"找不到 Scene Card 版本：{version_id}")
            return
        card = services.scene_cards.load_card(version_id)
        participant_ids = [p.character_version_id for p in card.participants]
        pov_vid = card.pov_character_version_id
        missing = _missing_version_refs((*participant_ids, pov_vid), version_options)
        if missing:
            st.error(
                "無法載入：此 Scene Card 綁定的角色版本已不在本專案中"
                f"（{'、'.join(_short(m) for m in missing)}）。"
                "為避免以其他版本靜默替換，載入已中止。"
            )
            return
        warnings: tuple[str, ...] = ()
        target_words_value = card.target_word_count
        if target_words_value > 20000:
            warnings = (
                f"歷史目標字數 {target_words_value} 超出表單上限 20000，"
                "表單顯示為 20000；若直接儲存，新版本將採表單值。",
            )
            target_words_value = 20000
        st.session_state["story_pending"] = {
            "story_location": card.location,
            "story_time": card.start_time,
            "story_goal_p": card.scene_goal.protagonist,
            "story_goal_n": card.scene_goal.narrative,
            "story_conf_ext": card.conflict.external,
            "story_conf_int": card.conflict.internal,
            "story_entry_emotion": card.entry_state.emotion,
            "story_beats": "\n".join(card.beats),
            "story_turning": card.turning_point,
            "story_exit_emotion": card.exit_state.emotion,
            "story_card_forbidden": "\n".join(card.forbidden_reveals),
            "story_card_must": "\n".join(card.must_include),
            "story_card_avoid": "\n".join(card.avoid),
            "story_target_words": target_words_value,
            "story_card_mode": card.content_mode,
            "story_participants": participant_ids,
            "story_pov_participant": pov_vid,
            "story_loaded_scene_card_version_id": version_id,
            "story_loaded_scene_card_context": {
                "entity_id": scene_id,
                "version_number": record.version_number,
                "accepted": record.accepted,
                "warnings": warnings,
                "base": card,
            },
        }
        st.rerun()

    # ============================================================ CENTER
    with center:
        # This timed fragment is a page-level job status layer. It must stay
        # mounted while the author edits another section; otherwise a real
        # browser stops consuming worker events until the next interaction.
        active_story_job = bool(st.session_state.get("story_active_job_id"))
        active_screenplay_job = bool(st.session_state.get(_SCREENPLAY_ACTIVE_JOB_ID_KEY))
        if active_story_job:
            _render_active_job_fragment(scene_id)
        if active_screenplay_job:
            _render_screenplay_job_fragment(scene_id)
        active_job = active_story_job or active_screenplay_job

        # ---- requirement -------------------------------------------
        if active_section is StoryStudioSection.REQUIREMENT:
            st.caption("這一區可直接手動編輯，不需要 AI。")
            parse_notice = st.session_state.pop("story_requirement_parse_notice", None)
            if isinstance(parse_notice, dict):
                message = str(parse_notice.get("message", ""))
                if message:
                    if parse_notice.get("level") == "warning":
                        st.warning(message)
                    else:
                        st.success(message)
            _render_loaded_marker("requirement", expected_entity_id=req_id)
            concept = st.text_area("概念（必填）", key="story_concept")
            c1, c2 = st.columns(2)
            genre = c1.text_input("類型", key="story_genre")
            tone = c2.text_input("基調", key="story_tone")
            c3, c4 = st.columns(2)
            pov = c3.selectbox(
                "敘事人稱",
                list(NarrativePov),
                format_func=lambda p: p.value,
                key="story_pov",
            )
            tense = c4.selectbox(
                "時態",
                list(NarrativeTense),
                format_func=lambda t: t.value,
                key="story_tense",
            )
            central_conflict = st.text_area("核心衝突", key="story_conflict")
            themes = st.text_area("主題（每行一項）", key="story_themes")
            must_include = st.text_area("必須包含（每行一項）", key="story_must")
            must_avoid = st.text_area("必須避免（每行一項）", key="story_avoid")
            i1, i2 = st.columns(2)
            violence = i1.selectbox(
                "暴力強度",
                list(IntensityLevel),
                format_func=lambda i: i.value,
                key="story_violence",
            )
            horror = i2.selectbox(
                "恐怖強度",
                list(IntensityLevel),
                format_func=lambda i: i.value,
                key="story_horror",
            )
            if st.session_state.get("story_req_mode") not in tuple(ContentMode):
                st.session_state.pop("story_req_mode", None)
            req_mode = st.selectbox(
                "內容模式",
                list(ContentMode),
                format_func=lambda m: m.value,
                key="story_req_mode",
            )
            if st.button("儲存為新需求版本", key="story_save_req"):
                try:
                    # merge the visible edits OVER the loaded base so
                    # the 11 fields this form does not display survive a
                    # load→edit→save cycle, then re-validate the WHOLE model.
                    data = _loaded_base("requirement", expected_entity_id=req_id)
                    data.update(
                        concept=concept,
                        genre=genre,
                        tone=tone,
                        pov=pov,
                        tense=tense,
                        central_conflict=central_conflict,
                        themes=_lines(themes),
                        must_include=_lines(must_include),
                        must_avoid=_lines(must_avoid),
                        violence_intensity=violence,
                        horror_intensity=horror,
                        content_mode=req_mode,
                    )
                    requirement = StoryRequirement.model_validate(data)
                    req_version = services.story_requirements.add_version(
                        req_id, requirement=requirement
                    )
                    _clear_loaded_marker("requirement")
                    st.success(
                        f"已建立需求版本 v{req_version.version_number}"
                        "（尚未接受；生成仍使用已接受版本）"
                    )
                # hardening: a stale loaded base fails closed — no new
                # version is created and the author is told to reload.
                except _StaleLoadedContextError as exc:
                    present_exception(
                        exc,
                        operation_zh_tw="執行 Story Studio 操作",
                        key_prefix="story_studio_error",
                    )
                # P1: Model.model_validate raises ValidationError (a ValueError)
                # on over-long or malformed historical data; show it, never crash.
                except (ApplicationError, ValueError) as exc:
                    present_exception(
                        exc,
                        operation_zh_tw="執行 Story Studio 操作",
                        key_prefix="story_studio_error",
                    )

            # free-text parsing is reachable from the UI
            with st.expander("用 AI 整理故事構想"):
                source_text = st.text_area("自由描述你的故事構想", key="story_free_text")
                st.caption(
                    "解析結果只是草稿，仍需你檢視後才會建立版本。"
                    "模型只能『建議』內容模式，永遠不能授權。"
                )
                if not ai_runtime.config or not ai_runtime.config.uses_llm:
                    st.caption("全域 AI route 尚未就緒；手寫內容仍會完整保留。")
                if st.button(
                    "解析為結構化需求",
                    key="story_parse_req",
                    disabled=not ai_runtime.can_send,
                ):
                    captured: _CapturedProvider | None = None
                    try:
                        assistant, captured = _assistant_for_runtime(ai_runtime)
                        result = assistant.parse_requirement(
                            source_text=source_text,
                            model=ai_runtime.model,
                        )
                        candidate_fields = (
                            ("story_concept", "概念", result.requirement.concept, concept),
                            ("story_genre", "類型", result.requirement.genre, genre),
                            ("story_tone", "基調", result.requirement.tone, tone),
                        )
                        pending: dict[str, str] = {}
                        preserved: list[str] = []
                        for key, label, candidate, current in candidate_fields:
                            if candidate.strip():
                                pending[key] = candidate
                            elif current.strip():
                                preserved.append(label)
                        if result.fallback:
                            notice = (
                                _friendly_provider_error(
                                    result.error_reason.value, result.error_detail
                                )
                                + "已保留你的原文；AI 未提供的既有欄位也未變更。"
                            )
                            notice_level = "warning"
                        else:
                            notice = "已產生需求草稿。"
                            notice_level = "success"
                            if preserved:
                                notice += f" AI 未提供{'、'.join(preserved)}，已保留原本內容。"
                        if result.suggested_content_mode is not None:
                            notice += (
                                f" 模型建議內容模式："
                                f"{result.suggested_content_mode.value}（僅供參考）。"
                            )
                        st.session_state["story_pending"] = pending
                        st.session_state["story_requirement_parse_notice"] = {
                            "level": notice_level,
                            "message": notice,
                        }
                        st.rerun()
                    except ProviderError as exc:
                        present_exception(
                            exc,
                            operation_zh_tw="生成故事需求建議",
                            key_prefix="story_studio_requirement_provider",
                        )
                    except ApplicationError as exc:
                        present_exception(
                            exc,
                            operation_zh_tw="執行 Story Studio 操作",
                            key_prefix="story_studio_error",
                        )
                    finally:
                        _close_captured_provider(captured)
                        _schedule_ai_action_cleanup(
                            mode=ai_runtime.mode,
                            key_retention=ai_runtime.key_retention,
                        )

            # version browser + explicit acceptance
            if req_id:
                _render_version_browser(
                    st,
                    title="需求版本",
                    versions=services.story_requirements.list_versions(req_id),
                    accepted_id=services.story_requirements.get(req_id).accepted_version_id,
                    accept=services.story_requirements.accept_version,
                    load_into_editor=_load_requirement_version,
                    key_prefix="story_req",
                )

        # ---- bible --------------------------------------------------
        if active_section is StoryStudioSection.BIBLE:
            draft_notice = st.session_state.pop("story_bible_draft_notice", None)
            if isinstance(draft_notice, dict):
                message = str(draft_notice.get("message", ""))
                if message:
                    if draft_notice.get("level") == "warning":
                        st.warning(message)
                    else:
                        st.success(message)
            _render_loaded_marker("bible", expected_entity_id=bible_id)
            title = st.text_input("故事標題", key="story_bible_title")
            logline = st.text_area("故事線（logline）", key="story_logline")
            world_rules = st.text_area("世界規則（每行一項）", key="story_world")
            prohibited = st.text_area("禁用語句（每行一項）", key="story_prohibited")
            forbidden = st.text_area("禁止揭露事項（每行一項）", key="story_forbidden")
            promise = st.text_area("故事承諾", key="story_promise")
            char_name = st.text_input("角色名（加入聖經）", key="story_bible_char_name")
            # a Canon link is the PAIR (character, version)
            # option values are Character Version IDs, not labels.
            linked = st.selectbox(
                "連結已保存的角色版本（可留空）",
                options=["", *version_options],
                format_func=lambda vid: version_labels[vid] if vid else "（不連結）",
                key="story_bible_link",
            )
            if st.button("儲存為新聖經版本", key="story_save_bible"):
                try:
                    # nested merge over the loaded base — the contract
                    # keeps its tone/pov/style rules, the first character
                    # entry keeps role/goal/fear/voice/relationships, and any
                    # further entries ride along untouched. Clearing the name
                    # field removes ONLY the first (editable) entry.
                    data = _loaded_base("bible", expected_entity_id=bible_id)
                    contract = dict(data.get("narrative_contract") or {})
                    contract.update(
                        prohibited_phrases=_lines(prohibited),
                        forbidden_reveals=_lines(forbidden),
                    )
                    base_characters = [dict(c) for c in (data.get("characters") or [])]
                    entries: list[dict[str, Any]] = []
                    if char_name.strip():
                        first = base_characters[0] if base_characters else {}
                        canon_character = ""
                        canon_version = ""
                        if linked:
                            canon_character, canon_version = version_options[linked]
                        first.update(
                            name=char_name.strip(),
                            canon_character_id=canon_character,
                            canon_character_version_id=canon_version,
                        )
                        entries = [first, *base_characters[1:]]
                    elif base_characters[1:]:
                        entries = base_characters[1:]
                    data.update(
                        title=title,
                        logline=logline,
                        world_rules=_lines(world_rules),
                        characters=entries,
                        narrative_contract=contract,
                        story_promise=promise,
                    )
                    bible = StoryBible.model_validate(data)
                    requirement_entity = services.story_requirements.get(req_id) if req_id else None
                    accepted_req = (
                        requirement_entity.accepted_version_id if requirement_entity else None
                    )
                    bible_version = services.story_bibles.add_version(
                        bible_id,
                        bible=bible,
                        requirement_version_id=accepted_req,
                    )
                    _clear_loaded_marker("bible")
                    st.success(f"已建立聖經版本 v{bible_version.version_number}")
                # hardening: a stale loaded base fails closed — no new
                # version is created and the author is told to reload.
                except _StaleLoadedContextError as exc:
                    present_exception(
                        exc,
                        operation_zh_tw="執行 Story Studio 操作",
                        key_prefix="story_studio_error",
                    )
                # P1: Model.model_validate raises ValidationError (a ValueError)
                # on over-long or malformed historical data; show it, never crash.
                except (ApplicationError, ValueError) as exc:
                    present_exception(
                        exc,
                        operation_zh_tw="執行 Story Studio 操作",
                        key_prefix="story_studio_error",
                    )

            with st.expander("用 AI 從需求草擬故事聖經"):
                st.caption("草稿不會自動建立正式版本，也不會自行連結作品角色。")
                if not ai_runtime.config or not ai_runtime.config.uses_llm:
                    st.caption("目前是純手寫模式；故事聖經欄位仍可自行編輯與儲存。")
                if st.button(
                    "草擬聖經",
                    key="story_draft_bible",
                    disabled=not ai_runtime.can_send,
                ):
                    captured = None
                    try:
                        # draft the Bible from the ACCEPTED requirement,
                        # not from whichever version happens to be newest.
                        requirement_entity = (
                            services.story_requirements.get(req_id) if req_id else None
                        )
                        accepted_requirement = (
                            requirement_entity.accepted_version_id if requirement_entity else None
                        )
                        if not accepted_requirement:
                            st.error("請先建立並「接受」一個故事需求版本，草擬才會依據定稿的需求。")
                        else:
                            requirement = services.story_requirements.load(accepted_requirement)
                            assistant, captured = _assistant_for_runtime(ai_runtime)
                            bible_result = assistant.draft_bible(
                                requirement=requirement,
                                model=ai_runtime.model,
                            )
                            bible = bible_result.bible
                            bible_candidate_fields = (
                                ("story_bible_title", "故事標題", bible.title, title),
                                ("story_logline", "故事線", bible.logline, logline),
                                (
                                    "story_world",
                                    "世界規則",
                                    "\n".join(bible.world_rules),
                                    world_rules,
                                ),
                                (
                                    "story_prohibited",
                                    "禁用語句",
                                    "\n".join(bible.narrative_contract.prohibited_phrases),
                                    prohibited,
                                ),
                                (
                                    "story_forbidden",
                                    "禁止揭露事項",
                                    "\n".join(bible.narrative_contract.forbidden_reveals),
                                    forbidden,
                                ),
                                (
                                    "story_promise",
                                    "故事承諾",
                                    bible.story_promise,
                                    promise,
                                ),
                            )
                            bible_pending: dict[str, str] = {}
                            bible_preserved: list[str] = []
                            for key, label, candidate, current in bible_candidate_fields:
                                if candidate.strip():
                                    bible_pending[key] = candidate
                                elif current.strip():
                                    bible_preserved.append(label)
                            if bible_result.fallback:
                                notice = (
                                    _friendly_provider_error(
                                        bible_result.error_reason.value,
                                        bible_result.error_detail,
                                    )
                                    + "已依需求內容產生基本草稿；"
                                    "AI 未提供的既有欄位未變更。"
                                )
                                notice_level = "warning"
                            elif not bible_pending:
                                notice = (
                                    "AI 未提供任何可套用的聖經欄位；"
                                    "所有作者內容均已保留，請補充需求後再試。"
                                )
                                notice_level = "warning"
                            else:
                                notice = "已產生聖經草稿。"
                                notice_level = "success"
                                if bible_preserved:
                                    notice += (
                                        f" AI 未提供{'、'.join(bible_preserved)}，已保留原本內容。"
                                    )
                            st.session_state["story_pending"] = bible_pending
                            st.session_state["story_bible_draft_notice"] = {
                                "level": notice_level,
                                "message": notice,
                            }
                            st.rerun()
                    except ProviderError as exc:
                        present_exception(
                            exc,
                            operation_zh_tw="生成故事聖經建議",
                            key_prefix="story_studio_bible_provider",
                        )
                    except ApplicationError as exc:
                        present_exception(
                            exc,
                            operation_zh_tw="執行 Story Studio 操作",
                            key_prefix="story_studio_error",
                        )
                    finally:
                        _close_captured_provider(captured)
                        _schedule_ai_action_cleanup(
                            mode=ai_runtime.mode,
                            key_retention=ai_runtime.key_retention,
                        )

            if bible_id:
                _render_version_browser(
                    st,
                    title="聖經版本",
                    versions=services.story_bibles.list_versions(bible_id),
                    accepted_id=services.story_bibles.get(bible_id).accepted_version_id,
                    accept=services.story_bibles.accept_version,
                    load_into_editor=_load_bible_version,
                    key_prefix="story_bible",
                )

        # ---- outline / chapter --------------------------------------
        if active_section is StoryStudioSection.OUTLINE:
            _render_loaded_marker("outline", expected_entity_id=outline_id)
            structure = st.selectbox(
                "結構模板（可選用，不強制）",
                list(StructureProfile),
                format_func=lambda s: s.value,
                key="story_structure",
            )
            reveals = st.text_area("揭露排程（每行一項）", key="story_reveals")
            withheld = st.text_area("刻意保留（每行一項）", key="story_withheld")
            ending_state = st.text_area("結局狀態", key="story_ending")
            if st.button("儲存為新大綱版本", key="story_save_outline"):
                try:
                    # acts, arc beats and foreshadowing have no
                    # widgets; the merge keeps them from the loaded base.
                    data = _loaded_base("outline", expected_entity_id=outline_id)
                    data.update(
                        structure_profile=structure,
                        reveals=_lines(reveals),
                        withheld=_lines(withheld),
                        ending_state=ending_state,
                    )
                    outline = StoryOutline.model_validate(data)
                    bible_entity = services.story_bibles.get(bible_id) if bible_id else None
                    accepted_bible = bible_entity.accepted_version_id if bible_entity else None
                    outline_version = services.story_outlines.add_version(
                        outline_id, outline=outline, bible_version_id=accepted_bible
                    )
                    _clear_loaded_marker("outline")
                    st.success(f"已建立大綱版本 v{outline_version.version_number}")
                # hardening: a stale loaded base fails closed — no new
                # version is created and the author is told to reload.
                except _StaleLoadedContextError as exc:
                    present_exception(
                        exc,
                        operation_zh_tw="執行 Story Studio 操作",
                        key_prefix="story_studio_error",
                    )
                # P1: Model.model_validate raises ValidationError (a ValueError)
                # on over-long or malformed historical data; show it, never crash.
                except (ApplicationError, ValueError) as exc:
                    present_exception(
                        exc,
                        operation_zh_tw="執行 Story Studio 操作",
                        key_prefix="story_studio_error",
                    )

            if outline_id:
                _render_version_browser(
                    st,
                    title="大綱版本",
                    versions=services.story_outlines.list_versions(outline_id),
                    accepted_id=services.story_outlines.get(outline_id).accepted_version_id,
                    accept=services.story_outlines.accept_version,
                    load_into_editor=_load_outline_version,
                    key_prefix="story_outline",
                )

            st.divider()
            st.caption("章節計畫")
            _render_loaded_marker("chapter_plan", expected_entity_id=chapter_id)
            ch_purpose = st.text_area("章節目的", key="story_ch_purpose")
            ch_goal = st.text_input("章節目標", key="story_ch_goal")
            ch_conflict = st.text_input("章節衝突", key="story_ch_conflict")
            ch_outcome = st.text_input("章節結果", key="story_ch_outcome")
            if st.button("儲存為新章節計畫版本", key="story_save_plan"):
                try:
                    chapter = services.chapter_plans.get_chapter(chapter_id)
                    # pov_character_id, scene intentions, reveals,
                    # forbidden reveals and the target word count have no
                    # widgets; the merge keeps them from the loaded base.
                    data = _loaded_base("chapter_plan", expected_entity_id=chapter_id)
                    data.update(
                        chapter_number=chapter.chapter_number,
                        title=chapter.title,
                        purpose=ch_purpose,
                        goal=ch_goal,
                        conflict=ch_conflict,
                        outcome=ch_outcome,
                    )
                    plan = ChapterPlan.model_validate(data)
                    plan_version = services.chapter_plans.add_plan_version(chapter_id, plan=plan)
                    _clear_loaded_marker("chapter_plan")
                    st.success(f"已建立章節計畫 v{plan_version.version_number}")
                # hardening: a stale loaded base fails closed — no new
                # version is created and the author is told to reload.
                except _StaleLoadedContextError as exc:
                    present_exception(
                        exc,
                        operation_zh_tw="執行 Story Studio 操作",
                        key_prefix="story_studio_error",
                    )
                # P1: Model.model_validate raises ValidationError (a ValueError)
                # on over-long or malformed historical data; show it, never crash.
                except (ApplicationError, ValueError) as exc:
                    present_exception(
                        exc,
                        operation_zh_tw="執行 Story Studio 操作",
                        key_prefix="story_studio_error",
                    )
            if chapter_id:
                _render_version_browser(
                    st,
                    title="章節計畫版本",
                    versions=services.chapter_plans.list_plan_versions(chapter_id),
                    accepted_id=services.chapter_plans.get_chapter(
                        chapter_id
                    ).accepted_plan_version_id,
                    accept=services.chapter_plans.accept_plan_version,
                    load_into_editor=_load_chapter_plan_version,
                    key_prefix="story_plan",
                )

        # ---- scene card ---------------------------------------------
        if active_section is StoryStudioSection.SCENE_CARD:
            _render_loaded_marker("scene_card", expected_entity_id=scene_id)
            location = st.text_input("地點", key="story_location")
            start_time = st.text_input("時間", key="story_time")
            goal_protagonist = st.text_input("主角目標", key="story_goal_p")
            goal_narrative = st.text_input("敘事目標", key="story_goal_n")
            conflict_external = st.text_input("外部衝突", key="story_conf_ext")
            conflict_internal = st.text_input("內在衝突", key="story_conf_int")
            entry_emotion = st.text_input("進場情緒", key="story_entry_emotion")
            beats = st.text_area("節拍（每行一項）", key="story_beats")
            turning_point = st.text_input("轉折", key="story_turning")
            exit_emotion = st.text_input("退場情緒", key="story_exit_emotion")
            card_forbidden = st.text_area("本場景禁止揭露（每行一項）", key="story_card_forbidden")
            card_must = st.text_area("必須包含（每行一項）", key="story_card_must")
            card_avoid = st.text_area("必須避免（每行一項）", key="story_card_avoid")
            target_words = st.number_input(
                "目標字數",
                min_value=0,
                max_value=20000,
                key="story_target_words",
            )
            if st.session_state.get("story_card_mode") not in tuple(ContentMode):
                st.session_state.pop("story_card_mode", None)
            card_mode = st.selectbox(
                "本場景內容模式",
                list(ContentMode),
                format_func=lambda m: m.value,
                key="story_card_mode",
            )
            # participants are (character, version) pairs
            st.caption(
                "參與角色需選到「角色 + 版本」。"
                "場景卡固定綁定該版本，日後角色改版不會回頭改動這張卡。"
            )
            selected_ids = st.multiselect(
                "參與角色版本",
                options=list(version_options),
                format_func=lambda vid: version_labels[vid],
                key="story_participants",
            )
            # the POV option set is the CURRENT participant
            # selection. Drop a session-state value that is either a stale
            # stale label or a just-deselected participant, before the POV
            # widget below is created in this rerun.
            pov_state = st.session_state.get("story_pov_participant")
            if pov_state and pov_state not in selected_ids:
                st.session_state["story_pov_participant"] = ""
            pov_id = st.selectbox(
                "POV 角色版本（需為參與者之一）",
                options=["", *selected_ids],
                format_func=lambda vid: version_labels[vid] if vid else "（未指定）",
                key="story_pov_participant",
            )
            if st.button("儲存為新 Scene Card 版本", key="story_save_card"):
                try:
                    # nested merge over the loaded base. Participant
                    # roles are preserved per Character Version ID; the POV
                    # entry REUSES its participant dict so the model-level
                    # "POV must equal a participant" check compares equal
                    # objects. duration_minutes and the entry/exit note
                    # tuples have no widgets and ride along unchanged.
                    data = _loaded_base("scene_card", expected_entity_id=scene_id)
                    base_participants = {
                        p.get("character_version_id"): dict(p)
                        for p in (data.get("participants") or [])
                    }
                    participants_data: list[dict[str, Any]] = []
                    for vid in selected_ids:
                        participant = dict(base_participants.get(vid) or {})
                        participant.update(
                            character_id=version_options[vid][0],
                            character_version_id=version_options[vid][1],
                        )
                        participants_data.append(participant)
                    pov_data: dict[str, Any] | None = None
                    if pov_id:
                        pov_data = next(
                            p for p in participants_data if p["character_version_id"] == pov_id
                        )
                    scene_goal = dict(data.get("scene_goal") or {})
                    scene_goal.update(protagonist=goal_protagonist, narrative=goal_narrative)
                    conflict_data = dict(data.get("conflict") or {})
                    conflict_data.update(external=conflict_external, internal=conflict_internal)
                    entry_state = dict(data.get("entry_state") or {})
                    entry_state.update(emotion=entry_emotion)
                    exit_state = dict(data.get("exit_state") or {})
                    exit_state.update(emotion=exit_emotion)
                    data.update(
                        location=location,
                        start_time=start_time,
                        participants=participants_data,
                        pov_character=pov_data,
                        scene_goal=scene_goal,
                        conflict=conflict_data,
                        entry_state=entry_state,
                        beats=_lines(beats),
                        turning_point=turning_point,
                        exit_state=exit_state,
                        forbidden_reveals=_lines(card_forbidden),
                        must_include=_lines(card_must),
                        avoid=_lines(card_avoid),
                        target_word_count=int(target_words),
                        content_mode=card_mode,
                    )
                    card = SceneCard.model_validate(data)
                    card_version = services.scene_cards.add_card_version(scene_id, card=card)
                    _clear_loaded_marker("scene_card")
                    st.success(f"已建立 Scene Card v{card_version.version_number}")
                # hardening: a stale loaded base fails closed — no new
                # version is created and the author is told to reload.
                except _StaleLoadedContextError as exc:
                    present_exception(
                        exc,
                        operation_zh_tw="執行 Story Studio 操作",
                        key_prefix="story_studio_error",
                    )
                # P1: Model.model_validate raises ValidationError (a ValueError)
                # on over-long or malformed historical data; show it, never crash.
                except (ApplicationError, ValueError) as exc:
                    present_exception(
                        exc,
                        operation_zh_tw="執行 Story Studio 操作",
                        key_prefix="story_studio_error",
                    )

            if scene_id:
                scene = services.scene_cards.get_scene(scene_id)
                _render_version_browser(
                    st,
                    title="Scene Card 版本",
                    versions=services.scene_cards.list_card_versions(scene_id),
                    accepted_id=scene.accepted_card_version_id,
                    accept=services.scene_cards.accept_card_version,
                    load_into_editor=_load_scene_card_version,
                    key_prefix="story_card",
                )

        # ---- generation + revision ----------------------------------
        if active_section is StoryStudioSection.GENERATION:
            with st.container(key="story_longform_entry_card", border=True):
                st.markdown("#### 要規劃一部長篇嗎？")
                st.caption(
                    "長篇工作區可直接從想法建立大綱與章節，不需要先手動建立 Scene Card；"
                    "關閉後也能從保存位置繼續。"
                )
                st.button(
                    "開啟長篇寫作工作區",
                    key="story_open_longform_workspace",
                    on_click=_queue_long_form_entry,
                    args=("continue",),
                    width="stretch",
                )
            # Active jobs are rendered by the page-level timed fragment above
            # so polling survives a section switch. The terminal status stays
            # owned by this section and is shown again when the author returns.
            if not active_job:
                _render_job_panel(scene_id)
            if not scene_id:
                _render_story_generation_readiness(
                    requirement_id=req_id,
                    bible_id=bible_id,
                    outline_id=outline_id,
                    chapter_id=chapter_id,
                    scene_id=scene_id,
                    has_card_version=False,
                )
            else:
                scene = services.scene_cards.get_scene(scene_id)
                card_versions = services.scene_cards.list_card_versions(scene_id)
                if not card_versions:
                    _render_story_generation_readiness(
                        requirement_id=req_id,
                        bible_id=bible_id,
                        outline_id=outline_id,
                        chapter_id=chapter_id,
                        scene_id=scene_id,
                        has_card_version=False,
                    )
                else:
                    active = st.session_state.get("story_active_job_id", "")
                    restored_purpose = _normalize_story_generation_purpose(
                        st.session_state.get(_STORY_GENERATION_TASK_KEY)
                    )
                    if st.session_state.get(_STORY_GENERATION_TASK_KEY) != restored_purpose.value:
                        st.session_state[_STORY_GENERATION_TASK_KEY] = restored_purpose.value
                    if _STORY_GENERATION_PREVIOUS_TASK_KEY not in st.session_state:
                        st.session_state[_STORY_GENERATION_PREVIOUS_TASK_KEY] = (
                            restored_purpose.value
                        )
                    generation_purpose_raw = st.radio(
                        "這次要生成什麼",
                        options=tuple(purpose.value for purpose in StoryGenerationPurpose),
                        format_func=_story_generation_purpose_label,
                        key=_STORY_GENERATION_TASK_KEY,
                        horizontal=True,
                        disabled=bool(active),
                        help=(
                            "完整短篇仍使用目前場景、故事設定與角色版本，"
                            "但輸出會是一篇有開端、發展、轉折與收束的未接受候選。"
                        ),
                        on_change=_remember_story_generation_purpose,
                    )
                    generation_purpose = StoryGenerationPurpose(generation_purpose_raw)
                    effective = scene.accepted_card_version_id or scene.working_head_card_version_id
                    match = next((v for v in card_versions if v.id == effective), None)
                    if match is None:
                        st.warning(
                            "此場景尚無已接受的 Scene Card 版本。"
                            "請先於上一個分頁接受一個版本，或改用試寫模式。"
                        )
                    else:
                        st.caption(
                            f"本次生成將使用 Scene Card "
                            f"{_version_label(match, effective)}　`{_short(effective)}`"
                        )
                    use_preview = st.checkbox(
                        "使用目前工作版本試寫",
                        key="story_generation_preview",
                        help=(
                            "精確釘選需求、聖經、大綱、章節計畫與 Scene Card 的 working head。"
                            "輸出會標記為試寫，不能直接接受為正式草稿。"
                        ),
                    )
                    preview_version_ids: dict[str, str | None] = {
                        "requirement_version_id": (
                            services.story_requirements.get(req_id).working_head_version_id
                            if req_id
                            else None
                        ),
                        "bible_version_id": (
                            services.story_bibles.get(bible_id).working_head_version_id
                            if bible_id
                            else None
                        ),
                        "outline_version_id": (
                            services.story_outlines.get(outline_id).working_head_version_id
                            if outline_id
                            else None
                        ),
                        "chapter_plan_version_id": (
                            services.chapter_plans.get_chapter(
                                chapter_id
                            ).working_head_plan_version_id
                            if chapter_id
                            else None
                        ),
                        "scene_card_version_id": scene.working_head_card_version_id,
                    }
                    preview_ready = all(preview_version_ids.values())
                    preview_acknowledged = False
                    if use_preview:
                        preview_bound = ":".join(
                            [project_id, scene_id]
                            + [str(value or "") for value in preview_version_ids.values()]
                        )
                        if st.session_state.get("story_preview_warning_bound") != preview_bound:
                            st.session_state.pop("story_preview_warning_ack", None)
                            st.session_state["story_preview_warning_bound"] = preview_bound
                        st.warning(
                            "試寫會使用尚未接受的規劃版本，只適合試寫與檢查；"
                            "之後仍需接受規劃鏈並重新生成，才能成為正式可接受草稿。"
                        )
                        if not preview_ready:
                            missing_preview = "、".join(
                                name.replace("_version_id", "")
                                for name, value in preview_version_ids.items()
                                if not value
                            )
                            st.error(f"試寫所需的故事設定不完整：{missing_preview}")
                        preview_acknowledged = st.checkbox(
                            "我了解這次輸出是試寫，並同意使用目前五層工作版本。",
                            key="story_preview_warning_ack",
                        )
                    keywords = ""
                    continuation_goal = ContinuationGoal.FOLLOW_OUTLINE.value
                    instruction = ""
                    source_excerpt = ""
                    continuation_must_include = ""
                    continuation_must_avoid = ""
                    continuation_min_chars: int | None = None
                    continuation_max_chars: int | None = None
                    pacing = ""
                    complete_story_brief: CompleteStoryBrief | None = None
                    active_length_spec: GenerationLengthSpec | None = None

                    if generation_purpose is StoryGenerationPurpose.COMPLETE_SHORT_STORY:
                        complete_story_brief = _render_complete_story_brief(active=bool(active))
                        length_bounds_valid = complete_story_brief is not None
                        if complete_story_brief is not None:
                            continuation_min_chars = complete_story_brief.min_visible_chars
                            continuation_max_chars = complete_story_brief.max_visible_chars
                            pacing = complete_story_brief.pacing
                        st.caption(
                            "完整短篇的字數以不含空白的可見字元計算；"
                            "模型或帳戶的單次輸出上限仍可能讓成品較短。"
                            "未達長度合約時保留為不可直接接受的 partial；"
                            "達標候選仍須由作者決定是否編輯或接受。"
                        )
                    else:
                        keywords = st.text_input(
                            "續寫關鍵字",
                            key="story_keywords",
                            placeholder="例如：暴雨，失蹤信件，舊車站",
                            help=(
                                "可用中文／英文逗號、分號或換行分隔。關鍵字會與目前"
                                "世界觀、大綱、Scene Card 和歷史上下文一起送入唯一生成流程。"
                            ),
                        )
                        continuation_goal = st.selectbox(
                            "本次續寫目標",
                            options=tuple(goal.value for goal in ContinuationGoal),
                            format_func=_continuation_goal_label,
                            key="story_continuation_goal",
                        )
                        instruction = st.text_area(
                            "本次額外寫作指示",
                            key="story_instruction",
                            help="保留你的原始意圖；系統不會用隨機內容覆蓋這個欄位。",
                        )
                        source_excerpt = st.text_area(
                            "要接續、擴寫或重寫的原文片段（選填）",
                            key="story_continuation_source_excerpt",
                            height=120,
                            help="選擇「擴寫」或「重寫」時請貼上來源片段；其他目標也可作參考。",
                        )
                        direction_left, direction_right = st.columns(2)
                        continuation_must_include = direction_left.text_area(
                            "這次一定要出現（每行一項）",
                            key="story_continuation_must_include",
                        )
                        continuation_must_avoid = direction_right.text_area(
                            "這次不要出現（每行一項）",
                            key="story_continuation_must_avoid",
                        )
                        use_target_words = st.checkbox(
                            "指定這次的字數限制",
                            key="story_continuation_use_target_words",
                        )
                        length_mode = st.radio(
                            "怎麼限制？",
                            options=("range", "minimum", "maximum"),
                            format_func=lambda value: {
                                "range": "設定範圍",
                                "minimum": "至少這麼長",
                                "maximum": "最多這麼長",
                            }[value],
                            horizontal=True,
                            key="story_continuation_length_mode",
                            disabled=not use_target_words,
                        )
                        length_left, length_right = st.columns(2)
                        st.session_state.setdefault("story_continuation_target_words", 1_000)
                        st.session_state.setdefault("story_continuation_max_chars", 1_800)
                        min_chars_value = int(
                            length_left.number_input(
                                "最少字數",
                                min_value=100,
                                max_value=12_000,
                                step=100,
                                key="story_continuation_target_words",
                                disabled=(not use_target_words or length_mode == "maximum"),
                            )
                        )
                        max_chars_value = int(
                            length_right.number_input(
                                "最多字數",
                                min_value=100,
                                max_value=12_000,
                                step=100,
                                key="story_continuation_max_chars",
                                disabled=(not use_target_words or length_mode == "minimum"),
                            )
                        )
                        continuation_min_chars = (
                            min_chars_value
                            if use_target_words and length_mode in {"range", "minimum"}
                            else None
                        )
                        continuation_max_chars = (
                            max_chars_value
                            if use_target_words and length_mode in {"range", "maximum"}
                            else None
                        )
                        length_bounds_valid = not (
                            continuation_min_chars is not None
                            and continuation_max_chars is not None
                            and continuation_min_chars > continuation_max_chars
                        )
                        if use_target_words:
                            st.caption(
                                "字數以不含空白的可見字元計算；生成後會顯示是否達標。"
                                "模型或帳戶本身的單次輸出上限仍可能讓成品較短。"
                            )
                        if not length_bounds_valid:
                            st.error("最少字數不可大於最多字數。")
                        pacing = st.text_input(
                            "節奏偏好（選填）",
                            key="story_continuation_pacing",
                            placeholder="例如：慢熱、緊湊、先靜後動",
                        )
                    if complete_story_brief is not None:
                        active_length_spec = _story_visible_char_length_spec(
                            complete_story_brief.min_visible_chars,
                            complete_story_brief.max_visible_chars,
                            target_hint=complete_story_brief.max_visible_chars,
                            max_continuations=3,
                        )
                    elif (
                        generation_purpose is StoryGenerationPurpose.SCENE
                        and length_bounds_valid
                    ):
                        selected_card_version_id = (
                            preview_version_ids["scene_card_version_id"]
                            if use_preview
                            else effective
                        )
                        scene_target_hint = 1_800
                        if selected_card_version_id:
                            selected_card = services.scene_cards.load_card(
                                str(selected_card_version_id)
                            )
                            if selected_card.target_word_count > 0:
                                scene_target_hint = selected_card.target_word_count
                        active_length_spec = _story_visible_char_length_spec(
                            continuation_min_chars,
                            continuation_max_chars,
                            target_hint=scene_target_hint,
                            max_continuations=1,
                        )
                        if not use_target_words:
                            st.caption(
                                "本次實際長度合約採 Scene Card／場景預設："
                                f"最少 {active_length_spec.minimum}、"
                                f"目標 {active_length_spec.target}、"
                                f"最多 {active_length_spec.maximum} 可見字元；"
                                "正常停止但不足不會視為完成。"
                            )
                        elif length_mode == "maximum":
                            st.caption(
                                "只設定上限時，為避免極短正常停止誤判，完成判定仍使用"
                                f"最少 {active_length_spec.minimum}、"
                                f"目標 {active_length_spec.target}、"
                                f"最多 {active_length_spec.maximum} 可見字元。"
                            )
                    context_budget = _render_context_budget_controls()
                    ollama_context = _render_story_ollama_context_controls(
                        ai_runtime,
                        active=bool(active),
                    )
                    ollama_context_ready = (
                        ai_runtime.mode is not GenerationMode.LOCAL
                        or ollama_context is not None
                    )
                    st.session_state.setdefault("story_stream", False)
                    use_stream = st.checkbox(
                        "顯示模型逐步輸出（暫時預覽，最終仍需安全與長度檢查）",
                        key="story_stream",
                    )

                    with st.expander("直接手寫正文"):
                        manual_prose = st.text_area(
                            "手寫正文",
                            key="story_manual_prose",
                            height=220,
                            help="不論是否啟用 AI，都可以直接建立一份完整手寫草稿。",
                        )
                        if st.button(
                            "儲存手寫草稿",
                            key="story_manual_create_draft",
                            disabled=bool(active),
                        ):
                            try:
                                services.story_drafts.add_manual_draft(
                                    scene_id=scene_id,
                                    prose_text=manual_prose,
                                    scene_card_version_id=str(effective or ""),
                                )
                                st.session_state["story_pending"] = {"story_manual_prose": ""}
                                st.success("已建立手寫草稿。")
                                st.rerun()
                            except ApplicationError as exc:
                                present_exception(
                                    exc,
                                    operation_zh_tw="執行 Story Studio 操作",
                                    key_prefix="story_studio_error",
                                )

                    if not ai_runtime.config or not ai_runtime.config.uses_llm:
                        st.caption("純手寫模式下，AI 生成與修訂按鈕會停用；上方手寫草稿不受影響。")

                    if generation_purpose is StoryGenerationPurpose.SCENE and st.button(
                        "生成場景",
                        key="story_generate",
                        disabled=(
                            bool(active)
                            or not ai_runtime.can_send
                            or not length_bounds_valid
                            or not ollama_context_ready
                            or (use_preview and (not preview_ready or not preview_acknowledged))
                        ),
                    ):
                        try:
                            user_instruction = _compose_continuation_instruction(
                                keywords=keywords,
                                goal=continuation_goal,
                                additional_instruction=instruction,
                                source_excerpt=source_excerpt,
                                must_include=continuation_must_include,
                                must_avoid=continuation_must_avoid,
                                min_chars=continuation_min_chars,
                                max_chars=continuation_max_chars,
                                pacing=pacing,
                            )
                            # returns immediately; the model call runs
                            # on a worker thread so the page stays usable and
                            # cancel is a real control rather than decoration.
                            state = _start_generation_job(
                                ai_runtime,
                                GenerateSceneRequest(
                                    scene_id=scene_id,
                                    model=ai_runtime.model,
                                    scene_card_version_id=(
                                        preview_version_ids["scene_card_version_id"]
                                        if use_preview
                                        else None
                                    ),
                                    requirement_version_id=(
                                        preview_version_ids["requirement_version_id"]
                                        if use_preview
                                        else None
                                    ),
                                    bible_version_id=(
                                        preview_version_ids["bible_version_id"]
                                        if use_preview
                                        else None
                                    ),
                                    outline_version_id=(
                                        preview_version_ids["outline_version_id"]
                                        if use_preview
                                        else None
                                    ),
                                    chapter_plan_version_id=(
                                        preview_version_ids["chapter_plan_version_id"]
                                        if use_preview
                                        else None
                                    ),
                                    user_instruction=user_instruction,
                                    structured_must_avoid=_lines(continuation_must_avoid),
                                    context_budget=context_budget,
                                    max_output_tokens=_length_output_token_budget(
                                        continuation_min_chars,
                                        continuation_max_chars,
                                    ),
                                    num_ctx=(
                                        ollama_context.num_ctx
                                        if ollama_context is not None
                                        else None
                                    ),
                                    length_spec=active_length_spec,
                                    stream=use_stream,
                                    planning_mode=(
                                        PlanningMode.PREVIEW
                                        if use_preview
                                        else PlanningMode.ACCEPTED
                                    ),
                                    preview_warning_acknowledged=preview_acknowledged,
                                ),
                            )
                            # The previous stream surface remains authoritative
                            # until the manager accepts this exact job.
                            _reset_stream_state()
                            st.session_state["story_active_job_id"] = state.job_id
                            st.session_state["story_active_job_scene_id"] = scene_id
                            st.session_state["story_active_job_kind"] = state.kind.value
                            st.session_state["story_stream_status"] = state.status.value
                            _remember_active_length_bounds(
                                (
                                    active_length_spec.minimum
                                    if active_length_spec is not None
                                    else continuation_min_chars
                                ),
                                (
                                    active_length_spec.maximum
                                    if active_length_spec is not None
                                    else continuation_max_chars
                                ),
                            )
                            _remember_active_runtime(ai_runtime)
                            # The status panel is rendered ABOVE this button,
                            # so without a rerun the author would not see the
                            # job as running until some later interaction.
                            st.rerun()
                        except ProviderError as exc:
                            present_exception(
                                exc,
                                operation_zh_tw="執行故事模型操作",
                                key_prefix="story_studio_provider",
                                data_safety=_job_start_failure_safety(),
                                provider_label=(
                                    "OpenAI"
                                    if ai_runtime.mode is GenerationMode.OPENAI
                                    else "Ollama"
                                ),
                            )
                        except (ApplicationError, ValueError) as exc:
                            present_exception(
                                exc,
                                operation_zh_tw="執行 Story Studio 操作",
                                key_prefix="story_studio_error",
                                data_safety=_job_start_failure_safety(),
                            )

                    if generation_purpose is StoryGenerationPurpose.COMPLETE_SHORT_STORY:
                        complete_left, complete_right = st.columns(2)
                        generate_current_complete = complete_left.button(
                            "依照目前設定生成完整短篇候選",
                            key="story_generate_complete",
                            disabled=(
                                bool(active)
                                or not ai_runtime.can_send
                                or not ollama_context_ready
                                or complete_story_brief is None
                                or (use_preview and (not preview_ready or not preview_acknowledged))
                            ),
                            help=(
                                "可以完全留空素材並只依正式規劃鏈創作；"
                                "也可以先使用離線隨機靈感再自行改寫。"
                            ),
                        )
                        generate_random_complete = complete_right.button(
                            "一鍵隨機生成完整故事候選",
                            key="story_generate_complete_random",
                            disabled=(
                                bool(active)
                                or not ai_runtime.can_send
                                or not ollama_context_ready
                                or (use_preview and (not preview_ready or not preview_acknowledged))
                            ),
                            help=(
                                "先在本機建立一組新的隨機創作設定，再以完全相同的設定"
                                "啟動背景生成；候選不會自動接受。"
                            ),
                        )
                        if ai_runtime.mode is GenerationMode.OPENAI:
                            st.caption(
                                "全域 route 為 OpenAI 時，一鍵隨機會先在本機建立一組"
                                "新設定並立即送出；遠端啟用狀態由 AI 設定統一管理。"
                            )
                        if generate_current_complete or generate_random_complete:
                            try:
                                selected_brief = (
                                    generate_random_complete_story_brief()
                                    if generate_random_complete
                                    else complete_story_brief
                                )
                                if selected_brief is None:
                                    raise ValueError("完整故事素材尚未通過驗證。")
                                selected_length_spec = _story_visible_char_length_spec(
                                    selected_brief.min_visible_chars,
                                    selected_brief.max_visible_chars,
                                    target_hint=selected_brief.max_visible_chars,
                                    max_continuations=3,
                                )
                                state = _start_generation_job(
                                    ai_runtime,
                                    GenerateSceneRequest(
                                        scene_id=scene_id,
                                        model=ai_runtime.model,
                                        generation_purpose=(
                                            StoryGenerationPurpose.COMPLETE_SHORT_STORY
                                        ),
                                        complete_story_brief=selected_brief,
                                        scene_card_version_id=(
                                            preview_version_ids["scene_card_version_id"]
                                            if use_preview
                                            else None
                                        ),
                                        requirement_version_id=(
                                            preview_version_ids["requirement_version_id"]
                                            if use_preview
                                            else None
                                        ),
                                        bible_version_id=(
                                            preview_version_ids["bible_version_id"]
                                            if use_preview
                                            else None
                                        ),
                                        outline_version_id=(
                                            preview_version_ids["outline_version_id"]
                                            if use_preview
                                            else None
                                        ),
                                        chapter_plan_version_id=(
                                            preview_version_ids["chapter_plan_version_id"]
                                            if use_preview
                                            else None
                                        ),
                                        context_budget=context_budget,
                                        max_output_tokens=_length_output_token_budget(
                                            selected_brief.min_visible_chars,
                                            selected_brief.max_visible_chars,
                                        ),
                                        num_ctx=(
                                            ollama_context.num_ctx
                                            if ollama_context is not None
                                            else None
                                        ),
                                        length_spec=selected_length_spec,
                                        stream=use_stream,
                                        planning_mode=(
                                            PlanningMode.PREVIEW
                                            if use_preview
                                            else PlanningMode.ACCEPTED
                                        ),
                                        preview_warning_acknowledged=preview_acknowledged,
                                    ),
                                )
                                _reset_stream_state()
                                if generate_random_complete:
                                    # Show the exact locally generated brief that
                                    # the worker already captured, never a reroll.
                                    _queue_complete_story_brief(selected_brief)
                                st.session_state["story_active_job_id"] = state.job_id
                                st.session_state["story_active_job_scene_id"] = scene_id
                                st.session_state["story_active_job_kind"] = state.kind.value
                                st.session_state["story_stream_status"] = state.status.value
                                _remember_active_length_bounds(
                                    selected_length_spec.minimum,
                                    selected_length_spec.maximum,
                                )
                                _remember_active_runtime(ai_runtime)
                                st.rerun()
                            except ProviderError as exc:
                                present_exception(
                                    exc,
                                    operation_zh_tw="生成故事候選",
                                    key_prefix="story_studio_generation_provider",
                                    data_safety=_job_start_failure_safety(),
                                    provider_label=(
                                        "OpenAI"
                                        if ai_runtime.mode is GenerationMode.OPENAI
                                        else "Ollama"
                                    ),
                                )
                            except (ApplicationError, ValueError) as exc:
                                present_exception(
                                    exc,
                                    operation_zh_tw="執行 Story Studio 操作",
                                    key_prefix="story_studio_error",
                                    data_safety=_job_start_failure_safety(),
                                )

                    # cancellation is a real control, not decoration.
                    # It requests cancellation; only the worker's terminal
                    # event may report that the run actually stopped.
                    if st.button("取消目前生成", key="story_cancel", disabled=not active):
                        try:
                            state = _job_manager().cancel(active)
                            st.session_state["story_stream_status"] = state.status.value
                            st.warning(
                                "取消要求已送出，等待 worker 結束；已收到的內容會保留為 partial。"
                            )
                        except ApplicationError as exc:
                            present_exception(
                                exc,
                                operation_zh_tw="執行 Story Studio 操作",
                                key_prefix="story_studio_error",
                            )


                    drafts = services.story_drafts.list_drafts(scene_id)
                    if drafts:
                        labels = {
                            d.id: (
                                f"#{d.draft_number}（{d.origin}"
                                f"{'・partial' if d.draft_status == 'partial' else ''}"
                                f"{'・目前已接受' if d.id == scene.accepted_draft_id else ''}"
                                f"）{d.word_count} 字詞"
                            )
                            for d in drafts
                        }
                        loaded_draft_id = st.session_state.get(_STORY_PROSE_LOADED_DRAFT_KEY)
                        if "story_draft_id" not in st.session_state and loaded_draft_id in labels:
                            st.session_state["story_draft_id"] = loaded_draft_id
                        chosen = st.selectbox(
                            "草稿",
                            options=list(labels),
                            format_func=lambda x: labels[x],
                            key="story_draft_id",
                        )
                        draft = next(d for d in drafts if d.id == chosen)
                        if st.session_state.get(_STORY_PROSE_LOADED_DRAFT_KEY) != draft.id:
                            st.session_state["story_prose_view"] = draft.prose_text
                            st.session_state[_STORY_PROSE_LOADED_DRAFT_KEY] = draft.id
                        elif "story_prose_view" not in st.session_state:
                            snapshot = st.session_state.get(_STORY_PROSE_EDITOR_SNAPSHOT_KEY)
                            st.session_state["story_prose_view"] = (
                                snapshot[1]
                                if isinstance(snapshot, tuple)
                                and len(snapshot) == 2
                                and snapshot[0] == draft.id
                                and isinstance(snapshot[1], str)
                                else draft.prose_text
                            )
                        st.text_area(
                            "正文",
                            height=300,
                            key="story_prose_view",
                            disabled=draft.was_accepted,
                            on_change=_remember_story_prose_editor,
                        )
                        _remember_story_prose_editor()
                        st.caption(
                            "草稿選單的「字詞」計數不含標點；下方長度提醒則以不含空白的字元計算。"
                        )
                        if draft.draft_status == "partial":
                            st.warning(
                                "這是未完成的 partial 草稿，不可接受；可作為重新生成的參考。"
                            )
                        replacing_accepted_memory = False
                        current_accepted_draft_id = scene.accepted_draft_id
                        if (
                            current_accepted_draft_id is not None
                            and current_accepted_draft_id != draft.id
                        ):
                            current_memory_proposals = (
                                services.story_memory.list_proposals_for_draft(
                                    current_accepted_draft_id
                                )
                            )
                            replacing_accepted_memory = any(
                                proposal.status is StoryMemoryProposalStatus.ACCEPTED
                                for proposal in current_memory_proposals
                            )
                        memory_replacement_acknowledged = False
                        if replacing_accepted_memory:
                            replacement_scope = f"{scene_id}:{current_accepted_draft_id}:{draft.id}"
                            if (
                                st.session_state.get(_STORY_ACCEPT_DRAFT_MEMORY_ACK_SCOPE_KEY)
                                != replacement_scope
                            ):
                                st.session_state.pop(_STORY_ACCEPT_DRAFT_MEMORY_ACK_KEY, None)
                                st.session_state[_STORY_ACCEPT_DRAFT_MEMORY_ACK_SCOPE_KEY] = (
                                    replacement_scope
                                )
                            st.warning(
                                "接受這份草稿會切換本場景的正式版本；舊草稿已接受的故事"
                                "記憶提案將不再進入後續場景投影。請在新草稿重新建立並"
                                "接受一份完整記憶提案。"
                            )
                            memory_replacement_acknowledged = st.checkbox(
                                "我了解切換後舊草稿的故事記憶提案不再供後續場景使用。",
                                key=_STORY_ACCEPT_DRAFT_MEMORY_ACK_KEY,
                            )
                        else:
                            st.session_state.pop(_STORY_ACCEPT_DRAFT_MEMORY_ACK_SCOPE_KEY, None)
                            st.session_state.pop(_STORY_ACCEPT_DRAFT_MEMORY_ACK_KEY, None)
                        a1, a2 = st.columns(2)
                        # fail closed at the UI too. The service and a
                        # DB CHECK still reject partials — this only stops the
                        # author from reaching a control that can never work.
                        if a1.button(
                            "接受此草稿",
                            key="story_accept_draft",
                            disabled=(
                                draft.draft_status == "partial"
                                or (
                                    replacing_accepted_memory
                                    and not memory_replacement_acknowledged
                                )
                            ),
                        ):
                            try:
                                services.story_drafts.accept_draft(draft.id)
                                st.success("已接受；此草稿現為本場景的正式版本。")
                                st.rerun()
                            except ApplicationError as exc:
                                present_exception(
                                    exc,
                                    operation_zh_tw="執行 Story Studio 操作",
                                    key_prefix="story_studio_error",
                                )
                        if a2.button("存為手動草稿", key="story_manual_draft"):
                            try:
                                services.story_drafts.add_manual_draft(
                                    scene_id=scene_id,
                                    prose_text=st.session_state["story_prose_view"],
                                    scene_card_version_id=draft.scene_card_version_id,
                                    parent_draft_id=draft.id,
                                )
                                st.rerun()
                            except ApplicationError as exc:
                                present_exception(
                                    exc,
                                    operation_zh_tw="執行 Story Studio 操作",
                                    key_prefix="story_studio_error",
                                )

                        st.divider()
                        st.caption("修訂（每次修訂都會產生新版本）")
                        operation = st.selectbox(
                            "修訂操作",
                            list(RevisionOperation),
                            format_func=lambda o: o.value,
                            key="story_rev_op",
                        )
                        rev_instruction = st.text_input("補充指示", key="story_rev_instruction")
                        st.session_state.setdefault("story_rev_events", True)
                        st.session_state.setdefault("story_rev_order", True)
                        st.session_state.setdefault("story_rev_dialogue", False)
                        r1, r2, r3 = st.columns(3)
                        preserve_events = r1.checkbox("保留事件", key="story_rev_events")
                        preserve_order = r2.checkbox("保留順序", key="story_rev_order")
                        preserve_dialogue = r3.checkbox("保留對白", key="story_rev_dialogue")
                        if st.button(
                            "套用修訂",
                            key="story_revise",
                            disabled=(
                                bool(active)
                                or not ai_runtime.can_send
                                or not length_bounds_valid
                                or not ollama_context_ready
                            ),
                        ):
                            try:
                                revision_direction = _compose_continuation_instruction(
                                    keywords=keywords,
                                    goal=continuation_goal,
                                    additional_instruction=instruction,
                                    source_excerpt=source_excerpt,
                                    must_include=continuation_must_include,
                                    must_avoid=continuation_must_avoid,
                                    min_chars=continuation_min_chars,
                                    max_chars=continuation_max_chars,
                                    pacing=pacing,
                                )
                                state = _start_revision_job(
                                    ai_runtime,
                                    ReviseSceneRequest(
                                        draft_id=draft.id,
                                        model=ai_runtime.model,
                                        revision=RevisionRequest(
                                            operation=operation,
                                            instruction=rev_instruction,
                                            preserve_events=preserve_events,
                                            preserve_order=preserve_order,
                                            preserve_dialogue=preserve_dialogue,
                                        ),
                                        user_instruction=revision_direction,
                                        structured_must_avoid=_lines(continuation_must_avoid),
                                        context_budget=context_budget,
                                        max_output_tokens=_length_output_token_budget(
                                            continuation_min_chars,
                                            continuation_max_chars,
                                        ),
                                        num_ctx=(
                                            ollama_context.num_ctx
                                            if ollama_context is not None
                                            else None
                                        ),
                                        length_spec=active_length_spec,
                                        # Revision must stream when generation
                                        # does; otherwise the author gets no
                                        # incremental output and no meaningful
                                        # mid-flight cancel on this path.
                                        stream=use_stream,
                                    ),
                                    scene_id=scene_id,
                                )
                                _reset_stream_state()
                                st.session_state["story_active_job_id"] = state.job_id
                                st.session_state["story_active_job_scene_id"] = scene_id
                                st.session_state["story_active_job_kind"] = state.kind.value
                                st.session_state["story_stream_status"] = state.status.value
                                _remember_active_length_bounds(
                                    (
                                        active_length_spec.minimum
                                        if active_length_spec is not None
                                        else continuation_min_chars
                                    ),
                                    (
                                        active_length_spec.maximum
                                        if active_length_spec is not None
                                        else continuation_max_chars
                                    ),
                                )
                                _remember_active_runtime(ai_runtime)
                                st.rerun()
                            except ProviderError as exc:
                                present_exception(
                                    exc,
                                    operation_zh_tw="修改故事候選",
                                    key_prefix="story_studio_revision_provider",
                                    data_safety=_job_start_failure_safety(),
                                    provider_label=(
                                        "OpenAI"
                                        if ai_runtime.mode is GenerationMode.OPENAI
                                        else "Ollama"
                                    ),
                                )
                            except (ApplicationError, ValueError) as exc:
                                present_exception(
                                    exc,
                                    operation_zh_tw="執行 Story Studio 操作",
                                    key_prefix="story_studio_error",
                                    data_safety=_job_start_failure_safety(),
                                )

                    runs = services.story_drafts.list_runs(scene_id)
                    if runs:
                        with st.expander(f"生成紀錄（{len(runs)} 筆）"):
                            for run in runs:
                                st.caption(
                                    f"{run.started_at[:19]}｜"
                                    f"{_generation_run_status_label(run.status)}"
                                    f"｜{run.model}｜{run.latency_ms} ms"
                                    f"｜輸入檢查碼 `{run.input_snapshot_sha256[:12]}…`"
                                )

                    st.session_state.setdefault("story_summary", scene.summary_text)
                    summary = st.text_area(
                        "場景摘要（可編輯，供後續場景參考）",
                        key="story_summary",
                        help=(
                            "這裡可先寫工作筆記；只有場景草稿被接受後，摘要才成為正式前情。"
                            "同一故事大綱的後續場景可跨章取用最近一筆正式前情。"
                        ),
                    )
                    st.caption(
                        "未接受草稿的摘要只是不會進入模型上下文的工作筆記；"
                        "接受草稿後，同一故事大綱的後續場景可跨章延續這筆摘要。"
                    )
                    if st.button("儲存摘要", key="story_save_summary"):
                        services.scene_cards.update_summary(scene_id, summary)
                        if scene.accepted_draft_id:
                            st.success("已更新正式前情；同一故事大綱的後續場景可跨章取用。")
                        else:
                            st.info(
                                "已保存為工作筆記；接受此場景草稿後，才會成為可跨章延續的正式前情。"
                            )

        # ---- independent screenplay adaptation -------------------------
        if active_section is StoryStudioSection.ADAPTATION:
            _render_screenplay_adaptation_section(
                services,
                scene_id=scene_id,
                ai_runtime=ai_runtime,
                active_job=active_job,
            )

        # ---- historical context inspection ----------------
        if active_section is StoryStudioSection.CONTEXT:
            if not scene_id:
                st.info("請先於左側選擇場景。")
            else:
                _render_context_inspector(services, scene_id, project_id)

        # ---- export --------------------------------------------------
        if active_section is StoryStudioSection.EXPORT:
            mode = st.selectbox(
                "匯出模式",
                list(StoryExportMode),
                format_func=lambda m: m.value,
                key="story_export_mode",
            )
            st.caption("正式匯出只使用**已接受**的規劃版本與每個場景**目前已接受**的草稿。")
            st.session_state.setdefault("story_export_preview", False)
            export_preview = st.checkbox(
                "包含尚未接受的規劃版本（試寫匯出）",
                key="story_export_preview",
            )
            if export_preview:
                st.warning("試寫匯出含尚未接受的規劃版本，文件內會標註，不代表已定稿內容。")
            if st.button("產生匯出", key="story_do_export"):
                try:
                    # follow the ACCEPTED pointer. Taking the last item
                    # of the version list meant a newly-saved draft silently
                    # replaced the accepted version in the exported document.
                    bible_entity = services.story_bibles.get(bible_id) if bible_id else None
                    req_entity = services.story_requirements.get(req_id) if req_id else None
                    markdown, as_json = services.story_exports.build(
                        project_id=project_id,
                        mode=mode,
                        outline_id=outline_id,
                        bible_version_id=(
                            (bible_entity.accepted_version_id or "") if bible_entity else ""
                        ),
                        requirement_version_id=(
                            (req_entity.accepted_version_id or "") if req_entity else ""
                        ),
                        planning_mode=(
                            PlanningMode.PREVIEW if export_preview else PlanningMode.ACCEPTED
                        ),
                    )
                    st.session_state["story_export_md"] = markdown
                    st.session_state["story_export_json"] = as_json
                    st.success("已產生匯出內容。")
                except ApplicationError as exc:
                    present_exception(
                        exc,
                        operation_zh_tw="執行 Story Studio 操作",
                        key_prefix="story_studio_error",
                    )
            if "story_export_md" in st.session_state:
                e1, e2 = st.columns(2)
                e1.download_button(
                    "下載 Markdown",
                    st.session_state["story_export_md"],
                    file_name="story_export.md",
                    key="story_dl_md",
                )
                e2.download_button(
                    "下載 JSON",
                    st.session_state["story_export_json"],
                    file_name="story_export.json",
                    key="story_dl_json",
                )
                st.code(st.session_state["story_export_md"][:2000], language="markdown")

            st.divider()
            st.subheader("重建歷史匯出")
            exports = services.story_exports.list_exports(project_id)
            if not exports:
                st.caption("這個專案還沒有可重建的歷史匯出。")
            else:
                export_by_id = {str(item["id"]): item for item in exports}
                export_id = st.selectbox(
                    "歷史匯出",
                    list(export_by_id),
                    format_func=lambda item_id: (
                        f"{str(export_by_id[item_id]['created_at'])[:19]}｜"
                        f"{export_by_id[item_id]['export_mode']}｜"
                        f"{item_id}"
                    ),
                    key="story_rebuild_export_id",
                )
                if st.button("從保存的匯出快照重建", key="story_rebuild_export"):
                    try:
                        rebuild_result = services.story_exports.rebuild_from_export(
                            export_id,
                            expected_project_id=project_id,
                        )
                        st.session_state["story_rebuilt_export_md"] = rebuild_result.markdown
                        st.session_state["story_rebuilt_export_json"] = rebuild_result.json_text
                        st.session_state["story_rebuilt_export_sha256"] = (
                            rebuild_result.snapshot_sha256
                        )
                        st.session_state["story_rebuilt_export_status"] = (
                            rebuild_result.reproduction_status.value
                        )
                        st.success("歷史匯出已依保存的原始快照完成重建。")
                    except ApplicationError as exc:
                        present_exception(
                            exc,
                            operation_zh_tw="執行 Story Studio 操作",
                            key_prefix="story_studio_error",
                        )
                if (
                    st.session_state.get("story_rebuilt_export_status")
                    == StoryExportReproductionStatus.REPRODUCED.value
                    and "story_rebuilt_export_md" in st.session_state
                ):
                    st.caption(
                        "重建完成｜快照 SHA-256：`"
                        f"{st.session_state['story_rebuilt_export_sha256']}`"
                    )
                    r1, r2 = st.columns(2)
                    r1.download_button(
                        "下載重建 Markdown",
                        st.session_state["story_rebuilt_export_md"],
                        file_name="story_export_rebuilt.md",
                        key="story_rebuilt_dl_md",
                    )
                    r2.download_button(
                        "下載重建 JSON",
                        st.session_state["story_rebuilt_export_json"],
                        file_name="story_export_rebuilt.json",
                        key="story_rebuilt_dl_json",
                    )

        # ---- author-reviewed story memory ---------------------------
        if active_section is StoryStudioSection.MEMORY:
            _render_story_memory_tab(services, scene_id)

    # ============================================================= RIGHT
    with right:
        st.subheader("作品設定與參與角色")
        mode_now: ContentMode = st.session_state.get("story_card_mode", ContentMode.GENERAL)
        st.caption(f"目前場景內容模式：{mode_now.value}")

        selected: list[str] = st.session_state.get("story_participants", [])
        if selected:
            st.caption("參與角色版本：")
            # entries are Character Version IDs; eligibility below is
            # evaluated on the exact selected version ID, never via a label.
            for vid in selected:
                st.caption(f"- {version_labels.get(vid, vid)}")
        else:
            st.caption("尚未選擇參與角色。")

        st.divider()
        st.caption("角色版本由你選定；後續生成會沿用目前作品設定。")


MODE_LABELS: dict[HistoricalContextDisplayMode, str] = {
    # these strings are the honesty contract. Mode B must not
    # read as "the original", and Mode C must not read as history at all.
    HistoricalContextDisplayMode.STORED_EXACT: "當時實際送出的訊息（已儲存）",
    HistoricalContextDisplayMode.RECONSTRUCTED_MATCH: (
        "由保存的輸入快照重建；SHA-256 與原始訊息相符"
    ),
    HistoricalContextDisplayMode.RECONSTRUCTION_MISMATCH: (
        "無法以目前版本的內容整理方式逐位元重現；原始 SHA-256 已保留，但當時的原文未儲存"
    ),
    HistoricalContextDisplayMode.STORED_INTEGRITY_ERROR: (
        "已儲存的原文未通過完整性驗證，不能視為當時的原文"
    ),
}


def _run_option_label(run: Any) -> str:
    """presentation only. The option VALUE is the run ID, so two
    identical labels still address different runs."""
    return (
        f"{run.started_at[:19]}｜{run.run_kind}｜{run.status}"
        f"｜{run.model or '（未記錄模型）'}｜{run.planning_mode}"
        f"｜紀錄 {_short(run.id)}"
    )


def _render_id_table(title: str, rows: list[tuple[str, str]]) -> None:
    """Full IDs, never only the short form ."""
    with st.expander(title):
        for label, value in rows:
            st.caption(label)
            st.code(value or "（無）")


def _render_context_inspector(services: Any, scene_id: str, project_id: str) -> None:
    """inspect ONE historical run of this scene.

    The previous tab rebuilt a context from the CURRENT scene and the CURRENT
    instruction box and showed it as though it were history. Everything here
    comes from the selected run through
    ``StoryContextService.inspect_generation_run``; the UI performs no
    database or reconstruction logic of its own.
    """
    runs = services.story_drafts.list_runs(scene_id)
    if not runs:
        st.info("本場景還沒有任何生成或修訂紀錄。")
        return

    labels = {run.id: _run_option_label(run) for run in runs}
    run_id = st.selectbox(
        "選擇要檢視的生成紀錄",
        options=list(labels),
        format_func=lambda rid: labels[rid],
        key="story_inspect_run_id",
    )
    try:
        inspection = services.story_context.inspect_generation_run(
            run_id, expected_project_id=project_id
        )
    except ApplicationError as exc:
        present_exception(
            exc,
            operation_zh_tw="執行 Story Studio 操作",
            key_prefix="story_studio_error",
        )
        return

    mode = inspection.display_mode
    if mode is HistoricalContextDisplayMode.STORED_EXACT:
        st.success(MODE_LABELS[mode])
    elif mode is HistoricalContextDisplayMode.RECONSTRUCTED_MATCH:
        st.info(MODE_LABELS[mode])
        st.caption("以下文字由保存的輸入快照重建，不是資料庫保存的原始訊息；兩者的 SHA-256 相符。")
    elif mode is HistoricalContextDisplayMode.STORED_INTEGRITY_ERROR:
        st.error(MODE_LABELS[mode])
    else:
        st.warning(MODE_LABELS[mode])

    for warning in inspection.warnings:
        st.warning(warning)

    # ---- messages -----------------------------------------------------
    if mode in (
        HistoricalContextDisplayMode.STORED_EXACT,
        HistoricalContextDisplayMode.RECONSTRUCTED_MATCH,
    ):
        with st.expander("系統訊息（system）"):
            st.text(inspection.system_message)
        with st.expander("使用者訊息（user）"):
            st.text(inspection.user_message)
    elif inspection.diagnostic_system_message or inspection.diagnostic_user_message:
        # a candidate or a corrupt stored text may only appear
        # under a label that denies it is the historical original.
        #
        # S7.3.1: the two diagnostics have DIFFERENT origins and must not
        # share a heading. Mode C's text really is produced by today's
        # renderer; the integrity-error text comes straight from the stored
        # message column and was never re-rendered — calling it a
        # reconstruction misstated where it came from.
        if mode is HistoricalContextDisplayMode.STORED_INTEGRITY_ERROR:
            with st.expander("資料庫中未通過完整性驗證的原始文字（僅供診斷）"):
                st.caption(
                    "這段文字來自資料庫保存的原始訊息，"
                    "但內容缺失或未通過 SHA-256／位元組大小驗證。"
                    "它不是目前版本重建的結果，"
                    "也不得視為已驗證的歷史原文。"
                )
                st.text(inspection.diagnostic_system_message)
                st.text(inspection.diagnostic_user_message)
        else:
            with st.expander("目前版本的重建結果（不等同歷史原文）"):
                st.caption(
                    "這段文字由目前版本的內容整理方式重新產生，僅供診斷。"
                    "它未通過與原始 SHA-256 的比對，"
                    "因此不是當時送出的訊息。"
                )
                st.text(inspection.diagnostic_system_message)
                st.text(inspection.diagnostic_user_message)

    # ---- metadata ---------------------------------------------
    left, right = st.columns(2)
    left.caption(f"紀錄類型：{inspection.run_kind}")
    left.caption(f"狀態：{inspection.status}")
    left.caption(f"規劃模式：{inspection.planning_mode}")
    left.caption(f"內容模式：{inspection.content_mode}")
    left.caption(f"生成服務／模型：{inspection.provider}／{inspection.model}")
    left.caption(f"開始：{inspection.started_at[:19]}")
    left.caption(f"結束：{inspection.completed_at[:19] or '（未記錄）'}")
    right.caption(f"內容整理版本：{inspection.renderer_version}")
    right.caption(f"內容結構版本：{inspection.context_schema_version}")
    right.caption(f"內容契約版本：{inspection.context_contract_version}")
    right.caption(f"內容長度政策：{inspection.context_budget_policy_version}")
    right.caption(
        "原始訊息儲存：" + ("開啟" if inspection.rendered_message_storage_enabled else "關閉")
    )
    right.caption(
        "已儲存訊息雜湊驗證："
        + ("通過" if inspection.stored_message_hashes_verified else "未通過／不適用")
    )
    right.caption(
        "輸入快照驗證：" + ("通過" if inspection.input_snapshot_sha256_verified else "未通過")
    )

    _render_id_table(
        "使用的規劃版本 ID（完整）",
        [
            (reference.entity_type, reference.entity_id)
            for reference in inspection.version_references
        ],
    )
    _render_id_table(
        "參與角色版本 ID（完整）",
        [
            ("POV", inspection.pov_character_version_id),
            *(
                (f"參與者 {index + 1}", version_id)
                for index, version_id in enumerate(inspection.character_version_ids)
            ),
        ],
    )
    _render_id_table(
        "指紋與雜湊（完整）",
        [
            ("生成紀錄 ID", inspection.run_id),
            ("內容設定指紋", inspection.context_fingerprint),
            ("規劃鏈指紋", inspection.planning_chain_fingerprint),
            ("資格判定指紋", inspection.eligibility_fingerprint),
            ("system 訊息 SHA-256", inspection.system_message_sha256),
            ("user 訊息 SHA-256", inspection.user_message_sha256),
            (
                "訊息位元組大小",
                f"system {inspection.system_message_byte_size}"
                f"／user {inspection.user_message_byte_size}",
            ),
        ],
    )
    # ---- typed context source refs ----------------------
    with st.expander("使用的內容來源（完整 ID）"):
        if inspection.context_source_refs_verified:
            st.caption("以下來源取自本次紀錄中已通過完整性驗證的輸入快照。")
        else:
            st.warning("本次紀錄的輸入快照未通過驗證，因此下列來源不得視為「當時實際使用的來源」。")
        if not inspection.context_source_refs:
            st.caption("（此紀錄的快照未記錄內容來源）")
        for ref in inspection.context_source_refs:
            st.caption(f"{ref.entity_type.value}" + ("｜version" if ref.version_id else ""))
            st.code(
                ref.entity_id if ref.version_id is None else f"{ref.entity_id}\n{ref.version_id}"
            )

    with st.expander("生成設定快照"):
        st.caption(
            "快照 SHA-256："
            f"{inspection.generation_options_snapshot_sha256 or '（格式無效或未記錄）'}"
        )
        st.caption(f"快照大小：{inspection.generation_options_snapshot_byte_size} UTF-8 bytes")
        snapshot = inspection.generation_options_snapshot
        if inspection.generation_options_snapshot_verified and snapshot is not None:
            st.success("快照狀態：已通過完整性驗證")
            for label, key in (
                ("temperature", "temperature"),
                ("timeout（秒）", "timeout_s"),
                ("串流", "stream"),
                ("seed", "seed"),
                ("最大輸出 tokens", "max_output_tokens"),
                ("top_p", "top_p"),
                ("停止序列", "stop_sequence_count"),
                ("結構化模式", "structured_mode"),
            ):
                value = snapshot.get(key)
                if isinstance(value, bool):
                    readable = "是" if value else "否"
                elif value is None:
                    readable = "未指定"
                elif key == "stop_sequence_count":
                    readable = f"{value} 項"
                else:
                    readable = str(value)
                st.caption(f"{label}：{readable}")
            st.caption(
                f"供應者進階選項：{snapshot.get('provider_option_count', 0)} 項（名稱與內容不顯示）"
            )
            st.caption("下一步：需要核對完整設定時，請在受控的本機備份中進行。")
        else:
            st.warning("快照狀態：未通過完整性驗證；原始內容不顯示。")
            st.caption("下一步：保留此紀錄，檢查本機備份完整性，或以目前設定建立新紀錄。")

    # ---- privacy ----------------------------------------------
    with st.expander("關於原始訊息與隱私"):
        if services.settings.store_rendered_generation_messages:
            st.info(
                "目前設定：開啟。之後的生成紀錄會在本機資料庫保存模型服務可見的"
                "系統與使用者訊息原文。"
            )
        else:
            st.info(
                "目前設定：關閉。之後的生成紀錄不保存模型服務可見的系統與使用者訊息原文，"
                "但仍保存 SHA-256 與 UTF-8 位元組大小。"
            )
        st.caption(
            "模型服務可見的原始訊息可能包含完整的私人故事內容與提示文字。"
            "本機資料庫備份可能一併包含這些內容；本應用程式不會自行上傳。"
            "關閉原始訊息儲存只影響之後的生成紀錄，既有紀錄不會被改寫，"
            "因為生成紀錄在完成後即為不可變。"
        )
        st.caption("進階保存、備份、刪除與歷史重建限制，請參閱隨附的隱私說明。")


def _render_version_browser(
    container: Any,
    *,
    title: str,
    versions: list[Any],
    accepted_id: str | None,
    accept: Callable[[str], None],
    load_into_editor: Callable[[str], None],
    key_prefix: str,
) -> None:
    """: browse, load into the editor, accept.

    Saving creates a working version; it becomes the generation default only
    when the author accepts it here. Loading populates the editor through the
    pending-form mechanism, mutates nothing, and creates no database row —
    only the normal save action persists anything.
    """
    if not versions:
        return
    with container.expander(f"{title}（{len(versions)} 個）"):
        labels = {v.id: _version_label(v, accepted_id) for v in versions}
        # normalisation, applied here too: selecting another
        # scene or entity leaves this widget holding a version ID that no
        # longer belongs to the list. Streamlit resets such a value silently,
        # but leaving it in place makes the selection inconsistent with what
        # the expander is actually showing.
        pick_key = f"{key_prefix}_version_pick"
        stale_pick = st.session_state.get(pick_key)
        if stale_pick is not None and stale_pick not in labels:
            del st.session_state[pick_key]
        chosen = container.selectbox(
            "選擇版本",
            options=list(labels),
            format_func=lambda x: labels[x],
            key=f"{key_prefix}_version_pick",
        )
        selected = next(v for v in versions if v.id == chosen)
        container.caption(
            f"建立於 {selected.created_at[:19]}｜{selected.change_note or '（無變更說明）'}"
        )
        # loading is allowed for EVERY version, accepted ones included.
        if container.button("載入此版本到編輯器", key=f"{key_prefix}_load"):
            load_into_editor(selected.id)
        if selected.id == accepted_id:
            container.info("此版本目前是生成預設。")
        elif container.button("接受此版本", key=f"{key_prefix}_accept"):
            try:
                accept(selected.id)
                container.success("已接受；生成預設已更新。")
            except ApplicationError as exc:
                present_exception(
                    exc,
                    operation_zh_tw="載入故事執行資料",
                    key_prefix="story_studio_run_load",
                    surface=container,
                )
