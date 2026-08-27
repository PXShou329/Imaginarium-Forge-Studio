"""Story planning services (Phase 3 C5, spec §34–§37).

Five services over one shared versioning discipline:

- ``StoryRequirementService`` — quick/detailed requirement capture;
- ``StoryBibleService`` — accepted bibles are immutable;
- ``StoryOutlineService`` — structure profile is offered, never forced;
- ``ChapterPlanService`` — chapters own append-only plan versions;
- ``SceneCardService`` — the generation unit; adult modes are gated here.

Shared rules enforced in every one of them:

- project ownership is validated before any write (§32);
- versions are append-only; accepting one freezes it (DB triggers back this);
- the UI never touches repositories or raw SQL — only these services.
"""

from __future__ import annotations

import json
import uuid

from imaginarium_forge.application.errors import (
    NotFoundError,
    ValidationFailedError,
)
from imaginarium_forge.application.services.base import ServiceBase, SessionProvider
from imaginarium_forge.domain.common.ids import utc_now_iso
from imaginarium_forge.domain.prompt.content_mode import ContentMode, derives_adult
from imaginarium_forge.domain.story.models import (
    ChapterPlan,
    SceneCard,
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
    DraftSummaryRecord,
    EntityRecord,
    ParticipantRecord,
    SceneCardParticipantRepository,
    SceneCardVersionRepository,
    SceneDraftSummaryRepository,
    SceneRecord,
    StoryChapterRepository,
    StorySceneRepository,
    VersionedEntityRepository,
    VersionRecord,
)


def _require_project(session: object, project_id: str) -> None:
    if ProjectRepository(session).get(project_id) is None:  # type: ignore[arg-type]
        raise NotFoundError(f"找不到專案：{project_id}")


class _VersionedService(ServiceBase):
    """Shared parent/version lifecycle for the four planning entities."""

    kind: str = ""
    label: str = ""

    def create(self, *, project_id: str, title: str) -> EntityRecord:
        if not title.strip():
            raise ValidationFailedError(f"{self.label}標題為必填")
        record = EntityRecord(
            id=str(uuid.uuid4()),
            project_id=project_id,
            title=title.strip(),
            working_head_version_id=None,
            accepted_version_id=None,
            created_at=utc_now_iso(),
            updated_at=utc_now_iso(),
        )
        with self._transaction() as session:
            _require_project(session, project_id)
            VersionedEntityRepository(session, self.kind).add_entity(record)
        return record

    def get(self, entity_id: str) -> EntityRecord:
        record = self._read_only(
            lambda s: VersionedEntityRepository(s, self.kind).get_entity(entity_id)
        )
        if record is None:
            raise NotFoundError(f"找不到{self.label}：{entity_id}")
        return record

    def list_for_project(self, project_id: str) -> list[EntityRecord]:
        return self._read_only(
            lambda s: VersionedEntityRepository(s, self.kind).list_entities(project_id)
        )

    def list_versions(self, entity_id: str) -> list[VersionRecord]:
        return self._read_only(
            lambda s: VersionedEntityRepository(s, self.kind).list_versions(entity_id)
        )

    def get_version(self, version_id: str) -> VersionRecord:
        record = self._read_only(
            lambda s: VersionedEntityRepository(s, self.kind).get_version(version_id)
        )
        if record is None:
            raise NotFoundError(f"找不到{self.label}版本：{version_id}")
        return record

    def accept_version(self, version_id: str) -> None:
        """A3-07: accepting freezes the version AND moves the parent's
        ``accepted_version_id``. Generation defaults to that pointer."""
        with self._transaction() as session:
            repo = VersionedEntityRepository(session, self.kind)
            version = repo.get_version(version_id)
            if version is None:
                raise NotFoundError(f"找不到{self.label}版本：{version_id}")
            repo.accept_version(version_id)
            repo.set_accepted_version(version.parent_id, version_id, utc_now_iso())

    def accepted_version(self, entity_id: str) -> VersionRecord | None:
        """The version generation uses by default (None until one is accepted)."""
        entity = self.get(entity_id)
        if not entity.accepted_version_id:
            return None
        return self.get_version(entity.accepted_version_id)

    def _require_same_project_version(
        self, entity_id: str, kind: str, version_id: str, label: str
    ) -> None:
        """A3-04: reject a link that crosses a project boundary.

        Relying on UI filtering was the original defect — a direct service
        call happily linked Project B's Bible to Project A's requirement.
        """
        owner = self.get(entity_id)
        linked = self._read_only(
            lambda s: VersionedEntityRepository(s, kind).get_version(version_id)
        )
        if linked is None:
            raise NotFoundError(f"找不到{label}版本：{version_id}")
        if linked.project_id != owner.project_id:
            raise ValidationFailedError(
                f"不可跨專案連結{label}版本："
                f"{label}屬於專案 {linked.project_id}，"
                f"但目標屬於專案 {owner.project_id}"
            )

    def _append_version(
        self,
        entity_id: str,
        *,
        payload: dict[str, object],
        change_note: str,
    ) -> VersionRecord:
        with self._transaction() as session:
            repo = VersionedEntityRepository(session, self.kind)
            parent = repo.get_entity(entity_id)
            if parent is None:
                raise NotFoundError(f"找不到{self.label}：{entity_id}")
            record = VersionRecord(
                id=str(uuid.uuid4()),
                parent_id=entity_id,
                project_id=parent.project_id,
                version_number=repo.next_version_number(entity_id),
                change_note=change_note,
                accepted=False,
                created_at=utc_now_iso(),
                payload=payload,
            )
            repo.add_version(record)
            session.flush()
            # A3-07: a new version is a working draft. It becomes the
            # generation default only when the author explicitly accepts it.
            repo.set_working_head(entity_id, record.id, utc_now_iso())
        return record


