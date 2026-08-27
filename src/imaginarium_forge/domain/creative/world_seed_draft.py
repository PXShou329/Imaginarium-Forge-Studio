"""Project-optional, editable world seed drafts.

The aggregate is intentionally smaller than a World Bible.  It preserves an
author's early setting material without asserting Canon, contacting a text
provider, or requiring a Project.
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

_Identifier = Annotated[str, StringConstraints(max_length=200)]
_Short = Annotated[str, StringConstraints(max_length=200)]
_Medium = Annotated[str, StringConstraints(max_length=1000)]


class WorldSeedGenerationMode(StrEnum):
    """How the current editor content was initially populated."""

    MANUAL = "manual"
    FILL_BLANKS = "fill_blanks"
    REROLL_ALL = "reroll_all"


class WorldSeedDraft(BaseModel):
    """A durable worldbuilding seed that may remain outside every Project."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: _Identifier
    project_id: _Identifier | None = None
    title: _Short
    setting: _Medium = ""
    time_period: _Short = ""
    world_rules: tuple[_Medium, ...] = Field(default=(), max_length=24)
    locations: tuple[_Short, ...] = Field(default=(), max_length=24)
    social_context: _Medium = ""
    technology_or_magic: _Medium = ""
    central_conflict: _Medium = ""
    themes: tuple[_Short, ...] = Field(default=(), max_length=24)
    generation_mode: WorldSeedGenerationMode = WorldSeedGenerationMode.MANUAL
    generation_seed: _Identifier = ""
    status: RecordStatus = RecordStatus.ACTIVE
    created_at: str
    updated_at: str

    @field_validator("id", "title")
    @classmethod
    def _required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("世界草稿 ID 與標題不可留白")
        return normalized

    @field_validator("project_id", mode="before")
    @classmethod
    def _canonical_project_id(cls, value: object) -> object:
        if isinstance(value, str):
            normalized = value.strip()
            return normalized or None
        return value

    @field_validator(
        "setting",
        "time_period",
        "social_context",
        "technology_or_magic",
        "central_conflict",
        "generation_seed",
    )
    @classmethod
    def _trim_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("world_rules", "locations", "themes", mode="before")
    @classmethod
    def _canonical_lines(cls, value: object) -> tuple[str, ...]:
        if value is None:
            return ()
        if isinstance(value, str):
            raise ValueError("多項世界設定必須以字串清單提供")
        try:
            raw_values: tuple[object, ...] = tuple(value)  # type: ignore[arg-type]
        except TypeError as exc:
            raise ValueError("多項世界設定必須以字串清單提供") from exc
        result: list[str] = []
        seen: set[str] = set()
        for raw in raw_values:
            if not isinstance(raw, str):
                raise ValueError("世界設定清單的每一項都必須是文字")
            normalized = " ".join(raw.strip().split())
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            result.append(normalized)
        return tuple(result)

    @model_validator(mode="after")
    def _content_and_provenance_are_coherent(self) -> WorldSeedDraft:
        if not any(
            (
                self.setting,
                self.time_period,
                self.world_rules,
                self.locations,
                self.social_context,
                self.technology_or_magic,
                self.central_conflict,
                self.themes,
            )
        ):
            raise ValueError("世界草稿除了標題外，至少要有一項世界設定")
        if self.generation_mode is WorldSeedGenerationMode.MANUAL:
            if self.generation_seed:
                raise ValueError("手寫世界草稿不可攜帶隨機種子")
        elif not self.generation_seed:
            raise ValueError("隨機輔助世界草稿必須保留非空白種子")
        return self


__all__ = ["WorldSeedDraft", "WorldSeedGenerationMode"]
