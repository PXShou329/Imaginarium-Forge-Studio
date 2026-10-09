"""Compatibility adapter from mature provider contracts to the shared runtime.

Legacy Story, Prompt Studio, and creative-planning services already own mature
domain and persistence behavior. Rewriting those services around a second
provider stack would put that behavior at risk. This adapter lets them retain
the narrow:class:`LLMProvider` contract while every actual provider client is
still created lazily by:class:`CreativeGenerationRuntime`.

Preferences are read at the explicit action boundary. Credentials are never
stored here as plain text and are resolved by the runtime on every attempt.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from threading import Event
from typing import Protocol
from uuid import uuid4

from pydantic import BaseModel, SecretStr

from imaginarium_forge.application.services.generation.contracts import (
    FallbackMode,
    GenerationErrorCategory,
    GenerationFailure,
    GenerationInvocation,
    GenerationRoute,
    GenerationTask,
    ProviderRoute,
)
from imaginarium_forge.application.services.generation.preference_routes import (
    task_route_preference,
)
from imaginarium_forge.application.services.generation.runtime import (
    CreativeGenerationRuntime,
)
from imaginarium_forge.domain.generation.preferences import (
    AIFallbackPolicy,
    AIProviderPreference,
    AIRuntimePreferences,
)
from imaginarium_forge.providers.contracts import (
    GenerationRequest,
    GenerationResult,
    GenerationStreamDelta,
    GenerationStreamEvent,
    GenerationStreamResult,
    ModelInfo,
    ProviderHealth,
    StructuredResult,
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


class RuntimePreferenceReader(Protocol):
    """Read the one live non-secret preference row."""

    def get_preferences(self) -> AIRuntimePreferences: ...


IdempotencyKeyFactory = Callable[[], str]
FINAL_WRITING_PROVIDER_ATTEMPT_CEILING = 3


def _random_key() -> str:
    return uuid4().hex


def _provider_error(failure: GenerationFailure) -> ProviderError:
    """Map a sanitized runtime failure back to the mature provider contract."""

    message = failure.safe_message
    category = failure.category
    if category is GenerationErrorCategory.AUTHENTICATION:
        return ProviderAuthenticationError(message)
    if category in {
        GenerationErrorCategory.CONFIGURATION,
        GenerationErrorCategory.ROUTE_UNAVAILABLE,
        GenerationErrorCategory.OFFLINE_FALLBACK_UNAVAILABLE,
        GenerationErrorCategory.DUPLICATE_IN_PROGRESS,
        GenerationErrorCategory.IDEMPOTENCY_CONFLICT,
    }:
        return ProviderConfigurationError(message)
    if category is GenerationErrorCategory.QUOTA:
        return ProviderQuotaError(message)
    if category is GenerationErrorCategory.RATE_LIMIT:
        return ProviderRateLimitError(message)
    if category is GenerationErrorCategory.TIMEOUT:
        return ProviderTimeoutError(message)
    if category is GenerationErrorCategory.UNAVAILABLE:
        return ProviderUnavailableError(message)
    if category is GenerationErrorCategory.MODEL_NOT_FOUND:
        return ModelNotFoundError(message)
    if category is GenerationErrorCategory.CANCELLED:
        return RequestCancelledError(message)
    if category is GenerationErrorCategory.REFUSAL:
        return ProviderRefusalError(message)
    if category is GenerationErrorCategory.INCOMPLETE_OUTPUT:
        return ProviderIncompleteError("max_output_tokens")
    if category is GenerationErrorCategory.INVALID_STRUCTURED_OUTPUT:
        return InvalidStructuredOutputError(
            message,
            raw_text="",
            validation_errors="shared runtime rejected structured output",
        )
    if category is GenerationErrorCategory.INVALID_RESPONSE:
        return InvalidProviderResponseError(message)
    return ProviderError(message, retriable=failure.retriable)


def _provider_error_with_runtime_attempts(
    failure: GenerationFailure,
    *,
    provenance_attempts: int,
) -> ProviderError:
    """Attach safe accounting for mature callers that persist cost evidence."""

    error = _provider_error(failure)
    error._runtime_attempts_used = max(1, provenance_attempts)  # type: ignore[attr-defined]
    return error


class CreativeRuntimeProviderAdapter:
    """Expose one shared-runtime action through the existing provider protocol."""

    name = "creative-generation-runtime"

    def __init__(
        self,
        runtime: CreativeGenerationRuntime,
        preferences: RuntimePreferenceReader,
        *,
        task: GenerationTask,
        action_id: str,
        provider_override: AIProviderPreference | None = None,
        model_override: str | None = None,
        session_override: SecretStr | str | None = None,
        idempotency_key_factory: IdempotencyKeyFactory = _random_key,
    ) -> None:
        self._runtime = runtime
        self._preferences = preferences
        self._task = task
        self._action_id = action_id
        self._provider_override = provider_override
        self._model_override = (model_override or "").strip() or None
        self._session_override = session_override
        self._idempotency_key_factory = idempotency_key_factory

    def generate_text(
        self,
        request: GenerationRequest,
        *,
        cancel: Event | None = None,
    ) -> GenerationResult:
        invocation = self._invocation(request)
        outcome = self._runtime.execute_text(
            invocation,
            session_override=self._session_override,
            cancel=cancel,
        )
        if outcome.result is not None:
            return self._with_runtime_retry_accounting(
                outcome.result,
                provenance_attempts=len(outcome.provenance.attempts),
            )
        assert outcome.failure is not None
        raise _provider_error_with_runtime_attempts(
            outcome.failure,
            provenance_attempts=len(outcome.provenance.attempts),
        )

    def generate_text_with_deltas(
        self,
        request: GenerationRequest,
        *,
        on_delta: Callable[[str], None],
        cancel: Event | None = None,
    ) -> GenerationResult:
        """Execute one genuinely streaming action and surface each raw prose delta."""

        invocation = self._invocation(request)
        outcome = self._runtime.execute_text_stream(
            invocation,
            on_delta=on_delta,
            session_override=self._session_override,
            cancel=cancel,
        )
        if outcome.result is not None:
            return self._with_runtime_retry_accounting(
                outcome.result,
                provenance_attempts=len(outcome.provenance.attempts),
            )
        assert outcome.failure is not None
        raise _provider_error_with_runtime_attempts(
            outcome.failure,
            provenance_attempts=len(outcome.provenance.attempts),
        )

    def assert_request_author_content_safe(self, request: GenerationRequest) -> None:
        """Check live-route credentials without creating a provider or ledger entry."""

        self._runtime.assert_request_author_content_safe(
            self._invocation(request),
            session_override=self._session_override,
        )

    @staticmethod
    def _with_runtime_retry_accounting(
        result: GenerationResult,
        *,
        provenance_attempts: int,
    ) -> GenerationResult:
        """Carry bounded runtime route attempts across the legacy result seam."""

        # The mature Story/Screenplay boundary has no separate provenance
        # parameter. Carry the shared runtime's bounded route attempts on the
        # terminal result so downstream length/cost evidence cannot silently
        # count a retried logical call as one provider attempt.
        runtime_retries = max(0, provenance_attempts - 1)
        return result.model_copy(
            update={
                "retry_attempts": max(
                    result.retry_attempts,
                    runtime_retries,
                )
            },
            deep=True,
        )

    def stream_text(
        self,
        request: GenerationRequest,
        *,
        cancel: Event | None = None,
    ) -> Iterator[str]:
        """Yield one terminal safe chunk while routing through the shared runtime.

        The legacy orchestration boundary still receives the exact prose and
        retains its RUNNING/partial/finalization behavior. Actual provider
        streaming remains owned by the shared runtime rather than exposing a
        second live provider client to the page.
        """

        for event in self.stream_text_events(request, cancel=cancel):
            if isinstance(event, GenerationStreamDelta):
                yield event.text

    def stream_text_events(
        self,
        request: GenerationRequest,
        *,
        cancel: Event | None = None,
    ) -> Iterator[GenerationStreamEvent]:
        """Expose safe terminal metadata without changing legacy chunk callers."""

        result = self.generate_text(request, cancel=cancel)
        if cancel is not None and cancel.is_set():
            raise RequestCancelledError("生成已取消")
        if result.text:
            yield GenerationStreamDelta(text=result.text)
        yield GenerationStreamResult.from_generation_result(result)

    def generate_structured_once(
        self,
        request: GenerationRequest,
        schema: type[BaseModel],
        *,
        cancel: Event | None = None,
    ) -> StructuredResult:
        invocation = self._invocation(request)
        outcome = self._runtime.execute_structured(
            invocation,
            schema,
            session_override=self._session_override,
            cancel=cancel,
        )
        if outcome.result is not None:
            return outcome.result
        assert outcome.failure is not None
        raise _provider_error(outcome.failure)

    def list_models(self) -> list[ModelInfo]:
        raise ProviderConfigurationError("模型清單必須透過共用 AI 連線服務查詢。")

    def health_check(self) -> ProviderHealth:
        return ProviderHealth(
            ok=False,
            error="連線檢查必須透過共用 AI 連線服務執行。",
        )

    def close(self) -> None:
        """Drop an action-scoped session secret; runtime clients are already closed."""

        self._session_override = None

    def resolve_route(self) -> GenerationRoute:
        """Return the same sanitized live route that the next call will use."""

        return self._route(self._preferences.get_preferences(), request_model="")

    @property
    def offline_fallback_allowed(self) -> bool:
        """Expose the live, explicit legacy-service fallback permission.

        Some mature candidate services predate the shared runtime and can build
        a deterministic local candidate after a provider error. They may do so
        only when the global (or task-specific) policy explicitly requests
        ``offline_then_fail``; every other policy remains fail-closed here.
        """

        preferences = self._preferences.get_preferences()
        if self._task is GenerationTask.FINAL_WRITING:
            return False
        task_override = task_route_preference(
            preferences,
            self._task,
            action_id=self._action_id,
        )
        fallback_policy = (
            task_override.fallback_policy
            if task_override is not None and task_override.fallback_policy is not None
            else preferences.fallback_policy
        )
        return fallback_policy is AIFallbackPolicy.OFFLINE_THEN_FAIL

    def _invocation(self, request: GenerationRequest) -> GenerationInvocation:
        preferences = self._preferences.get_preferences()
        route = self._route(preferences, request_model=request.model)
        suffix = self._idempotency_key_factory().strip()
        if not suffix:
            raise ProviderConfigurationError("無法建立安全的 action idempotency key。")
        return GenerationInvocation(
            task=self._task,
            action_id=self._action_id,
            request=request.model_copy(update={"model": route.primary.model}, deep=True),
            idempotency_key=(f"legacy:{self._task.value}:{self._action_id}:{suffix}"),
            route_override=route,
            # One Story logical call must have a hard runtime ceiling that is
            # compatible with its persisted worst-case cost reservation.
            provider_attempt_ceiling=(
                FINAL_WRITING_PROVIDER_ATTEMPT_CEILING
                if self._task is GenerationTask.FINAL_WRITING
                else None
            ),
        )

    def _route(
        self,
        preferences: AIRuntimePreferences,
        *,
        request_model: str,
    ) -> GenerationRoute:
        task_override = task_route_preference(
            preferences,
            self._task,
            action_id=self._action_id,
        )
        provider = self._provider_override or (
            task_override.provider
            if task_override is not None and task_override.provider is not None
            else preferences.default_provider
        )
        fallback_policy = (
            task_override.fallback_policy
            if task_override is not None and task_override.fallback_policy is not None
            else preferences.fallback_policy
        )
        quality = (
            task_override.quality_profile
            if task_override is not None and task_override.quality_profile is not None
            else preferences.default_quality_profile
        )

        if provider is AIProviderPreference.OFFLINE:
            raise ProviderConfigurationError(
                "全域 AI provider 目前設為離線；這個動作不會建立遠端 provider。"
            )
        if provider is AIProviderPreference.OPENAI:
            if not preferences.remote_provider_enabled:
                raise ProviderConfigurationError(
                    "全域 AI 設定尚未啟用遠端 provider；沒有傳送任何內容。"
                )
            default_model = preferences.openai_model
        else:
            default_model = preferences.ollama_model

        model = self._model_override
        if model is None and self._provider_override is not None:
            model = request_model.strip() or None
        if model is None and task_override is not None:
            model = task_override.model
        model = model or default_model
        if not model:
            raise ProviderConfigurationError("全域 AI 設定尚未選擇可用模型。")

        primary = ProviderRoute(
            provider_id=provider.value,
            model=model,
            profile_id=quality.value,
        )
        # Keep the mature Story/Screenplay boundary aligned with the facade:
        # final prose never silently changes provider or substitutes an offline
        # draft after the author explicitly starts a writing action.
        if self._task is GenerationTask.FINAL_WRITING:
            return GenerationRoute(primary=primary)
        if (
            provider is AIProviderPreference.OPENAI
            and fallback_policy is AIFallbackPolicy.OLLAMA_THEN_FAIL
        ):
            if not preferences.ollama_model:
                raise ProviderConfigurationError(
                    "已設定 Ollama fallback，但全域 AI 設定尚未選擇 Ollama 模型。"
                )
            return GenerationRoute(
                primary=primary,
                fallback_mode=FallbackMode.PROVIDER,
                fallback=ProviderRoute(
                    provider_id=AIProviderPreference.OLLAMA.value,
                    model=preferences.ollama_model,
                    profile_id=quality.value,
                ),
            )
        if fallback_policy is AIFallbackPolicy.OFFLINE_THEN_FAIL:
            return GenerationRoute(
                primary=primary,
                fallback_mode=FallbackMode.OFFLINE,
            )
        return GenerationRoute(primary=primary)


class CreativeRuntimeProviderFactory:
    """Create action-scoped adapters over one shared runtime/preferences pair."""

    def __init__(
        self,
        runtime: CreativeGenerationRuntime,
        preferences: RuntimePreferenceReader,
    ) -> None:
        self._runtime = runtime
        self._preferences = preferences

    def create(
        self,
        *,
        task: GenerationTask,
        action_id: str,
        provider_override: AIProviderPreference | None = None,
        model_override: str | None = None,
        session_override: SecretStr | str | None = None,
    ) -> CreativeRuntimeProviderAdapter:
        return CreativeRuntimeProviderAdapter(
            self._runtime,
            self._preferences,
            task=task,
            action_id=action_id,
            provider_override=provider_override,
            model_override=model_override,
            session_override=session_override,
        )

    def resolve_route(
        self,
        *,
        task: GenerationTask,
        action_id: str,
        provider_override: AIProviderPreference | None = None,
        model_override: str | None = None,
    ) -> GenerationRoute:
        """Preview a non-secret live route without capturing a credential."""

        return self.create(
            task=task,
            action_id=action_id,
            provider_override=provider_override,
            model_override=model_override,
        ).resolve_route()


__all__ = [
    "FINAL_WRITING_PROVIDER_ATTEMPT_CEILING",
    "CreativeRuntimeProviderAdapter",
    "CreativeRuntimeProviderFactory",
    "RuntimePreferenceReader",
]
