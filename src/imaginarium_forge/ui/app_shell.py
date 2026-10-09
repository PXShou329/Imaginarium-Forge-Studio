"""Presentation-only application shell for the Streamlit desktop workspace."""

from __future__ import annotations

import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from hashlib import sha256
from html import escape

import streamlit as st
from pydantic import SecretStr

from imaginarium_forge.application.services.ollama_model_service import (
    ollama_model_ids_match,
)
from imaginarium_forge.domain.generation.preferences import AIProviderPreference
from imaginarium_forge.environment import EnvironmentIssue
from imaginarium_forge.ui.bootstrap import get_services
from imaginarium_forge.ui.components import sidebar_brand
from imaginarium_forge.ui.navigation import (
    CURRENT_WORK_GROUP_KEY,
    FREE_CREATION_GROUP_KEY,
    NAVIGATION_GROUPS,
    NAVIGATION_GROUPS_BY_KEY,
    ROUTE_DESCRIPTIONS,
    ROUTE_ICONS,
    ROUTE_LABELS,
    SEARCHABLE_ROUTES,
    group_for_route,
)
from imaginarium_forge.ui.ollama_session import (
    OLLAMA_INVENTORY_ERROR_SESSION_KEY,
    OLLAMA_INVENTORY_KNOWN_SESSION_KEY,
    OLLAMA_INVENTORY_NAMES_SESSION_KEY,
)
from imaginarium_forge.ui.openai_session import (
    OPENAI_API_KEY_SESSION_KEY,
    OPENAI_DEVICE_CREDENTIAL_PRESENT_SESSION_KEY,
)
from imaginarium_forge.ui.pages import (
    ai_settings,
    free_creation_inbox,
    offline_translation,
    prompt_tag_builder,
)
from imaginarium_forge.ui.shell_state import (
    SHELL_POPOVER_EPOCH_STATE_KEY,
    bump_shell_popover_epoch,
)

NavigationCallback = Callable[..., None]

_ROUTE_KEYS = {route: f"{index:02d}" for index, route in enumerate(ROUTE_LABELS)}
TOPBAR_SEARCH_POPOVER_STATE_KEY = "shell_search_popover"
TOPBAR_CREATE_POPOVER_STATE_KEY = "shell_create_popover"
TOPBAR_STATUS_POPOVER_STATE_KEY = "shell_status_popover"
MOBILE_MORE_POPOVER_STATE_KEY = "shell_mobile_more_popover"
TOPBAR_PRIMARY_ACTION_LABELS = ("主頁", "搜尋", "建立", "翻譯", "狀態")
_AI_SETTINGS_OLLAMA_MODELS_STATE_KEY = "ai_settings_ollama_models"


@dataclass(frozen=True, slots=True)
class _RuntimeStatusSnapshot:
    provider: str
    model: str
    readiness: str
    key_source: str
    ollama: str
    offline: str


@dataclass(frozen=True, slots=True)
class _CredentialMetadata:
    label: str
    available: bool
    known: bool


def _credential_metadata() -> _CredentialMetadata:
    """Inspect non-secret presence metadata without resolving a credential."""

    session_value = st.session_state.get(OPENAI_API_KEY_SESSION_KEY)
    if isinstance(session_value, SecretStr) or (
        isinstance(session_value, str) and bool(session_value.strip())
    ):
        return _CredentialMetadata(
            "瀏覽器工作階段（內容已遮罩）",
            available=True,
            known=True,
        )

    cached_device = st.session_state.get(
        OPENAI_DEVICE_CREDENTIAL_PRESENT_SESSION_KEY
    )
    if cached_device is True:
        return _CredentialMetadata(
            "裝置安全保存（內容已遮罩）",
            available=True,
            known=True,
        )

    environment_available = bool(os.environ.get("OPENAI_API_KEY", "").strip())
    if environment_available:
        suffix = "" if cached_device is False else "；裝置來源未被動檢查"
        return _CredentialMetadata(
            f"系統環境變數（內容已遮罩{suffix}）",
            available=True,
            known=True,
        )

    if cached_device is False:
        return _CredentialMetadata("尚未設定", available=False, known=True)

    try:
        device_backend_available = get_services().credential_store.available()
    except Exception:
        device_backend_available = False
    if device_backend_available:
        return _CredentialMetadata(
            "裝置安全保存（存在狀態未被動檢查）",
            available=False,
            known=False,
        )
    return _CredentialMetadata("尚未設定", available=False, known=True)


