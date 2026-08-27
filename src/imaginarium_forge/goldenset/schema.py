"""Golden Set fixture schema and loader.

Layout:
  <root>/public/<category>/*.json    — committed, non-sensitive cases
  <root>/private/<category>/*.json   — local-only cases, NEVER committed
                                        (goldenset/private/README.md documents the rules)
The loader merges both, validates every case, and rejects duplicate ids.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

CATEGORIES: tuple[str, ...] = (
    "zht_instruction_following",
    "structured_output",
    "scene_parsing",
    "canon_adherence",
    "prohibited_reveal",
    "dark_fiction_refusal",
    "latency",
    "repair_behavior",
)

CategoryName = Literal[
    "zht_instruction_following",
    "structured_output",
    "scene_parsing",
    "canon_adherence",
    "prohibited_reveal",
    "dark_fiction_refusal",
    "latency",
    "repair_behavior",
]


class ScoringSpec(BaseModel):
    """Deterministic scoring parameters. Empty fields mean 'not checked'."""

    must_include: list[str] = Field(default_factory=list)
    must_not_include: list[str] = Field(default_factory=list)
    min_chars: int | None = None
    max_chars: int | None = None
    min_cjk_ratio: float | None = None
    # scene_parsing / structured_output field expectations.
    # scalar fields: expected value is a string OR a list of acceptable alternatives
    #                (substring match after casefold); integers compare by equality;
    #                null means "field must be empty/null (do not fabricate)".
    # list fields:   expected is a list of terms; each term must substring-match
    #                at least one produced element (subset semantics).
    expected_fields: dict[str, Any] = Field(default_factory=dict)
    forbidden_terms: list[str] = Field(default_factory=list)
    refusal_markers: list[str] = Field(default_factory=list)
    moralizing_markers: list[str] = Field(default_factory=list)


class FixtureCase(BaseModel):
    """One benchmark case."""

    id: str
    category: CategoryName
    description: str = ""
    system: str | None = None
    prompt: str
    schema_name: str | None = None
    scoring: ScoringSpec = Field(default_factory=ScoringSpec)
    notes: str | None = None
    source: Literal["public", "private"] = "public"


class GoldensetError(Exception):
    """Raised for invalid fixtures or duplicate case ids."""


def _load_dir(directory: Path, source: Literal["public", "private"]) -> list[FixtureCase]:
    cases: list[FixtureCase] = []
    if not directory.is_dir():
        return cases
    for path in sorted(directory.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise GoldensetError(f"invalid JSON in fixture {path}: {exc}") from exc
        try:
            case = FixtureCase.model_validate(payload)
        except Exception as exc:  # pydantic.ValidationError
            raise GoldensetError(f"invalid fixture {path}: {exc}") from exc
        cases.append(case.model_copy(update={"source": source}))
    return cases


def load_cases(
    root: Path,
    categories: Sequence[str] | None = None,
    include_private: bool = True,
) -> list[FixtureCase]:
    """Load and validate fixtures for the given categories (all when None)."""
    wanted = tuple(categories) if categories else CATEGORIES
    unknown = [c for c in wanted if c not in CATEGORIES]
    if unknown:
        raise GoldensetError(f"unknown categories: {unknown}")
    cases: list[FixtureCase] = []
    for category in wanted:
        cases.extend(_load_dir(root / "public" / category, "public"))
        if include_private:
            cases.extend(_load_dir(root / "private" / category, "private"))
    seen: dict[str, str] = {}
    for case in cases:
        if case.id in seen:
            raise GoldensetError(
                f"duplicate case id '{case.id}' ({seen[case.id]} vs {case.category})"
            )
        seen[case.id] = case.category
    return cases
