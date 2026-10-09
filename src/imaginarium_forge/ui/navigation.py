"""Typed six-section navigation over stable legacy leaf route keys."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from imaginarium_forge.ui.pages import (
    ai_settings,
    character_drafts,
    free_creation_inbox,
    guide,
    offline_translation,
    prompt_scratch,
    prompt_tag_builder,
    story_fragment_drafts,
    world_seed_drafts,
)

HOME_GROUP_KEY = "home"
FREE_CREATION_GROUP_KEY = "free_creation"
LIBRARY_GROUP_KEY = "library"
CURRENT_WORK_GROUP_KEY = "current_work"
AI_TOOLS_GROUP_KEY = "ai_tools"
SETTINGS_HELP_GROUP_KEY = "settings_help"
OFFLINE_TOOLS_GROUP_KEY = "offline_tools"


class RoutePlacement(StrEnum):
    """Where a registered direct route may be discovered in the product shell."""

    SIDEBAR = "sidebar"
    TOPBAR = "topbar"
    STATUS_ONLY = "status_only"
    HIDDEN = "hidden"


@dataclass(frozen=True, slots=True)
class NavigationGroup:
    key: str
    label: str
    icon: str
    description: str
    landing_route: str
    routes: tuple[str, ...]


NAVIGATION_GROUPS = (
    NavigationGroup(
        key=HOME_GROUP_KEY,
        label="首頁",
        icon="⌂",
        description="今天的創作入口",
        landing_route="首頁",
        routes=("首頁",),
    ),
    NavigationGroup(
        key=FREE_CREATION_GROUP_KEY,
        label="自由創作",
        icon="✦",
        description="不開作品也能先寫",
        landing_route=free_creation_inbox.PAGE_KEY,
        routes=(
            free_creation_inbox.PAGE_KEY,
            prompt_tag_builder.PAGE_KEY,
            story_fragment_drafts.PAGE_KEY,
            character_drafts.PAGE_KEY,
            world_seed_drafts.PAGE_KEY,
            prompt_scratch.PAGE_KEY,
        ),
    ),
    NavigationGroup(
        key=LIBRARY_GROUP_KEY,
        label="我的書架",
        icon="▤",
        description="管理所有作品",
        landing_route="專案",
        routes=("專案",),
    ),
    NavigationGroup(
        key=CURRENT_WORK_GROUP_KEY,
        label="創作工作室",
        icon="✎",
        description="目前作品的完整工具",
        landing_route="Creative Launchpad",
        routes=(
            "Creative Launchpad",
            "靈感房",
            "Canon Vault",
            "角色",
            "Style Profiles",
            "Prompt Studio",
            "Story Studio",
        ),
    ),
    NavigationGroup(
        key=AI_TOOLS_GROUP_KEY,
        label="AI 與工具",
        icon="⚙",
        description="模型、檢查點與實驗",
        landing_route="Checkpoint Registry",
        routes=(
            "Checkpoint Registry",
            "Experiment Lab",
        ),
    ),
    NavigationGroup(
        key=SETTINGS_HELP_GROUP_KEY,
        label="設定與說明",
        icon="?",
        description="備份、健康狀態與指南",
        landing_route="System Health",
        routes=("System Health", guide.PAGE_KEY),
    ),
)

_OFFLINE_TOOLS_CONTEXT_GROUP = NavigationGroup(
    key=OFFLINE_TOOLS_GROUP_KEY,
    label="離線工具",
    icon="⇄",
    description="完全在本機處理的語言工具",
    landing_route=offline_translation.PAGE_KEY,
    routes=(offline_translation.PAGE_KEY,),
)

NAVIGATION_GROUPS_BY_KEY = {
    group.key: group for group in (*NAVIGATION_GROUPS, _OFFLINE_TOOLS_CONTEXT_GROUP)
}
SIDEBAR_ROUTES = tuple(route for group in NAVIGATION_GROUPS for route in group.routes)
TOPBAR_ROUTES = (offline_translation.PAGE_KEY,)
STATUS_ONLY_ROUTES = (ai_settings.PAGE_KEY,)
HIDDEN_ROUTES: tuple[str, ...] = ()
SEARCHABLE_ROUTES = SIDEBAR_ROUTES
SEQUENCE_ROUTES = SIDEBAR_ROUTES

ROUTE_TO_GROUP = {
    route: group.key for group in NAVIGATION_GROUPS for route in group.routes
}
ROUTE_TO_GROUP.update(
    {
        ai_settings.PAGE_KEY: AI_TOOLS_GROUP_KEY,
        offline_translation.PAGE_KEY: OFFLINE_TOOLS_GROUP_KEY,
    }
)

# A deterministic reading order for the application-owned Previous / Next
# controls. Browser history is intentionally not involved: Streamlit widget
# state and the Prompt Scratch dirty guard both live inside this route model.
ORDERED_ROUTES = SEQUENCE_ROUTES

ROUTE_LABELS = {
    "首頁": "首頁・開始創作",
    free_creation_inbox.PAGE_KEY: free_creation_inbox.PAGE_LABEL,
    story_fragment_drafts.PAGE_KEY: "故事片段草稿（免建專案）",
    character_drafts.PAGE_KEY: "角色與故事草稿（免建專案）",
    world_seed_drafts.PAGE_KEY: "世界種子草稿（免建專案）",
    prompt_tag_builder.PAGE_KEY: "懶人標籤生成器（免寫英文）",
    prompt_scratch.PAGE_KEY: "圖片提示詞草稿（免建專案）",
    "專案": "作品書架",
    "Creative Launchpad": "完整創作與精修",
    "靈感房": "靈感生成",
    "Canon Vault": "世界觀設定",
    "角色": "專案角色",
    "Style Profiles": "圖片風格",
    "Prompt Studio": "正式提示詞工作台",
    "Story Studio": "故事寫作",
    ai_settings.PAGE_KEY: "AI 設定（API Key 與模型）",
    "Checkpoint Registry": "圖片模型檔案庫",
    "Experiment Lab": "生成實驗室",
    "System Health": "設定與備份",
    guide.PAGE_KEY: guide.PAGE_LABEL,
    offline_translation.PAGE_KEY: offline_translation.PAGE_LABEL,
}

ROUTE_ICONS = {
    "首頁": "⌂",
    free_creation_inbox.PAGE_KEY: "✦",
    story_fragment_drafts.PAGE_KEY: "✎",
    character_drafts.PAGE_KEY: "♙",
    world_seed_drafts.PAGE_KEY: "◇",
    prompt_tag_builder.PAGE_KEY: "⌘",
    prompt_scratch.PAGE_KEY: "▧",
    "專案": "▤",
    "Creative Launchpad": "↗",
    "靈感房": "✧",
    "Canon Vault": "◈",
    "角色": "♙",
    "Style Profiles": "◐",
    "Prompt Studio": "⌘",
    "Story Studio": "✎",
    ai_settings.PAGE_KEY: "⚙",
    "Checkpoint Registry": "△",
    "Experiment Lab": "⌁",
    "System Health": "●",
    guide.PAGE_KEY: "?",
    offline_translation.PAGE_KEY: "⇄",
}

ROUTE_DESCRIPTIONS = {
    "首頁": "最近作品、書架與快速入口",
    free_creation_inbox.PAGE_KEY: "集中整理所有免建專案草稿",
    story_fragment_drafts.PAGE_KEY: "先寫片段，之後再收進作品",
    character_drafts.PAGE_KEY: "建立角色與背景故事草稿",
    world_seed_drafts.PAGE_KEY: "從簡短概念長出世界觀",
    prompt_tag_builder.PAGE_KEY: "用繁體中文標籤組合英文 Prompt",
    prompt_scratch.PAGE_KEY: "暫存、復原與精修圖片提示詞",
    "專案": "建立、開啟與封存作品",
    "Creative Launchpad": "從想法建立完整創作素材",
    "靈感房": "離線隨機與 AI 候選生成",
    "Canon Vault": "掌握作品的世界觀與一致性",
    "角色": "管理作品內的正式角色",
    "Style Profiles": "管理可重用的視覺風格",
    "Prompt Studio": "建立可匯出的正式媒體 Prompt",
    "Story Studio": "規劃、寫作、續寫與改編",
    ai_settings.PAGE_KEY: "設定工作階段模型與 API Key",
    "Checkpoint Registry": "登錄本機圖片模型資訊",
    "Experiment Lab": "比較候選結果，不覆寫正式內容",
    "System Health": "檢查環境、備份與還原",
    guide.PAGE_KEY: "依工作目標找到正確工具",
    offline_translation.PAGE_KEY: "繁體中文與英文的完全離線雙向翻譯",
}

# Registration is independent from discovery placement. Preserve the stable
# metadata order so existing widget keys do not drift while adding direct-only
# routes to the end of the registry.
REGISTERED_ROUTES = tuple(ROUTE_LABELS)
ALL_ROUTES = tuple(REGISTERED_ROUTES)
ROUTE_PLACEMENTS = {
    **{route: RoutePlacement.SIDEBAR for route in SIDEBAR_ROUTES},
    **{route: RoutePlacement.TOPBAR for route in TOPBAR_ROUTES},
    **{route: RoutePlacement.STATUS_ONLY for route in STATUS_ONLY_ROUTES},
    **{route: RoutePlacement.HIDDEN for route in HIDDEN_ROUTES},
}


def group_for_route(route: str) -> str | None:
    return ROUTE_TO_GROUP.get(route)


def landing_route_for_group(group_key: str) -> str | None:
    group = NAVIGATION_GROUPS_BY_KEY.get(group_key)
    return group.landing_route if group is not None else None


def routes_for_group(group_key: str) -> tuple[str, ...]:
    group = NAVIGATION_GROUPS_BY_KEY.get(group_key)
    return group.routes if group is not None else ()


def adjacent_route(route: str, offset: int) -> str | None:
    """Return a stable neighbouring route without wrapping at either edge."""

    if offset not in {-1, 1}:
        raise ValueError("navigation offset must be -1 or 1")
    try:
        target_index = ORDERED_ROUTES.index(route) + offset
    except ValueError:
        return None
    if not 0 <= target_index < len(ORDERED_ROUTES):
        return None
    return ORDERED_ROUTES[target_index]


def _validate_registry() -> None:
    if len(SIDEBAR_ROUTES) != len(set(SIDEBAR_ROUTES)):
        raise RuntimeError("sidebar routes must belong to exactly one section")
    placement_routes = (
        *SIDEBAR_ROUTES,
        *TOPBAR_ROUTES,
        *STATUS_ONLY_ROUTES,
        *HIDDEN_ROUTES,
    )
    if len(placement_routes) != len(set(placement_routes)):
        raise RuntimeError("registered routes must have exactly one placement")
    if set(REGISTERED_ROUTES) != set(placement_routes):
        raise RuntimeError("registered routes and placements are out of sync")
    if set(REGISTERED_ROUTES) != set(ROUTE_TO_GROUP):
        raise RuntimeError("registered routes and route context are out of sync")
    if set(ROUTE_LABELS) != set(REGISTERED_ROUTES):
        raise RuntimeError("navigation labels and routes are out of sync")
    if set(ROUTE_ICONS) != set(REGISTERED_ROUTES):
        raise RuntimeError("navigation icons and routes are out of sync")
    if set(ROUTE_DESCRIPTIONS) != set(REGISTERED_ROUTES):
        raise RuntimeError("navigation descriptions and routes are out of sync")
    if set(ROUTE_PLACEMENTS) != set(REGISTERED_ROUTES):
        raise RuntimeError("route placement metadata and routes are out of sync")
    if ORDERED_ROUTES != SIDEBAR_ROUTES:
        raise RuntimeError("previous/next sequence must match sidebar route order")
    if any(group.landing_route not in group.routes for group in NAVIGATION_GROUPS):
        raise RuntimeError("each navigation landing route must belong to its section")


_validate_registry()


__all__ = [
    "AI_TOOLS_GROUP_KEY",
    "ALL_ROUTES",
    "CURRENT_WORK_GROUP_KEY",
    "FREE_CREATION_GROUP_KEY",
    "HIDDEN_ROUTES",
    "HOME_GROUP_KEY",
    "LIBRARY_GROUP_KEY",
    "NAVIGATION_GROUPS",
    "NAVIGATION_GROUPS_BY_KEY",
    "OFFLINE_TOOLS_GROUP_KEY",
    "ORDERED_ROUTES",
    "REGISTERED_ROUTES",
    "ROUTE_DESCRIPTIONS",
    "ROUTE_ICONS",
    "ROUTE_LABELS",
    "ROUTE_PLACEMENTS",
    "ROUTE_TO_GROUP",
    "SEARCHABLE_ROUTES",
    "SEQUENCE_ROUTES",
    "SETTINGS_HELP_GROUP_KEY",
    "SIDEBAR_ROUTES",
    "STATUS_ONLY_ROUTES",
    "TOPBAR_ROUTES",
    "NavigationGroup",
    "RoutePlacement",
    "adjacent_route",
    "group_for_route",
    "landing_route_for_group",
    "routes_for_group",
]
