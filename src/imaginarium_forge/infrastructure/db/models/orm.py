"""SQLAlchemy 2.x ORM models — Phase 1 Canon Vault tables.

These mirror migration 0001 exactly (hand-written greenfield migration is the
source of truth for DDL; models exist for repositories/queries). Ownership per
the approved schema decision note: identity data on `characters`, presentation
data on `character_versions`, eligibility audit in its own append-only table.
"""

from __future__ import annotations

from sqlalchemy import (
    CheckConstraint,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Text,
    UniqueConstraint,
)
from sqlalchemy import text as sql_text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class ProjectRow(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    default_language: Mapped[str] = mapped_column(Text, nullable=False, default="zh-TW")
    status: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        CheckConstraint("status IN ('active','archived')", name="ck_projects_status"),
    )


class CharacterRow(Base):
    __tablename__ = "characters"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    project_id: Mapped[str] = mapped_column(
        Text, ForeignKey("projects.id", name="fk_characters_project"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    character_origin: Mapped[str] = mapped_column(Text, nullable=False)
    current_version_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    age_status_json: Mapped[str] = mapped_column(Text, nullable=False)
    originality_review_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    profile_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    status: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        CheckConstraint(
            "character_origin IN ('original','existing')", name="ck_characters_origin"
        ),
        CheckConstraint("status IN ('active','archived')", name="ck_characters_status"),
        CheckConstraint("json_valid(age_status_json)", name="ck_characters_age_json"),
        CheckConstraint(
            "originality_review_json IS NULL OR json_valid(originality_review_json)",
            name="ck_characters_orig_json",
        ),
        CheckConstraint(
            "source_metadata_json IS NULL OR json_valid(source_metadata_json)",
            name="ck_characters_source_json",
        ),
        CheckConstraint("json_valid(profile_json)", name="ck_characters_profile_json"),
        # DB-level guarantee: the current version, when set, belongs to THIS character.
        ForeignKeyConstraint(
            ["current_version_id", "id"],
            ["character_versions.id", "character_versions.character_id"],
            name="fk_characters_current_version_ownership",
        ),
        Index("ix_characters_project", "project_id"),
    )


class CharacterVersionRow(Base):
    __tablename__ = "character_versions"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    character_id: Mapped[str] = mapped_column(
        Text, ForeignKey("characters.id", name="fk_versions_character"), nullable=False
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    visual_dna_json: Mapped[str] = mapped_column(Text, nullable=False)
    adult_presentation_json: Mapped[str] = mapped_column(Text, nullable=False)
    voice_profile: Mapped[str] = mapped_column(Text, nullable=False, default="")
    personality_profile: Mapped[str] = mapped_column(Text, nullable=False, default="")
    change_note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        UniqueConstraint("character_id", "version_number", name="uq_versions_number"),
        # composite target for the ownership FK on characters.current_version_id
        UniqueConstraint("id", "character_id", name="uq_versions_id_character"),
        CheckConstraint("json_valid(visual_dna_json)", name="ck_versions_dna_json"),
        CheckConstraint(
            "json_valid(adult_presentation_json)", name="ck_versions_presentation_json"
        ),
        Index("ix_versions_character", "character_id"),
    )


class StyleProfileRow(Base):
    __tablename__ = "style_profiles"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    project_id: Mapped[str] = mapped_column(
        Text, ForeignKey("projects.id", name="fk_styles_project"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    current_version_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        CheckConstraint("status IN ('active','archived')", name="ck_styles_status"),
        ForeignKeyConstraint(
            ["current_version_id", "id"],
            ["style_profile_versions.id", "style_profile_versions.style_profile_id"],
            name="fk_styles_current_version_ownership",
        ),
        Index("ix_styles_project", "project_id"),
    )


class StyleProfileVersionRow(Base):
    __tablename__ = "style_profile_versions"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    style_profile_id: Mapped[str] = mapped_column(
        Text, ForeignKey("style_profiles.id", name="fk_style_versions_profile"), nullable=False
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    style_dna_json: Mapped[str] = mapped_column(Text, nullable=False)
    change_note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        UniqueConstraint("style_profile_id", "version_number", name="uq_style_versions_number"),
        UniqueConstraint("id", "style_profile_id", name="uq_style_versions_id_profile"),
        CheckConstraint("json_valid(style_dna_json)", name="ck_style_versions_dna_json"),
        Index("ix_style_versions_profile", "style_profile_id"),
    )


class OutfitRow(Base):
    __tablename__ = "outfits"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    character_id: Mapped[str] = mapped_column(
        Text, ForeignKey("characters.id", name="fk_outfits_character"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    canonical_traits_json: Mapped[str] = mapped_column(Text, nullable=False)
    optional_traits_json: Mapped[str] = mapped_column(Text, nullable=False)
    prohibited_traits_json: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        CheckConstraint("status IN ('active','archived')", name="ck_outfits_status"),
        CheckConstraint("json_valid(canonical_traits_json)", name="ck_outfits_canonical_json"),
        CheckConstraint("json_valid(optional_traits_json)", name="ck_outfits_optional_json"),
        CheckConstraint("json_valid(prohibited_traits_json)", name="ck_outfits_prohibited_json"),
        Index("ix_outfits_character", "character_id"),
    )


class EligibilityEvaluationRow(Base):
    """Append-only audit — the repository exposes add/list ONLY (no update/delete)."""

    __tablename__ = "mature_content_eligibility_evaluations"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    character_id: Mapped[str] = mapped_column(
        Text, ForeignKey("characters.id", name="fk_elig_character"), nullable=False
    )
    character_version_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: raw attempted version ID (diagnostic only — never used for authorization;
    #: intentionally no FK so invalid/nonexistent attempts are preserved).
    requested_character_version_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    request_type: Mapped[str] = mapped_column(Text, nullable=False)
    allowed: Mapped[int] = mapped_column(Integer, nullable=False)
    reason_code: Mapped[str] = mapped_column(Text, nullable=False)
    validator_version: Mapped[str] = mapped_column(Text, nullable=False)
    input_fingerprint: Mapped[str] = mapped_column(Text, nullable=False)
    details_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    evaluated_at: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["character_version_id", "character_id"],
            ["character_versions.id", "character_versions.character_id"],
            name="fk_elig_version_ownership",
        ),
        CheckConstraint("allowed IN (0,1)", name="ck_elig_allowed_bool"),
        CheckConstraint(
            "details_json IS NULL OR json_valid(details_json)", name="ck_elig_details_json"
        ),
        Index("ix_elig_character_time", "character_id", "evaluated_at"),
        Index("ix_elig_reason", "reason_code"),
        Index("ix_elig_fingerprint", "input_fingerprint"),
    )


# ============================== Phase 2 (0002) ==============================


class PromptProfileSnapshotRow(Base):
    __tablename__ = "prompt_profile_snapshots"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    dialect_id: Mapped[str] = mapped_column(Text, nullable=False)
    dialect_version: Mapped[str] = mapped_column(Text, nullable=False)
    checkpoint_profile_id: Mapped[str] = mapped_column(Text, nullable=False, default="")
    checkpoint_profile_version: Mapped[str] = mapped_column(Text, nullable=False, default="")
    preset_id: Mapped[str] = mapped_column(Text, nullable=False, default="")
    preset_version: Mapped[str] = mapped_column(Text, nullable=False, default="")
    resolved_json: Mapped[str] = mapped_column(Text, nullable=False)
    sha256: Mapped[str] = mapped_column(Text, nullable=False)
    compiler_version: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (UniqueConstraint("sha256", name="uq_profile_snapshot_sha256"),)


class PromptProjectRow(Base):
    __tablename__ = "prompt_projects"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    project_id: Mapped[str] = mapped_column(
        Text, ForeignKey("projects.id", name="fk_pp_project"), nullable=False
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    source_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    character_id: Mapped[str | None] = mapped_column(
        Text, ForeignKey("characters.id", name="fk_pp_character"), nullable=True
    )
    character_version_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    style_profile_id: Mapped[str | None] = mapped_column(
        Text, ForeignKey("style_profiles.id", name="fk_pp_style"), nullable=True
    )
    style_version_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="draft")
    current_version_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["character_version_id", "character_id"],
            ["character_versions.id", "character_versions.character_id"],
            name="fk_pp_character_version_ownership",
        ),
        ForeignKeyConstraint(
            ["style_version_id", "style_profile_id"],
            ["style_profile_versions.id", "style_profile_versions.style_profile_id"],
            name="fk_pp_style_version_ownership",
        ),
        ForeignKeyConstraint(
            ["current_version_id", "id"],
            ["prompt_project_versions.id", "prompt_project_versions.prompt_project_id"],
            name="fk_pp_current_version_ownership",
        ),
        CheckConstraint("status IN ('draft','active','archived')", name="ck_pp_status"),
        Index("uq_prompt_projects_video_ownership", "id", "project_id", unique=True),
    )


