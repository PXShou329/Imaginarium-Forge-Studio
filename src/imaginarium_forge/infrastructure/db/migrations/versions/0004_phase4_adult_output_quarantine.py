"""Phase 4 durable adult-output quarantine and review receipts.

Revision ID: 0004_phase4_adult_output_quarantine
Revises: 0003_phase3_story_studio
Create Date: 2026-08-14

This additive migration leaves ``scene_drafts`` and its accepted/partial
contract unchanged.  Adult prose is first preserved in a candidate row.  A
candidate may finalize exactly once from pending to rejected or confirmed;
confirmation is structurally impossible until one immutable review receipt
links the same prose/run/manifest to a complete draft in the same ownership
chain.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004_phase4_adult_output_quarantine"
down_revision = "0003_phase3_story_studio"
branch_labels = None
depends_on = None

_CANDIDATE_STATUSES = "('pending','rejected','confirmed')"


def _sha256_check(column: str, name: str) -> sa.CheckConstraint:
    return sa.CheckConstraint(
        f"length({column}) = 64 AND {column} NOT GLOB '*[^0-9a-f]*'",
        name=name,
    )


def upgrade() -> None:
    # Parent keys for the exact run→project→scene→card and
    # draft→project→scene→card→complete ownership FKs below.  Both source
    # tables already have globally unique ids; these indexes expose the full
    # ownership tuples as legal SQLite composite-FK targets.
    op.create_index(
        "uq_generation_runs_adult_output_ownership",
        "generation_runs",
        ["id", "project_id", "story_scene_id", "scene_card_version_id"],
        unique=True,
    )
    op.create_index(
        "uq_scene_drafts_adult_output_ownership",
        "scene_drafts",
        [
            "id",
            "project_id",
            "story_scene_id",
            "scene_card_version_id",
            "draft_status",
        ],
        unique=True,
    )

    # Durable quarantine.  The prose and every structured claim are retained
    # even when the candidate is rejected; only status/reason/finalized_at may
    # change, and only once.
    op.create_table(
        "adult_output_candidates",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("generation_run_id", sa.Text(), nullable=False),
        sa.Column("project_id", sa.Text(), nullable=False),
        sa.Column("story_scene_id", sa.Text(), nullable=False),
        sa.Column("scene_card_version_id", sa.Text(), nullable=False),
        sa.Column("prose_text", sa.Text(), nullable=False),
        sa.Column("prose_sha256", sa.Text(), nullable=False),
        sa.Column("envelope_json", sa.Text(), nullable=False),
        sa.Column("envelope_sha256", sa.Text(), nullable=False),
        sa.Column("participant_manifest_json", sa.Text(), nullable=False),
        sa.Column("participant_manifest_sha256", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("finalized_at", sa.Text(), nullable=False, server_default=""),
        sa.ForeignKeyConstraint(
            [
                "generation_run_id",
                "project_id",
                "story_scene_id",
                "scene_card_version_id",
            ],
            [
                "generation_runs.id",
                "generation_runs.project_id",
                "generation_runs.story_scene_id",
                "generation_runs.scene_card_version_id",
            ],
            name="fk_adult_candidate_run_ownership",
        ),
        sa.ForeignKeyConstraint(
            ["story_scene_id", "project_id"],
            ["story_scenes.id", "story_scenes.project_id"],
            name="fk_adult_candidate_scene_ownership",
        ),
        sa.ForeignKeyConstraint(
            ["scene_card_version_id", "story_scene_id"],
            ["scene_card_versions.id", "scene_card_versions.story_scene_id"],
            name="fk_adult_candidate_card_ownership",
        ),
        sa.UniqueConstraint(
            "generation_run_id", name="uq_adult_candidate_one_per_run"
        ),
        sa.UniqueConstraint(
            "id",
            "project_id",
            "story_scene_id",
            "scene_card_version_id",
            name="uq_adult_candidate_full_ownership",
        ),
        sa.CheckConstraint(
            f"status IN {_CANDIDATE_STATUSES}", name="ck_adult_candidate_status"
        ),
        sa.CheckConstraint(
            "(status = 'pending' AND finalized_at = '') OR "
            "(status IN ('rejected','confirmed') AND finalized_at <> '')",
            name="ck_adult_candidate_finalized_state",
        ),
        sa.CheckConstraint(
            "status <> 'rejected' OR reason <> ''",
            name="ck_adult_candidate_rejection_reason",
        ),
        sa.CheckConstraint(
            "length(prose_text) > 0", name="ck_adult_candidate_prose_nonempty"
        ),
        sa.CheckConstraint(
            "json_valid(envelope_json) AND json_type(envelope_json) = 'object'",
            name="ck_json_adult_candidate_envelope",
        ),
        sa.CheckConstraint(
            "json_valid(participant_manifest_json) "
            "AND json_type(participant_manifest_json) = 'object'",
            name="ck_json_adult_candidate_manifest",
        ),
        _sha256_check("prose_sha256", "ck_adult_candidate_prose_sha256"),
        _sha256_check("envelope_sha256", "ck_adult_candidate_envelope_sha256"),
        _sha256_check(
            "participant_manifest_sha256", "ck_adult_candidate_manifest_sha256"
        ),
    )
    op.create_index(
        "ix_adult_candidates_scene",
        "adult_output_candidates",
        ["story_scene_id", "created_at", "id"],
    )
    op.create_index(
        "ix_adult_candidates_project_status",
        "adult_output_candidates",
        ["project_id", "status"],
    )

    # Immutable review receipt.  resulting_draft_status is deliberately
    # materialized and constrained to `complete` so the composite FK can prove
    # that a partial draft was never confirmed through this path.
    op.create_table(
        "adult_output_reviews",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("candidate_id", sa.Text(), nullable=False),
        sa.Column("resulting_scene_draft_id", sa.Text(), nullable=False),
        sa.Column("project_id", sa.Text(), nullable=False),
        sa.Column("story_scene_id", sa.Text(), nullable=False),
        sa.Column("scene_card_version_id", sa.Text(), nullable=False),
        sa.Column(
            "resulting_draft_status",
            sa.Text(),
            nullable=False,
            server_default="complete",
        ),
        sa.Column("participant_manifest_json", sa.Text(), nullable=False),
        sa.Column("participant_manifest_sha256", sa.Text(), nullable=False),
        sa.Column("eligibility_evaluation_ids_json", sa.Text(), nullable=False),
        sa.Column("eligibility_evaluation_ids_sha256", sa.Text(), nullable=False),
        sa.Column("reviewed", sa.Integer(), nullable=False),
        sa.Column("reviewed_at", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            [
                "candidate_id",
                "project_id",
                "story_scene_id",
                "scene_card_version_id",
            ],
            [
                "adult_output_candidates.id",
                "adult_output_candidates.project_id",
                "adult_output_candidates.story_scene_id",
                "adult_output_candidates.scene_card_version_id",
            ],
            name="fk_adult_review_candidate_ownership",
        ),
        sa.ForeignKeyConstraint(
            [
                "resulting_scene_draft_id",
                "project_id",
                "story_scene_id",
                "scene_card_version_id",
                "resulting_draft_status",
            ],
            [
                "scene_drafts.id",
                "scene_drafts.project_id",
                "scene_drafts.story_scene_id",
                "scene_drafts.scene_card_version_id",
                "scene_drafts.draft_status",
            ],
            name="fk_adult_review_complete_draft_ownership",
        ),
        sa.UniqueConstraint("candidate_id", name="uq_adult_review_one_per_candidate"),
        sa.UniqueConstraint(
            "resulting_scene_draft_id", name="uq_adult_review_one_per_draft"
        ),
        sa.CheckConstraint(
            "resulting_draft_status = 'complete'",
            name="ck_adult_review_complete_draft",
        ),
        sa.CheckConstraint("reviewed = 1", name="ck_adult_review_reviewed_true"),
        sa.CheckConstraint(
            "reviewed_at <> ''", name="ck_adult_review_time_nonempty"
        ),
        sa.CheckConstraint(
            "json_valid(participant_manifest_json) "
            "AND json_type(participant_manifest_json) = 'object'",
            name="ck_json_adult_review_manifest",
        ),
        sa.CheckConstraint(
            "json_valid(eligibility_evaluation_ids_json) "
            "AND json_type(eligibility_evaluation_ids_json) = 'array' "
            "AND json_array_length(eligibility_evaluation_ids_json) > 0",
            name="ck_json_adult_review_eligibility_ids",
        ),
        _sha256_check(
            "participant_manifest_sha256", "ck_adult_review_manifest_sha256"
        ),
        _sha256_check(
            "eligibility_evaluation_ids_sha256",
            "ck_adult_review_eligibility_sha256",
        ),
    )
    op.create_index(
        "ix_adult_reviews_scene",
        "adult_output_reviews",
        ["story_scene_id", "reviewed_at", "id"],
    )

    # ------------------------------------------------ lifecycle triggers
    op.execute(
        """
        CREATE TRIGGER adult_output_candidates_insert_pending
        BEFORE INSERT ON adult_output_candidates
        WHEN NEW.status <> 'pending'
          OR NEW.finalized_at <> ''
          OR NOT EXISTS (
                SELECT 1 FROM generation_runs AS run
                WHERE run.id = NEW.generation_run_id
                  AND run.project_id = NEW.project_id
                  AND run.story_scene_id = NEW.story_scene_id
                  AND run.scene_card_version_id = NEW.scene_card_version_id
                   AND run.content_mode IN ('suggestive','explicit_adult')
          )
          OR json_array_length(
                NEW.participant_manifest_json, '$.participants') <> (
                SELECT COUNT(*)
                FROM scene_card_participants AS persisted
                WHERE persisted.scene_card_version_id =
                    NEW.scene_card_version_id
          )
          OR EXISTS (
                SELECT 1
                FROM json_each(
                    NEW.participant_manifest_json,
                    '$.participants') AS pin
                WHERE NOT EXISTS (
                    SELECT 1
                    FROM scene_card_participants AS persisted
                    WHERE persisted.scene_card_version_id =
                        NEW.scene_card_version_id
                      AND persisted.character_id = json_extract(
                          pin.value, '$.character_id')
                      AND persisted.character_version_id = json_extract(
                          pin.value, '$.character_version_id')
                )
          )
          OR EXISTS (
                SELECT 1
                FROM scene_card_participants AS persisted
                WHERE persisted.scene_card_version_id =
                    NEW.scene_card_version_id
                  AND NOT EXISTS (
                    SELECT 1
                    FROM json_each(
                        NEW.participant_manifest_json,
                        '$.participants') AS pin
                    WHERE json_extract(pin.value, '$.character_id') =
                          persisted.character_id
                      AND json_extract(
                          pin.value, '$.character_version_id') =
                          persisted.character_version_id
                )
          )
        BEGIN
            SELECT RAISE(ABORT,
                'adult_output_candidates must start pending');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER adult_output_candidates_finalize_once_update
        BEFORE UPDATE ON adult_output_candidates
        WHEN OLD.status <> 'pending'
          OR NEW.status NOT IN ('rejected','confirmed')
          OR NEW.finalized_at = ''
          OR OLD.generation_run_id <> NEW.generation_run_id
          OR OLD.project_id <> NEW.project_id
          OR OLD.story_scene_id <> NEW.story_scene_id
          OR OLD.scene_card_version_id <> NEW.scene_card_version_id
          OR OLD.prose_text <> NEW.prose_text
          OR OLD.prose_sha256 <> NEW.prose_sha256
          OR OLD.envelope_json <> NEW.envelope_json
          OR OLD.envelope_sha256 <> NEW.envelope_sha256
          OR OLD.participant_manifest_json <> NEW.participant_manifest_json
          OR OLD.participant_manifest_sha256 <> NEW.participant_manifest_sha256
          OR OLD.created_at <> NEW.created_at
          OR (NEW.status = 'rejected' AND NEW.reason = '')
          OR (NEW.status = 'confirmed' AND NOT EXISTS (
                SELECT 1 FROM adult_output_reviews AS review
                WHERE review.candidate_id = OLD.id
                  AND review.project_id = OLD.project_id
                  AND review.story_scene_id = OLD.story_scene_id
                  AND review.scene_card_version_id = OLD.scene_card_version_id
                  AND review.reviewed = 1
          ))
        BEGIN
            SELECT RAISE(ABORT,
                'adult_output_candidates may finalize exactly once');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER adult_output_candidates_no_delete
        BEFORE DELETE ON adult_output_candidates
        BEGIN
            SELECT RAISE(ABORT,
                'adult_output_candidates are durable quarantine evidence');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER adult_output_reviews_validate_insert
        BEFORE INSERT ON adult_output_reviews
        WHEN NEW.reviewed <> 1
          OR NEW.resulting_draft_status <> 'complete'
          OR NOT EXISTS (
                SELECT 1
                FROM adult_output_candidates AS candidate
                JOIN generation_runs AS run
                  ON run.id = candidate.generation_run_id
                 AND run.project_id = candidate.project_id
                 AND run.story_scene_id = candidate.story_scene_id
                 AND run.scene_card_version_id = candidate.scene_card_version_id
                JOIN scene_drafts AS draft
                  ON draft.id = NEW.resulting_scene_draft_id
                 AND draft.project_id = NEW.project_id
                 AND draft.story_scene_id = NEW.story_scene_id
                 AND draft.scene_card_version_id = NEW.scene_card_version_id
                 AND draft.draft_status = 'complete'
                WHERE candidate.id = NEW.candidate_id
                  AND candidate.project_id = NEW.project_id
                  AND candidate.story_scene_id = NEW.story_scene_id
                  AND candidate.scene_card_version_id = NEW.scene_card_version_id
                  AND candidate.status = 'pending'
                  AND run.status = 'completed'
                  AND candidate.prose_text = draft.prose_text
                  AND candidate.generation_run_id = draft.generation_run_id
                  AND candidate.participant_manifest_json =
                      NEW.participant_manifest_json
                  AND candidate.participant_manifest_sha256 =
                      NEW.participant_manifest_sha256
                  AND json_array_length(NEW.eligibility_evaluation_ids_json) =
                      json_array_length(
                          NEW.participant_manifest_json, '$.participants')
                  AND json_array_length(NEW.eligibility_evaluation_ids_json) = (
                        SELECT COUNT(DISTINCT audit_id.value)
                        FROM json_each(
                            NEW.eligibility_evaluation_ids_json) AS audit_id
                  )
                  AND json_array_length(
                          NEW.participant_manifest_json, '$.participants') > 0
                  AND json_array_length(
                          NEW.participant_manifest_json, '$.participants') = (
                        SELECT COUNT(DISTINCT json_extract(
                            pin.value, '$.slot_id'))
                        FROM json_each(
                            NEW.participant_manifest_json,
                            '$.participants') AS pin
                  )
                  AND json_array_length(
                          NEW.participant_manifest_json, '$.participants') = (
                        SELECT COUNT(DISTINCT json_extract(
                            pin.value, '$.character_id'))
                        FROM json_each(
                            NEW.participant_manifest_json,
                            '$.participants') AS pin
                  )
                  AND 1 = (
                        SELECT COUNT(*)
                        FROM json_each(
                            NEW.participant_manifest_json,
                            '$.participants') AS pin
                        WHERE json_extract(pin.value, '$.is_primary') = 1
                  )
                  AND NOT EXISTS (
                        SELECT 1
                        FROM json_each(
                            NEW.eligibility_evaluation_ids_json) AS audit_id
                        LEFT JOIN mature_content_eligibility_evaluations AS audit
                          ON audit.id = audit_id.value
                        WHERE audit.id IS NULL
                           OR audit.request_type <> 'story_generation'
                           OR audit.allowed <> 1
                           OR audit.evaluated_at < candidate.created_at
                           OR audit.evaluated_at < run.completed_at
                           OR audit.evaluated_at > NEW.reviewed_at
                           OR EXISTS (
                                SELECT 1
                                FROM json_each(
                                    run.eligibility_evaluation_ids_json) AS old_id
                                WHERE old_id.value = audit.id
                           )
                           OR NOT EXISTS (
                                SELECT 1
                                FROM json_each(
                                    NEW.participant_manifest_json,
                                    '$.participants') AS pin
                                WHERE json_extract(
                                          pin.value, '$.character_id') =
                                      audit.character_id
                                  AND json_extract(
                                          pin.value,
                                          '$.character_version_id') =
                                      audit.character_version_id
                           )
                  )
                  AND NOT EXISTS (
                        SELECT 1
                        FROM json_each(
                            NEW.participant_manifest_json,
                            '$.participants') AS pin
                        WHERE NOT EXISTS (
                            SELECT 1
                            FROM json_each(
                                NEW.eligibility_evaluation_ids_json) AS audit_id
                            JOIN mature_content_eligibility_evaluations AS audit
                              ON audit.id = audit_id.value
                            WHERE audit.request_type = 'story_generation'
                              AND audit.allowed = 1
                              AND audit.character_id = json_extract(
                                  pin.value, '$.character_id')
                              AND audit.character_version_id = json_extract(
                                  pin.value, '$.character_version_id')
                        )
                  )
          )
        BEGIN
            SELECT RAISE(ABORT,
                'adult output review does not match pending candidate/draft');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER adult_output_reviews_write_once_update
        BEFORE UPDATE ON adult_output_reviews
        BEGIN
            SELECT RAISE(ABORT,
                'adult_output_reviews are immutable receipts');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER adult_output_reviews_write_once_delete
        BEFORE DELETE ON adult_output_reviews
        BEGIN
            SELECT RAISE(ABORT,
                'adult_output_reviews are immutable receipts');
        END
        """
    )


def downgrade() -> None:
    for trigger in (
        "adult_output_reviews_write_once_delete",
        "adult_output_reviews_write_once_update",
        "adult_output_reviews_validate_insert",
        "adult_output_candidates_no_delete",
        "adult_output_candidates_finalize_once_update",
        "adult_output_candidates_insert_pending",
    ):
        op.execute(f"DROP TRIGGER IF EXISTS {trigger}")

    op.drop_table("adult_output_reviews")
    op.drop_table("adult_output_candidates")
    op.drop_index(
        "uq_scene_drafts_adult_output_ownership", table_name="scene_drafts"
    )
    op.drop_index(
        "uq_generation_runs_adult_output_ownership", table_name="generation_runs"
    )