# ========================================================== requirement
class StoryRequirementService(_VersionedService):
    kind = "requirement"
    label = "故事需求"

    def add_version(
        self,
        requirement_id: str,
        *,
        requirement: StoryRequirement,
        source_text: str = "",
        structured_mode: bool = True,
        change_note: str = "",
    ) -> VersionRecord:
        if not requirement.concept.strip():
            raise ValidationFailedError("概念（concept）為必填")
        return self._append_version(
            requirement_id,
            payload={
                "requirement_json": requirement.model_dump_json(),
                "source_text": source_text,
                "structured_mode": int(structured_mode),
            },
            change_note=change_note,
        )

    def load(self, version_id: str) -> StoryRequirement:
        record = self.get_version(version_id)
        return StoryRequirement.model_validate_json(
            str(record.payload["requirement_json"])
        )


# =============================================================== bible
class StoryBibleService(_VersionedService):
    kind = "bible"
    label = "故事聖經"

    def add_version(
        self,
        bible_id: str,
        *,
        bible: StoryBible,
        requirement_version_id: str | None = None,
        change_note: str = "",
    ) -> VersionRecord:
        """A3-14: the linked requirement must be a real requirement VERSION
        owned by the SAME project. A composite FK enforces this in the
        database too; the check here produces a readable error first."""
        if requirement_version_id:
            self._require_same_project_version(
                bible_id, "requirement", requirement_version_id, "故事需求"
            )
        owner = self.get(bible_id)

        def validate_canon_links(session: object) -> None:
            repo = CharacterRepository(session)  # type: ignore[arg-type]
            for entry in bible.characters:
                character_id = entry.canon_character_id.strip()
                version_id = entry.canon_character_version_id.strip()
                if not character_id and not version_id:
                    continue
                character = repo.get(character_id)
                if character is None:
                    raise ValidationFailedError(
                        f"故事聖經角色「{entry.name}」引用的 Canon 角色不存在："
                        f"{character_id}"
                    )
                version = repo.get_version(version_id)
                if version is None:
                    raise ValidationFailedError(
                        f"故事聖經角色「{entry.name}」引用的 Canon 角色版本不存在："
                        f"{version_id}"
                    )
                if character.project_id != owner.project_id:
                    raise ValidationFailedError(
                        f"故事聖經角色「{entry.name}」不可引用其他專案的 Canon 角色"
                    )
                if version.character_id != character.id:
                    raise ValidationFailedError(
                        f"故事聖經角色「{entry.name}」的 Canon 版本不屬於指定角色"
                    )

        # Canon versions are immutable.  Validate every typed source reference
        # before the Bible version is written, so context construction can
        # never turn a ghost or cross-project ID into auditable provenance.
        self._read_only(validate_canon_links)
        return self._append_version(
            bible_id,
            payload={
                "bible_json": bible.model_dump_json(),
                "requirement_version_id": requirement_version_id,
            },
            change_note=change_note,
        )

    def load(self, version_id: str) -> StoryBible:
        record = self.get_version(version_id)
        return StoryBible.model_validate_json(str(record.payload["bible_json"]))


