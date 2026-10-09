"""Standalone editor for durable, project-optional story fragment drafts."""

from __future__ import annotations

import re
from typing import Any

import streamlit as st
from streamlit.delta_generator import DeltaGenerator

from imaginarium_forge.application.errors import (
    ApplicationError,
    ValidationFailedError,
)
from imaginarium_forge.application.services.generation.contracts import (
    GenerationErrorCategory,
    GenerationFailure,
    GenerationTask,
)
from imaginarium_forge.application.services.generation.facade import (
    CreativeCandidateAction,
)
from imaginarium_forge.application.services.generation.refinement import (
    InvalidRefinementCandidateError,
    StaleRefinementBaseError,
)
from imaginarium_forge.application.services.generation.refinement_candidate_persistence import (
    RefinementCandidateBinding,
)
from imaginarium_forge.application.services.generation.story_fragment_continuation import (
    StoryFragmentAppendReceipt,
    StoryFragmentContinuationSource,
    prepare_story_fragment_continuation,
    revalidate_story_fragment_pending_append,
)
from imaginarium_forge.application.services.story_fragment_inspiration_service import (
    StoryFragmentEditorContent,
    StoryFragmentInspirationService,
)
from imaginarium_forge.application.user_facing_errors import ErrorDataSafety
from imaginarium_forge.domain.common.ids import new_id
from imaginarium_forge.domain.generation.durable import ArtifactKind, sha256_json
from imaginarium_forge.domain.story.fragment_draft import (
    MAX_STORY_FRAGMENT_CHARS,
    MAX_STORY_FRAGMENT_TAGS,
    StoryFragmentDraft,
    StoryFragmentGenerationMode,
    StoryFragmentKind,
)
from imaginarium_forge.infrastructure.db.adapters.draft_candidate_target import (
    STORY_FRAGMENT_DRAFT_TARGET,
)
from imaginarium_forge.ui.bootstrap import get_services
from imaginarium_forge.ui.components import page_header
from imaginarium_forge.ui.creative_generation import (
    authoring_editor_state_sha256,
    candidate_authoring_response,
    candidate_value_replaces_current,
    clear_candidate_state,
    passive_creative_action_status,
    render_candidate_action_state,
    render_candidate_card,
    render_candidate_error,
    render_passive_creative_runtime_status,
    request_candidate,
)
from imaginarium_forge.ui.error_presenter import present_exception
from imaginarium_forge.ui.refinement import (
    RefinementUIDecision,
    candidate_receipt_for_save_as_new,
    clear_refinement_state,
    commit_durable_refinement_decision,
    record_refinement_apply,
    refinement_receipt,
    render_durable_refinement_history,
    render_refinement_candidate,
    render_refinement_controls,
    render_refinement_failure,
    render_refinement_undo,
    set_refinement_failure,
)
from imaginarium_forge.ui.writing_request_controls import (
    WritingContextSourceInput,
    WritingRequestDefaults,
    WritingRequestSurface,
    build_writing_request_defaults,
    render_writing_request_controls,
)

PAGE_KEY = "Story Fragment Drafts"
PAGE_LABEL = "故事片段草稿"

STATE_PREFIX = "story_fragment_draft"
PENDING_SELECTION_STATE_KEY = f"{STATE_PREFIX}_pending_selection"
BOUND_DRAFT_STATE_KEY = f"{STATE_PREFIX}_bound_draft"
SELECTED_DRAFT_STATE_KEY = f"{STATE_PREFIX}_selected_draft"
BASE_UPDATED_AT_STATE_KEY = f"{STATE_PREFIX}_base_updated_at"
FLASH_STATE_KEY = f"{STATE_PREFIX}_flash"
PENDING_FORM_STATE_KEY = f"{STATE_PREFIX}_pending_form"
PENDING_APPEND_STATE_KEY = f"{STATE_PREFIX}_pending_append"
EDITOR_INSTANCE_STATE_KEY = f"{STATE_PREFIX}_editor_instance"
EDITOR_VALUES_STATE_KEY = f"{STATE_PREFIX}_editor_values"
INSPIRATION_NOTICE_STATE_KEY = f"{STATE_PREFIX}_inspiration_notice"
ARCHIVE_CONFIRM_STATE_KEY = f"{STATE_PREFIX}_archive_confirm"
REROLL_CONFIRM_STATE_KEY = f"{STATE_PREFIX}_reroll_confirm_pending"

PROJECT_STATE_KEY = f"{STATE_PREFIX}_project_id"
TITLE_STATE_KEY = f"{STATE_PREFIX}_title"
KIND_STATE_KEY = f"{STATE_PREFIX}_kind"
TEXT_STATE_KEY = f"{STATE_PREFIX}_text"
CONTEXT_STATE_KEY = f"{STATE_PREFIX}_context_notes"
TAGS_STATE_KEY = f"{STATE_PREFIX}_tags"
GENERATION_MODE_STATE_KEY = f"{STATE_PREFIX}_generation_mode"
GENERATION_SEED_STATE_KEY = f"{STATE_PREFIX}_generation_seed"
AI_WRITING_REQUEST_PREFIX = f"{STATE_PREFIX}_ai"

AI_CONTEXT_SOURCE_TYPE = "story_fragment_field"
AI_FRAGMENT_TEXT_SOURCE_ID = "fragment_text"
AI_CONTEXT_NOTES_SOURCE_ID = "context_notes"

