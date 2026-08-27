"""Provider-independent data contracts (ADR-004)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class GenerationOptions(BaseModel):
    """Sampling options mapped onto each provider's native option names."""

    temperature: float = 0.8
    top_p: float = 0.9
    seed: int | None = None
    num_predict: int | None = None
    repeat_penalty: float | None = None
    stop: list[str] | None = None


class GenerationRequest(BaseModel):
    """A single-turn text generation request."""

    model: str
    prompt: str
    system: str | None = None
    options: GenerationOptions = Field(default_factory=GenerationOptions)
    # None -> provider default (settings.ollama_keep_alive). "0" unloads after the call.
    keep_alive: str | int | None = None
    timeout_s: float = 120.0


class TokenUsage(BaseModel):
    """Token accounting when the provider reports it."""

    prompt_tokens: int | None = None
    completion_tokens: int | None = None


class GenerationResult(BaseModel):
    """Normalized text generation result."""

    text: str
    model: str
    latency_ms: int
    usage: TokenUsage = Field(default_factory=TokenUsage)
    finish_reason: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class StructuredResult(BaseModel):
    """Normalized structured generation result (schema-validated payload)."""

    data: dict[str, Any]
    raw_text: str
    model: str
    latency_ms: int
    usage: TokenUsage = Field(default_factory=TokenUsage)
    repair_attempts: int = 0


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
    """A model currently loaded in memory (resource coordination, risk R-16)."""

    name: str
    size_vram_bytes: int | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class ProviderHealth(BaseModel):
    """Provider reachability probe result."""

    ok: bool
    version: str | None = None
    latency_ms: int | None = None
    error: str | None = None
