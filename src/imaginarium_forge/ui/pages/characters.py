"""Characters / Canon Vault page (A-05, A-02 UI reset, A-06 source fields, A-08 outfits).

Everything goes through application services — no SQL here, no engine handling
here. Eligibility-relevant edits never reuse old audit results: the eligibility
panel always triggers a live re-evaluation.
"""

from __future__ import annotations

from typing import Any

import streamlit as st

from imaginarium_forge.application.errors import ApplicationError
from imaginarium_forge.domain.character.gender import CharacterGender
from imaginarium_forge.domain.character.model import AgeStatus, Character, SourceMetadata
from imaginarium_forge.domain.character.version import AdultPresentation, VisualDNA
from imaginarium_forge.domain.common.enums import AgeClassification
from imaginarium_forge.ui.bootstrap import get_services
from imaginarium_forge.ui.components import page_header, section_heading
from imaginarium_forge.ui.forms import format_trait_lines, parse_comma_list, parse_trait_lines
from imaginarium_forge.ui.project_gateway import ProjectGatewayCopy, render_project_gateway

_TRAIT_HELP = (
    "每行一筆：分類 | 名稱 | 描述 | 強度代碼。"
    "hard_lock（不可更動）／soft_canon（盡量保留）／preference（偏好）"
)
_CREATE_RESET_PENDING_KEY = "characters_create_reset_pending"
_CREATE_FLASH_KEY = "characters_create_flash"
_CREATE_FORM_KEYS = (
    "orig_name",
    "orig_age",
    "orig_age_confirmed",
    "exist_name",
    "exist_class",
    "exist_source",
    "exist_source_char",
    "exist_method",
    "exist_verif",
)


# --------------------------------------------------------------- create forms

def _create_forms(services: Any, project_id: str) -> None:
    tab_original, tab_existing = st.tabs(["原創角色", "既有角色"])

    with tab_original:
        name = st.text_input("名稱", key="orig_name")
        age = st.number_input(
            "明確年齡（預填值 21，僅為表單預設，不代表已確認）",
            min_value=0, max_value=200, value=21, key="orig_age",
        )
        confirmed = st.checkbox(
            "我確認上述年齡為此角色的明確年齡（成人內容資格必要條件）",
            key="orig_age_confirmed",
        )
        if st.button("建立原創角色", key="create_orig_btn"):
            try:
                character = services.characters.create_original_character(
                    project_id=project_id,
                    name=name,
                    explicit_age=int(age),
                    user_confirmed_age=confirmed,
                )
                st.session_state[_CREATE_RESET_PENDING_KEY] = True
                st.session_state[_CREATE_FLASH_KEY] = (
                    f"已建立：{character.name}（分類由年齡導出）"
                )
                st.rerun()
            except ApplicationError as exc:
                st.error(str(exc))

    with tab_existing:
        ename = st.text_input("名稱", key="exist_name")
        classification = st.selectbox(
            "原作年齡狀態",
            options=[c.value for c in AgeClassification],
            key="exist_class",
        )
        # A-06: source_character_name initializes from the display name, editable
        source_title = st.text_input("來源作品（必填）", key="exist_source")
        source_char = st.text_input(
            "來源角色名稱（必填）", value=ename, key="exist_source_char"
        )
        method = st.text_input("驗證方式（如：官方設定集／官方網站）", key="exist_method")
        verification = st.text_area(
            "驗證註記（清楚說明來源如何確立成人身分；模糊描述無效）", key="exist_verif"
        )
        if st.button("建立既有角色", key="create_exist_btn"):
            try:
                character = services.characters.create_existing_character(
                    project_id=project_id,
                    name=ename,
                    age_status=AgeStatus(
                        classification=AgeClassification(classification),
                        verification_source_note=verification,
                        user_confirmed=bool(verification.strip()),
                    ),
                    source_metadata=SourceMetadata(
                        source_title=source_title,
                        source_character_name=source_char,
                        verification_method=method,
                        verification_note=verification,
                    ),
                )
                st.session_state[_CREATE_RESET_PENDING_KEY] = True
                st.session_state[_CREATE_FLASH_KEY] = f"已建立：{character.name}"
                st.rerun()
            except ApplicationError as exc:
                st.error(str(exc))


