"""Transactional autosave and crash recovery for Prompt Scratch only."""

from __future__ import annotations

from datetime import datetime, timedelta

from pydantic import ValidationError
from sqlalchemy.orm import Session

from imaginarium_forge.application.errors import (
    ApplicationError,
    ConflictError,
    NotFoundError,
    ValidationFailedError,
)
from imaginarium_forge.application.services.base import ServiceBase
from imaginarium_forge.domain.character.gender import CharacterGender
from imaginarium_forge.domain.common.enums import RecordStatus
from imaginarium_forge.domain.common.ids import new_id, utc_now_iso
from imaginarium_forge.domain.prompt.scratch_draft import (
    PromptScratchDraft,
    PromptScratchEditorKind,
)
from imaginarium_forge.domain.prompt.scratch_recovery import (
    PromptScratchActionablePage,
    PromptScratchCommitResult,
    PromptScratchCommitStatus,
    PromptScratchConflictReason,
    PromptScratchRecoveryIntent,
    PromptScratchRecoveryJournal,
    PromptScratchRecoveryPayload,
    PromptScratchRecoveryState,
    PromptScratchResolutionKind,
)
from imaginarium_forge.infrastructure.db.repositories.projects import ProjectRepository
from imaginarium_forge.infrastructure.db.repositories.prompt_scratch_drafts import (
    PromptScratchDraftRepository,
)
from imaginarium_forge.infrastructure.db.repositories.prompt_scratch_recovery import (
    PromptScratchRecoveryRepository,
)


def _validation_failure(exc: ValidationError) -> ValidationFailedError:
    errors = exc.errors()
    message = str(errors[0].get("msg", exc)) if errors else str(exc)
    return ValidationFailedError(message)


def _next_updated_at(base_updated_at: str) -> str:
    """Return a token that advances even under a frozen or reversing clock."""

    candidate = utc_now_iso()
    try:
        base = datetime.fromisoformat(base_updated_at)
        current = datetime.fromisoformat(candidate)
    except ValueError:
        return candidate if candidate != base_updated_at else f"{base_updated_at}:next"
    if current <= base:
        return (base + timedelta(microseconds=1)).isoformat()
    return candidate


def _require_project(session: Session, project_id: str | None) -> None:
    if project_id is not None and ProjectRepository(session).get(project_id) is None:
        raise NotFoundError(f"找不到專案：{project_id}")


