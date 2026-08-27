"""Phase 4 adult-output quarantine review and integrity guards.

Structured generation can make a durable candidate, but never a usable scene
draft.  Only this service may promote that candidate after a fresh, all-subject
eligibility evaluation.  The receipt, complete draft, candidate finalization,
and scene working pointer are committed together.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from imaginarium_forge.application.errors import (
    ConflictError,
    NotFoundError,
    ValidationFailedError,
)
from imaginarium_forge.application.services.base import ServiceBase, SessionProvider
from imaginarium_forge.canonical import canonical_json
from imaginarium_forge.domain.common.enums import RequestType
from imaginarium_forge.domain.common.ids import utc_now_iso
from imaginarium_forge.domain.creative.models import (
    ParticipantManifest,
)
from imaginarium_forge.domain.creative.models import (
    ParticipantPin as ManifestParticipantPin,
)
from imaginarium_forge.domain.prompt.content_mode import ContentMode, derives_adult
from imaginarium_forge.domain.story.generation_input import GenerationInputSnapshot
from imaginarium_forge.domain.story.output_envelope import (
    ADULT_STORY_OUTPUT_ENVELOPE_VERSION,
    AdultStoryOutputEnvelope,
)
from imaginarium_forge.infrastructure.db.models.orm import EligibilityEvaluationRow
from imaginarium_forge.infrastructure.db.repositories.adult_output_repos import (
    AdultOutputCandidateRecord,
    AdultOutputCandidateRepository,
    AdultOutputReviewRecord,
    AdultOutputReviewRepository,
)
from imaginarium_forge.infrastructure.db.repositories.story_repos import (
    DraftRecord,
    GenerationRunRecord,
    GenerationRunRepository,
    SceneDraftRepository,
    StorySceneRepository,
)


@dataclass(frozen=True, slots=True)
class AdultOutputConfirmation:
    candidate: AdultOutputCandidateRecord
    review: AdultOutputReviewRecord
    draft: DraftRecord


@dataclass(frozen=True, slots=True)
class PendingAdultOutputCandidate:
    candidate_id: str
    prose_text: str
    prose_sha256: str
    manifest: ParticipantManifest
    envelope_version: str
    envelope_sha256: str
    created_at: str


def build_adult_participant_manifest(
    snapshot: GenerationInputSnapshot,
) -> ParticipantManifest:
    """Map the immutable story snapshot to stable structured-output slots."""
    if not snapshot.participants:
        raise ValidationFailedError("成人輸出 snapshot 缺少參與角色")
    pov_count = sum(participant.is_pov for participant in snapshot.participants)
    if pov_count > 1:
        raise ValidationFailedError("成人輸出 snapshot 不可包含多位 POV primary")
    pins = tuple(
        ManifestParticipantPin(
            slot_id=f"scene-participant-{position}",
            character_id=participant.character_id,
            character_version_id=participant.character_version_id,
            role=participant.role,
            is_primary=(participant.is_pov if pov_count else position == 1),
        )
        for position, participant in enumerate(snapshot.participants, start=1)
    )
    return ParticipantManifest(participants=pins)


def build_adult_story_provider_contract(
    snapshot: GenerationInputSnapshot,
) -> str:
    """Render the exact structured-output suffix sent for an adult story run.

    Historical raw-off inspection calls this same renderer, so the audit path
    cannot drift from orchestration's provider-visible bytes.
    """

    manifest = build_adult_participant_manifest(snapshot)
    expected_pins_json = canonical_json(
        [pin.model_dump(mode="json") for pin in manifest.participants]
    )
    return (
        "## Phase 4 成人輸出隔離契約\n"
        "只能回傳符合 AdultStoryOutputEnvelope JSON Schema 的 JSON；"
        "prose_text 是完整正文，prose_sha256 必須是其 UTF-8 SHA-256；"
        "claimed_subjects 必須精確等於下列 expected participant pins；"
        "no_unlisted_sexual_subjects 必須為 true。此結構聲明不取代人工審閱。\n"
        f"expected_participant_pins={expected_pins_json}"
    )


def _word_count(text: str) -> int:
    cjk = sum(1 for character in text if "\u4e00" <= character <= "\u9fff")
    latin = len([word for word in text.split() if any(char.isalpha() for char in word)])
    return cjk + latin


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _audit_ids(raw: str) -> tuple[str, ...]:
    try:
        values = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValidationFailedError("成人輸出資格 audit IDs 不是有效 JSON") from exc
    if (
        not isinstance(values, list)
        or not values
        or not all(isinstance(value, str) and value.strip() for value in values)
        or len(values) != len(set(values))
    ):
        raise ValidationFailedError("成人輸出資格 audit IDs 必須為非空且不重複的字串陣列")
    return tuple(values)


def assert_adult_audit_coverage(
    session: Session,
    *,
    audit_ids: tuple[str, ...],
    manifest: ParticipantManifest,
    label: str,
) -> None:
    audits = session.scalars(
        select(EligibilityEvaluationRow).where(EligibilityEvaluationRow.id.in_(audit_ids))
    ).all()
    if len(audits) != len(audit_ids) or len(audits) != len(manifest.participants):
        raise ValidationFailedError(f"{label}資格 audits 未完整覆蓋所有參與角色")
    if any(
        not bool(audit.allowed) or audit.request_type != RequestType.STORY_GENERATION.value
        for audit in audits
    ):
        raise ValidationFailedError(f"{label}資格 audits 含未允許或非故事生成紀錄")
    audit_pairs = {
        (audit.character_id, audit.character_version_id) for audit in audits
    }
    if audit_pairs != set(manifest.exact_pairs):
        raise ValidationFailedError(f"{label}資格 audits 未精確覆蓋 participant pins")


def _validated_candidate_run(
    session: Session,
    candidate: AdultOutputCandidateRecord,
    *,
    require_pending: bool,
) -> tuple[GenerationRunRecord, GenerationInputSnapshot, ParticipantManifest]:
    if require_pending and candidate.status != "pending":
        raise ConflictError("成人輸出候選已完成審核或拒絕，不可重複確認")
    run = GenerationRunRepository(session).get(candidate.generation_run_id)
    if run is None:
        raise ValidationFailedError("成人輸出候選缺少 generation run")
    if (
        run.project_id,
        run.story_scene_id,
        run.scene_card_version_id,
    ) != (
        candidate.project_id,
        candidate.story_scene_id,
        candidate.scene_card_version_id,
    ):
        raise ValidationFailedError("成人輸出候選與 generation run ownership 不一致")
    if run.status != "completed":
        raise ValidationFailedError("只有 completed 成人 generation run 可進入人工確認")
    try:
        mode = ContentMode(run.content_mode)
    except ValueError as exc:
        raise ValidationFailedError("generation run content_mode 無效") from exc
    if not derives_adult(mode):
        raise ValidationFailedError("非成人 generation run 不可使用成人輸出確認流程")
    try:
        snapshot = GenerationInputSnapshot.model_validate_json(run.input_snapshot_json)
    except ValueError as exc:
        raise ValidationFailedError("成人 generation snapshot 無法驗證") from exc
    if (
        snapshot.canonical_payload() != run.input_snapshot_json
        or snapshot.sha256 != run.input_snapshot_sha256
    ):
        raise ValidationFailedError("成人 generation snapshot canonical/hash 不一致")
    if (
        snapshot.project_id,
        snapshot.story_scene_id,
        snapshot.scene_card_version_id,
    ) != (
        candidate.project_id,
        candidate.story_scene_id,
        candidate.scene_card_version_id,
    ):
        raise ValidationFailedError("成人 generation snapshot ownership 不一致")
    if (
        snapshot.content_mode.value != run.content_mode
        or snapshot.output_contract_version
        != ADULT_STORY_OUTPUT_ENVELOPE_VERSION
        or not snapshot.options.structured_mode
        or snapshot.options.stream
    ):
        raise ValidationFailedError("成人 generation snapshot 未綁定正確 structured contract")
    if run.run_kind == "revision":
        try:
            revision_payload = json.loads(snapshot.revision_request_json)
        except json.JSONDecodeError as exc:
            raise ValidationFailedError("成人 revision request JSON 無法驗證") from exc
        canonical_revision = canonical_json(revision_payload)
        revision_sha256 = _sha256_text(canonical_revision)
        if (
            canonical_revision != snapshot.revision_request_json
            or revision_sha256 != snapshot.revision_request_fingerprint
            or revision_sha256 != run.revision_request_fingerprint
        ):
            raise ValidationFailedError("成人 revision request provenance/hash 不一致")
    manifest = build_adult_participant_manifest(snapshot)
    try:
        stored_manifest = ParticipantManifest.model_validate_json(
            candidate.participant_manifest_json
        )
    except ValueError as exc:
        raise ValidationFailedError("成人候選 participant manifest 無法驗證") from exc
    if (
        stored_manifest != manifest
        or stored_manifest.fingerprint != candidate.participant_manifest_sha256
    ):
        raise ValidationFailedError("成人候選 participant manifest 與 run snapshot 不一致")
    generation_audit_ids = tuple(snapshot.eligibility_evaluation_ids)
    if generation_audit_ids != _audit_ids(run.eligibility_evaluation_ids_json):
        raise ValidationFailedError("成人 run 與 snapshot 的 eligibility audit IDs 不一致")
    assert_adult_audit_coverage(
        session,
        audit_ids=generation_audit_ids,
        manifest=manifest,
        label="生成當下",
    )
    return run, snapshot, manifest


def assert_adult_draft_reviewed(
    session: Session,
    draft: DraftRecord,
    *,
    action_label: str,
) -> None:
    """Fail closed when an adult-run draft lacks an intact review receipt."""
    if not draft.generation_run_id:
        return
    run = GenerationRunRepository(session).get(draft.generation_run_id)
    if run is None:
        raise ValidationFailedError(f"{action_label}失敗：草稿缺少 generation run")
    try:
        content_mode = ContentMode(run.content_mode)
    except ValueError as exc:
        raise ValidationFailedError(f"{action_label}失敗：run content_mode 無效") from exc
    if not derives_adult(content_mode):
        return
    try:
        candidate = AdultOutputCandidateRepository(session).get_for_run(run.id)
    except ValueError as exc:
        raise ValidationFailedError(f"{action_label}失敗：成人候選完整性驗證未通過") from exc
    if candidate is None:
        raise ValidationFailedError(f"{action_label}失敗：成人草稿缺少 durable candidate")
    _run, _snapshot, manifest = _validated_candidate_run(
        session, candidate, require_pending=False
    )
    try:
        review = AdultOutputReviewRepository(session).get_for_candidate(candidate.id)
    except ValueError as exc:
        raise ValidationFailedError(f"{action_label}失敗：成人 review receipt 無法驗證") from exc
    if candidate.status != "confirmed" or review is None or not review.reviewed:
        raise ValidationFailedError(f"{action_label}失敗：成人輸出尚未完成 durable 人工確認")
    if (
        review.resulting_scene_draft_id != draft.id
        or review.project_id != draft.project_id
        or review.story_scene_id != draft.story_scene_id
        or review.scene_card_version_id != draft.scene_card_version_id
        or review.participant_manifest_json != candidate.participant_manifest_json
        or review.participant_manifest_sha256 != manifest.fingerprint
        or draft.prose_text != candidate.prose_text
        or _sha256_text(draft.prose_text) != candidate.prose_sha256
    ):
        raise ValidationFailedError(f"{action_label}失敗：成人草稿、候選與 receipt 不一致")
    review_ids = _audit_ids(review.eligibility_evaluation_ids_json)
    if _sha256_text(canonical_json(list(review_ids))) != review.eligibility_evaluation_ids_sha256:
        raise ValidationFailedError(f"{action_label}失敗：成人 review audit IDs hash 不一致")
    assert_adult_audit_coverage(
        session,
        audit_ids=review_ids,
        manifest=manifest,
        label="確認當下",
    )


class AdultOutputReviewService(ServiceBase):
    def __init__(self, session_factory: SessionProvider, *, eligibility: object) -> None:
        super().__init__(session_factory)
        self._eligibility = eligibility

    def list_pending_for_scene(
        self, scene_id: str, *, expected_project_id: str
    ) -> list[PendingAdultOutputCandidate]:
        with self._session_factory() as session:
            scene = StorySceneRepository(session).get(scene_id)
            if scene is None or scene.project_id != expected_project_id:
                raise NotFoundError(f"找不到故事場景：{scene_id}")
            try:
                candidates = AdultOutputCandidateRepository(session).list_for_scene(
                    scene_id
                )
            except ValueError as exc:
                raise ValidationFailedError("成人輸出候選完整性驗證未通過") from exc
            pending: list[PendingAdultOutputCandidate] = []
            for candidate in candidates:
                if candidate.status != "pending":
                    continue
                _run, _snapshot, manifest = _validated_candidate_run(
                    session, candidate, require_pending=True
                )
                envelope = AdultStoryOutputEnvelope.model_validate_json(
                    candidate.envelope_json
                )
                pending.append(
                    PendingAdultOutputCandidate(
                        candidate_id=candidate.id,
                        prose_text=candidate.prose_text,
                        prose_sha256=candidate.prose_sha256,
                        manifest=manifest,
                        envelope_version=envelope.schema_version,
                        envelope_sha256=candidate.envelope_sha256,
                        created_at=candidate.created_at,
                    )
                )
        return pending

    def reject(
        self,
        candidate_id: str,
        *,
        reason: str,
        expected_project_id: str,
    ) -> AdultOutputCandidateRecord:
        if not reason.strip():
            raise ValidationFailedError("拒絕成人輸出候選必須提供理由")
        with self._transaction() as session:
            try:
                candidate = AdultOutputCandidateRepository(session).get(candidate_id)
            except ValueError as exc:
                raise ValidationFailedError("成人輸出候選完整性驗證未通過") from exc
            if candidate is None or candidate.project_id != expected_project_id:
                raise NotFoundError(f"找不到成人輸出候選：{candidate_id}")
            _validated_candidate_run(session, candidate, require_pending=True)
            if not AdultOutputCandidateRepository(session).reject(
                candidate_id,
                reason=reason.strip(),
                rejected_at=utc_now_iso(),
            ):
                raise ConflictError("成人輸出候選已完成確認或拒絕")
        with self._session_factory() as session:
            rejected = AdultOutputCandidateRepository(session).get(candidate_id)
        assert rejected is not None
        return rejected

    def confirm(
        self,
        candidate_id: str,
        *,
        reviewed_complete_output: bool,
        expected_project_id: str,
        expected_prose_sha256: str,
        expected_manifest_fingerprint: str,
    ) -> AdultOutputConfirmation:
        if not reviewed_complete_output:
            raise ValidationFailedError("必須明確確認已完整審閱成人輸出，才能建立草稿")

        with self._session_factory() as session:
            try:
                candidate = AdultOutputCandidateRepository(session).get(candidate_id)
            except ValueError as exc:
                raise ValidationFailedError("成人輸出候選完整性驗證未通過") from exc
            if candidate is None or candidate.project_id != expected_project_id:
                raise NotFoundError(f"找不到成人輸出候選：{candidate_id}")
            if (
                candidate.prose_sha256 != expected_prose_sha256
                or candidate.participant_manifest_sha256
                != expected_manifest_fingerprint
            ):
                raise ConflictError("成人輸出候選與作者剛審閱的正文/角色清單版本不一致")
            _run, _snapshot, manifest = _validated_candidate_run(
                session, candidate, require_pending=True
            )

        allowed, results, fresh_audit_ids = self._eligibility.evaluate_many_audited(  # type: ignore[attr-defined]
            participants=list(manifest.exact_pairs),
            request_type=RequestType.STORY_GENERATION,
            adult_content_requested=True,
        )
        if not allowed:
            blockers = "、".join(result.message for result in results if not result.allowed)
            raise ValidationFailedError(
                f"確認當下的成人內容資格驗證未通過：{blockers or '未提供理由'}"
            )
        if len(fresh_audit_ids) != len(manifest.participants):
            raise ValidationFailedError("確認當下的資格 audits 未完整覆蓋所有參與角色")

        reviewed_at = utc_now_iso()
        review_id = str(uuid.uuid4())
        draft_id = str(uuid.uuid4())
        audit_ids_json = canonical_json(list(fresh_audit_ids))
        with self._transaction() as session:
            try:
                current = AdultOutputCandidateRepository(session).get(candidate_id)
            except ValueError as exc:
                raise ValidationFailedError("成人輸出候選完整性驗證未通過") from exc
            if current is None or current.project_id != expected_project_id:
                raise NotFoundError(f"找不到成人輸出候選：{candidate_id}")
            if (
                current.prose_sha256 != expected_prose_sha256
                or current.participant_manifest_sha256
                != expected_manifest_fingerprint
            ):
                raise ConflictError("成人輸出候選在確認期間與已審閱內容不一致")
            run, snapshot, current_manifest = _validated_candidate_run(
                session, current, require_pending=True
            )
            if current_manifest != manifest:
                raise ConflictError("成人候選 participant manifest 在確認期間發生變更")
            assert_adult_audit_coverage(
                session,
                audit_ids=tuple(fresh_audit_ids),
                manifest=current_manifest,
                label="確認當下",
            )
            drafts = SceneDraftRepository(session)
            draft = DraftRecord(
                id=draft_id,
                story_scene_id=current.story_scene_id,
                project_id=current.project_id,
                draft_number=drafts.next_draft_number(current.story_scene_id),
                prose_text=current.prose_text,
                word_count=_word_count(current.prose_text),
                origin=("revised" if run.run_kind == "revision" else "generated"),
                draft_status="complete",
                was_accepted=False,
                accepted_at="",
                is_preview=snapshot.planning_mode.value == "preview",
                promoted_from_preview_draft_id=None,
                is_recovery_rebase=bool(run.is_recovery_rebase),
                scene_card_version_id=current.scene_card_version_id,
                generation_run_id=current.generation_run_id,
                revision_request_json=snapshot.revision_request_json,
                revision_request_fingerprint=(run.revision_request_fingerprint or None),
                parent_draft_id=run.parent_draft_id,
                created_at=reviewed_at,
            )
            review = AdultOutputReviewRecord(
                id=review_id,
                candidate_id=current.id,
                resulting_scene_draft_id=draft.id,
                project_id=current.project_id,
                story_scene_id=current.story_scene_id,
                scene_card_version_id=current.scene_card_version_id,
                resulting_draft_status="complete",
                participant_manifest_json=current.participant_manifest_json,
                participant_manifest_sha256=current.participant_manifest_sha256,
                eligibility_evaluation_ids_json=audit_ids_json,
                eligibility_evaluation_ids_sha256=_sha256_text(audit_ids_json),
                reviewed=True,
                reviewed_at=reviewed_at,
            )
            if not AdultOutputCandidateRepository(session).confirm(
                review, resulting_draft=draft
            ):
                raise ConflictError("成人輸出候選已由其他操作完成確認")
            StorySceneRepository(session).update_fields(
                draft.story_scene_id,
                working_draft_id=draft.id,
                updated_at=reviewed_at,
            )
        with self._session_factory() as session:
            confirmed_candidate = AdultOutputCandidateRepository(session).get(
                candidate_id
            )
        assert confirmed_candidate is not None
        return AdultOutputConfirmation(
            candidate=confirmed_candidate, review=review, draft=draft
        )
