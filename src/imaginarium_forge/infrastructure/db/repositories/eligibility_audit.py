"""Append-only eligibility audit repository.

CRITICAL: this repository intentionally exposes ONLY add + read. There is no
update and no delete method, at any layer. Audit rows never authorize a future
request — the service always re-evaluates live (see EligibilityService).
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from imaginarium_forge.infrastructure.db.models.orm import EligibilityEvaluationRow


@dataclass(frozen=True)
class EligibilityAuditRecord:
    id: str
    character_id: str
    character_version_id: str | None
    requested_character_version_id: str | None
    request_type: str
    allowed: bool
    reason_code: str
    validator_version: str
    input_fingerprint: str
    details_json: str | None
    evaluated_at: str


def _to_record(row: EligibilityEvaluationRow) -> EligibilityAuditRecord:
    return EligibilityAuditRecord(
        id=row.id,
        character_id=row.character_id,
        character_version_id=row.character_version_id,
        requested_character_version_id=row.requested_character_version_id,
        request_type=row.request_type,
        allowed=bool(row.allowed),
        reason_code=row.reason_code,
        validator_version=row.validator_version,
        input_fingerprint=row.input_fingerprint,
        details_json=row.details_json,
        evaluated_at=row.evaluated_at,
    )


class EligibilityAuditRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, record: EligibilityAuditRecord) -> None:
        self._session.add(
            EligibilityEvaluationRow(
                id=record.id,
                character_id=record.character_id,
                character_version_id=record.character_version_id,
                requested_character_version_id=record.requested_character_version_id,
                request_type=record.request_type,
                allowed=1 if record.allowed else 0,
                reason_code=record.reason_code,
                validator_version=record.validator_version,
                input_fingerprint=record.input_fingerprint,
                details_json=record.details_json,
                evaluated_at=record.evaluated_at,
            )
        )

    def list_for_character(
        self, character_id: str, *, limit: int | None = None
    ) -> list[EligibilityAuditRecord]:
        stmt = (
            select(EligibilityEvaluationRow)
            .where(EligibilityEvaluationRow.character_id == character_id)
            .order_by(EligibilityEvaluationRow.evaluated_at.desc())
        )
        if limit is not None:
            stmt = stmt.limit(limit)
        return [_to_record(r) for r in self._session.scalars(stmt).all()]
