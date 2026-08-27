"""Visual Prompt Studio — Gate A hardened (spec §4–§16).

Hardening honored here:

- A-01: the current input fingerprint is recomputed EVERY rerun; Compile
  always re-resolves; save/accept/export/experiment are blocked with
  「輸入在編譯後已變更；請重新編譯。」 when the fingerprint drifted.
- A-02/A-03: an explicit 7-value content-mode selector replaces the old
  checkbox; a deterministic preflight demands confirmation when obvious
  adult markers appear under a non-adult mode; adult modes require a
  selected, versioned, eligibility-verified character.
- A-04: blocked outcomes show conflicts/lint but never final prompts; only
  a `blocked_diagnostic_report` download is offered.
- A-05: saving a version freezes the selection snapshot + fingerprint;
  the AST carries Canon reference IDs.
- A-09: all fifteen compiler blocks render in fixed order, each copyable.
- A-10: every selector is ID-valued with disambiguating labels.
- A-13: final exports load the PERSISTED variant, never live UI state.

This page still never writes Canon (§10.6): Hard-Lock「建立新角色版本」only
routes to the Canon Vault workflow.
"""

from __future__ import annotations

from typing import Any

import streamlit as st
from pydantic import ValidationError

from imaginarium_forge.application.errors import ApplicationError
from imaginarium_forge.application.services.prompt_orchestration_service import (
    preflight_ack_fingerprint,
)
from imaginarium_forge.application.services.prompt_studio_service import (
    CompilationOutcome,
    ResolutionOutcome,
)
from imaginarium_forge.application.services.scene_parsing_service import (
    ParsingStatus,
    VisualSceneParsingRequest,
    VisualSceneParsingService,
)
from imaginarium_forge.application.services.video_prompt_bundle_service import (
    VideoPromptCompileResult,
    VideoPromptPreparedExport,
)
from imaginarium_forge.domain.common.ids import utc_now_iso
from imaginarium_forge.domain.prompt.ast import (
    CameraNode,
    EnvironmentNode,
    LightingNode,
    PromptAST,
    StyleNode,
    SubjectNode,
    UncertaintyState,
    UserIntent,
)
from imaginarium_forge.domain.prompt.conflicts import ConflictSeverity
from imaginarium_forge.domain.prompt.content_mode import (
    ContentMode,
    derives_adult,
    preflight_content_mode,
)
from imaginarium_forge.domain.prompt.input_snapshot import (
    PromptVersionSelectionSnapshot,
)
from imaginarium_forge.domain.prompt.lint import LintLevel
from imaginarium_forge.domain.prompt.video import VideoAspectRatio
from imaginarium_forge.infrastructure.export.prompt_export import (
    ExportBundle,
    ExportRefs,
    export_blocked_diagnostic,
    export_json,
    export_markdown,
)
from imaginarium_forge.ui.bootstrap import Services, get_services
from imaginarium_forge.ui.components import page_header
from imaginarium_forge.ui.forms import parse_comma_list
from imaginarium_forge.ui.project_gateway import ProjectGatewayCopy, render_project_gateway

_STATE_ICON = {
    UncertaintyState.CONFIRMED: "✅",
    UncertaintyState.INFERRED: "🔎",
    UncertaintyState.UNCERTAIN: "❓",
    UncertaintyState.MISSING: "⬜",
}

_MODE_LABELS = {
    ContentMode.GENERAL: "一般",
    ContentMode.MATURE_NONSEXUAL: "成熟非性",
    ContentMode.DARK: "黑暗",
    ContentMode.HORROR: "恐怖",
    ContentMode.VIOLENT: "暴力",
    ContentMode.SUGGESTIVE: "性暗示（需資格）",
    ContentMode.EXPLICIT_ADULT: "明確成人（需資格）",
}

_STALE_MESSAGE = "輸入在編譯後已變更；請重新編譯。"


# ------------------------------------------------------------ small helpers
def _short(record_id: str) -> str:
    return record_id[:8]


def _id_select(
    label: str,
    options: list[str | None],
    labels: dict[str | None, str],
    key: str,
) -> str | None:
    """A-10: ID-valued selector; duplicate display names can never collide."""
    return st.selectbox(label, options, format_func=lambda v: labels.get(v, str(v)), key=key)


def _get_parser() -> VisualSceneParsingService:
    injected = st.session_state.get("studio_parser")
    if injected is not None:
        return injected  # type: ignore[no-any-return]
    from imaginarium_forge.providers.ollama import OllamaProvider

    services = get_services()
    return VisualSceneParsingService(OllamaProvider(services.settings), provider_name="ollama")


# --------------------------------------------------------------- ast <-> form
def _form_to_ast(
    char_id: str | None,
    char_version_id: str | None,
    outfit_id: str | None,
    style_profile_id: str | None,
    style_version_id: str | None,
    source_text: str,
) -> PromptAST:
    """Structured form → PromptAST. A-05 §8.3: Canon reference IDs populated."""
    s = st.session_state
    overrides = parse_comma_list(str(s.get("studio_overrides", "")))
    return PromptAST(
        subjects=(
            SubjectNode(
                character_id=char_id or "",
                character_version_id=char_version_id or "",
                outfit_id=outfit_id or "",
                presentation=str(s.get("studio_presentation", "")),
                scene_overrides=overrides,
                pose=str(s.get("studio_pose", "")),
                expression=str(s.get("studio_expression", "")),
                emotional_subtext=str(s.get("studio_subtext", "")),
                gaze=str(s.get("studio_gaze", "")),
                motion_cues=parse_comma_list(str(s.get("studio_motion", ""))),
                preserve_identity=bool(s.get("studio_preserve_identity", False)),
                outfit_override_only=bool(s.get("studio_outfit_only", False)),
            ),
        ),
        camera=CameraNode(
            shot=str(s.get("studio_shot", "")),
            angle=str(s.get("studio_angle", "")),
            lens_intent=str(s.get("studio_lens", "")),
            framing=str(s.get("studio_framing", "")),
        ),
        environment=EnvironmentNode(
            location=str(s.get("studio_location", "")),
            time_of_day=str(s.get("studio_time", "")),
            weather=str(s.get("studio_weather", "")),
            background_elements=parse_comma_list(str(s.get("studio_background", ""))),
            atmosphere=str(s.get("studio_atmosphere", "")),
        ),
        lighting=LightingNode(
            key=str(s.get("studio_light_key", "")),
            color_temperature=str(s.get("studio_light_temp", "")),
        ),
        style=StyleNode(
            style_profile_id=style_profile_id or "",
            style_version_id=style_version_id or "",
            scene_mood_overrides=parse_comma_list(str(s.get("studio_mood", ""))),
        ),
        negative=st.session_state.get("studio_parsed_negative") or PromptAST().negative,
        user_intent=UserIntent(
            source_text=source_text,
            must_include=parse_comma_list(str(s.get("studio_must_include", ""))),
            must_avoid=parse_comma_list(str(s.get("studio_must_avoid", ""))),
        ),
        field_states=dict(s.get("studio_field_states", {})),
        metadata=st.session_state.get("studio_parsed_metadata") or PromptAST().metadata,
    )