_NEW_DRAFT = "__new__"
_FORM_DEFAULTS: dict[str, object] = {
    PROJECT_STATE_KEY: "",
    TITLE_STATE_KEY: "",
    KIND_STATE_KEY: StoryFragmentKind.NARRATIVE,
    TEXT_STATE_KEY: "",
    CONTEXT_STATE_KEY: "",
    TAGS_STATE_KEY: "",
    GENERATION_MODE_STATE_KEY: StoryFragmentGenerationMode.MANUAL.value,
    GENERATION_SEED_STATE_KEY: "",
}
_EDITOR_STATE_KEYS = (
    *_FORM_DEFAULTS,
    SELECTED_DRAFT_STATE_KEY,
    BOUND_DRAFT_STATE_KEY,
    BASE_UPDATED_AT_STATE_KEY,
    EDITOR_INSTANCE_STATE_KEY,
)
_KIND_LABELS = {
    StoryFragmentKind.NARRATIVE: "敘事片段",
    StoryFragmentKind.DIALOGUE: "對白片段",
    StoryFragmentKind.OPENING: "故事開場",
}
_GENERATION_LABELS = {
    StoryFragmentGenerationMode.MANUAL.value: "手寫",
    StoryFragmentGenerationMode.FILL_BLANKS.value: "本機靈感只補空白",
    StoryFragmentGenerationMode.REROLL_ALL.value: "本機靈感全部重抽",
}


def _join_tags(tags: tuple[str, ...]) -> str:
    return "、".join(tags)


def _split_tags(value: str) -> tuple[str, ...]:
    tags: list[str] = []
    seen: set[str] = set()
    for raw in re.split(r"[,，、\n]+", value):
        normalized = " ".join(raw.strip().split())
        if normalized and normalized not in seen:
            seen.add(normalized)
            tags.append(normalized)
    return tuple(tags)


def _kind(value: object) -> StoryFragmentKind:
    if isinstance(value, StoryFragmentKind):
        return value
    return StoryFragmentKind(str(value))


def _draft_form(draft: StoryFragmentDraft) -> dict[str, object]:
    return {
        PROJECT_STATE_KEY: draft.project_id or "",
        TITLE_STATE_KEY: draft.title,
        KIND_STATE_KEY: draft.fragment_kind,
        TEXT_STATE_KEY: draft.fragment_text,
        CONTEXT_STATE_KEY: draft.context_notes,
        TAGS_STATE_KEY: _join_tags(draft.tags),
        GENERATION_MODE_STATE_KEY: draft.generation_mode.value,
        GENERATION_SEED_STATE_KEY: draft.generation_seed,
    }


def _durable_refinement_binding(
    services: Any,
    *,
    selected: object,
) -> RefinementCandidateBinding | None:
    """Bind only when every mounted editor value is the saved snapshot."""

    if not isinstance(selected, str) or not selected or selected == _NEW_DRAFT:
        return None
    try:
        draft = services.story_fragment_drafts.get_draft(selected)
    except (ApplicationError, RuntimeError, TypeError, ValueError):
        return None
    if (
        st.session_state.get(BOUND_DRAFT_STATE_KEY) != draft.id
        or st.session_state.get(BASE_UPDATED_AT_STATE_KEY) != draft.updated_at
    ):
        return None
    expected_form = _draft_form(draft)
    if any(st.session_state.get(key) != value for key, value in expected_form.items()):
        return None
    return RefinementCandidateBinding(
        target_type=STORY_FRAGMENT_DRAFT_TARGET,
        target_id=draft.id,
        field_name="fragment_text",
        expected_base_snapshot_sha256=sha256_json(draft.model_dump(mode="json")),
    )


def _capture_editor_state() -> None:
    """Retain only this editor's author fields and binding in non-widget memory.

    Callbacks run before a destination route replaces the page. Capture its
    newest input there, even if the author navigates without a separate rerun.
    Never mirror credentials, confirmations, candidates, or pending actions.
    """

    st.session_state[EDITOR_VALUES_STATE_KEY] = {
        key: st.session_state[key] for key in _EDITOR_STATE_KEYS if key in st.session_state
    }


def _restore_editor_state() -> None:
    """Restore keys removed by widget cleanup without overriding new UI events."""

    saved = st.session_state.get(EDITOR_VALUES_STATE_KEY)
    if not isinstance(saved, dict):
        return
    for key in _EDITOR_STATE_KEYS:
        if key not in st.session_state and key in saved:
            st.session_state[key] = saved[key]


def _write_form(values: dict[str, object]) -> None:
    for key, value in values.items():
        st.session_state[key] = value
    _capture_editor_state()


def _bind_new() -> None:
    _write_form(_FORM_DEFAULTS)
    st.session_state[BASE_UPDATED_AT_STATE_KEY] = None
    st.session_state[BOUND_DRAFT_STATE_KEY] = _NEW_DRAFT
    st.session_state[ARCHIVE_CONFIRM_STATE_KEY] = False
    st.session_state[EDITOR_INSTANCE_STATE_KEY] = new_id()
    _capture_editor_state()


def _ensure_local_editor_state(*, selected: object) -> None:
    """Mount a safe editor even while its persistence service is unavailable."""

    for key, value in _FORM_DEFAULTS.items():
        st.session_state.setdefault(key, value)
    st.session_state.setdefault(BASE_UPDATED_AT_STATE_KEY, None)
    st.session_state.setdefault(BOUND_DRAFT_STATE_KEY, selected)
    st.session_state.setdefault(ARCHIVE_CONFIRM_STATE_KEY, False)
    st.session_state.setdefault(EDITOR_INSTANCE_STATE_KEY, new_id())
    _capture_editor_state()


def _bind_draft(draft: StoryFragmentDraft) -> None:
    _write_form(_draft_form(draft))
    st.session_state[BASE_UPDATED_AT_STATE_KEY] = draft.updated_at
    st.session_state[BOUND_DRAFT_STATE_KEY] = draft.id
    st.session_state[ARCHIVE_CONFIRM_STATE_KEY] = False
    st.session_state[EDITOR_INSTANCE_STATE_KEY] = new_id()
    _capture_editor_state()


def _apply_pending_form() -> None:
    pending = st.session_state.pop(PENDING_FORM_STATE_KEY, None)
    if isinstance(pending, dict):
        _write_form({str(key): value for key, value in pending.items()})