class PromptProjectVersionRow(Base):
    __tablename__ = "prompt_project_versions"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    prompt_project_id: Mapped[str] = mapped_column(
        Text, ForeignKey("prompt_projects.id", name="fk_ppv_project"), nullable=False
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    ast_json: Mapped[str] = mapped_column(Text, nullable=False)
    change_note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    selection_snapshot_json: Mapped[str] = mapped_column(
        Text, nullable=False, default="{}"
    )
    selection_snapshot_sha256: Mapped[str] = mapped_column(
        Text, nullable=False, default=""
    )
    input_fingerprint: Mapped[str] = mapped_column(Text, nullable=False, default="")
    accepted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        CheckConstraint("accepted IN (0,1)", name="ck_ppv_accepted_bool"),
        UniqueConstraint("prompt_project_id", "version_number", name="uq_ppv_number"),
        UniqueConstraint("id", "prompt_project_id", name="uq_ppv_id_project"),
    )


class PromptVariantRow(Base):
    __tablename__ = "prompt_variants"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    prompt_project_version_id: Mapped[str] = mapped_column(Text, nullable=False)
    prompt_project_id: Mapped[str] = mapped_column(Text, nullable=False)
    profile_snapshot_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("prompt_profile_snapshots.id", name="fk_pv_snapshot"),
        nullable=False,
    )
    positive_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    negative_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    natural_language_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    blocks_json: Mapped[str] = mapped_column(Text, nullable=False)
    lint_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    conflicts_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    compiler_version: Mapped[str] = mapped_column(Text, nullable=False)
    compilation_status: Mapped[str] = mapped_column(
        Text, nullable=False, default="valid"
    )
    lint_status: Mapped[str] = mapped_column(Text, nullable=False, default="ok")
    input_fingerprint: Mapped[str] = mapped_column(Text, nullable=False, default="")
    checkpoint_id: Mapped[str] = mapped_column(Text, nullable=False, default="")
    checkpoint_filename_snapshot: Mapped[str] = mapped_column(
        Text, nullable=False, default=""
    )
    checkpoint_sha256_snapshot: Mapped[str] = mapped_column(
        Text, nullable=False, default=""
    )
    checkpoint_hash_status: Mapped[str] = mapped_column(
        Text, nullable=False, default="not_computed"
    )
    dialect_id: Mapped[str] = mapped_column(Text, nullable=False, default="")
    checkpoint_profile_id: Mapped[str] = mapped_column(
        Text, nullable=False, default=""
    )
    preset_id: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        CheckConstraint(
            "compilation_status IN ('preview','valid')",
            name="ck_pv_compilation_status",
        ),
        CheckConstraint("lint_status IN ('ok','warnings')", name="ck_pv_lint_status"),
        ForeignKeyConstraint(
            ["prompt_project_version_id", "prompt_project_id"],
            ["prompt_project_versions.id", "prompt_project_versions.prompt_project_id"],
            name="fk_pv_version_ownership",
        ),
    )


class VideoPromptBundleRow(Base):
    """Append-only canonical Phase 4.3 video prompt bundle."""

    __tablename__ = "video_prompt_bundles"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    project_id: Mapped[str] = mapped_column(Text, nullable=False)
    prompt_project_id: Mapped[str] = mapped_column(Text, nullable=False)
    prompt_project_version_id: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="ready_draft")
    content_mode: Mapped[str] = mapped_column(Text, nullable=False)
    schema_version: Mapped[str] = mapped_column(Text, nullable=False)
    parent_input_fingerprint: Mapped[str] = mapped_column(Text, nullable=False)
    source_input_fingerprint: Mapped[str] = mapped_column(Text, nullable=False)
    input_fingerprint: Mapped[str] = mapped_column(Text, nullable=False)
    bundle_json: Mapped[str] = mapped_column(Text, nullable=False)
    bundle_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    participant_manifest_json: Mapped[str] = mapped_column(Text, nullable=False)
    participant_manifest_fingerprint: Mapped[str] = mapped_column(Text, nullable=False)
    eligibility_evaluation_ids_json: Mapped[str] = mapped_column(Text, nullable=False)
    eligibility_evaluation_ids_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    eligibility_window_started_at: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["prompt_project_id", "project_id"],
            ["prompt_projects.id", "prompt_projects.project_id"],
            name="fk_video_bundle_prompt_project_ownership",
        ),
        ForeignKeyConstraint(
            ["prompt_project_version_id", "prompt_project_id"],
            ["prompt_project_versions.id", "prompt_project_versions.prompt_project_id"],
            name="fk_video_bundle_prompt_version_ownership",
        ),
        UniqueConstraint(
            "prompt_project_version_id",
            "input_fingerprint",
            name="uq_video_bundle_version_input",
        ),
        CheckConstraint(
            "status = 'ready_draft'", name="ck_video_bundle_ready_draft"
        ),
        CheckConstraint(
            "content_mode IN ('general','mature_nonsexual','dark','horror',"
            "'violent','suggestive','explicit_adult')",
            name="ck_video_bundle_content_mode",
        ),
    )


class CheckpointRegistryRow(Base):
    __tablename__ = "checkpoint_registry"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    path: Mapped[str] = mapped_column(Text, nullable=False)
    filename: Mapped[str] = mapped_column(Text, nullable=False)
    extension: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    modified_at: Mapped[str] = mapped_column(Text, nullable=False)
    modified_at_ns: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sha256: Mapped[str] = mapped_column(Text, nullable=False, default="")
    hash_cached_at: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(Text, nullable=False, default="unidentified")
    display_name: Mapped[str] = mapped_column(Text, nullable=False, default="")
    base_model_hint: Mapped[str] = mapped_column(Text, nullable=False, default="")
    metadata_source: Mapped[str] = mapped_column(Text, nullable=False, default="unknown")
    assigned_profile_id: Mapped[str] = mapped_column(Text, nullable=False, default="")
    usage_status: Mapped[str] = mapped_column(Text, nullable=False, default="")
    sha256_status: Mapped[str] = mapped_column(
        Text, nullable=False, default="not_computed"
    )
    local_metadata_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    last_scanned_at: Mapped[str] = mapped_column(Text, nullable=False, default="")
    availability: Mapped[str] = mapped_column(Text, nullable=False, default="present")
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        UniqueConstraint("path", name="uq_ckpt_path"),
        CheckConstraint(
            "status IN ('unidentified','identified_but_untested','testing',"
            "'recommended_profile','personal_favorite','unsuitable')",
            name="ck_ckpt_status",
        ),
        CheckConstraint(
            "metadata_source IN ('manual','safetensors_metadata','sidecar_json',"
            "'filename_inference','unknown')",
            name="ck_ckpt_meta_source",
        ),
        CheckConstraint(
            "extension IN ('.safetensors','.ckpt')", name="ck_ckpt_extension"
        ),
    )


