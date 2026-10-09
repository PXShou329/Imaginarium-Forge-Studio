"""Author-defined terminology that must round-trip without model invention."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ProtectedTerm(BaseModel):
    """One exact Traditional-Chinese/English terminology pair."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        str_strip_whitespace=True,
        hide_input_in_errors=True,
    )

    zh_tw: str = Field(min_length=1, max_length=500, repr=False)
    en: str = Field(min_length=1, max_length=500, repr=False)

    @field_validator("zh_tw", "en")
    @classmethod
    def _single_line_non_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("protected terms must not be blank")
        if "\n" in value or "\r" in value or "\x00" in value:
            raise ValueError("protected terms must be single-line text")
        return value.strip()


__all__ = ["ProtectedTerm"]
