"""Standard native-Streamlit presenter for typed user-facing failures."""

from __future__ import annotations

from typing import Any

import streamlit as st

from imaginarium_forge.application.error_incidents import (
    map_and_record_user_facing_error,
)
from imaginarium_forge.application.errors import ConflictError
from imaginarium_forge.application.services.generation.contracts import (
    GenerationErrorCategory,
    GenerationFailure,
)
from imaginarium_forge.application.user_facing_errors import (
    ErrorDataSafety,
    ErrorDisposition,
    ErrorMappingContext,
    ErrorSeverity,
    RecoveryAction,
    UserFacingError,
    redact_sensitive_text,
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

_UNKNOWN_DATA_SAFETY = ErrorDataSafety(
    message_zh_tw="這次操作未被標示為成功；系統無法確認是否已完成寫入，請先不要重複送出。"
)


def render_error_card(
    error: UserFacingError,
    *,
    key_prefix: str = "user_error",
    surface: Any | None = None,
) -> str | None:
    """Render one failure card and return the selected recovery action ID.

    The presenter never invokes an action itself. Callers retain control of
    retry, navigation, and fallback policy. It also never renders success:
    partial and cancelled are distinct warning/info states.
    """

    selected_action: str | None = None
    ui = st if surface is None else surface
    card_key = f"{key_prefix}_{error.incident_id.lower()}"
    with ui.container(key=card_key):
        _render_status(error, surface=ui)
        ui.markdown(f"**影響**  \n{error.impact_zh_tw}")
        ui.markdown(f"**資料保存狀態**  \n{error.data_safety.message_zh_tw}")

        if error.actions:
            ui.markdown("**下一步**")
            columns = ui.columns(len(error.actions))
            for index, (column, action) in enumerate(
                zip(columns, error.actions, strict=True)
            ):
                if column.button(
                    action.label_zh_tw,
                    key=f"{card_key}_{action.action_id}",
                    type="primary" if index == 0 else "secondary",
                    width="stretch",
                ):
                    selected_action = action.action_id
        else:
            ui.caption("目前沒有可安全自動執行的下一步；請保留事件編號供診斷使用。")

        ui.caption(f"事件編號／Correlation ID：`{error.incident_id}`")
        with ui.expander("技術細節（已去敏）", expanded=False):
            ui.code(
                redact_sensitive_text(error.technical_details_zh_tw, limit=800),
                language=None,
                wrap_lines=True,
            )
    return selected_action


def present_exception(
    error: BaseException,
    *,
    operation_zh_tw: str,
    key_prefix: str,
    data_safety: ErrorDataSafety | None = None,
    provider_label: str = "模型服務",
    retry_permitted: bool = False,
    fallback_permitted: bool = False,
    job_id: str = "",
    step_id: str = "",
    additional_actions: tuple[RecoveryAction, ...] = (),
    surface: Any | None = None,
) -> str | None:
    """Map, record, and render one exception without exposing its message.

    This is the page-boundary convenience entrypoint. Its default data-safety
    statement is intentionally conservative: callers may supply stronger facts
    only when the action boundary can prove them.
    """

    mapped = map_and_record_user_facing_error(
        error,
        context=ErrorMappingContext(
            operation_zh_tw=operation_zh_tw,
            data_safety=data_safety or _UNKNOWN_DATA_SAFETY,
            provider_label=provider_label,
            retry_permitted=retry_permitted,
            fallback_permitted=fallback_permitted,
            job_id=job_id,
            step_id=step_id,
            additional_actions=additional_actions,
        ),
    )
    return render_error_card(mapped, key_prefix=key_prefix, surface=surface)


def map_generation_failure(
    failure: GenerationFailure,
    *,
    operation_zh_tw: str,
) -> UserFacingError:
    """Normalize a typed generation failure through the central error contract.

    ``GenerationFailure.safe_message`` is intentionally not copied into normal
    UI state. The allow-listed mapper supplies product copy, a local
    correlation ID, recovery policy and a precise no-mutation statement.
    """

    error = _generation_failure_exception(failure.category)
    return map_and_record_user_facing_error(
        error,
        context=ErrorMappingContext(
            operation_zh_tw=operation_zh_tw,
            provider_label="AI 服務",
            retry_permitted=failure.retriable,
            fallback_permitted=False,
            data_safety=ErrorDataSafety(
                message_zh_tw=(
                    "這次沒有建立或套用候選；原稿、編輯器內容與已保存版本均未變更。"
                )
            ),
        ),
    )


def _generation_failure_exception(category: GenerationErrorCategory) -> BaseException:
    safe = "typed generation failure"
    if category is GenerationErrorCategory.INCOMPLETE_OUTPUT:
        return ProviderIncompleteError("max_output_tokens")
    mapping: dict[GenerationErrorCategory, type[ProviderError]] = {
        GenerationErrorCategory.AUTHENTICATION: ProviderAuthenticationError,
        GenerationErrorCategory.CONFIGURATION: ProviderConfigurationError,
        GenerationErrorCategory.QUOTA: ProviderQuotaError,
        GenerationErrorCategory.RATE_LIMIT: ProviderRateLimitError,
        GenerationErrorCategory.TIMEOUT: ProviderTimeoutError,
        GenerationErrorCategory.UNAVAILABLE: ProviderUnavailableError,
        GenerationErrorCategory.MODEL_NOT_FOUND: ModelNotFoundError,
        GenerationErrorCategory.CANCELLED: RequestCancelledError,
        GenerationErrorCategory.REFUSAL: ProviderRefusalError,
        GenerationErrorCategory.INVALID_RESPONSE: InvalidProviderResponseError,
        GenerationErrorCategory.INVALID_STRUCTURED_OUTPUT: InvalidStructuredOutputError,
        GenerationErrorCategory.ROUTE_UNAVAILABLE: ProviderConfigurationError,
        GenerationErrorCategory.OFFLINE_FALLBACK_UNAVAILABLE: ProviderConfigurationError,
    }
    error_type = mapping.get(category)
    if error_type is not None:
        if error_type is InvalidStructuredOutputError:
            return error_type(
                safe,
                raw_text="",
                validation_errors="shared runtime rejected structured output",
            )
        return error_type(safe)
    if category in {
        GenerationErrorCategory.DUPLICATE_IN_PROGRESS,
        GenerationErrorCategory.IDEMPOTENCY_CONFLICT,
    }:
        return ConflictError(safe)
    return ProviderError(safe)


def _render_status(error: UserFacingError, *, surface: Any = st) -> None:
    copy = f"**{error.title_zh_tw}**\n\n{error.summary_zh_tw}"
    if error.disposition is ErrorDisposition.CANCELLED:
        surface.info(copy)
    elif error.disposition is ErrorDisposition.PARTIAL or error.severity is ErrorSeverity.WARNING:
        surface.warning(copy)
    else:
        surface.error(copy)


__all__ = ["map_generation_failure", "present_exception", "render_error_card"]
