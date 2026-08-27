"""Independent, versioned screenplay adaptations.

Revision ID: 0010_screenplay_adaptations
Revises: 0009_prompt_scratch_recovery
Create Date: 2026-08-25

The migration is additive and performs no backfill.  A screenplay pins one
exact accepted Scene draft; its revisions, generation evidence, and acceptance
history never reuse or mutate the novel's ``scene_drafts`` chain.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0010_screenplay_adaptations"
down_revision = "0009_prompt_scratch_recovery"
branch_labels = None
depends_on = None

_HASH_CHECK = "length({0}) = 64 AND {0} NOT GLOB '*[^0-9a-f]*'"


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
    # Exact composite FK targets. These indexes are additive over globally
    # unique IDs and do not rewrite any existing Story row.
    op.create_index(
        "uq_story_chapters_adaptation_chain",
        "story_chapters",
        ["id", "project_id", "story_outline_id"],
        unique=True,
    )
    op.create_index(
        "uq_story_scenes_adaptation_chain",
        "story_scenes",
        ["id", "project_id", "story_chapter_id"],
        unique=True,
    )
    op.create_index(
        "uq_scene_drafts_adaptation_chain",
        "scene_drafts",
        ["id", "project_id", "story_scene_id", "scene_card_version_id"],
        unique=True,
    )
    op.create_table(
        "adaptations",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("project_id", sa.Text(), nullable=False),
        sa.Column("adaptation_type", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False, server_default=""),
        sa.Column("source_outline_id", sa.Text(), nullable=False),
        sa.Column("source_chapter_id", sa.Text(), nullable=False),
        sa.Column("source_scene_id", sa.Text(), nullable=False),
        sa.Column("source_draft_id", sa.Text(), nullable=False),
        sa.Column("source_scene_card_version_id", sa.Text(), nullable=False),
        sa.Column("source_prose_sha256", sa.Text(), nullable=False),
        sa.Column("source_scene_card_sha256", sa.Text(), nullable=False),
        sa.Column("participant_manifest_json", sa.Text(), nullable=False),
        sa.Column("participant_manifest_sha256", sa.Text(), nullable=False),
        sa.Column("source_snapshot_json", sa.Text(), nullable=False),
        sa.Column("source_snapshot_sha256", sa.Text(), nullable=False),
        sa.Column("content_mode", sa.Text(), nullable=False),
        sa.Column("working_revision_id", sa.Text(), nullable=True),
        sa.Column("accepted_revision_id", sa.Text(), nullable=True),
        sa.Column("supersedes_adaptation_id", sa.Text(), nullable=True),
        sa.Column("lifecycle_status", sa.Text(), nullable=False, server_default="active"),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], name="fk_adaptation_project"),
        sa.ForeignKeyConstraint(
            ["source_outline_id", "project_id"],
            ["story_outlines.id", "story_outlines.project_id"],
            name="fk_adaptation_outline_ownership",
        ),
        sa.ForeignKeyConstraint(
            ["source_chapter_id", "project_id"],
            ["story_chapters.id", "story_chapters.project_id"],
            name="fk_adaptation_chapter_ownership",
        ),
        sa.ForeignKeyConstraint(
            ["source_chapter_id", "project_id", "source_outline_id"],
            [
                "story_chapters.id",
                "story_chapters.project_id",
                "story_chapters.story_outline_id",
            ],
            name="fk_adaptation_exact_chapter_chain",
        ),
        sa.ForeignKeyConstraint(
            ["source_scene_id", "project_id"],
            ["story_scenes.id", "story_scenes.project_id"],
            name="fk_adaptation_scene_ownership",
        ),
        sa.ForeignKeyConstraint(
            ["source_scene_id", "project_id", "source_chapter_id"],
            [
                "story_scenes.id",
                "story_scenes.project_id",
                "story_scenes.story_chapter_id",
            ],
            name="fk_adaptation_exact_scene_chain",
        ),
        sa.ForeignKeyConstraint(
            ["source_draft_id", "source_scene_id"],
            ["scene_drafts.id", "scene_drafts.story_scene_id"],
            name="fk_adaptation_draft_scene",
        ),
        sa.ForeignKeyConstraint(
            ["source_draft_id", "project_id"],
            ["scene_drafts.id", "scene_drafts.project_id"],
            name="fk_adaptation_draft_project",
        ),
        sa.ForeignKeyConstraint(
            [
                "source_draft_id",
                "project_id",
                "source_scene_id",
                "source_scene_card_version_id",
            ],
            [
                "scene_drafts.id",
                "scene_drafts.project_id",
                "scene_drafts.story_scene_id",
                "scene_drafts.scene_card_version_id",
            ],
            name="fk_adaptation_exact_draft_chain",
        ),
        sa.ForeignKeyConstraint(
            ["source_scene_card_version_id", "source_scene_id"],
            ["scene_card_versions.id", "scene_card_versions.story_scene_id"],
            name="fk_adaptation_card_scene",
        ),
        sa.ForeignKeyConstraint(
            ["working_revision_id", "id"],
            ["adaptation_revisions.id", "adaptation_revisions.adaptation_id"],
            name="fk_adaptation_working_revision",
        ),
        sa.ForeignKeyConstraint(
            ["accepted_revision_id", "id"],
            ["adaptation_revisions.id", "adaptation_revisions.adaptation_id"],
            name="fk_adaptation_accepted_revision",
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_adaptation_id", "project_id", "source_scene_id"],
            ["adaptations.id", "adaptations.project_id", "adaptations.source_scene_id"],
            name="fk_adaptation_supersedes_same_source_scope",
        ),
        sa.UniqueConstraint("id", "project_id", name="uq_adaptation_id_project"),
        sa.UniqueConstraint("id", "source_scene_id", name="uq_adaptation_id_scene"),
        sa.UniqueConstraint("id", "project_id", "source_scene_id", name="uq_adaptation_full_scope"),
        sa.CheckConstraint(
            "adaptation_type IN ('screenplay','storyboard','video_plan')",
            name="ck_adaptation_type",
        ),
        sa.CheckConstraint(
            "content_mode IN ('general','mature_nonsexual','dark','horror','violent',"
            "'suggestive','explicit_adult')",
            name="ck_adaptation_content_mode",
        ),
        sa.CheckConstraint(
            "lifecycle_status IN ('active','archived')",
            name="ck_adaptation_lifecycle",
        ),
        sa.CheckConstraint(
            "json_valid(participant_manifest_json) "
            "AND json_type(participant_manifest_json) = 'object'",
            name="ck_adaptation_participant_manifest_json",
        ),
        sa.CheckConstraint(
            "json_valid(source_snapshot_json) AND json_type(source_snapshot_json) = 'object'",
            name="ck_adaptation_source_snapshot_json",
        ),
        sa.CheckConstraint(
            _HASH_CHECK.format("source_prose_sha256"),
            name="ck_adaptation_source_prose_sha256",
        ),
        sa.CheckConstraint(
            _HASH_CHECK.format("source_scene_card_sha256"),
            name="ck_adaptation_source_card_sha256",
        ),
        sa.CheckConstraint(
            _HASH_CHECK.format("participant_manifest_sha256"),
            name="ck_adaptation_participant_sha256",
        ),
        sa.CheckConstraint(
            _HASH_CHECK.format("source_snapshot_sha256"),
            name="ck_adaptation_source_snapshot_sha256",
        ),
    )
    op.create_index(
        "ix_adaptations_scene_status",
        "adaptations",
        ["source_scene_id", "lifecycle_status", "updated_at"],
    )
    op.create_index(
        "ix_adaptations_project_type",
        "adaptations",
        ["project_id", "adaptation_type", "updated_at"],
    )

    op.create_table(
        "adaptation_revisions",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("adaptation_id", sa.Text(), nullable=False),
        sa.Column("project_id", sa.Text(), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("parent_revision_id", sa.Text(), nullable=True),
        sa.Column("screenplay_text", sa.Text(), nullable=False),
        sa.Column("screenplay_sha256", sa.Text(), nullable=False),
        sa.Column("origin", sa.Text(), nullable=False),
        sa.Column("completion_status", sa.Text(), nullable=False),
        sa.Column("generation_run_id", sa.Text(), nullable=True),
        sa.Column("change_note", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["adaptation_id", "project_id"],
            ["adaptations.id", "adaptations.project_id"],
            name="fk_adaptation_revision_parent_ownership",
        ),
        sa.ForeignKeyConstraint(
            ["parent_revision_id", "adaptation_id"],
            ["adaptation_revisions.id", "adaptation_revisions.adaptation_id"],
            name="fk_adaptation_revision_lineage",
        ),
        sa.ForeignKeyConstraint(
            ["generation_run_id", "adaptation_id"],
            ["adaptation_generation_runs.id", "adaptation_generation_runs.adaptation_id"],
            name="fk_adaptation_revision_run",
        ),
        sa.UniqueConstraint(
            "adaptation_id", "version_number", name="uq_adaptation_revision_number"
        ),
        sa.UniqueConstraint("id", "adaptation_id", name="uq_adaptation_revision_id_parent"),
        sa.UniqueConstraint("id", "project_id", name="uq_adaptation_revision_id_project"),
        sa.CheckConstraint("version_number > 0", name="ck_adaptation_revision_number"),
        sa.CheckConstraint(
            "origin IN ('manual','edited','ollama','openai')",
            name="ck_adaptation_revision_origin",
        ),
        sa.CheckConstraint(
            "completion_status IN ('complete','partial')",
            name="ck_adaptation_revision_completion_status",
        ),
        sa.CheckConstraint(
            "length(trim(screenplay_text)) > 0",
            name="ck_adaptation_revision_nonblank",
        ),
        sa.CheckConstraint(
            _HASH_CHECK.format("screenplay_sha256"),
            name="ck_adaptation_revision_sha256",
        ),
        sa.CheckConstraint(
            "(origin IN ('ollama','openai') AND generation_run_id IS NOT NULL) OR "
            "(origin IN ('manual','edited') AND generation_run_id IS NULL)",
            name="ck_adaptation_revision_origin_run",
        ),
        sa.CheckConstraint(
            "origin <> 'edited' OR parent_revision_id IS NOT NULL",
            name="ck_adaptation_revision_edit_parent",
        ),
        sa.CheckConstraint(
            "completion_status = 'complete' OR origin IN ('ollama','openai')",
            name="ck_adaptation_revision_manual_complete",
        ),
    )
    op.create_index(
        "ix_adaptation_revisions_parent",
        "adaptation_revisions",
        ["adaptation_id", "version_number"],
    )
    op.create_index(
        "uq_adaptation_revision_generation_run",
        "adaptation_revisions",
        ["generation_run_id"],
        unique=True,
        sqlite_where=sa.text("generation_run_id IS NOT NULL"),
    )

    op.create_table(
        "adaptation_generation_runs",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("adaptation_id", sa.Text(), nullable=False),
        sa.Column("project_id", sa.Text(), nullable=False),
        sa.Column("source_snapshot_sha256", sa.Text(), nullable=False),
        sa.Column("expected_working_revision_id", sa.Text(), nullable=True),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("brief_json", sa.Text(), nullable=False),
        sa.Column("options_json", sa.Text(), nullable=False),
        sa.Column("input_snapshot_json", sa.Text(), nullable=False),
        sa.Column("input_snapshot_sha256", sa.Text(), nullable=False),
        sa.Column("participant_manifest_json", sa.Text(), nullable=False),
        sa.Column("participant_manifest_sha256", sa.Text(), nullable=False),
        sa.Column("eligibility_evaluation_ids_json", sa.Text(), nullable=False),
        sa.Column("system_message_sha256", sa.Text(), nullable=False),
        sa.Column("user_message_sha256", sa.Text(), nullable=False),
        sa.Column("system_message_byte_size", sa.Integer(), nullable=False),
        sa.Column("user_message_byte_size", sa.Integer(), nullable=False),
        sa.Column("rendered_system_message", sa.Text(), nullable=True),
        sa.Column("rendered_user_message", sa.Text(), nullable=True),
        sa.Column("rendered_message_storage_enabled", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("reason_code", sa.Text(), nullable=False, server_default=""),
        sa.Column("output_sha256", sa.Text(), nullable=True),
        sa.Column("quarantined_output_text", sa.Text(), nullable=True),
        sa.Column("quarantined_output_sha256", sa.Text(), nullable=True),
        sa.Column("review_evaluation_ids_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("reviewed_at", sa.Text(), nullable=False, server_default=""),
        sa.Column("resulting_revision_id", sa.Text(), nullable=True),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("completion_tokens", sa.Integer(), nullable=True),
        sa.Column("started_at", sa.Text(), nullable=False),
        sa.Column("completed_at", sa.Text(), nullable=False, server_default=""),
        sa.Column("latency_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(
            ["adaptation_id", "project_id"],
            ["adaptations.id", "adaptations.project_id"],
            name="fk_adaptation_run_parent_ownership",
        ),
        sa.ForeignKeyConstraint(
            ["expected_working_revision_id", "adaptation_id"],
            ["adaptation_revisions.id", "adaptation_revisions.adaptation_id"],
            name="fk_adaptation_run_expected_working",
        ),
        sa.ForeignKeyConstraint(
            ["resulting_revision_id", "adaptation_id"],
            ["adaptation_revisions.id", "adaptation_revisions.adaptation_id"],
            name="fk_adaptation_run_result_revision",
        ),
        sa.UniqueConstraint("id", "adaptation_id", name="uq_adaptation_run_id_parent"),
        sa.UniqueConstraint("id", "project_id", name="uq_adaptation_run_id_project"),
        sa.CheckConstraint("provider IN ('ollama','openai')", name="ck_adaptation_run_provider"),
        sa.CheckConstraint(
            "status IN ('running','succeeded','failed','adult_pending')",
            name="ck_adaptation_run_status",
        ),
        sa.CheckConstraint(
            "reason_code IN ('','provider_authentication','provider_quota',"
            "'provider_rate_limit','provider_timeout','provider_unavailable',"
            "'model_not_found','provider_error','invalid_output',"
            "'adult_output_mode_mismatch','source_stale',"
            "'source_integrity_error','working_head_conflict',"
            "'adult_review_rejected')",
            name="ck_adaptation_run_reason_code",
        ),
        sa.CheckConstraint(
            "json_valid(brief_json) AND json_type(brief_json) = 'object'",
            name="ck_adaptation_run_brief_json",
        ),
        sa.CheckConstraint(
            "json_valid(options_json) AND json_type(options_json) = 'object'",
            name="ck_adaptation_run_options_json",
        ),
        sa.CheckConstraint(
            "json_valid(input_snapshot_json) AND json_type(input_snapshot_json) = 'object'",
            name="ck_adaptation_run_input_json",
        ),
        sa.CheckConstraint(
            "json_valid(participant_manifest_json) "
            "AND json_type(participant_manifest_json) = 'object'",
            name="ck_adaptation_run_participant_json",
        ),
        sa.CheckConstraint(
            "json_valid(eligibility_evaluation_ids_json) "
            "AND json_type(eligibility_evaluation_ids_json) = 'array'",
            name="ck_adaptation_run_eligibility_json",
        ),
        sa.CheckConstraint(
            "json_valid(review_evaluation_ids_json) "
            "AND json_type(review_evaluation_ids_json) = 'array'",
            name="ck_adaptation_run_review_json",
        ),
        sa.CheckConstraint(
            _HASH_CHECK.format("source_snapshot_sha256"),
            name="ck_adaptation_run_source_sha256",
        ),
        sa.CheckConstraint(
            _HASH_CHECK.format("input_snapshot_sha256"),
            name="ck_adaptation_run_input_sha256",
        ),
        sa.CheckConstraint(
            _HASH_CHECK.format("participant_manifest_sha256"),
            name="ck_adaptation_run_participant_sha256",
        ),
        sa.CheckConstraint(
            _HASH_CHECK.format("system_message_sha256"),
            name="ck_adaptation_run_system_sha256",
        ),
        sa.CheckConstraint(
            _HASH_CHECK.format("user_message_sha256"),
            name="ck_adaptation_run_user_sha256",
        ),
        sa.CheckConstraint(
            "output_sha256 IS NULL OR " + _HASH_CHECK.format("output_sha256"),
            name="ck_adaptation_run_output_sha256",
        ),
        sa.CheckConstraint(
            "quarantined_output_sha256 IS NULL OR "
            + _HASH_CHECK.format("quarantined_output_sha256"),
            name="ck_adaptation_run_quarantine_sha256",
        ),
        sa.CheckConstraint(
            "rendered_message_storage_enabled IN (0,1)",
            name="ck_adaptation_run_message_storage_bool",
        ),
        sa.CheckConstraint(
            "system_message_byte_size >= 0 AND user_message_byte_size >= 0 AND latency_ms >= 0",
            name="ck_adaptation_run_nonnegative_sizes",
        ),
        sa.CheckConstraint(
            "prompt_tokens IS NULL OR prompt_tokens >= 0",
            name="ck_adaptation_run_prompt_tokens",
        ),
        sa.CheckConstraint(
            "completion_tokens IS NULL OR completion_tokens >= 0",
            name="ck_adaptation_run_completion_tokens",
        ),
        sa.CheckConstraint(
            "(rendered_message_storage_enabled = 1) OR "
            "(rendered_system_message IS NULL AND rendered_user_message IS NULL)",
            name="ck_adaptation_run_raw_message_policy",
        ),
        sa.CheckConstraint(
            "(status = 'running' AND completed_at = '' AND reason_code = '' "
            "AND output_sha256 IS NULL "
            "AND quarantined_output_text IS NULL AND quarantined_output_sha256 IS NULL "
            "AND reviewed_at = '' AND resulting_revision_id IS NULL) OR "
            "(status = 'failed' AND completed_at <> '' AND reason_code <> '' "
            "AND resulting_revision_id IS NULL AND ((output_sha256 IS NULL "
            "AND quarantined_output_text IS NULL "
            "AND quarantined_output_sha256 IS NULL AND reviewed_at = '') OR "
            "(reason_code = 'adult_review_rejected' "
            "AND output_sha256 IS NOT NULL "
            "AND quarantined_output_text IS NOT NULL "
            "AND quarantined_output_sha256 = output_sha256 "
            "AND reviewed_at <> ''))) OR "
            "(status = 'adult_pending' AND completed_at <> '' AND reason_code = '' "
            "AND output_sha256 IS NOT NULL "
            "AND quarantined_output_text IS NOT NULL "
            "AND quarantined_output_sha256 IS NOT NULL "
            "AND output_sha256 = quarantined_output_sha256 "
            "AND reviewed_at = '' AND resulting_revision_id IS NULL) OR "
            "(status = 'succeeded' AND completed_at <> '' AND reason_code = '' "
            "AND output_sha256 IS NOT NULL AND resulting_revision_id IS NOT NULL "
            "AND ((quarantined_output_text IS NULL "
            "AND quarantined_output_sha256 IS NULL AND reviewed_at = '') OR "
            "(quarantined_output_text IS NOT NULL "
            "AND quarantined_output_sha256 IS NOT NULL AND reviewed_at <> ''))) ",
            name="ck_adaptation_run_lifecycle",
        ),
    )
    op.create_index(
        "ix_adaptation_runs_parent_started",
        "adaptation_generation_runs",
        ["adaptation_id", "started_at", "id"],
    )
    op.create_index(
        "ix_adaptation_runs_status",
        "adaptation_generation_runs",
        ["project_id", "status", "started_at"],
    )

    op.create_table(
        "adaptation_acceptance_events",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("adaptation_id", sa.Text(), nullable=False),
        sa.Column("revision_id", sa.Text(), nullable=False),
        sa.Column("previous_accepted_revision_id", sa.Text(), nullable=True),
        sa.Column("project_id", sa.Text(), nullable=False),
        sa.Column("source_snapshot_sha256", sa.Text(), nullable=False),
        sa.Column("accepted_at", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["adaptation_id", "project_id"],
            ["adaptations.id", "adaptations.project_id"],
            name="fk_adaptation_acceptance_parent_ownership",
        ),
        sa.ForeignKeyConstraint(
            ["revision_id", "adaptation_id"],
            ["adaptation_revisions.id", "adaptation_revisions.adaptation_id"],
            name="fk_adaptation_acceptance_revision",
        ),
        sa.ForeignKeyConstraint(
            ["previous_accepted_revision_id", "adaptation_id"],
            ["adaptation_revisions.id", "adaptation_revisions.adaptation_id"],
            name="fk_adaptation_acceptance_previous_revision",
        ),
        sa.CheckConstraint(
            _HASH_CHECK.format("source_snapshot_sha256"),
            name="ck_adaptation_acceptance_source_sha256",
        ),
        sa.CheckConstraint(
            "previous_accepted_revision_id IS NULL OR previous_accepted_revision_id <> revision_id",
            name="ck_adaptation_acceptance_changes_pointer",
        ),
    )
    op.create_index(
        "ix_adaptation_acceptance_parent_time",
        "adaptation_acceptance_events",
        ["adaptation_id", "accepted_at", "id"],
    )

    _write_once("adaptation_revisions")
    _write_once("adaptation_acceptance_events")
    op.execute(
        """
        CREATE TRIGGER adaptations_exact_accepted_source_insert
        BEFORE INSERT ON adaptations
        WHEN NOT EXISTS (
            SELECT 1
            FROM story_scenes AS scene
            JOIN scene_drafts AS draft
              ON draft.id = scene.accepted_draft_id
             AND draft.story_scene_id = scene.id
             AND draft.project_id = scene.project_id
            WHERE scene.id = NEW.source_scene_id
              AND scene.project_id = NEW.project_id
              AND draft.id = NEW.source_draft_id
              AND draft.scene_card_version_id = NEW.source_scene_card_version_id
              AND draft.draft_status = 'complete'
              AND draft.was_accepted = 1
              AND draft.is_preview = 0
        )
        BEGIN
            SELECT RAISE(ABORT,
                'adaptations: source must be the exact complete accepted Scene draft');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER adaptations_supersedes_scope_insert
        BEFORE INSERT ON adaptations
        WHEN NEW.supersedes_adaptation_id IS NOT NULL
          AND (NEW.supersedes_adaptation_id = NEW.id OR NOT EXISTS (
            SELECT 1 FROM adaptations AS previous
            WHERE previous.id = NEW.supersedes_adaptation_id
              AND previous.project_id = NEW.project_id
              AND previous.source_scene_id = NEW.source_scene_id
              AND previous.adaptation_type = NEW.adaptation_type
          ))
        BEGIN
            SELECT RAISE(ABORT,
                'adaptations: superseded root must be another same-scope adaptation');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER adaptations_source_identity_immutable
        BEFORE UPDATE ON adaptations
        WHEN OLD.id <> NEW.id
          OR OLD.project_id <> NEW.project_id
          OR OLD.adaptation_type <> NEW.adaptation_type
          OR OLD.source_outline_id <> NEW.source_outline_id
          OR OLD.source_chapter_id <> NEW.source_chapter_id
          OR OLD.source_scene_id <> NEW.source_scene_id
          OR OLD.source_draft_id <> NEW.source_draft_id
          OR OLD.source_scene_card_version_id <> NEW.source_scene_card_version_id
          OR OLD.source_prose_sha256 <> NEW.source_prose_sha256
          OR OLD.source_scene_card_sha256 <> NEW.source_scene_card_sha256
          OR OLD.participant_manifest_json <> NEW.participant_manifest_json
          OR OLD.participant_manifest_sha256 <> NEW.participant_manifest_sha256
          OR OLD.source_snapshot_json <> NEW.source_snapshot_json
          OR OLD.source_snapshot_sha256 <> NEW.source_snapshot_sha256
          OR OLD.content_mode <> NEW.content_mode
          OR OLD.supersedes_adaptation_id IS NOT NEW.supersedes_adaptation_id
          OR OLD.created_at <> NEW.created_at
        BEGIN
            SELECT RAISE(ABORT, 'adaptations: source identity is immutable');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER adaptations_accepted_pointer_requires_event
        BEFORE UPDATE OF accepted_revision_id ON adaptations
        WHEN OLD.accepted_revision_id IS NOT NEW.accepted_revision_id
          AND NOT EXISTS (
            SELECT 1
            FROM adaptation_acceptance_events AS event
            WHERE event.adaptation_id = NEW.id
              AND event.project_id = NEW.project_id
              AND event.revision_id = NEW.accepted_revision_id
              AND event.previous_accepted_revision_id IS OLD.accepted_revision_id
              AND event.source_snapshot_sha256 = NEW.source_snapshot_sha256
              AND event.accepted_at = NEW.updated_at
              AND event.rowid = (
                    SELECT max(latest.rowid)
                    FROM adaptation_acceptance_events AS latest
                    WHERE latest.adaptation_id = NEW.id
              )
          )
        BEGIN
            SELECT RAISE(ABORT,
                'adaptations: accepted pointer requires a matching acceptance event');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER adaptations_no_delete
        BEFORE DELETE ON adaptations
        BEGIN
            SELECT RAISE(ABORT, 'adaptations are archived, never deleted');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER adaptation_revision_exact_run_insert
        BEFORE INSERT ON adaptation_revisions
        WHEN NEW.origin IN ('ollama','openai') AND NOT EXISTS (
            SELECT 1
            FROM adaptation_generation_runs AS run
            JOIN adaptations AS adaptation
              ON adaptation.id = run.adaptation_id
             AND adaptation.project_id = run.project_id
            WHERE run.id = NEW.generation_run_id
              AND run.adaptation_id = NEW.adaptation_id
              AND run.project_id = NEW.project_id
              AND run.provider = NEW.origin
              AND run.status IN ('running','adult_pending')
              AND run.expected_working_revision_id IS NEW.parent_revision_id
              AND run.source_snapshot_sha256 = adaptation.source_snapshot_sha256
        )
        BEGIN
            SELECT RAISE(ABORT,
                'adaptation_revisions: AI revision requires its exact active run');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER adaptation_runs_start_running_insert
        BEFORE INSERT ON adaptation_generation_runs
        WHEN NEW.status <> 'running'
        BEGIN
            SELECT RAISE(ABORT,
                'adaptation_generation_runs: runs must start as running');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER adaptation_runs_exact_source_insert
        BEFORE INSERT ON adaptation_generation_runs
        WHEN NOT EXISTS (
            SELECT 1
            FROM adaptations AS adaptation
            WHERE adaptation.id = NEW.adaptation_id
              AND adaptation.project_id = NEW.project_id
              AND adaptation.source_snapshot_sha256 = NEW.source_snapshot_sha256
              AND adaptation.participant_manifest_json = NEW.participant_manifest_json
              AND adaptation.participant_manifest_sha256 =
                    NEW.participant_manifest_sha256
        )
        BEGIN
            SELECT RAISE(ABORT,
                'adaptation_generation_runs: source snapshot does not match root');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER adaptation_runs_identity_immutable
        BEFORE UPDATE ON adaptation_generation_runs
        WHEN OLD.id <> NEW.id
          OR OLD.adaptation_id <> NEW.adaptation_id
          OR OLD.project_id <> NEW.project_id
          OR OLD.source_snapshot_sha256 <> NEW.source_snapshot_sha256
          OR OLD.expected_working_revision_id IS NOT NEW.expected_working_revision_id
          OR OLD.provider <> NEW.provider
          OR OLD.model <> NEW.model
          OR OLD.brief_json <> NEW.brief_json
          OR OLD.options_json <> NEW.options_json
          OR OLD.input_snapshot_json <> NEW.input_snapshot_json
          OR OLD.input_snapshot_sha256 <> NEW.input_snapshot_sha256
          OR OLD.participant_manifest_json <> NEW.participant_manifest_json
          OR OLD.participant_manifest_sha256 <> NEW.participant_manifest_sha256
          OR OLD.eligibility_evaluation_ids_json <> NEW.eligibility_evaluation_ids_json
          OR OLD.system_message_sha256 <> NEW.system_message_sha256
          OR OLD.user_message_sha256 <> NEW.user_message_sha256
          OR OLD.system_message_byte_size <> NEW.system_message_byte_size
          OR OLD.user_message_byte_size <> NEW.user_message_byte_size
          OR OLD.rendered_system_message IS NOT NEW.rendered_system_message
          OR OLD.rendered_user_message IS NOT NEW.rendered_user_message
          OR OLD.rendered_message_storage_enabled <>
                NEW.rendered_message_storage_enabled
          OR OLD.started_at <> NEW.started_at
        BEGIN
            SELECT RAISE(ABORT, 'adaptation_generation_runs: identity is immutable');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER adaptation_runs_finalize_once
        BEFORE UPDATE ON adaptation_generation_runs
        WHEN OLD.status IN ('succeeded','failed')
        BEGIN
            SELECT RAISE(ABORT, 'adaptation_generation_runs: finalized runs are immutable');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER adaptation_runs_adult_review_only
        BEFORE UPDATE ON adaptation_generation_runs
        WHEN OLD.status = 'adult_pending'
          AND (NEW.status NOT IN ('succeeded','failed')
            OR OLD.quarantined_output_text IS NOT NEW.quarantined_output_text
            OR OLD.quarantined_output_sha256 IS NOT NEW.quarantined_output_sha256
            OR OLD.output_sha256 IS NOT NEW.output_sha256
            OR OLD.prompt_tokens IS NOT NEW.prompt_tokens
            OR OLD.completion_tokens IS NOT NEW.completion_tokens
            OR OLD.completed_at <> NEW.completed_at
            OR OLD.latency_ms <> NEW.latency_ms
            OR NEW.reviewed_at = ''
            OR (NEW.status = 'succeeded' AND NEW.resulting_revision_id IS NULL)
            OR (NEW.status = 'failed' AND
                (NEW.resulting_revision_id IS NOT NULL
                 OR NEW.reason_code <> 'adult_review_rejected')))
        BEGIN
            SELECT RAISE(ABORT,
                'adaptation_generation_runs: adult_pending requires reviewed promotion');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER adaptation_runs_succeeded_revision_update
        BEFORE UPDATE ON adaptation_generation_runs
        WHEN NEW.status = 'succeeded' AND NOT EXISTS (
            SELECT 1
            FROM adaptation_revisions AS revision
            WHERE revision.id = NEW.resulting_revision_id
              AND revision.adaptation_id = NEW.adaptation_id
              AND revision.project_id = NEW.project_id
              AND revision.generation_run_id = NEW.id
              AND revision.screenplay_sha256 = NEW.output_sha256
              AND revision.completion_status = 'complete'
        )
        BEGIN
            SELECT RAISE(ABORT,
                'adaptation_generation_runs: succeeded run requires exact revision');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER adaptation_runs_failed_without_revision
        BEFORE UPDATE ON adaptation_generation_runs
        WHEN NEW.status = 'failed' AND EXISTS (
            SELECT 1 FROM adaptation_revisions AS revision
            WHERE revision.generation_run_id = NEW.id
        )
        BEGIN
            SELECT RAISE(ABORT,
                'adaptation_generation_runs: failed run cannot own a revision');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER adaptation_runs_no_delete
        BEFORE DELETE ON adaptation_generation_runs
        BEGIN
            SELECT RAISE(ABORT,
                'adaptation_generation_runs rows are historical evidence');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER adaptation_acceptance_exact_state_insert
        BEFORE INSERT ON adaptation_acceptance_events
        WHEN NOT EXISTS (
            SELECT 1
            FROM adaptations AS adaptation
            JOIN story_scenes AS scene
             ON scene.id = adaptation.source_scene_id
             AND scene.project_id = adaptation.project_id
            JOIN adaptation_revisions AS revision
              ON revision.id = NEW.revision_id
             AND revision.adaptation_id = adaptation.id
             AND revision.project_id = adaptation.project_id
            WHERE adaptation.id = NEW.adaptation_id
              AND adaptation.project_id = NEW.project_id
              AND adaptation.source_snapshot_sha256 = NEW.source_snapshot_sha256
              AND adaptation.accepted_revision_id
                    IS NEW.previous_accepted_revision_id
              AND scene.accepted_draft_id = adaptation.source_draft_id
              AND revision.completion_status = 'complete'
        )
        BEGIN
            SELECT RAISE(ABORT,
                'adaptation_acceptance_events: stale source or accepted-pointer conflict');
        END
        """
    )


def downgrade() -> None:
    bind = op.get_bind()
    row_count = int(
        bind.execute(
            sa.text(
                "SELECT "
                "(SELECT count(*) FROM adaptations) + "
                "(SELECT count(*) FROM adaptation_revisions) + "
                "(SELECT count(*) FROM adaptation_generation_runs) + "
                "(SELECT count(*) FROM adaptation_acceptance_events)"
            )
        ).scalar_one()
    )
    if row_count:
        raise RuntimeError(
            "0010 downgrade blocked: screenplay adaptation data exists. "
            "Export it and restore a verified pre-0010 backup only with explicit "
            "author approval; this migration will not silently drop screenplay data."
        )

    for trigger in (
        "adaptation_acceptance_exact_state_insert",
        "adaptation_runs_no_delete",
        "adaptation_runs_failed_without_revision",
        "adaptation_runs_succeeded_revision_update",
        "adaptation_runs_adult_review_only",
        "adaptation_runs_finalize_once",
        "adaptation_runs_identity_immutable",
        "adaptation_runs_exact_source_insert",
        "adaptation_runs_start_running_insert",
        "adaptation_revision_exact_run_insert",
        "adaptations_no_delete",
        "adaptations_accepted_pointer_requires_event",
        "adaptations_source_identity_immutable",
        "adaptations_supersedes_scope_insert",
        "adaptations_exact_accepted_source_insert",
        "adaptation_acceptance_events_write_once_delete",
        "adaptation_acceptance_events_write_once_update",
        "adaptation_revisions_write_once_delete",
        "adaptation_revisions_write_once_update",
    ):
        op.execute(f"DROP TRIGGER IF EXISTS {trigger}")

    op.drop_index(
        "ix_adaptation_acceptance_parent_time",
        table_name="adaptation_acceptance_events",
    )
    op.drop_table("adaptation_acceptance_events")
    op.drop_index("ix_adaptation_runs_status", table_name="adaptation_generation_runs")
    op.drop_index(
        "ix_adaptation_runs_parent_started",
        table_name="adaptation_generation_runs",
    )
    op.drop_table("adaptation_generation_runs")
    op.drop_index(
        "uq_adaptation_revision_generation_run",
        table_name="adaptation_revisions",
    )
    op.drop_index("ix_adaptation_revisions_parent", table_name="adaptation_revisions")
    op.drop_table("adaptation_revisions")
    op.drop_index("ix_adaptations_project_type", table_name="adaptations")
    op.drop_index("ix_adaptations_scene_status", table_name="adaptations")
    op.drop_table("adaptations")
    op.drop_index("uq_scene_drafts_adaptation_chain", table_name="scene_drafts")
    op.drop_index("uq_story_scenes_adaptation_chain", table_name="story_scenes")
    op.drop_index("uq_story_chapters_adaptation_chain", table_name="story_chapters")
