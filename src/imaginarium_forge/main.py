"""Streamlit entry point: `streamlit run src/imaginarium_forge/main.py`."""

from __future__ import annotations

import json
from collections.abc import Mapping

import streamlit as st

from imaginarium_forge.application.errors import ApplicationError
from imaginarium_forge.environment import EnvironmentIssue, check_environment
from imaginarium_forge.ui.app_shell import (
    render_mobile_navigation,
    render_sidebar,
    render_topbar,
)
from imaginarium_forge.ui.bootstrap import get_services
from imaginarium_forge.ui.navigation import (
    HOME_GROUP_KEY,
    ROUTE_LABELS,
    adjacent_route,
    group_for_route,
    landing_route_for_group,
)
from imaginarium_forge.ui.pages import (
    ai_settings,
    canon_vault,
    character_drafts,
    characters,
    checkpoint_registry,
    creative_automation,
    creative_launchpad,
    experiment_lab,
    free_creation_inbox,
    guide,
    home,
    projects,
    prompt_scratch,
    prompt_studio,
    prompt_tag_builder,
    story_fragment_drafts,
    story_studio,
    style_profiles,
    system_health,
    world_seed_drafts,
)
from imaginarium_forge.ui.theme import apply_theme

_NAV_LABELS = ROUTE_LABELS

# ``靈感房`` is a reserved creator-facing destination.  Until its dedicated
# page is registered, old builds fall back to the manual launchpad instead of
# leaving a navigation click stranded.  A real page key always wins.
_PENDING_NAV_FALLBACKS = {"靈感房": "Creative Launchpad"}
_ACTIVE_NAV_STATE_KEY = "active_nav_choice"
_NAV_GROUP_STATE_KEY = "nav_group_choice"
_LAST_RENDERED_NAV_STATE_KEY = "last_rendered_nav_choice"
_NAV_HISTORY_STATE_KEY = "navigation_route_history"
_NAV_HISTORY_INDEX_STATE_KEY = "navigation_route_history_index"
_NAV_HISTORY_TARGET_STATE_KEY = "pending_navigation_history_target"
_NAV_HISTORY_LIMIT = 50


def _scroll_workspace_to_top(route_token: str) -> None:
    """Reset only real route changes; ordinary editor reruns keep their position."""

    # The keyed zero-size host keeps this one internal iframe from appearing as
    # a tiny scrollbar glyph.  Copyable Prompt iframes remain untouched.
    with st.container(key="route_scroll_reset"):
        st.iframe(
            f"""
            <script>
            // Vary this document so Streamlit remounts the iframe for each route.
            const routeToken = {json.dumps(route_token)};
            const resetMainScroll = () => {{
              const main = window.parent.document.querySelector('[data-testid="stMain"]');
              if (main) main.scrollTo({{ top: 0, left: 0, behavior: 'auto' }});
            }};
            void routeToken;
            resetMainScroll();
            window.requestAnimationFrame(resetMainScroll);
            window.setTimeout(resetMainScroll, 80);
            window.setTimeout(resetMainScroll, 240);
            </script>
            <style>
            html, body {{
              width: 100%;
              height: 0;
              margin: 0;
              overflow: hidden;
              background: transparent;
            }}
            </style>
            """,
            height=1,
            tab_index=-1,
        )


def _navigation_history() -> tuple[list[str], int]:
    """Return a validated application-owned route history and cursor."""

    raw_history = st.session_state.get(_NAV_HISTORY_STATE_KEY, [])
    history = (
        [str(route) for route in raw_history if route in _NAV_LABELS]
        if isinstance(raw_history, list)
        else []
    )
    raw_index = st.session_state.get(_NAV_HISTORY_INDEX_STATE_KEY, len(history) - 1)
    index = raw_index if isinstance(raw_index, int) else len(history) - 1
    index = min(max(index, 0), len(history) - 1) if history else -1
    return history, index