def _continuation_source() -> StoryFragmentContinuationSource | None:
    text = str(st.session_state.get(TEXT_STATE_KEY, ""))
    if not text.strip():
        return None
    instance_id = str(st.session_state.setdefault(EDITOR_INSTANCE_STATE_KEY, new_id()))
    return StoryFragmentContinuationSource.capture(
        text,
        editor_identity=sha256_json({
            "selected": str(st.session_state.get(SELECTED_DRAFT_STATE_KEY, _NEW_DRAFT)),
            "bound": str(st.session_state.get(BOUND_DRAFT_STATE_KEY, _NEW_DRAFT)),
            "project": str(st.session_state.get(PROJECT_STATE_KEY, "")),
        }),
        editor_instance_id=instance_id,
    )


def _apply_pending_append() -> None:
    """Consume one explicit append after binding, before mounting any editor widget."""
    pending = st.session_state.pop(PENDING_APPEND_STATE_KEY, None)
    if pending is None:
        return
    try:
        source = _continuation_source()
        response = candidate_authoring_response(STATE_PREFIX)
        if (
            not isinstance(pending, StoryFragmentAppendReceipt)
            or source is None or response is None
        ):
            raise ValueError("pending continuation evidence is unavailable")
        if pending.candidate_sha256 not in (
            response.provenance.candidate_sha256, response.provenance.condensed_candidate_sha256,
        ):
            raise ValueError("pending continuation candidate changed")
        combined = revalidate_story_fragment_pending_append(pending, current_source=source)
    except ValueError:
        st.warning("原文或目前紙頁已改變；續寫沒有追加，候選仍保留供比較。")
        return
    _write_form({TEXT_STATE_KEY: combined})
    clear_candidate_state(STATE_PREFIX)
    st.info("已追加續寫到正文；原文與其他欄位完整保留，尚未正式保存。")


def _project_options(services: Any) -> tuple[list[str], dict[str, str]]:
    projects = services.projects.list_projects(include_archived=True)
    options = ["", *(project.id for project in projects)]
    labels = {"": "不綁定作品（留在自由創作箱）"}
    labels.update(
        {
            project.id: (
                project.name
                if project.status.value == "active"
                else f"{project.name}（已收起的作品）"
            )
            for project in projects
        }
    )
    return options, labels


def _save(services: Any, selected: str) -> None:
    values = {
        "project_id": str(st.session_state.get(PROJECT_STATE_KEY, "")).strip()
        or None,
        "title": str(st.session_state.get(TITLE_STATE_KEY, "")),
        "fragment_kind": _kind(st.session_state.get(KIND_STATE_KEY)),
        "fragment_text": str(st.session_state.get(TEXT_STATE_KEY, "")),
        "context_notes": str(st.session_state.get(CONTEXT_STATE_KEY, "")),
        "tags": _split_tags(str(st.session_state.get(TAGS_STATE_KEY, ""))),
        "generation_mode": str(
            st.session_state.get(
                GENERATION_MODE_STATE_KEY,
                StoryFragmentGenerationMode.MANUAL.value,
            )
        ),
        "generation_seed": str(
            st.session_state.get(GENERATION_SEED_STATE_KEY, "")
        ),
    }
    missing_required_fields = tuple(
        label
        for label, raw_value in (
            ("片段標題", values["title"]),
            ("故事正文", values["fragment_text"]),
        )
        if not str(raw_value).strip()
    )
    if missing_required_fields:
        present_exception(
            ValidationFailedError("required story fragment fields are blank"),
            operation_zh_tw="檢查故事片段必填欄位",
            key_prefix="story_fragment_save_required_fields",
            data_safety=ErrorDataSafety(
                message_zh_tw=(
                    "這次保存尚未呼叫草稿保存服務，也未開始寫入；"
                    "目前編輯器內容與草稿選擇均保留。"
                    f"請填寫：{'、'.join(missing_required_fields)}。"
                ),
                author_input_preserved=True,
                original_preserved=True,
                completed_work_preserved=True,
                draft_selection_preserved=True,
            ),
        )
        return
    try:
        if selected == _NEW_DRAFT:
            draft = services.story_fragment_drafts.create_draft(**values)
            message = "故事片段已正式收進自由創作箱。"
        else:
            draft = services.story_fragment_drafts.update_draft(
                selected,
                **values,
                expected_updated_at=st.session_state.get(BASE_UPDATED_AT_STATE_KEY),
            )
            message = "故事片段已保存並重新讀取。"
    except ApplicationError as exc:
        present_exception(
            exc,
            operation_zh_tw="儲存故事片段草稿",
            key_prefix="story_fragment_save",
        )
        return
    st.session_state[PENDING_SELECTION_STATE_KEY] = draft.id
    st.session_state[BOUND_DRAFT_STATE_KEY] = None
    st.session_state[FLASH_STATE_KEY] = message
    st.rerun()


def _archive(services: Any, draft_id: str) -> None:
    try:
        services.story_fragment_drafts.archive_draft(
            draft_id,
            expected_updated_at=st.session_state.get(BASE_UPDATED_AT_STATE_KEY),
        )
    except ApplicationError as exc:
        present_exception(
            exc,
            operation_zh_tw="收起故事片段草稿",
            key_prefix="story_fragment_archive",
        )
        return
    st.session_state[PENDING_SELECTION_STATE_KEY] = _NEW_DRAFT
    st.session_state[BOUND_DRAFT_STATE_KEY] = None
    st.session_state[FLASH_STATE_KEY] = "故事片段已收起；內容仍保留在本機資料庫。"
    st.rerun()


def _reject_inspiration(message: str) -> None:
    """Fail closed without replacing any editor or provenance values."""

    st.session_state.pop(PENDING_FORM_STATE_KEY, None)
    st.session_state[INSPIRATION_NOTICE_STATE_KEY] = (
        f"{message} 編輯器與保存內容都沒有變更。"
    )


