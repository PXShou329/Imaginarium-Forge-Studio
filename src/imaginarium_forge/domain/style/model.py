"""Style DNA and versioned style profiles (execution spec §8.7).

No artist-name imitation field exists. `reference_note` is an optional freeform
note only — it is not compiled into prompts in Phase 1 and must never become a
direct imitation compiler.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

from imaginarium_forge.domain.canon.traits import CanonicalTrait, validate_base_version_traits
from imaginarium_forge.domain.common.enums import RecordStatus


class StyleDNA(BaseModel):
    model_config = ConfigDict(frozen=True)

    medium: str = ""
    linework: str = ""
    color: str = ""
    shading: str = ""
    lighting: str = ""
    face_rendering: str = ""
    background: str = ""
    composition: str = ""
    post_processing: str = ""
    prohibited_traits: tuple[str, ...] = ()
    canonical_traits: tuple[CanonicalTrait, ...] = ()
    reference_note: str = ""

    def validated(self) -> StyleDNA:
        """Canon invariants + prohibited/required conflict rule (spec §12.5)."""
        cleaned = validate_base_version_traits(list(self.canonical_traits))
        prohibited = {p.casefold().strip() for p in self.prohibited_traits if p.strip()}
        conflicts = [
            t.canonical_descriptor
            for t in cleaned
            if t.canonical_descriptor.casefold() in prohibited
            or t.name.casefold() in prohibited
        ]
        if conflicts:
            raise ValueError(f"風格衝突：下列特徵同時被要求與禁止：{'、'.join(conflicts)}")
        return self.model_copy(update={"canonical_traits": tuple(cleaned)})


class StyleProfile(BaseModel):
    id: str
    project_id: str
    name: str
    current_version_id: str | None = None
    status: RecordStatus = RecordStatus.ACTIVE
    created_at: str
    updated_at: str

    @field_validator("name")
    @classmethod
    def _name_required(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("風格名稱為必填")
        return value.strip()


class StyleProfileVersion(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    style_profile_id: str
    version_number: int = Field(ge=1)
    style_dna: StyleDNA = Field(default_factory=StyleDNA)
    change_note: str = ""
    created_at: str
