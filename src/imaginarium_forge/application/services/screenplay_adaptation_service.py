"""Independent, versioned screenplay adaptation services.

The accepted novel Scene is immutable input.  Manual edits and provider
outputs append screenplay revisions, and only an explicit author command may
move the screenplay's accepted pointer.  Nothing in this module updates the
novel draft chain.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Session

from imaginarium_forge.application.errors import (
    ApplicationError,
    ConflictError,
    NotFoundError,
    ValidationFailedError,
)
from imaginarium_forge.application.services.adult_output_review_service import (
    assert_adult_draft_reviewed,
)
from imaginarium_forge.application.services.base import ServiceBase, SessionProvider
from imaginarium_forge.canonical import canonical_json
from imaginarium_forge.config.settings import AppSettings
from imaginarium_forge.domain.adaptation.models import (
    MAX_SCREENPLAY_TEXT,
    AdaptationSourceHealth,
    ScreenplayBrief,
    ScreenplayParticipantPin,
    ScreenplayPromptContract,
    ScreenplaySourceSnapshot,
    canonical_participant_manifest,
    participant_manifest_sha256,
    utf8_sha256,
)
from imaginarium_forge.domain.character.presentation_cues import (
    detect_presentation_cues,
)
from imaginarium_forge.domain.common.enums import RequestType
from imaginarium_forge.domain.common.ids import new_id, utc_now_iso
from imaginarium_forge.domain.prompt.content_mode import derives_adult
from imaginarium_forge.domain.prompt.content_mode import (
    preflight_content_mode as content_preflight,
)
from imaginarium_forge.domain.story.models import SceneCard
from imaginarium_forge.infrastructure.db.models.orm import EligibilityEvaluationRow
from imaginarium_forge.infrastructure.db.repositories.adaptation_repos import (
    AdaptationAcceptanceEventRecord,
    AdaptationAcceptanceEventRepository,
    AdaptationGenerationRunRecord,
    AdaptationGenerationRunRepository,
    AdaptationRecord,
    AdaptationRepository,
    AdaptationRevisionRecord,
    AdaptationRevisionRepository,
)
from imaginarium_forge.infrastructure.db.repositories.story_repos import (
    SceneCardParticipantRepository,
    SceneCardVersionRepository,
    SceneDraftRepository,
    StoryChapterRepository,
    StorySceneRepository,
    VersionedEntityRepository,
)
from imaginarium_forge.providers.base import LLMProvider
from imaginarium_forge.providers.contracts import (
    GenerationOptions,
    GenerationRequest,
    GenerationResult,
)
from imaginarium_forge.providers.errors import (
    ModelNotFoundError,
    ProviderAuthenticationError,
    ProviderError,
    ProviderQuotaError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)

SCREENPLAY_INPUT_CONTRACT_VERSION = "screenplay-adaptation-input-v1"
MAX_TITLE = 300
MAX_CHANGE_NOTE = 1_000


@dataclass(frozen=True, slots=True)
class CurrentScreenplaySource:
    """Exact accepted Scene bytes plus their canonical typed projection."""

    scene_title: str
    prose_text: str
    snapshot: ScreenplaySourceSnapshot


@dataclass(frozen=True, slots=True)
class AdaptationView:
    adaptation: AdaptationRecord
    source_health: AdaptationSourceHealth


@dataclass(frozen=True, slots=True)
class GenerateScreenplayRequest:
    scene_id: str
    model: str
    brief: ScreenplayBrief = field(default_factory=ScreenplayBrief)
    adaptation_id: str | None = None
    title: str = ""
    expected_working_revision_id: str | None = None
    supersedes_adaptation_id: str | None = None
    options: GenerationOptions = field(default_factory=GenerationOptions)
    timeout_s: float = 300.0


@dataclass(frozen=True, slots=True)
class ScreenplayGenerationOutcome:
    adaptation: AdaptationRecord
    run: AdaptationGenerationRunRecord
    revision: AdaptationRevisionRecord | None
    source_health: AdaptationSourceHealth

    @property
    def awaiting_adult_review(self) -> bool:
        return self.run.status == "adult_pending"


class _SourceStateError(ValidationFailedError):
    def __init__(self, health: AdaptationSourceHealth) -> None:
        self.health = health
        detail = {
            AdaptationSourceHealth.SOURCE_STALE: (
                "來源小說的正式稿已變更；請由目前正式稿建立新的 successor 劇本改編。"
            ),
            AdaptationSourceHealth.SOURCE_MISSING: "劇本改編的歷史來源已不存在。",
            AdaptationSourceHealth.SOURCE_INTEGRITY_ERROR: (
                "劇本改編的來源版本、hash 或角色 pins 無法通過完整性驗證。"
            ),
            AdaptationSourceHealth.FRESH: "",
        }[health]
        super().__init__(detail or "劇本改編來源狀態無效。")


class _WorkingHeadConflict(ConflictError):
    pass


def _bounded_text(
    value: str,
    *,
    label: str,
    maximum: int,
    allow_blank: bool = False,
) -> str:
    if not allow_blank and not value.strip():
        raise ValidationFailedError(f"{label}不可為空")
    if len(value) > maximum:
        raise ValidationFailedError(f"{label}不可超過 {maximum} 字元")
    return value


def _participant_manifest_json(
    participants: tuple[ScreenplayParticipantPin, ...],
) -> str:
    return canonical_participant_manifest(participants)


def _load_current_source(session: Session, scene_id: str) -> CurrentScreenplaySource:
    scene = StorySceneRepository(session).get(scene_id)
    if scene is None:
        raise NotFoundError(f"找不到場景：{scene_id}")
    if not scene.accepted_draft_id:
        raise ValidationFailedError("目前場景沒有正式接受的完整小說稿，不能建立劇本改編。")

    chapter = StoryChapterRepository(session).get(scene.story_chapter_id)
    if chapter is None:
        raise ValidationFailedError("場景所屬章節不存在，無法釘選劇本來源。")
    outline = VersionedEntityRepository(session, "outline").get_entity(chapter.story_outline_id)
    if outline is None:
        raise ValidationFailedError("章節所屬故事大綱不存在，無法釘選劇本來源。")
    if chapter.project_id != scene.project_id or outline.project_id != scene.project_id:
        raise ValidationFailedError("故事 Outline、Chapter、Scene 的專案歸屬不一致。")

    draft = SceneDraftRepository(session).get(scene.accepted_draft_id)
    if draft is None:
        raise ValidationFailedError("場景的正式小說稿指標已懸空。")
    if draft.story_scene_id != scene.id or draft.project_id != scene.project_id:
        raise ValidationFailedError("正式小說稿不屬於目前場景與專案。")
    if draft.draft_status != "complete" or draft.is_preview:
        raise ValidationFailedError("劇本來源必須是 complete 且非 Preview 的正式小說稿。")
    if not draft.prose_text.strip():
        raise ValidationFailedError("正式小說稿內容為空白，不能建立劇本改編。")

    card_record = SceneCardVersionRepository(session).get(draft.scene_card_version_id)
    if card_record is None:
        raise ValidationFailedError("正式小說稿綁定的 Scene Card 版本不存在。")
    if card_record.parent_id != scene.id or card_record.project_id != scene.project_id:
        raise ValidationFailedError("正式小說稿的 Scene Card 不屬於目前場景與專案。")
    raw_card_json = str(card_record.payload.get("card_json", ""))
    try:
        card = SceneCard.model_validate_json(raw_card_json)
    except (ValidationError, ValueError) as exc:
        raise ValidationFailedError("正式小說稿的 Scene Card JSON 無法驗證。") from exc
    if str(card_record.payload.get("content_mode", "")) != card.content_mode.value:
        raise ValidationFailedError("Scene Card 的內容模式欄位與 typed payload 不一致。")

    participant_rows = tuple(
        SceneCardParticipantRepository(session).list_for_card_version(card_record.id)
    )
    participants = tuple(
        ScreenplayParticipantPin(
            character_id=row.character_id,
            character_version_id=row.character_version_id,
            role=row.role,
            is_pov=row.is_pov,
            position=row.position,
        )
        for row in participant_rows
    )
    for row in participant_rows:
        if row.project_id != scene.project_id or row.scene_card_version_id != card_record.id:
            raise ValidationFailedError("Scene Card participant pin 的專案歸屬不一致。")
    expected = tuple(
        (
            participant.character_id,
            participant.character_version_id,
            participant.role,
            bool(
                card.pov_character is not None
                and participant.character_id == card.pov_character.character_id
                and participant.character_version_id == card.pov_character.character_version_id
            ),
            position,
        )
        for position, participant in enumerate(card.participants)
    )
    actual = tuple(
        (
            pin.character_id,
            pin.character_version_id,
            pin.role,
            pin.is_pov,
            pin.position,
        )
        for pin in participants
    )
    if actual != expected:
        raise ValidationFailedError("Scene Card typed participants 與 durable pins 不一致。")

    if derives_adult(card.content_mode):
        assert_adult_draft_reviewed(session, draft, action_label="建立劇本改編")

    snapshot = ScreenplaySourceSnapshot(
        project_id=scene.project_id,
        outline_id=outline.id,
        chapter_id=chapter.id,
        scene_id=scene.id,
        draft_id=draft.id,
        scene_card_version_id=card_record.id,
        prose_sha256=utf8_sha256(draft.prose_text),
        scene_card_sha256=utf8_sha256(raw_card_json),
        participants=participants,
        participant_manifest_sha256=participant_manifest_sha256(participants),
        content_mode=card.content_mode,
    )
    return CurrentScreenplaySource(
        scene_title=scene.title,
        prose_text=draft.prose_text,
        snapshot=snapshot,
    )


def _source_health(
    session: Session,
    adaptation: AdaptationRecord,
) -> AdaptationSourceHealth:
    scene = StorySceneRepository(session).get(adaptation.source_scene_id)
    if scene is None:
        return AdaptationSourceHealth.SOURCE_MISSING
    if scene.project_id != adaptation.project_id:
        return AdaptationSourceHealth.SOURCE_INTEGRITY_ERROR
    if scene.accepted_draft_id != adaptation.source_draft_id:
        return AdaptationSourceHealth.SOURCE_STALE
    try:
        current = _load_current_source(session, scene.id)
        stored_snapshot = ScreenplaySourceSnapshot.model_validate_json(
            adaptation.source_snapshot_json
        )
    except NotFoundError:
        return AdaptationSourceHealth.SOURCE_MISSING
    except (ValidationFailedError, ValidationError, ValueError):
        return AdaptationSourceHealth.SOURCE_INTEGRITY_ERROR

    manifest_json = _participant_manifest_json(current.snapshot.participants)
    expected_root = (
        current.snapshot.project_id,
        current.snapshot.outline_id,
        current.snapshot.chapter_id,
        current.snapshot.scene_id,
        current.snapshot.draft_id,
        current.snapshot.scene_card_version_id,
        current.snapshot.prose_sha256,
        current.snapshot.scene_card_sha256,
        manifest_json,
        current.snapshot.participant_manifest_sha256,
        current.snapshot.canonical_json,
        current.snapshot.sha256,
        current.snapshot.content_mode.value,
    )
    actual_root = (
        adaptation.project_id,
        adaptation.source_outline_id,
        adaptation.source_chapter_id,
        adaptation.source_scene_id,
        adaptation.source_draft_id,
        adaptation.source_scene_card_version_id,
        adaptation.source_prose_sha256,
        adaptation.source_scene_card_sha256,
        adaptation.participant_manifest_json,
        adaptation.participant_manifest_sha256,
        adaptation.source_snapshot_json,
        adaptation.source_snapshot_sha256,
        adaptation.content_mode,
    )
    if (
        expected_root != actual_root
        or stored_snapshot != current.snapshot
        or stored_snapshot.canonical_json != adaptation.source_snapshot_json
        or utf8_sha256(adaptation.source_snapshot_json) != adaptation.source_snapshot_sha256
    ):
        return AdaptationSourceHealth.SOURCE_INTEGRITY_ERROR
    return AdaptationSourceHealth.FRESH


def _require_fresh(
    session: Session,
    adaptation: AdaptationRecord,
) -> CurrentScreenplaySource:
    health = _source_health(session, adaptation)
    if health is not AdaptationSourceHealth.FRESH:
        raise _SourceStateError(health)
    return _load_current_source(session, adaptation.source_scene_id)


def _require_active(adaptation: AdaptationRecord) -> None:
    if adaptation.lifecycle_status != "active":
        raise ValidationFailedError("已封存的劇本改編不可再新增、生成或接受版本。")


def _new_root_record(
    *,
    adaptation_id: str,
    source: CurrentScreenplaySource,
    title: str,
    supersedes_adaptation_id: str | None,
    now: str,
) -> AdaptationRecord:
    snapshot = source.snapshot
    manifest_json = _participant_manifest_json(snapshot.participants)
    effective_title = title.strip() or f"{source.scene_title}－劇本改編"
    _bounded_text(effective_title, label="劇本改編標題", maximum=MAX_TITLE)
    return AdaptationRecord(
        id=adaptation_id,
        project_id=snapshot.project_id,
        adaptation_type="screenplay",
        title=effective_title,
        source_outline_id=snapshot.outline_id,
        source_chapter_id=snapshot.chapter_id,
        source_scene_id=snapshot.scene_id,
        source_draft_id=snapshot.draft_id,
        source_scene_card_version_id=snapshot.scene_card_version_id,
        source_prose_sha256=snapshot.prose_sha256,
        source_scene_card_sha256=snapshot.scene_card_sha256,
        participant_manifest_json=manifest_json,
        participant_manifest_sha256=snapshot.participant_manifest_sha256,
        source_snapshot_json=snapshot.canonical_json,
        source_snapshot_sha256=snapshot.sha256,
        content_mode=snapshot.content_mode.value,
        working_revision_id=None,
        accepted_revision_id=None,
        supersedes_adaptation_id=supersedes_adaptation_id,
        lifecycle_status="active",
        created_at=now,
        updated_at=now,
    )


def _assert_supersedes_scope(
    session: Session,
    supersedes_adaptation_id: str | None,
    *,
    source: CurrentScreenplaySource,
) -> None:
    if supersedes_adaptation_id is None:
        return
    previous = AdaptationRepository(session).get(supersedes_adaptation_id)
    if previous is None:
        raise NotFoundError(f"找不到上一份劇本改編：{supersedes_adaptation_id}")
    if (
        previous.project_id != source.snapshot.project_id
        or previous.source_scene_id != source.snapshot.scene_id
        or previous.adaptation_type != "screenplay"
    ):
        raise ValidationFailedError("successor 只能接續同專案、同 Scene 的劇本改編。")


def _validate_candidate_policy(
    *,
    source: CurrentScreenplaySource,
    screenplay_text: str,
    additional_positive_texts: tuple[str, ...] = (),
) -> None:
    mode = source.snapshot.content_mode
    positive = (source.prose_text, screenplay_text, *additional_positive_texts)
    preflight = content_preflight(mode, *positive)
    if preflight.confirmation_required:
        raise ValidationFailedError(preflight.message_zh_tw)
    if derives_adult(mode) and detect_presentation_cues(*positive).has_conflict:
        raise ValidationFailedError(
            "成人劇本的來源、作者方向或候選內容含明確未成年期／孩童化線索，已阻止使用。"
        )


def _assert_audit_coverage(
    session: Session,
    *,
    audit_ids: tuple[str, ...],
    participants: tuple[ScreenplayParticipantPin, ...],
) -> None:
    if not audit_ids or len(set(audit_ids)) != len(audit_ids):
        raise ValidationFailedError("成人劇本資格 audit IDs 必須完整且不重複。")
    audits = session.scalars(
        select(EligibilityEvaluationRow).where(EligibilityEvaluationRow.id.in_(audit_ids))
    ).all()
    expected_pairs = {
        (participant.character_id, participant.character_version_id) for participant in participants
    }
    actual_pairs = {(audit.character_id, audit.character_version_id) for audit in audits}
    if (
        len(audits) != len(audit_ids)
        or len(audits) != len(participants)
        or actual_pairs != expected_pairs
        or any(
            not bool(audit.allowed) or audit.request_type != RequestType.STORY_GENERATION.value
            for audit in audits
        )
    ):
        raise ValidationFailedError("成人劇本資格 audits 未精確覆蓋所有 participant pins。")


class _ScreenplayServiceBase(ServiceBase):
    def __init__(
        self,
        session_factory: SessionProvider,
        *,
        eligibility: object | None,
    ) -> None:
        super().__init__(session_factory)
        self._eligibility = eligibility

    def _evaluate_adult(
        self,
        source: CurrentScreenplaySource,
        *,
        additional_positive_texts: tuple[str, ...] = (),
    ) -> tuple[str, ...]:
        if not derives_adult(source.snapshot.content_mode):
            return ()
        if detect_presentation_cues(source.prose_text, *additional_positive_texts).has_conflict:
            raise ValidationFailedError("成人劇本來源或正向指示含明確未成年期／孩童化線索。")
        if self._eligibility is None:
            raise ValidationFailedError("成人劇本需要即時資格驗證服務；目前未提供。")
        if not source.snapshot.participants:
            raise ValidationFailedError("成人劇本必須釘選至少一位可驗證的成人角色版本。")
        allowed, results, evaluation_ids = self._eligibility.evaluate_many_audited(  # type: ignore[attr-defined]
            participants=[
                (participant.character_id, participant.character_version_id)
                for participant in source.snapshot.participants
            ],
            request_type=RequestType.STORY_GENERATION,
            adult_content_requested=True,
        )
        if not allowed:
            blockers = "、".join(str(result.message) for result in results if not result.allowed)
            raise ValidationFailedError(f"成人劇本資格驗證未通過：{blockers or '未提供理由'}")
        audit_ids = tuple(str(value) for value in evaluation_ids)
        with self._session_factory() as session:
            _assert_audit_coverage(
                session,
                audit_ids=audit_ids,
                participants=source.snapshot.participants,
            )
        return audit_ids

    @staticmethod
    def _begin_immediate_source_decision(session: Session) -> None:
        """Reserve the SQLite writer before a source-freshness decision and write.

        Python's sqlite3 legacy transaction mode does not start a database
        transaction for SELECT statements.  Every screenplay command that
        validates the current accepted novel source and then writes therefore
        reserves the single SQLite writer before its first freshness read.  In
        addition to serializing adult eligibility checks, this prevents another
        connection from changing ``story_scenes.accepted_draft_id`` between the
        freshness decision and the adaptation write.
        """

        session.execute(sql_text("BEGIN IMMEDIATE"))

    def _assert_adult_audits_current(
        self,
        session: Session,
        *,
        source: CurrentScreenplaySource,
        audit_ids: tuple[str, ...],
    ) -> None:
        if not derives_adult(source.snapshot.content_mode):
            if audit_ids:
                raise ValidationFailedError("非成人劇本不可附帶成人資格 audits。")
            return
        if self._eligibility is None:
            raise ValidationFailedError("成人劇本需要即時資格驗證服務；目前未提供。")
        checker = getattr(
            self._eligibility,
            "assert_evaluations_current_in_session",
            None,
        )
        if not callable(checker):
            raise ValidationFailedError("成人資格服務不支援交易內 freshness 驗證。")
        _assert_audit_coverage(
            session,
            audit_ids=audit_ids,
            participants=source.snapshot.participants,
        )
        checker(
            session,
            participants=[
                (participant.character_id, participant.character_version_id)
                for participant in source.snapshot.participants
            ],
            evaluation_ids=audit_ids,
            request_type=RequestType.STORY_GENERATION,
            adult_content_requested=True,
        )


class ScreenplayAdaptationService(_ScreenplayServiceBase):
    """Manual screenplay commands and explicit acceptance."""

    def current_source(self, scene_id: str) -> CurrentScreenplaySource:
        return self._read_only(lambda session: _load_current_source(session, scene_id))

    def create_from_current_source(
        self,
        scene_id: str,
        *,
        title: str = "",
        supersedes_adaptation_id: str | None = None,
    ) -> AdaptationView:
        adaptation_id = new_id()
        now = utc_now_iso()
        with self._transaction() as session:
            self._begin_immediate_source_decision(session)
            source = _load_current_source(session, scene_id)
            _assert_supersedes_scope(session, supersedes_adaptation_id, source=source)
            record = _new_root_record(
                adaptation_id=adaptation_id,
                source=source,
                title=title,
                supersedes_adaptation_id=supersedes_adaptation_id,
                now=now,
            )
            AdaptationRepository(session).add(record)
        return AdaptationView(record, AdaptationSourceHealth.FRESH)

    def list_for_scene(self, scene_id: str) -> list[AdaptationView]:
        def load(session: Session) -> list[AdaptationView]:
            return [
                AdaptationView(record, _source_health(session, record))
                for record in AdaptationRepository(session).list_for_scene(scene_id)
                if record.adaptation_type == "screenplay"
            ]

        return self._read_only(load)

    def get(self, adaptation_id: str) -> AdaptationView:
        def load(session: Session) -> AdaptationView:
            record = AdaptationRepository(session).get(adaptation_id)
            if record is None or record.adaptation_type != "screenplay":
                raise NotFoundError(f"找不到劇本改編：{adaptation_id}")
            return AdaptationView(record, _source_health(session, record))

        return self._read_only(load)

    def list_revisions(self, adaptation_id: str) -> list[AdaptationRevisionRecord]:
        self.get(adaptation_id)
        return self._read_only(
            lambda session: AdaptationRevisionRepository(session).list_for_adaptation(adaptation_id)
        )

    def list_runs(self, adaptation_id: str) -> list[AdaptationGenerationRunRecord]:
        self.get(adaptation_id)
        return self._read_only(
            lambda session: AdaptationGenerationRunRepository(session).list_for_adaptation(
                adaptation_id
            )
        )

    def add_manual_revision(
        self,
        adaptation_id: str,
        *,
        screenplay_text: str,
        expected_working_revision_id: str | None,
        change_note: str = "",
    ) -> AdaptationRevisionRecord:
        return self._append_manual(
            adaptation_id,
            screenplay_text=screenplay_text,
            expected_working_revision_id=expected_working_revision_id,
            parent_revision_id=None,
            change_note=change_note,
            origin="manual",
        )

    def add_edited_revision(
        self,
        adaptation_id: str,
        *,
        parent_revision_id: str,
        screenplay_text: str,
        expected_working_revision_id: str | None,
        change_note: str = "",
    ) -> AdaptationRevisionRecord:
        return self._append_manual(
            adaptation_id,
            screenplay_text=screenplay_text,
            expected_working_revision_id=expected_working_revision_id,
            parent_revision_id=parent_revision_id,
            change_note=change_note,
            origin="edited",
        )

    def _append_manual(
        self,
        adaptation_id: str,
        *,
        screenplay_text: str,
        expected_working_revision_id: str | None,
        parent_revision_id: str | None,
        change_note: str,
        origin: str,
    ) -> AdaptationRevisionRecord:
        _bounded_text(
            screenplay_text,
            label="劇本文字",
            maximum=MAX_SCREENPLAY_TEXT,
        )
        _bounded_text(
            change_note,
            label="變更說明",
            maximum=MAX_CHANGE_NOTE,
            allow_blank=True,
        )
        with self._session_factory() as session:
            root = AdaptationRepository(session).get(adaptation_id)
            if root is None or root.adaptation_type != "screenplay":
                raise NotFoundError(f"找不到劇本改編：{adaptation_id}")
            _require_active(root)
            source = _require_fresh(session, root)
        _validate_candidate_policy(source=source, screenplay_text=screenplay_text)
        eligibility_ids = self._evaluate_adult(
            source,
            additional_positive_texts=(screenplay_text,),
        )

        revision_id = new_id()
        now = utc_now_iso()
        with self._transaction() as session:
            self._begin_immediate_source_decision(session)
            roots = AdaptationRepository(session)
            root = roots.get(adaptation_id)
            if root is None or root.adaptation_type != "screenplay":
                raise NotFoundError(f"找不到劇本改編：{adaptation_id}")
            _require_active(root)
            current_source = _require_fresh(session, root)
            self._assert_adult_audits_current(
                session,
                source=current_source,
                audit_ids=eligibility_ids,
            )
            if root.working_revision_id != expected_working_revision_id:
                raise _WorkingHeadConflict("劇本 working 版本已被另一個操作更新，請重新載入。")
            revisions = AdaptationRevisionRepository(session)
            if parent_revision_id is not None:
                parent = revisions.get(parent_revision_id)
                if parent is None or parent.adaptation_id != adaptation_id:
                    raise ValidationFailedError("編輯來源版本不屬於此劇本改編。")
            record = AdaptationRevisionRecord(
                id=revision_id,
                adaptation_id=adaptation_id,
                project_id=root.project_id,
                version_number=revisions.next_version_number(adaptation_id),
                parent_revision_id=parent_revision_id,
                screenplay_text=screenplay_text,
                screenplay_sha256=utf8_sha256(screenplay_text),
                origin=origin,
                completion_status="complete",
                generation_run_id=None,
                change_note=change_note,
                created_at=now,
            )
            revisions.add(record)
            session.flush()
            if not roots.compare_and_set_working(
                adaptation_id,
                expected_revision_id=expected_working_revision_id,
                new_revision_id=revision_id,
                updated_at=now,
            ):
                raise _WorkingHeadConflict("劇本 working 版本已變更，未覆寫其他操作。")
        return record

    def accept_revision(
        self,
        adaptation_id: str,
        revision_id: str,
        *,
        expected_accepted_revision_id: str | None,
    ) -> AdaptationView:
        with self._session_factory() as session:
            root = AdaptationRepository(session).get(adaptation_id)
            revision = AdaptationRevisionRepository(session).get(revision_id)
            if root is None or root.adaptation_type != "screenplay":
                raise NotFoundError(f"找不到劇本改編：{adaptation_id}")
            _require_active(root)
            source = _require_fresh(session, root)
            if revision is None or revision.adaptation_id != adaptation_id:
                raise ValidationFailedError("要接受的劇本版本不屬於此改編。")
            if revision.completion_status != "complete":
                raise ValidationFailedError("未完成（partial）的劇本版本不可接受。")
            if utf8_sha256(revision.screenplay_text) != revision.screenplay_sha256:
                raise ValidationFailedError("劇本版本內容與 SHA-256 不一致。")
        _validate_candidate_policy(source=source, screenplay_text=revision.screenplay_text)
        eligibility_ids = self._evaluate_adult(
            source,
            additional_positive_texts=(revision.screenplay_text,),
        )

        accepted_at = utc_now_iso()
        event = AdaptationAcceptanceEventRecord(
            id=new_id(),
            adaptation_id=adaptation_id,
            revision_id=revision_id,
            previous_accepted_revision_id=expected_accepted_revision_id,
            project_id=root.project_id,
            source_snapshot_sha256=root.source_snapshot_sha256,
            accepted_at=accepted_at,
        )
        with self._transaction() as session:
            self._begin_immediate_source_decision(session)
            roots = AdaptationRepository(session)
            current = roots.get(adaptation_id)
            candidate = AdaptationRevisionRepository(session).get(revision_id)
            if current is None or candidate is None:
                raise NotFoundError("劇本改編或版本已不存在。")
            _require_active(current)
            current_source = _require_fresh(session, current)
            self._assert_adult_audits_current(
                session,
                source=current_source,
                audit_ids=eligibility_ids,
            )
            if candidate.adaptation_id != adaptation_id:
                raise ValidationFailedError("要接受的劇本版本不屬於此改編。")
            if candidate.completion_status != "complete":
                raise ValidationFailedError("未完成（partial）的劇本版本不可接受。")
            if current.accepted_revision_id != expected_accepted_revision_id:
                raise _WorkingHeadConflict("正式劇本版本已由另一個操作更新，請重新載入。")
            AdaptationAcceptanceEventRepository(session).add(event)
            session.flush()
            if not roots.compare_and_set_accepted(
                adaptation_id,
                expected_revision_id=expected_accepted_revision_id,
                new_revision_id=revision_id,
                updated_at=accepted_at,
            ):
                raise _WorkingHeadConflict("正式劇本版本已變更，未覆寫其他操作。")
        return self.get(adaptation_id)


class ScreenplayGenerationService(_ScreenplayServiceBase):
    """OpenAI/Ollama screenplay candidates with finalize-once evidence."""

    def __init__(
        self,
        session_factory: SessionProvider,
        *,
        provider: LLMProvider | None,
        provider_name: str,
        eligibility: object | None,
        settings: AppSettings | None = None,
    ) -> None:
        super().__init__(session_factory, eligibility=eligibility)
        if provider_name not in {"openai", "ollama"}:
            raise ValueError("screenplay provider_name must be 'openai' or 'ollama'")
        self._provider = provider
        self._provider_name = provider_name
        self._settings = settings

    def generate(
        self,
        request: GenerateScreenplayRequest,
    ) -> ScreenplayGenerationOutcome:
        provider = self._provider
        if provider is None:
            raise ValidationFailedError("劇本生成需要 runtime provider；審閱既有候選則不需要。")
        model = request.model.strip()
        if not model:
            raise ValidationFailedError("劇本生成模型不可為空")
        if request.timeout_s <= 0 or request.timeout_s > 1_800:
            raise ValidationFailedError("劇本生成 timeout 必須介於 0 與 1800 秒。")

        adaptation_id = request.adaptation_id or new_id()
        with self._session_factory() as session:
            if request.adaptation_id is None:
                source = _load_current_source(session, request.scene_id)
                _assert_supersedes_scope(session, request.supersedes_adaptation_id, source=source)
                root: AdaptationRecord | None = None
            else:
                root = AdaptationRepository(session).get(request.adaptation_id)
                if root is None or root.adaptation_type != "screenplay":
                    raise NotFoundError(f"找不到劇本改編：{request.adaptation_id}")
                _require_active(root)
                if root.source_scene_id != request.scene_id:
                    raise ValidationFailedError("劇本改編不屬於指定 Scene。")
                source = _require_fresh(session, root)
                if root.working_revision_id != request.expected_working_revision_id:
                    raise _WorkingHeadConflict("劇本 working 版本已變更，請重新載入。")

        positive_brief = request.brief.positive_texts()
        _validate_candidate_policy(
            source=source,
            screenplay_text="",
            additional_positive_texts=positive_brief,
        )
        eligibility_ids = self._evaluate_adult(source, additional_positive_texts=positive_brief)
        try:
            prompt_contract = ScreenplayPromptContract(
                source=source.snapshot,
                source_prose=source.prose_text,
                brief=request.brief,
            )
        except ValidationError as exc:
            raise ValidationFailedError("劇本來源或 Prompt contract 超過限制或無法驗證。") from exc
        system_message = prompt_contract.system_message
        user_message = prompt_contract.user_message
        options_json = canonical_json(
            {
                "generation_options": request.options.model_dump(mode="json"),
                "timeout_s": request.timeout_s,
            }
        )
        system_hash = utf8_sha256(system_message)
        user_hash = utf8_sha256(user_message)
        input_snapshot_json = canonical_json(
            {
                "schema_version": SCREENPLAY_INPUT_CONTRACT_VERSION,
                "adaptation_id": adaptation_id,
                "source_snapshot_sha256": source.snapshot.sha256,
                "brief_sha256": request.brief.sha256,
                "prompt_contract_sha256": prompt_contract.sha256,
                "provider": self._provider_name,
                "model": model,
                "options": request.options.model_dump(mode="json"),
                "timeout_s": request.timeout_s,
                "participant_manifest_sha256": (source.snapshot.participant_manifest_sha256),
                "eligibility_evaluation_ids": list(eligibility_ids),
                "system_message_sha256": system_hash,
                "user_message_sha256": user_hash,
            }
        )
        run_id = new_id()
        started_at = utc_now_iso()
        store_raw = (
            True if self._settings is None else self._settings.store_rendered_generation_messages
        )
        run_record = AdaptationGenerationRunRecord(
            id=run_id,
            adaptation_id=adaptation_id,
            project_id=source.snapshot.project_id,
            source_snapshot_sha256=source.snapshot.sha256,
            expected_working_revision_id=request.expected_working_revision_id,
            provider=self._provider_name,
            model=model,
            brief_json=request.brief.canonical_json,
            options_json=options_json,
            input_snapshot_json=input_snapshot_json,
            input_snapshot_sha256=utf8_sha256(input_snapshot_json),
            participant_manifest_json=_participant_manifest_json(source.snapshot.participants),
            participant_manifest_sha256=(source.snapshot.participant_manifest_sha256),
            eligibility_evaluation_ids_json=canonical_json(list(eligibility_ids)),
            system_message_sha256=system_hash,
            user_message_sha256=user_hash,
            system_message_byte_size=len(system_message.encode("utf-8")),
            user_message_byte_size=len(user_message.encode("utf-8")),
            rendered_system_message=system_message if store_raw else None,
            rendered_user_message=user_message if store_raw else None,
            rendered_message_storage_enabled=int(store_raw),
            status="running",
            reason_code="",
            output_sha256=None,
            quarantined_output_text=None,
            quarantined_output_sha256=None,
            review_evaluation_ids_json="[]",
            reviewed_at="",
            resulting_revision_id=None,
            prompt_tokens=None,
            completion_tokens=None,
            started_at=started_at,
            completed_at="",
            latency_ms=0,
        )
        now = started_at
        with self._transaction() as session:
            self._begin_immediate_source_decision(session)
            roots = AdaptationRepository(session)
            current = roots.get(adaptation_id)
            if request.adaptation_id is None:
                if current is not None:
                    raise ConflictError("劇本改編識別碼衝突，請重試。")
                current_source = _load_current_source(session, request.scene_id)
                if current_source.snapshot.sha256 != source.snapshot.sha256:
                    raise _SourceStateError(AdaptationSourceHealth.SOURCE_STALE)
                _assert_supersedes_scope(
                    session, request.supersedes_adaptation_id, source=current_source
                )
                current = _new_root_record(
                    adaptation_id=adaptation_id,
                    source=current_source,
                    title=request.title,
                    supersedes_adaptation_id=request.supersedes_adaptation_id,
                    now=now,
                )
                roots.add(current)
                session.flush()
                self._assert_adult_audits_current(
                    session,
                    source=current_source,
                    audit_ids=eligibility_ids,
                )
            else:
                if current is None:
                    raise NotFoundError(f"找不到劇本改編：{adaptation_id}")
                _require_active(current)
                current_source = _require_fresh(session, current)
                self._assert_adult_audits_current(
                    session,
                    source=current_source,
                    audit_ids=eligibility_ids,
                )
                if current.working_revision_id != request.expected_working_revision_id:
                    raise _WorkingHeadConflict("劇本 working 版本已變更，請重新載入。")
            AdaptationGenerationRunRepository(session).add(run_record)

        started = time.monotonic()
        try:
            result = provider.generate_text(
                GenerationRequest(
                    model=model,
                    prompt=user_message,
                    system=system_message,
                    options=request.options,
                    timeout_s=request.timeout_s,
                )
            )
        except ProviderError as exc:
            self._finish_failed(
                run_id,
                reason_code=_provider_failure_code(exc),
                started=started,
            )
            raise
        except Exception as exc:
            self._finish_failed(run_id, reason_code="provider_error", started=started)
            raise ApplicationError("劇本 provider 發生未預期錯誤；未保存原始錯誤內容。") from exc

        output_text = result.text
        if not output_text.strip() or len(output_text) > MAX_SCREENPLAY_TEXT:
            self._finish_failed(run_id, reason_code="invalid_output", started=started)
            raise ValidationFailedError("模型回傳空白或超過大小限制的劇本，未建立候選。")
        output_preflight = content_preflight(source.snapshot.content_mode, output_text)
        output_cues = detect_presentation_cues(output_text)
        if output_preflight.confirmation_required or (
            derives_adult(source.snapshot.content_mode) and output_cues.has_conflict
        ):
            self._finish_failed(
                run_id,
                reason_code="adult_output_mode_mismatch",
                started=started,
            )
            raise ValidationFailedError("模型輸出的成人模式或年齡呈現不符合來源契約，未建立候選。")

        latency_ms = max(int(result.latency_ms), int((time.monotonic() - started) * 1000))
        if derives_adult(source.snapshot.content_mode):
            self._finish_adult_pending(
                run_id,
                output_text=output_text,
                result=result,
                latency_ms=latency_ms,
            )
            return self._outcome(adaptation_id, run_id, None)

        try:
            revision = self._finish_succeeded(
                adaptation_id=adaptation_id,
                run_id=run_id,
                source_snapshot_sha256=source.snapshot.sha256,
                output_text=output_text,
                expected_working_revision_id=request.expected_working_revision_id,
                result=result,
                latency_ms=latency_ms,
            )
        except _SourceStateError as exc:
            self._finish_failed(
                run_id,
                reason_code=(
                    "source_stale"
                    if exc.health is AdaptationSourceHealth.SOURCE_STALE
                    else "source_integrity_error"
                ),
                started=started,
            )
            raise
        except (ConflictError, ValidationFailedError):
            self._finish_failed(run_id, reason_code="working_head_conflict", started=started)
            raise
        return self._outcome(adaptation_id, run_id, revision)

    def confirm_adult_output(
        self,
        run_id: str,
        *,
        reviewed_complete_output: bool,
        expected_output_sha256: str,
        expected_working_revision_id: str | None,
    ) -> ScreenplayGenerationOutcome:
        if not reviewed_complete_output:
            raise ValidationFailedError("必須明確確認已完整審閱成人劇本輸出。")
        with self._session_factory() as session:
            run = AdaptationGenerationRunRepository(session).get(run_id)
            if run is None:
                raise NotFoundError(f"找不到劇本 generation run：{run_id}")
            root = AdaptationRepository(session).get(run.adaptation_id)
            if root is None:
                raise NotFoundError("成人劇本候選所屬 adaptation 不存在。")
            _require_active(root)
            source = _require_fresh(session, root)
            if run.status != "adult_pending":
                raise ConflictError("此成人劇本輸出不在待審閱狀態。")
            if not derives_adult(source.snapshot.content_mode):
                raise ValidationFailedError("非成人來源不可使用成人輸出確認流程。")
            if (
                run.quarantined_output_text is None
                or run.quarantined_output_sha256 != expected_output_sha256
                or utf8_sha256(run.quarantined_output_text) != expected_output_sha256
            ):
                raise ConflictError("待審閱成人劇本文字或 SHA-256 已不一致。")
            if run.expected_working_revision_id != expected_working_revision_id:
                raise ConflictError("審閱所綁定的劇本 working 版本不一致。")
            output_text = run.quarantined_output_text
        _validate_candidate_policy(source=source, screenplay_text=output_text)
        review_ids = self._evaluate_adult(source, additional_positive_texts=(output_text,))

        revision_id = new_id()
        reviewed_at = utc_now_iso()
        with self._transaction() as session:
            self._begin_immediate_source_decision(session)
            roots = AdaptationRepository(session)
            runs = AdaptationGenerationRunRepository(session)
            revisions = AdaptationRevisionRepository(session)
            current_run = runs.get(run_id)
            root = roots.get(run.adaptation_id)
            if current_run is None or root is None:
                raise NotFoundError("成人劇本候選或 adaptation 已不存在。")
            _require_active(root)
            current_source = _require_fresh(session, root)
            if current_source.snapshot.sha256 != run.source_snapshot_sha256:
                raise _SourceStateError(AdaptationSourceHealth.SOURCE_INTEGRITY_ERROR)
            if (
                current_run.status != "adult_pending"
                or current_run.quarantined_output_text != output_text
                or current_run.quarantined_output_sha256 != expected_output_sha256
            ):
                raise ConflictError("成人劇本候選在審閱期間發生變更。")
            if root.working_revision_id != expected_working_revision_id:
                raise _WorkingHeadConflict("劇本 working 版本已變更；成人候選未自動重綁。")
            self._assert_adult_audits_current(
                session,
                source=current_source,
                audit_ids=review_ids,
            )
            revision = AdaptationRevisionRecord(
                id=revision_id,
                adaptation_id=root.id,
                project_id=root.project_id,
                version_number=revisions.next_version_number(root.id),
                parent_revision_id=expected_working_revision_id,
                screenplay_text=output_text,
                screenplay_sha256=expected_output_sha256,
                origin=current_run.provider,
                completion_status="complete",
                generation_run_id=current_run.id,
                change_note="成人 AI 劇本候選已完成逐字審閱；尚未正式接受。",
                created_at=reviewed_at,
            )
            revisions.add(revision)
            session.flush()
            if not runs.finalize_succeeded(
                run_id,
                resulting_revision_id=revision_id,
                output_sha256=expected_output_sha256,
                prompt_tokens=current_run.prompt_tokens,
                completion_tokens=current_run.completion_tokens,
                completed_at=current_run.completed_at,
                latency_ms=current_run.latency_ms,
                review_evaluation_ids_json=canonical_json(list(review_ids)),
                reviewed_at=reviewed_at,
            ):
                raise ConflictError("成人劇本 generation run 已被其他操作完成。")
            session.flush()
            if not roots.compare_and_set_working(
                root.id,
                expected_revision_id=expected_working_revision_id,
                new_revision_id=revision_id,
                updated_at=reviewed_at,
            ):
                raise _WorkingHeadConflict("劇本 working 版本已變更，未覆寫其他操作。")
        return self._outcome(run.adaptation_id, run_id, revision)

    def reject_adult_output(
        self,
        run_id: str,
        *,
        confirm_rejection: bool,
        expected_output_sha256: str,
    ) -> ScreenplayGenerationOutcome:
        """Finalize one immutable adult quarantine without creating a revision.

        Rejection intentionally remains available when the novel source is
        stale or the screenplay working head has moved.  Those states should
        prevent promotion, not trap private quarantined output in a perpetual
        pending state.
        """

        if not confirm_rejection:
            raise ValidationFailedError("必須明確確認拒絕這份成人劇本輸出。")
        with self._session_factory() as session:
            run = AdaptationGenerationRunRepository(session).get(run_id)
            if run is None:
                raise NotFoundError(f"找不到劇本 generation run：{run_id}")
            if run.status != "adult_pending":
                raise ConflictError("此成人劇本輸出不在待審閱狀態。")
            if (
                run.quarantined_output_text is None
                or run.output_sha256 != expected_output_sha256
                or run.quarantined_output_sha256 != expected_output_sha256
                or utf8_sha256(run.quarantined_output_text) != expected_output_sha256
            ):
                raise ConflictError("待拒絕成人劇本文字或 SHA-256 已不一致。")
            adaptation_id = run.adaptation_id

        rejected_at = utc_now_iso()
        with self._transaction() as session:
            runs = AdaptationGenerationRunRepository(session)
            current = runs.get(run_id)
            if current is None:
                raise NotFoundError(f"找不到劇本 generation run：{run_id}")
            if (
                current.status != "adult_pending"
                or current.output_sha256 != expected_output_sha256
                or current.quarantined_output_sha256 != expected_output_sha256
                or current.quarantined_output_text is None
                or utf8_sha256(current.quarantined_output_text) != expected_output_sha256
            ):
                raise ConflictError("成人劇本候選在拒絕期間發生變更。")
            if not runs.reject_adult_pending(
                run_id,
                review_evaluation_ids_json="[]",
                reviewed_at=rejected_at,
            ):
                raise ConflictError("成人劇本 generation run 已由其他操作完成。")
        return self._outcome(adaptation_id, run_id, None)

    def _finish_failed(
        self,
        run_id: str,
        *,
        reason_code: str,
        started: float,
    ) -> None:
        completed_at = utc_now_iso()
        latency_ms = max(0, int((time.monotonic() - started) * 1000))
        with self._transaction() as session:
            if not AdaptationGenerationRunRepository(session).finalize_failed(
                run_id,
                reason_code=reason_code,
                completed_at=completed_at,
                latency_ms=latency_ms,
            ):
                raise ConflictError("劇本 generation run 無法完成失敗紀錄。")

    def _finish_adult_pending(
        self,
        run_id: str,
        *,
        output_text: str,
        result: GenerationResult,
        latency_ms: int,
    ) -> None:
        with self._transaction() as session:
            if not AdaptationGenerationRunRepository(session).finalize_adult_pending(
                run_id,
                output_text=output_text,
                output_sha256=utf8_sha256(output_text),
                prompt_tokens=result.usage.prompt_tokens,
                completion_tokens=result.usage.completion_tokens,
                completed_at=utc_now_iso(),
                latency_ms=latency_ms,
            ):
                raise ConflictError("劇本 generation run 已被其他操作完成。")

    def _finish_succeeded(
        self,
        *,
        adaptation_id: str,
        run_id: str,
        source_snapshot_sha256: str,
        output_text: str,
        expected_working_revision_id: str | None,
        result: GenerationResult,
        latency_ms: int,
    ) -> AdaptationRevisionRecord:
        revision_id = new_id()
        completed_at = utc_now_iso()
        output_hash = utf8_sha256(output_text)
        with self._transaction() as session:
            self._begin_immediate_source_decision(session)
            roots = AdaptationRepository(session)
            revisions = AdaptationRevisionRepository(session)
            runs = AdaptationGenerationRunRepository(session)
            root = roots.get(adaptation_id)
            run = runs.get(run_id)
            if root is None or run is None:
                raise NotFoundError("劇本 adaptation 或 generation run 已不存在。")
            _require_active(root)
            current_source = _require_fresh(session, root)
            if current_source.snapshot.sha256 != source_snapshot_sha256:
                raise _SourceStateError(AdaptationSourceHealth.SOURCE_INTEGRITY_ERROR)
            if run.status != "running" or run.source_snapshot_sha256 != source_snapshot_sha256:
                raise ConflictError("劇本 generation run 狀態或來源 snapshot 不一致。")
            if root.working_revision_id != expected_working_revision_id:
                raise _WorkingHeadConflict("劇本 working 版本已變更，候選未自動覆寫。")
            revision = AdaptationRevisionRecord(
                id=revision_id,
                adaptation_id=adaptation_id,
                project_id=root.project_id,
                version_number=revisions.next_version_number(adaptation_id),
                parent_revision_id=expected_working_revision_id,
                screenplay_text=output_text,
                screenplay_sha256=output_hash,
                origin=self._provider_name,
                completion_status="complete",
                generation_run_id=run_id,
                change_note="AI 劇本候選；尚未正式接受。",
                created_at=completed_at,
            )
            revisions.add(revision)
            session.flush()
            if not runs.finalize_succeeded(
                run_id,
                resulting_revision_id=revision_id,
                output_sha256=output_hash,
                prompt_tokens=result.usage.prompt_tokens,
                completion_tokens=result.usage.completion_tokens,
                completed_at=completed_at,
                latency_ms=latency_ms,
            ):
                raise ConflictError("劇本 generation run 已被其他操作完成。")
            session.flush()
            if not roots.compare_and_set_working(
                adaptation_id,
                expected_revision_id=expected_working_revision_id,
                new_revision_id=revision_id,
                updated_at=completed_at,
            ):
                raise _WorkingHeadConflict("劇本 working 版本已變更，未覆寫其他操作。")
        return revision

    def _outcome(
        self,
        adaptation_id: str,
        run_id: str,
        revision: AdaptationRevisionRecord | None,
    ) -> ScreenplayGenerationOutcome:
        with self._session_factory() as session:
            root = AdaptationRepository(session).get(adaptation_id)
            run = AdaptationGenerationRunRepository(session).get(run_id)
            if root is None or run is None:
                raise NotFoundError("劇本 generation outcome 無法重建。")
            health = _source_health(session, root)
        return ScreenplayGenerationOutcome(root, run, revision, health)


def _provider_failure_code(error: ProviderError) -> str:
    if isinstance(error, ProviderAuthenticationError):
        return "provider_authentication"
    if isinstance(error, ProviderQuotaError):
        return "provider_quota"
    if isinstance(error, ProviderRateLimitError):
        return "provider_rate_limit"
    if isinstance(error, ProviderTimeoutError):
        return "provider_timeout"
    if isinstance(error, ProviderUnavailableError):
        return "provider_unavailable"
    if isinstance(error, ModelNotFoundError):
        return "model_not_found"
    return "provider_error"


__all__ = [
    "SCREENPLAY_INPUT_CONTRACT_VERSION",
    "AdaptationView",
    "CurrentScreenplaySource",
    "GenerateScreenplayRequest",
    "ScreenplayAdaptationService",
    "ScreenplayGenerationOutcome",
    "ScreenplayGenerationService",
]
