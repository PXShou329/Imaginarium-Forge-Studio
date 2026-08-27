"""A project-free desk for character and background image prompts."""

from __future__ import annotations

import json
import re
import time
from collections.abc import Mapping
from typing import Any, Final

import streamlit as st
from pydantic import SecretStr

from imaginarium_forge.application.errors import ApplicationError, ConflictError
from imaginarium_forge.application.services.ollama_model_service import (
    is_loopback_ollama_endpoint,
)
from imaginarium_forge.application.services.prompt_scratch_generation_service import (
    PromptScratchBrief,
    PromptScratchGenerationMode,
    PromptScratchGenerationService,
    PromptScratchKind,
)
from imaginarium_forge.domain.character.biography_draft import (
    normalize_english_image_prompt,
)
from imaginarium_forge.domain.character.gender import CharacterGender
from imaginarium_forge.domain.common.ids import new_id
from imaginarium_forge.domain.prompt.scratch_recovery import (
    PromptScratchCommitStatus,
    PromptScratchRecoveryState,
)
from imaginarium_forge.providers.errors import ProviderError
from imaginarium_forge.ui.bootstrap import build_creative_provider, get_services
from imaginarium_forge.ui.components import page_header
from imaginarium_forge.ui.ollama_session import render_ollama_model_selector
from imaginarium_forge.ui.openai_session import (
    OPENAI_API_KEY_SESSION_KEY,
    apply_openai_session_state_transitions,
    consume_openai_key_after_action,
    render_openai_session_summary,
)
from imaginarium_forge.ui.prompt_scratch_autosave import (
    EditorStatus,
    autosave_due,
    legacy_gender_value,
    merge_editor_snapshot,
    needs_dirty_guard,
    reconcile_capture_result,
    reconcile_commit_result,
    resolve_guard_request,
    serialize_editor_snapshot,
    should_capture,
    snapshot_hash,
    status_view,
)
from imaginarium_forge.ui.provider_diagnostics import diagnose_provider_error

PAGE_KEY = "Prompt Scratchpad"
PAGE_LABEL = "圖片提示詞草稿"

STATE_PREFIX = "prompt_scratchpad"
SELECTED_STATE_KEY = f"{STATE_PREFIX}_selected"
BOUND_STATE_KEY = f"{STATE_PREFIX}_bound"
PENDING_SELECTION_STATE_KEY = f"{STATE_PREFIX}_pending_selection"
PENDING_FORM_STATE_KEY = f"{STATE_PREFIX}_pending_form"
FLASH_STATE_KEY = f"{STATE_PREFIX}_flash"
ERROR_STATE_KEY = f"{STATE_PREFIX}_error"
ARCHIVE_CONFIRM_STATE_KEY = f"{STATE_PREFIX}_archive_confirm"
PROVIDER_STATE_KEY = f"{STATE_PREFIX}_injected_provider"
OPENAI_API_KEY_STATE_KEY = OPENAI_API_KEY_SESSION_KEY
OPENAI_CONSENT_STATE_KEY = f"{STATE_PREFIX}_openai_consent"
CONSENT_RESET_PENDING_STATE_KEY = f"{STATE_PREFIX}_consent_reset_pending"
EDITOR_SNAPSHOT_STATE_KEY = f"{STATE_PREFIX}_editor_snapshot"
AUTOSAVE_CONTEXT_STATE_KEY = f"{STATE_PREFIX}_autosave_context"
SWITCH_REQUEST_STATE_KEY = f"{STATE_PREFIX}_switch_request"
PENDING_HANDOFF_STATE_KEY = f"{STATE_PREFIX}_pending_handoff"
RECOVERY_REQUEST_STATE_KEY = f"{STATE_PREFIX}_recovery_request"
RECOVERY_DISMISSED_STATE_KEY = f"{STATE_PREFIX}_recovery_dismissed"
RECOVERY_DISCARD_CONFIRM_STATE_KEY = f"{STATE_PREFIX}_recovery_discard_confirm"
NAV_GUARD_TARGET_STATE_KEY = f"{STATE_PREFIX}_nav_guard_target"
NAV_GUARD_BYPASS_STATE_KEY = f"{STATE_PREFIX}_nav_guard_bypass"

AUTOSAVE_DEBOUNCE_SECONDS = 1.5
_NEW_DRAFT = "__new__"
_FORM_KEYS: Final = {
    "kind": f"{STATE_PREFIX}_kind",
    "gender": f"{STATE_PREFIX}_gender",
    "title": f"{STATE_PREFIX}_title",
    "character_name": f"{STATE_PREFIX}_character_name",
    "character_image_prompt_en": f"{STATE_PREFIX}_character_prompt",
    "background_image_prompt_en": f"{STATE_PREFIX}_background_prompt",
    "notes": f"{STATE_PREFIX}_notes",
    "link_project": f"{STATE_PREFIX}_link_project",
    "project_id": f"{STATE_PREFIX}_project_id",
}

_KIND_LABELS: Final = {
    PromptScratchKind.CHARACTER: "只做角色圖",
    PromptScratchKind.BACKGROUND: "只做背景圖",
    PromptScratchKind.BOTH: "角色圖＋背景圖",
}


def _blank_form() -> dict[str, Any]:
    return {
        "kind": PromptScratchKind.CHARACTER,
        "gender": CharacterGender.FEMALE,
        "title": "",
        "character_name": "",
        "character_image_prompt_en": "",
        "background_image_prompt_en": "",
        "notes": "",
        "link_project": False,
        "project_id": None,
    }


def _record_value(record: Any, name: str, default: Any = "") -> Any:
    if isinstance(record, Mapping):
        return record.get(name, default)
    return getattr(record, name, default)


def _infer_kind(record: Any) -> PromptScratchKind:
    has_character = bool(str(_record_value(record, "character_image_prompt_en", "")).strip())
    has_background = bool(str(_record_value(record, "background_image_prompt_en", "")).strip())
    if has_character and has_background:
        return PromptScratchKind.BOTH
    if has_background:
        return PromptScratchKind.BACKGROUND
    return PromptScratchKind.CHARACTER


def _infer_gender(record: Any) -> CharacterGender:
    prompt = str(_record_value(record, "character_image_prompt_en", ""))
    return CharacterGender(legacy_gender_value(prompt))


def _form_from_record(record: Any) -> dict[str, Any]:
    project_id = _record_value(record, "project_id", None)
    editor_kind = _record_value(record, "editor_kind", _infer_kind(record))
    gender = _record_value(record, "editor_gender", _infer_gender(record))
    return {
        "kind": PromptScratchKind(_state_value(editor_kind)),
        "gender": CharacterGender(_state_value(gender)),
        "title": str(_record_value(record, "title", "")),
        "character_name": str(_record_value(record, "character_name", "")),
        "character_image_prompt_en": str(_record_value(record, "character_image_prompt_en", "")),
        "background_image_prompt_en": str(_record_value(record, "background_image_prompt_en", "")),
        "notes": str(_record_value(record, "notes", "")),
        "link_project": project_id is not None,
        "project_id": str(project_id) if project_id else None,
    }


def _write_form(values: Mapping[str, Any]) -> None:
    blank = _blank_form()
    for field, key in _FORM_KEYS.items():
        st.session_state[key] = values.get(field, blank[field])


def _mounted_form_values() -> dict[str, object]:
    values: dict[str, object] = {}
    for field, key in _FORM_KEYS.items():
        if key in st.session_state:
            values[field] = st.session_state[key]
    return values


def _editor_snapshot() -> dict[str, str | bool | None]:
    previous = st.session_state.get(EDITOR_SNAPSHOT_STATE_KEY, _blank_form())
    if not isinstance(previous, Mapping):
        previous = _blank_form()
    return merge_editor_snapshot(previous, _mounted_form_values())


def _autosave_context() -> dict[str, Any] | None:
    value = st.session_state.get(AUTOSAVE_CONTEXT_STATE_KEY)
    return value if isinstance(value, dict) else None


def _context_status(context: Mapping[str, Any] | None) -> EditorStatus:
    value = str((context or {}).get("status", "clean"))
    if value in {"clean", "dirty", "saving", "saved", "failed", "conflict"}:
        return value  # type: ignore[return-value]
    return "failed"


