"""A friendly, reusable gateway for pages whose storage is project-scoped.

The durable Canon/story/prompt services deliberately require a project id, but
that storage constraint should not read like a prerequisite for *all* creative
work.  Pages can render this gateway when no book is open: the author may move
to the project-optional draft box, open an existing book, or start a new one.

This module owns navigation intent only.  It never creates a project and never
copies draft content, so an accidental click cannot produce hidden records.
"""

from __future__ import annotations

from collections.abc import MutableMapping
from dataclasses import dataclass
from enum import StrEnum
from html import escape

import streamlit as st
from streamlit.runtime.state.session_state_proxy import SessionStateProxy

FREE_DRAFT_DESTINATION = "Free Creation Inbox"
PROJECT_LIBRARY_DESTINATION = "專案"
PROJECT_CREATE_REQUEST_STATE_KEY = "project_gateway_create_requested"


type _GatewayState = MutableMapping[str, object] | SessionStateProxy


class ProjectGatewayAction(StrEnum):
    """The three explicit paths offered at a project-bound page."""

    FREE_DRAFT = "free_draft"
    OPEN_PROJECT = "open_project"
    CREATE_PROJECT = "create_project"


@dataclass(frozen=True, slots=True)
class ProjectGatewayCopy:
    """Page-specific explanation without duplicating the navigation contract."""

    feature_name: str
    project_reason: str
    draft_description: str = (
        "到自由創作箱找回角色、個人故事、角色圖或背景圖提示詞；"
        "可保存成獨立草稿，之後再決定要不要放進作品。"
    )
    draft_destination: str = FREE_DRAFT_DESTINATION
    draft_heading: str = "自由創作／暫存"
    draft_button_label: str = "先去自由創作"


def queue_project_gateway_action(
    action: ProjectGatewayAction,
    state: _GatewayState,
    *,
    free_draft_destination: str = FREE_DRAFT_DESTINATION,
) -> str:
    """Record a navigation intent and return its destination for callers/tests.

    ``selected_project_id`` is intentionally left untouched.  In particular,
    choosing the draft path must not create, select, or silently bind a book.
    """

    if action is ProjectGatewayAction.FREE_DRAFT:
        destination = free_draft_destination.strip()
        if not destination:
            raise ValueError("free_draft_destination must not be blank")
    else:
        destination = PROJECT_LIBRARY_DESTINATION
    state["pending_nav"] = destination
    if action is ProjectGatewayAction.CREATE_PROJECT:
        state[PROJECT_CREATE_REQUEST_STATE_KEY] = True
    else:
        state.pop(PROJECT_CREATE_REQUEST_STATE_KEY, None)
    return destination


def consume_project_create_request(state: _GatewayState, *, no_active_projects: bool) -> bool:
    """Return whether the shelf's create form should open, consuming intent.

    An empty shelf keeps its established first-run behaviour.  A gateway click
    additionally opens the form even when the author already owns other books.
    The one-shot state cannot make later shelf visits expand unexpectedly.
    """

    requested = state.pop(PROJECT_CREATE_REQUEST_STATE_KEY, False) is True
    return no_active_projects or requested


def render_project_gateway(
    *,
    key_prefix: str,
    copy: ProjectGatewayCopy,
) -> None:
    """Render a non-blocking choice card for a page with no selected project.

    Typical caller usage::

        if not project_id:
            render_project_gateway(
                key_prefix="story_studio",
                copy=ProjectGatewayCopy(
                    feature_name="故事頁",
                    project_reason="章節與版本需要一本作品來保持前後一致。",
                ),
            )
            return

    The caller still returns because its underlying services are project-bound;
    unlike the old warning, however, this gateway offers three useful next
    actions and makes clear that free drafting is always available.
    """

    if not key_prefix.strip():
        raise ValueError("key_prefix must not be blank")
    if not copy.feature_name.strip():
        raise ValueError("feature_name must not be blank")
    if not copy.project_reason.strip():
        raise ValueError("project_reason must not be blank")
    if not copy.draft_destination.strip():
        raise ValueError("draft_destination must not be blank")
    if not copy.draft_heading.strip():
        raise ValueError("draft_heading must not be blank")
    if not copy.draft_button_label.strip():
        raise ValueError("draft_button_label must not be blank")

    with st.container(border=True):
        st.markdown(
            '<div class="if-kicker">這次想怎麼開始？</div>',
            unsafe_allow_html=True,
        )
        st.subheader("不必先開一本書，也能開始創作")
        st.write(
            f"「{escape(copy.feature_name)}」的正式收藏會依作品整理；"
            "如果現在只想試一個角色、一段故事或幾組提示詞，可以先放在自由草稿箱。"
        )
        st.caption(copy.project_reason)

        draft_column, open_column, create_column = st.columns(3, vertical_alignment="bottom")
        with draft_column:
            st.markdown(f"#### {copy.draft_heading}")
            st.caption(copy.draft_description)
            if st.button(
                copy.draft_button_label,
                key=f"{key_prefix}_gateway_free_draft",
                type="primary",
                use_container_width=True,
            ):
                queue_project_gateway_action(
                    ProjectGatewayAction.FREE_DRAFT,
                    st.session_state,
                    free_draft_destination=copy.draft_destination,
                )
                st.rerun()

        with open_column:
            st.markdown("#### 繼續已有作品")
            st.caption("到書架挑一本作品；打開後再回來，內容就會收在正確的位置。")
            if st.button(
                "打開已有作品",
                key=f"{key_prefix}_gateway_open_project",
                use_container_width=True,
            ):
                queue_project_gateway_action(ProjectGatewayAction.OPEN_PROJECT, st.session_state)
                st.rerun()

        with create_column:
            st.markdown("#### 建立新作品")
            st.caption("只要先取一個名字；故事、角色和世界觀都可以之後再補。")
            if st.button(
                "建立新的作品",
                key=f"{key_prefix}_gateway_create_project",
                use_container_width=True,
            ):
                queue_project_gateway_action(ProjectGatewayAction.CREATE_PROJECT, st.session_state)
                st.rerun()
