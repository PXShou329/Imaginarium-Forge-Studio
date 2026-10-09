"""Standalone editor for durable, project-optional world seed drafts."""

from __future__ import annotations

from typing import Any

import streamlit as st

from imaginarium_forge.application.errors import ApplicationError
from imaginarium_forge.application.services.creative_inspiration_service import (
    CreativeInspirationService,
)
from imaginarium_forge.application.services.generation.context_compiler import (
    ContextCompilationError,
    ContextCompiler,
)
from imaginarium_forge.application.services.generation.contracts import (
    GenerationErrorCategory,
    GenerationFailure,
    GenerationTask,
)
from imaginarium_forge.application.services.generation.facade import (
    CreativeCandidateAction,
    UnsupportedWorldSeedRequestError,
    WorldSeedGenerationPath,
    resolve_world_seed_generation_path,
)
from imaginarium_forge.application.services.generation.refinement import (
    InvalidRefinementCandidateError,
    StaleRefinementBaseError,
)
from imaginarium_forge.application.services.generation.refinement_candidate_persistence import (
    RefinementCandidateBinding,
)
from imaginarium_forge.application.user_facing_errors import ErrorDataSafety
from imaginarium_forge.domain.common.ids import new_id
from imaginarium_forge.domain.creative.world_seed_draft import (
    WorldSeedDraft,
    WorldSeedGenerationMode,
)
from imaginarium_forge.domain.generation.durable import ArtifactKind, sha256_json
from imaginarium_forge.infrastructure.db.adapters.draft_candidate_target import (
    WORLD_SEED_DRAFT_TARGET,
)
from imaginarium_forge.ui.bootstrap import get_services
from imaginarium_forge.ui.components import page_header
from imaginarium_forge.ui.creative_generation import (
    authoring_editor_state_sha256,
    candidate_value_replaces_current,
    clear_candidate_state,
    render_candidate_action_state,
    render_candidate_card,
    render_candidate_error,
    render_passive_creative_runtime_status,
    request_candidate,
)
from imaginarium_forge.ui.error_presenter import present_exception
from imaginarium_forge.ui.refinement import (
    RefinementUIDecision,
    candidate_receipt_for_save_as_new,
    clear_refinement_state,
    commit_durable_refinement_decision,
    protected_facts_and_spans,
    record_refinement_apply,
    refinement_receipt,
    render_durable_refinement_history,
    render_refinement_candidate,
    render_refinement_controls,
    render_refinement_failure,
    render_refinement_undo,
    set_refinement_failure,
)
from imaginarium_forge.ui.writing_request_controls import (
    WritingContextSourceInput,
    WritingRequestSnapshot,
    WritingRequestSurface,
    build_writing_request_defaults,
    render_writing_request_controls,
)

PAGE_KEY = "World Seed Drafts"
PAGE_LABEL = "世界種子草稿"

STATE_PREFIX = "world_seed_draft"
WORLD_WRITING_KEY_PREFIX = f"{STATE_PREFIX}_ai_world"
PENDING_SELECTION_STATE_KEY = f"{STATE_PREFIX}_pending_selection"
BOUND_DRAFT_STATE_KEY = f"{STATE_PREFIX}_bound_draft"
SELECTED_DRAFT_STATE_KEY = f"{STATE_PREFIX}_selected_draft"
BASE_UPDATED_AT_STATE_KEY = f"{STATE_PREFIX}_base_updated_at"
EDITOR_VALUES_STATE_KEY = f"{STATE_PREFIX}_editor_values"
FLASH_STATE_KEY = f"{STATE_PREFIX}_flash"
PENDING_FORM_STATE_KEY = f"{STATE_PREFIX}_pending_form"
INSPIRATION_NOTICE_STATE_KEY = f"{STATE_PREFIX}_inspiration_notice"
NEW_DRAFT_CONFIRM_STATE_KEY = f"{STATE_PREFIX}_new_confirm"
REROLL_CONFIRM_STATE_KEY = f"{STATE_PREFIX}_reroll_confirm_pending"

PROJECT_STATE_KEY = f"{STATE_PREFIX}_project_id"
TITLE_STATE_KEY = f"{STATE_PREFIX}_title"
SETTING_STATE_KEY = f"{STATE_PREFIX}_setting"
TIME_PERIOD_STATE_KEY = f"{STATE_PREFIX}_time_period"
WORLD_RULES_STATE_KEY = f"{STATE_PREFIX}_world_rules"
LOCATIONS_STATE_KEY = f"{STATE_PREFIX}_locations"
SOCIAL_CONTEXT_STATE_KEY = f"{STATE_PREFIX}_social_context"
TECHNOLOGY_OR_MAGIC_STATE_KEY = f"{STATE_PREFIX}_technology_or_magic"
CENTRAL_CONFLICT_STATE_KEY = f"{STATE_PREFIX}_central_conflict"
THEMES_STATE_KEY = f"{STATE_PREFIX}_themes"
GENERATION_MODE_STATE_KEY = f"{STATE_PREFIX}_generation_mode"
GENERATION_SEED_STATE_KEY = f"{STATE_PREFIX}_generation_seed"

_NEW_DRAFT = "__new__"
_FORM_DEFAULTS: dict[str, object] = {
    PROJECT_STATE_KEY: "",
    TITLE_STATE_KEY: "",
    SETTING_STATE_KEY: "",
    TIME_PERIOD_STATE_KEY: "",
    WORLD_RULES_STATE_KEY: "",
    LOCATIONS_STATE_KEY: "",
    SOCIAL_CONTEXT_STATE_KEY: "",
    TECHNOLOGY_OR_MAGIC_STATE_KEY: "",
    CENTRAL_CONFLICT_STATE_KEY: "",
    THEMES_STATE_KEY: "",
    GENERATION_MODE_STATE_KEY: WorldSeedGenerationMode.MANUAL.value,
    GENERATION_SEED_STATE_KEY: "",
}
_EDITOR_STATE_KEYS = (
    *_FORM_DEFAULTS, SELECTED_DRAFT_STATE_KEY, BOUND_DRAFT_STATE_KEY, BASE_UPDATED_AT_STATE_KEY,
)

