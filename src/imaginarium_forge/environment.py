"""Validate the interpreter and dependency configuration.

Reports clearly when the running environment does not match what the project
declares, instead of failing later with a confusing import or dependency
error. Checks are advisory: they never abort the application, because a
partially-matching environment is still usable for reading existing work.
"""

from __future__ import annotations

import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

#: the interpreter series this project is developed and tested against
SUPPORTED_PYTHON = (3, 12)


@dataclass(frozen=True, slots=True)
class EnvironmentIssue:
    level: str  # "error" | "warning"
    code: str
    message: str


def project_root(start: Path | None = None) -> Path | None:
    """Walk upwards looking for the directory that holds ``pyproject.toml``."""
    current = (start or Path(__file__).resolve()).parent
    for candidate in (current, *current.parents):
        if (candidate / "pyproject.toml").is_file():
            return candidate
    return None


def check_environment(root: Path | None = None) -> list[EnvironmentIssue]:
    """Return every detected environment problem, most severe first."""
    issues: list[EnvironmentIssue] = []
    base = root or project_root()

    major, minor = sys.version_info[:2]
    if (major, minor) < SUPPORTED_PYTHON:
        issues.append(
            EnvironmentIssue(
                level="error",
                code="unsupported_python",
                message=(
                    f"目前 Python 版本為 {major}.{minor}，"
                    f"本專案需要 {SUPPORTED_PYTHON[0]}.{SUPPORTED_PYTHON[1]} 以上。"
                    "請改用 Python 3.12（見 .python-version）。"
                ),
            )
        )
    elif (major, minor) > SUPPORTED_PYTHON:
        issues.append(
            EnvironmentIssue(
                level="warning",
                code="untested_python",
                message=(
                    f"目前 Python 版本為 {major}.{minor}，"
                    f"但本專案的測試環境是 "
                    f"{SUPPORTED_PYTHON[0]}.{SUPPORTED_PYTHON[1]}。"
                    "可以執行，但不在已驗證範圍內。"
                ),
            )
        )

    if base is None:
        return issues

    lock = base / "uv.lock"
    manifest = base / "pyproject.toml"
    if not lock.is_file():
        issues.append(
            EnvironmentIssue(
                level="warning",
                code="missing_lock",
                message=(
                    "找不到 uv.lock（權威依賴鎖定檔）。"
                    "請依 README.md 的安裝步驟重新取得完整專案。"
                ),
            )
        )
    # Filesystem timestamps do not prove dependency consistency. The launcher
    # checks the lock; the running application only verifies its presence.

    if manifest.is_file():
        declared = _declared_python(manifest)
        if declared and not declared.startswith(">=3.12"):
            issues.append(
                EnvironmentIssue(
                    level="warning",
                    code="requires_python_drift",
                    message=(
                        f"pyproject.toml 宣告 requires-python = {declared!r}，"
                        "與實際驗證過的 3.12 不一致。"
                    ),
                )
            )
    return sorted(issues, key=lambda i: 0 if i.level == "error" else 1)


def _declared_python(manifest: Path) -> str:
    try:
        with manifest.open("rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        return ""
    project = data.get("project", {})
    value = project.get("requires-python", "")
    return value if isinstance(value, str) else ""
