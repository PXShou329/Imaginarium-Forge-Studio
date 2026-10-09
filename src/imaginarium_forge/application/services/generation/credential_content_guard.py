"""Side-effect-free guard against dispatching an active credential as content."""

from __future__ import annotations

from pydantic import SecretStr

from imaginarium_forge.providers.errors import ProviderConfigurationError

_SAFE_REJECTION = (
    "偵測到目前 API Key 出現在即將送出的內容；已阻止傳送，請先移除憑證文字。"
)


def assert_active_credential_not_in_content(
    active_credential: SecretStr | str | None,
    *author_content: str,
) -> None:
    """Reject an exact active-secret match without echoing or retaining the secret."""

    if active_credential is None:
        return
    secret = (
        active_credential.get_secret_value()
        if isinstance(active_credential, SecretStr)
        else active_credential
    )
    if secret and any(secret in content for content in author_content):
        raise ProviderConfigurationError(_SAFE_REJECTION)


__all__ = ["assert_active_credential_not_in_content"]
