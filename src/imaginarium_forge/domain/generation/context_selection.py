"""Typed, reproducible context-selection contracts.

Priority is intentionally explicit: a lower numeric value is more important.
Source identity is always the typed ``(source_type, source_id)`` pair; a bare
identifier is never sufficient for selection, reporting, or hashing.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from imaginarium_forge.canonical import canonical_json, sha256_of_canonical

SourceKey = tuple[str, str]

_SOURCE_COMPONENT_RE = re.compile(r"^[^\s\x00-\x1f\x7f]+$")


class ContextScope(StrEnum):
    """Author-visible selection scopes from the R2 architecture contract."""

    CURRENT_FIELD = "current_field"
    CURRENT_DRAFT = "current_draft"
    CURRENT_SCENE = "current_scene"
    CURRENT_CHAPTER = "current_chapter"
    PROJECT_BIBLE = "project_bible"
    CUSTOM = "custom"


class ContextCompactionPolicy(StrEnum):
    """Author-selectable, executable compaction behavior."""

    PERSISTED_SUMMARY_THEN_PRIORITY = "persisted_summary_then_priority"
    PRIORITY_ONLY = "priority_only"


class ContextSourceSpec(BaseModel):
    """One typed source requested by the author.

    Smaller ``priority`` values are compiled first and protected longer during
    optional compaction. ``max_visible_chars`` is an explicit per-source cap,
    not a hidden global slice.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    source_id: str
    source_type: str
    max_visible_chars: int = Field(gt=0)
    priority: int = Field(ge=0)
    required: bool = False

    @field_validator("source_id", "source_type")
    @classmethod
    def _stable_source_component(cls, value: str) -> str:
        normalized = value.strip()
        if (
            not normalized
            or len(normalized) > 200
            or not _SOURCE_COMPONENT_RE.fullmatch(normalized)
        ):
            raise ValueError("source key components must be stable non-blank identifiers")
        return normalized

    @property
    def source_key(self) -> SourceKey:
        return (self.source_type, self.source_id)


class ContextSelectionSpec(BaseModel):
    """Immutable author selection plus explicit compaction policy and budget."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    scope: ContextScope
    sources: tuple[ContextSourceSpec, ...]
    protected_facts: tuple[str, ...] = ()
    must_avoid: tuple[str, ...] = ()
    total_budget: int = Field(gt=0)
    compaction_policy: ContextCompactionPolicy

    @field_validator("protected_facts", "must_avoid", mode="before")
    @classmethod
    def _normalized_constraints(cls, value: object) -> object:
        if value is None:
            return ()
        if isinstance(value, str) or not isinstance(value, (list, tuple)):
            raise ValueError("context constraints must be an ordered sequence of text")
        normalized: list[str] = []
        seen: set[str] = set()
        for raw_item in value:
            if not isinstance(raw_item, str):
                raise ValueError("context constraints must contain only text")
            item = raw_item.strip()
            if not item:
                raise ValueError("context constraints must not contain blank entries")
            if len(item) > 20_000:
                raise ValueError("one context constraint exceeds 20,000 characters")
            if item in seen:
                raise ValueError("context constraints must not contain duplicate entries")
            seen.add(item)
            normalized.append(item)
        return tuple(normalized)

    @model_validator(mode="after")
    def _unique_source_keys(self) -> Self:
        keys = [source.source_key for source in self.sources]
        if len(keys) != len(set(keys)):
            raise ValueError("context source keys must be unique")
        return self

    def canonical_json(self) -> str:
        """Return the byte-stable R2 selection snapshot."""

        return canonical_json(self.model_dump(mode="json"))

    @property
    def sha256(self) -> str:
        """Hash the canonical selection snapshot for durable replay evidence."""

        return sha256_of_canonical(self.model_dump(mode="json"))


__all__ = [
    "ContextCompactionPolicy",
    "ContextScope",
    "ContextSelectionSpec",
    "ContextSourceSpec",
    "SourceKey",
]