def _bind_autosave_editor(selected: str, record: Any | None) -> None:
    """Freeze the target/base token before any callback can capture text."""

    snapshot = serialize_editor_snapshot(
        _blank_form() if record is None else _form_from_record(record)
    )
    committed_hash = snapshot_hash(snapshot)
    st.session_state[EDITOR_SNAPSHOT_STATE_KEY] = snapshot
    st.session_state[AUTOSAVE_CONTEXT_STATE_KEY] = {
        "journal_id": new_id(),
        "sequence": None,
        "payload_hash": None,
        "captured_editor_hash": committed_hash,
        "intent": "new" if selected == _NEW_DRAFT else "existing",
        "existing_draft_id": None if selected == _NEW_DRAFT else selected,
        "reserved_draft_id": new_id() if selected == _NEW_DRAFT else None,
        "base_updated_at": (
            None if record is None else str(_record_value(record, "updated_at", ""))
        ),
        "status": "clean" if selected == _NEW_DRAFT else "saved",
        "committed_hash": committed_hash,
        "last_change": None,
        "durable": False,
        "error": "",
    }


def has_dirty_editor() -> bool:
    """Public hook used by the top-level navigation guard."""

    context = _autosave_context()
    if context is None:
        return False
    current_hash = snapshot_hash(_editor_snapshot())
    return needs_dirty_guard(
        status=_context_status(context),
        current_hash=current_hash,
        committed_hash=(str(context["committed_hash"]) if context.get("committed_hash") else None),
    )


def recovery_is_durable() -> bool:
    context = _autosave_context()
    return bool(context and context.get("durable"))


def capture_before_navigation() -> bool:
    """Flush the current widget values and report whether navigation needs a guard."""

    _capture_editor_changes()
    return has_dirty_editor()


def _on_draft_selection_change() -> None:
    """Capture the old editor, then fail closed before binding another draft."""

    _capture_editor_changes()
    requested = str(st.session_state.get(SELECTED_STATE_KEY, _NEW_DRAFT))
    bound = str(st.session_state.get(BOUND_STATE_KEY, _NEW_DRAFT))
    decision = resolve_guard_request(
        bound=bound,
        requested=requested,
        guard_required=has_dirty_editor(),
    )
    st.session_state[SELECTED_STATE_KEY] = decision.visible_value
    if decision.pending_target is not None:
        st.session_state[SWITCH_REQUEST_STATE_KEY] = decision.pending_target
    else:
        st.session_state.pop(SWITCH_REQUEST_STATE_KEY, None)


def _request_new_draft() -> None:
    _capture_editor_changes()
    if has_dirty_editor():
        st.session_state[SWITCH_REQUEST_STATE_KEY] = _NEW_DRAFT
        return
    st.session_state[PENDING_SELECTION_STATE_KEY] = _NEW_DRAFT
    st.session_state[BOUND_STATE_KEY] = None


def _approve_draft_switch(target: str) -> None:
    st.session_state[PENDING_SELECTION_STATE_KEY] = target
    st.session_state[BOUND_STATE_KEY] = None
    st.session_state.pop(SWITCH_REQUEST_STATE_KEY, None)
    st.session_state.pop(AUTOSAVE_CONTEXT_STATE_KEY, None)
    st.session_state.pop(EDITOR_SNAPSHOT_STATE_KEY, None)


def _approve_navigation(target: str) -> None:
    st.session_state[NAV_GUARD_BYPASS_STATE_KEY] = target
    st.session_state["pending_nav"] = target
    st.session_state.pop(NAV_GUARD_TARGET_STATE_KEY, None)
    st.session_state.pop(AUTOSAVE_CONTEXT_STATE_KEY, None)
    st.session_state.pop(EDITOR_SNAPSHOT_STATE_KEY, None)
    st.session_state[BOUND_STATE_KEY] = None


def _cancel_guard_request() -> None:
    switch_target = st.session_state.pop(SWITCH_REQUEST_STATE_KEY, None)
    if switch_target is not None:
        bound = str(st.session_state.get(BOUND_STATE_KEY, _NEW_DRAFT))
        st.session_state[PENDING_SELECTION_STATE_KEY] = bound
    st.session_state.pop(NAV_GUARD_TARGET_STATE_KEY, None)
    st.session_state.pop(PENDING_HANDOFF_STATE_KEY, None)


def clear_superseded_guard_requests() -> None:
    """Forget an older guard intent after a newer clean navigation succeeds."""

    st.session_state.pop(SWITCH_REQUEST_STATE_KEY, None)
    st.session_state.pop(NAV_GUARD_TARGET_STATE_KEY, None)
    st.session_state.pop(PENDING_HANDOFF_STATE_KEY, None)


def _autosave_service(services: Any | None = None) -> Any | None:
    resolved = services or get_services()
    return getattr(resolved, "prompt_scratch_autosave", None)


def _state_value(value: object) -> str:
    raw = getattr(value, "value", value)
    return str(raw).casefold()


def _capture_editor_changes(
    *,
    services: Any | None = None,
    _remaining_retries: int = 1,
) -> bool:
    """Synchronously journal the latest author fields before any debounce."""

    context = _autosave_context()
    if context is None:
        return False
    snapshot = _editor_snapshot()
    current_hash = snapshot_hash(snapshot)
    st.session_state[EDITOR_SNAPSHOT_STATE_KEY] = snapshot

    if current_hash == context.get("captured_editor_hash"):
        return bool(context.get("durable"))
    if not should_capture(snapshot, journal_exists=context.get("sequence") is not None):
        # A brand-new, wholly blank editor does not need a database row. UI-only
        # choices on that empty sheet may be safely treated as the new baseline.
        context.update(
            status="clean",
            committed_hash=current_hash,
            captured_editor_hash=current_hash,
            error="",
        )
        st.session_state[AUTOSAVE_CONTEXT_STATE_KEY] = context
        return False

    service = _autosave_service(services)
    if service is None:
        context.update(
            status="failed",
            durable=False,
            error="自動保存服務尚未就緒；請先複製目前文字。",
        )
        st.session_state[AUTOSAVE_CONTEXT_STATE_KEY] = context
        return False

    try:
        journal = service.capture_recovery(
            journal_id=str(context["journal_id"]),
            expected_sequence=context.get("sequence"),
            intent=str(context["intent"]),
            existing_draft_id=context.get("existing_draft_id"),
            reserved_draft_id=context.get("reserved_draft_id"),
            base_updated_at=context.get("base_updated_at"),
            project_id=snapshot.get("project_id"),
            link_project=bool(snapshot.get("link_project")),
            editor_kind=str(snapshot.get("kind") or "character"),
            gender=str(snapshot.get("gender") or "female"),
            title=str(snapshot.get("title") or ""),
            character_name=str(snapshot.get("character_name") or ""),
            character_image_prompt_en=str(snapshot.get("character_image_prompt_en") or ""),
            background_image_prompt_en=str(snapshot.get("background_image_prompt_en") or ""),
            notes=str(snapshot.get("notes") or ""),
        )
    except ConflictError as exc:
        context.update(
            status="conflict",
            error=str(exc),
            # The prior journal may still be durable, but this exact visible
            # snapshot was not accepted. Never enable "safe to leave" for it.
            durable=False,
        )
        st.session_state[AUTOSAVE_CONTEXT_STATE_KEY] = context
        return False
    except (ApplicationError, RuntimeError, TypeError, ValueError) as exc:
        context.update(status="failed", durable=False, error=str(exc))
        st.session_state[AUTOSAVE_CONTEXT_STATE_KEY] = context
        return False

    journal_state = _state_value(_record_value(journal, "state", "pending"))
    journal_hash = str(_record_value(journal, "payload_sha256", ""))
    journal_sequence = int(_record_value(journal, "sequence", 0))
    live_snapshot = _editor_snapshot()
    live_hash = snapshot_hash(live_snapshot)
    st.session_state[EDITOR_SNAPSHOT_STATE_KEY] = live_snapshot
    capture = reconcile_capture_result(
        journal_state=journal_state,
        journal_hash=journal_hash,
        current_hash=live_hash,
    )

    if journal_state == PromptScratchRecoveryState.COMMITTED.value:
        saved_id = str(_record_value(journal, "committed_draft_id", ""))
        saved_updated_at = str(_record_value(journal, "committed_updated_at", ""))
        if not saved_id or not saved_updated_at:
            context.update(
                status="failed",
                durable=False,
                error="保存回條缺少正式草稿識別；目前畫面尚未再次寫入磁碟。",
            )
            st.session_state[AUTOSAVE_CONTEXT_STATE_KEY] = context
            return False
        st.session_state[AUTOSAVE_CONTEXT_STATE_KEY] = _context_from_saved_target(
            draft_id=saved_id,
            updated_at=saved_updated_at,
            committed_hash=journal_hash,
        )
        if capture.retry_current and _remaining_retries > 0:
            return _capture_editor_changes(
                services=services,
                _remaining_retries=_remaining_retries - 1,
            )
        if capture.retry_current:
            rotated = _autosave_context()
            assert rotated is not None
            rotated.update(
                status="failed",
                durable=False,
                error="較新的畫面內容尚未完成復原保存；請再編輯一次或立即保存。",
            )
            st.session_state[AUTOSAVE_CONTEXT_STATE_KEY] = rotated
            return False
        st.session_state[PENDING_SELECTION_STATE_KEY] = saved_id
        st.session_state[BOUND_STATE_KEY] = None
        return True

    if journal_state == PromptScratchRecoveryState.DISCARDED.value:
        context.update(
            sequence=journal_sequence,
            payload_hash=journal_hash,
            status="failed",
            durable=False,
            error="這份復原內容已放棄，不能再次保存。",
        )
        st.session_state[AUTOSAVE_CONTEXT_STATE_KEY] = context
        return False

    context.update(
        journal_id=str(_record_value(journal, "id", context["journal_id"])),
        sequence=journal_sequence,
        payload_hash=journal_hash,
        captured_editor_hash=journal_hash,
        status=capture.status,
        last_change=time.monotonic(),
        durable=capture.durable_current,
        error=("" if capture.durable_current else "畫面內容比收到的復原回應更新，正在重新保存。"),
    )
    st.session_state[AUTOSAVE_CONTEXT_STATE_KEY] = context
    if capture.retry_current and _remaining_retries > 0:
        return _capture_editor_changes(
            services=services,
            _remaining_retries=_remaining_retries - 1,
        )
    if capture.retry_current:
        context.update(
            status="failed",
            durable=False,
            error="最新畫面內容尚未完成復原保存；請再編輯一次或立即保存。",
        )
        st.session_state[AUTOSAVE_CONTEXT_STATE_KEY] = context
        return False
    return bool(context["durable"])


