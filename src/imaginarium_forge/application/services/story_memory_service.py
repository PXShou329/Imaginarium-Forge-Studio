"""Author-reviewed story memory and deterministic continuity projection.

An accepted scene draft may *propose* facts, timeline events, foreshadowing
threads and character state.  It never changes active story memory by itself:
only :meth:`StoryMemoryService.accept_proposal` appends entries, and that
command is bound to the exact proposal digest the author reviewed.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from sqlalchemy.orm import Session

from imaginarium_forge.application.errors import (
    NotFoundError,
    StaleStoryMemoryError,
    StoryMemoryConflictError,
    ValidationFailedError,
)
from imaginarium_forge.application.services.base import ServiceBase
from imaginarium_forge.domain.common.ids import new_id, utc_now_iso
from imaginarium_forge.domain.story.memory import (
    MAX_MEMORY_CHANGES,
    MEMORY_PROPOSAL_CONTRACT_VERSION,
    StoryConsistencyReport,
    StoryMemoryChange,
    StoryMemoryEntry,
    StoryMemoryKind,
    StoryMemoryOperation,
    StoryMemoryProjection,
    StoryMemoryProposalOrigin,
    StoryMemoryProposalPayload,
    StoryMemoryProposalStatus,
    detect_memory_conflicts,
    project_memory_entries,
)
from imaginarium_forge.domain.story.models import SceneCard
from imaginarium_forge.infrastructure.db.repositories.story_memory_repos import (
    StoryMemoryEntryRepository,
    StoryMemoryProposalRecord,
    StoryMemoryProposalRepository,
    StoryMemorySceneRecord,
    StoryMemorySceneRepository,
)
from imaginarium_forge.infrastructure.db.repositories.story_repos import (
    SceneCardVersionRepository,
    SceneDraftRepository,
    SceneDraftSummaryRepository,
    StoryChapterRepository,
    StorySceneRepository,
)


@dataclass(frozen=True, slots=True)
class StoryMemoryAcceptanceResult:
    proposal: StoryMemoryProposalRecord
    entries: tuple[StoryMemoryEntry, ...]
    projection_before_source: StoryMemoryProjection


def _scope_for_scene(
    session: Session, scene_id: str
) -> tuple[str, str, tuple[StoryMemorySceneRecord, ...]]:
    scene = StorySceneRepository(session).get(scene_id)
    if scene is None:
        raise NotFoundError(f"找不到場景：{scene_id}")
    chapter = StoryChapterRepository(session).get(scene.story_chapter_id)
    if chapter is None:
        raise NotFoundError(f"找不到章節：{scene.story_chapter_id}")
    ordered = tuple(
        StoryMemorySceneRepository(session).list_for_outline(
            chapter.story_outline_id
        )
    )
    if not any(candidate.id == scene_id for candidate in ordered):
        raise ValidationFailedError("場景不在其故事大綱的時間順序中")
    return scene.project_id, chapter.story_outline_id, ordered


def _before_target(
    ordered: tuple[StoryMemorySceneRecord, ...], scene_id: str
) -> tuple[StoryMemorySceneRecord, ...]:
    for index, scene in enumerate(ordered):
        if scene.id == scene_id:
            return ordered[:index]
    raise ValidationFailedError("找不到目標場景的時間順序")


def _with_projection_state(
    projection: StoryMemoryProjection,
    *,
    entries: tuple[StoryMemoryEntry, ...] | None = None,
    applied_proposal_ids: tuple[str, ...] | None = None,
    gap_scene_ids: tuple[str, ...] | None = None,
    stale_proposal_ids: tuple[str, ...] | None = None,
) -> StoryMemoryProjection:
    try:
        return project_memory_entries(
            project_id=projection.project_id,
            story_outline_id=projection.story_outline_id,
            entries=projection.entries if entries is None else entries,
            applied_proposal_ids=(
                projection.applied_proposal_ids
                if applied_proposal_ids is None
                else applied_proposal_ids
            ),
            gap_scene_ids=(
                projection.gap_scene_ids
                if gap_scene_ids is None
                else gap_scene_ids
            ),
            stale_proposal_ids=(
                projection.stale_proposal_ids
                if stale_proposal_ids is None
                else stale_proposal_ids
            ),
        )
    except ValueError as exc:
        raise ValidationFailedError(
            "已接受的故事記憶紀錄彼此衝突，已停止組裝上下文："
            f"{exc}"
        ) from exc


def resolve_story_memory_projection(
    session: Session,
    scene_id: str,
    *,
    exact_proposal_ids: tuple[str, ...] | None = None,
    exact_gap_scene_ids: tuple[str, ...] = (),
    exact_stale_proposal_ids: tuple[str, ...] = (),
) -> StoryMemoryProjection:
    """Resolve accepted memory immediately before ``scene_id``.

    ``exact_proposal_ids is None`` follows current accepted-draft pointers.
    Supplying a tuple reconstructs a historical snapshot from immutable
    proposal/entry IDs and deliberately ignores later pointer changes.
    """

    project_id, outline_id, ordered = _scope_for_scene(session, scene_id)
    prior_scenes = _before_target(ordered, scene_id)
    proposals = StoryMemoryProposalRepository(session)
    entries = StoryMemoryEntryRepository(session)
    projection = project_memory_entries(
        project_id=project_id,
        story_outline_id=outline_id,
        entries=(),
    )

    if exact_proposal_ids is None:
        for prior in prior_scenes:
            if not prior.accepted_draft_id:
                continue
            proposal = proposals.accepted_for_draft(prior.accepted_draft_id)
            if proposal is None:
                projection = _with_projection_state(
                    projection,
                    gap_scene_ids=(*projection.gap_scene_ids, prior.id),
                )
                continue
            if proposal.base_memory_fingerprint != projection.fingerprint:
                projection = _with_projection_state(
                    projection,
                    gap_scene_ids=(*projection.gap_scene_ids, prior.id),
                    stale_proposal_ids=(
                        *projection.stale_proposal_ids,
                        proposal.id,
                    ),
                )
                continue
            accepted_entries = tuple(entries.list_for_proposal(proposal.id))
            projection = _with_projection_state(
                projection,
                entries=(*projection.entries, *accepted_entries),
                applied_proposal_ids=(
                    *projection.applied_proposal_ids,
                    proposal.id,
                ),
            )
        return projection

    if len(set(exact_proposal_ids)) != len(exact_proposal_ids):
        raise ValidationFailedError("歷史故事記憶提案 ID 不可重複")
    if len(set(exact_gap_scene_ids)) != len(exact_gap_scene_ids):
        raise ValidationFailedError("歷史故事記憶缺口場景 ID 不可重複")
    if len(set(exact_stale_proposal_ids)) != len(exact_stale_proposal_ids):
        raise ValidationFailedError("歷史過期故事記憶提案 ID 不可重複")

    applied_by_scene: dict[str, StoryMemoryProposalRecord] = {}
    stale_by_scene: dict[str, StoryMemoryProposalRecord] = {}
    targets = [
        *((proposal_id, applied_by_scene) for proposal_id in exact_proposal_ids),
        *(
            (proposal_id, stale_by_scene)
            for proposal_id in exact_stale_proposal_ids
        ),
    ]
    for proposal_id, target in targets:
        proposal = proposals.get(proposal_id)
        if proposal is None:
            raise NotFoundError(f"找不到故事記憶提案：{proposal_id}")
        if (
            proposal.project_id != project_id
            or proposal.story_outline_id != outline_id
            or proposal.status is not StoryMemoryProposalStatus.ACCEPTED
        ):
            raise ValidationFailedError("歷史故事記憶提案不屬於此故事或尚未接受")
        if proposal.story_scene_id in target:
            raise ValidationFailedError("同一場景不可套用兩份歷史故事記憶提案")
        target[proposal.story_scene_id] = proposal

    gaps = set(exact_gap_scene_ids)
    stale_ids = set(exact_stale_proposal_ids)
    prior_ids = {prior.id for prior in prior_scenes}
    if not gaps <= prior_ids:
        raise ValidationFailedError("歷史故事記憶缺口包含目標場景之後的場景")
    if not set(applied_by_scene) <= prior_ids or not set(stale_by_scene) <= prior_ids:
        raise ValidationFailedError("歷史故事記憶提案不在目標場景之前")
    if set(stale_by_scene) - gaps:
        raise ValidationFailedError("過期故事記憶提案必須同時標記其場景缺口")
    if set(applied_by_scene) & gaps:
        raise ValidationFailedError("同一場景不可同時套用提案並標記缺口")

    for prior in prior_scenes:
        if prior.id in gaps:
            stale = stale_by_scene.get(prior.id)
            projection = _with_projection_state(
                projection,
                gap_scene_ids=(*projection.gap_scene_ids, prior.id),
                stale_proposal_ids=(
                    projection.stale_proposal_ids
                    if stale is None
                    else (*projection.stale_proposal_ids, stale.id)
                ),
            )
            continue
        proposal = applied_by_scene.get(prior.id)
        if proposal is None:
            continue
        if proposal.id in stale_ids:
            raise ValidationFailedError("過期提案不可列為已套用提案")
        if proposal.base_memory_fingerprint != projection.fingerprint:
            raise StaleStoryMemoryError(
                proposal_id=proposal.id,
                expected_fingerprint=proposal.base_memory_fingerprint,
                current_fingerprint=projection.fingerprint,
            )
        proposal_entries = tuple(entries.list_for_proposal(proposal.id))
        projection = _with_projection_state(
            projection,
            entries=(*projection.entries, *proposal_entries),
            applied_proposal_ids=(*projection.applied_proposal_ids, proposal.id),
        )
    return projection


class StoryMemoryService(ServiceBase):
    """Public commands for proposing, reviewing and reading story memory."""

    def projection_for_scene(self, scene_id: str) -> StoryMemoryProjection:
        return self._read_only(
            lambda session: resolve_story_memory_projection(session, scene_id)
        )

    def list_proposals_for_draft(
        self, draft_id: str
    ) -> tuple[StoryMemoryProposalRecord, ...]:
        return self._read_only(
            lambda session: tuple(
                StoryMemoryProposalRepository(session).list_for_draft(draft_id)
            )
        )

    def propose_for_accepted_draft(
        self,
        draft_id: str,
        *,
        payload: StoryMemoryProposalPayload | None = None,
        origin: StoryMemoryProposalOrigin = StoryMemoryProposalOrigin.SCENE_CARD,
        provider: str = "",
        model: str = "",
    ) -> StoryMemoryProposalRecord:
        """Create an immutable pending proposal from one active accepted draft.

        ``payload=None`` is the deterministic Scene Card workflow.  An LLM may
        generate a structured payload outside this transaction and pass it
        here, but the same ownership, staleness and author-review gates apply.
        """

        if payload is None and origin is not StoryMemoryProposalOrigin.SCENE_CARD:
            raise ValidationFailedError("manual/llm 故事記憶提案必須提供結構化 payload")
        with self._transaction() as session:
            draft = SceneDraftRepository(session).get(draft_id)
            if draft is None:
                raise NotFoundError(f"找不到草稿：{draft_id}")
            scene = StorySceneRepository(session).get(draft.story_scene_id)
            if scene is None:
                raise NotFoundError(f"找不到場景：{draft.story_scene_id}")
            if draft.draft_status != "complete" or scene.accepted_draft_id != draft.id:
                raise ValidationFailedError(
                    "只有目前已接受的完整場景草稿可以提出故事記憶變更"
                )
            chapter = StoryChapterRepository(session).get(scene.story_chapter_id)
            if chapter is None:
                raise NotFoundError(f"找不到章節：{scene.story_chapter_id}")
            projection = resolve_story_memory_projection(session, scene.id)

            if payload is None:
                card_record = SceneCardVersionRepository(session).get(
                    draft.scene_card_version_id
                )
                if card_record is None or card_record.parent_id != scene.id:
                    raise ValidationFailedError("草稿綁定的 Scene Card 已不存在或歸屬錯誤")
                card = SceneCard.model_validate_json(
                    str(card_record.payload["card_json"])
                )
                summary = SceneDraftSummaryRepository(session).get_for_draft(
                    draft.id
                )
                payload = _payload_from_scene_card(
                    scene_id=scene.id,
                    card=card,
                    summary_text="" if summary is None else summary.summary_text,
                    projection=projection,
                )
            if not payload.changes:
                raise ValidationFailedError("此草稿沒有可供確認的故事記憶變更")

            repository = StoryMemoryProposalRepository(session)
            for existing in repository.list_for_draft(draft.id):
                if (
                    existing.status is StoryMemoryProposalStatus.PENDING
                    and existing.proposal_sha256 == payload.sha256
                    and existing.base_memory_fingerprint == projection.fingerprint
                ):
                    return existing

            record = StoryMemoryProposalRecord(
                id=new_id(),
                project_id=scene.project_id,
                story_outline_id=chapter.story_outline_id,
                story_scene_id=scene.id,
                scene_draft_id=draft.id,
                base_memory_fingerprint=projection.fingerprint,
                proposal_json=payload.canonical_payload,
                proposal_sha256=payload.sha256,
                origin=origin,
                provider=provider.strip()[:200],
                model=model.strip()[:200],
                contract_version=MEMORY_PROPOSAL_CONTRACT_VERSION,
                status=StoryMemoryProposalStatus.PENDING,
                decision_note="",
                created_at=utc_now_iso(),
                finalized_at="",
            )
            repository.add(record)
            session.flush()
            return record

    def consistency_for_proposal(
        self, proposal_id: str
    ) -> StoryConsistencyReport:
        def _read(session: Session) -> StoryConsistencyReport:
            proposal = StoryMemoryProposalRepository(session).get(proposal_id)
            if proposal is None:
                raise NotFoundError(f"找不到故事記憶提案：{proposal_id}")
            projection = resolve_story_memory_projection(
                session, proposal.story_scene_id
            )
            if proposal.base_memory_fingerprint != projection.fingerprint:
                raise StaleStoryMemoryError(
                    proposal_id=proposal.id,
                    expected_fingerprint=proposal.base_memory_fingerprint,
                    current_fingerprint=projection.fingerprint,
                )
            return detect_memory_conflicts(projection, proposal.payload)

        return self._read_only(_read)

    def accept_proposal(
        self,
        proposal_id: str,
        *,
        expected_sha256: str,
        decision_note: str = "",
    ) -> StoryMemoryAcceptanceResult:
        """Finalize one exact proposal and append its entries atomically."""

        with self._transaction() as session:
            proposals = StoryMemoryProposalRepository(session)
            entry_repository = StoryMemoryEntryRepository(session)
            proposal = proposals.get(proposal_id)
            if proposal is None:
                raise NotFoundError(f"找不到故事記憶提案：{proposal_id}")
            if proposal.proposal_sha256 != expected_sha256:
                raise ValidationFailedError("故事記憶提案內容已改變，請重新檢視後再確認")
            projection = resolve_story_memory_projection(
                session, proposal.story_scene_id
            )
            if proposal.status is StoryMemoryProposalStatus.ACCEPTED:
                return StoryMemoryAcceptanceResult(
                    proposal=proposal,
                    entries=tuple(entry_repository.list_for_proposal(proposal.id)),
                    projection_before_source=projection,
                )
            if proposal.status is StoryMemoryProposalStatus.REJECTED:
                raise ValidationFailedError("已拒絕的故事記憶提案不可再次接受")
            accepted_alternative = proposals.accepted_for_draft(
                proposal.scene_draft_id
            )
            if (
                accepted_alternative is not None
                and accepted_alternative.id != proposal.id
            ):
                raise ValidationFailedError(
                    "此場景草稿已有另一份作者接受的故事記憶提案"
                )

            scene = StorySceneRepository(session).get(proposal.story_scene_id)
            draft = SceneDraftRepository(session).get(proposal.scene_draft_id)
            if (
                scene is None
                or draft is None
                or scene.accepted_draft_id != proposal.scene_draft_id
                or draft.draft_status != "complete"
            ):
                raise ValidationFailedError(
                    "提案所屬草稿已不是目前接受版本；請從新接受的草稿重新提案"
                )
            if proposal.base_memory_fingerprint != projection.fingerprint:
                raise StaleStoryMemoryError(
                    proposal_id=proposal.id,
                    expected_fingerprint=proposal.base_memory_fingerprint,
                    current_fingerprint=projection.fingerprint,
                )
            report = detect_memory_conflicts(projection, proposal.payload)
            if report.has_errors:
                raise StoryMemoryConflictError(
                    proposal_id=proposal.id,
                    messages=tuple(
                        finding.message_zh_tw
                        for finding in report.findings
                        if finding.severity.value == "error"
                    ),
                )

            now = utc_now_iso()
            active = projection.by_identity
            accepted_entries: list[StoryMemoryEntry] = []
            for change in proposal.payload.changes:
                current = active.get(change.identity_key)
                if (
                    change.operation is StoryMemoryOperation.ASSERT
                    and current is not None
                    and current.value == change.value
                ):
                    continue
                entry = StoryMemoryEntry(
                    id=new_id(),
                    project_id=proposal.project_id,
                    story_outline_id=proposal.story_outline_id,
                    story_scene_id=proposal.story_scene_id,
                    scene_draft_id=proposal.scene_draft_id,
                    proposal_id=proposal.id,
                    kind=change.kind,
                    subject_id=change.subject_id,
                    attribute=change.attribute,
                    value=change.value,
                    operation=change.operation,
                    supersedes_entry_id=change.supersedes_entry_id,
                    source_excerpt=change.source_excerpt,
                    created_at=now,
                )
                accepted_entries.append(entry)
            proposals.finalize(
                proposal.id,
                status=StoryMemoryProposalStatus.ACCEPTED,
                decision_note=decision_note.strip()[:1000],
                finalized_at=now,
            )
            # The database only accepts entries whose proposal is already in
            # ACCEPTED state. This flush remains inside the same transaction;
            # any later entry failure rolls the finalization back as well.
            session.flush()
            for entry in accepted_entries:
                entry_repository.add(entry)
            session.flush()
            accepted = proposals.get(proposal.id)
            if accepted is None:  # defensive; the row is insert-protected
                raise NotFoundError(f"找不到故事記憶提案：{proposal.id}")
            return StoryMemoryAcceptanceResult(
                proposal=accepted,
                entries=tuple(accepted_entries),
                projection_before_source=projection,
            )

    def reject_proposal(
        self,
        proposal_id: str,
        *,
        expected_sha256: str,
        decision_note: str = "",
    ) -> StoryMemoryProposalRecord:
        with self._transaction() as session:
            repository = StoryMemoryProposalRepository(session)
            proposal = repository.get(proposal_id)
            if proposal is None:
                raise NotFoundError(f"找不到故事記憶提案：{proposal_id}")
            if proposal.proposal_sha256 != expected_sha256:
                raise ValidationFailedError("故事記憶提案內容已改變，請重新檢視後再確認")
            if proposal.status is StoryMemoryProposalStatus.REJECTED:
                return proposal
            if proposal.status is StoryMemoryProposalStatus.ACCEPTED:
                raise ValidationFailedError("已接受的故事記憶提案不可改為拒絕")
            repository.finalize(
                proposal.id,
                status=StoryMemoryProposalStatus.REJECTED,
                decision_note=decision_note.strip()[:1000],
                finalized_at=utc_now_iso(),
            )
            session.flush()
            rejected = repository.get(proposal.id)
            if rejected is None:
                raise NotFoundError(f"找不到故事記憶提案：{proposal.id}")
            return rejected


def _digest(text: str) -> str:
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()[:16]


def _state_change(
    *,
    kind: StoryMemoryKind,
    subject_id: str,
    attribute: str,
    value: str,
    source_excerpt: str,
    projection: StoryMemoryProjection,
) -> StoryMemoryChange | None:
    current = projection.by_identity.get(
        (subject_id.casefold(), attribute.casefold())
    )
    if current is not None and current.value == value:
        return None
    if current is None:
        return StoryMemoryChange(
            kind=kind,
            subject_id=subject_id,
            attribute=attribute,
            value=value,
            source_excerpt=source_excerpt,
        )
    return StoryMemoryChange(
        kind=kind,
        subject_id=subject_id,
        attribute=attribute,
        value=value,
        operation=StoryMemoryOperation.SUPERSEDE,
        supersedes_entry_id=current.id,
        source_excerpt=source_excerpt,
    )


def _payload_from_scene_card(
    *,
    scene_id: str,
    card: SceneCard,
    summary_text: str,
    projection: StoryMemoryProjection,
) -> StoryMemoryProposalPayload:
    """Deterministic fallback when no LLM-authored payload is supplied."""

    changes: list[StoryMemoryChange] = []
    scene_subject = f"scene:{scene_id}"
    for attribute, value in (
        ("summary", summary_text.strip()),
        ("location", card.location.strip()),
        ("start_time", card.start_time.strip()),
        (
            "duration_minutes",
            str(card.duration_minutes) if card.duration_minutes else "",
        ),
    ):
        if value:
            if len(changes) >= MAX_MEMORY_CHANGES:
                break
            changes.append(
                StoryMemoryChange(
                    kind=StoryMemoryKind.TIMELINE,
                    subject_id=scene_subject,
                    attribute=attribute,
                    value=value,
                    source_excerpt=value[:1000],
                )
            )

    pov_subject = (
        f"character:{card.pov_character_id}"
        if card.pov_character_id
        else scene_subject
    )
    if card.exit_state.emotion.strip():
        emotion = card.exit_state.emotion.strip()
        change = _state_change(
            kind=StoryMemoryKind.CHARACTER_STATE,
            subject_id=pov_subject,
            attribute="emotion",
            value=emotion,
            source_excerpt=emotion[:1000],
            projection=projection,
        )
        if change is not None and len(changes) < MAX_MEMORY_CHANGES:
            changes.append(change)

    for knowledge in card.exit_state.new_knowledge:
        normalized = knowledge.strip()
        if not normalized:
            continue
        if len(changes) >= MAX_MEMORY_CHANGES:
            break
        changes.append(
            StoryMemoryChange(
                kind=StoryMemoryKind.FACT,
                subject_id=pov_subject,
                attribute=f"known_fact:{_digest(normalized)}",
                value=normalized,
                source_excerpt=normalized[:1000],
            )
        )

    for unresolved in card.exit_state.unresolved:
        normalized = unresolved.strip()
        if not normalized:
            continue
        subject = f"thread:{_digest(normalized)}"
        if len(changes) + 2 > MAX_MEMORY_CHANGES:
            break
        changes.extend(
            (
                StoryMemoryChange(
                    kind=StoryMemoryKind.FORESHADOWING,
                    subject_id=subject,
                    attribute="description",
                    value=normalized,
                    source_excerpt=normalized[:1000],
                ),
                StoryMemoryChange(
                    kind=StoryMemoryKind.FORESHADOWING,
                    subject_id=subject,
                    attribute="status",
                    value="open",
                    source_excerpt=normalized[:1000],
                ),
            )
        )
    return StoryMemoryProposalPayload(changes=tuple(changes))


__all__ = [
    "StoryMemoryAcceptanceResult",
    "StoryMemoryService",
    "resolve_story_memory_projection",
]