_GENERATION_LABELS = {
    WorldSeedGenerationMode.MANUAL.value: "手寫",
    WorldSeedGenerationMode.FILL_BLANKS.value: "本機隨機只補空白",
    WorldSeedGenerationMode.REROLL_ALL.value: "本機隨機全部重抽",
}

_REFINEMENT_FIELDS: dict[str, tuple[str, str]] = {
    "setting": ("世界／舞台概述", SETTING_STATE_KEY),
    "time_period": ("時代／時間背景", TIME_PERIOD_STATE_KEY),
    "world_rules": ("世界規則", WORLD_RULES_STATE_KEY),
    "locations": ("地點", LOCATIONS_STATE_KEY),
    "social_context": ("社會與制度", SOCIAL_CONTEXT_STATE_KEY),
    "technology_or_magic": ("科技／魔法與限制", TECHNOLOGY_OR_MAGIC_STATE_KEY),
    "central_conflict": ("核心衝突", CENTRAL_CONFLICT_STATE_KEY),
    "themes": ("主題", THEMES_STATE_KEY),
}

_WORLD_CONTEXT_SOURCE_TYPE = "world_seed_field"
_WORLD_BODY_STRUCTURE = (
    "setting",
    "world_rules",
    "locations",
    "social_context",
    "technology_or_magic",
    "central_conflict",
    "themes",
)
_WORLD_AUTHOR_LOCKED_FIELDS = frozenset({"title", "time_period"})
_WORLD_CONTEXT_FIELDS: tuple[tuple[str, str, str, int, int], ...] = (
    ("central_conflict", "核心衝突", CENTRAL_CONFLICT_STATE_KEY, 1_000, 0),
    ("setting", "世界／舞台概述", SETTING_STATE_KEY, 1_000, 1),
    ("world_rules", "世界規則", WORLD_RULES_STATE_KEY, 1_500, 2),
    ("social_context", "社會與制度", SOCIAL_CONTEXT_STATE_KEY, 1_000, 3),
    (
        "technology_or_magic",
        "科技／魔法與限制",
        TECHNOLOGY_OR_MAGIC_STATE_KEY,
        1_000,
        4,
    ),
    ("locations", "地點", LOCATIONS_STATE_KEY, 1_000, 5),
    ("themes", "主題", THEMES_STATE_KEY, 800, 6),
)


def _join_lines(values: tuple[str, ...]) -> str:
    return "\n".join(values)


def _split_lines(value: str) -> tuple[str, ...]:
    return tuple(line for line in value.splitlines() if line.strip())


def _draft_form(draft: WorldSeedDraft) -> dict[str, object]:
    return {
        PROJECT_STATE_KEY: draft.project_id or "",
        TITLE_STATE_KEY: draft.title,
        SETTING_STATE_KEY: draft.setting,
        TIME_PERIOD_STATE_KEY: draft.time_period,
        WORLD_RULES_STATE_KEY: _join_lines(draft.world_rules),
        LOCATIONS_STATE_KEY: _join_lines(draft.locations),
        SOCIAL_CONTEXT_STATE_KEY: draft.social_context,
        TECHNOLOGY_OR_MAGIC_STATE_KEY: draft.technology_or_magic,
        CENTRAL_CONFLICT_STATE_KEY: draft.central_conflict,
        THEMES_STATE_KEY: _join_lines(draft.themes),
        GENERATION_MODE_STATE_KEY: draft.generation_mode.value,
        GENERATION_SEED_STATE_KEY: draft.generation_seed,
    }


def _durable_refinement_binding(
    services: Any,
    *,
    selected: object,
    field_name: str,
) -> RefinementCandidateBinding | None:
    """Bind only a fully clean editor to its exact persisted world snapshot."""

    if not isinstance(selected, str) or not selected or selected == _NEW_DRAFT:
        return None
    try:
        draft = services.world_seed_drafts.get_draft(selected)
    except (ApplicationError, RuntimeError, TypeError, ValueError):
        return None
    if (
        st.session_state.get(BOUND_DRAFT_STATE_KEY) != draft.id
        or st.session_state.get(BASE_UPDATED_AT_STATE_KEY) != draft.updated_at
    ):
        return None
    expected_form = _draft_form(draft)
    if any(st.session_state.get(key) != value for key, value in expected_form.items()):
        return None
    return RefinementCandidateBinding(
        target_type=WORLD_SEED_DRAFT_TARGET,
        target_id=draft.id,
        field_name=field_name,
        expected_base_snapshot_sha256=sha256_json(draft.model_dump(mode="json")),
    )


def _capture_editor_state() -> None:
    """Keep an exact, page-owned author/binding mirror across route unmounts.

    Input callbacks run before navigation renders its destination. Never mirror
    credentials, consent, candidates or pending actions with the author fields.
    """

    st.session_state[EDITOR_VALUES_STATE_KEY] = {
        key: st.session_state[key] for key in _EDITOR_STATE_KEYS if key in st.session_state
    }


def _restore_editor_state() -> None:
    """Restore only widget-cleaned fields, preserving new input and the old CAS base."""

    saved = st.session_state.get(EDITOR_VALUES_STATE_KEY)
    if isinstance(saved, dict):
        for key in _EDITOR_STATE_KEYS:
            if key not in st.session_state and key in saved:
                st.session_state[key] = saved[key]


