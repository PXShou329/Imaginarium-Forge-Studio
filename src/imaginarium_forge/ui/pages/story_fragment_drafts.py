"""Standalone editor for durable, project-optional story fragment drafts."""

from __future__ import annotations

import re
from typing import Any

import streamlit as st

from imaginarium_forge.application.errors import ApplicationError
from imaginarium_forge.application.services.story_fragment_inspiration_service import (
    StoryFragmentEditorContent,
    StoryFragmentInspirationService,
)
from imaginarium_forge.domain.story.fragment_draft import (
    MAX_STORY_FRAGMENT_CHARS,
    MAX_STORY_FRAGMENT_TAGS,
    StoryFragmentDraft,
    StoryFragmentGenerationMode,
    StoryFragmentKind,
)
from imaginarium_forge.ui.bootstrap import get_services
from imaginarium_forge.ui.components import page_header

PAGE_KEY = "Story Fragment Drafts"
PAGE_LABEL = "故事片段草稿"

STATE_PREFIX = "story_fragment_draft"
PENDING_SELECTION_STATE_KEY = f"{STATE_PREFIX}_pending_selection"
BOUND_DRAFT_STATE_KEY = f"{STATE_PREFIX}_bound_draft"
SELECTED_DRAFT_STATE_KEY = f"{STATE_PREFIX}_selected_draft"
BASE_UPDATED_AT_STATE_KEY = f"{STATE_PREFIX}_base_updated_at"
FLASH_STATE_KEY = f"{STATE_PREFIX}_flash"
PENDING_FORM_STATE_KEY = f"{STATE_PREFIX}_pending_form"
INSPIRATION_NOTICE_STATE_KEY = f"{STATE_PREFIX}_inspiration_notice"
ARCHIVE_CONFIRM_STATE_KEY = f"{STATE_PREFIX}_archive_confirm"

PROJECT_STATE_KEY = f"{STATE_PREFIX}_project_id"
TITLE_STATE_KEY = f"{STATE_PREFIX}_title"
KIND_STATE_KEY = f"{STATE_PREFIX}_kind"
TEXT_STATE_KEY = f"{STATE_PREFIX}_text"
CONTEXT_STATE_KEY = f"{STATE_PREFIX}_context_notes"
TAGS_STATE_KEY = f"{STATE_PREFIX}_tags"
GENERATION_MODE_STATE_KEY = f"{STATE_PREFIX}_generation_mode"
GENERATION_SEED_STATE_KEY = f"{STATE_PREFIX}_generation_seed"

_NEW_DRAFT = "__new__"
_FORM_DEFAULTS: dict[str, object] = {
    PROJECT_STATE_KEY: "",
    TITLE_STATE_KEY: "",
    KIND_STATE_KEY: StoryFragmentKind.NARRATIVE,
    TEXT_STATE_KEY: "",
    CONTEXT_STATE_KEY: "",
    TAGS_STATE_KEY: "",
    GENERATION_MODE_STATE_KEY: StoryFragmentGenerationMode.MANUAL.value,
    GENERATION_SEED_STATE_KEY: "",
}
_KIND_LABELS = {
    StoryFragmentKind.NARRATIVE: "敘事片段",
    StoryFragmentKind.DIALOGUE: "對白片段",
    StoryFragmentKind.OPENING: "故事開場",
}
_GENERATION_LABELS = {
    StoryFragmentGenerationMode.MANUAL.value: "手寫",
    StoryFragmentGenerationMode.FILL_BLANKS.value: "本機靈感只補空白",
    StoryFragmentGenerationMode.REROLL_ALL.value: "本機靈感全部重抽",
}


def _join_tags(tags: tuple[str, ...]) -> str:
    return "、".join(tags)


def _split_tags(value: str) -> tuple[str, ...]:
    tags: list[str] = []
    seen: set[str] = set()
    for raw in re.split(r"[,，、\n]+", value):
        normalized = " ".join(raw.strip().split())
        if normalized and normalized not in seen:
            seen.add(normalized)
            tags.append(normalized)
    return tuple(tags)


