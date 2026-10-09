"""Persistence adapters for author-reviewed story memory.

Repositories deliberately expose proposal and entry history separately.  A
proposal may be finalized, but its payload is immutable; accepted entries are
append-only.  Projection and conflict policy live in the application/domain
layers rather than being hidden in a query.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from imaginarium_forge.domain.story.memory import (
    StoryMemoryEntry,
    StoryMemoryKind,
    StoryMemoryOperation,
    StoryMemoryProposalOrigin,
    StoryMemoryProposalPayload,
    StoryMemoryProposalStatus,
)
from imaginarium_forge.infrastructure.db.models.orm import (
    StoryChapterRow,
    StoryMemoryEntryRow,
    StoryMemoryProposalRow,
    StorySceneRow,
)


@dataclass(frozen=True, slots=True)
class StoryMemoryProposalRecord:
    id: str
    project_id: str
    story_outline_id: str
    story_scene_id: str
    scene_draft_id: str
    base_memory_fingerprint: str
    proposal_json: str
    proposal_sha256: str
    origin: StoryMemoryProposalOrigin
    provider: str
    model: str
    contract_version: str
    status: StoryMemoryProposalStatus
    decision_note: str
    created_at: str
    finalized_at: str

    @property
    def payload(self) -> StoryMemoryProposalPayload:
        return StoryMemoryProposalPayload.model_validate_json(self.proposal_json)


@dataclass(frozen=True, slots=True)
class StoryMemorySceneRecord:
    id: str
    project_id: str
    story_outline_id: str
    chapter_number: int
    scene_number: int
    accepted_draft_id: str | None

    @property
    def position(self) -> tuple[int, int, str]:
        return (self.chapter_number, self.scene_number, self.id)


class StoryMemoryProposalRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, record: StoryMemoryProposalRecord) -> None:
        self._session.add(
            StoryMemoryProposalRow(
                id=record.id,
                project_id=record.project_id,
                story_outline_id=record.story_outline_id,
                story_scene_id=record.story_scene_id,
                scene_draft_id=record.scene_draft_id,
                base_memory_fingerprint=record.base_memory_fingerprint,
                proposal_json=record.proposal_json,
                proposal_sha256=record.proposal_sha256,
                origin=record.origin.value,
                provider=record.provider,
                model=record.model,
                contract_version=record.contract_version,
                status=record.status.value,
                decision_note=record.decision_note,
                created_at=record.created_at,
                finalized_at=record.finalized_at,
            )
        )

    def get(self, proposal_id: str) -> StoryMemoryProposalRecord | None:
        row = self._session.get(StoryMemoryProposalRow, proposal_id)
        return None if row is None else self._map(row)

    def list_for_draft(self, draft_id: str) -> list[StoryMemoryProposalRecord]:
        stmt = (
            select(StoryMemoryProposalRow)
            .where(StoryMemoryProposalRow.scene_draft_id == draft_id)
            .order_by(StoryMemoryProposalRow.created_at, StoryMemoryProposalRow.id)
        )
        return [self._map(row) for row in self._session.scalars(stmt)]

    def accepted_for_draft(
        self, draft_id: str
    ) -> StoryMemoryProposalRecord | None:
        stmt = select(StoryMemoryProposalRow).where(
            StoryMemoryProposalRow.scene_draft_id == draft_id,
            StoryMemoryProposalRow.status == StoryMemoryProposalStatus.ACCEPTED.value,
        )
        row = self._session.scalars(stmt).one_or_none()
        return None if row is None else self._map(row)

    def finalize(
        self,
        proposal_id: str,
        *,
        status: StoryMemoryProposalStatus,
        decision_note: str,
        finalized_at: str,
    ) -> bool:
        row = self._session.get(StoryMemoryProposalRow, proposal_id)
        if row is None:
            return False
        row.status = status.value
        row.decision_note = decision_note
        row.finalized_at = finalized_at
        return True

    @staticmethod
    def _map(row: StoryMemoryProposalRow) -> StoryMemoryProposalRecord:
        return StoryMemoryProposalRecord(
            id=row.id,
            project_id=row.project_id,
            story_outline_id=row.story_outline_id,
            story_scene_id=row.story_scene_id,
            scene_draft_id=row.scene_draft_id,
            base_memory_fingerprint=row.base_memory_fingerprint,
            proposal_json=row.proposal_json,
            proposal_sha256=row.proposal_sha256,
            origin=StoryMemoryProposalOrigin(row.origin),
            provider=row.provider,
            model=row.model,
            contract_version=row.contract_version,
            status=StoryMemoryProposalStatus(row.status),
            decision_note=row.decision_note,
            created_at=row.created_at,
            finalized_at=row.finalized_at,
        )


class StoryMemoryEntryRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, entry: StoryMemoryEntry) -> None:
        self._session.add(
            StoryMemoryEntryRow(
                id=entry.id,
                project_id=entry.project_id,
                story_outline_id=entry.story_outline_id,
                story_scene_id=entry.story_scene_id,
                scene_draft_id=entry.scene_draft_id,
                proposal_id=entry.proposal_id,
                kind=entry.kind.value,
                subject_id=entry.subject_id,
                attribute=entry.attribute,
                value=entry.value,
                operation=entry.operation.value,
                supersedes_entry_id=entry.supersedes_entry_id,
                source_excerpt=entry.source_excerpt,
                created_at=entry.created_at,
            )
        )

    def list_for_proposal(self, proposal_id: str) -> list[StoryMemoryEntry]:
        stmt = (
            select(StoryMemoryEntryRow)
            .where(StoryMemoryEntryRow.proposal_id == proposal_id)
            .order_by(StoryMemoryEntryRow.created_at, StoryMemoryEntryRow.id)
        )
        return [self._map(row) for row in self._session.scalars(stmt)]

    def get(self, entry_id: str) -> StoryMemoryEntry | None:
        row = self._session.get(StoryMemoryEntryRow, entry_id)
        return None if row is None else self._map(row)

    @staticmethod
    def _map(row: StoryMemoryEntryRow) -> StoryMemoryEntry:
        return StoryMemoryEntry(
            id=row.id,
            project_id=row.project_id,
            story_outline_id=row.story_outline_id,
            story_scene_id=row.story_scene_id,
            scene_draft_id=row.scene_draft_id,
            proposal_id=row.proposal_id,
            kind=StoryMemoryKind(row.kind),
            subject_id=row.subject_id,
            attribute=row.attribute,
            value=row.value,
            operation=StoryMemoryOperation(row.operation),
            supersedes_entry_id=row.supersedes_entry_id,
            source_excerpt=row.source_excerpt,
            created_at=row.created_at,
        )


class StoryMemorySceneRepository:
    """Chronological scene order across every chapter of one outline."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def list_for_outline(self, outline_id: str) -> list[StoryMemorySceneRecord]:
        stmt = (
            select(StorySceneRow, StoryChapterRow.chapter_number)
            .join(
                StoryChapterRow,
                StorySceneRow.story_chapter_id == StoryChapterRow.id,
            )
            .where(StoryChapterRow.story_outline_id == outline_id)
            .order_by(
                StoryChapterRow.chapter_number,
                StorySceneRow.scene_number,
                StorySceneRow.id,
            )
        )
        return [
            StoryMemorySceneRecord(
                id=scene.id,
                project_id=scene.project_id,
                story_outline_id=outline_id,
                chapter_number=chapter_number,
                scene_number=scene.scene_number,
                accepted_draft_id=scene.accepted_draft_id,
            )
            for scene, chapter_number in self._session.execute(stmt)
        ]


__all__ = [
    "StoryMemoryEntryRepository",
    "StoryMemoryProposalRecord",
    "StoryMemoryProposalRepository",
    "StoryMemorySceneRecord",
    "StoryMemorySceneRepository",
]