class PromptExperimentLogRow(Base):
    __tablename__ = "prompt_experiment_logs"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    checkpoint_id: Mapped[str | None] = mapped_column(
        Text, ForeignKey("checkpoint_registry.id", name="fk_exp_checkpoint"), nullable=True
    )
    checkpoint_sha256: Mapped[str] = mapped_column(Text, nullable=False, default="")
    prompt_project_id: Mapped[str | None] = mapped_column(
        Text, ForeignKey("prompt_projects.id", name="fk_exp_project"), nullable=True
    )
    prompt_variant_id: Mapped[str | None] = mapped_column(
        Text, ForeignKey("prompt_variants.id", name="fk_exp_variant"), nullable=True
    )
    resolved_profile_hash: Mapped[str] = mapped_column(Text, nullable=False, default="")
    positive_prompt: Mapped[str] = mapped_column(Text, nullable=False, default="")
    negative_prompt: Mapped[str] = mapped_column(Text, nullable=False, default="")
    sampler: Mapped[str] = mapped_column(Text, nullable=False, default="")
    scheduler: Mapped[str] = mapped_column(Text, nullable=False, default="")
    steps: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cfg: Mapped[float | None] = mapped_column(Float, nullable=True)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    seed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    lora_settings_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    result_rating: Mapped[int | None] = mapped_column(Integer, nullable=True)
    identity_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    style_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    instruction_adherence: Mapped[int | None] = mapped_column(Integer, nullable=True)
    failure_tags_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    local_output_reference: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        CheckConstraint(
            "result_rating IS NULL OR result_rating BETWEEN 1 AND 5", name="ck_exp_rating"
        ),
    )


# ============================================================ Phase 3 story
# Mirrors migration 0003_phase3_story_studio exactly (revised at Gate A).
# The migration remains the DDL source of truth; these rows exist for
# repositories/queries. Composite constraints are declared here too so the
# ORM/migration parity test can compare them.


class StoryRequirementRow(Base):
    __tablename__ = "story_requirements"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    project_id: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # A3-07: what the author is editing vs what generation uses by default
    working_head_version_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    accepted_version_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        UniqueConstraint("id", "project_id", name="uq_story_requirements_id_project"),
    )


class StoryRequirementVersionRow(Base):
    __tablename__ = "story_requirement_versions"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    story_requirement_id: Mapped[str] = mapped_column(Text, nullable=False)
    project_id: Mapped[str] = mapped_column(Text, nullable=False)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    change_note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    accepted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    requirement_json: Mapped[str] = mapped_column(Text, nullable=False)
    source_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    structured_mode: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class StoryBibleRow(Base):
    __tablename__ = "story_bibles"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    project_id: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False, default="")
    working_head_version_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    accepted_version_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        UniqueConstraint("id", "project_id", name="uq_story_bibles_id_project"),
    )


class StoryBibleVersionRow(Base):
    __tablename__ = "story_bible_versions"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    story_bible_id: Mapped[str] = mapped_column(Text, nullable=False)
    project_id: Mapped[str] = mapped_column(Text, nullable=False)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    change_note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    accepted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    bible_json: Mapped[str] = mapped_column(Text, nullable=False)
    # A3-04: NULL means "not linked"; a value must be a same-project version
    requirement_version_id: Mapped[str | None] = mapped_column(Text, nullable=True)


class StoryOutlineRow(Base):
    __tablename__ = "story_outlines"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    project_id: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False, default="")
    working_head_version_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    accepted_version_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        UniqueConstraint("id", "project_id", name="uq_story_outlines_id_project"),
    )


class StoryOutlineVersionRow(Base):
    __tablename__ = "story_outline_versions"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    story_outline_id: Mapped[str] = mapped_column(Text, nullable=False)
    project_id: Mapped[str] = mapped_column(Text, nullable=False)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    change_note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    accepted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    outline_json: Mapped[str] = mapped_column(Text, nullable=False)
    structure_profile: Mapped[str] = mapped_column(Text, nullable=False, default="")
    bible_version_id: Mapped[str | None] = mapped_column(Text, nullable=True)


class StoryChapterRow(Base):
    __tablename__ = "story_chapters"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    story_outline_id: Mapped[str] = mapped_column(Text, nullable=False)
    project_id: Mapped[str] = mapped_column(Text, nullable=False)
    chapter_number: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False, default="")
    working_head_plan_version_id: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )
    accepted_plan_version_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        UniqueConstraint("id", "project_id", name="uq_chapter_id_project"),
    )


class ChapterPlanVersionRow(Base):
    __tablename__ = "chapter_plan_versions"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    story_chapter_id: Mapped[str] = mapped_column(Text, nullable=False)
    project_id: Mapped[str] = mapped_column(Text, nullable=False)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    change_note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    accepted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    plan_json: Mapped[str] = mapped_column(Text, nullable=False)


class StorySceneRow(Base):
    __tablename__ = "story_scenes"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    story_chapter_id: Mapped[str] = mapped_column(Text, nullable=False)
    project_id: Mapped[str] = mapped_column(Text, nullable=False)
    scene_number: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False, default="")
    working_head_card_version_id: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )
    accepted_card_version_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    working_draft_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    # A3-11: the ONE canonical accepted draft; export follows this pointer
    accepted_draft_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (UniqueConstraint("id", "project_id", name="uq_scene_id_project"),)


class SceneCardVersionRow(Base):
    __tablename__ = "scene_card_versions"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    story_scene_id: Mapped[str] = mapped_column(Text, nullable=False)
    project_id: Mapped[str] = mapped_column(Text, nullable=False)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    change_note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    accepted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    card_json: Mapped[str] = mapped_column(Text, nullable=False)
    content_mode: Mapped[str] = mapped_column(Text, nullable=False, default="general")

    __table_args__ = (
        UniqueConstraint("id", "project_id", name="uq_scene_card_versions_id_project"),
    )


class SceneCardParticipantRow(Base):
    """A3-03: the EXACT Character Version this Scene Card was written for."""

    __tablename__ = "scene_card_participants"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    scene_card_version_id: Mapped[str] = mapped_column(Text, nullable=False)
    project_id: Mapped[str] = mapped_column(Text, nullable=False)
    character_id: Mapped[str] = mapped_column(Text, nullable=False)
    character_version_id: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(Text, nullable=False, default="")
    is_pov: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __table_args__ = (
        UniqueConstraint(
            "scene_card_version_id", "character_id", name="uq_participant_card_character"
        ),
    )


class SceneDraftRow(Base):
    __tablename__ = "scene_drafts"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    story_scene_id: Mapped[str] = mapped_column(Text, nullable=False)
    project_id: Mapped[str] = mapped_column(Text, nullable=False)
    draft_number: Mapped[int] = mapped_column(Integer, nullable=False)
    prose_text: Mapped[str] = mapped_column(Text, nullable=False)
    word_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    origin: Mapped[str] = mapped_column(Text, nullable=False, default="generated")
    # A3-10: partial output is preserved but can never be accepted
    draft_status: Mapped[str] = mapped_column(Text, nullable=False, default="complete")
    # A3-11: historical audit only — the ACTIVE pointer lives on the scene
    was_accepted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    accepted_at: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # A3-R12: produced from unaccepted planning; promote, never accept
    is_preview: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    promoted_from_preview_draft_id: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )
    # A3-R03: a recovery rebase draft is not an ordinary revision
    is_recovery_rebase: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    scene_card_version_id: Mapped[str] = mapped_column(Text, nullable=False)
    generation_run_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    revision_request_json: Mapped[str] = mapped_column(
        Text, nullable=False, default="{}"
    )
    revision_request_fingerprint: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )
    parent_draft_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        UniqueConstraint("id", "project_id", name="uq_draft_id_project"),
        UniqueConstraint("id", "story_scene_id", name="uq_draft_id_scene"),
    )


