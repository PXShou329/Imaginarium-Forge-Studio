"""Phase 4.3 canonical, immutable video prompt bundles.

Revision ID: 0005_phase4_video_prompt_bundles
Revises: 0004_phase4_adult_output_quarantine
Create Date: 2026-08-14

Only ready, fully audited VIDEO prompt bundles reach this append-only table.
Blocked attempts remain audit evidence in the eligibility table but never
become exportable media artifacts.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005_phase4_video_prompt_bundles"
down_revision = "0004_phase4_adult_output_quarantine"
branch_labels = None
depends_on = None

_BUNDLE_SCHEMA = "imaginarium-forge.video-prompt-bundle.v1"


def _sha256_check(column: str, name: str) -> sa.CheckConstraint:
    return sa.CheckConstraint(
        f"length({column}) = 64 AND {column} NOT GLOB '*[^0-9a-f]*'",
        name=name,
    )


def upgrade() -> None:
    # Expose the complete prompt-project ownership pair as a composite FK
    # target.  prompt_project_versions already owns
    # UNIQUE(id, prompt_project_id).
    op.create_index(
        "uq_prompt_projects_video_ownership",
        "prompt_projects",
        ["id", "project_id"],
        unique=True,
    )

    op.create_table(
        "video_prompt_bundles",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("project_id", sa.Text(), nullable=False),
        sa.Column("prompt_project_id", sa.Text(), nullable=False),
        sa.Column("prompt_project_version_id", sa.Text(), nullable=False),
        sa.Column(
            "status", sa.Text(), nullable=False, server_default="ready_draft"
        ),
        sa.Column("content_mode", sa.Text(), nullable=False),
        sa.Column("schema_version", sa.Text(), nullable=False),
        sa.Column("parent_input_fingerprint", sa.Text(), nullable=False),
        sa.Column("source_input_fingerprint", sa.Text(), nullable=False),
        sa.Column("input_fingerprint", sa.Text(), nullable=False),
        sa.Column("bundle_json", sa.Text(), nullable=False),
        sa.Column("bundle_sha256", sa.Text(), nullable=False),
        sa.Column("participant_manifest_json", sa.Text(), nullable=False),
        sa.Column("participant_manifest_fingerprint", sa.Text(), nullable=False),
        sa.Column("eligibility_evaluation_ids_json", sa.Text(), nullable=False),
        sa.Column("eligibility_evaluation_ids_sha256", sa.Text(), nullable=False),
        sa.Column("eligibility_window_started_at", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["prompt_project_id", "project_id"],
            ["prompt_projects.id", "prompt_projects.project_id"],
            name="fk_video_bundle_prompt_project_ownership",
        ),
        sa.ForeignKeyConstraint(
            ["prompt_project_version_id", "prompt_project_id"],
            ["prompt_project_versions.id", "prompt_project_versions.prompt_project_id"],
            name="fk_video_bundle_prompt_version_ownership",
        ),
        sa.UniqueConstraint(
            "prompt_project_version_id",
            "input_fingerprint",
            name="uq_video_bundle_version_input",
        ),
        sa.CheckConstraint(
            "status = 'ready_draft'", name="ck_video_bundle_ready_draft"
        ),
        sa.CheckConstraint(
            "content_mode IN ('general','mature_nonsexual','dark','horror',"
            "'violent','suggestive','explicit_adult')",
            name="ck_video_bundle_content_mode",
        ),
        sa.CheckConstraint(
            f"schema_version = '{_BUNDLE_SCHEMA}'",
            name="ck_video_bundle_schema_version",
        ),
        sa.CheckConstraint("created_at <> ''", name="ck_video_bundle_created_at"),
        sa.CheckConstraint(
            "eligibility_window_started_at <> ''",
            name="ck_video_bundle_eligibility_window_started_at",
        ),
        sa.CheckConstraint(
            "json_valid(bundle_json) AND json_type(bundle_json) = 'object'",
            name="ck_json_video_bundle",
        ),
        sa.CheckConstraint(
            "json_valid(participant_manifest_json) "
            "AND json_type(participant_manifest_json) = 'object' "
            "AND json_array_length(participant_manifest_json, '$.participants') > 0",
            name="ck_json_video_bundle_manifest",
        ),
        sa.CheckConstraint(
            "json_valid(eligibility_evaluation_ids_json) "
            "AND json_type(eligibility_evaluation_ids_json) = 'array' "
            "AND json_array_length(eligibility_evaluation_ids_json) > 0",
            name="ck_json_video_bundle_eligibility_ids",
        ),
        _sha256_check(
            "parent_input_fingerprint",
            "ck_video_bundle_parent_input_fingerprint",
        ),
        _sha256_check(
            "source_input_fingerprint",
            "ck_video_bundle_source_input_fingerprint",
        ),
        _sha256_check("input_fingerprint", "ck_video_bundle_input_fingerprint"),
        _sha256_check("bundle_sha256", "ck_video_bundle_sha256"),
        _sha256_check(
            "participant_manifest_fingerprint",
            "ck_video_bundle_manifest_fingerprint",
        ),
        _sha256_check(
            "eligibility_evaluation_ids_sha256",
            "ck_video_bundle_eligibility_ids_sha256",
        ),
    )
    op.create_index(
        "ix_video_bundles_project_created",
        "video_prompt_bundles",
        ["project_id", "created_at", "id"],
    )
    op.create_index(
        "ix_video_bundles_prompt_version",
        "video_prompt_bundles",
        ["prompt_project_version_id", "created_at", "id"],
    )

    # Validate duplicate-free, order-preserving provenance at the database
    # boundary as well as in the domain/repository.  The nth VIDEO audit must
    # cover the nth manifest pin and must have been produced no more than five
    # minutes before the bundle timestamp.
    op.execute(
        """
        CREATE TRIGGER video_prompt_bundles_validate_insert
        BEFORE INSERT ON video_prompt_bundles
        WHEN NEW.status <> 'ready_draft'
          OR json_extract(NEW.bundle_json, '$.schema_version') IS NOT
                NEW.schema_version
          OR json_extract(NEW.bundle_json, '$.project_id') IS NOT NEW.project_id
          OR json_extract(NEW.bundle_json, '$.prompt_project_id') IS NOT
                NEW.prompt_project_id
          OR json_extract(NEW.bundle_json, '$.prompt_project_version_id') IS NOT
                NEW.prompt_project_version_id
          OR json_extract(NEW.bundle_json, '$.content_mode') IS NOT NEW.content_mode
          OR json_extract(
                NEW.bundle_json, '$.parent_input_fingerprint') IS NOT
                NEW.parent_input_fingerprint
          OR json_extract(
                NEW.bundle_json, '$.source_input_fingerprint') IS NOT
                NEW.source_input_fingerprint
          OR json_extract(NEW.bundle_json, '$.input_fingerprint') IS NOT
                NEW.input_fingerprint
          OR json_extract(
                NEW.bundle_json, '$.participant_manifest_canonical_json') IS NOT
                NEW.participant_manifest_json
          OR json_extract(
                NEW.bundle_json, '$.participant_manifest_fingerprint') IS NOT
                NEW.participant_manifest_fingerprint
          OR json_extract(NEW.bundle_json, '$.created_at') IS NOT NEW.created_at
          OR json_extract(
                NEW.bundle_json, '$.eligibility_window_started_at') IS NOT
                NEW.eligibility_window_started_at
          OR julianday(NEW.eligibility_window_started_at) IS NULL
          OR julianday(NEW.created_at) IS NULL
          OR julianday(NEW.eligibility_window_started_at) >
                julianday(NEW.created_at)
          OR julianday(NEW.eligibility_window_started_at) <
                julianday(NEW.created_at, '-5 minutes')
          OR json(
                json_extract(
                    NEW.bundle_json,
                    '$.video_eligibility_evaluation_ids')) IS NOT
                json(NEW.eligibility_evaluation_ids_json)
          OR json_extract(
                NEW.bundle_json, '$.character_asset.media_target') IS NOT
                'character_video'
          OR json_extract(
                NEW.bundle_json, '$.scene_asset.media_target') IS NOT
                'scene_video'
          OR json_extract(
                NEW.bundle_json,
                '$.character_asset.ast.media_target') IS NOT 'character_video'
          OR json_extract(
                NEW.bundle_json,
                '$.scene_asset.ast.media_target') IS NOT 'scene_video'
          OR json_array_length(
                NEW.participant_manifest_json, '$.participants') IS NOT
                json_array_length(NEW.eligibility_evaluation_ids_json)
          OR json_array_length(
                NEW.participant_manifest_json, '$.participants') IS NOT
                json_array_length(
                    NEW.bundle_json, '$.character_asset.ast.subjects')
          OR json_array_length(
                NEW.participant_manifest_json, '$.participants') IS NOT
                json_array_length(
                    NEW.bundle_json, '$.scene_asset.ast.subjects')
          OR json_array_length(
                NEW.participant_manifest_json, '$.participants') IS NOT
                (SELECT COUNT(DISTINCT json_extract(pin.value, '$.slot_id'))
                 FROM json_each(
                    NEW.participant_manifest_json, '$.participants') AS pin)
          OR 1 <> (
                SELECT COUNT(*)
                FROM json_each(
                    NEW.participant_manifest_json, '$.participants') AS pin
                WHERE json_extract(pin.value, '$.is_primary') = 1
          )
          OR EXISTS (
                SELECT 1
                FROM json_each(
                    NEW.participant_manifest_json, '$.participants') AS left_pin
                JOIN json_each(
                    NEW.participant_manifest_json, '$.participants') AS right_pin
                  ON CAST(left_pin.key AS INTEGER) < CAST(right_pin.key AS INTEGER)
                 AND json_extract(left_pin.value, '$.character_id') =
                     json_extract(right_pin.value, '$.character_id')
                 AND json_extract(left_pin.value, '$.character_version_id') =
                     json_extract(right_pin.value, '$.character_version_id')
          )
          OR json_array_length(NEW.eligibility_evaluation_ids_json) IS NOT (
                SELECT COUNT(DISTINCT audit_id.value)
                FROM json_each(NEW.eligibility_evaluation_ids_json) AS audit_id
          )
          OR NOT EXISTS (
                SELECT 1
                FROM prompt_project_versions AS version
                WHERE version.id = NEW.prompt_project_version_id
                  AND version.prompt_project_id = NEW.prompt_project_id
                  AND version.input_fingerprint = NEW.parent_input_fingerprint
                  AND version.selection_snapshot_sha256 = json_extract(
                        NEW.bundle_json, '$.selection_snapshot_sha256')
                  AND json_extract(
                        version.selection_snapshot_json,
                        '$.participant_manifest_canonical_json') =
                        NEW.participant_manifest_json
                  AND json_extract(
                        version.selection_snapshot_json,
                        '$.participant_manifest_fingerprint') =
                        NEW.participant_manifest_fingerprint
                  AND json_extract(
                        version.selection_snapshot_json, '$.content_mode') =
                        NEW.content_mode
          )
          OR EXISTS (
                SELECT 1
                FROM json_each(
                    NEW.participant_manifest_json, '$.participants') AS pin
                WHERE json_extract(
                          NEW.bundle_json,
                          '$.character_asset.ast.subjects[' || pin.key ||
                          '].slot_id') IS NOT json_extract(pin.value, '$.slot_id')
                   OR json_extract(
                          NEW.bundle_json,
                          '$.character_asset.ast.subjects[' || pin.key ||
                          '].character_id') IS NOT
                          json_extract(pin.value, '$.character_id')
                   OR json_extract(
                          NEW.bundle_json,
                          '$.character_asset.ast.subjects[' || pin.key ||
                          '].character_version_id') IS NOT
                          json_extract(pin.value, '$.character_version_id')
                   OR json_extract(
                          NEW.bundle_json,
                          '$.character_asset.ast.subjects[' || pin.key ||
                          '].role') IS NOT json_extract(pin.value, '$.role')
                   OR json_extract(
                          NEW.bundle_json,
                          '$.character_asset.ast.subjects[' || pin.key ||
                          '].is_primary') IS NOT json_extract(pin.value, '$.is_primary')
                   OR json_extract(
                          NEW.bundle_json,
                          '$.scene_asset.ast.subjects[' || pin.key ||
                          '].slot_id') IS NOT json_extract(pin.value, '$.slot_id')
                   OR json_extract(
                          NEW.bundle_json,
                          '$.scene_asset.ast.subjects[' || pin.key ||
                          '].character_id') IS NOT
                          json_extract(pin.value, '$.character_id')
                   OR json_extract(
                          NEW.bundle_json,
                          '$.scene_asset.ast.subjects[' || pin.key ||
                          '].character_version_id') IS NOT
                          json_extract(pin.value, '$.character_version_id')
                   OR json_extract(
                          NEW.bundle_json,
                          '$.scene_asset.ast.subjects[' || pin.key ||
                          '].role') IS NOT json_extract(pin.value, '$.role')
                   OR json_extract(
                          NEW.bundle_json,
                          '$.scene_asset.ast.subjects[' || pin.key ||
                          '].is_primary') IS NOT json_extract(pin.value, '$.is_primary')
                   OR json(
                        json_extract(
                          NEW.bundle_json,
                          '$.character_asset.ast.subjects[' || pin.key ||
                          '].identity_features')) IS NOT json(
                        json_extract(
                          NEW.bundle_json,
                          '$.scene_asset.ast.subjects[' || pin.key ||
                          '].identity_features'))
                   OR json_extract(
                          NEW.bundle_json,
                          '$.character_asset.ast.subjects[' || pin.key ||
                          '].identity_features_sha256') IS NOT json_extract(
                          NEW.bundle_json,
                          '$.scene_asset.ast.subjects[' || pin.key ||
                          '].identity_features_sha256')
          )
          OR EXISTS (
                SELECT 1
                FROM json_each(NEW.eligibility_evaluation_ids_json) AS audit_id
                LEFT JOIN mature_content_eligibility_evaluations AS audit
                  ON audit.id = audit_id.value
                WHERE audit.id IS NULL
                   OR audit.request_type IS NOT 'video_prompt_compile'
                   OR audit.allowed IS NOT 1
                   OR audit.character_id IS NOT json_extract(
                        NEW.participant_manifest_json,
                        '$.participants[' || audit_id.key || '].character_id')
                   OR audit.character_version_id IS NOT json_extract(
                        NEW.participant_manifest_json,
                        '$.participants[' || audit_id.key ||
                        '].character_version_id')
                   OR audit.requested_character_version_id IS NOT json_extract(
                        NEW.participant_manifest_json,
                        '$.participants[' || audit_id.key ||
                        '].character_version_id')
                   OR NOT EXISTS (
                        SELECT 1 FROM characters AS character
                        WHERE character.id = audit.character_id
                          AND character.project_id = NEW.project_id
                   )
                   OR julianday(audit.evaluated_at) IS NULL
                   OR julianday(audit.evaluated_at) > julianday(NEW.created_at)
                   OR julianday(audit.evaluated_at) <
                        julianday(NEW.eligibility_window_started_at)
          )
        BEGIN
            SELECT RAISE(ABORT,
                'video prompt bundle provenance or VIDEO audits are invalid');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER video_prompt_bundles_write_once_update
        BEFORE UPDATE ON video_prompt_bundles
        BEGIN
            SELECT RAISE(ABORT, 'video prompt bundles are immutable');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER video_prompt_bundles_write_once_delete
        BEFORE DELETE ON video_prompt_bundles
        BEGIN
            SELECT RAISE(ABORT, 'video prompt bundles are immutable');
        END
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS video_prompt_bundles_write_once_delete")
    op.execute("DROP TRIGGER IF EXISTS video_prompt_bundles_write_once_update")
    op.execute("DROP TRIGGER IF EXISTS video_prompt_bundles_validate_insert")
    op.drop_index("ix_video_bundles_prompt_version", table_name="video_prompt_bundles")
    op.drop_index("ix_video_bundles_project_created", table_name="video_prompt_bundles")
    op.drop_table("video_prompt_bundles")
    op.drop_index("uq_prompt_projects_video_ownership", table_name="prompt_projects")