def _on_editor_change() -> None:
    _capture_editor_changes()


def _context_from_saved_target(
    *,
    draft_id: str,
    updated_at: str,
    committed_hash: str,
) -> dict[str, Any]:
    return {
        "journal_id": new_id(),
        "sequence": None,
        "payload_hash": None,
        "captured_editor_hash": committed_hash,
        "intent": "existing",
        "existing_draft_id": draft_id,
        "reserved_draft_id": None,
        "base_updated_at": updated_at,
        "status": "saved",
        "committed_hash": committed_hash,
        "last_change": None,
        "durable": True,
        "error": "",
    }


def _reconcile_terminal_journal(
    journal: Any,
    *,
    expected_editor_hash: str,
    manual: bool,
) -> bool:
    """Handle a terminal receipt found after a lost response or callback race."""

    state = _state_value(_record_value(journal, "state", ""))
    context = _autosave_context()
    if context is None:
        return False
    if state == PromptScratchRecoveryState.COMMITTED.value:
        saved_id = str(_record_value(journal, "committed_draft_id", ""))
        saved_updated_at = str(_record_value(journal, "committed_updated_at", ""))
        current_hash = snapshot_hash(_editor_snapshot())
        reconciliation = reconcile_commit_result(
            outcome="already_saved",
            current_hash=current_hash,
            committed_hash=expected_editor_hash,
        )
        if reconciliation.rotate_journal and saved_id and saved_updated_at:
            st.session_state[AUTOSAVE_CONTEXT_STATE_KEY] = _context_from_saved_target(
                draft_id=saved_id,
                updated_at=saved_updated_at,
                committed_hash=expected_editor_hash,
            )
            _capture_editor_changes()
            return False
        if not saved_id or not saved_updated_at:
            context.update(
                status="failed",
                durable=False,
                error="保存回條缺少正式草稿識別。",
            )
            st.session_state[AUTOSAVE_CONTEXT_STATE_KEY] = context
            return False
        st.session_state[AUTOSAVE_CONTEXT_STATE_KEY] = _context_from_saved_target(
            draft_id=saved_id,
            updated_at=saved_updated_at,
            committed_hash=current_hash,
        )
        st.session_state[PENDING_SELECTION_STATE_KEY] = saved_id
        st.session_state[BOUND_STATE_KEY] = None
        if manual:
            st.session_state[FLASH_STATE_KEY] = "圖片提示詞草稿已保存。"
        return True
    if state == PromptScratchRecoveryState.CONFLICT.value:
        journal_hash = str(
            _record_value(journal, "payload_sha256", context.get("payload_hash") or "")
        )
        current_hash = snapshot_hash(_editor_snapshot())
        durable = bool(journal_hash) and journal_hash == current_hash
        context.update(
            status="conflict",
            sequence=int(_record_value(journal, "sequence", context.get("sequence") or 0)),
            payload_hash=journal_hash,
            captured_editor_hash=journal_hash,
            durable=durable,
            error="" if durable else "正在把較新的畫面內容加入衝突復原資料。",
        )
        st.session_state[AUTOSAVE_CONTEXT_STATE_KEY] = context
        if not durable:
            _capture_editor_changes()
        return False
    if state == PromptScratchRecoveryState.DISCARDED.value:
        context.update(
            status="failed",
            durable=False,
            error="這份復原內容已放棄，不能再次保存。",
        )
        st.session_state[AUTOSAVE_CONTEXT_STATE_KEY] = context
        return False
    return False


