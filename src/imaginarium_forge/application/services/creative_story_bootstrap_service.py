"""Atomically create the first editable chapter and Scene Card from Launchpad.

This is deliberately a planning-only application slice:

* exact Foundation and Canon source references are reloaded and validated;
* adult sexual modes live-audit every exact participant for STORY_GENERATION
  before any chapter/scene write transaction starts;
* Chapter + ChapterPlan v1 + Scene + SceneCard v1 + participant pins commit in
  one transaction and remain unaccepted;
* no provider is called and no prose draft is created;
* deterministic IDs make a lost-response retry safely replayable.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from pydantic import ValidationError

from imaginarium_forge.application.errors import (
    ConflictError,
    NotFoundError,
    ValidationFailedError,
)
from imaginarium_forge.application.services.base import ServiceBase, SessionProvider
from imaginarium_forge.domain.common.enums import (
    ContentIntensity,
    ContentRating,
    RequestType,
)
from imaginarium_forge.domain.common.ids import utc_now_iso
from imaginarium_forge.domain.creative.story_bootstrap import (
    CreativeStoryBootstrapRequest,
    CreativeStoryBootstrapResult,
)
from imaginarium_forge.domain.prompt.content_mode import derives_adult
from imaginarium_forge.domain.story.models import (
    ChapterPlan,
    SceneCard,
    SceneConflict,
    SceneGoal,
    SceneParticipant,
    StoryBible,
    StoryOutline,
    StoryRequirement,
)
from imaginarium_forge.infrastructure.db.repositories.characters import (
    CharacterRepository,
)
from imaginarium_forge.infrastructure.db.repositories.projects import ProjectRepository
from imaginarium_forge.infrastructure.db.repositories.story_repos import (
    ChapterRecord,
    ParticipantRecord,
    SceneCardParticipantRepository,
    SceneCardVersionRepository,
    SceneRecord,
    StoryChapterRepository,
    StorySceneRepository,
    VersionedEntityRepository,
    VersionRecord,
)

_ID_NAMESPACE = uuid.UUID("31651a88-e213-5b52-93ea-9918ec3b525b")
_CHANGE_NOTE = "創作起點建立的第一章／第一場景工作草稿"


@dataclass(frozen=True, slots=True)
class _FoundationSources:
    requirement: StoryRequirement
    bible: StoryBible
    outline: StoryOutline


@dataclass(frozen=True, slots=True)
class _ExpectedBootstrap:
    chapter_id: str
    plan_version_id: str
    scene_id: str
    card_version_id: str
    participant_ids: tuple[str, ...]
    chapter_title: str
    scene_title: str
    plan: ChapterPlan
    card: SceneCard


def _clip(value: str, limit: int) -> str:
    return value.strip()[:limit]


def _unique_texts(*groups: tuple[str, ...], limit: int, item_limit: int) -> tuple[str, ...]:
    values: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for raw in group:
            value = _clip(raw, item_limit)
            if value and value not in seen:
                values.append(value)
                seen.add(value)
            if len(values) >= limit:
                return tuple(values)
    return tuple(values)


def _content_intensity(requirement: StoryRequirement) -> ContentIntensity:
    mode = requirement.content_mode.value
    if mode == "explicit_adult":
        return ContentIntensity.EXPLICIT
    if mode == "suggestive":
        return ContentIntensity.SUGGESTIVE
    if mode == "violent":
        return ContentIntensity.VIOLENT
    if mode == "horror":
        return ContentIntensity.HORROR
    if mode == "dark":
        return ContentIntensity.DARK
    return ContentIntensity.GENERAL


class CreativeStoryBootstrapService(ServiceBase):
    """Create one deterministic first-chapter/first-scene planning working set."""

    def __init__(
        self,
        session_factory: SessionProvider,
        *,
        eligibility: object | None = None,
    ) -> None:
        super().__init__(session_factory)
        self._eligibility = eligibility

    def bootstrap(self, request: CreativeStoryBootstrapRequest) -> CreativeStoryBootstrapResult:
        try:
            request = CreativeStoryBootstrapRequest.model_validate(request.model_dump(mode="json"))
        except ValidationError as exc:  # defensive for untyped boundary callers
            raise ValidationFailedError(f"第一場景起稿輸入無效：{exc}") from exc

        sources = self._read_only(lambda session: self._load_and_validate_sources(session, request))
        expected = self._build_expected(request, sources)

        evaluation_ids: tuple[str, ...] = ()
        if derives_adult(sources.requirement.content_mode):
            if self._eligibility is None:
                raise ValidationFailedError("成人故事起稿需要 live 資格驗證服務")
            allowed, results, audit_ids = self._eligibility.evaluate_many_audited(  # type: ignore[attr-defined]
                participants=list(request.participant_manifest.exact_pairs),
                request_type=RequestType.STORY_GENERATION,
                content_rating=ContentRating.MATURE,
                content_intensity=_content_intensity(sources.requirement),
                adult_content_requested=True,
            )
            if not allowed:
                blockers = "；".join(result.message for result in results if not result.allowed)
                raise ValidationFailedError(
                    f"成人故事起稿資格驗證未通過：{blockers or '未提供理由'}"
                )
            evaluation_ids = tuple(audit_ids)

        try:
            replayed = self._write_or_replay(request, expected)
        except ConflictError:
            # A simultaneous identical command can win the deterministic-key
            # race after our initial read.  Only an exact, pristine full-set
            # readback converts that uniqueness conflict into a safe replay.
            if self._read_only(
                lambda session: self._existing_matches(session, request, expected, require_any=True)
            ):
                replayed = True
            else:
                raise

        return CreativeStoryBootstrapResult(
            idempotency_fingerprint=request.idempotency_fingerprint,
            project_id=request.project_id,
            outline_id=request.foundation.outline_id,
            outline_version_id=request.foundation.outline_version_id,
            chapter_id=expected.chapter_id,
            chapter_plan_version_id=expected.plan_version_id,
            scene_id=expected.scene_id,
            scene_card_version_id=expected.card_version_id,
            participant_manifest_fingerprint=request.participant_manifest.fingerprint,
            eligibility_evaluation_ids=evaluation_ids,
            replayed=replayed,
        )

    # --------------------------------------------------------- source proof
    @staticmethod
    def _load_and_validate_sources(
        session: object, request: CreativeStoryBootstrapRequest
    ) -> _FoundationSources:
        project_id = request.project_id
        if ProjectRepository(session).get(project_id) is None:  # type: ignore[arg-type]
            raise NotFoundError(f"找不到專案：{project_id}")

        foundation = request.foundation
        required_text = {
            "request_fingerprint": foundation.request_fingerprint,
            "requirement_id": foundation.requirement_id,
            "requirement_version_id": foundation.requirement_version_id,
            "bible_id": foundation.bible_id,
            "bible_version_id": foundation.bible_version_id,
            "outline_id": foundation.outline_id,
            "outline_version_id": foundation.outline_version_id,
        }
        missing = [name for name, value in required_text.items() if not value.strip()]
        if missing:
            raise ValidationFailedError("故事基礎缺少精確來源 ID：" + "、".join(missing))

        loaded: dict[str, tuple[object, VersionRecord]] = {}
        for kind, entity_id, version_id, label in (
            (
                "requirement",
                foundation.requirement_id,
                foundation.requirement_version_id,
                "故事需求",
            ),
            ("bible", foundation.bible_id, foundation.bible_version_id, "故事聖經"),
            (
                "outline",
                foundation.outline_id,
                foundation.outline_version_id,
                "故事大綱",
            ),
        ):
            repo = VersionedEntityRepository(session, kind)  # type: ignore[arg-type]
            entity = repo.get_entity(entity_id)
            version = repo.get_version(version_id)
            if entity is None:
                raise NotFoundError(f"找不到{label}：{entity_id}")
            if version is None:
                raise NotFoundError(f"找不到{label}版本：{version_id}")
            if entity.project_id != project_id or version.project_id != project_id:
                raise ValidationFailedError(f"{label}不可跨專案用於第一場景起稿")
            if version.parent_id != entity.id:
                raise ValidationFailedError(
                    f"{label}版本 {version.id} 不屬於指定 parent {entity.id}"
                )
            loaded[kind] = (entity, version)

        requirement_version = loaded["requirement"][1]
        bible_version = loaded["bible"][1]
        outline_version = loaded["outline"][1]
        if bible_version.payload.get("requirement_version_id") != requirement_version.id:
            raise ValidationFailedError("故事聖經未精確連結此 StoryFoundation 的需求版本")
        if outline_version.payload.get("bible_version_id") != bible_version.id:
            raise ValidationFailedError("故事大綱未精確連結此 StoryFoundation 的聖經版本")

        try:
            requirement = StoryRequirement.model_validate_json(
                str(requirement_version.payload["requirement_json"])
            )
            bible = StoryBible.model_validate_json(str(bible_version.payload["bible_json"]))
            outline = StoryOutline.model_validate_json(str(outline_version.payload["outline_json"]))
        except (KeyError, ValidationError) as exc:
            raise ValidationFailedError("StoryFoundation 的規劃 JSON 無法驗證") from exc
        if not requirement.concept.strip():
            raise ValidationFailedError("故事需求 concept 為空，不能建立第一場景")

        # Validate every manifest pair and ownership before adult audit writes.
        characters = CharacterRepository(session)  # type: ignore[arg-type]
        names_by_pair: dict[tuple[str, str], str] = {}
        for pin in request.participant_manifest.participants:
            character = characters.get(pin.character_id)
            canon_version = characters.get_version(pin.character_version_id)
            if character is None:
                raise NotFoundError(f"找不到角色：{pin.character_id}")
            if canon_version is None:
                raise NotFoundError(f"找不到角色版本：{pin.character_version_id}")
            if character.project_id != project_id:
                raise ValidationFailedError("participant manifest 含其他專案角色")
            if canon_version.character_id != character.id:
                raise ValidationFailedError("participant manifest 的角色版本不屬於指定角色")
            names_by_pair[(character.id, canon_version.id)] = character.name

        bible_entries = tuple(entry for entry in bible.characters if entry.canon_character_id)
        bible_pairs = tuple(
            (entry.canon_character_id, entry.canon_character_version_id) for entry in bible_entries
        )
        if bible_pairs != request.participant_manifest.exact_pairs:
            raise ValidationFailedError(
                "participant manifest 必須與 StoryFoundation Bible 的精確 Canon roster 完全一致"
            )
        for entry, pin in zip(
            bible_entries, request.participant_manifest.participants, strict=True
        ):
            expected_name = names_by_pair[(pin.character_id, pin.character_version_id)]
            expected_role = pin.role or ("主角" if pin.is_primary else "參與角色")
            if entry.name != expected_name or entry.role != expected_role:
                raise ValidationFailedError(
                    "participant manifest 的名稱／role／primary 與 StoryFoundation Bible 不一致"
                )

        return _FoundationSources(
            requirement=requirement,
            bible=bible,
            outline=outline,
        )

    # ------------------------------------------------------- draft builder
    @staticmethod
    def _build_expected(
        request: CreativeStoryBootstrapRequest, sources: _FoundationSources
    ) -> _ExpectedBootstrap:
        fingerprint = request.idempotency_fingerprint

        def stable_id(label: str) -> str:
            return str(uuid.uuid5(_ID_NAMESPACE, f"{fingerprint}:{label}"))

        first_act = sources.outline.acts[0] if sources.outline.acts else None
        purpose = first_act.purpose if first_act is not None else sources.requirement.concept
        goal = first_act.goal if first_act is not None else sources.requirement.central_conflict
        turning_point = first_act.turning_point if first_act is not None else ""
        scene_intentions = _unique_texts(
            tuple(
                value
                for value in (purpose, goal, turning_point, *sources.outline.arc_beats)
                if value
            ),
            limit=32,
            item_limit=1000,
        )
        forbidden = _unique_texts(
            sources.bible.narrative_contract.forbidden_reveals,
            sources.outline.withheld,
            sources.requirement.must_avoid,
            limit=32,
            item_limit=1000,
        )
        plan = ChapterPlan(
            chapter_number=1,
            title="第一章",
            purpose=_clip(purpose, 1000),
            pov_character_id=request.participant_manifest.primary.character_id,
            goal=_clip(goal or sources.requirement.concept, 1000),
            conflict=_clip(sources.requirement.central_conflict, 1000),
            outcome=_clip(turning_point, 1000),
            scene_intentions=scene_intentions,
            reveals=_unique_texts(sources.outline.reveals, limit=32, item_limit=1000),
            forbidden_reveals=forbidden,
            target_word_count=0,
        )

        participants = tuple(
            SceneParticipant(
                character_id=pin.character_id,
                character_version_id=pin.character_version_id,
                role=pin.role,
            )
            for pin in request.participant_manifest.participants
        )
        primary_index = next(
            index
            for index, pin in enumerate(request.participant_manifest.participants)
            if pin.is_primary
        )
        primary_entry = next(
            entry
            for entry in sources.bible.characters
            if entry.canon_character_id == request.participant_manifest.primary.character_id
        )
        location = (
            sources.bible.locations[0] if sources.bible.locations else sources.requirement.setting
        )
        beats = _unique_texts(
            scene_intentions,
            sources.requirement.must_include,
            limit=24,
            item_limit=1000,
        )
        card = SceneCard(
            pov_character=participants[primary_index],
            participants=participants,
            location=_clip(location, 200),
            scene_goal=SceneGoal(
                protagonist=_clip(primary_entry.goal or goal, 1000),
                narrative=_clip(goal or sources.requirement.concept, 1000),
            ),
            conflict=SceneConflict(
                external=_clip(sources.requirement.central_conflict, 1000),
                internal=_clip(primary_entry.fear, 1000),
            ),
            beats=beats,
            turning_point=_clip(turning_point, 1000),
            forbidden_reveals=forbidden,
            must_include=_unique_texts(sources.requirement.must_include, limit=32, item_limit=1000),
            avoid=_unique_texts(sources.requirement.must_avoid, limit=32, item_limit=1000),
            target_word_count=0,
            content_mode=sources.requirement.content_mode,
        )
        story_title = _clip(sources.bible.title or "故事", 180)
        scene_label = _clip(location or "開場", 170)
        return _ExpectedBootstrap(
            chapter_id=stable_id("chapter"),
            plan_version_id=stable_id("chapter-plan-v1"),
            scene_id=stable_id("scene"),
            card_version_id=stable_id("scene-card-v1"),
            participant_ids=tuple(
                stable_id(f"participant:{pin.slot_id}")
                for pin in request.participant_manifest.participants
            ),
            chapter_title=f"{story_title}｜第一章",
            scene_title=f"第一場景｜{scene_label}",
            plan=plan,
            card=card,
        )

    # ----------------------------------------------------- transaction/retry
    def _write_or_replay(
        self,
        request: CreativeStoryBootstrapRequest,
        expected: _ExpectedBootstrap,
    ) -> bool:
        with self._transaction() as session:
            if self._existing_matches(session, request, expected, require_any=False):
                return True

            chapters = StoryChapterRepository(session)
            first_chapter = next(
                (
                    chapter
                    for chapter in chapters.list_for_outline(request.foundation.outline_id)
                    if chapter.chapter_number == 1
                ),
                None,
            )
            if first_chapter is not None:
                raise ConflictError("此故事大綱已有第一章，且不是同一個可重試的 Launchpad 起稿命令")

            now = utc_now_iso()
            chapters.add(
                ChapterRecord(
                    id=expected.chapter_id,
                    story_outline_id=request.foundation.outline_id,
                    project_id=request.project_id,
                    chapter_number=1,
                    title=expected.chapter_title,
                    working_head_plan_version_id=None,
                    accepted_plan_version_id=None,
                    created_at=now,
                    updated_at=now,
                )
            )
            session.flush()

            plans = VersionedEntityRepository(session, "chapter_plan")
            plans.add_version(
                VersionRecord(
                    id=expected.plan_version_id,
                    parent_id=expected.chapter_id,
                    project_id=request.project_id,
                    version_number=1,
                    change_note=_CHANGE_NOTE,
                    accepted=False,
                    created_at=now,
                    payload={"plan_json": expected.plan.model_dump_json()},
                )
            )
            session.flush()
            if not chapters.update_fields(
                expected.chapter_id,
                working_head_plan_version_id=expected.plan_version_id,
                updated_at=now,
            ):
                raise ValidationFailedError("無法設定第一章的 ChapterPlan working head")

            scenes = StorySceneRepository(session)
            scenes.add(
                SceneRecord(
                    id=expected.scene_id,
                    story_chapter_id=expected.chapter_id,
                    project_id=request.project_id,
                    scene_number=1,
                    title=expected.scene_title,
                    working_head_card_version_id=None,
                    accepted_card_version_id=None,
                    working_draft_id=None,
                    accepted_draft_id=None,
                    summary_text="",
                    created_at=now,
                    updated_at=now,
                )
            )
            session.flush()

            cards = SceneCardVersionRepository(session)
            cards.add(
                VersionRecord(
                    id=expected.card_version_id,
                    parent_id=expected.scene_id,
                    project_id=request.project_id,
                    version_number=1,
                    change_note=_CHANGE_NOTE,
                    accepted=False,
                    created_at=now,
                    payload={
                        "card_json": expected.card.model_dump_json(),
                        "content_mode": expected.card.content_mode.value,
                    },
                )
            )
            session.flush()

            pins = SceneCardParticipantRepository(session)
            primary_id = request.participant_manifest.primary.character_id
            for position, (record_id, pin) in enumerate(
                zip(
                    expected.participant_ids,
                    request.participant_manifest.participants,
                    strict=True,
                )
            ):
                pins.add(
                    ParticipantRecord(
                        id=record_id,
                        scene_card_version_id=expected.card_version_id,
                        project_id=request.project_id,
                        character_id=pin.character_id,
                        character_version_id=pin.character_version_id,
                        role=pin.role,
                        is_pov=pin.character_id == primary_id,
                        position=position,
                    )
                )
            if not scenes.update_fields(
                expected.scene_id,
                working_head_card_version_id=expected.card_version_id,
                updated_at=now,
            ):
                raise ValidationFailedError("無法設定第一場景的 SceneCard working head")
            session.flush()
        return False

    @staticmethod
    def _existing_matches(
        session: object,
        request: CreativeStoryBootstrapRequest,
        expected: _ExpectedBootstrap,
        *,
        require_any: bool,
    ) -> bool:
        chapters = StoryChapterRepository(session)  # type: ignore[arg-type]
        plans = VersionedEntityRepository(session, "chapter_plan")  # type: ignore[arg-type]
        scenes = StorySceneRepository(session)  # type: ignore[arg-type]
        cards = SceneCardVersionRepository(session)  # type: ignore[arg-type]
        pin_repo = SceneCardParticipantRepository(session)  # type: ignore[arg-type]
        chapter = chapters.get(expected.chapter_id)
        plan_version = plans.get_version(expected.plan_version_id)
        scene = scenes.get(expected.scene_id)
        card_version = cards.get(expected.card_version_id)
        present = tuple(item is not None for item in (chapter, plan_version, scene, card_version))
        if not any(present):
            if require_any:
                return False
            return False
        if not all(present):
            raise ConflictError("第一場景起稿的 deterministic aggregate 僅部分存在")

        assert chapter is not None
        assert plan_version is not None
        assert scene is not None
        assert card_version is not None
        try:
            stored_plan = ChapterPlan.model_validate_json(str(plan_version.payload["plan_json"]))
            stored_card = SceneCard.model_validate_json(str(card_version.payload["card_json"]))
        except (KeyError, ValidationError) as exc:
            raise ConflictError("既有第一場景起稿 payload 無法驗證") from exc

        base_matches = (
            chapter.story_outline_id == request.foundation.outline_id
            and chapter.project_id == request.project_id
            and chapter.chapter_number == 1
            and chapter.title == expected.chapter_title
            and chapter.working_head_plan_version_id == expected.plan_version_id
            and chapter.accepted_plan_version_id is None
            and plan_version.parent_id == expected.chapter_id
            and plan_version.project_id == request.project_id
            and plan_version.version_number == 1
            and plan_version.change_note == _CHANGE_NOTE
            and not plan_version.accepted
            and stored_plan == expected.plan
            and scene.story_chapter_id == expected.chapter_id
            and scene.project_id == request.project_id
            and scene.scene_number == 1
            and scene.title == expected.scene_title
            and scene.working_head_card_version_id == expected.card_version_id
            and scene.accepted_card_version_id is None
            and scene.working_draft_id is None
            and scene.accepted_draft_id is None
            and scene.summary_text == ""
            and card_version.parent_id == expected.scene_id
            and card_version.project_id == request.project_id
            and card_version.version_number == 1
            and card_version.change_note == _CHANGE_NOTE
            and not card_version.accepted
            and card_version.payload.get("content_mode") == expected.card.content_mode.value
            and stored_card == expected.card
        )
        stored_pins = pin_repo.list_for_card_version(expected.card_version_id)
        pins_match = len(stored_pins) == len(request.participant_manifest.participants)
        if pins_match:
            primary_id = request.participant_manifest.primary.character_id
            pins_match = all(
                stored.id == expected.participant_ids[position]
                and stored.scene_card_version_id == expected.card_version_id
                and stored.project_id == request.project_id
                and stored.character_id == pin.character_id
                and stored.character_version_id == pin.character_version_id
                and stored.role == pin.role
                and stored.is_pov == (pin.character_id == primary_id)
                and stored.position == position
                for position, (stored, pin) in enumerate(
                    zip(
                        stored_pins,
                        request.participant_manifest.participants,
                        strict=True,
                    )
                )
            )
        if not base_matches or not pins_match:
            raise ConflictError("既有 deterministic 第一場景起稿與本次命令不完全一致；拒絕覆寫")
        return True
