"""Durable, allow-listed recovery state for the Prompt Scratch editor."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, model_validator

from imaginarium_forge.canonical import canonical_json, sha256_of_canonical
from imaginarium_forge.domain.character.gender import CharacterGender
from imaginarium_forge.domain.prompt.scratch_draft import (
    PromptScratchDraft,
    PromptScratchEditorKind,
)

MAX_RECOVERY_PAYLOAD_UTF8_BYTES = 1_048_576


class PromptScratchRecoveryIntent(StrEnum):
    NEW = "new"
    EXISTING = "existing"


class PromptScratchRecoveryState(StrEnum):
    PENDING = "pending"
    CONFLICT = "conflict"
    COMMITTED = "committed"
    DISCARDED = "discarded"


class PromptScratchResolutionKind(StrEnum):
    TARGET = "target"
    SAVE_AS_NEW = "save_as_new"


class PromptScratchConflictReason(StrEnum):
    STALE_REVISION = "stale_revision"
    TARGET_MISSING = "target_missing"
    TARGET_ARCHIVED = "target_archived"
    TARGET_ID_TAKEN = "target_id_taken"


class PromptScratchCommitStatus(StrEnum):
    SAVED = "saved"
    ALREADY_SAVED = "already_saved"
    CONFLICT = "conflict"
    STALE = "stale"


class PromptScratchRecoveryPayload(BaseModel):
    """Exact editor snapshot; provider/session fields are impossible to supply."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: PromptScratchEditorKind = PromptScratchEditorKind.CHARACTER
    gender: CharacterGender = CharacterGender.FEMALE
    title: str = ""
    character_name: str = ""
    character_image_prompt_en: str = ""
    background_image_prompt_en: str = ""
    notes: str = ""
    link_project: bool = False
    project_id: str | None = None

    def canonical_snapshot(self) -> dict[str, object]:
        """Return the UI-neutral canonical payload in its normative key set."""

        return self.model_dump(mode="json")

    def canonical_json(self) -> str:
        return canonical_json(self.canonical_snapshot())

    def sha256(self) -> str:
        return sha256_of_canonical(self.canonical_snapshot())

    @model_validator(mode="after")
    def _bounded_utf8_payload(self) -> PromptScratchRecoveryPayload:
        if len(self.canonical_json().encode("utf-8")) > MAX_RECOVERY_PAYLOAD_UTF8_BYTES:
            raise ValueError("Prompt Scratch 復原資料超過 1 MiB 上限；內容未被截斷")
        return self


class PromptScratchRecoveryJournal(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    intent: PromptScratchRecoveryIntent
    existing_draft_id: str | None = None
    reserved_draft_id: str | None = None
    base_updated_at: str | None = None
    payload: PromptScratchRecoveryPayload | None = None
    receipt_kind: PromptScratchEditorKind
    receipt_gender: CharacterGender
    payload_sha256: str
    sequence: int
    state: PromptScratchRecoveryState
    conflict_reason: PromptScratchConflictReason | None = None
    resolution_kind: PromptScratchResolutionKind | None = None
    committed_draft_id: str | None = None
    committed_updated_at: str | None = None
    resolved_from_sequence: int | None = None
    created_at: str
    updated_at: str
    resolved_at: str | None = None

    @property
    def target_draft_id(self) -> str:
        return self.existing_draft_id or self.reserved_draft_id or ""

    @model_validator(mode="after")
    def _state_contract(self) -> PromptScratchRecoveryJournal:
        if self.sequence < 1:
            raise ValueError("recovery sequence must be positive")
        if self.intent is PromptScratchRecoveryIntent.NEW:
            if not self.reserved_draft_id or self.existing_draft_id or self.base_updated_at:
                raise ValueError("new recovery target contract is invalid")
        elif (
            not self.existing_draft_id
            or self.reserved_draft_id is not None
            or self.base_updated_at is None
        ):
            raise ValueError("existing recovery target contract is invalid")
        if self.state in {
            PromptScratchRecoveryState.PENDING,
            PromptScratchRecoveryState.CONFLICT,
        }:
            if self.payload is None:
                raise ValueError("actionable recovery must retain its payload")
            if self.payload.sha256() != self.payload_sha256:
                raise ValueError("recovery payload integrity check failed")
        elif self.payload is not None:
            raise ValueError("terminal recovery receipts must clear author text")
        if self.state is PromptScratchRecoveryState.PENDING:
            if any(
                value is not None
                for value in (
                    self.conflict_reason,
                    self.resolution_kind,
                    self.committed_draft_id,
                    self.committed_updated_at,
                    self.resolved_from_sequence,
                    self.resolved_at,
                )
            ):
                raise ValueError("pending recovery contains terminal fields")
        else:
            if (
                self.resolved_from_sequence is None
                or self.sequence != self.resolved_from_sequence + 1
                or self.resolved_at is None
            ):
                raise ValueError("terminal recovery receipt is incomplete")
        if self.state is PromptScratchRecoveryState.CONFLICT:
            if self.conflict_reason is None or self.committed_draft_id is not None:
                raise ValueError("conflict recovery receipt is invalid")
        elif self.conflict_reason is not None:
            raise ValueError("non-conflict recovery cannot have a conflict reason")
        if self.state is PromptScratchRecoveryState.COMMITTED:
            if (
                self.resolution_kind is None
                or not self.committed_draft_id
                or not self.committed_updated_at
            ):
                raise ValueError("committed recovery receipt is incomplete")
            if (
                self.resolution_kind is PromptScratchResolutionKind.TARGET
                and self.committed_draft_id != self.target_draft_id
            ):
                raise ValueError("target receipt must name its bound draft")
            if (
                self.resolution_kind is PromptScratchResolutionKind.SAVE_AS_NEW
                and self.committed_draft_id == self.target_draft_id
            ):
                raise ValueError("save-as-new receipt must name a different draft")
        elif (
            self.resolution_kind is not None
            or self.committed_draft_id is not None
            or self.committed_updated_at is not None
        ):
            raise ValueError("non-committed recovery cannot name a saved draft")
        return self


class PromptScratchCommitResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    status: PromptScratchCommitStatus
    journal: PromptScratchRecoveryJournal
    saved_draft_id: str | None = None
    saved_updated_at: str | None = None
    reason: PromptScratchConflictReason | None = None
    draft: PromptScratchDraft | None = None


class PromptScratchActionablePage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    items: tuple[PromptScratchRecoveryJournal, ...]
    total: int
    has_more: bool


__all__ = [
    "MAX_RECOVERY_PAYLOAD_UTF8_BYTES",
    "PromptScratchActionablePage",
    "PromptScratchCommitResult",
    "PromptScratchCommitStatus",
    "PromptScratchConflictReason",
    "PromptScratchRecoveryIntent",
    "PromptScratchRecoveryJournal",
    "PromptScratchRecoveryPayload",
    "PromptScratchRecoveryState",
    "PromptScratchResolutionKind",
]
