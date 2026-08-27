"""Project-optional, editable standalone story fragments.

Fragments are author scratch-space, not Story Studio Scene drafts.  Their
text may be edited in place until the author explicitly chooses a future
promotion workflow.  Saving a fragment never creates Canon, an accepted
story draft, or a stable story-block projection.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from imaginarium_forge.domain.common.enums import RecordStatus

MAX_STORY_FRAGMENT_CHARS = 500_000
MAX_STORY_FRAGMENT_TAGS = 24

_Identifier = Annotated[str, StringConstraints(max_length=200)]
_Short = Annotated[str, StringConstraints(max_length=200)]
_Context = Annotated[str, StringConstraints(max_length=4_000)]


class StoryFragmentKind(StrEnum):
    """The author-facing shape of one standalone fragment."""

    NARRATIVE = "narrative"
    DIALOGUE = "dialogue"
    OPENING = "opening"


class StoryFragmentGenerationMode(StrEnum):
    """How local inspiration was applied before the fragment was saved."""

    MANUAL = "manual"
    FILL_BLANKS = "fill_blanks"
    REROLL_ALL = "reroll_all"


def normalize_story_fragment_tags(value: object) -> tuple[str, ...]:
    """Return the one canonical representation accepted for fragment tags."""

    if value is None:
        return ()
    if isinstance(value, str):
        raise ValueError("故事片段標籤必須以字串清單提供")
    try:
        raw_values: tuple[object, ...] = tuple(value)  # type: ignore[arg-type]
    except TypeError as exc:
        raise ValueError("故事片段標籤必須以字串清單提供") from exc
    result: list[str] = []
    seen: set[str] = set()
    for raw in raw_values:
        if not isinstance(raw, str):
            raise ValueError("故事片段標籤的每一項都必須是文字")
        normalized = " ".join(raw.strip().split())
        if not normalized or normalized in seen:
            continue
        if len(normalized) > 200:
            raise ValueError("故事片段標籤不可超過 200 個字元")
        seen.add(normalized)
        result.append(normalized)
    return tuple(result)


class StoryFragmentDraft(BaseModel):
    """A durable story fragment that may remain outside every Project."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: _Identifier
    project_id: _Identifier | None = None
    title: _Short
    fragment_kind: StoryFragmentKind = StoryFragmentKind.NARRATIVE
    fragment_text: str = Field(max_length=MAX_STORY_FRAGMENT_CHARS)
    context_notes: _Context = ""
    tags: tuple[_Short, ...] = Field(default=(), max_length=MAX_STORY_FRAGMENT_TAGS)
    generation_mode: StoryFragmentGenerationMode = StoryFragmentGenerationMode.MANUAL
    generation_seed: _Identifier = ""
    status: RecordStatus = RecordStatus.ACTIVE
    created_at: str
    updated_at: str

    @field_validator("id", "title")
    @classmethod
    def _required_labels(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("故事片段 ID 與標題不可留白")
        return normalized

    @field_validator("project_id", mode="before")
    @classmethod
    def _canonical_project_id(cls, value: object) -> object:
        if isinstance(value, str):
            normalized = value.strip()
            return normalized or None
        return value

    @field_validator("fragment_text")
    @classmethod
    def _required_exact_fragment_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("故事片段正文不可留白")
        # Do not strip or normalize prose. Newlines and surrounding whitespace
        # are author-owned and must survive an exact persistence round-trip.
        return value

    @field_validator("context_notes", "generation_seed")
    @classmethod
    def _trim_metadata(cls, value: str) -> str:
        return value.strip()

    @field_validator("tags", mode="before")
    @classmethod
    def _canonical_tags(cls, value: object) -> tuple[str, ...]:
        return normalize_story_fragment_tags(value)

    @model_validator(mode="after")
    def _generation_provenance_is_coherent(self) -> StoryFragmentDraft:
        if self.generation_mode is StoryFragmentGenerationMode.MANUAL:
            if self.generation_seed:
                raise ValueError("手寫故事片段不可攜帶隨機種子")
        elif not self.generation_seed:
            raise ValueError("隨機輔助故事片段必須保留非空白種子")
        return self


__all__ = [
    "MAX_STORY_FRAGMENT_CHARS",
    "MAX_STORY_FRAGMENT_TAGS",
    "StoryFragmentDraft",
    "StoryFragmentGenerationMode",
    "StoryFragmentKind",
    "normalize_story_fragment_tags",
]
