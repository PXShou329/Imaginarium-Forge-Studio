"""Phase 2 Visual Prompt Studio tables.

Revision ID: 0002_phase2_prompt_studio
Revises: 0001_phase1
Create Date: 2026-07-15

Six tables (spec §35): prompt_projects, prompt_project_versions, prompt_variants,
prompt_profile_snapshots, checkpoint_registry, prompt_experiment_logs.

Ownership follows the Phase 1 pattern: composite FKs guarantee that referenced
versions belong to the referencing parent. Accepted prompt-project versions are
immutable — enforced at the DB level with triggers (edits create a new version).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002_phase2_prompt_studio"
down_revision = "0001_phase1"
branch_labels = None
depends_on = None

_STATUSES = "('draft','active','archived')"
_CKPT_STATUSES = (
    "('unidentified','identified_but_untested','testing',"
    "'recommended_profile','personal_favorite','unsuitable')"
)
_META_SOURCES = (
    "('manual','safetensors_metadata','sidecar_json','filename_inference','unknown')"
)


def upgrade() -> None:
    # ---------------------------------------------------------- profile snapshots
    op.create_table(
        "prompt_profile_snapshots",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("dialect_id", sa.Text(), nullable=False),
        sa.Column("dialect_version", sa.Text(), nullable=False),
        sa.Column("checkpoint_profile_id", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "checkpoint_profile_version", sa.Text(), nullable=False, server_default=""
        ),
        sa.Column("preset_id", sa.Text(), nullable=False, server_default=""),
        sa.Column("preset_version", sa.Text(), nullable=False, server_default=""),
        sa.Column("resolved_json", sa.Text(), nullable=False),
        sa.Column("sha256", sa.Text(), nullable=False),
        sa.Column("compiler_version", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.UniqueConstraint("sha256", name="uq_profile_snapshot_sha256"),
        # A2-11: syntactic JSON integrity (Pydantic remains the schema validator)
        sa.CheckConstraint(
            "json_valid(resolved_json)", name="ck_json_snapshot_resolved"
        ),
    )

    # ---------------------------------------------------------------- projects
    # A2-03: composite-FK parents — SQLite accepts any UNIQUE index as an FK
    # target, so 0001 tables gain (id, project_id) uniqueness here in 0002
    # without rewriting the tagged Phase 1 migration.
    op.create_index(
        "uq_characters_id_project", "characters", ["id", "project_id"], unique=True
    )
    op.create_index(
        "uq_style_profiles_id_project",
        "style_profiles",
        ["id", "project_id"],
        unique=True,
    )

    op.create_table(
        "prompt_projects",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("project_id", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("source_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("character_id", sa.Text(), nullable=True),
        sa.Column("character_version_id", sa.Text(), nullable=True),
        sa.Column("style_profile_id", sa.Text(), nullable=True),
        sa.Column("style_version_id", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="draft"),
        sa.Column("current_version_id", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], name="fk_pp_project"),
        sa.ForeignKeyConstraint(
            ["character_id"], ["characters.id"], name="fk_pp_character"
        ),
        # A2-03: the selected character must belong to THIS project (NULL
        # character_id → SQLite skips the composite FK, i.e. "no selection")
        sa.ForeignKeyConstraint(
            ["character_id", "project_id"],
            ["characters.id", "characters.project_id"],
            name="fk_pp_character_project_ownership",
        ),
        # ownership: the selected character version must belong to the character
        sa.ForeignKeyConstraint(
            ["character_version_id", "character_id"],
            ["character_versions.id", "character_versions.character_id"],
            name="fk_pp_character_version_ownership",
        ),
        sa.ForeignKeyConstraint(
            ["style_profile_id"], ["style_profiles.id"], name="fk_pp_style"
        ),
        sa.ForeignKeyConstraint(
            ["style_profile_id", "project_id"],
            ["style_profiles.id", "style_profiles.project_id"],
            name="fk_pp_style_project_ownership",
        ),
        sa.ForeignKeyConstraint(
            ["style_version_id", "style_profile_id"],
            ["style_profile_versions.id", "style_profile_versions.style_profile_id"],
            name="fk_pp_style_version_ownership",
        ),
        sa.CheckConstraint(f"status IN {_STATUSES}", name="ck_pp_status"),
        # ownership: the current version must belong to this prompt project
        # (same circular-FK pattern as Phase 1 characters↔character_versions;
        # SQLite resolves FK targets at DML time, so declaration order is fine)
        sa.ForeignKeyConstraint(
            ["current_version_id", "id"],
            ["prompt_project_versions.id", "prompt_project_versions.prompt_project_id"],
            name="fk_pp_current_version_ownership",
        ),
    )
    op.create_index("ix_pp_project", "prompt_projects", ["project_id"])

    # ------------------------------------------------------------------ versions
    op.create_table(
        "prompt_project_versions",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("prompt_project_id", sa.Text(), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("ast_json", sa.Text(), nullable=False),
        sa.Column("change_note", sa.Text(), nullable=False, server_default=""),
        # Gate A A-05: immutable per-version selection snapshot
        sa.Column(
            "selection_snapshot_json", sa.Text(), nullable=False, server_default="{}"
        ),
        sa.Column(
            "selection_snapshot_sha256", sa.Text(), nullable=False, server_default=""
        ),
        # Gate A A-04 §7.4: acceptance requires a variant with this fingerprint
        sa.Column(
            "input_fingerprint", sa.Text(), nullable=False, server_default=""
        ),
        sa.Column("accepted", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["prompt_project_id"], ["prompt_projects.id"], name="fk_ppv_project"
        ),
        sa.CheckConstraint("accepted IN (0,1)", name="ck_ppv_accepted_bool"),
        sa.CheckConstraint("json_valid(ast_json)", name="ck_json_ppv_ast"),
        sa.CheckConstraint(
            "json_valid(selection_snapshot_json)", name="ck_json_ppv_selection"
        ),
        sa.UniqueConstraint(
            "prompt_project_id", "version_number", name="uq_ppv_number"
        ),
        sa.UniqueConstraint("id", "prompt_project_id", name="uq_ppv_id_project"),
    )

    # accepted versions are immutable (spec §35.3)
    op.execute(
        """
        CREATE TRIGGER ppv_accepted_immutable_update
        BEFORE UPDATE ON prompt_project_versions
        WHEN OLD.accepted = 1
        BEGIN
            SELECT RAISE(ABORT, 'accepted prompt project versions are immutable');
        END;
        """
    )
    op.execute(
        """
        CREATE TRIGGER ppv_accepted_immutable_delete
        BEFORE DELETE ON prompt_project_versions
        WHEN OLD.accepted = 1
        BEGIN
            SELECT RAISE(ABORT, 'accepted prompt project versions are immutable');
        END;
        """
    )

    # ------------------------------------------------------------------ variants
    op.create_table(
        "prompt_variants",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("prompt_project_version_id", sa.Text(), nullable=False),
        sa.Column("prompt_project_id", sa.Text(), nullable=False),
        sa.Column("profile_snapshot_id", sa.Text(), nullable=False),
        sa.Column("positive_prompt", sa.Text(), nullable=False),
        sa.Column("negative_prompt", sa.Text(), nullable=False),
        sa.Column("natural_language_prompt", sa.Text(), nullable=False),
        sa.Column("blocks_json", sa.Text(), nullable=False),
        sa.Column("lint_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("conflicts_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("compiler_version", sa.Text(), nullable=False),
        # Gate A A-04: only preview/valid may persist; blocked never reaches DB
        sa.Column(
            "compilation_status", sa.Text(), nullable=False, server_default="valid"
        ),
        sa.Column("lint_status", sa.Text(), nullable=False, server_default="ok"),
        sa.Column("input_fingerprint", sa.Text(), nullable=False, server_default=""),
        # Gate A A-06: checkpoint identity snapshot per compiled variant
        sa.Column("checkpoint_id", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "checkpoint_filename_snapshot", sa.Text(), nullable=False, server_default=""
        ),
        sa.Column(
            "checkpoint_sha256_snapshot", sa.Text(), nullable=False, server_default=""
        ),
        sa.Column(
            "checkpoint_hash_status",
            sa.Text(),
            nullable=False,
            server_default="not_computed",
        ),
        sa.Column("dialect_id", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "checkpoint_profile_id", sa.Text(), nullable=False, server_default=""
        ),
        sa.Column("preset_id", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "compilation_status IN ('preview','valid')",
            name="ck_pv_compilation_status",
        ),
        sa.CheckConstraint("lint_status IN ('ok','warnings')", name="ck_pv_lint_status"),
        sa.CheckConstraint("json_valid(blocks_json)", name="ck_json_pv_blocks"),
        sa.CheckConstraint("json_valid(lint_json)", name="ck_json_pv_lint"),
        sa.CheckConstraint(
            "json_valid(conflicts_json)", name="ck_json_pv_conflicts"
        ),
        # ownership: the variant's version must belong to the named prompt project
        sa.ForeignKeyConstraint(
            ["prompt_project_version_id", "prompt_project_id"],
            ["prompt_project_versions.id", "prompt_project_versions.prompt_project_id"],
            name="fk_pv_version_ownership",
        ),
        sa.ForeignKeyConstraint(
            ["profile_snapshot_id"],
            ["prompt_profile_snapshots.id"],
            name="fk_pv_snapshot",
        ),
    )
    op.create_index("ix_pv_version", "prompt_variants", ["prompt_project_version_id"])

    # --------------------------------------------------------------- checkpoints
    # A2-12: prompt variants are write-once historical records
    op.execute(
        """
        CREATE TRIGGER pv_write_once_update
        BEFORE UPDATE ON prompt_variants
        BEGIN
            SELECT RAISE(ABORT, 'prompt_variants rows are write-once (immutable)');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER pv_write_once_delete
        BEFORE DELETE ON prompt_variants
        BEGIN
            SELECT RAISE(ABORT, 'prompt_variants rows are write-once (immutable)');
        END
        """
    )

    op.create_table(
        "checkpoint_registry",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("extension", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("modified_at", sa.Text(), nullable=False),
        # A2-09: high-resolution identity for the hash cache (st_mtime_ns);
        # the ISO string above stays for human display
        sa.Column(
            "modified_at_ns", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("sha256", sa.Text(), nullable=False, server_default=""),
        sa.Column("hash_cached_at", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.Text(), nullable=False, server_default="unidentified"),
        sa.Column("display_name", sa.Text(), nullable=False, server_default=""),
        sa.Column("base_model_hint", sa.Text(), nullable=False, server_default=""),
        sa.Column("metadata_source", sa.Text(), nullable=False, server_default="unknown"),
        sa.Column("assigned_profile_id", sa.Text(), nullable=False, server_default=""),
        sa.Column("usage_status", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "sha256_status", sa.Text(), nullable=False, server_default="not_computed"
        ),
        sa.Column("local_metadata_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("last_scanned_at", sa.Text(), nullable=False, server_default=""),
        # Gate A A-07 §10.3: non-destructive availability from rescans
        sa.Column(
            "availability", sa.Text(), nullable=False, server_default="present"
        ),
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.UniqueConstraint("path", name="uq_ckpt_path"),
        sa.CheckConstraint(
            "json_valid(local_metadata_json)", name="ck_json_ckpt_metadata"
        ),
        sa.CheckConstraint(f"status IN {_CKPT_STATUSES}", name="ck_ckpt_status"),
        sa.CheckConstraint(
            f"metadata_source IN {_META_SOURCES}", name="ck_ckpt_meta_source"
        ),
        sa.CheckConstraint(
            "extension IN ('.safetensors','.ckpt')", name="ck_ckpt_extension"
        ),
        sa.CheckConstraint(
            "availability IN ('present','missing','changed')",
            name="ck_ckpt_availability",
        ),
    )

    # --------------------------------------------------------------- experiments
    op.create_table(
        "prompt_experiment_logs",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("checkpoint_id", sa.Text(), nullable=True),
        sa.Column("checkpoint_sha256", sa.Text(), nullable=False, server_default=""),
        sa.Column("prompt_project_id", sa.Text(), nullable=True),
        sa.Column("prompt_variant_id", sa.Text(), nullable=True),
        sa.Column("resolved_profile_hash", sa.Text(), nullable=False, server_default=""),
        sa.Column("positive_prompt", sa.Text(), nullable=False, server_default=""),
        sa.Column("negative_prompt", sa.Text(), nullable=False, server_default=""),
        sa.Column("sampler", sa.Text(), nullable=False, server_default=""),
        sa.Column("scheduler", sa.Text(), nullable=False, server_default=""),
        sa.Column("steps", sa.Integer(), nullable=True),
        sa.Column("cfg", sa.Float(), nullable=True),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("seed", sa.Integer(), nullable=True),
        sa.Column("lora_settings_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("result_rating", sa.Integer(), nullable=True),
        sa.Column("identity_score", sa.Integer(), nullable=True),
        sa.Column("style_score", sa.Integer(), nullable=True),
        sa.Column("instruction_adherence", sa.Integer(), nullable=True),
        sa.Column("failure_tags_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
        sa.Column("local_output_reference", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["checkpoint_id"], ["checkpoint_registry.id"], name="fk_exp_checkpoint"
        ),
        sa.ForeignKeyConstraint(
            ["prompt_project_id"], ["prompt_projects.id"], name="fk_exp_project"
        ),
        sa.ForeignKeyConstraint(
            ["prompt_variant_id"], ["prompt_variants.id"], name="fk_exp_variant"
        ),
        sa.CheckConstraint(
            "result_rating IS NULL OR result_rating BETWEEN 1 AND 5",
            name="ck_exp_rating",
        ),
        sa.CheckConstraint(
            "json_valid(lora_settings_json)", name="ck_json_exp_lora"
        ),
        sa.CheckConstraint(
            "json_valid(failure_tags_json)", name="ck_json_exp_failure_tags"
        ),
    )
    op.create_index("ix_exp_checkpoint", "prompt_experiment_logs", ["checkpoint_id"])


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS ppv_accepted_immutable_update")
    op.execute("DROP TRIGGER IF EXISTS ppv_accepted_immutable_delete")
    op.execute("DROP TRIGGER IF EXISTS pv_write_once_update")
    op.execute("DROP TRIGGER IF EXISTS pv_write_once_delete")
    op.drop_index("uq_characters_id_project", table_name="characters")
    op.drop_index("uq_style_profiles_id_project", table_name="style_profiles")
    op.drop_table("prompt_experiment_logs")
    op.drop_table("checkpoint_registry")
    op.drop_table("prompt_variants")
    op.drop_table("prompt_project_versions")
    op.drop_table("prompt_projects")
    op.drop_table("prompt_profile_snapshots")
