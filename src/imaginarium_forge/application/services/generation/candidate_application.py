"""Atomic, versioned application of durable creative candidates.

The target adapter is deliberately passed the SQLAlchemy ``Session`` owned by
this service. Adapter implementations must use that session and must not
commit or roll back it. Consequently a target version, the completed action
receipt, and the candidate status transition either commit together or are all
rolled back.

This module does not define a second formal-content aggregate. Adapters remain
responsible for the rules of Story, character, world, and draft targets.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from imaginarium_forge.domain.generation.durable import (
    CandidateAction,
    CandidateActionKind,
    CandidateActionStatus,
    CandidateStatus,
    GenerationCandidate,
    sha256_json,
    sha256_text,
)
from imaginarium_forge.infrastructure.db.repositories.creative_candidates import (
    CreativeCandidateRepository,
)


class VersionedTargetSnapshot(BaseModel):
    """A target's current immutable snapshot and version pointer."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    target_type: str
    target_id: str | None
    snapshot: dict[str, object]
    version_ref: str | None


class TargetSnapshotRequest(BaseModel):
    """Locator for the target state that an action expects to advance."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    target_type: str
    target_id: str | None
    version_ref_hint: str | None = None


class TargetVersionRequest(BaseModel):
    """Adapter command for creating, never overwriting, a formal version."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    candidate_id: str
    action_kind: CandidateActionKind
    idempotency_key: str
    target_type: str
    target_id: str | None
    selection: dict[str, object]
    candidate_text: str
    candidate_json: dict[str, object] | None
    before_snapshot: dict[str, object]
    expected_snapshot_sha256: str
    expected_version_ref: str | None
    restore_snapshot: dict[str, object] | None = None


class VersionedCandidateTargetAdapter(Protocol):
    """Bridge to an existing formal aggregate's version service.

    Both methods receive the service-owned session. ``create_version`` must
    append an immutable version and CAS the aggregate's current-version pointer
    against both expected fields. It must not call commit or rollback. A
    version race should raise:class:`TargetVersionConflict`.
    """

    def read_snapshot(
        self,
        session: Session,
        request: TargetSnapshotRequest,
    ) -> VersionedTargetSnapshot: ...

    def create_version(
        self,
        session: Session,
        request: TargetVersionRequest,
    ) -> VersionedTargetSnapshot: ...


class SessionProvider(Protocol):
    """Callable session source used by both sessionmaker and app lifecycle."""

    def __call__(self) -> Session: ...


class TargetVersionConflict(RuntimeError):
    """Safe adapter signal for an optimistic target-version race."""

    def __init__(self) -> None:
        super().__init__("The target changed before the new version could be saved.")


class CandidateApplicationError(RuntimeError):
    """Typed boundary error whose string is always safe for presentation."""

    def __init__(
        self,
        *,
        code: str,
        safe_message: str,
        retryable: bool = False,
        recordable: bool = False,
    ) -> None:
        super().__init__(safe_message)
        self.code = code
        self.safe_message = safe_message
        self.retryable = retryable
        self.recordable = recordable
        self.receipt: CandidateAction | None = None

    def attach_receipt(self, receipt: CandidateAction) -> CandidateApplicationError:
        self.receipt = receipt
        return self


class CandidateNotFoundError(CandidateApplicationError):
    def __init__(self) -> None:
        super().__init__(
            code="candidate_not_found",
            safe_message="The candidate is no longer available.",
        )


class CandidateInvalidRequestError(CandidateApplicationError):
    def __init__(self) -> None:
        super().__init__(
            code="candidate_invalid_request",
            safe_message="The candidate action request is invalid.",
        )


class CandidateStateConflictError(CandidateApplicationError):
    def __init__(self) -> None:
        super().__init__(
            code="candidate_state_conflict",
            safe_message="The candidate has already been decided.",
        )


class CandidateIdempotencyConflictError(CandidateApplicationError):
    def __init__(self) -> None:
        super().__init__(
            code="candidate_idempotency_conflict",
            safe_message="This action key was already used for a different request.",
        )


