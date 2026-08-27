"""Pure UI state helpers for Prompt Scratch autosave and recovery.

The Streamlit page deliberately keeps these decisions free of Streamlit and
database imports.  Widget callbacks and timed fragments are event sources;
this module decides what is safe to capture, when a commit is due, and how a
commit result must be reconciled with text that may have changed meanwhile.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Final, Literal

EditorStatus = Literal["clean", "dirty", "saving", "saved", "failed", "conflict"]

FORM_FIELDS: Final = (
    "kind",
    "gender",
    "title",
    "character_name",
    "character_image_prompt_en",
    "background_image_prompt_en",
    "notes",
    "link_project",
    "project_id",
)

AUTHOR_CONTENT_FIELDS: Final = (
    "title",
    "character_name",
    "character_image_prompt_en",
    "background_image_prompt_en",
    "notes",
)

_DEFAULTS: Final[dict[str, object]] = {
    "kind": "character",
    "gender": "female",
    "title": "",
    "character_name": "",
    "character_image_prompt_en": "",
    "background_image_prompt_en": "",
    "notes": "",
    "link_project": False,
    "project_id": None,
}


def _json_scalar(value: object) -> str | bool | None:
    if isinstance(value, Enum):
        value = value.value
    if value is None or isinstance(value, bool):
        return value
    return str(value)


def serialize_editor_snapshot(values: Mapping[str, object]) -> dict[str, str | bool | None]:
    """Return only the explicit author-field allowlist as JSON-safe values.

    Prefix scans are intentionally forbidden: the Prompt Scratch session also
    contains provider objects, one-shot consent and shared API-key state.
    """

    snapshot: dict[str, str | bool | None] = {}
    for field in FORM_FIELDS:
        value = values.get(field, _DEFAULTS[field])
        scalar = _json_scalar(value)
        if field == "link_project":
            snapshot[field] = bool(scalar)
        elif field == "project_id":
            snapshot[field] = str(scalar) if scalar else None
        else:
            snapshot[field] = scalar
    return snapshot


def merge_editor_snapshot(
    previous: Mapping[str, object],
    changed: Mapping[str, object],
) -> dict[str, str | bool | None]:
    """Merge mounted-widget changes without dropping conditionally hidden fields."""

    merged = {**serialize_editor_snapshot(previous)}
    for field in FORM_FIELDS:
        if field in changed:
            merged[field] = _json_scalar(changed[field])
    if not bool(merged["link_project"]):
        # Keep the last project selection in the UI shadow, but payload builders
        # must treat it as unlinked.  This allows a reversible checkbox toggle.
        merged["link_project"] = False
    return serialize_editor_snapshot(merged)


def snapshot_hash(snapshot: Mapping[str, object]) -> str:
    """Stable fingerprint for exact recovery and commit reconciliation."""

    encoded = json.dumps(
        serialize_editor_snapshot(snapshot),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def has_author_content(snapshot: Mapping[str, object]) -> bool:
    """Whether an official Prompt Scratch draft can satisfy its domain rule."""

    normalized = serialize_editor_snapshot(snapshot)
    return any(str(normalized.get(field) or "").strip() for field in AUTHOR_CONTENT_FIELDS)


def should_capture(
    snapshot: Mapping[str, object],
    *,
    journal_exists: bool,
) -> bool:
    """Avoid an empty first journal, but never resurrect text after a clear-all."""

    return journal_exists or has_author_content(snapshot)


def legacy_gender_value(character_prompt: str) -> Literal["female", "male"]:
    """Infer only the legacy exact leading token; never match word prefixes."""

    leading_token = character_prompt.casefold().split(",", 1)[0].strip()
    return "male" if leading_token == "adult man" else "female"


def needs_dirty_guard(
    *,
    status: EditorStatus,
    current_hash: str,
    committed_hash: str | None,
) -> bool:
    """Fail closed if the visible editor differs from its committed baseline."""

    if status in {"dirty", "saving", "failed", "conflict"}:
        return True
    return committed_hash is not None and current_hash != committed_hash


def autosave_due(
    *,
    status: EditorStatus,
    last_change: float | None,
    now: float,
    debounce_seconds: float = 1.5,
) -> bool:
    """Return whether the latest durable journal is idle long enough to commit."""

    if status != "dirty" or last_change is None:
        return False
    return now - last_change >= debounce_seconds


@dataclass(frozen=True, slots=True)
class GuardDecision:
    """A selection/navigation request resolved without touching Streamlit state."""

    allow: bool
    visible_value: str
    pending_target: str | None


def resolve_guard_request(
    *,
    bound: str,
    requested: str,
    guard_required: bool,
) -> GuardDecision:
    if requested == bound:
        return GuardDecision(True, bound, None)
    if guard_required:
        return GuardDecision(False, bound, requested)
    return GuardDecision(True, requested, None)


CommitOutcome = Literal[
    "saved",
    "already_saved",
    "stale",
    "conflict",
    "failed",
]


@dataclass(frozen=True, slots=True)
class CommitReconciliation:
    """How the UI should proceed after an exact journal commit attempt."""

    status: EditorStatus
    rotate_journal: bool = False
    capture_latest: bool = False
    refresh_app: bool = False


@dataclass(frozen=True, slots=True)
class CaptureReconciliation:
    """Whether the returned journal durably represents the visible editor."""

    status: EditorStatus
    durable_current: bool
    retry_current: bool
    terminal: bool = False


def reconcile_capture_result(
    *,
    journal_state: str,
    journal_hash: str,
    current_hash: str,
) -> CaptureReconciliation:
    """Reject stale callback responses that only persisted an older snapshot."""

    if journal_state in {"pending", "conflict"}:
        matches = bool(journal_hash) and journal_hash == current_hash
        return CaptureReconciliation(
            status="conflict" if journal_state == "conflict" else "dirty",
            durable_current=matches,
            retry_current=not matches,
        )
    if journal_state == "committed":
        return CaptureReconciliation(
            status="saved",
            durable_current=bool(journal_hash) and journal_hash == current_hash,
            retry_current=bool(journal_hash) and journal_hash != current_hash,
            terminal=True,
        )
    return CaptureReconciliation(
        status="failed",
        durable_current=False,
        retry_current=False,
        terminal=True,
    )


def reconcile_commit_result(
    *,
    outcome: CommitOutcome,
    current_hash: str,
    committed_hash: str,
) -> CommitReconciliation:
    """Preserve input regardless of whether commit N or capture N+1 wins."""

    if outcome in {"saved", "already_saved"}:
        if current_hash != committed_hash:
            return CommitReconciliation(
                status="dirty",
                rotate_journal=True,
                capture_latest=True,
                refresh_app=False,
            )
        return CommitReconciliation(status="saved", refresh_app=True)
    if outcome == "stale":
        # A newer capture won.  Never commit the older payload and never call
        # this a cross-document conflict.
        return CommitReconciliation(status="dirty")
    if outcome == "conflict":
        return CommitReconciliation(status="conflict")
    return CommitReconciliation(status="failed")


@dataclass(frozen=True, slots=True)
class StatusView:
    renderer: Literal["caption", "info", "success", "warning", "error"]
    text: str


def status_view(
    status: EditorStatus,
    *,
    recovery_is_durable: bool,
    detail: str = "",
) -> StatusView:
    """Creator-facing copy that never overstates durability."""

    if status == "dirty":
        if recovery_is_durable:
            return StatusView("info", "尚未保存；復原內容已保留在這台電腦。")
        return StatusView("warning", "尚未保存；復原內容尚未寫入磁碟。")
    if status == "saving":
        return StatusView("info", "正在保存……")
    if status == "saved":
        return StatusView("success", "已保存。")
    if status == "conflict":
        return StatusView(
            "warning",
            "另一個視窗或工作階段已更新這張草稿；目前內容沒有覆寫資料庫版本。",
        )
    if status == "failed":
        safety = (
            "復原內容仍保留在這台電腦。"
            if recovery_is_durable
            else "目前內容仍在畫面中，但尚未安全寫入磁碟。"
        )
        suffix = f" {detail}" if detail else ""
        return StatusView("error", f"保存失敗；{safety}{suffix}")
    return StatusView("caption", "內容尚未變更。")


__all__ = [
    "AUTHOR_CONTENT_FIELDS",
    "FORM_FIELDS",
    "CaptureReconciliation",
    "CommitOutcome",
    "CommitReconciliation",
    "EditorStatus",
    "GuardDecision",
    "StatusView",
    "autosave_due",
    "has_author_content",
    "legacy_gender_value",
    "merge_editor_snapshot",
    "needs_dirty_guard",
    "reconcile_capture_result",
    "reconcile_commit_result",
    "resolve_guard_request",
    "serialize_editor_snapshot",
    "should_capture",
    "snapshot_hash",
    "status_view",
]
