"""High-precision visual cues for fail-closed adult-content checks."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from imaginarium_forge.domain.character.version import VisualDNA

PRESENTATION_CUE_DETECTOR_VERSION = "presentation-cues-v1"

_MINOR_ERA_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "minor_era_design",
        re.compile(
            r"\b(?:minor[- ]era|underage)\s+(?:version|design)\b|"
            r"\bchildhood\s+(?:version|design)\b|未成年期|童年版|幼年版",
            re.IGNORECASE,
        ),
    ),
)
_CHILDLIKE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "explicit_minor_age",
        re.compile(
            r"(?<!\d)(?:[0-9]|1[0-7])(?:\s+years?[- ]old|[- ]year[- ]old|"
            r"\s*yo\b|\s*歲)|未滿\s*18\s*歲|未成年(?:角色|人物|外觀|身形|呈現)?",
            re.IGNORECASE,
        ),
    ),
    (
        "childlike_presentation",
        re.compile(
            r"\bchild(?:like)?\s+(?:face|body|proportions?|appearance|pose)\b|"
            r"\bprepubescent\b|\bunderage\b|\bminor[- ]aged\b|\bunder\s+18\b|"
            r"\b(?:loli|shota)\b|蘿莉|正太|孩童化|幼態",
            re.IGNORECASE,
        ),
    ),
    (
        "school_age_presentation",
        re.compile(
            r"\b(?:elementary|primary|middle|high)[- ]school\s+"
            r"(?:student|uniform)\b|\b(?:schoolgirl|schoolboy)\b|"
            r"小學生|國中生|高中生|幼童|孩童|小女孩|小男孩",
            re.IGNORECASE,
        ),
    ),
)


@dataclass(frozen=True, slots=True)
class PresentationCueResult:
    minor_era: bool
    childlike_flags: tuple[str, ...]
    detector_version: str = PRESENTATION_CUE_DETECTOR_VERSION

    @property
    def has_conflict(self) -> bool:
        return self.minor_era or bool(self.childlike_flags)


def detect_presentation_cues(*positive_visual_texts: str) -> PresentationCueResult:
    """Inspect positive visual directions, excluding biography/prohibitions."""
    text = "\n".join(value for value in positive_visual_texts if value).strip()
    minor_era = any(pattern.search(text) for _, pattern in _MINOR_ERA_PATTERNS)
    childlike_flags = tuple(flag for flag, pattern in _CHILDLIKE_PATTERNS if pattern.search(text))
    return PresentationCueResult(
        minor_era=minor_era,
        childlike_flags=childlike_flags,
    )


def detect_visual_dna_presentation_cues(visual_dna: VisualDNA) -> PresentationCueResult:
    """Inspect every positive, persisted VisualDNA source in one place.

    Canonical traits are rendered by Prompt resolution, so omitting their
    descriptor/value would let a cue bypass live eligibility merely by moving
    it out of the five convenience fields.  Prohibited mutations and notes are
    intentionally negative/contextual text and therefore are not scanned.
    """

    trait_texts = tuple(
        text
        for trait in visual_dna.canonical_traits
        for text in (trait.name, trait.canonical_descriptor, trait.value)
        if text.strip()
    )
    return detect_presentation_cues(
        visual_dna.identity,
        visual_dna.face,
        visual_dna.hair,
        visual_dna.eyes,
        visual_dna.body,
        *visual_dna.distinguishing_features,
        *trait_texts,
    )
