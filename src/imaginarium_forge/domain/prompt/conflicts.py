"""Conflict detection contracts (spec §27): 16 categories, no destructive auto-fix."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class ConflictCode(StrEnum):
    HAIR_COLOR = "hair_color"
    HAIR_STREAK = "hair_streak"
    EYE_COLOR = "eye_color"
    AGE_PRESENTATION = "age_presentation"
    DISTINGUISHING_FEATURE = "distinguishing_feature"
    OUTFIT = "outfit"
    LEFT_RIGHT_ORIENTATION = "left_right_orientation"
    FULL_BODY_VS_CROP = "full_body_vs_crop"
    DAY_NIGHT = "day_night"
    WEATHER = "weather"
    POSITIVE_NEGATIVE_OVERLAP = "positive_negative_overlap"
    INCOMPATIBLE_STYLE_TERMS = "incompatible_style_terms"
    CAMERA_FRAMING_CONTRADICTION = "camera_framing_contradiction"
    PROHIBITED_CANON_MUTATION = "prohibited_canon_mutation"
    ADULT_CONTENT_POLICY = "adult_content_policy"
    ADULT_CONTENT_REQUIRES_VERIFIED_CHARACTER = (
        "adult_content_requires_verified_character"
    )
    UNSUPPORTED_PROFILE_SYNTAX = "unsupported_profile_syntax"


class ConflictSeverity(StrEnum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class Conflict(BaseModel):
    model_config = ConfigDict(frozen=True)

    code: ConflictCode
    severity: ConflictSeverity
    source_a: str
    source_b: str
    message_zh_tw: str
    suggested_actions: tuple[str, ...] = ()
    auto_fix_safe: bool = False


#: Hard-Lock override decision options (spec §26.2) — the UI must offer exactly these.
HARD_LOCK_DECISIONS: tuple[str, ...] = ("preserve_canon", "create_new_version", "cancel_override")
