"""Eligibility application service.

The single rule that matters: authorization is ALWAYS a live re-evaluation of
current character data + selected version + current request. This service reads
the character and version fresh, runs the deterministic validator, writes an
append-only audit row, and returns the result. It NEVER reads an audit row to
authorize anything — prior "allowed" outcomes cannot grant a future request.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from imaginarium_forge.application.errors import NotFoundError, ValidationFailedError
from imaginarium_forge.application.services.base import ServiceBase, SessionProvider
from imaginarium_forge.canonical import canonical_json
from imaginarium_forge.domain.character.model import Character
from imaginarium_forge.domain.character.presentation_cues import (
    detect_presentation_cues,
    detect_visual_dna_presentation_cues,
)
from imaginarium_forge.domain.character.version import CharacterVersion
from imaginarium_forge.domain.common.enums import (
    ContentIntensity,
    ContentRating,
    RequestType,
)
from imaginarium_forge.domain.common.ids import new_id, utc_now_iso
from imaginarium_forge.domain.eligibility.projection import EligibilityRequestProjection
from imaginarium_forge.domain.eligibility.validator import (
    EligibilityResult,
    MatureContentEligibilityValidator,
)
from imaginarium_forge.infrastructure.db.models.orm import EligibilityEvaluationRow
from imaginarium_forge.infrastructure.db.repositories.characters import CharacterRepository
from imaginarium_forge.infrastructure.db.repositories.eligibility_audit import (
    EligibilityAuditRecord,
    EligibilityAuditRepository,
)


class EligibilityService(ServiceBase):
    def __init__(self, session_factory: SessionProvider) -> None:
        super().__init__(session_factory)
        self._validator = MatureContentEligibilityValidator()

    def _build_projection(
        self,
        *,
        character: Character,
        selected_version: CharacterVersion | None,
        request_type: RequestType,
        content_rating: ContentRating,
        content_intensity: ContentIntensity,
        adult_content_requested: bool,
        policy_profile_id: str,
        policy_profile_version: str,
        eligibility_relevant_flags: tuple[str, ...],
    ) -> EligibilityRequestProjection:
        review = character.originality_review
        cues = (
            detect_visual_dna_presentation_cues(selected_version.visual_dna)
            if selected_version
            else detect_presentation_cues()
        )
        return EligibilityRequestProjection(
            request_type=request_type,
            content_rating=content_rating,
            content_intensity=content_intensity,
            adult_content_requested=adult_content_requested,
            policy_profile_id=policy_profile_id,
            policy_profile_version=policy_profile_version,
            character_id=character.id,
            character_version_id=selected_version.id if selected_version else None,
            age_status=character.age_status.model_dump(mode="json"),
            adult_presentation=(
                selected_version.adult_presentation.model_dump(mode="json")
                if selected_version
                else {}
            ),
            visual_presentation_cues={
                "minor_era": cues.minor_era,
                "childlike_flags": list(cues.childlike_flags),
                "detector_version": cues.detector_version,
            },
            source_metadata=(
                character.source_metadata.model_dump(mode="json")
                if character.source_metadata
                else None
            ),
            originality_review=review.model_dump(mode="json") if review else None,
            eligibility_relevant_flags=eligibility_relevant_flags,
            validator_version=self._validator.version,
        )

    def evaluate(
        self,
        *,
        character_id: str,
        version_id: str | None,
        request_type: RequestType = RequestType.IMAGE_PROMPT_COMPILE,
        content_rating: ContentRating = ContentRating.MATURE,
        content_intensity: ContentIntensity = ContentIntensity.EXPLICIT,
        adult_content_requested: bool = True,
        policy_profile_id: str = "default",
        policy_profile_version: str = "1",
        eligibility_relevant_flags: tuple[str, ...] = (),
    ) -> EligibilityResult:
        """Live evaluation + append-only audit write. Never reads audit to decide."""
        result, _evaluation_id = self.evaluate_audited(
            character_id=character_id,
            version_id=version_id,
            request_type=request_type,
            content_rating=content_rating,
            content_intensity=content_intensity,
            adult_content_requested=adult_content_requested,
            policy_profile_id=policy_profile_id,
            policy_profile_version=policy_profile_version,
            eligibility_relevant_flags=eligibility_relevant_flags,
        )
        return result

    def evaluate_audited(
        self,
        *,
        character_id: str,
        version_id: str | None,
        request_type: RequestType = RequestType.IMAGE_PROMPT_COMPILE,
        content_rating: ContentRating = ContentRating.MATURE,
        content_intensity: ContentIntensity = ContentIntensity.EXPLICIT,
        adult_content_requested: bool = True,
        policy_profile_id: str = "default",
        policy_profile_version: str = "1",
        eligibility_relevant_flags: tuple[str, ...] = (),
    ) -> tuple[EligibilityResult, str]:
        """As ``evaluate``, but also returns the append-only audit row ID.

        A3-01 §7.4 requires a generation run to name the exact eligibility
        evaluations it relied on, so the decision can be re-examined later.
        """
        with self._transaction() as session:
            char_repo = CharacterRepository(session)
            character = char_repo.get(character_id)
            if character is None:
                raise NotFoundError(f"找不到角色：{character_id}")

            selected_version: CharacterVersion | None = None
            # The RAW attempted identifier (diagnostic; may be invalid/nonexistent).
            requested_version_id = version_id or character.current_version_id
            if requested_version_id is not None:
                selected_version = char_repo.get_version(requested_version_id)

            # A-03: the canonical audit relationship is ownership-protected. If the
            # attempted version does not exist or belongs to another character, the
            # canonical field stays NULL and only the diagnostic field keeps the
            # attempted value; the validator then blocks invalid_character_version.
            canonical_version_id: str | None = None
            if selected_version is not None and selected_version.character_id == character.id:
                canonical_version_id = selected_version.id

            projection = self._build_projection(
                character=character,
                selected_version=selected_version,
                request_type=request_type,
                content_rating=content_rating,
                content_intensity=content_intensity,
                adult_content_requested=adult_content_requested,
                policy_profile_id=policy_profile_id,
                policy_profile_version=policy_profile_version,
                eligibility_relevant_flags=eligibility_relevant_flags,
            )
            result = self._validator.evaluate(character, selected_version, projection)

            evaluation_id = new_id()
            EligibilityAuditRepository(session).add(
                EligibilityAuditRecord(
                    id=evaluation_id,
                    character_id=character.id,
                    character_version_id=canonical_version_id,
                    requested_character_version_id=requested_version_id,
                    request_type=request_type.value,
                    allowed=result.allowed,
                    reason_code=result.reason_code.value,
                    validator_version=result.validator_version,
                    input_fingerprint=result.input_fingerprint,
                    details_json=canonical_json({"message": result.message}),
                    evaluated_at=utc_now_iso(),
                )
            )
        return result, evaluation_id

    def evaluate_many(
        self,
        *,
        participants: list[tuple[str, str | None]],
        request_type: RequestType = RequestType.STORY_GENERATION,
        content_rating: ContentRating = ContentRating.MATURE,
        content_intensity: ContentIntensity = ContentIntensity.EXPLICIT,
        adult_content_requested: bool = True,
        policy_profile_id: str = "default",
        policy_profile_version: str = "1",
    ) -> tuple[bool, list[EligibilityResult]]:
        """Multi-character rule: ONE ineligible participant blocks the whole request.

        Each participant is still evaluated (and audited) individually so the UI
        can show precisely who blocked it. Returns (overall_allowed, per-result).
        """
        overall, results, _ids = self.evaluate_many_audited(
            participants=participants,
            request_type=request_type,
            content_rating=content_rating,
            content_intensity=content_intensity,
            adult_content_requested=adult_content_requested,
            policy_profile_id=policy_profile_id,
            policy_profile_version=policy_profile_version,
        )
        return overall, results

    def evaluate_many_audited(
        self,
        *,
        participants: list[tuple[str, str | None]],
        request_type: RequestType = RequestType.STORY_GENERATION,
        content_rating: ContentRating = ContentRating.MATURE,
        content_intensity: ContentIntensity = ContentIntensity.EXPLICIT,
        adult_content_requested: bool = True,
        policy_profile_id: str = "default",
        policy_profile_version: str = "1",
    ) -> tuple[bool, list[EligibilityResult], list[str]]:
        """As ``evaluate_many``, plus the audit row IDs (A3-01 §7.4)."""
        results: list[EligibilityResult] = []
        evaluation_ids: list[str] = []
        for character_id, version_id in participants:
            result, evaluation_id = self.evaluate_audited(
                character_id=character_id,
                version_id=version_id,
                request_type=request_type,
                content_rating=content_rating,
                content_intensity=content_intensity,
                adult_content_requested=adult_content_requested,
                policy_profile_id=policy_profile_id,
                policy_profile_version=policy_profile_version,
            )
            results.append(result)
            evaluation_ids.append(evaluation_id)
        overall = all(r.allowed for r in results) and len(results) > 0
        return overall, results, evaluation_ids

    def assert_evaluations_current_in_session(
        self,
        session: Session,
        *,
        participants: list[tuple[str, str | None]],
        evaluation_ids: tuple[str, ...],
        request_type: RequestType = RequestType.STORY_GENERATION,
        content_rating: ContentRating = ContentRating.MATURE,
        content_intensity: ContentIntensity = ContentIntensity.EXPLICIT,
        adult_content_requested: bool = True,
        policy_profile_id: str = "default",
        policy_profile_version: str = "1",
    ) -> None:
        """Recompute prior audit decisions inside the caller's write lock.

        Provider orchestration must commit its durable ``running`` evidence
        before network I/O, so the eligibility service first creates append-only
        audit rows in ordinary short transactions.  A mutable Character identity
        (not its immutable selected version) could change between that audit and
        the caller's final decision transaction.  This method closes that window:
        the caller acquires SQLite ``BEGIN IMMEDIATE``, then asks us to rebuild
        the exact projections and require byte-identical fingerprints before it
        writes a run, candidate, review, or acceptance receipt.

        The method deliberately does not commit and does not create another
        audit.  It is only an atomic freshness assertion over the exact rows the
        command is about to cite.
        """

        if (
            not participants
            or len(evaluation_ids) != len(participants)
            or len(set(evaluation_ids)) != len(evaluation_ids)
        ):
            raise ValidationFailedError("資格 audits 未完整且唯一地覆蓋所有參與角色")
        rows = session.scalars(
            select(EligibilityEvaluationRow).where(EligibilityEvaluationRow.id.in_(evaluation_ids))
        ).all()
        if len(rows) != len(evaluation_ids):
            raise ValidationFailedError("資格 audit rows 已缺失，不能授權目前操作")

        by_pair = {(row.character_id, row.character_version_id): row for row in rows}
        if len(by_pair) != len(rows) or set(by_pair) != set(participants):
            raise ValidationFailedError("資格 audits 與 exact participant pins 不一致")

        characters = CharacterRepository(session)
        for character_id, version_id in participants:
            audit = by_pair[(character_id, version_id)]
            character = characters.get(character_id)
            selected_version = (
                characters.get_version(version_id) if version_id is not None else None
            )
            if character is None:
                raise ValidationFailedError("資格驗證所需角色已不存在")
            projection = self._build_projection(
                character=character,
                selected_version=selected_version,
                request_type=request_type,
                content_rating=content_rating,
                content_intensity=content_intensity,
                adult_content_requested=adult_content_requested,
                policy_profile_id=policy_profile_id,
                policy_profile_version=policy_profile_version,
                eligibility_relevant_flags=(),
            )
            current = self._validator.evaluate(character, selected_version, projection)
            if (
                selected_version is None
                or selected_version.character_id != character_id
                or not current.allowed
                or not bool(audit.allowed)
                or audit.request_type != request_type.value
                or audit.character_version_id != version_id
                or audit.requested_character_version_id != version_id
                or audit.validator_version != current.validator_version
                or audit.reason_code != current.reason_code.value
                or audit.input_fingerprint != current.input_fingerprint
            ):
                raise ValidationFailedError(
                    "角色資格在 audit 後已變更或證據不一致；請重新執行目前操作"
                )

    def list_audit(self, character_id: str, *, limit: int | None = None) -> list[dict[str, object]]:
        """Read-only audit view (debugging/traceability ONLY — not authorization)."""
        records = self._read_only(
            lambda s: EligibilityAuditRepository(s).list_for_character(character_id, limit=limit)
        )
        return [
            {
                "evaluated_at": r.evaluated_at,
                "request_type": r.request_type,
                "allowed": r.allowed,
                "reason_code": r.reason_code,
                "character_version_id": r.character_version_id,
                "requested_character_version_id": r.requested_character_version_id,
                "validator_version": r.validator_version,
                "fingerprint": r.input_fingerprint,
            }
            for r in records
        ]
