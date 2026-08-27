"""Explicit local Ollama model inventory and download orchestration."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from ipaddress import ip_address
from threading import Event
from typing import Protocol
from urllib.parse import urlsplit

from imaginarium_forge.config.settings import AppSettings
from imaginarium_forge.providers.contracts import ModelInfo, ModelPullProgress
from imaginarium_forge.providers.errors import (
    InvalidProviderResponseError,
    ProviderConfigurationError,
)
from imaginarium_forge.providers.ollama import OllamaProvider, normalize_ollama_model_id


class _OllamaModelProvider(Protocol):
    def list_models(self) -> list[ModelInfo]: ...

    def pull_model(
        self,
        model: str,
        *,
        cancel: Event | None = None,
        timeout_s: float | None = None,
    ) -> Iterator[ModelPullProgress]: ...

    def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class OllamaModelInventory:
    models: tuple[ModelInfo, ...]

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(model.name for model in self.models if model.name.strip())

    def installed(self, model: str) -> bool:
        return any(ollama_model_ids_match(model, installed) for installed in self.names)

    def matching(self, model: str) -> ModelInfo | None:
        return next(
            (
                installed
                for installed in self.models
                if ollama_model_ids_match(model, installed.name)
            ),
            None,
        )


def canonical_ollama_model_id(value: object) -> str:
    """Compare Ollama's implicit and explicit latest tags consistently."""

    model = normalize_ollama_model_id(value).casefold()
    final_segment = model.rsplit("/", maxsplit=1)[-1]
    return model if ":" in final_segment else f"{model}:latest"


def ollama_model_ids_match(left: object, right: object) -> bool:
    try:
        return canonical_ollama_model_id(left) == canonical_ollama_model_id(right)
    except ValueError:
        return False


def is_loopback_ollama_endpoint(value: str) -> bool:
    """Return whether an endpoint is an uncredentialed local HTTP origin."""

    try:
        parsed = urlsplit(value.strip())
        port = parsed.port
    except ValueError:
        return False
    if parsed.scheme.casefold() not in {"http", "https"}:
        return False
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        return False
    if parsed.path not in {"", "/"} or (port is None and parsed.netloc.endswith(":")):
        return False
    host = (parsed.hostname or "").casefold().rstrip(".")
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        return ip_address(host).is_loopback
    except ValueError:
        return False


class OllamaModelService:
    """Fail-closed service used only after an explicit author action."""

    def __init__(
        self,
        settings: AppSettings,
        *,
        provider: _OllamaModelProvider | None = None,
    ) -> None:
        self._settings = settings
        self._provider = provider or OllamaProvider(settings)
        self._owns_provider = provider is None
        self._closed = False

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._owns_provider:
            self._provider.close()

    def inventory(self) -> OllamaModelInventory:
        if self._closed:
            raise RuntimeError("Ollama model service is closed")
        return OllamaModelInventory(tuple(self._provider.list_models()))

    def pull_model(
        self,
        model: str,
        *,
        cancel: Event | None = None,
        on_progress: Callable[[ModelPullProgress], None] | None = None,
    ) -> ModelInfo:
        """Download once, then verify the installed inventory before returning."""

        if self._closed:
            raise RuntimeError("Ollama model service is closed")
        normalized = normalize_ollama_model_id(model)
        if not is_loopback_ollama_endpoint(self._settings.ollama_base_url):
            raise ProviderConfigurationError(
                "model downloads are allowed only through a loopback Ollama endpoint"
            )
        completed = False
        for event in self._provider.pull_model(
            normalized,
            cancel=cancel,
            timeout_s=self._settings.ollama_pull_timeout_s,
        ):
            if on_progress is not None:
                on_progress(event)
            completed = completed or event.done
        if not completed:
            raise InvalidProviderResponseError(
                "Ollama model download did not report success"
            )
        installed = self.inventory().matching(normalized)
        if installed is None:
            raise InvalidProviderResponseError(
                "Ollama reported success but the model is absent from inventory"
            )
        return installed


__all__ = [
    "OllamaModelInventory",
    "OllamaModelService",
    "canonical_ollama_model_id",
    "is_loopback_ollama_endpoint",
    "ollama_model_ids_match",
]