def _apply_inspiration(*, fill_blanks_only: bool) -> None:
    title = str(st.session_state.get(TITLE_STATE_KEY, ""))
    fragment_text = str(st.session_state.get(TEXT_STATE_KEY, ""))
    context_notes = str(st.session_state.get(CONTEXT_STATE_KEY, ""))
    tags = _split_tags(str(st.session_state.get(TAGS_STATE_KEY, "")))
    if len(title) > 200:
        _reject_inspiration("標題最多 200 個字元；請先縮短再使用本機靈感。")
        return
    if len(fragment_text) > MAX_STORY_FRAGMENT_CHARS:
        _reject_inspiration("故事片段正文最多 500,000 個字元；請先縮短再使用本機靈感。")
        return
    if len(context_notes) > 4_000:
        _reject_inspiration("上下文／延伸備註最多 4,000 個字元；請先縮短再使用本機靈感。")
        return
    if len(tags) > MAX_STORY_FRAGMENT_TAGS:
        _reject_inspiration("故事片段最多 24 個不同標籤；請先減少再使用本機靈感。")
        return
    if any(len(tag) > 200 for tag in tags):
        _reject_inspiration("每個故事片段標籤最多 200 個字元；請先縮短再使用本機靈感。")
        return
    mode = (
        StoryFragmentGenerationMode.FILL_BLANKS
        if fill_blanks_only
        else StoryFragmentGenerationMode.REROLL_ALL
    )
    try:
        current = StoryFragmentEditorContent(
            title=title,
            fragment_text=fragment_text,
            context_notes=context_notes,
            tags=tags,
        )
        prepared = StoryFragmentInspirationService.prepare_editor(
            current=current,
            fragment_kind=_kind(st.session_state.get(KIND_STATE_KEY)),
            mode=mode,
        )
    except ValueError:
        _reject_inspiration(
            "目前編輯器內容不符合故事片段限制；請檢查標題、正文、備註與標籤。"
        )
        return
    if prepared is None:
        st.session_state.pop(PENDING_FORM_STATE_KEY, None)
        st.session_state[INSPIRATION_NOTICE_STATE_KEY] = (
            "目前沒有可補的空白欄位；編輯器與保存內容都沒有變更。"
        )
        return
    st.session_state[PENDING_FORM_STATE_KEY] = {
        TITLE_STATE_KEY: prepared.content.title,
        TEXT_STATE_KEY: prepared.content.fragment_text,
        CONTEXT_STATE_KEY: prepared.content.context_notes,
        TAGS_STATE_KEY: _join_tags(prepared.content.tags),
        GENERATION_MODE_STATE_KEY: prepared.generation_mode.value,
        GENERATION_SEED_STATE_KEY: prepared.generation_seed,
    }
    changed = "、".join(field.value for field in prepared.changed_fields)
    st.session_state[INSPIRATION_NOTICE_STATE_KEY] = (
        f"本機靈感已填入編輯器（{changed}）；尚未保存，仍可自由改寫。"
    )


def _render_archived(service: Any) -> None:
    archived = tuple(
        draft
        for draft in service.list_drafts(include_archived=True)
        if draft.status.value == "archived"
    )
    if not archived:
        return
    with st.expander(f"已收起的故事片段（唯讀） · {len(archived)}"):
        st.caption("收起不等於刪除；可在這裡下載保存當下的 JSON 備份。")
        for draft in archived:
            st.markdown(f"**{draft.title}** · {_KIND_LABELS[draft.fragment_kind]}")
            st.caption(f"草稿 `{draft.id}` · 最後更新 `{draft.updated_at}`")
            try:
                payload = service.export_draft_json(draft.id)
            except ApplicationError:
                st.warning(f"暫時無法讀取 `{draft.id}` 的 JSON。")
            else:
                st.download_button(
                    f"下載《{draft.title}》JSON",
                    data=payload,
                    file_name=f"story-fragment-{draft.id}.json",
                    mime="application/json",
                    key=f"{STATE_PREFIX}_archived_download_{draft.id}",
                )


def _offline_ai_candidate() -> dict[str, object]:
    current = StoryFragmentEditorContent(
        title=str(st.session_state.get(TITLE_STATE_KEY, "")),
        fragment_text=str(st.session_state.get(TEXT_STATE_KEY, "")),
        context_notes=str(st.session_state.get(CONTEXT_STATE_KEY, "")),
        tags=_split_tags(str(st.session_state.get(TAGS_STATE_KEY, ""))),
    )
    prepared = StoryFragmentInspirationService.prepare_editor(
        current=current,
        fragment_kind=_kind(st.session_state.get(KIND_STATE_KEY)),
        mode=StoryFragmentGenerationMode.REROLL_ALL,
    )
    if prepared is None:
        raise RuntimeError("offline story candidate is unavailable")
    return {
        "title": prepared.content.title,
        "fragment_text": prepared.content.fragment_text,
        "context_notes": prepared.content.context_notes,
        "tags": prepared.content.tags,
    }


def _writing_request_defaults() -> WritingRequestDefaults:
    return build_writing_request_defaults(
        WritingRequestSurface.STORY_FRAGMENT,
        sources=(
            WritingContextSourceInput(
                source_id=AI_FRAGMENT_TEXT_SOURCE_ID,
                source_type=AI_CONTEXT_SOURCE_TYPE,
                label="目前故事正文",
                text=str(st.session_state.get(TEXT_STATE_KEY, "")),
                included=True,
                max_visible_chars=6_000,
                priority=0,
            ),
            WritingContextSourceInput(
                source_id=AI_CONTEXT_NOTES_SOURCE_ID,
                source_type=AI_CONTEXT_SOURCE_TYPE,
                label="目前上下文／延伸備註",
                text=str(st.session_state.get(CONTEXT_STATE_KEY, "")),
                included=True,
                max_visible_chars=2_000,
                priority=10,
            ),
        ),
    )


