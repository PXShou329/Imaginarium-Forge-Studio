r"""Benchmark CLI.

Windows (PowerShell):
  .\.venv\Scripts\python.exe -m imaginarium_forge.benchmarks --model qwen3:8b
Linux / macOS:
  python3 -m imaginarium_forge.benchmarks --model qwen3:8b
Plumbing smoke run (no Ollama, meaningless scores, clearly labeled):
  python3 -m imaginarium_forge.benchmarks --provider mock
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from imaginarium_forge.benchmarks.harness import BenchmarkRunner
from imaginarium_forge.benchmarks.report import write_reports
from imaginarium_forge.benchmarks.run_config import run_directory_name
from imaginarium_forge.config.settings import load_settings
from imaginarium_forge.goldenset.schema import CATEGORIES, load_cases
from imaginarium_forge.logging_setup import configure_logging, get_logger
from imaginarium_forge.providers.base import LLMProvider
from imaginarium_forge.providers.mock import MockProvider
from imaginarium_forge.providers.ollama import OllamaProvider

log = get_logger(__name__)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="imaginarium-forge-benchmarks")
    parser.add_argument("--model", default=None, help="model name (required for --provider ollama)")
    parser.add_argument("--provider", choices=["ollama", "mock"], default="ollama")
    parser.add_argument("--category", action="append", choices=CATEGORIES, default=None)
    parser.add_argument("--goldenset-root", type=Path, default=None)
    parser.add_argument("--out-dir", type=Path, default=None)
    parser.add_argument("--no-private", action="store_true", help="skip goldenset/private cases")
    parser.add_argument("--limit", type=int, default=None, help="run at most N cases")
    parser.add_argument("--max-repair", type=int, default=2)
    parser.add_argument("--temperature", type=float, default=0.2)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args(argv)

    settings = load_settings()
    configure_logging(settings.log_level, settings.log_format)

    provider: LLMProvider
    if args.provider == "mock":
        provider = MockProvider()
        model = args.model or "mock-model"
    else:
        provider = OllamaProvider(settings)
        model = args.model or settings.default_model
        if not model:
            parser.error("--model is required (or set IMF_DEFAULT_MODEL)")
        health = provider.health_check()
        if not health.ok:
            log.error("Ollama unreachable: %s", health.error)
            return 3

    root = args.goldenset_root or settings.goldenset_dir
    cases = load_cases(root, categories=args.category, include_private=not args.no_private)
    if args.limit is not None:
        cases = cases[: args.limit]
    if not cases:
        log.error("no benchmark cases found under %s", root)
        return 2

    runner = BenchmarkRunner(
        provider,
        temperature=args.temperature,
        seed=args.seed,
        max_repair_attempts=args.max_repair,
        settings=settings,
    )
    report = runner.run(cases, model)

    base_dir = args.out_dir or settings.data_dir / "benchmark_runs"
    out_dir = base_dir / run_directory_name(report.run_configuration)
    json_path, md_path = write_reports(report, out_dir)
    print(f"report written: {json_path}")
    print(f"report written: {md_path}")
    if report.is_mock:
        print("NOTE: mock provider run — plumbing validation only, scores are meaningless.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
