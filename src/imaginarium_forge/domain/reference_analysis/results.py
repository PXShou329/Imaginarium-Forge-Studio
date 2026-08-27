"""Reference-analysis result shapes — schema only in Phase 2 (no provider)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class TagObservation(BaseModel):
    model_config = ConfigDict(frozen=True)

    tag: str
    confidence: float  # 0..1 as reported by a future provider


class ReferenceAnalysisResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    provider: str
    model: str
    observations: tuple[TagObservation, ...] = ()
    notes: str = ""
