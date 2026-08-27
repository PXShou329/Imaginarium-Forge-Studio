"""Benchmark harness: run Golden Set cases against a provider and score them.

Reproducibility rules:
  - never fabricate results: every number in a report comes from an actual run;
  - reports carry provider name, model, app version, timestamp and settings snapshot;
  - runs with the mock provider are plumbing validation only and are labeled as such.
"""

from __future__ import annotations

import statistics
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from imaginarium_forge import __version__
from imaginarium_forge.benchmarks.run_config import RunConfiguration, build_run_configuration
from imaginarium_forge.benchmarks.schemas import DEFAULT_SCHEMA_BY_CATEGORY, SCHEMA_REGISTRY
from imaginarium_forge.benchmarks.scoring import CaseScore, score_case
from imaginarium_forge.benchmarks.stats import p95
from imaginarium_forge.config.settings import AppSettings
from imaginarium_forge.goldenset.schema import FixtureCase
from imaginarium_forge.logging_setup import get_logger
from imaginarium_forge.providers.base import LLMProvider
from imaginarium_forge.providers.contracts import GenerationOptions, GenerationRequest
from imaginarium_forge.providers.errors import InvalidStructuredOutputError, ProviderError
from imaginarium_forge.providers.structured_repair import generate_with_repair

log = get_logger(__name__)

STRUCTURED_CATEGORIES = {"structured_output", "scene_parsing", "repair_behavior"}


@dataclass
class CaseRunResult:
    """Raw outcome + score for one case."""

    case: FixtureCase
    score: CaseScore
    text: str | None
    data: dict[str, Any] | None
    latency_ms: int


class BenchmarkRunner:
    """Runs fixture cases sequentially against one provider/model."""

    def __init__(
        self,
        provider: LLMProvider,
        *,
        temperature: float = 0.2,
        seed: int | None = 42,
        timeout_s: float = 180.0,
        max_repair_attempts: int = 2,
        settings: AppSettings | None = None,
    ) -> None:
        self._provider = provider
        self._temperature = temperature
        self._seed = seed
        self._timeout_s = timeout_s
        self._max_repair_attempts = max_repair_attempts
        self._settings = settings or AppSettings(_env_file=None)  # type: ignore[call-arg]

    def _request(self, case: FixtureCase, model: str) -> GenerationRequest:
        return GenerationRequest(
            model=model,
            prompt=case.prompt,
            system=case.system,
            options=GenerationOptions(temperature=self._temperature, seed=self._seed),
            timeout_s=self._timeout_s,
        )

    def _resolve_schema_name(self, case: FixtureCase) -> str:
        name = case.schema_name or DEFAULT_SCHEMA_BY_CATEGORY.get(case.category)
        if name is None or name not in SCHEMA_REGISTRY:
            raise ValueError(f"case {case.id}: unknown or missing schema_name '{name}'")
        return name

    def run_case(self, case: FixtureCase, model: str) -> CaseRunResult:
        request = self._request(case, model)
        text: str | None = None
        data: dict[str, Any] | None = None
        json_valid: bool | None = None
        repair_attempts = 0
        error: str | None = None
        started = time.perf_counter()
        try:
            if case.category in STRUCTURED_CATEGORIES:
                schema = SCHEMA_REGISTRY[self._resolve_schema_name(case)]
                try:
                    result = generate_with_repair(
                        self._provider,
                        request,
                        schema,
                        max_repair_attempts=self._max_repair_attempts,
                    )
                    data = result.data
                    text = result.raw_text
                    json_valid = True
                    repair_attempts = result.repair_attempts
                except InvalidStructuredOutputError as exc:
                    json_valid = False
                    text = exc.raw_text
                    repair_attempts = self._max_repair_attempts
            else:
                gen = self._provider.generate_text(request)
                text = gen.text
        except ProviderError as exc:
            error = f"{type(exc).__name__}: {exc}"
        latency_ms = int((time.perf_counter() - started) * 1000)
        score = score_case(
            case,
            text=text,
            data=data,
            json_valid=json_valid,
            repair_attempts=repair_attempts,
            latency_ms=latency_ms,
            error=error,
        )
        return CaseRunResult(case=case, score=score, text=text, data=data, latency_ms=latency_ms)

    def run(self, cases: list[FixtureCase], model: str) -> BenchmarkReport:
        run_configuration = build_run_configuration(
            provider_name=self._provider.name,
            model=model,
            temperature=self._temperature,
            seed=self._seed,
            timeout_s=self._timeout_s,
            max_repair_attempts=self._max_repair_attempts,
            settings=self._settings,
            cases=cases,
        )
        results: list[CaseRunResult] = []
        for case in cases:
            log.info("running case %s (%s)", case.id, case.category)
            results.append(self.run_case(case, model))
        return BenchmarkReport.build(
            provider_name=self._provider.name,
            model=model,
            results=results,
            run_configuration=run_configuration,
        )


@dataclass
class CategoryAggregate:
    """Per-category pass-rate and latency stats."""

    category: str
    cases: int
    passed: int
    pass_rate: float
    latency_ms_mean: float
    latency_ms_p95: float
    json_validity_rate: float | None


@dataclass
class BenchmarkReport:
    """Full run report (serialized by benchmarks.report)."""

    created_at: str
    provider_name: str
    model: str
    app_version: str
    results: list[CaseRunResult]
    aggregates: list[CategoryAggregate]
    run_configuration: RunConfiguration

    @property
    def is_mock(self) -> bool:
        return self.provider_name == "mock"

    @staticmethod
    def build(
        *,
        provider_name: str,
        model: str,
        results: list[CaseRunResult],
        run_configuration: RunConfiguration,
    ) -> BenchmarkReport:
        by_category: dict[str, list[CaseRunResult]] = {}
        for item in results:
            by_category.setdefault(item.case.category, []).append(item)
        aggregates: list[CategoryAggregate] = []
        for category, items in sorted(by_category.items()):
            latencies = [i.latency_ms for i in items]
            passed = sum(1 for i in items if i.score.passed)
            json_flags = [
                i.score.metrics["json_valid"]
                for i in items
                if "json_valid" in i.score.metrics
            ]
            aggregates.append(
                CategoryAggregate(
                    category=category,
                    cases=len(items),
                    passed=passed,
                    pass_rate=round(passed / len(items), 3),
                    latency_ms_mean=round(statistics.fmean(latencies), 1),
                    latency_ms_p95=p95(latencies),
                    json_validity_rate=(
                        round(sum(1 for f in json_flags if f) / len(json_flags), 3)
                        if json_flags
                        else None
                    ),
                )
            )
        return BenchmarkReport(
            created_at=datetime.now(UTC).isoformat(),
            provider_name=provider_name,
            model=model,
            app_version=__version__,
            results=results,
            aggregates=aggregates,
            run_configuration=run_configuration,
        )
