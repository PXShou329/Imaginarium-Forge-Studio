"""Standalone, editable character-biography drafts.

These drafts intentionally do not require a project.  They are an author's
scratch-space artifact and only become project-bound when ``project_id`` is
explicitly supplied.
"""

from __future__ import annotations

import re
import unicodedata

from pydantic import BaseModel, ConfigDict, field_validator

from imaginarium_forge.domain.character.gender import CharacterGender
from imaginarium_forge.domain.common.enums import RecordStatus

_PROMPT_SEPARATOR = re.compile(r"[,;\r\n]+")


def normalize_english_image_prompt(value: str) -> str:
    """Return canonical comma-separated ASCII prompt text.

    NFKC converts full-width Latin characters and punctuation to their ASCII
    equivalents.  Commas, semicolons and line breaks are accepted as authoring
    separators, then persisted as ``, ``.  Non-ASCII content fails closed so a
    Chinese description can never be mislabeled as an English image prompt.
    """

    normalized = unicodedata.normalize("NFKC", value)
    if not normalized.isascii():
        raise ValueError("英文圖片提示詞只能包含 ASCII 字元")
    if any(
        (ord(character) < 32 and not character.isspace())
        or ord(character) == 127
        for character in normalized
    ):
        raise ValueError("英文圖片提示詞不可包含控制字元")
    tokens = [
        " ".join(token.strip().split())
        for token in _PROMPT_SEPARATOR.split(normalized)
    ]
    return ", ".join(token for token in tokens if token)


class CharacterBiographyDraft(BaseModel):
    """A durable biography draft that may exist outside every project."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    project_id: str | None = None
    title: str
    character_name: str
    gender: CharacterGender
    character_details: str = ""
    character_image_prompt_en: str = ""
    personal_story: str = ""
    background_image_prompt_en: str = ""
    notes: str = ""
    status: RecordStatus = RecordStatus.ACTIVE
    created_at: str
    updated_at: str

    @field_validator("title", "character_name")
    @classmethod
    def _required_label(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("標題與角色名稱不可留白")
        return normalized

    @field_validator(
        "character_image_prompt_en",
        "background_image_prompt_en",
        mode="before",
    )
    @classmethod
    def _canonical_prompt(cls, value: object) -> str:
        if not isinstance(value, str):
            raise ValueError("英文圖片提示詞必須是文字")
        return normalize_english_image_prompt(value)


__all__ = ["CharacterBiographyDraft", "normalize_english_image_prompt"]