def _commit_editor(
    *,
    services: Any | None = None,
    save_as_new: bool = False,
    manual: bool = False,
) -> bool:
    """Commit one exact journal sequence; return whether a full refresh is due."""

    _capture_editor_changes(services=services)
    context = _autosave_context()
    if context is None or context.get("sequence") is None:
        return False
    if _context_status(context) == "conflict" and not save_as_new:
        return False
    if _context_status(context) == "failed" and not context.get("durable"):
        return False

    service = _autosave_service(services)
    if service is None:
        context.update(
            status="failed",
            durable=False,
            error="自動保存服務尚未就緒；請先複製目前文字。",
        )
        st.session_state[AUTOSAVE_CONTEXT_STATE_KEY] = context
        return False

    expected_sequence = int(context["sequence"])
    expected_hash = str(context.get("payload_hash") or "")
    expected_editor_hash = str(
        context.get("captured_editor_hash") or snapshot_hash(_editor_snapshot())
    )
    if not expected_hash:
        context.update(status="failed", error="復原資料不完整，無法驗證。")
        st.session_state[AUTOSAVE_CONTEXT_STATE_KEY] = context
        return False

    context["status"] = "saving"
    st.session_state[AUTOSAVE_CONTEXT_STATE_KEY] = context
    try:
        if save_as_new:
            result = service.save_recovery_as_new(
                str(context["journal_id"]),
                expected_sequence=expected_sequence,
                expected_hash=expected_hash,
            )
        else:
            result = service.commit_recovery(
                str(context["journal_id"]),
                expected_sequence=expected_sequence,
                expected_hash=expected_hash,
            )
    except ConflictError as exc:
        # A newer capture or a terminal receipt may have won between the timer
        # decision and this call. Read, classify, and never blindly rebase.
        try:
            latest = service.get_recovery(str(context["journal_id"]))
        except (ApplicationError, RuntimeError, TypeError, ValueError):
            context.update(status="conflict", durable=False, error=str(exc))
            st.session_state[AUTOSAVE_CONTEXT_STATE_KEY] = context
            return False
        latest_state = _state_value(_record_value(latest, "state", ""))
        if latest_state == PromptScratchRecoveryState.PENDING.value:
            context.update(
                status="dirty",
                sequence=int(_record_value(latest, "sequence", expected_sequence)),
                payload_hash=str(_record_value(latest, "payload_sha256", expected_hash)),
                durable=True,
                error="",
            )
            st.session_state[AUTOSAVE_CONTEXT_STATE_KEY] = context
            return False
        return _reconcile_terminal_journal(
            latest,
            expected_editor_hash=expected_editor_hash,
            manual=manual,
        )
    except (ApplicationError, RuntimeError, TypeError, ValueError) as exc:
        context.update(status="failed", durable=True, error=str(exc))
        st.session_state[AUTOSAVE_CONTEXT_STATE_KEY] = context
        return False

    result_status = _state_value(_record_value(result, "status", "conflict"))
    journal = _record_value(result, "journal", None)
    if result_status == PromptScratchCommitStatus.STALE.value:
        if journal is not None:
            context.update(
                sequence=int(_record_value(journal, "sequence", expected_sequence)),
                payload_hash=str(_record_value(journal, "payload_sha256", expected_hash)),
            )
            st.session_state[AUTOSAVE_CONTEXT_STATE_KEY] = context
            if _state_value(_record_value(journal, "state", "")) != (
                PromptScratchRecoveryState.PENDING.value
            ):
                return _reconcile_terminal_journal(
                    journal,
                    expected_editor_hash=expected_editor_hash,
                    manual=manual,
                )
        journal_hash = str(
            _record_value(journal, "payload_sha256", "") if journal is not None else ""
        )
        current_hash = snapshot_hash(_editor_snapshot())
        durable = bool(journal_hash) and journal_hash == current_hash
        context.update(
            status="dirty" if durable else "failed",
            captured_editor_hash=journal_hash,
            durable=durable,
            error=("" if durable else "較新的畫面內容尚未完成復原保存；請再編輯一次或立即保存。"),
        )
        st.session_state[AUTOSAVE_CONTEXT_STATE_KEY] = context
        if not durable:
            _capture_editor_changes(services=services)
        return False
    if result_status == PromptScratchCommitStatus.CONFLICT.value:
        if journal is not None:
            context.update(
                sequence=int(_record_value(journal, "sequence", expected_sequence + 1)),
                payload_hash=str(_record_value(journal, "payload_sha256", expected_hash)),
            )
        journal_hash = str(
            _record_value(journal, "payload_sha256", "") if journal is not None else ""
        )
        current_hash = snapshot_hash(_editor_snapshot())
        durable = bool(journal_hash) and journal_hash == current_hash
        context.update(
            status="conflict",
            captured_editor_hash=journal_hash,
            durable=durable,
            error="" if durable else "正在把較新的畫面內容加入衝突復原資料。",
        )
        st.session_state[AUTOSAVE_CONTEXT_STATE_KEY] = context
        if not durable:
            _capture_editor_changes(services=services)
        return False

    if result_status not in {
        PromptScratchCommitStatus.SAVED.value,
        PromptScratchCommitStatus.ALREADY_SAVED.value,
    }:
        context.update(
            status="failed",
            durable=True,
            error=f"保存服務回傳未知狀態：{result_status}",
        )
        st.session_state[AUTOSAVE_CONTEXT_STATE_KEY] = context
        return False

    saved_id = str(_record_value(result, "saved_draft_id", ""))
    saved_updated_at = str(_record_value(result, "saved_updated_at", ""))
    current_hash = snapshot_hash(_editor_snapshot())
    reconciliation = reconcile_commit_result(
        outcome=(
            "already_saved"
            if result_status == PromptScratchCommitStatus.ALREADY_SAVED.value
            else "saved"
        ),
        current_hash=current_hash,
        committed_hash=expected_editor_hash,
    )
    if reconciliation.rotate_journal and saved_id and saved_updated_at:
        st.session_state[AUTOSAVE_CONTEXT_STATE_KEY] = _context_from_saved_target(
            draft_id=saved_id,
            updated_at=saved_updated_at,
            committed_hash=expected_editor_hash,
        )
        _capture_editor_changes(services=services)
        return False

    if not saved_id or not saved_updated_at:
        context.update(
            status="failed",
            durable=False,
            error="保存回條缺少正式草稿識別。",
        )
        st.session_state[AUTOSAVE_CONTEXT_STATE_KEY] = context
        return False
    st.session_state[AUTOSAVE_CONTEXT_STATE_KEY] = _context_from_saved_target(
        draft_id=saved_id,
        updated_at=saved_updated_at,
        committed_hash=current_hash,
    )
    st.session_state[PENDING_SELECTION_STATE_KEY] = saved_id
    st.session_state[BOUND_STATE_KEY] = None
    if manual:
        st.session_state[FLASH_STATE_KEY] = "圖片提示詞草稿已保存。"
    return reconciliation.refresh_app


def _render_autosave_status() -> None:
    context = _autosave_context()
    if context is None:
        return
    status = _context_status(context)
    if status == "clean" and context.get("intent") == "new":
        return
    view = status_view(
        status,
        recovery_is_durable=bool(context.get("durable")),
        detail=str(context.get("error") or ""),
    )
    renderer = getattr(st, view.renderer)
    renderer(view.text)


@st.fragment(run_every=0.5)
def _render_autosave_fragment() -> None:
    """Commit the latest exact recovery after 1.5 idle seconds."""

    context = _autosave_context()
    if context is None:
        return
    refresh = False
    if autosave_due(
        status=_context_status(context),
        last_change=context.get("last_change"),
        now=time.monotonic(),
        debounce_seconds=AUTOSAVE_DEBOUNCE_SECONDS,
    ):
        with st.spinner("正在保存……"):
            refresh = _commit_editor()
    _render_autosave_status()
    if refresh:
        # The selector/list live outside this fragment. One full refresh is
        # required after a terminal save, then the timer is no longer mounted.
        st.rerun()


def _render_save_state() -> None:
    context = _autosave_context()
    if context is not None and _context_status(context) == "dirty":
        _render_autosave_fragment()
    else:
        _render_autosave_status()


def _execute_guard_target(kind: str, target: str) -> None:
    if kind == "draft":
        _approve_draft_switch(target)
    else:
        handoff = st.session_state.pop(PENDING_HANDOFF_STATE_KEY, None)
        if isinstance(handoff, Mapping):
            target = _stage_character_handoff(handoff)
        _approve_navigation(target)


def _render_dirty_guard() -> None:
    draft_target = st.session_state.get(SWITCH_REQUEST_STATE_KEY)
    nav_target = st.session_state.get(NAV_GUARD_TARGET_STATE_KEY)
    if draft_target is None and nav_target is None:
        return
    kind = "draft" if draft_target is not None else "nav"
    target = str(draft_target if draft_target is not None else nav_target)
    destination = "新草稿" if target == _NEW_DRAFT else target
    st.warning(f"目前內容尚未安全完成保存。要前往「{destination}」嗎？")
    save, keep, stay = st.columns(3)
    if save.button(
        "先保存，再切換",
        type="primary",
        use_container_width=True,
        key=f"{STATE_PREFIX}_guard_save",
    ):
        _commit_editor(manual=True)
        context = _autosave_context()
        # The debounce fragment can finish while this guard is visible.  In
        # that case the manual retry is intentionally idempotent and returns
        # no refresh request, but the durable saved receipt still authorizes
        # the pending switch.
        if context is not None and _context_status(context) == "saved":
            _execute_guard_target(kind, target)
            st.rerun()
    if keep.button(
        "保留復原內容並切換",
        use_container_width=True,
        key=f"{STATE_PREFIX}_guard_keep",
        disabled=not recovery_is_durable(),
        help="只有最新內容已寫入本機復原資料時才能安全離開。",
    ):
        _execute_guard_target(kind, target)
        st.rerun()
    if stay.button(
        "繼續編輯",
        use_container_width=True,
        key=f"{STATE_PREFIX}_guard_stay",
    ):
        _cancel_guard_request()
        st.rerun()


