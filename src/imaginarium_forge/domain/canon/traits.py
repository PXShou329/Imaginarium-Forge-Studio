"""Canonical traits and canon-strength rules (execution spec §8.5).

Rules implemented here:
- all four strengths (hard_lock / soft_canon / preference / scene_override) are
  representable and validated;
- conflicting Hard Locks are rejected;
- duplicate descriptors are normalized;
- a BASE version must not contain scene_override traits (Scene Overrides never
  mutate the base version — they exist as a separate validated schema and are
  applied per request via the eligibility/compile projections in later phases).
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from imaginarium_forge.domain.common.enums import CanonStrength
from imaginarium_forge.domain.common.ids import new_id


class CanonicalTrait(BaseModel):
    trait_id: str = Field(default_factory=new_id)
    category: str
    name: str
    canonical_descriptor: str
    value: str = ""
    strength: CanonStrength = CanonStrength.SOFT_CANON
    source: str = "user"
    notes: str = ""

    @field_validator("category", "name", "canonical_descriptor")
    @classmethod
    def _required_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("category/name/canonical_descriptor 為必填")
        return value.strip()

    def identity_key(self) -> tuple[str, str]:
        return (self.category.casefold(), self.name.casefold())


class SceneOverride(BaseModel):
    """A per-scene, non-mutating override of one trait (schema only in Phase 1)."""

    override_id: str = Field(default_factory=new_id)
    target_category: str
    target_name: str
    override_descriptor: str
    reason: str = ""

    @field_validator("target_category", "target_name", "override_descriptor")
    @classmethod
    def _required_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("scene override 欄位為必填")
        return value.strip()


def normalize_traits(traits: list[CanonicalTrait]) -> list[CanonicalTrait]:
    """Drop exact duplicate descriptors for the same (category, name)."""
    seen: set[tuple[str, str, str]] = set()
    result: list[CanonicalTrait] = []
    for trait in traits:
        key = (*trait.identity_key(), trait.canonical_descriptor.casefold())
        if key in seen:
            continue
        seen.add(key)
        result.append(trait)
    return result


def detect_hard_lock_conflicts(traits: list[CanonicalTrait]) -> list[str]:
    """Two hard locks on the same (category, name) with different descriptors conflict."""
    locks: dict[tuple[str, str], str] = {}
    conflicts: list[str] = []
    for trait in traits:
        if trait.strength is not CanonStrength.HARD_LOCK:
            continue
        key = trait.identity_key()
        descriptor = trait.canonical_descriptor.casefold()
        if key in locks and locks[key] != descriptor:
            conflicts.append(
                f"Hard Lock 衝突：{trait.category}/{trait.name} 同時鎖定"
                f"「{locks[key]}」與「{descriptor}」"
            )
        else:
            locks.setdefault(key, descriptor)
    return conflicts


def validate_base_version_traits(traits: list[CanonicalTrait]) -> list[CanonicalTrait]:
    """Base-version invariants: no scene_override strength, no hard-lock conflicts."""
    if any(t.strength is CanonStrength.SCENE_OVERRIDE for t in traits):
        raise ValueError(
            "scene_override（場景暫時覆寫）不得存入固定特徵"
            "（場景暫時覆寫不會改變固定特徵）"
        )
    normalized = normalize_traits(traits)
    conflicts = detect_hard_lock_conflicts(normalized)
    if conflicts:
        raise ValueError("；".join(conflicts))
    return normalized