# ============================================================= outline
class StoryOutlineService(_VersionedService):
    kind = "outline"
    label = "大綱"

    def add_version(
        self,
        outline_id: str,
        *,
        outline: StoryOutline,
        bible_version_id: str | None = None,
        change_note: str = "",
    ) -> VersionRecord:
        if bible_version_id:
            self._require_same_project_version(
                outline_id, "bible", bible_version_id, "故事聖經"
            )
        return self._append_version(
            outline_id,
            payload={
                "outline_json": outline.model_dump_json(),
                "structure_profile": outline.structure_profile.value,
                "bible_version_id": bible_version_id,
            },
            change_note=change_note,
        )

    def load(self, version_id: str) -> StoryOutline:
        record = self.get_version(version_id)
        return StoryOutline.model_validate_json(str(record.payload["outline_json"]))


# ======================================================== chapter plan
class ChapterPlanService(ServiceBase):
    """Chapters live under an outline; each owns append-only plan versions."""

    label = "章節計畫"

    def create_chapter(
        self, *, outline_id: str, title: str = "", chapter_number: int | None = None
    ) -> ChapterRecord:
        with self._transaction() as session:
            outlines = VersionedEntityRepository(session, "outline")
            outline = outlines.get_entity(outline_id)
            if outline is None:
                raise NotFoundError(f"找不到大綱：{outline_id}")
            repo = StoryChapterRepository(session)
            number = chapter_number or repo.next_chapter_number(outline_id)
            if number <= 0:
                raise ValidationFailedError("章節編號必須為正整數")
            record = ChapterRecord(
                id=str(uuid.uuid4()),
                story_outline_id=outline_id,
                project_id=outline.project_id,
                chapter_number=number,
                title=title,
                working_head_plan_version_id=None,
                accepted_plan_version_id=None,
                created_at=utc_now_iso(),
                updated_at=utc_now_iso(),
            )
            repo.add(record)
        return record

    def list_chapters(self, outline_id: str) -> list[ChapterRecord]:
        return self._read_only(
            lambda s: StoryChapterRepository(s).list_for_outline(outline_id)
        )

    def get_chapter(self, chapter_id: str) -> ChapterRecord:
        record = self._read_only(lambda s: StoryChapterRepository(s).get(chapter_id))
        if record is None:
            raise NotFoundError(f"找不到章節：{chapter_id}")
        return record

    def add_plan_version(
        self, chapter_id: str, *, plan: ChapterPlan, change_note: str = ""
    ) -> VersionRecord:
        with self._transaction() as session:
            chapters = StoryChapterRepository(session)
            chapter = chapters.get(chapter_id)
            if chapter is None:
                raise NotFoundError(f"找不到章節：{chapter_id}")
            if plan.chapter_number != chapter.chapter_number:
                raise ValidationFailedError(
                    f"章節計畫的 chapter_number（{plan.chapter_number}）"
                    f"必須與章節本身（{chapter.chapter_number}）一致"
                )
            repo = VersionedEntityRepository(session, "chapter_plan")
            record = VersionRecord(
                id=str(uuid.uuid4()),
                parent_id=chapter_id,
                project_id=chapter.project_id,
                version_number=repo.next_version_number(chapter_id),
                change_note=change_note,
                accepted=False,
                created_at=utc_now_iso(),
                payload={"plan_json": plan.model_dump_json()},
            )
            repo.add_version(record)
            session.flush()
            chapters.update_fields(
                chapter_id,
                working_head_plan_version_id=record.id,
                updated_at=utc_now_iso(),
            )
        return record

    def list_plan_versions(self, chapter_id: str) -> list[VersionRecord]:
        return self._read_only(
            lambda s: VersionedEntityRepository(s, "chapter_plan").list_versions(
                chapter_id
            )
        )

    def load_plan(self, version_id: str) -> ChapterPlan:
        record = self._read_only(
            lambda s: VersionedEntityRepository(s, "chapter_plan").get_version(
                version_id
            )
        )
        if record is None:
            raise NotFoundError(f"找不到章節計畫版本：{version_id}")
        return ChapterPlan.model_validate_json(str(record.payload["plan_json"]))

    def accept_plan_version(self, version_id: str) -> None:
        with self._transaction() as session:
            repo = VersionedEntityRepository(session, "chapter_plan")
            version = repo.get_version(version_id)
            if version is None:
                raise NotFoundError(f"找不到章節計畫版本：{version_id}")
            repo.accept_version(version_id)
            StoryChapterRepository(session).update_fields(
                version.parent_id,
                accepted_plan_version_id=version_id,
                updated_at=utc_now_iso(),
            )


