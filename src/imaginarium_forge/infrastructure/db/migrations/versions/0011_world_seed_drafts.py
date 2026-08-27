"""Project-optional standalone world seed drafts.

Revision ID: 0011_world_seed_drafts
Revises: 0010_screenplay_adaptations
Create Date: 2026-08-26

This migration is additive: it creates one independent authoring table and
does not rebuild, backfill, or rewrite any existing story, screenplay, Canon,
prompt, adult-review, or provider data.  Downgrade is permitted only while the
new table is empty.  Once any author content exists, restore a verified
pre-0011 backup or deploy a reviewed fix-forward migration instead of silently
dropping it.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0011_world_seed_drafts"
down_revision = "0010_screenplay_adaptations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "world_seed_drafts",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("project_id", sa.Text(), nullable=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("setting", sa.Text(), nullable=False, server_default=""),
        sa.Column("time_period", sa.Text(), nullable=False, server_default=""),
        sa.Column("world_rules_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("locations_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("social_context", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "technology_or_magic", sa.Text(), nullable=False, server_default=""
        ),
        sa.Column("central_conflict", sa.Text(), nullable=False, server_default=""),
        sa.Column("themes_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column(
            "generation_mode", sa.Text(), nullable=False, server_default="manual"
        ),
        sa.Column("generation_seed", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.Text(), nullable=False, server_default="active"),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_world_seed_drafts_project",
            ondelete="SET NULL",
        ),
        sa.CheckConstraint(
            "length(trim(title)) > 0",
            name="ck_world_seed_drafts_title",
        ),
        sa.CheckConstraint(
            "length(trim(setting)) > 0 OR length(trim(time_period)) > 0 OR "
            "json_array_length(world_rules_json) > 0 OR "
            "json_array_length(locations_json) > 0 OR "
            "length(trim(social_context)) > 0 OR "
            "length(trim(technology_or_magic)) > 0 OR "
            "length(trim(central_conflict)) > 0 OR "
            "json_array_length(themes_json) > 0",
            name="ck_world_seed_drafts_has_content",
        ),
        sa.CheckConstraint(
            "json_valid(world_rules_json) AND "
            "json_type(world_rules_json) = 'array' AND "
            "world_rules_json = json(world_rules_json) AND "
            "json_array_length(world_rules_json) <= 24",
            name="ck_world_seed_drafts_rules_json",
        ),
        sa.CheckConstraint(
            "json_valid(locations_json) AND "
            "json_type(locations_json) = 'array' AND "
            "locations_json = json(locations_json) AND "
            "json_array_length(locations_json) <= 24",
            name="ck_world_seed_drafts_locations_json",
        ),
        sa.CheckConstraint(
            "json_valid(themes_json) AND "
            "json_type(themes_json) = 'array' AND "
            "themes_json = json(themes_json) AND "
            "json_array_length(themes_json) <= 24",
            name="ck_world_seed_drafts_themes_json",
        ),
        sa.CheckConstraint(
            "generation_mode IN ('manual','fill_blanks','reroll_all')",
            name="ck_world_seed_drafts_generation_mode",
        ),
        sa.CheckConstraint(
            "(generation_mode = 'manual' AND generation_seed = '') OR "
            "(generation_mode IN ('fill_blanks','reroll_all') AND "
            "length(trim(generation_seed)) > 0)",
            name="ck_world_seed_drafts_generation_seed",
        ),
        sa.CheckConstraint(
            "status IN ('active','archived')",
            name="ck_world_seed_drafts_status",
        ),
    )
    op.create_index(
        "ix_world_seed_drafts_project_status_updated",
        "world_seed_drafts",
        ["project_id", "status", "updated_at"],
    )
    op.execute(
        """
        CREATE TRIGGER world_seed_drafts_text_arrays_insert
        BEFORE INSERT ON world_seed_drafts
        WHEN EXISTS (
            SELECT 1 FROM json_each(NEW.world_rules_json)
            WHERE type <> 'text' OR length(value) > 1000
        ) OR EXISTS (
            SELECT 1 FROM json_each(NEW.locations_json)
            WHERE type <> 'text' OR length(value) > 200
        ) OR EXISTS (
            SELECT 1 FROM json_each(NEW.themes_json)
            WHERE type <> 'text' OR length(value) > 200
        )
        BEGIN
            SELECT RAISE(ABORT,
                'world_seed_drafts JSON arrays require bounded text values');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER world_seed_drafts_text_arrays_update
        BEFORE UPDATE OF world_rules_json, locations_json, themes_json
        ON world_seed_drafts
        WHEN EXISTS (
            SELECT 1 FROM json_each(NEW.world_rules_json)
            WHERE type <> 'text' OR length(value) > 1000
        ) OR EXISTS (
            SELECT 1 FROM json_each(NEW.locations_json)
            WHERE type <> 'text' OR length(value) > 200
        ) OR EXISTS (
            SELECT 1 FROM json_each(NEW.themes_json)
            WHERE type <> 'text' OR length(value) > 200
        )
        BEGIN
            SELECT RAISE(ABORT,
                'world_seed_drafts JSON arrays require bounded text values');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER world_seed_drafts_no_delete
        BEFORE DELETE ON world_seed_drafts
        BEGIN
            SELECT RAISE(ABORT, 'world_seed_drafts are archived, never deleted');
        END
        """
    )


def downgrade() -> None:
    bind = op.get_bind()
    row_count = int(
        bind.execute(sa.text("SELECT count(*) FROM world_seed_drafts")).scalar_one()
    )
    if row_count:
        raise RuntimeError(
            "0011 downgrade blocked: standalone world seed draft data exists. "
            "Archive does not make author content disposable. Export it and restore "
            "a verified pre-0011 backup only with explicit author approval, or use "
            "a reviewed fix-forward migration."
        )

    op.execute("DROP TRIGGER IF EXISTS world_seed_drafts_no_delete")
    op.execute("DROP TRIGGER IF EXISTS world_seed_drafts_text_arrays_update")
    op.execute("DROP TRIGGER IF EXISTS world_seed_drafts_text_arrays_insert")
    op.drop_index(
        "ix_world_seed_drafts_project_status_updated",
        table_name="world_seed_drafts",
    )
    op.drop_table("world_seed_drafts")