# =========================== Stable story block projection (0012)
class StoryBlockAnchorRow(Base):
    """Stable logical paragraph identity within one Story scene."""

    __tablename__ = "story_block_anchors"

    logical_block_id: Mapped[str] = mapped_column(Text, primary_key=True)
    project_id: Mapped[str] = mapped_column(Text, nullable=False)
    story_scene_id: Mapped[str] = mapped_column(Text, nullable=False)
    created_from_draft_id: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["created_from_draft_id", "project_id"],
            ["scene_drafts.id", "scene_drafts.project_id"],
            name="fk_story_block_anchor_draft_project",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["created_from_draft_id", "story_scene_id"],
            ["scene_drafts.id", "scene_drafts.story_scene_id"],
            name="fk_story_block_anchor_draft_scene",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "logical_block_id",
            "project_id",
            "story_scene_id",
            name="uq_story_block_anchor_ownership",
        ),
        CheckConstraint(
            "length(trim(logical_block_id)) > 0",
            name="ck_story_block_anchor_id",
        ),
        Index(
            "ix_story_block_anchors_scene",
            "story_scene_id",
            "logical_block_id",
        ),
    )


class SceneDraftBlockSetRow(Base):
    """One immutable, exact projection manifest for a Scene draft."""

    __tablename__ = "scene_draft_block_sets"

    scene_draft_id: Mapped[str] = mapped_column(Text, primary_key=True)
    project_id: Mapped[str] = mapped_column(Text, nullable=False)
    story_scene_id: Mapped[str] = mapped_column(Text, nullable=False)
    parent_draft_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    parent_draft_available: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    schema_version: Mapped[str] = mapped_column(Text, nullable=False)
    segmentation_version: Mapped[str] = mapped_column(Text, nullable=False)
    mapping_version: Mapped[str] = mapped_column(Text, nullable=False)
    leading_text: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=""
    )
    prose_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    manifest_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    lineage_quality: Mapped[str] = mapped_column(Text, nullable=False)
    block_count: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["scene_draft_id", "project_id"],
            ["scene_drafts.id", "scene_drafts.project_id"],
            name="fk_scene_draft_block_set_draft_project",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["scene_draft_id", "story_scene_id"],
            ["scene_drafts.id", "scene_drafts.story_scene_id"],
            name="fk_scene_draft_block_set_draft_scene",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "scene_draft_id",
            "project_id",
            "story_scene_id",
            name="uq_scene_draft_block_set_ownership",
        ),
        CheckConstraint(
            "schema_version = 'story-block-v1'",
            name="ck_scene_draft_block_set_schema",
        ),
        CheckConstraint(
            "segmentation_version = 'paragraph-v1'",
            name="ck_scene_draft_block_set_segmentation",
        ),
        CheckConstraint(
            "mapping_version = 'parent-aware-v1'",
            name="ck_scene_draft_block_set_mapping",
        ),
        CheckConstraint(
            "length(prose_sha256) = 64 AND "
            "prose_sha256 NOT GLOB '*[^0-9a-f]*'",
            name="ck_scene_draft_block_set_prose_sha256",
        ),
        CheckConstraint(
            "length(manifest_sha256) = 64 AND "
            "manifest_sha256 NOT GLOB '*[^0-9a-f]*'",
            name="ck_scene_draft_block_set_manifest_sha256",
        ),
        CheckConstraint(
            "lineage_quality IN ('root','exact','conservative','legacy_unlinked')",
            name="ck_scene_draft_block_set_lineage_quality",
        ),
        CheckConstraint(
            "parent_draft_available IN (0,1)",
            name="ck_scene_draft_block_set_parent_available",
        ),
        CheckConstraint(
            "(parent_draft_id IS NULL AND parent_draft_available = 0 "
            "AND lineage_quality = 'root') OR "
            "(parent_draft_id IS NOT NULL AND length(trim(parent_draft_id)) > 0 "
            "AND parent_draft_available = 0 "
            "AND lineage_quality = 'legacy_unlinked') OR "
            "(parent_draft_id IS NOT NULL AND length(trim(parent_draft_id)) > 0 "
            "AND parent_draft_available = 1 "
            "AND lineage_quality IN ('exact','conservative'))",
            name="ck_scene_draft_block_set_lineage_state",
        ),
        CheckConstraint(
            "block_count >= 0",
            name="ck_scene_draft_block_set_count",
        ),
        Index(
            "ix_scene_draft_block_sets_scene",
            "story_scene_id",
            "scene_draft_id",
        ),
    )


class StoryBlockRevisionRow(Base):
    """Exact paragraph text and separator for one immutable Scene draft."""

    __tablename__ = "story_block_revisions"

    block_revision_id: Mapped[str] = mapped_column(Text, primary_key=True)
    logical_block_id: Mapped[str] = mapped_column(Text, nullable=False)
    scene_draft_id: Mapped[str] = mapped_column(Text, nullable=False)
    project_id: Mapped[str] = mapped_column(Text, nullable=False)
    story_scene_id: Mapped[str] = mapped_column(Text, nullable=False)
    parent_block_revision_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    block_type: Mapped[str] = mapped_column(Text, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    separator_after: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=""
    )
    text_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    source_slice_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["logical_block_id", "project_id", "story_scene_id"],
            [
                "story_block_anchors.logical_block_id",
                "story_block_anchors.project_id",
                "story_block_anchors.story_scene_id",
            ],
            name="fk_story_block_revision_anchor_ownership",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["scene_draft_id", "project_id", "story_scene_id"],
            [
                "scene_draft_block_sets.scene_draft_id",
                "scene_draft_block_sets.project_id",
                "scene_draft_block_sets.story_scene_id",
            ],
            name="fk_story_block_revision_set_ownership",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["parent_block_revision_id", "project_id", "story_scene_id"],
            [
                "story_block_revisions.block_revision_id",
                "story_block_revisions.project_id",
                "story_block_revisions.story_scene_id",
            ],
            name="fk_story_block_revision_parent_ownership",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "block_revision_id",
            "project_id",
            "story_scene_id",
            name="uq_story_block_revision_ownership",
        ),
        UniqueConstraint(
            "scene_draft_id",
            "ordinal",
            name="uq_story_block_revision_draft_ordinal",
        ),
        UniqueConstraint(
            "scene_draft_id",
            "logical_block_id",
            name="uq_story_block_revision_draft_logical",
        ),
        CheckConstraint(
            "ordinal >= 0",
            name="ck_story_block_revision_ordinal",
        ),
        CheckConstraint(
            "block_type = 'paragraph'",
            name="ck_story_block_revision_type",
        ),
        CheckConstraint(
            "length(text_sha256) = 64 AND "
            "text_sha256 NOT GLOB '*[^0-9a-f]*'",
            name="ck_story_block_revision_text_sha256",
        ),
        CheckConstraint(
            "length(source_slice_sha256) = 64 AND "
            "source_slice_sha256 NOT GLOB '*[^0-9a-f]*'",
            name="ck_story_block_revision_slice_sha256",
        ),
        Index(
            "ix_story_block_revisions_draft",
            "scene_draft_id",
            "ordinal",
        ),
        Index(
            "ix_story_block_revisions_logical",
            "logical_block_id",
            "scene_draft_id",
        ),
    )


class SceneDraftSummaryRow(Base):
    """A3-R05: a summary owned by exactly one draft."""

    __tablename__ = "scene_draft_summaries"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    scene_draft_id: Mapped[str] = mapped_column(Text, nullable=False)
    story_scene_id: Mapped[str] = mapped_column(Text, nullable=False)
    project_id: Mapped[str] = mapped_column(Text, nullable=False)
    summary_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        UniqueConstraint("scene_draft_id", name="uq_summary_one_per_draft"),
    )


