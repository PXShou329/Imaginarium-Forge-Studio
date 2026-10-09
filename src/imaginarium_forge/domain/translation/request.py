"""Validated input for an entirely local translation execution."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

from imaginarium_forge.domain.translation.direction import TranslationDirection
from imaginarium_forge.domain.translation.protected_terms import ProtectedTerm

MAX_TRANSLATION_SOURCE_CHARS = 250_000
DEFAULT_TRANSLATION_SEGMENT_SIZE = 1_800


class OfflineTranslationRequest(BaseModel):
    """Content-only request with no creative-provider or credential fields."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    direction: TranslationDirection
    source_text: str = Field(
        min_length=1,
        max_length=MAX_TRANSLATION_SOURCE_CHARS,
        repr=False,
    )
    protected_terms: tuple[ProtectedTerm, ...] = Field(default=(), repr=False)
    preserve_markdown: bool = True
    segment_size: int | None = Field(default=None, ge=256, le=8_000)

    @field_validator("source_text")
    @classmethod
    def _source_must_contain_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("source_text must not be blank")
        if "\x00" in value:
            raise ValueError("source_text must not contain NUL")
        return value

    @field_validator("protected_terms")
    @classmethod
    def _terms_must_be_unambiguous(
        cls,
        value: tuple[ProtectedTerm, ...],
    ) -> tuple[ProtectedTerm, ...]:
        zh_seen: set[str] = set()
        en_seen: set[str] = set()
        for term in value:
            zh_key = term.zh_tw.casefold()
            en_key = term.en.casefold()
            if zh_key in zh_seen or en_key in en_seen:
                raise ValueError("protected term sources must be unique per language")
            zh_seen.add(zh_key)
            en_seen.add(en_key)
        return value

    @property
    def effective_segment_size(self) -> int:
        return self.segment_size or DEFAULT_TRANSLATION_SEGMENT_SIZE


__all__ = [
    "DEFAULT_TRANSLATION_SEGMENT_SIZE",
    "MAX_TRANSLATION_SOURCE_CHARS",
    "OfflineTranslationRequest",
]