def _runtime_status_snapshot() -> _RuntimeStatusSnapshot:
    """Project saved/session state only; deliberately performs no provider probe."""

    preferences = get_services().ai_runtime_preferences.get_preferences()
    credential = _credential_metadata()
    key_source = credential.label
    catalog_known = _AI_SETTINGS_OLLAMA_MODELS_STATE_KEY in st.session_state
    raw_names = st.session_state.get(
        _AI_SETTINGS_OLLAMA_MODELS_STATE_KEY,
        st.session_state.get(OLLAMA_INVENTORY_NAMES_SESSION_KEY, ()),
    )
    inventory = (
        tuple(str(item) for item in raw_names)
        if isinstance(raw_names, (list, tuple))
        else ()
    )
    inventory_failed = OLLAMA_INVENTORY_ERROR_SESSION_KEY in st.session_state
    # A shared Advanced scan can fail after the primary settings page populated
    # its own catalog. The latest explicit failure is authoritative; a stale
    # successful list must never make the status portal report a live success.
    inventory_known = not inventory_failed and (
        catalog_known
        or bool(st.session_state.get(OLLAMA_INVENTORY_KNOWN_SESSION_KEY, False))
    )

    if inventory_failed:
        ollama_status = "上次檢查失敗（狀態可能已過期）"
    elif inventory_known:
        ollama_status = "已連線（最近一次手動檢查，可能已過期）"
    else:
        ollama_status = "尚未檢查（狀態可能已過期）"

    if preferences.default_provider is AIProviderPreference.OPENAI:
        provider = "OpenAI"
        model = preferences.openai_model
        if not preferences.remote_provider_enabled:
            readiness = "尚未啟用遠端傳送"
        elif credential.available:
            readiness = "可用（未主動連線檢查）"
        elif credential.known:
            readiness = "需要 API Key"
        else:
            readiness = "credential 狀態未被動確認"
    elif preferences.default_provider is AIProviderPreference.OLLAMA:
        provider = "Ollama"
        model = preferences.ollama_model or "尚未選擇"
        installed = bool(
            model != "尚未選擇"
            and any(ollama_model_ids_match(model, item) for item in inventory)
        )
        if inventory_failed:
            readiness = "上次檢查失敗（狀態可能已過期）"
        elif inventory_known and installed:
            readiness = "可用（最近一次手動檢查，可能已過期）"
        elif inventory_known:
            readiness = "需要已安裝模型"
        else:
            readiness = "尚未檢查"
    else:
        provider = "Offline"
        model = "離線靈感模板（快速短版）"
        readiness = "可用"

    return _RuntimeStatusSnapshot(
        provider=provider,
        model=model,
        readiness=readiness,
        key_source=key_source,
        ollama=ollama_status,
        offline="可用（本機）",
    )


def _route_button_label(route: str) -> str:
    return f"{ROUTE_ICONS[route]}  {ROUTE_LABELS[route]}"


def _route_key(prefix: str, route: str) -> str:
    return f"{prefix}_{_ROUTE_KEYS[route]}"


def _popover_key(base_key: str, current_route: str) -> str:
    """Remount browser-owned popovers after navigation, including same-page links."""

    raw_epoch = st.session_state.get(SHELL_POPOVER_EPOCH_STATE_KEY, 0)
    epoch = raw_epoch if isinstance(raw_epoch, int) else 0
    return f"{base_key}_{_ROUTE_KEYS[current_route]}_{epoch}"


def _status_popover_key(
    current_route: str,
    status: _RuntimeStatusSnapshot,
    *,
    project_context: str,
    issue_count: int,
) -> str:
    """Remount client-owned status DOM whenever its visible snapshot changes."""

    display_snapshot = "\x1f".join(
        (
            project_context,
            str(issue_count),
            status.provider,
            status.model,
            status.readiness,
            status.key_source,
            status.ollama,
            status.offline,
        )
    )
    digest = sha256(display_snapshot.encode("utf-8")).hexdigest()[:16]
    return f"{_popover_key(TOPBAR_STATUS_POPOVER_STATE_KEY, current_route)}_{digest}"


