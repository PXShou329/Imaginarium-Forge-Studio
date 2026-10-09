"""Provider-neutral contracts for the unified creative generation runtime.

Everything in this module is safe to serialize. Runtime credentials and live
provider clients deliberately live in:mod:`provider_registry` instead.
"""

from __future__ import annotations

import hashlib
import json
import re
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from imaginarium_forge.application.ports.credentials import CredentialSource
from imaginarium_forge.providers.contracts import (
    GenerationRequest,
    GenerationResult,
    StructuredResult,
)

_IDENTIFIER_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_.:-]*$")
_IDEMPOTENCY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")


class ProviderCapability(StrEnum):
    """Features a provider factory guarantees for the configured adapter."""

    TEXT_GENERATION = "text_generation"
    STRUCTURED_OUTPUT = "structured_output"
    STREAMING = "streaming"
    DETERMINISTIC_SEED = "deterministic_seed"
    MODEL_LISTING = "model_listing"
    HEALTH_CHECK = "health_check"
    RESPONSE_CHAINING = "response_chaining"
    REASONING_CONTROLS = "reasoning_controls"


class ProviderCapabilities(BaseModel):
    """Immutable capability set plus optional non-authoritative size hints."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    supported: frozenset[ProviderCapability] = Field(default_factory=frozenset)
    max_output_tokens: int | None = Field(default=None, ge=1)
    context_window_hint: int | None = Field(default=None, ge=1)

    def supports(self, required: frozenset[ProviderCapability]) -> bool:
        return required.issubset(self.supported)


class ProviderDescriptor(BaseModel):
    """Serializable provider metadata; never a live client or credential."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    provider_id: str = Field(min_length=1, max_length=64)
    display_name: str = Field(min_length=1, max_length=120)
    capabilities: ProviderCapabilities
    credential_id: str | None = Field(default=None, max_length=200)
    remote: bool = False

    @field_validator("provider_id")
    @classmethod
    def _normalize_provider_id(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not _IDENTIFIER_PATTERN.fullmatch(normalized):
            raise ValueError("provider_id must be a lowercase identifier")
        return normalized

    @field_validator("display_name")
    @classmethod
    def _normalize_display_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("display_name must not be blank")
        return normalized

    @field_validator("credential_id")
    @classmethod
    def _normalize_credential_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("credential_id must not be blank")
        return normalized


class GenerationTask(StrEnum):
    """Stable task families used by task-level routing."""

    GENERAL = "general"
    STORY_FRAGMENT = "story_fragment"
    CHARACTER_DRAFT = "character_draft"
    WORLD_SEED = "world_seed"
    PROMPT_SCRATCH = "prompt_scratch"
    STORY_CONTINUATION = "story_continuation"
    LONG_FORM = "long_form"
    FINAL_WRITING = "final_writing"


class GenerationOperation(StrEnum):
    TEXT = "text"
    STREAM = "stream"
    STRUCTURED = "structured"


class FallbackMode(StrEnum):
    FAIL_CLOSED = "fail_closed"
    PROVIDER = "provider"
    OFFLINE = "offline"


class GenerationErrorCategory(StrEnum):
    AUTHENTICATION = "authentication"
    CONFIGURATION = "configuration"
    QUOTA = "quota"
    RATE_LIMIT = "rate_limit"
    TIMEOUT = "timeout"
    UNAVAILABLE = "unavailable"
    MODEL_NOT_FOUND = "model_not_found"
    CANCELLED = "cancelled"
    REFUSAL = "refusal"
    INCOMPLETE_OUTPUT = "incomplete_output"
    INVALID_RESPONSE = "invalid_response"
    INVALID_STRUCTURED_OUTPUT = "invalid_structured_output"
    PROVIDER_ERROR = "provider_error"
    ROUTE_UNAVAILABLE = "route_unavailable"
    DUPLICATE_IN_PROGRESS = "duplicate_in_progress"
    IDEMPOTENCY_CONFLICT = "idempotency_conflict"
    OFFLINE_FALLBACK_UNAVAILABLE = "offline_fallback_unavailable"


_TRANSIENT_CATEGORIES = frozenset(
    {
        GenerationErrorCategory.RATE_LIMIT,
        GenerationErrorCategory.TIMEOUT,
        GenerationErrorCategory.UNAVAILABLE,
    }
)

_DEFAULT_FALLBACK_CATEGORIES = frozenset(
    {
        GenerationErrorCategory.AUTHENTICATION,
        GenerationErrorCategory.CONFIGURATION,
        GenerationErrorCategory.QUOTA,
        GenerationErrorCategory.RATE_LIMIT,
        GenerationErrorCategory.TIMEOUT,
        GenerationErrorCategory.UNAVAILABLE,
        GenerationErrorCategory.MODEL_NOT_FOUND,
    }
)


class ProviderRoute(BaseModel):
    """One provider/model destination. It contains no provider secret."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    provider_id: str = Field(min_length=1, max_length=64)
    model: str = Field(min_length=1, max_length=256)
    profile_id: str = Field(default="default", min_length=1, max_length=100)

    @field_validator("provider_id")
    @classmethod
    def _normalize_provider_id(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not _IDENTIFIER_PATTERN.fullmatch(normalized):
            raise ValueError("provider_id must be a lowercase identifier")
        return normalized

    @field_validator("model", "profile_id")
    @classmethod
    def _normalize_non_blank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("route values must not be blank")
        return normalized


class GenerationRoute(BaseModel):
    """Primary route with an explicit, typed fallback policy."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    primary: ProviderRoute
    fallback_mode: FallbackMode = FallbackMode.FAIL_CLOSED
    fallback: ProviderRoute | None = None
    fallback_on: frozenset[GenerationErrorCategory] = Field(
        default_factory=lambda: _DEFAULT_FALLBACK_CATEGORIES
    )

    @model_validator(mode="after")
    def _validate_fallback_shape(self) -> Self:
        if self.fallback_mode is FallbackMode.PROVIDER and self.fallback is None:
            raise ValueError("provider fallback requires a fallback route")
        if self.fallback_mode is not FallbackMode.PROVIDER and self.fallback is not None:
            raise ValueError("fallback route is only valid for provider fallback")
        return self


class RetryPolicy(BaseModel):
    """Bounded retry policy. Attempts are counted per provider route."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    max_attempts: int = Field(default=2, ge=1, le=5)
    base_delay_s: float = Field(default=0.25, ge=0.0, le=30.0)
    max_delay_s: float = Field(default=2.0, ge=0.0, le=120.0)
    jitter_ratio: float = Field(default=0.1, ge=0.0, le=1.0)
    retry_on: frozenset[GenerationErrorCategory] = Field(
        default_factory=lambda: _TRANSIENT_CATEGORIES
    )

    @model_validator(mode="after")
    def _validate_delay_bounds(self) -> Self:
        if self.max_delay_s < self.base_delay_s:
            raise ValueError("max_delay_s must be greater than or equal to base_delay_s")
        return self


class TaskRoute(BaseModel):
    """A task-level route rule."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    task: GenerationTask
    route: GenerationRoute


class ActionRoute(BaseModel):
    """An action-level route rule scoped to one task."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    task: GenerationTask
    action_id: str = Field(min_length=1, max_length=128)
    route: GenerationRoute

    @field_validator("action_id")
    @classmethod
    def _normalize_action_id(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not _IDENTIFIER_PATTERN.fullmatch(normalized):
            raise ValueError("action_id must be a lowercase identifier")
        return normalized


class GenerationRoutingTable(BaseModel):
    """Immutable ordered rules used by:class:`ProviderRouter`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    global_route: GenerationRoute
    task_routes: tuple[TaskRoute, ...] = ()
    action_routes: tuple[ActionRoute, ...] = ()

    @model_validator(mode="after")
    def _reject_duplicate_rules(self) -> Self:
        task_keys = [rule.task for rule in self.task_routes]
        if len(task_keys) != len(set(task_keys)):
            raise ValueError("task route rules must be unique")
        action_keys = [(rule.task, rule.action_id) for rule in self.action_routes]
        if len(action_keys) != len(set(action_keys)):
            raise ValueError("action route rules must be unique")
        return self


class RouteSource(StrEnum):
    REQUEST_OVERRIDE = "request_override"
    ACTION = "action"
    TASK = "task"
    GLOBAL = "global"


class GenerationInvocation(BaseModel):
    """Serializable execution command. Credentials are intentionally absent."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    task: GenerationTask
    action_id: str = Field(default="generate", min_length=1, max_length=128)
    request: GenerationRequest
    idempotency_key: str = Field(min_length=1, max_length=200)
    route_override: GenerationRoute | None = None
    provider_attempt_ceiling: int | None = Field(default=None, ge=1, le=10)
    required_capabilities: frozenset[ProviderCapability] = Field(
        default_factory=lambda: frozenset({ProviderCapability.TEXT_GENERATION})
    )

    @field_validator("action_id")
    @classmethod
    def _normalize_action_id(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not _IDENTIFIER_PATTERN.fullmatch(normalized):
            raise ValueError("action_id must be a lowercase identifier")
        return normalized

    @field_validator("idempotency_key")
    @classmethod
    def _normalize_idempotency_key(cls, value: str) -> str:
        normalized = value.strip()
        if not _IDEMPOTENCY_PATTERN.fullmatch(normalized):
            raise ValueError("idempotency_key must contain only safe identifier characters")
        return normalized

    @model_validator(mode="after")
    def _require_text_generation(self) -> Self:
        if ProviderCapability.TEXT_GENERATION not in self.required_capabilities:
            raise ValueError("text_generation capability is always required")
        return self

    def fingerprint(self, operation: GenerationOperation) -> str:
        """Return a stable request fingerprint without the idempotency key."""

        payload = self.model_dump(mode="json", exclude={"idempotency_key"})
        payload["required_capabilities"] = sorted(
            capability.value for capability in self.required_capabilities
        )
        route_payload = payload.get("route_override")
        if isinstance(route_payload, dict) and self.route_override is not None:
            route_payload["fallback_on"] = sorted(
                category.value for category in self.route_override.fallback_on
            )
        payload["operation"] = operation.value
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


class AttemptStatus(StrEnum):
    SUCCEEDED = "succeeded"
    INTERRUPTED = "interrupted"
    FAILED = "failed"


class ProviderLifecycleStatus(StrEnum):
    CLOSED = "closed"
    CLOSE_UNSUPPORTED = "close_unsupported"
    CLOSE_FAILED = "close_failed"


class GenerationAttemptProvenance(BaseModel):
    """One redacted provider attempt suitable for persistence and diagnostics."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    provider_id: str
    requested_model: str
    actual_model: str | None = None
    attempt_number: int = Field(ge=1)
    credential_source: CredentialSource | None = None
    status: AttemptStatus
    latency_ms: int = Field(ge=0)
    error_category: GenerationErrorCategory | None = None
    retriable: bool = False
    lifecycle_status: ProviderLifecycleStatus


class GenerationProvenance(BaseModel):
    """Redacted routing and execution evidence for one operation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    idempotency_key: str
    route_source: RouteSource | None = None
    requested_provider_id: str | None = None
    requested_model: str | None = None
    actual_provider_id: str | None = None
    actual_model: str | None = None
    fallback_used: bool = False
    offline_fallback_used: bool = False
    attempts: tuple[GenerationAttemptProvenance, ...] = ()


class GenerationFailure(BaseModel):
    """Sanitized failure safe for UI, jobs, logs and persistence."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    category: GenerationErrorCategory
    safe_message: str
    retriable: bool = False
    partial_text: str = ""


class GenerationExecutionStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class TextGenerationOutcome(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    status: GenerationExecutionStatus
    provenance: GenerationProvenance
    result: GenerationResult | None = None
    failure: GenerationFailure | None = None

    @model_validator(mode="after")
    def _validate_terminal_shape(self) -> Self:
        if self.status is GenerationExecutionStatus.SUCCEEDED:
            if self.result is None or self.failure is not None:
                raise ValueError("successful text outcome requires only a result")
        elif self.failure is None or self.result is not None:
            raise ValueError("failed text outcome requires only a failure")
        return self


class TextStreamStatus(StrEnum):
    """Terminal status for a streamed text call.

    ``PARTIAL`` is intentionally distinct from both success and failure: the
    caller may checkpoint the prose, but must not publish it as completed.
    """

    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"


class TextStreamOutcome(BaseModel):
    """Typed streamed prose outcome with an explicit partial state."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: TextStreamStatus
    provenance: GenerationProvenance
    result: GenerationResult | None = None
    failure: GenerationFailure | None = None

    @model_validator(mode="after")
    def _validate_terminal_shape(self) -> Self:
        if self.status is TextStreamStatus.COMPLETED:
            if self.result is None or self.result.partial or self.failure is not None:
                raise ValueError("completed stream outcome requires a complete result")
        elif self.status is TextStreamStatus.PARTIAL:
            if (
                self.result is None
                or not self.result.partial
                or not self.result.text
                or self.failure is not None
            ):
                raise ValueError("partial stream outcome requires partial text only")
        elif self.failure is None or self.result is not None:
            raise ValueError("failed stream outcome requires only a failure")
        return self


class StructuredGenerationOutcome(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    status: GenerationExecutionStatus
    provenance: GenerationProvenance
    result: StructuredResult | None = None
    failure: GenerationFailure | None = None

    @model_validator(mode="after")
    def _validate_terminal_shape(self) -> Self:
        if self.status is GenerationExecutionStatus.SUCCEEDED:
            if self.result is None or self.failure is not None:
                raise ValueError("successful structured outcome requires only a result")
        elif self.failure is None or self.result is not None:
            raise ValueError("failed structured outcome requires only a failure")
        return self


__all__ = [
    "ActionRoute",
    "AttemptStatus",
    "FallbackMode",
    "GenerationAttemptProvenance",
    "GenerationErrorCategory",
    "GenerationExecutionStatus",
    "GenerationFailure",
    "GenerationInvocation",
    "GenerationOperation",
    "GenerationProvenance",
    "GenerationRoute",
    "GenerationRoutingTable",
    "GenerationTask",
    "ProviderCapabilities",
    "ProviderCapability",
    "ProviderDescriptor",
    "ProviderLifecycleStatus",
    "ProviderRoute",
    "RetryPolicy",
    "RouteSource",
    "StructuredGenerationOutcome",
    "TaskRoute",
    "TextGenerationOutcome",
    "TextStreamOutcome",
    "TextStreamStatus",
]
