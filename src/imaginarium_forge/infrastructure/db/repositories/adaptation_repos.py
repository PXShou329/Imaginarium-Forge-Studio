"""Repositories for the independent screenplay adaptation aggregate."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, fields

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from imaginarium_forge.infrastructure.db.models.orm import (
    AdaptationAcceptanceEventRow,
    AdaptationGenerationRunRow,
    AdaptationRevisionRow,
    AdaptationRow,
)

_SCREENPLAY_ASSURANCE_SCHEMA = "screenplay-prose-adapter-v1"
_SCREENPLAY_RUNTIME_ATTEMPT_CEILING = 3
_COMPLETE_REASONS = frozenset({"length_satisfied", "advisory_miss"})
_FORBIDDEN_ASSURANCE_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "openai_api_key",
        "authorization",
        "authorization_header",
        "bearer_token",
        "access_token",
        "refresh_token",
        "credential",
        "credentials",
        "credential_value",
        "text",
        "prompt",
        "system",
        "instruction",
        "context_text",
        "rolling_summary",
        "protected_facts",
        "raw",
        "raw_response",
        "content_text",
    }
)


def _validate_screenplay_assurance(
    assurance_json: str,
    *,
    terminal_status: str,
    reason_code: str,
    output_sha256: str | None,
) -> None:
    """Validate generation evidence before SQLite enforces the same invariants."""

    try:
        payload = json.loads(assurance_json)
    except (TypeError, ValueError) as exc:
        raise ValueError("screenplay assurance evidence is invalid") from exc
    if payload == {}:
        if terminal_status in {"partial", "cancelled"}:
            raise ValueError("screenplay partial/cancelled runs require assurance evidence")
        return
    if not isinstance(payload, dict):
        raise ValueError("screenplay assurance evidence must be an object")
    _assert_assurance_hash_only(payload)
    if payload.get("schema_version") != _SCREENPLAY_ASSURANCE_SCHEMA:
        raise ValueError("screenplay assurance evidence has an unsupported schema")

    selected_candidate = payload.get("selected_candidate")
    selected_reason = payload.get("selected_reason")
    domain_complete = payload.get("domain_complete")
    selected_hash = payload.get("selected_output_sha256")
    result = payload.get("result")
    if selected_candidate not in {"none", "accumulated", "condensed"}:
        raise ValueError("screenplay assurance selected candidate is invalid")
    if not isinstance(selected_reason, str) or not selected_reason:
        raise ValueError("screenplay assurance selected reason is invalid")
    if type(domain_complete) is not bool:
        raise ValueError("screenplay assurance completion flag is invalid")
    if selected_candidate == "none":
        if selected_hash is not None:
            raise ValueError("screenplay assurance without a candidate cannot have an output hash")
    elif not _is_sha256(selected_hash):
        raise ValueError("screenplay assurance selected output hash is invalid")
    if not isinstance(result, dict) or not result:
        raise ValueError("screenplay assurance result cannot be empty")

    calls = result.get("calls")
    progress = result.get("progress")
    decision = result.get("decision")
    if not isinstance(calls, list) or not calls:
        raise ValueError("screenplay assurance requires a non-empty call ledger")
    if not isinstance(progress, dict) or not isinstance(decision, dict):
        raise ValueError("screenplay assurance result structure is invalid")

    runtime_attempts = 0
    estimated_cost = 0
    for call in calls:
        if not isinstance(call, dict):
            raise ValueError("screenplay assurance call ledger is invalid")
        attempts = _required_nonnegative_int(call.get("runtime_attempts_used"))
        cost = _required_nonnegative_int(call.get("estimated_cost_microunits"))
        if attempts > _SCREENPLAY_RUNTIME_ATTEMPT_CEILING:
            raise ValueError("screenplay runtime attempt ceiling exceeded")
        if (attempts == 0) != (cost == 0):
            raise ValueError("screenplay runtime attempt cost evidence is inconsistent")
        if attempts and cost % attempts:
            raise ValueError("screenplay runtime attempt cost evidence is inconsistent")
        runtime_attempts += attempts
        estimated_cost += cost

    if _required_nonnegative_int(progress.get("provider_attempts")) != len(calls):
        raise ValueError("screenplay logical attempt ledger is inconsistent")
    if (
        _required_nonnegative_int(progress.get("runtime_provider_attempts"))
        != runtime_attempts
    ):
        raise ValueError("screenplay runtime attempt ledger is inconsistent")
    if (
        _required_nonnegative_int(progress.get("estimated_cost_microunits"))
        != estimated_cost
    ):
        raise ValueError("screenplay cost ledger is inconsistent")

    selected_decision: object = decision
    if selected_candidate == "condensed":
        selected_decision = result.get("condensation_decision")
    if not isinstance(selected_decision, dict):
        raise ValueError("screenplay selected decision is missing")
    if selected_decision.get("reason") != selected_reason:
        raise ValueError("screenplay selected reason disagrees with the result")
    if selected_decision.get("domain_complete") is not domain_complete:
        raise ValueError("screenplay completion flag disagrees with the result")

    if selected_candidate != "none":
        candidate_key = (
            "condensed_candidate"
            if selected_candidate == "condensed"
            else "accumulated_candidate"
        )
        candidate = result.get(candidate_key)
        if not isinstance(candidate, dict) or candidate.get("text_sha256") != selected_hash:
            raise ValueError("screenplay selected candidate hash disagrees with the result")

    if terminal_status == "succeeded":
        valid = (
            domain_complete
            and selected_candidate != "none"
            and selected_reason in _COMPLETE_REASONS
            and selected_hash == output_sha256
        )
    elif terminal_status == "partial":
        valid = (
            not domain_complete
            and selected_candidate != "none"
            and selected_reason == reason_code
            and selected_hash == output_sha256
        )
    elif terminal_status == "cancelled":
        valid = (
            not domain_complete
            and selected_candidate == "none"
            and selected_reason == "cancelled_by_author"
        )
    elif terminal_status == "failed":
        valid = not domain_complete and selected_candidate == "none"
    else:
        valid = False
    if not valid:
        raise ValueError("screenplay assurance evidence disagrees with terminal state")


def _assert_assurance_hash_only(value: object) -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            if str(key).casefold() in _FORBIDDEN_ASSURANCE_KEYS:
                raise ValueError("screenplay assurance evidence must be hash-only")
            _assert_assurance_hash_only(nested)
    elif isinstance(value, list):
        for nested in value:
            _assert_assurance_hash_only(nested)


def _required_nonnegative_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("screenplay assurance integer evidence is invalid")
    return value


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
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
    resulting_revision_id: str | None
    prompt_tokens: int | None
    completion_tokens: int | None
    started_at: str
    completed_at: str
    latency_ms: int
    assurance_json: str = "{}"


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
        assurance_json: str | None = None,
    ) -> bool:
        self._validate_assurance_transition(
            run_id,
            assurance_json=assurance_json,
            terminal_status="failed",
            reason_code=reason_code,
            output_sha256=None,
        )
        values: dict[str, object] = {
            "status": "failed",
            "reason_code": reason_code,
            "completed_at": completed_at,
            "latency_ms": latency_ms,
        }
        if assurance_json is not None:
            values["assurance_json"] = assurance_json
        result = self._session.execute(
            update(AdaptationGenerationRunRow)
            .where(
                AdaptationGenerationRunRow.id == run_id,
                AdaptationGenerationRunRow.status == "running",
            )
            .values(**values)
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
        terminal_status: str = "succeeded",
        reason_code: str = "",
        assurance_json: str | None = None,
    ) -> bool:
        if terminal_status not in {"succeeded", "partial"}:
            raise ValueError("screenplay candidate status must be succeeded or partial")
        if (terminal_status == "succeeded") != (reason_code == ""):
            raise ValueError("only a succeeded screenplay candidate has a blank reason code")
        self._validate_assurance_transition(
            run_id,
            assurance_json=assurance_json,
            terminal_status=terminal_status,
            reason_code=reason_code,
            output_sha256=output_sha256,
        )
        values: dict[str, object] = {
            "status": terminal_status,
            "reason_code": reason_code,
            "resulting_revision_id": resulting_revision_id,
            "output_sha256": output_sha256,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "completed_at": completed_at,
            "latency_ms": latency_ms,
        }
        if assurance_json is not None:
            values["assurance_json"] = assurance_json
        running_result = self._session.execute(
            update(AdaptationGenerationRunRow)
            .where(
                AdaptationGenerationRunRow.id == run_id,
                AdaptationGenerationRunRow.status == "running",
            )
            .values(**values)
        )
        if _rowcount(running_result):
            return True

        return False

    def finalize_cancelled(
        self,
        run_id: str,
        *,
        completed_at: str,
        latency_ms: int,
        assurance_json: str,
    ) -> bool:
        self._validate_assurance_transition(
            run_id,
            assurance_json=assurance_json,
            terminal_status="cancelled",
            reason_code="cancelled_by_author",
            output_sha256=None,
        )
        result = self._session.execute(
            update(AdaptationGenerationRunRow)
            .where(
                AdaptationGenerationRunRow.id == run_id,
                AdaptationGenerationRunRow.status == "running",
            )
            .values(
                status="cancelled",
                reason_code="cancelled_by_author",
                completed_at=completed_at,
                latency_ms=latency_ms,
                assurance_json=assurance_json,
            )
        )
        return bool(_rowcount(result))


    def _validate_assurance_transition(
        self,
        run_id: str,
        *,
        assurance_json: str | None,
        terminal_status: str,
        reason_code: str,
        output_sha256: str | None,
    ) -> None:
        row = self._session.get(AdaptationGenerationRunRow, run_id)
        if row is None:
            return
        _validate_screenplay_assurance(
            row.assurance_json if assurance_json is None else assurance_json,
            terminal_status=terminal_status,
            reason_code=reason_code,
            output_sha256=output_sha256,
        )

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
