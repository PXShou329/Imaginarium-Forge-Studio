"""Compiled output blocks (spec §30.2): the 14 required outputs, plus coverage."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class CoverageReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    covered_weight: int = 0
    required_weight: int = 0
    missing: tuple[str, ...] = ()

    @property
    def display(self) -> str:
        return f"{self.covered_weight} / {self.required_weight}"


class CompiledBlocks(BaseModel):
    """One deterministic compilation result (same AST+profile ⇒ identical blocks)."""

    model_config = ConfigDict(frozen=True)

    explanation_zh_tw: str = ""          # 1. Traditional Chinese structural explanation
    character_lock_block: str = ""       # 2.
    outfit_block: str = ""               # 3.
    pose_expression_block: str = ""      # 4.
    camera_block: str = ""               # 5.
    environment_block: str = ""          # 6.
    lighting_block: str = ""             # 7.
    style_block: str = ""                # 8.
    quality_block: str = ""              # 9.
    positive_prompt: str = ""            # 10.
    negative_prompt: str = ""            # 11.
    natural_language_prompt: str = ""    # 12.
    generation_notes: str = ""           # 13.
    conflict_warning_report: str = ""    # 14.

    identity_coverage: CoverageReport = Field(default_factory=CoverageReport)
    style_coverage: CoverageReport = Field(default_factory=CoverageReport)
    #: Gate A A-12 — recomputed against the final positive output; may differ
    #: from resolution-time coverage when a profile omits/truncates traits
    identity_coverage_compiled: CoverageReport = Field(default_factory=CoverageReport)
    style_coverage_compiled: CoverageReport = Field(default_factory=CoverageReport)
