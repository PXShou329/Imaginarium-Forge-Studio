"""Provider- and Story-aggregate-neutral durable workflow contracts."""

from __future__ import annotations

import re
from collections.abc import Callable
from enum import StrEnum
from threading import Event
from typing import Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

from imaginarium_forge.application.services.generation.length_assurance import (
    ProviderTermination,
)
from imaginarium_forge.domain.generation.durable import (
    ArtifactKind,
    CandidateActionKind,
    CreativeOperation,
    StepKind,
)

_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]*$")
_PROVIDER_RE = re.compile(r"^[a-z0-9][a-z0-9_.:-]*$")
_SAFE_FINISH_REASON_CODES = frozenset(
    {
        "attempts_exhausted_partial",
        "canceled",
        "cancelled",
        "completed",
        "content_filter",
        "deadline_exceeded",
        "deterministic_assembly",
        "end_turn",
        "eos",
        "error",
        "incomplete",
        "interrupted",
        "length",
        "max_output_tokens",
        "max_tokens",
        "needs_author_input",
        "normal_stop",
        "num_predict",
        "offline_fixture",
        "partial_needs_action",
        "paused",
        "provider_partial",
        "provider_stop",
        "repetition_rejected",
        "request_cancelled",
        "stop",
        "streaming",
        "timed_out",
        "timeout",
        "token_limit",
        "tool_calls",
        "transport_interruption",
    }
)
LENGTH_ASSURANCE_STATE_KEY = "_cwe_2_1_length_assurance_state"


def _identifier(value: str, *, field_name: str, max_length: int = 200) -> str:
    normalized = value.strip()
    if not normalized or len(normalized) > max_length:
        raise ValueError(f"{field_name} must be a bounded identifier")
    if not _IDENTIFIER_RE.fullmatch(normalized):
        raise ValueError(f"{field_name} contains unsafe characters")
    return normalized


def _safe_finish_reason(value: object) -> str:
    normalized = value.strip().casefold() if isinstance(value, str) else ""
    return normalized if normalized in _SAFE_FINISH_REASON_CODES else "provider_stop"


class WorkflowUnit(BaseModel):
    """A planning unit; it is not a second formal Story Studio Scene."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    id: str
    title: str = Field(min_length=1, max_length=200)
    target_words: int = Field(ge=1, le=500_000)
    metadata: dict[str, object] = Field(default_factory=dict)

    @field_validator("id")
    @classmethod
    def _id(cls, value: str) -> str:
        return _identifier(value, field_name="workflow unit id")

    @field_validator("title")
    @classmethod
    def _title(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("workflow unit title must not be blank")
        return normalized


class DurableWorkflowRoute(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    provider: str = Field(min_length=1, max_length=64)
    model: str = Field(min_length=1, max_length=256)
    fallback_policy: str = Field(default="fail_closed", min_length=1, max_length=100)
    fallback_provider: str | None = Field(default=None, max_length=64)
    fallback_model: str | None = Field(default=None, max_length=256)

    @field_validator("provider", "fallback_provider")
    @classmethod
    def _provider(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().casefold()
        if not _PROVIDER_RE.fullmatch(normalized):
            raise ValueError("provider must be a lowercase identifier")
        return normalized

    @field_validator("model", "fallback_policy", "fallback_model")
    @classmethod
    def _labels(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized or any(character.isspace() for character in normalized):
            raise ValueError("route values must not contain whitespace")
        return normalized

    @model_validator(mode="after")
    def _fallback_is_explicit(self) -> Self:
        if self.fallback_policy not in {
            "fail_closed",
            "ollama_then_fail",
            "offline_then_fail",
        }:
            raise ValueError("unsupported durable fallback policy")
        if (self.fallback_provider is None) != (self.fallback_model is None):
            raise ValueError("fallback provider/model must appear together")
        if self.fallback_policy == "fail_closed" and self.fallback_provider is not None:
            raise ValueError("fail-closed routes cannot carry a fallback")
        if self.fallback_policy != "fail_closed" and self.fallback_provider is None:
            raise ValueError("fallback policy requires an explicit frozen fallback route")
        return self


class CreateDurableWorkflow(BaseModel):
    """Complete local source/context snapshot used to create one durable job."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    job_id: str
    artifact_kind: ArtifactKind
    operation: CreativeOperation = CreativeOperation.GENERATE
    target_type: str
    target_id: str | None = None
    route: DurableWorkflowRoute
    quality_profile: str = Field(min_length=1, max_length=100)
    length_spec: dict[str, object]
    source_snapshot: dict[str, object]
    context_snapshot: dict[str, object]
    workflow_version: str = Field(default="long-form-v1", min_length=1, max_length=100)
    units: tuple[WorkflowUnit, ...] = Field(min_length=1, max_length=500)
    created_at: str

    @field_validator("job_id")
    @classmethod
    def _job_id(cls, value: str) -> str:
        return _identifier(value, field_name="job id", max_length=160)

    @field_validator("target_type")
    @classmethod
    def _target_type(cls, value: str) -> str:
        return _identifier(value, field_name="target type", max_length=100)

    @field_validator("target_id")
    @classmethod
    def _target_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _identifier(value, field_name="target id")

    @model_validator(mode="after")
    def _unique_units(self) -> Self:
        unit_ids = [unit.id for unit in self.units]
        if len(unit_ids) != len(set(unit_ids)):
            raise ValueError("workflow unit ids must be unique")
        return self


