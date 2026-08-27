"""Read-only projection for project-free authoring drafts.

The inbox deliberately projects existing typed aggregates instead of creating
a second persistence model.  Source records remain owned by their original
services and editors; this service only maps, filters, sorts, and pages labels.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from math import ceil
from typing import Protocol

from sqlalchemy.exc import SQLAlchemyError

from imaginarium_forge.application.errors import (
    ApplicationError,
    ValidationFailedError,
)
from imaginarium_forge.domain.character.biography_draft import (
    CharacterBiographyDraft,
)
from imaginarium_forge.domain.creative.world_seed_draft import WorldSeedDraft
from imaginarium_forge.domain.prompt.scratch_draft import PromptScratchDraft
from imaginarium_forge.domain.story.fragment_draft import StoryFragmentDraft


class FreeCreationItemKind(StrEnum):
    CHARACTER = "character"
    PROMPT = "prompt"
    STORY_FRAGMENT = "story_fragment"
    WORLD = "world"


class FreeCreationSource(StrEnum):
    CHARACTER_DRAFTS = "character_drafts"
    PROMPT_DRAFTS = "prompt_drafts"
    STORY_FRAGMENT_DRAFTS = "story_fragment_drafts"
    WORLD_DRAFTS = "world_drafts"


@dataclass(frozen=True, slots=True)
class FreeCreationInboxItem:
    """A small navigation label; author body text is intentionally excluded."""

    kind: FreeCreationItemKind
    aggregate_id: str
    title: str
    subtitle: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class FreeCreationSourceIssue:
    source: FreeCreationSource
    message: str


@dataclass(frozen=True, slots=True)
class FreeCreationInboxPage:
    items: tuple[FreeCreationInboxItem, ...]
    total: int
    page: int
    page_size: int
    page_count: int
    character_count: int
    prompt_count: int
    story_fragment_count: int
    world_count: int
    issues: tuple[FreeCreationSourceIssue, ...] = ()


class _CharacterDraftReader(Protocol):
    def list_drafts(
        self,
        *,
        project_id: str | None = None,
        standalone_only: bool = False,
        include_archived: bool = False,
    ) -> list[CharacterBiographyDraft]: ...


class _PromptDraftReader(Protocol):
    def list_drafts(
        self,
        *,
        project_id: str | None = None,
        standalone_only: bool = False,
        include_archived: bool = False,
    ) -> list[PromptScratchDraft]: ...


class _WorldDraftReader(Protocol):
    def list_drafts(
        self,
        *,
        project_id: str | None = None,
        standalone_only: bool = False,
        include_archived: bool = False,
    ) -> list[WorldSeedDraft]: ...


class _StoryFragmentDraftReader(Protocol):
    def list_drafts(
        self,
        *,
        project_id: str | None = None,
        standalone_only: bool = False,
        include_archived: bool = False,
    ) -> list[StoryFragmentDraft]: ...


_PROMPT_KIND_LABELS = {
    "character": "角色圖 Prompt",
    "background": "背景圖 Prompt",
    "both": "角色圖＋背景圖 Prompt",
}
_STORY_FRAGMENT_KIND_LABELS = {
    "narrative": "敘事片段",
    "dialogue": "對白片段",
    "opening": "故事開場",
}


class FreeCreationInboxService:
    """Merge durable standalone drafts without mutating their source tables."""

    def __init__(
        self,
        *,
        character_drafts: _CharacterDraftReader,
        prompt_drafts: _PromptDraftReader,
        world_drafts: _WorldDraftReader,
        story_fragment_drafts: _StoryFragmentDraftReader,
    ) -> None:
        self._character_drafts = character_drafts
        self._prompt_drafts = prompt_drafts
        self._world_drafts = world_drafts
        self._story_fragment_drafts = story_fragment_drafts

    def list_page(
        self,
        *,
        query: str = "",
        kind: FreeCreationItemKind | str | None = None,
        page: int = 1,
        page_size: int = 12,
    ) -> FreeCreationInboxPage:
        if page < 1:
            raise ValidationFailedError("page 必須大於或等於 1")
        if not 1 <= page_size <= 100:
            raise ValidationFailedError("page_size 必須介於 1 與 100")
        if kind is None or kind == "":
            selected_kind = None
        elif isinstance(kind, FreeCreationItemKind):
            selected_kind = kind
        else:
            try:
                selected_kind = FreeCreationItemKind(kind)
            except ValueError as exc:
                raise ValidationFailedError(f"不支援的自由創作類型：{kind}") from exc

        character_items: list[FreeCreationInboxItem] = []
        prompt_items: list[FreeCreationInboxItem] = []
        story_fragment_items: list[FreeCreationInboxItem] = []
        world_items: list[FreeCreationInboxItem] = []
        issues: list[FreeCreationSourceIssue] = []
        try:
            character_items = [
                self._character_item(draft)
                for draft in self._character_drafts.list_drafts(
                    standalone_only=True,
                    include_archived=False,
                )
            ]
        except (ApplicationError, RuntimeError, SQLAlchemyError, TypeError, ValueError):
            issues.append(
                FreeCreationSourceIssue(
                    source=FreeCreationSource.CHARACTER_DRAFTS,
                    message="角色草稿清單暫時無法讀取；其他類型仍可使用。",
                )
            )
        try:
            prompt_items = [
                self._prompt_item(draft)
                for draft in self._prompt_drafts.list_drafts(
                    standalone_only=True,
                    include_archived=False,
                )
            ]
        except (ApplicationError, RuntimeError, SQLAlchemyError, TypeError, ValueError):
            issues.append(
                FreeCreationSourceIssue(
                    source=FreeCreationSource.PROMPT_DRAFTS,
                    message="圖片提示詞草稿清單暫時無法讀取；其他類型仍可使用。",
                )
            )

        try:
            world_items = [
                self._world_item(draft)
                for draft in self._world_drafts.list_drafts(
                    standalone_only=True,
                    include_archived=False,
                )
            ]
        except (ApplicationError, RuntimeError, SQLAlchemyError, TypeError, ValueError):
            issues.append(
                FreeCreationSourceIssue(
                    source=FreeCreationSource.WORLD_DRAFTS,
                    message="世界種子草稿清單暫時無法讀取；其他類型仍可使用。",
                )
            )

        try:
            story_fragment_items = [
                self._story_fragment_item(draft)
                for draft in self._story_fragment_drafts.list_drafts(
                    standalone_only=True,
                    include_archived=False,
                )
            ]
        except (ApplicationError, RuntimeError, SQLAlchemyError, TypeError, ValueError):
            issues.append(
                FreeCreationSourceIssue(
                    source=FreeCreationSource.STORY_FRAGMENT_DRAFTS,
                    message="故事片段草稿清單暫時無法讀取；其他類型仍可使用。",
                )
            )

        items = [
            *character_items,
            *prompt_items,
            *story_fragment_items,
            *world_items,
        ]
        if selected_kind is not None:
            items = [item for item in items if item.kind is selected_kind]
        normalized_query = query.strip().casefold()
        if normalized_query:
            items = [
                item
                for item in items
                if normalized_query in f"{item.title}\n{item.subtitle}".casefold()
            ]

        # Stable two-pass sorting gives updated_at DESC followed by kind/id ASC
        # for equal timestamps.  Source timestamps are canonical UTC ISO values.
        items.sort(key=lambda item: (item.kind.value, item.aggregate_id))
        items.sort(key=lambda item: item.updated_at, reverse=True)
        total = len(items)
        page_count = max(1, ceil(total / page_size))
        effective_page = min(page, page_count)
        start = (effective_page - 1) * page_size
        paged = tuple(items[start : start + page_size])
        return FreeCreationInboxPage(
            items=paged,
            total=total,
            page=effective_page,
            page_size=page_size,
            page_count=page_count,
            character_count=len(character_items),
            prompt_count=len(prompt_items),
            story_fragment_count=len(story_fragment_items),
            world_count=len(world_items),
            issues=tuple(issues),
        )

    @staticmethod
    def _character_item(draft: CharacterBiographyDraft) -> FreeCreationInboxItem:
        return FreeCreationInboxItem(
            kind=FreeCreationItemKind.CHARACTER,
            aggregate_id=draft.id,
            title=draft.title,
            subtitle=f"角色草稿 · {draft.character_name}",
            updated_at=draft.updated_at,
        )

    @staticmethod
    def _prompt_item(draft: PromptScratchDraft) -> FreeCreationInboxItem:
        title = draft.title or draft.character_name or "未命名圖片提示詞"
        kind_value = str(getattr(draft.editor_kind, "value", draft.editor_kind))
        detail = _PROMPT_KIND_LABELS.get(kind_value, "圖片 Prompt")
        if draft.character_name and draft.character_name != title:
            detail = f"{detail} · {draft.character_name}"
        return FreeCreationInboxItem(
            kind=FreeCreationItemKind.PROMPT,
            aggregate_id=draft.id,
            title=title,
            subtitle=detail,
            updated_at=draft.updated_at,
        )

    @staticmethod
    def _world_item(draft: WorldSeedDraft) -> FreeCreationInboxItem:
        detail = draft.setting or draft.time_period or "自由世界設定"
        return FreeCreationInboxItem(
            kind=FreeCreationItemKind.WORLD,
            aggregate_id=draft.id,
            title=draft.title,
            subtitle=f"世界種子 · {detail}",
            updated_at=draft.updated_at,
        )

    @staticmethod
    def _story_fragment_item(draft: StoryFragmentDraft) -> FreeCreationInboxItem:
        kind_value = str(getattr(draft.fragment_kind, "value", draft.fragment_kind))
        detail = _STORY_FRAGMENT_KIND_LABELS.get(kind_value, "故事片段")
        return FreeCreationInboxItem(
            kind=FreeCreationItemKind.STORY_FRAGMENT,
            aggregate_id=draft.id,
            title=draft.title,
            subtitle=f"故事片段 · {detail}",
            updated_at=draft.updated_at,
        )


__all__ = [
    "FreeCreationInboxItem",
    "FreeCreationInboxPage",
    "FreeCreationInboxService",
    "FreeCreationItemKind",
    "FreeCreationSource",
    "FreeCreationSourceIssue",
]
