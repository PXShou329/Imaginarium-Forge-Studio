"""Factory-only provider registry for short-lived runtime clients."""

from __future__ import annotations

from dataclasses import dataclass, field
from threading import RLock
from typing import Protocol

from pydantic import SecretStr

from imaginarium_forge.application.ports.credentials import (
    CredentialSource,
    ResolvedCredential,
)
from imaginarium_forge.application.services.generation.contracts import (
    ProviderDescriptor,
    ProviderRoute,
)
from imaginarium_forge.providers.base import LLMProvider


class ProviderRegistryError(RuntimeError):
    """Base class for sanitized registry configuration failures."""


class DuplicateProviderError(ProviderRegistryError):
    """A provider identifier was registered more than once."""


class UnknownProviderError(ProviderRegistryError):
    """No factory is registered for the selected provider identifier."""


@dataclass(frozen=True, slots=True)
class ProviderFactoryRequest:
    """Ephemeral factory input which must never be persisted or logged."""

    descriptor: ProviderDescriptor
    route: ProviderRoute
    credential_source: CredentialSource | None = None
    credential: SecretStr | None = field(default=None, repr=False)


class ProviderFactory(Protocol):
    """Build one short-lived provider client for one runtime attempt."""

    def __call__(self, request: ProviderFactoryRequest, /) -> LLMProvider: ...


@dataclass(frozen=True, slots=True)
class _ProviderRegistration:
    descriptor: ProviderDescriptor
    factory: ProviderFactory = field(repr=False)


class ProviderRegistry:
    """Thread-safe descriptors and factories, with no client/secret retention."""

    def __init__(self) -> None:
        self._registrations: dict[str, _ProviderRegistration] = {}
        self._lock = RLock()

    def register(self, descriptor: ProviderDescriptor, factory: ProviderFactory) -> None:
        """Register one descriptor/factory pair without constructing a client."""

        with self._lock:
            if descriptor.provider_id in self._registrations:
                raise DuplicateProviderError(
                    f"provider is already registered: {descriptor.provider_id}"
                )
            self._registrations[descriptor.provider_id] = _ProviderRegistration(
                descriptor=descriptor,
                factory=factory,
            )

    def descriptor(self, provider_id: str) -> ProviderDescriptor:
        normalized = provider_id.strip().lower()
        with self._lock:
            registration = self._registrations.get(normalized)
        if registration is None:
            raise UnknownProviderError(f"provider is not registered: {normalized}")
        return registration.descriptor

    def descriptors(self) -> tuple[ProviderDescriptor, ...]:
        """Return a deterministic, serializable registry snapshot."""

        with self._lock:
            return tuple(
                registration.descriptor for _, registration in sorted(self._registrations.items())
            )

    def create(
        self,
        route: ProviderRoute,
        credential: ResolvedCredential | None = None,
    ) -> LLMProvider:
        """Create a fresh provider and retain neither request nor client."""

        with self._lock:
            registration = self._registrations.get(route.provider_id)
        if registration is None:
            raise UnknownProviderError(f"provider is not registered: {route.provider_id}")
        request = ProviderFactoryRequest(
            descriptor=registration.descriptor,
            route=route,
            credential_source=None if credential is None else credential.source,
            credential=None if credential is None else credential.secret,
        )
        return registration.factory(request)

    def __repr__(self) -> str:
        provider_ids = tuple(descriptor.provider_id for descriptor in self.descriptors())
        return f"ProviderRegistry(provider_ids={provider_ids!r})"


__all__ = [
    "DuplicateProviderError",
    "ProviderFactory",
    "ProviderFactoryRequest",
    "ProviderRegistry",
    "ProviderRegistryError",
    "UnknownProviderError",
]
