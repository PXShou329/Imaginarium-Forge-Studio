"""Local Ollama provider adapter.

Endpoints used (Ollama HTTP API):
  POST /api/generate — single-turn generation (stream and non-stream)
  POST /api/show — explicit installed-model capability inspection
  GET /api/tags — installed models
  GET /api/ps — currently loaded models
  GET /api/version — health probe

Unloading: POST /api/generate with an empty prompt and keep_alive=0 asks Ollama to
release the model from memory.
Resource limits are configured in the provider settings.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from datetime import datetime
from threading import Event
from types import TracebackType
from typing import Any, Self

import httpx
from pydantic import BaseModel

from imaginarium_forge.config.endpoint_security import summarize_endpoint
from imaginarium_forge.config.settings import AppSettings
from imaginarium_forge.domain.generation.ollama_context import (
    OLLAMA_CONTEXT_PROFILE_POLICY_VERSION as OLLAMA_CONTEXT_PROFILE_POLICY_VERSION,
)
from imaginarium_forge.domain.generation.ollama_context import (
    OLLAMA_HIGH_RESOURCE_NUM_CTX as OLLAMA_HIGH_RESOURCE_NUM_CTX,
)
from imaginarium_forge.domain.generation.ollama_context import (
    OLLAMA_LONG_NUM_CTX as OLLAMA_LONG_NUM_CTX,
)
from imaginarium_forge.domain.generation.ollama_context import (
    OLLAMA_MODEL_CAPABILITY_POLICY_VERSION as OLLAMA_MODEL_CAPABILITY_POLICY_VERSION,
)
from imaginarium_forge.domain.generation.ollama_context import (
    OLLAMA_STANDARD_NUM_CTX as OLLAMA_STANDARD_NUM_CTX,
)
from imaginarium_forge.domain.generation.ollama_context import (
    OllamaContextProfile as OllamaContextProfile,
)
from imaginarium_forge.domain.generation.ollama_context import (
    OllamaContextProfileDecision as OllamaContextProfileDecision,
)
from imaginarium_forge.domain.generation.ollama_context import (
    OllamaContextProfileRationale as OllamaContextProfileRationale,
)
from imaginarium_forge.domain.generation.ollama_context import (
    OllamaModelCapabilitySnapshot as OllamaModelCapabilitySnapshot,
)
from imaginarium_forge.domain.generation.ollama_context import (
    _resolved_profile_fields as _resolved_profile_fields,
)
from imaginarium_forge.domain.generation.ollama_context import (
    normalize_ollama_model_id as normalize_ollama_model_id,
)
from imaginarium_forge.domain.generation.ollama_context import (
    resolve_ollama_context_profile as resolve_ollama_context_profile,
)
from imaginarium_forge.providers.contracts import (
    GenerationRequest,
    GenerationResult,
    GenerationStreamDelta,
    GenerationStreamEvent,
    GenerationStreamResult,
    LoadedModelInfo,
    ModelInfo,
    ModelPullProgress,
    ProviderHealth,
    StructuredResult,
    TokenUsage,
)
from imaginarium_forge.providers.errors import (
    InvalidProviderResponseError,
    ModelNotFoundError,
    ProviderConfigurationError,
    ProviderError,
    ProviderIncompleteError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    RequestCancelledError,
)
from imaginarium_forge.providers.ollama_schema import ollama_wire_schema
from imaginarium_forge.providers.structured_repair import parse_structured

JSON_MODE_SUFFIX = (
    "\n\n請只輸出符合下列 JSON Schema 的 JSON，"
    "不要輸出任何其他文字、說明或 Markdown 圍欄：\n{schema}\n"
)


def _check_cancel(cancel: Event | None) -> None:
    if cancel is not None and cancel.is_set():
        raise RequestCancelledError("request cancelled by caller before dispatch")


def _positive_metadata_integer(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value


def _model_info_limit(
    model_info: dict[str, Any],
    *,
    architecture: str | None,
    suffixes: tuple[str, ...],
) -> tuple[int | None, str | None, bool]:
    """Read an explicit GGUF metadata value without inferring from model size.

    When architecture metadata is available, only that architecture's key is
    authoritative. Otherwise a value is accepted only when all matching keys
    agree; mixed text/vision metadata therefore fails closed as ambiguous.
    """

    if architecture:
        for suffix in suffixes:
            key = f"{architecture}.{suffix}"
            value = _positive_metadata_integer(model_info.get(key))
            if value is not None:
                return value, key, False

    candidates: list[tuple[str, int]] = []
    for raw_key, raw_value in model_info.items():
        key = str(raw_key)
        if not any(key == suffix or key.endswith(f".{suffix}") for suffix in suffixes):
            continue
        value = _positive_metadata_integer(raw_value)
        if value is not None:
            candidates.append((key, value))
    if not candidates:
        return None, None, False
    distinct_values = {value for _, value in candidates}
    if len(distinct_values) != 1:
        return None, None, True
    key, value = sorted(candidates)[0]
    return value, key, False


def parse_ollama_model_capability(
    model: str,
    body: dict[str, Any],
    *,
    hardware_context_limit: int | None = None,
) -> OllamaModelCapabilitySnapshot:
    """Sanitize one ``/api/show`` response into explicit token-limit facts."""

    normalized_model = normalize_ollama_model_id(model)
    raw_model_info = body.get("model_info")
    if raw_model_info is None:
        model_info: dict[str, Any] = {}
    elif isinstance(raw_model_info, dict):
        model_info = raw_model_info
    else:
        raise InvalidProviderResponseError("/api/show 'model_info' is not an object")

    raw_architecture = model_info.get("general.architecture")
    architecture = (
        str(raw_architecture).strip()
        if isinstance(raw_architecture, str) and raw_architecture.strip()
        else None
    )
    context_limit, context_key, context_ambiguous = _model_info_limit(
        model_info,
        architecture=architecture,
        suffixes=("context_length", "max_context_length"),
    )
    output_limit, output_key, output_ambiguous = _model_info_limit(
        model_info,
        architecture=architecture,
        suffixes=(
            "max_output_length",
            "output_length",
            "max_output_tokens",
            "max_generation_length",
        ),
    )
    if context_limit is not None and output_limit is not None and output_limit > context_limit:
        raise InvalidProviderResponseError(
            "/api/show reported an output limit larger than its context limit"
        )

    warning_parts: list[str] = []
    if context_limit is None:
        warning_parts.append(
            "模型 context 上限資料互相衝突。"
            if context_ambiguous
            else "模型未回報可驗證的 context 上限。"
        )
    if output_limit is None:
        warning_parts.append(
            "模型輸出 token 上限資料互相衝突。"
            if output_ambiguous
            else "模型未回報獨立輸出 token 上限。"
        )
    if hardware_context_limit is None:
        warning_parts.append("尚未提供可驗證的硬體 context 上限。")

    return OllamaModelCapabilitySnapshot(
        model=normalized_model,
        model_context_limit=context_limit,
        model_output_limit=output_limit,
        hardware_context_limit=hardware_context_limit,
        context_metadata_key=context_key,
        output_metadata_key=output_key,
        hardware_limit_source=("explicit_snapshot" if hardware_context_limit is not None else None),
        warning=" ".join(warning_parts) or None,
    )


class OllamaProvider:
    """Synchronous Ollama client implementing LLMProvider + ModelLifecycle."""

    name = "ollama"

    def __init__(self, settings: AppSettings, client: httpx.Client | None = None) -> None:
        endpoint = summarize_endpoint(settings.ollama_base_url)
        if not endpoint.valid or not endpoint.is_loopback:
            raise ProviderConfigurationError(
                "Ollama endpoint must be an uncredentialed loopback HTTP(S) endpoint"
            )
        self._settings = settings
        self._base_url = settings.ollama_base_url.rstrip("/")
        self._owns_client = client is None
        self._client = client or httpx.Client(
            base_url=self._base_url,
            timeout=settings.ollama_timeout_s,
            # Ollama is a local-first provider. Inheriting HTTP(S)_PROXY can
            # route localhost author text through a remote proxy on systems
            # without a matching NO_PROXY rule.
            trust_env=False,
        )
        self._closed = False

    @property
    def closed(self) -> bool:
        return self._closed

    def close(self) -> None:
        """Release a transient provider's owned HTTP client exactly once."""

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
            raise ProviderError("Ollama provider is closed")

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _error_message(resp: httpx.Response) -> str:
        """Return only an allow-listed classification, never a remote body."""

        if resp.status_code == 404:
            return "requested model or endpoint was not found"
        return f"request failed with HTTP {resp.status_code}"

    def _raise_for_status(self, resp: httpx.Response) -> None:
        if resp.status_code == 404:
            raise ModelNotFoundError(self._error_message(resp))
        if resp.status_code >= 400:
            raise ProviderError(
                f"Ollama returned HTTP {resp.status_code}: {self._error_message(resp)}"
            )

    def _post(self, path: str, payload: dict[str, Any], timeout_s: float) -> httpx.Response:
        self._ensure_open()
        try:
            resp = self._client.post(path, json=payload, timeout=timeout_s)
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError(f"Ollama request timed out after {timeout_s}s") from exc
        except httpx.ConnectError as exc:
            raise ProviderUnavailableError("cannot reach the configured Ollama endpoint") from exc
        except httpx.RemoteProtocolError as exc:
            raise ProviderIncompleteError("transport_interruption") from exc
        except httpx.HTTPError as exc:
            raise ProviderError("HTTP transport error while calling Ollama") from exc
        self._raise_for_status(resp)
        return resp

    def _json_body(self, resp: httpx.Response) -> dict[str, Any]:
        """Parse a response body that must be a JSON object; normalize failures."""
        try:
            body = resp.json()
        except ValueError as exc:
            raise InvalidProviderResponseError("Ollama returned a non-JSON response") from exc
        if not isinstance(body, dict):
            raise InvalidProviderResponseError(
                "Ollama returned JSON whose top-level value is not an object"
            )
        return body

    def _get(self, path: str, timeout_s: float = 10.0) -> httpx.Response:
        self._ensure_open()
        try:
            resp = self._client.get(path, timeout=timeout_s)
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError(f"Ollama request timed out after {timeout_s}s") from exc
        except httpx.ConnectError as exc:
            raise ProviderUnavailableError("cannot reach the configured Ollama endpoint") from exc
        except httpx.RemoteProtocolError as exc:
            raise ProviderUnavailableError("Ollama response was interrupted") from exc
        except httpx.HTTPError as exc:
            raise ProviderError("HTTP transport error while calling Ollama") from exc
        self._raise_for_status(resp)
        return resp

    def _build_payload(
        self, request: GenerationRequest, *, stream: bool, fmt: Any = None
    ) -> dict[str, Any]:
        options: dict[str, Any] = {
            "temperature": request.options.temperature,
            "top_p": request.options.top_p,
        }
        if request.options.seed is not None:
            options["seed"] = request.options.seed
        if request.options.num_predict is not None:
            options["num_predict"] = request.options.num_predict
        if request.options.num_ctx is not None:
            # Exact mapping is intentional. Resource-aware profile selection
            # happens before dispatch and the adapter never silently lowers an
            # explicit context window.
            options["num_ctx"] = request.options.num_ctx
        if request.options.repeat_penalty is not None:
            options["repeat_penalty"] = request.options.repeat_penalty
        if request.options.stop:
            options["stop"] = request.options.stop

        payload: dict[str, Any] = {
            "model": request.model,
            "prompt": request.prompt,
            "stream": stream,
            "options": options,
            "keep_alive": (
                request.keep_alive
                if request.keep_alive is not None
                else self._settings.ollama_keep_alive
            ),
        }
        if request.system:
            payload["system"] = request.system
        if fmt is not None:
            payload["format"] = fmt
        return payload

    @staticmethod
    def _usage(body: dict[str, Any]) -> TokenUsage:
        return TokenUsage(
            prompt_tokens=body.get("prompt_eval_count"),
            completion_tokens=body.get("eval_count"),
        )

    @staticmethod
    def _safe_raw(body: dict[str, Any], request: GenerationRequest) -> dict[str, Any]:
        """Allow-list provenance without retaining response prose or prompts."""

        safe: dict[str, Any] = {"provider": "ollama"}
        for key in (
            "model",
            "created_at",
            "done",
            "done_reason",
            "prompt_eval_count",
            "eval_count",
            "total_duration",
            "load_duration",
            "prompt_eval_duration",
            "eval_duration",
        ):
            if key in body:
                safe[key] = body[key]
        request_options: dict[str, int] = {}
        if request.options.num_predict is not None:
            request_options["num_predict"] = request.options.num_predict
        if request.options.num_ctx is not None:
            request_options["num_ctx"] = request.options.num_ctx
        if request_options:
            safe["request_options"] = request_options
        return safe

    # ------------------------------------------------------------ LLMProvider
    def generate_text(
        self, request: GenerationRequest, *, cancel: Event | None = None
    ) -> GenerationResult:
        _check_cancel(cancel)
        started = time.perf_counter()
        resp = self._post(
            "/api/generate", self._build_payload(request, stream=False), request.timeout_s
        )
        latency_ms = int((time.perf_counter() - started) * 1000)
        body = self._json_body(resp)
        done_reason = body.get("done_reason")
        hit_length_limit = done_reason == "length"
        return GenerationResult(
            text=str(body.get("response", "")),
            model=str(body.get("model", request.model)),
            latency_ms=latency_ms,
            usage=self._usage(body),
            finish_reason=str(done_reason) if done_reason is not None else None,
            partial=hit_length_limit,
            incomplete_reason="max_output_tokens" if hit_length_limit else None,
            raw=self._safe_raw(body, request),
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
        """Yield NDJSON text deltas followed by one sanitized terminal result."""

        self._ensure_open()
        _check_cancel(cancel)
        started = time.perf_counter()
        payload = self._build_payload(request, stream=True)
        try:
            with self._client.stream(
                "POST", "/api/generate", json=payload, timeout=request.timeout_s
            ) as resp:
                if resp.status_code >= 400:
                    resp.read()
                    self._raise_for_status(resp)
                completed = False
                for line in resp.iter_lines():
                    if cancel is not None and cancel.is_set():
                        raise RequestCancelledError(
                            "stream cancelled by caller (best-effort interruption)"
                        )
                    if not line:
                        continue
                    try:
                        chunk = json.loads(line)
                    except ValueError as exc:
                        raise InvalidProviderResponseError("malformed NDJSON stream chunk") from exc
                    if not isinstance(chunk, dict):
                        raise InvalidProviderResponseError("stream chunk is not a JSON object")
                    piece = chunk.get("response")
                    if piece:
                        yield GenerationStreamDelta(text=str(piece))
                    if chunk.get("done"):
                        done_reason = chunk.get("done_reason")
                        hit_length_limit = done_reason == "length"
                        yield GenerationStreamResult(
                            model=str(chunk.get("model", request.model)),
                            latency_ms=max(
                                0,
                                int((time.perf_counter() - started) * 1000),
                            ),
                            usage=self._usage(chunk),
                            finish_reason=(str(done_reason) if done_reason is not None else None),
                            partial=hit_length_limit,
                            incomplete_reason=("max_output_tokens" if hit_length_limit else None),
                            raw=self._safe_raw(chunk, request),
                        )
                        completed = True
                        break
                if not completed:
                    raise ProviderIncompleteError("transport_interruption")
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError(
                f"Ollama stream timed out after {request.timeout_s}s"
            ) from exc
        except httpx.ConnectError as exc:
            raise ProviderUnavailableError("cannot reach the configured Ollama endpoint") from exc
        except httpx.RemoteProtocolError as exc:
            raise ProviderIncompleteError("transport_interruption") from exc
        except httpx.HTTPError as exc:
            raise ProviderError("HTTP transport error during Ollama stream") from exc

    def generate_structured_once(
        self,
        request: GenerationRequest,
        schema: type[BaseModel],
        *,
        cancel: Event | None = None,
    ) -> StructuredResult:
        _check_cancel(cancel)
        json_schema = schema.model_json_schema()
        if self._settings.structured_mode == "schema":
            req = request
            fmt: Any = ollama_wire_schema(schema)
        else:
            suffix = JSON_MODE_SUFFIX.format(schema=json.dumps(json_schema, ensure_ascii=False))
            req = request.model_copy(update={"prompt": request.prompt + suffix})
            fmt = "json"
        started = time.perf_counter()
        resp = self._post(
            "/api/generate", self._build_payload(req, stream=False, fmt=fmt), req.timeout_s
        )
        latency_ms = int((time.perf_counter() - started) * 1000)
        body = self._json_body(resp)
        if body.get("done_reason") == "length":
            raise ProviderIncompleteError("max_output_tokens")
        raw_text = str(body.get("response", ""))
        data = parse_structured(raw_text, schema)
        return StructuredResult(
            data=data,
            raw_text=raw_text,
            model=str(body.get("model", request.model)),
            latency_ms=latency_ms,
            usage=self._usage(body),
        )

    def list_models(self) -> list[ModelInfo]:
        body = self._json_body(self._get("/api/tags"))
        raw_models = body.get("models", [])
        if not isinstance(raw_models, list):
            raise InvalidProviderResponseError("/api/tags 'models' is not a list")
        models: list[ModelInfo] = []
        for item in raw_models:
            if not isinstance(item, dict):
                raise InvalidProviderResponseError(
                    "/api/tags contains a model item that is not an object"
                )
            modified = item.get("modified_at")
            modified_at: datetime | None = None
            if isinstance(modified, str):
                try:
                    modified_at = datetime.fromisoformat(modified)
                except ValueError:
                    modified_at = None
            try:
                models.append(
                    ModelInfo(
                        name=str(item.get("name", "")),
                        size_bytes=item.get("size"),
                        modified_at=modified_at,
                        details=item.get("details") or {},
                    )
                )
            except (TypeError, ValueError) as exc:
                raise InvalidProviderResponseError(
                    "/api/tags contains invalid model metadata"
                ) from exc
        return models

    def inspect_model_capability(
        self,
        model: str,
        *,
        hardware_context_limit: int | None = None,
    ) -> OllamaModelCapabilitySnapshot:
        """Inspect one installed model after an explicit author action.

        ``/api/show`` is intentionally not called by construction, health
        checks, inventory reads, or generation payload mapping. The optional
        hardware limit must come from a separately measured/saved snapshot;
        this adapter never guesses it from parameter count or VRAM size.
        """

        normalized = normalize_ollama_model_id(model)
        body = self._json_body(
            self._post(
                "/api/show",
                {"model": normalized, "verbose": False},
                timeout_s=min(self._settings.ollama_timeout_s, 30.0),
            )
        )
        return parse_ollama_model_capability(
            normalized,
            body,
            hardware_context_limit=hardware_context_limit,
        )

    def pull_model(
        self,
        model: str,
        *,
        cancel: Event | None = None,
        timeout_s: float | None = None,
    ) -> Iterator[ModelPullProgress]:
        """Stream an author-confirmed Ollama model download.

        Callers must enforce the local-endpoint policy before invoking this
        method. Health checks and inventory calls never call it implicitly.
        """

        self._ensure_open()
        normalized = normalize_ollama_model_id(model)
        _check_cancel(cancel)
        payload = {"model": normalized, "stream": True}
        effective_timeout = timeout_s or self._settings.ollama_pull_timeout_s
        completed = False
        try:
            with self._client.stream(
                "POST",
                "/api/pull",
                json=payload,
                timeout=effective_timeout,
            ) as resp:
                if resp.status_code >= 400:
                    resp.read()
                    self._raise_for_status(resp)
                for line in resp.iter_lines():
                    _check_cancel(cancel)
                    if not line:
                        continue
                    try:
                        chunk = json.loads(line)
                    except ValueError as exc:
                        raise InvalidProviderResponseError(
                            "Ollama model download returned malformed NDJSON"
                        ) from exc
                    if not isinstance(chunk, dict):
                        raise InvalidProviderResponseError(
                            "Ollama model download returned a non-object event"
                        )
                    if chunk.get("error"):
                        raise ProviderError("Ollama model download failed")
                    status = str(chunk.get("status", "")).strip()
                    done = status.casefold() == "success"
                    event = ModelPullProgress(
                        model=normalized,
                        status=status or "下載中",
                        completed_bytes=(
                            int(chunk["completed"])
                            if isinstance(chunk.get("completed"), int)
                            else None
                        ),
                        total_bytes=(
                            int(chunk["total"]) if isinstance(chunk.get("total"), int) else None
                        ),
                        done=done,
                    )
                    yield event
                    if done:
                        completed = True
                        break
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError(
                f"Ollama model download timed out after {effective_timeout:g}s"
            ) from exc
        except httpx.ConnectError as exc:
            raise ProviderUnavailableError(f"cannot reach Ollama at {self._base_url}") from exc
        except httpx.RemoteProtocolError as exc:
            raise ProviderUnavailableError("Ollama model download was interrupted") from exc
        except httpx.HTTPError as exc:
            raise ProviderError("HTTP error during Ollama model download") from exc
        if not completed:
            raise InvalidProviderResponseError("Ollama model download ended before a success event")

    def health_check(self) -> ProviderHealth:
        started = time.perf_counter()
        try:
            body = self._json_body(self._get("/api/version", timeout_s=5.0))
        except ProviderError as exc:
            return ProviderHealth(ok=False, error=str(exc))
        latency_ms = int((time.perf_counter() - started) * 1000)
        version = body.get("version")
        return ProviderHealth(
            ok=True,
            version=str(version) if version is not None else None,
            latency_ms=latency_ms,
        )

    # --------------------------------------------------------- ModelLifecycle
    def list_loaded_models(self) -> list[LoadedModelInfo]:
        body = self._json_body(self._get("/api/ps"))
        raw_loaded = body.get("models", [])
        if not isinstance(raw_loaded, list):
            raise InvalidProviderResponseError("/api/ps 'models' is not a list")
        loaded: list[LoadedModelInfo] = []
        for item in raw_loaded:
            loaded.append(
                LoadedModelInfo(
                    name=str(item.get("name", "")),
                    size_vram_bytes=item.get("size_vram"),
                    details={k: v for k, v in item.items() if k not in {"name", "size_vram"}},
                )
            )
        return loaded

    def unload_model(self, model: str) -> bool:
        """Ask Ollama to release the model (empty prompt + keep_alive=0). Best-effort."""
        payload = {"model": model, "prompt": "", "stream": False, "keep_alive": 0}
        try:
            self._json_body(self._post("/api/generate", payload, timeout_s=30.0))
        except ProviderError:
            return False
        return True