class PromptScratchAutosaveService(ServiceBase):
    """Persist raw recovery first, then consume it with guarded formal writes."""

    def capture_recovery(
        self,
        *,
        journal_id: str | None = None,
        expected_sequence: int | None = None,
        intent: PromptScratchRecoveryIntent | str,
        existing_draft_id: str | None = None,
        reserved_draft_id: str | None = None,
        base_updated_at: str | None = None,
        project_id: str | None = None,
        link_project: bool = False,
        editor_kind: PromptScratchEditorKind | str = PromptScratchEditorKind.CHARACTER,
        gender: CharacterGender | str = CharacterGender.FEMALE,
        title: str = "",
        character_name: str = "",
        character_image_prompt_en: str = "",
        background_image_prompt_en: str = "",
        notes: str = "",
    ) -> PromptScratchRecoveryJournal:
        try:
            parsed_intent = PromptScratchRecoveryIntent(intent)
            payload = PromptScratchRecoveryPayload(
                kind=PromptScratchEditorKind(editor_kind),
                gender=CharacterGender(gender),
                title=title,
                character_name=character_name,
                character_image_prompt_en=character_image_prompt_en,
                background_image_prompt_en=background_image_prompt_en,
                notes=notes,
                link_project=link_project,
                project_id=project_id,
            )
        except (ValidationError, ValueError) as exc:
            if isinstance(exc, ValidationError):
                raise _validation_failure(exc) from exc
            raise ValidationFailedError(str(exc)) from exc

        if parsed_intent is PromptScratchRecoveryIntent.NEW:
            if existing_draft_id is not None or base_updated_at is not None:
                raise ValidationFailedError("新草稿不可帶 existing_draft_id 或 base_updated_at")
            reserved_draft_id = reserved_draft_id or new_id()
        else:
            if not existing_draft_id or reserved_draft_id is not None or base_updated_at is None:
                raise ValidationFailedError(
                    "既有草稿需要 existing_draft_id、base_updated_at，且不可帶 reserved_draft_id"
                )

        journal_id = journal_id or new_id()
        now = utc_now_iso()
        if expected_sequence is None:
            journal = PromptScratchRecoveryJournal(
                id=journal_id,
                intent=parsed_intent,
                existing_draft_id=existing_draft_id,
                reserved_draft_id=reserved_draft_id,
                base_updated_at=base_updated_at,
                payload=payload,
                receipt_kind=payload.kind,
                receipt_gender=payload.gender,
                payload_sha256=payload.sha256(),
                sequence=1,
                state=PromptScratchRecoveryState.PENDING,
                created_at=now,
                updated_at=now,
            )
            with self._transaction() as session:
                repository = PromptScratchRecoveryRepository(session)
                inserted = repository.insert_pending_if_absent(journal)
                current = repository.get(journal_id)
                if current is None:
                    raise ApplicationError("Prompt Scratch 復原資料建立後無法讀回")
                if not inserted and not self._same_initial_capture(current, journal):
                    raise ConflictError("相同 recovery journal id 已綁定不同內容或目標")
                return current

        if expected_sequence < 1:
            raise ValidationFailedError("expected_sequence 必須大於零")
        with self._transaction() as session:
            repository = PromptScratchRecoveryRepository(session)
            changed = repository.capture_cas(
                journal_id=journal_id,
                expected_sequence=expected_sequence,
                intent=parsed_intent.value,
                existing_draft_id=existing_draft_id,
                reserved_draft_id=reserved_draft_id,
                base_updated_at=base_updated_at,
                payload=payload,
                updated_at=now,
            )
            current = repository.get(journal_id)
            if current is None:
                raise NotFoundError(f"找不到 Prompt Scratch recovery journal：{journal_id}")
            self._require_immutable_binding(
                current,
                intent=parsed_intent,
                existing_draft_id=existing_draft_id,
                reserved_draft_id=reserved_draft_id,
                base_updated_at=base_updated_at,
            )
            if changed:
                return current
            if (
                current.state is PromptScratchRecoveryState.PENDING
                and current.payload_sha256 == payload.sha256()
                and current.sequence in {expected_sequence, expected_sequence + 1}
            ):
                return current
            # A newer callback or terminal receipt always wins; callers rotate
            # a new journal after a committed receipt instead of reviving it.
            return current

    def commit_recovery(
        self,
        journal_id: str,
        *,
        expected_sequence: int,
        expected_hash: str,
    ) -> PromptScratchCommitResult:
        with self._transaction() as session:
            recovery = PromptScratchRecoveryRepository(session)
            claimed = recovery.claim(
                journal_id,
                expected_sequence=expected_sequence,
                expected_hash=expected_hash,
                allowed_states=(PromptScratchRecoveryState.PENDING,),
            )
            current = recovery.get(journal_id)
            if current is None:
                raise NotFoundError(f"找不到 Prompt Scratch recovery journal：{journal_id}")
            if not claimed:
                return self._retry_or_stale(
                    current,
                    expected_sequence=expected_sequence,
                    expected_hash=expected_hash,
                )
            if current.payload is None:
                raise ValidationFailedError("Prompt Scratch 復原資料沒有可提交內容")
            payload = current.payload
            if payload.sha256() != expected_hash:
                raise ValidationFailedError("Prompt Scratch 復原資料完整性檢查失敗")

            drafts = PromptScratchDraftRepository(session)
            if current.intent is PromptScratchRecoveryIntent.NEW:
                target_id = current.reserved_draft_id or ""
                if drafts.get(target_id) is not None:
                    return self._mark_conflict(
                        recovery,
                        current,
                        PromptScratchConflictReason.TARGET_ID_TAKEN,
                    )
                saved = self._new_formal_draft(target_id, current, payload)
                _require_project(session, saved.project_id)
                drafts.add(saved)
                session.flush()
            else:
                target_id = current.existing_draft_id or ""
                formal = drafts.get(target_id)
                if formal is None:
                    return self._mark_conflict(
                        recovery,
                        current,
                        PromptScratchConflictReason.TARGET_MISSING,
                    )
                if formal.status is RecordStatus.ARCHIVED:
                    return self._mark_conflict(
                        recovery,
                        current,
                        PromptScratchConflictReason.TARGET_ARCHIVED,
                    )
                if formal.updated_at != current.base_updated_at:
                    return self._mark_conflict(
                        recovery,
                        current,
                        PromptScratchConflictReason.STALE_REVISION,
                    )
                saved = self._revised_formal_draft(formal, payload)
                _require_project(session, saved.project_id)
                persisted = saved.model_dump(mode="json")
                for field in ("id", "created_at", "status"):
                    persisted.pop(field)
                if not drafts.update_active_cas(
                    target_id,
                    expected_updated_at=current.base_updated_at or "",
                    fields=persisted,
                ):
                    latest = drafts.get(target_id)
                    reason = (
                        PromptScratchConflictReason.TARGET_MISSING
                        if latest is None
                        else PromptScratchConflictReason.TARGET_ARCHIVED
                        if latest.status is RecordStatus.ARCHIVED
                        else PromptScratchConflictReason.STALE_REVISION
                    )
                    return self._mark_conflict(recovery, current, reason)

            resolved_at = utc_now_iso()
            if not recovery.mark_committed(
                journal_id,
                source_state=PromptScratchRecoveryState.PENDING,
                expected_sequence=expected_sequence,
                expected_hash=expected_hash,
                resolution_kind=PromptScratchResolutionKind.TARGET,
                draft_id=saved.id,
                draft_updated_at=saved.updated_at,
                resolved_at=resolved_at,
            ):
                raise ApplicationError("正式草稿已寫入，但 recovery receipt 無法原子完成")
            receipt = recovery.get(journal_id)
            if receipt is None:
                raise ApplicationError("recovery receipt 完成後無法讀回")
            return PromptScratchCommitResult(
                status=PromptScratchCommitStatus.SAVED,
                journal=receipt,
                saved_draft_id=saved.id,
                saved_updated_at=saved.updated_at,
                draft=saved,
            )

    def save_recovery_as_new(
        self,
        journal_id: str,
        *,
        expected_sequence: int,
        expected_hash: str,
    ) -> PromptScratchCommitResult:
        with self._transaction() as session:
            recovery = PromptScratchRecoveryRepository(session)
            claimed = recovery.claim(
                journal_id,
                expected_sequence=expected_sequence,
                expected_hash=expected_hash,
                allowed_states=(
                    PromptScratchRecoveryState.PENDING,
                    PromptScratchRecoveryState.CONFLICT,
                ),
            )
            current = recovery.get(journal_id)
            if current is None:
                raise NotFoundError(f"找不到 Prompt Scratch recovery journal：{journal_id}")
            if not claimed:
                return self._retry_or_stale(
                    current,
                    expected_sequence=expected_sequence,
                    expected_hash=expected_hash,
                )
            if current.payload is None or current.payload.sha256() != expected_hash:
                raise ValidationFailedError("Prompt Scratch 復原資料完整性檢查失敗")

            drafts = PromptScratchDraftRepository(session)
            for _ in range(16):
                draft_id = new_id()
                if draft_id != current.target_draft_id and drafts.get(draft_id) is None:
                    break
            else:
                raise ApplicationError("無法分配不衝突的另存新檔 ID")
            saved = self._new_formal_draft(draft_id, current, current.payload)
            _require_project(session, saved.project_id)
            drafts.add(saved)
            session.flush()
            resolved_at = utc_now_iso()
            if not recovery.mark_committed(
                journal_id,
                source_state=current.state,
                expected_sequence=expected_sequence,
                expected_hash=expected_hash,
                resolution_kind=PromptScratchResolutionKind.SAVE_AS_NEW,
                draft_id=saved.id,
                draft_updated_at=saved.updated_at,
                resolved_at=resolved_at,
            ):
                raise ApplicationError("另存新草稿後 recovery receipt 無法原子完成")
            receipt = recovery.get(journal_id)
            if receipt is None:
                raise ApplicationError("recovery receipt 完成後無法讀回")
            return PromptScratchCommitResult(
                status=PromptScratchCommitStatus.SAVED,
                journal=receipt,
                saved_draft_id=saved.id,
                saved_updated_at=saved.updated_at,
                draft=saved,
            )

    def discard_recovery(
        self,
        journal_id: str,
        *,
        expected_sequence: int,
        confirm: bool,
    ) -> PromptScratchRecoveryJournal:
        if not confirm:
            raise ValidationFailedError("放棄復原資料必須明確確認")
        with self._transaction() as session:
            repository = PromptScratchRecoveryRepository(session)
            changed = repository.mark_discarded(
                journal_id,
                expected_sequence=expected_sequence,
                resolved_at=utc_now_iso(),
            )
            current = repository.get(journal_id)
            if current is None:
                raise NotFoundError(f"找不到 Prompt Scratch recovery journal：{journal_id}")
            if changed:
                return current
            if (
                current.state is PromptScratchRecoveryState.DISCARDED
                and current.resolved_from_sequence == expected_sequence
            ):
                return current
            raise ConflictError("復原資料已被較新的編輯或狀態取代，未執行放棄")

    def get_recovery(self, journal_id: str) -> PromptScratchRecoveryJournal:
        try:
            journal = self._read_only(
                lambda session: PromptScratchRecoveryRepository(session).get(journal_id)
            )
        except (ValidationError, ValueError) as exc:
            raise ValidationFailedError("Prompt Scratch 復原資料完整性檢查失敗") from exc
        if journal is None:
            raise NotFoundError(f"找不到 Prompt Scratch recovery journal：{journal_id}")
        return journal

    def list_actionable(self, *, limit: int = 100) -> PromptScratchActionablePage:
        if not 1 <= limit <= 500:
            raise ValidationFailedError("limit 必須介於 1 與 500")

        def read(session: Session) -> PromptScratchActionablePage:
            repository = PromptScratchRecoveryRepository(session)
            total = repository.count_actionable()
            items = tuple(repository.list_actionable(limit=limit))
            return PromptScratchActionablePage(
                items=items,
                total=total,
                has_more=total > len(items),
            )

        try:
            return self._read_only(read)
        except (ValidationError, ValueError) as exc:
            raise ValidationFailedError("Prompt Scratch 復原資料完整性檢查失敗") from exc

    @staticmethod
    def _same_initial_capture(
        current: PromptScratchRecoveryJournal,
        requested: PromptScratchRecoveryJournal,
    ) -> bool:
        return (
            current.state is PromptScratchRecoveryState.PENDING
            and current.sequence == 1
            and current.intent is requested.intent
            and current.existing_draft_id == requested.existing_draft_id
            and current.reserved_draft_id == requested.reserved_draft_id
            and current.base_updated_at == requested.base_updated_at
            and current.payload_sha256 == requested.payload_sha256
        )

    @staticmethod
    def _require_immutable_binding(
        current: PromptScratchRecoveryJournal,
        *,
        intent: PromptScratchRecoveryIntent,
        existing_draft_id: str | None,
        reserved_draft_id: str | None,
        base_updated_at: str | None,
    ) -> None:
        if (
            current.intent is not intent
            or current.existing_draft_id != existing_draft_id
            or current.reserved_draft_id != reserved_draft_id
            or current.base_updated_at != base_updated_at
        ):
            raise ConflictError("recovery intent、target 與 base revision 在 capture 後不可變更")

    @staticmethod
    def _new_formal_draft(
        draft_id: str,
        journal: PromptScratchRecoveryJournal,
        payload: PromptScratchRecoveryPayload,
    ) -> PromptScratchDraft:
        try:
            return PromptScratchDraft.model_validate(
                {
                    "id": draft_id,
                    "project_id": payload.project_id if payload.link_project else None,
                    "title": payload.title,
                    "character_name": payload.character_name,
                    "character_image_prompt_en": payload.character_image_prompt_en,
                    "background_image_prompt_en": payload.background_image_prompt_en,
                    "notes": payload.notes,
                    "editor_kind": payload.kind,
                    "editor_gender": payload.gender,
                    "created_at": journal.created_at,
                    "updated_at": utc_now_iso(),
                }
            )
        except ValidationError as exc:
            raise _validation_failure(exc) from exc

    @staticmethod
    def _revised_formal_draft(
        formal: PromptScratchDraft,
        payload: PromptScratchRecoveryPayload,
    ) -> PromptScratchDraft:
        try:
            return PromptScratchDraft.model_validate(
                {
                    **formal.model_dump(mode="python"),
                    "project_id": payload.project_id if payload.link_project else None,
                    "title": payload.title,
                    "character_name": payload.character_name,
                    "character_image_prompt_en": payload.character_image_prompt_en,
                    "background_image_prompt_en": payload.background_image_prompt_en,
                    "notes": payload.notes,
                    "editor_kind": payload.kind,
                    "editor_gender": payload.gender,
                    "updated_at": _next_updated_at(formal.updated_at),
                }
            )
        except ValidationError as exc:
            raise _validation_failure(exc) from exc

    @staticmethod
    def _mark_conflict(
        repository: PromptScratchRecoveryRepository,
        journal: PromptScratchRecoveryJournal,
        reason: PromptScratchConflictReason,
    ) -> PromptScratchCommitResult:
        resolved_at = utc_now_iso()
        if not repository.mark_conflict(
            journal.id,
            expected_sequence=journal.sequence,
            expected_hash=journal.payload_sha256,
            reason=reason,
            resolved_at=resolved_at,
        ):
            raise ApplicationError("formal CAS 失敗後無法原子保存 conflict receipt")
        receipt = repository.get(journal.id)
        if receipt is None:
            raise ApplicationError("conflict receipt 完成後無法讀回")
        return PromptScratchCommitResult(
            status=PromptScratchCommitStatus.CONFLICT,
            journal=receipt,
            reason=reason,
        )

    @staticmethod
    def _retry_or_stale(
        journal: PromptScratchRecoveryJournal,
        *,
        expected_sequence: int,
        expected_hash: str,
    ) -> PromptScratchCommitResult:
        if (
            journal.resolved_from_sequence == expected_sequence
            and journal.payload_sha256 == expected_hash
        ):
            if journal.state is PromptScratchRecoveryState.COMMITTED:
                return PromptScratchCommitResult(
                    status=PromptScratchCommitStatus.ALREADY_SAVED,
                    journal=journal,
                    saved_draft_id=journal.committed_draft_id,
                    saved_updated_at=journal.committed_updated_at,
                )
            if journal.state is PromptScratchRecoveryState.CONFLICT:
                return PromptScratchCommitResult(
                    status=PromptScratchCommitStatus.CONFLICT,
                    journal=journal,
                    reason=journal.conflict_reason,
                )
        return PromptScratchCommitResult(
            status=PromptScratchCommitStatus.STALE,
            journal=journal,
        )


__all__ = ["PromptScratchAutosaveService"]