def _record_navigation_visit(route: str) -> None:
    """Record only successful route changes, including a guarded Back action."""

    history, index = _navigation_history()
    if not history:
        st.session_state[_NAV_HISTORY_STATE_KEY] = [route]
        st.session_state[_NAV_HISTORY_INDEX_STATE_KEY] = 0
        return
    if history[index] == route:
        return

    raw_target = st.session_state.get(_NAV_HISTORY_TARGET_STATE_KEY)
    target_route = raw_target.get("route") if isinstance(raw_target, dict) else None
    target_index = raw_target.get("index") if isinstance(raw_target, dict) else None
    if (
        target_route == route
        and isinstance(target_index, int)
        and 0 <= target_index < len(history)
        and history[target_index] == route
    ):
        index = target_index
    else:
        history = history[: index + 1]
        history.append(route)
        if len(history) > _NAV_HISTORY_LIMIT:
            history = history[-_NAV_HISTORY_LIMIT:]
        index = len(history) - 1
    st.session_state.pop(_NAV_HISTORY_TARGET_STATE_KEY, None)
    st.session_state[_NAV_HISTORY_STATE_KEY] = history
    st.session_state[_NAV_HISTORY_INDEX_STATE_KEY] = index


def _queue_navigation(route: str, *, history_index: int | None = None) -> None:
    """Queue a native route change for the normal dirty-editor guard."""

    if route not in _NAV_LABELS:
        return
    if history_index is None:
        st.session_state.pop(_NAV_HISTORY_TARGET_STATE_KEY, None)
    else:
        st.session_state[_NAV_HISTORY_TARGET_STATE_KEY] = {
            "route": route,
            "index": history_index,
        }
    st.session_state["pending_nav"] = route


def _render_global_navigation(
    current: str,
    *,
    project_name: str | None,
    issues: list[EnvironmentIssue],
) -> None:
    """Render the application topbar over the existing guarded route model."""

    history, history_index = _navigation_history()
    back_index = history_index - 1
    back_target = history[back_index] if back_index >= 0 else None
    previous_target = adjacent_route(current, -1)
    next_target = adjacent_route(current, 1)

    render_topbar(
        current_route=current,
        project_name=project_name,
        issues=issues,
        back_target=back_target,
        back_index=back_index,
        previous_target=previous_target,
        next_target=next_target,
        queue_navigation=_queue_navigation,
    )


def _navigation_target(
    requested: object,
    pages: Mapping[str, object],
) -> str | None:
    if requested in pages:
        return str(requested)
    fallback = _PENDING_NAV_FALLBACKS.get(str(requested))
    return fallback if fallback in pages else None


def _activate_navigation(route: str) -> bool:
    group = group_for_route(route)
    if group is None:
        return False
    st.session_state["nav_choice"] = route
    st.session_state[_ACTIVE_NAV_STATE_KEY] = route
    st.session_state[_NAV_GROUP_STATE_KEY] = group
    _record_navigation_visit(route)
    return True


def _request_navigation(requested: str) -> bool:
    """Apply one leaf route through the Prompt Scratch dirty guard."""

    active = str(st.session_state.get(_ACTIVE_NAV_STATE_KEY, requested))
    bypass = st.session_state.pop(prompt_scratch.NAV_GUARD_BYPASS_STATE_KEY, None)
    if requested != active:
        # Sidebar choices are new navigation intents, never continuations of a
        # previously guarded Back cursor.  The approved Back flow is handled
        # separately by ``_apply_pending_navigation``.
        st.session_state.pop(_NAV_HISTORY_TARGET_STATE_KEY, None)
    if (
        active == prompt_scratch.PAGE_KEY
        and requested != active
        and bypass != requested
        and prompt_scratch.capture_before_navigation()
    ):
        st.session_state[prompt_scratch.NAV_GUARD_TARGET_STATE_KEY] = requested
        # Streamlit callbacks may restore their own widget value before the
        # next render.  The page remains mounted so no editor state is lost.
        st.session_state["nav_choice"] = active
        active_group = group_for_route(active)
        if active_group is not None:
            st.session_state[_NAV_GROUP_STATE_KEY] = active_group
        return False
    if requested != active:
        prompt_scratch.clear_superseded_guard_requests()
    return _activate_navigation(requested)


def _on_navigation_change() -> None:
    """Capture Prompt Scratch and hold leaf navigation until the author decides."""

    requested = str(st.session_state.get("nav_choice", "首頁"))
    if not _request_navigation(requested):
        active = str(st.session_state.get(_ACTIVE_NAV_STATE_KEY, "首頁"))
        st.session_state["nav_choice"] = active


def _on_navigation_group_change() -> None:
    """Turn a visible six-section choice into its stable landing leaf."""

    requested_group = str(st.session_state.get(_NAV_GROUP_STATE_KEY, HOME_GROUP_KEY))
    target = landing_route_for_group(requested_group)
    if target is not None and _request_navigation(target):
        return
    active = str(st.session_state.get(_ACTIVE_NAV_STATE_KEY, "首頁"))
    active_group = group_for_route(active) or HOME_GROUP_KEY
    st.session_state[_NAV_GROUP_STATE_KEY] = active_group
    st.session_state["nav_choice"] = active