def _bind_new() -> None:
    for key, value in _FORM_DEFAULTS.items():
        st.session_state[key] = value
    st.session_state[SELECTED_DRAFT_STATE_KEY] = None
    st.session_state[BASE_UPDATED_AT_STATE_KEY] = None
    st.session_state[BOUND_DRAFT_STATE_KEY] = _NEW_DRAFT
    st.session_state.pop(NEW_DRAFT_CONFIRM_STATE_KEY, None)
    _capture_editor_state()


def _bind_draft(draft: WorldSeedDraft) -> None:
    for key, value in _draft_form(draft).items():
        st.session_state[key] = value
    st.session_state[SELECTED_DRAFT_STATE_KEY] = draft.id
    st.session_state[BASE_UPDATED_AT_STATE_KEY] = draft.updated_at
    st.session_state[BOUND_DRAFT_STATE_KEY] = draft.id
    _capture_editor_state()


def _ensure_bound_editor(service: Any) -> bool:
    pending = st.session_state.pop(PENDING_SELECTION_STATE_KEY, None)
    if pending is not None:
        if pending == _NEW_DRAFT:
            _bind_new()
            return True
        try:
            _bind_draft(service.get_draft(str(pending)))
        except (ApplicationError, RuntimeError, TypeError, ValueError) as exc:
            present_exception(
                exc,
                operation_zh_tw="讀取世界種子草稿",
                key_prefix="world_seed_load",
                data_safety=ErrorDataSafety(
                    message_zh_tw=(
                        "目前編輯器文字與原草稿選擇均保留；未載入失敗的草稿，也沒有寫入資料。"
                    ),
                    author_input_preserved=True,
                    original_preserved=True,
                    draft_selection_preserved=True,
                ),
            )
            return False
        return True
    if BOUND_DRAFT_STATE_KEY not in st.session_state:
        _bind_new()
    return True


def _inspiration_values(seed: str) -> dict[str, str]:
    suggestion = CreativeInspirationService.world(seed=seed)
    return {
        TITLE_STATE_KEY: suggestion.title_suggestion,
        SETTING_STATE_KEY: suggestion.setting,
        TIME_PERIOD_STATE_KEY: suggestion.time_period,
        WORLD_RULES_STATE_KEY: _join_lines(suggestion.world_rules),
        LOCATIONS_STATE_KEY: _join_lines(suggestion.locations),
        SOCIAL_CONTEXT_STATE_KEY: suggestion.social_context,
        TECHNOLOGY_OR_MAGIC_STATE_KEY: suggestion.technology_or_magic,
        CENTRAL_CONFLICT_STATE_KEY: suggestion.central_conflict,
        THEMES_STATE_KEY: _join_lines(suggestion.themes),
    }


def _apply_local_inspiration(*, fill_blanks_only: bool) -> None:
    seed = new_id()
    pending: dict[str, str] = {}
    for key, value in _inspiration_values(seed).items():
        if not fill_blanks_only or not str(st.session_state.get(key, "")).strip():
            pending[key] = value
    if fill_blanks_only and not pending:
        st.session_state[INSPIRATION_NOTICE_STATE_KEY] = (
            "目前沒有可補的空白欄位；編輯器與保存內容都沒有變更。"
        )
        return
    mode = (
        WorldSeedGenerationMode.FILL_BLANKS
        if fill_blanks_only
        else WorldSeedGenerationMode.REROLL_ALL
    )
    pending[GENERATION_MODE_STATE_KEY] = mode.value
    pending[GENERATION_SEED_STATE_KEY] = seed
    st.session_state[PENDING_FORM_STATE_KEY] = pending


def _project_options(services: Any) -> tuple[list[str], dict[str, str]]:
    projects = services.projects.list_projects(include_archived=True)
    options = ["", *(project.id for project in projects)]
    labels = {"": "不綁定作品（留在自由創作箱）"}
    labels.update(
        {
            project.id: (
                project.name
                if project.status.value == "active"
                else f"{project.name}（已封存作品）"
            )
            for project in projects
        }
    )
    return options, labels


def _save(services: Any) -> None:
    selected_id = st.session_state.get(SELECTED_DRAFT_STATE_KEY)
    values = {
        "project_id": str(st.session_state.get(PROJECT_STATE_KEY, "")).strip() or None,
        "title": str(st.session_state.get(TITLE_STATE_KEY, "")),
        "setting": str(st.session_state.get(SETTING_STATE_KEY, "")),
        "time_period": str(st.session_state.get(TIME_PERIOD_STATE_KEY, "")),
        "world_rules": _split_lines(str(st.session_state.get(WORLD_RULES_STATE_KEY, ""))),
        "locations": _split_lines(str(st.session_state.get(LOCATIONS_STATE_KEY, ""))),
        "social_context": str(st.session_state.get(SOCIAL_CONTEXT_STATE_KEY, "")),
        "technology_or_magic": str(st.session_state.get(TECHNOLOGY_OR_MAGIC_STATE_KEY, "")),
        "central_conflict": str(st.session_state.get(CENTRAL_CONFLICT_STATE_KEY, "")),
        "themes": _split_lines(str(st.session_state.get(THEMES_STATE_KEY, ""))),
        "generation_mode": str(
            st.session_state.get(
                GENERATION_MODE_STATE_KEY,
                WorldSeedGenerationMode.MANUAL.value,
            )
        ),
        "generation_seed": str(st.session_state.get(GENERATION_SEED_STATE_KEY, "")),
    }
    try:
        if selected_id:
            draft = services.world_seed_drafts.update_draft(
                str(selected_id),
                **values,
                expected_updated_at=st.session_state.get(BASE_UPDATED_AT_STATE_KEY),
            )
            message = "世界種子草稿已更新並重新讀取。"
        else:
            draft = services.world_seed_drafts.create_draft(**values)
            message = "世界種子草稿已正式保存並重新讀取。"
    except ApplicationError as exc:
        present_exception(
            exc,
            operation_zh_tw="儲存世界種子草稿",
            key_prefix="world_seed_save",
        )
        return
    st.session_state[PENDING_SELECTION_STATE_KEY] = draft.id
    st.session_state[BOUND_DRAFT_STATE_KEY] = None
    st.session_state[FLASH_STATE_KEY] = message
    st.rerun()


