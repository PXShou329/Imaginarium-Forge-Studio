"""Repositories for the independent screenplay adaptation aggregate."""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from imaginarium_forge.infrastructure.db.models.orm import (
    AdaptationAcceptanceEventRow,
    AdaptationGenerationRunRow,
    AdaptationRevisionRow,
    AdaptationRow,
)


@dataclass(frozen=True, slots=True)
class AdaptationRecord:
    id: str
    project_id: str
    adaptation_type: str
    title: str
    source_outline_id: str
    source_chapter_id: str
    source_scene_id: str
    source_draft_id: str
    source_scene_card_version_id: str
    source_prose_sha256: str
    source_scene_card_sha256: str
    participant_manifest_json: str
    participant_manifest_sha256: str
    source_snapshot_json: str
    source_snapshot_sha256: str
    content_mode: str
    working_revision_id: str | None
    accepted_revision_id: str | None
    supersedes_adaptation_id: str | None
    lifecycle_status: str
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class AdaptationRevisionRecord:
    id: str
    adaptation_id: str
    project_id: str
    version_number: int
    parent_revision_id: str | None
    screenplay_text: str
    screenplay_sha256: str
    origin: str
    completion_status: str
    generation_run_id: str | None
    change_note: str
    created_at: str


@dataclass(frozen=True, slots=True)
class AdaptationGenerationRunRecord:
    id: str
    adaptation_id: str
    project_id: str
    source_snapshot_sha256: str
    expected_working_revision_id: str | None
    provider: str
    model: str
    brief_json: str
    options_json: str
    input_snapshot_json: str
    input_snapshot_sha256: str
    participant_manifest_json: str
    participant_manifest_sha256: str
    eligibility_evaluation_ids_json: str
    system_message_sha256: str
    user_message_sha256: str
    system_message_byte_size: int
    user_message_byte_size: int
    rendered_system_message: str | None
    rendered_user_message: str | None
    rendered_message_storage_enabled: int
    status: str
    reason_code: str
    output_sha256: str | None
    quarantined_output_text: str | None
    quarantined_output_sha256: str | None
    review_evaluation_ids_json: str
    reviewed_at: str
    resulting_revision_id: str | None
    prompt_tokens: int | None
    completion_tokens: int | None
    started_at: str
    completed_at: str
    latency_ms: int


@dataclass(frozen=True, slots=True)
class AdaptationAcceptanceEventRecord:
    id: str
    adaptation_id: str
    revision_id: str
    previous_accepted_revision_id: str | None
    project_id: str
    source_snapshot_sha256: str
    accepted_at: str


class AdaptationRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, record: AdaptationRecord) -> None:
        self._session.add(AdaptationRow(**asdict(record)))

    def get(self, adaptation_id: str) -> AdaptationRecord | None:
        row = self._session.get(AdaptationRow, adaptation_id)
        return None if row is None else self._map(row)

    def list_for_scene(self, scene_id: str) -> list[AdaptationRecord]:
        stmt = (
            select(AdaptationRow)
            .where(AdaptationRow.source_scene_id == scene_id)
            .order_by(AdaptationRow.created_at, AdaptationRow.id)
        )
        return [self._map(row) for row in self._session.scalars(stmt)]

    def compare_and_set_working(
        self,
        adaptation_id: str,
        *,
        expected_revision_id: str | None,
        new_revision_id: str,
        updated_at: str,
    ) -> bool:
        expected = (
            AdaptationRow.working_revision_id.is_(None)
            if expected_revision_id is None
            else AdaptationRow.working_revision_id == expected_revision_id
        )
        result = self._session.execute(
            update(AdaptationRow)
            .where(AdaptationRow.id == adaptation_id, expected)
            .values(working_revision_id=new_revision_id, updated_at=updated_at)
        )
        return bool(_rowcount(result))

    def compare_and_set_accepted(
        self,
        adaptation_id: str,
        *,
        expected_revision_id: str | None,
        new_revision_id: str,
        updated_at: str,
    ) -> bool:
        expected = (
            AdaptationRow.accepted_revision_id.is_(None)
            if expected_revision_id is None
            else AdaptationRow.accepted_revision_id == expected_revision_id
        )
        result = self._session.execute(
            update(AdaptationRow)
            .where(AdaptationRow.id == adaptation_id, expected)
            .values(accepted_revision_id=new_revision_id, updated_at=updated_at)
        )
        return bool(_rowcount(result))

    @staticmethod
    def _map(row: AdaptationRow) -> AdaptationRecord:
        return AdaptationRecord(
            **{field.name: getattr(row, field.name) for field in fields(AdaptationRecord)}
        )


class AdaptationRevisionRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, record: AdaptationRevisionRecord) -> None:
        self._session.add(AdaptationRevisionRow(**asdict(record)))

    def get(self, revision_id: str) -> AdaptationRevisionRecord | None:
        row = self._session.get(AdaptationRevisionRow, revision_id)
        return None if row is None else self._map(row)

    def list_for_adaptation(self, adaptation_id: str) -> list[AdaptationRevisionRecord]:
        stmt = (
            select(AdaptationRevisionRow)
            .where(AdaptationRevisionRow.adaptation_id == adaptation_id)
            .order_by(AdaptationRevisionRow.version_number)
        )
        return [self._map(row) for row in self._session.scalars(stmt)]

    def next_version_number(self, adaptation_id: str) -> int:
        current = self._session.scalar(
            select(func.max(AdaptationRevisionRow.version_number)).where(
                AdaptationRevisionRow.adaptation_id == adaptation_id
            )
        )
        return int(current or 0) + 1

    @staticmethod
    def _map(row: AdaptationRevisionRow) -> AdaptationRevisionRecord:
        return AdaptationRevisionRecord(
            **{field.name: getattr(row, field.name) for field in fields(AdaptationRevisionRecord)}
        )


class AdaptationGenerationRunRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, record: AdaptationGenerationRunRecord) -> None:
        self._session.add(AdaptationGenerationRunRow(**asdict(record)))

    def get(self, run_id: str) -> AdaptationGenerationRunRecord | None:
        row = self._session.get(AdaptationGenerationRunRow, run_id)
        return None if row is None else self._map(row)

    def list_for_adaptation(self, adaptation_id: str) -> list[AdaptationGenerationRunRecord]:
        stmt = (
            select(AdaptationGenerationRunRow)
            .where(AdaptationGenerationRunRow.adaptation_id == adaptation_id)
            .order_by(AdaptationGenerationRunRow.started_at, AdaptationGenerationRunRow.id)
        )
        return [self._map(row) for row in self._session.scalars(stmt)]

    def finalize_failed(
        self,
        run_id: str,
        *,
        reason_code: str,
        completed_at: str,
        latency_ms: int,
    ) -> bool:
        result = self._session.execute(
            update(AdaptationGenerationRunRow)
            .where(
                AdaptationGenerationRunRow.id == run_id,
                AdaptationGenerationRunRow.status == "running",
            )
            .values(
                status="failed",
                reason_code=reason_code,
                completed_at=completed_at,
                latency_ms=latency_ms,
            )
        )
        return bool(_rowcount(result))

    def finalize_adult_pending(
        self,
        run_id: str,
        *,
        output_text: str,
        output_sha256: str,
        prompt_tokens: int | None,
        completion_tokens: int | None,
        completed_at: str,
        latency_ms: int,
    ) -> bool:
        result = self._session.execute(
            update(AdaptationGenerationRunRow)
            .where(
                AdaptationGenerationRunRow.id == run_id,
                AdaptationGenerationRunRow.status == "running",
            )
            .values(
                status="adult_pending",
                output_sha256=output_sha256,
                quarantined_output_text=output_text,
                quarantined_output_sha256=output_sha256,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                completed_at=completed_at,
                latency_ms=latency_ms,
            )
        )
        return bool(_rowcount(result))

    def finalize_succeeded(
        self,
        run_id: str,
        *,
        resulting_revision_id: str,
        output_sha256: str,
        prompt_tokens: int | None,
        completion_tokens: int | None,
        completed_at: str,
        latency_ms: int,
        review_evaluation_ids_json: str = "[]",
        reviewed_at: str = "",
    ) -> bool:
        running_result = self._session.execute(
            update(AdaptationGenerationRunRow)
            .where(
                AdaptationGenerationRunRow.id == run_id,
                AdaptationGenerationRunRow.status == "running",
            )
            .values(
                status="succeeded",
                resulting_revision_id=resulting_revision_id,
                output_sha256=output_sha256,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                completed_at=completed_at,
                latency_ms=latency_ms,
                review_evaluation_ids_json=review_evaluation_ids_json,
                reviewed_at=reviewed_at,
            )
        )
        if _rowcount(running_result):
            return True

        reviewed_result = self._session.execute(
            update(AdaptationGenerationRunRow)
            .where(
                AdaptationGenerationRunRow.id == run_id,
                AdaptationGenerationRunRow.status == "adult_pending",
                AdaptationGenerationRunRow.output_sha256 == output_sha256,
                AdaptationGenerationRunRow.prompt_tokens.is_(prompt_tokens),
                AdaptationGenerationRunRow.completion_tokens.is_(completion_tokens),
                AdaptationGenerationRunRow.completed_at == completed_at,
                AdaptationGenerationRunRow.latency_ms == latency_ms,
            )
            .values(
                status="succeeded",
                resulting_revision_id=resulting_revision_id,
                review_evaluation_ids_json=review_evaluation_ids_json,
                reviewed_at=reviewed_at,
            )
        )
        return bool(_rowcount(reviewed_result))

    def reject_adult_pending(
        self,
        run_id: str,
        *,
        review_evaluation_ids_json: str,
        reviewed_at: str,
    ) -> bool:
        result = self._session.execute(
            update(AdaptationGenerationRunRow)
            .where(
                AdaptationGenerationRunRow.id == run_id,
                AdaptationGenerationRunRow.status == "adult_pending",
            )
            .values(
                status="failed",
                reason_code="adult_review_rejected",
                review_evaluation_ids_json=review_evaluation_ids_json,
                reviewed_at=reviewed_at,
            )
        )
        return bool(_rowcount(result))

    @staticmethod
    def _map(row: AdaptationGenerationRunRow) -> AdaptationGenerationRunRecord:
        return AdaptationGenerationRunRecord(
            **{
                field.name: getattr(row, field.name)
                for field in fields(AdaptationGenerationRunRecord)
            }
        )


class AdaptationAcceptanceEventRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, record: AdaptationAcceptanceEventRecord) -> None:
        self._session.add(AdaptationAcceptanceEventRow(**asdict(record)))

    def list_for_adaptation(self, adaptation_id: str) -> list[AdaptationAcceptanceEventRecord]:
        stmt = (
            select(AdaptationAcceptanceEventRow)
            .where(AdaptationAcceptanceEventRow.adaptation_id == adaptation_id)
            .order_by(
                AdaptationAcceptanceEventRow.accepted_at,
                AdaptationAcceptanceEventRow.id,
            )
        )
        return [self._map(row) for row in self._session.scalars(stmt)]

    @staticmethod
    def _map(row: AdaptationAcceptanceEventRow) -> AdaptationAcceptanceEventRecord:
        return AdaptationAcceptanceEventRecord(
            **{
                field.name: getattr(row, field.name)
                for field in fields(AdaptationAcceptanceEventRecord)
            }
        )


def _rowcount(result: object) -> int:
    return int(getattr(result, "rowcount", 0) or 0)


__all__ = [
    "AdaptationAcceptanceEventRecord",
    "AdaptationAcceptanceEventRepository",
    "AdaptationGenerationRunRecord",
    "AdaptationGenerationRunRepository",
    "AdaptationRecord",
    "AdaptationRepository",
    "AdaptationRevisionRecord",
    "AdaptationRevisionRepository",
]
