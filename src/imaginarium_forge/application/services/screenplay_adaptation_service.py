"""Independent, versioned screenplay adaptation services.

The accepted novel Scene is immutable input. Manual edits and provider
outputs append screenplay revisions, and only an explicit author command may
move the screenplay's accepted pointer. Nothing in this module updates the
novel draft chain.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field
from threading import Event

from pydantic import SecretStr, ValidationError
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Session

from imaginarium_forge.application.errors import (
    ApplicationError,
    ConflictError,
    NotFoundError,
    ValidationFailedError,
)
from imaginarium_forge.application.services.base import ServiceBase, SessionProvider
from imaginarium_forge.application.services.generation.credential_content_guard import (
    assert_active_credential_not_in_content,
)
from imaginarium_forge.application.services.generation.length_assurance import (
    ProviderTermination,
)
from imaginarium_forge.application.services.generation.prose_pipeline import (
    ProseCandidate,
    ProsePipelineResult,
)
from imaginarium_forge.application.services.generation.screenplay_pipeline import (
    SCREENPLAY_PROSE_ADAPTER_VERSION,
    ScreenplayPipelineExecution,
    run_screenplay_pipeline,
    screenplay_assurance_limits,
    screenplay_initial_system_message,
    screenplay_length_spec_for_minutes,
)
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
from imaginarium_forge.domain.common.ids import new_id, utc_now_iso
from imaginarium_forge.domain.generation.length import GenerationLengthSpec, LengthUnit
from imaginarium_forge.domain.story.models import SceneCard
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
    general_audience_system,
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
    RequestCancelledError,
)

SCREENPLAY_INPUT_CONTRACT_VERSION = "screenplay-adaptation-input-v2"
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
    length_spec: GenerationLengthSpec | None = None
    cancel: Event | None = field(default=None, repr=False, compare=False)
    on_delta: Callable[[str], None] | None = field(default=None, repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class ScreenplayGenerationOutcome:
    adaptation: AdaptationRecord
    run: AdaptationGenerationRunRecord
    revision: AdaptationRevisionRecord | None
    source_health: AdaptationSourceHealth
    preserved_revision: AdaptationRevisionRecord | None = None
    assurance: ProsePipelineResult | None = field(default=None, repr=False)


    @property
    def partial_needs_action(self) -> bool:
        return self.revision is not None and self.revision.completion_status == "partial"

    @property
    def cancelled(self) -> bool:
        return self.run.status == "cancelled" or (
            self.assurance is not None
            and self.assurance.decision.provider_termination is ProviderTermination.CANCELLED
        )


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






class _ScreenplayServiceBase(ServiceBase):
    @staticmethod
    def _begin_immediate_source_decision(session: Session) -> None:
        """Reserve the SQLite writer before a source-freshness decision and write.

        Python's sqlite3 legacy transaction mode does not start a database
        transaction for SELECT statements. Every screenplay command that
        validates the current accepted novel source and then writes therefore
        reserves the single SQLite writer before its first freshness read. In
        addition to serializing source checks, this prevents another
        connection from changing ``story_scenes.accepted_draft_id`` between the
        freshness decision and the adaptation write.
        """

        session.execute(sql_text("BEGIN IMMEDIATE"))



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
        settings: AppSettings | None = None,
        active_credential: SecretStr | str | None = None,
    ) -> None:
        super().__init__(session_factory)
        if provider_name not in {"openai", "ollama"}:
            raise ValueError("screenplay provider_name must be 'openai' or 'ollama'")
        self._provider = provider
        self._provider_name = provider_name
        self._settings = settings
        self._active_credential = (
            active_credential
            if isinstance(active_credential, SecretStr) or active_credential is None
            else SecretStr(active_credential)
        )

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
        length_spec = request.length_spec or screenplay_length_spec_for_minutes(
            request.brief.target_minutes
        )
        if length_spec.unit is not LengthUnit.SCREENPLAY_PAGES:
            raise ValidationFailedError("劇本生成的長度單位必須是劇本頁數估算。")
        assurance_limits = screenplay_assurance_limits(
            length_spec=length_spec,
            options=request.options,
        )

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
        try:
            prompt_contract = ScreenplayPromptContract(
                source=source.snapshot,
                source_prose=source.prose_text,
                brief=request.brief,
            )
        except ValidationError as exc:
            raise ValidationFailedError("劇本來源或 Prompt contract 超過限制或無法驗證。") from exc
        base_system_message = general_audience_system(prompt_contract.system_message)
        system_message = screenplay_initial_system_message(
            system_message=base_system_message,
            length_spec=length_spec,
        )
        user_message = prompt_contract.user_message
        predispatch_request = GenerationRequest(
            model=model,
            prompt=user_message,
            system=system_message,
            options=request.options,
            timeout_s=request.timeout_s,
        )
        assert_active_credential_not_in_content(
            self._active_credential,
            system_message,
            user_message,
        )
        provider_content_guard = getattr(
            provider,
            "assert_request_author_content_safe",
            None,
        )
        if callable(provider_content_guard):
            provider_content_guard(predispatch_request)
        options_json = canonical_json(
            {
                "generation_options": request.options.model_dump(mode="json"),
                "timeout_s": request.timeout_s,
                "screenplay_prose_adapter": SCREENPLAY_PROSE_ADAPTER_VERSION,
                "length_spec": length_spec.model_dump(mode="json"),
                "assurance_limits": assurance_limits.model_dump(mode="json"),
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
                "screenplay_prose_adapter": SCREENPLAY_PROSE_ADAPTER_VERSION,
                "length_spec": length_spec.model_dump(mode="json"),
                "length_spec_sha256": length_spec.sha256,
                "assurance_limits": assurance_limits.model_dump(mode="json"),
                "participant_manifest_sha256": (source.snapshot.participant_manifest_sha256),
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
            eligibility_evaluation_ids_json="[]",
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
            resulting_revision_id=None,
            prompt_tokens=None,
            completion_tokens=None,
            started_at=started_at,
            completed_at="",
            latency_ms=0,
            assurance_json="{}",
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
            else:
                if current is None:
                    raise NotFoundError(f"找不到劇本改編：{adaptation_id}")
                _require_active(current)
                current_source = _require_fresh(session, current)
                if current.working_revision_id != request.expected_working_revision_id:
                    raise _WorkingHeadConflict("劇本 working 版本已變更，請重新載入。")
            AdaptationGenerationRunRepository(session).add(run_record)

        started = time.monotonic()
        execution: ScreenplayPipelineExecution | None = None
        # Deliberately discard the raw exception object. A provider may attach
        # an active key to its message, repr, context, or custom attributes.
        with suppress(Exception):
            execution = run_screenplay_pipeline(
                run_id=run_id,
                provider=provider,
                provider_name=self._provider_name,
                model=model,
                system_message=base_system_message,
                user_message=user_message,
                options=request.options,
                timeout_s=request.timeout_s,
                length_spec=length_spec,
                protected_facts=request.brief.must_include,
                cancel=request.cancel,
                on_delta=request.on_delta,
            )
        if execution is None:
            self._finish_failed(run_id, reason_code="provider_error", started=started)
            raise ApplicationError(
                "劇本 provider 發生未預期錯誤；未保存原始錯誤內容。"
            ) from None

        pipeline = execution.pipeline
        accumulated = pipeline.accumulated_candidate
        if accumulated is None:
            if pipeline.decision.provider_termination is ProviderTermination.CANCELLED:
                self._finish_cancelled(run_id, started=started, assurance=pipeline)
                return self._outcome(
                    adaptation_id,
                    run_id,
                    None,
                    assurance=pipeline,
                )
            provider_error = execution.provider_errors[-1] if execution.provider_errors else None
            self._finish_failed(
                run_id,
                reason_code=(
                    _provider_failure_code(provider_error)
                    if provider_error is not None
                    else pipeline.decision.reason.value
                ),
                started=started,
                assurance=pipeline,
            )
            if provider_error is not None:
                raise _sanitized_provider_error(provider_error) from None
            raise ValidationFailedError("模型沒有回傳可保存的劇本 partial；未建立候選。")

        candidates = tuple(
            candidate
            for candidate in (accumulated, pipeline.condensed_candidate)
            if candidate is not None
        )
        if any(len(candidate.text) > MAX_SCREENPLAY_TEXT for candidate in candidates):
            self._finish_failed(run_id, reason_code="invalid_output", started=started)
            raise ValidationFailedError("模型回傳超過大小限制的劇本，未截斷、未建立候選。")

        result = execution.aggregate_generation_result(fallback_model=model)
        latency_ms = max(int(result.latency_ms), int((time.monotonic() - started) * 1000))

        try:
            revision, preserved_revision = self._finish_pipeline_candidates(
                adaptation_id=adaptation_id,
                run_id=run_id,
                source_snapshot_sha256=source.snapshot.sha256,
                expected_working_revision_id=request.expected_working_revision_id,
                execution=execution,
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
        return self._outcome(
            adaptation_id,
            run_id,
            revision,
            preserved_revision=preserved_revision,
            assurance=pipeline,
        )




    def _finish_failed(
        self,
        run_id: str,
        *,
        reason_code: str,
        started: float,
        assurance: ProsePipelineResult | None = None,
    ) -> None:
        completed_at = utc_now_iso()
        latency_ms = max(0, int((time.monotonic() - started) * 1000))
        with self._transaction() as session:
            runs = AdaptationGenerationRunRepository(session)
            run = runs.get(run_id)
            if run is None:
                raise NotFoundError(f"找不到劇本 generation run：{run_id}")
            if not runs.finalize_failed(
                run_id,
                reason_code=reason_code,
                completed_at=completed_at,
                latency_ms=latency_ms,
                assurance_json=(
                    None
                    if assurance is None
                    else _assurance_json(
                        assurance,
                        selected_candidate="none",
                    )
                ),
            ):
                raise ConflictError("劇本 generation run 無法完成失敗紀錄。")

    def _finish_cancelled(
        self,
        run_id: str,
        *,
        started: float,
        assurance: ProsePipelineResult,
    ) -> None:
        completed_at = utc_now_iso()
        latency_ms = max(0, int((time.monotonic() - started) * 1000))
        with self._transaction() as session:
            runs = AdaptationGenerationRunRepository(session)
            run = runs.get(run_id)
            if run is None:
                raise NotFoundError(f"找不到劇本 generation run：{run_id}")
            if not runs.finalize_cancelled(
                run_id,
                completed_at=completed_at,
                latency_ms=latency_ms,
                assurance_json=_assurance_json(
                    assurance,
                    selected_candidate="none",
                ),
            ):
                raise ConflictError("劇本 generation run 無法完成取消紀錄。")


    def _finish_pipeline_candidates(
        self,
        *,
        adaptation_id: str,
        run_id: str,
        source_snapshot_sha256: str,
        expected_working_revision_id: str | None,
        execution: ScreenplayPipelineExecution,
        result: GenerationResult,
        latency_ms: int,
    ) -> tuple[AdaptationRevisionRecord, AdaptationRevisionRecord | None]:
        pipeline = execution.pipeline
        accumulated = pipeline.accumulated_candidate
        if accumulated is None:
            raise ValidationFailedError("劇本 pipeline 沒有可保存的候選。")
        selected_candidate = pipeline.condensed_candidate or accumulated
        selected_decision = pipeline.condensation_decision or pipeline.decision
        selected_status = "complete" if selected_decision.domain_complete else "partial"
        completed_at = utc_now_iso()
        output_hash = utf8_sha256(selected_candidate.text)
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

            preserved_revision: AdaptationRevisionRecord | None = None
            selected_parent_id = expected_working_revision_id
            if pipeline.condensed_candidate is not None:
                preserved_revision = self._new_pipeline_revision(
                    revisions=revisions,
                    root=root,
                    run_id=run_id,
                    candidate=accumulated,
                    completion_status="partial",
                    parent_revision_id=expected_working_revision_id,
                    reason_code=pipeline.decision.reason.value,
                    pipeline=pipeline,
                    created_at=completed_at,
                    label="AI 劇本原始候選（已另建濃縮候選）",
                )
                revisions.add(preserved_revision)
                session.flush()
                selected_parent_id = preserved_revision.id

            revision = self._new_pipeline_revision(
                revisions=revisions,
                root=root,
                run_id=run_id,
                candidate=selected_candidate,
                completion_status=selected_status,
                parent_revision_id=selected_parent_id,
                reason_code=selected_decision.reason.value,
                pipeline=pipeline,
                created_at=completed_at,
                label=(
                    "AI 劇本濃縮候選" if pipeline.condensed_candidate is not None else "AI 劇本候選"
                ),
            )
            revisions.add(revision)
            session.flush()
            if not runs.finalize_succeeded(
                run_id,
                resulting_revision_id=revision.id,
                output_sha256=output_hash,
                prompt_tokens=result.usage.prompt_tokens,
                completion_tokens=result.usage.completion_tokens,
                completed_at=completed_at,
                latency_ms=latency_ms,
                terminal_status=("succeeded" if selected_status == "complete" else "partial"),
                reason_code=(
                    "" if selected_status == "complete" else selected_decision.reason.value
                ),
                assurance_json=_assurance_json(
                    pipeline,
                    selected_candidate=(
                        "condensed" if pipeline.condensed_candidate is not None else "accumulated"
                    ),
                ),
            ):
                raise ConflictError("劇本 generation run 已被其他操作完成。")
            session.flush()
            if not roots.compare_and_set_working(
                adaptation_id,
                expected_revision_id=expected_working_revision_id,
                new_revision_id=revision.id,
                updated_at=completed_at,
            ):
                raise _WorkingHeadConflict("劇本 working 版本已變更，未覆寫其他操作。")
        return revision, preserved_revision

    def _new_pipeline_revision(
        self,
        *,
        revisions: AdaptationRevisionRepository,
        root: AdaptationRecord,
        run_id: str,
        candidate: ProseCandidate,
        completion_status: str,
        parent_revision_id: str | None,
        reason_code: str,
        pipeline: ProsePipelineResult,
        created_at: str,
        label: str,
    ) -> AdaptationRevisionRecord:
        measurement = candidate.measurement
        return AdaptationRevisionRecord(
            id=new_id(),
            adaptation_id=root.id,
            project_id=root.project_id,
            version_number=revisions.next_version_number(root.id),
            parent_revision_id=parent_revision_id,
            screenplay_text=candidate.text,
            screenplay_sha256=candidate.text_sha256,
            origin=self._provider_name,
            completion_status=completion_status,
            generation_run_id=run_id,
            change_note=(
                f"{label}；長度 {completion_status}/{reason_code}；"
                f"{measurement.screenplay_pages_estimate:.3f} 頁估算、"
                f"{measurement.words} words、{measurement.visible_chars} 可見字元；"
                f"logical calls {pipeline.progress.provider_attempts}、"
                f"runtime attempts {pipeline.progress.runtime_provider_attempts}、"
                f"continuations {pipeline.progress.continuations_used}；尚未正式接受。"
            ),
            created_at=created_at,
        )

    def _outcome(
        self,
        adaptation_id: str,
        run_id: str,
        revision: AdaptationRevisionRecord | None,
        *,
        preserved_revision: AdaptationRevisionRecord | None = None,
        assurance: ProsePipelineResult | None = None,
    ) -> ScreenplayGenerationOutcome:
        with self._session_factory() as session:
            root = AdaptationRepository(session).get(adaptation_id)
            run = AdaptationGenerationRunRepository(session).get(run_id)
            if root is None or run is None:
                raise NotFoundError("劇本 generation outcome 無法重建。")
            health = _source_health(session, root)
        return ScreenplayGenerationOutcome(
            adaptation=root,
            run=run,
            revision=revision,
            source_health=health,
            preserved_revision=preserved_revision,
            assurance=assurance,
        )


def _provider_failure_code(error: ProviderError) -> str:
    if isinstance(error, RequestCancelledError):
        return "provider_error"
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


def _sanitized_provider_error(error: ProviderError) -> ProviderError:
    """Keep the normalized type/retry contract without retaining remote text."""

    if isinstance(error, ProviderAuthenticationError):
        return ProviderAuthenticationError("劇本 provider 驗證失敗。")
    if isinstance(error, ProviderQuotaError):
        return ProviderQuotaError("劇本 provider 配額不足。")
    if isinstance(error, ProviderRateLimitError):
        return ProviderRateLimitError("劇本 provider 暫時限制請求頻率。")
    if isinstance(error, ProviderTimeoutError):
        return ProviderTimeoutError("劇本 provider 已逾時。")
    if isinstance(error, ProviderUnavailableError):
        return ProviderUnavailableError("劇本 provider 暫時無法連線。")
    if isinstance(error, ModelNotFoundError):
        return ModelNotFoundError("劇本 provider 找不到指定模型。")
    if isinstance(error, RequestCancelledError):
        return RequestCancelledError("作者已取消劇本生成。")
    return ProviderError(
        "劇本 provider 失敗；未保留遠端錯誤內容。",
        retriable=error.retriable,
    )






def _assurance_json(
    assurance: ProsePipelineResult,
    *,
    selected_candidate: str,
) -> str:
    """Serialize the prose-free N3 result into the dedicated evidence column."""

    selected = {
        "none": None,
        "accumulated": assurance.accumulated_candidate,
        "condensed": assurance.condensed_candidate,
    }[selected_candidate]
    decision = (
        assurance.condensation_decision
        if selected_candidate == "condensed" and assurance.condensation_decision is not None
        else assurance.decision
    )
    payload: dict[str, object] = {
        "schema_version": SCREENPLAY_PROSE_ADAPTER_VERSION,
        "selected_candidate": selected_candidate,
        "selected_output_sha256": None if selected is None else selected.text_sha256,
        "selected_reason": decision.reason.value,
        "domain_complete": decision.domain_complete,
        "result": assurance.model_dump(mode="json"),
    }
    _assert_hash_only_assurance(payload)
    return canonical_json(payload)


_FORBIDDEN_ASSURANCE_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "openai_api_key",
        "authorization",
        "authorization_header",
        "bearer_token",
        "access_token",
        "refresh_token",
        "credential",
        "credentials",
        "credential_value",
        "text",
        "prompt",
        "system",
        "instruction",
        "context_text",
        "rolling_summary",
        "protected_facts",
        "raw",
        "raw_response",
        "content_text",
    }
)


def _assert_hash_only_assurance(payload: object) -> None:
    """Fail closed if a future pipeline dump adds a raw or credential field."""

    if isinstance(payload, dict):
        for key, value in payload.items():
            if key.casefold() in _FORBIDDEN_ASSURANCE_KEYS:
                raise ValueError("screenplay assurance evidence must remain hash-only")
            _assert_hash_only_assurance(value)
    elif isinstance(payload, list | tuple):
        for value in payload:
            _assert_hash_only_assurance(value)


__all__ = [
    "SCREENPLAY_INPUT_CONTRACT_VERSION",
    "AdaptationView",
    "CurrentScreenplaySource",
    "GenerateScreenplayRequest",
    "ScreenplayAdaptationService",
    "ScreenplayGenerationOutcome",
    "ScreenplayGenerationService",
    "screenplay_length_spec_for_minutes",
]
