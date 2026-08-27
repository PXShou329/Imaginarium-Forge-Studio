"""Benchmark report serialization (JSON + Markdown)."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from imaginarium_forge.benchmarks.harness import BenchmarkReport

MOCK_WARNING = (
    "> ⚠ **MOCK PROVIDER RUN** — plumbing validation only. "
    "These numbers say nothing about any real model.\n"
)


def _report_dict(report: BenchmarkReport) -> dict[str, object]:
    return {
        "created_at": report.created_at,
        "provider": report.provider_name,
        "model": report.model,
        "app_version": report.app_version,
        "is_mock": report.is_mock,
        "percentile_method": "nearest-rank (imaginarium_forge.benchmarks.stats)",
        "run_configuration": report.run_configuration.model_dump(mode="json"),
        "aggregates": [asdict(a) for a in report.aggregates],
        "results": [
            {
                "case_id": r.case.id,
                "category": r.case.category,
                "source": r.case.source,
                "passed": r.score.passed,
                "passed_checks": r.score.passed_checks,
                "failed_checks": r.score.failed_checks,
                "metrics": r.score.metrics,
                "needs_manual_review": r.score.needs_manual_review,
                "error": r.score.error,
                "latency_ms": r.latency_ms,
                "output_excerpt": (r.text or "")[:400],
                "structured_data": r.data,
            }
            for r in report.results
        ],
    }


def write_reports(report: BenchmarkReport, out_dir: Path) -> tuple[Path, Path]:
    """Write report.json and report.md into a timestamped directory; return both paths."""
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "report.json"
    md_path = out_dir / "report.md"
    json_path.write_text(
        json.dumps(_report_dict(report), ensure_ascii=False, indent=2), encoding="utf-8"
    )

    lines: list[str] = [f"# Benchmark Report — {report.model} ({report.provider_name})", ""]
    if report.is_mock:
        lines += [
            MOCK_WARNING,
            "> ⚠ Real-model validation is PENDING: no conclusion in this report is "
            "based on an actual model run.\n",
        ]
    cfg = report.run_configuration
    lines += [
        f"- created_at: {report.created_at}",
        f"- app_version: {report.app_version}",
        f"- total cases: {len(report.results)}",
        "- latency percentiles: nearest-rank method "
        "(`imaginarium_forge.benchmarks.stats`)",
        "",
        "## Run configuration",
        "",
        "```yaml",
        *(f"{key}: {value}" for key, value in cfg.model_dump(mode="json").items()),
        "```",
        "",
        "## Per-category aggregates",
        "",
        "| category | cases | passed | pass_rate | latency mean (ms) "
        "| latency p95 (ms) | json validity |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for agg in report.aggregates:
        jv = "-" if agg.json_validity_rate is None else f"{agg.json_validity_rate:.0%}"
        lines.append(
            f"| {agg.category} | {agg.cases} | {agg.passed} | {agg.pass_rate:.0%} "
            f"| {agg.latency_ms_mean} | {agg.latency_ms_p95} | {jv} |"
        )
    lines += ["", "## Failed / review-needed cases", ""]
    flagged = [
        r
        for r in report.results
        if (not r.score.passed) or r.score.needs_manual_review
    ]
    if not flagged:
        lines.append("(none)")
    for r in flagged:
        status = "FAIL" if not r.score.passed else "manual-review"
        detail = ", ".join(r.score.failed_checks) or r.score.error or "-"
        lines.append(f"- `{r.case.id}` [{status}] {detail}")
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, md_path
