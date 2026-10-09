"""Replaceable, non-persistent candidate schemas for the four workflow editors."""

from __future__ import annotations

import re
import unicodedata
from typing import Annotated

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
)

from imaginarium_forge.domain.character.biography_draft import (
    normalize_english_image_prompt,
)

_Title = Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)]
_Short = Annotated[str, StringConstraints(strip_whitespace=True, max_length=1_000)]
_RequiredTitle = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=200),
]
_RequiredShort = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=1_000),
]
_Medium = Annotated[str, StringConstraints(strip_whitespace=True, max_length=4_000)]
_Long = Annotated[str, StringConstraints(strip_whitespace=True, max_length=100_000)]
_CharacterDetails = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=4_000),
]
_CharacterStory = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=12_000),
]
_Tag = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
_AI_IMAGE_PROMPT_MAX_LENGTH = 600
_RequiredImagePrompt = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=_AI_IMAGE_PROMPT_MAX_LENGTH,
    ),
]
_EmptyImagePrompt = Annotated[
    str,
    StringConstraints(strip_whitespace=True, max_length=0),
]
_MODEL_PROMPT_SEPARATOR = re.compile(r"[,;\r\n]+")


def _sanitize_generated_image_prompt(value: object) -> object:
    """Keep complete printable-ASCII tags from a model-generated prompt.

    Local constrained decoders can end a max-length string on a partial
    multibyte token. Dropping only the affected tag preserves the completed
    candidate while the public author-input schema remains strictly fail closed.
    """

    if not isinstance(value, str):
        return value
    normalized = unicodedata.normalize("NFKC", value)
    raw_tags = _MODEL_PROMPT_SEPARATOR.split(normalized)
    if len(normalized) >= _AI_IMAGE_PROMPT_MAX_LENGTH and raw_tags:
        # A decoder that lands exactly on maxLength may leave the final tag as
        # a partial word even though its JSON envelope is complete.
        raw_tags = raw_tags[:-1]
    tags: list[str] = []
    for raw_tag in raw_tags:
        tag = " ".join(raw_tag.strip().split())
        if not tag or not tag.isascii():
            continue
        if any(ord(character) < 32 or ord(character) == 127 for character in tag):
            continue
        tags.append(tag)
    return ", ".join(tags)


class _CandidateBase(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class StoryFragmentContinuationCandidate(_CandidateBase):
    """Exact new suffix; unlike full rewrites its boundary whitespace is authored data."""

    title: _Title
    fragment_text: str = Field(min_length=1, max_length=100_000)
    context_notes: _Medium = ""
    tags: tuple[_Tag, ...] = Field(default=(), max_length=24)

    @field_validator("fragment_text")
    @classmethod
    def _nonblank_exact_suffix(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("continuation suffix must contain prose")
        return value


class StoryFragmentCandidate(_CandidateBase):
    title: _Title
    fragment_text: _Long
    context_notes: _Medium = ""
    tags: tuple[_Tag, ...] = Field(default=(), max_length=24)


class WorldSeedCandidate(_CandidateBase):
    title: _Title
    setting: _Short = ""
    time_period: _Title = ""
    world_rules: tuple[_Short, ...] = Field(default=(), max_length=40)
    locations: tuple[_Short, ...] = Field(default=(), max_length=40)
    social_context: _Short = ""
    technology_or_magic: _Short = ""
    central_conflict: _Short = ""
    themes: tuple[_Short, ...] = Field(default=(), max_length=24)


class WorldSeedGenerationCandidate(WorldSeedCandidate):
    """Complete model-facing world candidate that cannot erase author fields."""

    title: _RequiredTitle
    setting: _RequiredShort
    time_period: _RequiredTitle
    world_rules: tuple[_RequiredShort, ...] = Field(min_length=1, max_length=40)
    locations: tuple[_RequiredShort, ...] = Field(min_length=1, max_length=40)
    social_context: _RequiredShort
    technology_or_magic: _RequiredShort
    central_conflict: _RequiredShort
    themes: tuple[_RequiredShort, ...] = Field(min_length=1, max_length=24)


class PromptScratchCandidate(_CandidateBase):
    character_image_prompt_en: str = Field(default="", max_length=8_000)
    background_image_prompt_en: str = Field(default="", max_length=8_000)

    @field_validator("character_image_prompt_en", "background_image_prompt_en")
    @classmethod
    def _english_prompt(cls, value: str) -> str:
        return normalize_english_image_prompt(value)


class _PromptScratchGenerationCandidate(PromptScratchCandidate):
    @field_validator(
        "character_image_prompt_en",
        "background_image_prompt_en",
        mode="before",
    )
    @classmethod
    def _sanitize_model_prompt(cls, value: object) -> object:
        return _sanitize_generated_image_prompt(value)


class PromptScratchCharacterGenerationCandidate(_PromptScratchGenerationCandidate):
    """Bounded model output for a character-only Prompt Scratch request."""

    character_image_prompt_en: _RequiredImagePrompt
    background_image_prompt_en: _EmptyImagePrompt


class PromptScratchBackgroundGenerationCandidate(_PromptScratchGenerationCandidate):
    """Bounded model output for a background-only Prompt Scratch request."""

    character_image_prompt_en: _EmptyImagePrompt
    background_image_prompt_en: _RequiredImagePrompt


class PromptScratchBothGenerationCandidate(_PromptScratchGenerationCandidate):
    """Bounded model output for a two-prompt Prompt Scratch request."""

    character_image_prompt_en: _RequiredImagePrompt
    background_image_prompt_en: _RequiredImagePrompt


class CharacterDraftCandidate(_CandidateBase):
    title: _Title
    character_name: _Title
    character_details: _Long = ""
    character_image_prompt_en: str = Field(default="", max_length=8_000)
    personal_story: _Long = ""
    background_image_prompt_en: str = Field(default="", max_length=8_000)

    @field_validator("character_image_prompt_en", "background_image_prompt_en")
    @classmethod
    def _english_prompt(cls, value: str) -> str:
        return normalize_english_image_prompt(value)


class CharacterDraftGenerationCandidate(CharacterDraftCandidate):
    """Model-facing character schema with complete, bounded prompt fields.

    Durable editors may hold longer manually authored image prompts. The local
    model schema is intentionally narrower so Ollama cannot spend the entire
    output budget filling one optional 8,000-character prompt and leave the JSON
    envelope incomplete.
    """

    title: _RequiredTitle
    character_name: _RequiredTitle
    character_details: _CharacterDetails
    character_image_prompt_en: _RequiredImagePrompt
    personal_story: _CharacterStory
    background_image_prompt_en: _RequiredImagePrompt

    @field_validator(
        "character_image_prompt_en",
        "background_image_prompt_en",
        mode="before",
    )
    @classmethod
    def _sanitize_model_prompt(cls, value: object) -> object:
        return _sanitize_generated_image_prompt(value)


__all__ = [
    "CharacterDraftCandidate",
    "CharacterDraftGenerationCandidate",
    "PromptScratchBackgroundGenerationCandidate",
    "PromptScratchBothGenerationCandidate",
    "PromptScratchCandidate",
    "PromptScratchCharacterGenerationCandidate",
    "StoryFragmentCandidate",
    "WorldSeedCandidate",
    "WorldSeedGenerationCandidate",
]
