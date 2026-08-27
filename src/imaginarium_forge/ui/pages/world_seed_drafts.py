"""Standalone editor for durable, project-optional world seed drafts."""

from __future__ import annotations

from typing import Any

import streamlit as st

from imaginarium_forge.application.errors import ApplicationError
from imaginarium_forge.application.services.creative_inspiration_service import (
    CreativeInspirationService,
)
from imaginarium_forge.domain.common.ids import new_id
from imaginarium_forge.domain.creative.world_seed_draft import (
    WorldSeedDraft,
    WorldSeedGenerationMode,
)
from imaginarium_forge.ui.bootstrap import get_services
from imaginarium_forge.ui.components import page_header

PAGE_KEY = "World Seed Drafts"
PAGE_LABEL = "世界種子草稿"

STATE_PREFIX = "world_seed_draft"
PENDING_SELECTION_STATE_KEY = f"{STATE_PREFIX}_pending_selection"
BOUND_DRAFT_STATE_KEY = f"{STATE_PREFIX}_bound_draft"
SELECTED_DRAFT_STATE_KEY = f"{STATE_PREFIX}_selected_draft"
BASE_UPDATED_AT_STATE_KEY = f"{STATE_PREFIX}_base_updated_at"
FLASH_STATE_KEY = f"{STATE_PREFIX}_flash"
PENDING_FORM_STATE_KEY = f"{STATE_PREFIX}_pending_form"
INSPIRATION_NOTICE_STATE_KEY = f"{STATE_PREFIX}_inspiration_notice"
NEW_DRAFT_CONFIRM_STATE_KEY = f"{STATE_PREFIX}_new_confirm"

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

_GENERATION_LABELS = {
    WorldSeedGenerationMode.MANUAL.value: "手寫",
    WorldSeedGenerationMode.FILL_BLANKS.value: "本機隨機只補空白",
    WorldSeedGenerationMode.REROLL_ALL.value: "本機隨機全部重抽",
}


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


def _bind_new() -> None:
    for key, value in _FORM_DEFAULTS.items():
        st.session_state[key] = value
    st.session_state[SELECTED_DRAFT_STATE_KEY] = None
    st.session_state[BASE_UPDATED_AT_STATE_KEY] = None
    st.session_state[BOUND_DRAFT_STATE_KEY] = _NEW_DRAFT
    st.session_state.pop(NEW_DRAFT_CONFIRM_STATE_KEY, None)


def _bind_draft(draft: WorldSeedDraft) -> None:
    for key, value in _draft_form(draft).items():
        st.session_state[key] = value
    st.session_state[SELECTED_DRAFT_STATE_KEY] = draft.id
    st.session_state[BASE_UPDATED_AT_STATE_KEY] = draft.updated_at
    st.session_state[BOUND_DRAFT_STATE_KEY] = draft.id


def _ensure_bound_editor(service: Any) -> bool:
    pending = st.session_state.pop(PENDING_SELECTION_STATE_KEY, None)
    if pending is not None:
        if pending == _NEW_DRAFT:
            _bind_new()
            return True
        try:
            _bind_draft(service.get_draft(str(pending)))
        except ApplicationError:
            st.error("指定的世界種子草稿目前無法讀取；沒有修改任何內容。")
            _bind_new()
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
        "world_rules": _split_lines(
            str(st.session_state.get(WORLD_RULES_STATE_KEY, ""))
        ),
        "locations": _split_lines(str(st.session_state.get(LOCATIONS_STATE_KEY, ""))),
        "social_context": str(st.session_state.get(SOCIAL_CONTEXT_STATE_KEY, "")),
        "technology_or_magic": str(
            st.session_state.get(TECHNOLOGY_OR_MAGIC_STATE_KEY, "")
        ),
        "central_conflict": str(
            st.session_state.get(CENTRAL_CONFLICT_STATE_KEY, "")
        ),
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
        st.error(str(exc))
        return
    st.session_state[PENDING_SELECTION_STATE_KEY] = draft.id
    st.session_state[BOUND_DRAFT_STATE_KEY] = None
    st.session_state[FLASH_STATE_KEY] = message
    st.rerun()


def _archive(services: Any, draft_id: str) -> None:
    try:
        services.world_seed_drafts.archive_draft(draft_id)
    except ApplicationError as exc:
        st.error(str(exc))
        return
    st.session_state[PENDING_SELECTION_STATE_KEY] = _NEW_DRAFT
    st.session_state[BOUND_DRAFT_STATE_KEY] = None
    st.session_state[FLASH_STATE_KEY] = "世界種子草稿已封存；內容仍保留在本機資料庫。"
    st.rerun()


