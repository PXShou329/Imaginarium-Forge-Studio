"""Project-optional prompt scratch drafts.

Revision ID: 0008_prompt_scratch_drafts
Revises: 0007_character_biography_drafts
Create Date: 2026-08-23

The migration is additive.  It does not rewrite existing project, character,
biography, prompt-project, or story content.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0008_prompt_scratch_drafts"
down_revision = "0007_character_biography_drafts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "prompt_scratch_drafts",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("project_id", sa.Text(), nullable=True),
        sa.Column("title", sa.Text(), nullable=False, server_default=""),
        sa.Column("character_name", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "character_image_prompt_en", sa.Text(), nullable=False, server_default=""
        ),
        sa.Column(
            "background_image_prompt_en", sa.Text(), nullable=False, server_default=""
        ),
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.Text(), nullable=False, server_default="active"),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_prompt_scratch_drafts_project",
            ondelete="SET NULL",
        ),
        sa.CheckConstraint(
            "length(trim(title)) > 0 OR length(trim(character_name)) > 0 OR "
            "length(trim(character_image_prompt_en)) > 0 OR "
            "length(trim(background_image_prompt_en)) > 0 OR "
            "length(trim(notes)) > 0",
            name="ck_prompt_scratch_drafts_has_content",
        ),
        sa.CheckConstraint(
            "status IN ('active','archived')",
            name="ck_prompt_scratch_drafts_status",
        ),
        sa.CheckConstraint(
            "character_image_prompt_en NOT GLOB '*[^ -~]*'",
            name="ck_prompt_scratch_drafts_character_prompt_ascii",
        ),
        sa.CheckConstraint(
            "background_image_prompt_en NOT GLOB '*[^ -~]*'",
            name="ck_prompt_scratch_drafts_background_prompt_ascii",
        ),
    )
    op.create_index(
        "ix_prompt_scratch_drafts_project_status_updated",
        "prompt_scratch_drafts",
        ["project_id", "status", "updated_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_prompt_scratch_drafts_project_status_updated",
        table_name="prompt_scratch_drafts",
    )
    op.drop_table("prompt_scratch_drafts")
