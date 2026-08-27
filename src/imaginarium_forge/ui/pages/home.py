"""The creator's study: recent books, the open project, and gentle next steps."""

from __future__ import annotations

from importlib import resources

import streamlit as st

from imaginarium_forge import __version__
from imaginarium_forge.application.errors import ApplicationError
from imaginarium_forge.domain.project.model import Project
from imaginarium_forge.ui.bootstrap import get_services
from imaginarium_forge.ui.components import (
    bookshelf_plank,
    creator_path_grid,
    empty_bookshelf,
    open_book,
    recent_book_card,
    section_heading,
)
from imaginarium_forge.ui.library import BookProgress, read_book_progress
from imaginarium_forge.ui.pages.character_drafts import PAGE_KEY as CHARACTER_DRAFTS_PAGE_KEY
from imaginarium_forge.ui.pages.free_creation_inbox import (
    PAGE_KEY as FREE_CREATION_INBOX_PAGE_KEY,
)
from imaginarium_forge.ui.pages.prompt_scratch import PAGE_KEY as PROMPT_SCRATCH_PAGE_KEY
from imaginarium_forge.ui.pages.prompt_tag_builder import (
    PAGE_KEY as PROMPT_TAG_BUILDER_PAGE_KEY,
)
from imaginarium_forge.ui.pages.story_fragment_drafts import (
    PAGE_KEY as STORY_FRAGMENT_DRAFTS_PAGE_KEY,
)
from imaginarium_forge.ui.pages.world_seed_drafts import (
    PAGE_KEY as WORLD_SEED_DRAFTS_PAGE_KEY,
)


def _go(page: str) -> None:
    st.session_state["pending_nav"] = page
    st.rerun()


@st.cache_data(show_spinner=False)
def _hero_asset() -> bytes:
    """Load the packaged hero image without assuming a source checkout."""

    return (
        resources.files("imaginarium_forge.ui")
        .joinpath("assets", "forge-study-hero.png")
        .read_bytes()
    )


def _go_story_section(section: str) -> None:
    """Open one existing Story Studio section without inventing a new route."""

    st.session_state["story_active_section"] = section
    st.session_state["_story_active_section_durable"] = section
    _go("Story Studio")


def _select_book(project: Project) -> None:
    st.session_state["selected_project_id"] = project.id
    st.session_state["home_book_notice"] = f"已打開《{project.name}》"
    st.rerun()


def _creator_entries() -> None:
    """Offer automation first, then the four persistent content pages."""

    st.markdown("#### 這次想從哪一頁開始？")
    st.caption("可以讓靈感房從一點線索開始，也可以直接翻到你熟悉的內容頁。")

    first = st.columns(2)
    if first[0].button(
        "✦ 靈感生成：幫我補足想法",
        key="home_go_starter",
        type="primary",
        use_container_width=True,
    ):
        _go("靈感房")
    if first[1].button(
        "✎ 完整創作與精修",
        key="home_go_refine",
        use_container_width=True,
    ):
        _go("Creative Launchpad")

    second = st.columns(4)
    if second[0].button("專案角色", key="home_go_characters", use_container_width=True):
        _go("角色")
    if second[1].button("世界觀設定", key="home_go_world", use_container_width=True):
        _go("Canon Vault")
    if second[2].button("故事／完整故事", key="home_go_story", use_container_width=True):
        _go("Story Studio")
    if second[3].button(
        "正式提示詞工作台", key="home_go_prompt", use_container_width=True
    ):
        _go("Prompt Studio")


def _draft_box_cta() -> None:
    """Keep all project-optional creation surfaces one click away."""

    section_heading(
        "快速創作",
        "不用先開作品。從繁體中文標籤、故事片段、角色、世界或圖片 Prompt 任選一種開始。",
        eyebrow="QUICK CREATE",
    )
    with st.container(key="home_quick_create"):
        buttons = st.columns(5)
        if buttons[0].button(
            "✦ 懶人標籤",
            key="home_go_prompt_tag_builder",
            type="primary",
            use_container_width=True,
        ):
            _go(PROMPT_TAG_BUILDER_PAGE_KEY)
        if buttons[1].button(
            "寫一段故事",
            key="home_go_story_fragment",
            use_container_width=True,
        ):
            _go(STORY_FRAGMENT_DRAFTS_PAGE_KEY)
        if buttons[2].button(
            "建立角色草稿",
            key="home_go_character_drafts",
            use_container_width=True,
        ):
            _go(CHARACTER_DRAFTS_PAGE_KEY)
        if buttons[3].button(
            "建立世界種子",
            key="home_go_world_seed_drafts",
            use_container_width=True,
        ):
            _go(WORLD_SEED_DRAFTS_PAGE_KEY)
        if buttons[4].button(
            "建立圖片 Prompt 草稿",
            key="home_go_prompt_scratch",
            use_container_width=True,
        ):
            _go(PROMPT_SCRATCH_PAGE_KEY)

        if st.button(
            "查看所有自由草稿",
            key="home_go_free_creation_inbox",
            use_container_width=True,
        ):
            _go(FREE_CREATION_INBOX_PAGE_KEY)


