"""Persistence for durable adult-output quarantine and review receipts.

Repositories never commit.  A caller-owned transaction makes inserting the
immutable review receipt and finalizing its candidate one atomic confirmation.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from imaginarium_forge.canonical import canonical_json
from imaginarium_forge.domain.creative.models import ParticipantManifest
from imaginarium_forge.domain.story.output_envelope import AdultStoryOutputEnvelope
from imaginarium_forge.infrastructure.db.models.orm import (
    AdultOutputCandidateRow,
    AdultOutputReviewRow,
    EligibilityEvaluationRow,
    GenerationRunRow,
    SceneCardParticipantRow,
    SceneDraftRow,
)
from imaginarium_forge.infrastructure.db.repositories.story_repos import (
    DraftRecord,
    SceneDraftRepository,
)

_CANDIDATE_STATUSES = frozenset({"pending", "rejected", "confirmed"})


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _canonical_payload(raw: str, digest: str, *, label: str) -> Any:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label} 必須是有效 JSON") from exc
    canonical = canonical_json(value)
    if raw != canonical:
        raise ValueError(f"{label} 必須使用 canonical JSON")
    if digest != _sha256_text(canonical):
        raise ValueError(f"{label} SHA-256 不符")
    return value


@dataclass(frozen=True, slots=True)
class AdultOutputCandidateRecord:
    id: str
    generation_run_id: str
    project_id: str
    story_scene_id: str
    scene_card_version_id: str
    prose_text: str
    prose_sha256: str
    envelope_json: str
    envelope_sha256: str
    participant_manifest_json: str
    participant_manifest_sha256: str
    status: str
    reason: str
    created_at: str
    finalized_at: str = ""

    def __post_init__(self) -> None:
        required = (
            self.id,
            self.generation_run_id,
            self.project_id,
            self.story_scene_id,
            self.scene_card_version_id,
            self.created_at,
        )
        if not all(value.strip() for value in required):
            raise ValueError("adult output candidate ownership/time 不得空白")
        if not self.prose_text:
            raise ValueError("adult output candidate prose 不得空白")
        if self.prose_sha256 != _sha256_text(self.prose_text):
            raise ValueError("adult output candidate prose SHA-256 不符")
        if self.status not in _CANDIDATE_STATUSES:
            raise ValueError("adult output candidate status 無效")
        if self.status == "pending" and self.finalized_at:
            raise ValueError("pending candidate 不得有 finalized_at")
        if self.status != "pending" and not self.finalized_at:
            raise ValueError("finalized candidate 必須有 finalized_at")
        if self.status == "rejected" and not self.reason.strip():
            raise ValueError("rejected candidate 必須有 reason")

        _canonical_payload(
            self.envelope_json,
            self.envelope_sha256,
            label="adult output envelope",
        )
        _canonical_payload(
            self.participant_manifest_json,
            self.participant_manifest_sha256,
            label="adult output participant manifest",
        )
        envelope = AdultStoryOutputEnvelope.model_validate_json(self.envelope_json)
        manifest = ParticipantManifest.model_validate_json(
            self.participant_manifest_json
        )
        if envelope.canonical() != self.envelope_json:
            raise ValueError("adult output envelope 必須使用 domain canonical JSON")
        if envelope.sha256 != self.envelope_sha256:
            raise ValueError("adult output envelope domain SHA-256 不符")
        if (
            envelope.prose_text != self.prose_text
            or envelope.prose_sha256 != self.prose_sha256
        ):
            raise ValueError("adult output envelope 未精確綁定候選 prose 與 SHA-256")
        if manifest.fingerprint != self.participant_manifest_sha256:
            raise ValueError("adult output participant manifest fingerprint 不符")
        if envelope.expected_participant_pins != manifest.participants:
            raise ValueError("adult output envelope pins 與 participant manifest 不一致")


@dataclass(frozen=True, slots=True)
class AdultOutputReviewRecord:
    id: str
    candidate_id: str
    resulting_scene_draft_id: str
    project_id: str
    story_scene_id: str
    scene_card_version_id: str
    resulting_draft_status: str
    participant_manifest_json: str
    participant_manifest_sha256: str
    eligibility_evaluation_ids_json: str
    eligibility_evaluation_ids_sha256: str
    reviewed: bool
    reviewed_at: str

    def __post_init__(self) -> None:
        required = (
            self.id,
            self.candidate_id,
            self.resulting_scene_draft_id,
            self.project_id,
            self.story_scene_id,
            self.scene_card_version_id,
            self.reviewed_at,
        )
        if not all(value.strip() for value in required):
            raise ValueError("adult output review ownership/time 不得空白")
        if self.resulting_draft_status != "complete":
            raise ValueError("adult output review 只能指向 complete draft")
        if not self.reviewed:
            raise ValueError("adult output review 必須明確 reviewed")
        _canonical_payload(
            self.participant_manifest_json,
            self.participant_manifest_sha256,
            label="adult output review manifest",
        )
        ParticipantManifest.model_validate_json(self.participant_manifest_json)
        eligibility_ids = _canonical_payload(
            self.eligibility_evaluation_ids_json,
            self.eligibility_evaluation_ids_sha256,
            label="adult output eligibility audit IDs",
        )
        if not isinstance(eligibility_ids, list) or not eligibility_ids or not all(
            isinstance(value, str) and value.strip() for value in eligibility_ids
        ):
            raise ValueError("eligibility audit IDs 必須是非空白字串陣列")
        if len(eligibility_ids) != len(set(eligibility_ids)):
            raise ValueError("eligibility audit IDs 不可重複")


class AdultOutputCandidateRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, record: AdultOutputCandidateRecord) -> None:
        if record.status != "pending":
            raise ValueError("new adult output candidate 必須是 pending")
        manifest = ParticipantManifest.model_validate_json(
            record.participant_manifest_json
        )
        persisted = self._session.scalars(
            select(SceneCardParticipantRow).where(
                SceneCardParticipantRow.scene_card_version_id
                == record.scene_card_version_id
            )
        ).all()
        expected_pairs = set(manifest.exact_pairs)
        persisted_pairs = {
            (participant.character_id, participant.character_version_id)
            for participant in persisted
        }
        if expected_pairs != persisted_pairs:
            raise ValueError(
                "adult output participant manifest 與 Scene Card exact pins 不一致"
            )
        self._session.add(AdultOutputCandidateRow(**asdict(record)))

    def get(self, candidate_id: str) -> AdultOutputCandidateRecord | None:
        row = self._session.get(AdultOutputCandidateRow, candidate_id)
        return None if row is None else self._map(row)

    def get_for_run(self, generation_run_id: str) -> AdultOutputCandidateRecord | None:
        row = self._session.scalar(
            select(AdultOutputCandidateRow).where(
                AdultOutputCandidateRow.generation_run_id == generation_run_id
            )
        )
        return None if row is None else self._map(row)

    def list_for_scene(self, scene_id: str) -> list[AdultOutputCandidateRecord]:
        stmt = (
            select(AdultOutputCandidateRow)
            .where(AdultOutputCandidateRow.story_scene_id == scene_id)
            .order_by(AdultOutputCandidateRow.created_at, AdultOutputCandidateRow.id)
        )
        return [self._map(row) for row in self._session.scalars(stmt)]

    def list_for_project(
        self, project_id: str, *, status: str | None = None
    ) -> list[AdultOutputCandidateRecord]:
        stmt = select(AdultOutputCandidateRow).where(
            AdultOutputCandidateRow.project_id == project_id
        )
        if status is not None:
            if status not in _CANDIDATE_STATUSES:
                raise ValueError("adult output candidate status 無效")
            stmt = stmt.where(AdultOutputCandidateRow.status == status)
        stmt = stmt.order_by(
            AdultOutputCandidateRow.created_at, AdultOutputCandidateRow.id
        )
        return [self._map(row) for row in self._session.scalars(stmt)]

    def confirm(
        self,
        review: AdultOutputReviewRecord,
        *,
        resulting_draft: DraftRecord,
        reason: str = "adult_output_review_confirmed",
    ) -> bool:
        """Create draft + receipt + pending→confirmed in one transaction.

        The resulting complete draft must not pre-exist.  This prevents a
        caller from exposing quarantined prose through ``scene_drafts`` before
        the durable review receipt exists.
        """
        candidate = self._session.get(AdultOutputCandidateRow, review.candidate_id)
        if candidate is None or candidate.status != "pending":
            return False
        ownership = (
            review.project_id,
            review.story_scene_id,
            review.scene_card_version_id,
        )
        if ownership != (
            candidate.project_id,
            candidate.story_scene_id,
            candidate.scene_card_version_id,
        ):
            raise ValueError("adult output review 與 candidate ownership 不一致")
        if (
            review.participant_manifest_json != candidate.participant_manifest_json
            or review.participant_manifest_sha256
            != candidate.participant_manifest_sha256
        ):
            raise ValueError("adult output review 與 candidate manifest 不一致")
        if review.resulting_scene_draft_id != resulting_draft.id:
            raise ValueError("adult output review resulting draft ID 不一致")
        if self._session.get(SceneDraftRow, resulting_draft.id) is not None:
            raise ValueError("adult output review resulting draft 不得預先存在")
        if (
            resulting_draft.project_id,
            resulting_draft.story_scene_id,
            resulting_draft.scene_card_version_id,
            resulting_draft.draft_status,
        ) != (*ownership, "complete"):
            raise ValueError("adult output review resulting draft ownership/status 不一致")
        if (
            resulting_draft.prose_text != candidate.prose_text
            or resulting_draft.generation_run_id != candidate.generation_run_id
        ):
            raise ValueError("adult output review resulting draft 未精確對應候選輸出")
        run = self._session.get(GenerationRunRow, candidate.generation_run_id)
        if (
            run is None
            or (run.project_id, run.story_scene_id, run.scene_card_version_id)
            != ownership
            or run.status != "completed"
        ):
            raise ValueError("adult output review 找不到相符的 completed generation run")
        eligibility_ids = json.loads(review.eligibility_evaluation_ids_json)
        generation_eligibility_ids = set(
            json.loads(run.eligibility_evaluation_ids_json)
        )
        if generation_eligibility_ids.intersection(eligibility_ids):
            raise ValueError("adult output review 必須使用確認當下的新資格稽核")
        audits = self._session.scalars(
            select(EligibilityEvaluationRow).where(
                EligibilityEvaluationRow.id.in_(eligibility_ids)
            )
        ).all()
        if len(audits) != len(eligibility_ids) or any(
            not bool(audit.allowed)
            or audit.request_type != "story_generation"
            or audit.evaluated_at < candidate.created_at
            or audit.evaluated_at < run.completed_at
            or audit.evaluated_at > review.reviewed_at
            for audit in audits
        ):
            raise ValueError("adult output review 必須綁定真實且允許的故事資格稽核")
        manifest = ParticipantManifest.model_validate_json(
            review.participant_manifest_json
        )
        if len(audits) != len(manifest.participants):
            raise ValueError("adult output review 資格稽核數量未完整覆蓋 participants")
        audit_pairs = {
            (audit.character_id, audit.character_version_id) for audit in audits
        }
        if audit_pairs != set(manifest.exact_pairs):
            raise ValueError("adult output review 資格稽核未完整覆蓋 exact participants")

        SceneDraftRepository(self._session).add(resulting_draft)
        self._session.flush()
        AdultOutputReviewRepository(self._session).add(review)
        self._session.flush()
        candidate.status = "confirmed"
        candidate.reason = reason
        candidate.finalized_at = review.reviewed_at
        self._session.flush()
        return True

    def reject(self, candidate_id: str, *, reason: str, rejected_at: str) -> bool:
        if not reason.strip() or not rejected_at.strip():
            raise ValueError("reject 必須提供 reason 與 rejected_at")
        row = self._session.get(AdultOutputCandidateRow, candidate_id)
        if row is None or row.status != "pending":
            return False
        row.status = "rejected"
        row.reason = reason
        row.finalized_at = rejected_at
        self._session.flush()
        return True

    @staticmethod
    def _map(row: AdultOutputCandidateRow) -> AdultOutputCandidateRecord:
        return AdultOutputCandidateRecord(
            id=row.id,
            generation_run_id=row.generation_run_id,
            project_id=row.project_id,
            story_scene_id=row.story_scene_id,
            scene_card_version_id=row.scene_card_version_id,
            prose_text=row.prose_text,
            prose_sha256=row.prose_sha256,
            envelope_json=row.envelope_json,
            envelope_sha256=row.envelope_sha256,
            participant_manifest_json=row.participant_manifest_json,
            participant_manifest_sha256=row.participant_manifest_sha256,
            status=row.status,
            reason=row.reason,
            created_at=row.created_at,
            finalized_at=row.finalized_at,
        )


class AdultOutputReviewRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, record: AdultOutputReviewRecord) -> None:
        """Append one reviewed receipt; confirmation should do this atomically."""
        self._session.add(AdultOutputReviewRow(**asdict(record)))

    def get(self, review_id: str) -> AdultOutputReviewRecord | None:
        row = self._session.get(AdultOutputReviewRow, review_id)
        return None if row is None else self._map(row)

    def get_for_candidate(self, candidate_id: str) -> AdultOutputReviewRecord | None:
        row = self._session.scalar(
            select(AdultOutputReviewRow).where(
                AdultOutputReviewRow.candidate_id == candidate_id
            )
        )
        return None if row is None else self._map(row)

    def list_for_scene(self, scene_id: str) -> list[AdultOutputReviewRecord]:
        stmt = (
            select(AdultOutputReviewRow)
            .where(AdultOutputReviewRow.story_scene_id == scene_id)
            .order_by(AdultOutputReviewRow.reviewed_at, AdultOutputReviewRow.id)
        )
        return [self._map(row) for row in self._session.scalars(stmt)]

    @staticmethod
    def _map(row: AdultOutputReviewRow) -> AdultOutputReviewRecord:
        return AdultOutputReviewRecord(
            id=row.id,
            candidate_id=row.candidate_id,
            resulting_scene_draft_id=row.resulting_scene_draft_id,
            project_id=row.project_id,
            story_scene_id=row.story_scene_id,
            scene_card_version_id=row.scene_card_version_id,
            resulting_draft_status=row.resulting_draft_status,
            participant_manifest_json=row.participant_manifest_json,
            participant_manifest_sha256=row.participant_manifest_sha256,
            eligibility_evaluation_ids_json=row.eligibility_evaluation_ids_json,
            eligibility_evaluation_ids_sha256=row.eligibility_evaluation_ids_sha256,
            reviewed=bool(row.reviewed),
            reviewed_at=row.reviewed_at,
        )
