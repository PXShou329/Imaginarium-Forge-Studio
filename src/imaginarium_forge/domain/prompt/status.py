"""Compilation / lint status (Gate A A-04).

`blocked` outcomes stay fully visible as diagnostics but are second-class
citizens everywhere else: they cannot be persisted, accepted, exported as
final, or turned into experiments — enforced at the service layer AND by DB
CHECK constraints (persisted variants may only be preview/valid, ok/warnings).
"""

from __future__ import annotations

from enum import StrEnum

from imaginarium_forge.domain.prompt.lint import LintLevel, LintReport


class CompilationStatus(StrEnum):
    PREVIEW = "preview"
    VALID = "valid"
    BLOCKED = "blocked"


class LintStatus(StrEnum):
    OK = "ok"
    WARNINGS = "warnings"
    BLOCKED = "blocked"


def derive_lint_status(report: LintReport) -> LintStatus:
    if report.has_errors:
        return LintStatus.BLOCKED
    if report.by_level(LintLevel.WARNING):
        return LintStatus.WARNINGS
    return LintStatus.OK
