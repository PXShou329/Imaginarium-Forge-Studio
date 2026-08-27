"""Age-policy constants for mature-content eligibility.

Normative source: Mature Content Addendum v1.1 (corrected revision) §4.1 and
ADR-018 with the C-1 modification approved on 2026-07-13:
  - hard floor 18 (non-configurable),
  - default minimum eligibility age 18 (18/19-year-old explicit adults must not be
    rejected merely because the new-character form prefills a higher age),
  - 21 is a UI prefill for newly created original adult characters ONLY, visible
    and user-confirmed; it is NOT an eligibility threshold.

The Phase 1 deterministic eligibility validator must import these constants from
here and must never silently override them.
"""

from dataclasses import dataclass
from typing import Final

HARD_FLOOR_EXPLICIT_AGE: Final[int] = 18
DEFAULT_MINIMUM_AGE_FOR_MATURE_CONTENT: Final[int] = 18
DEFAULT_NEW_ORIGINAL_ADULT_AGE: Final[int] = 21

EXPLICIT_ADULT_REQUIRED_FOR_MATURE_CONTENT: Final[bool] = True
ADULT_PRESENTATION_REQUIRED: Final[bool] = True


@dataclass(frozen=True)
class AgePolicy:
    """Aggregate read-only view over the module constants (validator convenience)."""

    hard_floor_explicit_age: int = HARD_FLOOR_EXPLICIT_AGE
    minimum_age_for_mature_content: int = DEFAULT_MINIMUM_AGE_FOR_MATURE_CONTENT
    default_new_original_adult_age: int = DEFAULT_NEW_ORIGINAL_ADULT_AGE
    explicit_adult_required: bool = EXPLICIT_ADULT_REQUIRED_FOR_MATURE_CONTENT
    adult_presentation_required: bool = ADULT_PRESENTATION_REQUIRED


AGE_POLICY = AgePolicy()


def clamp_project_minimum_age(requested: int) -> int:
    """Return a valid project-level minimum age; never below the hard floor."""
    return max(HARD_FLOOR_EXPLICIT_AGE, requested)
