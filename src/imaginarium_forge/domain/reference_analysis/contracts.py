"""Reference-analysis provider contracts — INTERFACE ONLY in Phase 2.

WD14 tagger / VLM analysis are Phase 2 non-blocking deferred scope. These
contracts exist so later phases can plug in providers without reshaping the
domain. No inference, no model loading, no network happens here.
"""

from __future__ import annotations

from typing import Protocol

from imaginarium_forge.domain.reference_analysis.results import ReferenceAnalysisResult


class ReferenceAnalyzer(Protocol):
    """A future image-reference analyzer (e.g. WD14 tagger adapter)."""

    def analyze(self, image_path: str) -> ReferenceAnalysisResult:
        """Analyze a LOCAL image and return structured results."""
        ...