# ---------------------------------------------------------- identity editing

def _identity_panel(services: Any, character: Character) -> None:
    cid = character.id
    st.write("**基本資料與側寫**")
    name = st.text_input("角色名稱", value=character.name, key=f"id_name_{cid}")
    biography = st.text_area(
        "傳記（biography）", value=character.profile.biography, key=f"id_bio_{cid}"
    )
    personality = st.text_area(
        "性格筆記", value=character.profile.personality_notes, key=f"id_pers_{cid}"
    )
    voice = st.text_area(
        "聲線筆記", value=character.profile.voice_notes, key=f"id_voice_{cid}"
    )
    profile_a, profile_b = st.columns(2)
    motivation = profile_a.text_area(
        "真正想要的事", value=character.profile.motivation, key=f"id_motivation_{cid}"
    )
    fear = profile_b.text_area(
        "最害怕的事", value=character.profile.fear, key=f"id_fear_{cid}"
    )
    secret = profile_a.text_area(
        "不願被知道的秘密", value=character.profile.secret, key=f"id_secret_{cid}"
    )
    internal_conflict = profile_b.text_area(
        "內在衝突",
        value=character.profile.internal_conflict,
        key=f"id_internal_conflict_{cid}",
    )
    relationships = st.text_area(
        "人際關係鉤子（每行一項）",
        value="\n".join(character.profile.relationship_hooks),
        key=f"id_relationships_{cid}",
    )
    st.write("**角色成長線**")
    arc_a, arc_b = st.columns(2)
    arc_start = arc_a.text_area(
        "故事開始時", value=character.profile.arc_start, key=f"id_arc_start_{cid}"
    )
    arc_end = arc_b.text_area(
        "故事結束時", value=character.profile.arc_end, key=f"id_arc_end_{cid}"
    )
    arc_turns = st.text_area(
        "重要轉折（每行一項）",
        value="\n".join(character.profile.arc_turning_points),
        key=f"id_arc_turns_{cid}",
    )
    freeform = st.text_area(
        "自由筆記", value=character.profile.freeform_notes, key=f"id_free_{cid}"
    )
    if st.button("儲存基本資料", key=f"id_save_{cid}"):
        try:
            services.characters.update_identity(cid, name=name)
            services.characters.update_profile(
                cid,
                biography=biography,
                personality_notes=personality,
                voice_notes=voice,
                motivation=motivation,
                fear=fear,
                secret=secret,
                internal_conflict=internal_conflict,
                relationship_hooks=tuple(
                    line.strip() for line in relationships.splitlines() if line.strip()
                ),
                arc_start=arc_start,
                arc_turning_points=tuple(
                    line.strip() for line in arc_turns.splitlines() if line.strip()
                ),
                arc_end=arc_end,
                freeform_notes=freeform,
            )
            st.success("已儲存基本資料")
            st.rerun()
        except ApplicationError as exc:
            st.error(str(exc))