def _archive(services: Any, draft_id: str) -> None:
    try:
        services.world_seed_drafts.archive_draft(draft_id)
    except ApplicationError as exc:
        present_exception(
            exc,
            operation_zh_tw="收起世界種子草稿",
            key_prefix="world_seed_archive",
        )
        return
    st.session_state[PENDING_SELECTION_STATE_KEY] = _NEW_DRAFT
    st.session_state[BOUND_DRAFT_STATE_KEY] = None
    st.session_state[FLASH_STATE_KEY] = "世界種子草稿已封存；內容仍保留在本機資料庫。"
    st.rerun()


def _offline_ai_candidate() -> dict[str, object]:
    values = _inspiration_values(new_id())
    return {
        "title": values[TITLE_STATE_KEY],
        "setting": values[SETTING_STATE_KEY],
        "time_period": values[TIME_PERIOD_STATE_KEY],
        "world_rules": _split_lines(values[WORLD_RULES_STATE_KEY]),
        "locations": _split_lines(values[LOCATIONS_STATE_KEY]),
        "social_context": values[SOCIAL_CONTEXT_STATE_KEY],
        "technology_or_magic": values[TECHNOLOGY_OR_MAGIC_STATE_KEY],
        "central_conflict": values[CENTRAL_CONFLICT_STATE_KEY],
        "themes": _split_lines(values[THEMES_STATE_KEY]),
    }


def _world_authoring_sources() -> tuple[WritingContextSourceInput, ...]:
    """Keep raw author body fields in typed sources, never parallel base inputs."""

    return tuple(
        WritingContextSourceInput(
            source_id=field_name,
            source_type=_WORLD_CONTEXT_SOURCE_TYPE,
            label=label,
            text=str(st.session_state.get(state_key, "")),
            included=True,
            max_visible_chars=max_visible_chars,
            priority=priority,
            required=False,
        )
        for field_name, label, state_key, max_visible_chars, priority in _WORLD_CONTEXT_FIELDS
    )


def _world_protected_facts() -> tuple[str, ...]:
    """Project author-locked identity/time fields as non-compacting facts."""

    facts: list[str] = []
    title = str(st.session_state.get(TITLE_STATE_KEY, "")).strip()
    time_period = str(st.session_state.get(TIME_PERIOD_STATE_KEY, "")).strip()
    seen: set[str] = set()
    for fact in (title, time_period):
        identity = fact.casefold()
        if fact and identity not in seen:
            seen.add(identity)
            facts.append(fact)
    return tuple(facts)


def _world_authoring_base_inputs() -> dict[str, object]:
    """Return schema structure and locked fields, excluding every raw body source."""

    return {
        "title": str(st.session_state.get(TITLE_STATE_KEY, "")),
        "time_period": str(st.session_state.get(TIME_PERIOD_STATE_KEY, "")),
        "body_structure": list(_WORLD_BODY_STRUCTURE),
    }


def _validated_world_writing_request(
    writing_request: WritingRequestSnapshot | None,
) -> WritingRequestSnapshot | None:
    """Fail closed before enabling actions when non-negotiable context cannot fit."""

    if writing_request is None:
        return None
    try:
        generation_path = resolve_world_seed_generation_path(
            writing_request.intent,
            writing_request.length_spec,
        )
    except UnsupportedWorldSeedRequestError as error:
        present_exception(
            error,
            operation_zh_tw="檢查世界設定創作要求",
            key_prefix="world_seed_unsupported_request",
            data_safety=ErrorDataSafety(
                message_zh_tw=(
                    "這次沒有傳送內容、建立或套用候選；原稿、編輯器內容與已保存版本均未變更。"
                ),
                author_input_preserved=True,
                original_preserved=True,
                completed_work_preserved=True,
            ),
        )
        st.info(
            "世界靈感限 400 可見字元內；較長介紹請選「段落」或「場景」，"
            "設定最少 401、目標與最多皆不超過 1,000 可見字元的非建議式長度。"
            "長篇故事或劇本請使用對應工作區。"
        )
        return None
    if generation_path is WorldSeedGenerationPath.LONG_INTRODUCTION_PROSE:
        st.caption(
            "本次會先建立精簡世界結構，再以 raw prose 生成世界介紹／歷史敘事；"
            "長度只量世界介紹欄位，正常停止但不足仍會有界續寫。"
        )
    else:
        st.caption("本次為 400 可見字元內的精簡世界靈感，維持結構化候選。")
    try:
        ContextCompiler().compile(
            writing_request.context_spec,
            writing_request.resolved_sources,
        )
    except ContextCompilationError:
        st.error(
            "上下文預算不足，作者鎖定事實或必要來源無法安全納入；"
            "已停用 AI 候選操作，請提高預算或調整來源。"
        )
        return None
    return writing_request


