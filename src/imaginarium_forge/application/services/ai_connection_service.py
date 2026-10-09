"""Explicit OpenAI connection/model probes for the settings screen.

The service performs no work during construction. A network call can happen
only through an explicit ``test_connection`` or ``refresh_models`` command.
Credentials are resolved immediately before creating a short-lived provider.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from pydantic import SecretStr

from imaginarium_forge.application.ports.credentials import (
    OPENAI_CREDENTIAL_ID,
    CredentialResolver,
    CredentialSource,
    CredentialStatus,
)
from imaginarium_forge.domain.common.ids import utc_now_iso
from imaginarium_forge.providers.base import LLMProvider
from imaginarium_forge.providers.contracts import ModelInfo
from imaginarium_forge.providers.errors import (
    ProviderConfigurationError,
    ProviderError,
    ProviderUnavailableError,
)


class _ClosableProvider(LLMProvider, Protocol):
    def close(self) -> None: ...


OpenAIProviderBuilder = Callable[[SecretStr], _ClosableProvider]
OllamaProviderBuilder = Callable[[], _ClosableProvider]


@dataclass(frozen=True, slots=True)
class ConnectionProbe:
    provider: str
    credential_source: CredentialSource
    model: str
    checked_at: str
    latency_ms: int | None
    available_models: tuple[str, ...]


class AiConnectionService:
    """Bounded, user-triggered provider diagnostics with runtime credentials."""

    def __init__(
        self,
        resolver: CredentialResolver,
        openai_builder: OpenAIProviderBuilder,
        ollama_builder: OllamaProviderBuilder | None = None,
    ) -> None:
        self._resolver = resolver
        self._openai_builder = openai_builder
        self._ollama_builder = ollama_builder

    def credential_status(self) -> CredentialStatus:
        resolved = self._resolver.resolve(OPENAI_CREDENTIAL_ID)
        return resolved.device_status or CredentialStatus(available=False)

    def _provider(
        self,
        *,
        session_override: SecretStr | str | None,
    ) -> tuple[_ClosableProvider, CredentialSource]:
        resolved = self._resolver.resolve(
            OPENAI_CREDENTIAL_ID,
            session_override=session_override,
        )
        if not resolved.available or resolved.secret is None:
            raise ProviderConfigurationError(
                "OpenAI 尚未設定可用憑證；請使用工作階段、裝置安全保存或環境變數"
            )
        return self._openai_builder(resolved.secret), resolved.source

    def refresh_models(
        self,
        *,
        session_override: SecretStr | str | None = None,
    ) -> tuple[ModelInfo, ...]:
        provider, _ = self._provider(session_override=session_override)
        try:
            return tuple(provider.list_models())
        finally:
            provider.close()

    def test_connection(
        self,
        *,
        model: str,
        session_override: SecretStr | str | None = None,
    ) -> ConnectionProbe:
        provider, source = self._provider(session_override=session_override)
        try:
            health = provider.health_check()
            if not health.ok:
                raise ProviderError(
                    "OpenAI 連線測試未通過；請檢查憑證、網路與模型權限",
                    retriable=True,
                )
            models = tuple(provider.list_models())
        finally:
            provider.close()
        return ConnectionProbe(
            provider="openai",
            credential_source=source,
            model=model,
            checked_at=utc_now_iso(),
            latency_ms=health.latency_ms,
            available_models=tuple(item.name for item in models),
        )

    def refresh_ollama_models(self) -> tuple[ModelInfo, ...]:
        """List local models only after the author explicitly requests it."""

        provider = self._ollama_provider()
        try:
            return tuple(provider.list_models())
        finally:
            provider.close()

    def test_ollama_connection(self, *, model: str) -> ConnectionProbe:
        """Probe the configured local Ollama endpoint without generating text."""

        provider = self._ollama_provider()
        try:
            health = provider.health_check()
            if not health.ok:
                raise ProviderUnavailableError(
                    "Ollama 連線測試未通過；請確認本機服務已啟動"
                )
            models = tuple(provider.list_models())
        finally:
            provider.close()
        return ConnectionProbe(
            provider="ollama",
            credential_source=CredentialSource.UNAVAILABLE,
            model=model,
            checked_at=utc_now_iso(),
            latency_ms=health.latency_ms,
            available_models=tuple(item.name for item in models),
        )

    def _ollama_provider(self) -> _ClosableProvider:
        if self._ollama_builder is None:
            raise ProviderConfigurationError("Ollama 連線服務尚未設定")
        return self._ollama_builder()


__all__ = [
    "AiConnectionService",
    "ConnectionProbe",
    "OllamaProviderBuilder",
    "OpenAIProviderBuilder",
]
