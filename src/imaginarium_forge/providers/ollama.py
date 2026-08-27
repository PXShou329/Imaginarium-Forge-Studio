"""Ollama provider (ADR-003) — implemented only to the extent Phase 0 validation requires.

Endpoints used (Ollama HTTP API):
  POST /api/generate  — single-turn generation (stream and non-stream)
  GET  /api/tags      — installed models
  GET  /api/ps        — currently loaded models (resource coordination, risk R-16)
  GET  /api/version   — health probe

Unloading: POST /api/generate with an empty prompt and keep_alive=0 asks Ollama to
release the model from memory. Behavior must be verified on the target machine;
see docs/architecture/resource-coordination-rtx3070.md.
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

from imaginarium_forge.config.settings import AppSettings
from imaginarium_forge.providers.contracts import (
    GenerationRequest,
    GenerationResult,
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
    ProviderError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    RequestCancelledError,
)
from imaginarium_forge.providers.structured_repair import parse_structured

JSON_MODE_SUFFIX = (
    "\n\n請只輸出符合下列 JSON Schema 的 JSON，"
    "不要輸出任何其他文字、說明或 Markdown 圍欄：\n{schema}\n"
)


def _check_cancel(cancel: Event | None) -> None:
    if cancel is not None and cancel.is_set():
        raise RequestCancelledError("request cancelled by caller before dispatch")


_EXCERPT_LIMIT = 200


def _excerpt(text: str, limit: int = _EXCERPT_LIMIT) -> str:
    """Truncate raw provider output for error messages (never log full bodies)."""
    cleaned = text.strip().replace("\n", " ")
    return cleaned if len(cleaned) <= limit else cleaned[:limit] + "…"


def normalize_ollama_model_id(value: object) -> str:
    """Normalize one model identifier before placing it in a JSON request."""

    model = str(value).strip()
    if not model:
        raise ValueError("Ollama model ID must not be blank")
    if len(model) > 256:
        raise ValueError("Ollama model ID must not exceed 256 characters")
    if any(
        character.isspace() or ord(character) < 32 or ord(character) == 127
        for character in model
    ):
        raise ValueError("Ollama model ID must not contain whitespace or control characters")
    return model


class OllamaProvider:
    """Synchronous Ollama client implementing LLMProvider + ModelLifecycle."""

    name = "ollama"

    def __init__(self, settings: AppSettings, client: httpx.Client | None = None) -> None:
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
        try:
            body = resp.json()
        except ValueError:
            return resp.text
        if isinstance(body, dict) and "error" in body:
            return str(body["error"])
        return resp.text

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
            raise ProviderUnavailableError(f"cannot reach Ollama at {self._base_url}") from exc
        except httpx.RemoteProtocolError as exc:
            raise InvalidProviderResponseError(f"protocol violation from Ollama: {exc}") from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"HTTP error calling Ollama: {exc}") from exc
        self._raise_for_status(resp)
        return resp

    def _json_body(self, resp: httpx.Response) -> dict[str, Any]:
        """Parse a response body that must be a JSON object; normalize failures."""
        try:
            body = resp.json()
        except ValueError as exc:
            raise InvalidProviderResponseError(
                f"Ollama returned non-JSON body: {_excerpt(resp.text)}"
            ) from exc
        if not isinstance(body, dict):
            raise InvalidProviderResponseError(
                f"Ollama returned non-object JSON ({type(body).__name__}): "
                f"{_excerpt(resp.text)}"
            )
        return body

    def _get(self, path: str, timeout_s: float = 10.0) -> httpx.Response:
        self._ensure_open()
        try:
            resp = self._client.get(path, timeout=timeout_s)
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError(f"Ollama request timed out after {timeout_s}s") from exc
        except httpx.ConnectError as exc:
            raise ProviderUnavailableError(f"cannot reach Ollama at {self._base_url}") from exc
        except httpx.RemoteProtocolError as exc:
            raise InvalidProviderResponseError(f"protocol violation from Ollama: {exc}") from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"HTTP error calling Ollama: {exc}") from exc
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
        return GenerationResult(
            text=str(body.get("response", "")),
            model=str(body.get("model", request.model)),
            latency_ms=latency_ms,
            usage=self._usage(body),
            finish_reason=str(done_reason) if done_reason is not None else None,
            raw=body,
        )

    def stream_text(
        self, request: GenerationRequest, *, cancel: Event | None = None
    ) -> Iterator[str]:
        self._ensure_open()
        _check_cancel(cancel)
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
                        raise InvalidProviderResponseError(
                            f"malformed NDJSON stream chunk: {_excerpt(line)}"
                        ) from exc
                    if not isinstance(chunk, dict):
                        raise InvalidProviderResponseError(
                            f"stream chunk is not a JSON object: {_excerpt(line)}"
                        )
                    piece = chunk.get("response")
                    if piece:
                        yield str(piece)
                    if chunk.get("done"):
                        completed = True
                        break
                if not completed:
                    raise InvalidProviderResponseError(
                        "stream ended before Ollama signaled completion (done=true)"
                    )
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError(
                f"Ollama stream timed out after {request.timeout_s}s"
            ) from exc
        except httpx.ConnectError as exc:
            raise ProviderUnavailableError(f"cannot reach Ollama at {self._base_url}") from exc
        except httpx.RemoteProtocolError as exc:
            raise InvalidProviderResponseError(f"protocol violation from Ollama: {exc}") from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"HTTP error during Ollama stream: {exc}") from exc

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
            fmt: Any = json_schema
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
            raise InvalidProviderResponseError(
                f"/api/tags 'models' is not a list: {_excerpt(str(raw_models))}"
            )
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
                            int(chunk["total"])
                            if isinstance(chunk.get("total"), int)
                            else None
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
            raise InvalidProviderResponseError(
                "protocol violation during Ollama model download"
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderError("HTTP error during Ollama model download") from exc
        if not completed:
            raise InvalidProviderResponseError(
                "Ollama model download ended before a success event"
            )

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
            raise InvalidProviderResponseError(
                f"/api/ps 'models' is not a list: {_excerpt(str(raw_loaded))}"
            )
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