def _render_ai_generation(services: Any, *, assistant_slot: DeltaGenerator) -> None:
    with assistant_slot:
        generate_status, refine_status = render_passive_creative_runtime_status(
            services,
            task=GenerationTask.STORY_FRAGMENT,
        )
        continue_status = passive_creative_action_status(
            services, st.session_state, task=GenerationTask.STORY_FRAGMENT,
            action=CreativeCandidateAction.CONTINUE,
        )
        # Reserve the visible action position, then validate this rerun's values.
        action_slot = st.container(key=f"{STATE_PREFIX}_primary_actions")
        writing_request = render_writing_request_controls(
            _writing_request_defaults(),
            key_prefix=AI_WRITING_REQUEST_PREFIX,
            narrow=True,
        )
        with st.expander("這次的指示與套用方式", expanded=False):
            instruction = st.text_area(
                "這次想補什麼或怎麼改？（選填）",
                key=f"{STATE_PREFIX}_ai_instruction",
                max_chars=4_000,
                height=90,
            )
            apply_mode = st.radio(
                "之後套用時",
                options=("rewrite", "fill"),
                format_func=lambda value: (
                    "以候選稿更新欄位" if value == "rewrite" else "只補目前空白欄位"
                ),
                key=f"{STATE_PREFIX}_ai_apply_mode",
            )
            st.caption("沿用頂部「狀態」的 provider、模型與 fallback；只有明確按下動作才執行。")
        inputs = {
            "title": str(st.session_state.get(TITLE_STATE_KEY, "")),
            "fragment_kind": _kind(st.session_state.get(KIND_STATE_KEY)).value,
            "tags": list(_split_tags(str(st.session_state.get(TAGS_STATE_KEY, "")))),
        }
        editor_identity = str(
            st.session_state.get(SELECTED_DRAFT_STATE_KEY, _NEW_DRAFT)
        )
        prepared_continuation = None
        continuation_reason = ""
        try:
            source = _continuation_source()
        except ValueError:
            source = None
            continuation_reason = "目前正文超出續寫可驗證的容量；原稿保留，可繼續手寫及保存。"
        if source is None:
            continuation_reason = continuation_reason or "先寫下故事正文，才能從目前結尾續寫。"
        elif writing_request is not None:
            try:
                prepared_continuation = prepare_story_fragment_continuation(
                    source, length_spec=writing_request.length_spec,
                    context_spec=writing_request.context_spec,
                    context_sources=writing_request.resolved_sources,
                )
            except ValueError:
                continuation_reason = (
                    "續寫需包含目前正文，且必填來源與篇幅必須符合預算；"
                    "請檢查上下文與長度。"
                )
        with action_slot:
            busy = render_candidate_action_state(STATE_PREFIX)
            actions = st.columns(2)
            generate = actions[0].button(
                "生成候選稿",
                key=f"{STATE_PREFIX}_ai_generate",
                type="primary",
                disabled=busy or not generate_status.ready or writing_request is None,
                width="stretch",
            )
            refine = actions[1].button(
                "精修目前內容",
                key=f"{STATE_PREFIX}_ai_refine",
                disabled=busy or not refine_status.ready or writing_request is None,
                width="stretch",
            )
            continue_requested = st.button(
                "續寫候選", key=f"{STATE_PREFIX}_ai_continue", width="stretch",
                disabled=(busy or not continue_status.ready or prepared_continuation is None),
            )
            if not continue_status.ready:
                st.caption(continue_status.reason)
            elif continuation_reason:
                st.caption(continuation_reason)
        if prepared_continuation is not None:
            with st.expander("續寫將帶入的尾端上下文", expanded=False):
                st.caption("上方一般上下文預覽適用於生成／精修。續寫只使用這份尾端投影；目標長度只計新增正文。")
                st.json(prepared_continuation.report.model_dump(mode="json"), expanded=False)
                for context_source in prepared_continuation.context_sources:
                    st.caption(context_source.source_id)
                    st.code(context_source.text, language=None, wrap_lines=True)
        action = (
            CreativeCandidateAction.GENERATE
            if generate
            else CreativeCandidateAction.CONTINUE
            if continue_requested
            else CreativeCandidateAction.REFINE
            if refine
            else None
        )
        if action is not None and writing_request is not None:
            request_candidate(
                services,
                prefix=STATE_PREFIX,
                task=GenerationTask.STORY_FRAGMENT,
                action=action,
                inputs=inputs,
                instruction=instruction,
                apply_mode="append" if action is CreativeCandidateAction.CONTINUE else apply_mode,
                offline_candidate=_offline_ai_candidate,
                writing_request=writing_request,
                editor_identity=editor_identity,
                **(
                    {"continuation_source": source}
                    if action is CreativeCandidateAction.CONTINUE else {}
                ),
            )
            st.rerun()

    render_candidate_error(STATE_PREFIX)
    retained_response = candidate_authoring_response(STATE_PREFIX)
    candidate_is_continuation = (
        retained_response is not None
        and retained_response.provenance.action is CreativeCandidateAction.CONTINUE
    )
    decision = render_candidate_card(
        STATE_PREFIX,
        field_labels={
            "title": "候選標題",
            "fragment_text": "候選故事正文",
            "context_notes": "候選上下文／延伸備註",
            "tags": "候選標籤",
        },
        current_authoring_state_sha256=(
            authoring_editor_state_sha256(
                task=GenerationTask.STORY_FRAGMENT,
                inputs=inputs,
                instruction=instruction,
                writing_request=writing_request,
                editor_identity=editor_identity,
                continuation_source=source if candidate_is_continuation else None,
            )
            if writing_request is not None
            else None
        ),
        current_continuation_source=source,
    )
    if decision is None:
        return
    choice, candidate, apply_mode = decision
    if choice == "discard":
        clear_candidate_state(STATE_PREFIX)
        st.rerun()
    if apply_mode == "append":
        receipt = st.session_state.get(f"{STATE_PREFIX}_ai_append_receipt")
        if not isinstance(receipt, StoryFragmentAppendReceipt):
            st.error("續寫的追加證據已失效；原稿沒有變更。")
            return
        st.session_state[PENDING_APPEND_STATE_KEY] = receipt
        st.rerun()
    mappings = {
        "title": TITLE_STATE_KEY,
        "fragment_text": TEXT_STATE_KEY,
        "context_notes": CONTEXT_STATE_KEY,
        "tags": TAGS_STATE_KEY,
    }
    pending: dict[str, object] = {}
    for field, state_key in mappings.items():
        value: object = candidate.get(field, "")
        if field == "tags" and isinstance(value, list):
            value = _join_tags(tuple(str(tag) for tag in value))
        current = st.session_state.get(state_key, "")
        if candidate_value_replaces_current(value, current, apply_mode=apply_mode):
            pending[state_key] = value
    st.session_state[PENDING_FORM_STATE_KEY] = pending
    st.session_state[INSPIRATION_NOTICE_STATE_KEY] = (
        "已把 AI 候選稿套用到編輯器；尚未正式保存，仍可逐字修改。"
    )
    clear_candidate_state(STATE_PREFIX)
    st.rerun()


