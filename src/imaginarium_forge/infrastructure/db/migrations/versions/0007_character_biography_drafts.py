"""Standalone character-biography drafts.

Revision ID: 0007_character_biography_drafts
Revises: 0006_story_memory
Create Date: 2026-08-22

This migration only adds a nullable-project draft table and its index.  No
existing project, character, prompt or story table is rebuilt or rewritten.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0007_character_biography_drafts"
down_revision = "0006_story_memory"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "character_biography_drafts",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("project_id", sa.Text(), nullable=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("character_name", sa.Text(), nullable=False),
        sa.Column("gender", sa.Text(), nullable=False),
        sa.Column("character_details", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "character_image_prompt_en",
            sa.Text(),
            nullable=False,
            server_default="",
        ),
        sa.Column("personal_story", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "background_image_prompt_en",
            sa.Text(),
            nullable=False,
            server_default="",
        ),
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.Text(), nullable=False, server_default="active"),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_character_biography_drafts_project",
            ondelete="SET NULL",
        ),
        sa.CheckConstraint(
            "length(trim(title)) > 0 AND length(trim(character_name)) > 0",
            name="ck_character_biography_drafts_required_labels",
        ),
        sa.CheckConstraint(
            "gender IN ('female','male')",
            name="ck_character_biography_drafts_gender",
        ),
        sa.CheckConstraint(
            "status IN ('active','archived')",
            name="ck_character_biography_drafts_status",
        ),
        sa.CheckConstraint(
            "character_image_prompt_en NOT GLOB '*[^ -~]*'",
            name="ck_character_biography_drafts_character_prompt_ascii",
        ),
        sa.CheckConstraint(
            "background_image_prompt_en NOT GLOB '*[^ -~]*'",
            name="ck_character_biography_drafts_background_prompt_ascii",
        ),
    )
    op.create_index(
        "ix_character_biography_drafts_project_status_updated",
        "character_biography_drafts",
        ["project_id", "status", "updated_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_character_biography_drafts_project_status_updated",
        table_name="character_biography_drafts",
    )
    op.drop_table("character_biography_drafts")
