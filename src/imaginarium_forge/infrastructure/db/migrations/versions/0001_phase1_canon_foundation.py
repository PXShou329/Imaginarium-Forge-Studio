"""Phase 1 Canon Vault foundation — greenfield schema.

Revision ID: 0001_phase1
Revises: None
Create Date: 2026-07-14

Seven tables per the approved schema decision note (three-tier eligibility
ownership). DDL is hand-written with explicit ordering because `characters`
and `character_versions` reference each other (the current-version OWNERSHIP
composite FK); SQLite legally accepts a forward FK reference at CREATE time
and enforces it at DML time, but SQLAlchemy's create_all() cannot sort the
cycle. ORM parity is locked by tests/integration/test_migrations.py.

Downgrade drops everything in dependency order; only run it on a database you
have backed up (see WINDOWS_VALIDATION_CHECKLIST.md).
"""

import sqlalchemy as sa
from alembic import op

revision = "0001_phase1"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "projects",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("default_language", sa.Text(), nullable=False, server_default="zh-TW"),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.CheckConstraint("status IN ('active','archived')", name="ck_projects_status"),
    )

    # characters BEFORE character_versions: the ownership composite FK below is a
    # forward reference, which SQLite resolves at DML time (PRAGMA foreign_keys=ON).
    op.create_table(
        "characters",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("project_id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("character_origin", sa.Text(), nullable=False),
        sa.Column("current_version_id", sa.Text(), nullable=True),
        sa.Column("age_status_json", sa.Text(), nullable=False),
        sa.Column("originality_review_json", sa.Text(), nullable=True),
        sa.Column("source_metadata_json", sa.Text(), nullable=True),
        sa.Column("profile_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], name="fk_characters_project"),
        sa.ForeignKeyConstraint(
            ["current_version_id", "id"],
            ["character_versions.id", "character_versions.character_id"],
            name="fk_characters_current_version_ownership",
        ),
        sa.CheckConstraint(
            "character_origin IN ('original','existing')", name="ck_characters_origin"
        ),
        sa.CheckConstraint("status IN ('active','archived')", name="ck_characters_status"),
        sa.CheckConstraint("json_valid(age_status_json)", name="ck_characters_age_json"),
        sa.CheckConstraint(
            "originality_review_json IS NULL OR json_valid(originality_review_json)",
            name="ck_characters_orig_json",
        ),
        sa.CheckConstraint(
            "source_metadata_json IS NULL OR json_valid(source_metadata_json)",
            name="ck_characters_source_json",
        ),
        sa.CheckConstraint("json_valid(profile_json)", name="ck_characters_profile_json"),
    )
    op.create_index("ix_characters_project", "characters", ["project_id"])

    op.create_table(
        "character_versions",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("character_id", sa.Text(), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("visual_dna_json", sa.Text(), nullable=False),
        sa.Column("adult_presentation_json", sa.Text(), nullable=False),
        sa.Column("voice_profile", sa.Text(), nullable=False, server_default=""),
        sa.Column("personality_profile", sa.Text(), nullable=False, server_default=""),
        sa.Column("change_note", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["character_id"], ["characters.id"], name="fk_versions_character"),
        sa.UniqueConstraint("character_id", "version_number", name="uq_versions_number"),
        sa.UniqueConstraint("id", "character_id", name="uq_versions_id_character"),
        sa.CheckConstraint("json_valid(visual_dna_json)", name="ck_versions_dna_json"),
        sa.CheckConstraint(
            "json_valid(adult_presentation_json)", name="ck_versions_presentation_json"
        ),
    )
    op.create_index("ix_versions_character", "character_versions", ["character_id"])

    op.create_table(
        "style_profiles",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("project_id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("current_version_id", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], name="fk_styles_project"),
        sa.ForeignKeyConstraint(
            ["current_version_id", "id"],
            ["style_profile_versions.id", "style_profile_versions.style_profile_id"],
            name="fk_styles_current_version_ownership",
        ),
        sa.CheckConstraint("status IN ('active','archived')", name="ck_styles_status"),
    )
    op.create_index("ix_styles_project", "style_profiles", ["project_id"])

    op.create_table(
        "style_profile_versions",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("style_profile_id", sa.Text(), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("style_dna_json", sa.Text(), nullable=False),
        sa.Column("change_note", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["style_profile_id"], ["style_profiles.id"], name="fk_style_versions_profile"
        ),
        sa.UniqueConstraint("style_profile_id", "version_number", name="uq_style_versions_number"),
        sa.UniqueConstraint("id", "style_profile_id", name="uq_style_versions_id_profile"),
        sa.CheckConstraint("json_valid(style_dna_json)", name="ck_style_versions_dna_json"),
    )
    op.create_index("ix_style_versions_profile", "style_profile_versions", ["style_profile_id"])

    op.create_table(
        "outfits",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("character_id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("canonical_traits_json", sa.Text(), nullable=False),
        sa.Column("optional_traits_json", sa.Text(), nullable=False),
        sa.Column("prohibited_traits_json", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["character_id"], ["characters.id"], name="fk_outfits_character"),
        sa.CheckConstraint("status IN ('active','archived')", name="ck_outfits_status"),
        sa.CheckConstraint("json_valid(canonical_traits_json)", name="ck_outfits_canonical_json"),
        sa.CheckConstraint("json_valid(optional_traits_json)", name="ck_outfits_optional_json"),
        sa.CheckConstraint(
            "json_valid(prohibited_traits_json)", name="ck_outfits_prohibited_json"
        ),
    )
    op.create_index("ix_outfits_character", "outfits", ["character_id"])

    op.create_table(
        "mature_content_eligibility_evaluations",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("character_id", sa.Text(), nullable=False),
        sa.Column("character_version_id", sa.Text(), nullable=True),
        sa.Column("requested_character_version_id", sa.Text(), nullable=True),
        sa.Column("request_type", sa.Text(), nullable=False),
        sa.Column("allowed", sa.Integer(), nullable=False),
        sa.Column("reason_code", sa.Text(), nullable=False),
        sa.Column("validator_version", sa.Text(), nullable=False),
        sa.Column("input_fingerprint", sa.Text(), nullable=False),
        sa.Column("details_json", sa.Text(), nullable=True),
        sa.Column("evaluated_at", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["character_id"], ["characters.id"], name="fk_elig_character"),
        # A-03: the canonical version relationship is OWNERSHIP-protected — the
        # audited version must belong to the audited character. The raw attempted
        # ID lives in requested_character_version_id (diagnostic, deliberately
        # WITHOUT a foreign key so invalid/nonexistent attempts can be preserved).
        sa.ForeignKeyConstraint(
            ["character_version_id", "character_id"],
            ["character_versions.id", "character_versions.character_id"],
            name="fk_elig_version_ownership",
        ),
        sa.CheckConstraint("allowed IN (0,1)", name="ck_elig_allowed_bool"),
        sa.CheckConstraint(
            "details_json IS NULL OR json_valid(details_json)", name="ck_elig_details_json"
        ),
    )
    op.create_index(
        "ix_elig_character_time",
        "mature_content_eligibility_evaluations",
        ["character_id", "evaluated_at"],
    )
    op.create_index("ix_elig_reason", "mature_content_eligibility_evaluations", ["reason_code"])
    op.create_index(
        "ix_elig_fingerprint", "mature_content_eligibility_evaluations", ["input_fingerprint"]
    )

    # A-10: database-level append-only enforcement. The repository already exposes
    # no update/delete methods; these triggers make the guarantee hold even for
    # raw SQL, external tools, or future code paths.
    op.execute(
        """
        CREATE TRIGGER prevent_eligibility_audit_update
        BEFORE UPDATE ON mature_content_eligibility_evaluations
        BEGIN
            SELECT RAISE(ABORT, 'eligibility audits are append-only');
        END;
        """
    )
    op.execute(
        """
        CREATE TRIGGER prevent_eligibility_audit_delete
        BEFORE DELETE ON mature_content_eligibility_evaluations
        BEGIN
            SELECT RAISE(ABORT, 'eligibility audits are append-only');
        END;
        """
    )


def downgrade() -> None:
    # explicit trigger removal (SQLite would also drop them with the table,
    # but the spec requires the downgrade to remove them explicitly)
    op.execute("DROP TRIGGER IF EXISTS prevent_eligibility_audit_update")
    op.execute("DROP TRIGGER IF EXISTS prevent_eligibility_audit_delete")
    for name in [
        "mature_content_eligibility_evaluations",
        "outfits",
        "style_profile_versions",
        "style_profiles",
        "character_versions",
        "characters",
        "projects",
    ]:
        op.drop_table(name)
