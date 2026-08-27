"""Deterministic benchmark statistics.

Percentile method (normative for all benchmark reports): **nearest-rank**.

    Given N sorted values and percentile q in (0, 1]:
        index = max(0, ceil(q * N) - 1)
        percentile = sorted_values[index]

Properties: always returns an actually observed value (no interpolation),
well-defined for tiny samples (N=1 returns that value; N=2 at q=0.95 returns
the larger value), and stable under duplicates. Empty input is a caller error
and raises ValueError — reports must not silently invent a latency number.
"""

from __future__ import annotations

import math
from collections.abc import Sequence


def percentile_nearest_rank(values: Sequence[float], q: float) -> float:
    """Nearest-rank percentile of `values` at quantile `q` (0 < q <= 1)."""
    if not values:
        raise ValueError("percentile of empty input is undefined")
    if not 0.0 < q <= 1.0:
        raise ValueError(f"q must be in (0, 1], got {q}")
    ordered = sorted(values)
    index = max(0, math.ceil(q * len(ordered)) - 1)
    return float(ordered[index])


def p95(values: Sequence[float]) -> float:
    """Convenience wrapper: nearest-rank 95th percentile."""
    return percentile_nearest_rank(values, 0.95)