# =========================================================== scene card
class SceneCardService(ServiceBase):
    """Scenes + their append-only Scene Card versions (§37).

    Adult-content gating (§33) lives here: a sexual content mode requires
    EVERY participating character to be a Canon character version that the
    Phase 1 eligibility service allows. The LLM never authorizes anything.
    """

    def __init__(
        self, session_factory: SessionProvider, *, eligibility: object = None
    ) -> None:
        super().__init__(session_factory)
        self._eligibility = eligibility

    def create_scene(
        self, *, chapter_id: str, title: str = "", scene_number: int | None = None
    ) -> SceneRecord:
        with self._transaction() as session:
            chapters = StoryChapterRepository(session)
            chapter = chapters.get(chapter_id)
            if chapter is None:
                raise NotFoundError(f"找不到章節：{chapter_id}")
            repo = StorySceneRepository(session)
            number = scene_number or repo.next_scene_number(chapter_id)
            record = SceneRecord(
                id=str(uuid.uuid4()),
                story_chapter_id=chapter_id,
                project_id=chapter.project_id,
                scene_number=number,
                title=title,
                working_head_card_version_id=None,
                accepted_card_version_id=None,
                working_draft_id=None,
                accepted_draft_id=None,
                summary_text="",
                created_at=utc_now_iso(),
                updated_at=utc_now_iso(),
            )
            repo.add(record)
        return record

    def list_scenes(self, chapter_id: str) -> list[SceneRecord]:
        return self._read_only(
            lambda s: StorySceneRepository(s).list_for_chapter(chapter_id)
        )

    def get_scene(self, scene_id: str) -> SceneRecord:
        record = self._read_only(lambda s: StorySceneRepository(s).get(scene_id))
        if record is None:
            raise NotFoundError(f"找不到場景：{scene_id}")
        return record

    def add_card_version(
        self, scene_id: str, *, card: SceneCard, change_note: str = ""
    ) -> VersionRecord:
        """Create a new Scene Card version and PIN its participants (A3-03).

        Ownership of every referenced character version is validated here and
        again by composite foreign keys when the participant rows are written.
        """
        with self._transaction() as session:
            scenes = StorySceneRepository(session)
            scene = scenes.get(scene_id)
            if scene is None:
                raise NotFoundError(f"找不到場景：{scene_id}")

            self._validate_participant_ownership(session, scene.project_id, card)
            self._validate_adult_participants(card)

            repo = SceneCardVersionRepository(session)
            record = VersionRecord(
                id=str(uuid.uuid4()),
                parent_id=scene_id,
                project_id=scene.project_id,
                version_number=repo.next_version_number(scene_id),
                change_note=change_note,
                accepted=False,
                created_at=utc_now_iso(),
                payload={
                    "card_json": card.model_dump_json(),
                    "content_mode": card.content_mode.value,
                },
            )
            repo.add(record)
            session.flush()

            participants = SceneCardParticipantRepository(session)
            pov_id = card.pov_character_id
            for position, participant in enumerate(card.participants):
                participants.add(
                    ParticipantRecord(
                        id=str(uuid.uuid4()),
                        scene_card_version_id=record.id,
                        project_id=scene.project_id,
                        character_id=participant.character_id,
                        character_version_id=participant.character_version_id,
                        role=participant.role,
                        is_pov=participant.character_id == pov_id,
                        position=position,
                    )
                )
            scenes.update_fields(
                scene_id,
                working_head_card_version_id=record.id,
                updated_at=utc_now_iso(),
            )
        return record

    def list_card_versions(self, scene_id: str) -> list[VersionRecord]:
        return self._read_only(
            lambda s: SceneCardVersionRepository(s).list_for_scene(scene_id)
        )

    def load_card(self, version_id: str) -> SceneCard:
        record = self._read_only(lambda s: SceneCardVersionRepository(s).get(version_id))
        if record is None:
            raise NotFoundError(f"找不到 Scene Card 版本：{version_id}")
        return SceneCard.model_validate_json(str(record.payload["card_json"]))

    def accept_card_version(self, version_id: str) -> None:
        with self._transaction() as session:
            repo = SceneCardVersionRepository(session)
            version = repo.get(version_id)
            if version is None:
                raise NotFoundError(f"找不到 Scene Card 版本：{version_id}")
            repo.accept(version_id)
            StorySceneRepository(session).update_fields(
                version.parent_id,
                accepted_card_version_id=version_id,
                updated_at=utc_now_iso(),
            )

    def accepted_card_version(self, scene_id: str) -> VersionRecord | None:
        scene = self.get_scene(scene_id)
        accepted_id = scene.accepted_card_version_id
        if not accepted_id:
            return None
        return self._read_only(lambda s: SceneCardVersionRepository(s).get(accepted_id))

    def list_participants(self, card_version_id: str) -> list[ParticipantRecord]:
        """A3-03: the EXACT Character Versions pinned to this card version."""
        return self._read_only(
            lambda s: SceneCardParticipantRepository(s).list_for_card_version(
                card_version_id
            )
        )

    def update_summary(self, scene_id: str, summary_text: str) -> None:
        """§42 / A3-R05: the summary attaches to the scene's ACCEPTED draft.

        A summary is a claim about what happened in the scene, so it is only
        established history once a draft has been accepted. Writing it to a
        free-floating scene field let a working note on an unaccepted scene
        leak forward into the next scene's context as fact.

        The scene-level copy is kept as the author's editable working note.
        """
        with self._transaction() as session:
            scenes = StorySceneRepository(session)
            scene = scenes.get(scene_id)
            if scene is None:
                raise NotFoundError(f"找不到場景：{scene_id}")
            now = utc_now_iso()
            scenes.update_fields(scene_id, summary_text=summary_text, updated_at=now)
            if scene.accepted_draft_id:
                SceneDraftSummaryRepository(session).upsert(
                    DraftSummaryRecord(
                        id=str(uuid.uuid4()),
                        scene_draft_id=scene.accepted_draft_id,
                        story_scene_id=scene_id,
                        project_id=scene.project_id,
                        summary_text=summary_text,
                        created_at=now,
                        updated_at=now,
                    )
                )

    def accepted_summary(self, scene_id: str) -> str:
        """The summary that counts as established history, or ''."""
        scene = self.get_scene(scene_id)
        accepted_id = scene.accepted_draft_id
        if not accepted_id:
            return ""
        record = self._read_only(
            lambda s: SceneDraftSummaryRepository(s).get_for_draft(accepted_id)
        )
        return "" if record is None else record.summary_text

    # ------------------------------------------------------------ policy
    @staticmethod
    def _validate_participant_ownership(
        session: object, project_id: str, card: SceneCard
    ) -> None:
        """A3-14 §20: each character belongs to the project and each version
        belongs to its character."""
        repo = CharacterRepository(session)  # type: ignore[arg-type]
        for participant in card.participants:
            character = repo.get(participant.character_id)
            if character is None:
                raise NotFoundError(f"找不到角色：{participant.character_id}")
            if character.project_id != project_id:
                raise ValidationFailedError(
                    f"角色「{character.name}」屬於其他專案，不可加入本場景"
                )
            version = repo.get_version(participant.character_version_id)
            if version is None:
                raise NotFoundError(
                    f"找不到角色版本：{participant.character_version_id}"
                )
            if version.character_id != participant.character_id:
                raise ValidationFailedError(
                    f"角色版本 {participant.character_version_id} "
                    f"不屬於角色 {participant.character_id}"
                )

    def _validate_adult_participants(self, card: SceneCard) -> None:
        """§33: every participant in a sexual-mode scene must be a verified
        adult Canon character version. No selection ⇒ no adult content."""
        if not derives_adult(card.content_mode):
            return
        if self._eligibility is None:
            raise ValidationFailedError(
                "成人內容場景需要資格驗證服務；目前未提供。"
            )
        if not card.participants:
            raise ValidationFailedError(
                "成人內容場景必須指定參與角色（需為已建檔並通過資格驗證的成人角色）。"
            )
        # A3-03: evaluate the EXACT pinned versions. Passing None here was the
        # original defect — it re-resolved to whatever version was current, so
        # a later version change could silently alter an approved Scene Card.
        allowed, results = self._eligibility.evaluate_many(  # type: ignore[attr-defined]
            participants=[
                (p.character_id, p.character_version_id) for p in card.participants
            ],
            adult_content_requested=True,
        )
        if not allowed:
            blockers = "、".join(
                r.message for r in results if not r.allowed
            )
            raise ValidationFailedError(
                f"成人內容資格驗證未通過：{blockers or '未提供理由'}"
            )

    @staticmethod
    def content_mode_of(record: VersionRecord) -> ContentMode:
        return ContentMode(str(record.payload.get("content_mode", "general")))

    @staticmethod
    def card_payload_json(card: SceneCard) -> str:
        return json.dumps(card.model_dump(mode="json"), ensure_ascii=False)
