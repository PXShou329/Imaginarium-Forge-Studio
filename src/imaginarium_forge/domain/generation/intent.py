"""Provider-neutral author intent for Creative Writing Engine 2.1."""

from __future__ import annotations

from enum import StrEnum


class GenerationIntent(StrEnum):
    """The author's requested kind of writing, independent of provider."""

    IDEA = "idea"
    PASSAGE = "passage"
    SCENE = "scene"
    SHORT_STORY = "short_story"
    CHAPTER = "chapter"
    LONG_STORY = "long_story"
    CHARACTER_BACKSTORY = "character_backstory"
    SCRIPT_SCENE = "script_scene"
    SCREENPLAY = "screenplay"


__all__ = ["GenerationIntent"]