def _render_ai_generation(services: Any) -> None:
    with st.expander("✦ AI 生成／精修候選世界", expanded=False):
        st.caption(
            "沿用頂部「狀態」中保存的全域 provider、模型與 fallback。只有按下按鈕才會執行，"
            "候選世界不會自動寫回編輯器或資料庫。"
        )
        generate_status, refine_status = render_passive_creative_runtime_status(
            services,
            task=GenerationTask.WORLD_SEED,
        )
        instruction = st.text_area(
            "這次想補什麼或怎麼調整？（選填）",
            key=f"{STATE_PREFIX}_ai_instruction",
            max_chars=4_000,
            height=90,
        )
        apply_mode = st.radio(
            "之後套用時",
            options=("rewrite", "fill"),
            format_func=lambda value: (
                "以候選世界更新欄位" if value == "rewrite" else "只補目前空白欄位"
            ),
            horizontal=True,
            key=f"{STATE_PREFIX}_ai_apply_mode",
        )
        writing_defaults = build_writing_request_defaults(
            WritingRequestSurface.WORLD_SEED,
            sources=_world_authoring_sources(),
            protected_facts=_world_protected_facts(),
        )
        writing_request = _validated_world_writing_request(
            render_writing_request_controls(
                writing_defaults,
                key_prefix=WORLD_WRITING_KEY_PREFIX,
            )
        )
        inputs = _world_authoring_base_inputs()
        editor_identity = str(st.session_state.get(SELECTED_DRAFT_STATE_KEY, _NEW_DRAFT))
        busy = render_candidate_action_state(STATE_PREFIX)
        actions = st.columns(2)
        generate = actions[0].button(
            "生成候選世界",
            key=f"{STATE_PREFIX}_ai_generate",
            type="primary",
            disabled=busy or not generate_status.ready or writing_request is None,
            width="stretch",
        )
        refine = actions[1].button(
            "精修目前世界",
            key=f"{STATE_PREFIX}_ai_refine",
            disabled=busy or not refine_status.ready or writing_request is None,
            width="stretch",
        )
        action = (
            CreativeCandidateAction.GENERATE
            if generate
            else CreativeCandidateAction.REFINE
            if refine
            else None
        )
        if action is not None and writing_request is not None:
            request_candidate(
                services,
                prefix=STATE_PREFIX,
                task=GenerationTask.WORLD_SEED,
                action=action,
                inputs=inputs,
                instruction=instruction,
                apply_mode=apply_mode,
                offline_candidate=_offline_ai_candidate,
                writing_request=writing_request,
                editor_identity=editor_identity,
            )
            st.rerun()

    render_candidate_error(STATE_PREFIX)
    decision = render_candidate_card(
        STATE_PREFIX,
        field_labels={
            "title": "候選標題",
            "setting": "候選世界／舞台",
            "time_period": "候選時代／時間",
            "world_rules": "候選世界規則",
            "locations": "候選地點",
            "social_context": "候選社會與制度",
            "technology_or_magic": "候選科技／魔法",
            "central_conflict": "候選核心衝突",
            "themes": "候選主題",
        },
        current_authoring_state_sha256=(
            authoring_editor_state_sha256(
                task=GenerationTask.WORLD_SEED,
                inputs=inputs,
                instruction=instruction,
                writing_request=writing_request,
                editor_identity=editor_identity,
            )
            if writing_request is not None
            else None
        ),
    )
    if decision is None:
        return
    choice, candidate, apply_mode = decision
    if choice == "discard":
        clear_candidate_state(STATE_PREFIX)
        st.rerun()
    mappings = {
        "title": TITLE_STATE_KEY,
        "setting": SETTING_STATE_KEY,
        "time_period": TIME_PERIOD_STATE_KEY,
        "world_rules": WORLD_RULES_STATE_KEY,
        "locations": LOCATIONS_STATE_KEY,
        "social_context": SOCIAL_CONTEXT_STATE_KEY,
        "technology_or_magic": TECHNOLOGY_OR_MAGIC_STATE_KEY,
        "central_conflict": CENTRAL_CONFLICT_STATE_KEY,
        "themes": THEMES_STATE_KEY,
    }
    pending: dict[str, object] = {}
    for field, state_key in mappings.items():
        value: object = candidate.get(field, "")
        if isinstance(value, list):
            value = _join_lines(tuple(str(item) for item in value))
        current = st.session_state.get(state_key, "")
        if field in _WORLD_AUTHOR_LOCKED_FIELDS and str(current).strip():
            continue
        if candidate_value_replaces_current(value, current, apply_mode=apply_mode):
            pending[state_key] = value
    st.session_state[PENDING_FORM_STATE_KEY] = pending
    st.session_state[INSPIRATION_NOTICE_STATE_KEY] = (
        "已把 AI 候選世界套用到編輯器；尚未正式保存，仍可逐欄修改。"
    )
    clear_candidate_state(STATE_PREFIX)
    st.rerun()


def _world_copy_values(target: str, candidate_text: str) -> dict[str, object]:
    values: dict[str, object] = {
        "project_id": str(st.session_state.get(PROJECT_STATE_KEY, "")).strip() or None,
        "title": str(st.session_state.get(TITLE_STATE_KEY, "")),
        "setting": str(st.session_state.get(SETTING_STATE_KEY, "")),
        "time_period": str(st.session_state.get(TIME_PERIOD_STATE_KEY, "")),
        "world_rules": _split_lines(str(st.session_state.get(WORLD_RULES_STATE_KEY, ""))),
        "locations": _split_lines(str(st.session_state.get(LOCATIONS_STATE_KEY, ""))),
        "social_context": str(st.session_state.get(SOCIAL_CONTEXT_STATE_KEY, "")),
        "technology_or_magic": str(st.session_state.get(TECHNOLOGY_OR_MAGIC_STATE_KEY, "")),
        "central_conflict": str(st.session_state.get(CENTRAL_CONFLICT_STATE_KEY, "")),
        "themes": _split_lines(str(st.session_state.get(THEMES_STATE_KEY, ""))),
        "generation_mode": WorldSeedGenerationMode.MANUAL.value,
        "generation_seed": "",
    }
    values["title"] = (
        f"{str(values['title']).strip()}（精修副本）"
        if str(values["title"]).strip()
        else "精修副本"
    )[:200]
    values[target] = (
        _split_lines(candidate_text)
        if target in {"world_rules", "locations", "themes"}
        else candidate_text
    )
    return values


