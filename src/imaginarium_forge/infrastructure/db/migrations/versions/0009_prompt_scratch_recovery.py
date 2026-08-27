"""Prompt Scratch autosave, recovery journal and editor state.

Revision ID: 0009_prompt_scratch_recovery
Revises: 0008_prompt_scratch_drafts
Create Date: 2026-08-25
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0009_prompt_scratch_recovery"
down_revision = "0008_prompt_scratch_drafts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "prompt_scratch_drafts",
        sa.Column(
            "editor_kind",
            sa.Text(),
            sa.CheckConstraint(
                "editor_kind IN ('character','background','both')",
                name="ck_prompt_scratch_drafts_editor_kind",
            ),
            nullable=False,
            server_default="character",
        ),
    )
    op.add_column(
        "prompt_scratch_drafts",
        sa.Column(
            "editor_gender",
            sa.Text(),
            sa.CheckConstraint(
                "editor_gender IN ('female','male')",
                name="ck_prompt_scratch_drafts_editor_gender",
            ),
            nullable=False,
            server_default="female",
        ),
    )
    op.execute(
        sa.text(
            "UPDATE prompt_scratch_drafts SET editor_kind = CASE "
            "WHEN length(trim(character_image_prompt_en)) > 0 "
            "AND length(trim(background_image_prompt_en)) > 0 THEN 'both' "
            "WHEN length(trim(background_image_prompt_en)) > 0 "
            "AND length(trim(character_image_prompt_en)) = 0 THEN 'background' "
            "ELSE 'character' END, editor_gender = CASE "
            "WHEN lower(trim(CASE "
            "WHEN instr(character_image_prompt_en, ',') > 0 THEN "
            "substr(character_image_prompt_en, 1, "
            "instr(character_image_prompt_en, ',') - 1) "
            "ELSE character_image_prompt_en END)) = 'adult man' THEN 'male' "
            "ELSE 'female' END"
        )
    )

    op.create_table(
        "prompt_scratch_recovery_journals",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("intent", sa.Text(), nullable=False),
        sa.Column("existing_draft_id", sa.Text(), nullable=True),
        sa.Column("reserved_draft_id", sa.Text(), nullable=True),
        sa.Column("base_updated_at", sa.Text(), nullable=True),
        sa.Column("raw_project_id", sa.Text(), nullable=True),
        sa.Column("raw_link_project", sa.Integer(), nullable=True),
        sa.Column("raw_editor_kind", sa.Text(), nullable=False),
        sa.Column("raw_editor_gender", sa.Text(), nullable=False),
        sa.Column("raw_title", sa.Text(), nullable=True),
        sa.Column("raw_character_name", sa.Text(), nullable=True),
        sa.Column("raw_character_image_prompt_en", sa.Text(), nullable=True),
        sa.Column("raw_background_image_prompt_en", sa.Text(), nullable=True),
        sa.Column("raw_notes", sa.Text(), nullable=True),
        sa.Column("payload_sha256", sa.Text(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("conflict_reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("resolution_kind", sa.Text(), nullable=True),
        sa.Column("committed_draft_id", sa.Text(), nullable=True),
        sa.Column("committed_updated_at", sa.Text(), nullable=True),
        sa.Column("resolved_from_sequence", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.Column("resolved_at", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["existing_draft_id"],
            ["prompt_scratch_drafts.id"],
            name="fk_prompt_scratch_recovery_existing_draft",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["committed_draft_id"],
            ["prompt_scratch_drafts.id"],
            name="fk_prompt_scratch_recovery_committed_draft",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "(intent = 'new' AND existing_draft_id IS NULL AND "
            "reserved_draft_id IS NOT NULL AND base_updated_at IS NULL) OR "
            "(intent = 'existing' AND existing_draft_id IS NOT NULL AND "
            "reserved_draft_id IS NULL AND base_updated_at IS NOT NULL)",
            name="ck_prompt_scratch_recovery_intent_target",
        ),
        sa.CheckConstraint("sequence >= 1", name="ck_prompt_scratch_recovery_sequence"),
        sa.CheckConstraint(
            "state IN ('pending','conflict','committed','discarded')",
            name="ck_prompt_scratch_recovery_state",
        ),
        sa.CheckConstraint(
            "conflict_reason IN ('','stale_revision','target_missing',"
            "'target_archived','target_id_taken')",
            name="ck_prompt_scratch_recovery_conflict_reason",
        ),
        sa.CheckConstraint(
            "(state = 'committed' AND resolution_kind IN ('target','save_as_new')) OR "
            "(state <> 'committed' AND resolution_kind IS NULL)",
            name="ck_prompt_scratch_recovery_resolution_kind",
        ),
        sa.CheckConstraint(
            "state <> 'committed' OR "
            "(resolution_kind = 'target' AND committed_draft_id = "
            "COALESCE(existing_draft_id,reserved_draft_id)) OR "
            "(resolution_kind = 'save_as_new' AND committed_draft_id <> "
            "COALESCE(existing_draft_id,reserved_draft_id))",
            name="ck_prompt_scratch_recovery_resolution_target",
        ),
        sa.CheckConstraint(
            "length(payload_sha256) = 64 AND payload_sha256 NOT GLOB '*[^0-9a-f]*'",
            name="ck_prompt_scratch_recovery_sha256",
        ),
        sa.CheckConstraint(
            "raw_editor_kind IN ('character','background','both')",
            name="ck_prompt_scratch_recovery_editor_kind",
        ),
        sa.CheckConstraint(
            "raw_editor_gender IN ('female','male')",
            name="ck_prompt_scratch_recovery_editor_gender",
        ),
        sa.CheckConstraint(
            "((state IN ('pending','conflict')) AND raw_link_project IN (0,1) "
            "AND raw_title IS NOT NULL AND raw_character_name IS NOT NULL "
            "AND raw_character_image_prompt_en IS NOT NULL "
            "AND raw_background_image_prompt_en IS NOT NULL AND raw_notes IS NOT NULL) OR "
            "((state IN ('committed','discarded')) AND raw_project_id IS NULL "
            "AND raw_link_project IS NULL AND raw_title IS NULL "
            "AND raw_character_name IS NULL AND raw_character_image_prompt_en IS NULL "
            "AND raw_background_image_prompt_en IS NULL AND raw_notes IS NULL)",
            name="ck_prompt_scratch_recovery_raw_lifecycle",
        ),
        sa.CheckConstraint(
            "(state IN ('committed','discarded')) OR "
            "(length(CAST(COALESCE(raw_project_id,'') AS BLOB)) + "
            "length(CAST(raw_title AS BLOB)) + "
            "length(CAST(raw_character_name AS BLOB)) + "
            "length(CAST(raw_character_image_prompt_en AS BLOB)) + "
            "length(CAST(raw_background_image_prompt_en AS BLOB)) + "
            "length(CAST(raw_notes AS BLOB)) <= 1048576)",
            name="ck_prompt_scratch_recovery_payload_size",
        ),
        sa.CheckConstraint(
            "(state = 'pending' AND conflict_reason = '' "
            "AND resolution_kind IS NULL AND committed_draft_id IS NULL "
            "AND committed_updated_at IS NULL "
            "AND resolved_from_sequence IS NULL AND resolved_at IS NULL) OR "
            "(state = 'conflict' AND conflict_reason <> '' "
            "AND resolution_kind IS NULL AND committed_draft_id IS NULL "
            "AND committed_updated_at IS NULL "
            "AND resolved_from_sequence >= 1 "
            "AND sequence = resolved_from_sequence + 1 AND resolved_at IS NOT NULL) OR "
            "(state = 'committed' AND conflict_reason = '' "
            "AND resolution_kind IS NOT NULL AND committed_draft_id IS NOT NULL "
            "AND committed_updated_at IS NOT NULL "
            "AND resolved_from_sequence >= 1 "
            "AND sequence = resolved_from_sequence + 1 AND resolved_at IS NOT NULL) OR "
            "(state = 'discarded' AND conflict_reason = '' "
            "AND resolution_kind IS NULL AND committed_draft_id IS NULL "
            "AND committed_updated_at IS NULL "
            "AND resolved_from_sequence >= 1 "
            "AND sequence = resolved_from_sequence + 1 AND resolved_at IS NOT NULL)",
            name="ck_prompt_scratch_recovery_resolution_lifecycle",
        ),
    )
    op.create_index(
        "ix_prompt_scratch_recovery_state_updated",
        "prompt_scratch_recovery_journals",
        ["state", "updated_at", "id"],
    )
    op.create_index(
        "ix_prompt_scratch_recovery_existing_state",
        "prompt_scratch_recovery_journals",
        ["existing_draft_id", "state"],
    )
    op.create_index(
        "uq_prompt_scratch_recovery_reserved_draft_id",
        "prompt_scratch_recovery_journals",
        ["reserved_draft_id"],
        unique=True,
        sqlite_where=sa.text("reserved_draft_id IS NOT NULL"),
    )


def downgrade() -> None:
    bind = op.get_bind()
    unresolved = int(
        bind.execute(
            sa.text(
                "SELECT count(*) FROM prompt_scratch_recovery_journals "
                "WHERE state IN ('pending','conflict')"
            )
        ).scalar_one()
    )
    if unresolved:
        raise RuntimeError(
            "0009 downgrade blocked: commit, export, or explicitly discard all "
            "pending/conflict Prompt Scratch recovery journals first"
        )

    op.drop_index(
        "uq_prompt_scratch_recovery_reserved_draft_id",
        table_name="prompt_scratch_recovery_journals",
    )
    op.drop_index(
        "ix_prompt_scratch_recovery_existing_state",
        table_name="prompt_scratch_recovery_journals",
    )
    op.drop_index(
        "ix_prompt_scratch_recovery_state_updated",
        table_name="prompt_scratch_recovery_journals",
    )
    op.drop_table("prompt_scratch_recovery_journals")
    op.drop_column("prompt_scratch_drafts", "editor_gender")
    op.drop_column("prompt_scratch_drafts", "editor_kind")
