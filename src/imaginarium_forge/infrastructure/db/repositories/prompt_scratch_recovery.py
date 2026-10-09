"""Persistence primitives for Prompt Scratch recovery and idempotency receipts."""

from __future__ import annotations

from typing import Any, cast

from sqlalchemy import case, func, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from imaginarium_forge.domain.character.gender import CharacterGender
from imaginarium_forge.domain.prompt.scratch_draft import PromptScratchEditorKind
from imaginarium_forge.domain.prompt.scratch_recovery import (
    PromptScratchConflictReason,
    PromptScratchRecoveryIntent,
    PromptScratchRecoveryJournal,
    PromptScratchRecoveryPayload,
    PromptScratchRecoveryState,
    PromptScratchResolutionKind,
)
from imaginarium_forge.infrastructure.db.models.orm import (
    PromptScratchRecoveryJournalRow,
)


def _changed_one_row(result: object) -> bool:
    return bool(cast(CursorResult[Any], result).rowcount == 1)


def _payload_values(payload: PromptScratchRecoveryPayload) -> dict[str, object]:
    return {
        "raw_project_id": payload.project_id,
        "raw_link_project": int(payload.link_project),
        "raw_editor_kind": payload.kind.value,
        "raw_editor_gender": payload.gender.value,
        "raw_title": payload.title,
        "raw_character_name": payload.character_name,
        "raw_character_image_prompt_en": payload.character_image_prompt_en,
        "raw_background_image_prompt_en": payload.background_image_prompt_en,
        "raw_notes": payload.notes,
        "payload_sha256": payload.sha256(),
    }


def _to_domain(row: PromptScratchRecoveryJournalRow) -> PromptScratchRecoveryJournal:
    state = PromptScratchRecoveryState(row.state)
    payload: PromptScratchRecoveryPayload | None = None
    if state in {
        PromptScratchRecoveryState.PENDING,
        PromptScratchRecoveryState.CONFLICT,
    }:
        if any(
            value is None
            for value in (
                row.raw_link_project,
                row.raw_title,
                row.raw_character_name,
                row.raw_character_image_prompt_en,
                row.raw_background_image_prompt_en,
                row.raw_notes,
            )
        ):
            raise ValueError("Prompt Scratch 復原資料完整性檢查失敗")
        payload = PromptScratchRecoveryPayload(
            kind=PromptScratchEditorKind(row.raw_editor_kind),
            gender=CharacterGender(row.raw_editor_gender),
            title=cast(str, row.raw_title),
            character_name=cast(str, row.raw_character_name),
            character_image_prompt_en=cast(str, row.raw_character_image_prompt_en),
            background_image_prompt_en=cast(str, row.raw_background_image_prompt_en),
            notes=cast(str, row.raw_notes),
            link_project=bool(row.raw_link_project),
            project_id=row.raw_project_id,
        )
        if payload.sha256() != row.payload_sha256:
            raise ValueError("Prompt Scratch 復原資料完整性檢查失敗")
    return PromptScratchRecoveryJournal(
        id=row.id,
        intent=PromptScratchRecoveryIntent(row.intent),
        existing_draft_id=row.existing_draft_id,
        reserved_draft_id=row.reserved_draft_id,
        base_updated_at=row.base_updated_at,
        payload=payload,
        receipt_kind=PromptScratchEditorKind(row.raw_editor_kind),
        receipt_gender=CharacterGender(row.raw_editor_gender),
        payload_sha256=row.payload_sha256,
        sequence=row.sequence,
        state=state,
        conflict_reason=(
            PromptScratchConflictReason(row.conflict_reason) if row.conflict_reason else None
        ),
        resolution_kind=(
            PromptScratchResolutionKind(row.resolution_kind) if row.resolution_kind else None
        ),
        committed_draft_id=row.committed_draft_id,
        committed_updated_at=row.committed_updated_at,
        resolved_from_sequence=row.resolved_from_sequence,
        created_at=row.created_at,
        updated_at=row.updated_at,
        resolved_at=row.resolved_at,
    )


class PromptScratchRecoveryRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, journal_id: str) -> PromptScratchRecoveryJournal | None:
        row = self._session.get(PromptScratchRecoveryJournalRow, journal_id)
        return _to_domain(row) if row is not None else None

    def insert_pending_if_absent(self, journal: PromptScratchRecoveryJournal) -> bool:
        if journal.payload is None:
            raise ValueError("pending recovery requires a payload")
        values: dict[str, object] = {
            "id": journal.id,
            "intent": journal.intent.value,
            "existing_draft_id": journal.existing_draft_id,
            "reserved_draft_id": journal.reserved_draft_id,
            "base_updated_at": journal.base_updated_at,
            **_payload_values(journal.payload),
            "sequence": journal.sequence,
            "state": journal.state.value,
            "conflict_reason": "",
            "resolution_kind": None,
            "committed_draft_id": None,
            "committed_updated_at": None,
            "resolved_from_sequence": None,
            "created_at": journal.created_at,
            "updated_at": journal.updated_at,
            "resolved_at": None,
        }
        result = cast(
            CursorResult[Any],
            self._session.execute(
                sqlite_insert(PromptScratchRecoveryJournalRow)
                .values(**values)
                .on_conflict_do_nothing(index_elements=["id"])
            ),
        )
        return bool(result.rowcount == 1)

    def capture_cas(
        self,
        *,
        journal_id: str,
        expected_sequence: int,
        intent: str,
        existing_draft_id: str | None,
        reserved_draft_id: str | None,
        base_updated_at: str | None,
        payload: PromptScratchRecoveryPayload,
        updated_at: str,
    ) -> bool:
        result = self._session.execute(
            update(PromptScratchRecoveryJournalRow)
            .where(
                PromptScratchRecoveryJournalRow.id == journal_id,
                PromptScratchRecoveryJournalRow.state.in_(
                    (
                        PromptScratchRecoveryState.PENDING.value,
                        PromptScratchRecoveryState.CONFLICT.value,
                    )
                ),
                PromptScratchRecoveryJournalRow.sequence == expected_sequence,
                PromptScratchRecoveryJournalRow.intent == intent,
                PromptScratchRecoveryJournalRow.existing_draft_id == existing_draft_id,
                PromptScratchRecoveryJournalRow.reserved_draft_id == reserved_draft_id,
                PromptScratchRecoveryJournalRow.base_updated_at == base_updated_at,
                PromptScratchRecoveryJournalRow.payload_sha256 != payload.sha256(),
            )
            .values(
                **_payload_values(payload),
                sequence=expected_sequence + 1,
                updated_at=updated_at,
                resolved_from_sequence=case(
                    (
                        PromptScratchRecoveryJournalRow.state
                        == PromptScratchRecoveryState.CONFLICT.value,
                        expected_sequence,
                    ),
                    else_=PromptScratchRecoveryJournalRow.resolved_from_sequence,
                ),
                resolved_at=case(
                    (
                        PromptScratchRecoveryJournalRow.state
                        == PromptScratchRecoveryState.CONFLICT.value,
                        updated_at,
                    ),
                    else_=PromptScratchRecoveryJournalRow.resolved_at,
                ),
            )
        )
        return _changed_one_row(result)

    def claim(
        self,
        journal_id: str,
        *,
        expected_sequence: int,
        expected_hash: str,
        allowed_states: tuple[PromptScratchRecoveryState, ...],
    ) -> bool:
        """First-write guarded claim; the no-op UPDATE acquires SQLite's writer lock."""

        result = self._session.execute(
            update(PromptScratchRecoveryJournalRow)
            .where(
                PromptScratchRecoveryJournalRow.id == journal_id,
                PromptScratchRecoveryJournalRow.state.in_(
                    tuple(state.value for state in allowed_states)
                ),
                PromptScratchRecoveryJournalRow.sequence == expected_sequence,
                PromptScratchRecoveryJournalRow.payload_sha256 == expected_hash,
            )
            .values(sequence=PromptScratchRecoveryJournalRow.sequence)
        )
        return _changed_one_row(result)

    def mark_conflict(
        self,
        journal_id: str,
        *,
        expected_sequence: int,
        expected_hash: str,
        reason: PromptScratchConflictReason,
        resolved_at: str,
    ) -> bool:
        result = self._session.execute(
            update(PromptScratchRecoveryJournalRow)
            .where(
                PromptScratchRecoveryJournalRow.id == journal_id,
                PromptScratchRecoveryJournalRow.state == PromptScratchRecoveryState.PENDING.value,
                PromptScratchRecoveryJournalRow.sequence == expected_sequence,
                PromptScratchRecoveryJournalRow.payload_sha256 == expected_hash,
            )
            .values(
                state=PromptScratchRecoveryState.CONFLICT.value,
                conflict_reason=reason.value,
                sequence=expected_sequence + 1,
                resolved_from_sequence=expected_sequence,
                updated_at=resolved_at,
                resolved_at=resolved_at,
            )
        )
        return _changed_one_row(result)

    def mark_committed(
        self,
        journal_id: str,
        *,
        source_state: PromptScratchRecoveryState,
        expected_sequence: int,
        expected_hash: str,
        resolution_kind: PromptScratchResolutionKind,
        draft_id: str,
        draft_updated_at: str,
        resolved_at: str,
    ) -> bool:
        result = self._session.execute(
            update(PromptScratchRecoveryJournalRow)
            .where(
                PromptScratchRecoveryJournalRow.id == journal_id,
                PromptScratchRecoveryJournalRow.state == source_state.value,
                PromptScratchRecoveryJournalRow.sequence == expected_sequence,
                PromptScratchRecoveryJournalRow.payload_sha256 == expected_hash,
            )
            .values(
                raw_project_id=None,
                raw_link_project=None,
                raw_title=None,
                raw_character_name=None,
                raw_character_image_prompt_en=None,
                raw_background_image_prompt_en=None,
                raw_notes=None,
                state=PromptScratchRecoveryState.COMMITTED.value,
                conflict_reason="",
                resolution_kind=resolution_kind.value,
                committed_draft_id=draft_id,
                committed_updated_at=draft_updated_at,
                sequence=expected_sequence + 1,
                resolved_from_sequence=expected_sequence,
                updated_at=resolved_at,
                resolved_at=resolved_at,
            )
        )
        return _changed_one_row(result)

    def mark_discarded(
        self,
        journal_id: str,
        *,
        expected_sequence: int,
        resolved_at: str,
    ) -> bool:
        result = self._session.execute(
            update(PromptScratchRecoveryJournalRow)
            .where(
                PromptScratchRecoveryJournalRow.id == journal_id,
                PromptScratchRecoveryJournalRow.state.in_(
                    (
                        PromptScratchRecoveryState.PENDING.value,
                        PromptScratchRecoveryState.CONFLICT.value,
                    )
                ),
                PromptScratchRecoveryJournalRow.sequence == expected_sequence,
            )
            .values(
                raw_project_id=None,
                raw_link_project=None,
                raw_title=None,
                raw_character_name=None,
                raw_character_image_prompt_en=None,
                raw_background_image_prompt_en=None,
                raw_notes=None,
                state=PromptScratchRecoveryState.DISCARDED.value,
                conflict_reason="",
                resolution_kind=None,
                committed_draft_id=None,
                committed_updated_at=None,
                sequence=expected_sequence + 1,
                resolved_from_sequence=expected_sequence,
                updated_at=resolved_at,
                resolved_at=resolved_at,
            )
        )
        return _changed_one_row(result)

    def list_actionable(self, *, limit: int) -> list[PromptScratchRecoveryJournal]:
        stmt = (
            select(PromptScratchRecoveryJournalRow)
            .where(
                PromptScratchRecoveryJournalRow.state.in_(
                    (
                        PromptScratchRecoveryState.PENDING.value,
                        PromptScratchRecoveryState.CONFLICT.value,
                    )
                )
            )
            .order_by(
                PromptScratchRecoveryJournalRow.updated_at.desc(),
                PromptScratchRecoveryJournalRow.id,
            )
            .limit(limit)
        )
        return [_to_domain(row) for row in self._session.scalars(stmt).all()]

    def count_actionable(self) -> int:
        stmt = (
            select(func.count())
            .select_from(PromptScratchRecoveryJournalRow)
            .where(
                PromptScratchRecoveryJournalRow.state.in_(
                    (
                        PromptScratchRecoveryState.PENDING.value,
                        PromptScratchRecoveryState.CONFLICT.value,
                    )
                )
            )
        )
        return int(self._session.scalar(stmt) or 0)


__all__ = ["PromptScratchRecoveryRepository"]