def _age_panel(services: Any, character: Character) -> None:
    """A-02 §6.4: age display, default indicator, explicit confirmation, RESET on change."""
    cid = character.id
    st.write("**年齡狀態**")
    current_age = character.age_status.explicit_age
    st.caption(
        f"目前儲存：分類 `{character.age_status.classification.value}`"
        + (f"、明確年齡 {current_age}" if current_age is not None else "、未填年齡")
        + ("、已確認" if character.age_status.user_confirmed else "、**未確認**")
    )
    if character.character_origin.value == "original":
        age_input = st.number_input(
            "明確年齡", min_value=0, max_value=200,
            value=current_age if current_age is not None else 21,
            key=f"age_val_{cid}",
        )
        # confirmation RESET: if the entered age differs from last-seen, clear confirm
        last_key, confirm_key = f"age_last_{cid}", f"age_confirm_{cid}"
        if st.session_state.get(last_key) != age_input:
            st.session_state[confirm_key] = False
            st.session_state[last_key] = age_input
        confirmed = st.checkbox(
            "我確認此年齡（年齡變更後需重新確認）", key=confirm_key
        )
        if st.button("儲存年齡狀態", key=f"age_save_{cid}"):
            try:
                services.characters.update_age_status(
                    cid,
                    age_status=AgeStatus(
                        classification=(
                            AgeClassification.VERIFIED_ADULT
                            if int(age_input) >= 18
                            else AgeClassification.VERIFIED_MINOR
                        ),
                        explicit_age=int(age_input),
                        user_confirmed=confirmed,
                    ),
                )
                st.success("已更新年齡狀態")
                st.rerun()
            except (ApplicationError, ValueError) as exc:
                st.error(str(exc))
    else:
        classification = st.selectbox(
            "原作年齡狀態",
            options=[c.value for c in AgeClassification],
            index=[c.value for c in AgeClassification].index(
                character.age_status.classification.value
            ),
            key=f"age_class_{cid}",
        )
        if st.button("儲存年齡狀態", key=f"age_save_{cid}"):
            try:
                services.characters.update_age_status(
                    cid,
                    age_status=AgeStatus(
                        classification=AgeClassification(classification),
                        verification_source_note=character.age_status.verification_source_note,
                        user_confirmed=character.age_status.user_confirmed,
                    ),
                )
                st.success("已更新年齡狀態")
                st.rerun()
            except (ApplicationError, ValueError) as exc:
                st.error(str(exc))
        source = character.source_metadata or SourceMetadata()
        st.write("**來源資訊（A-06：作品與角色名稱必填）**")
        s_title = st.text_input("來源作品", value=source.source_title, key=f"src_t_{cid}")
        s_char = st.text_input(
            "來源角色名稱", value=source.source_character_name, key=f"src_c_{cid}"
        )
        s_method = st.text_input(
            "驗證方式", value=source.verification_method, key=f"src_m_{cid}"
        )
        s_note = st.text_area(
            "驗證註記", value=source.verification_note, key=f"src_n_{cid}"
        )
        if st.button("儲存來源資訊", key=f"src_save_{cid}"):
            try:
                services.characters.update_source_metadata(
                    cid,
                    source_metadata=SourceMetadata(
                        source_title=s_title,
                        source_character_name=s_char,
                        verification_method=s_method,
                        verification_note=s_note,
                    ),
                )
                st.success("已更新來源資訊")
                st.rerun()
            except ApplicationError as exc:
                st.error(str(exc))


# ------------------------------------------------------------ Visual DNA form

def _visual_dna_inputs(cid: str, base: VisualDNA) -> VisualDNA:
    """Structured Visual DNA sections (spec §9.2). Raises ValueError on bad traits."""
    identity = st.text_input("識別（Identity）", value=base.identity, key=f"dna_id_{cid}")
    gender = st.selectbox(
        "角色性別",
        options=(None, CharacterGender.FEMALE, CharacterGender.MALE),
        index=(
            0
            if base.gender is None
            else 1
            if base.gender is CharacterGender.FEMALE
            else 2
        ),
        format_func=lambda value: "尚未設定" if value is None else value.zh_label,
        key=f"dna_gender_{cid}",
    )
    face = st.text_input("臉部（Face）", value=base.face, key=f"dna_face_{cid}")
    hair = st.text_input("髮（Hair）", value=base.hair, key=f"dna_hair_{cid}")
    eyes = st.text_input("眼（Eyes）", value=base.eyes, key=f"dna_eyes_{cid}")
    body = st.text_input("體型（Body）", value=base.body, key=f"dna_body_{cid}")
    features = st.text_input(
        "辨識特徵（逗號分隔）",
        value=", ".join(base.distinguishing_features),
        key=f"dna_feat_{cid}",
    )
    prohibited = st.text_input(
        "禁止變異（逗號分隔）",
        value=", ".join(base.prohibited_mutations),
        key=f"dna_proh_{cid}",
    )
    traits_raw = st.text_area(
        "固定特徵", value=format_trait_lines(base.canonical_traits),
        help=_TRAIT_HELP, key=f"dna_traits_{cid}",
    )
    return VisualDNA(
        identity=identity,
        gender=gender,
        face=face,
        hair=hair,
        eyes=eyes,
        body=body,
        distinguishing_features=parse_comma_list(features),
        prohibited_mutations=parse_comma_list(prohibited),
        canonical_traits=parse_trait_lines(traits_raw),
    )


