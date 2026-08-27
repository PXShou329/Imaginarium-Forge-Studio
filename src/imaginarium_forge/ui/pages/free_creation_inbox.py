"""Unified index for durable drafts that are not bound to a Project."""

from __future__ import annotations

from typing import Any, NoReturn

import streamlit as st

from imaginarium_forge.application.errors import ApplicationError
from imaginarium_forge.application.services.free_creation_inbox_service import (
    FreeCreationInboxItem,
    FreeCreationItemKind,
    FreeCreationSource,
)
from imaginarium_forge.ui.bootstrap import get_services
from imaginarium_forge.ui.components import page_header
from imaginarium_forge.ui.pages import (
    character_drafts,
    prompt_scratch,
    story_fragment_drafts,
    world_seed_drafts,
)
from imaginarium_forge.ui.pages.prompt_tag_builder import (
    PAGE_KEY as PROMPT_TAG_BUILDER_PAGE_KEY,
)

PAGE_KEY = "Free Creation Inbox"
PAGE_LABEL = "自由創作箱"

STATE_PREFIX = "free_creation_inbox"
SEARCH_STATE_KEY = f"{STATE_PREFIX}_search"
FILTER_STATE_KEY = f"{STATE_PREFIX}_filter"
PAGE_STATE_KEY = f"{STATE_PREFIX}_page"
_NEW_DRAFT = "__new__"

_FILTER_LABELS = {
    "all": "全部",
    FreeCreationItemKind.STORY_FRAGMENT.value: "故事片段",
    FreeCreationItemKind.CHARACTER.value: "角色與故事",
    FreeCreationItemKind.PROMPT.value: "圖片 Prompt",
    FreeCreationItemKind.WORLD.value: "世界種子",
}
_KIND_LABELS = {
    FreeCreationItemKind.STORY_FRAGMENT: "故事片段草稿",
    FreeCreationItemKind.CHARACTER: "角色與故事草稿",
    FreeCreationItemKind.PROMPT: "圖片提示詞草稿",
    FreeCreationItemKind.WORLD: "世界種子草稿",
}


def _go(page: str) -> NoReturn:
    st.session_state["pending_nav"] = page
    st.rerun()


def _open_new(kind: FreeCreationItemKind) -> None:
    if kind is FreeCreationItemKind.STORY_FRAGMENT:
        st.session_state[story_fragment_drafts.PENDING_SELECTION_STATE_KEY] = _NEW_DRAFT
        st.session_state[story_fragment_drafts.BOUND_DRAFT_STATE_KEY] = None
        _go(story_fragment_drafts.PAGE_KEY)
    if kind is FreeCreationItemKind.CHARACTER:
        st.session_state[character_drafts.PENDING_SELECTION_STATE_KEY] = _NEW_DRAFT
        st.session_state[character_drafts.BOUND_DRAFT_STATE_KEY] = None
        _go(character_drafts.PAGE_KEY)
    if kind is FreeCreationItemKind.PROMPT:
        st.session_state[prompt_scratch.PENDING_SELECTION_STATE_KEY] = _NEW_DRAFT
        st.session_state[prompt_scratch.BOUND_STATE_KEY] = None
        _go(prompt_scratch.PAGE_KEY)
    if kind is FreeCreationItemKind.WORLD:
        st.session_state[world_seed_drafts.PENDING_SELECTION_STATE_KEY] = _NEW_DRAFT
        st.session_state[world_seed_drafts.BOUND_DRAFT_STATE_KEY] = None
        _go(world_seed_drafts.PAGE_KEY)
    raise ValueError(f"不支援的自由創作類型：{kind}")


def _open_item(item: FreeCreationInboxItem) -> None:
    """Stage an exact source aggregate; no Project or author text is mutated."""

    if item.kind is FreeCreationItemKind.STORY_FRAGMENT:
        st.session_state[story_fragment_drafts.PENDING_SELECTION_STATE_KEY] = (
            item.aggregate_id
        )
        st.session_state[story_fragment_drafts.BOUND_DRAFT_STATE_KEY] = None
        _go(story_fragment_drafts.PAGE_KEY)
    if item.kind is FreeCreationItemKind.CHARACTER:
        st.session_state[character_drafts.PENDING_SELECTION_STATE_KEY] = item.aggregate_id
        st.session_state[character_drafts.BOUND_DRAFT_STATE_KEY] = None
        _go(character_drafts.PAGE_KEY)
    if item.kind is FreeCreationItemKind.PROMPT:
        st.session_state[prompt_scratch.PENDING_SELECTION_STATE_KEY] = item.aggregate_id
        st.session_state[prompt_scratch.BOUND_STATE_KEY] = None
        _go(prompt_scratch.PAGE_KEY)
    if item.kind is FreeCreationItemKind.WORLD:
        st.session_state[world_seed_drafts.PENDING_SELECTION_STATE_KEY] = item.aggregate_id
        st.session_state[world_seed_drafts.BOUND_DRAFT_STATE_KEY] = None
        _go(world_seed_drafts.PAGE_KEY)
    raise ValueError(f"不支援的自由創作類型：{item.kind}")