class GenerationRunRow(Base):
    __tablename__ = "generation_runs"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    story_scene_id: Mapped[str] = mapped_column(Text, nullable=False)
    project_id: Mapped[str] = mapped_column(Text, nullable=False)
    run_kind: Mapped[str] = mapped_column(Text, nullable=False, default="generation")
    provider: Mapped[str] = mapped_column(Text, nullable=False, default="")
    model: Mapped[str] = mapped_column(Text, nullable=False, default="")
    options_snapshot_json: Mapped[str] = mapped_column(
        Text, nullable=False, default="{}"
    )
    options_snapshot_sha256: Mapped[str] = mapped_column(
        Text, nullable=False, default=""
    )
    context_fingerprint: Mapped[str] = mapped_column(Text, nullable=False, default="")
    planning_chain_fingerprint: Mapped[str] = mapped_column(
        Text, nullable=False, default=""
    )
    planning_mode: Mapped[str] = mapped_column(Text, nullable=False, default="accepted")
    preview_warning_acknowledged: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    renderer_version: Mapped[str] = mapped_column(Text, nullable=False, default="")
    context_schema_version: Mapped[str] = mapped_column(
        Text, nullable=False, default=""
    )
    context_contract_version: Mapped[str] = mapped_column(
        Text, nullable=False, default=""
    )
    context_budget_policy_version: Mapped[str] = mapped_column(
        Text, nullable=False, default=""
    )
    eligibility_fingerprint: Mapped[str] = mapped_column(
        Text, nullable=False, default=""
    )
    eligibility_evaluation_ids_json: Mapped[str] = mapped_column(
        Text, nullable=False, default="[]"
    )
    # A3-01: canonical JSON + SHA-256 of every resolved input
    input_snapshot_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    input_snapshot_sha256: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # A3-17: these hold real VERSION ids, enforced by composite FKs
    requirement_version_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    bible_version_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    outline_version_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    chapter_plan_version_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    scene_card_version_id: Mapped[str] = mapped_column(Text, nullable=False)
    pov_character_version_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    character_version_ids_json: Mapped[str] = mapped_column(
        Text, nullable=False, default="[]"
    )
    parent_draft_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    revision_request_fingerprint: Mapped[str] = mapped_column(
        Text, nullable=False, default=""
    )
    rebased_from_generation_run_id: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )
    rebase_reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    is_recovery_rebase: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    missing_source_version_ids_json: Mapped[str] = mapped_column(
        Text, nullable=False, default="[]"
    )
    # A3-R10: hashes always; raw text only when the setting allows
    system_message_sha256: Mapped[str] = mapped_column(Text, nullable=False, default="")
    user_message_sha256: Mapped[str] = mapped_column(Text, nullable=False, default="")
    system_message_byte_size: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    user_message_byte_size: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    rendered_system_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    rendered_user_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    rendered_message_storage_enabled: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1
    )
    content_mode: Mapped[str] = mapped_column(Text, nullable=False, default="general")
    status: Mapped[str] = mapped_column(Text, nullable=False, default="running")
    reason_code: Mapped[str] = mapped_column(Text, nullable=False, default="")
    error_reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    started_at: Mapped[str] = mapped_column(Text, nullable=False)
    completed_at: Mapped[str] = mapped_column(Text, nullable=False, default="")
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __table_args__ = (UniqueConstraint("id", "project_id", name="uq_run_id_project"),)


class AdultOutputCandidateRow(Base):
    """Durable adult prose held outside ``scene_drafts`` pending review."""

    __tablename__ = "adult_output_candidates"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    generation_run_id: Mapped[str] = mapped_column(Text, nullable=False)
    project_id: Mapped[str] = mapped_column(Text, nullable=False)
    story_scene_id: Mapped[str] = mapped_column(Text, nullable=False)
    scene_card_version_id: Mapped[str] = mapped_column(Text, nullable=False)
    prose_text: Mapped[str] = mapped_column(Text, nullable=False)
    prose_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    envelope_json: Mapped[str] = mapped_column(Text, nullable=False)
    envelope_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    participant_manifest_json: Mapped[str] = mapped_column(Text, nullable=False)
    participant_manifest_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    finalized_at: Mapped[str] = mapped_column(Text, nullable=False, default="")

    __table_args__ = (
        UniqueConstraint(
            "generation_run_id", name="uq_adult_candidate_one_per_run"
        ),
        UniqueConstraint(
            "id",
            "project_id",
            "story_scene_id",
            "scene_card_version_id",
            name="uq_adult_candidate_full_ownership",
        ),
    )


class AdultOutputReviewRow(Base):
    """Immutable human-review receipt for one confirmed candidate."""

    __tablename__ = "adult_output_reviews"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    candidate_id: Mapped[str] = mapped_column(Text, nullable=False)
    resulting_scene_draft_id: Mapped[str] = mapped_column(Text, nullable=False)
    project_id: Mapped[str] = mapped_column(Text, nullable=False)
    story_scene_id: Mapped[str] = mapped_column(Text, nullable=False)
    scene_card_version_id: Mapped[str] = mapped_column(Text, nullable=False)
    resulting_draft_status: Mapped[str] = mapped_column(
        Text, nullable=False, default="complete"
    )
    participant_manifest_json: Mapped[str] = mapped_column(Text, nullable=False)
    participant_manifest_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    eligibility_evaluation_ids_json: Mapped[str] = mapped_column(
        Text, nullable=False
    )
    eligibility_evaluation_ids_sha256: Mapped[str] = mapped_column(
        Text, nullable=False
    )
    reviewed: Mapped[int] = mapped_column(Integer, nullable=False)
    reviewed_at: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        UniqueConstraint("candidate_id", name="uq_adult_review_one_per_candidate"),
        UniqueConstraint(
            "resulting_scene_draft_id", name="uq_adult_review_one_per_draft"
        ),
    )


class StoryExportRow(Base):
    __tablename__ = "story_exports"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    project_id: Mapped[str] = mapped_column(Text, nullable=False)
    export_mode: Mapped[str] = mapped_column(Text, nullable=False)
    export_contract_version: Mapped[str] = mapped_column(
        Text, nullable=False, default=""
    )
    planning_mode: Mapped[str] = mapped_column(Text, nullable=False, default="accepted")
    story_outline_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    requirement_version_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    bible_version_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    outline_version_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    # A3-12: the immutable snapshot the export was rendered from
    snapshot_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    snapshot_sha256: Mapped[str] = mapped_column(Text, nullable=False, default="")
    markdown_byte_size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    json_byte_size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_byte_size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)


# ========================================= Screenplay adaptations (0010)
class AdaptationRow(Base):
    """One derivative root pinned to an exact accepted Story Scene draft."""

    __tablename__ = "adaptations"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    project_id: Mapped[str] = mapped_column(Text, nullable=False)
    adaptation_type: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False, default="")
    source_outline_id: Mapped[str] = mapped_column(Text, nullable=False)
    source_chapter_id: Mapped[str] = mapped_column(Text, nullable=False)
    source_scene_id: Mapped[str] = mapped_column(Text, nullable=False)
    source_draft_id: Mapped[str] = mapped_column(Text, nullable=False)
    source_scene_card_version_id: Mapped[str] = mapped_column(Text, nullable=False)
    source_prose_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    source_scene_card_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    participant_manifest_json: Mapped[str] = mapped_column(Text, nullable=False)
    participant_manifest_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    source_snapshot_json: Mapped[str] = mapped_column(Text, nullable=False)
    source_snapshot_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    content_mode: Mapped[str] = mapped_column(Text, nullable=False)
    working_revision_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    accepted_revision_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    supersedes_adaptation_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    lifecycle_status: Mapped[str] = mapped_column(Text, nullable=False, default="active")
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        UniqueConstraint("id", "project_id", name="uq_adaptation_id_project"),
        UniqueConstraint("id", "source_scene_id", name="uq_adaptation_id_scene"),
        UniqueConstraint(
            "id", "project_id", "source_scene_id", name="uq_adaptation_full_scope"
        ),
    )