def _ast_to_form(ast: PromptAST) -> dict[str, object]:
    """Parser draft → pending form values (applied pre-widget next run)."""
    s: dict[str, object] = {}
    subject = ast.primary_subject or SubjectNode()
    s["studio_presentation"] = subject.presentation
    s["studio_overrides"] = ", ".join(subject.scene_overrides)
    s["studio_pose"] = subject.pose
    s["studio_expression"] = subject.expression
    s["studio_subtext"] = subject.emotional_subtext
    s["studio_gaze"] = subject.gaze
    s["studio_motion"] = ", ".join(subject.motion_cues)
    s["studio_preserve_identity"] = subject.preserve_identity
    s["studio_outfit_only"] = subject.outfit_override_only
    s["studio_shot"] = ast.camera.shot
    s["studio_angle"] = ast.camera.angle
    s["studio_lens"] = ast.camera.lens_intent
    s["studio_framing"] = ast.camera.framing
    s["studio_location"] = ast.environment.location
    s["studio_time"] = ast.environment.time_of_day
    s["studio_weather"] = ast.environment.weather
    s["studio_background"] = ", ".join(ast.environment.background_elements)
    s["studio_atmosphere"] = ast.environment.atmosphere
    s["studio_light_key"] = ast.lighting.key
    s["studio_light_temp"] = ast.lighting.color_temperature
    s["studio_mood"] = ", ".join(ast.style.scene_mood_overrides)
    s["studio_must_include"] = ", ".join(ast.user_intent.must_include)
    s["studio_must_avoid"] = ", ".join(ast.user_intent.must_avoid)
    s["studio_field_states"] = dict(ast.field_states)
    s["studio_parsed_negative"] = ast.negative
    s["studio_parsed_metadata"] = ast.metadata
    return s


_FORM_KEYS = (
    "studio_presentation",
    "studio_overrides",
    "studio_pose",
    "studio_expression",
    "studio_subtext",
    "studio_gaze",
    "studio_motion",
    "studio_shot",
    "studio_angle",
    "studio_lens",
    "studio_framing",
    "studio_location",
    "studio_time",
    "studio_weather",
    "studio_background",
    "studio_atmosphere",
    "studio_light_key",
    "studio_light_temp",
    "studio_mood",
    "studio_must_include",
    "studio_must_avoid",
)


def _reset_parse() -> None:
    pending: dict[str, object] = {key: "" for key in _FORM_KEYS}
    pending["studio_preserve_identity"] = False
    pending["studio_outfit_only"] = False
    st.session_state["studio_pending_form"] = pending
    for key in (
        "studio_field_states",
        "studio_parsed_negative",
        "studio_parsed_metadata",
        "studio_parse_status",
        "studio_outcome",
        "studio_resolution",
        "studio_variant_id",
        "studio_version_id",
    ):
        st.session_state.pop(key, None)


def _drop_override(text: str) -> None:
    current = parse_comma_list(str(st.session_state.get("studio_overrides", "")))
    st.session_state["studio_pending_form"] = {
        "studio_overrides": ", ".join(i for i in current if i != text)
    }
    st.session_state.pop("studio_outcome", None)
    st.session_state.pop("studio_resolution", None)


def _override_from_source(source: str) -> str:
    return source.removeprefix("scene_override:")


def _reset_for_project(project_id: str | None) -> bool:
    """Drop all project-bound Studio state before keyed widgets instantiate."""
    previous = st.session_state.get("studio_project_id")
    if previous == project_id:
        return False
    for key in list(st.session_state):
        if str(key).startswith("studio_") and key != "studio_parser":
            st.session_state.pop(key, None)
    st.session_state["studio_project_id"] = project_id
    return True


def _video_result_for_version(
    version_id: str,
    *,
    duration: int,
    fps: int,
    aspect: VideoAspectRatio,
    loop: bool,
) -> VideoPromptCompileResult | None:
    raw = st.session_state.get("studio_video_result")
    if not isinstance(raw, dict):
        return None
    try:
        result = VideoPromptCompileResult.model_validate(raw)
    except ValidationError:
        st.session_state.pop("studio_video_result", None)
        return None
    if result.prompt_project_version_id != version_id:
        return None
    if result.allowed and result.bundle is not None:
        temporal = result.bundle.character_asset.ast.temporal
        if (
            temporal.duration_seconds != duration
            or temporal.fps != fps
            or temporal.aspect_ratio is not aspect
            or ("seamless loop" in temporal.continuity_constraints) != loop
        ):
            return None
    return result


def _prepared_video_export(bundle_id: str) -> VideoPromptPreparedExport | None:
    raw = st.session_state.get("studio_video_prepared_export")
    if not isinstance(raw, dict):
        return None
    try:
        prepared = VideoPromptPreparedExport.model_validate(raw)
    except ValidationError:
        st.session_state.pop("studio_video_prepared_export", None)
        return None
    return prepared if prepared.bundle_id == bundle_id else None


# ------------------------------------------------------------------- render