def _discard_current_recovery(services: Any) -> bool:
    context = _autosave_context()
    service = _autosave_service(services)
    if context is None or service is None or context.get("sequence") is None:
        return False
    try:
        journal = service.discard_recovery(
            str(context["journal_id"]),
            expected_sequence=int(context["sequence"]),
            confirm=True,
        )
    except (ApplicationError, RuntimeError, TypeError, ValueError) as exc:
        context.update(status="failed", error=str(exc))
        st.session_state[AUTOSAVE_CONTEXT_STATE_KEY] = context
        return False
    if _state_value(_record_value(journal, "state", "")) != "discarded":
        return False
    selected = str(st.session_state.get(SELECTED_STATE_KEY, _NEW_DRAFT))
    st.session_state[PENDING_SELECTION_STATE_KEY] = selected
    st.session_state[BOUND_STATE_KEY] = None
    st.session_state.pop(AUTOSAVE_CONTEXT_STATE_KEY, None)
    st.session_state.pop(EDITOR_SNAPSHOT_STATE_KEY, None)
    st.session_state.pop(RECOVERY_DISCARD_CONFIRM_STATE_KEY, None)
    st.session_state[FLASH_STATE_KEY] = "這份未完成的復原內容已放棄；正式草稿沒有被覆寫。"
    return True


def _render_conflict_actions(services: Any) -> None:
    context = _autosave_context()
    if context is None or _context_status(context) != "conflict":
        return
    st.caption("你可以把目前內容另存成新草稿，或明確放棄這份復原內容。")
    save_new, discard = st.columns(2)
    if save_new.button(
        "另存成新草稿",
        type="primary",
        use_container_width=True,
        key=f"{STATE_PREFIX}_conflict_save_new",
    ) and _commit_editor(services=services, save_as_new=True, manual=True):
        st.rerun()
    journal_id = str(context.get("journal_id") or "")
    armed = st.session_state.get(RECOVERY_DISCARD_CONFIRM_STATE_KEY) == journal_id
    if not armed:
        if discard.button(
            "放棄這份復原內容",
            use_container_width=True,
            key=f"{STATE_PREFIX}_conflict_discard_start",
        ):
            st.session_state[RECOVERY_DISCARD_CONFIRM_STATE_KEY] = journal_id
            st.rerun()
    else:
        discard.warning("這會清除復原文字，但不會刪除正式草稿。")
        confirm, cancel = discard.columns(2)
        if confirm.button(
            "確認放棄",
            type="primary",
            key=f"{STATE_PREFIX}_conflict_discard_confirm",
        ) and _discard_current_recovery(services):
            st.rerun()
        if cancel.button(
            "取消",
            key=f"{STATE_PREFIX}_conflict_discard_cancel",
        ):
            st.session_state.pop(RECOVERY_DISCARD_CONFIRM_STATE_KEY, None)
            st.rerun()


def _apply_pending_state() -> bool:
    form_changed = False
    apply_openai_session_state_transitions(st.session_state)
    if st.session_state.pop(CONSENT_RESET_PENDING_STATE_KEY, False):
        st.session_state.pop(OPENAI_CONSENT_STATE_KEY, None)
    pending = st.session_state.pop(PENDING_FORM_STATE_KEY, None)
    if isinstance(pending, Mapping):
        for field, value in pending.items():
            key = _FORM_KEYS.get(str(field))
            if key:
                st.session_state[key] = value
                form_changed = True
    selected = st.session_state.pop(PENDING_SELECTION_STATE_KEY, None)
    if selected is not None:
        st.session_state[SELECTED_STATE_KEY] = str(selected)
    return form_changed


def _display_name(record: Any) -> str:
    title = str(_record_value(record, "title", "")).strip()
    character_name = str(_record_value(record, "character_name", "")).strip()
    prompt = str(
        _record_value(record, "character_image_prompt_en", "")
        or _record_value(record, "background_image_prompt_en", "")
    ).strip()
    label = title or character_name or prompt[:34] or "未命名提示詞"
    draft_id = str(_record_value(record, "id", ""))
    return f"{label} · {draft_id[-6:]}" if draft_id else label


def _project_link(services: Any) -> str | None:
    try:
        projects = [
            project
            for project in services.projects.list_projects(include_archived=False)
            if str(getattr(getattr(project, "status", ""), "value", "active")) == "active"
        ]
    except ApplicationError as exc:
        st.warning(f"作品清單暫時讀不到：{exc}")
        return None

    link = st.checkbox(
        "先把這張草稿歸到某本作品",
        key=_FORM_KEYS["link_project"],
        on_change=_on_editor_change,
        disabled=not projects,
        help="不勾也能保存。之後再選作品即可，不會偷偷建立或綁定。",
    )
    if not projects:
        st.caption("目前沒有可連結的作品；這不影響草稿保存與匯出。")
        return None
    if not link:
        return None
    by_id = {str(project.id): project for project in projects}
    selected_before = st.session_state.get(_FORM_KEYS["project_id"])
    preferred = str(st.session_state.get("selected_project_id", ""))
    if selected_before not in by_id:
        st.session_state[_FORM_KEYS["project_id"]] = (
            preferred if preferred in by_id else next(iter(by_id))
        )
    return st.selectbox(
        "要歸到哪一本？",
        tuple(by_id),
        format_func=lambda project_id: str(by_id[project_id].name),
        key=_FORM_KEYS["project_id"],
        on_change=_on_editor_change,
    )


def _form_payload(project_id: str | None) -> tuple[dict[str, Any], str]:
    character_raw = str(st.session_state.get(_FORM_KEYS["character_image_prompt_en"], ""))
    background_raw = str(st.session_state.get(_FORM_KEYS["background_image_prompt_en"], ""))
    try:
        character_prompt = normalize_english_image_prompt(character_raw)
        background_prompt = normalize_english_image_prompt(background_raw)
        error = ""
    except ValueError as exc:
        character_prompt = character_raw.strip()
        background_prompt = background_raw.strip()
        error = str(exc)
    return (
        {
            "project_id": project_id,
            "title": str(st.session_state.get(_FORM_KEYS["title"], "")).strip(),
            "character_name": str(st.session_state.get(_FORM_KEYS["character_name"], "")).strip(),
            "character_image_prompt_en": character_prompt,
            "background_image_prompt_en": background_prompt,
            "notes": str(st.session_state.get(_FORM_KEYS["notes"], "")).strip(),
        },
        error,
    )


def _has_content(payload: Mapping[str, Any]) -> bool:
    return any(
        str(payload.get(field, "")).strip()
        for field in (
            "title",
            "character_name",
            "character_image_prompt_en",
            "background_image_prompt_en",
            "notes",
        )
    )


def _safe_filename(payload: Mapping[str, Any]) -> str:
    raw = str(payload.get("title") or payload.get("character_name") or "圖片提示詞草稿").strip()
    safe = re.sub(r"[<>:\"/\\|?*\x00-\x1f]", "_", raw).strip(" .")
    return safe[:80] or "圖片提示詞草稿"


def _export_txt(payload: Mapping[str, Any]) -> str:
    sections: list[str] = []
    labels = (
        ("title", "草稿標題"),
        ("character_name", "角色名稱"),
        ("character_image_prompt_en", "角色圖片提示詞（英文）"),
        ("background_image_prompt_en", "背景圖片提示詞（英文）"),
        ("notes", "靈感備註"),
    )
    for field, label in labels:
        value = str(payload.get(field, "")).strip()
        if value:
            sections.append(f"{label}\n{value}")
    return "\n\n".join(sections).rstrip() + "\n"


def _export_json(payload: Mapping[str, Any], draft_id: str | None) -> str:
    return json.dumps(
        {
            "schema_version": "prompt-scratch-draft-v1",
            "id": draft_id,
            **payload,
        },
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
    )


