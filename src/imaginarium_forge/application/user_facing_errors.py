"""Typed, redacted error state for product-facing recovery UI.

The mapper in this module deliberately does not expose ``str(exception)``.
Provider bodies, SQL errors, paths, and author content may all be embedded in
exception messages.  Pages can therefore map and present a failure without
turning an implementation detail into user-visible copy.

This module only describes the failure and available recovery actions.  It
never retries, falls back, mutates editor state, or marks work successful.
Those decisions remain explicit application commands.
"""

from __future__ import annotations

import re
import sqlite3
import uuid
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.exc import SQLAlchemyError

from imaginarium_forge.application.errors import (
    ApplicationError,
    ConflictError,
    NotFoundError,
    ValidationFailedError,
)
from imaginarium_forge.application.ports.credentials import (
    CredentialError,
    CredentialOperationError,
    CredentialStoreUnavailableError,
)
from imaginarium_forge.providers.errors import (
    InvalidProviderResponseError,
    InvalidStructuredOutputError,
    ModelNotFoundError,
    ProviderAuthenticationError,
    ProviderConfigurationError,
    ProviderError,
    ProviderIncompleteError,
    ProviderQuotaError,
    ProviderRateLimitError,
    ProviderRefusalError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    RequestCancelledError,
)


class ErrorCategory(StrEnum):
    """Stable categories used by presenters, diagnostics, and tests."""

    VALIDATION = "validation"
    CONFLICT = "conflict"
    NOT_FOUND = "not_found"
    AUTHENTICATION = "authentication"
    QUOTA = "quota"
    RATE_LIMIT = "rate_limit"
    MODEL_NOT_FOUND = "model_not_found"
    TIMEOUT = "timeout"
    UNAVAILABLE = "unavailable"
    CONFIGURATION = "configuration"
    REFUSAL = "refusal"
    INVALID_RESPONSE = "invalid_response"
    CANCELLED = "cancelled"
    PROVIDER = "provider_error"
    DATA_LAYER = "data_layer"
    APPLICATION = "application_error"
    UNEXPECTED = "unexpected_error"


class ErrorDisposition(StrEnum):
    """Failure states that must not be rendered as completed success."""

    FAILED = "failed"
    PARTIAL = "partial"
    CANCELLED = "cancelled"


