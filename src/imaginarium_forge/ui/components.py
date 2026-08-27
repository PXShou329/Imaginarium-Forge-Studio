"""Small, presentation-only components shared by Streamlit pages."""

from __future__ import annotations

from datetime import datetime
from html import escape

import streamlit as st

from imaginarium_forge.ui.library import BookProgress


def sidebar_brand() -> None:
    """Render the product mark without loading any remote asset."""

    st.sidebar.markdown(
        """
        <div class="if-brand">
          <div class="if-brand-mark" aria-hidden="true">✦</div>
          <div class="if-brand-name">Imaginarium Forge</div>
          <div class="if-brand-sub">你的私人故事書房</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def page_header(
    title: str,
    subtitle: str,
    *,
    eyebrow: str,
    badges: tuple[tuple[str, str], ...] = (),
) -> None:
    """Keep a real Streamlit title while adding a consistent product header."""

    with st.container(key="page_header"):
        st.markdown(
            f'<div class="if-kicker">{escape(eyebrow)}</div>',
            unsafe_allow_html=True,
        )
        st.title(title)
        st.caption(subtitle)
        if badges:
            rendered = "".join(
                f'<span class="if-badge {escape(tone)}">{escape(label)}</span>'
                for label, tone in badges
            )
            st.markdown(
                f'<div class="if-badges">{rendered}</div>', unsafe_allow_html=True
            )
        st.html('<div class="if-page-header-rule" aria-hidden="true"></div>')


def section_heading(title: str, description: str, *, eyebrow: str = "") -> None:
    """Render a consistent dashboard/workspace section introduction."""

    kicker = (
        f'<span class="if-section-eyebrow">{escape(eyebrow)}</span>' if eyebrow else ""
    )
    st.html(
        '<div class="if-section-heading">'
        f"<div>{kicker}<h2>{escape(title)}</h2><p>{escape(description)}</p></div>"
        "</div>"
    )


def recent_book_card(
    *,
    name: str,
    description: str,
    progress: BookProgress,
    cover_number: int,
    selected: bool = False,
) -> None:
    """Render one compact, truthful recent-project card for the dashboard."""

    tones = ("garnet", "forest", "indigo", "ochre")
    tone = tones[cover_number % len(tones)]
    selected_class = " is-selected" if selected else ""
    synopsis = description.strip() or "這本作品正等著你的下一句。"
    facts = (
        f"{progress.characters} 角色 · {progress.worlds} 世界 · "
        f"{progress.stories} 故事 · {progress.prompts} Prompt"
    )
    st.html(
        f"""
        <article class="if-recent-book {tone}{selected_class}"
                 aria-label="最近作品《{escape(name)}》">
          <div class="if-recent-book-art" aria-hidden="true">
            <span>IF</span><strong>{cover_number + 1:02d}</strong>
          </div>
          <div class="if-recent-book-copy">
            <span>{'目前作品' if selected else '最近編輯'}</span>
            <h3>{escape(name)}</h3>
            <p>{escape(synopsis)}</p>
            <small>{escape(facts)}</small>
            <time>{escape(_book_date(progress.updated_at))}</time>
          </div>
        </article>
        """
    )


def capability_grid(
    items: tuple[tuple[str, str, str, str], ...],
) -> None:
    """Render stable product-capability cards as one accessible HTML group."""

    # Keep the fragment unindented. Markdown treats a later indented HTML
    # sibling as a code block, which made cards two and three appear as source.
    cards = "".join(
        '<article class="if-cap-card">'
        f'<div class="if-cap-index">{escape(index)}</div>'
        f'<div class="if-cap-title">{escape(title)}</div>'
        f'<div class="if-cap-copy">{escape(copy)}</div>'
        f'<div class="if-cap-state">{escape(state)}</div>'
        "</article>"
        for index, title, copy, state in items
    )
    # ``st.html`` bypasses Markdown's block parsing, so sibling cards remain
    # HTML even when the parser would otherwise turn them into a code block.
    st.html(f'<div class="if-cap-grid">{cards}</div>')


def creator_path_grid(items: tuple[tuple[str, str, str], ...]) -> None:
    """Render friendly, non-technical ways to begin a creative session."""

    cards = "".join(
        '<article class="if-path-card">'
        f'<div class="if-path-icon" aria-hidden="true">{escape(icon)}</div>'
        f'<div class="if-path-title">{escape(title)}</div>'
        f'<div class="if-path-copy">{escape(copy)}</div>'
        "</article>"
        for icon, title, copy in items
    )
    st.html(f'<div class="if-path-grid">{cards}</div>')


def _book_date(value: str) -> str:
    """Return a compact, forgiving date for a book-cover footer."""

    if not value:
        return "等待第一筆內容"
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value[:10]
    return f"{parsed.year:04d}.{parsed.month:02d}.{parsed.day:02d}"


def book_cover(
    *,
    name: str,
    description: str,
    progress: BookProgress,
    cover_number: int,
    selected: bool = False,
    archived: bool = False,
) -> None:
    """Render a cloth-bound project cover; actions remain native buttons."""

    tones = ("garnet", "forest", "indigo", "ochre")
    tone = tones[cover_number % len(tones)]
    selected_class = " is-selected" if selected else ""
    archived_class = " is-archived" if archived else ""
    state = "正在閱讀" if selected else ("暫時收起" if archived else "收藏於書架")
    synopsis = description.strip() or "這本書還留著空白，等你寫下第一句。"
    folio = (
        f"{progress.characters} 角色 · {progress.worlds} 世界 · "
        f"{progress.stories} 故事 · {progress.prompts} 提示詞"
    )
    st.html(
        f"""
        <article class="if-book-cover {tone}{selected_class}{archived_class}"
                 aria-label="作品《{escape(name)}》">
          <div class="if-book-spine" aria-hidden="true">
            <span>IF</span><span>{cover_number + 1:02d}</span>
          </div>
          <div class="if-book-face">
            <div class="if-book-state">{escape(state)}</div>
            <div class="if-book-ornament" aria-hidden="true">✦</div>
            <h3>{escape(name)}</h3>
            <p>{escape(synopsis)}</p>
            <div class="if-book-folio">{escape(folio)}</div>
            <div class="if-book-date">最近編輯 · {escape(_book_date(progress.updated_at))}</div>
          </div>
        </article>
        """
    )


def bookshelf_plank() -> None:
    """Add a small visual shelf edge beneath a row of covers."""

    st.html('<div class="if-shelf-plank" aria-hidden="true"></div>')


def empty_bookshelf() -> None:
    """Render the first-run shelf state while the native create form stays usable."""

    st.html(
        """
        <section class="if-empty-shelf" aria-label="空書架">
          <div class="if-empty-shelf-stars" aria-hidden="true">✦　·　✧</div>
          <h2>書架還空著，正好放進第一個世界</h2>
          <p>不必先想完整。取個書名、留下一句念頭，其餘可以交給靈感房慢慢長出來。</p>
          <div class="if-empty-book" aria-hidden="true"><span>你的第一本書</span></div>
        </section>
        """
    )


def open_book(
    *,
    name: str,
    description: str,
    progress: BookProgress,
    archived: bool = False,
) -> None:
    """Render the selected project as an open book with a progress spread."""

    synopsis = description.strip() or "故事還沒定型。先寫一點，或讓靈感房替你補足空白。"
    status = "這本書目前已收起" if archived else "這本書正在你的書桌上"
    sections = (
        ("角色", progress.characters),
        ("世界", progress.worlds),
        ("故事", progress.stories),
        ("提示詞", progress.prompts),
    )
    section_html = "".join(
        '<div class="if-book-section">'
        f'<span>{escape(label)}</span><strong>{count}</strong>'
        "</div>"
        for label, count in sections
    )
    st.html(
        f"""
        <section class="if-open-book" aria-label="已打開《{escape(name)}》">
          <article class="if-open-page if-open-page-left">
            <div class="if-open-running">{escape(status)}</div>
            <div class="if-open-mark" aria-hidden="true">✦</div>
            <h2>{escape(name)}</h2>
            <p>{escape(synopsis)}</p>
            <div class="if-open-date">最近編輯 · {escape(_book_date(progress.updated_at))}</div>
          </article>
          <article class="if-open-page if-open-page-right">
            <div class="if-open-running">內容頁</div>
            <div class="if-book-sections">{section_html}</div>
            <div class="if-open-progress">
              已寫入 {progress.started_sections} / 4 個內容頁
              <span>{progress.content_total} 筆創作內容</span>
            </div>
          </article>
        </section>
        """
    )