def _render_content_refinement(services: Any) -> None:
    target = st.selectbox(
        "通用精修目標欄位",
        tuple(_REFINEMENT_FIELDS),
        format_func=lambda value: _REFINEMENT_FIELDS[value][0],
        key=f"{STATE_PREFIX}_refinement_target",
    )
    field_label, state_key = _REFINEMENT_FIELDS[target]
    prefix = f"{STATE_PREFIX}_content_{target}"
    original = str(st.session_state.get(state_key, ""))
    selected = st.session_state.get(SELECTED_DRAFT_STATE_KEY)
    binding = _durable_refinement_binding(
        services,
        selected=selected,
        field_name=target,
    )
    title = str(st.session_state.get(TITLE_STATE_KEY, "")).strip()
    time_period = str(st.session_state.get(TIME_PERIOD_STATE_KEY, "")).strip()
    protected_facts, protected_spans = protected_facts_and_spans(
        original,
        (title, time_period),
    )
    with st.expander("✦ 通用內容精修（10 種模式）", expanded=False):
        st.caption(f"目前目標：{field_label}。世界名稱與時代若出現在此欄，會作為鎖定事實逐字保護。")
        render_refinement_controls(
            services,
            prefix=prefix,
            artifact_kind=ArtifactKind.WORLD_SEED,
            original_text=original,
            context_snapshot={
                "locked_world_facts": {
                    "title": title,
                    "time_period": time_period,
                },
                "target_field": target,
                "world_rules": list(
                    _split_lines(str(st.session_state.get(WORLD_RULES_STATE_KEY, "")))
                ),
                "locations": list(_split_lines(str(st.session_state.get(LOCATIONS_STATE_KEY, "")))),
            },
            protected_facts=protected_facts,
            protected_spans=protected_spans,
            durable_binding=binding,
        )
    render_refinement_failure(prefix)
    durable_undo = render_durable_refinement_history(
        services,
        prefix=prefix,
        binding=binding,
    )
    if durable_undo is not None and durable_undo.after_snapshot is not None:
        restored_id = str(durable_undo.after_snapshot["id"])
        st.session_state[PENDING_SELECTION_STATE_KEY] = restored_id
        st.session_state[BOUND_DRAFT_STATE_KEY] = None
        st.session_state[FLASH_STATE_KEY] = "已建立復原版本；精修版本紀錄會在重新啟動後保留。"
        st.rerun()
    undo_text = render_refinement_undo(prefix, current_text=original)
    if undo_text is not None:
        st.session_state[PENDING_FORM_STATE_KEY] = {state_key: undo_text}
        st.session_state[INSPIRATION_NOTICE_STATE_KEY] = (
            f"已復原上次對「{field_label}」的精修；尚未正式保存。"
        )
        st.rerun()

    decision = render_refinement_candidate(prefix, field_name=target)
    if decision is None:
        return
    choice, candidate = decision
    try:
        durable_result = commit_durable_refinement_decision(
            services,
            prefix=prefix,
            decision=choice,
            candidate=candidate,
        )
    except (RuntimeError, TypeError, ValueError):
        set_refinement_failure(
            prefix,
            GenerationFailure(
                category=GenerationErrorCategory.IDEMPOTENCY_CONFLICT,
                safe_message="已保存世界草稿版本已變更；候選仍保留，沒有覆寫任何內容。",
            ),
        )
        st.rerun()
    if durable_result is not None:
        if choice is RefinementUIDecision.REJECT:
            clear_refinement_state(prefix)
            st.session_state[FLASH_STATE_KEY] = "已拒絕並保存候選決定；世界草稿沒有變更。"
            st.rerun()
        after = getattr(durable_result, "after_snapshot", None)
        if not isinstance(after, dict) or not isinstance(after.get("id"), str):
            set_refinement_failure(
                prefix,
                GenerationFailure(
                    category=GenerationErrorCategory.INVALID_RESPONSE,
                    safe_message="候選版本收據不完整；沒有覆寫任何內容。",
                ),
            )
            st.rerun()
        st.session_state[PENDING_SELECTION_STATE_KEY] = after["id"]
        st.session_state[BOUND_DRAFT_STATE_KEY] = None
        clear_refinement_state(prefix)
        st.session_state[FLASH_STATE_KEY] = (
            "精修候選已建立正式世界草稿版本與可復原收據。"
            if choice is not RefinementUIDecision.SAVE_AS_NEW
            else "精修候選已另存為新世界草稿，來源與版本關係已保存。"
        )
        st.rerun()
    if choice is RefinementUIDecision.REJECT:
        clear_refinement_state(prefix)
        st.session_state[INSPIRATION_NOTICE_STATE_KEY] = "已拒絕精修候選；所有世界欄位保持不變。"
        st.rerun()
    try:
        if choice is RefinementUIDecision.SAVE_AS_NEW:
            receipt = candidate_receipt_for_save_as_new(
                candidate,
                current_text=original,
                field_name=target,
            )
            services.world_seed_drafts.create_draft(
                **_world_copy_values(target, receipt.after_text)
            )
            clear_refinement_state(prefix)
            st.session_state[FLASH_STATE_KEY] = (
                "精修候選已另存為新世界種子；目前草稿與編輯器欄位均未覆寫。"
            )
            st.rerun()
        receipt = refinement_receipt(
            choice,
            candidate,
            current_text=original,
            field_name=target,
        )
    except (InvalidRefinementCandidateError, StaleRefinementBaseError, ApplicationError):
        set_refinement_failure(
            prefix,
            GenerationFailure(
                category=GenerationErrorCategory.IDEMPOTENCY_CONFLICT,
                safe_message="目前欄位已變更或候選無法安全套用。",
            ),
        )
        st.rerun()
    record_refinement_apply(prefix, receipt, candidate)
    st.session_state[PENDING_FORM_STATE_KEY] = {state_key: receipt.after_text}
    st.session_state[INSPIRATION_NOTICE_STATE_KEY] = (
        f"已把精修候選套用至「{field_label}」；尚未正式保存，可用工作階段復原。"
    )
    clear_refinement_state(prefix)
    st.rerun()