def _kind(value: object) -> StoryFragmentKind:
    if isinstance(value, StoryFragmentKind):
        return value
    return StoryFragmentKind(str(value))


def _draft_form(draft: StoryFragmentDraft) -> dict[str, object]:
    return {
        PROJECT_STATE_KEY: draft.project_id or "",
        TITLE_STATE_KEY: draft.title,
        KIND_STATE_KEY: draft.fragment_kind,
        TEXT_STATE_KEY: draft.fragment_text,
        CONTEXT_STATE_KEY: draft.context_notes,
        TAGS_STATE_KEY: _join_tags(draft.tags),
        GENERATION_MODE_STATE_KEY: draft.generation_mode.value,
        GENERATION_SEED_STATE_KEY: draft.generation_seed,
    }


def _write_form(values: dict[str, object]) -> None:
    for key, value in values.items():
        st.session_state[key] = value


def _bind_new() -> None:
    _write_form(_FORM_DEFAULTS)
    st.session_state[BASE_UPDATED_AT_STATE_KEY] = None
    st.session_state[BOUND_DRAFT_STATE_KEY] = _NEW_DRAFT
    st.session_state[ARCHIVE_CONFIRM_STATE_KEY] = False


def _bind_draft(draft: StoryFragmentDraft) -> None:
    _write_form(_draft_form(draft))
    st.session_state[BASE_UPDATED_AT_STATE_KEY] = draft.updated_at
    st.session_state[BOUND_DRAFT_STATE_KEY] = draft.id
    st.session_state[ARCHIVE_CONFIRM_STATE_KEY] = False


def _apply_pending_form() -> None:
    pending = st.session_state.pop(PENDING_FORM_STATE_KEY, None)
    if isinstance(pending, dict):
        _write_form({str(key): value for key, value in pending.items()})


def _project_options(services: Any) -> tuple[list[str], dict[str, str]]:
    projects = services.projects.list_projects(include_archived=True)
    options = ["", *(project.id for project in projects)]
    labels = {"": "不綁定作品（留在自由創作箱）"}
    labels.update(
        {
            project.id: (
                project.name
                if project.status.value == "active"
                else f"{project.name}（已收起的作品）"
            )
            for project in projects
        }
    )
    return options, labels


def _save(services: Any, selected: str) -> None:
    values = {
        "project_id": str(st.session_state.get(PROJECT_STATE_KEY, "")).strip()
        or None,
        "title": str(st.session_state.get(TITLE_STATE_KEY, "")),
        "fragment_kind": _kind(st.session_state.get(KIND_STATE_KEY)),
        "fragment_text": str(st.session_state.get(TEXT_STATE_KEY, "")),
        "context_notes": str(st.session_state.get(CONTEXT_STATE_KEY, "")),
        "tags": _split_tags(str(st.session_state.get(TAGS_STATE_KEY, ""))),
        "generation_mode": str(
            st.session_state.get(
                GENERATION_MODE_STATE_KEY,
                StoryFragmentGenerationMode.MANUAL.value,
            )
        ),
        "generation_seed": str(
            st.session_state.get(GENERATION_SEED_STATE_KEY, "")
        ),
    }
    try:
        if selected == _NEW_DRAFT:
            draft = services.story_fragment_drafts.create_draft(**values)
            message = "故事片段已正式收進自由創作箱。"
        else:
            draft = services.story_fragment_drafts.update_draft(
                selected,
                **values,
                expected_updated_at=st.session_state.get(BASE_UPDATED_AT_STATE_KEY),
            )
            message = "故事片段已保存並重新讀取。"
    except ApplicationError as exc:
        st.error(str(exc))
        return
    st.session_state[PENDING_SELECTION_STATE_KEY] = draft.id
    st.session_state[BOUND_DRAFT_STATE_KEY] = None
    st.session_state[FLASH_STATE_KEY] = message
    st.rerun()