def _restore_version(services, version_id: str) -> None:  # type: ignore[no-untyped-def]
    """A2-04: load a stored version back into the editor WITHOUT mutating it.

    Everything comes from the frozen selection snapshot + stored AST; the
    restored state is staged through the pending-form mechanism so Streamlit
    widget keys can be written before the widgets instantiate.
    """
    version, ast = services.prompt_projects.get_version(version_id)
    selection = services.prompt_projects.get_version_selection(version_id)
    pending = _ast_to_form(ast)
    pending["studio_source"] = selection.source_text
    pending["studio_char"] = selection.character_id or None
    pending["studio_char_ver"] = selection.character_version_id or None
    pending["studio_outfit"] = selection.outfit_id or None
    pending["studio_style"] = selection.style_profile_id or None
    pending["studio_style_ver"] = selection.style_version_id or None
    pending["studio_mode"] = selection.content_mode
    st.session_state["studio_pending_form"] = pending
    # a restored version is NOT a compiled state: force an explicit recompile
    for key in (
        "studio_outcome",
        "studio_resolution",
        "studio_variant_id",
        "studio_version_id",
        "studio_ack_fingerprint",
    ):
        st.session_state.pop(key, None)
    st.session_state["studio_loaded_version_id"] = version.id


def _render_video_bundle_panel(
    services: Services,
    project_id: str,
    version_id: str,
) -> None:
    """Compile/export VIDEO independently from the single-subject image compiler."""
    st.markdown("##### 影片 Prompt 套件")
    st.caption(
        "使用已保存的 Prompt 與參與角色名單建立；匯出前會重新檢查每位角色的內容資格。"
    )
    timing_a, timing_b = st.columns(2)
    duration = int(
        timing_a.number_input(
            "秒數",
            min_value=1,
            max_value=60,
            value=6,
            step=1,
            key="studio_video_duration",
        )
    )
    fps = int(
        timing_b.number_input(
            "FPS",
            min_value=1,
            max_value=120,
            value=24,
            step=1,
            key="studio_video_fps",
        )
    )
    aspect = st.selectbox(
        "畫面比例",
        list(VideoAspectRatio),
        format_func=lambda value: value.value,
        key="studio_video_aspect_ratio",
    )
    loop = st.checkbox("無縫循環約束", key="studio_video_loop")
    if st.button("建立影片 Prompt 套件", key="studio_video_compile_btn"):
        st.session_state.pop("studio_video_result", None)
        st.session_state.pop("studio_video_prepared_export", None)
        try:
            compiled_result = services.video_prompts.compile_from_version(
                prompt_version_id=version_id,
                expected_project_id=project_id,
                video_duration_seconds=duration,
                video_fps=fps,
                video_aspect=aspect,
                video_loop=loop,
            )
        except ApplicationError as exc:
            st.error(str(exc))
        else:
            st.session_state["studio_video_result"] = compiled_result.model_dump(mode="python")
            st.rerun()

    active_result = _video_result_for_version(
        version_id,
        duration=duration,
        fps=fps,
        aspect=aspect,
        loop=loop,
    )
    if active_result is not None and not active_result.allowed:
        st.error("影片資格或內容檢查未通過，因此沒有建立可匯出的影片 Prompt 套件。")
        for reason in active_result.blocking_reasons:
            st.warning(reason)
        if active_result.video_eligibility_evaluation_ids:
            st.caption(
                f"本次完成 {len(active_result.video_eligibility_evaluation_ids)} "
                "項角色資格檢查。"
            )

    if active_result is not None and active_result.allowed and active_result.bundle is not None:
        replay_note = "（已讀取先前成功結果）" if active_result.replayed else ""
        st.success(f"影片 Prompt 套件已準備完成{replay_note}")
        st.caption(
            f"角色資格檢查：{len(active_result.video_eligibility_evaluation_ids)} 項 · "
            f"內容檢查碼 `{active_result.input_fingerprint[:12]}…` · "
            f"套件 ID `{active_result.bundle_id}`"
        )
        with st.expander("角色影片 Prompt", expanded=False):
            st.code(active_result.bundle.character_asset.positive_prompt, language="text")
            st.code(
                active_result.bundle.character_asset.natural_language_prompt,
                language="text",
            )
        with st.expander("場景影片 Prompt", expanded=False):
            st.code(active_result.bundle.scene_asset.positive_prompt, language="text")
            st.code(
                active_result.bundle.scene_asset.natural_language_prompt,
                language="text",
            )

    try:
        all_summaries = services.video_prompts.list_summaries(expected_project_id=project_id)
    except ApplicationError as exc:
        st.error(str(exc))
        return
    summaries = [
        summary for summary in all_summaries if summary.prompt_project_version_id == version_id
    ]
    if not summaries:
        st.caption("此提示版本尚無影片 Prompt 套件。")
        return
    labels: dict[str | None, str] = {
        summary.bundle_id: (
            f"{summary.created_at[:16]} · {summary.content_mode.value} · "
            f"{_short(summary.bundle_id)}"
        )
        for summary in summaries
    }
    bundle_id = _id_select(
        "已保存的影片 Prompt 套件",
        list(labels),
        labels,
        key="studio_video_bundle_choice",
    )
    if not bundle_id:
        return
    if st.button(
        "再次檢查並準備匯出",
        key="studio_video_prepare_export",
    ):
        # Never retain an older authorized download after a failed re-audit.
        st.session_state.pop("studio_video_prepared_export", None)
        try:
            prepared_export = services.video_prompts.prepare_export(
                bundle_id,
                expected_project_id=project_id,
            )
        except ApplicationError as exc:
            st.error(str(exc))
        else:
            st.session_state["studio_video_prepared_export"] = prepared_export.model_dump(
                mode="python"
            )
            st.rerun()

    active_export = _prepared_video_export(bundle_id)
    if active_export is not None:
        st.caption(
            f"匯出前檢查已通過："
            f"{len(active_export.video_eligibility_evaluation_ids)} 項 · "
            f"SHA-256 `{active_export.bundle_sha256[:16]}…`"
        )
        export_a, export_b = st.columns(2)
        export_a.download_button(
            "下載 Video JSON",
            active_export.json_bytes,
            file_name=active_export.json_filename,
            mime="application/json",
            key="studio_video_export_json",
        )
        export_b.download_button(
            "下載 Video TXT",
            active_export.text_bytes,
            file_name=active_export.text_filename,
            mime="text/plain",
            key="studio_video_export_txt",
        )
    st.caption("此功能只建立與匯出文字，不會連線、排隊或啟動 ComfyUI。")