class CandidateTargetStaleError(CandidateApplicationError):
    def __init__(self) -> None:
        super().__init__(
            code="candidate_target_stale",
            safe_message="The original changed after this candidate was created.",
            recordable=True,
        )


class CandidateTargetUnavailableError(CandidateApplicationError):
    def __init__(self) -> None:
        super().__init__(
            code="candidate_target_unavailable",
            safe_message="The target could not be read safely.",
            retryable=True,
            recordable=True,
        )


class CandidateTargetWriteError(CandidateApplicationError):
    def __init__(self) -> None:
        super().__init__(
            code="candidate_target_write_failed",
            safe_message="The candidate was not applied; the original is unchanged.",
            retryable=True,
            recordable=True,
        )


class CandidateConcurrentUpdateError(CandidateApplicationError):
    def __init__(self) -> None:
        super().__init__(
            code="candidate_concurrent_update",
            safe_message="Another change won the race; the candidate was not applied.",
            retryable=True,
            recordable=True,
        )


class CandidateActionNotFoundError(CandidateApplicationError):
    def __init__(self) -> None:
        super().__init__(
            code="candidate_action_not_found",
            safe_message="The action to undo is no longer available.",
        )


class CandidateActionNotUndoableError(CandidateApplicationError):
    def __init__(self) -> None:
        super().__init__(
            code="candidate_action_not_undoable",
            safe_message="That action cannot be undone.",
        )


class CandidateActionAlreadyUndoneError(CandidateApplicationError):
    def __init__(self) -> None:
        super().__init__(
            code="candidate_action_already_undone",
            safe_message="That action has already been undone.",
        )


class CandidatePersistenceError(CandidateApplicationError):
    def __init__(self) -> None:
        super().__init__(
            code="candidate_persistence_failed",
            safe_message="The candidate action could not be recorded safely.",
            retryable=True,
        )


@dataclass(frozen=True)
class _ActionIntent:
    candidate: GenerationCandidate
    action_kind: CandidateActionKind
    idempotency_key: str
    selection: dict[str, object]
    undo_of_action_id: str | None
    expected_snapshot: dict[str, object]
    expected_snapshot_sha256: str
    expected_version_ref: str | None
    locator: TargetSnapshotRequest
    restore_snapshot: dict[str, object] | None


@dataclass(frozen=True)
class _FailureContext:
    intent: _ActionIntent
    target_id: str | None
    before_snapshot: dict[str, object]
    base_version_ref: str | None


Clock = Callable[[], str]


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


