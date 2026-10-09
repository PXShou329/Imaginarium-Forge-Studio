"""Durable Creative Writing Engine job, step, candidate, and undo contracts.

The database is the workflow source of truth. These models contain complete
local snapshots and hashes; a remote response or conversation identifier is
never required to resume work, and credentials are rejected from JSON fields.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Mapping, Sequence
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from imaginarium_forge.canonical import canonical_json

_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]*$")
_PROVIDER_RE = re.compile(r"^[a-z0-9][a-z0-9_.:-]*$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_FORBIDDEN_CREDENTIAL_KEYS = frozenset(
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
    }
)


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sha256_json(value: object) -> str:
    return sha256_text(canonical_json(value))


def sha256_content(*, text: str, structured: object | None) -> str:
    """Hash the unambiguous text/structured content envelope."""

    return sha256_json({"structured": structured, "text": text})


def _json_value(value: object, *, path: str) -> object:
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{path} 不可包含 NaN 或 Infinity")
        return value
    if isinstance(value, Mapping):
        result: dict[str, object] = {}
        for raw_key, child in value.items():
            if not isinstance(raw_key, str):
                raise ValueError(f"{path} 的 JSON key 必須是文字")
            key = raw_key.strip()
            if not key:
                raise ValueError(f"{path} 的 JSON key 不可留白")
            if key.casefold() in _FORBIDDEN_CREDENTIAL_KEYS:
                raise ValueError(f"{path} 不可包含 credential 欄位")
            result[key] = _json_value(child, path=f"{path}.{key}")
        return result
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_json_value(child, path=f"{path}[]") for child in value]
    raise ValueError(f"{path} 只能包含 JSON-compatible values")


def _json_object(value: object, *, field_name: str) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} 必須是 JSON object")
    normalized = _json_value(value, path=field_name)
    if not isinstance(normalized, dict):  # pragma: no cover - Mapping root guarantees it
        raise ValueError(f"{field_name} 必須是 JSON object")
    return normalized


def _optional_json_object(value: object, *, field_name: str) -> dict[str, object] | None:
    if value is None:
        return None
    return _json_object(value, field_name=field_name)


def _identifier(value: str, *, field_name: str, max_length: int = 200) -> str:
    normalized = value.strip()
    if not normalized or len(normalized) > max_length or not _IDENTIFIER_RE.fullmatch(normalized):
        raise ValueError(f"{field_name} 必須是長度 1–{max_length} 的穩定識別字")
    return normalized


def _optional_identifier(
    value: str | None,
    *,
    field_name: str,
    max_length: int = 200,
) -> str | None:
    if value is None:
        return None
    return _identifier(value, field_name=field_name, max_length=max_length)


def _provider_id(value: str | None, *, field_name: str) -> str | None:
    if value is None:
        return None
    normalized = value.strip().casefold()
    if not normalized or len(normalized) > 64 or not _PROVIDER_RE.fullmatch(normalized):
        raise ValueError(f"{field_name} 必須是 provider 識別字")
    return normalized


def _model_id(value: str | None, *, field_name: str) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized or len(normalized) > 256:
        raise ValueError(f"{field_name} 必須是長度 1–256 的模型識別字")
    if any(character.isspace() or ord(character) < 32 for character in normalized):
        raise ValueError(f"{field_name} 不可包含空白或控制字元")
    return normalized


def _sha256(value: str, *, field_name: str, allow_blank: bool = False) -> str:
    normalized = value.strip().casefold()
    if allow_blank and not normalized:
        return ""
    if not _SHA256_RE.fullmatch(normalized):
        raise ValueError(f"{field_name} 必須是 64 字元小寫 SHA-256")
    return normalized


def _bounded_text(value: str, *, field_name: str, max_length: int) -> str:
    normalized = value.strip()
    if len(normalized) > max_length or any(ord(character) < 32 for character in normalized):
        raise ValueError(f"{field_name} 格式無效或超過 {max_length} 字元")
    return normalized


class ArtifactKind(StrEnum):
    STORY_FRAGMENT = "story_fragment"
    SHORT_STORY = "short_story"
    LONG_STORY = "long_story"
    CHAPTER = "chapter"
    SCREENPLAY = "screenplay"
    CHARACTER_CARD = "character_card"
    CHARACTER_BACKSTORY = "character_backstory"
    WORLD_SEED = "world_seed"
    IMAGE_PROMPT = "image_prompt"


class CreativeOperation(StrEnum):
    GENERATE = "generate"
    CONTINUE = "continue"
    FILL_BLANKS = "fill_blanks"
    EXPAND = "expand"
    REWRITE = "rewrite"
    POLISH = "polish"
    CONDENSE = "condense"
    CRITIQUE = "critique"
    FIX_CONTINUITY = "fix_continuity"
    IMPROVE_DIALOGUE = "improve_dialogue"
    ADAPT = "adapt"


class JobStatus(StrEnum):
    QUEUED = "queued"
    NORMALIZING_BRIEF = "normalizing_brief"
    PLANNING = "planning"
    DRAFTING = "drafting"
    REVIEWING = "reviewing"
    REVISING = "revising"
    ASSEMBLING = "assembling"
    COMPLETED = "completed"
    PAUSED = "paused"
    CANCELLED = "cancelled"
    FAILED = "failed"
    NEEDS_AUTHOR_INPUT = "needs_author_input"


ACTIVE_JOB_STATUSES = frozenset(
    {
        JobStatus.NORMALIZING_BRIEF,
        JobStatus.PLANNING,
        JobStatus.DRAFTING,
        JobStatus.REVIEWING,
        JobStatus.REVISING,
        JobStatus.ASSEMBLING,
    }
)


class StepKind(StrEnum):
    BRIEF_NORMALIZATION = "brief_normalization"
    STORY_PLAN = "story_plan"
    CHARACTER_ARC_PLAN = "character_arc_plan"
    CHAPTER_OR_SCENE_PLAN = "chapter_or_scene_plan"
    PROSE_CHUNK = "prose_chunk"
    ROLLING_SUMMARY = "rolling_summary"
    CONTINUITY_CHECK = "continuity_check"
    TARGETED_REVISION = "targeted_revision"
    LENGTH_ASSURANCE = "length_assurance"
    FINAL_ASSEMBLY = "final_assembly"
    FINAL_QUALITY_CHECK = "final_quality_check"


class StepStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    INTERRUPTED = "interrupted"
    PAUSED = "paused"
    CANCELLED = "cancelled"
    FAILED = "failed"
    NEEDS_AUTHOR_INPUT = "needs_author_input"


class AttemptStatus(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    INTERRUPTED = "interrupted"
    CANCELLED = "cancelled"


class CandidateStatus(StrEnum):
    PROPOSED = "proposed"
    APPLIED = "applied"
    REJECTED = "rejected"
    ARCHIVED = "archived"


class CandidateActionKind(StrEnum):
    APPLY_ALL = "apply_all"
    APPLY_FIELD = "apply_field"
    APPLY_SELECTION = "apply_selection"
    SAVE_AS_NEW = "save_as_new"
    UNDO = "undo"


class CandidateActionStatus(StrEnum):
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"


_JOB_TRANSITIONS: dict[JobStatus, frozenset[JobStatus]] = {
    JobStatus.QUEUED: frozenset(
        {JobStatus.NORMALIZING_BRIEF, JobStatus.CANCELLED, JobStatus.FAILED}
    ),
    JobStatus.NORMALIZING_BRIEF: frozenset(
        {
            JobStatus.PLANNING,
            JobStatus.PAUSED,
            JobStatus.CANCELLED,
            JobStatus.FAILED,
            JobStatus.NEEDS_AUTHOR_INPUT,
        }
    ),
    JobStatus.PLANNING: frozenset(
        {
            JobStatus.DRAFTING,
            JobStatus.PAUSED,
            JobStatus.CANCELLED,
            JobStatus.FAILED,
            JobStatus.NEEDS_AUTHOR_INPUT,
        }
    ),
    JobStatus.DRAFTING: frozenset(
        {
            JobStatus.REVIEWING,
            JobStatus.PAUSED,
            JobStatus.CANCELLED,
            JobStatus.FAILED,
            JobStatus.NEEDS_AUTHOR_INPUT,
        }
    ),
    JobStatus.REVIEWING: frozenset(
        {
            JobStatus.REVISING,
            JobStatus.ASSEMBLING,
            JobStatus.PAUSED,
            JobStatus.CANCELLED,
            JobStatus.FAILED,
            JobStatus.NEEDS_AUTHOR_INPUT,
        }
    ),
    JobStatus.REVISING: frozenset(
        {
            JobStatus.DRAFTING,
            JobStatus.REVIEWING,
            JobStatus.ASSEMBLING,
            JobStatus.PAUSED,
            JobStatus.CANCELLED,
            JobStatus.FAILED,
            JobStatus.NEEDS_AUTHOR_INPUT,
        }
    ),
    JobStatus.ASSEMBLING: frozenset(
        {
            JobStatus.COMPLETED,
            JobStatus.REVIEWING,
            JobStatus.PAUSED,
            JobStatus.CANCELLED,
            JobStatus.FAILED,
            JobStatus.NEEDS_AUTHOR_INPUT,
        }
    ),
    JobStatus.PAUSED: ACTIVE_JOB_STATUSES | {JobStatus.CANCELLED, JobStatus.FAILED},
    JobStatus.FAILED: ACTIVE_JOB_STATUSES | {JobStatus.CANCELLED},
    JobStatus.NEEDS_AUTHOR_INPUT: ACTIVE_JOB_STATUSES | {
        JobStatus.CANCELLED,
        JobStatus.FAILED,
    },
    JobStatus.COMPLETED: frozenset(),
    JobStatus.CANCELLED: frozenset(),
}

_STEP_TRANSITIONS: dict[StepStatus, frozenset[StepStatus]] = {
    StepStatus.PENDING: frozenset({StepStatus.RUNNING, StepStatus.CANCELLED}),
    StepStatus.RUNNING: frozenset(
        {
            StepStatus.COMPLETED,
            StepStatus.INTERRUPTED,
            StepStatus.PAUSED,
            StepStatus.CANCELLED,
            StepStatus.FAILED,
            StepStatus.NEEDS_AUTHOR_INPUT,
        }
    ),
    StepStatus.INTERRUPTED: frozenset(
        {StepStatus.RUNNING, StepStatus.CANCELLED, StepStatus.FAILED}
    ),
    StepStatus.PAUSED: frozenset({StepStatus.RUNNING, StepStatus.CANCELLED}),
    StepStatus.FAILED: frozenset({StepStatus.RUNNING, StepStatus.CANCELLED}),
    StepStatus.NEEDS_AUTHOR_INPUT: frozenset(
        {StepStatus.RUNNING, StepStatus.CANCELLED, StepStatus.FAILED}
    ),
    StepStatus.COMPLETED: frozenset(),
    StepStatus.CANCELLED: frozenset(),
}


def job_transition_allowed(current: JobStatus, target: JobStatus) -> bool:
    return target in _JOB_TRANSITIONS[current]


def step_transition_allowed(current: StepStatus, target: StepStatus) -> bool:
    return target in _STEP_TRANSITIONS[current]


class GenerationJob(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    id: str
    artifact_kind: ArtifactKind
    operation: CreativeOperation
    target_type: str
    target_id: str | None = None
    status: JobStatus = JobStatus.QUEUED
    resume_phase: JobStatus | None = None
    route: dict[str, object]
    quality_profile: str
    length_spec: dict[str, object]
    source_snapshot: dict[str, object]
    source_snapshot_sha256: str
    context_snapshot: dict[str, object]
    context_snapshot_sha256: str
    workflow_version: str
    current_step_index: int = Field(default=0, ge=0)
    total_steps_hint: int | None = Field(default=None, ge=1)
    last_checkpoint_step_id: str | None = None
    checkpoint_sequence: int = Field(default=0, ge=0)
    pause_requested: bool = False
    cancel_requested: bool = False
    resume_requested: bool = False
    recovery_count: int = Field(default=0, ge=0)
    state_version: int = Field(default=1, ge=1)
    error_code: str = ""
    safe_error_message: str = ""
    created_at: str
    started_at: str | None = None
    updated_at: str
    heartbeat_at: str | None = None
    last_checkpoint_at: str | None = None
    completed_at: str | None = None

    @field_validator("id")
    @classmethod
    def _id(cls, value: str) -> str:
        return _identifier(value, field_name="job id")

    @field_validator("target_type")
    @classmethod
    def _target_type(cls, value: str) -> str:
        return _identifier(value, field_name="target type", max_length=100)

    @field_validator("target_id")
    @classmethod
    def _target_id(cls, value: str | None) -> str | None:
        return _optional_identifier(value, field_name="target id")

    @field_validator("route", mode="before")
    @classmethod
    def _route(cls, value: object) -> object:
        return _json_object(value, field_name="route")

    @field_validator("length_spec", mode="before")
    @classmethod
    def _length_spec(cls, value: object) -> object:
        return _json_object(value, field_name="length_spec")

    @field_validator("source_snapshot", mode="before")
    @classmethod
    def _source_snapshot(cls, value: object) -> object:
        return _json_object(value, field_name="source_snapshot")

    @field_validator("context_snapshot", mode="before")
    @classmethod
    def _context_snapshot(cls, value: object) -> object:
        return _json_object(value, field_name="context_snapshot")

    @field_validator("source_snapshot_sha256", "context_snapshot_sha256")
    @classmethod
    def _snapshot_hash(cls, value: str) -> str:
        return _sha256(value, field_name="snapshot sha256")

    @field_validator("quality_profile", "workflow_version")
    @classmethod
    def _required_label(cls, value: str) -> str:
        normalized = _bounded_text(value, field_name="workflow label", max_length=100)
        if not normalized:
            raise ValueError("workflow label 不可留白")
        return normalized

    @field_validator("last_checkpoint_step_id")
    @classmethod
    def _checkpoint_step(cls, value: str | None) -> str | None:
        return _optional_identifier(value, field_name="checkpoint step id")

    @field_validator("error_code")
    @classmethod
    def _error_code(cls, value: str) -> str:
        return _bounded_text(value, field_name="error code", max_length=100)

    @field_validator("safe_error_message")
    @classmethod
    def _safe_error(cls, value: str) -> str:
        return _bounded_text(value, field_name="safe error", max_length=1000)

    @model_validator(mode="after")
    def _coherent_state(self) -> Self:
        if self.source_snapshot_sha256 != sha256_json(self.source_snapshot):
            raise ValueError("source snapshot hash 不一致")
        if self.context_snapshot_sha256 != sha256_json(self.context_snapshot):
            raise ValueError("context snapshot hash 不一致")
        if self.total_steps_hint is not None and self.current_step_index > self.total_steps_hint:
            raise ValueError("current step 不可超過 total steps hint")
        if self.last_checkpoint_step_id is None:
            if self.checkpoint_sequence or self.last_checkpoint_at is not None:
                raise ValueError("checkpoint metadata 必須一起出現")
        elif self.checkpoint_sequence < 1 or self.last_checkpoint_at is None:
            raise ValueError("checkpoint 必須包含 sequence 與時間")
        if sum((self.pause_requested, self.cancel_requested, self.resume_requested)) > 1:
            raise ValueError("pause/cancel/resume request 不可同時成立")

        if self.status is JobStatus.QUEUED:
            if self.started_at is not None or self.completed_at is not None:
                raise ValueError("queued job 不可已有開始或完成時間")
        elif self.status in ACTIVE_JOB_STATUSES:
            if self.started_at is None or self.completed_at is not None:
                raise ValueError("active job 必須已開始且尚未結束")
        elif self.status is JobStatus.PAUSED:
            if self.started_at is None or self.completed_at is not None:
                raise ValueError("paused job 必須保留開始時間且尚未結束")
            if self.resume_phase not in ACTIVE_JOB_STATUSES:
                raise ValueError("paused job 必須保存可恢復 phase")
            if not (self.pause_requested or self.resume_requested):
                raise ValueError("paused job 必須保存 pause 或 resume request")
        elif self.status is JobStatus.NEEDS_AUTHOR_INPUT:
            if self.started_at is None or self.completed_at is not None:
                raise ValueError("needs-author-input job 必須尚未結束")
            if self.resume_phase not in ACTIVE_JOB_STATUSES:
                raise ValueError("needs-author-input job 必須保存恢復 phase")
        elif self.status is JobStatus.COMPLETED:
            if self.started_at is None or self.completed_at is None:
                raise ValueError("completed job 必須有開始與完成時間")
            if any((self.pause_requested, self.cancel_requested, self.resume_requested)):
                raise ValueError("completed job 不可保留控制 request")
        elif self.status is JobStatus.CANCELLED:
            if self.completed_at is None or not self.cancel_requested:
                raise ValueError("cancelled job 必須保存 cancel request 與終止時間")
        elif self.status is JobStatus.FAILED:
            if self.completed_at is None or not self.error_code or not self.safe_error_message:
                raise ValueError("failed job 必須保存安全錯誤與終止時間")
            if self.resume_phase not in ACTIVE_JOB_STATUSES:
                raise ValueError("failed job 必須保存可重試 phase")

        if self.status is not JobStatus.FAILED and (self.error_code or self.safe_error_message):
            raise ValueError("只有 failed job 可保存 job error")
        return self


class GenerationStepAttempt(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    attempt_number: int = Field(ge=1)
    status: AttemptStatus
    requested_provider: str | None = None
    requested_model: str | None = None
    provider: str | None = None
    model: str | None = None
    fallback_used: bool = False
    finish_reason: str = ""
    error_code: str = ""
    safe_error_message: str = ""
    prompt_tokens: int | None = Field(default=None, ge=0)
    completion_tokens: int | None = Field(default=None, ge=0)
    latency_ms: int | None = Field(default=None, ge=0)
    started_at: str
    completed_at: str | None = None

    @field_validator("requested_provider", "provider")
    @classmethod
    def _providers(cls, value: str | None) -> str | None:
        return _provider_id(value, field_name="provider")

    @field_validator("requested_model", "model")
    @classmethod
    def _models(cls, value: str | None) -> str | None:
        return _model_id(value, field_name="model")

    @field_validator("finish_reason", "error_code")
    @classmethod
    def _codes(cls, value: str) -> str:
        return _bounded_text(value, field_name="attempt code", max_length=100)

    @field_validator("safe_error_message")
    @classmethod
    def _error(cls, value: str) -> str:
        return _bounded_text(value, field_name="attempt safe error", max_length=1000)

    @model_validator(mode="after")
    def _coherent_attempt(self) -> Self:
        if (self.requested_provider is None) != (self.requested_model is None):
            raise ValueError("requested provider/model 必須一起出現")
        if (self.provider is None) != (self.model is None):
            raise ValueError("actual provider/model 必須一起出現")
        if self.fallback_used:
            requested = (self.requested_provider, self.requested_model)
            actual = (self.provider, self.model)
            if None in requested or None in actual or requested == actual:
                raise ValueError("fallback attempt 必須保存不同 requested/actual route")
        if self.status is AttemptStatus.RUNNING:
            if self.completed_at is not None or self.error_code or self.safe_error_message:
                raise ValueError("running attempt 尚不可有完成時間或錯誤")
        else:
            if self.completed_at is None:
                raise ValueError("terminal attempt 必須有完成時間")
        if self.status is AttemptStatus.FAILED:
            if not self.error_code or not self.safe_error_message:
                raise ValueError("failed attempt 必須保存安全錯誤")
        elif self.status is AttemptStatus.COMPLETED:
            if self.provider is None or self.model is None:
                raise ValueError("completed attempt 必須保存實際 provider/model")
        elif self.error_code or self.safe_error_message:
            raise ValueError("只有 failed attempt 可保存錯誤")
        return self


class GenerationStep(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    id: str
    job_id: str
    ordinal: int = Field(ge=0)
    step_kind: StepKind
    status: StepStatus = StepStatus.PENDING
    idempotency_key: str
    template_id: str
    template_version: str
    requested_provider: str | None = None
    requested_model: str | None = None
    provider: str | None = None
    model: str | None = None
    fallback_used: bool = False
    input_snapshot: dict[str, object]
    input_snapshot_sha256: str
    output_text: str = ""
    output_json: dict[str, object] | None = None
    output_sha256: str = ""
    partial: bool = False
    partial_text: str = ""
    partial_sha256: str = ""
    stable_boundary_offset: int = Field(default=0, ge=0)
    finish_reason: str = ""
    checkpoint_index: int = Field(default=0, ge=0)
    prompt_tokens: int | None = Field(default=None, ge=0)
    completion_tokens: int | None = Field(default=None, ge=0)
    latency_ms: int | None = Field(default=None, ge=0)
    attempts: int = Field(default=0, ge=0)
    max_attempts: int = Field(default=2, ge=1, le=10)
    attempt_history: tuple[GenerationStepAttempt, ...] = ()
    error_code: str = ""
    safe_error_message: str = ""
    state_version: int = Field(default=1, ge=1)
    created_at: str
    started_at: str | None = None
    updated_at: str
    checkpoint_at: str | None = None
    interrupted_at: str | None = None
    completed_at: str | None = None

    @field_validator("id", "job_id", "idempotency_key")
    @classmethod
    def _ids(cls, value: str) -> str:
        return _identifier(value, field_name="step identifier")

    @field_validator("template_id", "template_version")
    @classmethod
    def _template(cls, value: str) -> str:
        normalized = _bounded_text(value, field_name="template", max_length=200)
        if not normalized:
            raise ValueError("每個 step 都必須保存 template id/version")
        return normalized

    @field_validator("requested_provider", "provider")
    @classmethod
    def _providers(cls, value: str | None) -> str | None:
        return _provider_id(value, field_name="provider")

    @field_validator("requested_model", "model")
    @classmethod
    def _models(cls, value: str | None) -> str | None:
        return _model_id(value, field_name="model")

    @field_validator("input_snapshot", mode="before")
    @classmethod
    def _input_snapshot(cls, value: object) -> object:
        return _json_object(value, field_name="input_snapshot")

    @field_validator("output_json", mode="before")
    @classmethod
    def _output_json(cls, value: object) -> object:
        return _optional_json_object(value, field_name="output_json")

    @field_validator("input_snapshot_sha256")
    @classmethod
    def _input_hash(cls, value: str) -> str:
        return _sha256(value, field_name="input snapshot sha256")

    @field_validator("output_sha256", "partial_sha256")
    @classmethod
    def _optional_hash(cls, value: str) -> str:
        return _sha256(value, field_name="content sha256", allow_blank=True)

    @field_validator("finish_reason", "error_code")
    @classmethod
    def _codes(cls, value: str) -> str:
        return _bounded_text(value, field_name="step code", max_length=100)

    @field_validator("safe_error_message")
    @classmethod
    def _safe_error(cls, value: str) -> str:
        return _bounded_text(value, field_name="step safe error", max_length=1000)

    @model_validator(mode="after")
    def _coherent_step(self) -> Self:
        if self.input_snapshot_sha256 != sha256_json(self.input_snapshot):
            raise ValueError("step input snapshot hash 不一致")
        if (self.requested_provider is None) != (self.requested_model is None):
            raise ValueError("requested provider/model 必須一起出現")
        if (self.provider is None) != (self.model is None):
            raise ValueError("actual provider/model 必須一起出現")
        if self.fallback_used:
            requested = (self.requested_provider, self.requested_model)
            actual = (self.provider, self.model)
            if None in requested or None in actual or requested == actual:
                raise ValueError("fallback step 必須保存不同 requested/actual route")

        has_output = bool(self.output_text) or self.output_json is not None
        expected_output_hash = sha256_content(
            text=self.output_text,
            structured=self.output_json,
        )
        if has_output and self.output_sha256 != expected_output_hash:
            raise ValueError("step output hash 不一致")
        if not has_output and self.output_sha256:
            raise ValueError("沒有 output 時不可保存 output hash")

        if self.partial:
            if not self.partial_text or self.partial_sha256 != sha256_text(self.partial_text):
                raise ValueError("partial 必須保存文字與相符 hash")
            if self.stable_boundary_offset > len(self.partial_text):
                raise ValueError("stable boundary 不可超過 partial 長度")
        elif self.partial_text or self.partial_sha256 or self.stable_boundary_offset:
            raise ValueError("非 partial step 不可保存 partial buffer")

        if self.attempts != len(self.attempt_history):
            raise ValueError("attempts 必須等於 attempt history 長度")
        if self.attempts > self.max_attempts:
            raise ValueError("attempts 不可超過 max attempts")
        if tuple(item.attempt_number for item in self.attempt_history) != tuple(
            range(1, self.attempts + 1)
        ):
            raise ValueError("attempt history 必須從 1 連續編號")
        if self.checkpoint_index:
            if self.checkpoint_at is None:
                raise ValueError("checkpoint index 必須有 checkpoint time")
        elif self.checkpoint_at is not None:
            raise ValueError("checkpoint time 必須有 checkpoint index")

        if self.status is StepStatus.PENDING:
            if self.started_at is not None or self.attempts or has_output or self.partial:
                raise ValueError("pending step 不可已有執行結果")
        elif self.status is StepStatus.RUNNING:
            if self.started_at is None or self.completed_at is not None:
                raise ValueError("running step 必須已開始且未完成")
            if (
                not self.attempt_history
                or self.attempt_history[-1].status is not AttemptStatus.RUNNING
            ):
                raise ValueError("running step 必須有 running attempt")
        elif self.status is StepStatus.COMPLETED:
            if self.started_at is None or self.completed_at is None or not has_output:
                raise ValueError("completed step 必須有完整 output 與時間")
            if self.provider is None or self.model is None:
                raise ValueError("completed step 必須保存實際 provider/model")
            if self.partial or self.interrupted_at is not None:
                raise ValueError("completed step 不可是 partial/interrupted")
            if (
                not self.attempt_history
                or self.attempt_history[-1].status is not AttemptStatus.COMPLETED
            ):
                raise ValueError("completed step 最後一次 attempt 必須完成")
        elif self.status is StepStatus.INTERRUPTED:
            if (
                self.started_at is None
                or self.interrupted_at is None
                or self.completed_at is not None
            ):
                raise ValueError("interrupted step 必須保存中斷時間且未完成")
            if (
                not self.attempt_history
                or self.attempt_history[-1].status is not AttemptStatus.INTERRUPTED
            ):
                raise ValueError("interrupted step 最後一次 attempt 必須中斷")
        elif self.status in {StepStatus.CANCELLED, StepStatus.FAILED}:
            if self.completed_at is None:
                raise ValueError("terminal step 必須保存完成時間")
        elif self.status in {
            StepStatus.PAUSED,
            StepStatus.NEEDS_AUTHOR_INPUT,
        } and (self.started_at is None or self.completed_at is not None):
            raise ValueError("suspended step 必須已開始且尚未完成")

        if self.status is StepStatus.FAILED:
            if not self.error_code or not self.safe_error_message:
                raise ValueError("failed step 必須保存安全錯誤")
            if (
                not self.attempt_history
                or self.attempt_history[-1].status is not AttemptStatus.FAILED
            ):
                raise ValueError("failed step 最後一次 attempt 必須失敗")
        elif self.status is StepStatus.CANCELLED:
            if (
                not self.attempt_history
                or self.attempt_history[-1].status is not AttemptStatus.CANCELLED
            ):
                raise ValueError("cancelled step 最後一次 attempt 必須取消")
        elif self.error_code or self.safe_error_message:
            raise ValueError("只有 failed step 可保存 step error")
        return self


class CandidateProvenance(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    job_id: str
    step_id: str
    workflow_version: str
    template_id: str
    template_version: str
    requested_provider: str | None = None
    requested_model: str | None = None
    provider: str | None = None
    model: str | None = None
    fallback_used: bool = False
    attempt_number: int = Field(ge=1)
    input_snapshot_sha256: str
    output_sha256: str

    @field_validator("job_id", "step_id")
    @classmethod
    def _ids(cls, value: str) -> str:
        return _identifier(value, field_name="provenance id")

    @field_validator("workflow_version", "template_id", "template_version")
    @classmethod
    def _labels(cls, value: str) -> str:
        normalized = _bounded_text(value, field_name="provenance label", max_length=200)
        if not normalized:
            raise ValueError("provenance label 不可留白")
        return normalized

    @field_validator("requested_provider", "provider")
    @classmethod
    def _providers(cls, value: str | None) -> str | None:
        return _provider_id(value, field_name="provider")

    @field_validator("requested_model", "model")
    @classmethod
    def _models(cls, value: str | None) -> str | None:
        return _model_id(value, field_name="model")

    @field_validator("input_snapshot_sha256", "output_sha256")
    @classmethod
    def _hashes(cls, value: str) -> str:
        return _sha256(value, field_name="provenance sha256")

    @model_validator(mode="after")
    def _routes(self) -> Self:
        if (self.requested_provider is None) != (self.requested_model is None):
            raise ValueError("requested provenance route 不完整")
        if (self.provider is None) != (self.model is None):
            raise ValueError("actual provenance route 不完整")
        if self.provider is None or self.model is None:
            raise ValueError("candidate provenance 必須保存實際 provider/model")
        if self.fallback_used:
            requested = (self.requested_provider, self.requested_model)
            actual = (self.provider, self.model)
            if None in requested or None in actual or requested == actual:
                raise ValueError("fallback provenance 必須保存不同 routes")
        return self


class GenerationCandidate(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    id: str
    job_id: str
    producing_step_id: str
    artifact_kind: ArtifactKind
    target_type: str
    target_id: str | None = None
    parent_candidate_id: str | None = None
    candidate_version: int = Field(ge=1)
    original_version_ref: str | None = None
    base_snapshot: dict[str, object]
    base_snapshot_sha256: str
    content_text: str = ""
    content_json: dict[str, object] | None = None
    content_sha256: str
    quality_report: dict[str, object]
    provenance: CandidateProvenance
    status: CandidateStatus = CandidateStatus.PROPOSED
    state_version: int = Field(default=1, ge=1)
    created_at: str
    decided_at: str | None = None

    @field_validator("id", "job_id", "producing_step_id")
    @classmethod
    def _ids(cls, value: str) -> str:
        return _identifier(value, field_name="candidate id")

    @field_validator("target_type")
    @classmethod
    def _target_type(cls, value: str) -> str:
        return _identifier(value, field_name="candidate target type", max_length=100)

    @field_validator("target_id", "parent_candidate_id", "original_version_ref")
    @classmethod
    def _optional_ids(cls, value: str | None) -> str | None:
        return _optional_identifier(value, field_name="candidate reference")

    @field_validator("base_snapshot", mode="before")
    @classmethod
    def _base(cls, value: object) -> object:
        return _json_object(value, field_name="candidate base snapshot")

    @field_validator("content_json", mode="before")
    @classmethod
    def _content_json(cls, value: object) -> object:
        return _optional_json_object(value, field_name="candidate content")

    @field_validator("quality_report", mode="before")
    @classmethod
    def _quality(cls, value: object) -> object:
        return _json_object(value, field_name="quality report")

    @field_validator("base_snapshot_sha256", "content_sha256")
    @classmethod
    def _hashes(cls, value: str) -> str:
        return _sha256(value, field_name="candidate sha256")

    @model_validator(mode="after")
    def _coherent_candidate(self) -> Self:
        if (
            self.provenance.job_id != self.job_id
            or self.provenance.step_id != self.producing_step_id
        ):
            raise ValueError("candidate provenance 必須指向同一 job/step")
        if self.base_snapshot_sha256 != sha256_json(self.base_snapshot):
            raise ValueError("candidate base snapshot hash 不一致")
        if not self.content_text and self.content_json is None:
            raise ValueError("candidate 必須保存 text 或 structured content")
        if self.content_sha256 != sha256_content(
            text=self.content_text,
            structured=self.content_json,
        ):
            raise ValueError("candidate content hash 不一致")
        if self.provenance.output_sha256 != self.content_sha256:
            raise ValueError("candidate provenance output hash 不一致")
        if self.status is CandidateStatus.PROPOSED:
            if self.decided_at is not None:
                raise ValueError("proposed candidate 尚不可有 decision time")
        elif self.decided_at is None:
            raise ValueError("decided candidate 必須保存 decision time")
        return self


class CandidateAction(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    id: str
    candidate_id: str
    action_kind: CandidateActionKind
    status: CandidateActionStatus = CandidateActionStatus.PENDING
    idempotency_key: str
    target_type: str
    target_id: str | None = None
    selection: dict[str, object]
    before_snapshot: dict[str, object]
    before_snapshot_sha256: str
    after_snapshot: dict[str, object] | None = None
    after_snapshot_sha256: str = ""
    base_version_ref: str | None = None
    result_version_ref: str | None = None
    undo_of_action_id: str | None = None
    error_code: str = ""
    safe_error_message: str = ""
    created_at: str
    completed_at: str | None = None

    @field_validator("id", "candidate_id", "idempotency_key")
    @classmethod
    def _ids(cls, value: str) -> str:
        return _identifier(value, field_name="candidate action id")

    @field_validator("target_type")
    @classmethod
    def _target_type(cls, value: str) -> str:
        return _identifier(value, field_name="candidate action target", max_length=100)

    @field_validator(
        "target_id",
        "base_version_ref",
        "result_version_ref",
        "undo_of_action_id",
    )
    @classmethod
    def _references(cls, value: str | None) -> str | None:
        return _optional_identifier(value, field_name="candidate action reference")

    @field_validator("selection", mode="before")
    @classmethod
    def _selection(cls, value: object) -> object:
        return _json_object(value, field_name="candidate selection")

    @field_validator("before_snapshot", mode="before")
    @classmethod
    def _before(cls, value: object) -> object:
        return _json_object(value, field_name="before snapshot")

    @field_validator("after_snapshot", mode="before")
    @classmethod
    def _after(cls, value: object) -> object:
        return _optional_json_object(value, field_name="after snapshot")

    @field_validator("before_snapshot_sha256")
    @classmethod
    def _before_hash(cls, value: str) -> str:
        return _sha256(value, field_name="before snapshot sha256")

    @field_validator("after_snapshot_sha256")
    @classmethod
    def _after_hash(cls, value: str) -> str:
        return _sha256(value, field_name="after snapshot sha256", allow_blank=True)

    @field_validator("error_code")
    @classmethod
    def _error_code(cls, value: str) -> str:
        return _bounded_text(value, field_name="candidate action error code", max_length=100)

    @field_validator("safe_error_message")
    @classmethod
    def _safe_error(cls, value: str) -> str:
        return _bounded_text(value, field_name="candidate action safe error", max_length=1000)

    @model_validator(mode="after")
    def _coherent_action(self) -> Self:
        if self.before_snapshot_sha256 != sha256_json(self.before_snapshot):
            raise ValueError("before snapshot hash 不一致")
        if self.action_kind is CandidateActionKind.UNDO:
            if self.undo_of_action_id is None:
                raise ValueError("undo action 必須指向先前 apply action")
        elif self.undo_of_action_id is not None:
            raise ValueError("只有 undo action 可設定 undo reference")
        if self.action_kind in {
            CandidateActionKind.APPLY_FIELD,
            CandidateActionKind.APPLY_SELECTION,
        }:
            if not self.selection:
                raise ValueError("field/selection apply 必須保存 selection")
        elif self.selection:
            raise ValueError("只有 field/selection apply 可保存 selection")

        if self.status is CandidateActionStatus.PENDING:
            if self.completed_at is not None or self.after_snapshot is not None:
                raise ValueError("pending action 尚不可有完成結果")
            if self.error_code or self.safe_error_message:
                raise ValueError("pending action 不可有錯誤")
        elif self.status is CandidateActionStatus.COMPLETED:
            if self.completed_at is None or self.after_snapshot is None:
                raise ValueError("completed action 必須保存 after snapshot 與時間")
            if self.after_snapshot_sha256 != sha256_json(self.after_snapshot):
                raise ValueError("after snapshot hash 不一致")
            if not self.result_version_ref:
                raise ValueError("completed action 必須保存 resulting version reference")
            if self.error_code or self.safe_error_message:
                raise ValueError("completed action 不可保存錯誤")
        elif self.status is CandidateActionStatus.FAILED:
            if (
                self.completed_at is None
                or not self.error_code
                or not self.safe_error_message
            ):
                raise ValueError("failed action 必須保存安全錯誤與時間")
            if self.after_snapshot is not None or self.after_snapshot_sha256:
                raise ValueError("failed action 不可偽造 after snapshot")
        return self


__all__ = [
    "ACTIVE_JOB_STATUSES",
    "ArtifactKind",
    "AttemptStatus",
    "CandidateAction",
    "CandidateActionKind",
    "CandidateActionStatus",
    "CandidateProvenance",
    "CandidateStatus",
    "CreativeOperation",
    "GenerationCandidate",
    "GenerationJob",
    "GenerationStep",
    "GenerationStepAttempt",
    "JobStatus",
    "StepKind",
    "StepStatus",
    "job_transition_allowed",
    "sha256_content",
    "sha256_json",
    "sha256_text",
    "step_transition_allowed",
]
