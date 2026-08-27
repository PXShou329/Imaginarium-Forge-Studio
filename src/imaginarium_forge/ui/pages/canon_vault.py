"""Canon Vault landing — orients the user and links to the sub-pages."""

from __future__ import annotations

import streamlit as st

from imaginarium_forge.ui.bootstrap import get_services
from imaginarium_forge.ui.components import page_header, section_heading
from imaginarium_forge.ui.project_gateway import ProjectGatewayCopy, render_project_gateway


def render() -> None:
    page_header(
        "這本書的世界觀與設定",
        "集中查看作品內的角色與圖片風格；正式版本會留住每次修改的歷史。",
        eyebrow="讓同一本書的設定前後一致",
        badges=(("版本化設定", "teal"), ("修改歷史可追溯", "")),
    )
    services = get_services()
    project_id = st.session_state.get("selected_project_id")
    if not project_id:
        render_project_gateway(
            key_prefix="canon_vault",
            copy=ProjectGatewayCopy(
                feature_name="世界觀設定",
                project_reason="正式世界觀會依作品分開保存，避免不同故事的設定混在一起。",
                draft_description=(
                    "先做一個角色與個人故事，或只留圖片 Prompt；"
                    "等世界開始成形再收入作品。"
                ),
            ),
        )
        return
    project = services.projects.get_project(project_id)
    st.caption(f"目前作品 · {project.name}")
    characters = services.characters.list_characters(project_id, include_archived=False)
    bibles = services.story_bibles.list_for_project(project_id)
    styles = services.styles.list_profiles(project_id, include_archived=False)
    section_heading(
        "Canon 概覽",
        "這裡只整理目前已存在的設定入口；世界聖經、角色與視覺風格仍各自版本化。",
        eyebrow="PROJECT KNOWLEDGE",
    )
    col1, col2, col3 = st.columns(3)
    col1.metric("角色", len(characters))
    col2.metric("故事聖經", len(bibles))
    col3.metric("視覺風格", len(styles))

    actions = st.columns(3)
    if actions[0].button(
        "管理正式角色",
        key="canon_open_characters",
        use_container_width=True,
    ):
        st.session_state["pending_nav"] = "角色"
        st.rerun()
    if actions[1].button(
        "打開故事聖經",
        key="canon_open_story_bible",
        type="primary",
        use_container_width=True,
    ):
        st.session_state["story_active_section"] = "bible"
        st.session_state["_story_active_section_durable"] = "bible"
        st.session_state["pending_nav"] = "Story Studio"
        st.rerun()
    if actions[2].button(
        "管理視覺風格",
        key="canon_open_styles",
        use_container_width=True,
    ):
        st.session_state["pending_nav"] = "Style Profiles"
        st.rerun()

    st.info(
        "Canon Vault 目前是作品設定總覽，不會假裝成尚未存在的百科條目編輯器。"
        "正式世界觀內容請在 Story Studio 的「故事聖經」保存版本。"
    )
