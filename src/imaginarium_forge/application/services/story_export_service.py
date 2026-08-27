"""Story export (Gate A, A3-11 §17 / A3-12 §18).

Two defects drove this rewrite:

- export selected ``accepted[-1]`` — the *last* draft carrying an accepted
  flag — while the scene's own pointer might name a different draft, so the
  exported prose could differ from what the author actually accepted;
- export re-read whatever versions happened to be current at export time, so
  the same export request produced different bytes on different days.

Now an export is built from an explicit ``StoryExportSnapshot``: every
version ID, draft ID, and typed Canon reference is resolved once, validated
for project ownership, frozen, hashed, and stored. Re-rendering from the same
snapshot always yields the same document.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, TypeVar

from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy.orm import Session

from imaginarium_forge.application.errors import (
    MissingStoryExportReference,
    NotFoundError,
    StoryExportIntegrityError,
    StoryExportReferencesUnavailableError,
    UnacceptedPlanningError,
    ValidationFailedError,
)
from imaginarium_forge.application.services.adult_output_review_service import (
    assert_adult_draft_reviewed,
)
from imaginarium_forge.application.services.base import ServiceBase
from imaginarium_forge.canonical import canonical_json
from imaginarium_forge.domain.common.ids import utc_now_iso
from imaginarium_forge.domain.story.generation_input import PlanningMode
from imaginarium_forge.domain.story.models import (
    ChapterPlan,
    SceneCard,
    StoryBible,
    StoryOutline,
    StoryRequirement,
)
from imaginarium_forge.infrastructure.db.repositories.story_repos import (
    GenerationRunRepository,
    SceneCardParticipantRepository,
    SceneCardVersionRepository,
    SceneDraftRepository,
    StoryChapterRepository,
    StoryExportRepository,
    StorySceneRepository,
    VersionedEntityRepository,
)

#: bumped when the snapshot field set or rendering contract changes
EXPORT_CONTRACT_VERSION = "phase3-story-export-v2"

_ModelT = TypeVar("_ModelT", bound=BaseModel)


#: bumped when the export CONTRACT changes
class StoryExportMode(StrEnum):
    PROSE = "prose"
    CHAPTERS_PROSE = "chapters_prose"
    PLANNING = "planning"
    BIBLE = "bible"
    OUTLINE = "outline"
    CARDS = "cards"
    FULL = "full"


_NEEDS_OUTLINE = frozenset(
    {
        StoryExportMode.PROSE,
        StoryExportMode.CHAPTERS_PROSE,
        StoryExportMode.PLANNING,
        StoryExportMode.OUTLINE,
        StoryExportMode.CARDS,
        StoryExportMode.FULL,
    }
)
_WANTS_PROSE = frozenset(
    {StoryExportMode.PROSE, StoryExportMode.CHAPTERS_PROSE, StoryExportMode.FULL}
)
_WANTS_CARDS = frozenset(
    {StoryExportMode.CARDS, StoryExportMode.PLANNING, StoryExportMode.FULL}
)
_WANTS_PLANNING = frozenset({StoryExportMode.PLANNING, StoryExportMode.FULL})
_WANTS_BIBLE = frozenset(
    {StoryExportMode.BIBLE, StoryExportMode.PLANNING, StoryExportMode.FULL}
)
_WANTS_OUTLINE = frozenset(
    {StoryExportMode.OUTLINE, StoryExportMode.PLANNING, StoryExportMode.FULL}
)


class CanonReference(BaseModel):
    """A3-12 §18.4: typed, never a bare ID in an untyped list."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    entity_type: str
    entity_id: str


class SceneSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    scene_id: str
    scene_number: int
    title: str = ""
    summary: str = ""
    scene_card_version_id: str | None = None
    accepted_draft_id: str | None = None
    generation_run_id: str | None = None
    content_mode: str = ""


class ChapterSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    chapter_id: str
    chapter_number: int
    title: str = ""
    plan_version_id: str | None = None
    scenes: tuple[SceneSnapshot, ...] = ()


