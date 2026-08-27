"""Story Studio repositories (Phase 3 C4).

A single generic ``VersionedEntityRepository`` covers the four
parent/version pairs (requirement, bible, outline, chapter plan) because
they share an identical shape: a project-owned parent with a
``current_version_id`` pointer plus an append-only version table.

Scene cards, drafts, generation runs, and exports get purpose-built
repositories because their columns and lifecycle differ.

Repositories do not commit; the calling service owns the transaction.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from typing import Any, cast

from sqlalchemy import func, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from imaginarium_forge.domain.story.blocks import (
    BLOCK_SCHEMA_VERSION,
    MAPPING_VERSION,
    SEGMENTATION_VERSION,
    ParentStoryBlock,
    project_story_blocks,
)
from imaginarium_forge.infrastructure.db.models.orm import (
    Base,
    ChapterPlanVersionRow,
    GenerationRunRow,
    SceneCardParticipantRow,
    SceneCardVersionRow,
    SceneDraftBlockSetRow,
    SceneDraftRow,
    SceneDraftSummaryRow,
    StoryBibleRow,
    StoryBibleVersionRow,
    StoryBlockAnchorRow,
    StoryBlockRevisionRow,
    StoryChapterRow,
    StoryExportRow,
    StoryOutlineRow,
    StoryOutlineVersionRow,
    StoryRequirementRow,
    StoryRequirementVersionRow,
    StorySceneRow,
)


# --------------------------------------------------------------- records
@dataclass(slots=True)
class EntityRecord:
    """A3-07: two distinct pointers, never one overloaded field."""

    id: str
    project_id: str
    title: str
    working_head_version_id: str | None
    accepted_version_id: str | None
    created_at: str
    updated_at: str


@dataclass(slots=True)
class VersionRecord:
    id: str
    parent_id: str
    project_id: str
    version_number: int
    change_note: str
    accepted: bool
    created_at: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ChapterRecord:
    id: str
    story_outline_id: str
    project_id: str
    chapter_number: int
    title: str
    working_head_plan_version_id: str | None
    accepted_plan_version_id: str | None
    created_at: str
    updated_at: str


@dataclass(slots=True)
class SceneRecord:
    id: str
    story_chapter_id: str
    project_id: str
    scene_number: int
    title: str
    working_head_card_version_id: str | None
    accepted_card_version_id: str | None
    working_draft_id: str | None
    #: A3-11 — THE canonical accepted draft; export follows this
    accepted_draft_id: str | None
    summary_text: str
    created_at: str
    updated_at: str


@dataclass(slots=True)
class DraftRecord:
    id: str
    story_scene_id: str
    project_id: str
    draft_number: int
    prose_text: str
    word_count: int
    origin: str
    #: A3-10 — 'complete' or 'partial'; partial can never be accepted
    draft_status: str
    #: A3-11 — historical audit only, NOT the active pointer
    was_accepted: bool
    accepted_at: str
    #: A3-R12 — from unaccepted planning; promote, never accept
    is_preview: bool
    promoted_from_preview_draft_id: str | None
    #: A3-R03 — a recovery rebase draft, not an ordinary revision
    is_recovery_rebase: bool
    scene_card_version_id: str
    generation_run_id: str | None
    revision_request_json: str
    revision_request_fingerprint: str | None
    parent_draft_id: str | None
    created_at: str


@dataclass(frozen=True, slots=True)
class StoryBlockRevisionRecord:
    block_revision_id: str
    logical_block_id: str
    scene_draft_id: str
    project_id: str
    story_scene_id: str
    parent_block_revision_id: str | None
    ordinal: int
    block_type: str
    text: str
    separator_after: str
    text_sha256: str
    source_slice_sha256: str
    created_at: str


@dataclass(frozen=True, slots=True)
class StoryBlockProjectionRecord:
    scene_draft_id: str
    project_id: str
    story_scene_id: str
    parent_draft_id: str | None
    parent_draft_available: bool
    schema_version: str
    segmentation_version: str
    mapping_version: str
    leading_text: str
    prose_sha256: str
    manifest_sha256: str
    lineage_quality: str
    block_count: int
    created_at: str
    blocks: tuple[StoryBlockRevisionRecord, ...]

    def reconstruct(self) -> str:
        return self.leading_text + "".join(
            block.text + block.separator_after for block in self.blocks
        )


class StoryBlockIntegrityError(ValueError):
    """The derived block projection is absent or does not match its source."""


@dataclass(slots=True)
class GenerationRunRecord:
    id: str
    story_scene_id: str
    project_id: str
    scene_card_version_id: str
    started_at: str
    run_kind: str = "generation"
    provider: str = ""
    model: str = ""
    options_snapshot_json: str = "{}"
    options_snapshot_sha256: str = ""
    context_fingerprint: str = ""
    planning_chain_fingerprint: str = ""
    planning_mode: str = "accepted"
    preview_warning_acknowledged: int = 0
    renderer_version: str = ""
    context_schema_version: str = ""
    context_contract_version: str = ""
    context_budget_policy_version: str = ""
    eligibility_fingerprint: str = ""
    eligibility_evaluation_ids_json: str = "[]"
    #: A3-01 — canonical JSON + SHA-256 of every resolved input
    input_snapshot_json: str = "{}"
    input_snapshot_sha256: str = ""
    #: A3-17 — these hold real VERSION ids (NULL when absent, never "")
    requirement_version_id: str | None = None
    bible_version_id: str | None = None
    outline_version_id: str | None = None
    chapter_plan_version_id: str | None = None
    pov_character_version_id: str | None = None
    character_version_ids_json: str = "[]"
    parent_draft_id: str | None = None
    revision_request_fingerprint: str = ""
    rebased_from_generation_run_id: str | None = None
    rebase_reason: str = ""
    is_recovery_rebase: int = 0
    missing_source_version_ids_json: str = "[]"
    # A3-R10: hashes always; raw text only when the setting allows
    system_message_sha256: str = ""
    user_message_sha256: str = ""
    system_message_byte_size: int = 0
    user_message_byte_size: int = 0
    rendered_system_message: str | None = None
    rendered_user_message: str | None = None
    rendered_message_storage_enabled: int = 1
    content_mode: str = "general"
    status: str = "running"
    reason_code: str = ""
    error_reason: str = ""
    completed_at: str = ""
    latency_ms: int = 0


@dataclass(slots=True)
class ParticipantRecord:
    """A3-03: an exact Character Version pinned to a Scene Card version."""

    id: str
    scene_card_version_id: str
    project_id: str
    character_id: str
    character_version_id: str
    role: str = ""
    is_pov: bool = False
    position: int = 0


# ------------------------------------------------------- generic entity
#: parent row, version row, version FK column, payload column names
_ENTITY_SPECS: dict[str, tuple[type[Base], type[Base], str, tuple[str, ...]]] = {
    "requirement": (
        StoryRequirementRow,
        StoryRequirementVersionRow,
        "story_requirement_id",
        ("requirement_json", "source_text", "structured_mode"),
    ),
    "bible": (
        StoryBibleRow,
        StoryBibleVersionRow,
        "story_bible_id",
        ("bible_json", "requirement_version_id"),
    ),
    "outline": (
        StoryOutlineRow,
        StoryOutlineVersionRow,
        "story_outline_id",
        ("outline_json", "structure_profile", "bible_version_id"),
    ),
    "chapter_plan": (
        StoryChapterRow,
        ChapterPlanVersionRow,
        "story_chapter_id",
        ("plan_json",),
    ),
}


class VersionedEntityRepository:
    """Shared access for requirement / bible / outline / chapter-plan pairs."""

    def __init__(self, session: Session, kind: str) -> None:
        if kind not in _ENTITY_SPECS:
            raise ValueError(f"unknown story entity kind: {kind}")
        self._session = session
        self._kind = kind
        (
            self._parent_row,
            self._version_row,
            self._parent_fk,
            self._payload_cols,
        ) = _ENTITY_SPECS[kind]

    # ---- parents ----------------------------------------------------
    def add_entity(self, record: EntityRecord) -> None:
        self._session.add(
            self._parent_row(
                id=record.id,
                project_id=record.project_id,
                title=record.title,
                working_head_version_id=record.working_head_version_id,
                accepted_version_id=record.accepted_version_id,
                created_at=record.created_at,
                updated_at=record.updated_at,
            )
        )

    def get_entity(self, entity_id: str) -> EntityRecord | None:
        row = self._session.get(self._parent_row, entity_id)
        return None if row is None else self._entity(row)

    def list_entities(self, project_id: str) -> list[EntityRecord]:
        stmt = select(self._parent_row).where(
            self._parent_row.project_id == project_id  # type: ignore[attr-defined]
        )
        return [self._entity(r) for r in self._session.scalars(stmt)]

    def set_working_head(self, entity_id: str, version_id: str, now: str) -> bool:
        """A3-07: the version the author is currently editing."""
        row = self._session.get(self._parent_row, entity_id)
        if row is None:
            return False
        row.working_head_version_id = version_id  # type: ignore[attr-defined]
        row.updated_at = now  # type: ignore[attr-defined]
        return True

    def set_accepted_version(self, entity_id: str, version_id: str, now: str) -> bool:
        """A3-07: the version generation uses by default."""
        row = self._session.get(self._parent_row, entity_id)
        if row is None:
            return False
        row.accepted_version_id = version_id  # type: ignore[attr-defined]
        row.updated_at = now  # type: ignore[attr-defined]
        return True

    def update_title(self, entity_id: str, title: str, now: str) -> bool:
        row = self._session.get(self._parent_row, entity_id)
        if row is None:
            return False
        row.title = title  # type: ignore[attr-defined]
        row.updated_at = now  # type: ignore[attr-defined]
        return True

    # ---- versions ---------------------------------------------------
    def add_version(self, record: VersionRecord) -> None:
        payload = {c: record.payload.get(c) for c in self._payload_cols}
        payload = {k: v for k, v in payload.items() if v is not None}
        self._session.add(
            self._version_row(
                id=record.id,
                project_id=record.project_id,
                version_number=record.version_number,
                change_note=record.change_note,
                accepted=int(record.accepted),
                created_at=record.created_at,
                **{self._parent_fk: record.parent_id},
                **payload,
            )
        )

    def get_version(self, version_id: str) -> VersionRecord | None:
        row = self._session.get(self._version_row, version_id)
        return None if row is None else self._version(row)

    def list_versions(self, parent_id: str) -> list[VersionRecord]:
        column = getattr(self._version_row, self._parent_fk)
        stmt = (
            select(self._version_row)
            .where(column == parent_id)
            .order_by(self._version_row.version_number)  # type: ignore[attr-defined]
        )
        return [self._version(r) for r in self._session.scalars(stmt)]

    def next_version_number(self, parent_id: str) -> int:
        column = getattr(self._version_row, self._parent_fk)
        current = self._session.scalar(
            select(func.max(self._version_row.version_number)).where(  # type: ignore[attr-defined]
                column == parent_id
            )
        )
        return int(current or 0) + 1

    def accept_version(self, version_id: str) -> bool:
        row = self._session.get(self._version_row, version_id)
        if row is None:
            return False
        row.accepted = 1  # type: ignore[attr-defined]
        return True

    # ---- mappers ----------------------------------------------------
    @staticmethod
    def _entity(row: Any) -> EntityRecord:
        return EntityRecord(
            id=row.id,
            project_id=row.project_id,
            title=row.title,
            working_head_version_id=row.working_head_version_id,
            accepted_version_id=row.accepted_version_id,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    def _version(self, row: Any) -> VersionRecord:
        return VersionRecord(
            id=row.id,
            parent_id=getattr(row, self._parent_fk),
            project_id=row.project_id,
            version_number=row.version_number,
            change_note=row.change_note,
            accepted=bool(row.accepted),
            created_at=row.created_at,
            payload={c: getattr(row, c) for c in self._payload_cols},
        )


# ------------------------------------------------------------- chapters
class StoryChapterRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, record: ChapterRecord) -> None:
        self._session.add(
            StoryChapterRow(
                id=record.id,
                story_outline_id=record.story_outline_id,
                project_id=record.project_id,
                chapter_number=record.chapter_number,
                title=record.title,
                working_head_plan_version_id=record.working_head_plan_version_id,
                accepted_plan_version_id=record.accepted_plan_version_id,
                created_at=record.created_at,
                updated_at=record.updated_at,
            )
        )

    def get(self, chapter_id: str) -> ChapterRecord | None:
        row = self._session.get(StoryChapterRow, chapter_id)
        return None if row is None else self._map(row)

    def list_for_outline(self, outline_id: str) -> list[ChapterRecord]:
        stmt = (
            select(StoryChapterRow)
            .where(StoryChapterRow.story_outline_id == outline_id)
            .order_by(StoryChapterRow.chapter_number)
        )
        return [self._map(r) for r in self._session.scalars(stmt)]

    def next_chapter_number(self, outline_id: str) -> int:
        current = self._session.scalar(
            select(func.max(StoryChapterRow.chapter_number)).where(
                StoryChapterRow.story_outline_id == outline_id
            )
        )
        return int(current or 0) + 1

    def update_fields(self, chapter_id: str, **fields: object) -> bool:
        row = self._session.get(StoryChapterRow, chapter_id)
        if row is None:
            return False
        for key, value in fields.items():
            setattr(row, key, value)
        return True

    @staticmethod
    def _map(row: StoryChapterRow) -> ChapterRecord:
        return ChapterRecord(
            id=row.id,
            story_outline_id=row.story_outline_id,
            project_id=row.project_id,
            chapter_number=row.chapter_number,
            title=row.title,
            working_head_plan_version_id=row.working_head_plan_version_id,
            accepted_plan_version_id=row.accepted_plan_version_id,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )


# --------------------------------------------------------------- scenes
class StorySceneRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, record: SceneRecord) -> None:
        self._session.add(
            StorySceneRow(
                id=record.id,
                story_chapter_id=record.story_chapter_id,
                project_id=record.project_id,
                scene_number=record.scene_number,
                title=record.title,
                working_head_card_version_id=record.working_head_card_version_id,
                accepted_card_version_id=record.accepted_card_version_id,
                working_draft_id=record.working_draft_id,
                accepted_draft_id=record.accepted_draft_id,
                summary_text=record.summary_text,
                created_at=record.created_at,
                updated_at=record.updated_at,
            )
        )

    def get(self, scene_id: str) -> SceneRecord | None:
        row = self._session.get(StorySceneRow, scene_id)
        return None if row is None else self._map(row)

    def list_for_chapter(self, chapter_id: str) -> list[SceneRecord]:
        stmt = (
            select(StorySceneRow)
            .where(StorySceneRow.story_chapter_id == chapter_id)
            .order_by(StorySceneRow.scene_number)
        )
        return [self._map(r) for r in self._session.scalars(stmt)]

    def next_scene_number(self, chapter_id: str) -> int:
        current = self._session.scalar(
            select(func.max(StorySceneRow.scene_number)).where(
                StorySceneRow.story_chapter_id == chapter_id
            )
        )
        return int(current or 0) + 1

    def update_fields(self, scene_id: str, **fields: object) -> bool:
        row = self._session.get(StorySceneRow, scene_id)
        if row is None:
            return False
        for key, value in fields.items():
            setattr(row, key, value)
        return True

    @staticmethod
    def _map(row: StorySceneRow) -> SceneRecord:
        return SceneRecord(
            id=row.id,
            story_chapter_id=row.story_chapter_id,
            project_id=row.project_id,
            scene_number=row.scene_number,
            title=row.title,
            working_head_card_version_id=row.working_head_card_version_id,
            accepted_card_version_id=row.accepted_card_version_id,
            working_draft_id=row.working_draft_id,
            accepted_draft_id=row.accepted_draft_id,
            summary_text=row.summary_text,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )


# ---------------------------------------------------------- scene cards
class SceneCardVersionRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, record: VersionRecord) -> None:
        self._session.add(
            SceneCardVersionRow(
                id=record.id,
                story_scene_id=record.parent_id,
                project_id=record.project_id,
                version_number=record.version_number,
                change_note=record.change_note,
                accepted=int(record.accepted),
                created_at=record.created_at,
                card_json=str(record.payload.get("card_json", "{}")),
                content_mode=str(record.payload.get("content_mode", "general")),
            )
        )

    def get(self, version_id: str) -> VersionRecord | None:
        row = self._session.get(SceneCardVersionRow, version_id)
        return None if row is None else self._map(row)

    def list_for_scene(self, scene_id: str) -> list[VersionRecord]:
        stmt = (
            select(SceneCardVersionRow)
            .where(SceneCardVersionRow.story_scene_id == scene_id)
            .order_by(SceneCardVersionRow.version_number)
        )
        return [self._map(r) for r in self._session.scalars(stmt)]

    def next_version_number(self, scene_id: str) -> int:
        current = self._session.scalar(
            select(func.max(SceneCardVersionRow.version_number)).where(
                SceneCardVersionRow.story_scene_id == scene_id
            )
        )
        return int(current or 0) + 1

    def accept(self, version_id: str) -> bool:
        row = self._session.get(SceneCardVersionRow, version_id)
        if row is None:
            return False
        row.accepted = 1
        return True

    @staticmethod
    def _map(row: SceneCardVersionRow) -> VersionRecord:
        return VersionRecord(
            id=row.id,
            parent_id=row.story_scene_id,
            project_id=row.project_id,
            version_number=row.version_number,
            change_note=row.change_note,
            accepted=bool(row.accepted),
            created_at=row.created_at,
            payload={"card_json": row.card_json, "content_mode": row.content_mode},
        )


# ----------------------------------------------- stable story blocks
class StoryBlockProjectionRepository:
    """Materialize and validate the lossless projection of Scene prose."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, draft_id: str) -> StoryBlockProjectionRecord | None:
        block_set = self._session.get(SceneDraftBlockSetRow, draft_id)
        if block_set is None:
            return None
        rows = self._session.scalars(
            select(StoryBlockRevisionRow)
            .where(StoryBlockRevisionRow.scene_draft_id == draft_id)
            .order_by(StoryBlockRevisionRow.ordinal)
        )
        return StoryBlockProjectionRecord(
            scene_draft_id=block_set.scene_draft_id,
            project_id=block_set.project_id,
            story_scene_id=block_set.story_scene_id,
            parent_draft_id=block_set.parent_draft_id,
            parent_draft_available=bool(block_set.parent_draft_available),
            schema_version=block_set.schema_version,
            segmentation_version=block_set.segmentation_version,
            mapping_version=block_set.mapping_version,
            leading_text=block_set.leading_text,
            prose_sha256=block_set.prose_sha256,
            manifest_sha256=block_set.manifest_sha256,
            lineage_quality=block_set.lineage_quality,
            block_count=block_set.block_count,
            created_at=block_set.created_at,
            blocks=tuple(self._map_revision(row) for row in rows),
        )

    def materialize(self, record: DraftRecord) -> None:
        """Add a deterministic projection in the caller's draft transaction."""

        if self._session.get(SceneDraftBlockSetRow, record.id) is not None:
            raise StoryBlockIntegrityError(
                f"draft {record.id} already has a stable block projection"
            )

        parent_blocks: tuple[ParentStoryBlock, ...] = ()
        parent_available = False
        parent_logical_ids: set[str] = set()
        if record.parent_draft_id is not None:
            if record.parent_draft_id == record.id:
                raise StoryBlockIntegrityError("a Scene draft cannot parent itself")
            parent_draft = self._session.get(SceneDraftRow, record.parent_draft_id)
            if parent_draft is None:
                raise StoryBlockIntegrityError(
                    "a new Scene draft requires its persisted parent projection"
                )
            if (
                parent_draft.project_id,
                parent_draft.story_scene_id,
            ) != (record.project_id, record.story_scene_id):
                raise StoryBlockIntegrityError(
                    "a new Scene draft parent must belong to the same project and scene"
                )
            parent = self.require_valid(record.parent_draft_id)
            parent_blocks = tuple(
                ParentStoryBlock(
                    ordinal=block.ordinal,
                    logical_block_id=block.logical_block_id,
                    block_revision_id=block.block_revision_id,
                    text_sha256=block.text_sha256,
                )
                for block in parent.blocks
            )
            parent_logical_ids = {
                block.logical_block_id for block in parent.blocks
            }
            parent_available = True

        projection = project_story_blocks(
            draft_id=record.id,
            prose_text=record.prose_text,
            parent_blocks=parent_blocks,
            parent_draft_id=record.parent_draft_id,
            parent_draft_available=parent_available,
        )
        for block in projection.blocks:
            anchor = self._session.get(
                StoryBlockAnchorRow, block.logical_block_id
            )
            if anchor is None:
                self._session.add(
                    StoryBlockAnchorRow(
                        logical_block_id=block.logical_block_id,
                        project_id=record.project_id,
                        story_scene_id=record.story_scene_id,
                        created_from_draft_id=record.id,
                        created_at=record.created_at,
                    )
                )
            elif (
                anchor.project_id,
                anchor.story_scene_id,
            ) != (record.project_id, record.story_scene_id):
                raise StoryBlockIntegrityError(
                    "stable logical block ID ownership collision"
                )
            elif block.logical_block_id not in parent_logical_ids:
                raise StoryBlockIntegrityError(
                    "a new logical block ID unexpectedly reuses an existing anchor"
                )

        # No ORM relationships are declared for these audit rows.  Flush each
        # dependency layer explicitly rather than relying on mapper ordering.
        self._session.flush()

        self._session.add(
            SceneDraftBlockSetRow(
                scene_draft_id=record.id,
                project_id=record.project_id,
                story_scene_id=record.story_scene_id,
                parent_draft_id=record.parent_draft_id,
                parent_draft_available=int(parent_available),
                schema_version=BLOCK_SCHEMA_VERSION,
                segmentation_version=SEGMENTATION_VERSION,
                mapping_version=MAPPING_VERSION,
                leading_text=projection.leading_text,
                prose_sha256=projection.prose_sha256,
                manifest_sha256=projection.manifest_sha256,
                lineage_quality=projection.lineage_quality,
                block_count=len(projection.blocks),
                created_at=record.created_at,
            )
        )
        self._session.flush()
        for block in projection.blocks:
            self._session.add(
                StoryBlockRevisionRow(
                    block_revision_id=block.block_revision_id,
                    logical_block_id=block.logical_block_id,
                    scene_draft_id=record.id,
                    project_id=record.project_id,
                    story_scene_id=record.story_scene_id,
                    parent_block_revision_id=block.parent_block_revision_id,
                    ordinal=block.ordinal,
                    block_type=block.block_type,
                    text=block.text,
                    separator_after=block.separator_after,
                    text_sha256=block.text_sha256,
                    source_slice_sha256=block.source_slice_sha256,
                    created_at=record.created_at,
                )
            )
        self._session.flush()

    def require_valid(self, draft_id: str) -> StoryBlockProjectionRecord:
        """Read and independently recompute every hash and mapping decision."""

        draft = self._session.get(SceneDraftRow, draft_id)
        if draft is None:
            raise StoryBlockIntegrityError(f"Scene draft {draft_id} does not exist")
        stored = self.get(draft_id)
        if stored is None:
            raise StoryBlockIntegrityError(
                f"Scene draft {draft_id} has no stable block projection"
            )
        if (
            stored.project_id,
            stored.story_scene_id,
            stored.parent_draft_id,
            stored.created_at,
        ) != (
            draft.project_id,
            draft.story_scene_id,
            draft.parent_draft_id,
            draft.created_at,
        ):
            raise StoryBlockIntegrityError(
                f"Scene draft {draft_id} block-set ownership or source edge differs"
            )

        parent_blocks: tuple[ParentStoryBlock, ...] = ()
        if stored.parent_draft_available:
            if stored.parent_draft_id is None:
                raise StoryBlockIntegrityError(
                    f"Scene draft {draft_id} claims an available blank parent"
                )
            parent_draft = self._session.get(SceneDraftRow, stored.parent_draft_id)
            parent = self.get(stored.parent_draft_id)
            if parent_draft is None or parent is None:
                raise StoryBlockIntegrityError(
                    f"Scene draft {draft_id} parent projection is unavailable"
                )
            if (
                parent.project_id,
                parent.story_scene_id,
                parent.schema_version,
                parent.segmentation_version,
                parent.mapping_version,
                parent.block_count,
                parent.reconstruct(),
            ) != (
                stored.project_id,
                stored.story_scene_id,
                BLOCK_SCHEMA_VERSION,
                SEGMENTATION_VERSION,
                MAPPING_VERSION,
                len(parent.blocks),
                parent_draft.prose_text,
            ):
                raise StoryBlockIntegrityError(
                    f"Scene draft {draft_id} parent projection is inconsistent"
                )
            parent_blocks = tuple(
                ParentStoryBlock(
                    ordinal=block.ordinal,
                    logical_block_id=block.logical_block_id,
                    block_revision_id=block.block_revision_id,
                    text_sha256=block.text_sha256,
                )
                for block in parent.blocks
            )

        try:
            expected = project_story_blocks(
                draft_id=draft_id,
                prose_text=draft.prose_text,
                parent_blocks=parent_blocks,
                parent_draft_id=stored.parent_draft_id,
                parent_draft_available=stored.parent_draft_available,
            )
        except ValueError as exc:
            raise StoryBlockIntegrityError(
                f"Scene draft {draft_id} cannot reproduce its block manifest"
            ) from exc
        if (
            stored.schema_version,
            stored.segmentation_version,
            stored.mapping_version,
            stored.leading_text,
            stored.prose_sha256,
            stored.manifest_sha256,
            stored.lineage_quality,
            stored.block_count,
            stored.reconstruct(),
        ) != (
            BLOCK_SCHEMA_VERSION,
            SEGMENTATION_VERSION,
            MAPPING_VERSION,
            expected.leading_text,
            expected.prose_sha256,
            expected.manifest_sha256,
            expected.lineage_quality,
            len(expected.blocks),
            draft.prose_text,
        ):
            raise StoryBlockIntegrityError(
                f"Scene draft {draft_id} block-set hash or manifest differs"
            )

        expected_blocks = tuple(
            (
                block.ordinal,
                block.logical_block_id,
                block.block_revision_id,
                block.parent_block_revision_id,
                block.block_type,
                block.text,
                block.separator_after,
                block.text_sha256,
                block.source_slice_sha256,
            )
            for block in expected.blocks
        )
        stored_blocks = tuple(
            (
                block.ordinal,
                block.logical_block_id,
                block.block_revision_id,
                block.parent_block_revision_id,
                block.block_type,
                block.text,
                block.separator_after,
                block.text_sha256,
                block.source_slice_sha256,
            )
            for block in stored.blocks
        )
        if stored_blocks != expected_blocks:
            raise StoryBlockIntegrityError(
                f"Scene draft {draft_id} block revisions differ from the manifest"
            )

        for block in stored.blocks:
            if (
                block.scene_draft_id,
                block.project_id,
                block.story_scene_id,
                block.created_at,
            ) != (draft_id, draft.project_id, draft.story_scene_id, draft.created_at):
                raise StoryBlockIntegrityError(
                    f"Scene draft {draft_id} block revision ownership differs"
                )
            anchor = self._session.get(
                StoryBlockAnchorRow, block.logical_block_id
            )
            if anchor is None or (
                anchor.project_id,
                anchor.story_scene_id,
            ) != (draft.project_id, draft.story_scene_id):
                raise StoryBlockIntegrityError(
                    f"Scene draft {draft_id} has a missing or foreign block anchor"
                )
            anchor_source = self._session.get(
                SceneDraftRow, anchor.created_from_draft_id
            )
            if anchor_source is None or (
                anchor_source.project_id,
                anchor_source.story_scene_id,
                anchor_source.created_at,
            ) != (draft.project_id, draft.story_scene_id, anchor.created_at):
                raise StoryBlockIntegrityError(
                    f"Scene draft {draft_id} block anchor source differs"
                )
            created_revision = self._session.scalar(
                select(StoryBlockRevisionRow.block_revision_id).where(
                    StoryBlockRevisionRow.scene_draft_id
                    == anchor.created_from_draft_id,
                    StoryBlockRevisionRow.logical_block_id
                    == block.logical_block_id,
                )
            )
            if created_revision is None:
                raise StoryBlockIntegrityError(
                    f"Scene draft {draft_id} block anchor has no origin revision"
                )
        return stored

    @staticmethod
    def _map_revision(row: StoryBlockRevisionRow) -> StoryBlockRevisionRecord:
        return StoryBlockRevisionRecord(
            block_revision_id=row.block_revision_id,
            logical_block_id=row.logical_block_id,
            scene_draft_id=row.scene_draft_id,
            project_id=row.project_id,
            story_scene_id=row.story_scene_id,
            parent_block_revision_id=row.parent_block_revision_id,
            ordinal=row.ordinal,
            block_type=row.block_type,
            text=row.text,
            separator_after=row.separator_after,
            text_sha256=row.text_sha256,
            source_slice_sha256=row.source_slice_sha256,
            created_at=row.created_at,
        )


