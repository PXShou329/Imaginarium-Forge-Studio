"""DISPOSABLE SPIKE — Traditional-Chinese scene description → MinimalSceneParse.

Do NOT build production code on this module. It exists only to compare candidate
local models on zh-TW parsing quality (field extraction, negative intent,
emotional subtext, JSON validity, latency). The final Prompt AST and Scene
Parser belong to Phase 2 and will be designed separately.

Promotion / rejection criteria: docs/design/spike-scene-parser-criteria.md
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from imaginarium_forge.benchmarks.schemas import MinimalSceneParse
from imaginarium_forge.config.settings import load_settings
from imaginarium_forge.logging_setup import configure_logging
from imaginarium_forge.providers.base import LLMProvider
from imaginarium_forge.providers.contracts import GenerationOptions, GenerationRequest
from imaginarium_forge.providers.errors import InvalidStructuredOutputError, ProviderError
from imaginarium_forge.providers.mock import MockProvider
from imaginarium_forge.providers.ollama import OllamaProvider
from imaginarium_forge.providers.structured_repair import generate_with_repair

SPIKE_SYSTEM = "你是一個把繁體中文視覺場景描述轉成結構化 JSON 的解析器。只輸出 JSON。"

SPIKE_PROMPT_TEMPLATE = """將下列場景描述解析為結構化欄位。

規則：
1. 只輸出 JSON，不要任何說明文字或 Markdown 圍欄。
2. 描述中「明確可見」的表情放 expression_visible；「暗示的、內心的」放 expression_implied。
3. 使用者明確排除的元素（例如「不要⋯」「避免⋯」）放 negative_intent。
4. 無法判斷的欄位使用 null 或空陣列，不要編造。
5. 欄位值使用簡短英文詞（tag 風格），例如 "rooftop"、"sunset"、"from behind"。

場景描述：
{description}
"""


def build_request(model: str, description: str, *, timeout_s: float = 180.0) -> GenerationRequest:
    """Build the spike request (low temperature, fixed seed for comparability)."""
    return GenerationRequest(
        model=model,
        prompt=SPIKE_PROMPT_TEMPLATE.format(description=description),
        system=SPIKE_SYSTEM,
        options=GenerationOptions(temperature=0.1, seed=42),
        timeout_s=timeout_s,
    )


def parse_description(
    provider: LLMProvider, model: str, description: str, *, max_repair_attempts: int = 2
) -> tuple[MinimalSceneParse, int, int]:
    """Return (parsed, latency_ms, repair_attempts). Raises normalized provider errors."""
    request = build_request(model, description)
    started = time.perf_counter()
    result = generate_with_repair(
        provider, request, MinimalSceneParse, max_repair_attempts=max_repair_attempts
    )
    latency_ms = int((time.perf_counter() - started) * 1000)
    return MinimalSceneParse.model_validate(result.data), latency_ms, result.repair_attempts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="scene-parser-spike")
    parser.add_argument("--model", default=None)
    parser.add_argument("--provider", choices=["ollama", "mock"], default="ollama")
    parser.add_argument("--input", action="append", help="scene description (repeatable)")
    parser.add_argument("--input-file", type=Path, help="UTF-8 text file, one description per line")
    parser.add_argument("--max-repair", type=int, default=2)
    args = parser.parse_args(argv)

    settings = load_settings()
    configure_logging(settings.log_level, settings.log_format)

    descriptions: list[str] = list(args.input or [])
    if args.input_file:
        descriptions.extend(
            line.strip()
            for line in args.input_file.read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
    if not descriptions:
        parser.error("provide at least one --input or --input-file")

    provider: LLMProvider
    if args.provider == "mock":
        provider = MockProvider()
        model = args.model or "mock-model"
    else:
        provider = OllamaProvider(settings)
        model = args.model or settings.default_model
        if not model:
            parser.error("--model is required (or set IMF_DEFAULT_MODEL)")

    exit_code = 0
    for description in descriptions:
        print(f"\n=== {description}")
        try:
            parsed, latency_ms, repairs = parse_description(
                provider, model, description, max_repair_attempts=args.max_repair
            )
        except InvalidStructuredOutputError as exc:
            exit_code = 1
            print(f"[invalid-structured-output] {exc.validation_errors[:300]}")
            print(f"raw: {exc.raw_text[:300]}")
            continue
        except ProviderError as exc:
            print(f"[provider-error] {type(exc).__name__}: {exc}")
            return 3
        print(json.dumps(parsed.model_dump(), ensure_ascii=False, indent=2))
        print(f"latency_ms={latency_ms} repair_attempts={repairs}")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