class StoryExportSnapshot(BaseModel):
    """The frozen, hashable description of exactly what an export contains."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    export_contract_version: str = EXPORT_CONTRACT_VERSION
    export_mode: str
    planning_mode: str = "accepted"
    project_id: str
    story_outline_id: str | None = None
    requirement_version_id: str | None = None
    bible_version_id: str | None = None
    outline_version_id: str | None = None
    chapters: tuple[ChapterSnapshot, ...] = ()
    canon_references: tuple[CanonReference, ...] = ()

    def canonical_payload(self) -> str:
        return canonical_json(self.model_dump(mode="json"))

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.canonical_payload().encode("utf-8")).hexdigest()


class StoryExportReproductionStatus(StrEnum):
    REPRODUCED = "reproduced"


@dataclass(frozen=True, slots=True)
class StoryExportRebuildResult:
    markdown: str
    json_text: str
    snapshot_sha256: str
    reproduction_status: StoryExportReproductionStatus


def _fence(text: str) -> str:
    """A fence longer than any backtick run inside the content (A3-12 §18.5)."""
    longest = 0
    run = 0
    for ch in text:
        run = run + 1 if ch == "`" else 0
        longest = max(longest, run)
    return "`" * max(3, longest + 1)


class StoryExportService(ServiceBase):
    # ------------------------------------------------------------ public
    def build(
        self,
        *,
        project_id: str,
        mode: StoryExportMode,
        outline_id: str = "",
        bible_version_id: str = "",
        requirement_version_id: str = "",
        planning_mode: PlanningMode = PlanningMode.ACCEPTED,
        record_export: bool = True,
    ) -> tuple[str, str]:
        """Return ``(markdown, json)`` rendered from a frozen snapshot.

        A3-R07: ``accepted`` mode uses ONLY accepted versions and accepted
        drafts. Falling back to whatever was last saved produced a document
        that silently mixed accepted prose with draft planning, and nothing in
        the output said so. ``preview`` may include draft material, but the
        export row and the document both declare it.
        """
        snapshot, content = self._resolve(
            project_id=project_id,
            mode=mode,
            outline_id=outline_id,
            bible_version_id=bible_version_id,
            requirement_version_id=requirement_version_id,
            planning_mode=planning_mode,
        )
        markdown = self._render_markdown(mode, snapshot, content)
        as_json = self._render_json(snapshot, content)

        if record_export:
            with self._transaction() as session:
                StoryExportRepository(session).add(
                    export_id=str(uuid.uuid4()),
                    project_id=project_id,
                    export_mode=mode.value,
                    export_contract_version=EXPORT_CONTRACT_VERSION,
                    planning_mode=snapshot.planning_mode,
                    story_outline_id=snapshot.story_outline_id,
                    requirement_version_id=snapshot.requirement_version_id,
                    bible_version_id=snapshot.bible_version_id,
                    outline_version_id=snapshot.outline_version_id,
                    snapshot_json=snapshot.canonical_payload(),
                    snapshot_sha256=snapshot.sha256,
                    markdown_byte_size=len(markdown.encode("utf-8")),
                    json_byte_size=len(as_json.encode("utf-8")),
                    created_at=utc_now_iso(),
                )
        return markdown, as_json

    def list_exports(self, project_id: str) -> list[dict[str, object]]:
        rows = self._read_only(
            lambda s: StoryExportRepository(s).list_for_project(project_id)
        )
        return [
            {
                "id": r.id,
                "export_mode": r.export_mode,
                "planning_mode": r.planning_mode,
                "snapshot_sha256": r.snapshot_sha256,
                "markdown_byte_size": r.markdown_byte_size,
                "json_byte_size": r.json_byte_size,
                "total_byte_size": r.total_byte_size,
                "created_at": r.created_at,
            }
            for r in rows
        ]

    def rebuild_from_export(
        self,
        export_id: str,
        *,
        expected_project_id: str,
    ) -> StoryExportRebuildResult:
        """Reproduce one historical export solely from its persisted snapshot.

        S8.3 deliberately requires the active project as a separate authority
        boundary.  An export from another project is indistinguishable from an
        unknown ID, and no current accepted/working pointer is consulted.
        """
        session = self._session_factory()
        try:
            row = StoryExportRepository(session).get_for_project(
                export_id, expected_project_id
            )
            if row is None:
                raise NotFoundError(f"找不到故事匯出：{export_id}")

            snapshot = self._validated_persisted_snapshot(export_id, row)
            mode = self._validated_snapshot_contract(export_id, snapshot)
            content = self._resolve_persisted_snapshot(
                session=session,
                export_id=export_id,
                expected_project_id=expected_project_id,
                mode=mode,
                snapshot=snapshot,
            )
            markdown = self._render_markdown(mode, snapshot, content)
            json_text = self._render_json(snapshot, content)
            self._verify_rebuilt_sizes(
                export_id=export_id,
                row=row,
                markdown=markdown,
                json_text=json_text,
            )
            return StoryExportRebuildResult(
                markdown=markdown,
                json_text=json_text,
                snapshot_sha256=snapshot.sha256,
                reproduction_status=StoryExportReproductionStatus.REPRODUCED,
            )
        finally:
            session.close()

    @staticmethod
    def _validated_persisted_snapshot(
        export_id: str,
        row: Any,
    ) -> StoryExportSnapshot:
        try:
            raw_snapshot = json.loads(row.snapshot_json)
        except (json.JSONDecodeError, TypeError) as exc:
            raise StoryExportIntegrityError(
                export_id=export_id,
                detail=f"snapshot_json 不是合法 JSON（{exc}）",
            ) from exc
        if not isinstance(raw_snapshot, dict):
            raise StoryExportIntegrityError(
                export_id=export_id,
                detail="snapshot_json 的最外層必須是物件",
            )

        raw_canonical = canonical_json(raw_snapshot)
        calculated_sha256 = hashlib.sha256(raw_canonical.encode("utf-8")).hexdigest()
        if calculated_sha256 != row.snapshot_sha256:
            raise StoryExportIntegrityError(
                export_id=export_id,
                detail=(
                    "snapshot SHA-256 不符："
                    f"記錄為 {row.snapshot_sha256}，實際為 {calculated_sha256}"
                ),
            )

        try:
            snapshot = StoryExportSnapshot.model_validate(raw_snapshot)
        except ValidationError as exc:
            raise StoryExportIntegrityError(
                export_id=export_id,
                detail=f"snapshot 結構不符合匯出契約（{exc.errors()}）",
            ) from exc
        if snapshot.canonical_payload() != raw_canonical:
            raise StoryExportIntegrityError(
                export_id=export_id,
                detail="snapshot 結構需要預設值或型別正規化，並非原始完整契約",
            )

        redundant_fields = (
            ("project_id", row.project_id, snapshot.project_id),
            ("export_mode", row.export_mode, snapshot.export_mode),
            (
                "export_contract_version",
                row.export_contract_version,
                snapshot.export_contract_version,
            ),
            ("planning_mode", row.planning_mode, snapshot.planning_mode),
            ("story_outline_id", row.story_outline_id, snapshot.story_outline_id),
            (
                "requirement_version_id",
                row.requirement_version_id,
                snapshot.requirement_version_id,
            ),
            ("bible_version_id", row.bible_version_id, snapshot.bible_version_id),
            ("outline_version_id", row.outline_version_id, snapshot.outline_version_id),
        )
        for field_name, row_value, snapshot_value in redundant_fields:
            if row_value != snapshot_value:
                raise StoryExportIntegrityError(
                    export_id=export_id,
                    detail=(
                        f"欄位 {field_name} 與 snapshot 不一致："
                        f"記錄為 {row_value!r}，snapshot 為 {snapshot_value!r}"
                    ),
                )
        if row.total_byte_size != row.markdown_byte_size + row.json_byte_size:
            raise StoryExportIntegrityError(
                export_id=export_id,
                detail="記錄的 total_byte_size 不等於 Markdown 與 JSON byte size 總和",
            )
        return snapshot

    @staticmethod
    def _validated_snapshot_contract(
        export_id: str,
        snapshot: StoryExportSnapshot,
    ) -> StoryExportMode:
        if snapshot.export_contract_version != EXPORT_CONTRACT_VERSION:
            raise StoryExportIntegrityError(
                export_id=export_id,
                detail=f"不支援的匯出契約：{snapshot.export_contract_version}",
            )
        try:
            mode = StoryExportMode(snapshot.export_mode)
        except ValueError as exc:
            raise StoryExportIntegrityError(
                export_id=export_id,
                detail=f"未知的匯出模式：{snapshot.export_mode}",
            ) from exc
        try:
            PlanningMode(snapshot.planning_mode)
        except ValueError as exc:
            raise StoryExportIntegrityError(
                export_id=export_id,
                detail=f"未知的規劃模式：{snapshot.planning_mode}",
            ) from exc

        def present(reference: str | None, label: str) -> bool:
            if reference == "":
                raise StoryExportIntegrityError(
                    export_id=export_id,
                    detail=f"snapshot 的{label}不可是空字串",
                )
            return reference is not None

        if mode in _NEEDS_OUTLINE and not snapshot.story_outline_id:
            raise StoryExportIntegrityError(
                export_id=export_id,
                detail=f"{mode.value} snapshot 缺少 story_outline_id",
            )
        if mode not in _NEEDS_OUTLINE and (
            snapshot.story_outline_id
            or snapshot.outline_version_id
            or snapshot.chapters
        ):
            raise StoryExportIntegrityError(
                export_id=export_id,
                detail=f"{mode.value} snapshot 含不屬於此模式的大綱或章節資料",
            )
        for value, wanted, required, label in (
            (
                snapshot.requirement_version_id,
                mode in _WANTS_PLANNING,
                False,
                "需求版本",
            ),
            (
                snapshot.bible_version_id,
                mode in _WANTS_BIBLE,
                mode is StoryExportMode.BIBLE,
                "故事聖經版本",
            ),
            (
                snapshot.outline_version_id,
                mode in _WANTS_OUTLINE,
                mode is StoryExportMode.OUTLINE,
                "大綱版本",
            ),
        ):
            has_value = present(value, label)
            if has_value and not wanted:
                raise StoryExportIntegrityError(
                    export_id=export_id,
                    detail=f"{mode.value} snapshot 含不屬於此模式的{label}",
                )
            if required and not has_value:
                raise StoryExportIntegrityError(
                    export_id=export_id,
                    detail=f"{mode.value} snapshot 缺少必要的{label}",
                )

        chapter_ids = [chapter.chapter_id for chapter in snapshot.chapters]
        chapter_numbers = [chapter.chapter_number for chapter in snapshot.chapters]
        if len(chapter_ids) != len(set(chapter_ids)):
            raise StoryExportIntegrityError(
                export_id=export_id,
                detail="snapshot 含重複的 chapter_id",
            )
        if chapter_numbers != sorted(chapter_numbers) or any(n <= 0 for n in chapter_numbers):
            raise StoryExportIntegrityError(
                export_id=export_id,
                detail="snapshot 的章節順序或章節編號無效",
            )
        canon_pairs = [
            (reference.entity_type, reference.entity_id)
            for reference in snapshot.canon_references
        ]
        if canon_pairs != sorted(set(canon_pairs)) or any(
            not entity_type or not entity_id for entity_type, entity_id in canon_pairs
        ):
            raise StoryExportIntegrityError(
                export_id=export_id,
                detail="snapshot 的 Canon 參照不是非空、去重且穩定排序的型別化參照",
            )

        seen_scene_ids: set[str] = set()
        for chapter in snapshot.chapters:
            if not chapter.chapter_id:
                raise StoryExportIntegrityError(
                    export_id=export_id,
                    detail="snapshot 含空白 chapter_id",
                )
            if chapter.plan_version_id is not None and (
                not chapter.plan_version_id or mode not in _WANTS_PLANNING
            ):
                raise StoryExportIntegrityError(
                    export_id=export_id,
                    detail=f"章節 {chapter.chapter_id} 含不屬於此模式的計畫版本",
                )
            scene_numbers = [scene.scene_number for scene in chapter.scenes]
            if scene_numbers != sorted(scene_numbers) or any(n <= 0 for n in scene_numbers):
                raise StoryExportIntegrityError(
                    export_id=export_id,
                    detail=f"章節 {chapter.chapter_id} 的場景順序或場景編號無效",
                )
            for scene in chapter.scenes:
                if not scene.scene_id or scene.scene_id in seen_scene_ids:
                    raise StoryExportIntegrityError(
                        export_id=export_id,
                        detail=f"snapshot 含空白或重複的 scene_id：{scene.scene_id!r}",
                    )
                seen_scene_ids.add(scene.scene_id)
                if scene.scene_card_version_id is not None and (
                    not scene.scene_card_version_id or mode not in _WANTS_CARDS
                ):
                    raise StoryExportIntegrityError(
                        export_id=export_id,
                        detail=f"場景 {scene.scene_id} 含不屬於此模式的 Scene Card 版本",
                    )
                if scene.accepted_draft_id is not None and (
                    not scene.accepted_draft_id or mode not in _WANTS_PROSE
                ):
                    raise StoryExportIntegrityError(
                        export_id=export_id,
                        detail=f"場景 {scene.scene_id} 含不屬於此模式的草稿參照",
                    )
                if scene.generation_run_id and not scene.accepted_draft_id:
                    raise StoryExportIntegrityError(
                        export_id=export_id,
                        detail=f"場景 {scene.scene_id} 的生成紀錄沒有對應草稿",
                    )
                if scene.scene_card_version_id is None and scene.content_mode:
                    raise StoryExportIntegrityError(
                        export_id=export_id,
                        detail=f"場景 {scene.scene_id} 的 content_mode 沒有 Scene Card 來源",
                    )
        return mode

    def _resolve_persisted_snapshot(
        self,
        *,
        session: Session,
        export_id: str,
        expected_project_id: str,
        mode: StoryExportMode,
        snapshot: StoryExportSnapshot,
    ) -> dict[str, Any]:
        """Resolve exact rows named by the snapshot; never traverse a pointer."""
        missing: list[MissingStoryExportReference] = []

        def remember_missing(reference_type: str, reference_id: str) -> None:
            missing.append(
                MissingStoryExportReference(
                    reference_type=reference_type,
                    reference_id=reference_id,
                )
            )

        requirement_record = None
        if snapshot.requirement_version_id:
            requirement_record = VersionedEntityRepository(
                session, "requirement"
            ).get_version(snapshot.requirement_version_id)
            if requirement_record is None:
                remember_missing(
                    "story_requirement_version", snapshot.requirement_version_id
                )

        bible_record = None
        if snapshot.bible_version_id:
            bible_record = VersionedEntityRepository(session, "bible").get_version(
                snapshot.bible_version_id
            )
            if bible_record is None:
                remember_missing("story_bible_version", snapshot.bible_version_id)

        outline_repo = VersionedEntityRepository(session, "outline")
        outline_parent = None
        if snapshot.story_outline_id:
            outline_parent = outline_repo.get_entity(snapshot.story_outline_id)
            if outline_parent is None:
                remember_missing("story_outline", snapshot.story_outline_id)
        outline_record = None
        if snapshot.outline_version_id:
            outline_record = outline_repo.get_version(snapshot.outline_version_id)
            if outline_record is None:
                remember_missing("story_outline_version", snapshot.outline_version_id)

        chapter_repo = StoryChapterRepository(session)
        plan_repo = VersionedEntityRepository(session, "chapter_plan")
        scene_repo = StorySceneRepository(session)
        card_repo = SceneCardVersionRepository(session)
        draft_repo = SceneDraftRepository(session)
        run_repo = GenerationRunRepository(session)
        chapter_rows: dict[str, Any] = {}
        plan_rows: dict[str, Any] = {}
        scene_rows: dict[str, Any] = {}
        card_rows: dict[str, Any] = {}
        draft_rows: dict[str, Any] = {}
        run_rows: dict[str, Any] = {}

        for chapter in snapshot.chapters:
            chapter_row = chapter_repo.get(chapter.chapter_id)
            if chapter_row is None:
                remember_missing("story_chapter", chapter.chapter_id)
            else:
                chapter_rows[chapter.chapter_id] = chapter_row
            if chapter.plan_version_id:
                plan_row = plan_repo.get_version(chapter.plan_version_id)
                if plan_row is None:
                    remember_missing("chapter_plan_version", chapter.plan_version_id)
                else:
                    plan_rows[chapter.plan_version_id] = plan_row

            for scene in chapter.scenes:
                scene_row = scene_repo.get(scene.scene_id)
                if scene_row is None:
                    remember_missing("story_scene", scene.scene_id)
                else:
                    scene_rows[scene.scene_id] = scene_row
                if scene.scene_card_version_id:
                    card_row = card_repo.get(scene.scene_card_version_id)
                    if card_row is None:
                        remember_missing(
                            "scene_card_version", scene.scene_card_version_id
                        )
                    else:
                        card_rows[scene.scene_card_version_id] = card_row
                if scene.accepted_draft_id:
                    draft_row = draft_repo.get(scene.accepted_draft_id)
                    if draft_row is None:
                        remember_missing("scene_draft", scene.accepted_draft_id)
                    else:
                        assert_adult_draft_reviewed(
                            session, draft_row, action_label="重建故事匯出"
                        )
                        draft_rows[scene.accepted_draft_id] = draft_row
                if scene.generation_run_id:
                    run_row = run_repo.get(scene.generation_run_id)
                    if run_row is None:
                        remember_missing("generation_run", scene.generation_run_id)
                    else:
                        run_rows[scene.generation_run_id] = run_row

        if missing:
            raise StoryExportReferencesUnavailableError(
                export_id=export_id,
                missing_references=tuple(missing),
            )

        self._validate_snapshot_ownership(
            export_id=export_id,
            expected_project_id=expected_project_id,
            snapshot=snapshot,
            outline_parent=outline_parent,
            requirement_record=requirement_record,
            bible_record=bible_record,
            outline_record=outline_record,
            chapter_rows=chapter_rows,
            plan_rows=plan_rows,
            scene_rows=scene_rows,
            card_rows=card_rows,
            draft_rows=draft_rows,
            run_rows=run_rows,
        )

        content: dict[str, Any] = {}
        if requirement_record is not None:
            content["requirement"] = self._payload_model(
                export_id=export_id,
                reference_label=f"需求版本 {requirement_record.id}",
                model_type=StoryRequirement,
                payload=requirement_record.payload,
                payload_key="requirement_json",
            ).model_dump(mode="json")
        if bible_record is not None:
            content["bible"] = self._payload_model(
                export_id=export_id,
                reference_label=f"故事聖經版本 {bible_record.id}",
                model_type=StoryBible,
                payload=bible_record.payload,
                payload_key="bible_json",
            ).model_dump(mode="json")
        if outline_record is not None:
            content["outline"] = self._payload_model(
                export_id=export_id,
                reference_label=f"大綱版本 {outline_record.id}",
                model_type=StoryOutline,
                payload=outline_record.payload,
                payload_key="outline_json",
            ).model_dump(mode="json")

        if mode in _NEEDS_OUTLINE:
            chapters_content: list[dict[str, Any]] = []
            for chapter in snapshot.chapters:
                chapter_content: dict[str, Any] = {
                    "chapter_number": chapter.chapter_number,
                    "title": chapter.title,
                    "scenes": [],
                }
                if chapter.plan_version_id:
                    plan_record = plan_rows[chapter.plan_version_id]
                    chapter_content["plan"] = self._payload_model(
                        export_id=export_id,
                        reference_label=f"章節計畫版本 {plan_record.id}",
                        model_type=ChapterPlan,
                        payload=plan_record.payload,
                        payload_key="plan_json",
                    ).model_dump(mode="json")

                for scene in chapter.scenes:
                    scene_content: dict[str, Any] = {
                        "scene_number": scene.scene_number,
                        "title": scene.title,
                        "summary": scene.summary,
                    }
                    if scene.scene_card_version_id:
                        card_record = card_rows[scene.scene_card_version_id]
                        card = self._payload_model(
                            export_id=export_id,
                            reference_label=f"Scene Card 版本 {card_record.id}",
                            model_type=SceneCard,
                            payload=card_record.payload,
                            payload_key="card_json",
                        )
                        if (
                            card.content_mode.value != scene.content_mode
                            or str(card_record.payload.get("content_mode"))
                            != scene.content_mode
                        ):
                            raise StoryExportIntegrityError(
                                export_id=export_id,
                                detail=(
                                    f"Scene Card 版本 {card_record.id} 的 content_mode "
                                    "與 snapshot 不一致"
                                ),
                            )
                        scene_content["card"] = card.model_dump(mode="json")
                    if scene.accepted_draft_id:
                        draft = draft_rows[scene.accepted_draft_id]
                        scene_content.update(
                            prose=draft.prose_text,
                            draft_id=draft.id,
                            word_count=draft.word_count,
                            origin=draft.origin,
                        )
                        if scene.generation_run_id:
                            run = run_rows[scene.generation_run_id]
                            scene_content["generation"] = {
                                "provider": run.provider,
                                "model": run.model,
                                "context_fingerprint": run.context_fingerprint,
                                "input_snapshot_sha256": run.input_snapshot_sha256,
                                "content_mode": run.content_mode,
                                "scene_card_version_id": run.scene_card_version_id,
                            }
                    chapter_content["scenes"].append(scene_content)
                chapters_content.append(chapter_content)
            content["chapters"] = chapters_content
        return content

    @staticmethod
    def _validate_snapshot_ownership(
        *,
        export_id: str,
        expected_project_id: str,
        snapshot: StoryExportSnapshot,
        outline_parent: Any,
        requirement_record: Any,
        bible_record: Any,
        outline_record: Any,
        chapter_rows: dict[str, Any],
        plan_rows: dict[str, Any],
        scene_rows: dict[str, Any],
        card_rows: dict[str, Any],
        draft_rows: dict[str, Any],
        run_rows: dict[str, Any],
    ) -> None:
        def require_project(record: Any, reference_label: str) -> None:
            if record.project_id != expected_project_id:
                raise StoryExportIntegrityError(
                    export_id=export_id,
                    detail=(
                        f"{reference_label} 的 project_id 為 {record.project_id}，"
                        f"預期為 {expected_project_id}"
                    ),
                )

        def require_parent(
            *,
            reference_label: str,
            actual_parent_id: str,
            expected_parent_id: str,
        ) -> None:
            if actual_parent_id != expected_parent_id:
                raise StoryExportIntegrityError(
                    export_id=export_id,
                    detail=(
                        f"{reference_label} 的父項目為 {actual_parent_id}，"
                        f"snapshot 預期為 {expected_parent_id}"
                    ),
                )

        for record, label in (
            (requirement_record, "故事需求版本"),
            (bible_record, "故事聖經版本"),
            (outline_parent, "故事大綱"),
            (outline_record, "故事大綱版本"),
        ):
            if record is not None:
                require_project(record, label)
        if outline_record is not None and snapshot.story_outline_id is not None:
            require_parent(
                reference_label=f"故事大綱版本 {outline_record.id}",
                actual_parent_id=outline_record.parent_id,
                expected_parent_id=snapshot.story_outline_id,
            )

        for chapter in snapshot.chapters:
            chapter_row = chapter_rows[chapter.chapter_id]
            require_project(chapter_row, f"章節 {chapter.chapter_id}")
            require_parent(
                reference_label=f"章節 {chapter.chapter_id}",
                actual_parent_id=chapter_row.story_outline_id,
                expected_parent_id=snapshot.story_outline_id or "",
            )
            if chapter.plan_version_id:
                plan_row = plan_rows[chapter.plan_version_id]
                require_project(plan_row, f"章節計畫版本 {plan_row.id}")
                require_parent(
                    reference_label=f"章節計畫版本 {plan_row.id}",
                    actual_parent_id=plan_row.parent_id,
                    expected_parent_id=chapter.chapter_id,
                )

            for scene in chapter.scenes:
                scene_row = scene_rows[scene.scene_id]
                require_project(scene_row, f"場景 {scene.scene_id}")
                require_parent(
                    reference_label=f"場景 {scene.scene_id}",
                    actual_parent_id=scene_row.story_chapter_id,
                    expected_parent_id=chapter.chapter_id,
                )
                if scene.scene_card_version_id:
                    card_row = card_rows[scene.scene_card_version_id]
                    require_project(card_row, f"Scene Card 版本 {card_row.id}")
                    require_parent(
                        reference_label=f"Scene Card 版本 {card_row.id}",
                        actual_parent_id=card_row.parent_id,
                        expected_parent_id=scene.scene_id,
                    )
                if scene.accepted_draft_id:
                    draft_row = draft_rows[scene.accepted_draft_id]
                    require_project(draft_row, f"場景草稿 {draft_row.id}")
                    require_parent(
                        reference_label=f"場景草稿 {draft_row.id}",
                        actual_parent_id=draft_row.story_scene_id,
                        expected_parent_id=scene.scene_id,
                    )
                    if draft_row.generation_run_id != scene.generation_run_id:
                        raise StoryExportIntegrityError(
                            export_id=export_id,
                            detail=(
                                f"場景草稿 {draft_row.id} 的 generation_run_id "
                                "與 snapshot 不一致"
                            ),
                        )
                if scene.generation_run_id:
                    run_row = run_rows[scene.generation_run_id]
                    require_project(run_row, f"生成紀錄 {run_row.id}")
                    require_parent(
                        reference_label=f"生成紀錄 {run_row.id}",
                        actual_parent_id=run_row.story_scene_id,
                        expected_parent_id=scene.scene_id,
                    )

    @staticmethod
    def _payload_model(
        *,
        export_id: str,
        reference_label: str,
        model_type: type[_ModelT],
        payload: dict[str, Any],
        payload_key: str,
    ) -> _ModelT:
        try:
            return model_type.model_validate_json(str(payload[payload_key]))
        except (KeyError, TypeError, ValueError, ValidationError) as exc:
            raise StoryExportIntegrityError(
                export_id=export_id,
                detail=f"{reference_label} 的 {payload_key} 無法驗證（{exc}）",
            ) from exc

    @staticmethod
    def _verify_rebuilt_sizes(
        *,
        export_id: str,
        row: Any,
        markdown: str,
        json_text: str,
    ) -> None:
        markdown_size = len(markdown.encode("utf-8"))
        json_size = len(json_text.encode("utf-8"))
        if markdown_size != row.markdown_byte_size:
            raise StoryExportIntegrityError(
                export_id=export_id,
                detail=(
                    "重建 Markdown byte size 不符："
                    f"記錄為 {row.markdown_byte_size}，重建為 {markdown_size}"
                ),
            )
        if json_size != row.json_byte_size:
            raise StoryExportIntegrityError(
                export_id=export_id,
                detail=(
                    "重建 JSON byte size 不符："
                    f"記錄為 {row.json_byte_size}，重建為 {json_size}"
                ),
            )
        if markdown_size + json_size != row.total_byte_size:
            raise StoryExportIntegrityError(
                export_id=export_id,
                detail=(
                    "重建 total byte size 不符："
                    f"記錄為 {row.total_byte_size}，重建為 {markdown_size + json_size}"
                ),
            )

    # --------------------------------------------------------- resolution
    def _resolve(
        self,
        *,
        project_id: str,
        mode: StoryExportMode,
        outline_id: str,
        bible_version_id: str,
        requirement_version_id: str,
        planning_mode: PlanningMode = PlanningMode.ACCEPTED,
    ) -> tuple[StoryExportSnapshot, dict[str, Any]]:
        if mode in _NEEDS_OUTLINE and not outline_id:
            raise ValidationFailedError(f"匯出模式「{mode.value}」需要指定大綱")

        accepted_only = planning_mode is PlanningMode.ACCEPTED
        content: dict[str, Any] = {}
        canon: list[CanonReference] = []
        chapters_snapshot: list[ChapterSnapshot] = []
        resolved_requirement_version: str | None = None
        resolved_bible_version: str | None = None
        resolved_outline_version: str | None = None

        with self._session_factory() as session:
            # ---- requirement -------------------------------------------
            if requirement_version_id and mode in _WANTS_PLANNING:
                record = VersionedEntityRepository(session, "requirement").get_version(
                    requirement_version_id
                )
                if record is None:
                    raise NotFoundError(
                        f"找不到故事需求版本：{requirement_version_id}"
                    )
                self._require_project(record.project_id, project_id, "故事需求版本")
                self._require_accepted(record, accepted_only, "故事需求")
                content["requirement"] = StoryRequirement.model_validate_json(
                    str(record.payload["requirement_json"])
                ).model_dump(mode="json")
                resolved_requirement_version = record.id

            # ---- bible --------------------------------------------------
            if bible_version_id and mode in _WANTS_BIBLE:
                record = VersionedEntityRepository(session, "bible").get_version(
                    bible_version_id
                )
                if record is None:
                    raise NotFoundError(f"找不到故事聖經版本：{bible_version_id}")
                self._require_project(record.project_id, project_id, "故事聖經版本")
                self._require_accepted(record, accepted_only, "故事聖經")
                bible = StoryBible.model_validate_json(
                    str(record.payload["bible_json"])
                )
                content["bible"] = bible.model_dump(mode="json")
                resolved_bible_version = record.id
                for entry in bible.characters:
                    if entry.canon_character_id:
                        canon.append(
                            CanonReference(
                                entity_type="character",
                                entity_id=entry.canon_character_id,
                            )
                        )
                    if entry.canon_character_version_id:
                        canon.append(
                            CanonReference(
                                entity_type="character_version",
                                entity_id=entry.canon_character_version_id,
                            )
                        )

            # ---- outline + chapters + scenes ----------------------------
            if mode in _NEEDS_OUTLINE:
                outlines = VersionedEntityRepository(session, "outline")
                parent = outlines.get_entity(outline_id)
                if parent is None:
                    raise NotFoundError(f"找不到大綱：{outline_id}")
                # A3-12 §18.3 / A3-04: the reproduced defect was exporting
                # another project's outline under this project's request.
                self._require_project(parent.project_id, project_id, "大綱")

                version_id = parent.accepted_version_id or (
                    None if accepted_only else parent.working_head_version_id
                )
                if version_id and mode in _WANTS_OUTLINE:
                    record = outlines.get_version(version_id)
                    if record is not None:
                        content["outline"] = StoryOutline.model_validate_json(
                            str(record.payload["outline_json"])
                        ).model_dump(mode="json")
                        resolved_outline_version = record.id

                plans = VersionedEntityRepository(session, "chapter_plan")
                chapter_repo = StoryChapterRepository(session)
                scene_repo = StorySceneRepository(session)
                card_repo = SceneCardVersionRepository(session)
                draft_repo = SceneDraftRepository(session)
                run_repo = GenerationRunRepository(session)
                participant_repo = SceneCardParticipantRepository(session)
                chapters_content: list[dict[str, Any]] = []

                for chapter in chapter_repo.list_for_outline(outline_id):
                    plan_version_id: str | None = None
                    chapter_content: dict[str, Any] = {
                        "chapter_number": chapter.chapter_number,
                        "title": chapter.title,
                        "scenes": [],
                    }
                    if mode in _WANTS_PLANNING:
                        plan_version_id = chapter.accepted_plan_version_id or (
                            None
                            if accepted_only
                            else chapter.working_head_plan_version_id
                        )
                        if plan_version_id:
                            plan_record = plans.get_version(plan_version_id)
                            if plan_record is not None:
                                chapter_content["plan"] = (
                                    ChapterPlan.model_validate_json(
                                        str(plan_record.payload["plan_json"])
                                    ).model_dump(mode="json")
                                )

                    scene_snapshots: list[SceneSnapshot] = []
                    for scene in scene_repo.list_for_chapter(chapter.id):
                        scene_content: dict[str, Any] = {
                            "scene_number": scene.scene_number,
                            "title": scene.title,
                            "summary": scene.summary_text,
                        }
                        card_version_id: str | None = None
                        content_mode = ""
                        if mode in _WANTS_CARDS:
                            card_version_id = scene.accepted_card_version_id or (
                                None
                                if accepted_only
                                else scene.working_head_card_version_id
                            )
                            if card_version_id:
                                card_record = card_repo.get(card_version_id)
                                if card_record is not None:
                                    card = SceneCard.model_validate_json(
                                        str(card_record.payload["card_json"])
                                    )
                                    scene_content["card"] = card.model_dump(
                                        mode="json"
                                    )
                                    content_mode = card.content_mode.value
                                    for pin in participant_repo.list_for_card_version(
                                        card_version_id
                                    ):
                                        canon.append(
                                            CanonReference(
                                                entity_type="character_version",
                                                entity_id=pin.character_version_id,
                                            )
                                        )

                        accepted_draft_id = scene.accepted_draft_id
                        run_id: str | None = None
                        if mode in _WANTS_PROSE and accepted_draft_id:
                            # A3-11: follow the SCENE's pointer, never a scan
                            chosen = draft_repo.get(accepted_draft_id)
                            if chosen is not None:
                                assert_adult_draft_reviewed(
                                    session, chosen, action_label="匯出故事"
                                )
                                scene_content["prose"] = chosen.prose_text
                                scene_content["draft_id"] = chosen.id
                                scene_content["word_count"] = chosen.word_count
                                scene_content["origin"] = chosen.origin
                                run_id = chosen.generation_run_id
                                if run_id:
                                    run = run_repo.get(run_id)
                                    if run is not None:
                                        scene_content["generation"] = {
                                            "provider": run.provider,
                                            "model": run.model,
                                            "context_fingerprint": (
                                                run.context_fingerprint
                                            ),
                                            "input_snapshot_sha256": (
                                                run.input_snapshot_sha256
                                            ),
                                            "content_mode": run.content_mode,
                                            "scene_card_version_id": (
                                                run.scene_card_version_id
                                            ),
                                        }
                        chapter_content["scenes"].append(scene_content)
                        scene_snapshots.append(
                            SceneSnapshot(
                                scene_id=scene.id,
                                scene_number=scene.scene_number,
                                title=scene.title,
                                summary=scene.summary_text,
                                scene_card_version_id=card_version_id,
                                accepted_draft_id=(
                                    accepted_draft_id
                                    if mode in _WANTS_PROSE
                                    else None
                                ),
                                generation_run_id=run_id,
                                content_mode=content_mode,
                            )
                        )
                    chapters_content.append(chapter_content)
                    chapters_snapshot.append(
                        ChapterSnapshot(
                            chapter_id=chapter.id,
                            chapter_number=chapter.chapter_number,
                            title=chapter.title,
                            plan_version_id=plan_version_id,
                            scenes=tuple(scene_snapshots),
                        )
                    )
                content["chapters"] = chapters_content

        unique_canon = tuple(
            sorted(
                {(c.entity_type, c.entity_id) for c in canon},
            )
        )
        snapshot = StoryExportSnapshot(
            export_mode=mode.value,
            planning_mode=planning_mode.value,
            project_id=project_id,
            story_outline_id=outline_id or None,
            requirement_version_id=resolved_requirement_version,
            bible_version_id=resolved_bible_version,
            outline_version_id=resolved_outline_version,
            chapters=tuple(chapters_snapshot),
            canon_references=tuple(
                CanonReference(entity_type=t, entity_id=i) for t, i in unique_canon
            ),
        )
        return snapshot, content

    @staticmethod
    def _require_accepted(record: Any, accepted_only: bool, label: str) -> None:
        """A3-R07: an accepted-mode export never includes draft planning."""
        if accepted_only and not record.accepted:
            raise UnacceptedPlanningError(entity_label=label, version_id=record.id)

    @staticmethod
    def _require_project(actual: str, expected: str, label: str) -> None:
        if actual != expected:
            raise ValidationFailedError(
                f"不可匯出其他專案的{label}："
                f"該項目屬於專案 {actual}，但匯出請求屬於專案 {expected}"
            )

    # ---------------------------------------------------------- rendering
    @staticmethod
    def _render_json(
        snapshot: StoryExportSnapshot,
        content: dict[str, Any],
    ) -> str:
        document = {
            "snapshot": snapshot.model_dump(mode="json"),
            "snapshot_sha256": snapshot.sha256,
            "content": content,
        }
        return json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2)

    @staticmethod
    def _render_markdown(
        mode: StoryExportMode,
        snapshot: StoryExportSnapshot,
        content: dict[str, Any],
    ) -> str:
        lines: list[str] = [f"# 故事匯出（{mode.value}）", ""]
        lines.append(f"- 匯出契約：`{snapshot.export_contract_version}`")
        if snapshot.planning_mode != "accepted":
            lines.append(
                f"- **規劃模式：{snapshot.planning_mode}** — "
                "本文件含尚未接受的規劃版本，不代表已定稿內容。"
            )
        lines.append(f"- 快照 SHA-256：`{snapshot.sha256}`")
        for label, value in (
            ("需求版本", snapshot.requirement_version_id),
            ("聖經版本", snapshot.bible_version_id),
            ("大綱版本", snapshot.outline_version_id),
        ):
            if value:
                lines.append(f"- {label}：`{value}`")
        if snapshot.canon_references:
            lines.append(f"- Canon 參照：{len(snapshot.canon_references)} 筆（具型別）")
        lines.append("")

        requirement = content.get("requirement")
        if isinstance(requirement, dict):
            lines.extend(
                [
                    "## 故事需求",
                    "",
                    f"- 概念：{requirement.get('concept', '')}",
                    f"- 類型：{requirement.get('genre', '')}",
                    f"- 基調：{requirement.get('tone', '')}",
                    "",
                ]
            )

        bible = content.get("bible")
        if isinstance(bible, dict):
            lines.extend(
                [
                    "## 故事聖經",
                    "",
                    f"- 標題：{bible.get('title', '')}",
                    f"- 故事線：{bible.get('logline', '')}",
                    "",
                ]
            )
            characters = bible.get("characters", [])
            if isinstance(characters, list) and characters:
                lines.extend(["### 角色", ""])
                for character in characters:
                    if not isinstance(character, dict):
                        continue
                    version = character.get("canon_character_version_id")
                    suffix = f"　→ Canon 版本 `{version}`" if version else ""
                    lines.append(
                        f"- **{character.get('name', '')}**"
                        f"（{character.get('role', '')}）{suffix}"
                    )
                lines.append("")

        outline = content.get("outline")
        if isinstance(outline, dict):
            lines.extend(
                ["## 大綱", "", f"- 結構：{outline.get('structure_profile', '')}", ""]
            )
            for act in outline.get("acts", []) or []:
                if isinstance(act, dict):
                    lines.extend([f"### {act.get('name', '')}", ""])
                    if act.get("purpose"):
                        lines.append(f"- 目的：{act['purpose']}")
                    if act.get("turning_point"):
                        lines.append(f"- 轉折：{act['turning_point']}")
                    lines.append("")

        for chapter in content.get("chapters", []) or []:
            if not isinstance(chapter, dict):
                continue
            if mode is not StoryExportMode.PROSE:
                lines.extend(
                    [
                        f"## 第 {chapter.get('chapter_number', '?')} 章"
                        f"　{chapter.get('title', '')}",
                        "",
                    ]
                )
            plan = chapter.get("plan")
            if isinstance(plan, dict):
                lines.append(f"- 章節目的：{plan.get('purpose', '')}")
                lines.append(f"- 章節目標：{plan.get('goal', '')}")
                lines.append(f"- 章節衝突：{plan.get('conflict', '')}")
                lines.append("")
            for scene in chapter.get("scenes", []) or []:
                if not isinstance(scene, dict):
                    continue
                card = scene.get("card")
                if isinstance(card, dict):
                    lines.extend(
                        [
                            f"### 場景 {scene.get('scene_number', '?')}"
                            f"　{scene.get('title', '')}",
                            "",
                            f"- 地點：{card.get('location', '')}",
                        ]
                    )
                    beats = card.get("beats", [])
                    if isinstance(beats, list) and beats:
                        lines.append("- 節拍：")
                        lines.extend(f"  {i + 1}. {b}" for i, b in enumerate(beats))
                    lines.append("")
                prose = scene.get("prose")
                if isinstance(prose, str) and prose:
                    # A3-12 §18.5: author prose may itself contain backticks
                    fence = _fence(prose)
                    lines.extend([fence, prose, fence, ""])
        return "\n".join(lines).rstrip() + "\n"