def _queue_popover_navigation(
    queue_navigation: NavigationCallback,
    route: str,
) -> None:
    """Close browser-owned navigation popovers before queuing a route."""

    bump_shell_popover_epoch(st.session_state)
    queue_navigation(route)


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
                width="stretch",
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
                    width="stretch",
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
    group = NAVIGATION_GROUPS_BY_KEY.get(group_key or "")
    if group is None:
        raise RuntimeError(f"registered route {current_route!r} has no navigation context")
    project_context = project_name or "自由創作"
    runtime_status = _runtime_status_snapshot()
    breadcrumb_context = f"<small>{escape(project_name)}</small>" if project_name else ""

    with st.container(key="app_topbar"):
        controls = st.columns(
            (0.42, 0.42, 0.42, 2.65, 0.78, 1.0, 1.03, 0.9, 0.96)
        )
        controls[0].button(
            "←",
            key="shell_back",
            disabled=back_target is None,
            help="返回剛才使用的頁面",
            on_click=queue_navigation,
            args=(back_target or current_route,),
            kwargs={"history_index": max(back_index, 0)},
            width="stretch",
        )
        controls[1].button(
            "‹",
            key="shell_previous",
            disabled=previous_target is None,
            help="上一個工作頁",
            on_click=queue_navigation,
            args=(previous_target or current_route,),
            width="stretch",
        )
        controls[2].button(
            "›",
            key="shell_next",
            disabled=next_target is None,
            help="下一個工作頁",
            on_click=queue_navigation,
            args=(next_target or current_route,),
            width="stretch",
        )
        controls[3].html(
            '<nav class="if-breadcrumb" aria-label="目前位置">'
            f'<span>{escape(group.label)}</span><i aria-hidden="true">›</i>'
            f'<strong>{escape(ROUTE_LABELS[current_route])}</strong>'
            f"{breadcrumb_context}</nav>"
        )
        controls[4].button(
            "主頁",
            key="shell_home",
            help="回到首頁",
            on_click=queue_navigation,
            args=("首頁",),
            width="stretch",
        )
        with controls[5].popover(
            "⌕ 搜尋",
            width="stretch",
            key=_popover_key(TOPBAR_SEARCH_POPOVER_STATE_KEY, current_route),
            on_change="ignore",
        ):
            st.markdown("**前往功能**")
            st.caption("搜尋範圍是本機應用程式內的工作頁，不會送出任何內容。")
            target = st.selectbox(
                "選擇工作頁",
                SEARCHABLE_ROUTES,
                format_func=_route_button_label,
                index=(
                    SEARCHABLE_ROUTES.index(current_route)
                    if current_route in SEARCHABLE_ROUTES
                    else 0
                ),
                key="shell_route_search",
            )
            st.caption(ROUTE_DESCRIPTIONS[target])
            st.button(
                "前往這個工作頁",
                key="shell_route_search_go",
                type="primary",
                on_click=_queue_popover_navigation,
                args=(queue_navigation, target),
                width="stretch",
            )
        with controls[6].popover(
            "＋ 建立",
            width="stretch",
            key=_popover_key(TOPBAR_CREATE_POPOVER_STATE_KEY, current_route),
            on_change="ignore",
        ):
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
                    on_click=_queue_popover_navigation,
                    args=(queue_navigation, route),
                    width="stretch",
                )
        controls[7].button(
            "⇄ 翻譯",
            key="shell_translation",
            type=(
                "primary"
                if current_route == offline_translation.PAGE_KEY
                else "secondary"
            ),
            help="直接開啟完全離線的繁中與英文翻譯工作台",
            on_click=queue_navigation,
            args=(offline_translation.PAGE_KEY,),
            width="stretch",
        )
        with controls[8].popover(
            "● 狀態",
            width="stretch",
            key=_status_popover_key(
                current_route,
                runtime_status,
                project_context=project_context,
                issue_count=len(issues),
            ),
            on_change="ignore",
        ):
            st.markdown("**創作環境**")
            st.caption(f"作品：{project_context}")
            st.caption(f"目前引擎：{runtime_status.provider}")
            st.caption(f"模型：{runtime_status.model}")
            st.caption(f"狀態：{runtime_status.readiness}")
            st.caption(f"OpenAI Key：{runtime_status.key_source}")
            st.caption(f"Ollama：{runtime_status.ollama}")
            st.caption(f"Offline：{runtime_status.offline}")
            st.caption(f"環境檢查：{'正常' if not issues else f'{len(issues)} 項提醒'}")
            st.caption("這裡只顯示已保存／最近狀態，不會自動連線。")
            st.button(
                "AI 設定",
                key="shell_status_ai_settings",
                on_click=_queue_popover_navigation,
                args=(
                    queue_navigation,
                    ai_settings.PAGE_KEY,
                ),
                width="stretch",
            )
            st.button(
                "系統健康與備份",
                key="shell_status_system_health",
                on_click=_queue_popover_navigation,
                args=(
                    queue_navigation,
                    "System Health",
                ),
                width="stretch",
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
                width="stretch",
            )
        with columns[4].popover(
            "⋯ 更多",
            width="stretch",
            key=_popover_key(MOBILE_MORE_POPOVER_STATE_KEY, current_route),
            on_change="ignore",
        ):
            st.markdown("**更多工作頁**")
            for route in (
                "靈感房",
                "Prompt Studio",
                "Story Studio",
                "System Health",
                "Guide",
            ):
                st.button(
                    _route_button_label(route),
                    key=_route_key("mobile_more", route),
                    type="primary" if route == current_route else "secondary",
                    on_click=_queue_popover_navigation,
                    args=(queue_navigation, route),
                    width="stretch",
                )


__all__ = [
    "MOBILE_MORE_POPOVER_STATE_KEY",
    "SHELL_POPOVER_EPOCH_STATE_KEY",
    "TOPBAR_CREATE_POPOVER_STATE_KEY",
    "TOPBAR_PRIMARY_ACTION_LABELS",
    "TOPBAR_SEARCH_POPOVER_STATE_KEY",
    "TOPBAR_STATUS_POPOVER_STATE_KEY",
    "render_mobile_navigation",
    "render_sidebar",
    "render_topbar",
]
