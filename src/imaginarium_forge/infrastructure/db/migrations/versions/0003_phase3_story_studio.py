"""Phase 3 Story Studio tables.

Revision ID: 0003_phase3_story_studio
Revises: 0002_phase2_prompt_studio
Create Date: 2026-07-17
Revised: 2026-07-27 (Gate A hardening)

Fourteen tables: story_requirements(+versions), story_bibles(+versions),
story_outlines(+versions), story_chapters, chapter_plan_versions,
story_scenes, scene_card_versions, scene_card_participants, scene_drafts,
generation_runs, story_exports.

Migration decision (Gate A): The Phase 3 schema had not been tagged,
published, pushed, or used with persistent user data. Migration
``0003_phase3_story_studio`` was therefore revised directly before the first
accepted ``v0.3.0`` release. No additive ``0004_phase3_hardening`` exists.

Conventions inherited from Phase 1/2:

- every child row carries ``project_id`` and is bound to its parent by a
  composite FK, so a scene can never point at another project's chapter;
- accepted versions are immutable, enforced by BEFORE UPDATE/DELETE triggers
  (edits create a NEW version — never a mutation);
- every JSON column carries a ``json_valid()`` CHECK;
- downgrade is exactly symmetric (triggers → tables → indexes).

Gate A hardening applied to this revision:

A3-03  ``scene_card_participants`` binds each participant to an EXACT
       Character Version. Dual composite FKs prove, at the database level,
       that the version belongs to the character AND the character belongs
       to the project. A later current-version change cannot silently alter
       an old Scene Card.
A3-04  Composite ``(id, project_id)`` uniqueness plus composite FKs along the
       whole chain: Requirement → Bible → Outline → Chapter → Scene → Card →
       Draft → Run → Export. Cross-project links are structurally impossible.
A3-07  ``current_version_id`` is split into ``working_head_version_id`` and
       ``accepted_version_id``. One pointer never means two things.
A3-10  ``generation_runs`` is finalize-once, not write-once: a RUNNING row is
       committed BEFORE provider I/O and may be finalized exactly once. Audit
       identity columns are immutable even while running. Partial output is
       stored as a draft with ``draft_status='partial'``.
A3-11  ``story_scenes.accepted_draft_id`` is the single canonical accepted
       pointer; ``scene_drafts.was_accepted`` is historical audit only. Two
       simultaneously-active accepted drafts are structurally impossible.
A3-12  ``story_exports`` stores an immutable canonical snapshot + SHA-256,
       separate Markdown/JSON byte sizes, and typed ownership FKs.
A3-13  Numeric CHECK constraints (counts, sizes, latency, ordinals).
A3-17  Audit columns that name a version hold a real version ID, enforced by
       composite FKs to the version tables — a parent entity ID cannot be
       stored in a ``*_version_id`` column.

No speculative Phase 4 tables (no continuity checker, timeline, knowledge
matrix, inventory, embeddings, or state-application tables).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003_phase3_story_studio"
down_revision = "0002_phase2_prompt_studio"
branch_labels = None
depends_on = None

_CONTENT_MODES = (
    "('general','mature_nonsexual','dark','horror','violent',"
    "'suggestive','explicit_adult')"
)
_RUN_STATUSES = "('running','completed','failed','cancelled','timeout')"
_RUN_KINDS = "('generation','revision')"
_DRAFT_ORIGINS = "('generated','revised','manual')"
_DRAFT_STATUSES = "('complete','partial')"
_PLANNING_MODES = "('accepted','preview')"

#: version tables whose accepted rows are frozen
_VERSION_TABLES = (
    "story_requirement_versions",
    "story_bible_versions",
    "story_outline_versions",
    "chapter_plan_versions",
    "scene_card_versions",
)


def _versioned_parent(table: str) -> None:
    """Create a `<thing>` parent table: project-owned, two distinct pointers.

    A3-07: ``working_head_version_id`` is what the author is editing;
    ``accepted_version_id`` is what generation uses by default. Overloading a
    single ``current_version_id`` to mean both was the original defect.
    """
    op.create_table(
        table,
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("project_id", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False, server_default=""),
        sa.Column("working_head_version_id", sa.Text(), nullable=True),
        sa.Column("accepted_version_id", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name=f"fk_{table}_project"
        ),
        sa.UniqueConstraint("id", "project_id", name=f"uq_{table}_id_project"),
    )
    op.create_index(f"ix_{table}_project", table, ["project_id"])


def _version_table(
    table: str,
    parent: str,
    parent_col: str,
    payload_columns: list[object],
    checks: list[object],
) -> None:
    """Create an append-only `<thing>_versions` table with ownership FKs."""
    columns: list[object] = [
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column(parent_col, sa.Text(), nullable=False),
        sa.Column("project_id", sa.Text(), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("change_note", sa.Text(), nullable=False, server_default=""),
        sa.Column("accepted", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.Text(), nullable=False),
    ]
    columns.extend(payload_columns)
    columns.extend(
        [
            sa.ForeignKeyConstraint(
                [parent_col, "project_id"],
                [f"{parent}.id", f"{parent}.project_id"],
                name=f"fk_{table}_parent_ownership",
            ),
            sa.UniqueConstraint(
                parent_col, "version_number", name=f"uq_{table}_parent_version"
            ),
            sa.UniqueConstraint("id", "project_id", name=f"uq_{table}_id_project"),
            sa.UniqueConstraint("id", parent_col, name=f"uq_{table}_id_parent"),
            sa.CheckConstraint("accepted IN (0,1)", name=f"ck_{table}_accepted_bool"),
            sa.CheckConstraint(
                "version_number > 0", name=f"ck_{table}_version_positive"
            ),
            *checks,
        ]
    )
    op.create_table(table, *columns)  # type: ignore[arg-type]
    op.create_index(f"ix_{table}_parent", table, [parent_col])
    op.create_index(f"ix_{table}_accepted", table, [parent_col, "accepted"])


def _immutability_triggers(table: str) -> None:
    """Accepted versions may never be updated or deleted (edits → new version)."""
    op.execute(
        f"""
        CREATE TRIGGER {table}_accepted_immutable_update
        BEFORE UPDATE ON {table}
        WHEN OLD.accepted = 1
        BEGIN
            SELECT RAISE(ABORT, '{table}: accepted versions are immutable');
        END
        """
    )
    op.execute(
        f"""
        CREATE TRIGGER {table}_accepted_immutable_delete
        BEFORE DELETE ON {table}
        WHEN OLD.accepted = 1
        BEGIN
            SELECT RAISE(ABORT, '{table}: accepted versions are immutable');
        END
        """
    )


def _write_once_triggers(table: str) -> None:
    op.execute(
        f"""
        CREATE TRIGGER {table}_write_once_update
        BEFORE UPDATE ON {table}
        BEGIN
            SELECT RAISE(ABORT, '{table} rows are write-once (immutable)');
        END
        """
    )
    op.execute(
        f"""
        CREATE TRIGGER {table}_write_once_delete
        BEFORE DELETE ON {table}
        BEGIN
            SELECT RAISE(ABORT, '{table} rows are write-once (immutable)');
        END
        """
    )


def upgrade() -> None:
    # ------------------------------------------------------ requirements
    _versioned_parent("story_requirements")
    _version_table(
        "story_requirement_versions",
        "story_requirements",
        "story_requirement_id",
        [
            sa.Column("requirement_json", sa.Text(), nullable=False),
            sa.Column("source_text", sa.Text(), nullable=False, server_default=""),
            sa.Column(
                "structured_mode", sa.Integer(), nullable=False, server_default="1"
            ),
        ],
        [
            sa.CheckConstraint(
                "json_valid(requirement_json)", name="ck_json_srv_requirement"
            ),
            sa.CheckConstraint(
                "structured_mode IN (0,1)", name="ck_srv_structured_bool"
            ),
        ],
    )

    # ------------------------------------------------------------ bible
    # A3-04/A3-17: the requirement a Bible was written against is a real
    # requirement VERSION in the SAME project, proven by a composite FK.
    _versioned_parent("story_bibles")
    _version_table(
        "story_bible_versions",
        "story_bibles",
        "story_bible_id",
        [
            sa.Column("bible_json", sa.Text(), nullable=False),
            sa.Column("requirement_version_id", sa.Text(), nullable=True),
        ],
        [
            sa.CheckConstraint("json_valid(bible_json)", name="ck_json_sbv_bible"),
            sa.ForeignKeyConstraint(
                ["requirement_version_id", "project_id"],
                [
                    "story_requirement_versions.id",
                    "story_requirement_versions.project_id",
                ],
                name="fk_sbv_requirement_ownership",
            ),
        ],
    )

    # ---------------------------------------------------------- outline
    _versioned_parent("story_outlines")
    _version_table(
        "story_outline_versions",
        "story_outlines",
        "story_outline_id",
        [
            sa.Column("outline_json", sa.Text(), nullable=False),
            sa.Column(
                "structure_profile", sa.Text(), nullable=False, server_default=""
            ),
            sa.Column("bible_version_id", sa.Text(), nullable=True),
        ],
        [
            sa.CheckConstraint("json_valid(outline_json)", name="ck_json_sov_outline"),
            sa.ForeignKeyConstraint(
                ["bible_version_id", "project_id"],
                ["story_bible_versions.id", "story_bible_versions.project_id"],
                name="fk_sov_bible_ownership",
            ),
        ],
    )

    # --------------------------------------------------------- chapters
    op.create_table(
        "story_chapters",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("story_outline_id", sa.Text(), nullable=False),
        sa.Column("project_id", sa.Text(), nullable=False),
        sa.Column("chapter_number", sa.Integer(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False, server_default=""),
        sa.Column("working_head_plan_version_id", sa.Text(), nullable=True),
        sa.Column("accepted_plan_version_id", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["story_outline_id", "project_id"],
            ["story_outlines.id", "story_outlines.project_id"],
            name="fk_chapter_outline_ownership",
        ),
        sa.UniqueConstraint(
            "story_outline_id", "chapter_number", name="uq_chapter_outline_number"
        ),
        sa.UniqueConstraint("id", "project_id", name="uq_chapter_id_project"),
        sa.CheckConstraint("chapter_number > 0", name="ck_chapter_number_positive"),
    )
    op.create_index("ix_story_chapters_outline", "story_chapters", ["story_outline_id"])

    _version_table(
        "chapter_plan_versions",
        "story_chapters",
        "story_chapter_id",
        [sa.Column("plan_json", sa.Text(), nullable=False)],
        [sa.CheckConstraint("json_valid(plan_json)", name="ck_json_cpv_plan")],
    )

    # ----------------------------------------------------------- scenes
    # A3-11: `accepted_draft_id` is THE accepted pointer. It is a single
    # column, so two simultaneously-active accepted drafts cannot exist.
    op.create_table(
        "story_scenes",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("story_chapter_id", sa.Text(), nullable=False),
        sa.Column("project_id", sa.Text(), nullable=False),
        sa.Column("scene_number", sa.Integer(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False, server_default=""),
        sa.Column("working_head_card_version_id", sa.Text(), nullable=True),
        sa.Column("accepted_card_version_id", sa.Text(), nullable=True),
        sa.Column("working_draft_id", sa.Text(), nullable=True),
        sa.Column("accepted_draft_id", sa.Text(), nullable=True),
        sa.Column("summary_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["story_chapter_id", "project_id"],
            ["story_chapters.id", "story_chapters.project_id"],
            name="fk_scene_chapter_ownership",
        ),
        sa.ForeignKeyConstraint(
            ["accepted_draft_id", "id"],
            ["scene_drafts.id", "scene_drafts.story_scene_id"],
            name="fk_scene_accepted_draft_ownership",
        ),
        sa.ForeignKeyConstraint(
            ["working_draft_id", "id"],
            ["scene_drafts.id", "scene_drafts.story_scene_id"],
            name="fk_scene_working_draft_ownership",
        ),
        sa.UniqueConstraint(
            "story_chapter_id", "scene_number", name="uq_scene_chapter_number"
        ),
        sa.UniqueConstraint("id", "project_id", name="uq_scene_id_project"),
        sa.CheckConstraint("scene_number > 0", name="ck_scene_number_positive"),
    )
    op.create_index("ix_story_scenes_chapter", "story_scenes", ["story_chapter_id"])

    _version_table(
        "scene_card_versions",
        "story_scenes",
        "story_scene_id",
        [
            sa.Column("card_json", sa.Text(), nullable=False),
            sa.Column(
                "content_mode", sa.Text(), nullable=False, server_default="general"
            ),
        ],
        [
            sa.CheckConstraint("json_valid(card_json)", name="ck_json_scv_card"),
            sa.CheckConstraint(
                f"content_mode IN {_CONTENT_MODES}", name="ck_scv_content_mode"
            ),
        ],
    )

    # ------------------------------------------- scene card participants
    # A3-03: the EXACT Character Version a Scene Card was written for.
    # Two composite FKs together prove version→character→project ownership,
    # so eligibility can never be re-evaluated against a different version.
    op.create_table(
        "scene_card_participants",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("scene_card_version_id", sa.Text(), nullable=False),
        sa.Column("project_id", sa.Text(), nullable=False),
        sa.Column("character_id", sa.Text(), nullable=False),
        sa.Column("character_version_id", sa.Text(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False, server_default=""),
        sa.Column("is_pov", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(
            ["scene_card_version_id", "project_id"],
            ["scene_card_versions.id", "scene_card_versions.project_id"],
            name="fk_participant_card_ownership",
        ),
        sa.ForeignKeyConstraint(
            ["character_id", "project_id"],
            ["characters.id", "characters.project_id"],
            name="fk_participant_character_project",
        ),
        sa.ForeignKeyConstraint(
            ["character_version_id", "character_id"],
            ["character_versions.id", "character_versions.character_id"],
            name="fk_participant_version_character",
        ),
        sa.UniqueConstraint(
            "scene_card_version_id",
            "character_id",
            name="uq_participant_card_character",
        ),
        sa.CheckConstraint("is_pov IN (0,1)", name="ck_participant_pov_bool"),
        sa.CheckConstraint("position >= 0", name="ck_participant_position"),
    )
    op.create_index(
        "ix_participants_card", "scene_card_participants", ["scene_card_version_id"]
    )
    op.create_index(
        "ix_participants_character", "scene_card_participants", ["character_id"]
    )
    # at most one POV participant per Scene Card version
    op.execute(
        """
        CREATE UNIQUE INDEX uq_participant_single_pov
        ON scene_card_participants (scene_card_version_id)
        WHERE is_pov = 1
        """
    )

    # ----------------------------------------------------------- drafts
    # A3-11: `was_accepted` is HISTORICAL audit ("this row was accepted at
    # some point"), not the active pointer. The active pointer lives on the
    # scene. A3-10: `draft_status` distinguishes complete from partial.
    op.create_table(
        "scene_drafts",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("story_scene_id", sa.Text(), nullable=False),
        sa.Column("project_id", sa.Text(), nullable=False),
        sa.Column("draft_number", sa.Integer(), nullable=False),
        sa.Column("prose_text", sa.Text(), nullable=False),
        sa.Column("word_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("origin", sa.Text(), nullable=False, server_default="generated"),
        sa.Column(
            "draft_status", sa.Text(), nullable=False, server_default="complete"
        ),
        sa.Column("was_accepted", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("accepted_at", sa.Text(), nullable=False, server_default=""),
        # A3-R12: produced from UNACCEPTED planning material. A preview draft
        # can never become the official scene draft by flipping a flag; it
        # must be promoted through a fresh accepted-chain run.
        sa.Column("is_preview", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("promoted_from_preview_draft_id", sa.Text(), nullable=True),
        # A3-R03: a recovery rebase draft is NOT an ordinary revision
        sa.Column("is_recovery_rebase", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("scene_card_version_id", sa.Text(), nullable=False),
        sa.Column("generation_run_id", sa.Text(), nullable=True),
        sa.Column(
            "revision_request_json", sa.Text(), nullable=False, server_default="{}"
        ),
        sa.Column("revision_request_fingerprint", sa.Text(), nullable=True),
        sa.Column("parent_draft_id", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["story_scene_id", "project_id"],
            ["story_scenes.id", "story_scenes.project_id"],
            name="fk_draft_scene_ownership",
        ),
        sa.ForeignKeyConstraint(
            ["scene_card_version_id", "story_scene_id"],
            ["scene_card_versions.id", "scene_card_versions.story_scene_id"],
            name="fk_draft_card_scene_ownership",
        ),
        sa.ForeignKeyConstraint(
            ["parent_draft_id", "project_id"],
            ["scene_drafts.id", "scene_drafts.project_id"],
            name="fk_draft_parent_ownership",
        ),
        sa.UniqueConstraint(
            "story_scene_id", "draft_number", name="uq_draft_scene_number"
        ),
        sa.UniqueConstraint("id", "project_id", name="uq_draft_id_project"),
        sa.UniqueConstraint("id", "story_scene_id", name="uq_draft_id_scene"),
        sa.CheckConstraint("was_accepted IN (0,1)", name="ck_draft_accepted_bool"),
        sa.CheckConstraint("draft_number > 0", name="ck_draft_number_positive"),
        sa.CheckConstraint("word_count >= 0", name="ck_draft_word_count_nonneg"),
        sa.CheckConstraint(f"origin IN {_DRAFT_ORIGINS}", name="ck_draft_origin"),
        sa.CheckConstraint(
            f"draft_status IN {_DRAFT_STATUSES}", name="ck_draft_status"
        ),
        sa.CheckConstraint(
            "json_valid(revision_request_json)", name="ck_json_draft_revision"
        ),
        # A3-10: a partial draft is recoverable evidence, never an accepted one
        sa.CheckConstraint(
            "NOT (draft_status = 'partial' AND was_accepted = 1)",
            name="ck_draft_partial_never_accepted",
        ),
        sa.CheckConstraint("is_preview IN (0,1)", name="ck_draft_preview_bool"),
        sa.CheckConstraint(
            "is_recovery_rebase IN (0,1)", name="ck_draft_recovery_bool"
        ),
        sa.CheckConstraint(
            "NOT (is_preview = 1 AND was_accepted = 1)",
            name="ck_draft_preview_never_accepted",
        ),
        sa.ForeignKeyConstraint(
            ["promoted_from_preview_draft_id", "story_scene_id"],
            ["scene_drafts.id", "scene_drafts.story_scene_id"],
            name="fk_draft_promoted_from_ownership",
        ),
    )
    op.create_index("ix_scene_drafts_scene", "scene_drafts", ["story_scene_id"])

    # ------------------------------------------------- draft summaries
    # A3-R05: a summary counts as "previous accepted scene summary" only when
    # it belongs to the draft the scene actually accepted. One free-floating
    # `story_scenes.summary_text` meant a working note on a scene with NO
    # accepted draft leaked forward into the next scene as established fact.
    op.create_table(
        "scene_draft_summaries",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("scene_draft_id", sa.Text(), nullable=False),
        sa.Column("story_scene_id", sa.Text(), nullable=False),
        sa.Column("project_id", sa.Text(), nullable=False),
        sa.Column("summary_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["scene_draft_id", "story_scene_id"],
            ["scene_drafts.id", "scene_drafts.story_scene_id"],
            name="fk_summary_draft_ownership",
        ),
        sa.ForeignKeyConstraint(
            ["story_scene_id", "project_id"],
            ["story_scenes.id", "story_scenes.project_id"],
            name="fk_summary_scene_ownership",
        ),
        sa.UniqueConstraint("scene_draft_id", name="uq_summary_one_per_draft"),
    )
    op.create_index("ix_summaries_scene", "scene_draft_summaries", ["story_scene_id"])

    # --------------------------------------------------- generation runs
    # A3-01: `input_snapshot_json` + SHA-256 freeze every resolved input.
    # A3-17: every `*_version_id` column carries a composite FK to the
    # matching VERSION table, so a parent entity ID cannot be stored here.
    op.create_table(
        "generation_runs",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("story_scene_id", sa.Text(), nullable=False),
        sa.Column("project_id", sa.Text(), nullable=False),
        sa.Column("run_kind", sa.Text(), nullable=False, server_default="generation"),
        sa.Column("provider", sa.Text(), nullable=False, server_default=""),
        sa.Column("model", sa.Text(), nullable=False, server_default=""),
        # A3-R01/A3-R13: the COMPLETE normalized option set, immutable
        sa.Column(
            "options_snapshot_json", sa.Text(), nullable=False, server_default="{}"
        ),
        sa.Column(
            "options_snapshot_sha256", sa.Text(), nullable=False, server_default=""
        ),
        sa.Column("context_fingerprint", sa.Text(), nullable=False, server_default=""),
        # A3-R02: proves the six planning links formed ONE coherent chain
        sa.Column(
            "planning_chain_fingerprint", sa.Text(), nullable=False, server_default=""
        ),
        # A3-R12: accepted vs explicitly-selected preview planning material
        sa.Column(
            "planning_mode", sa.Text(), nullable=False, server_default="accepted"
        ),
        sa.Column(
            "preview_warning_acknowledged",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("renderer_version", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "context_schema_version", sa.Text(), nullable=False, server_default=""
        ),
        sa.Column(
            "context_contract_version", sa.Text(), nullable=False, server_default=""
        ),
        sa.Column(
            "context_budget_policy_version",
            sa.Text(),
            nullable=False,
            server_default="",
        ),
        sa.Column(
            "eligibility_fingerprint", sa.Text(), nullable=False, server_default=""
        ),
        sa.Column(
            "eligibility_evaluation_ids_json",
            sa.Text(),
            nullable=False,
            server_default="[]",
        ),
        sa.Column(
            "input_snapshot_json", sa.Text(), nullable=False, server_default="{}"
        ),
        sa.Column(
            "input_snapshot_sha256", sa.Text(), nullable=False, server_default=""
        ),
        sa.Column("requirement_version_id", sa.Text(), nullable=True),
        sa.Column("bible_version_id", sa.Text(), nullable=True),
        sa.Column("outline_version_id", sa.Text(), nullable=True),
        sa.Column("chapter_plan_version_id", sa.Text(), nullable=True),
        sa.Column("scene_card_version_id", sa.Text(), nullable=False),
        sa.Column("pov_character_version_id", sa.Text(), nullable=True),
        sa.Column(
            "character_version_ids_json", sa.Text(), nullable=False, server_default="[]"
        ),
        sa.Column("parent_draft_id", sa.Text(), nullable=True),
        sa.Column(
            "revision_request_fingerprint", sa.Text(), nullable=False, server_default=""
        ),
        # A3-R03: rebase provenance. An ordinary revision replays the source
        # run's chain; a rebase must say so, name its source, and give a
        # reason. A recovery rebase additionally records what was missing.
        sa.Column("rebased_from_generation_run_id", sa.Text(), nullable=True),
        sa.Column("rebase_reason", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "is_recovery_rebase", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column(
            "missing_source_version_ids_json",
            sa.Text(),
            nullable=False,
            server_default="[]",
        ),
        # A3-R10: hashes ALWAYS; raw text only when the project setting allows
        sa.Column("system_message_sha256", sa.Text(), nullable=False, server_default=""),
        sa.Column("user_message_sha256", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "system_message_byte_size", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column(
            "user_message_byte_size", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("rendered_system_message", sa.Text(), nullable=True),
        sa.Column("rendered_user_message", sa.Text(), nullable=True),
        sa.Column(
            "rendered_message_storage_enabled",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
        sa.Column("content_mode", sa.Text(), nullable=False, server_default="general"),
        sa.Column("status", sa.Text(), nullable=False, server_default="running"),
        sa.Column("reason_code", sa.Text(), nullable=False, server_default=""),
        sa.Column("error_reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("started_at", sa.Text(), nullable=False),
        sa.Column("completed_at", sa.Text(), nullable=False, server_default=""),
        sa.Column("latency_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(
            ["story_scene_id", "project_id"],
            ["story_scenes.id", "story_scenes.project_id"],
            name="fk_run_scene_ownership",
        ),
        sa.ForeignKeyConstraint(
            ["scene_card_version_id", "story_scene_id"],
            ["scene_card_versions.id", "scene_card_versions.story_scene_id"],
            name="fk_run_card_scene_ownership",
        ),
        sa.ForeignKeyConstraint(
            ["requirement_version_id", "project_id"],
            [
                "story_requirement_versions.id",
                "story_requirement_versions.project_id",
            ],
            name="fk_run_requirement_ownership",
        ),
        sa.ForeignKeyConstraint(
            ["bible_version_id", "project_id"],
            ["story_bible_versions.id", "story_bible_versions.project_id"],
            name="fk_run_bible_ownership",
        ),
        sa.ForeignKeyConstraint(
            ["outline_version_id", "project_id"],
            ["story_outline_versions.id", "story_outline_versions.project_id"],
            name="fk_run_outline_ownership",
        ),
        sa.ForeignKeyConstraint(
            ["chapter_plan_version_id", "project_id"],
            ["chapter_plan_versions.id", "chapter_plan_versions.project_id"],
            name="fk_run_chapter_plan_ownership",
        ),
        sa.ForeignKeyConstraint(
            ["parent_draft_id", "story_scene_id"],
            ["scene_drafts.id", "scene_drafts.story_scene_id"],
            name="fk_run_parent_draft_ownership",
        ),
        sa.ForeignKeyConstraint(
            ["rebased_from_generation_run_id", "project_id"],
            ["generation_runs.id", "generation_runs.project_id"],
            name="fk_run_rebase_source_ownership",
        ),
        sa.UniqueConstraint("id", "project_id", name="uq_run_id_project"),
        sa.CheckConstraint(f"status IN {_RUN_STATUSES}", name="ck_run_status"),
        sa.CheckConstraint(f"run_kind IN {_RUN_KINDS}", name="ck_run_kind"),
        sa.CheckConstraint(
            f"content_mode IN {_CONTENT_MODES}", name="ck_run_content_mode"
        ),
        sa.CheckConstraint("latency_ms >= 0", name="ck_run_latency_nonneg"),
        sa.CheckConstraint(
            f"planning_mode IN {_PLANNING_MODES}", name="ck_run_planning_mode"
        ),
        sa.CheckConstraint(
            "preview_warning_acknowledged IN (0,1)", name="ck_run_preview_ack_bool"
        ),
        sa.CheckConstraint(
            "is_recovery_rebase IN (0,1)", name="ck_run_recovery_bool"
        ),
        sa.CheckConstraint(
            "rendered_message_storage_enabled IN (0,1)",
            name="ck_run_message_storage_bool",
        ),
        sa.CheckConstraint(
            "system_message_byte_size >= 0", name="ck_run_system_size_nonneg"
        ),
        sa.CheckConstraint(
            "user_message_byte_size >= 0", name="ck_run_user_size_nonneg"
        ),
        # A3-R03: a rebase must name what it rebased from and why
        sa.CheckConstraint(
            "NOT (rebased_from_generation_run_id IS NOT NULL AND rebase_reason = '')",
            name="ck_run_rebase_needs_reason",
        ),
        sa.CheckConstraint(
            "NOT (is_recovery_rebase = 1 AND rebase_reason = '')",
            name="ck_run_recovery_needs_reason",
        ),
        sa.CheckConstraint(
            "json_valid(missing_source_version_ids_json)",
            name="ck_json_run_missing_versions",
        ),
        sa.CheckConstraint(
            "json_valid(options_snapshot_json)", name="ck_json_run_options"
        ),
        sa.CheckConstraint(
            "json_valid(character_version_ids_json)", name="ck_json_run_char_versions"
        ),
        sa.CheckConstraint(
            "json_valid(eligibility_evaluation_ids_json)",
            name="ck_json_run_eligibility_ids",
        ),
        sa.CheckConstraint(
            "json_valid(input_snapshot_json)", name="ck_json_run_input_snapshot"
        ),
        # A3-02: a revision run must name the draft it revised
        sa.CheckConstraint(
            "NOT (run_kind = 'revision' AND parent_draft_id IS NULL)",
            name="ck_run_revision_has_parent",
        ),
    )
    op.create_index("ix_generation_runs_scene", "generation_runs", ["story_scene_id"])
    op.create_index(
        "ix_generation_runs_status", "generation_runs", ["project_id", "status"]
    )

    # ---------------------------------------------------------- exports
    # A3-12: an export is an immutable snapshot, not a re-render of whatever
    # happens to be current. Markdown and JSON sizes are recorded separately.
    op.create_table(
        "story_exports",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("project_id", sa.Text(), nullable=False),
        sa.Column("export_mode", sa.Text(), nullable=False),
        sa.Column(
            "export_contract_version", sa.Text(), nullable=False, server_default=""
        ),
        # A3-R07: an export built from unaccepted planning must declare it
        sa.Column(
            "planning_mode", sa.Text(), nullable=False, server_default="accepted"
        ),
        sa.Column("story_outline_id", sa.Text(), nullable=True),
        sa.Column("requirement_version_id", sa.Text(), nullable=True),
        sa.Column("bible_version_id", sa.Text(), nullable=True),
        sa.Column("outline_version_id", sa.Text(), nullable=True),
        sa.Column("snapshot_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("snapshot_sha256", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "markdown_byte_size", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("json_byte_size", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_byte_size", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name="fk_export_project"
        ),
        sa.ForeignKeyConstraint(
            ["story_outline_id", "project_id"],
            ["story_outlines.id", "story_outlines.project_id"],
            name="fk_export_outline_ownership",
        ),
        sa.ForeignKeyConstraint(
            ["requirement_version_id", "project_id"],
            [
                "story_requirement_versions.id",
                "story_requirement_versions.project_id",
            ],
            name="fk_export_requirement_ownership",
        ),
        sa.ForeignKeyConstraint(
            ["bible_version_id", "project_id"],
            ["story_bible_versions.id", "story_bible_versions.project_id"],
            name="fk_export_bible_ownership",
        ),
        sa.ForeignKeyConstraint(
            ["outline_version_id", "project_id"],
            ["story_outline_versions.id", "story_outline_versions.project_id"],
            name="fk_export_outline_version_ownership",
        ),
        sa.CheckConstraint("json_valid(snapshot_json)", name="ck_json_export_snapshot"),
        sa.CheckConstraint(
            f"planning_mode IN {_PLANNING_MODES}", name="ck_export_planning_mode"
        ),
        sa.CheckConstraint("markdown_byte_size >= 0", name="ck_export_md_size_nonneg"),
        sa.CheckConstraint("json_byte_size >= 0", name="ck_export_json_size_nonneg"),
        sa.CheckConstraint("total_byte_size >= 0", name="ck_export_total_size_nonneg"),
    )
    op.create_index("ix_story_exports_project", "story_exports", ["project_id"])

    # ------------------------------------------------------- immutability
    for table in _VERSION_TABLES:
        _immutability_triggers(table)

    # A3-10: generation runs are FINALIZE-ONCE, not write-once. A RUNNING row
    # is committed before provider I/O so a crash still leaves evidence; it
    # may be finalized exactly once, and never mutated afterwards.
    op.execute(
        """
        CREATE TRIGGER generation_runs_finalize_once_update
        BEFORE UPDATE ON generation_runs
        WHEN OLD.status <> 'running'
        BEGIN
            SELECT RAISE(ABORT,
                'generation_runs: finalized runs are immutable');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER generation_runs_identity_immutable
        BEFORE UPDATE ON generation_runs
        WHEN OLD.project_id <> NEW.project_id
          OR OLD.story_scene_id <> NEW.story_scene_id
          OR OLD.scene_card_version_id <> NEW.scene_card_version_id
          OR OLD.input_snapshot_sha256 <> NEW.input_snapshot_sha256
          OR OLD.context_fingerprint <> NEW.context_fingerprint
          OR OLD.eligibility_fingerprint <> NEW.eligibility_fingerprint
          OR OLD.content_mode <> NEW.content_mode
          OR OLD.started_at <> NEW.started_at
        BEGIN
            SELECT RAISE(ABORT,
                'generation_runs: audit identity is immutable');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER generation_runs_no_delete
        BEFORE DELETE ON generation_runs
        BEGIN
            SELECT RAISE(ABORT, 'generation_runs rows are historical evidence');
        END
        """
    )

    # A3-11: once a draft has ever been accepted its content is frozen. The
    # active pointer moves on the SCENE, so switching never mutates a draft.
    op.execute(
        """
        CREATE TRIGGER scene_drafts_accepted_immutable_update
        BEFORE UPDATE ON scene_drafts
        WHEN OLD.was_accepted = 1
        BEGIN
            SELECT RAISE(ABORT, 'scene_drafts: accepted drafts are immutable');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER scene_drafts_accepted_immutable_delete
        BEFORE DELETE ON scene_drafts
        WHEN OLD.was_accepted = 1
        BEGIN
            SELECT RAISE(ABORT, 'scene_drafts: accepted drafts are immutable');
        END
        """
    )

    # A3-12: export snapshots are write-once historical records
    _write_once_triggers("story_exports")


def downgrade() -> None:
    for trigger in (
        "story_exports_write_once_update",
        "story_exports_write_once_delete",
        "scene_drafts_accepted_immutable_update",
        "scene_drafts_accepted_immutable_delete",
        "generation_runs_finalize_once_update",
        "generation_runs_identity_immutable",
        "generation_runs_no_delete",
        "scene_card_versions_accepted_immutable_update",
        "scene_card_versions_accepted_immutable_delete",
        "chapter_plan_versions_accepted_immutable_update",
        "chapter_plan_versions_accepted_immutable_delete",
        "story_outline_versions_accepted_immutable_update",
        "story_outline_versions_accepted_immutable_delete",
        "story_bible_versions_accepted_immutable_update",
        "story_bible_versions_accepted_immutable_delete",
        "story_requirement_versions_accepted_immutable_update",
        "story_requirement_versions_accepted_immutable_delete",
    ):
        op.execute(f"DROP TRIGGER IF EXISTS {trigger}")

    op.execute("DROP INDEX IF EXISTS uq_participant_single_pov")

    for table in (
        "story_exports",
        "generation_runs",
        "scene_draft_summaries",
        "scene_drafts",
        "scene_card_participants",
        "scene_card_versions",
        "story_scenes",
        "chapter_plan_versions",
        "story_chapters",
        "story_outline_versions",
        "story_outlines",
        "story_bible_versions",
        "story_bibles",
        "story_requirement_versions",
        "story_requirements",
    ):
        op.drop_table(table)
