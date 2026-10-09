"""Persist universal-refinement proposals as durable generation candidates."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Protocol

from pydantic import BaseModel, ConfigDict, field_validator
from sqlalchemy.orm import Session

from imaginarium_forge.application.services.generation.candidate_application import (
    TargetSnapshotRequest,
    VersionedTargetSnapshot,
)
from imaginarium_forge.application.services.generation.output_measurement import (
    measure_output,
)
from imaginarium_forge.application.services.generation.refinement import (
    RefinementCandidate,
    RefinementMode,
)
from imaginarium_forge.domain.generation.durable import (
    ArtifactKind,
    AttemptStatus,
    CandidateAction,
    CandidateProvenance,
    CreativeOperation,
    GenerationCandidate,
    GenerationJob,
    GenerationStep,
    GenerationStepAttempt,
    JobStatus,
    StepKind,
    StepStatus,
    sha256_content,
    sha256_json,
    sha256_text,
)
from imaginarium_forge.domain.generation.intent import GenerationIntent
from imaginarium_forge.domain.generation.length import (
    GenerationLengthSpec,
    LengthStrictness,
    LengthUnit,
)
from imaginarium_forge.infrastructure.db.adapters.draft_candidate_target import (
    SUPPORTED_DRAFT_TARGETS,
    DraftCandidateTargetAdapter,
)
from imaginarium_forge.infrastructure.db.repositories.creative_candidates import (
    CreativeCandidateRepository,
)
from imaginarium_forge.infrastructure.db.repositories.creative_generation import (
    CreativeGenerationRepository,
)


class SessionProvider(Protocol):
    def __call__(self) -> Session: ...


Clock = Callable[[], str]


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


class RefinementCandidateBinding(BaseModel):
    """A refinement field anchored to one existing formal draft."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    target_type: str
    target_id: str | None
    field_name: str
    expected_base_snapshot_sha256: str | None = None

    @field_validator("target_type", "field_name")
    @classmethod
    def _required(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized or len(normalized) > 200:
            raise ValueError("refinement target binding 無效")
        return normalized

    @field_validator("expected_base_snapshot_sha256")
    @classmethod
    def _valid_snapshot_hash(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().lower()
        if len(normalized) != 64 or any(
            character not in "0123456789abcdef" for character in normalized
        ):
            raise ValueError("refinement target snapshot hash 無效")
        return normalized


class RefinementCandidateHistoryEntry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    candidate: GenerationCandidate
    actions: tuple[CandidateAction, ...] = ()


class RefinementCandidatePersistenceError(RuntimeError):
    """Safe base error for candidate durability failures."""


class RefinementCandidateTargetRequiredError(RefinementCandidatePersistenceError):
    def __init__(self) -> None:
        super().__init__("請先保存目前草稿，再建立可套用與復原的精修候選。")


class RefinementCandidateTargetStaleError(RefinementCandidatePersistenceError):
    def __init__(self) -> None:
        super().__init__("編輯器內容與已保存草稿不同；請先保存或重新載入後再精修。")


class RefinementCandidateConflictError(RefinementCandidatePersistenceError):
    def __init__(self) -> None:
        super().__init__("同一候選識別碼已對應不同內容；沒有寫入或套用任何變更。")


class RefinementCandidateNotFoundError(RefinementCandidatePersistenceError):
    def __init__(self) -> None:
        super().__init__("找不到已保存的精修候選。")


_OPERATION_BY_MODE: dict[RefinementMode, CreativeOperation] = {
    RefinementMode.POLISH: CreativeOperation.POLISH,
    RefinementMode.EXPAND: CreativeOperation.EXPAND,
    RefinementMode.CONDENSE: CreativeOperation.CONDENSE,
    RefinementMode.EMOTION: CreativeOperation.REWRITE,
    RefinementMode.DIALOGUE: CreativeOperation.IMPROVE_DIALOGUE,
    RefinementMode.PACING: CreativeOperation.REWRITE,
    RefinementMode.LOGIC_CONTINUITY: CreativeOperation.FIX_CONTINUITY,
    RefinementMode.STYLE_REWRITE: CreativeOperation.REWRITE,
    RefinementMode.SCREENPLAY_ADAPTATION: CreativeOperation.ADAPT,
    RefinementMode.CRITIQUE_ONLY: CreativeOperation.CRITIQUE,
}


class RefinementCandidatePersistenceService:
    """Create a one-step durable job/candidate relationship after refinement.

    Provider I/O has already completed when this service is called. The
    terminal job, completed step, candidate, source hash, context hash,
    provenance, and quality report are inserted atomically. No credential or
    provider client crosses this boundary.
    """

    def __init__(
        self,
        session_factory: SessionProvider,
        target_adapter: DraftCandidateTargetAdapter,
        *,
        clock: Clock = _utc_now,
    ) -> None:
        self._session_provider = session_factory
        self._target_adapter = target_adapter
        self._clock = clock

    @contextmanager
    def _transaction(self) -> Iterator[Session]:
        session = self._session_provider()
        try:
            with session.begin():
                yield session
        finally:
            session.close()

    def verify_target(
        self,
        binding: RefinementCandidateBinding,
        *,
        original_text: str,
    ) -> None:
        with self._transaction() as session:
            snapshot = self._read_binding(session, binding)
            self._assert_expected_snapshot(snapshot.snapshot, binding)
            if (
                self._target_adapter.field_text(
                    snapshot.snapshot,
                    field_name=binding.field_name,
                )
                != original_text
            ):
                raise RefinementCandidateTargetStaleError

    def persist(
        self,
        candidate: RefinementCandidate,
        binding: RefinementCandidateBinding,
    ) -> GenerationCandidate:
        with self._transaction() as session:
            candidates = CreativeCandidateRepository(session)
            existing = candidates.get_candidate(candidate.id)
            if existing is not None:
                self._assert_replay(session, existing, candidate, binding)
                return existing

            base = self._read_binding(session, binding)
            self._assert_expected_snapshot(base.snapshot, binding)
            if (
                self._target_adapter.field_text(
                    base.snapshot,
                    field_name=binding.field_name,
                )
                != candidate.original_text
            ):
                raise RefinementCandidateTargetStaleError
            content_snapshot = self._target_adapter.candidate_snapshot(
                base.snapshot,
                target_type=binding.target_type,
                field_name=binding.field_name,
                candidate_text=candidate.candidate_text,
            )
            now = self._clock()
            job_id = f"refinement-job:{sha256_text(candidate.id)[:32]}"
            step_id = f"refinement-step:{sha256_text(candidate.id)[:32]}"
            content_hash = sha256_content(
                text=candidate.candidate_text,
                structured=content_snapshot,
            )
            requested_provider, requested_model, provider, model, fallback = self._routes(candidate)
            latest_attempt = (
                candidate.provenance.attempts[-1] if candidate.provenance.attempts else None
            )
            attempt_number = latest_attempt.attempt_number if latest_attempt is not None else 1
            latency_ms = latest_attempt.latency_ms if latest_attempt is not None else None
            length_spec = self._length_contract(candidate)
            input_snapshot: dict[str, object] = {
                "base_snapshot_sha256": sha256_json(base.snapshot),
                "candidate_original_sha256": candidate.original_sha256,
                "field_name": binding.field_name,
                "intent_sha256": self._intent_sha256(candidate, binding),
                "mode": candidate.mode.value,
                "generation_length_spec_sha256": length_spec["generation_length_spec_sha256"],
            }
            context_snapshot: dict[str, object] = dict(candidate.context_snapshot)
            attempt = GenerationStepAttempt(
                attempt_number=1,
                status=AttemptStatus.COMPLETED,
                requested_provider=requested_provider,
                requested_model=requested_model,
                provider=provider,
                model=model,
                fallback_used=fallback,
                finish_reason="refinement_candidate",
                latency_ms=latency_ms,
                started_at=now,
                completed_at=now,
            )
            step = GenerationStep(
                id=step_id,
                job_id=job_id,
                ordinal=0,
                step_kind=StepKind.TARGETED_REVISION,
                status=StepStatus.COMPLETED,
                idempotency_key=candidate.provenance.idempotency_key,
                template_id=candidate.template_id,
                template_version=candidate.template_version,
                requested_provider=requested_provider,
                requested_model=requested_model,
                provider=provider,
                model=model,
                fallback_used=fallback,
                input_snapshot=input_snapshot,
                input_snapshot_sha256=sha256_json(input_snapshot),
                output_text=candidate.candidate_text,
                output_json=content_snapshot,
                output_sha256=content_hash,
                finish_reason="refinement_candidate",
                checkpoint_index=1,
                latency_ms=latency_ms,
                attempts=1,
                attempt_history=(attempt,),
                created_at=now,
                started_at=now,
                updated_at=now,
                checkpoint_at=now,
                completed_at=now,
            )
            job = GenerationJob(
                id=job_id,
                artifact_kind=candidate.artifact_kind,
                operation=_OPERATION_BY_MODE[candidate.mode],
                target_type=binding.target_type,
                target_id=binding.target_id,
                status=JobStatus.COMPLETED,
                route={
                    "requested_provider": requested_provider,
                    "requested_model": requested_model,
                    "provider": provider,
                    "model": model,
                    "fallback_used": fallback,
                },
                quality_profile="refinement",
                length_spec=length_spec,
                source_snapshot=base.snapshot,
                source_snapshot_sha256=sha256_json(base.snapshot),
                context_snapshot=context_snapshot,
                context_snapshot_sha256=sha256_json(candidate.context_snapshot),
                workflow_version="universal-refinement-v1",
                current_step_index=1,
                total_steps_hint=1,
                last_checkpoint_step_id=step_id,
                checkpoint_sequence=1,
                created_at=now,
                started_at=now,
                updated_at=now,
                heartbeat_at=now,
                last_checkpoint_at=now,
                completed_at=now,
            )
            prior = candidates.list_candidates(
                target_type=binding.target_type,
                target_id=binding.target_id,
            )
            parent = prior[0] if prior else None
            provenance = CandidateProvenance(
                job_id=job_id,
                step_id=step_id,
                workflow_version=job.workflow_version,
                template_id=candidate.template_id,
                template_version=candidate.template_version,
                requested_provider=requested_provider,
                requested_model=requested_model,
                provider=provider,
                model=model,
                fallback_used=fallback,
                attempt_number=attempt_number,
                input_snapshot_sha256=step.input_snapshot_sha256,
                output_sha256=content_hash,
            )
            durable = GenerationCandidate(
                id=candidate.id,
                job_id=job_id,
                producing_step_id=step_id,
                artifact_kind=candidate.artifact_kind,
                target_type=binding.target_type,
                target_id=binding.target_id,
                parent_candidate_id=parent.id if parent is not None else None,
                candidate_version=(parent.candidate_version + 1 if parent is not None else 1),
                original_version_ref=base.version_ref,
                base_snapshot=base.snapshot,
                base_snapshot_sha256=sha256_json(base.snapshot),
                content_text=candidate.candidate_text,
                content_json=content_snapshot,
                content_sha256=content_hash,
                quality_report={
                    **candidate.quality_report,
                    "_cwe_2_1_length_contract": length_spec,
                },
                provenance=provenance,
                created_at=now,
            )
            generation = CreativeGenerationRepository(session)
            generation.add_job(job)
            # These repositories intentionally expose bare ORM rows without
            # relationships. Flush each FK parent explicitly so SQLite never
            # guesses an unsafe insertion order inside this one transaction.
            session.flush()
            generation.add_step(step)
            session.flush()
            candidates.add_candidate(durable)
            session.flush()
            return durable

    def get_candidate(self, candidate_id: str) -> GenerationCandidate:
        with self._transaction() as session:
            candidate = CreativeCandidateRepository(session).get_candidate(candidate_id)
            if candidate is None:
                raise RefinementCandidateNotFoundError
            return candidate

    def list_history(
        self,
        binding: RefinementCandidateBinding,
    ) -> list[RefinementCandidateHistoryEntry]:
        if binding.target_id is None:
            return []
        with self._transaction() as session:
            repository = CreativeCandidateRepository(session)
            return [
                RefinementCandidateHistoryEntry(
                    candidate=candidate,
                    actions=tuple(repository.list_actions(candidate.id)),
                )
                for candidate in repository.list_candidates(
                    target_type=binding.target_type,
                    target_id=binding.target_id,
                )
            ]

    def _read_binding(
        self,
        session: Session,
        binding: RefinementCandidateBinding,
    ) -> VersionedTargetSnapshot:
        if binding.target_id is None or binding.target_type not in SUPPORTED_DRAFT_TARGETS:
            raise RefinementCandidateTargetRequiredError
        try:
            return self._target_adapter.read_snapshot(
                session,
                TargetSnapshotRequest(
                    target_type=binding.target_type,
                    target_id=binding.target_id,
                ),
            )
        except RefinementCandidatePersistenceError:
            raise
        except Exception:
            raise RefinementCandidateTargetStaleError from None

    def _assert_replay(
        self,
        session: Session,
        existing: GenerationCandidate,
        candidate: RefinementCandidate,
        binding: RefinementCandidateBinding,
    ) -> None:
        step = CreativeGenerationRepository(session).get_step(existing.producing_step_id)
        base_text = self._target_adapter.field_text(
            existing.base_snapshot,
            field_name=binding.field_name,
        )
        content = existing.content_json or {}
        candidate_text = self._target_adapter.field_text(
            content,
            field_name=binding.field_name,
        )
        if (
            existing.target_type != binding.target_type
            or existing.target_id != binding.target_id
            or base_text != candidate.original_text
            or candidate_text != candidate.candidate_text
            or existing.content_text != candidate.candidate_text
            or step is None
            or step.input_snapshot.get("intent_sha256") != self._intent_sha256(candidate, binding)
        ):
            raise RefinementCandidateConflictError

    @staticmethod
    def _assert_expected_snapshot(
        snapshot: dict[str, object],
        binding: RefinementCandidateBinding,
    ) -> None:
        expected = binding.expected_base_snapshot_sha256
        if expected is not None and sha256_json(snapshot) != expected:
            raise RefinementCandidateTargetStaleError

    @staticmethod
    def _intent_sha256(
        candidate: RefinementCandidate,
        binding: RefinementCandidateBinding,
    ) -> str:
        """Fingerprint every stable author/provider input used by persistence."""

        return sha256_json(
            {
                "binding": binding.model_dump(mode="json"),
                "candidate": candidate.model_dump(mode="json"),
            }
        )

    @staticmethod
    def _length_contract(candidate: RefinementCandidate) -> dict[str, object]:
        """Persist the exact author request and final candidate assessment."""

        intent_by_artifact = {
            ArtifactKind.STORY_FRAGMENT: GenerationIntent.PASSAGE,
            ArtifactKind.SHORT_STORY: GenerationIntent.SHORT_STORY,
            ArtifactKind.LONG_STORY: GenerationIntent.LONG_STORY,
            ArtifactKind.CHAPTER: GenerationIntent.CHAPTER,
            ArtifactKind.SCREENPLAY: GenerationIntent.SCREENPLAY,
            ArtifactKind.CHARACTER_CARD: GenerationIntent.CHARACTER_BACKSTORY,
            ArtifactKind.CHARACTER_BACKSTORY: GenerationIntent.CHARACTER_BACKSTORY,
            ArtifactKind.WORLD_SEED: GenerationIntent.IDEA,
            ArtifactKind.IMAGE_PROMPT: GenerationIntent.IDEA,
        }
        original_visible_chars = measure_output(
            candidate.original_text,
            unit=LengthUnit.VISIBLE_CHARS,
        ).visible_chars
        desired = candidate.desired_length
        minimum = desired.minimum_characters if desired is not None else None
        maximum = desired.maximum_characters if desired is not None else None
        requested_target = desired.target_characters if desired is not None else None
        target = requested_target or max(1, original_visible_chars)
        if minimum is not None:
            target = max(target, minimum)
        if maximum is not None:
            target = min(target, maximum)
        strictness = (
            LengthStrictness.BOUNDED
            if maximum is not None
            else LengthStrictness.MINIMUM_REQUIRED
            if minimum is not None
            else LengthStrictness.ADVISORY
        )
        spec = GenerationLengthSpec(
            unit=LengthUnit.VISIBLE_CHARS,
            minimum=minimum,
            target=target,
            maximum=maximum,
            segment_target=None,
            max_continuations=0,
            strictness=strictness,
        )
        measurement = measure_output(
            candidate.candidate_text,
            unit=LengthUnit.VISIBLE_CHARS,
        )
        assessment = measurement.assess(spec)
        return {
            "contract_version": "cwe-2.1-generation-length-spec-v1",
            "generation_intent": intent_by_artifact[candidate.artifact_kind].value,
            "generation_length_spec": spec.model_dump(mode="json"),
            "generation_length_spec_sha256": spec.sha256,
            "output_measurement": measurement.model_dump(mode="json"),
            "output_measurement_sha256": measurement.sha256,
            "final_assessment": assessment.model_dump(mode="json"),
            "final_assessment_sha256": sha256_json(assessment.model_dump(mode="json")),
            "refinement_desired_length": (
                None if desired is None else desired.model_dump(mode="json")
            ),
            "adapter_source": (
                "refinement_request_length_spec_v1"
                if desired is not None
                else "legacy_refinement_adapter_v1"
            ),
        }

    @staticmethod
    def _routes(
        candidate: RefinementCandidate,
    ) -> tuple[str, str, str, str, bool]:
        provenance = candidate.provenance
        provider = provenance.actual_provider_id or provenance.requested_provider_id or "offline"
        model = provenance.actual_model or provenance.requested_model or "offline-refinement"
        requested_provider = provenance.requested_provider_id or provider
        requested_model = provenance.requested_model or model
        fallback = bool(
            provenance.fallback_used and (requested_provider, requested_model) != (provider, model)
        )
        return requested_provider, requested_model, provider, model, fallback


__all__ = [
    "RefinementCandidateBinding",
    "RefinementCandidateConflictError",
    "RefinementCandidateHistoryEntry",
    "RefinementCandidateNotFoundError",
    "RefinementCandidatePersistenceError",
    "RefinementCandidatePersistenceService",
    "RefinementCandidateTargetRequiredError",
    "RefinementCandidateTargetStaleError",
]
