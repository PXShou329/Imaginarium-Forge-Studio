"""Immutable author commands and evidence, distinct from prose candidates."""

from __future__ import annotations

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from imaginarium_forge.canonical import canonical_json
from imaginarium_forge.domain.generation.durable import _json_object, sha256_json


class UnitAuthorDecision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    id: str = Field(min_length=1, max_length=200)
    job_id: str = Field(min_length=1, max_length=200)
    step_id: str = Field(min_length=1, max_length=200)
    unit_id: str = Field(min_length=1, max_length=200)
    sequence: int = Field(ge=1)
    kind: Literal["grant_continuation", "adjust_target"]
    command_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    payload: dict[str, object]
    payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_at: str = Field(min_length=1, max_length=100)

    @field_validator("payload", mode="before")
    @classmethod
    def _safe_payload(cls, value: object) -> object:
        return _json_object(value, field_name="unit author decision")

    @model_validator(mode="after")
    def _verified(self) -> Self:
        if self.payload_sha256 != sha256_json(self.payload):
            raise ValueError("unit author decision payload hash mismatch")
        if len(canonical_json(self.payload)) > 4_000_000:
            raise ValueError("unit author decision payload exceeds storage limit")
        return self
