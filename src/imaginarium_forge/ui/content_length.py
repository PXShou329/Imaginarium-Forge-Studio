"""Shared, author-facing text-length helpers.

The UI calls these values "字數" and makes the exact counting rule visible:
all non-whitespace Unicode characters count, including punctuation.  Keeping
the rule here prevents the Story Studio and standalone character notebook from
quietly judging the same text in two different ways.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

LengthState = Literal["unbounded", "below", "within", "above"]


def count_visible_characters(text: str) -> int:
    """Count Unicode characters after excluding whitespace."""

    return sum(1 for character in text if not character.isspace())


@dataclass(frozen=True, slots=True)
class LengthAssessment:
    """A display-ready comparison between one output and optional bounds."""

    actual: int
    minimum: int | None
    maximum: int | None
    state: LengthState

    @property
    def message(self) -> str:
        actual = f"實際 {self.actual:,} 字（不含空白）"
        if self.state == "unbounded":
            return f"{actual}；這次沒有設定長度限制。"
        if self.state == "below":
            assert self.minimum is not None
            return f"{actual}，少於設定的最少 {self.minimum:,} 字。"
        if self.state == "above":
            assert self.maximum is not None
            return f"{actual}，超過設定的最多 {self.maximum:,} 字。"
        if self.minimum is not None and self.maximum is not None:
            target = f"{self.minimum:,}–{self.maximum:,} 字"
        elif self.minimum is not None:
            target = f"至少 {self.minimum:,} 字"
        else:
            assert self.maximum is not None
            target = f"最多 {self.maximum:,} 字"
        return f"{actual}，符合設定（{target}）。"


def assess_text_length(
    text: str,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> LengthAssessment:
    """Compare ``text`` with inclusive optional bounds."""

    if minimum is not None and minimum < 1:
        raise ValueError("最少字數必須大於 0")
    if maximum is not None and maximum < 1:
        raise ValueError("最多字數必須大於 0")
    if minimum is not None and maximum is not None and minimum > maximum:
        raise ValueError("最少字數不可大於最多字數")

    actual = count_visible_characters(text)
    if minimum is not None and actual < minimum:
        state: LengthState = "below"
    elif maximum is not None and actual > maximum:
        state = "above"
    elif minimum is None and maximum is None:
        state = "unbounded"
    else:
        state = "within"
    return LengthAssessment(
        actual=actual,
        minimum=minimum,
        maximum=maximum,
        state=state,
    )


__all__ = [
    "LengthAssessment",
    "LengthState",
    "assess_text_length",
    "count_visible_characters",
]
