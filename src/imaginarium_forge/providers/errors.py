"""Normalized provider errors (provider-independent contract, ADR-004)."""


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


class InvalidProviderResponseError(ProviderError):
    """Provider returned a syntactically or structurally invalid response.

    Covers: malformed JSON bodies, non-object JSON where an object is required,
    missing required response fields, malformed NDJSON stream chunks, protocol
    violations (httpx.RemoteProtocolError), and streams that end before the
    provider signals completion. Raw excerpts attached to messages are truncated.
    """


class InvalidStructuredOutputError(ProviderError):
    """Model output could not be parsed/validated against the requested schema."""

    def __init__(self, message: str, *, raw_text: str, validation_errors: str) -> None:
        super().__init__(message)
        self.raw_text = raw_text
        self.validation_errors = validation_errors
