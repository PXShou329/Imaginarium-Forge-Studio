"""Credential storage and resolution contracts.

Secrets cross this boundary only as :class:`pydantic.SecretStr`.  Status and
errors intentionally carry metadata only; they never include a credential
value or an underlying backend exception message.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol

from pydantic import SecretStr

OPENAI_CREDENTIAL_ID = "openai-api-key/default"
CREDENTIAL_SERVICE_NAME = "Imaginarium-Forge"


class CredentialError(RuntimeError):
    """Base class for sanitized credential failures."""


class CredentialStoreUnavailableError(CredentialError):
    """No approved secure operating-system backend is available."""


class CredentialOperationError(CredentialError):
    """A secure backend rejected an operation without exposing its details."""


@dataclass(frozen=True, slots=True)
class CredentialStatus:
    """Non-secret device-store status safe for UI, diagnostics and logs."""

    available: bool
    exists: bool = False
    backend_name: str = "unavailable"
    detail: str = "裝置安全保存不可用"


class CredentialStore(Protocol):
    """Application-level secure credential port."""

    def available(self) -> bool: ...

    def status(self, credential_id: str) -> CredentialStatus: ...

    def get(self, credential_id: str) -> SecretStr | None: ...

    def set(self, credential_id: str, value: SecretStr) -> None: ...

    def delete(self, credential_id: str) -> None: ...


class CredentialSource(StrEnum):
    SESSION = "session"
    DEVICE = "credential_store"
    ENVIRONMENT = "environment"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class ResolvedCredential:
    """One runtime-only resolution result.

    ``secret`` is hidden from dataclass repr and callers must not serialize the
    object.  Persistent commands and job rows store only ``source``.
    """

    source: CredentialSource
    secret: SecretStr | None = field(default=None, repr=False)
    device_status: CredentialStatus | None = None

    @property
    def available(self) -> bool:
        return bool(self.secret is not None and self.secret.get_secret_value())


def _normalized_secret(value: object) -> SecretStr | None:
    if isinstance(value, SecretStr):
        raw = value.get_secret_value()
    elif isinstance(value, str):
        raw = value
    else:
        return None
    normalized = raw.strip()
    return SecretStr(normalized) if normalized else None


class CredentialResolver:
    """Resolve credentials at execution time using the mandated precedence."""

    def __init__(
        self,
        store: CredentialStore,
        *,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        self._store = store
        self._environ = os.environ if environ is None else environ

    def resolve(
        self,
        credential_id: str,
        *,
        session_override: SecretStr | str | None = None,
    ) -> ResolvedCredential:
        session = _normalized_secret(session_override)
        if session is not None:
            return ResolvedCredential(CredentialSource.SESSION, session)

        status = self._store.status(credential_id)
        if status.available:
            try:
                stored = self._store.get(credential_id)
            except CredentialError:
                stored = None
            if stored is not None and stored.get_secret_value():
                return ResolvedCredential(
                    CredentialSource.DEVICE,
                    stored,
                    device_status=status,
                )

        environment = _normalized_secret(self._environ.get("OPENAI_API_KEY"))
        if environment is not None:
            return ResolvedCredential(
                CredentialSource.ENVIRONMENT,
                environment,
                device_status=status,
            )
        return ResolvedCredential(
            CredentialSource.UNAVAILABLE,
            device_status=status,
        )


__all__ = [
    "CREDENTIAL_SERVICE_NAME",
    "OPENAI_CREDENTIAL_ID",
    "CredentialError",
    "CredentialOperationError",
    "CredentialResolver",
    "CredentialSource",
    "CredentialStatus",
    "CredentialStore",
    "CredentialStoreUnavailableError",
    "ResolvedCredential",
]
