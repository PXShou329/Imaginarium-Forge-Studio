"""Optional OpenAI Responses API provider.

The adapter uses the existing provider-independent contract and ``httpx``
dependency. It deliberately has no persistent credential field in
``AppSettings``: the API key is read from ``OPENAI_API_KEY`` or accepted as a
runtime-only ``SecretStr``/string. Result metadata is allow-listed so neither
credentials nor response headers can leak through ``GenerationResult.raw``.

Structured generation uses the Responses API's native strict JSON Schema
format (``text.format``), not prompt-only JSON instructions.
"""

from __future__ import annotations

import copy
import json
import math
import os
import re
import time
from collections.abc import Iterator, Mapping
from enum import StrEnum
from threading import Event
from types import TracebackType
from typing import Any, Literal, Self

import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from imaginarium_forge.config.settings import OFFICIAL_OPENAI_BASE_URL, AppSettings
from imaginarium_forge.domain.generation.length import LengthUnit
from imaginarium_forge.providers.contracts import (
    GenerationOptions,
    GenerationRequest,
    GenerationResult,
    GenerationStreamDelta,
    GenerationStreamEvent,
    GenerationStreamResult,
    ModelInfo,
    ProviderHealth,
    StructuredResult,
    TokenUsage,
)
from imaginarium_forge.providers.errors import (
    InvalidProviderResponseError,
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
from imaginarium_forge.providers.structured_repair import parse_structured

_SCHEMA_NAME_RE = re.compile(r"[^A-Za-z0-9_-]+")
_DEFAULT_TOP_P = GenerationOptions().top_p
OPENAI_OUTPUT_TOKEN_BUDGET_POLICY_VERSION: Literal["openai-output-token-budget-v1"] = (
    "openai-output-token-budget-v1"
)
OPENAI_PROVIDER_CAP_POLICY_VERSION: Literal["openai-provider-output-cap-v1"] = (
    "openai-provider-output-cap-v1"
)
_SCREENPLAY_WORDS_PER_PAGE = 250


class OpenAIOutputTokenBudgetReason(StrEnum):
    """Why an application requested this bounded output-token allowance."""

    INITIAL_TARGET = "initial_target"
    CONTINUATION_REMAINING = "continuation_remaining"


class OpenAIOutputTokenBudgetMapping(BaseModel):
    """Auditable, prose-free evidence for an application token estimate.

    This mapping is only a request-cost/output cap. It deliberately contains
    no `length_satisfied` field: output measurement remains an application
    concern after the provider returns.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    policy_version: Literal["openai-output-token-budget-v1"] = (
        OPENAI_OUTPUT_TOKEN_BUDGET_POLICY_VERSION
    )
    reason: OpenAIOutputTokenBudgetReason
    unit: LengthUnit
    target_amount: int = Field(gt=0)
    remaining_amount: int | None = Field(default=None, gt=0)
    requested_amount: int = Field(gt=0)
    estimated_tokens: int = Field(gt=0)
    safety_margin_tokens: int = Field(ge=0)
    uncapped_max_output_tokens: int = Field(gt=0)
    provider_cap: int = Field(gt=0)
    request_cap: int | None = Field(default=None, gt=0)
    max_output_tokens: int = Field(gt=0)
    capped_by: (
        Literal[
            "provider_cap",
            "request_cap",
            "provider_cap+request_cap",
        ]
        | None
    ) = None


def map_openai_output_token_budget(
    *,
    target_amount: int,
    unit: LengthUnit | str,
    provider_cap: int,
    remaining_amount: int | None = None,
    request_cap: int | None = None,
) -> OpenAIOutputTokenBudgetMapping:
    """Map a selected-unit target/remaining amount to a bounded token cap.

    The estimate is intentionally conservative for mixed zh-TW/English prose:
    visible characters use 1.5 tokens each, words use 1.5 tokens each, and the
    versioned screenplay policy uses 250 words per page. A 20% margin (at
    least 64 tokens) absorbs tokenization variance. Provider and optional
    caller cost caps are always enforced and recorded rather than hidden.
    """

    normalized_unit = LengthUnit(unit)
    if target_amount <= 0:
        raise ValueError("target_amount must be positive")
    if remaining_amount is not None and remaining_amount <= 0:
        raise ValueError("remaining_amount must be positive when supplied")
    if provider_cap <= 0:
        raise ValueError("provider_cap must be positive")
    if request_cap is not None and request_cap <= 0:
        raise ValueError("request_cap must be positive when supplied")

    selected_amount = remaining_amount if remaining_amount is not None else target_amount
    reason = (
        OpenAIOutputTokenBudgetReason.CONTINUATION_REMAINING
        if remaining_amount is not None
        else OpenAIOutputTokenBudgetReason.INITIAL_TARGET
    )
    if normalized_unit is LengthUnit.SCREENPLAY_PAGES:
        estimated_tokens = math.ceil(selected_amount * _SCREENPLAY_WORDS_PER_PAGE * 1.5)
    else:
        estimated_tokens = math.ceil(selected_amount * 1.5)
    safety_margin_tokens = max(64, math.ceil(estimated_tokens * 0.2))
    uncapped = estimated_tokens + safety_margin_tokens
    max_output_tokens = min(
        uncapped,
        provider_cap,
        request_cap if request_cap is not None else uncapped,
    )
    provider_limited = provider_cap < uncapped and provider_cap == max_output_tokens
    request_limited = (
        request_cap is not None and request_cap < uncapped and request_cap == max_output_tokens
    )
    capped_by: (
        Literal[
            "provider_cap",
            "request_cap",
            "provider_cap+request_cap",
        ]
        | None
    )
    if provider_limited and request_limited:
        capped_by = "provider_cap+request_cap"
    elif provider_limited:
        capped_by = "provider_cap"
    elif request_limited:
        capped_by = "request_cap"
    else:
        capped_by = None
    return OpenAIOutputTokenBudgetMapping(
        reason=reason,
        unit=normalized_unit,
        target_amount=target_amount,
        remaining_amount=remaining_amount,
        requested_amount=selected_amount,
        estimated_tokens=estimated_tokens,
        safety_margin_tokens=safety_margin_tokens,
        uncapped_max_output_tokens=uncapped,
        provider_cap=provider_cap,
        request_cap=request_cap,
        max_output_tokens=max_output_tokens,
        capped_by=capped_by,
    )


def _check_cancel(cancel: Event | None) -> None:
    if cancel is not None and cancel.is_set():
        raise RequestCancelledError("request cancelled by caller before dispatch")


def _strict_schema(schema: Mapping[str, Any]) -> dict[str, Any]:
    """Return the subset-safe shape expected by strict Structured Outputs.

    OpenAI strict schemas require every object property to be listed in
    ``required`` and disallow unspecified object keys. Pydantic defaults are
    validation conveniences, not output-schema constraints, and are removed.
    """

    def visit(value: Any) -> Any:
        if isinstance(value, list):
            return [visit(item) for item in value]
        if not isinstance(value, dict):
            return value
        normalized = {
            key: visit(item) for key, item in value.items() if key not in {"default", "examples"}
        }
        properties = normalized.get("properties")
        if isinstance(properties, dict):
            normalized["required"] = list(properties)
            normalized["additionalProperties"] = False
        elif normalized.get("type") == "object":
            normalized["additionalProperties"] = False
        return normalized

    normalized = visit(copy.deepcopy(dict(schema)))
    if not isinstance(normalized, dict):  # pragma: no cover - Mapping root guarantees this
        raise TypeError("structured output schema must be a JSON object")
    return normalized


def _schema_name(schema: type[BaseModel]) -> str:
    candidate = _SCHEMA_NAME_RE.sub("_", schema.__name__).strip("_-")
    return (candidate or "structured_response")[:64]


class OpenAIProvider:
    """Synchronous Responses API adapter implementing ``LLMProvider``.

    Constructing this class never requires a key and never performs network
    I/O. An absent key becomes a typed error only if a remote operation is
    explicitly requested, which keeps normal offline/Ollama startup safe.
    """

    name = "openai"

    def __init__(
        self,
        settings: AppSettings,
        *,
        api_key: str | SecretStr | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self._settings = settings
        self._base_url = settings.openai_base_url.rstrip("/")
        if self._base_url != OFFICIAL_OPENAI_BASE_URL:
            # Defense in depth for callers that bypass Pydantic validation via
            # model_construct/model_copy(update=...). Custom remote endpoints
            # must not receive author text until the UI has a matching privacy
            # consent gate.
            raise ProviderConfigurationError(
                "only the official OpenAI endpoint is allowed; custom base URLs "
                "require an explicit remote-privacy gate"
            )
        resolved = os.environ.get("OPENAI_API_KEY", "") if api_key is None else api_key
        if isinstance(resolved, SecretStr):
            resolved = resolved.get_secret_value()
        self._api_key = SecretStr(str(resolved).strip())
        self._owns_client = client is None
        self._client = client or httpx.Client(
            base_url=self._base_url,
            timeout=settings.openai_timeout_s,
        )
        self._closed = False

    @property
    def closed(self) -> bool:
        """Whether this provider instance has completed its lifecycle."""

        return self._closed

    def close(self) -> None:
        """Close an internally-created HTTP client exactly once.

        Injected clients remain owned by their caller. The provider itself is
        still considered closed so a context-managed instance cannot be reused
        accidentally with a credential whose intended lifetime has ended.
        """

        if self._closed:
            return
        self._closed = True
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> Self:
        self._ensure_open()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc_value, traceback
        self.close()

    def _ensure_open(self) -> None:
        if self._closed:
            raise ProviderConfigurationError("OpenAI provider is closed")

    @property
    def configured(self) -> bool:
        """Whether a non-empty runtime key is available (never returns it)."""

        return bool(self._api_key.get_secret_value())

    @property
    def default_model(self) -> str:
        return self._settings.openai_model

    def _headers(self) -> dict[str, str]:
        self._ensure_open()
        key = self._api_key.get_secret_value()
        if not key:
            raise ProviderConfigurationError(
                "OpenAI is not configured; set OPENAI_API_KEY or provide a runtime secret"
            )
        return {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        }

    def _timeout(self, request: GenerationRequest) -> float:
        requested = request.timeout_s if request.timeout_s > 0 else self._settings.openai_timeout_s
        return min(requested, self._settings.openai_timeout_s)

    def _output_token_cap_evidence(self, request: GenerationRequest) -> dict[str, Any]:
        """Resolve one explicit cap with versioned, prompt-free evidence.

        `max_output_tokens` is the provider-neutral cap. `num_predict` remains a
        backwards-compatible fallback for older callers. When both are
        supplied the explicit field wins and the ignored legacy value is
        recorded, so the precedence is never ambiguous or silent.
        """

        explicit = request.options.max_output_tokens
        legacy = request.options.num_predict
        if explicit is not None:
            requested = explicit
            source = "explicit_max_output_tokens"
        elif legacy is not None:
            requested = max(1, legacy)
            source = "legacy_num_predict"
        else:
            requested = self._settings.openai_max_output_tokens
            source = "provider_default"
        applied = min(requested, self._settings.openai_max_output_tokens)
        evidence: dict[str, Any] = {
            "policy_version": OPENAI_PROVIDER_CAP_POLICY_VERSION,
            "source": source,
            "requested_max_output_tokens": requested,
            "provider_cap": self._settings.openai_max_output_tokens,
            "applied_max_output_tokens": applied,
            "capped_by_provider": applied < requested,
        }
        if explicit is not None and legacy is not None:
            evidence["ignored_legacy_num_predict"] = legacy
        return evidence

    def _max_output_tokens(self, request: GenerationRequest) -> int:
        return int(self._output_token_cap_evidence(request)["applied_max_output_tokens"])

    def _build_payload(
        self,
        request: GenerationRequest,
        *,
        stream: bool,
        schema: type[BaseModel] | None = None,
    ) -> dict[str, Any]:
        model = request.model.strip() or self._settings.openai_model
        messages: list[dict[str, str]] = []
        if request.input_items:
            messages.extend(item.model_dump() for item in request.input_items)
        else:
            if request.system:
                messages.append({"role": "system", "content": request.system})
            messages.append({"role": "user", "content": request.prompt})
        payload: dict[str, Any] = {
            "model": model,
            "input": messages,
            "max_output_tokens": self._max_output_tokens(request),
            "temperature": request.options.temperature,
            "store": False,
        }
        # The Responses API recommends tuning temperature OR top_p rather than
        # both. Preserve an explicit non-default top_p while keeping ordinary
        # requests on the simpler temperature-only path.
        if request.options.top_p != _DEFAULT_TOP_P:
            payload["top_p"] = request.options.top_p
        if stream:
            payload["stream"] = True
        if request.previous_response_id:
            payload["previous_response_id"] = request.previous_response_id
        if request.reasoning_effort:
            payload["reasoning"] = {"effort": request.reasoning_effort}
        if request.verbosity:
            payload["text"] = {"verbosity": request.verbosity}
        if schema is not None:
            payload.setdefault("text", {})["format"] = {
                "type": "json_schema",
                "name": _schema_name(schema),
                "strict": True,
                "schema": _strict_schema(schema.model_json_schema(mode="validation")),
            }
        return payload

    @staticmethod
    def _safe_error_codes(response: httpx.Response) -> frozenset[str]:
        """Read only allow-listable machine codes; never retain remote messages."""

        try:
            body = response.json()
        except ValueError:
            return frozenset()
        if not isinstance(body, dict):
            return frozenset()
        error = body.get("error")
        if not isinstance(error, dict):
            return frozenset()
        return frozenset(
            str(value).strip().casefold()
            for key in ("code", "type")
            if (value := error.get(key)) is not None and str(value).strip()
        )

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        status = response.status_code
        if status < 400:
            return
        if status in {401, 403}:
            raise ProviderAuthenticationError(
                "OpenAI authentication failed; verify the runtime API key and project access"
            )
        if status == 404:
            raise ModelNotFoundError("OpenAI model or endpoint was not found")
        if status in {408, 504}:
            raise ProviderTimeoutError("OpenAI request timed out")
        if status == 402:
            raise ProviderQuotaError("OpenAI account quota or billing limit was reached")
        if status == 429:
            quota_codes = {
                "billing_hard_limit_reached",
                "credit_balance_too_low",
                "insufficient_quota",
                "usage_limit_reached",
            }
            if OpenAIProvider._safe_error_codes(response) & quota_codes:
                raise ProviderQuotaError("OpenAI account quota or billing limit was reached")
            raise ProviderRateLimitError("OpenAI rate limit or quota was reached")
        if status >= 500:
            raise ProviderUnavailableError(f"OpenAI service is unavailable (HTTP {status})")
        raise ProviderError(f"OpenAI rejected the request (HTTP {status})")

    def _post(self, payload: dict[str, Any], *, timeout_s: float) -> httpx.Response:
        try:
            response = self._client.post(
                "/responses",
                json=payload,
                headers=self._headers(),
                timeout=timeout_s,
            )
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError(f"OpenAI request timed out after {timeout_s:g}s") from exc
        except httpx.ConnectError as exc:
            raise ProviderUnavailableError("cannot reach the official OpenAI API") from exc
        except httpx.RemoteProtocolError as exc:
            raise ProviderIncompleteError("transport_interruption") from exc
        except httpx.HTTPError as exc:
            raise ProviderError("HTTP transport error while calling OpenAI") from exc
        self._raise_for_status(response)
        return response

    @staticmethod
    def _json_body(response: httpx.Response) -> dict[str, Any]:
        try:
            body = response.json()
        except ValueError as exc:
            raise InvalidProviderResponseError("OpenAI returned a malformed JSON response") from exc
        if not isinstance(body, dict):
            raise InvalidProviderResponseError("OpenAI returned a non-object JSON response")
        return body

    @staticmethod
    def _usage(body: Mapping[str, Any]) -> TokenUsage:
        usage = body.get("usage")
        if not isinstance(usage, dict):
            return TokenUsage()
        return TokenUsage(
            prompt_tokens=usage.get("input_tokens"),
            completion_tokens=usage.get("output_tokens"),
            total_tokens=usage.get("total_tokens"),
            cached_prompt_tokens=(
                usage.get("input_tokens_details", {}).get("cached_tokens")
                if isinstance(usage.get("input_tokens_details"), dict)
                else None
            ),
        )

    @staticmethod
    def _safe_raw(
        body: Mapping[str, Any],
        *,
        request_budget: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Allow-list non-secret response metadata for the normalized result."""

        safe: dict[str, Any] = {"provider": "openai"}
        for key in ("id", "status"):
            value = body.get(key)
            if value is not None:
                safe[key] = value
        details = body.get("incomplete_details")
        if isinstance(details, dict) and details.get("reason") in {
            "max_output_tokens",
            "content_filter",
        }:
            safe["incomplete_details"] = {"reason": details["reason"]}
        usage = body.get("usage")
        if isinstance(usage, dict):
            safe["usage"] = {
                key: usage[key]
                for key in ("input_tokens", "output_tokens", "total_tokens")
                if key in usage
            }
        if request_budget is not None:
            safe["request_budget"] = dict(request_budget)
        return safe

    @staticmethod
    def _output_text(
        body: Mapping[str, Any],
        *,
        allow_max_output_partial: bool = False,
    ) -> str:
        status = body.get("status")
        if status == "failed":
            raise ProviderError("OpenAI response generation failed")
        output = body.get("output")
        if not isinstance(output, list):
            raise InvalidProviderResponseError("OpenAI response is missing its output list")
        pieces: list[str] = []
        for item in output:
            if not isinstance(item, dict) or item.get("type") != "message":
                continue
            content = item.get("content")
            if not isinstance(content, list):
                continue
            for part in content:
                if not isinstance(part, dict):
                    continue
                part_type = part.get("type")
                if part_type == "refusal":
                    raise ProviderRefusalError("OpenAI refused the requested generation")
                if part_type == "output_text" and isinstance(part.get("text"), str):
                    pieces.append(part["text"])
        if not pieces:
            if status == "incomplete":
                details = body.get("incomplete_details")
                reason = details.get("reason") if isinstance(details, dict) else None
                raise ProviderIncompleteError(reason)
            raise InvalidProviderResponseError("OpenAI response contains no output text")
        if status == "incomplete":
            details = body.get("incomplete_details")
            reason = details.get("reason") if isinstance(details, dict) else None
            if not (allow_max_output_partial and reason == "max_output_tokens"):
                raise ProviderIncompleteError(reason)
        return "".join(pieces)

    def generate_text(
        self, request: GenerationRequest, *, cancel: Event | None = None
    ) -> GenerationResult:
        _check_cancel(cancel)
        started = time.perf_counter()
        response = self._post(
            self._build_payload(request, stream=False),
            timeout_s=self._timeout(request),
        )
        body = self._json_body(response)
        text = self._output_text(body, allow_max_output_partial=True)
        latency_ms = int((time.perf_counter() - started) * 1000)
        model = body.get("model")
        status = str(body.get("status") or "")
        details = body.get("incomplete_details")
        incomplete_reason = (
            str(details.get("reason"))
            if isinstance(details, dict) and details.get("reason") is not None
            else None
        )
        return GenerationResult(
            text=text,
            model=str(model) if model else request.model or self._settings.openai_model,
            latency_ms=latency_ms,
            usage=self._usage(body),
            finish_reason=status or None,
            response_id=str(body.get("id")) if body.get("id") else None,
            partial=status == "incomplete",
            incomplete_reason=incomplete_reason,
            raw=self._safe_raw(
                body,
                request_budget=self._output_token_cap_evidence(request),
            ),
        )

    def stream_text(
        self, request: GenerationRequest, *, cancel: Event | None = None
    ) -> Iterator[str]:
        """Yield legacy string deltas while preserving legacy partial errors."""

        for event in self.stream_text_events(request, cancel=cancel):
            if isinstance(event, GenerationStreamDelta):
                yield event.text
                continue
            if event.partial:
                raise ProviderIncompleteError(event.incomplete_reason)

    def stream_text_events(
        self, request: GenerationRequest, *, cancel: Event | None = None
    ) -> Iterator[GenerationStreamEvent]:
        """Yield text deltas plus one sanitized Responses API terminal result."""

        _check_cancel(cancel)
        started = time.perf_counter()
        payload = self._build_payload(request, stream=True)
        timeout_s = self._timeout(request)
        try:
            with self._client.stream(
                "POST",
                "/responses",
                json=payload,
                headers=self._headers(),
                timeout=timeout_s,
            ) as response:
                if response.status_code >= 400:
                    self._raise_for_status(response)
                completed = False
                event_name = ""
                for line in response.iter_lines():
                    if cancel is not None and cancel.is_set():
                        raise RequestCancelledError(
                            "OpenAI stream cancelled by caller (best-effort interruption)"
                        )
                    if not line:
                        event_name = ""
                        continue
                    if line.startswith("event:"):
                        event_name = line.removeprefix("event:").strip()
                        continue
                    if not line.startswith("data:"):
                        continue
                    data = line.removeprefix("data:").strip()
                    if data == "[DONE]":
                        break
                    try:
                        event = json.loads(data)
                    except ValueError as exc:
                        raise InvalidProviderResponseError(
                            "OpenAI stream contained malformed event JSON"
                        ) from exc
                    if not isinstance(event, dict):
                        raise InvalidProviderResponseError(
                            "OpenAI stream event is not a JSON object"
                        )
                    event_type = str(event.get("type") or event_name)
                    if event_type == "response.output_text.delta":
                        delta = event.get("delta")
                        if not isinstance(delta, str):
                            raise InvalidProviderResponseError("OpenAI text delta is not a string")
                        if delta:
                            yield GenerationStreamDelta(text=delta)
                    elif event_type.startswith("response.refusal"):
                        raise ProviderRefusalError("OpenAI refused the requested generation")
                    elif event_type == "response.failed":
                        raise ProviderError("OpenAI response generation failed")
                    elif event_type == "response.incomplete":
                        response_payload = event.get("response")
                        response_body = (
                            response_payload if isinstance(response_payload, dict) else {}
                        )
                        details = (
                            response_body.get("incomplete_details")
                            if response_body
                            else event.get("incomplete_details")
                        )
                        reason = details.get("reason") if isinstance(details, dict) else "unknown"
                        yield self._stream_result(
                            request,
                            response_body,
                            started=started,
                            status="incomplete",
                            partial=True,
                            incomplete_reason=str(reason or "unknown"),
                        )
                        completed = True
                        break
                    elif event_type == "response.completed":
                        response_payload = event.get("response")
                        response_body = (
                            response_payload if isinstance(response_payload, dict) else {}
                        )
                        yield self._stream_result(
                            request,
                            response_body,
                            started=started,
                            status="completed",
                        )
                        completed = True
                        break
                if not completed:
                    raise ProviderIncompleteError("transport_interruption")
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError(f"OpenAI stream timed out after {timeout_s:g}s") from exc
        except httpx.ConnectError as exc:
            raise ProviderUnavailableError("cannot reach the official OpenAI API") from exc
        except httpx.RemoteProtocolError as exc:
            raise ProviderIncompleteError("transport_interruption") from exc
        except httpx.HTTPError as exc:
            raise ProviderError("HTTP transport error while streaming from OpenAI") from exc

    def _stream_result(
        self,
        request: GenerationRequest,
        body: Mapping[str, Any],
        *,
        started: float,
        status: Literal["completed", "incomplete"],
        partial: bool = False,
        incomplete_reason: str | None = None,
    ) -> GenerationStreamResult:
        """Normalize one SSE terminal event using the non-stream allow-list."""

        model = body.get("model")
        response_status = str(body.get("status") or status)
        return GenerationStreamResult(
            model=str(model) if model else request.model or self._settings.openai_model,
            latency_ms=max(0, int((time.perf_counter() - started) * 1000)),
            usage=self._usage(body),
            finish_reason=response_status,
            response_id=str(body.get("id")) if body.get("id") else None,
            partial=partial,
            incomplete_reason=incomplete_reason,
            raw=self._safe_raw(
                body,
                request_budget=self._output_token_cap_evidence(request),
            ),
        )

    def generate_structured_once(
        self,
        request: GenerationRequest,
        schema: type[BaseModel],
        *,
        cancel: Event | None = None,
    ) -> StructuredResult:
        _check_cancel(cancel)
        started = time.perf_counter()
        response = self._post(
            self._build_payload(request, stream=False, schema=schema),
            timeout_s=self._timeout(request),
        )
        body = self._json_body(response)
        raw_text = self._output_text(body)
        data = parse_structured(raw_text, schema)
        latency_ms = int((time.perf_counter() - started) * 1000)
        model = body.get("model")
        return StructuredResult(
            data=data,
            raw_text=raw_text,
            model=str(model) if model else request.model or self._settings.openai_model,
            latency_ms=latency_ms,
            usage=self._usage(body),
            response_id=str(body.get("id")) if body.get("id") else None,
        )

    def list_models(self) -> list[ModelInfo]:
        try:
            response = self._client.get(
                "/models",
                headers=self._headers(),
                timeout=min(10.0, self._settings.openai_timeout_s),
            )
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError("OpenAI model listing timed out") from exc
        except httpx.ConnectError as exc:
            raise ProviderUnavailableError("cannot reach the official OpenAI API") from exc
        except httpx.RemoteProtocolError as exc:
            raise ProviderUnavailableError("OpenAI model listing was interrupted") from exc
        except httpx.HTTPError as exc:
            raise ProviderError("HTTP transport error while calling OpenAI") from exc
        self._raise_for_status(response)
        body = self._json_body(response)
        data = body.get("data")
        if not isinstance(data, list):
            raise InvalidProviderResponseError("OpenAI model response is missing its data list")
        models: list[ModelInfo] = []
        for item in data:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                continue
            details = {key: item[key] for key in ("object", "created", "owned_by") if key in item}
            models.append(ModelInfo(name=item["id"], details=details))
        return models

    def health_check(self) -> ProviderHealth:
        if not self.configured:
            return ProviderHealth(ok=False, error="OPENAI_API_KEY is not configured")
        started = time.perf_counter()
        try:
            self.list_models()
        except ProviderError as exc:
            return ProviderHealth(ok=False, error=str(exc))
        return ProviderHealth(
            ok=True,
            version="responses-v1",
            latency_ms=int((time.perf_counter() - started) * 1000),
        )
