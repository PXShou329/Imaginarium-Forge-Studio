"""Deterministic stable paragraph IDs for immutable scene drafts.

Revision ID: 0012_stable_story_blocks
Revises: 0011_world_seed_drafts
Create Date: 2026-08-26

``scene_drafts.prose_text`` remains the sole author-owned source.  The three
tables added here are a lossless, deterministic projection used by future
media bindings.  Upgrade backfills every existing draft without rewriting its
text or either scene pointer.  Downgrade removes only that rebuildable
projection and its guards.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import sqlalchemy as sa
from alembic import op

from imaginarium_forge.domain.story.blocks import (
    BLOCK_SCHEMA_VERSION,
    MAPPING_VERSION,
    SEGMENTATION_VERSION,
    ParentStoryBlock,
    StoryBlockProjection,
    project_story_blocks,
)

revision = "0012_stable_story_blocks"
down_revision = "0011_world_seed_drafts"
branch_labels = None
depends_on = None

_HASH_CHECK = "length({0}) = 64 AND {0} NOT GLOB '*[^0-9a-f]*'"
_PINNED_CONTRACT = (
    "story-block-v1",
    "paragraph-v1",
    "parent-aware-v1",
)


def _mapping(row: Any) -> Mapping[str, Any]:
    return row._mapping  # type: ignore[no-any-return]


def _insert_projection(
    bind: sa.Connection,
    draft: Mapping[str, Any],
    projection: StoryBlockProjection,
    *,
    parent_draft_available: bool,
) -> None:
    draft_id = str(draft["id"])
    project_id = str(draft["project_id"])
    scene_id = str(draft["story_scene_id"])
    created_at = str(draft["created_at"])
    parent_draft_id = draft["parent_draft_id"]

    for block in projection.blocks:
        anchor = bind.execute(
            sa.text(
                "SELECT project_id,story_scene_id FROM story_block_anchors "
                "WHERE logical_block_id=:logical_block_id"
            ),
            {"logical_block_id": block.logical_block_id},
        ).first()
        if anchor is None:
            bind.execute(
                sa.text(
                    "INSERT INTO story_block_anchors "
                    "(logical_block_id,project_id,story_scene_id,"
                    "created_from_draft_id,created_at) VALUES "
                    "(:logical_block_id,:project_id,:story_scene_id,"
                    ":created_from_draft_id,:created_at)"
                ),
                {
                    "logical_block_id": block.logical_block_id,
                    "project_id": project_id,
                    "story_scene_id": scene_id,
                    "created_from_draft_id": draft_id,
                    "created_at": created_at,
                },
            )
        else:
            values = _mapping(anchor)
            if (values["project_id"], values["story_scene_id"]) != (
                project_id,
                scene_id,
            ):
                raise RuntimeError(
                    "0012 backfill found a logical block ID ownership collision"
                )

    bind.execute(
        sa.text(
            "INSERT INTO scene_draft_block_sets "
            "(scene_draft_id,project_id,story_scene_id,parent_draft_id,"
            "parent_draft_available,schema_version,segmentation_version,"
            "mapping_version,leading_text,prose_sha256,manifest_sha256,"
            "lineage_quality,block_count,created_at) VALUES "
            "(:scene_draft_id,:project_id,:story_scene_id,:parent_draft_id,"
            ":parent_draft_available,:schema_version,:segmentation_version,"
            ":mapping_version,:leading_text,:prose_sha256,:manifest_sha256,"
            ":lineage_quality,:block_count,:created_at)"
        ),
        {
            "scene_draft_id": draft_id,
            "project_id": project_id,
            "story_scene_id": scene_id,
            "parent_draft_id": parent_draft_id,
            "parent_draft_available": int(parent_draft_available),
            "schema_version": BLOCK_SCHEMA_VERSION,
            "segmentation_version": SEGMENTATION_VERSION,
            "mapping_version": MAPPING_VERSION,
            "leading_text": projection.leading_text,
            "prose_sha256": projection.prose_sha256,
            "manifest_sha256": projection.manifest_sha256,
            "lineage_quality": projection.lineage_quality,
            "block_count": len(projection.blocks),
            "created_at": created_at,
        },
    )
    for block in projection.blocks:
        bind.execute(
            sa.text(
                "INSERT INTO story_block_revisions "
                "(block_revision_id,logical_block_id,scene_draft_id,project_id,"
                "story_scene_id,parent_block_revision_id,ordinal,block_type,text,"
                "separator_after,text_sha256,source_slice_sha256,created_at) VALUES "
                "(:block_revision_id,:logical_block_id,:scene_draft_id,:project_id,"
                ":story_scene_id,:parent_block_revision_id,:ordinal,:block_type,:text,"
                ":separator_after,:text_sha256,:source_slice_sha256,:created_at)"
            ),
            {
                "block_revision_id": block.block_revision_id,
                "logical_block_id": block.logical_block_id,
                "scene_draft_id": draft_id,
                "project_id": project_id,
                "story_scene_id": scene_id,
                "parent_block_revision_id": block.parent_block_revision_id,
                "ordinal": block.ordinal,
                "block_type": block.block_type,
                "text": block.text,
                "separator_after": block.separator_after,
                "text_sha256": block.text_sha256,
                "source_slice_sha256": block.source_slice_sha256,
                "created_at": created_at,
            },
        )


def _build_backfill_plan(
    bind: sa.Connection,
) -> list[tuple[Mapping[str, Any], StoryBlockProjection, bool]]:
    """Validate every source row and compute all IDs before any 0012 DDL."""

    rows = bind.execute(
        sa.text(
            "SELECT id,project_id,story_scene_id,parent_draft_id,prose_text,"
            "created_at FROM scene_drafts "
            "ORDER BY project_id,story_scene_id,draft_number,id"
        )
    ).all()
    drafts = {str(_mapping(row)["id"]): _mapping(row) for row in rows}
    remaining = dict(drafts)
    projected: dict[str, StoryBlockProjection] = {}
    plan: list[tuple[Mapping[str, Any], StoryBlockProjection, bool]] = []

    invalid_ownership = bind.execute(
        sa.text(
            "SELECT draft.id FROM scene_drafts AS draft "
            "LEFT JOIN story_scenes AS scene "
            "ON scene.id=draft.story_scene_id "
            "AND scene.project_id=draft.project_id "
            "WHERE scene.id IS NULL ORDER BY draft.id LIMIT 1"
        )
    ).scalar_one_or_none()
    if invalid_ownership is not None:
        raise RuntimeError(
            "0012 backfill blocked: a Scene draft has invalid scene ownership"
        )
    invalid_pointer = bind.execute(
        sa.text(
            "SELECT scene.id FROM story_scenes AS scene "
            "WHERE (scene.working_draft_id IS NOT NULL AND NOT EXISTS ("
            "SELECT 1 FROM scene_drafts AS draft "
            "WHERE draft.id=scene.working_draft_id "
            "AND draft.story_scene_id=scene.id "
            "AND draft.project_id=scene.project_id)) "
            "OR (scene.accepted_draft_id IS NOT NULL AND NOT EXISTS ("
            "SELECT 1 FROM scene_drafts AS draft "
            "WHERE draft.id=scene.accepted_draft_id "
            "AND draft.story_scene_id=scene.id "
            "AND draft.project_id=scene.project_id)) "
            "ORDER BY scene.id LIMIT 1"
        )
    ).scalar_one_or_none()
    if invalid_pointer is not None:
        raise RuntimeError(
            "0012 backfill blocked: a Story scene has an invalid draft pointer"
        )

    for draft_id, draft in drafts.items():
        if draft["parent_draft_id"] == draft_id:
            raise RuntimeError(
                "0012 backfill blocked: a scene draft names itself as parent"
            )

    while remaining:
        progressed = False
        for draft_id in tuple(remaining):
            draft = remaining[draft_id]
            parent_id = draft["parent_draft_id"]
            if parent_id is None:
                parent_available = False
                parent_blocks: tuple[ParentStoryBlock, ...] = ()
            else:
                parent = drafts.get(str(parent_id))
                same_owner = parent is not None and (
                    parent["project_id"],
                    parent["story_scene_id"],
                ) == (draft["project_id"], draft["story_scene_id"])
                if same_owner and str(parent_id) in remaining:
                    continue
                parent_available = bool(same_owner)
                parent_projection = projected.get(str(parent_id))
                if parent_available and parent_projection is None:
                    raise RuntimeError(
                        "0012 preflight could not resolve an available parent draft"
                    )
                parent_blocks = (
                    tuple(
                        ParentStoryBlock(
                            ordinal=block.ordinal,
                            logical_block_id=block.logical_block_id,
                            block_revision_id=block.block_revision_id,
                            text_sha256=block.text_sha256,
                        )
                        for block in parent_projection.blocks
                    )
                    if parent_available and parent_projection is not None
                    else ()
                )
            projection = project_story_blocks(
                draft_id=draft_id,
                prose_text=str(draft["prose_text"]),
                parent_blocks=parent_blocks,
                parent_draft_id=(None if parent_id is None else str(parent_id)),
                parent_draft_available=parent_available,
            )
            if projection.reconstruct() != str(draft["prose_text"]):
                raise RuntimeError(
                    f"0012 preflight failed exact prose roundtrip for draft {draft_id}"
                )
            projected[draft_id] = projection
            plan.append((draft, projection, parent_available))
            del remaining[draft_id]
            progressed = True

        if progressed:
            continue

        # A same-scene legacy cycle has no trustworthy direction.  Preserve
        # the source edge but deliberately create fresh IDs for every member.
        for draft_id in tuple(remaining):
            draft = remaining[draft_id]
            projection = project_story_blocks(
                draft_id=draft_id,
                prose_text=str(draft["prose_text"]),
                parent_draft_id=str(draft["parent_draft_id"]),
                parent_draft_available=False,
            )
            projected[draft_id] = projection
            plan.append((draft, projection, False))
            del remaining[draft_id]

    anchor_owners: dict[str, tuple[str, str]] = {}
    for draft, projection, _parent_available in plan:
        owner = (str(draft["project_id"]), str(draft["story_scene_id"]))
        for block in projection.blocks:
            prior = anchor_owners.setdefault(block.logical_block_id, owner)
            if prior != owner:
                raise RuntimeError(
                    "0012 preflight found a logical block ID ownership collision"
                )
    return plan


def _backfill(
    bind: sa.Connection,
    plan: list[tuple[Mapping[str, Any], StoryBlockProjection, bool]],
) -> None:
    for draft, projection, parent_available in plan:
        _insert_projection(
            bind,
            draft,
            projection,
            parent_draft_available=parent_available,
        )

    for draft, _projection, _parent_available in plan:
        draft_id = str(draft["id"])
        stored_set = bind.execute(
            sa.text(
                "SELECT leading_text,block_count FROM scene_draft_block_sets "
                "WHERE scene_draft_id=:draft_id"
            ),
            {"draft_id": draft_id},
        ).one()
        values = _mapping(stored_set)
        blocks = bind.execute(
            sa.text(
                "SELECT text,separator_after FROM story_block_revisions "
                "WHERE scene_draft_id=:draft_id ORDER BY ordinal"
            ),
            {"draft_id": draft_id},
        ).all()
        reconstructed = str(values["leading_text"]) + "".join(
            str(_mapping(block)["text"]) + str(_mapping(block)["separator_after"])
            for block in blocks
        )
        if reconstructed != str(draft["prose_text"]):
            raise RuntimeError(
                f"0012 backfill failed exact prose roundtrip for draft {draft_id}"
            )
        if int(values["block_count"]) != len(blocks):
            raise RuntimeError(
                f"0012 backfill produced an incomplete manifest for draft {draft_id}"
            )


def _create_triggers() -> None:
    op.execute(
        """
        CREATE TRIGGER scene_draft_block_sets_source_guard_insert
        BEFORE INSERT ON scene_draft_block_sets
        WHEN NOT EXISTS (
            SELECT 1 FROM scene_drafts AS draft
            WHERE draft.id = NEW.scene_draft_id
              AND draft.project_id = NEW.project_id
              AND draft.story_scene_id = NEW.story_scene_id
              AND draft.parent_draft_id IS NEW.parent_draft_id
              AND draft.created_at = NEW.created_at
        )
        BEGIN
            SELECT RAISE(ABORT,
                'scene_draft_block_sets: source draft identity mismatch');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER story_block_revisions_manifest_guard_insert
        BEFORE INSERT ON story_block_revisions
        WHEN NOT EXISTS (
            SELECT 1 FROM scene_draft_block_sets AS block_set
            WHERE block_set.scene_draft_id = NEW.scene_draft_id
              AND block_set.project_id = NEW.project_id
              AND block_set.story_scene_id = NEW.story_scene_id
              AND NEW.ordinal < block_set.block_count
        )
        BEGIN
            SELECT RAISE(ABORT,
                'story_block_revisions: block-set manifest mismatch');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER story_block_revisions_parent_guard_insert
        BEFORE INSERT ON story_block_revisions
        WHEN NEW.parent_block_revision_id IS NOT NULL
         AND NOT EXISTS (
            SELECT 1
            FROM scene_draft_block_sets AS child_set
            JOIN story_block_revisions AS parent
              ON parent.block_revision_id = NEW.parent_block_revision_id
            WHERE child_set.scene_draft_id = NEW.scene_draft_id
              AND child_set.parent_draft_available = 1
              AND parent.scene_draft_id = child_set.parent_draft_id
              AND parent.logical_block_id = NEW.logical_block_id
              AND parent.project_id = NEW.project_id
              AND parent.story_scene_id = NEW.story_scene_id
        )
        BEGIN
            SELECT RAISE(ABORT,
                'story_block_revisions: invalid parent lineage');
        END
        """
    )
    for table in (
        "story_block_anchors",
        "scene_draft_block_sets",
        "story_block_revisions",
    ):
        op.execute(
            f"""
            CREATE TRIGGER {table}_write_once_update
            BEFORE UPDATE ON {table}
            BEGIN
                SELECT RAISE(ABORT, '{table}: stable projection is immutable');
            END
            """
        )
        op.execute(
            f"""
            CREATE TRIGGER {table}_write_once_delete
            BEFORE DELETE ON {table}
            BEGIN
                SELECT RAISE(ABORT, '{table}: stable projection is immutable');
            END
            """
        )
    op.execute(
        """
        CREATE TRIGGER scene_drafts_block_source_immutable_update
        BEFORE UPDATE OF id,project_id,story_scene_id,parent_draft_id,prose_text,
            created_at
        ON scene_drafts
        WHEN EXISTS (
            SELECT 1 FROM scene_draft_block_sets
            WHERE scene_draft_id = OLD.id
        ) AND (
            OLD.id IS NOT NEW.id
            OR OLD.project_id IS NOT NEW.project_id
            OR OLD.story_scene_id IS NOT NEW.story_scene_id
            OR OLD.parent_draft_id IS NOT NEW.parent_draft_id
            OR OLD.prose_text IS NOT NEW.prose_text
            OR OLD.created_at IS NOT NEW.created_at
        )
        BEGIN
            SELECT RAISE(ABORT,
                'scene_drafts: stable block source is immutable');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER story_scenes_block_pointer_guard_insert
        BEFORE INSERT ON story_scenes
        WHEN (NEW.working_draft_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM scene_draft_block_sets AS block_set
            WHERE block_set.scene_draft_id = NEW.working_draft_id
              AND block_set.project_id = NEW.project_id
              AND block_set.story_scene_id = NEW.id
              AND block_set.block_count = (
                  SELECT count(*) FROM story_block_revisions AS revision
                  WHERE revision.scene_draft_id = block_set.scene_draft_id
              )
        )) OR (NEW.accepted_draft_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM scene_draft_block_sets AS block_set
            WHERE block_set.scene_draft_id = NEW.accepted_draft_id
              AND block_set.project_id = NEW.project_id
              AND block_set.story_scene_id = NEW.id
              AND block_set.block_count = (
                  SELECT count(*) FROM story_block_revisions AS revision
                  WHERE revision.scene_draft_id = block_set.scene_draft_id
              )
        ))
        BEGIN
            SELECT RAISE(ABORT,
                'story_scenes: draft pointer requires a complete block projection');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER story_scenes_block_pointer_guard_update
        BEFORE UPDATE OF working_draft_id,accepted_draft_id ON story_scenes
        WHEN (NEW.working_draft_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM scene_draft_block_sets AS block_set
            WHERE block_set.scene_draft_id = NEW.working_draft_id
              AND block_set.project_id = NEW.project_id
              AND block_set.story_scene_id = NEW.id
              AND block_set.block_count = (
                  SELECT count(*) FROM story_block_revisions AS revision
                  WHERE revision.scene_draft_id = block_set.scene_draft_id
              )
        )) OR (NEW.accepted_draft_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM scene_draft_block_sets AS block_set
            WHERE block_set.scene_draft_id = NEW.accepted_draft_id
              AND block_set.project_id = NEW.project_id
              AND block_set.story_scene_id = NEW.id
              AND block_set.block_count = (
                  SELECT count(*) FROM story_block_revisions AS revision
                  WHERE revision.scene_draft_id = block_set.scene_draft_id
              )
        ))
        BEGIN
            SELECT RAISE(ABORT,
                'story_scenes: draft pointer requires a complete block projection');
        END
        """
    )


def upgrade() -> None:
    if (
        BLOCK_SCHEMA_VERSION,
        SEGMENTATION_VERSION,
        MAPPING_VERSION,
    ) != _PINNED_CONTRACT:
        raise RuntimeError(
            "0012 stable-block domain contract changed; add a reviewed "
            "fix-forward migration instead of reinterpreting historical drafts"
        )
    bind = op.get_bind()
    backfill_plan = _build_backfill_plan(bind)
    op.create_table(
        "story_block_anchors",
        sa.Column("logical_block_id", sa.Text(), primary_key=True),
        sa.Column("project_id", sa.Text(), nullable=False),
        sa.Column("story_scene_id", sa.Text(), nullable=False),
        sa.Column("created_from_draft_id", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["created_from_draft_id", "project_id"],
            ["scene_drafts.id", "scene_drafts.project_id"],
            name="fk_story_block_anchor_draft_project",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_from_draft_id", "story_scene_id"],
            ["scene_drafts.id", "scene_drafts.story_scene_id"],
            name="fk_story_block_anchor_draft_scene",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "logical_block_id",
            "project_id",
            "story_scene_id",
            name="uq_story_block_anchor_ownership",
        ),
        sa.CheckConstraint(
            "length(trim(logical_block_id)) > 0",
            name="ck_story_block_anchor_id",
        ),
    )
    op.create_index(
        "ix_story_block_anchors_scene",
        "story_block_anchors",
        ["story_scene_id", "logical_block_id"],
    )

    op.create_table(
        "scene_draft_block_sets",
        sa.Column("scene_draft_id", sa.Text(), primary_key=True),
        sa.Column("project_id", sa.Text(), nullable=False),
        sa.Column("story_scene_id", sa.Text(), nullable=False),
        sa.Column("parent_draft_id", sa.Text(), nullable=True),
        sa.Column(
            "parent_draft_available", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("schema_version", sa.Text(), nullable=False),
        sa.Column("segmentation_version", sa.Text(), nullable=False),
        sa.Column("mapping_version", sa.Text(), nullable=False),
        sa.Column("leading_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("prose_sha256", sa.Text(), nullable=False),
        sa.Column("manifest_sha256", sa.Text(), nullable=False),
        sa.Column("lineage_quality", sa.Text(), nullable=False),
        sa.Column("block_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["scene_draft_id", "project_id"],
            ["scene_drafts.id", "scene_drafts.project_id"],
            name="fk_scene_draft_block_set_draft_project",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["scene_draft_id", "story_scene_id"],
            ["scene_drafts.id", "scene_drafts.story_scene_id"],
            name="fk_scene_draft_block_set_draft_scene",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "scene_draft_id",
            "project_id",
            "story_scene_id",
            name="uq_scene_draft_block_set_ownership",
        ),
        sa.CheckConstraint(
            f"schema_version = '{BLOCK_SCHEMA_VERSION}'",
            name="ck_scene_draft_block_set_schema",
        ),
        sa.CheckConstraint(
            f"segmentation_version = '{SEGMENTATION_VERSION}'",
            name="ck_scene_draft_block_set_segmentation",
        ),
        sa.CheckConstraint(
            f"mapping_version = '{MAPPING_VERSION}'",
            name="ck_scene_draft_block_set_mapping",
        ),
        sa.CheckConstraint(
            _HASH_CHECK.format("prose_sha256"),
            name="ck_scene_draft_block_set_prose_sha256",
        ),
        sa.CheckConstraint(
            _HASH_CHECK.format("manifest_sha256"),
            name="ck_scene_draft_block_set_manifest_sha256",
        ),
        sa.CheckConstraint(
            "lineage_quality IN ('root','exact','conservative','legacy_unlinked')",
            name="ck_scene_draft_block_set_lineage_quality",
        ),
        sa.CheckConstraint(
            "parent_draft_available IN (0,1)",
            name="ck_scene_draft_block_set_parent_available",
        ),
        sa.CheckConstraint(
            "(parent_draft_id IS NULL AND parent_draft_available = 0 "
            "AND lineage_quality = 'root') OR "
            "(parent_draft_id IS NOT NULL AND length(trim(parent_draft_id)) > 0 "
            "AND parent_draft_available = 0 "
            "AND lineage_quality = 'legacy_unlinked') OR "
            "(parent_draft_id IS NOT NULL AND length(trim(parent_draft_id)) > 0 "
            "AND parent_draft_available = 1 "
            "AND lineage_quality IN ('exact','conservative'))",
            name="ck_scene_draft_block_set_lineage_state",
        ),
        sa.CheckConstraint(
            "block_count >= 0",
            name="ck_scene_draft_block_set_count",
        ),
    )
    op.create_index(
        "ix_scene_draft_block_sets_scene",
        "scene_draft_block_sets",
        ["story_scene_id", "scene_draft_id"],
    )

    op.create_table(
        "story_block_revisions",
        sa.Column("block_revision_id", sa.Text(), primary_key=True),
        sa.Column("logical_block_id", sa.Text(), nullable=False),
        sa.Column("scene_draft_id", sa.Text(), nullable=False),
        sa.Column("project_id", sa.Text(), nullable=False),
        sa.Column("story_scene_id", sa.Text(), nullable=False),
        sa.Column("parent_block_revision_id", sa.Text(), nullable=True),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("block_type", sa.Text(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("separator_after", sa.Text(), nullable=False, server_default=""),
        sa.Column("text_sha256", sa.Text(), nullable=False),
        sa.Column("source_slice_sha256", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["logical_block_id", "project_id", "story_scene_id"],
            [
                "story_block_anchors.logical_block_id",
                "story_block_anchors.project_id",
                "story_block_anchors.story_scene_id",
            ],
            name="fk_story_block_revision_anchor_ownership",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["scene_draft_id", "project_id", "story_scene_id"],
            [
                "scene_draft_block_sets.scene_draft_id",
                "scene_draft_block_sets.project_id",
                "scene_draft_block_sets.story_scene_id",
            ],
            name="fk_story_block_revision_set_ownership",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["parent_block_revision_id", "project_id", "story_scene_id"],
            [
                "story_block_revisions.block_revision_id",
                "story_block_revisions.project_id",
                "story_block_revisions.story_scene_id",
            ],
            name="fk_story_block_revision_parent_ownership",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "block_revision_id",
            "project_id",
            "story_scene_id",
            name="uq_story_block_revision_ownership",
        ),
        sa.UniqueConstraint(
            "scene_draft_id", "ordinal", name="uq_story_block_revision_draft_ordinal"
        ),
        sa.UniqueConstraint(
            "scene_draft_id",
            "logical_block_id",
            name="uq_story_block_revision_draft_logical",
        ),
        sa.CheckConstraint("ordinal >= 0", name="ck_story_block_revision_ordinal"),
        sa.CheckConstraint(
            "block_type = 'paragraph'", name="ck_story_block_revision_type"
        ),
        sa.CheckConstraint(
            _HASH_CHECK.format("text_sha256"),
            name="ck_story_block_revision_text_sha256",
        ),
        sa.CheckConstraint(
            _HASH_CHECK.format("source_slice_sha256"),
            name="ck_story_block_revision_slice_sha256",
        ),
    )
    op.create_index(
        "ix_story_block_revisions_draft",
        "story_block_revisions",
        ["scene_draft_id", "ordinal"],
    )
    op.create_index(
        "ix_story_block_revisions_logical",
        "story_block_revisions",
        ["logical_block_id", "scene_draft_id"],
    )

    _backfill(bind, backfill_plan)
    for table in (
        "story_block_anchors",
        "scene_draft_block_sets",
        "story_block_revisions",
    ):
        violations = bind.execute(
            sa.text(f"PRAGMA foreign_key_check({table})")
        ).all()
        if violations:
            raise RuntimeError(
                f"0012 backfill produced foreign-key violations in {table}: "
                f"{violations!r}"
            )
    _create_triggers()


def downgrade() -> None:
    for trigger in (
        "story_scenes_block_pointer_guard_update",
        "story_scenes_block_pointer_guard_insert",
        "scene_drafts_block_source_immutable_update",
        "story_block_revisions_write_once_delete",
        "story_block_revisions_write_once_update",
        "scene_draft_block_sets_write_once_delete",
        "scene_draft_block_sets_write_once_update",
        "story_block_anchors_write_once_delete",
        "story_block_anchors_write_once_update",
        "story_block_revisions_parent_guard_insert",
        "story_block_revisions_manifest_guard_insert",
        "scene_draft_block_sets_source_guard_insert",
    ):
        op.execute(f"DROP TRIGGER IF EXISTS {trigger}")
    op.drop_index(
        "ix_story_block_revisions_logical", table_name="story_block_revisions"
    )
    op.drop_index(
        "ix_story_block_revisions_draft", table_name="story_block_revisions"
    )
    op.drop_table("story_block_revisions")
    op.drop_index(
        "ix_scene_draft_block_sets_scene", table_name="scene_draft_block_sets"
    )
    op.drop_table("scene_draft_block_sets")
    op.drop_index("ix_story_block_anchors_scene", table_name="story_block_anchors")
    op.drop_table("story_block_anchors")