def _provider_controls(
    services: Any,
) -> tuple[str, str, bool, SecretStr | None]:
    source = st.radio(
        "怎麼生成？",
        options=("offline", "local", "openai"),
        format_func=lambda value: {
            "offline": "離線隨機（不需 AI）",
            "local": "本機 Ollama",
            "openai": "OpenAI",
        }[value],
        horizontal=True,
        key=f"{STATE_PREFIX}_generation_source",
    )
    if source == "offline":
        st.caption("使用內建素材與常見中文視覺詞對照，不需要 API Key 或網路。")
        return source, "", True, None

    injected = st.session_state.get(PROVIDER_STATE_KEY)
    if source == "local":
        if not is_loopback_ollama_endpoint(services.settings.ollama_base_url):
            st.error("此自由草稿入口只會把內容送到本機 Ollama；目前端點不是本機位址。")
            return source, "", False, None
        selection = render_ollama_model_selector(
            services.settings,
            key_prefix=STATE_PREFIX,
            allow_unverified=injected is not None,
        )
        st.caption("內容只會送到這台電腦上的 Ollama。")
        return source, selection.model, selection.ready, None

    shared = render_openai_session_summary(key_prefix=STATE_PREFIX)
    try:
        runtime = shared.runtime_config()
        model = runtime.model
    except ValueError:
        runtime = None
        model = ""
        st.error("AI 設定中的自訂模型 ID 尚未填完整。")
    has_key = shared.configured or injected is not None
    if not has_key:
        st.info("請先到 AI 設定放入 API Key，或設定系統環境變數 OPENAI_API_KEY。")
    st.warning("只有按下生成後，上方線索與目前 Prompt 才會送到 OpenAI。")
    consent = st.checkbox(
        "只同意這一次傳送上述內容到 OpenAI",
        key=OPENAI_CONSENT_STATE_KEY,
        disabled=not has_key,
    )
    return (
        source,
        model,
        bool(model) and has_key and consent,
        runtime.api_key_for_provider() if runtime is not None else None,
    )


def _render_generation(services: Any, kind: PromptScratchKind) -> None:
    with st.expander("✦ 從一句線索生成或調整 Prompt", expanded=True):
        clue = st.text_area(
            "你想看見什麼？（選填）",
            placeholder="例如：可愛的女孩子，孤單，雙馬尾，粉紅色頭髮",
            height=90,
            key=f"{STATE_PREFIX}_clue",
        )
        revision = st.text_area(
            "這次想怎麼改？（選填）",
            placeholder="例如：保留髮型，把服裝改成雨夜車站的制服。",
            height=75,
            key=f"{STATE_PREFIX}_revision",
        )
        with st.expander("上下文讀取長度"):
            limits = st.columns(3)
            clue_limit = limits[0].slider(
                "線索",
                min_value=200,
                max_value=8000,
                value=2000,
                step=200,
                key=f"{STATE_PREFIX}_clue_limit",
            )
            character_limit = limits[1].slider(
                "既有角色 Prompt",
                min_value=100,
                max_value=4000,
                value=1200,
                step=100,
                key=f"{STATE_PREFIX}_character_limit",
            )
            background_limit = limits[2].slider(
                "既有背景 Prompt",
                min_value=100,
                max_value=4000,
                value=1200,
                step=100,
                key=f"{STATE_PREFIX}_background_limit",
            )

        source, model, ready, transient_api_key = _provider_controls(services)
        merge_mode = st.radio(
            "生成結果怎麼放進草稿？",
            options=("rewrite", "fill"),
            format_func=lambda value: (
                "依照線索重寫需要的 Prompt" if value == "rewrite" else "只補目前空白的 Prompt"
            ),
            horizontal=True,
            key=f"{STATE_PREFIX}_merge_mode",
        )
        if st.button(
            "✦ 生成圖片提示詞",
            type="primary",
            use_container_width=True,
            key=f"{STATE_PREFIX}_generate",
            disabled=not ready,
        ):
            gender = CharacterGender(
                st.session_state.get(_FORM_KEYS["gender"], CharacterGender.FEMALE)
            )
            current_character = str(
                st.session_state.get(_FORM_KEYS["character_image_prompt_en"], "")
            )
            current_background = str(
                st.session_state.get(_FORM_KEYS["background_image_prompt_en"], "")
            )
            brief = PromptScratchBrief(
                kind=kind,
                selected_gender=gender,
                clues=clue[:clue_limit],
                revision_instruction=revision[:4000],
                existing_character_prompt_en=current_character[:character_limit],
                existing_background_prompt_en=current_background[:background_limit],
            )
            mode = {
                "offline": PromptScratchGenerationMode.OFFLINE,
                "local": PromptScratchGenerationMode.OLLAMA,
                "openai": PromptScratchGenerationMode.OPENAI,
            }[source]
            provider = st.session_state.get(PROVIDER_STATE_KEY)
            owns_provider = False
            generation: PromptScratchGenerationService | None = None
            if source == "openai":
                st.session_state[CONSENT_RESET_PENDING_STATE_KEY] = True
            try:
                if mode is not PromptScratchGenerationMode.OFFLINE and provider is None:
                    provider = build_creative_provider(
                        services.settings,
                        provider_name="openai" if source == "openai" else "ollama",
                        api_key=transient_api_key,
                    )
                    owns_provider = True
                generation = PromptScratchGenerationService(
                    provider,
                    owns_provider=owns_provider,
                )
                with st.spinner("正在整理成英文逗號提示詞……"):
                    result = generation.generate(brief, mode=mode, model=model)
            except (ApplicationError, ProviderError, RuntimeError, TypeError, ValueError) as exc:
                diagnostic = diagnose_provider_error(
                    exc,
                    provider_label="OpenAI" if source == "openai" else "本機 Ollama",
                )
                st.session_state[ERROR_STATE_KEY] = (
                    f"{diagnostic.message_zh_tw} 原本的 Prompt 完全沒變。"
                )
            else:
                pending: dict[str, Any] = {}
                generated = result.draft
                if kind.includes_character and (
                    merge_mode == "rewrite" or not current_character.strip()
                ):
                    pending["character_image_prompt_en"] = generated.character_image_prompt_en
                if kind.includes_background and (
                    merge_mode == "rewrite" or not current_background.strip()
                ):
                    pending["background_image_prompt_en"] = generated.background_image_prompt_en
                st.session_state[PENDING_FORM_STATE_KEY] = pending
                source_label = {
                    "offline": "離線素材",
                    "local": f"本機 Ollama · {result.model_used or model}",
                    "openai": f"OpenAI · {result.model_used or model}",
                }[source]
                st.session_state[FLASH_STATE_KEY] = (
                    f"{source_label} 已生成；內容尚未保存，每個英文關鍵字都能直接修改。"
                )
            finally:
                if generation is not None:
                    generation.close()
                if source == "openai":
                    consume_openai_key_after_action(st.session_state)
            transient_api_key = None
            st.rerun()


def _stage_character_handoff(payload: Mapping[str, Any]) -> str:
    from imaginarium_forge.ui.pages import character_drafts

    explicit_gender = payload.get("gender")
    try:
        gender = CharacterGender(_state_value(explicit_gender))
    except ValueError:
        gender = CharacterGender(
            legacy_gender_value(str(payload.get("character_image_prompt_en", "")))
        )
    st.session_state[character_drafts.PENDING_SELECTION_STATE_KEY] = "__new__"
    st.session_state[character_drafts.BOUND_DRAFT_STATE_KEY] = "__new__"
    st.session_state[character_drafts.PENDING_FORM_STATE_KEY] = {
        "title": str(payload.get("title", "")),
        "character_name": str(payload.get("character_name", "")),
        "gender": gender,
        "character_image_prompt_en": str(payload.get("character_image_prompt_en", "")),
        "background_image_prompt_en": str(payload.get("background_image_prompt_en", "")),
        "notes": str(payload.get("notes", "")),
    }
    return character_drafts.PAGE_KEY


def _handoff_to_character(payload: Mapping[str, Any]) -> None:
    from imaginarium_forge.ui.pages import character_drafts

    handoff = {
        **payload,
        "gender": st.session_state.get(_FORM_KEYS["gender"], CharacterGender.FEMALE),
    }
    _capture_editor_changes()
    if has_dirty_editor():
        st.session_state[PENDING_HANDOFF_STATE_KEY] = handoff
        st.session_state[NAV_GUARD_TARGET_STATE_KEY] = character_drafts.PAGE_KEY
        return
    target = _stage_character_handoff(handoff)
    st.session_state["pending_nav"] = target
    st.rerun()


