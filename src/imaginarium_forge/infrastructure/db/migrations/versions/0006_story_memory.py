"""Author-reviewed durable story memory.

Revision ID: 0006_story_memory
Revises: 0005_phase4_video_prompt_bundles
Create Date: 2026-08-22

Only additive tables are introduced.  Existing Story Studio tables and their
immutability triggers are not rebuilt, which keeps this SQLite migration safe
for local databases that already contain accepted prose.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0006_story_memory"
down_revision = "0005_phase4_video_prompt_bundles"
branch_labels = None
depends_on = None


def _sha256(column: str, name: str) -> sa.CheckConstraint:
    return sa.CheckConstraint(
        f"length({column}) = 64 AND {column} NOT GLOB '*[^0-9a-f]*'",
        name=name,
    )


def _write_once(table: str) -> None:
    op.execute(
        f"""
        CREATE TRIGGER {table}_write_once_update
        BEFORE UPDATE ON {table}
        BEGIN
            SELECT RAISE(ABORT, '{table} rows are immutable');
        END
        """
    )
    op.execute(
        f"""
        CREATE TRIGGER {table}_write_once_delete
        BEFORE DELETE ON {table}
        BEGIN
            SELECT RAISE(ABORT, '{table} rows are historical evidence');
        END
        """
    )


def upgrade() -> None:
    op.create_table(
        "story_memory_proposals",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("project_id", sa.Text(), nullable=False),
        sa.Column("story_outline_id", sa.Text(), nullable=False),
        sa.Column("story_scene_id", sa.Text(), nullable=False),
        sa.Column("scene_draft_id", sa.Text(), nullable=False),
        sa.Column("base_memory_fingerprint", sa.Text(), nullable=False),
        sa.Column("proposal_json", sa.Text(), nullable=False),
        sa.Column("proposal_sha256", sa.Text(), nullable=False),
        sa.Column("origin", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False, server_default=""),
        sa.Column("model", sa.Text(), nullable=False, server_default=""),
        sa.Column("contract_version", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("decision_note", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("finalized_at", sa.Text(), nullable=False, server_default=""),
        sa.ForeignKeyConstraint(
            ["story_outline_id", "project_id"],
            ["story_outlines.id", "story_outlines.project_id"],
            name="fk_memory_proposal_outline_ownership",
        ),
        sa.ForeignKeyConstraint(
            ["story_scene_id", "project_id"],
            ["story_scenes.id", "story_scenes.project_id"],
            name="fk_memory_proposal_scene_ownership",
        ),
        sa.ForeignKeyConstraint(
            ["scene_draft_id", "story_scene_id"],
            ["scene_drafts.id", "scene_drafts.story_scene_id"],
            name="fk_memory_proposal_draft_ownership",
        ),
        sa.UniqueConstraint(
            "id",
            "project_id",
            "story_outline_id",
            "story_scene_id",
            "scene_draft_id",
            name="uq_memory_proposal_full_ownership",
        ),
        sa.CheckConstraint(
            "json_valid(proposal_json) AND json_type(proposal_json) = 'object' "
            "AND json_extract(proposal_json, '$.schema_version') = "
            "'story-memory-v1' "
            "AND json_type(proposal_json, '$.changes') = 'array'",
            name="ck_memory_proposal_json",
        ),
        sa.CheckConstraint(
            "contract_version = 'story-memory-proposal-v1'",
            name="ck_memory_proposal_contract",
        ),
        sa.CheckConstraint(
            "origin IN ('manual','scene_card','llm')",
            name="ck_memory_proposal_origin",
        ),
        sa.CheckConstraint(
            "status IN ('pending','accepted','rejected')",
            name="ck_memory_proposal_status",
        ),
        sa.CheckConstraint(
            "(status = 'pending' AND finalized_at = '' AND decision_note = '') OR "
            "(status IN ('accepted','rejected') AND finalized_at <> '')",
            name="ck_memory_proposal_lifecycle",
        ),
        _sha256(
            "base_memory_fingerprint",
            "ck_memory_proposal_base_fingerprint",
        ),
        _sha256("proposal_sha256", "ck_memory_proposal_sha256"),
    )
    op.create_index(
        "ix_memory_proposals_scene",
        "story_memory_proposals",
        ["story_scene_id", "status"],
    )
    op.create_index(
        "uq_memory_proposal_one_pending_payload",
        "story_memory_proposals",
        ["scene_draft_id", "proposal_sha256", "base_memory_fingerprint"],
        unique=True,
        sqlite_where=sa.text("status = 'pending'"),
    )
    op.create_index(
        "uq_memory_proposal_one_accepted_per_draft",
        "story_memory_proposals",
        ["scene_draft_id"],
        unique=True,
        sqlite_where=sa.text("status = 'accepted'"),
    )

    op.create_table(
        "story_memory_entries",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("project_id", sa.Text(), nullable=False),
        sa.Column("story_outline_id", sa.Text(), nullable=False),
        sa.Column("story_scene_id", sa.Text(), nullable=False),
        sa.Column("scene_draft_id", sa.Text(), nullable=False),
        sa.Column("proposal_id", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("subject_id", sa.Text(), nullable=False),
        sa.Column("attribute", sa.Text(), nullable=False),
        sa.Column("value", sa.Text(), nullable=False, server_default=""),
        sa.Column("operation", sa.Text(), nullable=False),
        sa.Column("supersedes_entry_id", sa.Text(), nullable=True),
        sa.Column("source_excerpt", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            [
                "proposal_id",
                "project_id",
                "story_outline_id",
                "story_scene_id",
                "scene_draft_id",
            ],
            [
                "story_memory_proposals.id",
                "story_memory_proposals.project_id",
                "story_memory_proposals.story_outline_id",
                "story_memory_proposals.story_scene_id",
                "story_memory_proposals.scene_draft_id",
            ],
            name="fk_memory_entry_proposal_ownership",
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_entry_id", "project_id", "story_outline_id"],
            [
                "story_memory_entries.id",
                "story_memory_entries.project_id",
                "story_memory_entries.story_outline_id",
            ],
            name="fk_memory_entry_replacement_ownership",
        ),
        sa.UniqueConstraint(
            "id",
            "project_id",
            "story_outline_id",
            name="uq_memory_entry_outline_ownership",
        ),
        sa.UniqueConstraint(
            "supersedes_entry_id",
            name="uq_memory_entry_single_replacement",
        ),
        sa.CheckConstraint(
            "kind IN ('fact','timeline','foreshadowing','character_state')",
            name="ck_memory_entry_kind",
        ),
        sa.CheckConstraint(
            "operation IN ('assert','supersede','retract')",
            name="ck_memory_entry_operation",
        ),
        sa.CheckConstraint(
            "(operation = 'assert' AND supersedes_entry_id IS NULL) OR "
            "(operation IN ('supersede','retract') "
            "AND supersedes_entry_id IS NOT NULL)",
            name="ck_memory_entry_replacement_contract",
        ),
        sa.CheckConstraint(
            "subject_id <> '' AND attribute <> '' AND created_at <> '' AND "
            "((operation IN ('assert','supersede') AND value <> '') OR "
            "(operation = 'retract' AND value = ''))",
            name="ck_memory_entry_content",
        ),
    )
    op.create_index(
        "ix_memory_entries_outline_identity",
        "story_memory_entries",
        ["story_outline_id", "subject_id", "attribute"],
    )
    op.create_index(
        "ix_memory_entries_proposal",
        "story_memory_entries",
        ["proposal_id"],
    )

    # Proposal payload/ownership is immutable.  A pending proposal may only
    # move once to accepted/rejected; accepted entries are written in the same
    # application transaction as that finalization.
    op.execute(
        """
        CREATE TRIGGER story_memory_proposals_validate_insert
        BEFORE INSERT ON story_memory_proposals
        WHEN NOT EXISTS (
            SELECT 1
            FROM story_scenes AS scene
            JOIN story_chapters AS chapter
              ON chapter.id = scene.story_chapter_id
             AND chapter.project_id = scene.project_id
            JOIN scene_drafts AS draft
              ON draft.id = NEW.scene_draft_id
             AND draft.story_scene_id = scene.id
             AND draft.project_id = scene.project_id
            WHERE scene.id = NEW.story_scene_id
              AND scene.project_id = NEW.project_id
              AND chapter.story_outline_id = NEW.story_outline_id
              AND scene.accepted_draft_id = NEW.scene_draft_id
              AND draft.draft_status = 'complete'
        )
        BEGIN
            SELECT RAISE(ABORT,
                'story memory proposal requires the current accepted draft');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER story_memory_proposals_payload_immutable
        BEFORE UPDATE ON story_memory_proposals
        WHEN OLD.id <> NEW.id
          OR OLD.project_id <> NEW.project_id
          OR OLD.story_outline_id <> NEW.story_outline_id
          OR OLD.story_scene_id <> NEW.story_scene_id
          OR OLD.scene_draft_id <> NEW.scene_draft_id
          OR OLD.base_memory_fingerprint <> NEW.base_memory_fingerprint
          OR OLD.proposal_json <> NEW.proposal_json
          OR OLD.proposal_sha256 <> NEW.proposal_sha256
          OR OLD.origin <> NEW.origin
          OR OLD.provider <> NEW.provider
          OR OLD.model <> NEW.model
          OR OLD.contract_version <> NEW.contract_version
          OR OLD.created_at <> NEW.created_at
        BEGIN
            SELECT RAISE(ABORT, 'story memory proposal payload is immutable');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER story_memory_proposals_finalize_once
        BEFORE UPDATE ON story_memory_proposals
        WHEN OLD.status <> 'pending' OR NEW.status = 'pending'
        BEGIN
            SELECT RAISE(ABORT, 'story memory proposal may be finalized once');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER story_memory_proposals_accept_current_draft
        BEFORE UPDATE ON story_memory_proposals
        WHEN NEW.status = 'accepted' AND NOT EXISTS (
            SELECT 1
            FROM story_scenes AS scene
            JOIN scene_drafts AS draft
              ON draft.id = NEW.scene_draft_id
             AND draft.story_scene_id = scene.id
             AND draft.project_id = scene.project_id
            WHERE scene.id = NEW.story_scene_id
              AND scene.project_id = NEW.project_id
              AND scene.accepted_draft_id = NEW.scene_draft_id
              AND draft.draft_status = 'complete'
        )
        BEGIN
            SELECT RAISE(ABORT,
                'story memory proposal source draft is no longer accepted');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER story_memory_proposals_no_delete
        BEFORE DELETE ON story_memory_proposals
        BEGIN
            SELECT RAISE(ABORT, 'story memory proposals are historical evidence');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER story_memory_entries_require_accepted_proposal
        BEFORE INSERT ON story_memory_entries
        WHEN NOT EXISTS (
            SELECT 1
            FROM story_memory_proposals AS proposal
            WHERE proposal.id = NEW.proposal_id
              AND proposal.project_id = NEW.project_id
              AND proposal.story_outline_id = NEW.story_outline_id
              AND proposal.story_scene_id = NEW.story_scene_id
              AND proposal.scene_draft_id = NEW.scene_draft_id
              AND proposal.status = 'accepted'
        )
        BEGIN
            SELECT RAISE(ABORT,
                'story memory entries require an accepted proposal');
        END
        """
    )
    _write_once("story_memory_entries")


def downgrade() -> None:
    for trigger in (
        "story_memory_entries_write_once_delete",
        "story_memory_entries_write_once_update",
        "story_memory_entries_require_accepted_proposal",
        "story_memory_proposals_no_delete",
        "story_memory_proposals_accept_current_draft",
        "story_memory_proposals_finalize_once",
        "story_memory_proposals_payload_immutable",
        "story_memory_proposals_validate_insert",
    ):
        op.execute(f"DROP TRIGGER IF EXISTS {trigger}")
    op.drop_index("ix_memory_entries_proposal", table_name="story_memory_entries")
    op.drop_index(
        "ix_memory_entries_outline_identity", table_name="story_memory_entries"
    )
    op.drop_table("story_memory_entries")
    op.drop_index(
        "uq_memory_proposal_one_accepted_per_draft",
        table_name="story_memory_proposals",
    )
    op.drop_index(
        "uq_memory_proposal_one_pending_payload",
        table_name="story_memory_proposals",
    )
    op.drop_index("ix_memory_proposals_scene", table_name="story_memory_proposals")
    op.drop_table("story_memory_proposals")
