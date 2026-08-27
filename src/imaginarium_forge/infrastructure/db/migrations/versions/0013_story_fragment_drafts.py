"""Project-optional standalone story fragment drafts.

Revision ID: 0013_story_fragment_drafts
Revises: 0012_stable_story_blocks
Create Date: 2026-08-26

This additive migration creates one mutable scratch-space table.  It does not
backfill or rewrite Story Studio Scenes, accepted pointers, stable story-block
projections, Canon, provider evidence, or other standalone drafts.  Downgrade
is allowed only while the table is empty; archived author text is still data
and therefore blocks destructive rollback.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0013_story_fragment_drafts"
down_revision = "0012_stable_story_blocks"
branch_labels = None
depends_on = None

_NONCANONICAL_TAGS_SQL = """
EXISTS (
    SELECT 1 FROM json_each(NEW.tags_json)
    WHERE type <> 'text'
       OR length(value) = 0
       OR length(value) > 200
       OR value <> trim(value)
       OR instr(value, '  ') > 0
       OR instr(value, char(9)) > 0
       OR instr(value, char(10)) > 0
       OR instr(value, char(11)) > 0
       OR instr(value, char(12)) > 0
       OR instr(value, char(13)) > 0
       OR instr(value, char(28)) > 0
       OR instr(value, char(29)) > 0
       OR instr(value, char(30)) > 0
       OR instr(value, char(31)) > 0
       OR instr(value, char(133)) > 0
       OR instr(value, char(160)) > 0
       OR instr(value, char(5760)) > 0
       OR instr(value, char(8192)) > 0
       OR instr(value, char(8193)) > 0
       OR instr(value, char(8194)) > 0
       OR instr(value, char(8195)) > 0
       OR instr(value, char(8196)) > 0
       OR instr(value, char(8197)) > 0
       OR instr(value, char(8198)) > 0
       OR instr(value, char(8199)) > 0
       OR instr(value, char(8200)) > 0
       OR instr(value, char(8201)) > 0
       OR instr(value, char(8202)) > 0
       OR instr(value, char(8232)) > 0
       OR instr(value, char(8233)) > 0
       OR instr(value, char(8239)) > 0
       OR instr(value, char(8287)) > 0
       OR instr(value, char(12288)) > 0
)
OR EXISTS (
    SELECT 1
    FROM json_each(NEW.tags_json) AS earlier
    JOIN json_each(NEW.tags_json) AS later
      ON CAST(earlier.key AS INTEGER) < CAST(later.key AS INTEGER)
     AND earlier.value = later.value
)
"""


def upgrade() -> None:
    op.create_table(
        "story_fragment_drafts",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("project_id", sa.Text(), nullable=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("fragment_kind", sa.Text(), nullable=False, server_default="narrative"),
        sa.Column("fragment_text", sa.Text(), nullable=False),
        sa.Column("context_notes", sa.Text(), nullable=False, server_default=""),
        sa.Column("tags_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("generation_mode", sa.Text(), nullable=False, server_default="manual"),
        sa.Column("generation_seed", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.Text(), nullable=False, server_default="active"),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_story_fragment_drafts_project",
            ondelete="SET NULL",
        ),
        sa.CheckConstraint(
            "length(trim(id)) BETWEEN 1 AND 200",
            name="ck_story_fragment_drafts_id",
        ),
        sa.CheckConstraint(
            "project_id IS NULL OR length(trim(project_id)) BETWEEN 1 AND 200",
            name="ck_story_fragment_drafts_project_id",
        ),
        sa.CheckConstraint(
            "length(trim(title)) BETWEEN 1 AND 200",
            name="ck_story_fragment_drafts_title",
        ),
        sa.CheckConstraint(
            "fragment_kind IN ('narrative','dialogue','opening')",
            name="ck_story_fragment_drafts_kind",
        ),
        sa.CheckConstraint(
            "length(fragment_text) <= 500000 AND "
            "length(trim(fragment_text, char(9) || char(10) || char(13) || ' ')) > 0",
            name="ck_story_fragment_drafts_text",
        ),
        sa.CheckConstraint(
            "length(context_notes) <= 4000",
            name="ck_story_fragment_drafts_context",
        ),
        sa.CheckConstraint(
            "json_valid(tags_json) AND json_type(tags_json) = 'array' AND "
            "tags_json = json(tags_json) AND json_array_length(tags_json) <= 24",
            name="ck_story_fragment_drafts_tags_json",
        ),
        sa.CheckConstraint(
            "generation_mode IN ('manual','fill_blanks','reroll_all')",
            name="ck_story_fragment_drafts_generation_mode",
        ),
        sa.CheckConstraint(
            "length(generation_seed) <= 200 AND ("
            "(generation_mode = 'manual' AND generation_seed = '') OR "
            "(generation_mode IN ('fill_blanks','reroll_all') AND "
            "length(trim(generation_seed)) > 0))",
            name="ck_story_fragment_drafts_generation_seed",
        ),
        sa.CheckConstraint(
            "status IN ('active','archived')",
            name="ck_story_fragment_drafts_status",
        ),
    )
    op.create_index(
        "ix_story_fragment_drafts_project_status_updated",
        "story_fragment_drafts",
        ["project_id", "status", "updated_at"],
    )
    op.execute(
        f"""
        CREATE TRIGGER story_fragment_drafts_tags_insert
        BEFORE INSERT ON story_fragment_drafts
        WHEN {_NONCANONICAL_TAGS_SQL}
        BEGIN
            SELECT RAISE(ABORT,
                'story_fragment_drafts tags must be canonical bounded text');
        END
        """
    )
    op.execute(
        f"""
        CREATE TRIGGER story_fragment_drafts_tags_update
        BEFORE UPDATE OF tags_json ON story_fragment_drafts
        WHEN {_NONCANONICAL_TAGS_SQL}
        BEGIN
            SELECT RAISE(ABORT,
                'story_fragment_drafts tags must be canonical bounded text');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER story_fragment_drafts_no_delete
        BEFORE DELETE ON story_fragment_drafts
        BEGIN
            SELECT RAISE(ABORT,
                'story_fragment_drafts are archived, never deleted');
        END
        """
    )


def downgrade() -> None:
    bind = op.get_bind()
    row_count = int(
        bind.execute(sa.text("SELECT count(*) FROM story_fragment_drafts")).scalar_one()
    )
    if row_count:
        raise RuntimeError(
            "0013 downgrade blocked: standalone story fragment data exists. "
            "Archive does not make author text disposable. Export it and restore "
            "a verified pre-0013 backup only with explicit author approval, or use "
            "a reviewed fix-forward migration."
        )

    op.execute("DROP TRIGGER IF EXISTS story_fragment_drafts_no_delete")
    op.execute("DROP TRIGGER IF EXISTS story_fragment_drafts_tags_update")
    op.execute("DROP TRIGGER IF EXISTS story_fragment_drafts_tags_insert")
    op.drop_index(
        "ix_story_fragment_drafts_project_status_updated",
        table_name="story_fragment_drafts",
    )
    op.drop_table("story_fragment_drafts")
