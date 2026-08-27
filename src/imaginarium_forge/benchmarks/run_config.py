"""Reproducible benchmark run configuration (Gate A item A-03).

Every benchmark run serializes a RunConfiguration into both report formats so
that any number in any report can be traced to: which cases, which provider,
which sampling parameters, which app/python/platform, and when. Contains no
secrets and no prompt bodies (case IDs only).
"""

from __future__ import annotations

import platform as platform_module
import re
import sys
import uuid
from datetime import UTC, datetime

from pydantic import BaseModel

from imaginarium_forge import __version__
from imaginarium_forge.canonical import sha256_of_canonical
from imaginarium_forge.config.settings import AppSettings
from imaginarium_forge.goldenset.schema import FixtureCase


class RunConfiguration(BaseModel):
    """Snapshot of everything needed to reproduce a benchmark run."""

    run_id: str
    provider: str
    model: str
    temperature: float
    seed: int | None
    timeout_s: float
    max_repair_attempts: int
    structured_mode: str
    keep_alive: str
    app_version: str
    python_version: str
    platform: str
    fixture_case_ids: list[str]
    fixture_manifest_hash: str
    private_cases_included: bool
    started_at: str


def fixture_manifest_hash(cases: list[FixtureCase]) -> str:
    """Deterministic hash over the full content of every case, keyed by id.

    Changes when any case's content changes; independent of load order.
    """
    manifest = {case.id: sha256_of_canonical(case.model_dump(mode="json")) for case in cases}
    return sha256_of_canonical(manifest)


def build_run_configuration(
    *,
    provider_name: str,
    model: str,
    temperature: float,
    seed: int | None,
    timeout_s: float,
    max_repair_attempts: int,
    settings: AppSettings,
    cases: list[FixtureCase],
) -> RunConfiguration:
    """Assemble the run snapshot at run start."""
    return RunConfiguration(
        run_id=uuid.uuid4().hex,
        provider=provider_name,
        model=model,
        temperature=temperature,
        seed=seed,
        timeout_s=timeout_s,
        max_repair_attempts=max_repair_attempts,
        structured_mode=settings.structured_mode,
        keep_alive=str(settings.ollama_keep_alive),
        app_version=__version__,
        python_version=(
            f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
        ),
        platform=platform_module.platform(),
        fixture_case_ids=[case.id for case in cases],
        fixture_manifest_hash=fixture_manifest_hash(cases),
        private_cases_included=any(case.source == "private" for case in cases),
        started_at=datetime.now(UTC).isoformat(),
    )


_SANITIZE_RE = re.compile(r"[^A-Za-z0-9._-]+")


def sanitize_for_path(name: str) -> str:
    """Windows-safe path fragment: strip characters like ':' from model names."""
    cleaned = _SANITIZE_RE.sub("-", name).strip("-.")
    return cleaned or "model"


def run_directory_name(config: RunConfiguration) -> str:
    """Collision-resistant run directory: UTC microsecond timestamp + short run id.

    Two runs started in the same second still differ by microseconds AND by the
    random short run id, so reports are never overwritten.
    """
    stamp = (
        datetime.fromisoformat(config.started_at)
        .astimezone(UTC)
        .strftime("%Y%m%dT%H%M%S%fZ")
    )
    return f"{stamp}_{config.provider}_{sanitize_for_path(config.model)}_{config.run_id[:8]}"
