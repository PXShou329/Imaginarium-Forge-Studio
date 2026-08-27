"""The project library presented as a shelf of editable, recoverable books."""

from __future__ import annotations

import streamlit as st

from imaginarium_forge.application.errors import ApplicationError
from imaginarium_forge.domain.project.model import Project
from imaginarium_forge.ui.bootstrap import get_services
from imaginarium_forge.ui.components import (
    book_cover,
    bookshelf_plank,
    empty_bookshelf,
    open_book,
    page_header,
)
from imaginarium_forge.ui.library import BookProgress, read_book_progress
from imaginarium_forge.ui.project_gateway import consume_project_create_request

_BOOKS_PER_PAGE = 12
_SORT_RECENT = "最近編輯"
_SORT_NAME = "書名 A–Z"


def _go(page: str) -> None:
    st.session_state["pending_nav"] = page
    st.rerun()


def _book_entries(project_id: str) -> None:
    """Keep automation, refinement, and durable content pages one click away."""

    st.markdown("#### 翻到這本書的……")
    first = st.columns(2)
    if first[0].button(
        "✦ 靈感房：一鍵長出內容",
        key=f"project_book_inspiration_{project_id}",
        type="primary",
        use_container_width=True,
    ):
        _go("靈感房")
    if first[1].button(
        "✎ 精修工作台：自己慢慢調",
        key=f"project_book_refine_{project_id}",
        use_container_width=True,
    ):
        _go("Creative Launchpad")

    pages = st.columns(4)
    destinations = (
        ("角色頁", "角色", "characters"),
        ("世界頁", "Canon Vault", "world"),
        ("故事／完整故事", "Story Studio", "story"),
        ("提示詞頁", "Prompt Studio", "prompts"),
    )
    for column, (label, target, key_part) in zip(pages, destinations, strict=True):
        if column.button(
            label,
            key=f"project_book_{key_part}_{project_id}",
            use_container_width=True,
        ):
            _go(target)


def _safe_progress(project: Project) -> BookProgress:
    services = get_services()
    try:
        return read_book_progress(services, project)
    except ApplicationError:
        return BookProgress(updated_at=project.updated_at)


def _create_book(*, expanded: bool) -> None:
    services = get_services()
    with st.expander("＋ 把一本新書放上架", expanded=expanded):
        st.caption("只需要先取名字。描述可以是一句關鍵字，也可以完全留白。")
        name = st.text_input("書名", key="new_project_name", placeholder="例如：月蝕之城")
        description = st.text_area(
            "現在想到什麼？",
            key="new_project_desc",
            placeholder="一個畫面、角色、世界規則或故事方向都可以……",
        )
        if st.button(
            "把這本書放上書架",
            key="create_project_btn",
            type="primary",
            use_container_width=True,
        ):
            try:
                project = services.projects.create_project(
                    name=name, description=description
                )
                st.session_state["selected_project_id"] = project.id
                st.session_state["project_open_notice"] = (
                    f"已建立專案：{project.name}"
                )
                st.rerun()
            except ApplicationError as exc:
                st.error(str(exc))


def _edit_book(project: Project, *, shelf_position: int) -> None:
    services = get_services()
    with st.expander(
        f"調整《{project.name}》的書名與簡介（書架第 {shelf_position} 本）",
        expanded=False,
    ):
        e_name = st.text_input(
            "書名", value=project.name, key=f"edit_name_{project.id}"
        )
        e_desc = st.text_area(
            "簡介", value=project.description, key=f"edit_desc_{project.id}"
        )
        e_lang = st.text_input(
            "主要寫作語言",
            value=project.default_language,
            key=f"edit_lang_{project.id}",
        )
        if st.button(
            "收好變更", key=f"edit_save_{project.id}", use_container_width=True
        ):
            try:
                services.projects.update_metadata(
                    project.id,
                    name=e_name,
                    description=e_desc,
                    default_language=e_lang,
                )
                st.session_state["project_open_notice"] = "已更新這本書的資料"
                st.rerun()
            except ApplicationError as exc:
                st.error(str(exc))


