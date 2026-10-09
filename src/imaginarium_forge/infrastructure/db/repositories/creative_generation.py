"""Persistence adapter for restart-authoritative creative jobs and steps."""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any, cast

from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from imaginarium_forge.canonical import canonical_json
from imaginarium_forge.domain.generation.durable import (
    GenerationJob,
    GenerationStep,
    JobStatus,
    StepStatus,
)
from imaginarium_forge.infrastructure.db.models.orm import (
    CreativeGenerationJobRow,
    CreativeGenerationStepRow,
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


def _job_row(job: GenerationJob) -> CreativeGenerationJobRow:
    return CreativeGenerationJobRow(
        id=job.id,
        artifact_kind=job.artifact_kind.value,
        operation=job.operation.value,
        target_type=job.target_type,
        target_id=job.target_id,
        status=job.status.value,
        resume_phase=job.resume_phase.value if job.resume_phase is not None else None,
        route_json=canonical_json(job.route),
        quality_profile=job.quality_profile,
        length_spec_json=canonical_json(job.length_spec),
        source_snapshot_json=canonical_json(job.source_snapshot),
        source_snapshot_sha256=job.source_snapshot_sha256,
        context_snapshot_json=canonical_json(job.context_snapshot),
        context_snapshot_sha256=job.context_snapshot_sha256,
        workflow_version=job.workflow_version,
        current_step_index=job.current_step_index,
        total_steps_hint=job.total_steps_hint,
        last_checkpoint_step_id=job.last_checkpoint_step_id,
        checkpoint_sequence=job.checkpoint_sequence,
        pause_requested=int(job.pause_requested),
        cancel_requested=int(job.cancel_requested),
        resume_requested=int(job.resume_requested),
        recovery_count=job.recovery_count,
        state_version=job.state_version,
        error_code=job.error_code,
        safe_error_message=job.safe_error_message,
        created_at=job.created_at,
        started_at=job.started_at,
        updated_at=job.updated_at,
        heartbeat_at=job.heartbeat_at,
        last_checkpoint_at=job.last_checkpoint_at,
        completed_at=job.completed_at,
    )


def _job_domain(row: CreativeGenerationJobRow) -> GenerationJob:
    return GenerationJob.model_validate(
        {
            "id": row.id,
            "artifact_kind": row.artifact_kind,
            "operation": row.operation,
            "target_type": row.target_type,
            "target_id": row.target_id,
            "status": row.status,
            "resume_phase": row.resume_phase,
            "route": _json_object(row.route_json, field_name="route_json"),
            "quality_profile": row.quality_profile,
            "length_spec": _json_object(
                row.length_spec_json,
                field_name="length_spec_json",
            ),
            "source_snapshot": _json_object(
                row.source_snapshot_json,
                field_name="source_snapshot_json",
            ),
            "source_snapshot_sha256": row.source_snapshot_sha256,
            "context_snapshot": _json_object(
                row.context_snapshot_json,
                field_name="context_snapshot_json",
            ),
            "context_snapshot_sha256": row.context_snapshot_sha256,
            "workflow_version": row.workflow_version,
            "current_step_index": row.current_step_index,
            "total_steps_hint": row.total_steps_hint,
            "last_checkpoint_step_id": row.last_checkpoint_step_id,
            "checkpoint_sequence": row.checkpoint_sequence,
            "pause_requested": bool(row.pause_requested),
            "cancel_requested": bool(row.cancel_requested),
            "resume_requested": bool(row.resume_requested),
            "recovery_count": row.recovery_count,
            "state_version": row.state_version,
            "error_code": row.error_code,
            "safe_error_message": row.safe_error_message,
            "created_at": row.created_at,
            "started_at": row.started_at,
            "updated_at": row.updated_at,
            "heartbeat_at": row.heartbeat_at,
            "last_checkpoint_at": row.last_checkpoint_at,
            "completed_at": row.completed_at,
        }
    )


def _job_update_values(job: GenerationJob) -> dict[str, object]:
    return {
        "status": job.status.value,
        "resume_phase": job.resume_phase.value if job.resume_phase is not None else None,
        "current_step_index": job.current_step_index,
        "total_steps_hint": job.total_steps_hint,
        "last_checkpoint_step_id": job.last_checkpoint_step_id,
        "checkpoint_sequence": job.checkpoint_sequence,
        "pause_requested": int(job.pause_requested),
        "cancel_requested": int(job.cancel_requested),
        "resume_requested": int(job.resume_requested),
        "recovery_count": job.recovery_count,
        "state_version": job.state_version,
        "error_code": job.error_code,
        "safe_error_message": job.safe_error_message,
        "started_at": job.started_at,
        "updated_at": job.updated_at,
        "heartbeat_at": job.heartbeat_at,
        "last_checkpoint_at": job.last_checkpoint_at,
        "completed_at": job.completed_at,
    }


def _step_row(step: GenerationStep) -> CreativeGenerationStepRow:
    return CreativeGenerationStepRow(
        id=step.id,
        job_id=step.job_id,
        ordinal=step.ordinal,
        step_kind=step.step_kind.value,
        status=step.status.value,
        idempotency_key=step.idempotency_key,
        template_id=step.template_id,
        template_version=step.template_version,
        requested_provider=step.requested_provider,
        requested_model=step.requested_model,
        provider=step.provider,
        model=step.model,
        fallback_used=int(step.fallback_used),
        input_snapshot_json=canonical_json(step.input_snapshot),
        input_snapshot_sha256=step.input_snapshot_sha256,
        output_text=step.output_text,
        output_json=(
            canonical_json(step.output_json) if step.output_json is not None else None
        ),
        output_sha256=step.output_sha256,
        partial=int(step.partial),
        partial_text=step.partial_text,
        partial_sha256=step.partial_sha256,
        stable_boundary_offset=step.stable_boundary_offset,
        finish_reason=step.finish_reason,
        checkpoint_index=step.checkpoint_index,
        prompt_tokens=step.prompt_tokens,
        completion_tokens=step.completion_tokens,
        latency_ms=step.latency_ms,
        attempts=step.attempts,
        max_attempts=step.max_attempts,
        attempt_history_json=canonical_json(
            [attempt.model_dump(mode="json") for attempt in step.attempt_history]
        ),
        error_code=step.error_code,
        safe_error_message=step.safe_error_message,
        state_version=step.state_version,
        created_at=step.created_at,
        started_at=step.started_at,
        updated_at=step.updated_at,
        checkpoint_at=step.checkpoint_at,
        interrupted_at=step.interrupted_at,
        completed_at=step.completed_at,
    )


def _step_domain(row: CreativeGenerationStepRow) -> GenerationStep:
    attempts = json.loads(row.attempt_history_json)
    if not isinstance(attempts, list):
        raise ValueError("attempt_history_json 必須是 JSON array")
    return GenerationStep.model_validate(
        {
            "id": row.id,
            "job_id": row.job_id,
            "ordinal": row.ordinal,
            "step_kind": row.step_kind,
            "status": row.status,
            "idempotency_key": row.idempotency_key,
            "template_id": row.template_id,
            "template_version": row.template_version,
            "requested_provider": row.requested_provider,
            "requested_model": row.requested_model,
            "provider": row.provider,
            "model": row.model,
            "fallback_used": bool(row.fallback_used),
            "input_snapshot": _json_object(
                row.input_snapshot_json,
                field_name="input_snapshot_json",
            ),
            "input_snapshot_sha256": row.input_snapshot_sha256,
            "output_text": row.output_text,
            "output_json": _json_optional_object(
                row.output_json,
                field_name="output_json",
            ),
            "output_sha256": row.output_sha256,
            "partial": bool(row.partial),
            "partial_text": row.partial_text,
            "partial_sha256": row.partial_sha256,
            "stable_boundary_offset": row.stable_boundary_offset,
            "finish_reason": row.finish_reason,
            "checkpoint_index": row.checkpoint_index,
            "prompt_tokens": row.prompt_tokens,
            "completion_tokens": row.completion_tokens,
            "latency_ms": row.latency_ms,
            "attempts": row.attempts,
            "max_attempts": row.max_attempts,
            "attempt_history": attempts,
            "error_code": row.error_code,
            "safe_error_message": row.safe_error_message,
            "state_version": row.state_version,
            "created_at": row.created_at,
            "started_at": row.started_at,
            "updated_at": row.updated_at,
            "checkpoint_at": row.checkpoint_at,
            "interrupted_at": row.interrupted_at,
            "completed_at": row.completed_at,
        }
    )


def _step_update_values(step: GenerationStep) -> dict[str, object]:
    return {
        "status": step.status.value,
        "provider": step.provider,
        "model": step.model,
        "fallback_used": int(step.fallback_used),
        "output_text": step.output_text,
        "output_json": (
            canonical_json(step.output_json) if step.output_json is not None else None
        ),
        "output_sha256": step.output_sha256,
        "partial": int(step.partial),
        "partial_text": step.partial_text,
        "partial_sha256": step.partial_sha256,
        "stable_boundary_offset": step.stable_boundary_offset,
        "finish_reason": step.finish_reason,
        "checkpoint_index": step.checkpoint_index,
        "prompt_tokens": step.prompt_tokens,
        "completion_tokens": step.completion_tokens,
        "latency_ms": step.latency_ms,
        "attempts": step.attempts,
        "attempt_history_json": canonical_json(
            [attempt.model_dump(mode="json") for attempt in step.attempt_history]
        ),
        "error_code": step.error_code,
        "safe_error_message": step.safe_error_message,
        "state_version": step.state_version,
        "started_at": step.started_at,
        "updated_at": step.updated_at,
        "checkpoint_at": step.checkpoint_at,
        "interrupted_at": step.interrupted_at,
        "completed_at": step.completed_at,
    }


class CreativeGenerationRepository:
    """Store complete jobs/steps and recover them after a process restart."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def add_job(self, job: GenerationJob) -> None:
        self._session.add(_job_row(job))

    def update_job_cas(
        self,
        job: GenerationJob,
        *,
        expected_state_version: int,
    ) -> bool:
        if job.state_version != expected_state_version + 1:
            raise ValueError("job state_version 必須恰好遞增 1")
        result = cast(
            CursorResult[Any],
            self._session.execute(
                update(CreativeGenerationJobRow)
                .where(
                    CreativeGenerationJobRow.id == job.id,
                    CreativeGenerationJobRow.state_version == expected_state_version,
                )
                .values(**_job_update_values(job))
            ),
        )
        return bool(result.rowcount == 1)

    def get_job(self, job_id: str) -> GenerationJob | None:
        row = self._session.get(CreativeGenerationJobRow, job_id)
        return _job_domain(row) if row is not None else None

    def list_jobs(
        self,
        *,
        statuses: Iterable[JobStatus] | None = None,
        target_type: str | None = None,
        target_id: str | None = None,
    ) -> list[GenerationJob]:
        stmt = select(CreativeGenerationJobRow)
        if statuses is not None:
            values = tuple(status.value for status in statuses)
            if not values:
                return []
            stmt = stmt.where(CreativeGenerationJobRow.status.in_(values))
        if target_type is not None:
            stmt = stmt.where(CreativeGenerationJobRow.target_type == target_type)
        if target_id is not None:
            stmt = stmt.where(CreativeGenerationJobRow.target_id == target_id)
        stmt = stmt.order_by(
            CreativeGenerationJobRow.updated_at.desc(),
            CreativeGenerationJobRow.created_at.desc(),
            CreativeGenerationJobRow.id,
        )
        return [_job_domain(row) for row in self._session.scalars(stmt).all()]

    def add_step(self, step: GenerationStep) -> None:
        self._session.add(_step_row(step))

    def update_step_cas(
        self,
        step: GenerationStep,
        *,
        expected_state_version: int,
    ) -> bool:
        if step.state_version != expected_state_version + 1:
            raise ValueError("step state_version 必須恰好遞增 1")
        result = cast(
            CursorResult[Any],
            self._session.execute(
                update(CreativeGenerationStepRow)
                .where(
                    CreativeGenerationStepRow.id == step.id,
                    CreativeGenerationStepRow.state_version == expected_state_version,
                )
                .values(**_step_update_values(step))
            ),
        )
        return bool(result.rowcount == 1)

    def claim_step_cas(
        self,
        step: GenerationStep,
        *,
        expected_state_version: int,
    ) -> bool:
        """Claim once and freeze the fully resolved local input snapshot."""

        if step.status is not StepStatus.RUNNING:
            raise ValueError("claimed step 必須是 running")
        if step.state_version != expected_state_version + 1:
            raise ValueError("step state_version 必須恰好遞增 1")
        values = _step_update_values(step)
        values.update(
            {
                "input_snapshot_json": canonical_json(step.input_snapshot),
                "input_snapshot_sha256": step.input_snapshot_sha256,
            }
        )
        result = cast(
            CursorResult[Any],
            self._session.execute(
                update(CreativeGenerationStepRow)
                .where(
                    CreativeGenerationStepRow.id == step.id,
                    CreativeGenerationStepRow.state_version == expected_state_version,
                    CreativeGenerationStepRow.status.in_(
                        (
                            StepStatus.PENDING.value,
                            StepStatus.INTERRUPTED.value,
                            StepStatus.PAUSED.value,
                            StepStatus.FAILED.value,
                            StepStatus.NEEDS_AUTHOR_INPUT.value,
                        )
                    ),
                )
                .values(**values)
            ),
        )
        return bool(result.rowcount == 1)

    def authorize_unit_step_cas(
        self, step: GenerationStep, *, expected_state_version: int,
    ) -> bool:
        """Persist an explicit author decision; caller inserts its receipt in this transaction."""
        if step.state_version != expected_state_version + 1:
            raise ValueError("step state_version must increase exactly once")
        if step.status not in {
            StepStatus.PAUSED, StepStatus.NEEDS_AUTHOR_INPUT, StepStatus.COMPLETED,
        }:
            raise ValueError("author decision cannot claim or execute a provider step")
        values = _step_update_values(step)
        values["max_attempts"] = step.max_attempts
        result = cast(CursorResult[Any], self._session.execute(
            update(CreativeGenerationStepRow).where(
                CreativeGenerationStepRow.id == step.id,
                CreativeGenerationStepRow.state_version == expected_state_version,
                CreativeGenerationStepRow.status == StepStatus.NEEDS_AUTHOR_INPUT.value,
            ).values(**values)
        ))
        return result.rowcount == 1

    def get_step(self, step_id: str) -> GenerationStep | None:
        row = self._session.get(CreativeGenerationStepRow, step_id)
        return _step_domain(row) if row is not None else None

    def get_step_by_idempotency_key(self, idempotency_key: str) -> GenerationStep | None:
        row = self._session.scalar(
            select(CreativeGenerationStepRow).where(
                CreativeGenerationStepRow.idempotency_key == idempotency_key
            )
        )
        return _step_domain(row) if row is not None else None

    def list_steps(
        self,
        job_id: str,
        *,
        statuses: Iterable[StepStatus] | None = None,
    ) -> list[GenerationStep]:
        stmt = select(CreativeGenerationStepRow).where(
            CreativeGenerationStepRow.job_id == job_id
        )
        if statuses is not None:
            values = tuple(status.value for status in statuses)
            if not values:
                return []
            stmt = stmt.where(CreativeGenerationStepRow.status.in_(values))
        stmt = stmt.order_by(CreativeGenerationStepRow.ordinal, CreativeGenerationStepRow.id)
        return [_step_domain(row) for row in self._session.scalars(stmt).all()]


__all__ = ["CreativeGenerationRepository"]
