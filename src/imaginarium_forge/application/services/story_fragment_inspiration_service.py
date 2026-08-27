"""Pure, bounded offline inspiration for standalone story fragments.

This module performs no persistence, network access, provider call, or Canon
mutation.  Its output is only a proposed set of editable form values.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final

from pydantic import BaseModel, ConfigDict, Field, field_validator

from imaginarium_forge.application.services.creative_inspiration_service import (
    CreativeInspirationService,
)
from imaginarium_forge.domain.common.ids import new_id
from imaginarium_forge.domain.story.fragment_draft import (
    MAX_STORY_FRAGMENT_CHARS,
    MAX_STORY_FRAGMENT_TAGS,
    StoryFragmentGenerationMode,
    StoryFragmentKind,
    normalize_story_fragment_tags,
)

OFFLINE_STORY_FRAGMENT_CONTRACT_VERSION: Final = "story-fragment-offline-v1"


class StoryFragmentEditorField(StrEnum):
    """Editor fields the local inspiration helper is allowed to replace."""

    TITLE = "title"
    FRAGMENT_TEXT = "fragment_text"
    CONTEXT_NOTES = "context_notes"
    TAGS = "tags"


class StoryFragmentEditorContent(BaseModel):
    """Bounded values currently visible in the fragment editor."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    title: str = Field(default="", max_length=200)
    fragment_text: str = Field(default="", max_length=MAX_STORY_FRAGMENT_CHARS)
    context_notes: str = Field(default="", max_length=4_000)
    tags: tuple[str, ...] = Field(default=(), max_length=MAX_STORY_FRAGMENT_TAGS)

    @field_validator("tags", mode="before")
    @classmethod
    def _canonical_tags(cls, value: object) -> tuple[str, ...]:
        return normalize_story_fragment_tags(value)


class StoryFragmentInspiration(BaseModel):
    """One deterministic, editable local story-fragment suggestion."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    fragment_kind: StoryFragmentKind
    content: StoryFragmentEditorContent
    contract_version: str = OFFLINE_STORY_FRAGMENT_CONTRACT_VERSION


class StoryFragmentEditorPreparation(BaseModel):
    """Result of applying local inspiration to one editor snapshot."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    content: StoryFragmentEditorContent
    generation_mode: StoryFragmentGenerationMode
    generation_seed: str = Field(min_length=1, max_length=200)
    changed_fields: tuple[StoryFragmentEditorField, ...] = Field(min_length=1)
    contract_version: str = OFFLINE_STORY_FRAGMENT_CONTRACT_VERSION


_KIND_LABELS: Final = {
    StoryFragmentKind.NARRATIVE: "敘事片段",
    StoryFragmentKind.DIALOGUE: "對白片段",
    StoryFragmentKind.OPENING: "故事開場",
}


class StoryFragmentInspirationService:
    """Compose general-audience fragment scaffolds from curated local banks."""

    @staticmethod
    def generate(
        *,
        fragment_kind: StoryFragmentKind | str,
        seed: int | str,
    ) -> StoryFragmentInspiration:
        kind = StoryFragmentKind(fragment_kind)
        source = CreativeInspirationService.story(seed=seed)
        setting = source.setting or "一座規則正在鬆動的城市"
        conflict = source.central_conflict or "主角必須在兩個代價之間做出選擇"
        direction = source.direction or "一次微小的異常迫使角色採取行動"
        title = f"{source.title_suggestion}・{_KIND_LABELS[kind]}"

        if kind is StoryFragmentKind.DIALOGUE:
            fragment_text = (
                f"「你也看見了，對吧？」\n\n"
                f"窗外的{setting}安靜得不自然。對面的人沒有立刻回答，只把那份不該存在的"
                "紀錄推過桌面。\n\n"
                f"「一旦承認它是真的，我們就得面對另一件事。」對方說。\n\n"
                f"「{conflict}。」\n\n"
                "話音落下時，遠處傳來第二次警報。這一次，兩人都沒有假裝沒聽見。"
            )
        elif kind is StoryFragmentKind.OPENING:
            fragment_text = (
                f"{setting}從不在清晨響起警報。\n\n"
                "第一聲穿過街道時，主角正把一封沒有寄件人的信塞進外套。信封內只有一張"
                "舊照片，照片背面卻寫著今天才會發生的事。\n\n"
                f"人群開始往安全區移動，主角卻逆著他們前進。因為照片指向的不是災難現場，"
                f"而是{conflict}的第一個證據。"
            )
        else:
            fragment_text = (
                f"暮色沿著{setting}的屋脊緩慢下降。主角在封鎖線外停下，掌心那枚陌生的"
                "鑰匙仍帶著另一個人的體溫。\n\n"
                "門後沒有等待中的同伴，只有一排被刻意翻到空白頁的紀錄冊。最末一本夾著"
                "新的便條，上面是主角自己的筆跡，日期卻來自三天以後。\n\n"
                f"主角終於明白，眼前的問題不是誰留下了訊息，而是{conflict}。"
            )

        context_notes = f"舞台：{setting}\n核心衝突：{conflict}\n可延伸方向：{direction}"
        tags = tuple(
            dict.fromkeys(
                (
                    _KIND_LABELS[kind],
                    *source.genre_tags[:3],
                    *(source.themes[:2]),
                )
            )
        )
        return StoryFragmentInspiration(
            fragment_kind=kind,
            content=StoryFragmentEditorContent(
                title=title,
                fragment_text=fragment_text,
                context_notes=context_notes,
                tags=tags,
            ),
        )

    @classmethod
    def prepare_editor(
        cls,
        *,
        current: StoryFragmentEditorContent,
        fragment_kind: StoryFragmentKind | str,
        mode: StoryFragmentGenerationMode | str,
        seed: int | str | None = None,
    ) -> StoryFragmentEditorPreparation | None:
        """Apply fill-blanks or reroll-all without saving the returned values."""

        selected_mode = StoryFragmentGenerationMode(mode)
        if selected_mode is StoryFragmentGenerationMode.MANUAL:
            raise ValueError("本機靈感只能使用只補空白或全部重抽模式")
        effective_seed = str(seed if seed is not None else new_id()).strip()
        if not effective_seed:
            raise ValueError("本機靈感種子不可留白")
        if len(effective_seed) > 200:
            raise ValueError("本機靈感種子不可超過 200 字元")
        suggestion = cls.generate(
            fragment_kind=fragment_kind,
            seed=effective_seed,
        ).content
        current_values = current.model_dump(mode="python")
        suggested_values = suggestion.model_dump(mode="python")
        changed_fields: list[StoryFragmentEditorField] = []
        for field in StoryFragmentEditorField:
            current_value = current_values[field.value]
            is_blank = (
                not current_value
                if field is StoryFragmentEditorField.TAGS
                else not str(current_value).strip()
            )
            if selected_mode is StoryFragmentGenerationMode.REROLL_ALL or is_blank:
                current_values[field.value] = suggested_values[field.value]
                changed_fields.append(field)
        if not changed_fields:
            return None
        return StoryFragmentEditorPreparation(
            content=StoryFragmentEditorContent.model_validate(current_values),
            generation_mode=selected_mode,
            generation_seed=effective_seed,
            changed_fields=tuple(changed_fields),
        )


__all__ = [
    "OFFLINE_STORY_FRAGMENT_CONTRACT_VERSION",
    "StoryFragmentEditorContent",
    "StoryFragmentEditorField",
    "StoryFragmentEditorPreparation",
    "StoryFragmentInspiration",
    "StoryFragmentInspirationService",
]