def render(*, services: Any | None = None) -> None:
    services = services or get_services()
    page_header(
        PAGE_LABEL,
        "先自由寫世界，再決定要不要放進作品；內容不會自動加入任何一本書。",
        eyebrow="獨立世界設定紙頁",
        badges=(("本機正式草稿", "teal"), ("隨機只進編輯器", "amber")),
    )
    st.info(
        "手寫與本機隨機可以混用。『只補空白』不碰已有文字；『全部重抽』只取代目前"
        "編輯器。兩者都不會自動保存、不會呼叫 Ollama／OpenAI，也不會建立 World Bible。"
    )
    st.warning("此頁目前沒有 autosave；離開前請按『正式保存草稿』。")

    service = getattr(services, "world_seed_drafts", None)
    if service is None:
        st.error("世界種子草稿服務尚未就緒；沒有任何內容被寫入。")
        return
    _ensure_bound_editor(service)
    pending_form = st.session_state.pop(PENDING_FORM_STATE_KEY, {})
    if isinstance(pending_form, dict):
        for key, value in pending_form.items():
            st.session_state[str(key)] = value
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
        disabled=not bool(selected_id),
    )
    if new_action.button(
        "＋ 新世界草稿",
        key=f"{STATE_PREFIX}_new",
        disabled=not bool(selected_id) or not confirmed_new,
        use_container_width=True,
    ):
        st.session_state[PENDING_SELECTION_STATE_KEY] = _NEW_DRAFT
        st.session_state[BOUND_DRAFT_STATE_KEY] = None
        st.session_state[FLASH_STATE_KEY] = "已開啟一張空白世界紙頁；尚未寫入資料庫。"
        st.rerun()

    options, project_labels = _project_options(services)
    with st.form(f"{STATE_PREFIX}_editor_form", border=True):
        st.markdown("#### 本機靈感填入")
        st.caption("可以先手寫幾格，再只補空白；若按全部重抽，尚未保存的編輯器內容會被取代。")
        actions = st.columns(2)
        fill_blanks = actions[0].form_submit_button(
            "只補空白",
            key=f"{STATE_PREFIX}_fill_blanks",
            type="primary",
            use_container_width=True,
        )
        reroll_all = actions[1].form_submit_button(
            "全部重抽（取代編輯器）",
            key=f"{STATE_PREFIX}_reroll_all",
            use_container_width=True,
        )
        generation_mode = str(st.session_state.get(GENERATION_MODE_STATE_KEY, "manual"))
        st.caption(f"目前內容起點：{_GENERATION_LABELS.get(generation_mode, '未知')}")

        st.text_input("草稿標題（必填）", key=TITLE_STATE_KEY, max_chars=200)
        st.selectbox(
            "作品歸屬（可選）",
            options,
            format_func=lambda project_id: project_labels[project_id],
            key=PROJECT_STATE_KEY,
        )
        first = st.columns(2)
        first[0].text_area(
            "世界／舞台概述",
            key=SETTING_STATE_KEY,
            height=130,
            max_chars=1000,
        )
        first[1].text_input("時代／時間背景", key=TIME_PERIOD_STATE_KEY, max_chars=200)
        lists = st.columns(2)
        lists[0].text_area(
            "世界規則（每行一項）",
            key=WORLD_RULES_STATE_KEY,
            height=170,
        )
        lists[1].text_area(
            "地點（每行一項）",
            key=LOCATIONS_STATE_KEY,
            height=170,
        )
        context = st.columns(2)
        context[0].text_area(
            "社會與制度",
            key=SOCIAL_CONTEXT_STATE_KEY,
            height=130,
            max_chars=1000,
        )
        context[1].text_area(
            "科技／魔法與限制",
            key=TECHNOLOGY_OR_MAGIC_STATE_KEY,
            height=130,
            max_chars=1000,
        )
        st.text_area(
            "核心衝突",
            key=CENTRAL_CONFLICT_STATE_KEY,
            height=110,
            max_chars=1000,
        )
        st.text_area("主題（每行一項）", key=THEMES_STATE_KEY, height=110)
        submitted = st.form_submit_button(
            "正式保存草稿",
            key=f"{STATE_PREFIX}_save",
            type="primary",
            use_container_width=True,
        )
    if fill_blanks:
        _apply_local_inspiration(fill_blanks_only=True)
        st.rerun()
    if reroll_all:
        _apply_local_inspiration(fill_blanks_only=False)
        st.rerun()
    if submitted:
        _save(services)

    selected_id = st.session_state.get(SELECTED_DRAFT_STATE_KEY)
    if selected_id:
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

    archived_drafts = tuple(
        draft
        for draft in service.list_drafts(include_archived=True)
        if draft.status.value == "archived"
    )
    if archived_drafts:
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


__all__ = [
    "BOUND_DRAFT_STATE_KEY",
    "NEW_DRAFT_CONFIRM_STATE_KEY",
    "PAGE_KEY",
    "PAGE_LABEL",
    "PENDING_SELECTION_STATE_KEY",
    "SELECTED_DRAFT_STATE_KEY",
    "render",
]