class AdaptationRevisionRow(Base):
    """Append-only screenplay text; acceptance lives on the root pointer."""

    __tablename__ = "adaptation_revisions"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    adaptation_id: Mapped[str] = mapped_column(Text, nullable=False)
    project_id: Mapped[str] = mapped_column(Text, nullable=False)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    parent_revision_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    screenplay_text: Mapped[str] = mapped_column(Text, nullable=False)
    screenplay_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    origin: Mapped[str] = mapped_column(Text, nullable=False)
    completion_status: Mapped[str] = mapped_column(Text, nullable=False)
    generation_run_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    change_note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "adaptation_id", "version_number", name="uq_adaptation_revision_number"
        ),
        UniqueConstraint(
            "id", "adaptation_id", name="uq_adaptation_revision_id_parent"
        ),
        UniqueConstraint(
            "id", "project_id", name="uq_adaptation_revision_id_project"
        ),
    )


class AdaptationGenerationRunRow(Base):
    """Finalize-once AI attempt, including durable adult quarantine."""

    __tablename__ = "adaptation_generation_runs"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    adaptation_id: Mapped[str] = mapped_column(Text, nullable=False)
    project_id: Mapped[str] = mapped_column(Text, nullable=False)
    source_snapshot_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    expected_working_revision_id: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str] = mapped_column(Text, nullable=False)
    brief_json: Mapped[str] = mapped_column(Text, nullable=False)
    options_json: Mapped[str] = mapped_column(Text, nullable=False)
    input_snapshot_json: Mapped[str] = mapped_column(Text, nullable=False)
    input_snapshot_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    participant_manifest_json: Mapped[str] = mapped_column(Text, nullable=False)
    participant_manifest_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    eligibility_evaluation_ids_json: Mapped[str] = mapped_column(Text, nullable=False)
    system_message_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    user_message_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    system_message_byte_size: Mapped[int] = mapped_column(Integer, nullable=False)
    user_message_byte_size: Mapped[int] = mapped_column(Integer, nullable=False)
    rendered_system_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    rendered_user_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    rendered_message_storage_enabled: Mapped[int] = mapped_column(
        Integer, nullable=False
    )
    status: Mapped[str] = mapped_column(Text, nullable=False)
    reason_code: Mapped[str] = mapped_column(Text, nullable=False, default="")
    output_sha256: Mapped[str | None] = mapped_column(Text, nullable=True)
    quarantined_output_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    quarantined_output_sha256: Mapped[str | None] = mapped_column(Text, nullable=True)
    review_evaluation_ids_json: Mapped[str] = mapped_column(
        Text, nullable=False, default="[]"
    )
    reviewed_at: Mapped[str] = mapped_column(Text, nullable=False, default="")
    resulting_revision_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    started_at: Mapped[str] = mapped_column(Text, nullable=False)
    completed_at: Mapped[str] = mapped_column(Text, nullable=False, default="")
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __table_args__ = (
        UniqueConstraint("id", "adaptation_id", name="uq_adaptation_run_id_parent"),
        UniqueConstraint("id", "project_id", name="uq_adaptation_run_id_project"),
    )


class AdaptationAcceptanceEventRow(Base):
    """Immutable receipt for one explicit author acceptance."""

    __tablename__ = "adaptation_acceptance_events"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    adaptation_id: Mapped[str] = mapped_column(Text, nullable=False)
    revision_id: Mapped[str] = mapped_column(Text, nullable=False)
    previous_accepted_revision_id: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )
    project_id: Mapped[str] = mapped_column(Text, nullable=False)
    source_snapshot_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    accepted_at: Mapped[str] = mapped_column(Text, nullable=False)

# ===================================================== Story memory (0006)
class StoryMemoryProposalRow(Base):
    """A draft-bound proposal that only an author can finalize."""

    __tablename__ = "story_memory_proposals"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    project_id: Mapped[str] = mapped_column(Text, nullable=False)
    story_outline_id: Mapped[str] = mapped_column(Text, nullable=False)
    story_scene_id: Mapped[str] = mapped_column(Text, nullable=False)
    scene_draft_id: Mapped[str] = mapped_column(Text, nullable=False)
    base_memory_fingerprint: Mapped[str] = mapped_column(Text, nullable=False)
    proposal_json: Mapped[str] = mapped_column(Text, nullable=False)
    proposal_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    origin: Mapped[str] = mapped_column(Text, nullable=False)
    provider: Mapped[str] = mapped_column(Text, nullable=False, default="")
    model: Mapped[str] = mapped_column(Text, nullable=False, default="")
    contract_version: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    decision_note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    finalized_at: Mapped[str] = mapped_column(Text, nullable=False, default="")

    __table_args__ = (
        UniqueConstraint(
            "id",
            "project_id",
            "story_outline_id",
            "story_scene_id",
            "scene_draft_id",
            name="uq_memory_proposal_full_ownership",
        ),
        CheckConstraint(
            "json_valid(proposal_json) AND json_type(proposal_json) = 'object' "
            "AND json_extract(proposal_json, '$.schema_version') = "
            "'story-memory-v1' "
            "AND json_type(proposal_json, '$.changes') = 'array'",
            name="ck_memory_proposal_json",
        ),
        CheckConstraint(
            "contract_version = 'story-memory-proposal-v1'",
            name="ck_memory_proposal_contract",
        ),
        CheckConstraint(
            "origin IN ('manual','scene_card','llm')",
            name="ck_memory_proposal_origin",
        ),
        CheckConstraint(
            "status IN ('pending','accepted','rejected')",
            name="ck_memory_proposal_status",
        ),
        CheckConstraint(
            "(status = 'pending' AND finalized_at = '' AND decision_note = '') OR "
            "(status IN ('accepted','rejected') AND finalized_at <> '')",
            name="ck_memory_proposal_lifecycle",
        ),
        Index("ix_memory_proposals_scene", "story_scene_id", "status"),
        Index(
            "uq_memory_proposal_one_pending_payload",
            "scene_draft_id",
            "proposal_sha256",
            "base_memory_fingerprint",
            unique=True,
            sqlite_where=sql_text("status = 'pending'"),
        ),
        Index(
            "uq_memory_proposal_one_accepted_per_draft",
            "scene_draft_id",
            unique=True,
            sqlite_where=sql_text("status = 'accepted'"),
        ),
    )


class StoryMemoryEntryRow(Base):
    """One immutable operation in the accepted story-memory log."""

    __tablename__ = "story_memory_entries"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    project_id: Mapped[str] = mapped_column(Text, nullable=False)
    story_outline_id: Mapped[str] = mapped_column(Text, nullable=False)
    story_scene_id: Mapped[str] = mapped_column(Text, nullable=False)
    scene_draft_id: Mapped[str] = mapped_column(Text, nullable=False)
    proposal_id: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    subject_id: Mapped[str] = mapped_column(Text, nullable=False)
    attribute: Mapped[str] = mapped_column(Text, nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False, default="")
    operation: Mapped[str] = mapped_column(Text, nullable=False)
    supersedes_entry_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_excerpt: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "id",
            "project_id",
            "story_outline_id",
            name="uq_memory_entry_outline_ownership",
        ),
        UniqueConstraint(
            "supersedes_entry_id", name="uq_memory_entry_single_replacement"
        ),
        CheckConstraint(
            "kind IN ('fact','timeline','foreshadowing','character_state')",
            name="ck_memory_entry_kind",
        ),
        CheckConstraint(
            "operation IN ('assert','supersede','retract')",
            name="ck_memory_entry_operation",
        ),
        CheckConstraint(
            "(operation = 'assert' AND supersedes_entry_id IS NULL) OR "
            "(operation IN ('supersede','retract') AND supersedes_entry_id IS NOT NULL)",
            name="ck_memory_entry_replacement_contract",
        ),
        CheckConstraint(
            "subject_id <> '' AND attribute <> '' AND created_at <> '' AND "
            "((operation IN ('assert','supersede') AND value <> '') OR "
            "(operation = 'retract' AND value = ''))",
            name="ck_memory_entry_content",
        ),
        Index(
            "ix_memory_entries_outline_identity",
            "story_outline_id",
            "subject_id",
            "attribute",
        ),
        Index("ix_memory_entries_proposal", "proposal_id"),
    )


