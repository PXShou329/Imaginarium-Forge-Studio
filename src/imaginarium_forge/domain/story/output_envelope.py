"""Structural envelope for quarantined adult story output.

The envelope binds one prose digest to the exact Canon participant pins the
generation command expected and to the exact subjects the output producer
claims are present.  It deliberately does *not* inspect prose or prove that a
claim is semantically true.  A durable human-review receipt remains required
before quarantined prose may become a complete scene draft.
"""

from __future__ import annotations

import hashlib
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from imaginarium_forge.canonical import canonical_json
from imaginarium_forge.domain.creative.models import ParticipantPin

ADULT_STORY_OUTPUT_ENVELOPE_VERSION = "phase4-adult-story-output-v1"

_Identifier = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=200),
]
_Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class OutputSubjectClaim(BaseModel):
    """One claimed prose subject pinned to an exact Canon version.

    This is an assertion supplied alongside output, not an NLP classification
    or proof that the prose actually depicts the named subject.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    slot_id: _Identifier
    character_id: _Identifier
    character_version_id: _Identifier

    @property
    def exact_triple(self) -> tuple[str, str, str]:
        return (self.slot_id, self.character_id, self.character_version_id)

    def canonical(self) -> str:
        return canonical_json(self.model_dump(mode="json"))

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.canonical().encode("utf-8")).hexdigest()


class AdultStoryOutputEnvelope(BaseModel):
    """Hash-bound structural claims for adult story output quarantine.

    Exact set equality closes both omissions and injected-subject claims.  The
    model still cannot establish prose semantics; it only makes the producer's
    claims explicit, immutable, deterministic, and reviewable.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["phase4-adult-story-output-v1"] = (
        "phase4-adult-story-output-v1"
    )
    prose_text: str = Field(min_length=1, max_length=500_000)
    prose_sha256: _Sha256
    no_unlisted_sexual_subjects: Literal[True]
    expected_participant_pins: tuple[ParticipantPin, ...] = Field(
        min_length=1, max_length=24
    )
    claimed_subjects: tuple[OutputSubjectClaim, ...] = Field(
        min_length=1, max_length=24
    )

    @field_validator("claimed_subjects")
    @classmethod
    def _canonical_claim_order(
        cls, claims: tuple[OutputSubjectClaim, ...]
    ) -> tuple[OutputSubjectClaim, ...]:
        return tuple(sorted(claims, key=lambda claim: claim.exact_triple))

    @model_validator(mode="after")
    def _exact_subject_set(self) -> AdultStoryOutputEnvelope:
        calculated_prose_sha256 = hashlib.sha256(
            self.prose_text.encode("utf-8")
        ).hexdigest()
        if self.prose_sha256 != calculated_prose_sha256:
            raise ValueError("prose_sha256 必須精確綁定 envelope prose_text")
        expected = tuple(
            (
                pin.slot_id,
                pin.character_id,
                pin.character_version_id,
            )
            for pin in self.expected_participant_pins
        )
        claimed = tuple(claim.exact_triple for claim in self.claimed_subjects)
        if len(expected) != len(set(expected)):
            raise ValueError("expected participant pins 不可包含重複 exact triple")
        if len(claimed) != len(set(claimed)):
            raise ValueError("output subject claims 不可包含重複 exact triple")
        if set(claimed) != set(expected):
            raise ValueError("claimed subjects 必須與 expected participant pins 完全一致")
        return self

    def canonical(self) -> str:
        """Deterministic JSON used by durable quarantine storage."""
        return canonical_json(self.model_dump(mode="json"))

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.canonical().encode("utf-8")).hexdigest()
