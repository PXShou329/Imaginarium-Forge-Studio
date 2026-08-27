"""Presentation-only application shell for the Streamlit desktop workspace."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from html import escape

import streamlit as st

from imaginarium_forge.environment import EnvironmentIssue
from imaginarium_forge.ui.components import sidebar_brand
from imaginarium_forge.ui.navigation import (
    CURRENT_WORK_GROUP_KEY,
    FREE_CREATION_GROUP_KEY,
    NAVIGATION_GROUPS,
    ROUTE_DESCRIPTIONS,
    ROUTE_ICONS,
    ROUTE_LABELS,
    group_for_route,
)
from imaginarium_forge.ui.openai_session import read_openai_session_settings
from imaginarium_forge.ui.pages import (
    ai_settings,
    free_creation_inbox,
    prompt_tag_builder,
)

NavigationCallback = Callable[..., None]

_ROUTE_KEYS = {route: f"{index:02d}" for index, route in enumerate(ROUTE_LABELS)}


def _route_button_label(route: str) -> str:
    return f"{ROUTE_ICONS[route]}  {ROUTE_LABELS[route]}"


def _route_key(prefix: str, route: str) -> str:
    return f"{prefix}_{_ROUTE_KEYS[route]}"


def render_sidebar(
    *,
    current_route: str,
    project_name: str | None,
    issues: Sequence[EnvironmentIssue],
    queue_navigation: NavigationCallback,
) -> None:
    """Render the grouped product navigation and truthful local context."""

    sidebar_brand()
    context_name = project_name or "自由創作模式"
    context_copy = "目前作品" if project_name else "不開作品也能先寫"
    st.sidebar.html(
        '<section class="if-sidebar-context" aria-label="目前創作情境">'
        f'<span>{escape(context_copy)}</span><strong>{escape(context_name)}</strong>'
        "</section>"
    )

    st.sidebar.markdown('<div class="if-sidebar-label">工作區</div>', unsafe_allow_html=True)
    for group in NAVIGATION_GROUPS:
        if len(group.routes) == 1:
            route = group.routes[0]
            st.sidebar.button(
                f"{group.icon}  {group.label}",
                key=_route_key("sidebar_nav", route),
                type="primary" if route == current_route else "secondary",
                help=group.description,
                on_click=queue_navigation,
                args=(route,),
                use_container_width=True,
            )
            continue

        active_group = current_route in group.routes
        with st.sidebar.expander(
            f"{group.icon}  {group.label}",
            expanded=active_group,
        ):
            st.caption(group.description)
            for route in group.routes:
                st.button(
                    _route_button_label(route),
                    key=_route_key("sidebar_nav", route),
                    type="primary" if route == current_route else "secondary",
                    help=ROUTE_DESCRIPTIONS[route],
                    on_click=queue_navigation,
                    args=(route,),
                    use_container_width=True,
                )

    status_tone = "is-error" if any(issue.level == "error" for issue in issues) else ""
    status_label = f"本機環境有 {len(issues)} 項提醒" if issues else "本機環境正常"
    st.sidebar.html(
        f'<div class="if-sidebar-status {status_tone}">'
        '<span class="if-status-dot" aria-hidden="true"></span>'
        f"<span>{escape(status_label)}</span></div>"
    )
    if issues:
        with st.sidebar.expander("查看環境提醒"):
            for issue in issues:
                st.write(issue.message)
    st.sidebar.caption("文字預設只儲存在這台電腦。")


def render_topbar(
    *,
    current_route: str,
    project_name: str | None,
    issues: Sequence[EnvironmentIssue],
    back_target: str | None,
    back_index: int,
    previous_target: str | None,
    next_target: str | None,
    queue_navigation: NavigationCallback,
) -> None:
    """Render history controls, breadcrumb, route search and real status."""

    group_key = group_for_route(current_route)
    group = next(group for group in NAVIGATION_GROUPS if group.key == group_key)
    project_context = project_name or "自由創作"

    with st.container(key="app_topbar"):
        controls = st.columns((0.42, 0.42, 0.42, 3.1, 1.05, 1.08, 1.0))
        controls[0].button(
            "←",
            key="shell_back",
            disabled=back_target is None,
            help="返回剛才使用的頁面",
            on_click=queue_navigation,
            args=(back_target or current_route,),
            kwargs={"history_index": max(back_index, 0)},
            use_container_width=True,
        )
        controls[1].button(
            "‹",
            key="shell_previous",
            disabled=previous_target is None,
            help="上一個工作頁",
            on_click=queue_navigation,
            args=(previous_target or current_route,),
            use_container_width=True,
        )
        controls[2].button(
            "›",
            key="shell_next",
            disabled=next_target is None,
            help="下一個工作頁",
            on_click=queue_navigation,
            args=(next_target or current_route,),
            use_container_width=True,
        )
        controls[3].html(
            '<nav class="if-breadcrumb" aria-label="目前位置">'
            f'<span>{escape(group.label)}</span><i aria-hidden="true">›</i>'
            f'<strong>{escape(ROUTE_LABELS[current_route])}</strong>'
            f'<small>{escape(project_context)}</small></nav>'
        )
        with controls[4].popover("⌕ 搜尋", use_container_width=True):
            st.markdown("**前往功能**")
            st.caption("搜尋範圍是本機應用程式內的工作頁，不會送出任何內容。")
            target = st.selectbox(
                "選擇工作頁",
                tuple(ROUTE_LABELS),
                format_func=_route_button_label,
                index=tuple(ROUTE_LABELS).index(current_route),
                key="shell_route_search",
            )
            st.caption(ROUTE_DESCRIPTIONS[target])
            st.button(
                "前往這個工作頁",
                key="shell_route_search_go",
                type="primary",
                on_click=queue_navigation,
                args=(target,),
                use_container_width=True,
            )
        with controls[5].popover("＋ 建立", use_container_width=True):
            st.markdown("**直接開始**")
            quick_routes = (
                (prompt_tag_builder.PAGE_KEY, "懶人標籤生成器"),
                ("Story Fragment Drafts", "故事片段"),
                ("Character Drafts", "角色草稿"),
                ("World Seed Drafts", "世界種子"),
                ("Prompt Scratchpad", "圖片 Prompt"),
            )
            for route, label in quick_routes:
                st.button(
                    label,
                    key=_route_key("shell_quick", route),
                    on_click=queue_navigation,
                    args=(route,),
                    use_container_width=True,
                )
        with controls[6].popover("● 狀態", use_container_width=True):
            openai = read_openai_session_settings(st.session_state)
            st.markdown("**創作環境**")
            st.caption(f"作品：{project_context}")
            st.caption(f"OpenAI：{'已設定' if openai.configured else '尚未設定'}")
            st.caption(f"環境檢查：{'正常' if not issues else f'{len(issues)} 項提醒'}")
            st.button(
                "AI 設定",
                key="shell_status_ai_settings",
                on_click=queue_navigation,
                args=(ai_settings.PAGE_KEY,),
                use_container_width=True,
            )
            st.button(
                "系統健康與備份",
                key="shell_status_system_health",
                on_click=queue_navigation,
                args=("System Health",),
                use_container_width=True,
            )


def render_mobile_navigation(
    *, current_route: str, queue_navigation: NavigationCallback
) -> None:
    """Render five high-frequency destinations for narrow viewports."""

    current_group = group_for_route(current_route)
    destinations = (
        ("首頁", "⌂", "首頁", "home"),
        (free_creation_inbox.PAGE_KEY, "✦", "創作", FREE_CREATION_GROUP_KEY),
        ("專案", "▤", "書架", "library"),
        ("Creative Launchpad", "✎", "工作室", CURRENT_WORK_GROUP_KEY),
    )
    with st.container(key="mobile_navigation"):
        columns = st.columns(5)
        for column, (route, icon, label, group_key) in zip(
            columns[:4], destinations, strict=True
        ):
            column.button(
                f"{icon}\n{label}",
                key=_route_key("mobile_nav", route),
                type="primary" if current_group == group_key else "secondary",
                on_click=queue_navigation,
                args=(route,),
                use_container_width=True,
            )
        with columns[4].popover("⋯ 更多", use_container_width=True):
            st.markdown("**更多工作頁**")
            for route in (
                "靈感房",
                "Prompt Studio",
                "Story Studio",
                ai_settings.PAGE_KEY,
                "System Health",
                "Guide",
            ):
                st.button(
                    _route_button_label(route),
                    key=_route_key("mobile_more", route),
                    type="primary" if route == current_route else "secondary",
                    on_click=queue_navigation,
                    args=(route,),
                    use_container_width=True,
                )


__all__ = ["render_mobile_navigation", "render_sidebar", "render_topbar"]
