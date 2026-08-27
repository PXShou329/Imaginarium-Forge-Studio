"""Prompt linter contracts (spec §31): 17 checks; error blocks compilation output
acceptance, warning/information do not."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class LintCheck(StrEnum):
    MISSING_SUBJECT = "missing_subject"
    MISSING_REQUIRED_HARD_LOCKS = "missing_required_hard_locks"
    IDENTITY_COVERAGE_BELOW_THRESHOLD = "identity_coverage_below_threshold"
    STYLE_COVERAGE_BELOW_THRESHOLD = "style_coverage_below_threshold"
    DUPLICATE_TAGS = "duplicate_tags"
    POSITIVE_NEGATIVE_OVERLAP = "positive_negative_overlap"
    LEFT_RIGHT_CONFLICT = "left_right_conflict"
    HAIR_CONFLICT = "hair_conflict"
    EYE_CONFLICT = "eye_conflict"
    DISTINGUISHING_FEATURE_CONFLICT = "distinguishing_feature_conflict"
    ADULT_PRESENTATION_CONFLICT = "adult_presentation_conflict"
    UNSUPPORTED_WEIGHTING_SYNTAX = "unsupported_weighting_syntax"
    MODEL_PROFILE_INCOMPATIBILITY = "model_profile_incompatibility"
    FULL_BODY_CROP_CONFLICT = "full_body_crop_conflict"
    DAY_NIGHT_CONFLICT = "day_night_conflict"
    WEATHER_CONFLICT = "weather_conflict"
    EXCESSIVE_QUALITY_TAGS = "excessive_quality_tags"
    EXCESSIVELY_LONG_PROMPT = "excessively_long_prompt"
    STYLE_CONTRADICTION = "style_contradiction"
    BACKGROUND_OVERWHELMS_CHARACTER = "background_overwhelms_character"
    UNRESOLVED_HIGH_SEVERITY_CONFLICT = "unresolved_high_severity_conflict"
    UNKNOWN_CHECKPOINT = "unknown_checkpoint"
    EXPERIMENTAL_PROFILE = "experimental_profile"


class LintLevel(StrEnum):
    ERROR = "error"
    WARNING = "warning"
    INFORMATION = "information"


class LintFinding(BaseModel):
    model_config = ConfigDict(frozen=True)

    check: LintCheck
    level: LintLevel
    message_zh_tw: str
    detail: str = ""


class LintReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    findings: tuple[LintFinding, ...] = ()

    @property
    def has_errors(self) -> bool:
        return any(f.level is LintLevel.ERROR for f in self.findings)

    def by_level(self, level: LintLevel) -> tuple[LintFinding, ...]:
        return tuple(f for f in self.findings if f.level is level)