def _version_panel(services: Any, character: Character) -> None:
    cid = character.id
    st.write("**建立新版本（既有版本不可變）**")
    versions = services.versions.list_versions(cid)
    current = next(
        (v for v in versions if v.id == character.current_version_id), None
    )
    base_dna = current.visual_dna if current else VisualDNA()
    try:
        dna = _visual_dna_inputs(cid, base_dna)
        dna_error: str | None = None
    except ValueError as exc:
        dna, dna_error = VisualDNA(), str(exc)
        st.error(f"固定特徵格式錯誤：{exc}")

    st.write("**成人呈現（版本層）**")
    adult_face = st.checkbox("成人臉部呈現", key=f"v_face_{cid}")
    adult_body = st.checkbox("成人身體呈現", key=f"v_body_{cid}")
    minor_era = st.checkbox("未成年期設計", key=f"v_minor_{cid}")
    design_note = st.text_input("設計說明", key=f"v_design_{cid}")
    voice_profile = st.text_input("聲線設定（版本層）", key=f"v_voice_{cid}")
    personality_profile = st.text_input("性格設定（版本層）", key=f"v_pers_{cid}")
    change_note = st.text_input("版本說明（選填）", key=f"v_note_{cid}")
    set_current = st.checkbox(
        "建立後設為目前版本（明確確認）", value=True, key=f"v_setcur_{cid}"
    )
    if st.button("建立新版本", key=f"v_create_{cid}"):
        if dna_error:
            st.error("請先修正固定特徵格式")
        else:
            try:
                services.versions.create_version(
                    character_id=cid,
                    visual_dna=dna,
                    adult_presentation=AdultPresentation(
                        adult_face_presentation=adult_face,
                        adult_body_presentation=adult_body,
                        is_minor_era_design=minor_era,
                        selected_design_note=design_note,
                    ),
                    voice_profile=voice_profile,
                    personality_profile=personality_profile,
                    change_note=change_note,
                    set_as_current=set_current,
                )
                st.success("已建立新版本")
                st.rerun()
            except ApplicationError as exc:
                st.error(str(exc))

    if versions:
        st.write("**版本歷史（基本 metadata 對照）**")
        for version in versions:
            is_current = version.id == character.current_version_id
            marker = "（目前）" if is_current else ""
            presentation = version.adult_presentation
            summary = (
                f"成人呈現 {'✓' if presentation.is_valid_adult_presentation else '✗'}"
                + ("、未成年期設計" if presentation.is_minor_era_design else "")
            )
            cols = st.columns([4, 2])
            cols[0].write(
                f"v{version.version_number}{marker} — {version.change_note or '(無說明)'}"
                f" · {summary} · {version.created_at[:19]}"
            )
            if not is_current:
                confirm_key = f"switch_confirm_{version.id}"
                cols[1].checkbox("確認切換", key=confirm_key)
                if cols[1].button("設為目前", key=f"setcur_{version.id}"):
                    if st.session_state.get(confirm_key):
                        services.versions.set_current_version(
                            character_id=cid, version_id=version.id
                        )
                        st.rerun()
                    else:
                        st.warning("請先勾選「確認切換」（切換需明確確認）")


# ----------------------------------------------------------------- outfits

def _outfit_panel(services: Any, character: Character) -> None:
    cid = character.id
    st.write("**角色服裝設定**")
    with st.container(border=True):
        o_name = st.text_input("服裝名稱", key=f"o_name_{cid}")
        o_desc = st.text_input("描述", key=f"o_desc_{cid}")
        o_canonical = st.text_input("必備特徵（逗號分隔）", key=f"o_can_{cid}")
        o_optional = st.text_input("可選特徵（逗號分隔）", key=f"o_opt_{cid}")
        o_prohibited = st.text_input("禁止特徵（逗號分隔）", key=f"o_pro_{cid}")
        if st.button("建立服裝", key=f"o_create_{cid}"):
            try:
                services.outfits.create_outfit(
                    character_id=cid,
                    name=o_name,
                    description=o_desc,
                    canonical_traits=parse_comma_list(o_canonical),
                    optional_traits=parse_comma_list(o_optional),
                    prohibited_traits=parse_comma_list(o_prohibited),
                )
                st.success("已建立服裝")
                st.rerun()
            except ApplicationError as exc:
                st.error(str(exc))
    for outfit in services.outfits.list_outfits(cid):
        marker = "（已封存）" if outfit.status.value == "archived" else ""
        cols = st.columns([4, 1])
        cols[0].write(
            f"👗 **{outfit.name}**{marker} — 必備：{', '.join(outfit.canonical_traits) or '—'}；"
            f"禁止：{', '.join(outfit.prohibited_traits) or '—'}"
        )
        if outfit.status.value == "active":
            if cols[1].button("封存", key=f"o_arch_{outfit.id}"):
                services.outfits.archive_outfit(outfit.id)
                st.rerun()
        else:
            if cols[1].button("還原", key=f"o_rest_{outfit.id}"):
                services.outfits.restore_outfit(outfit.id)
                st.rerun()