# -------------------------------------------------------------- drafts
class SceneDraftRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, record: DraftRecord) -> None:
        self._session.add(
            SceneDraftRow(
                id=record.id,
                story_scene_id=record.story_scene_id,
                project_id=record.project_id,
                draft_number=record.draft_number,
                prose_text=record.prose_text,
                word_count=record.word_count,
                origin=record.origin,
                draft_status=record.draft_status,
                was_accepted=int(record.was_accepted),
                accepted_at=record.accepted_at,
                is_preview=int(record.is_preview),
                promoted_from_preview_draft_id=record.promoted_from_preview_draft_id,
                is_recovery_rebase=int(record.is_recovery_rebase),
                scene_card_version_id=record.scene_card_version_id,
                generation_run_id=record.generation_run_id,
                revision_request_json=record.revision_request_json,
                revision_request_fingerprint=record.revision_request_fingerprint,
                parent_draft_id=record.parent_draft_id,
                created_at=record.created_at,
            )
        )
        self._session.flush()
        StoryBlockProjectionRepository(self._session).materialize(record)

    def get(self, draft_id: str) -> DraftRecord | None:
        row = self._session.get(SceneDraftRow, draft_id)
        return None if row is None else self._map(row)

    def get_for_generation_run(self, run_id: str) -> DraftRecord | None:
        """A3-R10: the draft a run produced, looked up by its exact run ID.

        Historical inspection of a revision run needs the revision request and
        the parent draft it was applied to. Exposing the query here keeps that
        lookup on the repository, so neither the UI nor the context service
        scans drafts or assembles SQL of its own.
        """
        stmt = select(SceneDraftRow).where(SceneDraftRow.generation_run_id == run_id)
        row = self._session.scalars(stmt).first()
        return None if row is None else self._map(row)

    def list_for_scene(self, scene_id: str) -> list[DraftRecord]:
        stmt = (
            select(SceneDraftRow)
            .where(SceneDraftRow.story_scene_id == scene_id)
            .order_by(SceneDraftRow.draft_number)
        )
        return [self._map(r) for r in self._session.scalars(stmt)]

    def next_draft_number(self, scene_id: str) -> int:
        current = self._session.scalar(
            select(func.max(SceneDraftRow.draft_number)).where(
                SceneDraftRow.story_scene_id == scene_id
            )
        )
        return int(current or 0) + 1

    def mark_was_accepted(self, draft_id: str, now: str) -> bool:
        """A3-11: historical audit only. The ACTIVE pointer lives on the
        scene, so switching the accepted draft never mutates a draft row."""
        row = self._session.get(SceneDraftRow, draft_id)
        if row is None:
            return False
        if not row.was_accepted:
            row.was_accepted = 1
            row.accepted_at = now
        return True

    @staticmethod
    def _map(row: SceneDraftRow) -> DraftRecord:
        return DraftRecord(
            id=row.id,
            story_scene_id=row.story_scene_id,
            project_id=row.project_id,
            draft_number=row.draft_number,
            prose_text=row.prose_text,
            word_count=row.word_count,
            origin=row.origin,
            draft_status=row.draft_status,
            was_accepted=bool(row.was_accepted),
            accepted_at=row.accepted_at,
            is_preview=bool(row.is_preview),
            promoted_from_preview_draft_id=row.promoted_from_preview_draft_id,
            is_recovery_rebase=bool(row.is_recovery_rebase),
            scene_card_version_id=row.scene_card_version_id,
            generation_run_id=row.generation_run_id,
            revision_request_json=row.revision_request_json,
            revision_request_fingerprint=row.revision_request_fingerprint,
            parent_draft_id=row.parent_draft_id,
            created_at=row.created_at,
        )


