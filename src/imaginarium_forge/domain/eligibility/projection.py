"""Canonical eligibility request projection (approved schema note §1.5).

The projection is the ONLY input shape the validator sees and the ONLY object
the audit fingerprint is computed over. It carries no story/prompt prose and
no secrets. `adult_content_requested` is the sole trigger for adult-eligibility
validation — dark/horror/violent NON-sexual content does not trigger it.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from imaginarium_forge.canonical import sha256_of_canonical
from imaginarium_forge.domain.common.enums import ContentIntensity, ContentRating, RequestType


class EligibilityRequestProjection(BaseModel):
    model_config = ConfigDict(frozen=True)

    request_type: RequestType
    content_rating: ContentRating = ContentRating.GENERAL
    content_intensity: ContentIntensity = ContentIntensity.GENERAL
    adult_content_requested: bool = False
    policy_profile_id: str = ""
    policy_profile_version: str = ""
    character_id: str = ""
    character_version_id: str | None = None
    age_status: dict[str, Any] = Field(default_factory=dict)
    adult_presentation: dict[str, Any] = Field(default_factory=dict)
    visual_presentation_cues: dict[str, Any] = Field(default_factory=dict)
    source_metadata: dict[str, Any] | None = None
    originality_review: dict[str, Any] | None = None
    eligibility_relevant_flags: tuple[str, ...] = ()
    validator_version: str = ""

    def fingerprint(self) -> str:
        """sha256(canonical_json(projection)) — audit/debug only, never a cache key."""
        return sha256_of_canonical(self.model_dump(mode="json"))