def _apply_pending_navigation(pages: Mapping[str, object]) -> None:
    """Resolve programmatic navigation through the same Prompt Scratch guard."""

    current = st.session_state.get(_ACTIVE_NAV_STATE_KEY)
    if current not in pages:
        candidate = st.session_state.get("nav_choice")
        current = candidate if candidate in pages else "首頁"
    if not _activate_navigation(str(current)):
        current = "首頁"
        _activate_navigation(current)

    pending = st.session_state.pop("pending_nav", None)
    if (
        pending is None
        and prompt_scratch.NAV_GUARD_TARGET_STATE_KEY not in st.session_state
    ):
        # A guarded Back action can be cancelled from inside Prompt Scratch.
        # Once neither a guard nor an approved navigation remains, its cursor
        # target must not leak into a later, unrelated route change.
        st.session_state.pop(_NAV_HISTORY_TARGET_STATE_KEY, None)
    target = _navigation_target(pending, pages)
    if target is None:
        return

    bypass = st.session_state.pop(prompt_scratch.NAV_GUARD_BYPASS_STATE_KEY, None)
    if (
        current == prompt_scratch.PAGE_KEY
        and target != current
        and bypass != target
        and prompt_scratch.capture_before_navigation()
    ):
        st.session_state[prompt_scratch.NAV_GUARD_TARGET_STATE_KEY] = target
        _activate_navigation(str(current))
        return
    if target != current:
        prompt_scratch.clear_superseded_guard_requests()
    _activate_navigation(target)


def main() -> None:
    st.set_page_config(
        page_title="Imaginarium Forge · 你的故事書房",
        page_icon="✦",
        layout="wide",
        initial_sidebar_state="auto",
    )
    apply_theme()
    pages = {
        "首頁": home.render,
        free_creation_inbox.PAGE_KEY: free_creation_inbox.render,
        "專案": projects.render,
        character_drafts.PAGE_KEY: character_drafts.render,
        story_fragment_drafts.PAGE_KEY: story_fragment_drafts.render,
        world_seed_drafts.PAGE_KEY: world_seed_drafts.render,
        prompt_tag_builder.PAGE_KEY: prompt_tag_builder.render,
        prompt_scratch.PAGE_KEY: prompt_scratch.render,
        "靈感房": creative_automation.render,
        "Creative Launchpad": creative_launchpad.render,
        "Canon Vault": canon_vault.render,
        "角色": characters.render,
        "Style Profiles": style_profiles.render,
        "Prompt Studio": prompt_studio.render,
        "Story Studio": story_studio.render,
        ai_settings.PAGE_KEY: ai_settings.render,
        "Checkpoint Registry": checkpoint_registry.render,
        "Experiment Lab": experiment_lab.render,
        "System Health": system_health.render,
        # Keep help last: it is the stable, easy-to-find bottom item in the
        # creator-facing navigation rather than another project-only tool.
        guide.PAGE_KEY: guide.render,
    }
    if set(pages) != set(_NAV_LABELS):
        raise RuntimeError("page renderers and navigation registry are out of sync")
    _apply_pending_navigation(pages)
    choice = str(st.session_state.get(_ACTIVE_NAV_STATE_KEY, "首頁"))
    st.session_state[_ACTIVE_NAV_STATE_KEY] = choice
    if st.session_state.get(_LAST_RENDERED_NAV_STATE_KEY) != choice:
        _scroll_workspace_to_top(choice)
        st.session_state[_LAST_RENDERED_NAV_STATE_KEY] = choice

    selected_id = st.session_state.get("selected_project_id")
    project_name: str | None = None
    if selected_id:
        try:
            project = get_services().projects.get_project(selected_id)
            project_name = project.name
        except ApplicationError:
            project_name = None

    # A3-19 §25.3 item 10: keep drift visible without pushing navigation away.
    issues = check_environment()
    render_sidebar(
        current_route=choice,
        project_name=project_name,
        issues=issues,
        queue_navigation=_queue_navigation,
    )

    prompt_scratch.render_global_recovery_banner(services=get_services())
    _render_global_navigation(choice, project_name=project_name, issues=list(issues))
    render_mobile_navigation(current_route=choice, queue_navigation=_queue_navigation)
    with st.container(key="app_workspace"):
        pages[choice]()


if __name__ == "__main__":
    main()
