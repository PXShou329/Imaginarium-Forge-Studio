"""Deterministic overlap and repetition rejection for prose continuations."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

_PARAGRAPH_SPLIT = re.compile(r"(?:\r?\n)[ \t]*(?:\r?\n)+")
_WHITESPACE = re.compile(r"\s+")


class RepetitionWarning(StrEnum):
    """Stable warning codes; reports contain hashes/counts, not duplicated prose."""

    TAIL_OVERLAP = "tail_overlap"
    DUPLICATE_EXISTING_PARAGRAPH = "duplicate_existing_paragraph"
    DUPLICATE_CONTINUATION_PARAGRAPH = "duplicate_continuation_paragraph"
    HIGH_NGRAM_REPETITION = "high_ngram_repetition"


class RepetitionGuardReport(BaseModel):
    """Serializable mechanical evidence for accepting or rejecting a continuation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    accepted: bool
    existing_text_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    continuation_text_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    tail_overlap_chars: int = Field(ge=0)
    duplicate_existing_paragraph_sha256: tuple[str, ...] = ()
    duplicate_continuation_paragraph_sha256: tuple[str, ...] = ()
    repeated_ngram_ratio: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    ngram_size: int = Field(ge=4, le=128)
    warnings: tuple[RepetitionWarning, ...] = ()

    @model_validator(mode="after")
    def _coherent_report(self) -> RepetitionGuardReport:
        if self.accepted == bool(self.warnings):
            raise ValueError("accepted state and repetition warnings disagree")
        return self


