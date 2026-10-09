"""Author-controlled keyword guidance for continuing a story scene."""

from __future__ import annotations

import re
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

_Keyword = Annotated[str, StringConstraints(min_length=1, max_length=120)]
_SEPARATOR = re.compile(r"[,，、;；\n\r]+")


class ContinuationGoal(StrEnum):
    FOLLOW_OUTLINE = "follow_outline"
    DEEPEN_CHARACTER = "deepen_character"
    ADVANCE_CONFLICT = "advance_conflict"
    CLOSE_CURRENT_SCENE = "close_current_scene"
    EXPAND_EXCERPT = "expand_excerpt"
    REWRITE_EXCERPT = "rewrite_excerpt"
    DIALOGUE_FOCUS = "dialogue_focus"
    DESCRIPTION_FOCUS = "description_focus"
    REPAIR_CONTINUITY = "repair_continuity"


_GOAL_INSTRUCTIONS = {
    ContinuationGoal.FOLLOW_OUTLINE: "依照目前已選定的大綱與 Scene Card 自然接續，不重置劇情。",
    ContinuationGoal.DEEPEN_CHARACTER: "在推進事件的同時深化角色動機、關係與內在衝突。",
    ContinuationGoal.ADVANCE_CONFLICT: "讓既有衝突產生可觀察的新後果，並推進到下一個故事節點。",
    ContinuationGoal.CLOSE_CURRENT_SCENE: "完成目前場景的戲劇任務，留下能銜接下一場景的明確狀態。",
    ContinuationGoal.EXPAND_EXCERPT: "以作者提供的參考段落為核心擴寫，保留原意與已成立事實。",
    ContinuationGoal.REWRITE_EXCERPT: "重寫作者提供的參考段落，保留事件功能但改善敘事與文字。",
    ContinuationGoal.DIALOGUE_FOCUS: "以角色互動與自然對話為主推進場景，避免只用說明交代事件。",
    ContinuationGoal.DESCRIPTION_FOCUS: "加強可感知的環境、動作與情緒描寫，同時維持故事推進。",
    ContinuationGoal.REPAIR_CONTINUITY: "修復與已接受設定、時間線及角色狀態的矛盾，不另造新設定。",
}


