"""Persistence adapter for creative candidates and idempotent apply/undo receipts."""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any, cast

from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from imaginarium_forge.canonical import canonical_json
from imaginarium_forge.domain.generation.durable import (
    CandidateAction,
    CandidateActionStatus,
    CandidateStatus,
    GenerationCandidate,
)
from imaginarium_forge.infrastructure.db.models.orm import (
    CreativeCandidateActionRow,
    CreativeGenerationCandidateRow,
)


def _json_object(payload: str, *, field_name: str) -> dict[str, object]:
    value = json.loads(payload)
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} 必須是 JSON object")
    return value


def _json_optional_object(
    payload: str | None,
    *,
    field_name: str,
) -> dict[str, object] | None:
    if payload is None:
        return None
    return _json_object(payload, field_name=field_name)


def _candidate_row(candidate: GenerationCandidate) -> CreativeGenerationCandidateRow:
    return CreativeGenerationCandidateRow(
        id=candidate.id,
        job_id=candidate.job_id,
        producing_step_id=candidate.producing_step_id,
        artifact_kind=candidate.artifact_kind.value,
        target_type=candidate.target_type,
        target_id=candidate.target_id,
        parent_candidate_id=candidate.parent_candidate_id,
        candidate_version=candidate.candidate_version,
        original_version_ref=candidate.original_version_ref,
        base_snapshot_json=canonical_json(candidate.base_snapshot),
        base_snapshot_sha256=candidate.base_snapshot_sha256,
        content_text=candidate.content_text,
        content_json=(
            canonical_json(candidate.content_json)
            if candidate.content_json is not None
            else None
        ),
        content_sha256=candidate.content_sha256,
        quality_report_json=canonical_json(candidate.quality_report),
        provenance_json=canonical_json(candidate.provenance.model_dump(mode="json")),
        status=candidate.status.value,
        state_version=candidate.state_version,
        created_at=candidate.created_at,
        decided_at=candidate.decided_at,
    )


def _candidate_domain(row: CreativeGenerationCandidateRow) -> GenerationCandidate:
    return GenerationCandidate.model_validate(
        {
            "id": row.id,
            "job_id": row.job_id,
            "producing_step_id": row.producing_step_id,
            "artifact_kind": row.artifact_kind,
            "target_type": row.target_type,
            "target_id": row.target_id,
            "parent_candidate_id": row.parent_candidate_id,
            "candidate_version": row.candidate_version,
            "original_version_ref": row.original_version_ref,
            "base_snapshot": _json_object(
                row.base_snapshot_json,
                field_name="base_snapshot_json",
            ),
            "base_snapshot_sha256": row.base_snapshot_sha256,
            "content_text": row.content_text,
            "content_json": _json_optional_object(
                row.content_json,
                field_name="content_json",
            ),
            "content_sha256": row.content_sha256,
            "quality_report": _json_object(
                row.quality_report_json,
                field_name="quality_report_json",
            ),
            "provenance": _json_object(
                row.provenance_json,
                field_name="provenance_json",
            ),
            "status": row.status,
            "state_version": row.state_version,
            "created_at": row.created_at,
            "decided_at": row.decided_at,
        }
    )


def _candidate_update_values(candidate: GenerationCandidate) -> dict[str, object]:
    return {
        "status": candidate.status.value,
        "state_version": candidate.state_version,
        "decided_at": candidate.decided_at,
    }


def _action_row(action: CandidateAction) -> CreativeCandidateActionRow:
    return CreativeCandidateActionRow(
        id=action.id,
        candidate_id=action.candidate_id,
        action_kind=action.action_kind.value,
        status=action.status.value,
        idempotency_key=action.idempotency_key,
        target_type=action.target_type,
        target_id=action.target_id,
        selection_json=canonical_json(action.selection),
        before_snapshot_json=canonical_json(action.before_snapshot),
        before_snapshot_sha256=action.before_snapshot_sha256,
        after_snapshot_json=(
            canonical_json(action.after_snapshot)
            if action.after_snapshot is not None
            else None
        ),
        after_snapshot_sha256=action.after_snapshot_sha256,
        base_version_ref=action.base_version_ref,
        result_version_ref=action.result_version_ref,
        undo_of_action_id=action.undo_of_action_id,
        error_code=action.error_code,
        safe_error_message=action.safe_error_message,
        created_at=action.created_at,
        completed_at=action.completed_at,
    )


def _action_domain(row: CreativeCandidateActionRow) -> CandidateAction:
    return CandidateAction.model_validate(
        {
            "id": row.id,
            "candidate_id": row.candidate_id,
            "action_kind": row.action_kind,
            "status": row.status,
            "idempotency_key": row.idempotency_key,
            "target_type": row.target_type,
            "target_id": row.target_id,
            "selection": _json_object(
                row.selection_json,
                field_name="selection_json",
            ),
            "before_snapshot": _json_object(
                row.before_snapshot_json,
                field_name="before_snapshot_json",
            ),
            "before_snapshot_sha256": row.before_snapshot_sha256,
            "after_snapshot": _json_optional_object(
                row.after_snapshot_json,
                field_name="after_snapshot_json",
            ),
            "after_snapshot_sha256": row.after_snapshot_sha256,
            "base_version_ref": row.base_version_ref,
            "result_version_ref": row.result_version_ref,
            "undo_of_action_id": row.undo_of_action_id,
            "error_code": row.error_code,
            "safe_error_message": row.safe_error_message,
            "created_at": row.created_at,
            "completed_at": row.completed_at,
        }
    )