def _render_version_browser(services, pp_id, project_id) -> None:  # type: ignore[no-untyped-def]
    """A2-04 (review §4.2): browse, load, and export historical versions."""
    if not pp_id:
        return
    st.divider()
    with st.expander("版本瀏覽器", expanded=False):
        versions = services.prompt_projects.list_versions(pp_id)
        if not versions:
            st.caption("此提示專案尚無版本。編譯後按「儲存為新提示版本」。")
            return
        labels = {
            v.id: (
                f"v{v.version_number}"
                f"{'（已接受）' if v.accepted else ''}"
                f" — {v.created_at[:16]} — {_short(v.id)}"
            )
            for v in versions
        }
        chosen = _id_select("選擇版本", list(labels), labels, key="studio_version_browser")
        if not chosen:
            return
        record, _ = services.prompt_projects.get_version(chosen)
        selection = services.prompt_projects.get_version_selection(chosen)
        st.caption(
            f"狀態：{'已接受（不可修改）' if record.accepted else '草稿'}"
            f"｜內容模式：{_MODE_LABELS.get(selection.content_mode, selection.content_mode.value)}"
            f"｜快照 SHA-256：`{record.selection_snapshot_sha256[:12]}…`"
        )
        st.caption(
            f"角色：{_short(selection.character_id) or '（無）'}"
            f"／版本 {_short(selection.character_version_id) or '（無）'}"
            f"｜風格：{_short(selection.style_profile_id) or '（無）'}"
            f"／版本 {_short(selection.style_version_id) or '（無）'}"
        )
        _render_video_bundle_panel(services, project_id, chosen)
        if st.button("載入此版本到編輯器", key="studio_load_version"):
            _restore_version(services, chosen)
            st.success(
                f"已載入 v{record.version_number}；原版本未被修改。"
                "如需修改請重新編譯並儲存為新版本。"
            )
            st.rerun()

        variants = services.prompt_compilation.list_variants_for_version(chosen)
        st.caption(f"已編譯結果：{len(variants)} 筆")
        if variants:
            v_labels = {
                v.id: (
                    f"{v.created_at[:16]}"
                    f" — {v.checkpoint_filename_snapshot or '（無 checkpoint）'}"
                    f" — {_short(v.id)}"
                )
                for v in variants
            }
            picked = _id_select(
                "選擇已編譯結果以匯出",
                list(v_labels),
                v_labels,
                key="studio_history_variant",
            )
            if picked:
                bundle = services.prompt_export.build_bundle(picked, expected_project_id=project_id)
                h1, h2 = st.columns(2)
                h1.download_button(
                    "匯出歷史 Markdown",
                    export_markdown(bundle),
                    file_name=f"prompt_export_v{record.version_number}.md",
                    key="studio_hist_md",
                )
                h2.download_button(
                    "匯出歷史 JSON",
                    export_json(bundle),
                    file_name=f"prompt_export_v{record.version_number}.json",
                    key="studio_hist_json",
                )