class StoryContinuationBrief(BaseModel):
    """A bounded brief composed into the existing orchestration instruction."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    keywords: tuple[_Keyword, ...] = Field(default=(), max_length=32)
    goal: ContinuationGoal = ContinuationGoal.FOLLOW_OUTLINE
    additional_instruction: str = Field(default="", max_length=4000)
    source_excerpt: str = Field(default="", max_length=8000)
    must_include: tuple[_Keyword, ...] = Field(default=(), max_length=32)
    must_avoid: tuple[_Keyword, ...] = Field(default=(), max_length=32)
    min_chars: int | None = Field(default=None, ge=100, le=12_000)
    max_chars: int | None = Field(default=None, ge=100, le=12_000)
    # Backward-compatible input for saved callers from before length ranges.
    target_words: int | None = Field(default=None, ge=100, le=5000)
    pacing: str = Field(default="", max_length=120)

    @field_validator("keywords", "must_include", "must_avoid")
    @classmethod
    def _normalize_keyword_tuple(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized = parse_story_keywords(values)
        if values != normalized:
            raise ValueError("續寫關鍵字必須已正規化且不得重複")
        return values

    @field_validator("additional_instruction")
    @classmethod
    def _strip_instruction(cls, value: str) -> str:
        return value.strip()

    @field_validator("source_excerpt", "pacing")
    @classmethod
    def _strip_optional_text(cls, value: str) -> str:
        return value.strip()

    @model_validator(mode="after")
    def _length_bounds_are_unambiguous(self) -> StoryContinuationBrief:
        if self.target_words is not None and (
            self.min_chars is not None or self.max_chars is not None
        ):
            raise ValueError("舊版目標字數不可與新版字數上下限同時使用")
        if (
            self.min_chars is not None
            and self.max_chars is not None
            and self.min_chars > self.max_chars
        ):
            raise ValueError("最少字數不可大於最多字數")
        return self

    def compose(self) -> str:
        """Compose stable text for ``GenerateSceneRequest.user_instruction``."""

        if (
            self.goal
            in {
                ContinuationGoal.EXPAND_EXCERPT,
                ContinuationGoal.REWRITE_EXCERPT,
            }
            and not self.source_excerpt
        ):
            raise ValueError("擴寫或重寫模式必須提供參考段落")

        parts = [_GOAL_INSTRUCTIONS[self.goal]]
        if self.keywords:
            parts.append(f"作者指定續寫關鍵字：{'、'.join(self.keywords)}。")
            parts.append("請自然融入其語意，不要把關鍵字機械排列或逐項解說。")
        if self.source_excerpt:
            parts.append(f"作者指定的參考段落：\n{self.source_excerpt}")
        if self.must_include:
            parts.append(f"本次必須包含：{'、'.join(self.must_include)}。")
        if self.must_avoid:
            parts.append(f"本次不得出現：{'、'.join(self.must_avoid)}。")
        if self.min_chars is not None and self.max_chars is not None:
            parts.append(
                f"本次正文請寫在 {self.min_chars} 至 {self.max_chars} 字之間；"
                "字數以不含空白的可見字元計算，並以完整場景與自然收尾為優先。"
            )
        elif self.min_chars is not None:
            parts.append(
                f"本次正文至少 {self.min_chars} 字；字數以不含空白的可見字元計算，"
                "請用有效情節、動作、感官與角色反應補足，不要重複灌水。"
            )
        elif self.max_chars is not None:
            parts.append(
                f"本次正文最多 {self.max_chars} 字；字數以不含空白的可見字元計算，"
                "仍須保留完整場景弧線與自然收尾。"
            )
        elif self.target_words is not None:
            parts.append(f"本次正文目標約 {self.target_words} 字；以完整場景為優先。")
        if self.pacing:
            parts.append(f"本次節奏：{self.pacing}。")
        if self.additional_instruction:
            parts.append(f"作者補充指示：{self.additional_instruction}")
        return "\n".join(parts)


def parse_story_keywords(values: tuple[str, ...] | list[str] | str) -> tuple[str, ...]:
    """Parse Chinese/English comma, semicolon and newline separated keywords."""

    raw_values = _SEPARATOR.split(values) if isinstance(values, str) else values
    result: list[str] = []
    seen: set[str] = set()
    for raw in raw_values:
        value = " ".join(raw.strip().split())
        if not value:
            continue
        if len(value) > 120:
            raise ValueError("單一續寫關鍵字不可超過 120 個字元")
        key = value.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(value)
    if len(result) > 32:
        raise ValueError("續寫關鍵字最多 32 個")
    return tuple(result)


def compose_story_continuation(
    keywords: tuple[str, ...] | list[str] | str,
    *,
    goal: ContinuationGoal = ContinuationGoal.FOLLOW_OUTLINE,
    additional_instruction: str = "",
    source_excerpt: str = "",
    must_include: tuple[str, ...] | list[str] | str = (),
    must_avoid: tuple[str, ...] | list[str] | str = (),
    min_chars: int | None = None,
    max_chars: int | None = None,
    target_words: int | None = None,
    pacing: str = "",
) -> str:
    """Convenience boundary used by Streamlit before orchestration."""

    return StoryContinuationBrief(
        keywords=parse_story_keywords(keywords),
        goal=goal,
        additional_instruction=additional_instruction,
        source_excerpt=source_excerpt,
        must_include=parse_story_keywords(must_include),
        must_avoid=parse_story_keywords(must_avoid),
        min_chars=min_chars,
        max_chars=max_chars,
        target_words=target_words,
        pacing=pacing,
    ).compose()
