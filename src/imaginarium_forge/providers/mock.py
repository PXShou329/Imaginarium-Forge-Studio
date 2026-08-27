"""Deterministic in-memory provider for tests and harness plumbing validation.

HONESTY RULE: results produced with this provider validate code paths only.
Benchmark reports must label provider="mock" prominently; mock scores must never
be presented as model-quality conclusions.
"""

from __future__ import annotations

from collections.abc import Iterator
from threading import Event
from typing import Any

from pydantic import BaseModel

from imaginarium_forge.providers.contracts import (
    GenerationRequest,
    GenerationResult,
    LoadedModelInfo,
    ModelInfo,
    ProviderHealth,
    StructuredResult,
    TokenUsage,
)
from imaginarium_forge.providers.errors import RequestCancelledError
from imaginarium_forge.providers.structured_repair import parse_structured


class MockProvider:
    """Queue-driven fake provider implementing LLMProvider + ModelLifecycle."""

    name = "mock"

    def __init__(
        self,
        *,
        text_responses: list[str] | None = None,
        structured_responses: list[str] | None = None,
        latency_ms: int = 1,
        healthy: bool = True,
    ) -> None:
        self._text = list(text_responses or [])
        self._structured = list(structured_responses or [])
        self._latency_ms = latency_ms
        self._healthy = healthy
        self.requests: list[GenerationRequest] = []

    # ------------------------------------------------------------ LLMProvider
    def generate_text(
        self, request: GenerationRequest, *, cancel: Event | None = None
    ) -> GenerationResult:
        if cancel is not None and cancel.is_set():
            raise RequestCancelledError("cancelled")
        self.requests.append(request)
        text = self._text.pop(0) if self._text else f"[mock:{request.model}] {request.prompt[:60]}"
        return GenerationResult(
            text=text,
            model=request.model,
            latency_ms=self._latency_ms,
            usage=TokenUsage(prompt_tokens=len(request.prompt), completion_tokens=len(text)),
            finish_reason="stop",
            raw={"mock": True},
        )

    def stream_text(
        self, request: GenerationRequest, *, cancel: Event | None = None
    ) -> Iterator[str]:
        result = self.generate_text(request, cancel=cancel)
        midpoint = max(1, len(result.text) // 2)
        for piece in (result.text[:midpoint], result.text[midpoint:]):
            if cancel is not None and cancel.is_set():
                raise RequestCancelledError("stream cancelled")
            if piece:
                yield piece

    def generate_structured_once(
        self,
        request: GenerationRequest,
        schema: type[BaseModel],
        *,
        cancel: Event | None = None,
    ) -> StructuredResult:
        if cancel is not None and cancel.is_set():
            raise RequestCancelledError("cancelled")
        self.requests.append(request)
        # Default "{}" relies on Phase 0 schemas having safe defaults for every field.
        raw_text = self._structured.pop(0) if self._structured else "{}"
        data: dict[str, Any] = parse_structured(raw_text, schema)
        return StructuredResult(
            data=data,
            raw_text=raw_text,
            model=request.model,
            latency_ms=self._latency_ms,
        )

    def list_models(self) -> list[ModelInfo]:
        return [ModelInfo(name="mock-model")]

    def health_check(self) -> ProviderHealth:
        if not self._healthy:
            return ProviderHealth(ok=False, error="mock provider configured unhealthy")
        return ProviderHealth(ok=True, version="mock", latency_ms=0)

    # --------------------------------------------------------- ModelLifecycle
    def list_loaded_models(self) -> list[LoadedModelInfo]:
        return []

    def unload_model(self, model: str) -> bool:
        return True
