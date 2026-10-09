"""Provider-independent request, response, and streaming contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

GENERAL_AUDIENCE_INSTRUCTION = (
    "Create content suitable for a general audience. Follow the requested "
    "format and preserve the author's established characters and setting."
)


def general_audience_system(system: str | None) -> str:
    """Apply the workbench's audience instruction once, preserving the brief."""
    if system and system.startswith(GENERAL_AUDIENCE_INSTRUCTION):
        return system
    if not system:
        return GENERAL_AUDIENCE_INSTRUCTION
    return GENERAL_AUDIENCE_INSTRUCTION + "\n\n" + system


class GenerationOptions(BaseModel):
    """Sampling options mapped onto each provider's native option names."""

    temperature: float = 0.8
    top_p: float = 0.9
    seed: int | None = None
    num_predict: int | None = None
    # Application-owned, provider-neutral output cap. The application may
    # derive this from an author length contract, but providers must never use
    # it to declare that the requested author length was satisfied.
    max_output_tokens: int | None = Field(default=None, ge=1)
    # Explicit Ollama context-window request. Profile selection and resource
    # warnings happen before dispatch; the adapter maps this value unchanged.
    num_ctx: int | None = Field(default=None, ge=1)
    repeat_penalty: float | None = None
    stop: list[str] | None = None


class GenerationInputItem(BaseModel):
    """Reconstructable, provider-neutral conversational input item."""

    role: Literal["system", "developer", "user", "assistant"]
    content: str


class GenerationRequest(BaseModel):
    """A single-turn text generation request."""

    model: str
    prompt: str
    system: str | None = None
    options: GenerationOptions = Field(default_factory=GenerationOptions)
    # None -> provider default (settings.ollama_keep_alive). "0" unloads after the call.
    keep_alive: str | int | None = None
    timeout_s: float = 120.0
    input_items: tuple[GenerationInputItem, ...] = ()
    previous_response_id: str | None = None
    reasoning_effort: Literal["minimal", "low", "medium", "high", "xhigh"] | None = None
    verbosity: Literal["low", "medium", "high"] | None = None

    @model_validator(mode="after")
    def _audience_instruction(self) -> Self:
        self.system = general_audience_system(self.system)
        if self.input_items:
            first, *remaining = self.input_items
            if first.role == "system":
                first = first.model_copy(update={"content": general_audience_system(first.content)})
                self.input_items = (first, *remaining)
            else:
                self.input_items = (
                    GenerationInputItem(role="system", content=GENERAL_AUDIENCE_INSTRUCTION),
                    *self.input_items,
                )
        return self


class TokenUsage(BaseModel):
    """Token accounting when the provider reports it."""

    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    cached_prompt_tokens: int | None = None


class GenerationResult(BaseModel):
    """Normalized text generation result."""

    text: str
    model: str
    latency_ms: int
    usage: TokenUsage = Field(default_factory=TokenUsage)
    finish_reason: str | None = None
    response_id: str | None = None
    partial: bool = False
    incomplete_reason: str | None = None
    retry_attempts: int = 0
    raw: dict[str, Any] = Field(default_factory=dict)

class GenerationStreamDelta(BaseModel):
    """One provider-normalized text delta.

    This additive event contract leaves the long-standing ``Iterator[str]``
    provider API intact while allowing the shared runtime to opt in to terminal
    response metadata when a provider exposes ``stream_text_events``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["delta"] = "delta"
    text: str = Field(min_length=1)


class GenerationStreamResult(BaseModel):
    """Terminal metadata for a streamed generation, excluding generated prose."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["result"] = "result"
    model: str = Field(min_length=1)
    latency_ms: int = Field(ge=0)
    usage: TokenUsage = Field(default_factory=TokenUsage)
    finish_reason: str | None = None
    response_id: str | None = None
    partial: bool = False
    incomplete_reason: str | None = None
    retry_attempts: int = Field(default=0, ge=0)
    raw: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_partial_reason(self) -> Self:
        if self.partial and not self.incomplete_reason:
            raise ValueError("partial stream results require an incomplete reason")
        if not self.partial and self.incomplete_reason is not None:
            raise ValueError("complete stream results cannot have an incomplete reason")
        return self

    @classmethod
    def from_generation_result(cls, result: GenerationResult) -> Self:
        """Copy terminal fields without retaining a second copy of response prose."""

        return cls.model_validate(
            result.model_dump(mode="python", exclude={"text"})
        )

    def to_generation_result(self, text: str) -> GenerationResult:
        """Combine accumulated deltas with this immutable terminal snapshot."""

        return GenerationResult(
            text=text,
            model=self.model,
            latency_ms=self.latency_ms,
            usage=self.usage.model_copy(deep=True),
            finish_reason=self.finish_reason,
            response_id=self.response_id,
            partial=self.partial,
            incomplete_reason=self.incomplete_reason,
            retry_attempts=self.retry_attempts,
            raw=dict(self.raw),
        )


GenerationStreamEvent = GenerationStreamDelta | GenerationStreamResult


class StructuredResult(BaseModel):
    """Normalized structured generation result (schema-validated payload)."""

    data: dict[str, Any]
    raw_text: str
    model: str
    latency_ms: int
    usage: TokenUsage = Field(default_factory=TokenUsage)
    repair_attempts: int = 0
    response_id: str | None = None

class ModelInfo(BaseModel):
    """An installed model as reported by the provider."""

    name: str
    size_bytes: int | None = None
    modified_at: datetime | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class ModelPullProgress(BaseModel):
    """One redacted progress event from an explicit model download."""

    model: str
    status: str
    completed_bytes: int | None = None
    total_bytes: int | None = None
    done: bool = False


class LoadedModelInfo(BaseModel):
    """A model currently loaded in memory."""

    name: str
    size_vram_bytes: int | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class ProviderHealth(BaseModel):
    """Provider reachability probe result."""

    ok: bool
    version: str | None = None
    latency_ms: int | None = None
    error: str | None = None