def _archive(services: Any, draft_id: str) -> None:
    try:
        services.story_fragment_drafts.archive_draft(
            draft_id,
            expected_updated_at=st.session_state.get(BASE_UPDATED_AT_STATE_KEY),
        )
    except ApplicationError as exc:
        st.error(str(exc))
        return
    st.session_state[PENDING_SELECTION_STATE_KEY] = _NEW_DRAFT
    st.session_state[BOUND_DRAFT_STATE_KEY] = None
    st.session_state[FLASH_STATE_KEY] = "故事片段已收起；內容仍保留在本機資料庫。"
    st.rerun()


def _reject_inspiration(message: str) -> None:
    """Fail closed without replacing any editor or provenance values."""

    st.session_state.pop(PENDING_FORM_STATE_KEY, None)
    st.session_state[INSPIRATION_NOTICE_STATE_KEY] = (
        f"{message} 編輯器與保存內容都沒有變更。"
    )


def _apply_inspiration(*, fill_blanks_only: bool) -> None:
    title = str(st.session_state.get(TITLE_STATE_KEY, ""))
    fragment_text = str(st.session_state.get(TEXT_STATE_KEY, ""))
    context_notes = str(st.session_state.get(CONTEXT_STATE_KEY, ""))
    tags = _split_tags(str(st.session_state.get(TAGS_STATE_KEY, "")))
    if len(title) > 200:
        _reject_inspiration("標題最多 200 個字元；請先縮短再使用本機靈感。")
        return
    if len(fragment_text) > MAX_STORY_FRAGMENT_CHARS:
        _reject_inspiration("故事片段正文最多 500,000 個字元；請先縮短再使用本機靈感。")
        return
    if len(context_notes) > 4_000:
        _reject_inspiration("上下文／延伸備註最多 4,000 個字元；請先縮短再使用本機靈感。")
        return
    if len(tags) > MAX_STORY_FRAGMENT_TAGS:
        _reject_inspiration("故事片段最多 24 個不同標籤；請先減少再使用本機靈感。")
        return
    if any(len(tag) > 200 for tag in tags):
        _reject_inspiration("每個故事片段標籤最多 200 個字元；請先縮短再使用本機靈感。")
        return
    mode = (
        StoryFragmentGenerationMode.FILL_BLANKS
        if fill_blanks_only
        else StoryFragmentGenerationMode.REROLL_ALL
    )
    try:
        current = StoryFragmentEditorContent(
            title=title,
            fragment_text=fragment_text,
            context_notes=context_notes,
            tags=tags,
        )
        prepared = StoryFragmentInspirationService.prepare_editor(
            current=current,
            fragment_kind=_kind(st.session_state.get(KIND_STATE_KEY)),
            mode=mode,
        )
    except ValueError:
        _reject_inspiration(
            "目前編輯器內容不符合故事片段限制；請檢查標題、正文、備註與標籤。"
        )
        return
    if prepared is None:
        st.session_state.pop(PENDING_FORM_STATE_KEY, None)
        st.session_state[INSPIRATION_NOTICE_STATE_KEY] = (
            "目前沒有可補的空白欄位；編輯器與保存內容都沒有變更。"
        )
        return
    st.session_state[PENDING_FORM_STATE_KEY] = {
        TITLE_STATE_KEY: prepared.content.title,
        TEXT_STATE_KEY: prepared.content.fragment_text,
        CONTEXT_STATE_KEY: prepared.content.context_notes,
        TAGS_STATE_KEY: _join_tags(prepared.content.tags),
        GENERATION_MODE_STATE_KEY: prepared.generation_mode.value,
        GENERATION_SEED_STATE_KEY: prepared.generation_seed,
    }
    changed = "、".join(field.value for field in prepared.changed_fields)
    st.session_state[INSPIRATION_NOTICE_STATE_KEY] = (
        f"本機靈感已填入編輯器（{changed}）；尚未保存，仍可自由改寫。"
    )


