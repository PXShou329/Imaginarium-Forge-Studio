"""Durable, author-reviewed story memory contracts.

Story memory is deliberately separate from Canon.  A scene draft may propose
facts, timeline events, foreshadowing threads, and dynamic character state,
but none of those claims becomes active until the author accepts the exact
proposal bound to the exact accepted draft.

Entries form a small append-only event log.  Replacing or retracting an active
value requires an explicit ``supersedes_entry_id``; a second value for the
same subject/attribute can therefore never overwrite history silently.
"""

from __future__ import annotations

import hashlib
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from imaginarium_forge.canonical import canonical_json

MEMORY_SCHEMA_VERSION = "story-memory-v1"
MEMORY_PROPOSAL_CONTRACT_VERSION = "story-memory-proposal-v1"

MAX_MEMORY_CHANGES = 64
_Identifier = Annotated[str, StringConstraints(min_length=1, max_length=200)]
_Value = Annotated[str, StringConstraints(max_length=4000)]
_Excerpt = Annotated[str, StringConstraints(max_length=1000)]


class StoryMemoryKind(StrEnum):
    FACT = "fact"
    TIMELINE = "timeline"
    FORESHADOWING = "foreshadowing"
    CHARACTER_STATE = "character_state"


class StoryMemoryOperation(StrEnum):
    ASSERT = "assert"
    SUPERSEDE = "supersede"
    RETRACT = "retract"


class StoryMemoryProposalOrigin(StrEnum):
    MANUAL = "manual"
    SCENE_CARD = "scene_card"
    LLM = "llm"


class StoryMemoryProposalStatus(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class StoryMemoryChange(BaseModel):
    """One proposed memory mutation, not yet authoritative."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: StoryMemoryKind
    subject_id: _Identifier
    attribute: _Identifier
    value: _Value = ""
    operation: StoryMemoryOperation = StoryMemoryOperation.ASSERT
    supersedes_entry_id: str | None = None
    source_excerpt: _Excerpt = ""

    @field_validator("subject_id", "attribute")
    @classmethod
    def _strip_identifier(cls, value: str) -> str:
        return " ".join(value.strip().split())

    @field_validator("value", "source_excerpt")
    @classmethod
    def _strip_text(cls, value: str) -> str:
        return value.strip()

    @model_validator(mode="after")
    def _explicit_replacement_contract(self) -> StoryMemoryChange:
        target = (self.supersedes_entry_id or "").strip()
        if self.operation is StoryMemoryOperation.ASSERT and target:
            raise ValueError("assert 不可指定 supersedes_entry_id")
        if self.operation is not StoryMemoryOperation.ASSERT and not target:
            raise ValueError("supersede/retract 必須明確指定 supersedes_entry_id")
        if self.operation is not StoryMemoryOperation.RETRACT and not self.value:
            raise ValueError("assert/supersede 的 value 不可為空")
        if self.operation is StoryMemoryOperation.RETRACT and self.value:
            raise ValueError("retract 的 value 必須為空")
        return self

    @property
    def identity_key(self) -> tuple[str, str]:
        """The collision boundary required by the authoring contract."""

        return (self.subject_id.casefold(), self.attribute.casefold())


class StoryMemoryProposalPayload(BaseModel):
    """Immutable proposal body shown to the author before acceptance."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["story-memory-v1"] = "story-memory-v1"
    changes: tuple[StoryMemoryChange, ...] = Field(
        default=(), max_length=MAX_MEMORY_CHANGES
    )

    @model_validator(mode="after")
    def _one_change_per_identity(self) -> StoryMemoryProposalPayload:
        keys = [change.identity_key for change in self.changes]
        if len(keys) != len(set(keys)):
            raise ValueError("同一提案不可重複修改相同的 subject/attribute")
        return self

    @property
    def canonical_payload(self) -> str:
        return canonical_json(self.model_dump(mode="json"))

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.canonical_payload.encode("utf-8")).hexdigest()