def _shelf_card(project: Project, progress: BookProgress, cover_number: int) -> None:
    services = get_services()
    selected = st.session_state.get("selected_project_id") == project.id
    archived = project.status.value == "archived"
    shelf_position = cover_number + 1
    label_suffix = f"（書架第 {shelf_position} 本）"
    book_cover(
        name=project.name,
        description=project.description,
        progress=progress,
        cover_number=cover_number,
        selected=selected,
        archived=archived,
    )

    if st.button(
        (
            f"回到《{project.name}》{label_suffix}"
            if selected
            else f"打開《{project.name}》{label_suffix}"
        ),
        key=f"open_{project.id}",
        type="primary" if selected else "secondary",
        use_container_width=True,
    ):
        st.session_state["selected_project_id"] = project.id
        # Preserve the established one-shot notice contract used by AppTests.
        st.session_state["project_open_notice"] = f"已選取：{project.name}"
        st.rerun()

    _edit_book(project, shelf_position=shelf_position)
    if not archived:
        if st.button(
            f"暫時收起《{project.name}》{label_suffix}",
            key=f"archive_{project.id}",
            help="內容不會刪除，之後可以隨時放回書架。",
            use_container_width=True,
        ):
            services.projects.archive_project(project.id)
            st.rerun()
    elif st.button(
        f"把《{project.name}》放回書架{label_suffix}",
        key=f"restore_{project.id}",
        use_container_width=True,
    ):
        services.projects.restore_project(project.id)
        st.rerun()


def render() -> None:
    page_header(
        "我的書架",
        "每一本書都是獨立的創作世界；角色、設定、故事與提示詞會一起收在裡面。",
        eyebrow="挑一本繼續寫，或替新念頭留個位置",
        badges=(("作品只留在本機", "teal"),),
    )
    services = get_services()
    all_projects = services.projects.list_projects(include_archived=True)
    active_projects = [
        project for project in all_projects if project.status.value == "active"
    ]

    open_notice = st.session_state.pop("project_open_notice", None)
    if isinstance(open_notice, str) and open_notice:
        st.success(open_notice)

    selected_id = st.session_state.get("selected_project_id")
    current = next(
        (project for project in all_projects if project.id == selected_id), None
    )
    if current is not None:
        st.markdown("## 現在打開的書")
        open_book(
            name=current.name,
            description=current.description,
            progress=_safe_progress(current),
            archived=current.status.value == "archived",
        )
        _book_entries(current.id)
        st.divider()

    _create_book(
        expanded=consume_project_create_request(
            st.session_state, no_active_projects=not active_projects
        )
    )
    show_archived = st.checkbox(
        "也看看暫時收起的書", value=False, key="projects_show_archived"
    )
    available_projects = all_projects if show_archived else active_projects

    browse_columns = st.columns([2, 1])
    search = browse_columns[0].text_input(
        "搜尋書名或簡介",
        key="library_search",
        placeholder="輸入作品名稱或記得的一小段描述……",
    )
    sort_choice = browse_columns[1].selectbox(
        "排列方式",
        (_SORT_RECENT, _SORT_NAME),
        key="library_sort",
    )

    needle = search.strip().casefold()
    projects = [
        project
        for project in available_projects
        if not needle
        or needle in project.name.casefold()
        or needle in project.description.casefold()
    ]
    if sort_choice == _SORT_NAME:
        projects.sort(key=lambda project: (project.name.casefold(), project.id))
    else:
        projects.sort(
            key=lambda project: (project.updated_at, project.id), reverse=True
        )

    st.markdown("## 書架上的作品")
    if not available_projects:
        empty_bookshelf()
        st.caption("在上方取一個書名，就能先把位置留下；內容不必現在全部想好。")
        return
    if not projects:
        st.info(f"找不到包含「{search.strip()}」的作品。換個字詞再找找看。")
        return

    page_count = max(1, (len(projects) + _BOOKS_PER_PAGE - 1) // _BOOKS_PER_PAGE)
    stored_page = st.session_state.get("library_page", 1)
    requested_page = stored_page if isinstance(stored_page, int) else 1
    safe_page = min(max(requested_page, 1), page_count)
    if stored_page != safe_page:
        # Normalize before the keyed widget is created; changing it after
        # instantiation would violate Streamlit's session-state contract.
        st.session_state["library_page"] = safe_page
    page = int(
        st.number_input(
            "書架頁次",
            min_value=1,
            max_value=page_count,
            step=1,
            key="library_page",
        )
    )
    page_start = (page - 1) * _BOOKS_PER_PAGE
    page_projects = projects[page_start : page_start + _BOOKS_PER_PAGE]
    st.caption(
        f"第 {page} / {page_count} 頁 · 共 {len(projects)} 本 · 每頁最多 {_BOOKS_PER_PAGE} 本"
    )

    # Filtering, metadata sorting, and pagination all happen before these
    # content summaries are hydrated.  A 500-book library therefore performs
    # at most twelve card-summary reads for the visible page.
    summaries = [(project, _safe_progress(project)) for project in page_projects]
    for row_start in range(0, len(summaries), 2):
        columns = st.columns(2)
        for offset, (project, progress) in enumerate(summaries[row_start : row_start + 2]):
            with columns[offset]:
                _shelf_card(
                    project,
                    progress,
                    page_start + row_start + offset,
                )
        bookshelf_plank()
