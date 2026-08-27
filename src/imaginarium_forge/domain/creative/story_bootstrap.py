"""Pure contracts for bootstrapping the first editable story scene.

The command consumes an exact Launchpad foundation and participant manifest.
It creates planning *working* versions only; it never accepts planning and it
contains no provider or database behaviour.
"""

from __future__ import annotations

import hashlib

from pydantic import BaseModel, ConfigDict, field_validator

from imaginarium_forge.canonical import canonical_json
from imaginarium_forge.domain.creative.models import (
    ParticipantManifest,
    StoryFoundationResult,
)

BOOTSTRAP_CONTRACT_VERSION = "phase4-first-scene-bootstrap-v1"


class CreativeStoryBootstrapRequest(BaseModel):
    """Exact inputs for creating the first chapter and first scene.

    ``foundation`` and ``participant_manifest`` are values returned by the
    Launchpad save commands.  Persisted ownership and source-chain links are
    still reloaded and verified by the application service; these caller
    values are never trusted on their own.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    project_id: str
    foundation: StoryFoundationResult
    participant_manifest: ParticipantManifest

    @field_validator("project_id")
    @classmethod
    def _project_required(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("project_id 為必填")
        return value

    @property
    def idempotency_fingerprint(self) -> str:
        """Stable identity for this one first-scene bootstrap command.

        Prior eligibility IDs are intentionally excluded.  Eligibility is
        live evidence, not reusable authority, and an adult retry creates new
        audit rows while resolving to the same planning aggregate IDs.
        """

        foundation = self.foundation
        payload = {
            "contract_version": BOOTSTRAP_CONTRACT_VERSION,
            "project_id": self.project_id,
            "foundation": {
                "request_fingerprint": foundation.request_fingerprint,
                "requirement_id": foundation.requirement_id,
                "requirement_version_id": foundation.requirement_version_id,
                "bible_id": foundation.bible_id,
                "bible_version_id": foundation.bible_version_id,
                "outline_id": foundation.outline_id,
                "outline_version_id": foundation.outline_version_id,
            },
            "participant_manifest": self.participant_manifest.model_dump(mode="json"),
        }
        return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


class CreativeStoryBootstrapResult(BaseModel):
    """Exact readback of the created (or idempotently replayed) working set."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    idempotency_fingerprint: str
    project_id: str
    outline_id: str
    outline_version_id: str
    chapter_id: str
    chapter_plan_version_id: str
    scene_id: str
    scene_card_version_id: str
    participant_manifest_fingerprint: str
    eligibility_evaluation_ids: tuple[str, ...] = ()
    replayed: bool = False
