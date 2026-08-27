"""Content modes and deterministic preflight (Gate A A-03).

The single `studio_adult` checkbox was the only adult trigger; a description
with obvious sexual intent could compile in General mode simply because the
user forgot to flip it. This module replaces the checkbox with an explicit
mode plus a CONSERVATIVE deterministic preflight.

Design rules (spec §6.2/§6.3):

- ``adult_content_requested`` is DERIVED, never stored independently:
  suggestive/explicit_adult → True; dark/horror/violent → False (non-sexual
  intensity must not trigger sexual-content eligibility);
- the preflight is a warning/confirmation layer over an intentionally small
  marker lexicon — it is NOT a semantic classifier, and absence of a hit is
  not clearance;
- an LLM may SUGGEST a mode; only this deterministic code + the Phase 1
  eligibility validator are policy authorities.
"""

from __future__ import annotations

import re
from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class ContentMode(StrEnum):
    GENERAL = "general"
    MATURE_NONSEXUAL = "mature_nonsexual"
    DARK = "dark"
    HORROR = "horror"
    VIOLENT = "violent"
    SUGGESTIVE = "suggestive"
    EXPLICIT_ADULT = "explicit_adult"


#: modes whose selection requires the Phase 1 adult-eligibility gate
ADULT_MODES: frozenset[ContentMode] = frozenset(
    {ContentMode.SUGGESTIVE, ContentMode.EXPLICIT_ADULT}
)


def derives_adult(mode: ContentMode) -> bool:
    return mode in ADULT_MODES


#: deliberately small, high-precision markers of sexual-adult intent.
#: Conservative by design: misses are expected; hits demand confirmation.
_ADULT_MARKERS: tuple[str, ...] = (
    "nsfw", "explicit", "nude", "naked", "sex", "erotic", "hentai",
    "裸體", "全裸", "性愛", "情色", "色情", "露出", "十八禁", "18禁", "r18", "r-18",
)
_MARKER_RE = re.compile(
    "|".join(
        rf"\b{re.escape(m)}\b" if m.isascii() else re.escape(m)
        for m in _ADULT_MARKERS
    ),
    re.IGNORECASE,
)


class ContentPreflightResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    confirmation_required: bool = False
    matched_markers: tuple[str, ...] = ()
    message_zh_tw: str = ""


def preflight_content_mode(
    mode: ContentMode,
    *texts: str,
) -> ContentPreflightResult:
    """Flag obvious adult-sexual markers when the selected mode is not adult.

    Returns ``confirmation_required=True`` with the matched markers; the UI
    must then demand an explicit mode change (or text edit) before compiling.
    A clean pass under a non-adult mode is NOT a policy clearance.
    """
    if mode in ADULT_MODES:
        return ContentPreflightResult()
    haystack = "\n".join(t for t in texts if t)
    hits = tuple(dict.fromkeys(m.group(0).lower() for m in _MARKER_RE.finditer(haystack)))
    if not hits:
        return ContentPreflightResult()
    return ContentPreflightResult(
        confirmation_required=True,
        matched_markers=hits,
        message_zh_tw=(
            "偵測到明顯成人內容標記（"
            + "、".join(hits[:4])
            + f"），但目前內容模式為「{mode.value}」。"
            "請改選 suggestive／explicit_adult 並通過角色資格驗證，或移除相關描述。"
        ),
    )
