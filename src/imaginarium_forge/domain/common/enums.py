"""Explicit domain enums (execution spec §8.1). String-valued for canonical JSON."""

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
    SUGGESTIVE = "suggestive"
    EXPLICIT = "explicit"


class RequestType(StrEnum):
    STORY_GENERATION = "story_generation"
    IMAGE_PROMPT_COMPILE = "image_prompt_compile"
    VIDEO_PROMPT_COMPILE = "video_prompt_compile"


class EligibilityReason(StrEnum):
    ALLOWED = "allowed"
    NOT_REQUIRED = "eligibility_not_required"
    CONTRADICTORY_AGE_STATUS = "contradictory_age_status"
    MISSING_EXPLICIT_ADULT_AGE = "missing_explicit_adult_age"
    EXPLICIT_ADULT_AGE_UNCONFIRMED = "explicit_adult_age_unconfirmed"
    BELOW_ADULT_AGE_FLOOR = "below_adult_age_floor"
    EXISTING_CHARACTER_MINOR = "existing_character_minor"
    EXISTING_CHARACTER_AGE_UNKNOWN = "existing_character_age_unknown"
    EXISTING_CHARACTER_AGE_DISPUTED = "existing_character_age_disputed"
    EXISTING_CHARACTER_ADULT_STATUS_UNVERIFIED = "existing_character_adult_status_unverified"
    MINOR_ERA_VERSION_SELECTED = "minor_era_version_selected"
    CHILDLIKE_PRESENTATION_CONFLICT = "childlike_presentation_conflict"
    INVALID_SCENE_AGE_OVERRIDE = "invalid_scene_age_override"
    ORIGINALITY_REVIEW_REQUIRED = "originality_review_required"
    INVALID_CHARACTER_VERSION = "invalid_character_version"
    POLICY_PROFILE_MISSING = "policy_profile_missing"