def _first_run_actions() -> None:
    """Present two clear first decisions, then keep every direct draft route."""

    st.markdown("#### 先選一種最順手的開始方式")
    st.caption("建立一本書會打開完整工作區；自由紙頁則可以先寫，之後再決定放進哪本作品。")
    paths = st.columns(2)
    if paths[0].button(
        "建立第一本書",
        key="home_open_projects",
        type="primary",
        use_container_width=True,
    ):
        _go("專案")
    if paths[1].button(
        "先寫一張自由紙頁",
        key="home_first_free_creation",
        use_container_width=True,
    ):
        _go(FREE_CREATION_INBOX_PAGE_KEY)

    st.caption("下方的快速創作工具也可以直接使用，內容會先留在自由草稿箱。")


def _read_progress(projects: list[Project]) -> list[tuple[Project, BookProgress]]:
    services = get_services()
    summaries: list[tuple[Project, BookProgress]] = []
    for project in projects:
        try:
            progress = read_book_progress(services, project)
        except ApplicationError:
            # Keep one damaged book from hiding the rest of the shelf.  The
            # destination page will still surface the actionable error.
            progress = BookProgress(updated_at=project.updated_at)
        summaries.append((project, progress))
    return summaries


def _recent_shelf(
    summaries: list[tuple[Project, BookProgress]], selected_id: str | None
) -> None:
    for row_start in range(0, len(summaries), 3):
        columns = st.columns(3)
        for offset, (project, progress) in enumerate(summaries[row_start : row_start + 3]):
            with columns[offset]:
                shelf_position = row_start + offset + 1
                recent_book_card(
                    name=project.name,
                    description=project.description,
                    progress=progress,
                    cover_number=row_start + offset,
                    selected=project.id == selected_id,
                )
                button_label = (
                    f"繼續《{project.name}》"
                    if project.id == selected_id
                    else f"打開《{project.name}》"
                )
                if st.button(
                    button_label,
                    key=f"home_open_book_{project.id}",
                    help=f"書架第 {shelf_position} 本",
                    type="primary" if project.id == selected_id else "secondary",
                    use_container_width=True,
                ):
                    _select_book(project)
        bookshelf_plank()


def _hero(current: Project | None, *, has_projects: bool) -> None:
    """Render a truthful dashboard hero with one original local illustration."""

    with st.container(key="home_hero"):
        copy, artwork = st.columns((0.92, 1.18), vertical_alignment="center")
        with copy:
            st.markdown(
                '<div class="if-kicker">IMAGINARIUM FORGE · 本機創作工作室</div>',
                unsafe_allow_html=True,
            )
            st.title("今天想打造什麼故事宇宙？")
            st.markdown(
                "把角色、世界、故事與提示詞整理在同一間書房；AI 只提出候選，正式內容永遠由你決定。"
            )
            st.html(
                '<div class="if-badges">'
                '<span class="if-badge teal">文字預設留在本機</span>'
                '<span class="if-badge">版本化保存</span>'
                "</div>"
            )
            actions = st.columns(2)
            if current is not None:
                if actions[0].button(
                    f"繼續《{current.name}》",
                    key="home_hero_continue",
                    type="primary",
                    use_container_width=True,
                ):
                    _go("Story Studio")
                if actions[1].button(
                    "進入創作工作室",
                    key="home_hero_studio",
                    use_container_width=True,
                ):
                    _go("Creative Launchpad")
            else:
                if actions[0].button(
                    "開始自由創作",
                    key="home_hero_free_creation",
                    type="primary",
                    use_container_width=True,
                ):
                    _go(FREE_CREATION_INBOX_PAGE_KEY)
                if actions[1].button(
                    "打開我的書架" if has_projects else "建立第一本作品",
                    key="home_hero_library",
                    use_container_width=True,
                ):
                    _go("專案")
        with artwork:
            st.image(_hero_asset(), use_container_width=True)


