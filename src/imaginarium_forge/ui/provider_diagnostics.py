"""Safe, provider-independent troubleshooting messages for authoring pages."""

from __future__ import annotations

from dataclasses import dataclass

from imaginarium_forge.providers.errors import (
    InvalidProviderResponseError,
    InvalidStructuredOutputError,
    ModelNotFoundError,
    ProviderAuthenticationError,
    ProviderConfigurationError,
    ProviderError,
    ProviderQuotaError,
    ProviderRateLimitError,
    ProviderRefusalError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    RequestCancelledError,
)


@dataclass(frozen=True, slots=True)
class ProviderDiagnostic:
    """A redacted diagnosis suitable for UI display and tests."""

    code: str
    message_zh_tw: str
    retriable: bool = False


def _label(provider_label: str) -> str:
    normalized = provider_label.strip()
    return normalized or "模型服務"


def diagnose_provider_error(
    error: BaseException,
    *,
    provider_label: str = "",
) -> ProviderDiagnostic:
    """Classify one original failure without another network request.

    Deliberately never includes ``str(error)``. Remote bodies can contain
    private author text, endpoint details, or credentials and therefore are
    not suitable for user-facing diagnostics or persistence.
    """

    provider = _label(provider_label)
    if isinstance(error, ProviderAuthenticationError):
        return ProviderDiagnostic(
            "authentication",
            f"{provider} 驗證失敗：API Key 無效、已撤銷，或沒有所選專案／模型的權限。"
            "請重新確認 Key。",
        )
    if isinstance(error, ProviderQuotaError):
        return ProviderDiagnostic(
            "quota",
            f"{provider} 回報帳戶額度或計費限制：請檢查 API 帳戶用量、餘額與付費狀態。",
        )
    if isinstance(error, ProviderRateLimitError):
        return ProviderDiagnostic(
            "rate_limit",
            f"{provider} 暫時限制請求速率：稍候再試；若持續發生，再檢查帳戶使用上限。",
            retriable=True,
        )
    if isinstance(error, ModelNotFoundError):
        return ProviderDiagnostic(
            "model_not_found",
            f"{provider} 找不到或不允許使用所選模型：請重新選擇模型或確認模型 ID。",
        )
    if isinstance(error, ProviderTimeoutError):
        return ProviderDiagnostic(
            "timeout",
            f"{provider} 在期限內沒有回應：可稍後重試、縮短上下文，或確認本機／網路狀態。",
            retriable=True,
        )
    if isinstance(error, ProviderUnavailableError):
        return ProviderDiagnostic(
            "unavailable",
            f"目前連不到 {provider}：請確認網路、服務狀態；若是 Ollama，也請確認它已在本機啟動。",
            retriable=True,
        )
    if isinstance(error, ProviderConfigurationError):
        return ProviderDiagnostic(
            "configuration",
            f"{provider} 尚未完成安全設定：請確認 API Key、端點與模型設定。",
        )
    if isinstance(error, ProviderRefusalError):
        return ProviderDiagnostic(
            "refusal",
            f"{provider} 拒絕這次內容；原稿沒有被覆寫。請調整要求後再試。",
        )
    if isinstance(error, (InvalidStructuredOutputError, InvalidProviderResponseError)):
        return ProviderDiagnostic(
            "invalid_response",
            f"{provider} 有回應，但格式不完整或無法解析；原稿沒有被覆寫，可重試或改用其他模型。",
            retriable=True,
        )
    if isinstance(error, RequestCancelledError):
        return ProviderDiagnostic("cancelled", "這次模型操作已取消；原稿沒有被覆寫。")
    if isinstance(error, ProviderError):
        return ProviderDiagnostic(
            "provider_error",
            f"{provider} 沒有完成這次操作；原稿保持不變。請檢查模型與服務狀態後重試。",
            retriable=error.retriable,
        )
    return ProviderDiagnostic(
        "unexpected_error",
        "這次生成沒有完成；原稿保持不變。系統沒有保存 API Key 或原始錯誤內容。",
    )


def diagnose_provider_reason(
    reason: str,
    *,
    provider_label: str = "",
) -> ProviderDiagnostic:
    """Classify a persisted normalized reason code without using raw details."""

    normalized = str(reason or "").strip().casefold()
    provider = _label(provider_label)
    if normalized in {
        "providerauthenticationerror",
        "provider_authentication",
        "authentication",
    }:
        return diagnose_provider_error(
            ProviderAuthenticationError("redacted"), provider_label=provider
        )
    if normalized in {"providerquotaerror", "provider_quota", "quota"}:
        return diagnose_provider_error(ProviderQuotaError("redacted"), provider_label=provider)
    if normalized in {"providerratelimiterror", "provider_rate_limit", "rate_limit"}:
        return diagnose_provider_error(
            ProviderRateLimitError("redacted"), provider_label=provider
        )
    if normalized in {"modelnotfounderror", "model_not_found"}:
        return diagnose_provider_error(ModelNotFoundError("redacted"), provider_label=provider)
    if normalized in {"providertimeouterror", "provider_timeout", "timeout"}:
        return diagnose_provider_error(ProviderTimeoutError("redacted"), provider_label=provider)
    if normalized in {
        "providerunavailableerror",
        "provider_unavailable",
        "unavailable",
    }:
        return diagnose_provider_error(
            ProviderUnavailableError("redacted"), provider_label=provider
        )
    if normalized in {"providerconfigurationerror", "provider_configuration"}:
        return diagnose_provider_error(
            ProviderConfigurationError("redacted"), provider_label=provider
        )
    if normalized in {"empty_generation_output", "invalid_provider_response"}:
        return diagnose_provider_error(
            InvalidProviderResponseError("redacted"), provider_label=provider
        )
    return ProviderDiagnostic(
        "provider_error",
        f"{provider} 沒有完成這次操作；API Key 與原始錯誤內容不會顯示或保存。",
    )


__all__ = [
    "ProviderDiagnostic",
    "diagnose_provider_error",
    "diagnose_provider_reason",
]