class CandidateApplicationService:
    """Apply candidate decisions with durable receipts and optimistic guards."""

    def __init__(
        self,
        session_factory: SessionProvider,
        target_adapter: VersionedCandidateTargetAdapter,
        *,
        clock: Clock = _utc_now,
    ) -> None:
        self._session_provider = session_factory
        self._target_adapter = target_adapter
        self._clock = clock

    @contextmanager
    def _transaction(self) -> Iterator[Session]:
        """Own one short transaction and always release its session."""

        session = self._session_provider()
        try:
            with session.begin():
                yield session
        finally:
            session.close()

    def list_actions(self, candidate_id: str) -> tuple[CandidateAction, ...]:
        """Read persistent action history without mutating a candidate or target."""
        try:
            with self._transaction() as session:
                repository = CreativeCandidateRepository(session)
                if repository.get_candidate(candidate_id) is None:
                    raise CandidateNotFoundError
                return tuple(repository.list_actions(candidate_id))
        except CandidateApplicationError:
            raise
        except Exception:
            raise CandidatePersistenceError from None

    def apply_all(self, candidate_id: str, *, idempotency_key: str) -> CandidateAction:
        return self._apply(
            candidate_id=candidate_id,
            action_kind=CandidateActionKind.APPLY_ALL,
            idempotency_key=idempotency_key,
            selection={},
        )

    def apply_field(
        self,
        candidate_id: str,
        *,
        field_name: str,
        idempotency_key: str,
    ) -> CandidateAction:
        normalized = field_name.strip()
        if not normalized or len(normalized) > 200:
            raise CandidateInvalidRequestError
        return self._apply(
            candidate_id=candidate_id,
            action_kind=CandidateActionKind.APPLY_FIELD,
            idempotency_key=idempotency_key,
            selection={"field": normalized},
        )

    def apply_selection(
        self,
        candidate_id: str,
        *,
        selection: Mapping[str, object],
        idempotency_key: str,
    ) -> CandidateAction:
        normalized = dict(selection)
        if not normalized:
            raise CandidateInvalidRequestError
        return self._apply(
            candidate_id=candidate_id,
            action_kind=CandidateActionKind.APPLY_SELECTION,
            idempotency_key=idempotency_key,
            selection=normalized,
        )

    def save_as_new(self, candidate_id: str, *, idempotency_key: str) -> CandidateAction:
        return self._apply(
            candidate_id=candidate_id,
            action_kind=CandidateActionKind.SAVE_AS_NEW,
            idempotency_key=idempotency_key,
            selection={},
        )

    def reject(self, candidate_id: str) -> GenerationCandidate:
        """Reject a proposal idempotently without creating a target action row.

        The current schema intentionally has no ``reject`` action kind; the
        candidate's CAS-protected decision is therefore the durable receipt.
        """

        try:
            with self._transaction() as session:
                repository = CreativeCandidateRepository(session)
                candidate = repository.get_candidate(candidate_id)
                if candidate is None:
                    raise CandidateNotFoundError
                if candidate.status is CandidateStatus.REJECTED:
                    return candidate
                if candidate.status is not CandidateStatus.PROPOSED:
                    raise CandidateStateConflictError
                rejected = GenerationCandidate.model_validate(
                    {
                        **candidate.model_dump(mode="json"),
                        "status": CandidateStatus.REJECTED,
                        "state_version": candidate.state_version + 1,
                        "decided_at": self._clock(),
                    }
                )
                if not repository.update_candidate_cas(
                    rejected,
                    expected_state_version=candidate.state_version,
                ):
                    raise CandidateConcurrentUpdateError
                return rejected
        except CandidateApplicationError:
            raise
        except Exception:
            raise CandidatePersistenceError from None

    def undo(
        self,
        candidate_id: str,
        *,
        action_id: str,
        idempotency_key: str,
    ) -> CandidateAction:
        return self._apply(
            candidate_id=candidate_id,
            action_kind=CandidateActionKind.UNDO,
            idempotency_key=idempotency_key,
            selection={},
            undo_of_action_id=action_id,
        )

    def _apply(
        self,
        *,
        candidate_id: str,
        action_kind: CandidateActionKind,
        idempotency_key: str,
        selection: dict[str, object],
        undo_of_action_id: str | None = None,
    ) -> CandidateAction:
        failure_context: _FailureContext | None = None
        try:
            with self._transaction() as session:
                repository = CreativeCandidateRepository(session)
                candidate = repository.get_candidate(candidate_id)
                if candidate is None:
                    raise CandidateNotFoundError
                intent = self._build_intent(
                    repository,
                    candidate=candidate,
                    action_kind=action_kind,
                    idempotency_key=idempotency_key,
                    selection=selection,
                    undo_of_action_id=undo_of_action_id,
                )
                existing = repository.get_action_by_idempotency_key(idempotency_key)
                if existing is not None:
                    self._assert_same_intent(existing, intent)
                    if existing.status is CandidateActionStatus.COMPLETED:
                        return existing
                    if existing.status is CandidateActionStatus.FAILED:
                        raise self._error_from_failed_action(existing)

                self._require_candidate_state(candidate, action_kind)
                failure_context = _FailureContext(
                    intent=intent,
                    target_id=intent.locator.target_id,
                    before_snapshot=deepcopy(intent.expected_snapshot),
                    base_version_ref=intent.expected_version_ref,
                )
                current = self._read_current(session, intent)
                failure_context = _FailureContext(
                    intent=intent,
                    target_id=current.target_id,
                    before_snapshot=deepcopy(current.snapshot),
                    base_version_ref=current.version_ref,
                )
                if (
                    sha256_json(current.snapshot) != intent.expected_snapshot_sha256
                    or current.version_ref != intent.expected_version_ref
                ):
                    raise CandidateTargetStaleError
                if existing is not None and (
                    existing.target_id != current.target_id
                    or existing.before_snapshot_sha256 != sha256_json(current.snapshot)
                    or existing.base_version_ref != current.version_ref
                ):
                    raise CandidateTargetStaleError

                pending = self._pending_action(intent, current)
                if existing is None:
                    repository.add_action(pending)
                    session.flush()
                else:
                    pending = existing

                applied = self._create_target_version(session, intent, current)
                completed = self._completed_action(pending, applied)
                if not repository.complete_action_once(completed):
                    raise CandidateConcurrentUpdateError
                decided = self._decided_candidate(candidate, action_kind, completed.completed_at)
                if not repository.update_candidate_cas(
                    decided,
                    expected_state_version=candidate.state_version,
                ):
                    raise CandidateConcurrentUpdateError
                return completed
        except CandidateApplicationError as error:
            if not error.recordable or failure_context is None:
                raise
            receipt = self._persist_failed_action(failure_context, error)
            if receipt.status is CandidateActionStatus.COMPLETED:
                return receipt
            raise self._error_from_failed_action(receipt) from None
        except Exception:
            unexpected_error = CandidateTargetWriteError()
            if failure_context is None:
                raise CandidatePersistenceError from None
            receipt = self._persist_failed_action(failure_context, unexpected_error)
            if receipt.status is CandidateActionStatus.COMPLETED:
                return receipt
            raise self._error_from_failed_action(receipt) from None

    def _build_intent(
        self,
        repository: CreativeCandidateRepository,
        *,
        candidate: GenerationCandidate,
        action_kind: CandidateActionKind,
        idempotency_key: str,
        selection: dict[str, object],
        undo_of_action_id: str | None,
    ) -> _ActionIntent:
        if action_kind is not CandidateActionKind.UNDO:
            return _ActionIntent(
                candidate=candidate,
                action_kind=action_kind,
                idempotency_key=idempotency_key,
                selection=deepcopy(selection),
                undo_of_action_id=None,
                expected_snapshot=deepcopy(candidate.base_snapshot),
                expected_snapshot_sha256=candidate.base_snapshot_sha256,
                expected_version_ref=candidate.original_version_ref,
                locator=TargetSnapshotRequest(
                    target_type=candidate.target_type,
                    target_id=candidate.target_id,
                    version_ref_hint=candidate.original_version_ref,
                ),
                restore_snapshot=None,
            )
        if undo_of_action_id is None:
            raise CandidateInvalidRequestError
        original = repository.get_action(undo_of_action_id)
        if original is None or original.candidate_id != candidate.id:
            raise CandidateActionNotFoundError
        if (
            original.status is not CandidateActionStatus.COMPLETED
            or original.action_kind is CandidateActionKind.UNDO
            or original.after_snapshot is None
            or original.result_version_ref is None
        ):
            raise CandidateActionNotUndoableError
        already_undone = any(
            action.action_kind is CandidateActionKind.UNDO
            and action.status is CandidateActionStatus.COMPLETED
            and action.undo_of_action_id == original.id
            for action in repository.list_actions(candidate.id)
        )
        if already_undone:
            existing = repository.get_action_by_idempotency_key(idempotency_key)
            if existing is None:
                raise CandidateActionAlreadyUndoneError
        return _ActionIntent(
            candidate=candidate,
            action_kind=action_kind,
            idempotency_key=idempotency_key,
            selection={},
            undo_of_action_id=original.id,
            expected_snapshot=deepcopy(original.after_snapshot),
            expected_snapshot_sha256=original.after_snapshot_sha256,
            expected_version_ref=original.result_version_ref,
            locator=TargetSnapshotRequest(
                target_type=original.target_type,
                target_id=original.target_id,
                version_ref_hint=original.result_version_ref,
            ),
            restore_snapshot=deepcopy(original.before_snapshot),
        )

    def _read_current(
        self,
        session: Session,
        intent: _ActionIntent,
    ) -> VersionedTargetSnapshot:
        try:
            current = self._target_adapter.read_snapshot(session, intent.locator)
            if not isinstance(current, VersionedTargetSnapshot):
                raise TypeError
            if current.target_type != intent.locator.target_type:
                raise TypeError
            return current
        except TargetVersionConflict:
            raise CandidateTargetStaleError from None
        except Exception:
            raise CandidateTargetUnavailableError from None

    def _create_target_version(
        self,
        session: Session,
        intent: _ActionIntent,
        current: VersionedTargetSnapshot,
    ) -> VersionedTargetSnapshot:
        request = TargetVersionRequest(
            candidate_id=intent.candidate.id,
            action_kind=intent.action_kind,
            idempotency_key=intent.idempotency_key,
            target_type=intent.locator.target_type,
            target_id=current.target_id,
            selection=deepcopy(intent.selection),
            candidate_text=intent.candidate.content_text,
            candidate_json=deepcopy(intent.candidate.content_json),
            before_snapshot=deepcopy(current.snapshot),
            expected_snapshot_sha256=intent.expected_snapshot_sha256,
            expected_version_ref=intent.expected_version_ref,
            restore_snapshot=deepcopy(intent.restore_snapshot),
        )
        try:
            result = self._target_adapter.create_version(session, request)
            if not isinstance(result, VersionedTargetSnapshot):
                raise TypeError
            if result.target_type != request.target_type or not result.version_ref:
                raise TypeError
            if (
                intent.action_kind is not CandidateActionKind.SAVE_AS_NEW
                and result.target_id != current.target_id
            ):
                raise TypeError
            if result.version_ref == current.version_ref:
                raise TypeError
            return result
        except TargetVersionConflict:
            raise CandidateTargetStaleError from None
        except CandidateApplicationError:
            raise
        except Exception:
            raise CandidateTargetWriteError from None

    def _pending_action(
        self,
        intent: _ActionIntent,
        current: VersionedTargetSnapshot,
    ) -> CandidateAction:
        try:
            return CandidateAction(
                id=f"candidate-action:{sha256_text(intent.idempotency_key)[:40]}",
                candidate_id=intent.candidate.id,
                action_kind=intent.action_kind,
                status=CandidateActionStatus.PENDING,
                idempotency_key=intent.idempotency_key,
                target_type=intent.locator.target_type,
                target_id=current.target_id,
                selection=deepcopy(intent.selection),
                before_snapshot=deepcopy(current.snapshot),
                before_snapshot_sha256=sha256_json(current.snapshot),
                base_version_ref=current.version_ref,
                undo_of_action_id=intent.undo_of_action_id,
                created_at=self._clock(),
            )
        except Exception:
            raise CandidateInvalidRequestError from None

    def _completed_action(
        self,
        pending: CandidateAction,
        applied: VersionedTargetSnapshot,
    ) -> CandidateAction:
        try:
            return CandidateAction.model_validate(
                {
                    **pending.model_dump(mode="json"),
                    "status": CandidateActionStatus.COMPLETED,
                    "after_snapshot": deepcopy(applied.snapshot),
                    "after_snapshot_sha256": sha256_json(applied.snapshot),
                    "result_version_ref": applied.version_ref,
                    "completed_at": self._clock(),
                }
            )
        except Exception:
            raise CandidateTargetWriteError from None

    def _decided_candidate(
        self,
        candidate: GenerationCandidate,
        action_kind: CandidateActionKind,
        completed_at: str | None,
    ) -> GenerationCandidate:
        target_status = (
            CandidateStatus.ARCHIVED
            if action_kind is CandidateActionKind.UNDO
            else CandidateStatus.APPLIED
        )
        return GenerationCandidate.model_validate(
            {
                **candidate.model_dump(mode="json"),
                "status": target_status,
                "state_version": candidate.state_version + 1,
                "decided_at": candidate.decided_at or completed_at or self._clock(),
            }
        )

    def _persist_failed_action(
        self,
        context: _FailureContext,
        error: CandidateApplicationError,
    ) -> CandidateAction:
        try:
            with self._transaction() as session:
                repository = CreativeCandidateRepository(session)
                existing = repository.get_action_by_idempotency_key(context.intent.idempotency_key)
                if existing is not None:
                    self._assert_same_intent(existing, context.intent)
                    if existing.status is not CandidateActionStatus.PENDING:
                        return existing
                    pending = existing
                else:
                    current = VersionedTargetSnapshot(
                        target_type=context.intent.locator.target_type,
                        target_id=context.target_id,
                        snapshot=deepcopy(context.before_snapshot),
                        version_ref=context.base_version_ref,
                    )
                    pending = self._pending_action(context.intent, current)
                    repository.add_action(pending)
                    session.flush()
                failed = CandidateAction.model_validate(
                    {
                        **pending.model_dump(mode="json"),
                        "status": CandidateActionStatus.FAILED,
                        "error_code": error.code,
                        "safe_error_message": error.safe_message,
                        "completed_at": self._clock(),
                    }
                )
                if not repository.complete_action_once(failed):
                    terminal = repository.get_action(pending.id)
                    if terminal is None or terminal.status is CandidateActionStatus.PENDING:
                        raise CandidateConcurrentUpdateError
                    return terminal
                return failed
        except CandidateApplicationError:
            raise
        except Exception:
            raise CandidatePersistenceError from None

    @staticmethod
    def _require_candidate_state(
        candidate: GenerationCandidate,
        action_kind: CandidateActionKind,
    ) -> None:
        required = (
            CandidateStatus.APPLIED
            if action_kind is CandidateActionKind.UNDO
            else CandidateStatus.PROPOSED
        )
        if candidate.status is not required:
            raise CandidateStateConflictError

    @staticmethod
    def _assert_same_intent(action: CandidateAction, intent: _ActionIntent) -> None:
        if (
            action.candidate_id != intent.candidate.id
            or action.action_kind is not intent.action_kind
            or action.target_type != intent.locator.target_type
            or action.selection != intent.selection
            or action.undo_of_action_id != intent.undo_of_action_id
        ):
            raise CandidateIdempotencyConflictError

    @staticmethod
    def _error_from_failed_action(action: CandidateAction) -> CandidateApplicationError:
        factories: dict[str, Callable[[], CandidateApplicationError]] = {
            "candidate_target_stale": CandidateTargetStaleError,
            "candidate_target_unavailable": CandidateTargetUnavailableError,
            "candidate_target_write_failed": CandidateTargetWriteError,
            "candidate_concurrent_update": CandidateConcurrentUpdateError,
        }
        factory = factories.get(action.error_code, CandidateTargetWriteError)
        return factory().attach_receipt(action)


__all__ = [
    "CandidateActionAlreadyUndoneError",
    "CandidateActionNotFoundError",
    "CandidateActionNotUndoableError",
    "CandidateApplicationError",
    "CandidateApplicationService",
    "CandidateConcurrentUpdateError",
    "CandidateIdempotencyConflictError",
    "CandidateInvalidRequestError",
    "CandidateNotFoundError",
    "CandidatePersistenceError",
    "CandidateStateConflictError",
    "CandidateTargetStaleError",
    "CandidateTargetUnavailableError",
    "CandidateTargetWriteError",
    "SessionProvider",
    "TargetSnapshotRequest",
    "TargetVersionConflict",
    "TargetVersionRequest",
    "VersionedCandidateTargetAdapter",
    "VersionedTargetSnapshot",
]
