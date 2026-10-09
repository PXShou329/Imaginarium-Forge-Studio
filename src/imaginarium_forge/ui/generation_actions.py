"""Shared UI boundary for credential-safe generation actions.

Feature pages may render the non-secret route/readiness projections from this
module and may borrow a provider for one explicit action. They never read a
session key, query the credential store, or construct a provider themselves.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from typing import Any, Protocol

from imaginarium_forge.application.ports.credentials import (
    OPENAI_CREDENTIAL_ID,
    CredentialResolver,
)
from imaginarium_forge.application.services.generation.contracts import (
    GenerationRoute,
    GenerationTask,
)
from imaginarium_forge.application.services.generation.provider_adapter import (
    CreativeRuntimeProviderAdapter,
    CreativeRuntimeProviderFactory,
    RuntimePreferenceReader,
)
from imaginarium_forge.domain.generation.preferences import AIProviderPreference
from imaginarium_forge.ui.ai_runtime import (
    GenerationMode,
    GenerationRuntimeConfig,
)
from imaginarium_forge.ui.openai_session import (
    OpenAIKeyRetention,
    OpenAISessionSettings,
    SessionStateStore,
    consume_openai_key_after_action,
    read_openai_session_settings,
    render_openai_session_summary,
)


class GenerationActionServices(Protocol):
    """Narrow structural view supplied by ``ui.bootstrap.Services``."""

    @property
    def credential_resolver(self) -> CredentialResolver: ...

    @property
    def ai_runtime_preferences(self) -> RuntimePreferenceReader: ...

    @property
    def generation_providers(self) -> CreativeRuntimeProviderFactory: ...


@dataclass(frozen=True, slots=True)
class OpenAIActionReadiness:
    """Safe OpenAI UI projection; it deliberately contains no credential."""

    config: GenerationRuntimeConfig | None
    credential_available: bool
    remote_enabled: bool
    key_retention: OpenAIKeyRetention

    @property
    def model(self) -> str:
        return self.config.model if self.config is not None else ""


@dataclass(frozen=True, slots=True)
class GenerationActionReadiness:
    """Resolved non-secret route plus whether its credential is available."""

    route: GenerationRoute
    credential_available: bool


def _safe_openai_projection(
    services: GenerationActionServices,
    settings: OpenAISessionSettings,
) -> OpenAIActionReadiness:
    try:
        config = GenerationRuntimeConfig(
            mode=GenerationMode.OPENAI,
            openai_model_preset=settings.preset,
            custom_openai_model=settings.custom_model,
        )
    except ValueError:
        config = None
    resolved = services.credential_resolver.resolve(
        OPENAI_CREDENTIAL_ID,
        session_override=settings.api_key,
    )
    preferences = services.ai_runtime_preferences.get_preferences()
    return OpenAIActionReadiness(
        config=config,
        credential_available=resolved.available,
        remote_enabled=preferences.remote_provider_enabled,
        key_retention=settings.key_retention,
    )


def openai_action_readiness(
    services: GenerationActionServices,
    state: SessionStateStore,
) -> OpenAIActionReadiness:
    """Read session -> device -> environment readiness without exposing a key."""

    return _safe_openai_projection(
        services,
        read_openai_session_settings(state),
    )


def render_openai_action_readiness(
    services: GenerationActionServices,
    *,
    key_prefix: str,
    disabled: bool = False,
) -> OpenAIActionReadiness:
    """Render the common masked summary and return only non-secret readiness."""

    settings = render_openai_session_summary(
        key_prefix=key_prefix,
        disabled=disabled,
    )
    return _safe_openai_projection(services, settings)


def generation_action_readiness(
    services: GenerationActionServices,
    state: SessionStateStore,
    *,
    task: GenerationTask,
    action_id: str,
) -> GenerationActionReadiness:
    """Resolve one live global route and its credential availability."""

    route = services.generation_providers.resolve_route(
        task=task,
        action_id=action_id,
    )
    available = True
    if route.primary.provider_id == AIProviderPreference.OPENAI.value:
        available = openai_action_readiness(services, state).credential_available
    return GenerationActionReadiness(
        route=route,
        credential_available=available,
    )


def provider_action_credential_available(
    services: GenerationActionServices,
    state: SessionStateStore,
    *,
    provider_name: str,
) -> bool:
    """Check a frozen provider route without exposing its resolved credential."""

    if provider_name != AIProviderPreference.OPENAI.value:
        return True
    return openai_action_readiness(services, state).credential_available


def background_provider_credential_available(
    services: GenerationActionServices,
    *,
    provider_name: str,
) -> bool:
    """Check only durable device/environment credentials for background work."""

    if provider_name != AIProviderPreference.OPENAI.value:
        return True
    return services.credential_resolver.resolve(OPENAI_CREDENTIAL_ID).available


def run_provider_credential_action[ActionResultT](
    services: GenerationActionServices,
    state: SessionStateStore,
    *,
    provider_name: str,
    action: Callable[[Any], ActionResultT],
) -> ActionResultT:
    """Borrow a session credential for one explicit non-provider UI action."""

    uses_openai = provider_name == AIProviderPreference.OPENAI.value
    session_override = (
        read_openai_session_settings(state).api_key if uses_openai else None
    )
    try:
        return action(session_override)
    finally:
        if uses_openai:
            consume_openai_key_after_action(state)
        session_override = None


class GenerationProviderLease:
    """One action-scoped provider plus idempotent credential cleanup."""

    def __init__(
        self,
        provider: CreativeRuntimeProviderAdapter,
        *,
        provider_name: str,
        state: SessionStateStore,
        consumes_openai_key: bool,
    ) -> None:
        self.provider = provider
        self.provider_name = provider_name
        self._state = state
        self._consumes_openai_key = consumes_openai_key
        self._closed = False

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        with suppress(Exception):
            self.provider.close()
        if self._consumes_openai_key:
            consume_openai_key_after_action(self._state)


def capture_generation_provider_action(
    services: GenerationActionServices,
    state: SessionStateStore,
    *,
    task: GenerationTask,
    action_id: str,
    provider_override: AIProviderPreference | None = None,
    model_override: str | None = None,
) -> GenerationProviderLease:
    """Capture one provider at an explicit action boundary.

    Only this function reads the session secret. The adapter receives a
    ``SecretStr`` and clears its reference when the lease closes.
    """

    if provider_override is not None:
        provider_name = provider_override.value
    else:
        if model_override is None:
            route = services.generation_providers.resolve_route(
                task=task,
                action_id=action_id,
            )
        else:
            route = services.generation_providers.resolve_route(
                task=task,
                action_id=action_id,
                model_override=model_override,
            )
        provider_name = route.primary.provider_id
    uses_openai = provider_name == AIProviderPreference.OPENAI.value
    session_override = (
        read_openai_session_settings(state).api_key if uses_openai else None
    )
    if provider_override is None and model_override is None:
        provider = services.generation_providers.create(
            task=task,
            action_id=action_id,
            session_override=session_override,
        )
    else:
        provider = services.generation_providers.create(
            task=task,
            action_id=action_id,
            provider_override=provider_override,
            model_override=model_override,
            session_override=session_override,
        )
    session_override = None
    return GenerationProviderLease(
        provider,
        provider_name=provider_name,
        state=state,
        consumes_openai_key=uses_openai,
    )


@contextmanager
def generation_provider_action(
    services: GenerationActionServices,
    state: SessionStateStore,
    *,
    task: GenerationTask,
    action_id: str,
    provider_override: AIProviderPreference | None = None,
    model_override: str | None = None,
) -> Iterator[Any]:
    """Yield a provider only for the duration of one explicit UI action."""

    lease = capture_generation_provider_action(
        services,
        state,
        task=task,
        action_id=action_id,
        provider_override=provider_override,
        model_override=model_override,
    )
    try:
        yield lease.provider
    finally:
        lease.close()


def consume_openai_action_credential(state: SessionStateStore) -> bool:
    """Hide the retention implementation from production feature pages."""

    return consume_openai_key_after_action(state)


__all__ = [
    "GenerationActionReadiness",
    "GenerationActionServices",
    "GenerationProviderLease",
    "OpenAIActionReadiness",
    "background_provider_credential_available",
    "capture_generation_provider_action",
    "consume_openai_action_credential",
    "generation_action_readiness",
    "generation_provider_action",
    "openai_action_readiness",
    "provider_action_credential_available",
    "render_openai_action_readiness",
    "run_provider_credential_action",
]