def _render_quick_actions() -> None:
    st.markdown("#### 想先放進一張新紙頁？")
    actions = st.columns(4)
    if actions[0].button(
        "寫一段故事",
        key=f"{STATE_PREFIX}_new_story_fragment",
        type="primary",
        use_container_width=True,
    ):
        _open_new(FreeCreationItemKind.STORY_FRAGMENT)
    if actions[1].button(
        "建立角色與故事草稿",
        key=f"{STATE_PREFIX}_new_character",
        use_container_width=True,
    ):
        _open_new(FreeCreationItemKind.CHARACTER)
    if actions[2].button(
        "建立世界種子草稿",
        key=f"{STATE_PREFIX}_new_world",
        use_container_width=True,
    ):
        _open_new(FreeCreationItemKind.WORLD)
    if actions[3].button(
        "建立圖片 Prompt 草稿",
        key=f"{STATE_PREFIX}_new_prompt",
        use_container_width=True,
    ):
        _open_new(FreeCreationItemKind.PROMPT)
    if st.button(
        "用繁中標籤拼 Prompt",
        key=f"{STATE_PREFIX}_prompt_tags",
        use_container_width=True,
    ):
        _go(PROMPT_TAG_BUILDER_PAGE_KEY)


def _render_item(item: FreeCreationInboxItem) -> None:
    with st.container(border=True):
        text, action = st.columns((5, 1))
        text.markdown(f"**{item.title}**")
        text.caption(
            f"{_KIND_LABELS[item.kind]} · {item.subtitle} · 最近更新 {item.updated_at}"
        )
        if action.button(
            "打開原稿",
            key=f"{STATE_PREFIX}_open_{item.kind.value}_{item.aggregate_id}",
            use_container_width=True,
        ):
            _open_item(item)


def render(*, services: Any | None = None) -> None:
    services = services or get_services()
    page_header(
        PAGE_LABEL,
        "不用先打開作品，從同一個地方找回故事、角色、世界與圖片 Prompt，再回原編輯器繼續寫。",
        eyebrow="獨立草稿的共同入口",
        badges=(("本機正式草稿", "teal"), ("不自動建立作品", "amber")),
    )
    st.info(
        "這裡只列出已正式保存、未綁作品且未封存的草稿。打開項目不會複製、轉換或設為正式內容，"
        "也不會改變目前作品。"
    )
    _render_quick_actions()
    st.divider()

    query_service = getattr(services, "free_creation_inbox", None)
    if query_service is None:
        st.error("自由創作箱暫時無法讀取草稿；沒有修改任何內容。")
        return

    controls = st.columns((3, 2))
    query = controls[0].text_input(
        "搜尋標題、角色名或草稿類型",
        key=SEARCH_STATE_KEY,
        placeholder="例如：雨夜、桃桃、對白",
    )
    selected_filter = controls[1].selectbox(
        "顯示類型",
        tuple(_FILTER_LABELS),
        format_func=lambda value: _FILTER_LABELS[value],
        key=FILTER_STATE_KEY,
    )
    requested_page = int(st.session_state.get(PAGE_STATE_KEY, 1))
    try:
        result = query_service.list_page(
            query=query,
            kind=None if selected_filter == "all" else selected_filter,
            page=requested_page,
            page_size=12,
        )
    except (ApplicationError, RuntimeError, TypeError, ValueError):
        st.error("自由創作箱暫時讀不到；沒有修改、移動或刪除任何草稿。")
        return

    if result.page != requested_page:
        st.session_state[PAGE_STATE_KEY] = result.page
    failed_sources = {issue.source for issue in result.issues}
    metrics = st.columns(5)
    metrics[0].metric(
        "目前可讀的正式草稿",
        result.character_count
        + result.story_fragment_count
        + result.prompt_count
        + result.world_count,
    )
    metrics[1].metric(
        "故事片段",
        "—"
        if FreeCreationSource.STORY_FRAGMENT_DRAFTS in failed_sources
        else result.story_fragment_count,
    )
    metrics[2].metric(
        "角色與故事",
        "—"
        if FreeCreationSource.CHARACTER_DRAFTS in failed_sources
        else result.character_count,
    )
    metrics[3].metric(
        "世界種子",
        "—" if FreeCreationSource.WORLD_DRAFTS in failed_sources else result.world_count,
    )
    metrics[4].metric(
        "圖片 Prompt",
        "—"
        if FreeCreationSource.PROMPT_DRAFTS in failed_sources
        else result.prompt_count,
    )
    for issue in result.issues:
        st.warning(issue.message)

    if result.items:
        st.caption(f"找到 {result.total} 筆草稿；搜尋結果會在這台電腦即時更新。")
        for item in result.items:
            _render_item(item)
    else:
        if result.issues:
            st.info("目前可讀取的來源沒有符合條件的草稿；警告來源的內容數量仍是未知。")
        else:
            st.info("目前條件下沒有已正式保存的獨立草稿。可以先建立一張新紙頁。")

    if result.page_count > 1:
        st.number_input(
            "頁數",
            min_value=1,
            max_value=result.page_count,
            step=1,
            key=PAGE_STATE_KEY,
        )
        st.caption(f"第 {result.page} / {result.page_count} 頁")

    st.caption(
        "尚未完成的 Prompt 復原內容不會混成正式草稿；若有可恢復內容，頁面上方會另行提示。"
    )


__all__ = ["PAGE_KEY", "PAGE_LABEL", "render"]
