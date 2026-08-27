"""Character versions — presentation-level data; ACCEPTED VERSIONS ARE IMMUTABLE.

Immutability is enforced three ways: (1) frozen Pydantic models here, (2) the
repository exposes no update method for versions, (3) edits go through the
service as "create new version + explicit current-version switch".
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from imaginarium_forge.domain.canon.traits import CanonicalTrait, validate_base_version_traits
from imaginarium_forge.domain.common.enums import CanonStrength

from .gender import CharacterGender, strip_gender_prompt_tokens


class AdultPresentation(BaseModel):
    model_config = ConfigDict(frozen=True)

    adult_face_presentation: bool = False
    adult_body_presentation: bool = False
    is_minor_era_design: bool = False
    childlike_presentation_flags: tuple[str, ...] = ()
    selected_design_note: str = ""

    @property
    def is_valid_adult_presentation(self) -> bool:
        return (
            self.adult_face_presentation
            and self.adult_body_presentation
            and not self.is_minor_era_design
            and not self.childlike_presentation_flags
        )


class VisualDNA(BaseModel):
    model_config = ConfigDict(frozen=True)

    identity: str = ""
    gender: CharacterGender | None = None
    face: str = ""
    hair: str = ""
    eyes: str = ""
    body: str = ""
    distinguishing_features: tuple[str, ...] = ()
    prohibited_mutations: tuple[str, ...] = ()
    canonical_traits: tuple[CanonicalTrait, ...] = ()

    def validated_for_base_version(self) -> VisualDNA:
        """Apply base-version invariants and keep gender at one source of truth.

        ``gender`` drives visual prompts while Story context consumes Hard
        Canon traits.  A new version must never let those two projections
        disagree.  The structured gender field wins when explicitly set; a
        legacy version containing only the standard ``gender/gender`` trait
        is upgraded into the structured field.
        """

        traits = list(self.canonical_traits)
        gender_traits = [
            trait for trait in traits if trait.identity_key() == ("gender", "gender")
        ]
        effective_gender = self.gender
        if effective_gender is None and gender_traits:
            descriptors = {
                trait.canonical_descriptor.strip().casefold()
                for trait in gender_traits
            }
            if len(descriptors) != 1:
                raise ValueError("角色性別 Canon 鎖彼此衝突")
            try:
                effective_gender = CharacterGender(next(iter(descriptors)))
            except ValueError as exc:
                raise ValueError("角色性別 Canon 鎖只能是 female 或 male") from exc

        if effective_gender is not None:
            source_trait = gender_traits[0] if gender_traits else None
            traits = [
                trait
                for trait in traits
                if trait.identity_key() != ("gender", "gender")
            ]
            traits.append(
                source_trait.model_copy(
                    update={
                        "category": "gender",
                        "name": "gender",
                        "canonical_descriptor": effective_gender.value,
                        "strength": CanonStrength.HARD_LOCK,
                    }
                )
                if source_trait is not None
                else CanonicalTrait(
                    category="gender",
                    name="gender",
                    canonical_descriptor=effective_gender.value,
                    strength=CanonStrength.HARD_LOCK,
                    source="structured_gender",
                )
            )

        cleaned = validate_base_version_traits(traits)
        return self.model_copy(
            update={
                "gender": effective_gender,
                "distinguishing_features": (
                    strip_gender_prompt_tokens(self.distinguishing_features)
                    if effective_gender is not None
                    else self.distinguishing_features
                ),
                "canonical_traits": tuple(cleaned),
            }
        )


class CharacterVersion(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    character_id: str
    version_number: int = Field(ge=1)
    visual_dna: VisualDNA = Field(default_factory=VisualDNA)
    adult_presentation: AdultPresentation = Field(default_factory=AdultPresentation)
    voice_profile: str = ""
    personality_profile: str = ""
    change_note: str = ""
    created_at: str
