"""Normalized provider errors ."""

from typing import ClassVar


class ProviderError(Exception):
    """Base class for all normalized provider errors."""

    def __init__(self, message: str, *, retriable: bool = False) -> None:
        super().__init__(message)
        self.retriable = retriable


class ProviderUnavailableError(ProviderError):
    """The provider endpoint cannot be reached."""

    def __init__(self, message: str) -> None:
        super().__init__(message, retriable=True)


class ProviderTimeoutError(ProviderError):
    """The request exceeded its timeout."""

    def __init__(self, message: str) -> None:
        super().__init__(message, retriable=True)


class ProviderConfigurationError(ProviderError):
    """Required runtime-only provider configuration is absent or invalid."""


class ProviderAuthenticationError(ProviderError):
    """The provider rejected its runtime credential."""


class ProviderRateLimitError(ProviderError):
    """The provider throttled the request and it may succeed later."""

    def __init__(self, message: str) -> None:
        super().__init__(message, retriable=True)


class ProviderQuotaError(ProviderError):
    """The provider reported an account quota or billing limit."""


class ModelNotFoundError(ProviderError):
    """The requested model is not available on the provider."""


class RequestCancelledError(ProviderError):
    """The caller cancelled the request (best-effort interruption)."""


class ProviderRefusalError(ProviderError):
    """The provider returned a machine-readable safety refusal."""


class ProviderIncompleteError(ProviderError):
    """The provider ended prose generation without a complete terminal result.

    ``reason`` is deliberately normalized through a small allow-list. Remote
    bodies, generated prose, endpoint credentials, and arbitrary provider text
    therefore cannot enter ``str(error)`` or ``repr(error)``.

    Max-output truncation needs a continuation rather than a blind retry, and a
    content filter must fail closed. Only a transport interruption is marked
    retriable for the shared runtime's bounded retry policy.
    """

    _REASON_ALIASES: ClassVar[dict[str, str]] = {
        "max_output": "max_output_tokens",
        "max_output_tokens": "max_output_tokens",
        "length": "max_output_tokens",
        "content_filter": "content_filter",
        "transport_interruption": "transport_interruption",
        "unknown": "unknown",
    }

    def __init__(self, reason: object) -> None:
        candidate = reason if isinstance(reason, str) else "unknown"
        normalized = self._REASON_ALIASES.get(candidate.strip().casefold(), "unknown")
        self.reason = normalized
        super().__init__(
            f"provider response was incomplete ({normalized})",
            retriable=normalized == "transport_interruption",
        )


class InvalidProviderResponseError(ProviderError):
    """Provider returned a syntactically or structurally invalid response.

    Covers: malformed JSON bodies, non-object JSON where an object is required,
    missing required response fields, and malformed NDJSON stream chunks. Raw
    excerpts attached to messages are truncated. Transport interruptions and
    machine-readable incomplete responses use ``ProviderIncompleteError``.
    """


class InvalidStructuredOutputError(ProviderError):
    """Model output could not be parsed/validated against the requested schema."""

    def __init__(self, message: str, *, raw_text: str, validation_errors: str) -> None:
        super().__init__(message)
        self.raw_text = raw_text
        self.validation_errors = validation_errors