# ================================= Character biography drafts (0007)
class CharacterBiographyDraftRow(Base):
    """Editable character scratch space; project ownership is optional."""

    __tablename__ = "character_biography_drafts"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    project_id: Mapped[str | None] = mapped_column(
        Text,
        ForeignKey(
            "projects.id",
            name="fk_character_biography_drafts_project",
            ondelete="SET NULL",
        ),
        nullable=True,
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    character_name: Mapped[str] = mapped_column(Text, nullable=False)
    gender: Mapped[str] = mapped_column(Text, nullable=False)
    character_details: Mapped[str] = mapped_column(Text, nullable=False, default="")
    character_image_prompt_en: Mapped[str] = mapped_column(
        Text, nullable=False, default=""
    )
    personal_story: Mapped[str] = mapped_column(Text, nullable=False, default="")
    background_image_prompt_en: Mapped[str] = mapped_column(
        Text, nullable=False, default=""
    )
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(Text, nullable=False, default="active")
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        CheckConstraint(
            "length(trim(title)) > 0 AND length(trim(character_name)) > 0",
            name="ck_character_biography_drafts_required_labels",
        ),
        CheckConstraint(
            "gender IN ('female','male')",
            name="ck_character_biography_drafts_gender",
        ),
        CheckConstraint(
            "status IN ('active','archived')",
            name="ck_character_biography_drafts_status",
        ),
        CheckConstraint(
            "character_image_prompt_en NOT GLOB '*[^ -~]*'",
            name="ck_character_biography_drafts_character_prompt_ascii",
        ),
        CheckConstraint(
            "background_image_prompt_en NOT GLOB '*[^ -~]*'",
            name="ck_character_biography_drafts_background_prompt_ascii",
        ),
        Index(
            "ix_character_biography_drafts_project_status_updated",
            "project_id",
            "status",
            "updated_at",
        ),
    )


# ================================ Prompt scratch drafts (0008)
class PromptScratchDraftRow(Base):
    """Small prompt-only scratch item; neither project nor name is required."""

    __tablename__ = "prompt_scratch_drafts"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    project_id: Mapped[str | None] = mapped_column(
        Text,
        ForeignKey(
            "projects.id",
            name="fk_prompt_scratch_drafts_project",
            ondelete="SET NULL",
        ),
        nullable=True,
    )
    title: Mapped[str] = mapped_column(Text, nullable=False, default="")
    character_name: Mapped[str] = mapped_column(Text, nullable=False, default="")
    character_image_prompt_en: Mapped[str] = mapped_column(
        Text, nullable=False, default=""
    )
    background_image_prompt_en: Mapped[str] = mapped_column(
        Text, nullable=False, default=""
    )
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    editor_kind: Mapped[str] = mapped_column(
        Text, nullable=False, default="character", server_default="character"
    )
    editor_gender: Mapped[str] = mapped_column(
        Text, nullable=False, default="female", server_default="female"
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, default="active")
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        CheckConstraint(
            "length(trim(title)) > 0 OR length(trim(character_name)) > 0 OR "
            "length(trim(character_image_prompt_en)) > 0 OR "
            "length(trim(background_image_prompt_en)) > 0 OR "
            "length(trim(notes)) > 0",
            name="ck_prompt_scratch_drafts_has_content",
        ),
        CheckConstraint(
            "status IN ('active','archived')",
            name="ck_prompt_scratch_drafts_status",
        ),
        CheckConstraint(
            "character_image_prompt_en NOT GLOB '*[^ -~]*'",
            name="ck_prompt_scratch_drafts_character_prompt_ascii",
        ),
        CheckConstraint(
            "background_image_prompt_en NOT GLOB '*[^ -~]*'",
            name="ck_prompt_scratch_drafts_background_prompt_ascii",
        ),
        CheckConstraint(
            "editor_kind IN ('character','background','both')",
            name="ck_prompt_scratch_drafts_editor_kind",
        ),
        CheckConstraint(
            "editor_gender IN ('female','male')",
            name="ck_prompt_scratch_drafts_editor_gender",
        ),
        Index(
            "ix_prompt_scratch_drafts_project_status_updated",
            "project_id",
            "status",
            "updated_at",
        ),
    )