# ------------------------------------------------------ generation runs
class GenerationRunRepository:
    """A3-10: runs are FINALIZE-ONCE — created RUNNING, finalized exactly once."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, record: GenerationRunRecord) -> None:
        self._session.add(GenerationRunRow(**asdict(record)))

    def finalize(
        self,
        run_id: str,
        *,
        status: str,
        reason_code: str = "",
        error_reason: str = "",
        completed_at: str,
        latency_ms: int,
    ) -> bool:
        """Move a RUNNING run to a terminal state. A DB trigger rejects a
        second finalization, so a lost race cannot rewrite history."""
        row = self._session.get(GenerationRunRow, run_id)
        if row is None:
            return False
        row.status = status
        row.reason_code = reason_code
        row.error_reason = error_reason
        row.completed_at = completed_at
        row.latency_ms = latency_ms
        return True

    def get(self, run_id: str) -> GenerationRunRecord | None:
        row = self._session.get(GenerationRunRow, run_id)
        return None if row is None else self._map(row)

    def list_running(self) -> list[GenerationRunRecord]:
        """RUNNING rows ordered deterministically for stale-run recovery."""
        stmt = (
            select(GenerationRunRow)
            .where(GenerationRunRow.status == "running")
            .order_by(GenerationRunRow.started_at, GenerationRunRow.id)
        )
        return [self._map(r) for r in self._session.scalars(stmt)]

    def finalize_recovered_run(
        self,
        run_id: str,
        *,
        status: str,
        reason_code: str,
        completed_at: str,
        latency_ms: int,
    ) -> bool:
        """Finalize an orphan only while it is still RUNNING.

        The state re-check keeps recovery idempotent and avoids attempting to
        rewrite a row that another finalizer has already made immutable.
        Draft rows are intentionally outside this update, preserving partial
        output exactly as it was recorded before the process stopped.
        """
        result = cast(
            CursorResult[Any],
            self._session.execute(
                update(GenerationRunRow)
                .where(
                    GenerationRunRow.id == run_id,
                    GenerationRunRow.status == "running",
                )
                .values(
                    status=status,
                    reason_code=reason_code,
                    completed_at=completed_at,
                    latency_ms=latency_ms,
                )
            ),
        )
        return bool(result.rowcount)

    def list_for_scene(self, scene_id: str) -> list[GenerationRunRecord]:
        stmt = (
            select(GenerationRunRow)
            .where(GenerationRunRow.story_scene_id == scene_id)
            .order_by(GenerationRunRow.started_at, GenerationRunRow.id)
        )
        return [self._map(r) for r in self._session.scalars(stmt)]

    @staticmethod
    def _map(row: GenerationRunRow) -> GenerationRunRecord:
        return GenerationRunRecord(
            **{f.name: getattr(row, f.name) for f in fields(GenerationRunRecord)}
        )


@dataclass(slots=True)
class DraftSummaryRecord:
    """A3-R05: a summary that BELONGS to one specific draft."""

    id: str
    scene_draft_id: str
    story_scene_id: str
    project_id: str
    summary_text: str
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class AcceptedSummaryProjectionRecord:
    """Scalar-only continuity row for one scene in deterministic outline order.

    The optional draft and summary fields deliberately preserve the difference
    between "no accepted draft", "dangling accepted pointer", and "accepted
    draft without a summary".  Ownership policy stays in the application
    service instead of being hidden inside JOIN predicates.
    """

    scene_id: str
    story_chapter_id: str
    scene_project_id: str
    chapter_project_id: str
    story_outline_id: str
    chapter_number: int
    scene_number: int
    accepted_draft_id: str | None
    draft_id: str | None
    draft_story_scene_id: str | None
    draft_project_id: str | None
    summary_id: str | None
    summary_scene_draft_id: str | None
    summary_story_scene_id: str | None
    summary_project_id: str | None
    summary_text: str | None


class AcceptedSummaryProjectionRepository:
    """Load accepted-summary provenance for an outline in one bounded query."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def list_for_outline(
        self, outline_id: str
    ) -> list[AcceptedSummaryProjectionRecord]:
        # Join only by row identity.  Adding ownership predicates here would
        # turn contradictory persisted records into NULLs and let the service
        # mistake corruption for an ordinary missing summary.
        stmt = (
            select(
                StorySceneRow.id.label("scene_id"),
                StorySceneRow.story_chapter_id.label("story_chapter_id"),
                StorySceneRow.project_id.label("scene_project_id"),
                StoryChapterRow.project_id.label("chapter_project_id"),
                StoryChapterRow.story_outline_id.label("story_outline_id"),
                StoryChapterRow.chapter_number.label("chapter_number"),
                StorySceneRow.scene_number.label("scene_number"),
                StorySceneRow.accepted_draft_id.label("accepted_draft_id"),
                SceneDraftRow.id.label("draft_id"),
                SceneDraftRow.story_scene_id.label("draft_story_scene_id"),
                SceneDraftRow.project_id.label("draft_project_id"),
                SceneDraftSummaryRow.id.label("summary_id"),
                SceneDraftSummaryRow.scene_draft_id.label(
                    "summary_scene_draft_id"
                ),
                SceneDraftSummaryRow.story_scene_id.label(
                    "summary_story_scene_id"
                ),
                SceneDraftSummaryRow.project_id.label("summary_project_id"),
                SceneDraftSummaryRow.summary_text.label("summary_text"),
            )
            .join(
                StoryChapterRow,
                StorySceneRow.story_chapter_id == StoryChapterRow.id,
            )
            .outerjoin(
                SceneDraftRow,
                SceneDraftRow.id == StorySceneRow.accepted_draft_id,
            )
            .outerjoin(
                SceneDraftSummaryRow,
                SceneDraftSummaryRow.scene_draft_id
                == StorySceneRow.accepted_draft_id,
            )
            .where(StoryChapterRow.story_outline_id == outline_id)
            .order_by(
                StoryChapterRow.chapter_number,
                StorySceneRow.scene_number,
                StorySceneRow.id,
            )
        )
        return [
            AcceptedSummaryProjectionRecord(
                scene_id=scene_id,
                story_chapter_id=story_chapter_id,
                scene_project_id=scene_project_id,
                chapter_project_id=chapter_project_id,
                story_outline_id=story_outline_id,
                chapter_number=chapter_number,
                scene_number=scene_number,
                accepted_draft_id=accepted_draft_id,
                draft_id=draft_id,
                draft_story_scene_id=draft_story_scene_id,
                draft_project_id=draft_project_id,
                summary_id=summary_id,
                summary_scene_draft_id=summary_scene_draft_id,
                summary_story_scene_id=summary_story_scene_id,
                summary_project_id=summary_project_id,
                summary_text=summary_text,
            )
            for (
                scene_id,
                story_chapter_id,
                scene_project_id,
                chapter_project_id,
                story_outline_id,
                chapter_number,
                scene_number,
                accepted_draft_id,
                draft_id,
                draft_story_scene_id,
                draft_project_id,
                summary_id,
                summary_scene_draft_id,
                summary_story_scene_id,
                summary_project_id,
                summary_text,
            ) in self._session.execute(stmt)
        ]


