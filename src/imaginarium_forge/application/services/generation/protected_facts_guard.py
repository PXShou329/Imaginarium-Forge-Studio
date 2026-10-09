"""Hash-only literal evidence for protected facts in generated prose.

Literal presence is deterministic; semantic contradiction detection is not.
The report therefore makes that boundary explicit so callers can fail closed or
route a candidate to continuity/author review without logging prose or facts.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

_WHITESPACE = re.compile(r"\s+")


class ProtectedFactDisposition(StrEnum):
    """Result of the deterministic literal check for one protected fact."""

    LITERAL_PRESENT = "literal_present"
    NOT_MENTIONED_UNVERIFIED = "not_mentioned_unverified"


class ProtectedFactCheck(BaseModel):
    """Hash-only check record; raw protected facts never enter telemetry."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    fact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    disposition: ProtectedFactDisposition
    literal_present: bool

    @model_validator(mode="after")
    def _coherent_disposition(self) -> ProtectedFactCheck:
        if self.literal_present != (self.disposition is ProtectedFactDisposition.LITERAL_PRESENT):
            raise ValueError("literal presence and disposition disagree")
        return self


class ProtectedFactsReport(BaseModel):
    """Explicit validation boundary for protected-fact continuity evidence."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    candidate_text_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    checks: tuple[ProtectedFactCheck, ...] = ()
    fact_count: int = Field(ge=0)
    literal_present_count: int = Field(ge=0)
    unverified_count: int = Field(ge=0)
    literal_gate_passed: bool
    semantic_preservation_verified: bool = False
    requires_semantic_review: bool

    @model_validator(mode="after")
    def _coherent_counts(self) -> ProtectedFactsReport:
        if self.fact_count != len(self.checks):
            raise ValueError("fact count does not match checks")
        expected_present = sum(check.literal_present for check in self.checks)
        if self.literal_present_count != expected_present:
            raise ValueError("literal-present count does not match checks")
        if self.unverified_count != self.fact_count - expected_present:
            raise ValueError("unverified count does not match checks")
        if self.literal_gate_passed != (self.unverified_count == 0):
            raise ValueError("literal gate state does not match unverified facts")
        if self.semantic_preservation_verified:
            raise ValueError("literal matching cannot claim semantic preservation")
        if self.requires_semantic_review != bool(self.fact_count):
            raise ValueError("protected facts require explicit semantic review")
        return self


class ProtectedFactsGuard:
    """Check normalized literal presence and disclose what cannot be verified."""

    def inspect(
        self,
        *,
        candidate_text: str,
        protected_facts: tuple[str, ...],
    ) -> ProtectedFactsReport:
        """Return hash-only evidence without inferring semantic correctness."""

        normalized_candidate = _normalize(candidate_text)
        normalized_facts = tuple(_normalize(fact) for fact in protected_facts)
        if any(not fact for fact in normalized_facts):
            raise ValueError("protected facts must not be blank")
        fact_hashes = tuple(_sha256_text(fact) for fact in normalized_facts)
        if len(fact_hashes) != len(set(fact_hashes)):
            raise ValueError("protected facts must be unique after normalization")

        checks = tuple(
            ProtectedFactCheck(
                fact_sha256=fact_hash,
                disposition=(
                    ProtectedFactDisposition.LITERAL_PRESENT
                    if fact in normalized_candidate
                    else ProtectedFactDisposition.NOT_MENTIONED_UNVERIFIED
                ),
                literal_present=fact in normalized_candidate,
            )
            for fact, fact_hash in zip(normalized_facts, fact_hashes, strict=True)
        )
        present_count = sum(check.literal_present for check in checks)
        fact_count = len(checks)
        return ProtectedFactsReport(
            candidate_text_sha256=_sha256_text(candidate_text),
            checks=checks,
            fact_count=fact_count,
            literal_present_count=present_count,
            unverified_count=fact_count - present_count,
            literal_gate_passed=present_count == fact_count,
            semantic_preservation_verified=False,
            requires_semantic_review=bool(fact_count),
        )


def _normalize(text: str) -> str:
    return _WHITESPACE.sub(" ", unicodedata.normalize("NFKC", text).casefold()).strip()


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


__all__ = [
    "ProtectedFactCheck",
    "ProtectedFactDisposition",
    "ProtectedFactsGuard",
    "ProtectedFactsReport",
]