def render() -> None:
    pending = st.session_state.pop("studio_pending_form", None)
    pending_project_id = st.session_state.pop("studio_pending_project_id", None)
    page_header(
        "正式圖片提示詞工作台",
        "把作品內的場景、角色與圖片風格組合成可重現的模型提示詞。",
        eyebrow="將這本書的設定精準組合",
        badges=(("組字結果可重現", "teal"), ("沿用作品設定", "")),
    )
    services = get_services()
    selected = st.session_state.get("selected_project_id")
    project_id = selected if isinstance(selected, str) else None
    project_changed = _reset_for_project(project_id)
    pending_matches = pending_project_id == project_id or (
        pending_project_id is None and not project_changed
    )
    if isinstance(pending, dict) and pending_matches:
        for key, value in pending.items():
            st.session_state[key] = value
    if not project_id:
        render_project_gateway(
            key_prefix="prompt_studio",
            copy=ProjectGatewayCopy(
                feature_name="正式提示詞工作台",
                project_reason="這裡會套用作品內的角色、場景與風格版本，所以正式編譯需要一本作品。",
                draft_description=(
                    "如果只要角色圖或背景圖的英文 Prompt，不需建立作品，也不需準備完整設定。"
                ),
                draft_destination="Prompt Scratchpad",
                draft_heading="只做圖片 Prompt",
                draft_button_label="直接做提示詞",
            ),
        )
        return

    version_browser_had_versions = False

    # Prompt-project history is deliberately mounted before every compile
    # control.  Returning authors can therefore resume a persisted version
    # without first producing a new in-memory outcome.
    with st.container(border=True):
        st.subheader("01 提示專案與歷史")
        st.caption("先選擇或建立提示專案；已儲存版本可在編譯前直接載入。")
        pps = services.prompt_projects.list_for_project(project_id)
        pp_labels: dict[str | None, str] = {None: "（新建）"}
        pp_labels.update({p.id: f"{p.title} — {_short(p.id)}" for p in pps})
        pp_id = _id_select("提示專案", list(pp_labels), pp_labels, key="studio_pp_choice")
        if pp_id is None:
            new_title = st.text_input("新提示專案標題", key="studio_new_pp_title")
            if st.button("建立提示專案", key="studio_create_pp") and new_title.strip():
                record = services.prompt_projects.create(project_id=project_id, title=new_title)
                st.session_state["studio_pp_id"] = record.id
                st.success(f"已建立提示專案：{record.title}")
                st.rerun()
            pp_id = st.session_state.get("studio_pp_id")
        else:
            st.session_state["studio_pp_id"] = pp_id

        # Historical versions belong to the prompt project, not to the current
        # compilation result.  Keep the browser available before any compile so
        # a returning author can resume directly from a persisted version.
        version_browser_had_versions = bool(pp_id and services.prompt_projects.list_versions(pp_id))
        _render_version_browser(services, pp_id, project_id)

    with st.container(border=True):
        st.subheader("02 素材與輸出規格")
        st.caption("將常用的角色、風格與內容安全放在同一層；低頻編譯參數收納在進階設定。")
        character_col, style_col, safety_col = st.columns(3, gap="large")

        with character_col:
            st.markdown("**角色與服裝**")
            chars = services.characters.list_characters(project_id, include_archived=False)
            char_labels: dict[str | None, str] = {None: "（無）"}
            char_labels.update({c.id: f"{c.name} — {_short(c.id)}" for c in chars})
            char_id = _id_select("角色", list(char_labels), char_labels, key="studio_char")

            version_id: str | None = None
            outfit_id: str | None = None
            if char_id:
                versions = services.versions.list_versions(char_id)
                ver_labels: dict[str | None, str] = {None: "（無）"}
                ver_labels.update(
                    {
                        v.id: f"v{v.version_number} — {v.created_at[:10]} — {_short(v.id)}"
                        for v in versions
                    }
                )
                version_id = _id_select(
                    "角色版本", list(ver_labels), ver_labels, key="studio_char_ver"
                )
                outfits = services.outfits.list_outfits(char_id, include_archived=False)
                outfit_labels: dict[str | None, str] = {None: "（無）"}
                outfit_labels.update({o.id: f"{o.name} — {_short(o.id)}" for o in outfits})
                outfit_id = _id_select(
                    "服裝", list(outfit_labels), outfit_labels, key="studio_outfit"
                )

        with style_col:
            st.markdown("**視覺風格與模型**")
            styles = services.styles.list_profiles(project_id, include_archived=False)
            style_labels: dict[str | None, str] = {None: "（無）"}
            style_labels.update({sp.id: f"{sp.name} — {_short(sp.id)}" for sp in styles})
            style_profile_id = _id_select(
                "圖片風格", list(style_labels), style_labels, key="studio_style"
            )
            style_version_id: str | None = None
            if style_profile_id:
                sversions = services.styles.list_versions(style_profile_id)
                sv_labels: dict[str | None, str] = {None: "（無）"}
                sv_labels.update(
                    {
                        v.id: f"v{v.version_number} — {v.created_at[:10]} — {_short(v.id)}"
                        for v in sversions
                    }
                )
                style_version_id = _id_select(
                    "風格版本", list(sv_labels), sv_labels, key="studio_style_ver"
                )

            checkpoints = services.checkpoints.list_all()
            ck_labels: dict[str | None, str] = {None: "（無）"}
            ck_by_id = {c.id: c for c in checkpoints}
            ck_labels.update(
                {c.id: f"{c.filename} — {c.path} — {_short(c.id)}" for c in checkpoints}
            )
            checkpoint_pk = _id_select("Checkpoint", list(ck_labels), ck_labels, key="studio_ckpt")
            selected_ck = ck_by_id.get(checkpoint_pk or "")
            checkpoint_filename = selected_ck.filename if selected_ck else ""
            assigned = selected_ck.assigned_profile_id if selected_ck else ""

        with safety_col:
            st.markdown("**內容模式與安全**")
            # ---- A-03: explicit content mode --------------------------
            mode: ContentMode = st.selectbox(
                "內容模式",
                list(ContentMode),
                format_func=lambda m: _MODE_LABELS[m],
                key="studio_mode",
            )
            adult_required = derives_adult(mode)
            qualification_label = "是" if adult_required else "否"
            st.caption(
                f"目前模式：{_MODE_LABELS[mode]}｜需要成人資格驗證：{qualification_label}"
            )
            # ---- A2-16: acknowledgement bound to exact inputs ---------
            _preflight_now = preflight_content_mode(
                mode,
                str(st.session_state.get("studio_source", "")),
                str(st.session_state.get("studio_overrides", "")),
                str(st.session_state.get("studio_must_include", "")),
            )
            _ack_fp = preflight_ack_fingerprint(
                mode,
                str(st.session_state.get("studio_source", "")),
                str(st.session_state.get("studio_overrides", "")),
                str(st.session_state.get("studio_must_include", "")),
            )
            _stored_ack = st.session_state.get("studio_ack_fingerprint")
            if _stored_ack is not None and _stored_ack != _ack_fp:
                # A2-16: an edit to mode/text invalidates a PREVIOUSLY
                # RECORDED acknowledgement.  Reset the checkbox too so a
                # stale tick cannot silently confirm different input.
                st.session_state.pop("studio_ack_fingerprint", None)
                st.session_state.pop("studio_nonsexual_ack", None)
            nonsexual_ack = False
            if _preflight_now.confirmation_required and not adult_required:
                st.warning(_preflight_now.message_zh_tw)
                nonsexual_ack = st.checkbox(
                    "我確認此用途不是性內容（僅用於解除誤判）",
                    key="studio_nonsexual_ack",
                )
                if nonsexual_ack:
                    st.session_state["studio_ack_fingerprint"] = _ack_fp
                st.caption("此確認僅解除自動判斷的誤判；不會繞過成人模式、資格驗證或任何角色設定規則。")

        with st.expander("進階編譯設定", expanded=False):
            dialect_col, profile_col, preset_col = st.columns(3, gap="large")
            dialects = [d.id for d in services.prompt_profiles.list_dialects()]
            dialect_id = dialect_col.selectbox("Prompt 語法", dialects, key="studio_dialect")
            ck_profiles = [
                "（自動）",
                *(p.id for p in services.prompt_profiles.list_checkpoint_profiles()),
            ]
            ck_profile_label = profile_col.selectbox(
                "模型設定檔", ck_profiles, key="studio_ck_profile"
            )
            checkpoint_profile_id = ck_profile_label if ck_profile_label != "（自動）" else assigned
            presets = ["（無）", *(p.id for p in services.prompt_profiles.list_presets())]
            preset_label = preset_col.selectbox("個人預設", presets, key="studio_preset")
            preset_id = preset_label if preset_label != "（無）" else ""

    # ---------------- fingerprint of the CURRENT inputs (A-01) ----------
    source_text = str(st.session_state.get("studio_source", ""))
    current_ast = _form_to_ast(
        char_id,
        version_id,
        outfit_id,
        style_profile_id,
        style_version_id,
        source_text,
    )

    def _current_fingerprint(outcome: CompilationOutcome) -> str:
        return outcome.input_snapshot.model_copy(
            update={
                "source_text": source_text,
                "prompt_ast_canonical_json": current_ast.canonical_dump(),
                "character_id": char_id or "",
                "character_version_id": version_id or "",
                "outfit_id": outfit_id or "",
                "style_profile_id": style_profile_id or "",
                "style_version_id": style_version_id or "",
                "adult_content_requested": derives_adult(mode),
                "content_mode": mode,
                "dialect_id": dialect_id,
                "checkpoint_id": checkpoint_pk or "",
                "checkpoint_filename": checkpoint_filename,
                "checkpoint_profile_id": checkpoint_profile_id,
                "preset_id": preset_id,
            }
        ).fingerprint()

    prompt_workspace_shell = st.container(key="prompt_workspace_shell")
    scene, delivery = prompt_workspace_shell.columns([1.2, 1], gap="large")

    # =============================================================== SCENE
    with scene.container(border=True):
        st.subheader("03 場景與作品設定")
        st.caption("先解析與微調場景，再明確合併這本作品已保存的設定。")
        st.text_area(
            "繁體中文場景描述",
            key="studio_source",
            height=110,
            placeholder="例：她站在東京夜晚的天橋上，全身低角度鏡頭，不要賽博龐克。",
        )
        c1, c2 = st.columns(2)
        if c1.button("解析", key="studio_parse_btn"):
            text_now = str(st.session_state.get("studio_source", ""))
            if not text_now.strip():
                st.warning("請先輸入場景描述。")
            else:
                try:
                    result = _get_parser().parse(
                        VisualSceneParsingRequest(
                            source_text=text_now,
                            model=services.settings.default_model or "qwen2.5:7b",
                        )
                    )
                except ApplicationError as exc:
                    st.error(str(exc))
                else:
                    st.session_state["studio_parse_status"] = result
                    if result.status is ParsingStatus.OK:
                        st.session_state["studio_pending_form"] = _ast_to_form(result.ast)
                    st.rerun()
        if c2.button("重設解析", key="studio_reset_btn"):
            _reset_parse()
            st.rerun()

        parse_result = st.session_state.get("studio_parse_status")
        if parse_result is not None:
            if parse_result.status is ParsingStatus.OK:
                st.success(
                    f"解析完成（修復 {parse_result.repair_attempts} 次）。"
                    "請審閱下方欄位與不確定標記。"
                )
                if parse_result.rejected_field_state_paths:
                    st.caption(
                        "已忽略未知欄位狀態路徑："
                        + "、".join(parse_result.rejected_field_state_paths)
                    )
            else:
                st.warning(
                    f"解析失敗（{parse_result.error_reason.value}）。"
                    "原文已保留；結構化表單仍可正常編譯。"
                )

        with st.expander("結構化表單", expanded=True):
            st.text_input("外觀呈現", key="studio_presentation")
            st.text_input("外觀覆寫（逗號分隔）", key="studio_overrides")
            f1, f2 = st.columns(2)
            f1.text_input("姿勢", key="studio_pose")
            f2.text_input("表情", key="studio_expression")
            f1.text_input("情緒潛台詞", key="studio_subtext")
            f2.text_input("視線", key="studio_gaze")
            st.text_input("動態線索（逗號分隔）", key="studio_motion")
            f3, f4 = st.columns(2)
            f3.text_input("鏡頭景別", key="studio_shot")
            f4.text_input("鏡頭角度", key="studio_angle")
            f3.text_input("鏡頭意圖", key="studio_lens")
            f4.text_input("取景裁切", key="studio_framing")
            st.text_input("場景地點", key="studio_location")
            f5, f6 = st.columns(2)
            f5.text_input("時間", key="studio_time")
            f6.text_input("天氣", key="studio_weather")
            st.text_input("背景元素（逗號分隔）", key="studio_background")
            st.text_input("氛圍", key="studio_atmosphere")
            f7, f8 = st.columns(2)
            f7.text_input("主光", key="studio_light_key")
            f8.text_input("色溫", key="studio_light_temp")
            st.text_input("風格情緒（逗號分隔）", key="studio_mood")
            st.text_input("必含（逗號分隔）", key="studio_must_include")
            st.text_input("必避（逗號分隔）", key="studio_must_avoid")
            st.checkbox("保留角色身分（只換衣服）", key="studio_preserve_identity")
            st.checkbox("僅服裝覆寫", key="studio_outfit_only")

        field_states = st.session_state.get("studio_field_states", {})
        if field_states:
            st.caption("欄位狀態（解析器標注）")
            for path, state in sorted(field_states.items()):
                st.caption(f"{_STATE_ICON.get(state, '·')} `{path}` — {state.value}")
                if state is UncertaintyState.UNCERTAIN:
                    st.caption("　↳ 有多種可能解讀，請人工確認。")

        # -------- Canon merge preview + conflict decisions (§10.4) -------
        st.subheader("作品設定合併預覽")
        resolution: ResolutionOutcome | None
        if st.button("載入並合併作品設定", key="studio_merge_btn"):
            preflight = preflight_content_mode(
                mode,
                str(st.session_state.get("studio_source", "")),
                str(st.session_state.get("studio_overrides", "")),
                str(st.session_state.get("studio_must_include", "")),
            )
            acked = st.session_state.get("studio_ack_fingerprint") == _ack_fp and not adult_required
            if preflight.confirmation_required and not acked:
                st.error(preflight.message_zh_tw)
            else:
                try:
                    resolution = services.prompt_resolution.resolve(
                        ast=current_ast,
                        character_id=char_id,
                        character_version_id=version_id,
                        outfit_id=outfit_id,
                        style_version_id=style_version_id,
                        content_mode=mode,
                    )
                except ApplicationError as exc:
                    st.error(str(exc))
                else:
                    st.session_state["studio_resolution"] = resolution
                    st.rerun()

        resolution = st.session_state.get("studio_resolution")
        if resolution is not None:
            st.caption(f"鎖定特徵：{', '.join(resolution.character.lock_tags) or '（無）'}")
            if resolution.adult_requested:
                st.caption(
                    "成人資格驗證："
                    + ("通過" if resolution.adult_allowed else "未通過／未選角色")
                    + (
                        f"｜{resolution.eligibility_message}"
                        if resolution.eligibility_message
                        else ""
                    )
                )
            for idx, conflict in enumerate(resolution.conflicts):
                box = st.error if conflict.severity is ConflictSeverity.ERROR else st.warning
                box(f"[{conflict.code.value}] {conflict.message_zh_tw}")
                if conflict.suggested_actions == (
                    "preserve_canon",
                    "create_new_version",
                    "cancel_override",
                ):
                    override_text = _override_from_source(conflict.source_b)
                    a1, a2, a3 = st.columns(3)
                    if a1.button("保留作品設定", key=f"studio_keep_{idx}"):
                        _drop_override(override_text)
                        st.rerun()
                    if a2.button("建立新角色版本", key=f"studio_newver_{idx}"):
                        st.info(
                            "不會自動建立版本：請到「專案角色」建立新角色版本，"
                            "再回到這裡重新選取。"
                        )
                    if a3.button("取消場景覆寫", key=f"studio_cancel_{idx}"):
                        _drop_override(override_text)
                        st.rerun()

    # ============================================================ DELIVERY
    with delivery.container(border=True):
        st.subheader("04 編譯與交付")
        st.caption("整理提示詞、審閱、保存版本與匯出都集中在這裡。")
        outcome: CompilationOutcome | None
        if st.button("編譯", key="studio_compile_btn", type="primary"):
            preflight = preflight_content_mode(
                mode,
                source_text,
                str(st.session_state.get("studio_overrides", "")),
                str(st.session_state.get("studio_must_include", "")),
            )
            acked = st.session_state.get("studio_ack_fingerprint") == _ack_fp and not adult_required
            if preflight.confirmation_required and not acked:
                st.error(preflight.message_zh_tw)
            else:
                try:
                    # A-01 §4.3: ALWAYS re-resolve on Compile
                    fresh = services.prompt_resolution.resolve(
                        ast=current_ast,
                        character_id=char_id,
                        character_version_id=version_id,
                        outfit_id=outfit_id,
                        style_version_id=style_version_id,
                        content_mode=mode,
                    )
                    outcome = services.prompt_compilation.compile(
                        ast=current_ast,
                        resolution=fresh,
                        dialect_id=dialect_id,
                        checkpoint_profile_id=checkpoint_profile_id,
                        preset_id=preset_id,
                        checkpoint_filename=checkpoint_filename,
                        checkpoint_id=checkpoint_pk or "",
                        checkpoint_sha256=(selected_ck.sha256 if selected_ck else ""),
                        checkpoint_hash_status=(
                            selected_ck.sha256_status if selected_ck else "not_computed"
                        ),
                        source_text=source_text,
                        character_id=char_id,
                        character_version_id=version_id,
                        outfit_id=outfit_id,
                        style_profile_id=style_profile_id,
                        style_version_id=style_version_id,
                    )
                except ApplicationError as exc:
                    st.error(str(exc))
                else:
                    st.session_state["studio_outcome"] = outcome
                    st.session_state["studio_resolution"] = fresh
                    st.session_state["studio_ast_compiled"] = current_ast
                    st.session_state.pop("studio_variant_id", None)
                    st.session_state.pop("studio_version_id", None)
                    st.rerun()

        outcome = st.session_state.get("studio_outcome")
        if outcome is None:
            st.caption("尚未編譯。填好表單後按「編譯」。")
            return

        # ---- A-01 §4.5: stale-input guard ------------------------------
        stale = _current_fingerprint(outcome) != outcome.input_fingerprint
        if stale:
            st.warning(_STALE_MESSAGE)

        m1, m2 = st.columns(2)
        m1.metric("身分覆蓋率（解析）", outcome.blocks.identity_coverage.display)
        m2.metric(
            "身分覆蓋率（輸出）",
            outcome.blocks.identity_coverage_compiled.display,
        )
        m3, m4 = st.columns(2)
        m3.metric("風格覆蓋率（解析）", outcome.blocks.style_coverage.display)
        m4.metric("風格覆蓋率（輸出）", outcome.blocks.style_coverage_compiled.display)
        if outcome.blocks.identity_coverage_compiled != outcome.blocks.identity_coverage:
            st.caption(
                "⚠ 最終輸出覆蓋率與解析時不同："
                + ("、".join(outcome.blocks.identity_coverage_compiled.missing) or "—")
            )

        if outcome.blocked:
            st.error(
                "存在阻斷級錯誤：最終提示不會顯示，也無法保存／接受／匯出／建立實驗。"
                "可下載診斷報告（不含提示字串）。"
            )

        # ---- A-09: all fifteen blocks, fixed order, copyable -----------
        named_blocks: list[tuple[str, str, str]] = [
            ("zh-TW 結構說明", outcome.blocks.explanation_zh_tw, "text"),
            ("Character Lock Block", outcome.blocks.character_lock_block, "text"),
            ("Outfit Block", outcome.blocks.outfit_block, "text"),
            ("Pose / Expression Block", outcome.blocks.pose_expression_block, "text"),
            ("Camera Block", outcome.blocks.camera_block, "text"),
            ("Environment Block", outcome.blocks.environment_block, "text"),
            ("Lighting Block", outcome.blocks.lighting_block, "text"),
            ("Style Block", outcome.blocks.style_block, "text"),
            ("Quality Block", outcome.blocks.quality_block, "text"),
        ]
        final_blocks: list[tuple[str, str, str]] = [
            ("Positive Prompt", outcome.blocks.positive_prompt, "text"),
            ("Negative Prompt", outcome.blocks.negative_prompt, "text"),
            (
                "English Natural-Language Prompt",
                outcome.blocks.natural_language_prompt,
                "text",
            ),
        ]
        trailing_blocks: list[tuple[str, str, str]] = [
            ("Generation Notes", outcome.blocks.generation_notes, "text"),
            ("Conflict Report", outcome.blocks.conflict_warning_report, "text"),
        ]
        for title, body, lang in named_blocks:
            with st.expander(title, expanded=False):
                st.code(body or "（無）", language=lang)
        if outcome.blocked:
            st.caption("（最終提示因阻斷而隱藏）")
        else:
            for title, body, lang in final_blocks:
                with st.expander(title, expanded=True):
                    st.code(body or "（無）", language=lang)
        for title, body, lang in trailing_blocks:
            with st.expander(title, expanded=False):
                st.code(body or "（無）", language=lang)
        with st.expander("Lint Report", expanded=outcome.blocked):
            for finding in outcome.lint.by_level(LintLevel.ERROR):
                st.error(f"[{finding.check.value}] {finding.message_zh_tw}")
            for finding in outcome.lint.by_level(LintLevel.WARNING):
                st.warning(f"[{finding.check.value}] {finding.message_zh_tw}")
            for finding in outcome.lint.by_level(LintLevel.INFORMATION):
                st.info(f"[{finding.check.value}] {finding.message_zh_tw}")

        # ------------------------------------------------ save / export
        st.divider()
        pp_id = st.session_state.get("studio_pp_id")
        can_act = not outcome.blocked and not stale
        if stale:
            st.caption(f"⛔ {_STALE_MESSAGE}")

        if outcome.blocked:
            refs = ExportRefs(
                project_name=services.projects.get_project(project_id).name,
                prompt_project_title=(services.prompt_projects.get(pp_id).title if pp_id else ""),
                checkpoint_filename=checkpoint_filename,
            )
            diagnostic_bundle = ExportBundle(
                source_text=source_text,
                refs=refs,
                ast=st.session_state.get("studio_ast_compiled") or current_ast,
                profile=outcome.profile,
                character=st.session_state["studio_resolution"].character,
                style=st.session_state["studio_resolution"].style,
                blocks=outcome.blocks,
                conflicts=outcome.conflicts,
                lint=outcome.lint,
                created_at=utc_now_iso(),
            )
            st.download_button(
                "下載問題診斷報告",
                export_blocked_diagnostic(diagnostic_bundle),
                file_name="blocked_diagnostic_report.md",
                key="studio_export_diag",
            )
            return

        s1, s2 = st.columns(2)
        if s1.button("儲存為新提示版本", key="studio_save_version", disabled=not can_act):
            if not pp_id:
                st.error("請先在上方建立/選取提示專案。")
            else:
                try:
                    ast = st.session_state.get("studio_ast_compiled") or current_ast
                    services.prompt_projects.update_selections(
                        pp_id,
                        source_text=source_text,
                        character_id=char_id,
                        character_version_id=version_id,
                        style_profile_id=style_profile_id,
                        style_version_id=style_version_id,
                    )
                    selection = PromptVersionSelectionSnapshot(
                        project_id=project_id,
                        character_id=char_id or "",
                        character_version_id=version_id or "",
                        outfit_id=outfit_id or "",
                        style_profile_id=style_profile_id or "",
                        style_version_id=style_version_id or "",
                        content_mode=mode,
                        adult_content_requested=derives_adult(mode),
                        source_text=source_text,
                        created_at=utc_now_iso(),
                    )
                    version_record = services.prompt_projects.add_version(
                        pp_id,
                        ast=ast,
                        selection_snapshot=selection,
                        input_fingerprint=outcome.input_fingerprint,
                        change_note="Studio 儲存",
                    )
                    variant = services.prompt_compilation.persist_variant(
                        prompt_project_id=pp_id,
                        prompt_project_version_id=version_record.id,
                        outcome=outcome,
                        current_input_fingerprint=_current_fingerprint(outcome),
                    )
                except ApplicationError as exc:
                    st.error(str(exc))
                else:
                    st.session_state["studio_variant_id"] = variant.id
                    st.session_state["studio_version_id"] = version_record.id
                    st.success(
                        f"已儲存第 {version_record.version_number} 版，並記錄編譯結果。"
                    )
        version_id_saved = st.session_state.get("studio_version_id")
        if version_id_saved and s2.button(
            "接受此版本（不可再改）", key="studio_accept", disabled=not can_act
        ):
            try:
                services.prompt_projects.accept_version(version_id_saved)
            except ApplicationError as exc:
                st.error(str(exc))
            else:
                st.success("版本已接受；後續編輯將產生新版本。")

        # ---- A-13 §16.2: exports load the PERSISTED variant -------------
        variant_id = st.session_state.get("studio_variant_id")
        e1, e2, e3 = st.columns(3)
        if variant_id and not stale:
            # A2-05: built purely from persisted rows — never live UI state
            persisted_bundle = services.prompt_export.build_bundle(
                variant_id, expected_project_id=project_id
            )
            e1.download_button(
                "匯出 Markdown",
                export_markdown(persisted_bundle),
                file_name="prompt_export.md",
                key="studio_export_md",
            )
            e2.download_button(
                "匯出 JSON",
                export_json(persisted_bundle),
                file_name="prompt_export.json",
                key="studio_export_json",
            )
        else:
            e1.caption("匯出需先「儲存為新提示版本」。")
        # On the first save the upper history section was rendered before the
        # version row existed.  Render the newly-created browser once in this
        # same run so save confirmation and immediate history access stay
        # together.
        if (
            pp_id
            and not version_browser_had_versions
            and services.prompt_projects.list_versions(pp_id)
        ):
            _render_version_browser(services, pp_id, project_id)
        if e3.button("建立實驗紀錄", key="studio_create_exp", disabled=not can_act):
            if not variant_id:
                st.error("請先儲存為新提示版本，才能建立實驗紀錄。")
            else:
                log = services.experiments.create_from_variant(variant_id)
                st.success(
                    f"已建立實驗紀錄：{log.id[:8]}…（到「生成實驗室」補齊設定與評分）"
                )


_ = Any  # keep typing import stable for future annotations