def _render_archived(service: Any) -> None:
    archived = tuple(
        draft
        for draft in service.list_drafts(include_archived=True)
        if draft.status.value == "archived"
    )
    if not archived:
        return
    with st.expander(f"已收起的故事片段（唯讀） · {len(archived)}"):
        st.caption("收起不等於刪除；可在這裡下載保存當下的 JSON 備份。")
        for draft in archived:
            st.markdown(f"**{draft.title}** · {_KIND_LABELS[draft.fragment_kind]}")
            st.caption(f"草稿 `{draft.id}` · 最後更新 `{draft.updated_at}`")
            try:
                payload = service.export_draft_json(draft.id)
            except ApplicationError:
                st.warning(f"暫時無法讀取 `{draft.id}` 的 JSON。")
            else:
                st.download_button(
                    f"下載《{draft.title}》JSON",
                    data=payload,
                    file_name=f"story-fragment-{draft.id}.json",
                    mime="application/json",
                    key=f"{STATE_PREFIX}_archived_download_{draft.id}",
                )


def render(*, services: Any | None = None) -> None:
    """Render one local-first fragment editor without creating Story state."""

    services = services or get_services()
    page_header(
        PAGE_LABEL,
        "先寫一小段敘事、對白或開場，不必先建立作品；喜歡後再決定要不要歸到某本書。",
        eyebrow="一張獨立的故事紙頁",
        badges=(("不用開作品", "teal"), ("靈感只填編輯器", "amber")),
    )
    st.info(
        "手寫與本機靈感可以混用。這裡不會呼叫 AI、自動保存或自動轉入作品；只有按下正式保存，"
        "才會寫入故事片段草稿。"
    )

    service = getattr(services, "story_fragment_drafts", None)
    if service is None:
        st.error("故事片段草稿服務尚未就緒；沒有任何內容被寫入。")
        return

    _apply_pending_form()
    pending_selection = st.session_state.pop(PENDING_SELECTION_STATE_KEY, None)
    if pending_selection is not None:
        st.session_state[SELECTED_DRAFT_STATE_KEY] = str(pending_selection)

    flash = st.session_state.pop(FLASH_STATE_KEY, None)
    if flash:
        st.success(str(flash))
    notice = st.session_state.pop(INSPIRATION_NOTICE_STATE_KEY, None)
    if notice:
        st.info(str(notice))

    inspiration_slot = st.container(border=True)
    project_options, project_labels = _project_options(services)
    try:
        active_drafts = service.list_drafts(include_archived=False)
    except ApplicationError as exc:
        st.error(str(exc))
        return
    by_id = {draft.id: draft for draft in active_drafts}
    options = [_NEW_DRAFT, *by_id]
    selected_before = st.session_state.get(SELECTED_DRAFT_STATE_KEY)
    if selected_before not in options:
        st.session_state[SELECTED_DRAFT_STATE_KEY] = _NEW_DRAFT

    selector, new_action = st.columns((4, 1), vertical_alignment="bottom")
    selected = selector.selectbox(
        "翻開哪張故事紙頁？",
        options,
        format_func=lambda value: (
            "＋ 一張新的故事片段"
            if value == _NEW_DRAFT
            else (
                f"{by_id[value].title} · {_KIND_LABELS[by_id[value].fragment_kind]} · "
                f"{project_labels.get(by_id[value].project_id or '', '作品歸屬待確認')}"
            )
        ),
        key=SELECTED_DRAFT_STATE_KEY,
    )
    if new_action.button(
        "新紙頁",
        key=f"{STATE_PREFIX}_new",
        disabled=selected == _NEW_DRAFT,
        use_container_width=True,
    ):
        st.session_state[PENDING_SELECTION_STATE_KEY] = _NEW_DRAFT
        st.session_state[BOUND_DRAFT_STATE_KEY] = None
        st.session_state[FLASH_STATE_KEY] = "已翻開空白故事紙頁；尚未寫入資料庫。"
        st.rerun()

    bound = st.session_state.get(BOUND_DRAFT_STATE_KEY)
    if selected != bound:
        if selected == _NEW_DRAFT:
            _bind_new()
        else:
            try:
                _bind_draft(service.get_draft(selected))
            except ApplicationError as exc:
                st.error(str(exc))
                _bind_new()
                st.session_state[SELECTED_DRAFT_STATE_KEY] = _NEW_DRAFT
                selected = _NEW_DRAFT

    st.caption("切換紙頁前請先保存；未保存的編輯器內容不會自動寫入資料庫。")
    identity = st.columns((3, 2))
    identity[0].text_input(
        "片段標題（必填）",
        key=TITLE_STATE_KEY,
        max_chars=200,
        placeholder="例如：雨夜月台的第二封信",
    )
    identity[1].selectbox(
        "作品歸屬（可選）",
        project_options,
        format_func=lambda project_id: project_labels[project_id],
        key=PROJECT_STATE_KEY,
    )
    st.radio(
        "這張紙頁想寫什麼？",
        tuple(StoryFragmentKind),
        format_func=lambda value: _KIND_LABELS[value],
        horizontal=True,
        key=KIND_STATE_KEY,
    )
    st.text_area(
        "故事正文（必填）",
        key=TEXT_STATE_KEY,
        height=340,
        placeholder="可以只寫一個畫面、一段對話，或故事的第一頁……",
    )
    details = st.columns((3, 2))
    details[0].text_area(
        "上下文／延伸備註（選填）",
        key=CONTEXT_STATE_KEY,
        height=130,
        max_chars=4_000,
        placeholder="角色關係、前因後果、下一步想寫的方向……",
    )
    details[1].text_area(
        "標籤（選填，以逗號或換行分隔）",
        key=TAGS_STATE_KEY,
        height=130,
        placeholder="懸疑、雨夜、重逢",
    )

    with inspiration_slot:
        st.markdown("#### 本機故事靈感")
        st.caption("只改目前編輯器，不會自動保存，也不會加入作品或建立正式故事版本。")
        inspiration_actions = st.columns(2)
        fill_blanks = inspiration_actions[0].button(
            "✦ 只補空白",
            key=f"{STATE_PREFIX}_fill_blanks",
            type="primary",
            use_container_width=True,
        )
        reroll_all = inspiration_actions[1].button(
            "全部重抽（取代編輯器）",
            key=f"{STATE_PREFIX}_reroll_all",
            use_container_width=True,
        )
        generation_mode = str(
            st.session_state.get(
                GENERATION_MODE_STATE_KEY,
                StoryFragmentGenerationMode.MANUAL.value,
            )
        )
        st.caption(f"目前內容起點：{_GENERATION_LABELS.get(generation_mode, '未知')}")
    if fill_blanks:
        _apply_inspiration(fill_blanks_only=True)
        st.rerun()
    if reroll_all:
        _apply_inspiration(fill_blanks_only=False)
        st.rerun()

    save_column, archive_column = st.columns((3, 2))
    if save_column.button(
        "正式保存故事片段" if selected == _NEW_DRAFT else "保存這張故事紙頁",
        key=f"{STATE_PREFIX}_save",
        type="primary",
        use_container_width=True,
    ):
        _save(services, selected)

    if selected != _NEW_DRAFT:
        confirmed = archive_column.checkbox(
            "我確認要把這張紙頁收起來",
            key=ARCHIVE_CONFIRM_STATE_KEY,
        )
        if archive_column.button(
            "收起故事片段（不刪除）",
            key=f"{STATE_PREFIX}_archive",
            disabled=not confirmed,
            use_container_width=True,
        ):
            _archive(services, selected)

        st.markdown("#### 帶走已保存內容")
        try:
            export_json = service.export_draft_json(selected)
        except ApplicationError:
            st.warning("目前無法重新讀取這份草稿，因此暫不提供匯出。")
        else:
            st.download_button(
                "下載 JSON 備份",
                data=export_json,
                file_name=f"story-fragment-{selected}.json",
                mime="application/json",
                key=f"{STATE_PREFIX}_download",
            )

    _render_archived(service)
    st.caption(
        "這份獨立草稿不會自動加入任何作品。要放進作品時，"
        "會先讓你選擇目標作品並確認內容。"
    )


__all__ = [
    "BASE_UPDATED_AT_STATE_KEY",
    "BOUND_DRAFT_STATE_KEY",
    "PAGE_KEY",
    "PAGE_LABEL",
    "PENDING_SELECTION_STATE_KEY",
    "SELECTED_DRAFT_STATE_KEY",
    "render",
]