# ======================== Prompt scratch recovery journal (0009)
class PromptScratchRecoveryJournalRow(Base):
    """Crash-safe editor snapshot and terminal idempotency receipt."""

    __tablename__ = "prompt_scratch_recovery_journals"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    intent: Mapped[str] = mapped_column(Text, nullable=False)
    existing_draft_id: Mapped[str | None] = mapped_column(
        Text,
        ForeignKey(
            "prompt_scratch_drafts.id",
            name="fk_prompt_scratch_recovery_existing_draft",
            ondelete="RESTRICT",
        ),
        nullable=True,
    )
    reserved_draft_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    base_updated_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_project_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_link_project: Mapped[int | None] = mapped_column(Integer, nullable=True)
    raw_editor_kind: Mapped[str] = mapped_column(Text, nullable=False)
    raw_editor_gender: Mapped[str] = mapped_column(Text, nullable=False)
    raw_title: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_character_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_character_image_prompt_en: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_background_image_prompt_en: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(
        Text, nullable=False, default="pending", server_default="pending"
    )
    conflict_reason: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=""
    )
    resolution_kind: Mapped[str | None] = mapped_column(Text, nullable=True)
    committed_draft_id: Mapped[str | None] = mapped_column(
        Text,
        ForeignKey(
            "prompt_scratch_drafts.id",
            name="fk_prompt_scratch_recovery_committed_draft",
            ondelete="RESTRICT",
        ),
        nullable=True,
    )
    committed_updated_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolved_from_sequence: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)
    resolved_at: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "(intent = 'new' AND existing_draft_id IS NULL AND "
            "reserved_draft_id IS NOT NULL AND base_updated_at IS NULL) OR "
            "(intent = 'existing' AND existing_draft_id IS NOT NULL AND "
            "reserved_draft_id IS NULL AND base_updated_at IS NOT NULL)",
            name="ck_prompt_scratch_recovery_intent_target",
        ),
        CheckConstraint("sequence >= 1", name="ck_prompt_scratch_recovery_sequence"),
        CheckConstraint(
            "state IN ('pending','conflict','committed','discarded')",
            name="ck_prompt_scratch_recovery_state",
        ),
        CheckConstraint(
            "conflict_reason IN ('','stale_revision','target_missing',"
            "'target_archived','target_id_taken')",
            name="ck_prompt_scratch_recovery_conflict_reason",
        ),
        CheckConstraint(
            "(state = 'committed' AND resolution_kind IN ('target','save_as_new')) OR "
            "(state <> 'committed' AND resolution_kind IS NULL)",
            name="ck_prompt_scratch_recovery_resolution_kind",
        ),
        CheckConstraint(
            "state <> 'committed' OR "
            "(resolution_kind = 'target' AND committed_draft_id = "
            "COALESCE(existing_draft_id,reserved_draft_id)) OR "
            "(resolution_kind = 'save_as_new' AND committed_draft_id <> "
            "COALESCE(existing_draft_id,reserved_draft_id))",
            name="ck_prompt_scratch_recovery_resolution_target",
        ),
        CheckConstraint(
            "length(payload_sha256) = 64 AND "
            "payload_sha256 NOT GLOB '*[^0-9a-f]*'",
            name="ck_prompt_scratch_recovery_sha256",
        ),
        CheckConstraint(
            "raw_editor_kind IN ('character','background','both')",
            name="ck_prompt_scratch_recovery_editor_kind",
        ),
        CheckConstraint(
            "raw_editor_gender IN ('female','male')",
            name="ck_prompt_scratch_recovery_editor_gender",
        ),
        CheckConstraint(
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
        CheckConstraint(
            "(state IN ('committed','discarded')) OR "
            "(length(CAST(COALESCE(raw_project_id,'') AS BLOB)) + "
            "length(CAST(raw_title AS BLOB)) + "
            "length(CAST(raw_character_name AS BLOB)) + "
            "length(CAST(raw_character_image_prompt_en AS BLOB)) + "
            "length(CAST(raw_background_image_prompt_en AS BLOB)) + "
            "length(CAST(raw_notes AS BLOB)) <= 1048576)",
            name="ck_prompt_scratch_recovery_payload_size",
        ),
        CheckConstraint(
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
        Index(
            "ix_prompt_scratch_recovery_state_updated",
            "state",
            "updated_at",
            "id",
        ),
        Index(
            "ix_prompt_scratch_recovery_existing_state",
            "existing_draft_id",
            "state",
        ),
        Index(
            "uq_prompt_scratch_recovery_reserved_draft_id",
            "reserved_draft_id",
            unique=True,
            sqlite_where=sql_text("reserved_draft_id IS NOT NULL"),
        ),
    )


# ================================ Standalone world seed drafts (0011)
class WorldSeedDraftRow(Base):
    """Editable early world material; Project ownership remains optional."""

    __tablename__ = "world_seed_drafts"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    project_id: Mapped[str | None] = mapped_column(
        Text,
        ForeignKey(
            "projects.id",
            name="fk_world_seed_drafts_project",
            ondelete="SET NULL",
        ),
        nullable=True,
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    setting: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=""
    )
    time_period: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=""
    )
    world_rules_json: Mapped[str] = mapped_column(
        Text, nullable=False, default="[]", server_default="[]"
    )
    locations_json: Mapped[str] = mapped_column(
        Text, nullable=False, default="[]", server_default="[]"
    )
    social_context: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=""
    )
    technology_or_magic: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=""
    )
    central_conflict: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=""
    )
    themes_json: Mapped[str] = mapped_column(
        Text, nullable=False, default="[]", server_default="[]"
    )
    generation_mode: Mapped[str] = mapped_column(
        Text, nullable=False, default="manual", server_default="manual"
    )
    generation_seed: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=""
    )
    status: Mapped[str] = mapped_column(
        Text, nullable=False, default="active", server_default="active"
    )
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        CheckConstraint(
            "length(trim(title)) > 0",
            name="ck_world_seed_drafts_title",
        ),
        CheckConstraint(
            "length(trim(setting)) > 0 OR length(trim(time_period)) > 0 OR "
            "json_array_length(world_rules_json) > 0 OR "
            "json_array_length(locations_json) > 0 OR "
            "length(trim(social_context)) > 0 OR "
            "length(trim(technology_or_magic)) > 0 OR "
            "length(trim(central_conflict)) > 0 OR "
            "json_array_length(themes_json) > 0",
            name="ck_world_seed_drafts_has_content",
        ),
        CheckConstraint(
            "json_valid(world_rules_json) AND "
            "json_type(world_rules_json) = 'array' AND "
            "world_rules_json = json(world_rules_json) AND "
            "json_array_length(world_rules_json) <= 24",
            name="ck_world_seed_drafts_rules_json",
        ),
        CheckConstraint(
            "json_valid(locations_json) AND "
            "json_type(locations_json) = 'array' AND "
            "locations_json = json(locations_json) AND "
            "json_array_length(locations_json) <= 24",
            name="ck_world_seed_drafts_locations_json",
        ),
        CheckConstraint(
            "json_valid(themes_json) AND "
            "json_type(themes_json) = 'array' AND "
            "themes_json = json(themes_json) AND "
            "json_array_length(themes_json) <= 24",
            name="ck_world_seed_drafts_themes_json",
        ),
        CheckConstraint(
            "generation_mode IN ('manual','fill_blanks','reroll_all')",
            name="ck_world_seed_drafts_generation_mode",
        ),
        CheckConstraint(
            "(generation_mode = 'manual' AND generation_seed = '') OR "
            "(generation_mode IN ('fill_blanks','reroll_all') AND "
            "length(trim(generation_seed)) > 0)",
            name="ck_world_seed_drafts_generation_seed",
        ),
        CheckConstraint(
            "status IN ('active','archived')",
            name="ck_world_seed_drafts_status",
        ),
        Index(
            "ix_world_seed_drafts_project_status_updated",
            "project_id",
            "status",
            "updated_at",
        ),
    )


# ================================ Standalone story fragment drafts (0013)
class StoryFragmentDraftRow(Base):
    """Mutable fragment scratch-space; never a Story Studio Scene draft."""

    __tablename__ = "story_fragment_drafts"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    project_id: Mapped[str | None] = mapped_column(
        Text,
        ForeignKey(
            "projects.id",
            name="fk_story_fragment_drafts_project",
            ondelete="SET NULL",
        ),
        nullable=True,
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    fragment_kind: Mapped[str] = mapped_column(
        Text, nullable=False, default="narrative", server_default="narrative"
    )
    fragment_text: Mapped[str] = mapped_column(Text, nullable=False)
    context_notes: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=""
    )
    tags_json: Mapped[str] = mapped_column(
        Text, nullable=False, default="[]", server_default="[]"
    )
    generation_mode: Mapped[str] = mapped_column(
        Text, nullable=False, default="manual", server_default="manual"
    )
    generation_seed: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=""
    )
    status: Mapped[str] = mapped_column(
        Text, nullable=False, default="active", server_default="active"
    )
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        CheckConstraint(
            "length(trim(id)) BETWEEN 1 AND 200",
            name="ck_story_fragment_drafts_id",
        ),
        CheckConstraint(
            "project_id IS NULL OR length(trim(project_id)) BETWEEN 1 AND 200",
            name="ck_story_fragment_drafts_project_id",
        ),
        CheckConstraint(
            "length(trim(title)) BETWEEN 1 AND 200",
            name="ck_story_fragment_drafts_title",
        ),
        CheckConstraint(
            "fragment_kind IN ('narrative','dialogue','opening')",
            name="ck_story_fragment_drafts_kind",
        ),
        CheckConstraint(
            "length(fragment_text) <= 500000 AND "
            "length(trim(fragment_text, char(9) || char(10) || char(13) || ' ')) > 0",
            name="ck_story_fragment_drafts_text",
        ),
        CheckConstraint(
            "length(context_notes) <= 4000",
            name="ck_story_fragment_drafts_context",
        ),
        CheckConstraint(
            "json_valid(tags_json) AND json_type(tags_json) = 'array' AND "
            "tags_json = json(tags_json) AND json_array_length(tags_json) <= 24",
            name="ck_story_fragment_drafts_tags_json",
        ),
        CheckConstraint(
            "generation_mode IN ('manual','fill_blanks','reroll_all')",
            name="ck_story_fragment_drafts_generation_mode",
        ),
        CheckConstraint(
            "length(generation_seed) <= 200 AND ("
            "(generation_mode = 'manual' AND generation_seed = '') OR "
            "(generation_mode IN ('fill_blanks','reroll_all') AND "
            "length(trim(generation_seed)) > 0))",
            name="ck_story_fragment_drafts_generation_seed",
        ),
        CheckConstraint(
            "status IN ('active','archived')",
            name="ck_story_fragment_drafts_status",
        ),
        Index(
            "ix_story_fragment_drafts_project_status_updated",
            "project_id",
            "status",
            "updated_at",
        ),
    )