def render_global_recovery_banner(*, services: Any | None = None) -> None:
    """Show actionable recovery on any page without auto-restoring text."""

    service = _autosave_service(services)
    if service is None:
        return
    try:
        page = service.list_actionable(limit=20)
    except (ApplicationError, RuntimeError, TypeError, ValueError):
        st.warning("未完成內容清單暫時無法讀取；沒有復原內容被刪除。")
        return
    dismissed = {str(value) for value in st.session_state.get(RECOVERY_DISMISSED_STATE_KEY, ())}
    current_context = _autosave_context()
    current_journal_id = str((current_context or {}).get("journal_id") or "")
    items = [
        item
        for item in page.items
        if str(item.id) not in dismissed and str(item.id) != current_journal_id
    ]
    if not items:
        return
    journal = items[0]
    journal_id = str(journal.id)
    label = "未完成的圖片提示詞內容"
    if page.total > 1:
        label = f"{page.total} 份未完成的圖片提示詞內容"
    st.warning(f"找到{label}。內容不會自動覆寫目前畫面。")
    restore, later, discard = st.columns(3)
    if restore.button(
        "恢復上次內容",
        type="primary",
        use_container_width=True,
        key=f"{STATE_PREFIX}_recovery_restore_global",
        disabled=has_dirty_editor(),
    ):
        st.session_state[RECOVERY_REQUEST_STATE_KEY] = journal_id
        st.session_state[NAV_GUARD_BYPASS_STATE_KEY] = PAGE_KEY
        st.session_state["pending_nav"] = PAGE_KEY
        st.rerun()
    if later.button(
        "稍後處理",
        use_container_width=True,
        key=f"{STATE_PREFIX}_recovery_later_global",
    ):
        st.session_state[RECOVERY_DISMISSED_STATE_KEY] = (*dismissed, journal_id)
        st.rerun()
    armed = st.session_state.get(RECOVERY_DISCARD_CONFIRM_STATE_KEY) == journal_id
    if not armed:
        if discard.button(
            "放棄這份復原內容",
            use_container_width=True,
            key=f"{STATE_PREFIX}_recovery_discard_global",
        ):
            st.session_state[RECOVERY_DISCARD_CONFIRM_STATE_KEY] = journal_id
            st.rerun()
    else:
        discard.warning("復原文字將被清除，正式草稿不會被刪除。")
        yes, no = discard.columns(2)
        if yes.button(
            "確認放棄",
            type="primary",
            key=f"{STATE_PREFIX}_recovery_discard_global_confirm",
        ):
            try:
                service.discard_recovery(
                    journal_id,
                    expected_sequence=int(journal.sequence),
                    confirm=True,
                )
            except (ApplicationError, RuntimeError, TypeError, ValueError) as exc:
                st.error(f"無法放棄復原內容：{exc}")
                return
            st.session_state[FLASH_STATE_KEY] = "未完成的復原內容已放棄。"
            st.session_state.pop(RECOVERY_DISCARD_CONFIRM_STATE_KEY, None)
            st.rerun()
        if no.button(
            "取消",
            key=f"{STATE_PREFIX}_recovery_discard_global_cancel",
        ):
            st.session_state.pop(RECOVERY_DISCARD_CONFIRM_STATE_KEY, None)
            st.rerun()
    if page.has_more:
        st.caption("還有其他復原內容；進入圖片提示詞草稿後可逐一處理。")


def _consume_recovery_request(
    service: Any,
    by_id: Mapping[str, Any],
) -> None:
    journal_id = st.session_state.pop(RECOVERY_REQUEST_STATE_KEY, None)
    if not journal_id:
        return
    try:
        journal = service.get_recovery(str(journal_id))
    except (ApplicationError, RuntimeError, TypeError, ValueError) as exc:
        st.session_state[ERROR_STATE_KEY] = f"復原內容讀取失敗：{exc}"
        return
    state = _state_value(journal.state)
    if (
        state
        not in {
            PromptScratchRecoveryState.PENDING.value,
            PromptScratchRecoveryState.CONFLICT.value,
        }
        or journal.payload is None
    ):
        st.session_state[ERROR_STATE_KEY] = "這份復原內容已處理，不能再次載入。"
        return

    raw = journal.payload.model_dump(mode="json")
    snapshot = serialize_editor_snapshot(raw)
    form_values = {
        **snapshot,
        "kind": PromptScratchKind(str(snapshot["kind"])),
        "gender": CharacterGender(str(snapshot["gender"])),
    }
    target = str(journal.existing_draft_id or _NEW_DRAFT)
    target_record = by_id.get(target)
    base_is_stale = bool(target_record is None and journal.existing_draft_id) or bool(
        target_record is not None
        and str(_record_value(target_record, "updated_at", ""))
        != str(journal.base_updated_at or "")
    )
    selected = target if target in by_id else _NEW_DRAFT
    _write_form(form_values)
    st.session_state[SELECTED_STATE_KEY] = selected
    st.session_state[BOUND_STATE_KEY] = selected
    st.session_state[EDITOR_SNAPSHOT_STATE_KEY] = snapshot
    baseline = (
        serialize_editor_snapshot(_form_from_record(target_record))
        if target_record is not None
        else serialize_editor_snapshot(_blank_form())
    )
    st.session_state[AUTOSAVE_CONTEXT_STATE_KEY] = {
        "journal_id": str(journal.id),
        "sequence": int(journal.sequence),
        "payload_hash": str(journal.payload_sha256),
        "captured_editor_hash": snapshot_hash(snapshot),
        "intent": _state_value(journal.intent),
        "existing_draft_id": journal.existing_draft_id,
        "reserved_draft_id": journal.reserved_draft_id,
        "base_updated_at": journal.base_updated_at,
        "status": (
            "conflict"
            if state == PromptScratchRecoveryState.CONFLICT.value or base_is_stale
            else "dirty"
        ),
        "committed_hash": snapshot_hash(baseline),
        "last_change": time.monotonic(),
        "durable": True,
        "error": "",
    }
    dismissed = {str(value) for value in st.session_state.get(RECOVERY_DISMISSED_STATE_KEY, ())}
    dismissed.discard(str(journal.id))
    st.session_state[RECOVERY_DISMISSED_STATE_KEY] = tuple(sorted(dismissed))
    st.session_state[FLASH_STATE_KEY] = "已載入上次未完成的內容；尚未覆寫正式草稿。"