class PartialCheckpoint(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    text: str = Field(min_length=1, max_length=4_000_000)
    stable_boundary_offset: int = Field(ge=0)
    finish_reason: str = Field(default="streaming", max_length=100)

    @field_validator("finish_reason", mode="before")
    @classmethod
    def _finish_reason(cls, value: object) -> str:
        return _safe_finish_reason(value)

    @model_validator(mode="after")
    def _boundary(self) -> Self:
        if self.stable_boundary_offset > len(self.text):
            raise ValueError("stable boundary exceeds checkpoint text")
        return self


class DurableStepRunRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    job_id: str
    step_id: str
    ordinal: int = Field(ge=0)
    step_kind: StepKind
    template_id: str
    template_version: str
    requested_provider: str
    requested_model: str
    fallback_policy: str = "fail_closed"
    fallback_provider: str | None = None
    fallback_model: str | None = None
    attempt_number: int = Field(ge=1)
    input_snapshot: dict[str, object]
    prior_partial_text: str = ""
    stable_boundary_offset: int = Field(default=0, ge=0)


class DurableStepRunResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    output_text: str = Field(default="", max_length=4_000_000)
    output_json: dict[str, object] | None = None
    provider: str = Field(min_length=1, max_length=64)
    model: str = Field(min_length=1, max_length=256)
    fallback_used: bool = False
    finish_reason: str = Field(default="stop", max_length=100)
    prompt_tokens: int | None = Field(default=None, ge=0)
    completion_tokens: int | None = Field(default=None, ge=0)
    latency_ms: int | None = Field(default=None, ge=0)
    provider_termination: ProviderTermination = ProviderTermination.NORMAL_STOP
    estimated_cost_microunits: int = Field(default=0, ge=0)
    runtime_provider_attempts: int = Field(default=0, ge=0, le=100)
    runtime_provider_attempt_ceiling: int = Field(default=0, ge=0, le=100)
    continuation_required: bool = False
    stable_boundary_offset: int = Field(default=0, ge=0)

    @field_validator("provider")
    @classmethod
    def _provider(cls, value: str) -> str:
        normalized = value.strip().casefold()
        if not _PROVIDER_RE.fullmatch(normalized):
            raise ValueError("provider must be a lowercase identifier")
        return normalized

    @field_validator("finish_reason", mode="before")
    @classmethod
    def _finish_reason(cls, value: object) -> str:
        return _safe_finish_reason(value)

    @model_validator(mode="after")
    def _output(self) -> Self:
        if not self.output_text and self.output_json is None:
            raise ValueError("step result must contain text or structured output")
        if self.stable_boundary_offset > len(self.output_text):
            raise ValueError("stable boundary exceeds step output")
        if self.continuation_required:
            if not self.output_text:
                raise ValueError("continuation requires preserved partial text")
        elif self.stable_boundary_offset:
            raise ValueError("only continuation results carry a stable boundary")
        if self.runtime_provider_attempts > self.runtime_provider_attempt_ceiling:
            raise ValueError("runtime provider attempts exceed their saved ceiling")
        return self


CheckpointSink = Callable[[PartialCheckpoint], None]


class DurableStepRunner(Protocol):
    """Potentially slow provider boundary; the executor holds no DB transaction."""

    def run(
        self,
        request: DurableStepRunRequest,
        checkpoint: CheckpointSink,
        *,
        session_override: SecretStr | str | None = None,
        cancel: Event | None = None,
    ) -> DurableStepRunResult: ...


class StepExecutionError(RuntimeError):
    """Safe classified runner failure; raw provider payloads are never persisted."""

    def __init__(
        self,
        *,
        code: str,
        safe_message: str,
        needs_author_input: bool = False,
        discard_partial: bool = False,
    ) -> None:
        super().__init__(safe_message)
        self.code = _identifier(code, field_name="error code", max_length=100)
        self.safe_message = safe_message.strip()[:1000]
        if not self.safe_message:
            raise ValueError("safe_message must not be blank")
        self.needs_author_input = needs_author_input
        self.discard_partial = discard_partial


class ExecutionDisposition(StrEnum):
    STEP_COMPLETED = "step_completed"
    CONTINUATION_REQUIRED = "continuation_required"
    JOB_COMPLETED = "job_completed"
    PAUSED = "paused"
    CANCELLED = "cancelled"
    FAILED = "failed"
    NEEDS_AUTHOR_INPUT = "needs_author_input"
    ALREADY_COMPLETE = "already_complete"


class DurableExecutionResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    job_id: str
    disposition: ExecutionDisposition
    step_id: str | None = None
    candidate_id: str | None = None


class CandidateApplyCommand(BaseModel):
    """Generic boundary command; project adapters own formal aggregate semantics."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    candidate_id: str
    action_kind: CandidateActionKind
    idempotency_key: str
    selection: dict[str, object] = Field(default_factory=dict)
    before_snapshot: dict[str, object]
    base_version_ref: str | None = None
    undo_of_action_id: str | None = None
    created_at: str

    @field_validator("candidate_id", "idempotency_key")
    @classmethod
    def _ids(cls, value: str) -> str:
        return _identifier(value, field_name="candidate apply identifier")


class ProjectApplyRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    candidate_id: str
    action_kind: CandidateActionKind
    idempotency_key: str
    target_type: str
    target_id: str | None
    selection: dict[str, object]
    candidate_text: str
    candidate_json: dict[str, object] | None
    before_snapshot: dict[str, object]
    base_version_ref: str | None
    undo_of_action_id: str | None
    durable_candidate_snapshot: dict[str, object] | None = None
    durable_job_snapshot: dict[str, object] | None = None
    durable_step_snapshot: dict[str, object] | None = None


class ProjectApplyResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    after_snapshot: dict[str, object]
    result_version_ref: str

    @field_validator("result_version_ref")
    @classmethod
    def _version(cls, value: str) -> str:
        return _identifier(value, field_name="result version")


class ProjectArtifactAdapter(Protocol):
    """Adapter to existing Story/character/world contracts; never a new aggregate."""

    def apply_candidate(self, request: ProjectApplyRequest) -> ProjectApplyResult: ...


__all__ = [
    "LENGTH_ASSURANCE_STATE_KEY",
    "CandidateApplyCommand",
    "CheckpointSink",
    "CreateDurableWorkflow",
    "DurableExecutionResult",
    "DurableStepRunRequest",
    "DurableStepRunResult",
    "DurableStepRunner",
    "DurableWorkflowRoute",
    "ExecutionDisposition",
    "PartialCheckpoint",
    "ProjectApplyRequest",
    "ProjectApplyResult",
    "ProjectArtifactAdapter",
    "StepExecutionError",
    "WorkflowUnit",
]