class StoryMemoryEntry(BaseModel):
    """One persisted append-only memory operation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    project_id: str
    story_outline_id: str
    story_scene_id: str
    scene_draft_id: str
    proposal_id: str
    kind: StoryMemoryKind
    subject_id: str
    attribute: str
    value: str = ""
    operation: StoryMemoryOperation
    supersedes_entry_id: str | None = None
    source_excerpt: str = ""
    created_at: str

    @property
    def identity_key(self) -> tuple[str, str]:
        return (self.subject_id.casefold(), self.attribute.casefold())

    def canonical(self) -> dict[str, str | None]:
        return {
            "id": self.id,
            "project_id": self.project_id,
            "story_outline_id": self.story_outline_id,
            "story_scene_id": self.story_scene_id,
            "scene_draft_id": self.scene_draft_id,
            "proposal_id": self.proposal_id,
            "kind": self.kind.value,
            "subject_id": self.subject_id,
            "attribute": self.attribute,
            "value": self.value,
            "operation": self.operation.value,
            "supersedes_entry_id": self.supersedes_entry_id,
            "source_excerpt": self.source_excerpt,
            "created_at": self.created_at,
        }


class StoryMemoryProjection(BaseModel):
    """Active story state immediately before a target scene."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: str = MEMORY_SCHEMA_VERSION
    project_id: str
    story_outline_id: str
    entries: tuple[StoryMemoryEntry, ...] = ()
    applied_proposal_ids: tuple[str, ...] = ()
    gap_scene_ids: tuple[str, ...] = ()
    stale_proposal_ids: tuple[str, ...] = ()

    @property
    def fingerprint(self) -> str:
        payload = canonical_json(
            {
                "schema_version": self.schema_version,
                "project_id": self.project_id,
                "story_outline_id": self.story_outline_id,
                "entries": [entry.canonical() for entry in self.entries],
                "applied_proposal_ids": list(self.applied_proposal_ids),
                "gap_scene_ids": list(self.gap_scene_ids),
                "stale_proposal_ids": list(self.stale_proposal_ids),
            }
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @property
    def by_identity(self) -> dict[tuple[str, str], StoryMemoryEntry]:
        return {entry.identity_key: entry for entry in self.entries}


class StoryConsistencySeverity(StrEnum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class StoryConsistencyCode(StrEnum):
    SILENT_OVERWRITE = "silent_overwrite"
    REPLACEMENT_TARGET_MISSING = "replacement_target_missing"
    REPLACEMENT_IDENTITY_MISMATCH = "replacement_identity_mismatch"
    REPLACEMENT_KIND_MISMATCH = "replacement_kind_mismatch"
    NO_OP = "no_op"


class StoryConsistencyFinding(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    code: StoryConsistencyCode
    severity: StoryConsistencySeverity
    message_zh_tw: str
    subject_id: str
    attribute: str
    existing_entry_id: str | None = None


class StoryConsistencyReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    findings: tuple[StoryConsistencyFinding, ...] = ()

    @property
    def has_errors(self) -> bool:
        return any(
            finding.severity is StoryConsistencySeverity.ERROR
            for finding in self.findings
        )


def detect_memory_conflicts(
    projection: StoryMemoryProjection,
    proposal: StoryMemoryProposalPayload,
) -> StoryConsistencyReport:
    """Pure fail-closed check; it never repairs or overwrites a value."""

    active = projection.by_identity
    active_by_id = {entry.id: entry for entry in projection.entries}
    findings: list[StoryConsistencyFinding] = []
    for change in proposal.changes:
        current = active.get(change.identity_key)
        if change.operation is StoryMemoryOperation.ASSERT:
            if current is not None and current.value != change.value:
                findings.append(
                    StoryConsistencyFinding(
                        code=StoryConsistencyCode.SILENT_OVERWRITE,
                        severity=StoryConsistencySeverity.ERROR,
                        message_zh_tw=(
                            "此 subject/attribute 已有不同的有效值；"
                            "請明確改用 supersede 並指定既有 entry。"
                        ),
                        subject_id=change.subject_id,
                        attribute=change.attribute,
                        existing_entry_id=current.id,
                    )
                )
            elif current is not None:
                findings.append(
                    StoryConsistencyFinding(
                        code=StoryConsistencyCode.NO_OP,
                        severity=StoryConsistencySeverity.INFO,
                        message_zh_tw="此記憶與目前有效值相同，不會重複建立。",
                        subject_id=change.subject_id,
                        attribute=change.attribute,
                        existing_entry_id=current.id,
                    )
                )
            continue

        target = active_by_id.get(change.supersedes_entry_id or "")
        if target is None:
            findings.append(
                StoryConsistencyFinding(
                    code=StoryConsistencyCode.REPLACEMENT_TARGET_MISSING,
                    severity=StoryConsistencySeverity.ERROR,
                    message_zh_tw="指定要取代／撤回的記憶已不是目前有效項目。",
                    subject_id=change.subject_id,
                    attribute=change.attribute,
                    existing_entry_id=change.supersedes_entry_id,
                )
            )
            continue
        if target.identity_key != change.identity_key:
            findings.append(
                StoryConsistencyFinding(
                    code=StoryConsistencyCode.REPLACEMENT_IDENTITY_MISMATCH,
                    severity=StoryConsistencySeverity.ERROR,
                    message_zh_tw="取代目標的 subject/attribute 與提案不一致。",
                    subject_id=change.subject_id,
                    attribute=change.attribute,
                    existing_entry_id=target.id,
                )
            )
        if target.kind is not change.kind:
            findings.append(
                StoryConsistencyFinding(
                    code=StoryConsistencyCode.REPLACEMENT_KIND_MISMATCH,
                    severity=StoryConsistencySeverity.ERROR,
                    message_zh_tw="取代目標的記憶種類與提案不一致。",
                    subject_id=change.subject_id,
                    attribute=change.attribute,
                    existing_entry_id=target.id,
                )
            )
    return StoryConsistencyReport(findings=tuple(findings))


def project_memory_entries(
    *,
    project_id: str,
    story_outline_id: str,
    entries: tuple[StoryMemoryEntry, ...],
    applied_proposal_ids: tuple[str, ...] = (),
    gap_scene_ids: tuple[str, ...] = (),
    stale_proposal_ids: tuple[str, ...] = (),
) -> StoryMemoryProjection:
    """Replay append-only entries into the currently active projection."""

    active: dict[tuple[str, str], StoryMemoryEntry] = {}
    by_id: dict[str, StoryMemoryEntry] = {}
    for entry in entries:
        if entry.operation is StoryMemoryOperation.ASSERT:
            existing = active.get(entry.identity_key)
            if existing is not None and existing.value != entry.value:
                raise ValueError("story memory log contains an implicit overwrite")
            active.setdefault(entry.identity_key, entry)
            by_id[entry.id] = entry
            continue

        target = by_id.get(entry.supersedes_entry_id or "")
        if target is None or active.get(target.identity_key) is not target:
            raise ValueError("story memory log replaces an inactive or missing entry")
        if target.identity_key != entry.identity_key or target.kind is not entry.kind:
            raise ValueError("story memory replacement identity does not match")
        active.pop(target.identity_key)
        by_id[entry.id] = entry
        if entry.operation is StoryMemoryOperation.SUPERSEDE:
            active[entry.identity_key] = entry

    ordered = tuple(
        sorted(
            active.values(),
            key=lambda item: (item.kind.value, *item.identity_key),
        )
    )
    return StoryMemoryProjection(
        project_id=project_id,
        story_outline_id=story_outline_id,
        entries=ordered,
        applied_proposal_ids=applied_proposal_ids,
        gap_scene_ids=gap_scene_ids,
        stale_proposal_ids=stale_proposal_ids,
    )


__all__ = [
    "MEMORY_PROPOSAL_CONTRACT_VERSION",
    "MEMORY_SCHEMA_VERSION",
    "StoryConsistencyCode",
    "StoryConsistencyFinding",
    "StoryConsistencyReport",
    "StoryConsistencySeverity",
    "StoryMemoryChange",
    "StoryMemoryEntry",
    "StoryMemoryKind",
    "StoryMemoryOperation",
    "StoryMemoryProjection",
    "StoryMemoryProposalOrigin",
    "StoryMemoryProposalPayload",
    "StoryMemoryProposalStatus",
    "detect_memory_conflicts",
    "project_memory_entries",
]
