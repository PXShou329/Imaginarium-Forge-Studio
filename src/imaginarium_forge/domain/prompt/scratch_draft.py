"""Project-optional image-prompt scratch drafts.

This artifact is deliberately smaller than a character biography or a prompt
project.  It gives an author somewhere durable to keep one character-image
prompt, one background-image prompt, or both, before deciding whether the idea
belongs to a book.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from imaginarium_forge.domain.character.biography_draft import (
    normalize_english_image_prompt,
)
from imaginarium_forge.domain.character.gender import CharacterGender
from imaginarium_forge.domain.common.enums import RecordStatus


class PromptScratchEditorKind(StrEnum):
    """The editor shape explicitly selected by the author."""

    CHARACTER = "character"
    BACKGROUND = "background"
    BOTH = "both"


class PromptScratchDraft(BaseModel):
    """A mutable prompt note with no required project or character identity."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    project_id: str | None = None
    title: str = ""
    character_name: str = ""
    character_image_prompt_en: str = ""
    background_image_prompt_en: str = ""
    notes: str = ""
    editor_kind: PromptScratchEditorKind = PromptScratchEditorKind.CHARACTER
    editor_gender: CharacterGender = CharacterGender.FEMALE
    status: RecordStatus = RecordStatus.ACTIVE
    created_at: str
    updated_at: str

    @field_validator("title", "character_name", "notes")
    @classmethod
    def _trim_free_text(cls, value: str) -> str:
        return value.strip()

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

    @model_validator(mode="after")
    def _has_something_to_keep(self) -> PromptScratchDraft:
        if not any(
            (
                self.title,
                self.character_name,
                self.character_image_prompt_en,
                self.background_image_prompt_en,
                self.notes,
            )
        ):
            raise ValueError("提示詞草稿至少要有一項內容")
        return self


__all__ = ["PromptScratchDraft", "PromptScratchEditorKind"]