class SceneDraftSummaryRepository:
    """Summaries tied to drafts, so "previous accepted scene summary" is a
    fact about an accepted draft rather than a free-floating scene note."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, summary_id: str) -> DraftSummaryRecord | None:
        """Load one exact historical summary record by its immutable ID."""
        row = self._session.get(SceneDraftSummaryRow, summary_id)
        return None if row is None else self._map(row)

    def upsert(self, record: DraftSummaryRecord) -> None:
        row = self._session.scalar(
            select(SceneDraftSummaryRow).where(
                SceneDraftSummaryRow.scene_draft_id == record.scene_draft_id
            )
        )
        if row is None:
            self._session.add(
                SceneDraftSummaryRow(
                    id=record.id,
                    scene_draft_id=record.scene_draft_id,
                    story_scene_id=record.story_scene_id,
                    project_id=record.project_id,
                    summary_text=record.summary_text,
                    created_at=record.created_at,
                    updated_at=record.updated_at,
                )
            )
            return
        row.summary_text = record.summary_text
        row.updated_at = record.updated_at

    def get_for_draft(self, draft_id: str) -> DraftSummaryRecord | None:
        row = self._session.scalar(
            select(SceneDraftSummaryRow).where(
                SceneDraftSummaryRow.scene_draft_id == draft_id
            )
        )
        return None if row is None else self._map(row)

    def list_for_scene(self, scene_id: str) -> list[DraftSummaryRecord]:
        stmt = (
            select(SceneDraftSummaryRow)
            .where(SceneDraftSummaryRow.story_scene_id == scene_id)
            .order_by(SceneDraftSummaryRow.created_at, SceneDraftSummaryRow.id)
        )
        return [self._map(r) for r in self._session.scalars(stmt)]

    @staticmethod
    def _map(row: SceneDraftSummaryRow) -> DraftSummaryRecord:
        return DraftSummaryRecord(
            id=row.id,
            scene_draft_id=row.scene_draft_id,
            story_scene_id=row.story_scene_id,
            project_id=row.project_id,
            summary_text=row.summary_text,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )


# ------------------------------------------------- scene card participants
class SceneCardParticipantRepository:
    """A3-03: exact Character Version pins for one Scene Card version."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, record: ParticipantRecord) -> None:
        self._session.add(
            SceneCardParticipantRow(
                id=record.id,
                scene_card_version_id=record.scene_card_version_id,
                project_id=record.project_id,
                character_id=record.character_id,
                character_version_id=record.character_version_id,
                role=record.role,
                is_pov=int(record.is_pov),
                position=record.position,
            )
        )

    def list_for_card_version(self, card_version_id: str) -> list[ParticipantRecord]:
        stmt = (
            select(SceneCardParticipantRow)
            .where(SceneCardParticipantRow.scene_card_version_id == card_version_id)
            .order_by(SceneCardParticipantRow.position, SceneCardParticipantRow.id)
        )
        return [self._map(r) for r in self._session.scalars(stmt)]

    @staticmethod
    def _map(row: SceneCardParticipantRow) -> ParticipantRecord:
        return ParticipantRecord(
            id=row.id,
            scene_card_version_id=row.scene_card_version_id,
            project_id=row.project_id,
            character_id=row.character_id,
            character_version_id=row.character_version_id,
            role=row.role,
            is_pov=bool(row.is_pov),
            position=row.position,
        )