def render() -> None:
    services = get_services()
    all_projects = services.projects.list_projects(include_archived=True)
    active_projects = [
        project for project in all_projects if project.status.value == "active"
    ]
    selected_id = st.session_state.get("selected_project_id")
    current = next(
        (project for project in all_projects if project.id == selected_id), None
    )

    _hero(current, has_projects=bool(all_projects))

    notice = st.session_state.pop("home_book_notice", None)
    if isinstance(notice, str) and notice:
        st.success(notice)

    # Project metadata is one inexpensive list read.  Pick the six recent
    # covers first, then hydrate only those summaries; a large library must not
    # multiply five content-list reads by every book just to render the home.
    recent_projects = sorted(
        active_projects,
        key=lambda project: (project.updated_at, project.id),
        reverse=True,
    )[:6]
    summaries = _read_progress(recent_projects)
    current_progress = None
    if current is not None:
        current_progress = next(
            (progress for project, progress in summaries if project.id == current.id), None
        )
        if current_progress is None:
            try:
                current_progress = read_book_progress(services, current)
            except ApplicationError:
                current_progress = BookProgress(updated_at=current.updated_at)

    if current is not None and current_progress is not None:
        section_heading(
            "目前作品",
            "接著上次的位置工作，或切換到角色、世界觀、Prompt 與故事工作區。",
            eyebrow="CURRENT PROJECT",
        )
        with st.container(key="home_current_project"):
            open_book(
                name=current.name,
                description=current.description,
                progress=current_progress,
                archived=current.status.value == "archived",
            )
            _creator_entries()

    section_heading(
        "最近作品",
        "最近編輯的作品排在前面；卡片只顯示實際存在的內容與更新日期。",
        eyebrow="RECENT WORK",
    )
    if not summaries:
        empty_bookshelf()
        _first_run_actions()
        if all_projects:
            st.caption("書架上的作品目前都已收起；到「我的書架」可以重新放回來。")
    else:
        st.caption("最近碰過的作品排在前面。每一本書都收著自己的角色、世界、故事與提示詞。")
        with st.container(key="home_recent_grid"):
            _recent_shelf(summaries, selected_id)
        if len(active_projects) > 6:
            st.caption(f"還有 {len(active_projects) - 6} 本作品收藏在完整書架裡。")
        if st.button("看看完整書架", key="home_open_projects", use_container_width=True):
            _go("專案")

    _draft_box_cta()

    section_heading(
        "草稿與故事記憶",
        "自由草稿不會自動變成正式作品；故事記憶也必須經過你接受才會加入上下文。",
        eyebrow="DRAFTS & MEMORY",
    )
    draft_area, memory_area = st.columns(2)
    with draft_area.container(border=True):
        st.markdown("#### 自由創作箱")
        st.caption("集中查看已保存的故事片段、角色草稿、世界種子與圖片 Prompt 草稿。")
        if st.button(
            "整理自由草稿",
            key="home_open_all_drafts",
            use_container_width=True,
        ):
            _go(FREE_CREATION_INBOX_PAGE_KEY)
    with memory_area.container(border=True):
        st.markdown("#### Story Memory")
        if current is None:
            st.caption("先打開一本作品，才能審核該作品已接受的記憶與提案。")
            st.button(
                "需要先開啟作品",
                key="home_open_story_memory_disabled",
                disabled=True,
                use_container_width=True,
            )
        else:
            st.caption(f"查看《{current.name}》的記憶提案、接受紀錄與來源證據。")
            if st.button(
                "打開故事記憶",
                key="home_open_story_memory",
                use_container_width=True,
            ):
                _go_story_section("memory")

    section_heading(
        "選一種順手的創作方式",
        "自由輸入、離線隨機與 AI 候選可以混合使用；任何候選都不會靜默覆寫正式內容。",
        eyebrow="CREATIVE PATHS",
    )
    creator_path_grid(
        (
            (
                "✦",
                "只給一點線索",
                "留下一個關鍵字或半個念頭，讓靈感房補足世界、角色細節、背景故事與走向。",
            ),
            (
                "❖",
                "一次長出完整設定",
                "一鍵隨機角色、故事，或單獨世界觀；每個作品與角色仍然由你命名、修改。",
            ),
            (
                "✎",
                "直接接著自己的文字寫",
                "把已有上下文放進故事頁，指定關鍵字與期望走向，再接續成下一段。",
            ),
        )
    )

    with st.container(key="home_local_first_note"):
        st.info(
            "**這是你的本機書房。** 資料庫、故事與提示預設留在這台電腦；"
            "應用程式沒有獨立遙測或上傳。"
            "若日後把模型端點改成遠端服務，送出的生成內容仍會傳往該端點。"
        )
    st.caption(f"Imaginarium Forge v{__version__} · 保存版本，不覆蓋你的創作歷史")