# -------------------------------------------------------------- eligibility

def _eligibility_panel(services: Any, character_id: str) -> None:
    st.write("**成人內容資格檢查**（不使用 AI；每次都依目前角色版本重新檢查）")
    adult_requested = st.checkbox(
        "此請求包含成人內容", value=True, key=f"elig_adult_{character_id}"
    )
    if st.button("執行資格驗證", key=f"elig_run_{character_id}"):
        result = services.eligibility.evaluate(
            character_id=character_id,
            version_id=None,  # uses current version
            adult_content_requested=adult_requested,
        )
        if result.allowed:
            st.success(f"✅ {result.message}")
        else:
            st.error(f"⛔ {result.message}")
        st.caption("本次結果已依目前角色版本與畫面選項重新計算。")


# --------------------------------------------------------------------- page

def render() -> None:
    page_header(
        "這本書的角色",
        "管理已正式收入作品的角色、外觀版本與服裝；每次改動都能保留。",
        eyebrow="角色定稿與版本收藏",
        badges=(("角色版本不覆寫", "teal"), ("資格規則可追溯", "")),
    )
    services = get_services()
    project_id = st.session_state.get("selected_project_id")
    if not project_id:
        render_project_gateway(
            key_prefix="characters",
            copy=ProjectGatewayCopy(
                feature_name="專案角色",
                project_reason="正式角色版本需要知道屬於哪本作品，才能維持故事與外觀一致。",
                draft_description="還沒決定作品也沒關係：先建立完整角色、個人故事與圖片 Prompt。",
                draft_button_label="先做角色草稿",
            ),
        )
        return

    if st.session_state.pop(_CREATE_RESET_PENDING_KEY, False):
        for key in _CREATE_FORM_KEYS:
            st.session_state.pop(key, None)
    flash = st.session_state.pop(_CREATE_FLASH_KEY, None)
    if flash:
        st.success(str(flash))

    characters = services.characters.list_characters(project_id)
    active_count = sum(
        character.status.value == "active" for character in characters
    )
    archived_count = len(characters) - active_count
    section_heading(
        "角色庫",
        "先從角色摘要掌握作品陣容；展開角色後再編輯側寫、年齡、Visual DNA、服裝與資格。",
        eyebrow="CHARACTER LIBRARY",
    )
    metrics = st.columns(3)
    metrics[0].metric("全部角色", len(characters))
    metrics[1].metric("使用中", active_count)
    metrics[2].metric("已封存", archived_count)
    with st.expander("＋ 新增角色到這本書", expanded=not characters):
        _create_forms(services, project_id)

    if not characters:
        st.write("此專案尚無角色。")
        return

    for character in characters:
        marker = "（已封存）" if character.status.value == "archived" else ""
        age_label = (
            str(character.age_status.explicit_age)
            if character.age_status.explicit_age is not None
            else character.age_status.classification.value
        )
        with st.expander(
            f"{character.name}{marker} · {character.character_origin.value} · {age_label}"
        ):
            _identity_panel(services, character)
            st.divider()
            _age_panel(services, character)
            st.divider()
            _version_panel(services, character)
            st.divider()
            _outfit_panel(services, character)
            st.divider()
            _eligibility_panel(services, character.id)
            st.divider()
            if character.status.value == "active":
                if st.button("封存角色", key=f"char_archive_{character.id}"):
                    services.characters.archive_character(character.id)
                    st.rerun()
            else:
                if st.button("還原角色", key=f"char_restore_{character.id}"):
                    services.characters.restore_character(character.id)
                    st.rerun()