class ContinuationMergeResult(BaseModel):
    """Accepted text or the unchanged saved prefix when a retry is required."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    accepted: bool
    requires_retry: bool
    combined_text: str
    combined_text_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    report: RepetitionGuardReport

    @model_validator(mode="after")
    def _coherent_merge(self) -> ContinuationMergeResult:
        if self.accepted == self.requires_retry:
            raise ValueError("accepted merge cannot require retry")
        if self.accepted != self.report.accepted:
            raise ValueError("merge and guard report disagree")
        if self.combined_text_sha256 != _sha256_text(self.combined_text):
            raise ValueError("combined text hash does not match")
        return self


class ContinuationRepetitionGuard:
    """Reject repeated prose before it can be merged into the saved candidate."""

    def __init__(
        self,
        *,
        minimum_overlap_chars: int = 24,
        minimum_paragraph_chars: int = 24,
        ngram_size: int = 16,
        repeated_ngram_threshold: float = 0.55,
        max_tail_scan_chars: int = 4_000,
    ) -> None:
        if not 8 <= minimum_overlap_chars <= 1_000:
            raise ValueError("minimum_overlap_chars must be between 8 and 1000")
        if not 8 <= minimum_paragraph_chars <= 10_000:
            raise ValueError("minimum_paragraph_chars must be between 8 and 10000")
        if not 4 <= ngram_size <= 128:
            raise ValueError("ngram_size must be between 4 and 128")
        if not 0.0 <= repeated_ngram_threshold <= 1.0:
            raise ValueError("repeated_ngram_threshold must be between 0 and 1")
        if not 128 <= max_tail_scan_chars <= 100_000:
            raise ValueError("max_tail_scan_chars must be between 128 and 100000")
        self._minimum_overlap_chars = minimum_overlap_chars
        self._minimum_paragraph_chars = minimum_paragraph_chars
        self._ngram_size = ngram_size
        self._repeated_ngram_threshold = repeated_ngram_threshold
        self._max_tail_scan_chars = max_tail_scan_chars

    def inspect(self, *, existing_text: str, continuation_text: str) -> RepetitionGuardReport:
        """Inspect a proposed continuation without returning or logging its prose."""

        if not existing_text:
            raise ValueError("repetition inspection requires existing prose")
        if not continuation_text.strip():
            raise ValueError("continuation text must not be blank")

        normalized_existing = _normalize(existing_text)
        normalized_continuation = _normalize(continuation_text)
        overlap = _tail_overlap(
            normalized_existing,
            normalized_continuation,
            max_scan=self._max_tail_scan_chars,
        )

        existing_paragraphs = _substantive_paragraphs(
            existing_text, minimum_chars=self._minimum_paragraph_chars
        )
        continuation_paragraphs = _substantive_paragraphs(
            continuation_text, minimum_chars=self._minimum_paragraph_chars
        )
        existing_hashes = {_sha256_text(paragraph) for paragraph in existing_paragraphs}
        continuation_hashes = tuple(
            _sha256_text(paragraph) for paragraph in continuation_paragraphs
        )
        duplicate_existing = tuple(sorted(set(continuation_hashes).intersection(existing_hashes)))
        duplicate_continuation = tuple(
            sorted({item for item in continuation_hashes if continuation_hashes.count(item) > 1})
        )
        ngram_ratio = _repeated_ngram_ratio(
            normalized_existing,
            normalized_continuation,
            ngram_size=self._ngram_size,
        )

        warnings: list[RepetitionWarning] = []
        # Exact replay is always a duplicate, even when the provider returned
        # fewer characters than the ordinary overlap/noise threshold. This
        # matters for token-limit/timeout continuations, where preserving the
        # short partial twice would corrupt the author's recovery candidate.
        if (
            overlap >= self._minimum_overlap_chars
            or normalized_existing == normalized_continuation
        ):
            warnings.append(RepetitionWarning.TAIL_OVERLAP)
        if duplicate_existing:
            warnings.append(RepetitionWarning.DUPLICATE_EXISTING_PARAGRAPH)
        if duplicate_continuation:
            warnings.append(RepetitionWarning.DUPLICATE_CONTINUATION_PARAGRAPH)
        if ngram_ratio >= self._repeated_ngram_threshold:
            warnings.append(RepetitionWarning.HIGH_NGRAM_REPETITION)

        return RepetitionGuardReport(
            accepted=not warnings,
            existing_text_sha256=_sha256_text(existing_text),
            continuation_text_sha256=_sha256_text(continuation_text),
            tail_overlap_chars=overlap,
            duplicate_existing_paragraph_sha256=duplicate_existing,
            duplicate_continuation_paragraph_sha256=duplicate_continuation,
            repeated_ngram_ratio=ngram_ratio,
            ngram_size=self._ngram_size,
            warnings=tuple(warnings),
        )

    def merge(self, *, existing_text: str, continuation_text: str) -> ContinuationMergeResult:
        """Merge accepted prose; rejected text leaves the saved prefix byte-for-byte intact."""

        report = self.inspect(existing_text=existing_text, continuation_text=continuation_text)
        if report.accepted:
            separator = (
                "" if existing_text[-1].isspace() or continuation_text[0].isspace() else "\n\n"
            )
            combined_text = f"{existing_text}{separator}{continuation_text}"
        else:
            combined_text = existing_text
        return ContinuationMergeResult(
            accepted=report.accepted,
            requires_retry=not report.accepted,
            combined_text=combined_text,
            combined_text_sha256=_sha256_text(combined_text),
            report=report,
        )


def _normalize(text: str) -> str:
    return _WHITESPACE.sub(" ", unicodedata.normalize("NFKC", text).casefold()).strip()


def _substantive_paragraphs(text: str, *, minimum_chars: int) -> tuple[str, ...]:
    normalized = (_normalize(part) for part in _PARAGRAPH_SPLIT.split(text))
    return tuple(part for part in normalized if len(part) >= minimum_chars)


def _tail_overlap(existing: str, continuation: str, *, max_scan: int) -> int:
    existing_tail = existing[-max_scan:]
    continuation_prefix = continuation[:max_scan]
    maximum = min(len(existing_tail), len(continuation_prefix))
    for length in range(maximum, 0, -1):
        if existing_tail[-length:] == continuation_prefix[:length]:
            return length
    return 0


def _repeated_ngram_ratio(existing: str, continuation: str, *, ngram_size: int) -> float:
    if len(existing) < ngram_size or len(continuation) < ngram_size:
        return 0.0
    existing_ngrams = {
        existing[index : index + ngram_size] for index in range(len(existing) - ngram_size + 1)
    }
    continuation_ngrams = [
        continuation[index : index + ngram_size]
        for index in range(len(continuation) - ngram_size + 1)
    ]
    matches = sum(ngram in existing_ngrams for ngram in continuation_ngrams)
    return matches / len(continuation_ngrams)


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


__all__ = [
    "ContinuationMergeResult",
    "ContinuationRepetitionGuard",
    "RepetitionGuardReport",
    "RepetitionWarning",
]
