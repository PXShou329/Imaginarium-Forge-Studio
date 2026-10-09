"""Provider protocols ."""

from __future__ import annotations

from collections.abc import Iterator
from threading import Event
from typing import Protocol, runtime_checkable

from pydantic import BaseModel

from imaginarium_forge.providers.contracts import (
    GenerationRequest,
    GenerationResult,
    GenerationStreamEvent,
    LoadedModelInfo,
    ModelInfo,
    ModelPullProgress,
    ProviderHealth,
    StructuredResult,
)


class LLMProvider(Protocol):
    """Contract every text provider must satisfy.

    Implementations must raise only errors from `imaginarium_forge.providers.errors`.
    Cancellation is best-effort: providers check the event between network chunks.
    """

    name: str

    def generate_text(
        self, request: GenerationRequest, *, cancel: Event | None = None
    ) -> GenerationResult: ...

    def stream_text(
        self, request: GenerationRequest, *, cancel: Event | None = None
    ) -> Iterator[str]: ...

    def generate_structured_once(
        self,
        request: GenerationRequest,
        schema: type[BaseModel],
        *,
        cancel: Event | None = None,
    ) -> StructuredResult: ...

    def list_models(self) -> list[ModelInfo]: ...

    def health_check(self) -> ProviderHealth: ...


@runtime_checkable
class TextStreamEventProvider(Protocol):
    """Optional richer streaming extension implemented by capable providers.

    Keeping this separate from ``LLMProvider`` preserves structural
    compatibility with existing third-party and test providers that only
    implement ``stream_text() -> Iterator[str]``.
    """

    def stream_text_events(
        self, request: GenerationRequest, *, cancel: Event | None = None
    ) -> Iterator[GenerationStreamEvent]: ...


class ModelLifecycle(Protocol):
    """Optional lifecycle extension used for RTX 3070 resource coordination ."""

    def list_loaded_models(self) -> list[LoadedModelInfo]: ...

    def unload_model(self, model: str) -> bool: ...


class ModelInstaller(Protocol):
    """Optional explicit-install extension; never called by health checks."""

    def pull_model(
        self,
        model: str,
        *,
        cancel: Event | None = None,
        timeout_s: float = 1800.0,
    ) -> Iterator[ModelPullProgress]: ...
