"""Reusable outfit profiles owned by a character (execution spec §8.8)."""

from __future__ import annotations

from pydantic import BaseModel, field_validator, model_validator

from imaginarium_forge.domain.common.enums import RecordStatus


def _normalized(values: tuple[str, ...]) -> set[str]:
    return {v.casefold().strip() for v in values if v.strip()}


class OutfitProfile(BaseModel):
    id: str
    character_id: str
    name: str
    description: str = ""
    canonical_traits: tuple[str, ...] = ()
    optional_traits: tuple[str, ...] = ()
    prohibited_traits: tuple[str, ...] = ()
    status: RecordStatus = RecordStatus.ACTIVE
    created_at: str
    updated_at: str

    @field_validator("name")
    @classmethod
    def _name_required(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("服裝名稱為必填")
        return value.strip()

    @model_validator(mode="after")
    def _no_trait_conflicts(self) -> OutfitProfile:
        prohibited = _normalized(self.prohibited_traits)
        canonical_clash = _normalized(self.canonical_traits) & prohibited
        optional_clash = _normalized(self.optional_traits) & prohibited
        if canonical_clash or optional_clash:
            clashes = "、".join(sorted(canonical_clash | optional_clash))
            raise ValueError(f"服裝特徵衝突：同一特徵不可同時列為必備／可選與禁止：{clashes}")
        return self
