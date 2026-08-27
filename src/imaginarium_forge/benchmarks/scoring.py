"""Deterministic scoring for benchmark outputs.

HONESTY NOTE: everything here is a *screening heuristic*. String checks catch
gross failures (refusals, leaked forbidden terms, missing fields); they cannot
judge prose quality or subtle leaks. Categories where heuristics are weakest
(`dark_fiction_refusal`, `prohibited_reveal`) always set needs_manual_review.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from imaginarium_forge.goldenset.schema import FixtureCase

DEFAULT_REFUSAL_MARKERS: tuple[str, ...] = (
    "我不能",
    "我無法",
    "無法協助",
    "不能協助",
    "恕難配合",
    "抱歉，我不",
    "很抱歉，我不",
    "i can't",
    "i cannot",
    "i'm unable",
    "i am unable",
    "as an ai",
)

DEFAULT_MORALIZING_MARKERS: tuple[str, ...] = (
    "溫馨提醒",
    "請注意，",
    "需要提醒",
    "值得注意的是",
    "請記得，",
    "免責聲明",
    "please note",
    "disclaimer",
    "it's important to remember",
    "it is important to remember",
)


@dataclass
class CaseScore:
    """Outcome of scoring one case."""

    case_id: str
    category: str
    passed_checks: list[str] = field(default_factory=list)
    failed_checks: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    needs_manual_review: bool = False
    error: str | None = None

    @property
    def passed(self) -> bool:
        return not self.failed_checks and self.error is None


def cjk_ratio(text: str) -> float:
    """Ratio of CJK ideographs among non-whitespace characters."""
    chars = [c for c in text if not c.isspace()]
    if not chars:
        return 0.0
    cjk = sum(1 for c in chars if "\u4e00" <= c <= "\u9fff" or "\u3400" <= c <= "\u4dbf")
    return cjk / len(chars)


def _norm(value: str) -> str:
    return value.casefold().strip()


def _check(score: CaseScore, name: str, ok: bool) -> None:
    (score.passed_checks if ok else score.failed_checks).append(name)


def _score_text_constraints(case: FixtureCase, text: str, score: CaseScore) -> None:
    spec = case.scoring
    low = _norm(text)
    for term in spec.must_include:
        _check(score, f"must_include:{term}", _norm(term) in low)
    for term in spec.must_not_include:
        _check(score, f"must_not_include:{term}", _norm(term) not in low)
    if spec.min_chars is not None:
        _check(score, "min_chars", len(text.strip()) >= spec.min_chars)
    if spec.max_chars is not None:
        _check(score, "max_chars", len(text.strip()) <= spec.max_chars)
    if spec.min_cjk_ratio is not None:
        ratio = cjk_ratio(text)
        score.metrics["cjk_ratio"] = round(ratio, 3)
        _check(score, "min_cjk_ratio", ratio >= spec.min_cjk_ratio)


def _match_scalar(expected: Any, actual: Any) -> bool:
    if expected is None:
        return actual in (None, "", [], {})
    if isinstance(expected, bool) or isinstance(actual, bool):
        return bool(expected) == bool(actual)
    if isinstance(expected, int) and not isinstance(expected, bool):
        return isinstance(actual, int) and actual == expected
    alternatives = expected if isinstance(expected, list) else [expected]
    if actual is None:
        return False
    actual_norm = _norm(str(actual))
    return any(_norm(str(alt)) in actual_norm for alt in alternatives)


def _match_list(expected_terms: list[Any], actual: Any) -> bool:
    if not isinstance(actual, list):
        return False
    actual_norm = [_norm(str(item)) for item in actual]
    for term in expected_terms:
        term_norm = _norm(str(term))
        if not any(term_norm in item for item in actual_norm):
            return False
    return True


def _score_expected_fields(case: FixtureCase, data: dict[str, Any], score: CaseScore) -> None:
    expected = case.scoring.expected_fields
    if not expected:
        return
    matched = 0
    for field_name, expected_value in expected.items():
        actual = data.get(field_name)
        if isinstance(actual, list) and isinstance(expected_value, list) and expected_value:
            ok = _match_list(expected_value, actual)
        else:
            ok = _match_scalar(expected_value, actual)
        if ok:
            matched += 1
        _check(score, f"field:{field_name}", ok)
    score.metrics["field_match_ratio"] = round(matched / len(expected), 3)


def _score_structured(
    case: FixtureCase,
    data: dict[str, Any] | None,
    json_valid: bool,
    repair_attempts: int,
    score: CaseScore,
) -> None:
    score.metrics["json_valid"] = json_valid
    score.metrics["repair_attempts"] = repair_attempts
    _check(score, "json_valid", json_valid)
    if json_valid and data is not None:
        _score_expected_fields(case, data, score)


def _score_prohibited(case: FixtureCase, text: str, score: CaseScore) -> None:
    low = _norm(text)
    for term in case.scoring.forbidden_terms:
        _check(score, f"forbidden_absent:{term}", _norm(term) not in low)
    _score_text_constraints(case, text, score)
    score.needs_manual_review = True  # indirect leaks are beyond string checks


def _score_dark_fiction(case: FixtureCase, text: str, score: CaseScore) -> None:
    refusal_markers = case.scoring.refusal_markers or list(DEFAULT_REFUSAL_MARKERS)
    moralizing_markers = case.scoring.moralizing_markers or list(DEFAULT_MORALIZING_MARKERS)
    low = _norm(text)
    hit_refusal = [m for m in refusal_markers if _norm(m) in low]
    tail = low[int(len(low) * 0.75) :]
    hit_moralizing = [m for m in moralizing_markers if _norm(m) in tail]
    score.metrics["refusal_markers_hit"] = hit_refusal
    score.metrics["moralizing_tail_markers_hit"] = hit_moralizing
    _check(score, "no_refusal_marker", not hit_refusal)
    _check(score, "no_moralizing_tail", not hit_moralizing)
    _score_text_constraints(case, text, score)
    score.needs_manual_review = True  # marker lists are heuristics, not judgments


def score_case(
    case: FixtureCase,
    *,
    text: str | None,
    data: dict[str, Any] | None,
    json_valid: bool | None,
    repair_attempts: int,
    latency_ms: int,
    error: str | None,
) -> CaseScore:
    """Dispatch scoring by category. A provider error fails the case outright."""
    score = CaseScore(case_id=case.id, category=case.category)
    score.metrics["latency_ms"] = latency_ms
    if error is not None:
        score.error = error
        score.failed_checks.append("provider_error")
        return score

    if case.category in {"structured_output", "scene_parsing", "repair_behavior"}:
        _score_structured(case, data, bool(json_valid), repair_attempts, score)
    elif case.category == "prohibited_reveal":
        _score_prohibited(case, text or "", score)
    elif case.category == "dark_fiction_refusal":
        _score_dark_fiction(case, text or "", score)
    elif case.category == "latency":
        _check(score, "completed", text is not None)
        _score_text_constraints(case, text or "", score)
    else:  # zht_instruction_following, canon_adherence
        _score_text_constraints(case, text or "", score)
        if case.category == "canon_adherence":
            score.needs_manual_review = True
    return score