def _render_content_refinement(services: Any) -> None:
    prefix = f"{STATE_PREFIX}_content"
    original = str(st.session_state.get(TEXT_STATE_KEY, ""))
    selected = st.session_state.get(SELECTED_DRAFT_STATE_KEY)
    binding = _durable_refinement_binding(services, selected=selected)
    with st.expander("✦ 通用內容精修（10 種模式）", expanded=False):
        render_refinement_controls(
            services,
            prefix=prefix,
            artifact_kind=ArtifactKind.STORY_FRAGMENT,
            original_text=original,
            context_snapshot={
                "title": str(st.session_state.get(TITLE_STATE_KEY, "")),
                "fragment_kind": _kind(st.session_state.get(KIND_STATE_KEY)).value,
                "context_notes": str(st.session_state.get(CONTEXT_STATE_KEY, "")),
                "tags": list(_split_tags(str(st.session_state.get(TAGS_STATE_KEY, "")))),
            },
            durable_binding=binding,
        )
    render_refinement_failure(prefix)
    durable_undo = render_durable_refinement_history(
        services,
        prefix=prefix,
        binding=binding,
    )
    if durable_undo is not None and durable_undo.after_snapshot is not None:
        restored_id = str(durable_undo.after_snapshot["id"])
        st.session_state[PENDING_SELECTION_STATE_KEY] = restored_id
        st.session_state[BOUND_DRAFT_STATE_KEY] = None
        st.session_state[FLASH_STATE_KEY] = "已建立復原版本；重新啟動後仍可在精修版本紀錄查閱。"
        st.rerun()
    undo_text = render_refinement_undo(prefix, current_text=original)
    if undo_text is not None:
        st.session_state[PENDING_FORM_STATE_KEY] = {TEXT_STATE_KEY: undo_text}
        st.session_state[INSPIRATION_NOTICE_STATE_KEY] = (
            "已復原上次精修套用；這次變更仍只在編輯器，尚未正式保存。"
        )
        st.rerun()

    decision = render_refinement_candidate(prefix)
    if decision is None:
        return
    choice, candidate = decision
    try:
        durable_result = commit_durable_refinement_decision(
            services,
            prefix=prefix,
            decision=choice,
            candidate=candidate,
        )
    except (RuntimeError, TypeError, ValueError):
        set_refinement_failure(
            prefix,
            GenerationFailure(
                category=GenerationErrorCategory.IDEMPOTENCY_CONFLICT,
                safe_message="已保存草稿版本已變更；候選仍保留，沒有覆寫任何內容。",
            ),
        )
        st.rerun()
    if durable_result is not None:
        if choice is RefinementUIDecision.REJECT:
            clear_refinement_state(prefix)
            st.session_state[FLASH_STATE_KEY] = "已拒絕並保存候選決定；原稿沒有變更。"
            st.rerun()
        after = getattr(durable_result, "after_snapshot", None)
        if not isinstance(after, dict) or not isinstance(after.get("id"), str):
            set_refinement_failure(
                prefix,
                GenerationFailure(
                    category=GenerationErrorCategory.INVALID_RESPONSE,
                    safe_message="候選版本收據不完整；沒有覆寫任何內容。",
                ),
            )
            st.rerun()
        st.session_state[PENDING_SELECTION_STATE_KEY] = after["id"]
        st.session_state[BOUND_DRAFT_STATE_KEY] = None
        clear_refinement_state(prefix)
        st.session_state[FLASH_STATE_KEY] = (
            "精修候選已建立正式草稿版本與可復原收據。"
            if choice is not RefinementUIDecision.SAVE_AS_NEW
            else "精修候選已另存為新草稿，來源與版本關係已保存。"
        )
        st.rerun()
    if choice is RefinementUIDecision.REJECT:
        clear_refinement_state(prefix)
        st.session_state[INSPIRATION_NOTICE_STATE_KEY] = "已拒絕精修候選；原稿沒有變更。"
        st.rerun()
    try:
        if choice is RefinementUIDecision.SAVE_AS_NEW:
            receipt = candidate_receipt_for_save_as_new(
                candidate,
                current_text=original,
            )
            title = str(st.session_state.get(TITLE_STATE_KEY, "")).strip()
            services.story_fragment_drafts.create_draft(
                project_id=str(st.session_state.get(PROJECT_STATE_KEY, "")).strip() or None,
                title=(f"{title}（精修副本）" if title else "精修副本")[:200],
                fragment_kind=_kind(st.session_state.get(KIND_STATE_KEY)),
                fragment_text=receipt.after_text,
                context_notes=str(st.session_state.get(CONTEXT_STATE_KEY, "")),
                tags=_split_tags(str(st.session_state.get(TAGS_STATE_KEY, ""))),
                generation_mode=StoryFragmentGenerationMode.MANUAL.value,
                generation_seed="",
            )
            clear_refinement_state(prefix)
            st.session_state[FLASH_STATE_KEY] = (
                "精修候選已另存為新故事片段；目前原稿與綁定草稿均未覆寫。"
            )
            st.rerun()
        receipt = refinement_receipt(choice, candidate, current_text=original)
    except (InvalidRefinementCandidateError, StaleRefinementBaseError, ApplicationError):
        set_refinement_failure(
            prefix,
            GenerationFailure(
                category=GenerationErrorCategory.IDEMPOTENCY_CONFLICT,
                safe_message="目前原稿已變更或候選無法安全套用。",
            ),
        )
        st.rerun()
    record_refinement_apply(prefix, receipt, candidate)
    st.session_state[PENDING_FORM_STATE_KEY] = {TEXT_STATE_KEY: receipt.after_text}
    st.session_state[INSPIRATION_NOTICE_STATE_KEY] = (
        "已把精修候選套用到編輯器；尚未正式保存，可用工作階段復原。"
    )
    clear_refinement_state(prefix)
    st.rerun()