def render(*, services: Any | None = None) -> None:
    """Render a durable prompt scratchpad that never requires a Project."""

    pending_form_changed = _apply_pending_state()
    services = services or get_services()
    prompt_drafts = getattr(services, "prompt_scratch_drafts", None)
    autosave = _autosave_service(services)

    page_header(
        PAGE_LABEL,
        "只拿角色圖 Prompt、只拿背景圖 Prompt，或兩組一起做；不用先建立作品，也不用填完整角色。",
        eyebrow="先做眼前需要的那一張圖，書與故事之後再決定",
        badges=(("免建專案", "teal"), ("英文逗號格式", "amber")),
    )
    # Reserve the visual position now, but render the provider controls only
    # after the selected draft and its exact editor kind are bound below.
    generation_slot = st.container()
    st.info("這裡是自由草稿。保存不會建立作品；只有你勾選作品時才會連結。")
    flash = st.session_state.pop(FLASH_STATE_KEY, None)
    if isinstance(flash, str) and flash:
        st.success(flash)
    error = st.session_state.pop(ERROR_STATE_KEY, None)
    if isinstance(error, str) and error:
        st.error(error)

    if prompt_drafts is None:
        st.error("圖片提示詞草稿服務尚未就緒；目前仍可編輯與匯出，但不能保存。")
        records: list[Any] = []
    else:
        try:
            records = prompt_drafts.list_drafts(include_archived=False)
        except (ApplicationError, RuntimeError, TypeError, ValueError) as exc:
            st.error(f"草稿清單暫時讀不到：{exc}")
            records = []

    by_id = {str(_record_value(record, "id")): record for record in records}
    if autosave is not None:
        _consume_recovery_request(autosave, by_id)
    options = (_NEW_DRAFT, *by_id)
    if st.session_state.get(SELECTED_STATE_KEY) not in options:
        st.session_state[SELECTED_STATE_KEY] = _NEW_DRAFT
    selector, new_column = st.columns((4, 1))
    with selector:
        selected = st.selectbox(
            "打開哪張 Prompt 草稿？",
            options,
            format_func=lambda value: (
                "＋ 新的圖片提示詞" if value == _NEW_DRAFT else _display_name(by_id[value])
            ),
            key=SELECTED_STATE_KEY,
            on_change=_on_draft_selection_change,
        )
    with new_column:
        st.write("")
        if st.button(
            "新草稿",
            key=f"{STATE_PREFIX}_new",
            use_container_width=True,
            disabled=selected == _NEW_DRAFT,
        ):
            _request_new_draft()
            st.rerun()

    if selected != st.session_state.get(BOUND_STATE_KEY):
        selected_record = None if selected == _NEW_DRAFT else by_id[selected]
        _write_form(
            _blank_form() if selected_record is None else _form_from_record(selected_record)
        )
        _bind_autosave_editor(selected, selected_record)
        st.session_state[BOUND_STATE_KEY] = selected
        st.session_state[ARCHIVE_CONFIRM_STATE_KEY] = None
    if pending_form_changed:
        _capture_editor_changes(services=services)

    kind = st.radio(
        "今天要做哪一種？",
        tuple(PromptScratchKind),
        format_func=lambda value: _KIND_LABELS[value],
        horizontal=True,
        key=_FORM_KEYS["kind"],
        on_change=_on_editor_change,
    )
    identity = st.columns(2)
    identity[0].text_input(
        "草稿標題（選填）",
        placeholder="例如：粉紅雙馬尾角色圖",
        key=_FORM_KEYS["title"],
        on_change=_on_editor_change,
    )
    identity[1].text_input(
        "角色名稱（選填）",
        placeholder="可以現在命名，也可以完全不填",
        key=_FORM_KEYS["character_name"],
        on_change=_on_editor_change,
        disabled=kind is PromptScratchKind.BACKGROUND,
    )
    if kind.includes_character:
        st.radio(
            "角色性別",
            tuple(CharacterGender),
            format_func=lambda value: value.zh_label,
            horizontal=True,
            key=_FORM_KEYS["gender"],
            on_change=_on_editor_change,
        )

    if kind.includes_character:
        st.text_area(
            "角色圖片 Prompt（英文，逗號分隔）",
            placeholder="pink hair, twin tails, gentle expression, ...",
            height=130,
            key=_FORM_KEYS["character_image_prompt_en"],
            on_change=_on_editor_change,
        )
    else:
        st.caption("這張草稿目前只做背景圖；既有角色 Prompt 仍會保留，不會被刪除。")
    if kind.includes_background:
        st.text_area(
            "背景圖片 Prompt（英文，逗號分隔）",
            placeholder="rainy night, quiet train platform, cinematic lighting, no people, ...",
            height=130,
            key=_FORM_KEYS["background_image_prompt_en"],
            on_change=_on_editor_change,
        )
    else:
        st.caption("這張草稿目前只做角色圖；既有背景 Prompt 仍會保留，不會被刪除。")
    st.text_area(
        "靈感備註（選填）",
        placeholder="中文想法、負面提示、之後想改的地方都可以先放這裡。",
        height=95,
        key=_FORM_KEYS["notes"],
        on_change=_on_editor_change,
    )
    # Keep the provider controls visually at the top while mounting every
    # editor widget first.  Generation may rerun immediately, so this ordering
    # is required for Streamlit to retain authored prompts in fill mode.
    with generation_slot:
        _render_generation(services, kind)
    project_id = _project_link(services)
    _capture_editor_changes(services=services)
    payload, prompt_error = _form_payload(project_id)
    if prompt_error:
        st.warning(f"Prompt 必須保持英文並使用逗號分隔：{prompt_error}")

    _render_save_state()
    _render_conflict_actions(services)

    save_column, archive_column = st.columns((3, 2))
    if save_column.button(
        "立即保存",
        type="primary",
        use_container_width=True,
        key=f"{STATE_PREFIX}_save",
        disabled=prompt_drafts is None or bool(prompt_error),
    ):
        if not _has_content(payload):
            st.error("至少留下一組 Prompt、標題、角色名或備註，就能保存。")
        else:
            if autosave is not None:
                if _commit_editor(services=services, manual=True):
                    st.rerun()
            else:
                assert prompt_drafts is not None
                try:
                    saved = (
                        prompt_drafts.create_draft(**payload)
                        if selected == _NEW_DRAFT
                        else prompt_drafts.update_draft(selected, **payload)
                    )
                    saved_id = str(_record_value(saved, "id"))
                    st.session_state[PENDING_SELECTION_STATE_KEY] = saved_id
                    st.session_state[BOUND_STATE_KEY] = None
                    st.session_state[FLASH_STATE_KEY] = "圖片提示詞草稿已保存。"
                    st.rerun()
                except (ApplicationError, RuntimeError, TypeError, ValueError) as exc:
                    st.error(f"這次沒有保存成功：{exc}")

    if selected != _NEW_DRAFT and prompt_drafts is not None:
        if st.session_state.get(ARCHIVE_CONFIRM_STATE_KEY) != selected:
            if archive_column.button(
                "收起這張草稿",
                use_container_width=True,
                key=f"{STATE_PREFIX}_archive_start",
                disabled=has_dirty_editor(),
                help="請先保存或明確放棄目前變更，再收起草稿。",
            ):
                st.session_state[ARCHIVE_CONFIRM_STATE_KEY] = selected
                st.rerun()
        else:
            archive_column.warning("收起不會刪除內容，但會離開目前清單。")
            confirm, cancel = archive_column.columns(2)
            if confirm.button(
                "確定收起",
                type="primary",
                use_container_width=True,
                key=f"{STATE_PREFIX}_archive_confirm",
                disabled=has_dirty_editor(),
                help="請先保存或明確放棄目前變更，再收起草稿。",
            ):
                try:
                    prompt_drafts.archive_draft(selected)
                    st.session_state[PENDING_SELECTION_STATE_KEY] = _NEW_DRAFT
                    st.session_state[BOUND_STATE_KEY] = None
                    st.session_state[ARCHIVE_CONFIRM_STATE_KEY] = None
                    st.session_state[FLASH_STATE_KEY] = "Prompt 草稿已收起，內容沒有刪除。"
                    st.rerun()
                except (ApplicationError, RuntimeError, TypeError, ValueError) as exc:
                    st.error(f"這次沒有收起成功：{exc}")
            if cancel.button(
                "先不要",
                use_container_width=True,
                key=f"{STATE_PREFIX}_archive_cancel",
            ):
                st.session_state[ARCHIVE_CONFIRM_STATE_KEY] = None
                st.rerun()

    if str(payload.get("character_image_prompt_en", "")).strip():
        st.markdown("#### 想把它繼續長成一個人？")
        st.caption("把目前的名稱與兩組 Prompt 帶到角色草稿，再補個性、背景故事與人物關係。")
        if st.button(
            "發展成完整角色與個人故事",
            use_container_width=True,
            key=f"{STATE_PREFIX}_to_character",
        ):
            _handoff_to_character(payload)

    _render_dirty_guard()

    st.markdown("#### 帶走目前內容")
    filename = _safe_filename(payload)
    exports = st.columns(2)
    exports[0].download_button(
        "下載 TXT",
        data=_export_txt(payload),
        file_name=f"{filename}.txt",
        mime="text/plain; charset=utf-8",
        use_container_width=True,
        key=f"{STATE_PREFIX}_download_txt",
        disabled=bool(prompt_error) or not _has_content(payload),
    )
    exports[1].download_button(
        "下載 JSON",
        data=_export_json(payload, None if selected == _NEW_DRAFT else selected),
        file_name=f"{filename}.json",
        mime="application/json",
        use_container_width=True,
        key=f"{STATE_PREFIX}_download_json",
        disabled=bool(prompt_error) or not _has_content(payload),
    )


__all__ = [
    "AUTOSAVE_CONTEXT_STATE_KEY",
    "BOUND_STATE_KEY",
    "NAV_GUARD_BYPASS_STATE_KEY",
    "NAV_GUARD_TARGET_STATE_KEY",
    "OPENAI_API_KEY_STATE_KEY",
    "OPENAI_CONSENT_STATE_KEY",
    "PAGE_KEY",
    "PAGE_LABEL",
    "PENDING_FORM_STATE_KEY",
    "PROVIDER_STATE_KEY",
    "SELECTED_STATE_KEY",
    "capture_before_navigation",
    "has_dirty_editor",
    "render",
    "render_global_recovery_banner",
]
