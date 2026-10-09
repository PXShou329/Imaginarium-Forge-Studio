"""Explicit domain enums . String-valued for canonical JSON."""

from __future__ import annotations

from enum import StrEnum


class ProjectStatus(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class RecordStatus(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class CharacterOrigin(StrEnum):
    ORIGINAL = "original"
    EXISTING = "existing"


class AgeClassification(StrEnum):
    VERIFIED_ADULT = "verified_adult"
    VERIFIED_MINOR = "verified_minor"
    UNKNOWN = "unknown"
    DISPUTED = "disputed"


class CanonStrength(StrEnum):
    HARD_LOCK = "hard_lock"
    SOFT_CANON = "soft_canon"
    PREFERENCE = "preference"
    SCENE_OVERRIDE = "scene_override"


class ContentRating(StrEnum):
    GENERAL = "general"
    MATURE = "mature"


class ContentIntensity(StrEnum):
    GENERAL = "general"
    DARK = "dark"
    HORROR = "horror"
    VIOLENT = "violent"


class RequestType(StrEnum):
    STORY_GENERATION = "story_generation"
    IMAGE_PROMPT_COMPILE = "image_prompt_compile"
    VIDEO_PROMPT_COMPILE = "video_prompt_compile"


class EligibilityReason(StrEnum):
    ALLOWED = "allowed"
    INVALID_CHARACTER_VERSION = "invalid_character_version"
