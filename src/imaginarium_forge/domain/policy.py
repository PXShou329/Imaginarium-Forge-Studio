"""Chronological age metadata defaults for fictional characters."""

from dataclasses import dataclass
from typing import Final

AGE_OF_MAJORITY: Final[int] = 18


@dataclass(frozen=True)
class AgePolicy:
    """Aggregate read-only view over the module constants (validator convenience)."""

    age_of_majority: int = AGE_OF_MAJORITY


AGE_POLICY = AgePolicy()
