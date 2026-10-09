"""Inspiration Desk — stage a complete creative candidate before refinement.

This page deliberately keeps generated material in Streamlit session state. It
does not write Canon, story, world or prompt rows. A candidate only crosses
that boundary after the author explicitly sends it to the refinement desk.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Final

import streamlit as st
from pydantic import ValidationError

from imaginarium_forge.application.error_incidents import (
    map_and_record_user_facing_error,
)
from imaginarium_forge.application.services.creative_automation_service import (
    AutomationBrief,
    AutomationKind,
    AutomationSection,
    AutomationStrategy,
    CreativeAutomationDraft,
    CreativeAutomationResult,
    CreativeAutomationService,
)
from imaginarium_forge.application.services.creative_inspiration_service import (
    render_english_keywords,
)
from imaginarium_forge.application.services.generation.contracts import GenerationTask
from imaginarium_forge.application.user_facing_errors import (
    ErrorDataSafety,
    ErrorMappingContext,
    UserFacingError,
)
from imaginarium_forge.domain.character.gender import CharacterGender
from imaginarium_forge.domain.creative.models import CreationMode, CreativeLaunchRequest
from imaginarium_forge.domain.prompt.content_mode import ContentMode
from imaginarium_forge.providers.errors import ProviderError
from imaginarium_forge.ui.bootstrap import Services
from imaginarium_forge.ui.endpoint_security import summarize_endpoint
from imaginarium_forge.ui.error_presenter import render_error_card
from imaginarium_forge.ui.generation_actions import (
    generation_action_readiness,
    generation_provider_action,
)
from imaginarium_forge.ui.openai_session import (
    OPENAI_API_KEY_SESSION_KEY,
    apply_openai_session_state_transitions,
)

STATE_PREFIX: Final = "inspiration_desk_"
PROJECT_STATE_KEY: Final = f"{STATE_PREFIX}project_id"
CANDIDATE_STATE_KEY: Final = f"{STATE_PREFIX}candidate"
CANDIDATES_STATE_KEY: Final = f"{STATE_PREFIX}candidates"
CANDIDATE_SELECTION_STATE_KEY: Final = f"{STATE_PREFIX}selected_candidate"
CANDIDATE_SELECTION_PENDING_STATE_KEY: Final = f"{STATE_PREFIX}selected_candidate_pending"
CANDIDATE_HISTORY_STATE_KEY: Final = f"{STATE_PREFIX}candidate_history"
CANDIDATE_GOAL_STATE_KEY: Final = f"{STATE_PREFIX}candidate_goal"
ERROR_STATE_KEY: Final = f"{STATE_PREFIX}error"
NOTICE_STATE_KEY: Final = f"{STATE_PREFIX}notice"
HANDOFF_STATE_KEY: Final = "creative_automation_handoff"
OPENAI_API_KEY_STATE_KEY: Final = OPENAI_API_KEY_SESSION_KEY
MAX_UNDO_STEPS: Final = 10


@dataclass(frozen=True, slots=True)
class _FriendlyChoice:
    """Human-facing label and copy for an internal option value."""

    value: str
    label: str
    help: str


_GOALS: Final = (
    _FriendlyChoice("complete", "整本都幫我想", "一次長出世界、角色與故事方向。"),
    _FriendlyChoice("character", "只做一個角色", "完整人物細節、背景故事與英文提示詞。"),
    _FriendlyChoice("world", "先長出世界", "從規則、社會到重要地點都補齊。"),
    _FriendlyChoice("story", "找到故事主線", "把零星念頭變成可繼續寫的故事方向。"),
)

_SOURCES: Final = (
    _FriendlyChoice("offline", "離線靈感抽卡", "使用內建素材，快速而且完全不連線。"),
    _FriendlyChoice(
        "global_ai",
        "使用全域 AI route",
        "依「AI 設定」的 provider、模型與 fallback 規則產生完整候選。",
    ),
)

_GENDER_LABELS: Final = {
    CharacterGender.FEMALE: "女",
    CharacterGender.MALE: "男",
}

_MERGE_ACTIONS: Final = (
    _FriendlyChoice("fill_blanks", "只補空白", "保留已有內容，只替缺少的部分動筆。"),
    _FriendlyChoice("remix_unlocked", "重混沒鎖的", "鎖住喜歡的頁面，其餘重新發想。"),
    _FriendlyChoice("replace_all", "全部換一版", "放下目前版本，重新抽一份完整候選。"),
)

_GOAL_KIND_AND_MODE: Final = {
    "complete": (AutomationKind.STORY, CreationMode.CHARACTER_STORY),
    "character": (AutomationKind.CHARACTER, CreationMode.CHARACTER_ONLY),
    "world": (AutomationKind.WORLD, CreationMode.WORLD_ONLY),
    "story": (AutomationKind.STORY, CreationMode.SERIES_STORY),
}

_GOAL_SECTIONS: Final = {
    "complete": (
        AutomationSection.WORLD,
        AutomationSection.CHARACTER,
        AutomationSection.STORY,
    ),
    "character": (AutomationSection.CHARACTER,),
    "world": (AutomationSection.WORLD,),
    "story": (AutomationSection.WORLD, AutomationSection.STORY),
}

_CONTENT_LABELS: Final = {
    ContentMode.GENERAL: "一般",
    ContentMode.DRAMA: "劇情",
    ContentMode.DARK: "黑暗",
    ContentMode.HORROR: "恐怖",
    ContentMode.VIOLENT: "暴力",
}

_SECTION_FIELD_PATHS: Final = {
    AutomationSection.WORLD: frozenset(
        {
            "setting",
            "time_period",
            "world_rules",
            "locations",
            "social_context",
            "technology_or_magic",
        }
    ),
    AutomationSection.CHARACTER: frozenset(
        {
            "character.gender",
            "character.biography",
            "character.personality",
            "character.voice",
            "character.motivation",
            "character.fear",
            "character.secret",
            "character.internal_conflict",
            "character.relationship_hooks",
            "character.arc_start",
            "character.arc_turning_points",
            "character.arc_end",
            "character.identity",
            "character.face",
            "character.hair",
            "character.eyes",
            "character.body",
            "character.distinguishing_features",
            "character.prohibited_mutations",
            "character.action",
            "character.expression",
            "english_character_keywords",
            "scene_location",
            "scene_time",
            "scene_weather",
            "scene_atmosphere",
            "scene_lighting",
            "scene_camera",
            "scene_motion",
        }
    ),
    AutomationSection.STORY: frozenset(
        {
            "title",
            "concept",
            "story_logline",
            "story_synopsis",
            "story_opening_hook",
            "primary_genre",
            "secondary_genres",
            "genre_tags",
            "custom_genre",
            "target_length",
            "audience",
            "tone",
            "central_conflict",
            "themes",
            "must_include",
            "must_avoid",
            "pacing",
            "prose_style_notes",
            "dialogue_density",
            "direction",
            "ending_preference",
            "structure_profile",
        }
    ),
}


def _choice_label(value: str, choices: tuple[_FriendlyChoice, ...]) -> str:
    for choice in choices:
        if choice.value == value:
            return choice.label
    return value


def _reset_for_project(project_id: str | None) -> None:
    """Keep staged candidates from leaking across books/projects."""

    if st.session_state.get(PROJECT_STATE_KEY) == project_id:
        return
    for key in list(st.session_state):
        if str(key).startswith(STATE_PREFIX) or key == HANDOFF_STATE_KEY:
            st.session_state.pop(key, None)
    st.session_state[PROJECT_STATE_KEY] = project_id


def _go(page: str) -> None:
    st.session_state["pending_nav"] = page
    st.rerun()


def _locked_sections() -> frozenset[AutomationSection]:
    """Return coarse author locks; fine-grained editing belongs to refinement."""

    return frozenset(
        AutomationSection(section)
        for section in ("world", "character", "story")
        if bool(st.session_state.get(f"{STATE_PREFIX}lock_{section}"))
    )


def _locked_fields(sections: frozenset[AutomationSection]) -> frozenset[str]:
    fields = {field for section in sections for field in _SECTION_FIELD_PATHS[section]}
    if AutomationSection.CHARACTER in sections:
        fields.add("character.name")
    return frozenset(fields)


def _load_candidates() -> tuple[CreativeAutomationResult, ...]:
    """Load the board and migrate the former single-candidate session shape."""

    candidates: list[CreativeAutomationResult] = []
    raw_board = st.session_state.get(CANDIDATES_STATE_KEY)
    if isinstance(raw_board, (list, tuple)):
        try:
            candidates = [CreativeAutomationResult.model_validate(item) for item in raw_board]
        except ValidationError:
            candidates = []
            st.session_state.pop(CANDIDATES_STATE_KEY, None)

    if not candidates:
        raw_candidate = st.session_state.get(CANDIDATE_STATE_KEY)
        if raw_candidate is not None:
            try:
                candidates = [CreativeAutomationResult.model_validate(raw_candidate)]
            except ValidationError:
                st.session_state.pop(CANDIDATE_STATE_KEY, None)

    if not candidates:
        st.session_state.pop(CANDIDATE_SELECTION_STATE_KEY, None)
        st.session_state.pop(CANDIDATE_SELECTION_PENDING_STATE_KEY, None)
        st.session_state.pop(CANDIDATE_GOAL_STATE_KEY, None)
        return ()

    pending = st.session_state.pop(CANDIDATE_SELECTION_PENDING_STATE_KEY, None)
    fingerprints = {candidate.result_fingerprint for candidate in candidates}
    selected_fingerprint = pending or st.session_state.get(CANDIDATE_SELECTION_STATE_KEY)
    if selected_fingerprint not in fingerprints:
        selected_fingerprint = candidates[0].result_fingerprint
    st.session_state[CANDIDATE_SELECTION_STATE_KEY] = selected_fingerprint
    selected = next(
        candidate
        for candidate in candidates
        if candidate.result_fingerprint == selected_fingerprint
    )
    board_payload = [candidate.model_dump(mode="json") for candidate in candidates]
    st.session_state[CANDIDATES_STATE_KEY] = board_payload
    # Backward-compatible mirror used by the refinement handoff and existing
    # sessions/tests that know only the former singular key.
    st.session_state[CANDIDATE_STATE_KEY] = selected.model_dump(mode="json")
    return tuple(candidates)


def _selected_candidate(
    candidates: tuple[CreativeAutomationResult, ...],
) -> CreativeAutomationResult | None:
    selected_fingerprint = st.session_state.get(CANDIDATE_SELECTION_STATE_KEY)
    return next(
        (
            candidate
            for candidate in candidates
            if candidate.result_fingerprint == selected_fingerprint
        ),
        candidates[0] if candidates else None,
    )


def _board_snapshot(
    candidates: tuple[CreativeAutomationResult, ...],
    *,
    selected_fingerprint: str,
    goal: str,
) -> dict[str, object]:
    return {
        "candidates": [candidate.model_dump(mode="json") for candidate in candidates],
        "selected_fingerprint": selected_fingerprint,
        "goal": goal,
    }


def _commit_board(
    candidates: tuple[CreativeAutomationResult, ...],
    *,
    selected_fingerprint: str,
    goal: str,
    previous_candidates: tuple[CreativeAutomationResult, ...],
    previous_selected_fingerprint: str,
    previous_goal: str,
) -> None:
    history = st.session_state.get(CANDIDATE_HISTORY_STATE_KEY, [])
    if not isinstance(history, list):
        history = []
    history.append(
        _board_snapshot(
            previous_candidates,
            selected_fingerprint=previous_selected_fingerprint,
            goal=previous_goal,
        )
    )
    st.session_state[CANDIDATE_HISTORY_STATE_KEY] = history[-MAX_UNDO_STEPS:]
    st.session_state[CANDIDATES_STATE_KEY] = [
        candidate.model_dump(mode="json") for candidate in candidates
    ]
    selected = next(
        candidate
        for candidate in candidates
        if candidate.result_fingerprint == selected_fingerprint
    )
    st.session_state[CANDIDATE_STATE_KEY] = selected.model_dump(mode="json")
    st.session_state[CANDIDATE_SELECTION_PENDING_STATE_KEY] = selected_fingerprint
    st.session_state[CANDIDATE_GOAL_STATE_KEY] = goal


def _undo_board() -> None:
    history = st.session_state.get(CANDIDATE_HISTORY_STATE_KEY, [])
    if not isinstance(history, list) or not history:
        return
    snapshot = history.pop()
    st.session_state[CANDIDATE_HISTORY_STATE_KEY] = history
    if not isinstance(snapshot, dict):
        return
    raw_candidates = snapshot.get("candidates")
    if not isinstance(raw_candidates, list) or not raw_candidates:
        st.session_state.pop(CANDIDATES_STATE_KEY, None)
        st.session_state.pop(CANDIDATE_STATE_KEY, None)
        st.session_state.pop(CANDIDATE_GOAL_STATE_KEY, None)
        st.session_state.pop(CANDIDATE_SELECTION_PENDING_STATE_KEY, None)
        return
    candidates = tuple(CreativeAutomationResult.model_validate(item) for item in raw_candidates)
    selected_fingerprint = str(snapshot.get("selected_fingerprint", ""))
    fingerprints = {candidate.result_fingerprint for candidate in candidates}
    if selected_fingerprint not in fingerprints:
        selected_fingerprint = candidates[0].result_fingerprint
    st.session_state[CANDIDATES_STATE_KEY] = raw_candidates
    selected = next(
        candidate
        for candidate in candidates
        if candidate.result_fingerprint == selected_fingerprint
    )
    st.session_state[CANDIDATE_STATE_KEY] = selected.model_dump(mode="json")
    st.session_state[CANDIDATE_SELECTION_PENDING_STATE_KEY] = selected_fingerprint
    st.session_state[CANDIDATE_GOAL_STATE_KEY] = str(snapshot.get("goal", "complete"))


def _meaningful(value: object) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (tuple, list, set, frozenset, dict)):
        return bool(value)
    return True


def _candidate_survives_error(
    previous: object,
    error: BaseException,
    *,
    operation: str,
    provider_label: str = "AI 服務",
) -> None:
    """Record a provider failure without discarding a useful staged draft."""

    if _meaningful(previous):
        st.session_state[CANDIDATE_STATE_KEY] = previous
    st.session_state[ERROR_STATE_KEY] = map_and_record_user_facing_error(
        error,
        context=ErrorMappingContext(
            operation_zh_tw=operation,
            provider_label=provider_label,
            retry_permitted=isinstance(error, ProviderError) and error.retriable,
            data_safety=ErrorDataSafety(
                message_zh_tw=(
                    "作者輸入與桌上的舊候選完整保留；這次失敗結果沒有套用、保存或覆寫內容。"
                ),
                author_input_preserved=True,
                original_preserved=True,
                completed_work_preserved=True,
                draft_selection_preserved=True,
            ),
        ),
    )


def _generate_candidate_batch(
    service: CreativeAutomationService,
    *,
    project_id: str,
    goal: str,
    clue: str,
    author_title: str,
    author_character_name: str,
    content_mode: ContentMode,
    character_gender: CharacterGender | None,
    author_character_age: int | None,
    author_confirmed_age: bool,
    revision_instruction: str,
    strategy: AutomationStrategy,
    model: str,
    action: str,
    locked_sections: frozenset[AutomationSection],
    previous: CreativeAutomationResult | None,
    previous_goal: str,
) -> tuple[CreativeAutomationResult, ...]:
    """Translate friendly controls into the frozen automation API."""

    kind, mode = _GOAL_KIND_AND_MODE[goal]
    brief = AutomationBrief(
        project_id=project_id,
        kind=kind,
        mode=mode,
        author_title=author_title,
        author_character_name=author_character_name,
        clue=clue,
        content_mode=content_mode,
        character_gender=character_gender,
        author_character_age=author_character_age,
        author_confirmed_age=author_confirmed_age,
    )
    locked_fields = _locked_fields(locked_sections)
    same_goal = previous is not None and previous_goal == goal
    if previous is None or not same_goal or action == "replace_all":
        return service.generate_candidates(
            brief,
            count=3,
            strategy=strategy,
            model=model,
            revision_instruction=revision_instruction,
        )
    previous = service.apply_author_overrides(
        previous,
        author_title=author_title,
        author_character_name=author_character_name,
        clue=clue,
        content_mode=content_mode,
        character_gender=character_gender,
        author_character_age=author_character_age,
        author_confirmed_age=author_confirmed_age,
    )
    if action == "remix_unlocked":
        targets = tuple(
            section for section in _GOAL_SECTIONS[goal] if section not in locked_sections
        )
        if not targets:
            return (previous,)
        return tuple(
            service.remix(
                previous,
                target_fields=targets,
                strategy=strategy,
                model=model,
                revision_instruction=revision_instruction,
                locked_fields=locked_fields,
            )
            for _ in range(3)
        )
    return tuple(
        service.fill_candidate(
            previous,
            kind=kind,
            strategy=strategy,
            model=model,
            revision_instruction=revision_instruction,
            locked_fields=locked_fields,
        )
        for _ in range(3)
    )


def _provider_note(result: CreativeAutomationResult) -> str:
    provenance = result.provenance
    if provenance.fallback:
        return "模型沒有完成這次生成；此版本由離線靈感安全接手。"
    if provenance.strategy_used is AutomationStrategy.PROVIDER:
        return f"模型生成 · {provenance.model}"
    return "離線靈感抽卡 · 不使用網路或模型"


def _provider_failure_message(
    results: tuple[CreativeAutomationResult, ...], *, provider_label: str
) -> str:
    """Explain a normalized provider failure without echoing remote text."""

    reasons = {result.provenance.error_reason for result in results}
    if "ProviderAuthenticationError" in reasons or "ProviderConfigurationError" in reasons:
        return f"{provider_label}這次沒有完成：驗證失敗，請確認 API Key／連線設定後再試。"
    if "ModelNotFoundError" in reasons:
        return f"{provider_label}這次沒有完成：找不到或無權使用所選模型，請改選另一個。"
    if "ProviderRateLimitError" in reasons:
        return f"{provider_label}這次沒有完成：已達速率或額度限制，請稍後重試。"
    if "ProviderQuotaError" in reasons:
        return f"{provider_label}這次沒有完成：帳戶額度或計費受限，請檢查用量與付費狀態。"
    if "ProviderTimeoutError" in reasons:
        return f"{provider_label}這次沒有完成：回應逾時，可稍後重試或減少文字。"
    if reasons & {"ProviderUnavailableError", "provider_unavailable"}:
        return f"{provider_label}這次沒有完成：目前無法連線，請檢查網路或模型服務。"
    return f"{provider_label}這次沒有完成；API Key 與原始錯誤不會顯示或保存。"


def _queue_handoff(result: CreativeAutomationResult, *, project_id: str, goal: str) -> None:
    """Stage an explicit, project-bound payload for the manual refinement desk."""

    st.session_state[HANDOFF_STATE_KEY] = {
        "schema_version": 2,
        "project_id": project_id,
        "goal": goal,
        "request": result.request.model_dump(mode="json"),
        "draft": result.draft.model_dump(mode="json"),
        "filled_fields": result.filled_fields,
        "preserved_fields": result.preserved_fields,
        "provenance": result.provenance.model_dump(mode="json"),
        "result_fingerprint": result.result_fingerprint,
    }
    _go("Creative Launchpad")


def _render_goal_picker() -> str:
    st.markdown("### 今天想讓靈感替你做什麼？")
    selected = st.radio(
        "創作目標",
        [choice.value for choice in _GOALS],
        format_func=lambda value: _choice_label(value, _GOALS),
        horizontal=True,
        label_visibility="collapsed",
        key=f"{STATE_PREFIX}goal",
    )
    selected_choice = next(choice for choice in _GOALS if choice.value == selected)
    st.caption(selected_choice.help)
    return selected


def _render_seed_inputs(
    goal: str,
) -> tuple[
    str,
    str,
    str,
    str,
    ContentMode,
    CharacterGender | None,
    int | None,
    bool,
]:
    st.markdown("### 給我一點點就好")
    st.caption("三格都可以留白；只填一個關鍵字也能開始。")
    clue = st.text_area(
        "一句線索（選填）",
        placeholder="例如：一座只在退潮時出現的城，守門人正在忘記自己的名字……",
        height=108,
        key=f"{STATE_PREFIX}clue",
    )
    name_left, name_right = st.columns(2)
    with name_left:
        title = st.text_input(
            "作品名（選填）",
            placeholder="留白就讓靈感替你命名",
            key=f"{STATE_PREFIX}author_title",
        )
    with name_right:
        character_name = st.text_input(
            "角色名（選填）",
            placeholder="留白就讓靈感替你命名",
            key=f"{STATE_PREFIX}author_character_name",
        )
    revision = st.text_input(
        "這次想怎麼調整？（選填）",
        placeholder="例如：更浪漫、換成東方奇幻、讓角色更危險一點",
        key=f"{STATE_PREFIX}revision_instruction",
    )
    character_gender: CharacterGender | None = None
    if goal in {"complete", "character"}:
        st.markdown("#### 角色性別")
        character_gender = st.radio(
            "角色性別",
            [CharacterGender.FEMALE, CharacterGender.MALE],
            format_func=lambda value: _GENDER_LABELS[value],
            horizontal=True,
            key=f"{STATE_PREFIX}character_gender",
            label_visibility="collapsed",
            help="這個選擇會固定角色稱謂、外觀與英文提示詞，不讓模型自行猜測。",
        )
        st.caption("會依你的選擇統一角色稱謂、外觀與英文提示詞。")
    st.markdown("#### 內容尺度")
    content_options = [
        ContentMode.GENERAL,
        ContentMode.DRAMA,
        ContentMode.DARK,
        ContentMode.HORROR,
        ContentMode.VIOLENT,
    ]
    raw_current_mode = st.session_state.get(f"{STATE_PREFIX}content_mode")
    if raw_current_mode not in content_options:
        st.session_state.pop(f"{STATE_PREFIX}content_mode", None)
    content_mode = st.selectbox(
        "內容尺度",
        content_options,
        format_func=lambda value: _CONTENT_LABELS[value],
        key=f"{STATE_PREFIX}content_mode",
        label_visibility="collapsed",
    )
    character_age: int | None = None
    confirmed_age = False
    return (
        clue,
        title,
        character_name,
        revision,
        content_mode,
        character_gender,
        character_age,
        confirmed_age,
    )


def _render_locks(*, has_candidate: bool) -> frozenset[AutomationSection]:
    st.markdown("### 喜歡的頁面先夾起來")
    st.caption("重抽時，打勾的頁面會原封不動留下；第一次生成可以先不用管。")
    columns = st.columns(3)
    labels = {
        "world": "保留世界觀",
        "character": "保留角色",
        "story": "保留故事",
    }
    for column, section in zip(columns, labels, strict=True):
        with column:
            st.checkbox(
                labels[section],
                key=f"{STATE_PREFIX}lock_{section}",
                disabled=not has_candidate,
            )
    return _locked_sections()


def _render_provider_picker(
    services: Services,
) -> tuple[str, str, str, bool]:
    """Return source, resolved provider/model and non-secret readiness."""

    source = st.radio(
        "靈感來源",
        [choice.value for choice in _SOURCES],
        format_func=lambda value: _choice_label(value, _SOURCES),
        horizontal=True,
        key=f"{STATE_PREFIX}strategy",
    )
    selected_choice = next(choice for choice in _SOURCES if choice.value == source)
    st.caption(selected_choice.help)
    if source == "offline":
        return source, "offline", "", True

    try:
        readiness = generation_action_readiness(
            services,
            st.session_state,
            task=GenerationTask.GENERAL,
            action_id="creative_automation",
        )
    except ProviderError:
        st.info("全域 AI route 尚未可用；請到「AI 設定」選擇 provider 與模型。")
        return source, "", "", False

    provider_id = readiness.route.primary.provider_id
    model = readiness.route.primary.model
    if provider_id == "openai":
        st.caption(f"全域 route：OpenAI · {model}")
        if not readiness.credential_available:
            st.info(
                "請先到 AI 設定保存 API Key、提供本次工作階段 Key，"
                "或設定系統環境變數 OPENAI_API_KEY。"
            )
        else:
            st.warning(
                "全域 AI 設定已啟用 OpenAI；這次的創作線索與方向會送往遠端。"
                "API Key 不會寫入作品。"
            )
        return source, provider_id, model, readiness.credential_available

    if provider_id == "ollama":
        # Remote Ollama has no global disclosure/credential contract. Keep
        # this generation action local-only and fail closed for any non-loopback
        # endpoint instead of reintroducing a page-local consent override.
        endpoint_info = summarize_endpoint(services.settings.ollama_base_url)
        ready = endpoint_info.valid and endpoint_info.is_loopback
        st.caption(f"全域 route：本機 Ollama · {model}")
        if not endpoint_info.valid:
            st.error(
                f"模型端點設定無效：{endpoint_info.redacted_origin}。"
                "請使用不含使用者資訊、查詢參數或片段的 http(s) URL。"
            )
        elif endpoint_info.is_loopback:
            st.caption(f"本機模型端點：{endpoint_info.redacted_origin}")
        else:
            st.error(
                f"此動作只允許 loopback Ollama；目前端點為 "
                f"{endpoint_info.redacted_origin}。請在 AI 設定改用本機端點或 OpenAI。"
            )
        return source, provider_id, model, ready

    st.info(
        "全域 AI provider 目前為離線；請使用離線靈感抽卡，或到 AI 設定啟用 provider。"
    )
    return source, provider_id, model, False


def _render_action_buttons(
    *, has_candidate: bool, provider_ready: bool, remix_ready: bool
) -> str | None:
    """Return the one author-requested merge action, if any."""

    st.markdown("### 想怎麼長出下一版？")
    st.caption("每一次都只產生桌面候選，不會偷偷覆寫你已保存的內容。")
    columns = st.columns(3)
    clicked: str | None = None
    for column, action in zip(columns, _MERGE_ACTIONS, strict=True):
        with column:
            disabled = not provider_ready or (
                action.value == "remix_unlocked" and (not has_candidate or not remix_ready)
            )
            if st.button(
                action.label,
                help=action.help,
                key=f"{STATE_PREFIX}action_{action.value}",
                type="primary" if action.value == "fill_blanks" else "secondary",
                width="stretch",
                disabled=disabled,
            ):
                clicked = action.value
    return clicked


def _render_labeled_text(label: str, value: str) -> None:
    if value.strip():
        st.markdown(f"**{label}**")
        st.write(value)


def _render_list(label: str, values: tuple[str, ...]) -> None:
    cleaned = tuple(value.strip() for value in values if value.strip())
    if not cleaned:
        return
    st.markdown(f"**{label}**")
    st.markdown("\n".join(f"- {value}" for value in cleaned))


def _render_world_page(request: CreativeLaunchRequest) -> None:
    with st.container(border=True):
        st.markdown("#### 🌍 世界頁")
        heading = request.setting or "這個世界還沒有名字"
        st.markdown(f"### {heading}")
        _render_labeled_text("時代與時間感", request.time_period)
        _render_labeled_text("世界如何運轉", request.technology_or_magic)
        _render_labeled_text("人們如何生活", request.social_context)
        _render_list("不能打破的規則", request.world_rules)
        _render_list("故事會去的地方", request.locations)


def _render_character_page(request: CreativeLaunchRequest, draft: CreativeAutomationDraft) -> None:
    character = request.character
    if character is None:
        return
    with st.container(border=True):
        st.markdown("#### 🗝️ 角色頁")
        age = f" · {character.explicit_age} 歲" if character.explicit_age is not None else ""
        st.markdown(f"### {character.name}{age}")
        identity_label = "她是誰" if draft.character_gender is CharacterGender.FEMALE else "他是誰"
        _render_labeled_text(identity_label, character.identity)
        _render_labeled_text(
            "完整背景故事",
            draft.character_biography or character.biography,
        )
        _render_labeled_text("個性", character.personality)
        _render_labeled_text("說話與聲音", character.voice)
        _render_labeled_text("真正想要的事", character.motivation)
        _render_labeled_text("最害怕的事", character.fear)
        _render_labeled_text("藏著的祕密", character.secret)
        _render_labeled_text("內在拉扯", character.internal_conflict)
        _render_list("可延伸的人際線", character.relationship_hooks)
        _render_labeled_text("故事開始時", character.arc_start)
        _render_list("成長轉折", character.arc_turning_points)
        _render_labeled_text("故事結束時", character.arc_end)
        st.markdown("**外觀記事**")
        visual_lines = tuple(
            value
            for value in (
                character.face,
                character.hair,
                character.eyes,
                character.body,
                *character.distinguishing_features,
            )
            if value.strip()
        )
        if visual_lines:
            st.write(" · ".join(visual_lines))
        english_prompt = draft.english_character_prompt or render_english_keywords(
            request.english_character_keywords
        )
        if english_prompt:
            st.markdown("**英文角色提示詞**")
            st.code(english_prompt, language=None)
            st.caption("英文、逗號分隔；可直接複製到你自己的圖片或影片工作流。")


def _render_story_page(request: CreativeLaunchRequest, draft: CreativeAutomationDraft) -> None:
    if request.mode is CreationMode.WORLD_ONLY:
        return
    with st.container(border=True):
        st.markdown("#### 📖 故事頁")
        st.markdown(f"### {request.title}")
        _render_labeled_text("一句話抓住故事", draft.logline)
        _render_labeled_text("故事全貌", draft.synopsis or request.concept)
        _render_labeled_text("第一個讓人想讀下去的畫面", draft.opening_hook)
        _render_labeled_text("核心衝突", request.central_conflict)
        _render_labeled_text("接下來會往哪裡走", request.direction)
        _render_labeled_text("希望留下的結尾感受", request.ending_preference)
        _render_labeled_text("氣質與語感", request.tone)
        _render_list("故事想碰觸的主題", request.themes)


def _render_candidate(
    request: CreativeLaunchRequest,
    draft: CreativeAutomationDraft,
    *,
    provider_note: str,
    fingerprint: str,
    goal: str = "complete",
) -> None:
    st.divider()
    st.markdown("## 這一版靈感")
    st.caption("這只是放在桌上的候選稿；還沒有覆寫或保存到作品裡。")
    sections = _GOAL_SECTIONS.get(goal, _GOAL_SECTIONS["complete"])
    if AutomationSection.WORLD in sections:
        _render_world_page(request)
    if AutomationSection.CHARACTER in sections:
        _render_character_page(request, draft)
    if AutomationSection.STORY in sections:
        _render_story_page(request, draft)
    with st.expander("生成小記（進階）"):
        st.caption(provider_note or "未提供生成來源說明")
        if fingerprint:
            st.code(fingerprint, language=None)


def _candidate_label(
    fingerprint: str,
    candidates: tuple[CreativeAutomationResult, ...],
) -> str:
    for index, candidate in enumerate(candidates):
        if candidate.result_fingerprint == fingerprint:
            return f"候選 {chr(65 + index)}"
    return "候選"


def _short_summary(value: str, *, limit: int = 90) -> str:
    compact = " ".join(value.split())
    if len(compact) <= limit:
        return compact
    return f"{compact[: limit - 1].rstrip()}…"


def _render_candidate_board(
    candidates: tuple[CreativeAutomationResult, ...],
) -> CreativeAutomationResult:
    st.divider()
    st.markdown("## 桌上的三個方向")
    st.caption("先選一個主候選；下面會展開它的完整世界、角色與故事頁。")
    fingerprints = tuple(candidate.result_fingerprint for candidate in candidates)
    selected_fingerprint = st.radio(
        "主候選",
        fingerprints,
        format_func=lambda value: _candidate_label(value, candidates),
        horizontal=True,
        key=CANDIDATE_SELECTION_STATE_KEY,
    )
    selected = next(
        candidate
        for candidate in candidates
        if candidate.result_fingerprint == selected_fingerprint
    )
    st.session_state[CANDIDATE_STATE_KEY] = selected.model_dump(mode="json")

    columns = st.columns(len(candidates))
    for index, (column, candidate) in enumerate(zip(columns, candidates, strict=True)):
        with column.container(border=True):
            marker = " · 主候選" if candidate is selected else ""
            st.markdown(f"#### 候選 {chr(65 + index)}{marker}")
            st.markdown(f"**{candidate.request.title}**")
            st.caption(candidate.request.setting or "世界仍待命名")
            if candidate.request.character is not None:
                st.write(candidate.request.character.name)
            summary = candidate.draft.logline or candidate.draft.synopsis
            if summary:
                st.write(_short_summary(summary))
    history = st.session_state.get(CANDIDATE_HISTORY_STATE_KEY, [])
    if st.button(
        "復原上一步",
        key=f"{STATE_PREFIX}undo",
        disabled=not isinstance(history, list) or not history,
    ):
        _undo_board()
        st.rerun()
    return selected


def _render_partial_remix(
    *,
    goal: str,
    locked_sections: frozenset[AutomationSection],
    provider_ready: bool,
) -> AutomationSection | None:
    st.markdown("### 只重抽其中一頁")
    st.caption("只替主候選換掉這一頁，另外兩頁與其他候選都留在桌上。")
    sections = _GOAL_SECTIONS.get(goal, _GOAL_SECTIONS["complete"])
    labels = {
        AutomationSection.WORLD: "重抽世界",
        AutomationSection.CHARACTER: "重抽角色",
        AutomationSection.STORY: "重抽故事",
    }
    clicked: AutomationSection | None = None
    columns = st.columns(len(sections))
    for column, section in zip(columns, sections, strict=True):
        with column:
            if st.button(
                labels[section],
                key=f"{STATE_PREFIX}remix_{section.value}",
                width="stretch",
                disabled=not provider_ready or section in locked_sections,
            ):
                clicked = section
    return clicked


def _render_mix_controls(
    candidates: tuple[CreativeAutomationResult, ...],
    selected: CreativeAutomationResult,
    *,
    goal: str,
    locked_sections: frozenset[AutomationSection],
) -> tuple[dict[AutomationSection, CreativeAutomationResult], bool]:
    st.markdown("### 從三個方向各挑一頁")
    st.caption("世界、角色、故事可以分別取自不同候選；夾住的頁面維持主候選。")
    sections = _GOAL_SECTIONS.get(goal, _GOAL_SECTIONS["complete"])
    fingerprints = tuple(candidate.result_fingerprint for candidate in candidates)
    by_fingerprint = {candidate.result_fingerprint: candidate for candidate in candidates}
    labels = {
        AutomationSection.WORLD: "世界取自",
        AutomationSection.CHARACTER: "角色取自",
        AutomationSection.STORY: "故事取自",
    }
    selected_sources: dict[AutomationSection, CreativeAutomationResult] = {}
    columns = st.columns(len(sections))
    for column, section in zip(columns, sections, strict=True):
        key = f"{STATE_PREFIX}mix_{section.value}_source"
        if st.session_state.get(key) not in fingerprints or section in locked_sections:
            st.session_state[key] = selected.result_fingerprint
        with column:
            source_fingerprint = st.selectbox(
                labels[section],
                fingerprints,
                format_func=lambda value: _candidate_label(value, candidates),
                key=key,
                disabled=section in locked_sections,
            )
        selected_sources[section] = by_fingerprint[source_fingerprint]
    can_mix = len(candidates) > 1 and any(section not in locked_sections for section in sections)
    clicked = st.button(
        "混成新的主候選",
        key=f"{STATE_PREFIX}combine_candidates",
        width="stretch",
        disabled=not can_mix,
    )
    return selected_sources, clicked


@contextmanager
def _automation_service_for_action(
    services: Services,
    *,
    source: str,
) -> Iterator[CreativeAutomationService]:
    if source == "offline":
        yield services.creative_automation
        return
    with generation_provider_action(
        services,
        st.session_state,
            task=GenerationTask.GENERAL,
            action_id="creative_automation",
    ) as provider:
        yield CreativeAutomationService(provider)


def render() -> None:
    """Render the staged, automation-first Inspiration Desk."""

    from imaginarium_forge.ui.bootstrap import get_services
    from imaginarium_forge.ui.components import page_header
    from imaginarium_forge.ui.project_gateway import (
        ProjectGatewayCopy,
        render_project_gateway,
    )

    apply_openai_session_state_transitions(st.session_state)

    page_header(
        "靈感桌",
        "只要留下一句線索，先讓世界、角色和故事長成一份可挑選的草稿。",
        eyebrow="你的故事書房",
        badges=(("不會自動存檔", "teal"), ("隨時可以重抽", "")),
    )
    services = get_services()
    raw_project_id = st.session_state.get("selected_project_id")
    project_id = raw_project_id if isinstance(raw_project_id, str) else None
    _reset_for_project(project_id)
    if not project_id:
        render_project_gateway(
            key_prefix="creative_automation",
            copy=ProjectGatewayCopy(
                feature_name="靈感生成",
                project_reason="這個完整生成流程目前會同時準備世界、角色與故事，正式候選需要作品作為範圍。",
                draft_description=(
                    "不想先開書時，可先做完整角色與個人故事；圖片 Prompt 也會一起生成。"
                ),
                draft_button_label="先做免專案角色草稿",
            ),
        )
        return

    project = services.projects.get_project(project_id)
    st.caption(f"正在替《{project.name}》發想 · 生成結果先留在桌面，不會直接寫進作品")
    notice = st.session_state.pop(NOTICE_STATE_KEY, None)
    if isinstance(notice, dict) and notice.get("kind") == "warning":
        st.warning(str(notice.get("text", "模型失敗後已建立離線候選。")))
    elif isinstance(notice, str) and notice:
        st.success(notice)
    error = st.session_state.get(ERROR_STATE_KEY)
    if isinstance(error, UserFacingError):
        render_error_card(error, key_prefix="creative_automation")
    elif isinstance(error, str) and error:
        _candidate_survives_error(
            st.session_state.get(CANDIDATE_STATE_KEY),
            ProviderError("legacy creative automation failure"),
            operation="產生靈感候選",
        )
        migrated = st.session_state.get(ERROR_STATE_KEY)
        if isinstance(migrated, UserFacingError):
            render_error_card(migrated, key_prefix="creative_automation")

    candidates = _load_candidates()
    candidate = _selected_candidate(candidates)
    goal = _render_goal_picker()
    (
        clue,
        author_title,
        author_character_name,
        revision_instruction,
        content_mode,
        character_gender,
        author_character_age,
        author_confirmed_age,
    ) = _render_seed_inputs(goal)
    has_candidate = candidate is not None
    locked_sections = _render_locks(has_candidate=has_candidate)
    st.markdown("### 選一種發想方式")
    source, provider_id, model, provider_ready = _render_provider_picker(services)
    remix_ready = any(section not in locked_sections for section in _GOAL_SECTIONS[goal])
    action = _render_action_buttons(
        has_candidate=has_candidate,
        provider_ready=provider_ready,
        remix_ready=remix_ready,
    )
    if action is not None:
        strategy = (
            AutomationStrategy.PROVIDER
            if source == "global_ai"
            else AutomationStrategy.OFFLINE
        )
        previous_raw = st.session_state.get(CANDIDATE_STATE_KEY)
        try:
            with st.spinner("靈感正在把零星線索排成一頁頁草稿……"), (
                _automation_service_for_action(services, source=source)
            ) as automation_service:
                results = _generate_candidate_batch(
                    automation_service,
                    project_id=project_id,
                    goal=goal,
                    clue=clue,
                    author_title=author_title,
                    author_character_name=author_character_name,
                    content_mode=content_mode,
                    character_gender=character_gender,
                    author_character_age=author_character_age,
                    author_confirmed_age=author_confirmed_age,
                    revision_instruction=revision_instruction,
                    strategy=strategy,
                    model=model,
                    action=action,
                    locked_sections=locked_sections,
                    previous=candidate,
                    previous_goal=str(
                        st.session_state.get(CANDIDATE_GOAL_STATE_KEY, "")
                    ),
                )
        except Exception as exc:
            _candidate_survives_error(
                previous_raw,
                exc,
                operation="產生靈感候選",
                provider_label=("OpenAI" if provider_id == "openai" else "本機模型"),
            )
        else:
            if (
                strategy is AutomationStrategy.PROVIDER
                and any(result.provenance.fallback for result in results)
                and candidates
            ):
                provider_label = "OpenAI" if provider_id == "openai" else "本機模型"
                _candidate_survives_error(
                    previous_raw,
                    ProviderError("provider returned a fallback candidate"),
                    operation="產生靈感候選",
                    provider_label=provider_label,
                )
            else:
                _commit_board(
                    results,
                    selected_fingerprint=results[0].result_fingerprint,
                    goal=goal,
                    previous_candidates=candidates,
                    previous_selected_fingerprint=(
                        candidate.result_fingerprint if candidate is not None else ""
                    ),
                    previous_goal=str(st.session_state.get(CANDIDATE_GOAL_STATE_KEY, goal)),
                )
                st.session_state.pop(ERROR_STATE_KEY, None)
                provider_label = "OpenAI" if provider_id == "openai" else "本機模型"
                if any(result.provenance.fallback for result in results):
                    st.session_state[NOTICE_STATE_KEY] = {
                        "kind": "warning",
                        "text": (
                            _provider_failure_message(
                                results,
                                provider_label=provider_label,
                            )
                            + " 全域 fallback policy 允許建立離線候選；請確認後再使用。"
                        ),
                    }
                else:
                    st.session_state[NOTICE_STATE_KEY] = (
                        "三個新方向已放上桌面；先選主候選，再夾住喜歡的頁面。"
                    )
        st.rerun()

    candidates = _load_candidates()
    if not candidates:
        st.caption("第一批候選產生後，三個方向會並排放在這裡。")
        return
    candidate_goal = str(st.session_state.get(CANDIDATE_GOAL_STATE_KEY, goal))
    candidate = _render_candidate_board(candidates)

    partial_section = _render_partial_remix(
        goal=candidate_goal,
        locked_sections=locked_sections,
        provider_ready=provider_ready,
    )
    if partial_section is not None:
        strategy = (
            AutomationStrategy.PROVIDER
            if source == "global_ai"
            else AutomationStrategy.OFFLINE
        )
        previous_raw = st.session_state.get(CANDIDATE_STATE_KEY)
        try:
            with st.spinner("只替這一頁找新的可能……"), _automation_service_for_action(
                services,
                source=source,
            ) as automation_service:
                rebased = automation_service.apply_author_overrides(
                    candidate,
                    author_title=author_title,
                    author_character_name=author_character_name,
                    clue=clue,
                    content_mode=content_mode,
                    character_gender=character_gender,
                    author_character_age=author_character_age,
                    author_confirmed_age=author_confirmed_age,
                )
                remixed = automation_service.remix(
                    rebased,
                    target_fields=(partial_section,),
                    strategy=strategy,
                    model=model,
                    revision_instruction=revision_instruction,
                    locked_fields=_locked_fields(locked_sections),
                )
        except Exception as exc:
            _candidate_survives_error(
                previous_raw,
                exc,
                operation="重抽靈感候選頁面",
                provider_label=("OpenAI" if provider_id == "openai" else "本機模型"),
            )
        else:
            if strategy is AutomationStrategy.PROVIDER and remixed.provenance.fallback:
                provider_label = "OpenAI" if provider_id == "openai" else "本機模型"
                _candidate_survives_error(
                    previous_raw,
                    ProviderError("provider returned a fallback candidate"),
                    operation="重抽靈感候選頁面",
                    provider_label=provider_label,
                )
            else:
                replacement = tuple(
                    remixed if item.result_fingerprint == candidate.result_fingerprint else item
                    for item in candidates
                )
                _commit_board(
                    replacement,
                    selected_fingerprint=remixed.result_fingerprint,
                    goal=candidate_goal,
                    previous_candidates=candidates,
                    previous_selected_fingerprint=candidate.result_fingerprint,
                    previous_goal=candidate_goal,
                )
                st.session_state.pop(ERROR_STATE_KEY, None)
                st.session_state[NOTICE_STATE_KEY] = "主候選的這一頁已重抽；其他內容都留著。"
        st.rerun()

    mix_sources, combine_clicked = _render_mix_controls(
        candidates,
        candidate,
        goal=candidate_goal,
        locked_sections=locked_sections,
    )
    if combine_clicked:
        try:
            combined = services.creative_automation.combine_candidates(
                candidate,
                world_candidate=mix_sources.get(AutomationSection.WORLD),
                character_candidate=mix_sources.get(AutomationSection.CHARACTER),
                story_candidate=mix_sources.get(AutomationSection.STORY),
                locked_fields=_locked_fields(locked_sections),
            )
        except Exception as exc:
            _candidate_survives_error(
                st.session_state.get(CANDIDATE_STATE_KEY),
                exc,
                operation="混搭靈感候選",
            )
        else:
            replacement = tuple(
                combined if item.result_fingerprint == candidate.result_fingerprint else item
                for item in candidates
            )
            _commit_board(
                replacement,
                selected_fingerprint=combined.result_fingerprint,
                goal=candidate_goal,
                previous_candidates=candidates,
                previous_selected_fingerprint=candidate.result_fingerprint,
                previous_goal=candidate_goal,
            )
            st.session_state.pop(ERROR_STATE_KEY, None)
            st.session_state[NOTICE_STATE_KEY] = "挑選的世界、角色與故事已混成新的主候選。"
        st.rerun()

    _render_candidate(
        candidate.request,
        candidate.draft,
        provider_note=_provider_note(candidate),
        fingerprint=candidate.result_fingerprint,
        goal=candidate_goal,
    )
    st.caption("想逐欄調整、確認內容分級或正式保存時，再把這一版帶到精修桌。")
    if st.button(
        "帶到精修桌",
        key=f"{STATE_PREFIX}send_to_refinement",
        type="primary",
        width="stretch",
    ):
        _queue_handoff(candidate, project_id=project_id, goal=candidate_goal)


__all__ = [
    "CANDIDATES_STATE_KEY",
    "CANDIDATE_GOAL_STATE_KEY",
    "CANDIDATE_HISTORY_STATE_KEY",
    "CANDIDATE_SELECTION_STATE_KEY",
    "CANDIDATE_STATE_KEY",
    "ERROR_STATE_KEY",
    "HANDOFF_STATE_KEY",
    "OPENAI_API_KEY_STATE_KEY",
    "PROJECT_STATE_KEY",
    "STATE_PREFIX",
    "render",
]