def render(*, services: Any | None = None) -> None:
    services = services or get_services()
    _restore_editor_state()
    page_header(
        PAGE_LABEL,
        "先自由寫世界，再決定要不要放進作品；內容不會自動加入任何一本書。",
        eyebrow="獨立世界設定紙頁",
        badges=(("本機正式草稿", "teal"), ("隨機只進編輯器", "amber")),
    )
    st.info(
        "手寫與本機隨機可以混用；本機按鈕永不連網。AI 只會在你按下生成／精修後依全域"
        "設定執行，結果先進候選區；不會自動保存或建立 World Bible。"
    )
    st.warning("此頁目前沒有 autosave；離開前請按『正式保存草稿』。")

    service = getattr(services, "world_seed_drafts", None)
    draft_service_available = service is not None
    if not draft_service_available:
        present_exception(
            RuntimeError("world seed draft service unavailable"),
            operation_zh_tw="使用世界種子草稿服務",
            key_prefix="world_seed_service",
            data_safety=ErrorDataSafety(
                message_zh_tw=(
                    "目前編輯器文字、草稿選擇與所有已保存版本均保留；沒有寫入或覆蓋內容。"
                ),
                author_input_preserved=True,
                original_preserved=True,
                draft_selection_preserved=True,
            ),
        )
    elif not _ensure_bound_editor(service):
        draft_service_available = False
    if not draft_service_available:
        for key, value in _FORM_DEFAULTS.items():
            st.session_state.setdefault(key, value)
        selected_before = st.session_state.get(SELECTED_DRAFT_STATE_KEY)
        st.session_state.setdefault(SELECTED_DRAFT_STATE_KEY, selected_before)
        st.session_state.setdefault(BASE_UPDATED_AT_STATE_KEY, None)
        st.session_state.setdefault(
            BOUND_DRAFT_STATE_KEY,
            selected_before if selected_before is not None else _NEW_DRAFT,
        )
        st.caption("正式草稿服務暫時不可用；目前編輯器仍可閱讀與修改，但保存、切換與封存已鎖定。")
    pending_form = st.session_state.pop(PENDING_FORM_STATE_KEY, {})
    if isinstance(pending_form, dict):
        for key, value in pending_form.items():
            st.session_state[str(key)] = value
        _capture_editor_state()
    flash = st.session_state.pop(FLASH_STATE_KEY, None)
    if flash:
        st.success(str(flash))
    inspiration_notice = st.session_state.pop(INSPIRATION_NOTICE_STATE_KEY, None)
    if inspiration_notice:
        st.info(str(inspiration_notice))

    selected_id = st.session_state.get(SELECTED_DRAFT_STATE_KEY)
    new_action, new_confirm = st.columns((2, 3), vertical_alignment="center")
    confirmed_new = new_confirm.checkbox(
        "我知道切換到新草稿會捨棄目前尚未保存的編輯器變更",
        key=NEW_DRAFT_CONFIRM_STATE_KEY,
        disabled=not draft_service_available or not bool(selected_id),
    )
    if new_action.button(
        "＋ 新世界草稿",
        key=f"{STATE_PREFIX}_new",
        disabled=not draft_service_available or not bool(selected_id) or not confirmed_new,
        width="stretch",
    ):
        st.session_state[PENDING_SELECTION_STATE_KEY] = _NEW_DRAFT
        st.session_state[BOUND_DRAFT_STATE_KEY] = None
        st.session_state[FLASH_STATE_KEY] = "已開啟一張空白世界紙頁；尚未寫入資料庫。"
        st.rerun()

    options, project_labels = _project_options(services)
    with st.container(border=True):
        st.markdown("#### 本機靈感填入")
        st.caption("可以先手寫幾格，再只補空白；若按全部重抽，尚未保存的編輯器內容會被取代。")
        actions = st.columns(2)
        fill_blanks = actions[0].button(
            "只補空白",
            key=f"{STATE_PREFIX}_fill_blanks",
            width="stretch",
        )
        reroll_all = actions[1].button(
            "全部重抽（取代編輯器）",
            key=f"{STATE_PREFIX}_reroll_all",
            width="stretch",
        )
        reroll_requested = bool(st.session_state.get(REROLL_CONFIRM_STATE_KEY, False))
        if reroll_all:
            st.session_state[REROLL_CONFIRM_STATE_KEY] = True
            reroll_requested = True
        reroll_confirmed = False
        generation_mode = str(st.session_state.get(GENERATION_MODE_STATE_KEY, "manual"))
        st.caption(f"目前內容起點：{_GENERATION_LABELS.get(generation_mode, '未知')}")

        st.text_input(
            "草稿標題（必填）", key=TITLE_STATE_KEY, max_chars=200,
            on_change=_capture_editor_state,
        )
        st.selectbox(
            "作品歸屬（可選）",
            options,
            format_func=lambda project_id: project_labels[project_id],
            key=PROJECT_STATE_KEY,
            on_change=_capture_editor_state,
        )
        first = st.columns(2)
        first[0].text_area(
            "世界／舞台概述",
            key=SETTING_STATE_KEY,
            on_change=_capture_editor_state,
            height=130,
            max_chars=1000,
        )
        first[1].text_input(
            "時代／時間背景", key=TIME_PERIOD_STATE_KEY, max_chars=200,
            on_change=_capture_editor_state,
        )
        lists = st.columns(2)
        lists[0].text_area(
            "世界規則（每行一項）",
            key=WORLD_RULES_STATE_KEY,
            on_change=_capture_editor_state,
            height=170,
        )
        lists[1].text_area(
            "地點（每行一項）",
            key=LOCATIONS_STATE_KEY,
            on_change=_capture_editor_state,
            height=170,
        )
        context = st.columns(2)
        context[0].text_area(
            "社會與制度",
            key=SOCIAL_CONTEXT_STATE_KEY,
            on_change=_capture_editor_state,
            height=130,
            max_chars=1000,
        )
        context[1].text_area(
            "科技／魔法與限制",
            key=TECHNOLOGY_OR_MAGIC_STATE_KEY,
            on_change=_capture_editor_state,
            height=130,
            max_chars=1000,
        )
        st.text_area(
            "核心衝突",
            key=CENTRAL_CONFLICT_STATE_KEY,
            on_change=_capture_editor_state,
            height=110,
            max_chars=1000,
        )
        st.text_area(
            "主題（每行一項）", key=THEMES_STATE_KEY, height=110,
            on_change=_capture_editor_state,
        )
        submitted = st.button(
            "正式保存草稿",
            key=f"{STATE_PREFIX}_save",
            type="primary",
            disabled=not draft_service_available,
            width="stretch",
        )
        if reroll_requested:
            st.warning("全部重抽會取代目前尚未保存的世界設定欄位；正式草稿不會被寫入或刪除。")
            confirm_reroll, cancel_reroll = st.columns(2)
            reroll_confirmed = confirm_reroll.button(
                "確認全部重抽",
                key=f"{STATE_PREFIX}_reroll_all_confirm",
                type="secondary",
                width="stretch",
            )
            if cancel_reroll.button(
                "保留目前內容",
                key=f"{STATE_PREFIX}_reroll_all_cancel",
                width="stretch",
            ):
                st.session_state.pop(REROLL_CONFIRM_STATE_KEY, None)
                st.rerun()
    if fill_blanks:
        st.session_state.pop(REROLL_CONFIRM_STATE_KEY, None)
        _apply_local_inspiration(fill_blanks_only=True)
        st.rerun()
    if reroll_confirmed:
        st.session_state.pop(REROLL_CONFIRM_STATE_KEY, None)
        _apply_local_inspiration(fill_blanks_only=False)
        st.rerun()
    if submitted:
        _save(services)

    _render_ai_generation(services)
    if draft_service_available:
        _render_content_refinement(services)
    else:
        st.caption("正式草稿服務恢復後，才會重新開放通用內容精修與版本收據。")

    selected_id = st.session_state.get(SELECTED_DRAFT_STATE_KEY)
    if draft_service_available and service is not None and selected_id:
        with st.expander("保存、匯出與封存"):
            st.caption("JSON 備份不包含 API Key，可保存到你指定的位置。")
            try:
                export_json = service.export_draft_json(str(selected_id))
            except ApplicationError:
                st.warning("目前無法重新讀取這份草稿，因此暫不提供匯出。")
            else:
                st.download_button(
                    "下載 JSON 備份",
                    data=export_json,
                    file_name=f"world-seed-{selected_id}.json",
                    mime="application/json",
                    key=f"{STATE_PREFIX}_download",
                )
            confirmed = st.checkbox(
                "我確認要把這份草稿收進封存",
                key=f"{STATE_PREFIX}_archive_confirm",
            )
            if st.button(
                "封存草稿（不刪除）",
                key=f"{STATE_PREFIX}_archive",
                disabled=not confirmed,
            ):
                _archive(services, str(selected_id))

    archived_drafts: tuple[WorldSeedDraft, ...] = ()
    if draft_service_available and service is not None:
        try:
            archived_drafts = tuple(
                draft
                for draft in service.list_drafts(include_archived=True)
                if draft.status.value == "archived"
            )
        except (ApplicationError, RuntimeError, TypeError, ValueError) as exc:
            present_exception(
                exc,
                operation_zh_tw="讀取已封存世界種子草稿",
                key_prefix="world_seed_archived_list",
                data_safety=ErrorDataSafety(
                    message_zh_tw=(
                        "目前編輯器文字、草稿選擇與所有正式草稿均保留；沒有寫入、封存或覆蓋內容。"
                    ),
                    author_input_preserved=True,
                    original_preserved=True,
                    draft_selection_preserved=True,
                ),
            )
    if archived_drafts and service is not None:
        with st.expander(f"已封存的世界草稿（唯讀） · {len(archived_drafts)}"):
            st.caption("封存不會刪除內容；目前可在此讀回與下載，尚不提供直接還原為可編輯狀態。")
            for draft in archived_drafts:
                st.markdown(f"**{draft.title}**")
                st.caption(f"草稿 `{draft.id}` · 最後更新 `{draft.updated_at}`")
                try:
                    archived_json = service.export_draft_json(draft.id)
                except ApplicationError:
                    st.warning(f"暫時無法讀取 `{draft.id}` 的 JSON 備份。")
                else:
                    st.download_button(
                        f"下載 {draft.title} 的 JSON 備份",
                        data=archived_json,
                        file_name=f"world-seed-{draft.id}.json",
                        mime="application/json",
                        key=f"{STATE_PREFIX}_archived_download_{draft.id}",
                    )
    _capture_editor_state()


__all__ = [
    "BOUND_DRAFT_STATE_KEY",
    "NEW_DRAFT_CONFIRM_STATE_KEY",
    "PAGE_KEY",
    "PAGE_LABEL",
    "PENDING_SELECTION_STATE_KEY",
    "SELECTED_DRAFT_STATE_KEY",
    "render",
]