class ErrorSeverity(StrEnum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class RecoveryActionKind(StrEnum):
    RETRY = "retry"
    REVIEW_INPUT = "review_input"
    OPEN_SETTINGS = "open_settings"
    CHANGE_PROVIDER = "change_provider"
    CHECK_ACCOUNT = "check_account"
    CHECK_SERVICE = "check_service"
    RELOAD_STATE = "reload_state"
    RETURN = "return"
    OPEN_SYSTEM_HEALTH = "open_system_health"
    CUSTOM = "custom"


class RecoveryAction(BaseModel):
    """One explicit action a page may choose to wire after presentation."""

    model_config = ConfigDict(frozen=True)

    action_id: str = Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")
    label_zh_tw: str = Field(min_length=1, max_length=80)
    kind: RecoveryActionKind = RecoveryActionKind.CUSTOM

    @field_validator("label_zh_tw")
    @classmethod
    def _strip_label(cls, value: str) -> str:
        return value.strip()


class ErrorDataSafety(BaseModel):
    """What the application can honestly assert remained safe.

    ``None`` means the boundary cannot prove that state.  It is intentionally
    different from ``False`` so a generic handler never invents a data-loss or
    data-preservation claim.
    """

    model_config = ConfigDict(frozen=True)

    message_zh_tw: str = Field(min_length=1, max_length=500)
    author_input_preserved: bool | None = None
    original_preserved: bool | None = None
    partial_preserved: bool | None = None
    completed_work_preserved: bool | None = None
    draft_selection_preserved: bool | None = None

    @field_validator("message_zh_tw")
    @classmethod
    def _strip_message(cls, value: str) -> str:
        return value.strip()


class ErrorMappingContext(BaseModel):
    """Safe facts supplied by the action boundary performing the mapping.

    Retry and fallback default to disabled.  A page may not infer either from
    a button label; the calling application policy must opt in explicitly.
    """

    model_config = ConfigDict(frozen=True)

    operation_zh_tw: str = Field(min_length=1, max_length=100)
    data_safety: ErrorDataSafety
    provider_label: str = Field(default="模型服務", max_length=50)
    retry_permitted: bool = False
    fallback_permitted: bool = False
    job_id: str = Field(default="", max_length=128)
    step_id: str = Field(default="", max_length=128)
    additional_actions: tuple[RecoveryAction, ...] = Field(default=(), max_length=3)

    @field_validator("operation_zh_tw", "provider_label", "job_id", "step_id")
    @classmethod
    def _strip_text(cls, value: str) -> str:
        return value.strip()


class UserFacingError(BaseModel):
    """Fully normalized failure state safe for normal product UI."""

    model_config = ConfigDict(frozen=True)

    code: str = Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")
    category: ErrorCategory
    disposition: ErrorDisposition
    severity: ErrorSeverity
    title_zh_tw: str = Field(min_length=1, max_length=160)
    summary_zh_tw: str = Field(min_length=1, max_length=500)
    impact_zh_tw: str = Field(min_length=1, max_length=500)
    data_safety: ErrorDataSafety
    actions: tuple[RecoveryAction, ...] = Field(default=(), max_length=3)
    incident_id: str = Field(pattern=r"^IF-[A-F0-9]{12}$")
    retryable: bool = False
    fallback_allowed: bool = False
    job_id: str = ""
    step_id: str = ""
    technical_details_zh_tw: str = Field(min_length=1, max_length=800)

    @property
    def correlation_id(self) -> str:
        """Alias used by specifications that call the incident a correlation ID."""

        return self.incident_id


_BEARER_RE = re.compile(
    r"(?i)\b(?:authorization\s*[:=]\s*)?bearer\s+[a-z0-9._~+/=-]{8,}"
)
_SECRET_TOKEN_RE = re.compile(r"(?i)\b(?:sk|rk|pk)-(?:proj-)?[a-z0-9_-]{8,}\b")
_KEY_VALUE_RE = re.compile(
    r"(?i)\b(api[_-]?key|access[_-]?token|refresh[_-]?token|password|secret)\b"
    r"\s*[:=]\s*(?:\"[^\"\r\n]*\"|'[^'\r\n]*'|[^\s,;]+)"
)
_CONNECTION_RE = re.compile(r"(?i)\b(?:sqlite|postgres(?:ql)?|mysql)://\S+")
_WINDOWS_PATH_RE = re.compile(r"(?i)(?<![a-z0-9_])[a-z]:\\[^\r\n\t]+")
_UNC_PATH_RE = re.compile(r"\\\\[^\\\s]+\\[^\r\n\t]+")
_UNIX_PATH_RE = re.compile(
    r"(?<![a-z0-9_:])/(?:home|users|tmp|var|etc|opt|srv|mnt|private|volumes)/"
    r"[^\s,;)\]}]+",
    re.IGNORECASE,
)
_STRUCTURED_RE = re.compile(r"\{[^{}\r\n]{2,}\}|\[[^\[\]\r\n]{2,}\]")
_SQL_RE = re.compile(
    r"(?im)\b(?:select|insert|update|delete|create|alter|drop|pragma)\b[^\r\n]*"
)


def redact_sensitive_text(value: object, *, limit: int = 4_000) -> str:
    """Aggressively redact common diagnostic hazards from arbitrary text.

    This is defense in depth for diagnostic metadata.  Normal UI copy should
    still come from the allow-listed mapper below, never from sanitized raw
    exception messages.
    """

    text = str(value or "")
    # Replace raw JSON/list-shaped payloads before inserting our own bracketed
    # redaction markers; otherwise the structured-data pattern would consume
    # those markers on a later pass.
    text = _STRUCTURED_RE.sub("[REDACTED_STRUCTURED_DATA]", text)
    text = _BEARER_RE.sub("[REDACTED_AUTHORIZATION]", text)
    text = _SECRET_TOKEN_RE.sub("[REDACTED_SECRET]", text)
    text = _KEY_VALUE_RE.sub(lambda match: f"{match.group(1)}=[REDACTED]", text)
    text = _CONNECTION_RE.sub("[REDACTED_CONNECTION]", text)
    text = _WINDOWS_PATH_RE.sub("[REDACTED_PATH]", text)
    text = _UNC_PATH_RE.sub("[REDACTED_PATH]", text)
    text = _UNIX_PATH_RE.sub("[REDACTED_PATH]", text)
    text = _SQL_RE.sub("[REDACTED_SQL]", text)
    if len(text) > limit:
        return f"{text[: max(0, limit - 1)]}…"
    return text


def new_incident_id() -> str:
    """Return a non-secret, local correlation identifier."""

    return f"IF-{uuid.uuid4().hex[:12].upper()}"


def map_user_facing_error(
    error: BaseException,
    *,
    context: ErrorMappingContext,
    incident_id: str | None = None,
) -> UserFacingError:
    """Map one exception to an allow-listed, actionable product error.

    The original exception remains available to the caller for a separate
    sanitized incident logger.  It is never swallowed or copied into this
    returned UI state.
    """

    normalized_incident_id = _incident_id_or_new(incident_id)
    operation = _safe_label(context.operation_zh_tw, fallback="這次操作")
    provider = _safe_label(context.provider_label, fallback="模型服務")
    category, code, summary, base_retryable, severity = _classify(error, provider)
    retryable = bool(base_retryable and context.retry_permitted)
    disposition = _disposition(error, context.data_safety)
    actions = _actions_for(
        category,
        retryable=retryable,
        fallback_allowed=context.fallback_permitted,
        additional=context.additional_actions,
    )
    data_safety = context.data_safety.model_copy(
        update={
            "message_zh_tw": _safe_copy(
                context.data_safety.message_zh_tw,
                fallback="系統無法確認資料保存狀態；請先不要重複送出。",
            )
        }
    )
    safe_job_id = _safe_identifier(context.job_id)
    safe_step_id = _safe_identifier(context.step_id)
    exception_type = _safe_identifier(type(error).__name__) or "Exception"
    technical = (
        f"錯誤代碼：{code}\n"
        f"例外類型：{exception_type}\n"
        f"事件編號：{normalized_incident_id}"
    )
    if safe_job_id:
        technical += f"\n工作編號：{safe_job_id}"
    if safe_step_id:
        technical += f"\n步驟編號：{safe_step_id}"

    title = (
        f"{operation}已取消"
        if disposition is ErrorDisposition.CANCELLED
        else f"{operation}未完成"
    )
    return UserFacingError(
        code=code,
        category=category,
        disposition=disposition,
        severity=severity,
        title_zh_tw=title,
        summary_zh_tw=summary,
        impact_zh_tw=_impact_for(disposition),
        data_safety=data_safety,
        actions=actions,
        incident_id=normalized_incident_id,
        retryable=retryable,
        fallback_allowed=context.fallback_permitted,
        job_id=safe_job_id,
        step_id=safe_step_id,
        technical_details_zh_tw=technical,
    )


def _classify(
    error: BaseException, provider: str
) -> tuple[ErrorCategory, str, str, bool, ErrorSeverity]:
    if isinstance(error, CredentialStoreUnavailableError):
        return (
            ErrorCategory.CONFIGURATION,
            "credential_store_unavailable",
            "這台裝置目前沒有可核准的安全憑證庫。"
            "請改用 session-only 或環境變數；系統不會退回明文保存。",
            False,
            ErrorSeverity.WARNING,
        )
    if isinstance(error, CredentialOperationError | CredentialError):
        return (
            ErrorCategory.CONFIGURATION,
            "credential_operation_failed",
            "安全憑證庫未能完成這次操作。既有 Key 不會在介面中回顯，"
            "請改用 session-only 或稍後重試。",
            False,
            ErrorSeverity.ERROR,
        )
    if isinstance(error, ProviderAuthenticationError):
        return (
            ErrorCategory.AUTHENTICATION,
            "provider_authentication",
            f"{provider} 無法驗證目前的憑證。請替換 API Key 或改用其他已設定的服務。",
            False,
            ErrorSeverity.ERROR,
        )
    if isinstance(error, ProviderQuotaError):
        return (
            ErrorCategory.QUOTA,
            "provider_quota",
            f"{provider} 未接受本次請求，可能是額度或計費限制。請檢查帳務或改用其他服務。",
            False,
            ErrorSeverity.ERROR,
        )
    if isinstance(error, ProviderRateLimitError):
        return (
            ErrorCategory.RATE_LIMIT,
            "provider_rate_limit",
            f"{provider} 暫時限制請求速率。可在安全等待後重試。",
            True,
            ErrorSeverity.WARNING,
        )
    if isinstance(error, ModelNotFoundError):
        return (
            ErrorCategory.MODEL_NOT_FOUND,
            "provider_model_not_found",
            f"{provider} 找不到或不允許使用所選模型。請重新選擇模型。",
            False,
            ErrorSeverity.ERROR,
        )
    if isinstance(error, ProviderTimeoutError | TimeoutError):
        return (
            ErrorCategory.TIMEOUT,
            "provider_timeout",
            f"{provider} 在期限內沒有完成。可縮短內容、檢查連線，或在安全時重試。",
            True,
            ErrorSeverity.WARNING,
        )
    if isinstance(error, ProviderUnavailableError):
        return (
            ErrorCategory.UNAVAILABLE,
            "provider_unavailable",
            f"目前無法連接 {provider}。請檢查服務與網路狀態。",
            True,
            ErrorSeverity.WARNING,
        )
    if isinstance(error, ProviderConfigurationError):
        return (
            ErrorCategory.CONFIGURATION,
            "provider_configuration",
            f"{provider} 尚未完成安全設定。請檢查憑證、端點與模型設定。",
            False,
            ErrorSeverity.ERROR,
        )
    if isinstance(error, ProviderRefusalError):
        return (
            ErrorCategory.REFUSAL,
            "provider_refusal",
            f"{provider} 拒絕處理這次內容。請調整要求後再試。",
            False,
            ErrorSeverity.WARNING,
        )
    if isinstance(error, ProviderIncompleteError):
        if error.reason == "content_filter":
            return (
                ErrorCategory.REFUSAL,
                "provider_refusal",
                f"{provider} 因內容安全政策中止這次生成。請調整要求後再試。",
                False,
                ErrorSeverity.WARNING,
            )
        if error.reason == "max_output_tokens":
            return (
                ErrorCategory.INVALID_RESPONSE,
                "provider_incomplete_output",
                f"{provider} 在完整回應前達到輸出長度上限；未建立可套用的結果。",
                False,
                ErrorSeverity.WARNING,
            )
        return (
            ErrorCategory.UNAVAILABLE,
            "provider_incomplete_response",
            f"{provider} 的回應在完成前中斷；未建立可套用的結果。",
            error.retriable,
            ErrorSeverity.WARNING,
        )
    if isinstance(error, InvalidStructuredOutputError | InvalidProviderResponseError):
        return (
            ErrorCategory.INVALID_RESPONSE,
            "provider_invalid_response",
            f"{provider} 的回應格式不完整或無法安全解析，未建立可套用的結果。",
            True,
            ErrorSeverity.ERROR,
        )
    if isinstance(error, RequestCancelledError):
        return (
            ErrorCategory.CANCELLED,
            "operation_cancelled",
            "作者已取消這次操作；系統沒有把 partial 標成完整結果。",
            False,
            ErrorSeverity.INFO,
        )
    if isinstance(error, ProviderError):
        return (
            ErrorCategory.PROVIDER,
            "provider_error",
            f"{provider} 沒有完成這次操作。請檢查服務狀態與設定。",
            error.retriable,
            ErrorSeverity.ERROR,
        )
    if isinstance(error, ValidationFailedError | ValueError):
        return (
            ErrorCategory.VALIDATION,
            "validation_failed",
            "輸入或目前狀態未通過驗證。請檢查標示欄位後再試。",
            False,
            ErrorSeverity.WARNING,
        )
    if isinstance(error, ConflictError):
        return (
            ErrorCategory.CONFLICT,
            "state_conflict",
            "資料在操作期間已變更。請重新載入最新狀態，再決定是否重做。",
            False,
            ErrorSeverity.WARNING,
        )
    if isinstance(error, NotFoundError | LookupError):
        return (
            ErrorCategory.NOT_FOUND,
            "resource_not_found",
            "找不到這次操作需要的資料；它可能已被移動、封存或變更。",
            False,
            ErrorSeverity.WARNING,
        )
    if isinstance(error, sqlite3.DatabaseError | SQLAlchemyError):
        return (
            ErrorCategory.DATA_LAYER,
            "data_layer_error",
            "本機資料層未能安全完成操作。請停止重複寫入並開啟系統健康檢查。",
            False,
            ErrorSeverity.ERROR,
        )
    if isinstance(error, ApplicationError):
        return (
            ErrorCategory.APPLICATION,
            "application_error",
            "應用程式規則阻止了這次操作。請檢查目前狀態與必要前置步驟。",
            False,
            ErrorSeverity.ERROR,
        )
    return (
        ErrorCategory.UNEXPECTED,
        "unexpected_error",
        "發生未預期的問題，這次操作沒有被標示為成功。請使用事件編號查看去敏診斷。",
        False,
        ErrorSeverity.ERROR,
    )


def _disposition(error: BaseException, safety: ErrorDataSafety) -> ErrorDisposition:
    if isinstance(error, RequestCancelledError):
        return ErrorDisposition.CANCELLED
    if safety.partial_preserved is True:
        return ErrorDisposition.PARTIAL
    return ErrorDisposition.FAILED


def _impact_for(disposition: ErrorDisposition) -> str:
    if disposition is ErrorDisposition.CANCELLED:
        return "本次操作已停止，沒有建立或套用完整成功結果。"
    if disposition is ErrorDisposition.PARTIAL:
        return "本次操作尚未完整完成；已保存的 partial 需要先審閱，不會自動套用。"
    return "本次操作未完成，不會被標記為成功，也不會自動套用結果。"


def _actions_for(
    category: ErrorCategory,
    *,
    retryable: bool,
    fallback_allowed: bool,
    additional: tuple[RecoveryAction, ...],
) -> tuple[RecoveryAction, ...]:
    actions: list[RecoveryAction] = []
    if retryable:
        actions.append(
            RecoveryAction(
                action_id="retry",
                label_zh_tw="重試這次操作",
                kind=RecoveryActionKind.RETRY,
            )
        )
    if category in {ErrorCategory.AUTHENTICATION, ErrorCategory.CONFIGURATION}:
        actions.append(
            RecoveryAction(
                action_id="open_ai_settings",
                label_zh_tw="前往 AI 設定",
                kind=RecoveryActionKind.OPEN_SETTINGS,
            )
        )
    elif category is ErrorCategory.QUOTA:
        actions.append(
            RecoveryAction(
                action_id="check_account",
                label_zh_tw="檢查帳戶用量",
                kind=RecoveryActionKind.CHECK_ACCOUNT,
            )
        )
    elif category is ErrorCategory.MODEL_NOT_FOUND:
        actions.append(
            RecoveryAction(
                action_id="choose_model",
                label_zh_tw="重新選擇模型",
                kind=RecoveryActionKind.OPEN_SETTINGS,
            )
        )
    elif category in {ErrorCategory.UNAVAILABLE, ErrorCategory.TIMEOUT}:
        actions.append(
            RecoveryAction(
                action_id="check_service",
                label_zh_tw="檢查服務狀態",
                kind=RecoveryActionKind.CHECK_SERVICE,
            )
        )
    elif category in {ErrorCategory.VALIDATION, ErrorCategory.REFUSAL}:
        actions.append(
            RecoveryAction(
                action_id="review_input",
                label_zh_tw="檢查輸入內容",
                kind=RecoveryActionKind.REVIEW_INPUT,
            )
        )
    elif category is ErrorCategory.CONFLICT:
        actions.append(
            RecoveryAction(
                action_id="reload_state",
                label_zh_tw="重新載入最新狀態",
                kind=RecoveryActionKind.RELOAD_STATE,
            )
        )
    elif category is ErrorCategory.NOT_FOUND:
        actions.append(
            RecoveryAction(
                action_id="return",
                label_zh_tw="返回上一層",
                kind=RecoveryActionKind.RETURN,
            )
        )
    elif category in {
        ErrorCategory.DATA_LAYER,
        ErrorCategory.APPLICATION,
        ErrorCategory.UNEXPECTED,
    }:
        actions.append(
            RecoveryAction(
                action_id="open_system_health",
                label_zh_tw="開啟系統健康檢查",
                kind=RecoveryActionKind.OPEN_SYSTEM_HEALTH,
            )
        )
    if fallback_allowed:
        actions.append(
            RecoveryAction(
                action_id="change_provider",
                label_zh_tw="選擇其他服務",
                kind=RecoveryActionKind.CHANGE_PROVIDER,
            )
        )
    actions.extend(additional)

    unique: list[RecoveryAction] = []
    seen: set[str] = set()
    for action in actions:
        if action.action_id in seen:
            continue
        seen.add(action.action_id)
        unique.append(action)
        if len(unique) == 3:
            break
    return tuple(unique)


def _incident_id_or_new(value: str | None) -> str:
    normalized = str(value or "").strip().upper()
    if re.fullmatch(r"IF-[A-F0-9]{12}", normalized):
        return normalized
    return new_incident_id()


def _safe_identifier(value: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        return ""
    if re.search(r"(?i)(?:bearer|api[_-]?key|secret|password|sk-)", normalized):
        return "[REDACTED]"
    return re.sub(r"[^A-Za-z0-9_.:-]", "_", normalized)[:128]


def _safe_label(value: str, *, fallback: str) -> str:
    normalized = redact_sensitive_text(value, limit=100)
    normalized = re.sub(r"[^\w\s\u3400-\u9fff.()／-]", "", normalized).strip()
    return normalized or fallback


def _safe_copy(value: str, *, fallback: str) -> str:
    normalized = redact_sensitive_text(value, limit=500).strip()
    return normalized or fallback


__all__ = [
    "ErrorCategory",
    "ErrorDataSafety",
    "ErrorDisposition",
    "ErrorMappingContext",
    "ErrorSeverity",
    "RecoveryAction",
    "RecoveryActionKind",
    "UserFacingError",
    "map_user_facing_error",
    "new_incident_id",
    "redact_sensitive_text",
]
