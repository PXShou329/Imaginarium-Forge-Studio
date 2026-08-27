"""Environment doctor checks.

Rules (Phase 0 spec §7.3):
  - report, never mutate beyond creating the app's own data directories;
  - NEVER download models or any other artifact;
  - missing optional components (Ollama, GPU) are warnings/skips, not failures.
"""

from __future__ import annotations

import platform
import shutil
import sqlite3
import subprocess
import sys
import uuid
from dataclasses import dataclass
from typing import Literal

from imaginarium_forge.config.settings import AppSettings
from imaginarium_forge.providers.errors import ProviderError
from imaginarium_forge.providers.ollama import OllamaProvider

Status = Literal["ok", "warn", "fail", "skip"]


@dataclass
class CheckResult:
    name: str
    status: Status
    detail: str


def check_python() -> CheckResult:
    version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    if sys.version_info[:2] == (3, 12):
        return CheckResult("python", "ok", f"Python {version}")
    return CheckResult(
        "python", "warn", f"Python {version} (project targets 3.12; behavior unverified)"
    )


def check_platform() -> CheckResult:
    return CheckResult("platform", "ok", platform.platform())


def check_directories(settings: AppSettings) -> CheckResult:
    targets = [
        settings.data_dir,
        settings.data_dir / "exports",
        settings.data_dir / "backups",
        settings.data_dir / "benchmarks",
        settings.goldenset_dir / "private",
    ]
    problems: list[str] = []
    for target in targets:
        try:
            target.mkdir(parents=True, exist_ok=True)
            probe = target / f".write_probe_{uuid.uuid4().hex[:8]}"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
        except OSError as exc:
            problems.append(f"{target}: {exc}")
    if problems:
        return CheckResult("directories", "fail", "; ".join(problems))
    return CheckResult("directories", "ok", f"{len(targets)} directories writable")


def check_sqlite() -> CheckResult:
    version = sqlite3.sqlite_version
    try:
        conn = sqlite3.connect(":memory:")
        try:
            conn.execute("CREATE VIRTUAL TABLE fts_probe USING fts5(content)")
        finally:
            conn.close()
    except sqlite3.Error as exc:
        return CheckResult(
            "sqlite_fts5", "fail", f"SQLite {version}; FTS5 unavailable: {exc}"
        )
    return CheckResult("sqlite_fts5", "ok", f"SQLite {version}; FTS5 available")


def check_gpu() -> CheckResult:
    binary = shutil.which("nvidia-smi")
    if binary is None:
        return CheckResult("gpu", "skip", "nvidia-smi not found (optional)")
    try:
        proc = subprocess.run(
            [binary, "--query-gpu=name,memory.total", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return CheckResult("gpu", "warn", f"nvidia-smi present but failed: {exc}")
    if proc.returncode != 0:
        return CheckResult(
            "gpu", "warn", f"nvidia-smi exit {proc.returncode}: {proc.stderr.strip()}"
        )
    return CheckResult("gpu", "ok", proc.stdout.strip() or "nvidia-smi returned no devices")


def check_settings_summary(settings: AppSettings) -> CheckResult:
    model = settings.default_model or "(unset)"
    return CheckResult(
        "settings",
        "ok",
        (
            f"ollama={settings.ollama_base_url}, structured_mode={settings.structured_mode}, "
            f"keep_alive={settings.ollama_keep_alive}, default_model={model}"
        ),
    )


def check_ollama(provider: OllamaProvider) -> list[CheckResult]:
    results: list[CheckResult] = []
    health = provider.health_check()
    if not health.ok:
        results.append(
            CheckResult(
                "ollama", "warn", f"unreachable (optional for non-LLM work): {health.error}"
            )
        )
        results.append(CheckResult("ollama_models", "skip", "server unreachable"))
        results.append(CheckResult("ollama_loaded", "skip", "server unreachable"))
        return results
    results.append(
        CheckResult("ollama", "ok", f"version {health.version} ({health.latency_ms} ms)")
    )
    try:
        models = provider.list_models()
        names = ", ".join(m.name for m in models) or "(none installed)"
        status: Status = "ok" if models else "warn"
        results.append(CheckResult("ollama_models", status, names))
    except ProviderError as exc:
        results.append(CheckResult("ollama_models", "warn", str(exc)))
    try:
        loaded = provider.list_loaded_models()
        if loaded:
            def _fmt(name: str, vram: int | None) -> str:
                return f"{name} ({vram / 1_073_741_824:.1f} GiB VRAM)" if vram else name

            detail = ", ".join(_fmt(m.name, m.size_vram_bytes) for m in loaded)
        else:
            detail = "no models currently loaded"
        results.append(CheckResult("ollama_loaded", "ok", detail))
    except ProviderError as exc:
        results.append(CheckResult("ollama_loaded", "warn", str(exc)))
    return results


def check_database(settings: AppSettings) -> CheckResult:
    """Report database existence, connectivity, FK enforcement, and migration revision."""
    from sqlalchemy import text

    from imaginarium_forge.infrastructure.db.session import (
        create_db_engine,
        get_migration_status,
    )

    db_path = settings.database_path
    if not db_path.exists():
        return CheckResult(
            "database",
            "warn",
            f"database not created yet at {db_path} (will initialize on first app run)",
        )
    try:
        engine = create_db_engine(db_path)
        try:
            with engine.connect() as conn:
                fk = conn.execute(text("PRAGMA foreign_keys")).scalar()
        finally:
            engine.dispose()
        status = get_migration_status(db_path)
    except Exception as exc:
        return CheckResult("database", "fail", f"database check failed: {exc}")

    if not status.is_current:
        return CheckResult(
            "database",
            "warn",
            f"migration behind head (current={status.current_revision}, "
            f"head={status.head_revision}); back up then run the explicit upgrade",
        )
    return CheckResult(
        "database",
        "ok",
        f"foreign_keys={'ON' if fk == 1 else 'OFF'}, revision={status.current_revision}",
    )


def run_all(settings: AppSettings, provider: OllamaProvider | None = None) -> list[CheckResult]:
    results = [
        check_python(),
        check_platform(),
        check_settings_summary(settings),
        check_directories(settings),
        check_sqlite(),
        check_database(settings),
        check_gpu(),
    ]
    results.extend(check_ollama(provider or OllamaProvider(settings)))
    return results


def has_failure(results: list[CheckResult]) -> bool:
    return any(r.status == "fail" for r in results)
