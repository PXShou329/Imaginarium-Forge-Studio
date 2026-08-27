"""Style Profiles page (A-07) — full Style DNA, versioning, conflict display.

No artist-name imitation field exists anywhere on this page (policy).
"""

from __future__ import annotations

from typing import Any

import streamlit as st

from imaginarium_forge.application.errors import ApplicationError
from imaginarium_forge.domain.style.model import StyleDNA, StyleProfile
from imaginarium_forge.ui.bootstrap import get_services
from imaginarium_forge.ui.components import page_header
from imaginarium_forge.ui.forms import format_trait_lines, parse_comma_list, parse_trait_lines
from imaginarium_forge.ui.project_gateway import ProjectGatewayCopy, render_project_gateway

_DNA_FIELDS: tuple[tuple[str, str], ...] = (
    ("medium", "媒材（Medium）"),
    ("linework", "線條（Linework）"),
    ("color", "色彩（Color）"),
    ("shading", "上色／陰影（Shading）"),
    ("lighting", "光照（Lighting）"),
    ("face_rendering", "臉部渲染（Face Rendering）"),
    ("background", "背景（Background）"),
    ("composition", "構圖（Composition）"),
    ("post_processing", "後製（Post-Processing）"),
)


def _dna_inputs(prefix: str, base: StyleDNA) -> StyleDNA:
    """All Style DNA groups (spec §11). Raises ValueError on bad trait lines."""
    values: dict[str, str] = {}
    for field, label in _DNA_FIELDS:
        values[field] = st.text_input(
            label, value=getattr(base, field), key=f"{prefix}_{field}"
        )
    prohibited = st.text_input(
        "禁止特徵（逗號分隔）",
        value=", ".join(base.prohibited_traits),
        key=f"{prefix}_prohibited",
    )
    traits_raw = st.text_area(
        "必要特徵",
        value=format_trait_lines(base.canonical_traits),
        help=(
            "每行一筆：分類 | 名稱 | 描述 | 強度代碼。"
            "hard_lock（不可更動）／soft_canon（盡量保留）／preference（偏好）"
        ),
        key=f"{prefix}_traits",
    )
    return StyleDNA(
        **values,
        prohibited_traits=parse_comma_list(prohibited),
        canonical_traits=parse_trait_lines(traits_raw),
    )


def _profile_detail(services: Any, profile: StyleProfile) -> None:
    pid = profile.id
    versions = services.styles.list_versions(pid)
    current = next((v for v in versions if v.id == profile.current_version_id), None)
    base = current.style_dna if current else StyleDNA()

    st.write("**建立新版本（既有版本不可變）**")
    try:
        dna = _dna_inputs(f"sv_{pid}", base)
        dna_error: str | None = None
    except ValueError as exc:
        dna, dna_error = StyleDNA(), str(exc)
        st.error(f"必要特徵格式錯誤：{exc}")
    change_note = st.text_input("版本說明（選填）", key=f"sv_note_{pid}")
    set_current = st.checkbox("建立後設為目前版本", value=True, key=f"sv_setcur_{pid}")
    if st.button("建立新風格版本", key=f"sv_create_{pid}"):
        if dna_error:
            st.error("請先修正必要特徵格式")
        else:
            try:
                services.styles.create_version(
                    profile_id=pid,
                    style_dna=dna,
                    change_note=change_note,
                    set_as_current=set_current,
                )
                st.success("已建立新風格版本")
                st.rerun()
            except ApplicationError as exc:
                # A-07: required/prohibited conflict is surfaced verbatim here
                st.error(str(exc))

    if versions:
        st.write("**版本歷史**")
        for version in versions:
            is_current = version.id == profile.current_version_id
            marker = "（目前）" if is_current else ""
            cols = st.columns([4, 1])
            cols[0].write(
                f"v{version.version_number}{marker} — "
                f"{version.change_note or '(無說明)'} · "
                f"{version.style_dna.medium or '(未指定媒材)'} · {version.created_at[:19]}"
            )
            if not is_current and cols[1].button("設為目前", key=f"sv_cur_{version.id}"):
                try:
                    services.styles.set_current_version(
                        profile_id=pid, version_id=version.id
                    )
                    st.rerun()
                except ApplicationError as exc:
                    st.error(str(exc))


def render() -> None:
    page_header(
        "這本書的圖片風格",
        "把媒材、色彩、光線、構圖與禁用元素保存成可重用的風格版本。",
        eyebrow="讓每張圖看起來屬於同一個世界",
        badges=(("風格要素", "teal"), ("保留修改歷史", "")),
    )
    services = get_services()
    project_id = st.session_state.get("selected_project_id")
    if not project_id:
        render_project_gateway(
            key_prefix="style_profiles",
            copy=ProjectGatewayCopy(
                feature_name="圖片風格",
                project_reason="正式風格版本會綁定作品，讓同一本書的角色圖與背景圖保持一致。",
                draft_description="只想拿一組角色圖或背景圖 Prompt，可以直接做獨立提示詞草稿。",
                draft_destination="Prompt Scratchpad",
                draft_heading="只做圖片 Prompt",
                draft_button_label="打開提示詞草稿",
            ),
        )
        return

    with st.expander("建立風格設定", expanded=False):
        name = st.text_input("名稱", key="style_name")
        try:
            dna = _dna_inputs("style_new", StyleDNA())
            new_error: str | None = None
        except ValueError as exc:
            dna, new_error = StyleDNA(), str(exc)
            st.error(f"必要特徵格式錯誤：{exc}")
        if st.button("建立風格", key="create_style_btn"):
            if new_error:
                st.error("請先修正必要特徵格式")
            else:
                try:
                    profile = services.styles.create_profile(
                        project_id=project_id, name=name, style_dna=dna
                    )
                    st.success(f"已建立風格：{profile.name}")
                except ApplicationError as exc:
                    st.error(str(exc))

    profiles = services.styles.list_profiles(project_id)
    if not profiles:
        st.write("此專案尚無風格設定。")
        return

    for profile in profiles:
        marker = "（已封存）" if profile.status.value == "archived" else ""
        with st.expander(f"{profile.name}{marker}"):
            _profile_detail(services, profile)
            st.divider()
            if profile.status.value == "active":
                if st.button("封存", key=f"style_archive_{profile.id}"):
                    services.styles.archive_profile(profile.id)
                    st.rerun()
            else:
                if st.button("還原", key=f"style_restore_{profile.id}"):
                    services.styles.restore_profile(profile.id)
                    st.rerun()