def render(*, services: Any | None = None) -> None:
    """Render one local-first fragment editor without creating Story state."""

    services = services or get_services()
    _restore_editor_state()
    page_header(
        PAGE_LABEL,
        "不用先開作品。AI 結果先進候選區；明確套用才改編輯器，正式保存才寫入草稿。",
        eyebrow="",
        compact=True,
    )

    pending_selection = st.session_state.pop(PENDING_SELECTION_STATE_KEY, None)
    if pending_selection is not None:
        st.session_state[SELECTED_DRAFT_STATE_KEY] = str(pending_selection)

    flash = st.session_state.pop(FLASH_STATE_KEY, None)
    if flash:
        st.success(str(flash))
    notice = st.session_state.pop(INSPIRATION_NOTICE_STATE_KEY, None)
    if notice:
        st.info(str(notice))

    service = getattr(services, "story_fragment_drafts", None)
    draft_list_available = service is not None
    if not draft_list_available:
        present_exception(
            RuntimeError("story fragment draft service unavailable"),
            operation_zh_tw="使用故事片段草稿服務",
            key_prefix="story_fragment_service",
            data_safety=ErrorDataSafety(
                message_zh_tw=(
                    "目前編輯器文字、草稿選擇與所有已保存版本均保留；沒有寫入或覆蓋內容。"
                ),
                author_input_preserved=True,
                original_preserved=True,
                draft_selection_preserved=True,
            ),
        )

    project_options, project_labels = _project_options(services)
    active_drafts: list[StoryFragmentDraft] = []
    if draft_list_available and service is not None:
        try:
            active_drafts = list(service.list_drafts(include_archived=False))
        except (ApplicationError, RuntimeError, TypeError, ValueError) as exc:
            draft_list_available = False
            present_exception(
                exc,
                operation_zh_tw="讀取故事片段草稿清單",
                key_prefix="story_fragment_list",
                data_safety=ErrorDataSafety(
                    message_zh_tw=(
                        "目前編輯器文字、草稿選擇與所有已保存版本均保留；沒有寫入或覆蓋內容。"
                    ),
                    author_input_preserved=True,
                    original_preserved=True,
                    draft_selection_preserved=True,
                ),
            )
    by_id = {draft.id: draft for draft in active_drafts}

    # Widget values are available before rendering; bind before mounting the editor.
    if draft_list_available:
        options = [_NEW_DRAFT, *by_id]
        selected_before = st.session_state.get(SELECTED_DRAFT_STATE_KEY)
        if selected_before not in options:
            st.session_state[SELECTED_DRAFT_STATE_KEY] = _NEW_DRAFT
        selected = str(st.session_state[SELECTED_DRAFT_STATE_KEY])
    else:
        selected = str(st.session_state.get(SELECTED_DRAFT_STATE_KEY) or _NEW_DRAFT)
        st.session_state[SELECTED_DRAFT_STATE_KEY] = selected

    # Detach this value from the late-mounted selector's widget lifetime. An AI
    # action reruns before that selector mounts; cleanup must not turn a saved
    # draft into a new blank editor on the following run.
    st.session_state[SELECTED_DRAFT_STATE_KEY] = selected
    bound = st.session_state.get(BOUND_DRAFT_STATE_KEY)
    if draft_list_available and service is not None and selected != bound:
        if selected == _NEW_DRAFT:
            _bind_new()
        else:
            try:
                _bind_draft(service.get_draft(selected))
            except (ApplicationError, RuntimeError, TypeError, ValueError) as exc:
                present_exception(
                    exc,
                    operation_zh_tw="讀取故事片段草稿",
                    key_prefix="story_fragment_load",
                    data_safety=ErrorDataSafety(
                        message_zh_tw=(
                            "目前編輯器文字與原草稿選擇均保留；未載入失敗的草稿，也沒有寫入資料。"
                        ),
                        author_input_preserved=True,
                        original_preserved=True,
                        draft_selection_preserved=True,
                    ),
                )
                draft_list_available = False
    if not draft_list_available:
        _ensure_local_editor_state(selected=selected)
        st.caption("草稿清單暫時不可用；目前編輯器保持原狀，待服務恢復後再切換或保存草稿。")
    _apply_pending_form()
    _apply_pending_append()

    with st.container(key=f"{STATE_PREFIX}_workspace"):
        editor_column, assistant_column = st.columns((2, 1), gap="large")
        with editor_column, st.container(key=f"{STATE_PREFIX}_editor"):
            st.radio(
                "這張紙頁想寫什麼？",
                tuple(StoryFragmentKind),
                format_func=lambda value: _KIND_LABELS[value],
                horizontal=True,
                key=KIND_STATE_KEY,
                on_change=_capture_editor_state,
            )
            st.text_input(
                "片段標題（必填）",
                key=TITLE_STATE_KEY,
                max_chars=200,
                placeholder="例如：雨夜月台的第二封信",
                on_change=_capture_editor_state,
            )
            st.text_area(
                "故事正文（必填）",
                key=TEXT_STATE_KEY,
                height=300,
                placeholder="可以只寫一個畫面、一段對話，或故事的第一頁……",
                on_change=_capture_editor_state,
            )
            save_requested = st.button(
                "正式保存故事片段" if selected == _NEW_DRAFT else "保存這張故事紙頁",
                key=f"{STATE_PREFIX}_save",
                type="primary",
                disabled=not draft_list_available,
                width="stretch",
            )
            with st.expander("作品歸屬、備註與標籤", expanded=False):
                st.selectbox(
                    "作品歸屬（可選）",
                    project_options,
                    format_func=lambda project_id: project_labels[project_id],
                    key=PROJECT_STATE_KEY,
                    on_change=_capture_editor_state,
                )
                st.text_area(
                    "上下文／延伸備註（選填）",
                    key=CONTEXT_STATE_KEY,
                    height=130,
                    max_chars=4_000,
                    placeholder="角色關係、前因後果、下一步想寫的方向……",
                    on_change=_capture_editor_state,
                )
                st.text_area(
                    "標籤（選填，以逗號或換行分隔）",
                    key=TAGS_STATE_KEY,
                    height=130,
                    placeholder="懸疑、雨夜、重逢",
                    on_change=_capture_editor_state,
                )
        with assistant_column:
            assistant_slot = st.container(key=f"{STATE_PREFIX}_assistant")
    if save_requested:
        _save(services, selected)

    _render_ai_generation(services, assistant_slot=assistant_slot)
    if draft_list_available:
        _render_content_refinement(services)
    else:
        st.caption("正式草稿服務恢復後，才會重新開放通用內容精修與版本收據。")

    with st.expander("本機故事靈感（只填編輯器）", expanded=False):
        st.caption("只改目前編輯器，不會自動保存，也不會加入作品或建立正式故事版本。")
        inspiration_actions = st.columns(2)
        fill_blanks = inspiration_actions[0].button(
            "✦ 只補空白",
            key=f"{STATE_PREFIX}_fill_blanks",
            width="stretch",
        )
        reroll_all = inspiration_actions[1].button(
            "全部重抽（取代編輯器）",
            key=f"{STATE_PREFIX}_reroll_all",
            width="stretch",
        )
        reroll_requested = bool(st.session_state.get(REROLL_CONFIRM_STATE_KEY, False))
        if reroll_all:
            st.session_state[REROLL_CONFIRM_STATE_KEY] = True
            reroll_requested = True
        reroll_confirmed = False
        if reroll_requested:
            st.warning("全部重抽會取代目前尚未保存的標題、正文、備註與標籤；正式草稿不會被寫入或刪除。")
            confirm_reroll, cancel_reroll = st.columns(2)
            reroll_confirmed = confirm_reroll.button(
                "確認全部重抽",
                key=f"{STATE_PREFIX}_reroll_all_confirm",
                type="secondary",
                width="stretch",
            )
            if cancel_reroll.button(
                "保留目前內容",
                key=f"{STATE_PREFIX}_reroll_all_cancel",
                width="stretch",
            ):
                st.session_state.pop(REROLL_CONFIRM_STATE_KEY, None)
                st.rerun()
        generation_mode = str(
            st.session_state.get(
                GENERATION_MODE_STATE_KEY,
                StoryFragmentGenerationMode.MANUAL.value,
            )
        )
        st.caption(f"目前內容起點：{_GENERATION_LABELS.get(generation_mode, '未知')}")
    if fill_blanks:
        st.session_state.pop(REROLL_CONFIRM_STATE_KEY, None)
        _apply_inspiration(fill_blanks_only=True)
        st.rerun()
    if reroll_confirmed:
        st.session_state.pop(REROLL_CONFIRM_STATE_KEY, None)
        _apply_inspiration(fill_blanks_only=False)
        st.rerun()

    with st.expander("紙頁管理與已保存內容", expanded=False):
        st.caption("切換紙頁前請先保存；未保存的編輯器內容不會自動寫入資料庫。")
        selector, new_action = st.columns((4, 1), vertical_alignment="bottom")
        if draft_list_available:
            selector.selectbox(
                "翻開哪張故事紙頁？",
                options,
                format_func=lambda value: (
                    "＋ 一張新的故事片段"
                    if value == _NEW_DRAFT
                    else (
                        f"{by_id[value].title} · {_KIND_LABELS[by_id[value].fragment_kind]} · "
                        f"{project_labels.get(by_id[value].project_id or '', '作品歸屬待確認')}"
                    )
                ),
                key=SELECTED_DRAFT_STATE_KEY,
                on_change=_capture_editor_state,
            )
        else:
            selector.selectbox(
                "翻開哪張故事紙頁？",
                (selected,),
                format_func=lambda _value: "目前編輯器（草稿清單暫時不可用）",
                key=SELECTED_DRAFT_STATE_KEY,
                disabled=True,
            )
        if new_action.button(
            "新紙頁",
            key=f"{STATE_PREFIX}_new",
            disabled=not draft_list_available or selected == _NEW_DRAFT,
            width="stretch",
        ):
            st.session_state[PENDING_SELECTION_STATE_KEY] = _NEW_DRAFT
            st.session_state[BOUND_DRAFT_STATE_KEY] = None
            st.session_state[FLASH_STATE_KEY] = "已翻開空白故事紙頁；尚未寫入資料庫。"
            st.rerun()

        if draft_list_available and service is not None and selected != _NEW_DRAFT:
            confirmed = st.checkbox(
                "我確認要把這張紙頁收起來",
                key=ARCHIVE_CONFIRM_STATE_KEY,
            )
            if st.button(
                "收起故事片段（不刪除）",
                key=f"{STATE_PREFIX}_archive",
                disabled=not confirmed,
                width="stretch",
            ):
                _archive(services, selected)

            st.markdown("#### 帶走已保存內容")
            try:
                export_json = service.export_draft_json(selected)
            except ApplicationError:
                st.warning("目前無法重新讀取這份草稿，因此暫不提供匯出。")
            else:
                st.download_button(
                    "下載 JSON 備份",
                    data=export_json,
                    file_name=f"story-fragment-{selected}.json",
                    mime="application/json",
                    key=f"{STATE_PREFIX}_download",
                )

        if draft_list_available and service is not None:
            _render_archived(service)
        st.caption(
            "這份獨立草稿不會自動加入任何作品。要放進作品時，"
            "會先讓你選擇目標作品並確認內容。"
        )
    _capture_editor_state()


__all__ = [
    "BASE_UPDATED_AT_STATE_KEY",
    "BOUND_DRAFT_STATE_KEY",
    "PAGE_KEY",
    "PAGE_LABEL",
    "PENDING_SELECTION_STATE_KEY",
    "SELECTED_DRAFT_STATE_KEY",
    "render",
]
