"""Canonical character and request data used for live integrity validation."""

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
    policy_profile_id: str = ""
    policy_profile_version: str = ""
    character_id: str = ""
    character_version_id: str | None = None
    age_status: dict[str, Any] = Field(default_factory=dict)
    source_metadata: dict[str, Any] | None = None
    originality_review: dict[str, Any] | None = None
    eligibility_relevant_flags: tuple[str, ...] = ()
    validator_version: str = ""

    def fingerprint(self) -> str:
        """sha256(canonical_json(projection)) — audit/debug only, never a cache key."""
        return sha256_of_canonical(self.model_dump(mode="json"))
