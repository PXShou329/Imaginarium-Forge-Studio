"""Prompt experiment log (spec §34). LoRA settings are schema-only in Phase 2."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, field_validator


class ExperimentLog(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    checkpoint_id: str = ""
    checkpoint_sha256: str = ""
    prompt_project_id: str = ""
    prompt_variant_id: str = ""
    resolved_profile_hash: str = ""
    positive_prompt: str = ""
    negative_prompt: str = ""
    sampler: str = ""
    scheduler: str = ""
    steps: int | None = None
    cfg: float | None = None
    width: int | None = None
    height: int | None = None
    seed: int | None = None
    lora_settings: tuple[dict[str, str], ...] = ()  # schema-only until LoRA scanning
    overall_rating: int | None = None  # 1..5
    identity_score: int | None = None
    style_score: int | None = None
    instruction_adherence: int | None = None
    failure_tags: tuple[str, ...] = ()
    notes: str = ""
    local_output_reference: str = ""
    created_at: str = ""

    @field_validator("overall_rating", "identity_score", "style_score", "instruction_adherence")
    @classmethod
    def _score_range(cls, value: int | None) -> int | None:
        if value is not None and not 1 <= value <= 5:
            raise ValueError("評分必須在 1..5")
        return value