class CreativeCandidateRepository:
    """Keep generated alternatives and reversible apply history separate."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def add_candidate(self, candidate: GenerationCandidate) -> None:
        self._session.add(_candidate_row(candidate))

    def update_candidate_cas(
        self,
        candidate: GenerationCandidate,
        *,
        expected_state_version: int,
    ) -> bool:
        if candidate.state_version != expected_state_version + 1:
            raise ValueError("candidate state_version 必須恰好遞增 1")
        result = cast(
            CursorResult[Any],
            self._session.execute(
                update(CreativeGenerationCandidateRow)
                .where(
                    CreativeGenerationCandidateRow.id == candidate.id,
                    CreativeGenerationCandidateRow.state_version
                    == expected_state_version,
                )
                .values(**_candidate_update_values(candidate))
            ),
        )
        return bool(result.rowcount == 1)

    def get_candidate(self, candidate_id: str) -> GenerationCandidate | None:
        row = self._session.get(CreativeGenerationCandidateRow, candidate_id)
        return _candidate_domain(row) if row is not None else None

    def list_candidates(
        self,
        *,
        job_id: str | None = None,
        target_type: str | None = None,
        target_id: str | None = None,
        statuses: Iterable[CandidateStatus] | None = None,
    ) -> list[GenerationCandidate]:
        stmt = select(CreativeGenerationCandidateRow)
        if job_id is not None:
            stmt = stmt.where(CreativeGenerationCandidateRow.job_id == job_id)
        if target_type is not None:
            stmt = stmt.where(CreativeGenerationCandidateRow.target_type == target_type)
        if target_id is not None:
            stmt = stmt.where(CreativeGenerationCandidateRow.target_id == target_id)
        if statuses is not None:
            values = tuple(status.value for status in statuses)
            if not values:
                return []
            stmt = stmt.where(CreativeGenerationCandidateRow.status.in_(values))
        stmt = stmt.order_by(
            CreativeGenerationCandidateRow.created_at.desc(),
            CreativeGenerationCandidateRow.candidate_version.desc(),
            CreativeGenerationCandidateRow.id,
        )
        return [_candidate_domain(row) for row in self._session.scalars(stmt).all()]

    def add_action(self, action: CandidateAction) -> None:
        self._session.add(_action_row(action))

    def complete_action_once(self, action: CandidateAction) -> bool:
        if action.status is CandidateActionStatus.PENDING:
            raise ValueError("candidate action completion 必須是 terminal state")
        result = cast(
            CursorResult[Any],
            self._session.execute(
                update(CreativeCandidateActionRow)
                .where(
                    CreativeCandidateActionRow.id == action.id,
                    CreativeCandidateActionRow.candidate_id == action.candidate_id,
                    CreativeCandidateActionRow.idempotency_key == action.idempotency_key,
                    CreativeCandidateActionRow.status
                    == CandidateActionStatus.PENDING.value,
                )
                .values(
                    status=action.status.value,
                    after_snapshot_json=(
                        canonical_json(action.after_snapshot)
                        if action.after_snapshot is not None
                        else None
                    ),
                    after_snapshot_sha256=action.after_snapshot_sha256,
                    result_version_ref=action.result_version_ref,
                    error_code=action.error_code,
                    safe_error_message=action.safe_error_message,
                    completed_at=action.completed_at,
                )
            ),
        )
        return bool(result.rowcount == 1)

    def get_action(self, action_id: str) -> CandidateAction | None:
        row = self._session.get(CreativeCandidateActionRow, action_id)
        return _action_domain(row) if row is not None else None

    def get_action_by_idempotency_key(
        self,
        idempotency_key: str,
    ) -> CandidateAction | None:
        row = self._session.scalar(
            select(CreativeCandidateActionRow).where(
                CreativeCandidateActionRow.idempotency_key == idempotency_key
            )
        )
        return _action_domain(row) if row is not None else None

    def list_actions(
        self,
        candidate_id: str,
        *,
        statuses: Iterable[CandidateActionStatus] | None = None,
    ) -> list[CandidateAction]:
        stmt = select(CreativeCandidateActionRow).where(
            CreativeCandidateActionRow.candidate_id == candidate_id
        )
        if statuses is not None:
            values = tuple(status.value for status in statuses)
            if not values:
                return []
            stmt = stmt.where(CreativeCandidateActionRow.status.in_(values))
        stmt = stmt.order_by(
            CreativeCandidateActionRow.created_at,
            CreativeCandidateActionRow.id,
        )
        return [_action_domain(row) for row in self._session.scalars(stmt).all()]


__all__ = ["CreativeCandidateRepository"]
