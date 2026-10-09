"""Fail-closed Python keyring adapter.

Only platform keychains supplied by the core ``keyring`` distribution are
accepted.  Null, fail, chainer and third-party ``keyrings.alt`` backends are
rejected so an unavailable secure backend can never become plaintext storage.
"""

from __future__ import annotations

from typing import Any, Protocol

import keyring
from keyring.errors import KeyringError, PasswordDeleteError
from pydantic import SecretStr

from imaginarium_forge.application.ports.credentials import (
    CREDENTIAL_SERVICE_NAME,
    CredentialOperationError,
    CredentialStatus,
    CredentialStoreUnavailableError,
)


class _KeyringApi(Protocol):
    def get_keyring(self) -> Any: ...

    def get_password(self, service_name: str, username: str) -> str | None: ...

    def set_password(self, service_name: str, username: str, password: str) -> None: ...

    def delete_password(self, service_name: str, username: str) -> None: ...


_REJECTED_BACKEND_PARTS = (
    "backends.null",
    "backends.fail",
    "backends.chainer",
    "keyrings.alt",
)
_APPROVED_CORE_BACKENDS = (
    "keyring.backends.Windows",
    "keyring.backends.macOS",
    "keyring.backends.SecretService",
    "keyring.backends.kwallet",
)


class DisabledCredentialStore:
    """Explicit no-op adapter used by tests and isolated smoke environments."""

    def available(self) -> bool:
        return False

    def status(self, credential_id: str) -> CredentialStatus:
        del credential_id
        return CredentialStatus(
            available=False,
            backend_name="disabled",
            detail="裝置安全保存已在此隔離環境停用",
        )

    def get(self, credential_id: str) -> SecretStr | None:
        del credential_id
        return None

    def set(self, credential_id: str, value: SecretStr) -> None:
        del credential_id, value
        raise CredentialStoreUnavailableError("裝置安全保存不可用")

    def delete(self, credential_id: str) -> None:
        del credential_id
        raise CredentialStoreUnavailableError("裝置安全保存不可用")


class KeyringCredentialStore:
    """Store one secret in an approved operating-system keychain."""

    def __init__(self, api: _KeyringApi | None = None) -> None:
        self._api = api or keyring

    def _backend(self) -> tuple[bool, str]:
        try:
            backend = self._api.get_keyring()
            backend_type = type(backend)
            name = f"{backend_type.__module__}.{backend_type.__name__}"
            priority = float(getattr(backend, "priority", 0))
        except Exception:
            return False, "unavailable"
        lowered = name.casefold()
        approved = any(name.startswith(prefix) for prefix in _APPROVED_CORE_BACKENDS)
        rejected = any(part.casefold() in lowered for part in _REJECTED_BACKEND_PARTS)
        return bool(priority > 0 and approved and not rejected), name

    def available(self) -> bool:
        return self._backend()[0]

    def status(self, credential_id: str) -> CredentialStatus:
        available, backend_name = self._backend()
        if not available:
            return CredentialStatus(
                available=False,
                backend_name=backend_name,
                detail="裝置安全保存不可用；可改用本次工作階段或環境變數",
            )
        try:
            exists = bool(self._api.get_password(CREDENTIAL_SERVICE_NAME, credential_id))
        except Exception:
            return CredentialStatus(
                available=False,
                backend_name=backend_name,
                detail="裝置安全保存目前無法讀取；未改用明文儲存",
            )
        return CredentialStatus(
            available=True,
            exists=exists,
            backend_name=backend_name,
            detail="已連接作業系統安全憑證庫",
        )

    def _require_available(self) -> None:
        if not self.available():
            raise CredentialStoreUnavailableError("裝置安全保存不可用")

    def get(self, credential_id: str) -> SecretStr | None:
        self._require_available()
        try:
            value = self._api.get_password(CREDENTIAL_SERVICE_NAME, credential_id)
        except Exception as exc:
            raise CredentialOperationError("無法從裝置安全保存讀取憑證") from exc
        normalized = value.strip() if isinstance(value, str) else ""
        return SecretStr(normalized) if normalized else None

    def set(self, credential_id: str, value: SecretStr) -> None:
        self._require_available()
        secret = value.get_secret_value().strip()
        if not secret:
            raise CredentialOperationError("不能保存空白憑證")
        try:
            self._api.set_password(CREDENTIAL_SERVICE_NAME, credential_id, secret)
        except Exception as exc:
            raise CredentialOperationError("無法將憑證保存到裝置安全保存") from exc

    def delete(self, credential_id: str) -> None:
        self._require_available()
        try:
            self._api.delete_password(CREDENTIAL_SERVICE_NAME, credential_id)
        except PasswordDeleteError:
            return
        except KeyringError as exc:
            raise CredentialOperationError("無法從裝置安全保存刪除憑證") from exc
        except Exception as exc:
            raise CredentialOperationError("無法從裝置安全保存刪除憑證") from exc


__all__ = ["DisabledCredentialStore", "KeyringCredentialStore"]
