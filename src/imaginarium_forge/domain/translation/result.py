"""Safe result, progress, and QA warning types for offline translation."""

from __future__ import annotations

from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from imaginarium_forge.domain.translation.direction import TranslationDirection


class TranslationWarningSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"


class TranslationWarningCode(StrEnum):
    EMPTY_SEGMENT = "empty_segment"
    PROTECTED_TOKEN_MISSING = "protected_token_missing"
    PROTECTED_TOKEN_DUPLICATED = "protected_token_duplicated"
    PROTECTED_TOKEN_REMAINING = "protected_token_remaining"
    NUMBER_CHANGED = "number_changed"
    URL_CHANGED = "url_changed"
    EMAIL_CHANGED = "email_changed"
    PATH_CHANGED = "path_changed"
    CODE_CHANGED = "code_changed"
    LAYOUT_CHANGED = "layout_changed"
    SOURCE_LEAKAGE = "source_leakage"
    SIMPLIFIED_CHINESE_DRIFT = "simplified_chinese_drift"
    REPEATED_PARAGRAPH = "repeated_paragraph"


class TranslationWarning(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    code: TranslationWarningCode
    message: str = Field(min_length=1, max_length=500)
    severity: TranslationWarningSeverity = TranslationWarningSeverity.WARNING
    segment_id: str | None = Field(default=None, min_length=1, max_length=100)


class TranslationResultStatus(StrEnum):
    COMPLETED = "completed"
    COMPLETED_WITH_WARNINGS = "completed_with_warnings"
    CANCELLED_WITH_PARTIAL = "cancelled_with_partial"


class TranslationProgress(BaseModel):
    """Immutable progress snapshot; partial content is not repr-visible."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    completed_segments: int = Field(default=0, ge=0)
    total_segments: int = Field(default=0, ge=0)
    partial_text: str = Field(default="", repr=False)
    warnings: tuple[TranslationWarning, ...] = ()

    @model_validator(mode="after")
    def _counts_are_consistent(self) -> Self:
        if self.completed_segments > self.total_segments:
            raise ValueError("completed_segments must not exceed total_segments")
        return self


class OfflineTranslationResult(BaseModel):
    """Editable translation result. It is returned, never persisted by this service."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    status: TranslationResultStatus
    translated_text: str = Field(repr=False)
    direction: TranslationDirection
    pack_id: str = Field(min_length=1, max_length=128)
    model_ids: tuple[str, ...] = Field(min_length=1)
    source_chars: int = Field(ge=1)
    output_chars: int = Field(ge=0)
    warnings: tuple[TranslationWarning, ...] = ()
    elapsed_ms: int = Field(ge=0)
    completed_segments: int = Field(ge=0)
    total_segments: int = Field(ge=1)

    @model_validator(mode="after")
    def _status_matches_counts_and_warnings(self) -> Self:
        if self.completed_segments > self.total_segments:
            raise ValueError("completed_segments must not exceed total_segments")
        if self.status is TranslationResultStatus.COMPLETED:
            if self.warnings:
                raise ValueError("completed result cannot contain warnings")
            if self.completed_segments != self.total_segments:
                raise ValueError("completed result must contain every segment")
        if self.status is TranslationResultStatus.COMPLETED_WITH_WARNINGS:
            if not self.warnings:
                raise ValueError("completed_with_warnings requires at least one warning")
            if self.completed_segments != self.total_segments:
                raise ValueError("completed_with_warnings must contain every segment")
        if (
            self.status is TranslationResultStatus.CANCELLED_WITH_PARTIAL
            and self.completed_segments >= self.total_segments
        ):
            raise ValueError("cancelled result cannot contain every segment")
        return self


__all__ = [
    "OfflineTranslationResult",
    "TranslationProgress",
    "TranslationResultStatus",
    "TranslationWarning",
    "TranslationWarningCode",
    "TranslationWarningSeverity",
]
