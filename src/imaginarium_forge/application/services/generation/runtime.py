"""Unified short-lived creative generation runtime.

The runtime owns routing, execution-time credential resolution, bounded retry,
explicit fallback, provider lifecycle, provenance and process-local
idempotency. It performs no database writes; callers keep candidate apply and
durable domain mutations outside provider I/O.
"""

from __future__ import annotations

import hashlib
import random
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from threading import Event, RLock
from typing import Protocol, TypeVar, cast

from pydantic import BaseModel, SecretStr

from imaginarium_forge.application.ports.credentials import (
    CredentialResolver,
    CredentialSource,
    ResolvedCredential,
)
from imaginarium_forge.application.services.generation.contracts import (
    AttemptStatus,
    FallbackMode,
    GenerationAttemptProvenance,
    GenerationErrorCategory,
    GenerationExecutionStatus,
    GenerationFailure,
    GenerationInvocation,
    GenerationOperation,
    GenerationProvenance,
    ProviderCapability,
    ProviderLifecycleStatus,
    ProviderRoute,
    RetryPolicy,
    StructuredGenerationOutcome,
    TextGenerationOutcome,
    TextStreamOutcome,
    TextStreamStatus,
)
from imaginarium_forge.application.services.generation.provider_registry import (
    ProviderRegistry,
)
from imaginarium_forge.application.services.generation.provider_router import (
    ProviderRouter,
    RouteResolution,
    RouteResolutionError,
)
from imaginarium_forge.providers.base import LLMProvider, TextStreamEventProvider
from imaginarium_forge.providers.contracts import (
    GenerationRequest,
    GenerationResult,
    GenerationStreamDelta,
    GenerationStreamResult,
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

_ResultT = TypeVar("_ResultT", GenerationResult, StructuredResult)
_ProviderCall = Callable[[LLMProvider, GenerationRequest, ResolvedCredential | None], _ResultT]
TextOfflineFallback = Callable[[GenerationInvocation], GenerationResult]
StructuredOfflineFallback = Callable[[GenerationInvocation], StructuredResult]
TextDeltaCallback = Callable[[str], None]


@dataclass(slots=True)
class _StreamCredentialGuard:
    """Hold an ambiguous active-key suffix before any live callback can see it.

    This state is action-local and never serialized. Ordinary safe deltas remain
    live; only a suffix equal to an active credential prefix is buffered.
    """

    secret: str = field(repr=False)
    on_delta: TextDeltaCallback | None = field(repr=False)
    pending: str = field(default="", init=False, repr=False)

    def feed(self, delta: str, *, publish: bool = True) -> None:
        combined = self.pending + delta
        if self.secret and self.secret in combined:
            self.pending = ""
            raise ProviderRefusalError("generated output failed credential safety")
        hold = 0
        if self.secret:
            for size in range(min(len(combined), len(self.secret) - 1), 0, -1):
                if combined.endswith(self.secret[:size]):
                    hold = size
                    break
        safe = combined[:-hold] if hold else combined
        self.pending = combined[-hold:] if hold else ""
        if safe and publish and self.on_delta is not None:
            self.on_delta(safe)

    def finish(self, *, interrupted: bool) -> None:
        if interrupted and self.pending:
            # Do not save a partially received credential when a stream ends
            # abnormally. An ordinary matching suffix is conservatively refused.
            self.pending = ""
            raise ProviderRefusalError("interrupted output failed credential safety")
        pending = self.pending
        self.pending = ""
        if pending and self.on_delta is not None:
            self.on_delta(pending)


def _resolved_secret(resolved: ResolvedCredential | None) -> str:
    if resolved is None or resolved.secret is None:
        return ""
    return resolved.secret.get_secret_value()


def _assert_result_credential_safe(
    result: GenerationResult | StructuredResult, resolved: ResolvedCredential | None
) -> None:
    """Guard all returned prose and metadata before caching or domain persistence."""
    secret = _resolved_secret(resolved)
    if not secret:
        return
    values: list[object] = [result.model_dump(mode="python")]
    while values:
        value = values.pop()
        if isinstance(value, str) and secret in value:
            raise ProviderRefusalError("generated output failed credential safety")
        if isinstance(value, dict):
            values.extend(value.keys())
            values.extend(value.values())
        elif isinstance(value, (list, tuple)):
            values.extend(value)


class IdempotencyDecisionKind(StrEnum):
    STARTED = "started"
    COMPLETED = "completed"
    IN_PROGRESS = "in_progress"
    CONFLICT = "conflict"


@dataclass(frozen=True, slots=True)
class IdempotencyDecision:
    """Reservation result; cached values are intentionally hidden from repr."""

    kind: IdempotencyDecisionKind
    cached: object | None = field(default=None, repr=False)


class IdempotencyLedger(Protocol):
    """Replaceable idempotency boundary for future durable implementations."""

    def begin(self, key: str, fingerprint: str) -> IdempotencyDecision: ...

    def complete(self, key: str, fingerprint: str, outcome: object) -> None: ...

    def abandon(self, key: str, fingerprint: str) -> None: ...


@dataclass(frozen=True, slots=True)
class _CompletedIdempotencyEntry:
    fingerprint: str
    outcome: object = field(repr=False)


class InMemoryIdempotencyLedger:
    """Bounded, thread-safe process-local idempotency implementation."""

    def __init__(self, *, max_entries: int = 1024) -> None:
        if max_entries < 1:
            raise ValueError("max_entries must be positive")
        self._max_entries = max_entries
        self._inflight: dict[str, str] = {}
        self._completed: OrderedDict[str, _CompletedIdempotencyEntry] = OrderedDict()
        self._lock = RLock()

    def begin(self, key: str, fingerprint: str) -> IdempotencyDecision:
        with self._lock:
            completed = self._completed.get(key)
            if completed is not None:
                if completed.fingerprint != fingerprint:
                    return IdempotencyDecision(IdempotencyDecisionKind.CONFLICT)
                self._completed.move_to_end(key)
                return IdempotencyDecision(
                    IdempotencyDecisionKind.COMPLETED,
                    cached=completed.outcome,
                )
            inflight_fingerprint = self._inflight.get(key)
            if inflight_fingerprint is not None:
                kind = (
                    IdempotencyDecisionKind.IN_PROGRESS
                    if inflight_fingerprint == fingerprint
                    else IdempotencyDecisionKind.CONFLICT
                )
                return IdempotencyDecision(kind)
            self._inflight[key] = fingerprint
            return IdempotencyDecision(IdempotencyDecisionKind.STARTED)

    def complete(self, key: str, fingerprint: str, outcome: object) -> None:
        with self._lock:
            if self._inflight.get(key) != fingerprint:
                return
            del self._inflight[key]
            self._completed[key] = _CompletedIdempotencyEntry(fingerprint, outcome)
            self._completed.move_to_end(key)
            while len(self._completed) > self._max_entries:
                self._completed.popitem(last=False)

    def abandon(self, key: str, fingerprint: str) -> None:
        with self._lock:
            if self._inflight.get(key) == fingerprint:
                del self._inflight[key]


@dataclass(frozen=True, slots=True)
class _ClassifiedError:
    category: GenerationErrorCategory
    safe_message: str
    retriable: bool


@dataclass(frozen=True, slots=True)
class _Execution[ResultT]:
    result: ResultT | None
    failure: GenerationFailure | None
    provenance: GenerationProvenance


@dataclass(frozen=True, slots=True)
class _RouteExecution[ResultT]:
    result: ResultT | None
    failure: GenerationFailure | None
    attempts: tuple[GenerationAttemptProvenance, ...]


class CreativeGenerationRuntime:
    """Execute provider-neutral text and structured generation commands."""

    def __init__(
        self,
        registry: ProviderRegistry,
        router: ProviderRouter,
        credential_resolver: CredentialResolver,
        *,
        retry_policy: RetryPolicy | None = None,
        idempotency_ledger: IdempotencyLedger | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        jitter_source: Callable[[], float] = random.random,
    ) -> None:
        self._registry = registry
        self._router = router
        self._credential_resolver = credential_resolver
        self._retry_policy = RetryPolicy() if retry_policy is None else retry_policy
        self._idempotency = (
            InMemoryIdempotencyLedger() if idempotency_ledger is None else idempotency_ledger
        )
        self._sleeper = sleeper
        self._jitter_source = jitter_source

    def execute_text(
        self,
        invocation: GenerationInvocation,
        *,
        session_override: SecretStr | str | None = None,
        cancel: Event | None = None,
        offline_fallback: TextOfflineFallback | None = None,
    ) -> TextGenerationOutcome:
        """Execute a text operation and return a typed terminal outcome."""

        invocation = _snapshot_invocation(invocation)

        def provider_call(
            provider: LLMProvider,
            request: GenerationRequest,
            _resolved: ResolvedCredential | None,
        ) -> GenerationResult:
            return provider.generate_text(request, cancel=cancel)

        execution = self._execute(
            invocation,
            operation=GenerationOperation.TEXT,
            additional_capabilities=frozenset(),
            provider_call=provider_call,
            session_override=session_override,
            cancel=cancel,
            offline_fallback=offline_fallback,
        )
        if execution.result is not None and not execution.result.partial:
            return TextGenerationOutcome(
                status=GenerationExecutionStatus.SUCCEEDED,
                result=execution.result.model_copy(deep=True),
                provenance=execution.provenance,
            )
        failure = execution.failure
        if execution.result is not None:
            failure = _failure_from_partial(execution.result)
        return TextGenerationOutcome(
            status=GenerationExecutionStatus.FAILED,
            failure=failure,
            provenance=execution.provenance,
        )

    def assert_request_author_content_safe(
        self,
        invocation: GenerationInvocation,
        *,
        session_override: SecretStr | str | None = None,
    ) -> None:
        """Reject author content containing any credential on the selected route.

        Mature Story and Screenplay services persist a RUNNING audit record before
        provider I/O. They call this side-effect-free pre-dispatch boundary first
        so an accidentally pasted active credential can never reach either the
        provider or their SQLite input snapshots. The normal execution path keeps
        its own identical check as defence in depth.
        """

        invocation = _snapshot_invocation(invocation)
        try:
            resolution = self._router.resolve(invocation)
        except RouteResolutionError as exc:
            raise ProviderConfigurationError("目前沒有符合這項創作動作需求的提供者路由。") from exc

        routes = [resolution.primary]
        if resolution.fallback is not None:
            routes.append(resolution.fallback)
        for route in routes:
            if route.provider_id == "offline":
                continue
            try:
                descriptor = self._registry.descriptor(route.provider_id)
            except Exception as exc:
                raise ProviderConfigurationError(
                    "目前沒有符合這項創作動作需求的提供者路由。"
                ) from exc
            resolved, failure = self._resolve_credential(
                descriptor.credential_id,
                session_override=session_override,
            )
            if failure is not None:
                raise ProviderConfigurationError(failure.safe_message)
            if resolved is not None and _request_contains_resolved_secret(
                invocation.request,
                resolved,
            ):
                raise ProviderConfigurationError(
                    "偵測到目前 API Key 出現在即將送出的內容；已阻止傳送，請先移除憑證文字。"
                )

    def execute_text_stream(
        self,
        invocation: GenerationInvocation,
        *,
        on_delta: TextDeltaCallback | None = None,
        session_override: SecretStr | str | None = None,
        cancel: Event | None = None,
        offline_fallback: TextOfflineFallback | None = None,
    ) -> TextStreamOutcome:
        """Stream prose while preserving an interrupted prefix as typed partial.

        Once a provider has emitted any text, a transport interruption is not
        retried or routed to a fallback provider. Replaying from the beginning
        could duplicate prose or incur a second charge. Safety refusals and
        content filters fail closed without a returned candidate. Callers must
        discard any provisional safe preview when the terminal outcome fails.
        """

        invocation = _snapshot_invocation(invocation)

        def provider_call(
            provider: LLMProvider,
            request: GenerationRequest,
            resolved: ResolvedCredential | None,
        ) -> GenerationResult:
            chunks: list[str] = []
            terminal: GenerationStreamResult | None = None
            output_guard = _StreamCredentialGuard(_resolved_secret(resolved), on_delta)
            try:
                if isinstance(provider, TextStreamEventProvider):
                    for event in provider.stream_text_events(request, cancel=cancel):
                        if isinstance(event, GenerationStreamResult):
                            # Refusal dominates a simultaneous user cancellation:
                            # a filtered prefix must not become a cancellable
                            # transport partial just because the event is set.
                            if (
                                event.incomplete_reason == "content_filter"
                                or event.finish_reason == "content_filter"
                            ):
                                raise ProviderRefusalError("content filter")
                            if terminal is not None:
                                raise InvalidProviderResponseError(
                                    "provider emitted multiple stream results"
                                )
                            terminal = event
                            continue
                        if not isinstance(event, GenerationStreamDelta):
                            raise InvalidProviderResponseError(
                                "provider emitted an invalid stream event"
                            )
                        if terminal is not None:
                            raise InvalidProviderResponseError(
                                "provider emitted text after its stream result"
                            )
                        chunks.append(event.text)
                        output_guard.feed(
                            event.text, publish=cancel is None or not cancel.is_set()
                        )
                        if cancel is not None and cancel.is_set():
                            raise RequestCancelledError("generation cancelled")
                    if terminal is None:
                        raise ProviderIncompleteError("transport_interruption")
                else:
                    for delta in provider.stream_text(request, cancel=cancel):
                        if not delta:
                            continue
                        chunks.append(delta)
                        output_guard.feed(delta, publish=cancel is None or not cancel.is_set())
                        if cancel is not None and cancel.is_set():
                            raise RequestCancelledError("generation cancelled")
                if cancel is not None and cancel.is_set():
                    raise RequestCancelledError("generation cancelled")
            except ProviderIncompleteError as exc:
                if exc.reason == "content_filter":
                    raise ProviderRefusalError("content filter") from exc
                if chunks:
                    output_guard.finish(interrupted=True)
                    return _partial_stream_result(
                        request,
                        chunks,
                        reason=exc.reason,
                    )
                raise
            except (ProviderTimeoutError, ProviderUnavailableError) as exc:
                if chunks:
                    output_guard.finish(interrupted=True)
                    classified = _classify_error(exc)
                    return _partial_stream_result(
                        request,
                        chunks,
                        reason=classified.category.value,
                    )
                raise
            except RequestCancelledError:
                if chunks:
                    output_guard.finish(interrupted=True)
                    return _partial_stream_result(
                        request,
                        chunks,
                        reason=GenerationErrorCategory.CANCELLED.value,
                    )
                raise
            if terminal is not None:
                if (
                    terminal.incomplete_reason == "content_filter"
                    or terminal.finish_reason == "content_filter"
                ):
                    raise ProviderRefusalError("content filter")
                try:
                    output_guard.finish(interrupted=terminal.partial)
                    if cancel is not None and cancel.is_set():
                        raise RequestCancelledError("generation cancelled")
                except RequestCancelledError:
                    return _partial_stream_result(request, chunks, reason="cancelled")
                return terminal.to_generation_result("".join(chunks))
            try:
                output_guard.finish(interrupted=False)
                if cancel is not None and cancel.is_set():
                    raise RequestCancelledError("generation cancelled")
            except RequestCancelledError:
                return _partial_stream_result(request, chunks, reason="cancelled")
            return GenerationResult(
                text="".join(chunks),
                model=request.model,
                latency_ms=0,
            )

        stream_capability = (
            frozenset()
            if (
                invocation.route_override is not None
                and invocation.route_override.primary.provider_id == "offline"
            )
            else frozenset({ProviderCapability.STREAMING})
        )
        execution = self._execute(
            invocation,
            operation=GenerationOperation.STREAM,
            additional_capabilities=stream_capability,
            provider_call=provider_call,
            session_override=session_override,
            cancel=cancel,
            offline_fallback=offline_fallback,
        )
        if execution.result is not None:
            return TextStreamOutcome(
                status=(
                    TextStreamStatus.PARTIAL
                    if execution.result.partial
                    else TextStreamStatus.COMPLETED
                ),
                result=execution.result.model_copy(deep=True),
                provenance=execution.provenance,
            )
        return TextStreamOutcome(
            status=TextStreamStatus.FAILED,
            failure=execution.failure,
            provenance=execution.provenance,
        )

    def execute_structured(
        self,
        invocation: GenerationInvocation,
        schema: type[BaseModel],
        *,
        session_override: SecretStr | str | None = None,
        cancel: Event | None = None,
        offline_fallback: StructuredOfflineFallback | None = None,
    ) -> StructuredGenerationOutcome:
        """Execute a schema-constrained operation with the same runtime policy."""

        invocation = _snapshot_invocation(invocation)

        def provider_call(
            provider: LLMProvider,
            request: GenerationRequest,
            _resolved: ResolvedCredential | None,
        ) -> StructuredResult:
            return provider.generate_structured_once(request, schema, cancel=cancel)

        schema_tag = f"{schema.__module__}.{schema.__qualname__}"
        execution = self._execute(
            invocation,
            operation=GenerationOperation.STRUCTURED,
            fingerprint_suffix=schema_tag,
            additional_capabilities=frozenset({ProviderCapability.STRUCTURED_OUTPUT}),
            provider_call=provider_call,
            session_override=session_override,
            cancel=cancel,
            offline_fallback=offline_fallback,
        )
        if execution.result is not None:
            return StructuredGenerationOutcome(
                status=GenerationExecutionStatus.SUCCEEDED,
                result=execution.result.model_copy(deep=True),
                provenance=execution.provenance,
            )
        return StructuredGenerationOutcome(
            status=GenerationExecutionStatus.FAILED,
            failure=execution.failure,
            provenance=execution.provenance,
        )

    def _execute(
        self,
        invocation: GenerationInvocation,
        *,
        operation: GenerationOperation,
        additional_capabilities: frozenset[ProviderCapability],
        provider_call: _ProviderCall[_ResultT],
        session_override: SecretStr | str | None,
        cancel: Event | None,
        offline_fallback: Callable[[GenerationInvocation], _ResultT] | None,
        fingerprint_suffix: str = "",
    ) -> _Execution[_ResultT]:
        try:
            resolution = self._router.resolve(
                invocation,
                additional_capabilities=additional_capabilities,
            )
        except RouteResolutionError:
            return _Execution(
                result=None,
                failure=GenerationFailure(
                    category=GenerationErrorCategory.ROUTE_UNAVAILABLE,
                    safe_message="目前沒有符合這項創作動作需求的提供者路由。",
                ),
                provenance=self._unresolved_provenance(invocation),
            )

        fingerprint = invocation.fingerprint(operation)
        if fingerprint_suffix:
            fingerprint = hashlib.sha256(f"{fingerprint}:{fingerprint_suffix}".encode()).hexdigest()
        decision = self._idempotency.begin(invocation.idempotency_key, fingerprint)
        if decision.kind is IdempotencyDecisionKind.COMPLETED:
            return cast(_Execution[_ResultT], decision.cached)
        if decision.kind is IdempotencyDecisionKind.IN_PROGRESS:
            return self._idempotency_failure(
                invocation,
                resolution,
                GenerationErrorCategory.DUPLICATE_IN_PROGRESS,
                "相同請求仍在執行，已阻止重複送出。",
            )
        if decision.kind is IdempotencyDecisionKind.CONFLICT:
            return self._idempotency_failure(
                invocation,
                resolution,
                GenerationErrorCategory.IDEMPOTENCY_CONFLICT,
                "相同冪等鍵已用於不同請求，請建立新的請求識別碼。",
            )

        try:
            execution = self._execute_resolved(
                invocation,
                resolution,
                provider_call=provider_call,
                session_override=session_override,
                cancel=cancel,
                offline_fallback=offline_fallback,
            )
        except BaseException:
            self._idempotency.abandon(invocation.idempotency_key, fingerprint)
            raise
        self._idempotency.complete(
            invocation.idempotency_key,
            fingerprint,
            execution,
        )
        return execution

    def _execute_resolved(
        self,
        invocation: GenerationInvocation,
        resolution: RouteResolution,
        *,
        provider_call: _ProviderCall[_ResultT],
        session_override: SecretStr | str | None,
        cancel: Event | None,
        offline_fallback: Callable[[GenerationInvocation], _ResultT] | None,
    ) -> _Execution[_ResultT]:
        if resolution.primary.provider_id == "offline":
            if offline_fallback is None:
                return _Execution(
                    result=None,
                    failure=GenerationFailure(
                        category=(GenerationErrorCategory.OFFLINE_FALLBACK_UNAVAILABLE),
                        safe_message="目前頁面沒有提供可用的離線候選產生器。",
                    ),
                    provenance=self._provenance(invocation, resolution, []),
                )
            offline = self._execute_offline(
                invocation,
                offline_fallback,
                attempt_number=1,
            )
            return _Execution(
                result=offline.result,
                failure=offline.failure,
                provenance=self._provenance(
                    invocation,
                    resolution,
                    list(offline.attempts),
                    offline_fallback_used=True,
                ),
            )

        primary = self._execute_route(
            resolution.primary,
            invocation,
            provider_call=provider_call,
            session_override=session_override,
            cancel=cancel,
            attempt_offset=0,
            attempts_remaining=invocation.provider_attempt_ceiling,
        )
        attempts = list(primary.attempts)
        if primary.result is not None:
            return _Execution(
                result=primary.result,
                failure=None,
                provenance=self._provenance(invocation, resolution, attempts),
            )

        primary_failure = primary.failure
        if primary_failure is None:
            primary_failure = GenerationFailure(
                category=GenerationErrorCategory.PROVIDER_ERROR,
                safe_message="提供者未回傳可用結果。",
            )
        if primary_failure.category not in resolution.fallback_on:
            return _Execution(
                result=None,
                failure=primary_failure,
                provenance=self._provenance(invocation, resolution, attempts),
            )

        if resolution.fallback_mode is FallbackMode.PROVIDER:
            fallback_route = resolution.fallback
            if fallback_route is None:
                return _Execution(
                    result=None,
                    failure=primary_failure,
                    provenance=self._provenance(invocation, resolution, attempts),
                )
            attempts_remaining = (
                None
                if invocation.provider_attempt_ceiling is None
                else invocation.provider_attempt_ceiling - len(attempts)
            )
            if attempts_remaining is not None and attempts_remaining <= 0:
                return _Execution(
                    result=None,
                    failure=primary_failure,
                    provenance=self._provenance(invocation, resolution, attempts),
                )
            fallback = self._execute_route(
                fallback_route,
                invocation,
                provider_call=provider_call,
                session_override=session_override,
                cancel=cancel,
                attempt_offset=len(attempts),
                attempts_remaining=attempts_remaining,
            )
            attempts.extend(fallback.attempts)
            return _Execution(
                result=fallback.result,
                failure=fallback.failure,
                provenance=self._provenance(
                    invocation,
                    resolution,
                    attempts,
                    fallback_used=True,
                ),
            )

        if resolution.fallback_mode is FallbackMode.OFFLINE:
            if offline_fallback is None:
                return _Execution(
                    result=None,
                    failure=GenerationFailure(
                        category=(GenerationErrorCategory.OFFLINE_FALLBACK_UNAVAILABLE),
                        safe_message="已設定離線替代流程，但目前沒有可用的離線處理器。",
                    ),
                    provenance=self._provenance(
                        invocation,
                        resolution,
                        attempts,
                        fallback_used=True,
                        offline_fallback_used=True,
                    ),
                )
            offline = self._execute_offline(
                invocation,
                offline_fallback,
                attempt_number=len(attempts) + 1,
            )
            attempts.extend(offline.attempts)
            return _Execution(
                result=offline.result,
                failure=offline.failure,
                provenance=self._provenance(
                    invocation,
                    resolution,
                    attempts,
                    fallback_used=True,
                    offline_fallback_used=True,
                ),
            )

        return _Execution(
            result=None,
            failure=primary_failure,
            provenance=self._provenance(invocation, resolution, attempts),
        )

    def _execute_route(
        self,
        route: ProviderRoute,
        invocation: GenerationInvocation,
        *,
        provider_call: _ProviderCall[_ResultT],
        session_override: SecretStr | str | None,
        cancel: Event | None,
        attempt_offset: int,
        attempts_remaining: int | None,
    ) -> _RouteExecution[_ResultT]:
        attempts: list[GenerationAttemptProvenance] = []
        request = GenerationRequest.model_validate(
            {**invocation.request.model_dump(mode="python"), "model": route.model}
        )
        descriptor = self._registry.descriptor(route.provider_id)

        route_attempt_ceiling = self._retry_policy.max_attempts
        if attempts_remaining is not None:
            route_attempt_ceiling = min(route_attempt_ceiling, attempts_remaining)

        for route_attempt in range(1, route_attempt_ceiling + 1):
            attempt_number = attempt_offset + route_attempt
            if cancel is not None and cancel.is_set():
                cancellation = _classify_error(RequestCancelledError("cancelled"))
                attempts.append(
                    self._failed_attempt(
                        route,
                        attempt_number=attempt_number,
                        credential_source=None,
                        classified=cancellation,
                        latency_ms=0,
                        lifecycle_status=(ProviderLifecycleStatus.CLOSE_UNSUPPORTED),
                    )
                )
                return _RouteExecution(
                    result=None,
                    failure=_failure_from_classified(cancellation),
                    attempts=tuple(attempts),
                )

            resolved, credential_failure = self._resolve_credential(
                descriptor.credential_id,
                session_override=session_override,
            )
            if credential_failure is not None:
                attempts.append(
                    self._failed_attempt(
                        route,
                        attempt_number=attempt_number,
                        credential_source=(None if resolved is None else resolved.source),
                        classified=credential_failure,
                        latency_ms=0,
                        lifecycle_status=(ProviderLifecycleStatus.CLOSE_UNSUPPORTED),
                    )
                )
                return _RouteExecution(
                    result=None,
                    failure=_failure_from_classified(credential_failure),
                    attempts=tuple(attempts),
                )
            if resolved is not None and _request_contains_resolved_secret(
                request,
                resolved,
            ):
                credential_content_failure = _classify_error(
                    ProviderConfigurationError("author content contains the active credential")
                )
                attempts.append(
                    self._failed_attempt(
                        route,
                        attempt_number=attempt_number,
                        credential_source=resolved.source,
                        classified=credential_content_failure,
                        latency_ms=0,
                        lifecycle_status=(ProviderLifecycleStatus.CLOSE_UNSUPPORTED),
                    )
                )
                return _RouteExecution(
                    result=None,
                    failure=GenerationFailure(
                        category=GenerationErrorCategory.CONFIGURATION,
                        safe_message=(
                            "偵測到目前 API Key 出現在即將送出的內容；已阻止傳送，"
                            "請先移除憑證文字。"
                        ),
                        retriable=False,
                    ),
                    attempts=tuple(attempts),
                )

            provider: LLMProvider | None = None
            result: _ResultT | None = None
            classified: _ClassifiedError | None = None
            started = time.monotonic()
            fatal: BaseException | None = None
            try:
                provider = self._registry.create(route, resolved)
                result = provider_call(provider, request, resolved)
                _assert_result_credential_safe(result, resolved)
            except Exception as exc:
                result = None
                classified = _classify_error(exc)
            except BaseException as exc:
                fatal = exc
            lifecycle_status = _close_provider(provider)
            latency_ms = max(0, int((time.monotonic() - started) * 1000))
            if fatal is not None:
                raise fatal

            if result is not None:
                partial = isinstance(result, GenerationResult) and result.partial
                attempts.append(
                    GenerationAttemptProvenance(
                        provider_id=route.provider_id,
                        requested_model=route.model,
                        actual_model=result.model,
                        attempt_number=attempt_number,
                        credential_source=(None if resolved is None else resolved.source),
                        status=(AttemptStatus.INTERRUPTED if partial else AttemptStatus.SUCCEEDED),
                        latency_ms=latency_ms,
                        error_category=(
                            GenerationErrorCategory.INCOMPLETE_OUTPUT if partial else None
                        ),
                        lifecycle_status=lifecycle_status,
                    )
                )
                return _RouteExecution(
                    result=result,
                    failure=None,
                    attempts=tuple(attempts),
                )

            if classified is None:
                classified = _classify_error(RuntimeError("provider returned no terminal value"))
            attempts.append(
                self._failed_attempt(
                    route,
                    attempt_number=attempt_number,
                    credential_source=None if resolved is None else resolved.source,
                    classified=classified,
                    latency_ms=latency_ms,
                    lifecycle_status=lifecycle_status,
                )
            )
            should_retry = (
                classified.retriable
                and classified.category in self._retry_policy.retry_on
                and route_attempt < route_attempt_ceiling
            )
            if not should_retry:
                return _RouteExecution(
                    result=None,
                    failure=_failure_from_classified(classified),
                    attempts=tuple(attempts),
                )
            self._sleeper(self._retry_delay(route_attempt))

        unreachable = _classify_error(RuntimeError("retry loop exhausted"))
        return _RouteExecution(
            result=None,
            failure=_failure_from_classified(unreachable),
            attempts=tuple(attempts),
        )

    def _execute_offline(
        self,
        invocation: GenerationInvocation,
        fallback: Callable[[GenerationInvocation], _ResultT],
        *,
        attempt_number: int,
    ) -> _RouteExecution[_ResultT]:
        started = time.monotonic()
        try:
            result = fallback(invocation)
        except Exception as exc:
            classified = _classify_error(exc)
            attempt = GenerationAttemptProvenance(
                provider_id="offline",
                requested_model="offline",
                attempt_number=attempt_number,
                status=AttemptStatus.FAILED,
                latency_ms=max(0, int((time.monotonic() - started) * 1000)),
                error_category=classified.category,
                retriable=classified.retriable,
                lifecycle_status=ProviderLifecycleStatus.CLOSE_UNSUPPORTED,
            )
            return _RouteExecution(
                result=None,
                failure=_failure_from_classified(classified),
                attempts=(attempt,),
            )
        attempt = GenerationAttemptProvenance(
            provider_id="offline",
            requested_model="offline",
            actual_model=result.model,
            attempt_number=attempt_number,
            status=AttemptStatus.SUCCEEDED,
            latency_ms=max(0, int((time.monotonic() - started) * 1000)),
            lifecycle_status=ProviderLifecycleStatus.CLOSE_UNSUPPORTED,
        )
        return _RouteExecution(result=result, failure=None, attempts=(attempt,))

    def _resolve_credential(
        self,
        credential_id: str | None,
        *,
        session_override: SecretStr | str | None,
    ) -> tuple[ResolvedCredential | None, _ClassifiedError | None]:
        if credential_id is None:
            return None, None
        try:
            resolved = self._credential_resolver.resolve(
                credential_id,
                session_override=session_override,
            )
        except Exception:
            classified = _ClassifiedError(
                category=GenerationErrorCategory.CONFIGURATION,
                safe_message="無法從核准的憑證來源取得提供者憑證。",
                retriable=False,
            )
            return None, classified
        if not resolved.available:
            classified = _ClassifiedError(
                category=GenerationErrorCategory.CONFIGURATION,
                safe_message="尚未設定這個提供者所需的執行期憑證。",
                retriable=False,
            )
            return resolved, classified
        return resolved, None

    def _retry_delay(self, failed_attempt: int) -> float:
        max_delay = float(self._retry_policy.max_delay_s)
        base_delay = float(self._retry_policy.base_delay_s)
        jitter_ratio = float(self._retry_policy.jitter_ratio)
        base: float = float(min(max_delay, base_delay * (2 ** (failed_attempt - 1))))
        if base == 0 or jitter_ratio == 0:
            return float(base)
        centered = (max(0.0, min(1.0, float(self._jitter_source()))) * 2.0) - 1.0
        return float(max(0.0, base * (1.0 + centered * jitter_ratio)))

    @staticmethod
    def _failed_attempt(
        route: ProviderRoute,
        *,
        attempt_number: int,
        credential_source: CredentialSource | None,
        classified: _ClassifiedError,
        latency_ms: int,
        lifecycle_status: ProviderLifecycleStatus,
    ) -> GenerationAttemptProvenance:
        return GenerationAttemptProvenance(
            provider_id=route.provider_id,
            requested_model=route.model,
            attempt_number=attempt_number,
            credential_source=credential_source,
            status=AttemptStatus.FAILED,
            latency_ms=latency_ms,
            error_category=classified.category,
            retriable=classified.retriable,
            lifecycle_status=lifecycle_status,
        )

    @staticmethod
    def _provenance(
        invocation: GenerationInvocation,
        resolution: RouteResolution,
        attempts: list[GenerationAttemptProvenance],
        *,
        fallback_used: bool = False,
        offline_fallback_used: bool = False,
    ) -> GenerationProvenance:
        last = attempts[-1] if attempts else None
        return GenerationProvenance(
            idempotency_key=invocation.idempotency_key,
            route_source=resolution.source,
            requested_provider_id=resolution.primary.provider_id,
            requested_model=resolution.primary.model,
            actual_provider_id=None if last is None else last.provider_id,
            actual_model=(None if last is None else (last.actual_model or last.requested_model)),
            fallback_used=fallback_used,
            offline_fallback_used=offline_fallback_used,
            attempts=tuple(attempts),
        )

    @staticmethod
    def _unresolved_provenance(
        invocation: GenerationInvocation,
    ) -> GenerationProvenance:
        route = invocation.route_override
        return GenerationProvenance(
            idempotency_key=invocation.idempotency_key,
            requested_provider_id=(None if route is None else route.primary.provider_id),
            requested_model=None if route is None else route.primary.model,
        )

    @staticmethod
    def _idempotency_failure(
        invocation: GenerationInvocation,
        resolution: RouteResolution,
        category: GenerationErrorCategory,
        message: str,
    ) -> _Execution[_ResultT]:
        return _Execution(
            result=None,
            failure=GenerationFailure(category=category, safe_message=message),
            provenance=GenerationProvenance(
                idempotency_key=invocation.idempotency_key,
                route_source=resolution.source,
                requested_provider_id=resolution.primary.provider_id,
                requested_model=resolution.primary.model,
            ),
        )


def _close_provider(provider: LLMProvider | None) -> ProviderLifecycleStatus:
    if provider is None:
        return ProviderLifecycleStatus.CLOSE_UNSUPPORTED
    closer = getattr(provider, "close", None)
    if not callable(closer):
        return ProviderLifecycleStatus.CLOSE_UNSUPPORTED
    try:
        closer()
    except Exception:
        return ProviderLifecycleStatus.CLOSE_FAILED
    return ProviderLifecycleStatus.CLOSED


def _snapshot_invocation(invocation: GenerationInvocation) -> GenerationInvocation:
    """Revalidate and detach nested mutable provider request models."""

    return GenerationInvocation.model_validate(invocation.model_dump(mode="python"))


def _request_contains_resolved_secret(
    request: GenerationRequest,
    resolved: ResolvedCredential,
) -> bool:
    """Fail closed before dispatch when author content contains the active key."""

    if resolved.secret is None:
        return False
    secret = resolved.secret.get_secret_value()
    if not secret:
        return False
    text_fields = [request.prompt, request.system or ""]
    text_fields.extend(item.content for item in request.input_items)
    return any(secret in text for text in text_fields)


def _failure_from_classified(classified: _ClassifiedError) -> GenerationFailure:
    return GenerationFailure(
        category=classified.category,
        safe_message=classified.safe_message,
        retriable=classified.retriable,
    )


def _failure_from_partial(result: GenerationResult) -> GenerationFailure:
    if result.incomplete_reason == "content_filter":
        return GenerationFailure(
            category=GenerationErrorCategory.REFUSAL,
            safe_message="提供者因內容安全政策中止這次生成。",
        )
    return GenerationFailure(
        category=GenerationErrorCategory.INCOMPLETE_OUTPUT,
        safe_message="提供者回應尚未完成；已保留可供續寫的部分內容。",
        partial_text=result.text,
    )


def _partial_stream_result(
    request: GenerationRequest,
    chunks: list[str],
    *,
    reason: str,
) -> GenerationResult:
    return GenerationResult(
        text="".join(chunks),
        model=request.model,
        latency_ms=0,
        partial=True,
        incomplete_reason=reason,
        finish_reason=reason,
    )


def _classify_error(error: Exception) -> _ClassifiedError:
    category: GenerationErrorCategory
    message: str
    if isinstance(error, ProviderIncompleteError):
        if error.reason == "content_filter":
            category = GenerationErrorCategory.REFUSAL
            message = "提供者因內容安全政策中止這次生成。"
        elif error.reason == "max_output_tokens":
            category = GenerationErrorCategory.INCOMPLETE_OUTPUT
            message = "提供者在完成回應前達到輸出上限。"
        else:
            category = GenerationErrorCategory.UNAVAILABLE
            message = "提供者連線中斷，未能完成這次生成。"
    elif isinstance(error, ProviderAuthenticationError):
        category = GenerationErrorCategory.AUTHENTICATION
        message = "提供者拒絕執行期憑證，請重新確認憑證設定。"
    elif isinstance(error, ProviderConfigurationError):
        category = GenerationErrorCategory.CONFIGURATION
        message = "提供者設定不完整，尚未送出可用的生成請求。"
    elif isinstance(error, ProviderQuotaError):
        category = GenerationErrorCategory.QUOTA
        message = "提供者回報額度或計費限制。"
    elif isinstance(error, ProviderRateLimitError):
        category = GenerationErrorCategory.RATE_LIMIT
        message = "提供者暫時限制請求速率。"
    elif isinstance(error, ProviderTimeoutError):
        category = GenerationErrorCategory.TIMEOUT
        message = "提供者在期限內沒有完成回應。"
    elif isinstance(error, ProviderUnavailableError):
        category = GenerationErrorCategory.UNAVAILABLE
        message = "目前無法連線到選定的提供者。"
    elif isinstance(error, ModelNotFoundError):
        category = GenerationErrorCategory.MODEL_NOT_FOUND
        message = "選定的模型在這個提供者上不可用。"
    elif isinstance(error, RequestCancelledError):
        category = GenerationErrorCategory.CANCELLED
        message = "這次生成已取消。"
    elif isinstance(error, ProviderRefusalError):
        category = GenerationErrorCategory.REFUSAL
        message = "提供者拒絕了這次生成請求。"
    elif isinstance(error, InvalidStructuredOutputError):
        category = GenerationErrorCategory.INVALID_STRUCTURED_OUTPUT
        message = "提供者輸出未通過要求的結構驗證。"
    elif isinstance(error, InvalidProviderResponseError):
        category = GenerationErrorCategory.INVALID_RESPONSE
        message = "提供者回應格式無法安全解析。"
    elif isinstance(error, ProviderError):
        category = GenerationErrorCategory.PROVIDER_ERROR
        message = "提供者未能完成這次生成。"
    else:
        category = GenerationErrorCategory.PROVIDER_ERROR
        message = "生成執行期間發生未預期錯誤。"
    retriable = isinstance(error, ProviderError) and error.retriable
    return _ClassifiedError(category=category, safe_message=message, retriable=retriable)


__all__ = [
    "CreativeGenerationRuntime",
    "IdempotencyDecision",
    "IdempotencyDecisionKind",
    "IdempotencyLedger",
    "InMemoryIdempotencyLedger",
    "StructuredOfflineFallback",
    "TextDeltaCallback",
    "TextOfflineFallback",
]
