"""Structured-output parsing and the bounded repair loop.

The repair loop concept comes from proposal v1.1 §14.5; Phase 0 only needs it for
the `repair_behavior` benchmark category and the parser spike.
"""

from __future__ import annotations

import json
from threading import Event
from typing import Any

from pydantic import BaseModel, ValidationError

from imaginarium_forge.providers.base import LLMProvider
from imaginarium_forge.providers.contracts import GenerationRequest, StructuredResult
from imaginarium_forge.providers.errors import InvalidStructuredOutputError

REPAIR_PROMPT_TEMPLATE = (
    "你先前的輸出無法通過結構驗證。\n"
    "驗證錯誤：\n{errors}\n\n"
    "原始輸出：\n{raw}\n\n"
    "請重新輸出「只有 JSON」的修正版本，必須符合下列 JSON Schema，"
    "不要輸出任何說明文字或 Markdown 圍欄：\n{schema}\n"
)


def extract_json_text(raw: str) -> str:
    """Best-effort extraction of a JSON object from model output.

    Handles the two most common local-model failure shapes: Markdown code fences
    and leading/trailing prose around a JSON object.
    """
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    if not text.startswith("{"):
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end > start:
            text = text[start : end + 1]
    return text


def parse_structured(raw_text: str, schema: type[BaseModel]) -> dict[str, Any]:
    """Parse and validate model output against `schema`; raise a normalized error otherwise."""
    candidate = extract_json_text(raw_text)
    try:
        payload = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise InvalidStructuredOutputError(
            "model output is not valid JSON", raw_text=raw_text, validation_errors=str(exc)
        ) from exc
    try:
        return schema.model_validate(payload).model_dump()
    except ValidationError as exc:
        raise InvalidStructuredOutputError(
            "JSON does not match the requested schema",
            raw_text=raw_text,
            validation_errors=str(exc),
        ) from exc


def generate_with_repair(
    provider: LLMProvider,
    request: GenerationRequest,
    schema: type[BaseModel],
    *,
    max_repair_attempts: int = 2,
    cancel: Event | None = None,
) -> StructuredResult:
    """Bounded structured generation: initial attempt + up to N repair rounds.

    Latency of failed attempts is not accumulated (kept simple for Phase 0); the
    returned `repair_attempts` counts how many repair rounds were needed.
    """
    attempts = 0
    last_error: InvalidStructuredOutputError | None = None
    current = request
    while attempts <= max_repair_attempts:
        try:
            result = provider.generate_structured_once(current, schema, cancel=cancel)
            return result.model_copy(update={"repair_attempts": attempts})
        except InvalidStructuredOutputError as exc:
            last_error = exc
            attempts += 1
            if attempts > max_repair_attempts:
                break
            schema_json = json.dumps(schema.model_json_schema(), ensure_ascii=False)
            repair_prompt = REPAIR_PROMPT_TEMPLATE.format(
                errors=exc.validation_errors[:2000],
                raw=exc.raw_text[:2000],
                schema=schema_json,
            )
            current = request.model_copy(update={"prompt": repair_prompt})
    assert last_error is not None
    raise last_error
