"""Supported language directions for the first offline translation release."""

from __future__ import annotations

from enum import StrEnum


class TranslationDirection(StrEnum):
    """An explicit direction; automatic language detection is intentionally absent."""

    ZH_TW_TO_EN = "zh_tw_to_en"
    EN_TO_ZH_TW = "en_to_zh_tw"

    @property
    def source_language(self) -> str:
        return "zh-TW" if self is self.ZH_TW_TO_EN else "en"

    @property
    def target_language(self) -> str:
        return "en" if self is self.ZH_TW_TO_EN else "zh-TW"


__all__ = ["TranslationDirection"]
