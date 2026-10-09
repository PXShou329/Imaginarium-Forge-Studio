"""Read/append only; transaction ownership remains with the author command service."""

from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from imaginarium_forge.canonical import canonical_json
from imaginarium_forge.domain.generation.unit_author_decision import UnitAuthorDecision
from imaginarium_forge.infrastructure.db.models.unit_author_decisions import (
    CreativeUnitAuthorDecisionRow,
)


def _domain(row: CreativeUnitAuthorDecisionRow) -> UnitAuthorDecision:
    return UnitAuthorDecision.model_validate({
        "id": row.id, "job_id": row.job_id, "step_id": row.step_id, "unit_id": row.unit_id,
        "sequence": row.sequence, "kind": row.kind, "command_sha256": row.command_sha256,
        "payload": json.loads(row.payload_json), "payload_sha256": row.payload_sha256,
        "created_at": row.created_at,
    })


class UnitAuthorDecisionRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, decision_id: str) -> UnitAuthorDecision | None:
        row = self._session.get(CreativeUnitAuthorDecisionRow, decision_id)
        return None if row is None else _domain(row)

    def list_for_job(self, job_id: str) -> tuple[UnitAuthorDecision, ...]:
        rows = self._session.scalars(
            select(CreativeUnitAuthorDecisionRow)
            .where(CreativeUnitAuthorDecisionRow.job_id == job_id)
            .order_by(CreativeUnitAuthorDecisionRow.sequence)
        ).all()
        return tuple(_domain(row) for row in rows)

    def add(self, decision: UnitAuthorDecision) -> None:
        # Frozen models still contain mutable JSON containers; validate again at the write boundary.
        decision = UnitAuthorDecision.model_validate(decision.model_dump(mode="json"))
        self._session.add(CreativeUnitAuthorDecisionRow(
            id=decision.id, job_id=decision.job_id, step_id=decision.step_id,
            unit_id=decision.unit_id, sequence=decision.sequence, kind=decision.kind,
            command_sha256=decision.command_sha256, payload_json=canonical_json(decision.payload),
            payload_sha256=decision.payload_sha256, created_at=decision.created_at,
        ))
