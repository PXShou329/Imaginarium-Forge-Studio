"""Character aggregate — identity-level data .

Ownership per the approved schema note: origin, age status, originality review,
and source metadata live on the CHARACTER; presentation lives on the VERSION.
No online age verification in workflow — user-entered verification is explicit
and auditable (verification note + confirmation flag).
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator, model_validator

from imaginarium_forge.domain.common.enums import (
    AgeClassification,
    CharacterOrigin,
    RecordStatus,
)


class AgeStatus(BaseModel):
    """Age status with deterministic consistency invariants ().

    Contradictory combinations are rejected at construction (defense layer 1):
    - verified_adult with an explicit age below 18 is contradictory;
    - verified_minor with an explicit age of 18 or older is contradictory.
    unknown/disputed records MAY retain an explicit age for audit, but that
    value never overrides the classification (enforced in the validator).
    """

    classification: AgeClassification = AgeClassification.UNKNOWN
    explicit_age: int | None = None
    verification_source_note: str = ""
    user_confirmed: bool = False

    @field_validator("explicit_age")
    @classmethod
    def _age_range(cls, value: int | None) -> int | None:
        if value is not None and not 0 <= value <= 200:
            raise ValueError("explicit_age 超出合理範圍")
        return value

    @model_validator(mode="after")
    def _no_contradiction(self) -> AgeStatus:
        from imaginarium_forge.domain.policy import AGE_POLICY

        floor = AGE_POLICY.age_of_majority
        if self.explicit_age is not None:
            if (
                self.classification is AgeClassification.VERIFIED_ADULT
                and self.explicit_age < floor
            ):
                raise ValueError(
                    f"矛盾的年齡狀態：verified_adult 但明確年齡 {self.explicit_age} < {floor}"
                )
            if (
                self.classification is AgeClassification.VERIFIED_MINOR
                and self.explicit_age >= floor
            ):
                raise ValueError(
                    f"矛盾的年齡狀態：verified_minor 但明確年齡 {self.explicit_age} >= {floor}"
                )
        return self


def derive_original_age_classification(explicit_age: int | None) -> AgeClassification:
    """For ORIGINAL characters the classification is derived from the explicit age.

    This keeps the classification consistent with the character's age.
    """
    from imaginarium_forge.domain.policy import AGE_POLICY

    if explicit_age is None:
        return AgeClassification.UNKNOWN
    if explicit_age < AGE_POLICY.age_of_majority:
        return AgeClassification.VERIFIED_MINOR
    return AgeClassification.VERIFIED_ADULT


class OriginalityReview(BaseModel):
    """Path B originality checklist result ."""

    completed: bool = False
    independent_identity_confirmed: bool = False
    no_source_identity_retained: bool = False
    review_note: str = ""
    reviewed_at: str | None = None

    @property
    def satisfied(self) -> bool:
        return (
            self.completed
            and self.independent_identity_confirmed
            and self.no_source_identity_retained
        )


class SourceMetadata(BaseModel):
    """User-entered source reference for an existing fictional character."""

    source_title: str = ""
    source_character_name: str = ""
    verification_method: str = ""
    verification_note: str = ""

class CharacterProfile(BaseModel):
    biography: str = ""
    personality_notes: str = ""
    voice_notes: str = ""
    motivation: str = ""
    fear: str = ""
    secret: str = ""
    internal_conflict: str = ""
    relationship_hooks: tuple[str, ...] = ()
    arc_start: str = ""
    arc_turning_points: tuple[str, ...] = ()
    arc_end: str = ""
    freeform_notes: str = ""


class Character(BaseModel):
    id: str
    project_id: str
    name: str
    character_origin: CharacterOrigin
    current_version_id: str | None = None
    age_status: AgeStatus = Field(default_factory=AgeStatus)
    originality_review: OriginalityReview | None = None
    source_metadata: SourceMetadata | None = None
    profile: CharacterProfile = Field(default_factory=CharacterProfile)
    status: RecordStatus = RecordStatus.ACTIVE
    created_at: str
    updated_at: str

    @field_validator("name")
    @classmethod
    def _name_required(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("角色名稱為必填")
        return value.strip()

    @model_validator(mode="after")
    def _origin_consistency(self) -> Character:
        if self.character_origin is CharacterOrigin.EXISTING:
            source = self.source_metadata
            if source is None:
                raise ValueError("既有角色必須填寫來源資訊（source_metadata）")
            # an empty SourceMetadata() is not valid — both core fields required.
            if not source.source_title.strip():
                raise ValueError("既有角色的來源作品名稱（source_title）為必填")
            if not source.source_character_name.strip():
                raise ValueError("既有角色的來源角色名稱（source_character_name）為必填")
        return self
