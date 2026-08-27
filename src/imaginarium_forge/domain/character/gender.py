"""Small, explicit character-gender vocabulary used by authoring workflows.

The product intentionally exposes only the two choices requested by the
author.  ``None`` remains available on legacy/imported characters whose gender
has never been confirmed; generation must not guess in that case.
"""

from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum


class CharacterGender(StrEnum):
    FEMALE = "female"
    MALE = "male"

    @property
    def prompt_token(self) -> str:
        return "adult woman" if self is CharacterGender.FEMALE else "adult man"

    @property
    def zh_label(self) -> str:
        return "女" if self is CharacterGender.FEMALE else "男"


_STANDARD_PROMPT_TOKENS = frozenset(
    gender.prompt_token.casefold() for gender in CharacterGender
)


def strip_gender_prompt_tokens(values: Iterable[str]) -> tuple[str, ...]:
    """Remove gender markers that are represented by the structured field."""

    return tuple(
        value.strip()
        for value in values
        if value.strip() and value.strip().casefold() not in _STANDARD_PROMPT_TOKENS
    )


def apply_gender_prompt_token(
    values: Iterable[str], gender: CharacterGender
) -> tuple[str, ...]:
    """Render exactly one selected gender token at the front of a prompt."""

    return (gender.prompt_token, *strip_gender_prompt_tokens(values))


__all__ = [
    "CharacterGender",
    "apply_gender_prompt_token",
    "strip_gender_prompt_tokens",
]