# -------------------------------------------------------------- exports
class StoryExportRepository:
    """A3-12: an export row IS the immutable snapshot record."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def add(
        self,
        *,
        export_id: str,
        project_id: str,
        export_mode: str,
        export_contract_version: str,
        planning_mode: str,
        story_outline_id: str | None,
        requirement_version_id: str | None,
        bible_version_id: str | None,
        outline_version_id: str | None,
        snapshot_json: str,
        snapshot_sha256: str,
        markdown_byte_size: int,
        json_byte_size: int,
        created_at: str,
    ) -> None:
        self._session.add(
            StoryExportRow(
                id=export_id,
                project_id=project_id,
                export_mode=export_mode,
                export_contract_version=export_contract_version,
                planning_mode=planning_mode,
                story_outline_id=story_outline_id,
                requirement_version_id=requirement_version_id,
                bible_version_id=bible_version_id,
                outline_version_id=outline_version_id,
                snapshot_json=snapshot_json,
                snapshot_sha256=snapshot_sha256,
                markdown_byte_size=markdown_byte_size,
                json_byte_size=json_byte_size,
                total_byte_size=markdown_byte_size + json_byte_size,
                created_at=created_at,
            )
        )

    def get(self, export_id: str) -> StoryExportRow | None:
        return self._session.get(StoryExportRow, export_id)

    def get_for_project(
        self,
        export_id: str,
        project_id: str,
    ) -> StoryExportRow | None:
        """Scope an ID lookup to the active project without leaking its owner."""
        stmt = select(StoryExportRow).where(
            StoryExportRow.id == export_id,
            StoryExportRow.project_id == project_id,
        )
        return self._session.scalar(stmt)

    def list_for_project(self, project_id: str) -> list[StoryExportRow]:
        stmt = (
            select(StoryExportRow)
            .where(StoryExportRow.project_id == project_id)
            .order_by(StoryExportRow.created_at, StoryExportRow.id)
        )
        return list(self._session.scalars(stmt))
