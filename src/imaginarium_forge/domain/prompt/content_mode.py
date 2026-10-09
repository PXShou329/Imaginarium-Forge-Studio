"""Supported narrative tones for general-audience creative work."""

from enum import StrEnum


class ContentMode(StrEnum):
    GENERAL = "general"
    DRAMA = "drama"
    DARK = "dark"
    HORROR = "horror"
    VIOLENT = "violent"
