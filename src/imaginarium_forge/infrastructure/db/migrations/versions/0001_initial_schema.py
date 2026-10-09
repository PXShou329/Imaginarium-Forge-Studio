"""Initial database schema for projects, creative workflows, and local settings.

The baseline includes SQLite ownership checks, immutable version records,
generation lifecycle guards, and credential-free audit records.
"""

from alembic import op

revision = "0001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None

_TABLES = (
    """CREATE TABLE adaptation_acceptance_events (
    id TEXT NOT NULL,
    adaptation_id TEXT NOT NULL,
    revision_id TEXT NOT NULL,
    previous_accepted_revision_id TEXT,
    project_id TEXT NOT NULL,
    source_snapshot_sha256 TEXT NOT NULL,
    accepted_at TEXT NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT fk_adaptation_acceptance_parent_ownership FOREIGN KEY(adaptation_id, project_id) REFERENCES adaptations (id, project_id),
    CONSTRAINT fk_adaptation_acceptance_revision FOREIGN KEY(revision_id, adaptation_id) REFERENCES adaptation_revisions (id, adaptation_id),
    CONSTRAINT fk_adaptation_acceptance_previous_revision FOREIGN KEY(previous_accepted_revision_id, adaptation_id) REFERENCES adaptation_revisions (id, adaptation_id),
    CONSTRAINT ck_adaptation_acceptance_source_sha256 CHECK ((length(source_snapshot_sha256) = 64) AND (source_snapshot_sha256 NOT GLOB '*[^0-9a-f]*')),
    CONSTRAINT ck_adaptation_acceptance_changes_pointer CHECK ((previous_accepted_revision_id IS NULL) OR (previous_accepted_revision_id <> revision_id))
)""",
    """CREATE TABLE "adaptation_generation_runs" (
    id TEXT NOT NULL,
    adaptation_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    source_snapshot_sha256 TEXT NOT NULL,
    expected_working_revision_id TEXT,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    brief_json TEXT NOT NULL,
    options_json TEXT NOT NULL,
    input_snapshot_json TEXT NOT NULL,
    input_snapshot_sha256 TEXT NOT NULL,
    participant_manifest_json TEXT NOT NULL,
    participant_manifest_sha256 TEXT NOT NULL,
    eligibility_evaluation_ids_json TEXT NOT NULL,
    system_message_sha256 TEXT NOT NULL,
    user_message_sha256 TEXT NOT NULL,
    system_message_byte_size INTEGER NOT NULL,
    user_message_byte_size INTEGER NOT NULL,
    rendered_system_message TEXT,
    rendered_user_message TEXT,
    rendered_message_storage_enabled INTEGER NOT NULL,
    status TEXT NOT NULL,
    reason_code TEXT DEFAULT ('') NOT NULL,
    output_sha256 TEXT,
    resulting_revision_id TEXT,
    prompt_tokens INTEGER,
    completion_tokens INTEGER,
    started_at TEXT NOT NULL,
    completed_at TEXT DEFAULT ('') NOT NULL,
    latency_ms INTEGER DEFAULT '0' NOT NULL,
    assurance_json TEXT DEFAULT '{}' NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT ck_adaptation_run_user_sha256 CHECK ((length(user_message_sha256) = 64) AND (user_message_sha256 NOT GLOB '*[^0-9a-f]*')),
    CONSTRAINT ck_adaptation_run_nonnegative_sizes CHECK ((system_message_byte_size >= 0) AND (user_message_byte_size >= 0) AND (latency_ms >= 0)),
    CONSTRAINT fk_adaptation_run_parent_ownership FOREIGN KEY(adaptation_id, project_id) REFERENCES adaptations (id, project_id),
    CONSTRAINT ck_adaptation_run_options_json CHECK ((json_valid(options_json)) AND (json_type(options_json) = 'object')),
    CONSTRAINT uq_adaptation_run_id_parent UNIQUE (id, adaptation_id),
    CONSTRAINT fk_adaptation_run_result_revision FOREIGN KEY(resulting_revision_id, adaptation_id) REFERENCES adaptation_revisions (id, adaptation_id),
    CONSTRAINT ck_adaptation_run_output_sha256 CHECK ((output_sha256 IS NULL) OR ((length(output_sha256) = 64) AND (output_sha256 NOT GLOB '*[^0-9a-f]*'))),
    CONSTRAINT ck_adaptation_run_participant_json CHECK ((json_valid(participant_manifest_json)) AND (json_type(participant_manifest_json) = 'object')),
    CONSTRAINT uq_adaptation_run_id_project UNIQUE (id, project_id),
    CONSTRAINT ck_adaptation_run_participant_sha256 CHECK ((length(participant_manifest_sha256) = 64) AND (participant_manifest_sha256 NOT GLOB '*[^0-9a-f]*')),
    CONSTRAINT ck_adaptation_run_assurance_json CHECK ((length(assurance_json) <= 1000000) AND (json_valid(assurance_json)) AND (json_type(assurance_json) = 'object') AND (assurance_json = json(assurance_json)) AND ((assurance_json = '{}') OR ((json_extract(assurance_json, '$.schema_version') = 'screenplay-prose-adapter-v1') AND (json_extract(assurance_json, '$.selected_candidate') IN ('none','accumulated','condensed')) AND (json_type(assurance_json, '$.selected_reason') = 'text') AND (json_type(assurance_json, '$.domain_complete') IN ('true','false')) AND (json_type(assurance_json, '$.result') = 'object') AND (json_type(assurance_json, '$.result.calls') = 'array') AND (json_array_length(assurance_json, '$.result.calls') >= 1) AND (json_type(assurance_json, '$.result.progress') = 'object') AND (json_type(assurance_json, '$.result.decision') = 'object') AND (((json_extract(assurance_json, '$.selected_candidate') = 'none') AND (json_type(assurance_json, '$.selected_output_sha256') = 'null')) OR ((json_extract(assurance_json, '$.selected_candidate') <> 'none') AND (length(json_extract(assurance_json, '$.selected_output_sha256')) = 64) AND (json_extract(assurance_json, '$.selected_output_sha256') NOT GLOB '*[^0-9a-f]*')))))),
    CONSTRAINT ck_adaptation_run_prompt_tokens CHECK ((prompt_tokens IS NULL) OR (prompt_tokens >= 0)),
    CONSTRAINT ck_adaptation_run_provider CHECK (provider IN ('ollama','openai')),
    CONSTRAINT ck_adaptation_run_brief_json CHECK ((json_valid(brief_json)) AND (json_type(brief_json) = 'object')),
    CONSTRAINT ck_adaptation_run_raw_message_policy CHECK ((rendered_message_storage_enabled = 1) OR ((rendered_system_message IS NULL) AND (rendered_user_message IS NULL))),
    CONSTRAINT ck_adaptation_run_completion_tokens CHECK ((completion_tokens IS NULL) OR (completion_tokens >= 0)),
    CONSTRAINT ck_adaptation_run_reason_code CHECK (reason_code IN ('','provider_authentication','provider_quota','provider_rate_limit','provider_timeout','provider_unavailable','model_not_found','provider_error','invalid_output','provider_refused','source_stale','source_integrity_error','working_head_conflict','length_satisfied','advisory_miss','below_minimum','above_maximum','token_limit_partial','timeout_partial','empty_output','cancelled_by_author','repetition_retry_required','repetition_retry_limit_reached','continuation_limit_reached','attempt_limit_reached','cost_limit_reached','protected_facts_unverified')),
    CONSTRAINT ck_adaptation_run_eligibility_json CHECK ((json_valid(eligibility_evaluation_ids_json)) AND (json_type(eligibility_evaluation_ids_json) = 'array')),
    CONSTRAINT ck_adaptation_run_input_json CHECK ((json_valid(input_snapshot_json)) AND (json_type(input_snapshot_json) = 'object')),
    CONSTRAINT ck_adaptation_run_source_sha256 CHECK ((length(source_snapshot_sha256) = 64) AND (source_snapshot_sha256 NOT GLOB '*[^0-9a-f]*')),
    CONSTRAINT ck_adaptation_run_input_sha256 CHECK ((length(input_snapshot_sha256) = 64) AND (input_snapshot_sha256 NOT GLOB '*[^0-9a-f]*')),
    CONSTRAINT fk_adaptation_run_expected_working FOREIGN KEY(expected_working_revision_id, adaptation_id) REFERENCES adaptation_revisions (id, adaptation_id),
    CONSTRAINT ck_adaptation_run_status CHECK (status IN ('running','succeeded','partial','failed','cancelled')),
    CONSTRAINT ck_adaptation_run_system_sha256 CHECK ((length(system_message_sha256) = 64) AND (system_message_sha256 NOT GLOB '*[^0-9a-f]*')),
    CONSTRAINT ck_adaptation_run_message_storage_bool CHECK (rendered_message_storage_enabled IN (0,1)),
    CONSTRAINT ck_adaptation_run_lifecycle CHECK (
    (status = 'running' AND completed_at = '' AND reason_code = ''
     AND output_sha256 IS NULL AND resulting_revision_id IS NULL AND assurance_json = '{}')
    OR (status = 'failed' AND completed_at <> '' AND reason_code <> ''
        AND output_sha256 IS NULL AND resulting_revision_id IS NULL)
    OR (status = 'cancelled' AND completed_at <> '' AND reason_code = 'cancelled_by_author'
        AND output_sha256 IS NULL AND resulting_revision_id IS NULL AND assurance_json <> '{}'
        AND json_extract(assurance_json, '$.selected_candidate') = 'none')
    OR (status = 'succeeded' AND completed_at <> '' AND reason_code = ''
        AND output_sha256 IS NOT NULL AND resulting_revision_id IS NOT NULL
        AND (assurance_json = '{}' OR json_extract(assurance_json, '$.selected_output_sha256') = output_sha256))
    OR (status = 'partial' AND completed_at <> '' AND reason_code <> ''
        AND output_sha256 IS NOT NULL AND resulting_revision_id IS NOT NULL AND assurance_json <> '{}'
        AND json_extract(assurance_json, '$.selected_output_sha256') = output_sha256)
)
)""",
    """CREATE TABLE adaptation_revisions (
    id TEXT NOT NULL,
    adaptation_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    version_number INTEGER NOT NULL,
    parent_revision_id TEXT,
    screenplay_text TEXT NOT NULL,
    screenplay_sha256 TEXT NOT NULL,
    origin TEXT NOT NULL,
    completion_status TEXT NOT NULL,
    generation_run_id TEXT,
    change_note TEXT DEFAULT '' NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT fk_adaptation_revision_parent_ownership FOREIGN KEY(adaptation_id, project_id) REFERENCES adaptations (id, project_id),
    CONSTRAINT fk_adaptation_revision_lineage FOREIGN KEY(parent_revision_id, adaptation_id) REFERENCES adaptation_revisions (id, adaptation_id),
    CONSTRAINT fk_adaptation_revision_run FOREIGN KEY(generation_run_id, adaptation_id) REFERENCES adaptation_generation_runs (id, adaptation_id),
    CONSTRAINT uq_adaptation_revision_number UNIQUE (adaptation_id, version_number),
    CONSTRAINT uq_adaptation_revision_id_parent UNIQUE (id, adaptation_id),
    CONSTRAINT uq_adaptation_revision_id_project UNIQUE (id, project_id),
    CONSTRAINT ck_adaptation_revision_number CHECK (version_number > 0),
    CONSTRAINT ck_adaptation_revision_origin CHECK (origin IN ('manual','edited','ollama','openai')),
    CONSTRAINT ck_adaptation_revision_completion_status CHECK (completion_status IN ('complete','partial')),
    CONSTRAINT ck_adaptation_revision_nonblank CHECK (length(trim(screenplay_text)) > 0),
    CONSTRAINT ck_adaptation_revision_sha256 CHECK ((length(screenplay_sha256) = 64) AND (screenplay_sha256 NOT GLOB '*[^0-9a-f]*')),
    CONSTRAINT ck_adaptation_revision_origin_run CHECK (((origin IN ('ollama','openai')) AND (generation_run_id IS NOT NULL)) OR ((origin IN ('manual','edited')) AND (generation_run_id IS NULL))),
    CONSTRAINT ck_adaptation_revision_edit_parent CHECK ((origin <> 'edited') OR (parent_revision_id IS NOT NULL)),
    CONSTRAINT ck_adaptation_revision_manual_complete CHECK ((completion_status = 'complete') OR (origin IN ('ollama','openai')))
)""",
    """CREATE TABLE adaptations (
    id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    adaptation_type TEXT NOT NULL,
    title TEXT DEFAULT '' NOT NULL,
    source_outline_id TEXT NOT NULL,
    source_chapter_id TEXT NOT NULL,
    source_scene_id TEXT NOT NULL,
    source_draft_id TEXT NOT NULL,
    source_scene_card_version_id TEXT NOT NULL,
    source_prose_sha256 TEXT NOT NULL,
    source_scene_card_sha256 TEXT NOT NULL,
    participant_manifest_json TEXT NOT NULL,
    participant_manifest_sha256 TEXT NOT NULL,
    source_snapshot_json TEXT NOT NULL,
    source_snapshot_sha256 TEXT NOT NULL,
    content_mode TEXT NOT NULL,
    working_revision_id TEXT,
    accepted_revision_id TEXT,
    supersedes_adaptation_id TEXT,
    lifecycle_status TEXT DEFAULT 'active' NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT fk_adaptation_project FOREIGN KEY(project_id) REFERENCES projects (id),
    CONSTRAINT fk_adaptation_outline_ownership FOREIGN KEY(source_outline_id, project_id) REFERENCES story_outlines (id, project_id),
    CONSTRAINT fk_adaptation_chapter_ownership FOREIGN KEY(source_chapter_id, project_id) REFERENCES story_chapters (id, project_id),
    CONSTRAINT fk_adaptation_exact_chapter_chain FOREIGN KEY(source_chapter_id, project_id, source_outline_id) REFERENCES story_chapters (id, project_id, story_outline_id),
    CONSTRAINT fk_adaptation_scene_ownership FOREIGN KEY(source_scene_id, project_id) REFERENCES story_scenes (id, project_id),
    CONSTRAINT fk_adaptation_exact_scene_chain FOREIGN KEY(source_scene_id, project_id, source_chapter_id) REFERENCES story_scenes (id, project_id, story_chapter_id),
    CONSTRAINT fk_adaptation_draft_scene FOREIGN KEY(source_draft_id, source_scene_id) REFERENCES scene_drafts (id, story_scene_id),
    CONSTRAINT fk_adaptation_draft_project FOREIGN KEY(source_draft_id, project_id) REFERENCES scene_drafts (id, project_id),
    CONSTRAINT fk_adaptation_exact_draft_chain FOREIGN KEY(source_draft_id, project_id, source_scene_id, source_scene_card_version_id) REFERENCES scene_drafts (id, project_id, story_scene_id, scene_card_version_id),
    CONSTRAINT fk_adaptation_card_scene FOREIGN KEY(source_scene_card_version_id, source_scene_id) REFERENCES scene_card_versions (id, story_scene_id),
    CONSTRAINT fk_adaptation_working_revision FOREIGN KEY(working_revision_id, id) REFERENCES adaptation_revisions (id, adaptation_id),
    CONSTRAINT fk_adaptation_accepted_revision FOREIGN KEY(accepted_revision_id, id) REFERENCES adaptation_revisions (id, adaptation_id),
    CONSTRAINT fk_adaptation_supersedes_same_source_scope FOREIGN KEY(supersedes_adaptation_id, project_id, source_scene_id) REFERENCES adaptations (id, project_id, source_scene_id),
    CONSTRAINT uq_adaptation_id_project UNIQUE (id, project_id),
    CONSTRAINT uq_adaptation_id_scene UNIQUE (id, source_scene_id),
    CONSTRAINT uq_adaptation_full_scope UNIQUE (id, project_id, source_scene_id),
    CONSTRAINT ck_adaptation_type CHECK (adaptation_type IN ('screenplay','storyboard','video_plan')),
    CONSTRAINT ck_adaptation_content_mode CHECK (content_mode IN ('general','drama','dark','horror','violent')),
    CONSTRAINT ck_adaptation_lifecycle CHECK (lifecycle_status IN ('active','archived')),
    CONSTRAINT ck_adaptation_participant_manifest_json CHECK ((json_valid(participant_manifest_json)) AND (json_type(participant_manifest_json) = 'object')),
    CONSTRAINT ck_adaptation_source_snapshot_json CHECK ((json_valid(source_snapshot_json)) AND (json_type(source_snapshot_json) = 'object')),
    CONSTRAINT ck_adaptation_source_prose_sha256 CHECK ((length(source_prose_sha256) = 64) AND (source_prose_sha256 NOT GLOB '*[^0-9a-f]*')),
    CONSTRAINT ck_adaptation_source_card_sha256 CHECK ((length(source_scene_card_sha256) = 64) AND (source_scene_card_sha256 NOT GLOB '*[^0-9a-f]*')),
    CONSTRAINT ck_adaptation_participant_sha256 CHECK ((length(participant_manifest_sha256) = 64) AND (participant_manifest_sha256 NOT GLOB '*[^0-9a-f]*')),
    CONSTRAINT ck_adaptation_source_snapshot_sha256 CHECK ((length(source_snapshot_sha256) = 64) AND (source_snapshot_sha256 NOT GLOB '*[^0-9a-f]*'))
)""",
    """CREATE TABLE ai_runtime_preferences (
    id TEXT NOT NULL,
    default_provider TEXT DEFAULT 'offline' NOT NULL,
    openai_model TEXT DEFAULT 'gpt-5.6' NOT NULL,
    ollama_model TEXT DEFAULT '' NOT NULL,
    default_quality_profile TEXT DEFAULT 'standard_draft' NOT NULL,
    default_language TEXT DEFAULT 'zh-TW' NOT NULL,
    fallback_policy TEXT DEFAULT 'fail_closed' NOT NULL,
    remote_provider_enabled INTEGER DEFAULT '0' NOT NULL,
    remote_ack_version TEXT DEFAULT '' NOT NULL,
    streaming_enabled INTEGER DEFAULT '1' NOT NULL,
    auto_continue INTEGER DEFAULT '1' NOT NULL,
    show_token_usage INTEGER DEFAULT '1' NOT NULL,
    task_routes_json TEXT DEFAULT '{}' NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT ck_ai_runtime_preferences_global_id CHECK (id = 'global'),
    CONSTRAINT ck_ai_runtime_preferences_provider CHECK (default_provider IN ('offline','ollama','openai')),
    CONSTRAINT ck_ai_runtime_preferences_openai_model CHECK ((length(openai_model) BETWEEN 1 AND 256) AND (openai_model = trim(openai_model))),
    CONSTRAINT ck_ai_runtime_preferences_ollama_model CHECK ((length(ollama_model) <= 256) AND (ollama_model = trim(ollama_model))),
    CONSTRAINT ck_ai_runtime_preferences_ollama_ready CHECK ((default_provider <> 'ollama') OR (length(ollama_model) > 0)),
    CONSTRAINT ck_ai_runtime_preferences_quality CHECK (default_quality_profile IN ('quick_idea','standard_draft','deep_long_form','polish_review','image_prompt')),
    CONSTRAINT ck_ai_runtime_preferences_language CHECK ((length(default_language) BETWEEN 2 AND 32) AND (default_language = trim(default_language)) AND (default_language NOT GLOB '*[^A-Za-z0-9-]*') AND (default_language NOT LIKE '-%') AND (default_language NOT LIKE '%-') AND (default_language NOT LIKE '%--%')),
    CONSTRAINT ck_ai_runtime_preferences_fallback CHECK (fallback_policy IN ('fail_closed','ollama_then_fail','offline_then_fail')),
    CONSTRAINT ck_ai_runtime_preferences_booleans CHECK ((remote_provider_enabled IN (0,1)) AND (streaming_enabled IN (0,1)) AND (auto_continue IN (0,1)) AND (show_token_usage IN (0,1))),
    CONSTRAINT ck_ai_runtime_preferences_remote_ack CHECK ((length(remote_ack_version) <= 100) AND (remote_ack_version = trim(remote_ack_version)) AND ((remote_provider_enabled = 0) OR (length(remote_ack_version) > 0))),
    CONSTRAINT ck_ai_runtime_preferences_openai_enabled CHECK ((default_provider <> 'openai') OR (remote_provider_enabled = 1)),
    CONSTRAINT ck_ai_runtime_preferences_task_routes_json CHECK ((length(task_routes_json) <= 65536) AND (json_valid(task_routes_json)) AND (json_type(task_routes_json) = 'object') AND (task_routes_json = json(task_routes_json))),
    CONSTRAINT ck_ai_runtime_preferences_timestamps CHECK ((length(trim(created_at)) BETWEEN 1 AND 64) AND (length(trim(updated_at)) BETWEEN 1 AND 64))
)""",
    """CREATE TABLE chapter_plan_versions (
    id TEXT NOT NULL,
    story_chapter_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    version_number INTEGER NOT NULL,
    change_note TEXT DEFAULT '' NOT NULL,
    accepted INTEGER DEFAULT '0' NOT NULL,
    created_at TEXT NOT NULL,
    plan_json TEXT NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT ck_json_cpv_plan CHECK (json_valid(plan_json)),
    CONSTRAINT fk_chapter_plan_versions_parent_ownership FOREIGN KEY(story_chapter_id, project_id) REFERENCES story_chapters (id, project_id),
    CONSTRAINT uq_chapter_plan_versions_parent_version UNIQUE (story_chapter_id, version_number),
    CONSTRAINT uq_chapter_plan_versions_id_project UNIQUE (id, project_id),
    CONSTRAINT uq_chapter_plan_versions_id_parent UNIQUE (id, story_chapter_id),
    CONSTRAINT ck_chapter_plan_versions_accepted_bool CHECK (accepted IN (0,1)),
    CONSTRAINT ck_chapter_plan_versions_version_positive CHECK (version_number > 0)
)""",
    """CREATE TABLE character_biography_drafts (
    id TEXT NOT NULL,
    project_id TEXT,
    title TEXT NOT NULL,
    character_name TEXT NOT NULL,
    gender TEXT NOT NULL,
    character_details TEXT DEFAULT '' NOT NULL,
    character_image_prompt_en TEXT DEFAULT '' NOT NULL,
    personal_story TEXT DEFAULT '' NOT NULL,
    background_image_prompt_en TEXT DEFAULT '' NOT NULL,
    notes TEXT DEFAULT '' NOT NULL,
    status TEXT DEFAULT 'active' NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT fk_character_biography_drafts_project FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE SET NULL,
    CONSTRAINT ck_character_biography_drafts_required_labels CHECK ((length(trim(title)) > 0) AND (length(trim(character_name)) > 0)),
    CONSTRAINT ck_character_biography_drafts_gender CHECK (gender IN ('female','male')),
    CONSTRAINT ck_character_biography_drafts_status CHECK (status IN ('active','archived')),
    CONSTRAINT ck_character_biography_drafts_character_prompt_ascii CHECK (character_image_prompt_en NOT GLOB '*[^ -~]*'),
    CONSTRAINT ck_character_biography_drafts_background_prompt_ascii CHECK (background_image_prompt_en NOT GLOB '*[^ -~]*')
)""",
    """CREATE TABLE character_versions (
    id TEXT NOT NULL,
    character_id TEXT NOT NULL,
    version_number INTEGER NOT NULL,
    visual_dna_json TEXT NOT NULL,
    design_note TEXT DEFAULT '' NOT NULL,
    voice_profile TEXT DEFAULT '' NOT NULL,
    personality_profile TEXT DEFAULT '' NOT NULL,
    change_note TEXT DEFAULT '' NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT fk_versions_character FOREIGN KEY(character_id) REFERENCES characters (id),
    CONSTRAINT uq_versions_number UNIQUE (character_id, version_number),
    CONSTRAINT uq_versions_id_character UNIQUE (id, character_id),
    CONSTRAINT ck_versions_dna_json CHECK (json_valid(visual_dna_json))
)""",
    """CREATE TABLE characters (
    id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    name TEXT NOT NULL,
    character_origin TEXT NOT NULL,
    current_version_id TEXT,
    age_status_json TEXT NOT NULL,
    originality_review_json TEXT,
    source_metadata_json TEXT,
    profile_json TEXT DEFAULT '{}' NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT fk_characters_project FOREIGN KEY(project_id) REFERENCES projects (id),
    CONSTRAINT fk_characters_current_version_ownership FOREIGN KEY(current_version_id, id) REFERENCES character_versions (id, character_id),
    CONSTRAINT ck_characters_origin CHECK (character_origin IN ('original','existing')),
    CONSTRAINT ck_characters_status CHECK (status IN ('active','archived')),
    CONSTRAINT ck_characters_age_json CHECK (json_valid(age_status_json)),
    CONSTRAINT ck_characters_orig_json CHECK ((originality_review_json IS NULL) OR (json_valid(originality_review_json))),
    CONSTRAINT ck_characters_source_json CHECK ((source_metadata_json IS NULL) OR (json_valid(source_metadata_json))),
    CONSTRAINT ck_characters_profile_json CHECK (json_valid(profile_json))
)""",
    """CREATE TABLE checkpoint_registry (
    id TEXT NOT NULL,
    path TEXT NOT NULL,
    filename TEXT NOT NULL,
    extension TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    modified_at TEXT NOT NULL,
    modified_at_ns INTEGER DEFAULT '0' NOT NULL,
    sha256 TEXT DEFAULT '' NOT NULL,
    hash_cached_at TEXT DEFAULT '' NOT NULL,
    status TEXT DEFAULT 'unidentified' NOT NULL,
    display_name TEXT DEFAULT '' NOT NULL,
    base_model_hint TEXT DEFAULT '' NOT NULL,
    metadata_source TEXT DEFAULT 'unknown' NOT NULL,
    assigned_profile_id TEXT DEFAULT '' NOT NULL,
    usage_status TEXT DEFAULT '' NOT NULL,
    sha256_status TEXT DEFAULT 'not_computed' NOT NULL,
    local_metadata_json TEXT DEFAULT '{}' NOT NULL,
    last_scanned_at TEXT DEFAULT '' NOT NULL,
    availability TEXT DEFAULT 'present' NOT NULL,
    notes TEXT DEFAULT '' NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_ckpt_path UNIQUE (path),
    CONSTRAINT ck_json_ckpt_metadata CHECK (json_valid(local_metadata_json)),
    CONSTRAINT ck_ckpt_status CHECK (status IN ('unidentified','identified_but_untested','testing','recommended_profile','personal_favorite','unsuitable')),
    CONSTRAINT ck_ckpt_meta_source CHECK (metadata_source IN ('manual','safetensors_metadata','sidecar_json','filename_inference','unknown')),
    CONSTRAINT ck_ckpt_extension CHECK (extension IN ('.safetensors','.ckpt')),
    CONSTRAINT ck_ckpt_availability CHECK (availability IN ('present','missing','changed'))
)""",
    """CREATE TABLE creative_candidate_actions (
    id TEXT NOT NULL,
    candidate_id TEXT NOT NULL,
    action_kind TEXT NOT NULL,
    status TEXT DEFAULT 'pending' NOT NULL,
    idempotency_key TEXT NOT NULL,
    target_type TEXT NOT NULL,
    target_id TEXT,
    selection_json TEXT DEFAULT '{}' NOT NULL,
    before_snapshot_json TEXT NOT NULL,
    before_snapshot_sha256 TEXT NOT NULL,
    after_snapshot_json TEXT,
    after_snapshot_sha256 TEXT DEFAULT '' NOT NULL,
    base_version_ref TEXT,
    result_version_ref TEXT,
    undo_of_action_id TEXT,
    error_code TEXT DEFAULT '' NOT NULL,
    safe_error_message TEXT DEFAULT '' NOT NULL,
    created_at TEXT NOT NULL,
    completed_at TEXT,
    PRIMARY KEY (id),
    CONSTRAINT fk_creative_candidate_actions_candidate FOREIGN KEY(candidate_id) REFERENCES creative_generation_candidates (id) ON DELETE RESTRICT,
    CONSTRAINT fk_creative_candidate_actions_undo FOREIGN KEY(undo_of_action_id) REFERENCES creative_candidate_actions (id) ON DELETE RESTRICT,
    CONSTRAINT uq_creative_candidate_actions_idempotency UNIQUE (idempotency_key),
    CONSTRAINT ck_creative_candidate_actions_ids CHECK ((length(trim(id)) BETWEEN 1 AND 200) AND (length(trim(candidate_id)) BETWEEN 1 AND 200) AND (length(trim(idempotency_key)) BETWEEN 1 AND 200)),
    CONSTRAINT ck_creative_candidate_actions_kind_status CHECK ((action_kind IN ('apply_all','apply_field','apply_selection','save_as_new','undo')) AND (status IN ('pending','completed','failed'))),
    CONSTRAINT ck_creative_candidate_actions_references CHECK ((length(trim(target_type)) BETWEEN 1 AND 100) AND ((target_id IS NULL) OR (length(trim(target_id)) BETWEEN 1 AND 200)) AND ((base_version_ref IS NULL) OR (length(trim(base_version_ref)) BETWEEN 1 AND 200)) AND ((result_version_ref IS NULL) OR (length(trim(result_version_ref)) BETWEEN 1 AND 200)) AND ((undo_of_action_id IS NULL) OR (length(trim(undo_of_action_id)) BETWEEN 1 AND 200))),
    CONSTRAINT ck_creative_candidate_actions_required_json CHECK ((length(selection_json) <= 1000000) AND (json_valid(selection_json)) AND (json_type(selection_json) = 'object') AND (selection_json = json(selection_json)) AND (length(before_snapshot_json) <= 4000000) AND (json_valid(before_snapshot_json)) AND (json_type(before_snapshot_json) = 'object') AND (before_snapshot_json = json(before_snapshot_json))),
    CONSTRAINT ck_creative_candidate_actions_after_json CHECK ((after_snapshot_json IS NULL) OR ((length(after_snapshot_json) <= 4000000) AND (json_valid(after_snapshot_json)) AND (json_type(after_snapshot_json) = 'object') AND (after_snapshot_json = json(after_snapshot_json)))),
    CONSTRAINT ck_creative_candidate_actions_hashes CHECK ((length(before_snapshot_sha256) = 64) AND (before_snapshot_sha256 NOT GLOB '*[^0-9a-f]*') AND ((after_snapshot_sha256 = '') OR ((length(after_snapshot_sha256) = 64) AND (after_snapshot_sha256 NOT GLOB '*[^0-9a-f]*')))),
    CONSTRAINT ck_creative_candidate_actions_scope CHECK ((((action_kind = 'undo') AND (undo_of_action_id IS NOT NULL)) OR ((action_kind <> 'undo') AND (undo_of_action_id IS NULL))) AND (((action_kind IN ('apply_field','apply_selection')) AND (selection_json <> '{}')) OR ((action_kind NOT IN ('apply_field','apply_selection')) AND (selection_json = '{}')))),
    CONSTRAINT ck_creative_candidate_actions_lifecycle CHECK ((length(error_code) <= 100) AND (length(safe_error_message) <= 1000) AND (((status = 'pending') AND (completed_at IS NULL) AND (after_snapshot_json IS NULL) AND (after_snapshot_sha256 = '') AND (error_code = '') AND (safe_error_message = '')) OR ((status = 'completed') AND (completed_at IS NOT NULL) AND (after_snapshot_json IS NOT NULL) AND (length(after_snapshot_sha256) = 64) AND (result_version_ref IS NOT NULL) AND (error_code = '') AND (safe_error_message = '')) OR ((status = 'failed') AND (completed_at IS NOT NULL) AND (after_snapshot_json IS NULL) AND (after_snapshot_sha256 = '') AND (length(error_code) > 0) AND (length(safe_error_message) > 0)))),
    CONSTRAINT ck_creative_candidate_actions_timestamps CHECK (length(trim(created_at)) BETWEEN 1 AND 64)
)""",
    """CREATE TABLE creative_generation_candidates (
    id TEXT NOT NULL,
    job_id TEXT NOT NULL,
    producing_step_id TEXT NOT NULL,
    artifact_kind TEXT NOT NULL,
    target_type TEXT NOT NULL,
    target_id TEXT,
    parent_candidate_id TEXT,
    candidate_version INTEGER NOT NULL,
    original_version_ref TEXT,
    base_snapshot_json TEXT NOT NULL,
    base_snapshot_sha256 TEXT NOT NULL,
    content_text TEXT DEFAULT '' NOT NULL,
    content_json TEXT,
    content_sha256 TEXT NOT NULL,
    quality_report_json TEXT NOT NULL,
    provenance_json TEXT NOT NULL,
    status TEXT DEFAULT 'proposed' NOT NULL,
    state_version INTEGER DEFAULT '1' NOT NULL,
    created_at TEXT NOT NULL,
    decided_at TEXT,
    PRIMARY KEY (id),
    CONSTRAINT fk_creative_generation_candidates_job FOREIGN KEY(job_id) REFERENCES creative_generation_jobs (id) ON DELETE RESTRICT,
    CONSTRAINT fk_creative_generation_candidates_step FOREIGN KEY(producing_step_id) REFERENCES creative_generation_steps (id) ON DELETE RESTRICT,
    CONSTRAINT fk_creative_generation_candidates_parent FOREIGN KEY(parent_candidate_id) REFERENCES creative_generation_candidates (id) ON DELETE RESTRICT,
    CONSTRAINT uq_creative_generation_candidates_job_version UNIQUE (job_id, candidate_version),
    CONSTRAINT ck_creative_generation_candidates_ids CHECK ((length(trim(id)) BETWEEN 1 AND 200) AND (length(trim(job_id)) BETWEEN 1 AND 200) AND (length(trim(producing_step_id)) BETWEEN 1 AND 200)),
    CONSTRAINT ck_creative_generation_candidates_artifact CHECK (artifact_kind IN ('story_fragment','short_story','long_story','chapter','screenplay','character_card','character_backstory','world_seed','image_prompt')),
    CONSTRAINT ck_creative_generation_candidates_references CHECK ((length(trim(target_type)) BETWEEN 1 AND 100) AND ((target_id IS NULL) OR (length(trim(target_id)) BETWEEN 1 AND 200)) AND ((parent_candidate_id IS NULL) OR (length(trim(parent_candidate_id)) BETWEEN 1 AND 200)) AND ((original_version_ref IS NULL) OR (length(trim(original_version_ref)) BETWEEN 1 AND 200))),
    CONSTRAINT ck_creative_generation_candidates_versions CHECK ((candidate_version >= 1) AND (state_version >= 1)),
    CONSTRAINT ck_creative_generation_candidates_base_json CHECK ((length(base_snapshot_json) <= 4000000) AND (json_valid(base_snapshot_json)) AND (json_type(base_snapshot_json) = 'object') AND (base_snapshot_json = json(base_snapshot_json))),
    CONSTRAINT ck_creative_generation_candidates_content_json CHECK ((content_json IS NULL) OR ((length(content_json) <= 4000000) AND (json_valid(content_json)) AND (json_type(content_json) = 'object') AND (content_json = json(content_json)))),
    CONSTRAINT ck_creative_generation_candidates_metadata_json CHECK ((length(quality_report_json) <= 1000000) AND (json_valid(quality_report_json)) AND (json_type(quality_report_json) = 'object') AND (quality_report_json = json(quality_report_json)) AND (length(provenance_json) <= 1000000) AND (json_valid(provenance_json)) AND (json_type(provenance_json) = 'object') AND (provenance_json = json(provenance_json))),
    CONSTRAINT ck_creative_generation_candidates_hashes CHECK ((length(base_snapshot_sha256) = 64) AND (base_snapshot_sha256 NOT GLOB '*[^0-9a-f]*') AND (length(content_sha256) = 64) AND (content_sha256 NOT GLOB '*[^0-9a-f]*')),
    CONSTRAINT ck_creative_generation_candidates_content CHECK ((length(content_text) > 0) OR (content_json IS NOT NULL)),
    CONSTRAINT ck_creative_generation_candidates_status CHECK ((status IN ('proposed','applied','rejected','archived')) AND (((status = 'proposed') AND (decided_at IS NULL)) OR ((status <> 'proposed') AND (decided_at IS NOT NULL)))),
    CONSTRAINT ck_creative_generation_candidates_timestamps CHECK (length(trim(created_at)) BETWEEN 1 AND 64)
)""",
    """CREATE TABLE creative_generation_jobs (
    id TEXT NOT NULL,
    artifact_kind TEXT NOT NULL,
    operation TEXT NOT NULL,
    target_type TEXT NOT NULL,
    target_id TEXT,
    status TEXT DEFAULT 'queued' NOT NULL,
    resume_phase TEXT,
    route_json TEXT NOT NULL,
    quality_profile TEXT NOT NULL,
    length_spec_json TEXT NOT NULL,
    source_snapshot_json TEXT NOT NULL,
    source_snapshot_sha256 TEXT NOT NULL,
    context_snapshot_json TEXT NOT NULL,
    context_snapshot_sha256 TEXT NOT NULL,
    workflow_version TEXT NOT NULL,
    current_step_index INTEGER DEFAULT '0' NOT NULL,
    total_steps_hint INTEGER,
    last_checkpoint_step_id TEXT,
    checkpoint_sequence INTEGER DEFAULT '0' NOT NULL,
    pause_requested INTEGER DEFAULT '0' NOT NULL,
    cancel_requested INTEGER DEFAULT '0' NOT NULL,
    resume_requested INTEGER DEFAULT '0' NOT NULL,
    recovery_count INTEGER DEFAULT '0' NOT NULL,
    state_version INTEGER DEFAULT '1' NOT NULL,
    error_code TEXT DEFAULT '' NOT NULL,
    safe_error_message TEXT DEFAULT '' NOT NULL,
    created_at TEXT NOT NULL,
    started_at TEXT,
    updated_at TEXT NOT NULL,
    heartbeat_at TEXT,
    last_checkpoint_at TEXT,
    completed_at TEXT,
    PRIMARY KEY (id),
    CONSTRAINT ck_creative_generation_jobs_id CHECK (length(trim(id)) BETWEEN 1 AND 200),
    CONSTRAINT ck_creative_generation_jobs_artifact CHECK (artifact_kind IN ('story_fragment','short_story','long_story','chapter','screenplay','character_card','character_backstory','world_seed','image_prompt')),
    CONSTRAINT ck_creative_generation_jobs_operation CHECK (operation IN ('generate','continue','fill_blanks','expand','rewrite','polish','condense','critique','fix_continuity','improve_dialogue','adapt')),
    CONSTRAINT ck_creative_generation_jobs_target CHECK ((length(trim(target_type)) BETWEEN 1 AND 100) AND ((target_id IS NULL) OR (length(trim(target_id)) BETWEEN 1 AND 200))),
    CONSTRAINT ck_creative_generation_jobs_status CHECK ((status IN ('queued','normalizing_brief','planning','drafting','reviewing','revising','assembling','completed','paused','cancelled','failed','needs_author_input')) AND ((resume_phase IS NULL) OR (resume_phase IN ('normalizing_brief','planning','drafting','reviewing','revising','assembling')))),
    CONSTRAINT ck_creative_generation_jobs_route_json CHECK ((length(route_json) <= 65536) AND (json_valid(route_json)) AND (json_type(route_json) = 'object') AND (route_json = json(route_json))),
    CONSTRAINT ck_creative_generation_jobs_labels CHECK ((length(trim(quality_profile)) BETWEEN 1 AND 100) AND (length(trim(workflow_version)) BETWEEN 1 AND 100)),
    CONSTRAINT ck_creative_generation_jobs_length_spec_json CHECK ((length(length_spec_json) <= 65536) AND (json_valid(length_spec_json)) AND (json_type(length_spec_json) = 'object') AND (length_spec_json = json(length_spec_json))),
    CONSTRAINT ck_creative_generation_jobs_source_json CHECK ((length(source_snapshot_json) <= 4000000) AND (json_valid(source_snapshot_json)) AND (json_type(source_snapshot_json) = 'object') AND (source_snapshot_json = json(source_snapshot_json))),
    CONSTRAINT ck_creative_generation_jobs_context_json CHECK ((length(context_snapshot_json) <= 4000000) AND (json_valid(context_snapshot_json)) AND (json_type(context_snapshot_json) = 'object') AND (context_snapshot_json = json(context_snapshot_json))),
    CONSTRAINT ck_creative_generation_jobs_hashes CHECK ((length(source_snapshot_sha256) = 64) AND (source_snapshot_sha256 NOT GLOB '*[^0-9a-f]*') AND (length(context_snapshot_sha256) = 64) AND (context_snapshot_sha256 NOT GLOB '*[^0-9a-f]*')),
    CONSTRAINT ck_creative_generation_jobs_counters CHECK ((current_step_index >= 0) AND (recovery_count >= 0) AND (state_version >= 1) AND ((total_steps_hint IS NULL) OR ((total_steps_hint >= 1) AND (current_step_index <= total_steps_hint)))),
    CONSTRAINT ck_creative_generation_jobs_checkpoint CHECK ((checkpoint_sequence >= 0) AND (((last_checkpoint_step_id IS NULL) AND (checkpoint_sequence = 0) AND (last_checkpoint_at IS NULL)) OR ((length(trim(last_checkpoint_step_id)) BETWEEN 1 AND 200) AND (checkpoint_sequence >= 1) AND (last_checkpoint_at IS NOT NULL)))),
    CONSTRAINT ck_creative_generation_jobs_controls CHECK ((pause_requested IN (0,1)) AND (cancel_requested IN (0,1)) AND (resume_requested IN (0,1)) AND (pause_requested + cancel_requested + resume_requested <= 1)),
    CONSTRAINT ck_creative_generation_jobs_error CHECK ((length(error_code) <= 100) AND (length(safe_error_message) <= 1000) AND (((status = 'failed') AND (completed_at IS NOT NULL) AND (length(error_code) > 0) AND (length(safe_error_message) > 0) AND (resume_phase IN ('normalizing_brief','planning','drafting','reviewing','revising','assembling'))) OR ((status <> 'failed') AND (error_code = '') AND (safe_error_message = '')))),
    CONSTRAINT ck_creative_generation_jobs_lifecycle CHECK (((status = 'queued') AND (started_at IS NULL) AND (completed_at IS NULL)) OR ((status IN ('normalizing_brief','planning','drafting','reviewing','revising','assembling')) AND (started_at IS NOT NULL) AND (completed_at IS NULL)) OR ((status = 'paused') AND (started_at IS NOT NULL) AND (completed_at IS NULL) AND (resume_phase IN ('normalizing_brief','planning','drafting','reviewing','revising','assembling')) AND ((pause_requested = 1) OR (resume_requested = 1))) OR ((status = 'needs_author_input') AND (started_at IS NOT NULL) AND (completed_at IS NULL) AND (resume_phase IN ('normalizing_brief','planning','drafting','reviewing','revising','assembling'))) OR ((status = 'completed') AND (started_at IS NOT NULL) AND (completed_at IS NOT NULL) AND (pause_requested = 0) AND (cancel_requested = 0) AND (resume_requested = 0)) OR ((status = 'cancelled') AND (completed_at IS NOT NULL) AND (cancel_requested = 1)) OR ((status = 'failed') AND (completed_at IS NOT NULL))),
    CONSTRAINT ck_creative_generation_jobs_timestamps CHECK ((length(trim(created_at)) BETWEEN 1 AND 64) AND (length(trim(updated_at)) BETWEEN 1 AND 64))
)""",
    """CREATE TABLE creative_generation_steps (
    id TEXT NOT NULL,
    job_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    step_kind TEXT NOT NULL,
    status TEXT DEFAULT 'pending' NOT NULL,
    idempotency_key TEXT NOT NULL,
    template_id TEXT NOT NULL,
    template_version TEXT NOT NULL,
    requested_provider TEXT,
    requested_model TEXT,
    provider TEXT,
    model TEXT,
    fallback_used INTEGER DEFAULT '0' NOT NULL,
    input_snapshot_json TEXT NOT NULL,
    input_snapshot_sha256 TEXT NOT NULL,
    output_text TEXT DEFAULT '' NOT NULL,
    output_json TEXT,
    output_sha256 TEXT DEFAULT '' NOT NULL,
    partial INTEGER DEFAULT '0' NOT NULL,
    partial_text TEXT DEFAULT '' NOT NULL,
    partial_sha256 TEXT DEFAULT '' NOT NULL,
    stable_boundary_offset INTEGER DEFAULT '0' NOT NULL,
    finish_reason TEXT DEFAULT '' NOT NULL,
    checkpoint_index INTEGER DEFAULT '0' NOT NULL,
    prompt_tokens INTEGER,
    completion_tokens INTEGER,
    latency_ms INTEGER,
    attempts INTEGER DEFAULT '0' NOT NULL,
    max_attempts INTEGER DEFAULT '2' NOT NULL,
    attempt_history_json TEXT DEFAULT '[]' NOT NULL,
    error_code TEXT DEFAULT '' NOT NULL,
    safe_error_message TEXT DEFAULT '' NOT NULL,
    state_version INTEGER DEFAULT '1' NOT NULL,
    created_at TEXT NOT NULL,
    started_at TEXT,
    updated_at TEXT NOT NULL,
    checkpoint_at TEXT,
    interrupted_at TEXT,
    completed_at TEXT,
    PRIMARY KEY (id),
    CONSTRAINT fk_creative_generation_steps_job FOREIGN KEY(job_id) REFERENCES creative_generation_jobs (id) ON DELETE RESTRICT,
    CONSTRAINT uq_creative_generation_steps_job_ordinal UNIQUE (job_id, ordinal),
    CONSTRAINT uq_creative_generation_steps_idempotency UNIQUE (idempotency_key),
    CONSTRAINT ck_creative_generation_steps_ids CHECK ((length(trim(id)) BETWEEN 1 AND 200) AND (length(trim(job_id)) BETWEEN 1 AND 200) AND (length(trim(idempotency_key)) BETWEEN 1 AND 200)),
    CONSTRAINT ck_creative_generation_steps_kind_status CHECK ((step_kind IN ('brief_normalization','story_plan','character_arc_plan','chapter_or_scene_plan','prose_chunk','rolling_summary','continuity_check','targeted_revision','length_assurance','final_assembly','final_quality_check')) AND (status IN ('pending','running','completed','interrupted','paused','cancelled','failed','needs_author_input'))),
    CONSTRAINT ck_creative_generation_steps_counters CHECK ((ordinal >= 0) AND (attempts >= 0) AND (max_attempts BETWEEN 1 AND 10) AND (attempts <= max_attempts) AND (state_version >= 1)),
    CONSTRAINT ck_creative_generation_steps_template CHECK ((length(trim(template_id)) BETWEEN 1 AND 200) AND (length(trim(template_version)) BETWEEN 1 AND 200)),
    CONSTRAINT ck_creative_generation_steps_route CHECK (((requested_provider IS NULL) = (requested_model IS NULL)) AND ((provider IS NULL) = (model IS NULL)) AND (fallback_used IN (0,1)) AND ((fallback_used = 0) OR ((requested_provider IS NOT NULL) AND (provider IS NOT NULL) AND ((requested_provider <> provider) OR (requested_model <> model))))),
    CONSTRAINT ck_creative_generation_steps_input_json CHECK ((length(input_snapshot_json) <= 4000000) AND (json_valid(input_snapshot_json)) AND (json_type(input_snapshot_json) = 'object') AND (input_snapshot_json = json(input_snapshot_json))),
    CONSTRAINT ck_creative_generation_steps_output_json CHECK ((output_json IS NULL) OR ((length(output_json) <= 4000000) AND (json_valid(output_json)) AND (json_type(output_json) = 'object') AND (output_json = json(output_json)))),
    CONSTRAINT ck_creative_generation_steps_attempts_json CHECK ((length(attempt_history_json) <= 1000000) AND (json_valid(attempt_history_json)) AND (json_type(attempt_history_json) = 'array') AND (attempt_history_json = json(attempt_history_json)) AND (attempts = json_array_length(attempt_history_json))),
    CONSTRAINT ck_creative_generation_steps_hashes CHECK ((length(input_snapshot_sha256) = 64) AND (input_snapshot_sha256 NOT GLOB '*[^0-9a-f]*') AND ((output_sha256 = '') OR ((length(output_sha256) = 64) AND (output_sha256 NOT GLOB '*[^0-9a-f]*'))) AND ((partial_sha256 = '') OR ((length(partial_sha256) = 64) AND (partial_sha256 NOT GLOB '*[^0-9a-f]*')))),
    CONSTRAINT ck_creative_generation_steps_partial CHECK ((partial IN (0,1)) AND (stable_boundary_offset >= 0) AND (((partial = 0) AND (partial_text = '') AND (partial_sha256 = '') AND (stable_boundary_offset = 0)) OR ((partial = 1) AND (length(partial_text) > 0) AND (length(partial_sha256) = 64) AND (stable_boundary_offset <= length(partial_text))))),
    CONSTRAINT ck_creative_generation_steps_checkpoint CHECK ((checkpoint_index >= 0) AND (((checkpoint_index = 0) AND (checkpoint_at IS NULL)) OR ((checkpoint_index >= 1) AND (checkpoint_at IS NOT NULL)))),
    CONSTRAINT ck_creative_generation_steps_usage CHECK (((prompt_tokens IS NULL) OR (prompt_tokens >= 0)) AND ((completion_tokens IS NULL) OR (completion_tokens >= 0)) AND ((latency_ms IS NULL) OR (latency_ms >= 0)) AND (length(finish_reason) <= 100)),
    CONSTRAINT ck_creative_generation_steps_error CHECK ((length(error_code) <= 100) AND (length(safe_error_message) <= 1000) AND (((status = 'failed') AND (completed_at IS NOT NULL) AND (length(error_code) > 0) AND (length(safe_error_message) > 0)) OR ((status <> 'failed') AND (error_code = '') AND (safe_error_message = '')))),
    CONSTRAINT ck_creative_generation_steps_lifecycle CHECK (((status = 'pending') AND (started_at IS NULL) AND (completed_at IS NULL) AND (attempts = 0) AND (output_text = '') AND (output_json IS NULL) AND (output_sha256 = '') AND (partial = 0)) OR ((status = 'running') AND (started_at IS NOT NULL) AND (completed_at IS NULL)) OR ((status = 'completed') AND (started_at IS NOT NULL) AND (completed_at IS NOT NULL) AND (partial = 0) AND (interrupted_at IS NULL) AND (provider IS NOT NULL) AND (model IS NOT NULL) AND ((length(output_text) > 0) OR (output_json IS NOT NULL)) AND (length(output_sha256) = 64)) OR ((status = 'interrupted') AND (started_at IS NOT NULL) AND (interrupted_at IS NOT NULL) AND (completed_at IS NULL)) OR ((status IN ('paused','needs_author_input')) AND (started_at IS NOT NULL) AND (completed_at IS NULL)) OR ((status IN ('cancelled','failed')) AND (completed_at IS NOT NULL))),
    CONSTRAINT ck_creative_generation_steps_timestamps CHECK ((length(trim(created_at)) BETWEEN 1 AND 64) AND (length(trim(updated_at)) BETWEEN 1 AND 64))
)""",
    """CREATE TABLE creative_unit_author_decisions (
    id TEXT PRIMARY KEY NOT NULL CHECK(length(trim(id)) BETWEEN 1 AND 200),
    job_id TEXT NOT NULL REFERENCES creative_generation_jobs(id) ON DELETE RESTRICT,
    step_id TEXT NOT NULL REFERENCES creative_generation_steps(id) ON DELETE RESTRICT,
    unit_id TEXT NOT NULL CHECK(length(trim(unit_id)) BETWEEN 1 AND 200),
    sequence INTEGER NOT NULL CHECK(typeof(sequence) = 'integer' AND sequence >= 1),
    kind TEXT NOT NULL CHECK(kind IN ('grant_continuation', 'adjust_target')),
    command_sha256 TEXT NOT NULL CHECK(
                length(command_sha256) = 64 AND command_sha256 NOT GLOB '*[^0-9a-f]*'
            ),
    payload_json TEXT NOT NULL CHECK(
                length(payload_json) <= 4000000 AND json_valid(payload_json)
                AND json_type(payload_json) = 'object' AND payload_json = json(payload_json)
            ),
    payload_sha256 TEXT NOT NULL CHECK(
                length(payload_sha256) = 64 AND payload_sha256 NOT GLOB '*[^0-9a-f]*'
            ),
    created_at TEXT NOT NULL CHECK(length(trim(created_at)) BETWEEN 1 AND 100),
    UNIQUE(job_id, sequence)
)""",
    """CREATE TABLE generation_runs (
    id TEXT NOT NULL,
    story_scene_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    run_kind TEXT DEFAULT 'generation' NOT NULL,
    provider TEXT DEFAULT '' NOT NULL,
    model TEXT DEFAULT '' NOT NULL,
    options_snapshot_json TEXT DEFAULT '{}' NOT NULL,
    options_snapshot_sha256 TEXT DEFAULT '' NOT NULL,
    context_fingerprint TEXT DEFAULT '' NOT NULL,
    planning_chain_fingerprint TEXT DEFAULT '' NOT NULL,
    planning_mode TEXT DEFAULT 'accepted' NOT NULL,
    preview_warning_acknowledged INTEGER DEFAULT '0' NOT NULL,
    renderer_version TEXT DEFAULT '' NOT NULL,
    context_schema_version TEXT DEFAULT '' NOT NULL,
    context_contract_version TEXT DEFAULT '' NOT NULL,
    context_budget_policy_version TEXT DEFAULT '' NOT NULL,
    eligibility_fingerprint TEXT DEFAULT '' NOT NULL,
    eligibility_evaluation_ids_json TEXT DEFAULT '[]' NOT NULL,
    input_snapshot_json TEXT DEFAULT '{}' NOT NULL,
    input_snapshot_sha256 TEXT DEFAULT '' NOT NULL,
    requirement_version_id TEXT,
    bible_version_id TEXT,
    outline_version_id TEXT,
    chapter_plan_version_id TEXT,
    scene_card_version_id TEXT NOT NULL,
    pov_character_version_id TEXT,
    character_version_ids_json TEXT DEFAULT '[]' NOT NULL,
    parent_draft_id TEXT,
    revision_request_fingerprint TEXT DEFAULT '' NOT NULL,
    rebased_from_generation_run_id TEXT,
    rebase_reason TEXT DEFAULT '' NOT NULL,
    is_recovery_rebase INTEGER DEFAULT '0' NOT NULL,
    missing_source_version_ids_json TEXT DEFAULT '[]' NOT NULL,
    system_message_sha256 TEXT DEFAULT '' NOT NULL,
    user_message_sha256 TEXT DEFAULT '' NOT NULL,
    system_message_byte_size INTEGER DEFAULT '0' NOT NULL,
    user_message_byte_size INTEGER DEFAULT '0' NOT NULL,
    rendered_system_message TEXT,
    rendered_user_message TEXT,
    rendered_message_storage_enabled INTEGER DEFAULT '1' NOT NULL,
    content_mode TEXT DEFAULT 'general' NOT NULL,
    status TEXT DEFAULT 'running' NOT NULL,
    reason_code TEXT DEFAULT '' NOT NULL,
    error_reason TEXT DEFAULT '' NOT NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT DEFAULT '' NOT NULL,
    latency_ms INTEGER DEFAULT '0' NOT NULL,
    length_assurance_json TEXT NOT NULL DEFAULT '{}'
        CONSTRAINT ck_generation_run_length_assurance_json CHECK ((length(length_assurance_json) <= 1000000) AND (json_valid(length_assurance_json)) AND (json_type(length_assurance_json) = 'object') AND (length_assurance_json = json(length_assurance_json)) AND ((length_assurance_json = '{}') OR ((json_extract(length_assurance_json, '$.policy_version') =
                        'story-length-assurance-v1') AND (json_type(length_assurance_json, '$.length_spec') = 'object') AND (length(
                        json_extract(
                            length_assurance_json,
                            '$.length_spec_sha256'
                        )
                    ) = 64) AND (json_extract(
                        length_assurance_json,
                        '$.length_spec_sha256'
                    ) NOT GLOB '*[^0-9a-f]*') AND (json_type(
                        length_assurance_json,
                        '$.logical_provider_attempts'
                    ) = 'integer') AND (json_extract(
                        length_assurance_json,
                        '$.logical_provider_attempts'
                    ) >= 0) AND (json_type(
                        length_assurance_json,
                        '$.logical_provider_attempt_ceiling'
                    ) = 'integer') AND (json_extract(
                        length_assurance_json,
                        '$.logical_provider_attempt_ceiling'
                    ) >= 1) AND (json_extract(
                        length_assurance_json,
                        '$.logical_provider_attempts'
                    ) <= json_extract(
                        length_assurance_json,
                        '$.logical_provider_attempt_ceiling'
                    )) AND (json_type(
                        length_assurance_json,
                        '$.runtime_provider_attempts'
                    ) = 'integer') AND (json_extract(
                        length_assurance_json,
                        '$.runtime_provider_attempts'
                    ) >= 0) AND (json_type(
                        length_assurance_json,
                        '$.runtime_provider_attempt_ceiling'
                    ) = 'integer') AND (json_type(
                        length_assurance_json,
                        '$.runtime_provider_attempt_ceiling_per_logical_call'
                    ) = 'integer') AND (json_extract(
                        length_assurance_json,
                        '$.runtime_provider_attempt_ceiling_per_logical_call'
                    ) = 3) AND (json_extract(
                        length_assurance_json,
                        '$.runtime_provider_attempt_ceiling'
                    ) = json_extract(
                        length_assurance_json,
                        '$.logical_provider_attempt_ceiling'
                    ) * json_extract(
                        length_assurance_json,
                        '$.runtime_provider_attempt_ceiling_per_logical_call'
                    )) AND (json_extract(
                        length_assurance_json,
                        '$.runtime_provider_attempts'
                    ) <= json_extract(
                        length_assurance_json,
                        '$.runtime_provider_attempt_ceiling'
                    )) AND (json_type(
                        length_assurance_json,
                        '$.estimated_cost_microunits'
                    ) = 'integer') AND (json_type(
                        length_assurance_json,
                        '$.estimated_cost_ceiling_microunits'
                    ) = 'integer') AND (json_extract(
                        length_assurance_json,
                        '$.estimated_cost_microunits'
                    ) >= 0) AND (json_extract(
                        length_assurance_json,
                        '$.estimated_cost_ceiling_microunits'
                    ) >= 0) AND (json_extract(
                        length_assurance_json,
                        '$.estimated_cost_microunits'
                    ) <= json_extract(
                        length_assurance_json,
                        '$.estimated_cost_ceiling_microunits'
                    )) AND (json_type(
                        length_assurance_json,
                        '$.attempt_ledger'
                    ) = 'array') AND (json_array_length(
                        length_assurance_json,
                        '$.attempt_ledger'
                    ) = json_extract(
                        length_assurance_json,
                        '$.logical_provider_attempts'
                    )) AND (length(
                        json_extract(
                            length_assurance_json,
                            '$.attempt_ledger_sha256'
                        )
                    ) = 64) AND (json_extract(
                        length_assurance_json,
                        '$.attempt_ledger_sha256'
                    ) NOT GLOB '*[^0-9a-f]*') AND (json_extract(
                        length_assurance_json,
                        '$.provider_termination'
                    ) IN ('normal_stop','token_limit','timeout','cancelled','error')) AND (json_extract(
                        length_assurance_json,
                        '$.partial_disposition'
                    ) IN ('complete_unaccepted_candidate','saved_unacceptable_partial','condensed_candidate_with_preserved_original','no_candidate')) AND (length(
                        json_extract(length_assurance_json, '$.output_text_sha256')
                    ) = 64) AND (json_extract(
                        length_assurance_json,
                        '$.output_text_sha256'
                    ) NOT GLOB '*[^0-9a-f]*') AND (length(
                        json_extract(
                            length_assurance_json,
                            '$.evidence_payload_sha256'
                        )
                    ) = 64) AND (json_extract(
                        length_assurance_json,
                        '$.evidence_payload_sha256'
                    ) NOT GLOB '*[^0-9a-f]*')))),
    PRIMARY KEY (id),
    CONSTRAINT fk_run_scene_ownership FOREIGN KEY(story_scene_id, project_id) REFERENCES story_scenes (id, project_id),
    CONSTRAINT fk_run_card_scene_ownership FOREIGN KEY(scene_card_version_id, story_scene_id) REFERENCES scene_card_versions (id, story_scene_id),
    CONSTRAINT fk_run_requirement_ownership FOREIGN KEY(requirement_version_id, project_id) REFERENCES story_requirement_versions (id, project_id),
    CONSTRAINT fk_run_bible_ownership FOREIGN KEY(bible_version_id, project_id) REFERENCES story_bible_versions (id, project_id),
    CONSTRAINT fk_run_outline_ownership FOREIGN KEY(outline_version_id, project_id) REFERENCES story_outline_versions (id, project_id),
    CONSTRAINT fk_run_chapter_plan_ownership FOREIGN KEY(chapter_plan_version_id, project_id) REFERENCES chapter_plan_versions (id, project_id),
    CONSTRAINT fk_run_parent_draft_ownership FOREIGN KEY(parent_draft_id, story_scene_id) REFERENCES scene_drafts (id, story_scene_id),
    CONSTRAINT fk_run_rebase_source_ownership FOREIGN KEY(rebased_from_generation_run_id, project_id) REFERENCES generation_runs (id, project_id),
    CONSTRAINT uq_run_id_project UNIQUE (id, project_id),
    CONSTRAINT ck_run_status CHECK (status IN ('running','completed','failed','cancelled','timeout')),
    CONSTRAINT ck_run_kind CHECK (run_kind IN ('generation','revision')),
    CONSTRAINT ck_run_content_mode CHECK (content_mode IN ('general','drama','dark','horror','violent')),
    CONSTRAINT ck_run_latency_nonneg CHECK (latency_ms >= 0),
    CONSTRAINT ck_run_planning_mode CHECK (planning_mode IN ('accepted','preview')),
    CONSTRAINT ck_run_preview_ack_bool CHECK (preview_warning_acknowledged IN (0,1)),
    CONSTRAINT ck_run_recovery_bool CHECK (is_recovery_rebase IN (0,1)),
    CONSTRAINT ck_run_message_storage_bool CHECK (rendered_message_storage_enabled IN (0,1)),
    CONSTRAINT ck_run_system_size_nonneg CHECK (system_message_byte_size >= 0),
    CONSTRAINT ck_run_user_size_nonneg CHECK (user_message_byte_size >= 0),
    CONSTRAINT ck_run_rebase_needs_reason CHECK (NOT ((rebased_from_generation_run_id IS NOT NULL) AND (rebase_reason = ''))),
    CONSTRAINT ck_run_recovery_needs_reason CHECK (NOT ((is_recovery_rebase = 1) AND (rebase_reason = ''))),
    CONSTRAINT ck_json_run_missing_versions CHECK (json_valid(missing_source_version_ids_json)),
    CONSTRAINT ck_json_run_options CHECK (json_valid(options_snapshot_json)),
    CONSTRAINT ck_json_run_char_versions CHECK (json_valid(character_version_ids_json)),
    CONSTRAINT ck_json_run_eligibility_ids CHECK (json_valid(eligibility_evaluation_ids_json)),
    CONSTRAINT ck_json_run_input_snapshot CHECK (json_valid(input_snapshot_json)),
    CONSTRAINT ck_run_revision_has_parent CHECK (NOT ((run_kind = 'revision') AND (parent_draft_id IS NULL)))
)""",
    """CREATE TABLE character_validations (
    id TEXT NOT NULL,
    character_id TEXT NOT NULL,
    character_version_id TEXT,
    requested_character_version_id TEXT,
    request_type TEXT NOT NULL,
    allowed INTEGER NOT NULL,
    reason_code TEXT NOT NULL,
    validator_version TEXT NOT NULL,
    input_fingerprint TEXT NOT NULL,
    details_json TEXT,
    evaluated_at TEXT NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT fk_elig_character FOREIGN KEY(character_id) REFERENCES characters (id),
    CONSTRAINT fk_elig_version_ownership FOREIGN KEY(character_version_id, character_id) REFERENCES character_versions (id, character_id),
    CONSTRAINT ck_elig_allowed_bool CHECK (allowed IN (0,1)),
    CONSTRAINT ck_elig_details_json CHECK ((details_json IS NULL) OR (json_valid(details_json)))
)""",
    """CREATE TABLE outfits (
    id TEXT NOT NULL,
    character_id TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT DEFAULT '' NOT NULL,
    canonical_traits_json TEXT NOT NULL,
    optional_traits_json TEXT NOT NULL,
    prohibited_traits_json TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT fk_outfits_character FOREIGN KEY(character_id) REFERENCES characters (id),
    CONSTRAINT ck_outfits_status CHECK (status IN ('active','archived')),
    CONSTRAINT ck_outfits_canonical_json CHECK (json_valid(canonical_traits_json)),
    CONSTRAINT ck_outfits_optional_json CHECK (json_valid(optional_traits_json)),
    CONSTRAINT ck_outfits_prohibited_json CHECK (json_valid(prohibited_traits_json))
)""",
    """CREATE TABLE projects (
    id TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT DEFAULT '' NOT NULL,
    default_language TEXT DEFAULT 'zh-TW' NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT ck_projects_status CHECK (status IN ('active','archived'))
)""",
    """CREATE TABLE prompt_experiment_logs (
    id TEXT NOT NULL,
    checkpoint_id TEXT,
    checkpoint_sha256 TEXT DEFAULT '' NOT NULL,
    prompt_project_id TEXT,
    prompt_variant_id TEXT,
    resolved_profile_hash TEXT DEFAULT '' NOT NULL,
    positive_prompt TEXT DEFAULT '' NOT NULL,
    negative_prompt TEXT DEFAULT '' NOT NULL,
    sampler TEXT DEFAULT '' NOT NULL,
    scheduler TEXT DEFAULT '' NOT NULL,
    steps INTEGER,
    cfg FLOAT,
    width INTEGER,
    height INTEGER,
    seed INTEGER,
    lora_settings_json TEXT DEFAULT '[]' NOT NULL,
    result_rating INTEGER,
    identity_score INTEGER,
    style_score INTEGER,
    instruction_adherence INTEGER,
    failure_tags_json TEXT DEFAULT '[]' NOT NULL,
    notes TEXT DEFAULT '' NOT NULL,
    local_output_reference TEXT DEFAULT '' NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT fk_exp_checkpoint FOREIGN KEY(checkpoint_id) REFERENCES checkpoint_registry (id),
    CONSTRAINT fk_exp_project FOREIGN KEY(prompt_project_id) REFERENCES prompt_projects (id),
    CONSTRAINT fk_exp_variant FOREIGN KEY(prompt_variant_id) REFERENCES prompt_variants (id),
    CONSTRAINT ck_exp_rating CHECK ((result_rating IS NULL) OR (result_rating BETWEEN 1 AND 5)),
    CONSTRAINT ck_json_exp_lora CHECK (json_valid(lora_settings_json)),
    CONSTRAINT ck_json_exp_failure_tags CHECK (json_valid(failure_tags_json))
)""",
    """CREATE TABLE prompt_profile_snapshots (
    id TEXT NOT NULL,
    dialect_id TEXT NOT NULL,
    dialect_version TEXT NOT NULL,
    checkpoint_profile_id TEXT DEFAULT '' NOT NULL,
    checkpoint_profile_version TEXT DEFAULT '' NOT NULL,
    preset_id TEXT DEFAULT '' NOT NULL,
    preset_version TEXT DEFAULT '' NOT NULL,
    resolved_json TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    compiler_version TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_profile_snapshot_sha256 UNIQUE (sha256),
    CONSTRAINT ck_json_snapshot_resolved CHECK (json_valid(resolved_json))
)""",
    """CREATE TABLE prompt_project_versions (
    id TEXT NOT NULL,
    prompt_project_id TEXT NOT NULL,
    version_number INTEGER NOT NULL,
    ast_json TEXT NOT NULL,
    change_note TEXT DEFAULT '' NOT NULL,
    selection_snapshot_json TEXT DEFAULT '{}' NOT NULL,
    selection_snapshot_sha256 TEXT DEFAULT '' NOT NULL,
    input_fingerprint TEXT DEFAULT '' NOT NULL,
    accepted INTEGER DEFAULT '0' NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT fk_ppv_project FOREIGN KEY(prompt_project_id) REFERENCES prompt_projects (id),
    CONSTRAINT ck_ppv_accepted_bool CHECK (accepted IN (0,1)),
    CONSTRAINT ck_json_ppv_ast CHECK (json_valid(ast_json)),
    CONSTRAINT ck_json_ppv_selection CHECK (json_valid(selection_snapshot_json)),
    CONSTRAINT uq_ppv_number UNIQUE (prompt_project_id, version_number),
    CONSTRAINT uq_ppv_id_project UNIQUE (id, prompt_project_id)
)""",
    """CREATE TABLE prompt_projects (
    id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    title TEXT NOT NULL,
    source_text TEXT DEFAULT '' NOT NULL,
    character_id TEXT,
    character_version_id TEXT,
    style_profile_id TEXT,
    style_version_id TEXT,
    status TEXT DEFAULT 'draft' NOT NULL,
    current_version_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT fk_pp_project FOREIGN KEY(project_id) REFERENCES projects (id),
    CONSTRAINT fk_pp_character FOREIGN KEY(character_id) REFERENCES characters (id),
    CONSTRAINT fk_pp_character_project_ownership FOREIGN KEY(character_id, project_id) REFERENCES characters (id, project_id),
    CONSTRAINT fk_pp_character_version_ownership FOREIGN KEY(character_version_id, character_id) REFERENCES character_versions (id, character_id),
    CONSTRAINT fk_pp_style FOREIGN KEY(style_profile_id) REFERENCES style_profiles (id),
    CONSTRAINT fk_pp_style_project_ownership FOREIGN KEY(style_profile_id, project_id) REFERENCES style_profiles (id, project_id),
    CONSTRAINT fk_pp_style_version_ownership FOREIGN KEY(style_version_id, style_profile_id) REFERENCES style_profile_versions (id, style_profile_id),
    CONSTRAINT ck_pp_status CHECK (status IN ('draft','active','archived')),
    CONSTRAINT fk_pp_current_version_ownership FOREIGN KEY(current_version_id, id) REFERENCES prompt_project_versions (id, prompt_project_id)
)""",
    """CREATE TABLE prompt_scratch_drafts (
    id TEXT NOT NULL,
    project_id TEXT,
    title TEXT DEFAULT '' NOT NULL,
    character_name TEXT DEFAULT '' NOT NULL,
    character_image_prompt_en TEXT DEFAULT '' NOT NULL,
    background_image_prompt_en TEXT DEFAULT '' NOT NULL,
    notes TEXT DEFAULT '' NOT NULL,
    status TEXT DEFAULT 'active' NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    editor_kind TEXT DEFAULT 'character' NOT NULL CONSTRAINT ck_prompt_scratch_drafts_editor_kind CHECK (editor_kind IN ('character','background','both')),
    editor_gender TEXT DEFAULT 'female' NOT NULL CONSTRAINT ck_prompt_scratch_drafts_editor_gender CHECK (editor_gender IN ('female','male')),
    PRIMARY KEY (id),
    CONSTRAINT fk_prompt_scratch_drafts_project FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE SET NULL,
    CONSTRAINT ck_prompt_scratch_drafts_has_content CHECK ((length(trim(title)) > 0) OR (length(trim(character_name)) > 0) OR (length(trim(character_image_prompt_en)) > 0) OR (length(trim(background_image_prompt_en)) > 0) OR (length(trim(notes)) > 0)),
    CONSTRAINT ck_prompt_scratch_drafts_status CHECK (status IN ('active','archived')),
    CONSTRAINT ck_prompt_scratch_drafts_character_prompt_ascii CHECK (character_image_prompt_en NOT GLOB '*[^ -~]*'),
    CONSTRAINT ck_prompt_scratch_drafts_background_prompt_ascii CHECK (background_image_prompt_en NOT GLOB '*[^ -~]*')
)""",
    """CREATE TABLE prompt_scratch_recovery_journals (
    id TEXT NOT NULL,
    intent TEXT NOT NULL,
    existing_draft_id TEXT,
    reserved_draft_id TEXT,
    base_updated_at TEXT,
    raw_project_id TEXT,
    raw_link_project INTEGER,
    raw_editor_kind TEXT NOT NULL,
    raw_editor_gender TEXT NOT NULL,
    raw_title TEXT,
    raw_character_name TEXT,
    raw_character_image_prompt_en TEXT,
    raw_background_image_prompt_en TEXT,
    raw_notes TEXT,
    payload_sha256 TEXT NOT NULL,
    sequence INTEGER NOT NULL,
    state TEXT DEFAULT 'pending' NOT NULL,
    conflict_reason TEXT DEFAULT '' NOT NULL,
    resolution_kind TEXT,
    committed_draft_id TEXT,
    committed_updated_at TEXT,
    resolved_from_sequence INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    resolved_at TEXT,
    PRIMARY KEY (id),
    CONSTRAINT fk_prompt_scratch_recovery_existing_draft FOREIGN KEY(existing_draft_id) REFERENCES prompt_scratch_drafts (id) ON DELETE RESTRICT,
    CONSTRAINT fk_prompt_scratch_recovery_committed_draft FOREIGN KEY(committed_draft_id) REFERENCES prompt_scratch_drafts (id) ON DELETE RESTRICT,
    CONSTRAINT ck_prompt_scratch_recovery_intent_target CHECK (((intent = 'new') AND (existing_draft_id IS NULL) AND (reserved_draft_id IS NOT NULL) AND (base_updated_at IS NULL)) OR ((intent = 'existing') AND (existing_draft_id IS NOT NULL) AND (reserved_draft_id IS NULL) AND (base_updated_at IS NOT NULL))),
    CONSTRAINT ck_prompt_scratch_recovery_sequence CHECK (sequence >= 1),
    CONSTRAINT ck_prompt_scratch_recovery_state CHECK (state IN ('pending','conflict','committed','discarded')),
    CONSTRAINT ck_prompt_scratch_recovery_conflict_reason CHECK (conflict_reason IN ('','stale_revision','target_missing','target_archived','target_id_taken')),
    CONSTRAINT ck_prompt_scratch_recovery_resolution_kind CHECK (((state = 'committed') AND (resolution_kind IN ('target','save_as_new'))) OR ((state <> 'committed') AND (resolution_kind IS NULL))),
    CONSTRAINT ck_prompt_scratch_recovery_resolution_target CHECK ((state <> 'committed') OR ((resolution_kind = 'target') AND (committed_draft_id = COALESCE(existing_draft_id,reserved_draft_id))) OR ((resolution_kind = 'save_as_new') AND (committed_draft_id <> COALESCE(existing_draft_id,reserved_draft_id)))),
    CONSTRAINT ck_prompt_scratch_recovery_sha256 CHECK ((length(payload_sha256) = 64) AND (payload_sha256 NOT GLOB '*[^0-9a-f]*')),
    CONSTRAINT ck_prompt_scratch_recovery_editor_kind CHECK (raw_editor_kind IN ('character','background','both')),
    CONSTRAINT ck_prompt_scratch_recovery_editor_gender CHECK (raw_editor_gender IN ('female','male')),
    CONSTRAINT ck_prompt_scratch_recovery_raw_lifecycle CHECK (((state IN ('pending','conflict')) AND (raw_link_project IN (0,1)) AND (raw_title IS NOT NULL) AND (raw_character_name IS NOT NULL) AND (raw_character_image_prompt_en IS NOT NULL) AND (raw_background_image_prompt_en IS NOT NULL) AND (raw_notes IS NOT NULL)) OR ((state IN ('committed','discarded')) AND (raw_project_id IS NULL) AND (raw_link_project IS NULL) AND (raw_title IS NULL) AND (raw_character_name IS NULL) AND (raw_character_image_prompt_en IS NULL) AND (raw_background_image_prompt_en IS NULL) AND (raw_notes IS NULL))),
    CONSTRAINT ck_prompt_scratch_recovery_payload_size CHECK ((state IN ('committed','discarded')) OR (length(CAST(COALESCE(raw_project_id,'') AS BLOB)) + length(CAST(raw_title AS BLOB)) + length(CAST(raw_character_name AS BLOB)) + length(CAST(raw_character_image_prompt_en AS BLOB)) + length(CAST(raw_background_image_prompt_en AS BLOB)) + length(CAST(raw_notes AS BLOB)) <= 1048576)),
    CONSTRAINT ck_prompt_scratch_recovery_resolution_lifecycle CHECK (((state = 'pending') AND (conflict_reason = '') AND (resolution_kind IS NULL) AND (committed_draft_id IS NULL) AND (committed_updated_at IS NULL) AND (resolved_from_sequence IS NULL) AND (resolved_at IS NULL)) OR ((state = 'conflict') AND (conflict_reason <> '') AND (resolution_kind IS NULL) AND (committed_draft_id IS NULL) AND (committed_updated_at IS NULL) AND (resolved_from_sequence >= 1) AND (sequence = resolved_from_sequence + 1) AND (resolved_at IS NOT NULL)) OR ((state = 'committed') AND (conflict_reason = '') AND (resolution_kind IS NOT NULL) AND (committed_draft_id IS NOT NULL) AND (committed_updated_at IS NOT NULL) AND (resolved_from_sequence >= 1) AND (sequence = resolved_from_sequence + 1) AND (resolved_at IS NOT NULL)) OR ((state = 'discarded') AND (conflict_reason = '') AND (resolution_kind IS NULL) AND (committed_draft_id IS NULL) AND (committed_updated_at IS NULL) AND (resolved_from_sequence >= 1) AND (sequence = resolved_from_sequence + 1) AND (resolved_at IS NOT NULL)))
)""",
    """CREATE TABLE prompt_variants (
    id TEXT NOT NULL,
    prompt_project_version_id TEXT NOT NULL,
    prompt_project_id TEXT NOT NULL,
    profile_snapshot_id TEXT NOT NULL,
    positive_prompt TEXT NOT NULL,
    negative_prompt TEXT NOT NULL,
    natural_language_prompt TEXT NOT NULL,
    blocks_json TEXT NOT NULL,
    lint_json TEXT DEFAULT '{}' NOT NULL,
    conflicts_json TEXT DEFAULT '[]' NOT NULL,
    compiler_version TEXT NOT NULL,
    compilation_status TEXT DEFAULT 'valid' NOT NULL,
    lint_status TEXT DEFAULT 'ok' NOT NULL,
    input_fingerprint TEXT DEFAULT '' NOT NULL,
    checkpoint_id TEXT DEFAULT '' NOT NULL,
    checkpoint_filename_snapshot TEXT DEFAULT '' NOT NULL,
    checkpoint_sha256_snapshot TEXT DEFAULT '' NOT NULL,
    checkpoint_hash_status TEXT DEFAULT 'not_computed' NOT NULL,
    dialect_id TEXT DEFAULT '' NOT NULL,
    checkpoint_profile_id TEXT DEFAULT '' NOT NULL,
    preset_id TEXT DEFAULT '' NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT ck_pv_compilation_status CHECK (compilation_status IN ('preview','valid')),
    CONSTRAINT ck_pv_lint_status CHECK (lint_status IN ('ok','warnings')),
    CONSTRAINT ck_json_pv_blocks CHECK (json_valid(blocks_json)),
    CONSTRAINT ck_json_pv_lint CHECK (json_valid(lint_json)),
    CONSTRAINT ck_json_pv_conflicts CHECK (json_valid(conflicts_json)),
    CONSTRAINT fk_pv_version_ownership FOREIGN KEY(prompt_project_version_id, prompt_project_id) REFERENCES prompt_project_versions (id, prompt_project_id),
    CONSTRAINT fk_pv_snapshot FOREIGN KEY(profile_snapshot_id) REFERENCES prompt_profile_snapshots (id)
)""",
    """CREATE TABLE scene_card_participants (
    id TEXT NOT NULL,
    scene_card_version_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    character_id TEXT NOT NULL,
    character_version_id TEXT NOT NULL,
    role TEXT DEFAULT '' NOT NULL,
    is_pov INTEGER DEFAULT '0' NOT NULL,
    position INTEGER DEFAULT '0' NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT fk_participant_card_ownership FOREIGN KEY(scene_card_version_id, project_id) REFERENCES scene_card_versions (id, project_id),
    CONSTRAINT fk_participant_character_project FOREIGN KEY(character_id, project_id) REFERENCES characters (id, project_id),
    CONSTRAINT fk_participant_version_character FOREIGN KEY(character_version_id, character_id) REFERENCES character_versions (id, character_id),
    CONSTRAINT uq_participant_card_character UNIQUE (scene_card_version_id, character_id),
    CONSTRAINT ck_participant_pov_bool CHECK (is_pov IN (0,1)),
    CONSTRAINT ck_participant_position CHECK (position >= 0)
)""",
    """CREATE TABLE scene_card_versions (
    id TEXT NOT NULL,
    story_scene_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    version_number INTEGER NOT NULL,
    change_note TEXT DEFAULT '' NOT NULL,
    accepted INTEGER DEFAULT '0' NOT NULL,
    created_at TEXT NOT NULL,
    card_json TEXT NOT NULL,
    content_mode TEXT DEFAULT 'general' NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT ck_json_scv_card CHECK (json_valid(card_json)),
    CONSTRAINT ck_scv_content_mode CHECK (content_mode IN ('general','drama','dark','horror','violent')),
    CONSTRAINT fk_scene_card_versions_parent_ownership FOREIGN KEY(story_scene_id, project_id) REFERENCES story_scenes (id, project_id),
    CONSTRAINT uq_scene_card_versions_parent_version UNIQUE (story_scene_id, version_number),
    CONSTRAINT uq_scene_card_versions_id_project UNIQUE (id, project_id),
    CONSTRAINT uq_scene_card_versions_id_parent UNIQUE (id, story_scene_id),
    CONSTRAINT ck_scene_card_versions_accepted_bool CHECK (accepted IN (0,1)),
    CONSTRAINT ck_scene_card_versions_version_positive CHECK (version_number > 0)
)""",
    """CREATE TABLE scene_draft_block_sets (
    scene_draft_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    story_scene_id TEXT NOT NULL,
    parent_draft_id TEXT,
    parent_draft_available INTEGER DEFAULT '0' NOT NULL,
    schema_version TEXT NOT NULL,
    segmentation_version TEXT NOT NULL,
    mapping_version TEXT NOT NULL,
    leading_text TEXT DEFAULT '' NOT NULL,
    prose_sha256 TEXT NOT NULL,
    manifest_sha256 TEXT NOT NULL,
    lineage_quality TEXT NOT NULL,
    block_count INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (scene_draft_id),
    CONSTRAINT fk_scene_draft_block_set_draft_project FOREIGN KEY(scene_draft_id, project_id) REFERENCES scene_drafts (id, project_id) ON DELETE RESTRICT,
    CONSTRAINT fk_scene_draft_block_set_draft_scene FOREIGN KEY(scene_draft_id, story_scene_id) REFERENCES scene_drafts (id, story_scene_id) ON DELETE RESTRICT,
    CONSTRAINT uq_scene_draft_block_set_ownership UNIQUE (scene_draft_id, project_id, story_scene_id),
    CONSTRAINT ck_scene_draft_block_set_schema CHECK (schema_version = 'story-block-v1'),
    CONSTRAINT ck_scene_draft_block_set_segmentation CHECK (segmentation_version = 'paragraph-v1'),
    CONSTRAINT ck_scene_draft_block_set_mapping CHECK (mapping_version = 'parent-aware-v1'),
    CONSTRAINT ck_scene_draft_block_set_prose_sha256 CHECK ((length(prose_sha256) = 64) AND (prose_sha256 NOT GLOB '*[^0-9a-f]*')),
    CONSTRAINT ck_scene_draft_block_set_manifest_sha256 CHECK ((length(manifest_sha256) = 64) AND (manifest_sha256 NOT GLOB '*[^0-9a-f]*')),
    CONSTRAINT ck_scene_draft_block_set_lineage_quality CHECK (lineage_quality IN ('root','exact','conservative','legacy_unlinked')),
    CONSTRAINT ck_scene_draft_block_set_parent_available CHECK (parent_draft_available IN (0,1)),
    CONSTRAINT ck_scene_draft_block_set_lineage_state CHECK (((parent_draft_id IS NULL) AND (parent_draft_available = 0) AND (lineage_quality = 'root')) OR ((parent_draft_id IS NOT NULL) AND (length(trim(parent_draft_id)) > 0) AND (parent_draft_available = 0) AND (lineage_quality = 'legacy_unlinked')) OR ((parent_draft_id IS NOT NULL) AND (length(trim(parent_draft_id)) > 0) AND (parent_draft_available = 1) AND (lineage_quality IN ('exact','conservative')))),
    CONSTRAINT ck_scene_draft_block_set_count CHECK (block_count >= 0)
)""",
    """CREATE TABLE scene_draft_summaries (
    id TEXT NOT NULL,
    scene_draft_id TEXT NOT NULL,
    story_scene_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    summary_text TEXT DEFAULT '' NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT fk_summary_draft_ownership FOREIGN KEY(scene_draft_id, story_scene_id) REFERENCES scene_drafts (id, story_scene_id),
    CONSTRAINT fk_summary_scene_ownership FOREIGN KEY(story_scene_id, project_id) REFERENCES story_scenes (id, project_id),
    CONSTRAINT uq_summary_one_per_draft UNIQUE (scene_draft_id)
)""",
    """CREATE TABLE scene_drafts (
    id TEXT NOT NULL,
    story_scene_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    draft_number INTEGER NOT NULL,
    prose_text TEXT NOT NULL,
    word_count INTEGER DEFAULT '0' NOT NULL,
    origin TEXT DEFAULT 'generated' NOT NULL,
    draft_status TEXT DEFAULT 'complete' NOT NULL,
    was_accepted INTEGER DEFAULT '0' NOT NULL,
    accepted_at TEXT DEFAULT '' NOT NULL,
    is_preview INTEGER DEFAULT '0' NOT NULL,
    promoted_from_preview_draft_id TEXT,
    is_recovery_rebase INTEGER DEFAULT '0' NOT NULL,
    scene_card_version_id TEXT NOT NULL,
    generation_run_id TEXT,
    revision_request_json TEXT DEFAULT '{}' NOT NULL,
    revision_request_fingerprint TEXT,
    parent_draft_id TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT fk_draft_scene_ownership FOREIGN KEY(story_scene_id, project_id) REFERENCES story_scenes (id, project_id),
    CONSTRAINT fk_draft_card_scene_ownership FOREIGN KEY(scene_card_version_id, story_scene_id) REFERENCES scene_card_versions (id, story_scene_id),
    CONSTRAINT fk_draft_parent_ownership FOREIGN KEY(parent_draft_id, project_id) REFERENCES scene_drafts (id, project_id),
    CONSTRAINT uq_draft_scene_number UNIQUE (story_scene_id, draft_number),
    CONSTRAINT uq_draft_id_project UNIQUE (id, project_id),
    CONSTRAINT uq_draft_id_scene UNIQUE (id, story_scene_id),
    CONSTRAINT ck_draft_accepted_bool CHECK (was_accepted IN (0,1)),
    CONSTRAINT ck_draft_number_positive CHECK (draft_number > 0),
    CONSTRAINT ck_draft_word_count_nonneg CHECK (word_count >= 0),
    CONSTRAINT ck_draft_origin CHECK (origin IN ('generated','revised','manual')),
    CONSTRAINT ck_draft_status CHECK (draft_status IN ('complete','partial')),
    CONSTRAINT ck_json_draft_revision CHECK (json_valid(revision_request_json)),
    CONSTRAINT ck_draft_partial_never_accepted CHECK (NOT ((draft_status = 'partial') AND (was_accepted = 1))),
    CONSTRAINT ck_draft_preview_bool CHECK (is_preview IN (0,1)),
    CONSTRAINT ck_draft_recovery_bool CHECK (is_recovery_rebase IN (0,1)),
    CONSTRAINT ck_draft_preview_never_accepted CHECK (NOT ((is_preview = 1) AND (was_accepted = 1))),
    CONSTRAINT fk_draft_promoted_from_ownership FOREIGN KEY(promoted_from_preview_draft_id, story_scene_id) REFERENCES scene_drafts (id, story_scene_id)
)""",
    """CREATE TABLE story_bible_versions (
    id TEXT NOT NULL,
    story_bible_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    version_number INTEGER NOT NULL,
    change_note TEXT DEFAULT '' NOT NULL,
    accepted INTEGER DEFAULT '0' NOT NULL,
    created_at TEXT NOT NULL,
    bible_json TEXT NOT NULL,
    requirement_version_id TEXT,
    PRIMARY KEY (id),
    CONSTRAINT ck_json_sbv_bible CHECK (json_valid(bible_json)),
    CONSTRAINT fk_sbv_requirement_ownership FOREIGN KEY(requirement_version_id, project_id) REFERENCES story_requirement_versions (id, project_id),
    CONSTRAINT fk_story_bible_versions_parent_ownership FOREIGN KEY(story_bible_id, project_id) REFERENCES story_bibles (id, project_id),
    CONSTRAINT uq_story_bible_versions_parent_version UNIQUE (story_bible_id, version_number),
    CONSTRAINT uq_story_bible_versions_id_project UNIQUE (id, project_id),
    CONSTRAINT uq_story_bible_versions_id_parent UNIQUE (id, story_bible_id),
    CONSTRAINT ck_story_bible_versions_accepted_bool CHECK (accepted IN (0,1)),
    CONSTRAINT ck_story_bible_versions_version_positive CHECK (version_number > 0)
)""",
    """CREATE TABLE story_bibles (
    id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    title TEXT DEFAULT '' NOT NULL,
    working_head_version_id TEXT,
    accepted_version_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT fk_story_bibles_project FOREIGN KEY(project_id) REFERENCES projects (id),
    CONSTRAINT uq_story_bibles_id_project UNIQUE (id, project_id)
)""",
    """CREATE TABLE story_block_anchors (
    logical_block_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    story_scene_id TEXT NOT NULL,
    created_from_draft_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (logical_block_id),
    CONSTRAINT fk_story_block_anchor_draft_project FOREIGN KEY(created_from_draft_id, project_id) REFERENCES scene_drafts (id, project_id) ON DELETE RESTRICT,
    CONSTRAINT fk_story_block_anchor_draft_scene FOREIGN KEY(created_from_draft_id, story_scene_id) REFERENCES scene_drafts (id, story_scene_id) ON DELETE RESTRICT,
    CONSTRAINT uq_story_block_anchor_ownership UNIQUE (logical_block_id, project_id, story_scene_id),
    CONSTRAINT ck_story_block_anchor_id CHECK (length(trim(logical_block_id)) > 0)
)""",
    """CREATE TABLE story_block_revisions (
    block_revision_id TEXT NOT NULL,
    logical_block_id TEXT NOT NULL,
    scene_draft_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    story_scene_id TEXT NOT NULL,
    parent_block_revision_id TEXT,
    ordinal INTEGER NOT NULL,
    block_type TEXT NOT NULL,
    text TEXT NOT NULL,
    separator_after TEXT DEFAULT '' NOT NULL,
    text_sha256 TEXT NOT NULL,
    source_slice_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (block_revision_id),
    CONSTRAINT fk_story_block_revision_anchor_ownership FOREIGN KEY(logical_block_id, project_id, story_scene_id) REFERENCES story_block_anchors (logical_block_id, project_id, story_scene_id) ON DELETE RESTRICT,
    CONSTRAINT fk_story_block_revision_set_ownership FOREIGN KEY(scene_draft_id, project_id, story_scene_id) REFERENCES scene_draft_block_sets (scene_draft_id, project_id, story_scene_id) ON DELETE RESTRICT,
    CONSTRAINT fk_story_block_revision_parent_ownership FOREIGN KEY(parent_block_revision_id, project_id, story_scene_id) REFERENCES story_block_revisions (block_revision_id, project_id, story_scene_id) ON DELETE RESTRICT,
    CONSTRAINT uq_story_block_revision_ownership UNIQUE (block_revision_id, project_id, story_scene_id),
    CONSTRAINT uq_story_block_revision_draft_ordinal UNIQUE (scene_draft_id, ordinal),
    CONSTRAINT uq_story_block_revision_draft_logical UNIQUE (scene_draft_id, logical_block_id),
    CONSTRAINT ck_story_block_revision_ordinal CHECK (ordinal >= 0),
    CONSTRAINT ck_story_block_revision_type CHECK (block_type = 'paragraph'),
    CONSTRAINT ck_story_block_revision_text_sha256 CHECK ((length(text_sha256) = 64) AND (text_sha256 NOT GLOB '*[^0-9a-f]*')),
    CONSTRAINT ck_story_block_revision_slice_sha256 CHECK ((length(source_slice_sha256) = 64) AND (source_slice_sha256 NOT GLOB '*[^0-9a-f]*'))
)""",
    """CREATE TABLE story_chapters (
    id TEXT NOT NULL,
    story_outline_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    chapter_number INTEGER NOT NULL,
    title TEXT DEFAULT '' NOT NULL,
    working_head_plan_version_id TEXT,
    accepted_plan_version_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT fk_chapter_outline_ownership FOREIGN KEY(story_outline_id, project_id) REFERENCES story_outlines (id, project_id),
    CONSTRAINT uq_chapter_outline_number UNIQUE (story_outline_id, chapter_number),
    CONSTRAINT uq_chapter_id_project UNIQUE (id, project_id),
    CONSTRAINT ck_chapter_number_positive CHECK (chapter_number > 0)
)""",
    """CREATE TABLE story_exports (
    id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    export_mode TEXT NOT NULL,
    export_contract_version TEXT DEFAULT '' NOT NULL,
    planning_mode TEXT DEFAULT 'accepted' NOT NULL,
    story_outline_id TEXT,
    requirement_version_id TEXT,
    bible_version_id TEXT,
    outline_version_id TEXT,
    snapshot_json TEXT DEFAULT '{}' NOT NULL,
    snapshot_sha256 TEXT DEFAULT '' NOT NULL,
    markdown_byte_size INTEGER DEFAULT '0' NOT NULL,
    json_byte_size INTEGER DEFAULT '0' NOT NULL,
    total_byte_size INTEGER DEFAULT '0' NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT fk_export_project FOREIGN KEY(project_id) REFERENCES projects (id),
    CONSTRAINT fk_export_outline_ownership FOREIGN KEY(story_outline_id, project_id) REFERENCES story_outlines (id, project_id),
    CONSTRAINT fk_export_requirement_ownership FOREIGN KEY(requirement_version_id, project_id) REFERENCES story_requirement_versions (id, project_id),
    CONSTRAINT fk_export_bible_ownership FOREIGN KEY(bible_version_id, project_id) REFERENCES story_bible_versions (id, project_id),
    CONSTRAINT fk_export_outline_version_ownership FOREIGN KEY(outline_version_id, project_id) REFERENCES story_outline_versions (id, project_id),
    CONSTRAINT ck_json_export_snapshot CHECK (json_valid(snapshot_json)),
    CONSTRAINT ck_export_planning_mode CHECK (planning_mode IN ('accepted','preview')),
    CONSTRAINT ck_export_md_size_nonneg CHECK (markdown_byte_size >= 0),
    CONSTRAINT ck_export_json_size_nonneg CHECK (json_byte_size >= 0),
    CONSTRAINT ck_export_total_size_nonneg CHECK (total_byte_size >= 0)
)""",
    """CREATE TABLE story_fragment_drafts (
    id TEXT NOT NULL,
    project_id TEXT,
    title TEXT NOT NULL,
    fragment_kind TEXT DEFAULT 'narrative' NOT NULL,
    fragment_text TEXT NOT NULL,
    context_notes TEXT DEFAULT '' NOT NULL,
    tags_json TEXT DEFAULT '[]' NOT NULL,
    generation_mode TEXT DEFAULT 'manual' NOT NULL,
    generation_seed TEXT DEFAULT '' NOT NULL,
    status TEXT DEFAULT 'active' NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT fk_story_fragment_drafts_project FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE SET NULL,
    CONSTRAINT ck_story_fragment_drafts_id CHECK (length(trim(id)) BETWEEN 1 AND 200),
    CONSTRAINT ck_story_fragment_drafts_project_id CHECK ((project_id IS NULL) OR (length(trim(project_id)) BETWEEN 1 AND 200)),
    CONSTRAINT ck_story_fragment_drafts_title CHECK (length(trim(title)) BETWEEN 1 AND 200),
    CONSTRAINT ck_story_fragment_drafts_kind CHECK (fragment_kind IN ('narrative','dialogue','opening')),
    CONSTRAINT ck_story_fragment_drafts_text CHECK ((length(fragment_text) <= 500000) AND (length(trim(fragment_text, char(9) || char(10) || char(13) || ' ')) > 0)),
    CONSTRAINT ck_story_fragment_drafts_context CHECK (length(context_notes) <= 4000),
    CONSTRAINT ck_story_fragment_drafts_tags_json CHECK ((json_valid(tags_json)) AND (json_type(tags_json) = 'array') AND (tags_json = json(tags_json)) AND (json_array_length(tags_json) <= 24)),
    CONSTRAINT ck_story_fragment_drafts_generation_mode CHECK (generation_mode IN ('manual','fill_blanks','reroll_all')),
    CONSTRAINT ck_story_fragment_drafts_generation_seed CHECK ((length(generation_seed) <= 200) AND (((generation_mode = 'manual') AND (generation_seed = '')) OR ((generation_mode IN ('fill_blanks','reroll_all')) AND (length(trim(generation_seed)) > 0)))),
    CONSTRAINT ck_story_fragment_drafts_status CHECK (status IN ('active','archived'))
)""",
    """CREATE TABLE story_memory_entries (
    id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    story_outline_id TEXT NOT NULL,
    story_scene_id TEXT NOT NULL,
    scene_draft_id TEXT NOT NULL,
    proposal_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    subject_id TEXT NOT NULL,
    attribute TEXT NOT NULL,
    value TEXT DEFAULT '' NOT NULL,
    operation TEXT NOT NULL,
    supersedes_entry_id TEXT,
    source_excerpt TEXT DEFAULT '' NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT fk_memory_entry_proposal_ownership FOREIGN KEY(proposal_id, project_id, story_outline_id, story_scene_id, scene_draft_id) REFERENCES story_memory_proposals (id, project_id, story_outline_id, story_scene_id, scene_draft_id),
    CONSTRAINT fk_memory_entry_replacement_ownership FOREIGN KEY(supersedes_entry_id, project_id, story_outline_id) REFERENCES story_memory_entries (id, project_id, story_outline_id),
    CONSTRAINT uq_memory_entry_outline_ownership UNIQUE (id, project_id, story_outline_id),
    CONSTRAINT uq_memory_entry_single_replacement UNIQUE (supersedes_entry_id),
    CONSTRAINT ck_memory_entry_kind CHECK (kind IN ('fact','timeline','foreshadowing','character_state')),
    CONSTRAINT ck_memory_entry_operation CHECK (operation IN ('assert','supersede','retract')),
    CONSTRAINT ck_memory_entry_replacement_contract CHECK (((operation = 'assert') AND (supersedes_entry_id IS NULL)) OR ((operation IN ('supersede','retract')) AND (supersedes_entry_id IS NOT NULL))),
    CONSTRAINT ck_memory_entry_content CHECK ((subject_id <> '') AND (attribute <> '') AND (created_at <> '') AND (((operation IN ('assert','supersede')) AND (value <> '')) OR ((operation = 'retract') AND (value = ''))))
)""",
    """CREATE TABLE story_memory_proposals (
    id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    story_outline_id TEXT NOT NULL,
    story_scene_id TEXT NOT NULL,
    scene_draft_id TEXT NOT NULL,
    base_memory_fingerprint TEXT NOT NULL,
    proposal_json TEXT NOT NULL,
    proposal_sha256 TEXT NOT NULL,
    origin TEXT NOT NULL,
    provider TEXT DEFAULT '' NOT NULL,
    model TEXT DEFAULT '' NOT NULL,
    contract_version TEXT NOT NULL,
    status TEXT DEFAULT 'pending' NOT NULL,
    decision_note TEXT DEFAULT '' NOT NULL,
    created_at TEXT NOT NULL,
    finalized_at TEXT DEFAULT '' NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT fk_memory_proposal_outline_ownership FOREIGN KEY(story_outline_id, project_id) REFERENCES story_outlines (id, project_id),
    CONSTRAINT fk_memory_proposal_scene_ownership FOREIGN KEY(story_scene_id, project_id) REFERENCES story_scenes (id, project_id),
    CONSTRAINT fk_memory_proposal_draft_ownership FOREIGN KEY(scene_draft_id, story_scene_id) REFERENCES scene_drafts (id, story_scene_id),
    CONSTRAINT uq_memory_proposal_full_ownership UNIQUE (id, project_id, story_outline_id, story_scene_id, scene_draft_id),
    CONSTRAINT ck_memory_proposal_json CHECK ((json_valid(proposal_json)) AND (json_type(proposal_json) = 'object') AND (json_extract(proposal_json, '$.schema_version') = 'story-memory-v1') AND (json_type(proposal_json, '$.changes') = 'array')),
    CONSTRAINT ck_memory_proposal_contract CHECK (contract_version = 'story-memory-proposal-v1'),
    CONSTRAINT ck_memory_proposal_origin CHECK (origin IN ('manual','scene_card','llm')),
    CONSTRAINT ck_memory_proposal_status CHECK (status IN ('pending','accepted','rejected')),
    CONSTRAINT ck_memory_proposal_lifecycle CHECK (((status = 'pending') AND (finalized_at = '') AND (decision_note = '')) OR ((status IN ('accepted','rejected')) AND (finalized_at <> ''))),
    CONSTRAINT ck_memory_proposal_base_fingerprint CHECK ((length(base_memory_fingerprint) = 64) AND (base_memory_fingerprint NOT GLOB '*[^0-9a-f]*')),
    CONSTRAINT ck_memory_proposal_sha256 CHECK ((length(proposal_sha256) = 64) AND (proposal_sha256 NOT GLOB '*[^0-9a-f]*'))
)""",
    """CREATE TABLE story_outline_versions (
    id TEXT NOT NULL,
    story_outline_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    version_number INTEGER NOT NULL,
    change_note TEXT DEFAULT '' NOT NULL,
    accepted INTEGER DEFAULT '0' NOT NULL,
    created_at TEXT NOT NULL,
    outline_json TEXT NOT NULL,
    structure_profile TEXT DEFAULT '' NOT NULL,
    bible_version_id TEXT,
    PRIMARY KEY (id),
    CONSTRAINT ck_json_sov_outline CHECK (json_valid(outline_json)),
    CONSTRAINT fk_sov_bible_ownership FOREIGN KEY(bible_version_id, project_id) REFERENCES story_bible_versions (id, project_id),
    CONSTRAINT fk_story_outline_versions_parent_ownership FOREIGN KEY(story_outline_id, project_id) REFERENCES story_outlines (id, project_id),
    CONSTRAINT uq_story_outline_versions_parent_version UNIQUE (story_outline_id, version_number),
    CONSTRAINT uq_story_outline_versions_id_project UNIQUE (id, project_id),
    CONSTRAINT uq_story_outline_versions_id_parent UNIQUE (id, story_outline_id),
    CONSTRAINT ck_story_outline_versions_accepted_bool CHECK (accepted IN (0,1)),
    CONSTRAINT ck_story_outline_versions_version_positive CHECK (version_number > 0)
)""",
    """CREATE TABLE story_outlines (
    id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    title TEXT DEFAULT '' NOT NULL,
    working_head_version_id TEXT,
    accepted_version_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT fk_story_outlines_project FOREIGN KEY(project_id) REFERENCES projects (id),
    CONSTRAINT uq_story_outlines_id_project UNIQUE (id, project_id)
)""",
    """CREATE TABLE story_requirement_versions (
    id TEXT NOT NULL,
    story_requirement_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    version_number INTEGER NOT NULL,
    change_note TEXT DEFAULT '' NOT NULL,
    accepted INTEGER DEFAULT '0' NOT NULL,
    created_at TEXT NOT NULL,
    requirement_json TEXT NOT NULL,
    source_text TEXT DEFAULT '' NOT NULL,
    structured_mode INTEGER DEFAULT '1' NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT ck_json_srv_requirement CHECK (json_valid(requirement_json)),
    CONSTRAINT ck_srv_structured_bool CHECK (structured_mode IN (0,1)),
    CONSTRAINT fk_story_requirement_versions_parent_ownership FOREIGN KEY(story_requirement_id, project_id) REFERENCES story_requirements (id, project_id),
    CONSTRAINT uq_story_requirement_versions_parent_version UNIQUE (story_requirement_id, version_number),
    CONSTRAINT uq_story_requirement_versions_id_project UNIQUE (id, project_id),
    CONSTRAINT uq_story_requirement_versions_id_parent UNIQUE (id, story_requirement_id),
    CONSTRAINT ck_story_requirement_versions_accepted_bool CHECK (accepted IN (0,1)),
    CONSTRAINT ck_story_requirement_versions_version_positive CHECK (version_number > 0)
)""",
    """CREATE TABLE story_requirements (
    id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    title TEXT DEFAULT '' NOT NULL,
    working_head_version_id TEXT,
    accepted_version_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT fk_story_requirements_project FOREIGN KEY(project_id) REFERENCES projects (id),
    CONSTRAINT uq_story_requirements_id_project UNIQUE (id, project_id)
)""",
    """CREATE TABLE story_scene_revisions (
    scene_id TEXT PRIMARY KEY NOT NULL,
    revision INTEGER NOT NULL CHECK(typeof(revision) = 'integer' AND revision >= 0)
)""",
    """CREATE TABLE story_scenes (
    id TEXT NOT NULL,
    story_chapter_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    scene_number INTEGER NOT NULL,
    title TEXT DEFAULT '' NOT NULL,
    working_head_card_version_id TEXT,
    accepted_card_version_id TEXT,
    working_draft_id TEXT,
    accepted_draft_id TEXT,
    summary_text TEXT DEFAULT '' NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT fk_scene_chapter_ownership FOREIGN KEY(story_chapter_id, project_id) REFERENCES story_chapters (id, project_id),
    CONSTRAINT fk_scene_accepted_draft_ownership FOREIGN KEY(accepted_draft_id, id) REFERENCES scene_drafts (id, story_scene_id),
    CONSTRAINT fk_scene_working_draft_ownership FOREIGN KEY(working_draft_id, id) REFERENCES scene_drafts (id, story_scene_id),
    CONSTRAINT uq_scene_chapter_number UNIQUE (story_chapter_id, scene_number),
    CONSTRAINT uq_scene_id_project UNIQUE (id, project_id),
    CONSTRAINT ck_scene_number_positive CHECK (scene_number > 0)
)""",
    """CREATE TABLE style_profile_versions (
    id TEXT NOT NULL,
    style_profile_id TEXT NOT NULL,
    version_number INTEGER NOT NULL,
    style_dna_json TEXT NOT NULL,
    change_note TEXT DEFAULT '' NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT fk_style_versions_profile FOREIGN KEY(style_profile_id) REFERENCES style_profiles (id),
    CONSTRAINT uq_style_versions_number UNIQUE (style_profile_id, version_number),
    CONSTRAINT uq_style_versions_id_profile UNIQUE (id, style_profile_id),
    CONSTRAINT ck_style_versions_dna_json CHECK (json_valid(style_dna_json))
)""",
    """CREATE TABLE style_profiles (
    id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    name TEXT NOT NULL,
    current_version_id TEXT,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT fk_styles_project FOREIGN KEY(project_id) REFERENCES projects (id),
    CONSTRAINT fk_styles_current_version_ownership FOREIGN KEY(current_version_id, id) REFERENCES style_profile_versions (id, style_profile_id),
    CONSTRAINT ck_styles_status CHECK (status IN ('active','archived'))
)""",
    """CREATE TABLE video_prompt_bundles (
    id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    prompt_project_id TEXT NOT NULL,
    prompt_project_version_id TEXT NOT NULL,
    status TEXT DEFAULT 'ready_draft' NOT NULL,
    content_mode TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    parent_input_fingerprint TEXT NOT NULL,
    source_input_fingerprint TEXT NOT NULL,
    input_fingerprint TEXT NOT NULL,
    bundle_json TEXT NOT NULL,
    bundle_sha256 TEXT NOT NULL,
    participant_manifest_json TEXT NOT NULL,
    participant_manifest_fingerprint TEXT NOT NULL,
    eligibility_evaluation_ids_json TEXT NOT NULL,
    eligibility_evaluation_ids_sha256 TEXT NOT NULL,
    eligibility_window_started_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT fk_video_bundle_prompt_project_ownership FOREIGN KEY(prompt_project_id, project_id) REFERENCES prompt_projects (id, project_id),
    CONSTRAINT fk_video_bundle_prompt_version_ownership FOREIGN KEY(prompt_project_version_id, prompt_project_id) REFERENCES prompt_project_versions (id, prompt_project_id),
    CONSTRAINT uq_video_bundle_version_input UNIQUE (prompt_project_version_id, input_fingerprint),
    CONSTRAINT ck_video_bundle_ready_draft CHECK (status = 'ready_draft'),
    CONSTRAINT ck_video_bundle_content_mode CHECK (content_mode IN ('general','drama','dark','horror','violent')),
    CONSTRAINT ck_video_bundle_schema_version CHECK (schema_version = 'imaginarium-forge.video-prompt-bundle.v1'),
    CONSTRAINT ck_video_bundle_created_at CHECK (created_at <> ''),
    CONSTRAINT ck_video_bundle_eligibility_window_started_at CHECK (eligibility_window_started_at <> ''),
    CONSTRAINT ck_json_video_bundle CHECK ((json_valid(bundle_json)) AND (json_type(bundle_json) = 'object')),
    CONSTRAINT ck_json_video_bundle_manifest CHECK ((json_valid(participant_manifest_json)) AND (json_type(participant_manifest_json) = 'object') AND (json_array_length(participant_manifest_json, '$.participants') > 0)),
    CONSTRAINT ck_json_video_bundle_eligibility_ids CHECK ((json_valid(eligibility_evaluation_ids_json)) AND (json_type(eligibility_evaluation_ids_json) = 'array') AND (json_array_length(eligibility_evaluation_ids_json) > 0)),
    CONSTRAINT ck_video_bundle_parent_input_fingerprint CHECK ((length(parent_input_fingerprint) = 64) AND (parent_input_fingerprint NOT GLOB '*[^0-9a-f]*')),
    CONSTRAINT ck_video_bundle_source_input_fingerprint CHECK ((length(source_input_fingerprint) = 64) AND (source_input_fingerprint NOT GLOB '*[^0-9a-f]*')),
    CONSTRAINT ck_video_bundle_input_fingerprint CHECK ((length(input_fingerprint) = 64) AND (input_fingerprint NOT GLOB '*[^0-9a-f]*')),
    CONSTRAINT ck_video_bundle_sha256 CHECK ((length(bundle_sha256) = 64) AND (bundle_sha256 NOT GLOB '*[^0-9a-f]*')),
    CONSTRAINT ck_video_bundle_manifest_fingerprint CHECK ((length(participant_manifest_fingerprint) = 64) AND (participant_manifest_fingerprint NOT GLOB '*[^0-9a-f]*')),
    CONSTRAINT ck_video_bundle_eligibility_ids_sha256 CHECK ((length(eligibility_evaluation_ids_sha256) = 64) AND (eligibility_evaluation_ids_sha256 NOT GLOB '*[^0-9a-f]*'))
)""",
    """CREATE TABLE world_seed_drafts (
    id TEXT NOT NULL,
    project_id TEXT,
    title TEXT NOT NULL,
    setting TEXT DEFAULT '' NOT NULL,
    time_period TEXT DEFAULT '' NOT NULL,
    world_rules_json TEXT DEFAULT '[]' NOT NULL,
    locations_json TEXT DEFAULT '[]' NOT NULL,
    social_context TEXT DEFAULT '' NOT NULL,
    technology_or_magic TEXT DEFAULT '' NOT NULL,
    central_conflict TEXT DEFAULT '' NOT NULL,
    themes_json TEXT DEFAULT '[]' NOT NULL,
    generation_mode TEXT DEFAULT 'manual' NOT NULL,
    generation_seed TEXT DEFAULT '' NOT NULL,
    status TEXT DEFAULT 'active' NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT fk_world_seed_drafts_project FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE SET NULL,
    CONSTRAINT ck_world_seed_drafts_title CHECK (length(trim(title)) > 0),
    CONSTRAINT ck_world_seed_drafts_has_content CHECK ((length(trim(setting)) > 0) OR (length(trim(time_period)) > 0) OR (json_array_length(world_rules_json) > 0) OR (json_array_length(locations_json) > 0) OR (length(trim(social_context)) > 0) OR (length(trim(technology_or_magic)) > 0) OR (length(trim(central_conflict)) > 0) OR (json_array_length(themes_json) > 0)),
    CONSTRAINT ck_world_seed_drafts_rules_json CHECK ((json_valid(world_rules_json)) AND (json_type(world_rules_json) = 'array') AND (world_rules_json = json(world_rules_json)) AND (json_array_length(world_rules_json) <= 24)),
    CONSTRAINT ck_world_seed_drafts_locations_json CHECK ((json_valid(locations_json)) AND (json_type(locations_json) = 'array') AND (locations_json = json(locations_json)) AND (json_array_length(locations_json) <= 24)),
    CONSTRAINT ck_world_seed_drafts_themes_json CHECK ((json_valid(themes_json)) AND (json_type(themes_json) = 'array') AND (themes_json = json(themes_json)) AND (json_array_length(themes_json) <= 24)),
    CONSTRAINT ck_world_seed_drafts_generation_mode CHECK (generation_mode IN ('manual','fill_blanks','reroll_all')),
    CONSTRAINT ck_world_seed_drafts_generation_seed CHECK (((generation_mode = 'manual') AND (generation_seed = '')) OR ((generation_mode IN ('fill_blanks','reroll_all')) AND (length(trim(generation_seed)) > 0))),
    CONSTRAINT ck_world_seed_drafts_status CHECK (status IN ('active','archived'))
)""",
)

_INDEXES = (
    """CREATE INDEX ix_adaptation_acceptance_parent_time ON adaptation_acceptance_events (adaptation_id, accepted_at, id)""",
    """CREATE INDEX ix_adaptation_revisions_generation_run ON adaptation_revisions (generation_run_id, version_number) WHERE generation_run_id IS NOT NULL""",
    """CREATE INDEX ix_adaptation_revisions_parent ON adaptation_revisions (adaptation_id, version_number)""",
    """CREATE INDEX ix_adaptation_runs_parent_started ON adaptation_generation_runs (adaptation_id, started_at, id)""",
    """CREATE INDEX ix_adaptation_runs_status ON adaptation_generation_runs (project_id, status, started_at)""",
    """CREATE INDEX ix_adaptations_project_type ON adaptations (project_id, adaptation_type, updated_at)""",
    """CREATE INDEX ix_adaptations_scene_status ON adaptations (source_scene_id, lifecycle_status, updated_at)""",
    """CREATE INDEX ix_chapter_plan_versions_accepted ON chapter_plan_versions (story_chapter_id, accepted)""",
    """CREATE INDEX ix_chapter_plan_versions_parent ON chapter_plan_versions (story_chapter_id)""",
    """CREATE INDEX ix_character_biography_drafts_project_status_updated ON character_biography_drafts (project_id, status, updated_at)""",
    """CREATE INDEX ix_characters_project ON characters (project_id)""",
    """CREATE INDEX ix_creative_candidate_actions_candidate_created ON creative_candidate_actions (candidate_id, created_at)""",
    """CREATE INDEX ix_creative_generation_candidates_job_status_version ON creative_generation_candidates (job_id, status, candidate_version)""",
    """CREATE INDEX ix_creative_generation_candidates_target_status_created ON creative_generation_candidates (target_type, target_id, status, created_at)""",
    """CREATE INDEX ix_creative_generation_jobs_status_updated ON creative_generation_jobs (status, updated_at)""",
    """CREATE INDEX ix_creative_generation_jobs_target_created ON creative_generation_jobs (target_type, target_id, created_at)""",
    """CREATE INDEX ix_creative_generation_steps_job_status_ordinal ON creative_generation_steps (job_id, status, ordinal)""",
    """CREATE INDEX ix_creative_unit_decisions_scope
        ON creative_unit_author_decisions(job_id, unit_id, sequence)
    """,
    """CREATE INDEX ix_elig_character_time ON character_validations (character_id, evaluated_at)""",
    """CREATE INDEX ix_elig_fingerprint ON character_validations (input_fingerprint)""",
    """CREATE INDEX ix_elig_reason ON character_validations (reason_code)""",
    """CREATE INDEX ix_exp_checkpoint ON prompt_experiment_logs (checkpoint_id)""",
    """CREATE INDEX ix_generation_runs_scene ON generation_runs (story_scene_id)""",
    """CREATE INDEX ix_generation_runs_status ON generation_runs (project_id, status)""",
    """CREATE INDEX ix_memory_entries_outline_identity ON story_memory_entries (story_outline_id, subject_id, attribute)""",
    """CREATE INDEX ix_memory_entries_proposal ON story_memory_entries (proposal_id)""",
    """CREATE INDEX ix_memory_proposals_scene ON story_memory_proposals (story_scene_id, status)""",
    """CREATE INDEX ix_outfits_character ON outfits (character_id)""",
    """CREATE INDEX ix_participants_card ON scene_card_participants (scene_card_version_id)""",
    """CREATE INDEX ix_participants_character ON scene_card_participants (character_id)""",
    """CREATE INDEX ix_pp_project ON prompt_projects (project_id)""",
    """CREATE INDEX ix_prompt_scratch_drafts_project_status_updated ON prompt_scratch_drafts (project_id, status, updated_at)""",
    """CREATE INDEX ix_prompt_scratch_recovery_existing_state ON prompt_scratch_recovery_journals (existing_draft_id, state)""",
    """CREATE INDEX ix_prompt_scratch_recovery_state_updated ON prompt_scratch_recovery_journals (state, updated_at, id)""",
    """CREATE INDEX ix_pv_version ON prompt_variants (prompt_project_version_id)""",
    """CREATE INDEX ix_scene_card_versions_accepted ON scene_card_versions (story_scene_id, accepted)""",
    """CREATE INDEX ix_scene_card_versions_parent ON scene_card_versions (story_scene_id)""",
    """CREATE INDEX ix_scene_draft_block_sets_scene ON scene_draft_block_sets (story_scene_id, scene_draft_id)""",
    """CREATE INDEX ix_scene_drafts_scene ON scene_drafts (story_scene_id)""",
    """CREATE INDEX ix_story_bible_versions_accepted ON story_bible_versions (story_bible_id, accepted)""",
    """CREATE INDEX ix_story_bible_versions_parent ON story_bible_versions (story_bible_id)""",
    """CREATE INDEX ix_story_bibles_project ON story_bibles (project_id)""",
    """CREATE INDEX ix_story_block_anchors_scene ON story_block_anchors (story_scene_id, logical_block_id)""",
    """CREATE INDEX ix_story_block_revisions_draft ON story_block_revisions (scene_draft_id, ordinal)""",
    """CREATE INDEX ix_story_block_revisions_logical ON story_block_revisions (logical_block_id, scene_draft_id)""",
    """CREATE INDEX ix_story_chapters_outline ON story_chapters (story_outline_id)""",
    """CREATE INDEX ix_story_exports_project ON story_exports (project_id)""",
    """CREATE INDEX ix_story_fragment_drafts_project_status_updated ON story_fragment_drafts (project_id, status, updated_at)""",
    """CREATE INDEX ix_story_outline_versions_accepted ON story_outline_versions (story_outline_id, accepted)""",
    """CREATE INDEX ix_story_outline_versions_parent ON story_outline_versions (story_outline_id)""",
    """CREATE INDEX ix_story_outlines_project ON story_outlines (project_id)""",
    """CREATE INDEX ix_story_requirement_versions_accepted ON story_requirement_versions (story_requirement_id, accepted)""",
    """CREATE INDEX ix_story_requirement_versions_parent ON story_requirement_versions (story_requirement_id)""",
    """CREATE INDEX ix_story_requirements_project ON story_requirements (project_id)""",
    """CREATE INDEX ix_story_scenes_chapter ON story_scenes (story_chapter_id)""",
    """CREATE INDEX ix_style_versions_profile ON style_profile_versions (style_profile_id)""",
    """CREATE INDEX ix_styles_project ON style_profiles (project_id)""",
    """CREATE INDEX ix_summaries_scene ON scene_draft_summaries (story_scene_id)""",
    """CREATE INDEX ix_versions_character ON character_versions (character_id)""",
    """CREATE INDEX ix_video_bundles_project_created ON video_prompt_bundles (project_id, created_at, id)""",
    """CREATE INDEX ix_video_bundles_prompt_version ON video_prompt_bundles (prompt_project_version_id, created_at, id)""",
    """CREATE INDEX ix_world_seed_drafts_project_status_updated ON world_seed_drafts (project_id, status, updated_at)""",
    """CREATE UNIQUE INDEX uq_characters_id_project ON characters (id, project_id)""",
    """CREATE UNIQUE INDEX uq_generation_runs_output_ownership ON generation_runs (id, project_id, story_scene_id, scene_card_version_id)""",
    """CREATE UNIQUE INDEX uq_memory_proposal_one_accepted_per_draft ON story_memory_proposals (scene_draft_id) WHERE status = 'accepted'""",
    """CREATE UNIQUE INDEX uq_memory_proposal_one_pending_payload ON story_memory_proposals (scene_draft_id, proposal_sha256, base_memory_fingerprint) WHERE status = 'pending'""",
    """CREATE UNIQUE INDEX uq_participant_single_pov
        ON scene_card_participants (scene_card_version_id)
        WHERE is_pov = 1
        """,
    """CREATE UNIQUE INDEX uq_prompt_projects_video_ownership ON prompt_projects (id, project_id)""",
    """CREATE UNIQUE INDEX uq_prompt_scratch_recovery_reserved_draft_id ON prompt_scratch_recovery_journals (reserved_draft_id) WHERE reserved_draft_id IS NOT NULL""",
    """CREATE UNIQUE INDEX uq_scene_drafts_adaptation_chain ON scene_drafts (id, project_id, story_scene_id, scene_card_version_id)""",
    """CREATE UNIQUE INDEX uq_scene_drafts_output_ownership ON scene_drafts (id, project_id, story_scene_id, scene_card_version_id, draft_status)""",
    """CREATE UNIQUE INDEX uq_story_chapters_adaptation_chain ON story_chapters (id, project_id, story_outline_id)""",
    """CREATE UNIQUE INDEX uq_story_scenes_adaptation_chain ON story_scenes (id, project_id, story_chapter_id)""",
    """CREATE UNIQUE INDEX uq_style_profiles_id_project ON style_profiles (id, project_id)""",
)

_TRIGGERS = (
    """CREATE TRIGGER adaptation_acceptance_events_write_once_delete
        BEFORE DELETE ON adaptation_acceptance_events
        BEGIN
            SELECT RAISE(ABORT, 'adaptation_acceptance_events rows are historical evidence');
        END""",
    """CREATE TRIGGER adaptation_acceptance_events_write_once_update
        BEFORE UPDATE ON adaptation_acceptance_events
        BEGIN
            SELECT RAISE(ABORT, 'adaptation_acceptance_events rows are immutable');
        END""",
    """CREATE TRIGGER adaptation_acceptance_exact_state_insert
        BEFORE INSERT ON adaptation_acceptance_events
        WHEN
    NOT EXISTS (
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
        END""",
    """CREATE TRIGGER adaptation_revision_exact_run_insert
        BEFORE INSERT ON adaptation_revisions
        WHEN
    (NEW.origin IN ('ollama','openai')) AND (NOT EXISTS (
            SELECT 1
            FROM adaptation_generation_runs AS run
            JOIN adaptations AS adaptation
              ON adaptation.id = run.adaptation_id
             AND adaptation.project_id = run.project_id
            WHERE run.id = NEW.generation_run_id
              AND run.adaptation_id = NEW.adaptation_id
              AND run.project_id = NEW.project_id
              AND run.provider = NEW.origin
              AND run.status IN ('running')
              AND (run.expected_working_revision_id IS NEW.parent_revision_id OR EXISTS (SELECT 1 FROM adaptation_revisions AS parent WHERE parent.id = NEW.parent_revision_id AND parent.adaptation_id = NEW.adaptation_id AND parent.project_id = NEW.project_id AND parent.generation_run_id = NEW.generation_run_id))
              AND run.source_snapshot_sha256 = adaptation.source_snapshot_sha256
        ))
BEGIN
            SELECT RAISE(ABORT,
                'adaptation_revisions: AI revision requires its exact active run');
        END""",
    """CREATE TRIGGER adaptation_revisions_write_once_delete
        BEFORE DELETE ON adaptation_revisions
        BEGIN
            SELECT RAISE(ABORT, 'adaptation_revisions rows are historical evidence');
        END""",
    """CREATE TRIGGER adaptation_revisions_write_once_update
        BEFORE UPDATE ON adaptation_revisions
        BEGIN
            SELECT RAISE(ABORT, 'adaptation_revisions rows are immutable');
        END""",
    """CREATE TRIGGER adaptation_runs_assurance_integrity_update
        BEFORE UPDATE ON adaptation_generation_runs
        WHEN
    (NEW.assurance_json <> '{}') AND (NOT EXISTS (
              SELECT 1 FROM json_tree(NEW.assurance_json)
              WHERE lower(key) IN (
                  'api_key','apikey','openai_api_key','authorization',
                  'authorization_header','bearer_token','access_token',
                  'refresh_token','credential','credentials','credential_value',
                  'text','prompt','system','instruction','context_text',
                  'rolling_summary','protected_facts','raw','raw_response',
                  'content_text'
              )
          )) AND ((json_type(NEW.assurance_json, '$.result.calls') IS NOT 'array') OR (json_array_length(NEW.assurance_json, '$.result.calls') < 1) OR (json_type(NEW.assurance_json, '$.result.progress') IS NOT 'object') OR (json_type(
                NEW.assurance_json, '$.result.progress.provider_attempts'
            ) IS NOT 'integer') OR (json_type(
                NEW.assurance_json, '$.result.progress.runtime_provider_attempts'
            ) IS NOT 'integer') OR (json_type(
                NEW.assurance_json, '$.result.progress.estimated_cost_microunits'
            ) IS NOT 'integer') OR (EXISTS (
                SELECT 1
                FROM json_each(NEW.assurance_json, '$.result.calls') AS call
                WHERE json_type(call.value, '$.runtime_attempts_used') IS NOT 'integer'
                   OR json_extract(call.value, '$.runtime_attempts_used') NOT BETWEEN 0 AND 3
                   OR json_type(
                        call.value, '$.estimated_cost_microunits'
                      ) IS NOT 'integer'
                   OR json_extract(call.value, '$.estimated_cost_microunits') < 0
                   OR (
                        json_extract(call.value, '$.runtime_attempts_used') = 0
                        AND json_extract(call.value, '$.estimated_cost_microunits') <> 0
                      )
                   OR (
                        json_extract(call.value, '$.runtime_attempts_used') > 0
                        AND (
                            json_extract(call.value, '$.estimated_cost_microunits') <= 0
                            OR json_extract(call.value, '$.estimated_cost_microunits')
                               % json_extract(call.value, '$.runtime_attempts_used') <> 0
                        )
                      )
            )) OR (json_extract(
                NEW.assurance_json, '$.result.progress.provider_attempts'
            ) IS NOT json_array_length(NEW.assurance_json, '$.result.calls')) OR (json_extract(
                NEW.assurance_json, '$.result.progress.runtime_provider_attempts'
            ) IS NOT (
                SELECT coalesce(sum(json_extract(
                    call.value, '$.runtime_attempts_used'
                )), 0)
                FROM json_each(NEW.assurance_json, '$.result.calls') AS call
            )) OR (json_extract(
                NEW.assurance_json, '$.result.progress.estimated_cost_microunits'
            ) IS NOT (
                SELECT coalesce(sum(json_extract(
                    call.value, '$.estimated_cost_microunits'
                )), 0)
                FROM json_each(NEW.assurance_json, '$.result.calls') AS call
            )) OR ((json_extract(NEW.assurance_json, '$.selected_candidate') = 'accumulated') AND ((json_type(
                        NEW.assurance_json, '$.result.accumulated_candidate'
                    ) IS NOT 'object') OR (json_extract(
                        NEW.assurance_json,
                        '$.result.accumulated_candidate.text_sha256'
                    ) IS NOT json_extract(
                        NEW.assurance_json, '$.selected_output_sha256'
                    )))) OR ((json_extract(NEW.assurance_json, '$.selected_candidate') = 'condensed') AND ((json_type(
                        NEW.assurance_json, '$.result.condensed_candidate'
                    ) IS NOT 'object') OR (json_type(
                        NEW.assurance_json, '$.result.condensation_decision'
                    ) IS NOT 'object') OR (json_extract(
                        NEW.assurance_json,
                        '$.result.condensed_candidate.text_sha256'
                    ) IS NOT json_extract(
                        NEW.assurance_json, '$.selected_output_sha256'
                    )))) OR (json_extract(
                NEW.assurance_json, '$.selected_reason'
            ) IS NOT CASE
                WHEN json_extract(
                    NEW.assurance_json, '$.selected_candidate'
                ) = 'condensed'
                THEN json_extract(
                    NEW.assurance_json, '$.result.condensation_decision.reason'
                )
                ELSE json_extract(NEW.assurance_json, '$.result.decision.reason')
            END) OR (json_extract(
                NEW.assurance_json, '$.domain_complete'
            ) IS NOT CASE
                WHEN json_extract(
                    NEW.assurance_json, '$.selected_candidate'
                ) = 'condensed'
                THEN json_extract(
                    NEW.assurance_json,
                    '$.result.condensation_decision.domain_complete'
                )
                ELSE json_extract(
                    NEW.assurance_json, '$.result.decision.domain_complete'
                )
            END) OR (NEW.status = 'running') OR ((NEW.status = 'succeeded') AND ((json_extract(NEW.assurance_json, '$.domain_complete') IS NOT 1) OR (json_extract(
                    NEW.assurance_json, '$.selected_candidate'
                ) = 'none') OR (json_extract(
                    NEW.assurance_json, '$.selected_reason'
                ) NOT IN ('length_satisfied','advisory_miss')) OR (json_extract(
                    NEW.assurance_json, '$.selected_output_sha256'
                ) IS NOT NEW.output_sha256))) OR ((NEW.status = 'partial') AND ((json_extract(NEW.assurance_json, '$.domain_complete') IS NOT 0) OR (json_extract(
                    NEW.assurance_json, '$.selected_candidate'
                ) = 'none') OR (json_extract(
                    NEW.assurance_json, '$.selected_reason'
                ) IS NOT NEW.reason_code) OR (json_extract(
                    NEW.assurance_json, '$.selected_output_sha256'
                ) IS NOT NEW.output_sha256))) OR ((NEW.status = 'cancelled') AND ((json_extract(NEW.assurance_json, '$.domain_complete') IS NOT 0) OR (json_extract(
                    NEW.assurance_json, '$.selected_candidate'
                ) IS NOT 'none') OR (json_extract(
                    NEW.assurance_json, '$.selected_reason'
                ) IS NOT 'cancelled_by_author'))) OR ((NEW.status = 'failed') AND ((json_extract(NEW.assurance_json, '$.domain_complete') IS NOT 0) OR (json_extract(
                        NEW.assurance_json, '$.selected_candidate'
                    ) IS NOT 'none'))))
BEGIN
            SELECT RAISE(ABORT,
                'adaptation_generation_runs: assurance evidence is inconsistent');
        END""",
    """CREATE TRIGGER adaptation_runs_assurance_no_raw_insert
            BEFORE INSERT ON adaptation_generation_runs
            WHEN
    EXISTS (
                SELECT 1 FROM json_tree(NEW.assurance_json)
                WHERE lower(key) IN ('api_key','apikey','openai_api_key','authorization','authorization_header','bearer_token','access_token','refresh_token','credential','credentials','credential_value','text','prompt','system','instruction','context_text','rolling_summary','protected_facts','raw','raw_response','content_text')
            )
BEGIN
                SELECT RAISE(ABORT,
                    'adaptation_generation_runs: assurance JSON must be hash-only');
            END""",
    """CREATE TRIGGER adaptation_runs_assurance_no_raw_update
            BEFORE UPDATE ON adaptation_generation_runs
            WHEN
    EXISTS (
                SELECT 1 FROM json_tree(NEW.assurance_json)
                WHERE lower(key) IN ('api_key','apikey','openai_api_key','authorization','authorization_header','bearer_token','access_token','refresh_token','credential','credentials','credential_value','text','prompt','system','instruction','context_text','rolling_summary','protected_facts','raw','raw_response','content_text')
            )
BEGIN
                SELECT RAISE(ABORT,
                    'adaptation_generation_runs: assurance JSON must be hash-only');
            END""",
    """CREATE TRIGGER adaptation_runs_assurance_transition
        BEFORE UPDATE ON adaptation_generation_runs
        WHEN
    (OLD.assurance_json <> NEW.assurance_json) AND (NOT ((OLD.status = 'running') AND (NEW.status <> 'running')))
BEGIN
            SELECT RAISE(ABORT,
                'adaptation_generation_runs: assurance changes only on finalization');
        END""",
    """CREATE TRIGGER adaptation_runs_cancelled_without_revision
            BEFORE UPDATE ON adaptation_generation_runs
            WHEN
    (NEW.status = 'cancelled') AND (EXISTS (
                SELECT 1 FROM adaptation_revisions AS revision
                WHERE revision.generation_run_id = NEW.id
            ))
BEGIN
                SELECT RAISE(ABORT,
                    'adaptation_generation_runs: cancelled run cannot own a revision');
            END""",
    """CREATE TRIGGER adaptation_runs_exact_source_insert
        BEFORE INSERT ON adaptation_generation_runs
        WHEN
    NOT EXISTS (
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
        END""",
    """CREATE TRIGGER adaptation_runs_failed_without_revision
        BEFORE UPDATE ON adaptation_generation_runs
        WHEN
    (NEW.status = 'failed') AND (EXISTS (
            SELECT 1 FROM adaptation_revisions AS revision
            WHERE revision.generation_run_id = NEW.id
        ))
BEGIN
            SELECT RAISE(ABORT,
                'adaptation_generation_runs: failed run cannot own a revision');
        END""",
    """CREATE TRIGGER adaptation_runs_finalize_once
        BEFORE UPDATE ON adaptation_generation_runs
        WHEN
    OLD.status IN ('succeeded','partial','failed','cancelled')
BEGIN
            SELECT RAISE(ABORT, 'adaptation_generation_runs: finalized runs are immutable');
        END""",
    """CREATE TRIGGER adaptation_runs_identity_immutable
        BEFORE UPDATE ON adaptation_generation_runs
        WHEN
    (OLD.id <> NEW.id) OR (OLD.adaptation_id <> NEW.adaptation_id) OR (OLD.project_id <> NEW.project_id) OR (OLD.source_snapshot_sha256 <> NEW.source_snapshot_sha256) OR (OLD.expected_working_revision_id IS NOT NEW.expected_working_revision_id) OR (OLD.provider <> NEW.provider) OR (OLD.model <> NEW.model) OR (OLD.brief_json <> NEW.brief_json) OR (OLD.options_json <> NEW.options_json) OR (OLD.input_snapshot_json <> NEW.input_snapshot_json) OR (OLD.input_snapshot_sha256 <> NEW.input_snapshot_sha256) OR (OLD.participant_manifest_json <> NEW.participant_manifest_json) OR (OLD.participant_manifest_sha256 <> NEW.participant_manifest_sha256) OR (OLD.eligibility_evaluation_ids_json <> NEW.eligibility_evaluation_ids_json) OR (OLD.system_message_sha256 <> NEW.system_message_sha256) OR (OLD.user_message_sha256 <> NEW.user_message_sha256) OR (OLD.system_message_byte_size <> NEW.system_message_byte_size) OR (OLD.user_message_byte_size <> NEW.user_message_byte_size) OR (OLD.rendered_system_message IS NOT NEW.rendered_system_message) OR (OLD.rendered_user_message IS NOT NEW.rendered_user_message) OR (OLD.rendered_message_storage_enabled <>
                NEW.rendered_message_storage_enabled) OR (OLD.started_at <> NEW.started_at)
BEGIN
            SELECT RAISE(ABORT, 'adaptation_generation_runs: identity is immutable');
        END""",
    """CREATE TRIGGER adaptation_runs_no_delete
        BEFORE DELETE ON adaptation_generation_runs
        BEGIN
            SELECT RAISE(ABORT,
                'adaptation_generation_runs rows are historical evidence');
        END""",
    """CREATE TRIGGER adaptation_runs_partial_revision_update
            BEFORE UPDATE ON adaptation_generation_runs
            WHEN
    (NEW.status = 'partial') AND (NOT EXISTS (
                SELECT 1
                FROM adaptation_revisions AS revision
                WHERE revision.id = NEW.resulting_revision_id
                  AND revision.adaptation_id = NEW.adaptation_id
                  AND revision.project_id = NEW.project_id
                  AND revision.generation_run_id = NEW.id
                  AND revision.screenplay_sha256 = NEW.output_sha256
                  AND revision.completion_status = 'partial'
            ))
BEGIN
                SELECT RAISE(ABORT,
                    'adaptation_generation_runs: partial run requires exact revision');
            END""",
    """CREATE TRIGGER adaptation_runs_start_running_insert
        BEFORE INSERT ON adaptation_generation_runs
        WHEN
    NEW.status <> 'running'
BEGIN
            SELECT RAISE(ABORT,
                'adaptation_generation_runs: runs must start as running');
        END""",
    """CREATE TRIGGER adaptation_runs_succeeded_revision_update
        BEFORE UPDATE ON adaptation_generation_runs
        WHEN
    (NEW.status = 'succeeded') AND (NOT EXISTS (
            SELECT 1
            FROM adaptation_revisions AS revision
            WHERE revision.id = NEW.resulting_revision_id
              AND revision.adaptation_id = NEW.adaptation_id
              AND revision.project_id = NEW.project_id
              AND revision.generation_run_id = NEW.id
              AND revision.screenplay_sha256 = NEW.output_sha256
              AND revision.completion_status = 'complete'
        ))
BEGIN
            SELECT RAISE(ABORT,
                'adaptation_generation_runs: succeeded run requires exact revision');
        END""",
    """CREATE TRIGGER adaptations_accepted_pointer_requires_event
        BEFORE UPDATE OF accepted_revision_id ON adaptations
        WHEN
    (OLD.accepted_revision_id IS NOT NEW.accepted_revision_id) AND (NOT EXISTS (
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
          ))
BEGIN
            SELECT RAISE(ABORT,
                'adaptations: accepted pointer requires a matching acceptance event');
        END""",
    """CREATE TRIGGER adaptations_exact_accepted_source_insert
        BEFORE INSERT ON adaptations
        WHEN
    NOT EXISTS (
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
        END""",
    """CREATE TRIGGER adaptations_no_delete
        BEFORE DELETE ON adaptations
        BEGIN
            SELECT RAISE(ABORT, 'adaptations are archived, never deleted');
        END""",
    """CREATE TRIGGER adaptations_source_identity_immutable
        BEFORE UPDATE ON adaptations
        WHEN
    (OLD.id <> NEW.id) OR (OLD.project_id <> NEW.project_id) OR (OLD.adaptation_type <> NEW.adaptation_type) OR (OLD.source_outline_id <> NEW.source_outline_id) OR (OLD.source_chapter_id <> NEW.source_chapter_id) OR (OLD.source_scene_id <> NEW.source_scene_id) OR (OLD.source_draft_id <> NEW.source_draft_id) OR (OLD.source_scene_card_version_id <> NEW.source_scene_card_version_id) OR (OLD.source_prose_sha256 <> NEW.source_prose_sha256) OR (OLD.source_scene_card_sha256 <> NEW.source_scene_card_sha256) OR (OLD.participant_manifest_json <> NEW.participant_manifest_json) OR (OLD.participant_manifest_sha256 <> NEW.participant_manifest_sha256) OR (OLD.source_snapshot_json <> NEW.source_snapshot_json) OR (OLD.source_snapshot_sha256 <> NEW.source_snapshot_sha256) OR (OLD.content_mode <> NEW.content_mode) OR (OLD.supersedes_adaptation_id IS NOT NEW.supersedes_adaptation_id) OR (OLD.created_at <> NEW.created_at)
BEGIN
            SELECT RAISE(ABORT, 'adaptations: source identity is immutable');
        END""",
    """CREATE TRIGGER adaptations_supersedes_scope_insert
        BEFORE INSERT ON adaptations
        WHEN
    (NEW.supersedes_adaptation_id IS NOT NULL) AND ((NEW.supersedes_adaptation_id = NEW.id) OR (NOT EXISTS (
            SELECT 1 FROM adaptations AS previous
            WHERE previous.id = NEW.supersedes_adaptation_id
              AND previous.project_id = NEW.project_id
              AND previous.source_scene_id = NEW.source_scene_id
              AND previous.adaptation_type = NEW.adaptation_type
          )))
BEGIN
            SELECT RAISE(ABORT,
                'adaptations: superseded root must be another same-scope adaptation');
        END""",
    """CREATE TRIGGER ai_runtime_preferences_routes_insert
        BEFORE INSERT ON ai_runtime_preferences
        WHEN
    EXISTS (
    SELECT 1
    FROM json_each(NEW.task_routes_json) AS route
    WHERE route.type <> 'object'
       OR length(route.key) NOT BETWEEN 1 AND 64
       OR substr(route.key, 1, 1) NOT BETWEEN 'a' AND 'z'
       OR route.key GLOB '*[^a-z0-9_]*'
       OR NOT EXISTS (SELECT 1 FROM json_each(route.value))
       OR EXISTS (
           SELECT 1 FROM json_each(route.value) AS setting
           WHERE setting.key NOT IN ('provider','model','quality_profile','fallback_policy')
              OR (setting.key = 'provider' AND (
                    setting.type <> 'text'
                    OR setting.value NOT IN ('offline','ollama','openai')
                 ))
              OR (setting.key = 'model' AND (
                    setting.type <> 'text'
                    OR length(setting.value) NOT BETWEEN 1 AND 256
                    OR setting.value <> trim(setting.value)
                    OR setting.value GLOB '*[[:space:]]*'
                 ))
              OR (setting.key = 'quality_profile' AND (
                    setting.type <> 'text'
                    OR setting.value NOT IN (
                        'quick_idea','standard_draft','deep_long_form',
                        'polish_review','image_prompt'
                    )
                 ))
              OR (setting.key = 'fallback_policy' AND (
                    setting.type <> 'text'
                    OR setting.value NOT IN (
                        'fail_closed','ollama_then_fail','offline_then_fail'
                    )
                 ))
       )
)
BEGIN
            SELECT RAISE(ABORT, 'ai_runtime_preferences task routes are invalid');
        END""",
    """CREATE TRIGGER ai_runtime_preferences_routes_update
        BEFORE UPDATE OF task_routes_json ON ai_runtime_preferences
        WHEN
    EXISTS (
    SELECT 1
    FROM json_each(NEW.task_routes_json) AS route
    WHERE route.type <> 'object'
       OR length(route.key) NOT BETWEEN 1 AND 64
       OR substr(route.key, 1, 1) NOT BETWEEN 'a' AND 'z'
       OR route.key GLOB '*[^a-z0-9_]*'
       OR NOT EXISTS (SELECT 1 FROM json_each(route.value))
       OR EXISTS (
           SELECT 1 FROM json_each(route.value) AS setting
           WHERE setting.key NOT IN ('provider','model','quality_profile','fallback_policy')
              OR (setting.key = 'provider' AND (
                    setting.type <> 'text'
                    OR setting.value NOT IN ('offline','ollama','openai')
                 ))
              OR (setting.key = 'model' AND (
                    setting.type <> 'text'
                    OR length(setting.value) NOT BETWEEN 1 AND 256
                    OR setting.value <> trim(setting.value)
                    OR setting.value GLOB '*[[:space:]]*'
                 ))
              OR (setting.key = 'quality_profile' AND (
                    setting.type <> 'text'
                    OR setting.value NOT IN (
                        'quick_idea','standard_draft','deep_long_form',
                        'polish_review','image_prompt'
                    )
                 ))
              OR (setting.key = 'fallback_policy' AND (
                    setting.type <> 'text'
                    OR setting.value NOT IN (
                        'fail_closed','ollama_then_fail','offline_then_fail'
                    )
                 ))
       )
)
BEGIN
            SELECT RAISE(ABORT, 'ai_runtime_preferences task routes are invalid');
        END""",
    """CREATE TRIGGER chapter_plan_versions_accepted_immutable_delete
        BEFORE DELETE ON chapter_plan_versions
        WHEN
    OLD.accepted = 1
BEGIN
            SELECT RAISE(ABORT, 'chapter_plan_versions: accepted versions are immutable');
        END""",
    """CREATE TRIGGER chapter_plan_versions_accepted_immutable_update
        BEFORE UPDATE ON chapter_plan_versions
        WHEN
    OLD.accepted = 1
BEGIN
            SELECT RAISE(ABORT, 'chapter_plan_versions: accepted versions are immutable');
        END""",
    """CREATE TRIGGER creative_candidate_actions_no_credentials_insert
        BEFORE INSERT ON creative_candidate_actions
        WHEN
    EXISTS (
            SELECT 1 FROM (SELECT key FROM json_tree(COALESCE(NEW.selection_json, '{}')) UNION ALL SELECT key FROM json_tree(COALESCE(NEW.before_snapshot_json, '{}')) UNION ALL SELECT key FROM json_tree(COALESCE(NEW.after_snapshot_json, '{}')))
            WHERE lower(key) IN ('api_key','apikey','openai_api_key','authorization','authorization_header','bearer_token','access_token','refresh_token','credential','credentials','credential_value')
        )
BEGIN
            SELECT RAISE(ABORT, 'creative_candidate_actions JSON must not contain credentials');
        END""",
    """CREATE TRIGGER creative_candidate_actions_no_credentials_update
        BEFORE UPDATE OF selection_json,before_snapshot_json,after_snapshot_json ON creative_candidate_actions
        WHEN
    EXISTS (
            SELECT 1 FROM (SELECT key FROM json_tree(COALESCE(NEW.selection_json, '{}')) UNION ALL SELECT key FROM json_tree(COALESCE(NEW.before_snapshot_json, '{}')) UNION ALL SELECT key FROM json_tree(COALESCE(NEW.after_snapshot_json, '{}')))
            WHERE lower(key) IN ('api_key','apikey','openai_api_key','authorization','authorization_header','bearer_token','access_token','refresh_token','credential','credentials','credential_value')
        )
BEGIN
            SELECT RAISE(ABORT, 'creative_candidate_actions JSON must not contain credentials');
        END""",
    """CREATE TRIGGER creative_generation_candidates_no_credentials_insert
        BEFORE INSERT ON creative_generation_candidates
        WHEN
    EXISTS (
            SELECT 1 FROM (SELECT key FROM json_tree(COALESCE(NEW.base_snapshot_json, '{}')) UNION ALL SELECT key FROM json_tree(COALESCE(NEW.content_json, '{}')) UNION ALL SELECT key FROM json_tree(COALESCE(NEW.quality_report_json, '{}')) UNION ALL SELECT key FROM json_tree(COALESCE(NEW.provenance_json, '{}')))
            WHERE lower(key) IN ('api_key','apikey','openai_api_key','authorization','authorization_header','bearer_token','access_token','refresh_token','credential','credentials','credential_value')
        )
BEGIN
            SELECT RAISE(ABORT, 'creative_generation_candidates JSON must not contain credentials');
        END""",
    """CREATE TRIGGER creative_generation_candidates_no_credentials_update
        BEFORE UPDATE OF base_snapshot_json,content_json,quality_report_json,provenance_json ON creative_generation_candidates
        WHEN
    EXISTS (
            SELECT 1 FROM (SELECT key FROM json_tree(COALESCE(NEW.base_snapshot_json, '{}')) UNION ALL SELECT key FROM json_tree(COALESCE(NEW.content_json, '{}')) UNION ALL SELECT key FROM json_tree(COALESCE(NEW.quality_report_json, '{}')) UNION ALL SELECT key FROM json_tree(COALESCE(NEW.provenance_json, '{}')))
            WHERE lower(key) IN ('api_key','apikey','openai_api_key','authorization','authorization_header','bearer_token','access_token','refresh_token','credential','credentials','credential_value')
        )
BEGIN
            SELECT RAISE(ABORT, 'creative_generation_candidates JSON must not contain credentials');
        END""",
    """CREATE TRIGGER creative_generation_jobs_no_credentials_insert
        BEFORE INSERT ON creative_generation_jobs
        WHEN
    EXISTS (
            SELECT 1 FROM (SELECT key FROM json_tree(COALESCE(NEW.route_json, '{}')) UNION ALL SELECT key FROM json_tree(COALESCE(NEW.length_spec_json, '{}')) UNION ALL SELECT key FROM json_tree(COALESCE(NEW.source_snapshot_json, '{}')) UNION ALL SELECT key FROM json_tree(COALESCE(NEW.context_snapshot_json, '{}')))
            WHERE lower(key) IN ('api_key','apikey','openai_api_key','authorization','authorization_header','bearer_token','access_token','refresh_token','credential','credentials','credential_value')
        )
BEGIN
            SELECT RAISE(ABORT, 'creative_generation_jobs JSON must not contain credentials');
        END""",
    """CREATE TRIGGER creative_generation_jobs_no_credentials_update
        BEFORE UPDATE OF route_json,length_spec_json,source_snapshot_json,context_snapshot_json ON creative_generation_jobs
        WHEN
    EXISTS (
            SELECT 1 FROM (SELECT key FROM json_tree(COALESCE(NEW.route_json, '{}')) UNION ALL SELECT key FROM json_tree(COALESCE(NEW.length_spec_json, '{}')) UNION ALL SELECT key FROM json_tree(COALESCE(NEW.source_snapshot_json, '{}')) UNION ALL SELECT key FROM json_tree(COALESCE(NEW.context_snapshot_json, '{}')))
            WHERE lower(key) IN ('api_key','apikey','openai_api_key','authorization','authorization_header','bearer_token','access_token','refresh_token','credential','credentials','credential_value')
        )
BEGIN
            SELECT RAISE(ABORT, 'creative_generation_jobs JSON must not contain credentials');
        END""",
    """CREATE TRIGGER creative_generation_steps_no_credentials_insert
        BEFORE INSERT ON creative_generation_steps
        WHEN
    EXISTS (
            SELECT 1 FROM (SELECT key FROM json_tree(COALESCE(NEW.input_snapshot_json, '{}')) UNION ALL SELECT key FROM json_tree(COALESCE(NEW.output_json, '{}')) UNION ALL SELECT key FROM json_tree(COALESCE(NEW.attempt_history_json, '{}')))
            WHERE lower(key) IN ('api_key','apikey','openai_api_key','authorization','authorization_header','bearer_token','access_token','refresh_token','credential','credentials','credential_value')
        )
BEGIN
            SELECT RAISE(ABORT, 'creative_generation_steps JSON must not contain credentials');
        END""",
    """CREATE TRIGGER creative_generation_steps_no_credentials_update
        BEFORE UPDATE OF input_snapshot_json,output_json,attempt_history_json ON creative_generation_steps
        WHEN
    EXISTS (
            SELECT 1 FROM (SELECT key FROM json_tree(COALESCE(NEW.input_snapshot_json, '{}')) UNION ALL SELECT key FROM json_tree(COALESCE(NEW.output_json, '{}')) UNION ALL SELECT key FROM json_tree(COALESCE(NEW.attempt_history_json, '{}')))
            WHERE lower(key) IN ('api_key','apikey','openai_api_key','authorization','authorization_header','bearer_token','access_token','refresh_token','credential','credentials','credential_value')
        )
BEGIN
            SELECT RAISE(ABORT, 'creative_generation_steps JSON must not contain credentials');
        END""",
    """CREATE TRIGGER creative_unit_decisions_immutable_delete
            BEFORE DELETE ON creative_unit_author_decisions
            BEGIN SELECT RAISE(ABORT, 'unit author decisions are immutable'); END""",
    """CREATE TRIGGER creative_unit_decisions_immutable_update
            BEFORE UPDATE ON creative_unit_author_decisions
            BEGIN SELECT RAISE(ABORT, 'unit author decisions are immutable'); END""",
    """CREATE TRIGGER creative_unit_decisions_no_credentials
        BEFORE INSERT ON creative_unit_author_decisions
        WHEN
    EXISTS (
            SELECT 1 FROM json_tree(NEW.payload_json)
            WHERE lower(replace(replace(trim(key, char(9,10,11,12,13,28,29,30,31,32,133,160,5760,
                8192,8193,8194,8195,8196,8197,8198,8199,8200,8201,8202,8232,8233,
                8239,8287,12288)), char(8490), 'k'), char(383), 's')) IN (
                'api_key','apikey','openai_api_key','authorization','authorization_header',
                'bearer_token','access_token','refresh_token','credential','credentials',
                'credential_value'
            )
        )
BEGIN SELECT RAISE(ABORT, 'unit author decision JSON must not contain credentials'); END""",
    """CREATE TRIGGER creative_unit_decisions_no_replace
        BEFORE INSERT ON creative_unit_author_decisions
        WHEN
    EXISTS (
            SELECT 1 FROM creative_unit_author_decisions
            WHERE id = NEW.id OR (job_id = NEW.job_id AND sequence = NEW.sequence)
        )
BEGIN SELECT RAISE(ABORT, 'unit author decisions cannot be replaced'); END""",
    """CREATE TRIGGER creative_unit_decisions_ownership
        BEFORE INSERT ON creative_unit_author_decisions
        WHEN
    NOT EXISTS (
            SELECT 1 FROM creative_generation_steps AS step
            WHERE step.id = NEW.step_id AND step.job_id = NEW.job_id
            AND step.step_kind IN ('prose_chunk', 'targeted_revision', 'length_assurance')
            AND json_extract(step.input_snapshot_json, '$.planned.unit.id') = NEW.unit_id
        )
BEGIN SELECT RAISE(ABORT, 'unit author decision scope mismatch'); END""",
    """CREATE TRIGGER generation_runs_finalize_once_update
        BEFORE UPDATE ON generation_runs
        WHEN
    OLD.status <> 'running'
BEGIN
            SELECT RAISE(ABORT,
                'generation_runs: finalized runs are immutable');
        END""",
    """CREATE TRIGGER generation_runs_identity_immutable
        BEFORE UPDATE ON generation_runs
        WHEN
    (OLD.project_id <> NEW.project_id) OR (OLD.story_scene_id <> NEW.story_scene_id) OR (OLD.scene_card_version_id <> NEW.scene_card_version_id) OR (OLD.input_snapshot_sha256 <> NEW.input_snapshot_sha256) OR (OLD.context_fingerprint <> NEW.context_fingerprint) OR (OLD.eligibility_fingerprint <> NEW.eligibility_fingerprint) OR (OLD.content_mode <> NEW.content_mode) OR (OLD.started_at <> NEW.started_at)
BEGIN
            SELECT RAISE(ABORT,
                'generation_runs: audit identity is immutable');
        END""",
    """CREATE TRIGGER generation_runs_length_assurance_accounting
        BEFORE UPDATE OF length_assurance_json ON generation_runs
        WHEN
    (NEW.length_assurance_json <> '{}') AND ((COALESCE((
                SELECT sum(
                    json_extract(value, '$.runtime_provider_attempts')
                )
                FROM json_each(
                    NEW.length_assurance_json,
                    '$.attempt_ledger'
                )
            ), 0) <> json_extract(
                NEW.length_assurance_json,
                '$.runtime_provider_attempts'
            )) OR (EXISTS (
                SELECT 1
                FROM json_each(
                    NEW.length_assurance_json,
                    '$.attempt_ledger'
                )
                WHERE json_type(
                    value,
                    '$.runtime_provider_attempts'
                ) <> 'integer'
                   OR json_extract(
                        value,
                        '$.runtime_provider_attempts'
                   ) < 1
                   OR json_extract(
                        value,
                        '$.runtime_provider_attempts'
                   ) > json_extract(
                        NEW.length_assurance_json,
                        '$.runtime_provider_attempt_ceiling_per_logical_call'
                   )
            )) OR (json_type(
                NEW.input_snapshot_json,
                '$.options.max_output_tokens'
            ) <> 'integer') OR (json_extract(
                NEW.input_snapshot_json,
                '$.options.max_output_tokens'
            ) < 1) OR (json_extract(
                NEW.length_assurance_json,
                '$.estimated_cost_microunits'
            ) <> json_extract(
                NEW.input_snapshot_json,
                '$.options.max_output_tokens'
            ) * json_extract(
                NEW.length_assurance_json,
                '$.runtime_provider_attempts'
            )) OR (json_extract(
                NEW.length_assurance_json,
                '$.estimated_cost_ceiling_microunits'
            ) <> json_extract(
                NEW.input_snapshot_json,
                '$.options.max_output_tokens'
            ) * json_extract(
                NEW.length_assurance_json,
                '$.runtime_provider_attempt_ceiling'
            )))
BEGIN
            SELECT RAISE(ABORT,
                'generation_runs: invalid length assurance accounting');
        END""",
    """CREATE TRIGGER generation_runs_length_assurance_empty_insert
        BEFORE INSERT ON generation_runs
        WHEN
    NEW.length_assurance_json <> '{}'
BEGIN
            SELECT RAISE(ABORT,
                'generation_runs: length assurance must start empty');
        END""",
    """CREATE TRIGGER generation_runs_length_assurance_no_raw_insert
            BEFORE INSERT ON generation_runs
            WHEN
    EXISTS (
                SELECT 1 FROM json_tree(NEW.length_assurance_json)
                WHERE lower(key) IN ('api_key','apikey','openai_api_key','authorization','authorization_header','bearer_token','access_token','refresh_token','credential','credentials','credential_value','text','prompt','system','instruction','context_text','rolling_summary','protected_facts','raw','raw_response','content_text')
            )
BEGIN
                SELECT RAISE(ABORT,
                    'generation_runs: length assurance JSON must be hash-only');
            END""",
    """CREATE TRIGGER generation_runs_length_assurance_no_raw_update
            BEFORE UPDATE ON generation_runs
            WHEN
    EXISTS (
                SELECT 1 FROM json_tree(NEW.length_assurance_json)
                WHERE lower(key) IN ('api_key','apikey','openai_api_key','authorization','authorization_header','bearer_token','access_token','refresh_token','credential','credentials','credential_value','text','prompt','system','instruction','context_text','rolling_summary','protected_facts','raw','raw_response','content_text')
            )
BEGIN
                SELECT RAISE(ABORT,
                    'generation_runs: length assurance JSON must be hash-only');
            END""",
    """CREATE TRIGGER generation_runs_length_assurance_required
        BEFORE UPDATE ON generation_runs
        WHEN
    (OLD.status = 'running') AND (NEW.status <> 'running') AND (COALESCE(
                json_type(NEW.input_snapshot_json, '$.length_spec'),
                'null'
              ) <> 'null') AND (NEW.length_assurance_json = '{}')
BEGIN
            SELECT RAISE(ABORT,
                'generation_runs: saved length contract requires assurance evidence');
        END""",
    """CREATE TRIGGER generation_runs_length_assurance_snapshot_match
        BEFORE UPDATE OF length_assurance_json ON generation_runs
        WHEN
    (NEW.length_assurance_json <> '{}') AND ((COALESCE(
                json_extract(NEW.length_assurance_json, '$.length_spec_sha256'),
                ''
            ) <> COALESCE(
                json_extract(NEW.input_snapshot_json, '$.length_spec_sha256'),
                ''
            )) OR (COALESCE(
                json_extract(NEW.length_assurance_json, '$.length_spec'),
                ''
            ) <> COALESCE(
                json_extract(NEW.input_snapshot_json, '$.length_spec'),
                ''
            )))
BEGIN
            SELECT RAISE(ABORT,
                'generation_runs: length assurance does not match input snapshot');
        END""",
    """CREATE TRIGGER generation_runs_length_assurance_transition
        BEFORE UPDATE OF length_assurance_json ON generation_runs
        WHEN
    (OLD.length_assurance_json <> NEW.length_assurance_json) AND (NOT ((OLD.status = 'running') AND (NEW.status <> 'running')))
BEGIN
            SELECT RAISE(ABORT,
                'generation_runs: length assurance changes only on finalization');
        END""",
    """CREATE TRIGGER generation_runs_assurance_assurance_lifecycle_update
        BEFORE UPDATE ON generation_runs
        WHEN
    (OLD.status = 'running') AND (NEW.status <> 'running') AND ((COALESCE(
                json_type(OLD.input_snapshot_json, '$.length_spec'),
                'null'
            ) <> 'null') OR (COALESCE(
                json_type(NEW.input_snapshot_json, '$.length_spec'),
                'null'
            ) <> 'null') OR (NEW.length_assurance_json <> '{}')) AND ((OLD.input_snapshot_json <> NEW.input_snapshot_json) OR (OLD.input_snapshot_sha256 <> NEW.input_snapshot_sha256) OR (NEW.length_assurance_json = '{}') OR (json_type(
                NEW.length_assurance_json, '$.domain_complete'
            ) NOT IN ('true','false')) OR (json_extract(
                NEW.length_assurance_json, '$.selected_candidate'
            ) NOT IN ('none','accumulated','condensed')) OR (json_extract(
                NEW.length_assurance_json, '$.assurance_status'
            ) NOT IN (
                'succeeded_satisfied','succeeded_advisory_miss',
                'continue_required','partial_needs_action',
                'cancelled_with_partial','cancelled',
                'failed_recoverable','failed_terminal'
            )) OR (json_extract(
                NEW.length_assurance_json, '$.assurance_action'
            ) NOT IN (
                'complete','continue','save_partial',
                'create_condensed_candidate','cancel','fail'
            )) OR (json_type(
                NEW.length_assurance_json, '$.assurance_reason'
            ) <> 'text') OR (NEW.completed_at = '') OR ((NEW.status = 'completed') AND ((json_extract(
                    NEW.length_assurance_json, '$.domain_complete'
                ) IS NOT 1) OR (json_extract(
                    NEW.length_assurance_json, '$.selected_candidate'
                ) = 'none') OR (json_extract(
                    NEW.length_assurance_json, '$.provider_termination'
                ) <> 'normal_stop') OR (json_extract(
                    NEW.length_assurance_json, '$.assurance_status'
                ) <> 'succeeded_satisfied') OR (json_extract(
                    NEW.length_assurance_json, '$.assurance_action'
                ) <> 'complete') OR (json_extract(
                    NEW.length_assurance_json, '$.assurance_reason'
                ) <> 'length_satisfied') OR (json_extract(
                    NEW.length_assurance_json, '$.logical_provider_attempts'
                ) < 1) OR (json_extract(
                    NEW.length_assurance_json, '$.runtime_provider_attempts'
                ) < 1))) OR ((NEW.status = 'cancelled') AND ((NEW.reason_code <> 'cancelled_by_user') OR (json_extract(
                    NEW.length_assurance_json, '$.domain_complete'
                ) IS NOT 0) OR (json_extract(
                    NEW.length_assurance_json, '$.provider_termination'
                ) <> 'cancelled'))) OR ((NEW.status = 'timeout') AND ((NEW.reason_code <> 'provider_timeout') OR (json_extract(
                    NEW.length_assurance_json, '$.domain_complete'
                ) IS NOT 0) OR (json_extract(
                    NEW.length_assurance_json, '$.provider_termination'
                ) <> 'timeout'))) OR ((NEW.status = 'failed') AND (NEW.reason_code = '')) OR ((NEW.status <> 'completed') AND (json_extract(
                    NEW.length_assurance_json, '$.domain_complete'
                ) IS 1)) OR ((json_extract(
                    NEW.length_assurance_json, '$.partial_disposition'
                ) IN ('complete_unaccepted_candidate','condensed_candidate_with_preserved_original')) AND (NEW.status <> 'completed')) OR ((json_extract(
                    NEW.length_assurance_json, '$.partial_disposition'
                ) IN ('saved_unacceptable_partial','no_candidate')) AND (json_extract(
                    NEW.length_assurance_json, '$.domain_complete'
                ) IS NOT 0)) OR ((json_extract(
                    NEW.length_assurance_json, '$.selected_candidate'
                ) IN ('condensed')) AND (json_array_length(
                    NEW.length_assurance_json,
                    '$.preserved_overlength_output_sha256s'
                ) < 1)) OR ((NEW.status = 'completed') AND ((NEW.reason_code <> '') OR (NEW.error_reason <> '') OR (json_extract(
                        NEW.length_assurance_json, '$.partial_disposition'
                    ) NOT IN (
                        'complete_unaccepted_candidate',
                        'condensed_candidate_with_preserved_original'
                    )))))
BEGIN
            SELECT RAISE(ABORT,
                'generation_runs: terminal status contradicts length assurance assurance');
        END""",
    """CREATE TRIGGER generation_runs_assurance_audit_envelope_immutable
        BEFORE UPDATE ON generation_runs
        WHEN
    (OLD.id IS NOT NEW.id) OR (OLD.story_scene_id IS NOT NEW.story_scene_id) OR (OLD.project_id IS NOT NEW.project_id) OR (OLD.run_kind IS NOT NEW.run_kind) OR (OLD.provider IS NOT NEW.provider) OR (OLD.model IS NOT NEW.model) OR (OLD.options_snapshot_json IS NOT NEW.options_snapshot_json) OR (OLD.options_snapshot_sha256 IS NOT NEW.options_snapshot_sha256) OR (OLD.context_fingerprint IS NOT NEW.context_fingerprint) OR (OLD.planning_chain_fingerprint IS NOT NEW.planning_chain_fingerprint) OR (OLD.planning_mode IS NOT NEW.planning_mode) OR (OLD.preview_warning_acknowledged IS NOT NEW.preview_warning_acknowledged) OR (OLD.renderer_version IS NOT NEW.renderer_version) OR (OLD.context_schema_version IS NOT NEW.context_schema_version) OR (OLD.context_contract_version IS NOT NEW.context_contract_version) OR (OLD.context_budget_policy_version IS NOT NEW.context_budget_policy_version) OR (OLD.eligibility_fingerprint IS NOT NEW.eligibility_fingerprint) OR (OLD.eligibility_evaluation_ids_json IS NOT
             NEW.eligibility_evaluation_ids_json) OR (OLD.input_snapshot_json IS NOT NEW.input_snapshot_json) OR (OLD.input_snapshot_sha256 IS NOT NEW.input_snapshot_sha256) OR (OLD.requirement_version_id IS NOT NEW.requirement_version_id) OR (OLD.bible_version_id IS NOT NEW.bible_version_id) OR (OLD.outline_version_id IS NOT NEW.outline_version_id) OR (OLD.chapter_plan_version_id IS NOT NEW.chapter_plan_version_id) OR (OLD.scene_card_version_id IS NOT NEW.scene_card_version_id) OR (OLD.pov_character_version_id IS NOT NEW.pov_character_version_id) OR (OLD.character_version_ids_json IS NOT NEW.character_version_ids_json) OR (OLD.parent_draft_id IS NOT NEW.parent_draft_id) OR (OLD.revision_request_fingerprint IS NOT
             NEW.revision_request_fingerprint) OR (OLD.rebased_from_generation_run_id IS NOT
             NEW.rebased_from_generation_run_id) OR (OLD.rebase_reason IS NOT NEW.rebase_reason) OR (OLD.is_recovery_rebase IS NOT NEW.is_recovery_rebase) OR (OLD.missing_source_version_ids_json IS NOT
             NEW.missing_source_version_ids_json) OR (OLD.system_message_sha256 IS NOT NEW.system_message_sha256) OR (OLD.user_message_sha256 IS NOT NEW.user_message_sha256) OR (OLD.system_message_byte_size IS NOT NEW.system_message_byte_size) OR (OLD.user_message_byte_size IS NOT NEW.user_message_byte_size) OR (OLD.rendered_system_message IS NOT NEW.rendered_system_message) OR (OLD.rendered_user_message IS NOT NEW.rendered_user_message) OR (OLD.rendered_message_storage_enabled IS NOT
             NEW.rendered_message_storage_enabled) OR (OLD.content_mode IS NOT NEW.content_mode) OR (OLD.started_at IS NOT NEW.started_at)
BEGIN
            SELECT RAISE(ABORT,
                'generation_runs: length assurance audit envelope is immutable');
        END""",
    """CREATE TRIGGER generation_runs_assurance_output_artifact_update
        BEFORE UPDATE ON generation_runs
        WHEN
    (OLD.status = 'running') AND (NEW.status <> 'running') AND (NEW.length_assurance_json <> '{}') AND (((NEW.status = 'completed') AND ((1 <> (
                        SELECT count(*)
                        FROM scene_drafts AS draft
                        JOIN scene_draft_block_sets AS blocks
                          ON blocks.scene_draft_id = draft.id
                         AND blocks.project_id = draft.project_id
                         AND blocks.story_scene_id = draft.story_scene_id
                        WHERE draft.generation_run_id = NEW.id
                          AND draft.project_id = NEW.project_id
                          AND draft.story_scene_id = NEW.story_scene_id
                          AND draft.scene_card_version_id =
                              NEW.scene_card_version_id
                          AND draft.draft_status = 'complete'
                          AND blocks.prose_sha256 = json_extract(
                              NEW.length_assurance_json,
                              '$.output_text_sha256'
                          )
                    )) OR ((json_extract(
                            NEW.length_assurance_json, '$.selected_candidate'
                        ) = 'condensed') AND (EXISTS (
                            SELECT 1
                            FROM json_each(
                                NEW.length_assurance_json,
                                '$.preserved_overlength_output_sha256s'
                            ) AS preserved
                            WHERE NOT EXISTS (
                                SELECT 1
                                FROM scene_drafts AS original
                                JOIN scene_draft_block_sets AS original_blocks
                                  ON original_blocks.scene_draft_id = original.id
                                WHERE original.generation_run_id = NEW.id
                                  AND original.draft_status = 'partial'
                                  AND original_blocks.prose_sha256 =
                                      preserved.value
                            )
                        ))))) OR ((NEW.status <> 'completed') AND (EXISTS (
                    SELECT 1 FROM scene_drafts AS draft
                    WHERE draft.generation_run_id = NEW.id
                      AND draft.draft_status = 'complete'
                ))) OR ((json_extract(
                    NEW.length_assurance_json, '$.partial_disposition'
                ) = 'saved_unacceptable_partial') AND (NOT EXISTS (
                        SELECT 1
                        FROM scene_drafts AS draft
                        JOIN scene_draft_block_sets AS blocks
                          ON blocks.scene_draft_id = draft.id
                         AND blocks.project_id = draft.project_id
                         AND blocks.story_scene_id = draft.story_scene_id
                        WHERE draft.generation_run_id = NEW.id
                          AND draft.draft_status = 'partial'
                          AND blocks.prose_sha256 = json_extract(
                              NEW.length_assurance_json,
                              '$.output_text_sha256'
                          )
                    ))) OR ((json_extract(
                    NEW.length_assurance_json, '$.partial_disposition'
                ) = 'no_candidate') AND (EXISTS (
                        SELECT 1 FROM scene_drafts AS draft
                        WHERE draft.generation_run_id = NEW.id
                    ))))
BEGIN
            SELECT RAISE(ABORT,
                'generation_runs: length assurance output artifact lifecycle is inconsistent');
        END""",
    """CREATE TRIGGER generation_runs_assurance_preserved_artifacts_update
        BEFORE UPDATE ON generation_runs
        WHEN
    (OLD.status = 'running') AND (NEW.status <> 'running') AND (NEW.length_assurance_json <> '{}') AND ((json_extract(
                NEW.length_assurance_json, '$.selected_candidate'
              ) = 'condensed') AND (EXISTS (
                SELECT 1
                FROM json_each(
                  NEW.length_assurance_json,
                  '$.preserved_overlength_output_sha256s'
                ) AS preserved
                WHERE NOT EXISTS (
                  SELECT 1
                  FROM scene_drafts AS original
                  JOIN scene_draft_block_sets AS original_blocks
                    ON original_blocks.scene_draft_id = original.id
                   AND original_blocks.project_id = original.project_id
                   AND original_blocks.story_scene_id = original.story_scene_id
                  WHERE original.generation_run_id = NEW.id
                    AND original.project_id = NEW.project_id
                    AND original.story_scene_id = NEW.story_scene_id
                    AND original.scene_card_version_id = NEW.scene_card_version_id
                    AND original.draft_status = 'partial'
                    AND original_blocks.prose_sha256 = preserved.value
                )
              )))
BEGIN
            SELECT RAISE(ABORT,
                'generation_runs: selected and preserved artifacts disagree');
        END""",
    """CREATE TRIGGER generation_runs_assurance_running_insert
        BEFORE INSERT ON generation_runs
        WHEN
    NEW.status <> 'running'
BEGIN
            SELECT RAISE(ABORT,
                'generation_runs: new runs must start running');
        END""",
    """CREATE TRIGGER generation_runs_assurance_terminal_matrix_update
        BEFORE UPDATE ON generation_runs
        WHEN
    (OLD.status = 'running') AND (NEW.status <> 'running') AND (NEW.length_assurance_json <> '{}') AND (NOT (((NEW.status = 'completed') AND (json_extract(
                    NEW.length_assurance_json, '$.domain_complete'
                  ) IS 1) AND (json_extract(
                    NEW.length_assurance_json, '$.provider_termination'
                  ) = 'normal_stop') AND (json_extract(
                    NEW.length_assurance_json, '$.assurance_status'
                  ) = 'succeeded_satisfied') AND (json_extract(
                    NEW.length_assurance_json, '$.assurance_action'
                  ) = 'complete') AND (json_extract(
                    NEW.length_assurance_json, '$.assurance_reason'
                  ) = 'length_satisfied') AND (json_extract(
                    NEW.length_assurance_json, '$.logical_provider_attempts'
                  ) >= 1) AND (json_extract(
                    NEW.length_assurance_json, '$.runtime_provider_attempts'
                  ) >= 1) AND ((NEW.reason_code = '') AND (NEW.error_reason = '') AND (((json_extract(
                        NEW.length_assurance_json, '$.selected_candidate'
                      ) = 'accumulated') AND (json_extract(
                        NEW.length_assurance_json, '$.partial_disposition'
                      ) = 'complete_unaccepted_candidate') AND (json_array_length(
                        NEW.length_assurance_json,
                        '$.preserved_overlength_output_sha256s'
                      ) = 0)) OR ((json_extract(
                        NEW.length_assurance_json, '$.selected_candidate'
                      ) = 'condensed') AND (json_extract(
                        NEW.length_assurance_json, '$.partial_disposition'
                      ) = 'condensed_candidate_with_preserved_original') AND (json_array_length(
                        NEW.length_assurance_json,
                        '$.preserved_overlength_output_sha256s'
                      ) = 1))))) OR ((NEW.status = 'failed') AND (NEW.reason_code <> '') AND (json_extract(
                    NEW.length_assurance_json, '$.domain_complete'
                  ) IS 0) AND (json_extract(
                    NEW.length_assurance_json, '$.assurance_reason'
                  ) NOT IN ('length_satisfied','advisory_miss')) AND (((json_extract(
                    NEW.length_assurance_json, '$.assurance_status'
                  ) IN ('failed_recoverable','failed_terminal')) AND (json_extract(
                    NEW.length_assurance_json, '$.assurance_action'
                  ) IN ('fail','save_partial'))) OR ((json_extract(
                    NEW.length_assurance_json, '$.assurance_status'
                  ) = 'partial_needs_action') AND (json_extract(
                    NEW.length_assurance_json, '$.assurance_action'
                  ) = 'save_partial')) OR ((json_extract(
                    NEW.length_assurance_json, '$.assurance_status'
                  ) = 'continue_required') AND (json_extract(
                    NEW.length_assurance_json, '$.assurance_action'
                  ) = 'continue')) OR ((json_extract(
                    NEW.length_assurance_json, '$.assurance_status'
                  ) = 'cancelled_with_partial') AND (json_extract(
                    NEW.length_assurance_json, '$.assurance_action'
                  ) = 'save_partial'))) AND (((json_extract(
                    NEW.length_assurance_json, '$.partial_disposition'
                  ) = 'no_candidate') AND (json_extract(
                    NEW.length_assurance_json, '$.assurance_action'
                  ) = 'fail') AND (json_array_length(
                    NEW.length_assurance_json,
                    '$.preserved_overlength_output_sha256s'
                  ) = 0)) OR ((json_extract(
                    NEW.length_assurance_json, '$.partial_disposition'
                  ) = 'saved_unacceptable_partial') AND (json_extract(
                    NEW.length_assurance_json, '$.assurance_action'
                  ) = 'save_partial') AND (((json_extract(
                        NEW.length_assurance_json, '$.selected_candidate'
                      ) = 'accumulated') AND (json_array_length(
                        NEW.length_assurance_json,
                        '$.preserved_overlength_output_sha256s'
                      ) = 0)) OR ((json_extract(
                        NEW.length_assurance_json, '$.selected_candidate'
                      ) = 'condensed') AND (json_array_length(
                        NEW.length_assurance_json,
                        '$.preserved_overlength_output_sha256s'
                      ) = 1)))))) OR ((NEW.status = 'cancelled') AND (NEW.reason_code = 'cancelled_by_user') AND (json_extract(
                    NEW.length_assurance_json, '$.domain_complete'
                  ) IS 0) AND (json_extract(
                    NEW.length_assurance_json, '$.provider_termination'
                  ) = 'cancelled') AND (json_extract(
                    NEW.length_assurance_json, '$.assurance_reason'
                  ) = 'cancelled_by_author') AND (((json_extract(
                    NEW.length_assurance_json, '$.partial_disposition'
                  ) = 'no_candidate') AND (json_extract(
                    NEW.length_assurance_json, '$.assurance_status'
                  ) = 'cancelled') AND (json_extract(
                    NEW.length_assurance_json, '$.assurance_action'
                  ) = 'cancel') AND (json_array_length(
                    NEW.length_assurance_json,
                    '$.preserved_overlength_output_sha256s'
                  ) = 0)) OR ((json_extract(
                    NEW.length_assurance_json, '$.assurance_status'
                  ) = 'cancelled_with_partial') AND (json_extract(
                    NEW.length_assurance_json, '$.assurance_action'
                  ) = 'save_partial') AND ((json_extract(
                        NEW.length_assurance_json, '$.partial_disposition'
                      ) = 'saved_unacceptable_partial') AND (((json_extract(
                            NEW.length_assurance_json, '$.selected_candidate'
                          ) = 'accumulated') AND (json_array_length(
                            NEW.length_assurance_json,
                            '$.preserved_overlength_output_sha256s'
                          ) = 0)) OR ((json_extract(
                            NEW.length_assurance_json, '$.selected_candidate'
                          ) = 'condensed') AND (json_array_length(
                            NEW.length_assurance_json,
                            '$.preserved_overlength_output_sha256s'
                          ) = 1))))))) OR ((NEW.status = 'timeout') AND (NEW.reason_code = 'provider_timeout') AND (json_extract(
                    NEW.length_assurance_json, '$.domain_complete'
                  ) IS 0) AND (json_extract(
                    NEW.length_assurance_json, '$.provider_termination'
                  ) = 'timeout') AND (json_extract(
                    NEW.length_assurance_json, '$.assurance_reason'
                  ) NOT IN ('length_satisfied','advisory_miss')) AND (((json_extract(
                    NEW.length_assurance_json, '$.partial_disposition'
                  ) = 'no_candidate') AND (json_extract(
                    NEW.length_assurance_json, '$.assurance_status'
                  ) IN ('failed_recoverable','failed_terminal')) AND (json_extract(
                    NEW.length_assurance_json, '$.assurance_action'
                  ) = 'fail') AND (json_array_length(
                    NEW.length_assurance_json,
                    '$.preserved_overlength_output_sha256s'
                  ) = 0)) OR ((json_extract(
                    NEW.length_assurance_json, '$.assurance_status'
                  ) IN (
                    'partial_needs_action','failed_recoverable','failed_terminal'
                  )) AND (json_extract(
                    NEW.length_assurance_json, '$.assurance_action'
                  ) = 'save_partial') AND ((json_extract(
                        NEW.length_assurance_json, '$.partial_disposition'
                      ) = 'saved_unacceptable_partial') AND (((json_extract(
                            NEW.length_assurance_json, '$.selected_candidate'
                          ) = 'accumulated') AND (json_array_length(
                            NEW.length_assurance_json,
                            '$.preserved_overlength_output_sha256s'
                          ) = 0)) OR ((json_extract(
                            NEW.length_assurance_json, '$.selected_candidate'
                          ) = 'condensed') AND (json_array_length(
                            NEW.length_assurance_json,
                            '$.preserved_overlength_output_sha256s'
                          ) = 1)))))))))
BEGIN
            SELECT RAISE(ABORT,
                'generation_runs: terminal tuple is outside length assurance allowlist');
        END""",
    """CREATE TRIGGER generation_runs_no_delete
        BEFORE DELETE ON generation_runs
        BEGIN
            SELECT RAISE(ABORT, 'generation_runs rows are historical evidence');
        END""",
    """CREATE TRIGGER ppv_accepted_immutable_delete
        BEFORE DELETE ON prompt_project_versions
        WHEN
    OLD.accepted = 1
BEGIN
            SELECT RAISE(ABORT, 'accepted prompt project versions are immutable');
        END""",
    """CREATE TRIGGER ppv_accepted_immutable_update
        BEFORE UPDATE ON prompt_project_versions
        WHEN
    OLD.accepted = 1
BEGIN
            SELECT RAISE(ABORT, 'accepted prompt project versions are immutable');
        END""",
    """CREATE TRIGGER prevent_eligibility_audit_delete
        BEFORE DELETE ON character_validations
        BEGIN
            SELECT RAISE(ABORT, 'eligibility audits are append-only');
        END""",
    """CREATE TRIGGER prevent_eligibility_audit_update
        BEFORE UPDATE ON character_validations
        BEGIN
            SELECT RAISE(ABORT, 'eligibility audits are append-only');
        END""",
    """CREATE TRIGGER pv_write_once_delete
        BEFORE DELETE ON prompt_variants
        BEGIN
            SELECT RAISE(ABORT, 'prompt_variants rows are write-once (immutable)');
        END""",
    """CREATE TRIGGER pv_write_once_update
        BEFORE UPDATE ON prompt_variants
        BEGIN
            SELECT RAISE(ABORT, 'prompt_variants rows are write-once (immutable)');
        END""",
    """CREATE TRIGGER scene_card_versions_accepted_immutable_delete
        BEFORE DELETE ON scene_card_versions
        WHEN
    OLD.accepted = 1
BEGIN
            SELECT RAISE(ABORT, 'scene_card_versions: accepted versions are immutable');
        END""",
    """CREATE TRIGGER scene_card_versions_accepted_immutable_update
        BEFORE UPDATE ON scene_card_versions
        WHEN
    OLD.accepted = 1
BEGIN
            SELECT RAISE(ABORT, 'scene_card_versions: accepted versions are immutable');
        END""",
    """CREATE TRIGGER scene_draft_block_sets_source_guard_insert
        BEFORE INSERT ON scene_draft_block_sets
        WHEN
    NOT EXISTS (
            SELECT 1 FROM scene_drafts AS draft
            WHERE draft.id = NEW.scene_draft_id
              AND draft.project_id = NEW.project_id
              AND draft.story_scene_id = NEW.story_scene_id
              AND draft.parent_draft_id IS NEW.parent_draft_id
              AND draft.created_at = NEW.created_at
        )
BEGIN
            SELECT RAISE(ABORT,
                'scene_draft_block_sets: source draft identity mismatch');
        END""",
    """CREATE TRIGGER scene_draft_block_sets_write_once_delete
            BEFORE DELETE ON scene_draft_block_sets
            BEGIN
                SELECT RAISE(ABORT, 'scene_draft_block_sets: stable projection is immutable');
            END""",
    """CREATE TRIGGER scene_draft_block_sets_write_once_update
            BEFORE UPDATE ON scene_draft_block_sets
            BEGIN
                SELECT RAISE(ABORT, 'scene_draft_block_sets: stable projection is immutable');
            END""",
    """CREATE TRIGGER scene_drafts_accepted_immutable_delete
        BEFORE DELETE ON scene_drafts
        WHEN
    OLD.was_accepted = 1
BEGIN
            SELECT RAISE(ABORT, 'scene_drafts: accepted drafts are immutable');
        END""",
    """CREATE TRIGGER scene_drafts_accepted_immutable_update
        BEFORE UPDATE ON scene_drafts
        WHEN
    OLD.was_accepted = 1
BEGIN
            SELECT RAISE(ABORT, 'scene_drafts: accepted drafts are immutable');
        END""",
    """CREATE TRIGGER scene_drafts_block_source_immutable_update
        BEFORE UPDATE OF id,project_id,story_scene_id,parent_draft_id,prose_text,
            created_at
        ON scene_drafts
        WHEN
    (EXISTS (
            SELECT 1 FROM scene_draft_block_sets
            WHERE scene_draft_id = OLD.id
        )) AND ((OLD.id IS NOT NEW.id) OR (OLD.project_id IS NOT NEW.project_id) OR (OLD.story_scene_id IS NOT NEW.story_scene_id) OR (OLD.parent_draft_id IS NOT NEW.parent_draft_id) OR (OLD.prose_text IS NOT NEW.prose_text) OR (OLD.created_at IS NOT NEW.created_at))
BEGIN
            SELECT RAISE(ABORT,
                'scene_drafts: stable block source is immutable');
        END""",
    """CREATE TRIGGER story_bible_versions_accepted_immutable_delete
        BEFORE DELETE ON story_bible_versions
        WHEN
    OLD.accepted = 1
BEGIN
            SELECT RAISE(ABORT, 'story_bible_versions: accepted versions are immutable');
        END""",
    """CREATE TRIGGER story_bible_versions_accepted_immutable_update
        BEFORE UPDATE ON story_bible_versions
        WHEN
    OLD.accepted = 1
BEGIN
            SELECT RAISE(ABORT, 'story_bible_versions: accepted versions are immutable');
        END""",
    """CREATE TRIGGER story_block_anchors_write_once_delete
            BEFORE DELETE ON story_block_anchors
            BEGIN
                SELECT RAISE(ABORT, 'story_block_anchors: stable projection is immutable');
            END""",
    """CREATE TRIGGER story_block_anchors_write_once_update
            BEFORE UPDATE ON story_block_anchors
            BEGIN
                SELECT RAISE(ABORT, 'story_block_anchors: stable projection is immutable');
            END""",
    """CREATE TRIGGER story_block_revisions_manifest_guard_insert
        BEFORE INSERT ON story_block_revisions
        WHEN
    NOT EXISTS (
            SELECT 1 FROM scene_draft_block_sets AS block_set
            WHERE block_set.scene_draft_id = NEW.scene_draft_id
              AND block_set.project_id = NEW.project_id
              AND block_set.story_scene_id = NEW.story_scene_id
              AND NEW.ordinal < block_set.block_count
        )
BEGIN
            SELECT RAISE(ABORT,
                'story_block_revisions: block-set manifest mismatch');
        END""",
    """CREATE TRIGGER story_block_revisions_parent_guard_insert
        BEFORE INSERT ON story_block_revisions
        WHEN
    (NEW.parent_block_revision_id IS NOT NULL) AND (NOT EXISTS (
            SELECT 1
            FROM scene_draft_block_sets AS child_set
            JOIN story_block_revisions AS parent
              ON parent.block_revision_id = NEW.parent_block_revision_id
            WHERE child_set.scene_draft_id = NEW.scene_draft_id
              AND child_set.parent_draft_available = 1
              AND parent.scene_draft_id = child_set.parent_draft_id
              AND parent.logical_block_id = NEW.logical_block_id
              AND parent.project_id = NEW.project_id
              AND parent.story_scene_id = NEW.story_scene_id
        ))
BEGIN
            SELECT RAISE(ABORT,
                'story_block_revisions: invalid parent lineage');
        END""",
    """CREATE TRIGGER story_block_revisions_write_once_delete
            BEFORE DELETE ON story_block_revisions
            BEGIN
                SELECT RAISE(ABORT, 'story_block_revisions: stable projection is immutable');
            END""",
    """CREATE TRIGGER story_block_revisions_write_once_update
            BEFORE UPDATE ON story_block_revisions
            BEGIN
                SELECT RAISE(ABORT, 'story_block_revisions: stable projection is immutable');
            END""",
    """CREATE TRIGGER story_exports_write_once_delete
        BEFORE DELETE ON story_exports
        BEGIN
            SELECT RAISE(ABORT, 'story_exports rows are write-once (immutable)');
        END""",
    """CREATE TRIGGER story_exports_write_once_update
        BEFORE UPDATE ON story_exports
        BEGIN
            SELECT RAISE(ABORT, 'story_exports rows are write-once (immutable)');
        END""",
    """CREATE TRIGGER story_fragment_drafts_no_delete
        BEFORE DELETE ON story_fragment_drafts
        BEGIN
            SELECT RAISE(ABORT,
                'story_fragment_drafts are archived, never deleted');
        END""",
    """CREATE TRIGGER story_fragment_drafts_tags_insert
        BEFORE INSERT ON story_fragment_drafts
        WHEN
    (EXISTS (
    SELECT 1 FROM json_each(NEW.tags_json)
    WHERE type <> 'text'
       OR length(value) = 0
       OR length(value) > 200
       OR value <> trim(value)
       OR instr(value, '  ') > 0
       OR instr(value, char(9)) > 0
       OR instr(value, char(10)) > 0
       OR instr(value, char(11)) > 0
       OR instr(value, char(12)) > 0
       OR instr(value, char(13)) > 0
       OR instr(value, char(28)) > 0
       OR instr(value, char(29)) > 0
       OR instr(value, char(30)) > 0
       OR instr(value, char(31)) > 0
       OR instr(value, char(133)) > 0
       OR instr(value, char(160)) > 0
       OR instr(value, char(5760)) > 0
       OR instr(value, char(8192)) > 0
       OR instr(value, char(8193)) > 0
       OR instr(value, char(8194)) > 0
       OR instr(value, char(8195)) > 0
       OR instr(value, char(8196)) > 0
       OR instr(value, char(8197)) > 0
       OR instr(value, char(8198)) > 0
       OR instr(value, char(8199)) > 0
       OR instr(value, char(8200)) > 0
       OR instr(value, char(8201)) > 0
       OR instr(value, char(8202)) > 0
       OR instr(value, char(8232)) > 0
       OR instr(value, char(8233)) > 0
       OR instr(value, char(8239)) > 0
       OR instr(value, char(8287)) > 0
       OR instr(value, char(12288)) > 0
)) OR (EXISTS (
    SELECT 1
    FROM json_each(NEW.tags_json) AS earlier
    JOIN json_each(NEW.tags_json) AS later
      ON CAST(earlier.key AS INTEGER) < CAST(later.key AS INTEGER)
     AND earlier.value = later.value
))
BEGIN
            SELECT RAISE(ABORT,
                'story_fragment_drafts tags must be canonical bounded text');
        END""",
    """CREATE TRIGGER story_fragment_drafts_tags_update
        BEFORE UPDATE OF tags_json ON story_fragment_drafts
        WHEN
    (EXISTS (
    SELECT 1 FROM json_each(NEW.tags_json)
    WHERE type <> 'text'
       OR length(value) = 0
       OR length(value) > 200
       OR value <> trim(value)
       OR instr(value, '  ') > 0
       OR instr(value, char(9)) > 0
       OR instr(value, char(10)) > 0
       OR instr(value, char(11)) > 0
       OR instr(value, char(12)) > 0
       OR instr(value, char(13)) > 0
       OR instr(value, char(28)) > 0
       OR instr(value, char(29)) > 0
       OR instr(value, char(30)) > 0
       OR instr(value, char(31)) > 0
       OR instr(value, char(133)) > 0
       OR instr(value, char(160)) > 0
       OR instr(value, char(5760)) > 0
       OR instr(value, char(8192)) > 0
       OR instr(value, char(8193)) > 0
       OR instr(value, char(8194)) > 0
       OR instr(value, char(8195)) > 0
       OR instr(value, char(8196)) > 0
       OR instr(value, char(8197)) > 0
       OR instr(value, char(8198)) > 0
       OR instr(value, char(8199)) > 0
       OR instr(value, char(8200)) > 0
       OR instr(value, char(8201)) > 0
       OR instr(value, char(8202)) > 0
       OR instr(value, char(8232)) > 0
       OR instr(value, char(8233)) > 0
       OR instr(value, char(8239)) > 0
       OR instr(value, char(8287)) > 0
       OR instr(value, char(12288)) > 0
)) OR (EXISTS (
    SELECT 1
    FROM json_each(NEW.tags_json) AS earlier
    JOIN json_each(NEW.tags_json) AS later
      ON CAST(earlier.key AS INTEGER) < CAST(later.key AS INTEGER)
     AND earlier.value = later.value
))
BEGIN
            SELECT RAISE(ABORT,
                'story_fragment_drafts tags must be canonical bounded text');
        END""",
    """CREATE TRIGGER story_memory_entries_require_accepted_proposal
        BEFORE INSERT ON story_memory_entries
        WHEN
    NOT EXISTS (
            SELECT 1
            FROM story_memory_proposals AS proposal
            WHERE proposal.id = NEW.proposal_id
              AND proposal.project_id = NEW.project_id
              AND proposal.story_outline_id = NEW.story_outline_id
              AND proposal.story_scene_id = NEW.story_scene_id
              AND proposal.scene_draft_id = NEW.scene_draft_id
              AND proposal.status = 'accepted'
        )
BEGIN
            SELECT RAISE(ABORT,
                'story memory entries require an accepted proposal');
        END""",
    """CREATE TRIGGER story_memory_entries_write_once_delete
        BEFORE DELETE ON story_memory_entries
        BEGIN
            SELECT RAISE(ABORT, 'story_memory_entries rows are historical evidence');
        END""",
    """CREATE TRIGGER story_memory_entries_write_once_update
        BEFORE UPDATE ON story_memory_entries
        BEGIN
            SELECT RAISE(ABORT, 'story_memory_entries rows are immutable');
        END""",
    """CREATE TRIGGER story_memory_proposals_accept_current_draft
        BEFORE UPDATE ON story_memory_proposals
        WHEN
    (NEW.status = 'accepted') AND (NOT EXISTS (
            SELECT 1
            FROM story_scenes AS scene
            JOIN scene_drafts AS draft
              ON draft.id = NEW.scene_draft_id
             AND draft.story_scene_id = scene.id
             AND draft.project_id = scene.project_id
            WHERE scene.id = NEW.story_scene_id
              AND scene.project_id = NEW.project_id
              AND scene.accepted_draft_id = NEW.scene_draft_id
              AND draft.draft_status = 'complete'
        ))
BEGIN
            SELECT RAISE(ABORT,
                'story memory proposal source draft is no longer accepted');
        END""",
    """CREATE TRIGGER story_memory_proposals_finalize_once
        BEFORE UPDATE ON story_memory_proposals
        WHEN
    (OLD.status <> 'pending') OR (NEW.status = 'pending')
BEGIN
            SELECT RAISE(ABORT, 'story memory proposal may be finalized once');
        END""",
    """CREATE TRIGGER story_memory_proposals_no_delete
        BEFORE DELETE ON story_memory_proposals
        BEGIN
            SELECT RAISE(ABORT, 'story memory proposals are historical evidence');
        END""",
    """CREATE TRIGGER story_memory_proposals_payload_immutable
        BEFORE UPDATE ON story_memory_proposals
        WHEN
    (OLD.id <> NEW.id) OR (OLD.project_id <> NEW.project_id) OR (OLD.story_outline_id <> NEW.story_outline_id) OR (OLD.story_scene_id <> NEW.story_scene_id) OR (OLD.scene_draft_id <> NEW.scene_draft_id) OR (OLD.base_memory_fingerprint <> NEW.base_memory_fingerprint) OR (OLD.proposal_json <> NEW.proposal_json) OR (OLD.proposal_sha256 <> NEW.proposal_sha256) OR (OLD.origin <> NEW.origin) OR (OLD.provider <> NEW.provider) OR (OLD.model <> NEW.model) OR (OLD.contract_version <> NEW.contract_version) OR (OLD.created_at <> NEW.created_at)
BEGIN
            SELECT RAISE(ABORT, 'story memory proposal payload is immutable');
        END""",
    """CREATE TRIGGER story_memory_proposals_validate_insert
        BEFORE INSERT ON story_memory_proposals
        WHEN
    NOT EXISTS (
            SELECT 1
            FROM story_scenes AS scene
            JOIN story_chapters AS chapter
              ON chapter.id = scene.story_chapter_id
             AND chapter.project_id = scene.project_id
            JOIN scene_drafts AS draft
              ON draft.id = NEW.scene_draft_id
             AND draft.story_scene_id = scene.id
             AND draft.project_id = scene.project_id
            WHERE scene.id = NEW.story_scene_id
              AND scene.project_id = NEW.project_id
              AND chapter.story_outline_id = NEW.story_outline_id
              AND scene.accepted_draft_id = NEW.scene_draft_id
              AND draft.draft_status = 'complete'
        )
BEGIN
            SELECT RAISE(ABORT,
                'story memory proposal requires the current accepted draft');
        END""",
    """CREATE TRIGGER story_outline_versions_accepted_immutable_delete
        BEFORE DELETE ON story_outline_versions
        WHEN
    OLD.accepted = 1
BEGIN
            SELECT RAISE(ABORT, 'story_outline_versions: accepted versions are immutable');
        END""",
    """CREATE TRIGGER story_outline_versions_accepted_immutable_update
        BEFORE UPDATE ON story_outline_versions
        WHEN
    OLD.accepted = 1
BEGIN
            SELECT RAISE(ABORT, 'story_outline_versions: accepted versions are immutable');
        END""",
    """CREATE TRIGGER story_requirement_versions_accepted_immutable_delete
        BEFORE DELETE ON story_requirement_versions
        WHEN
    OLD.accepted = 1
BEGIN
            SELECT RAISE(ABORT, 'story_requirement_versions: accepted versions are immutable');
        END""",
    """CREATE TRIGGER story_requirement_versions_accepted_immutable_update
        BEFORE UPDATE ON story_requirement_versions
        WHEN
    OLD.accepted = 1
BEGIN
            SELECT RAISE(ABORT, 'story_requirement_versions: accepted versions are immutable');
        END""",
    """CREATE TRIGGER story_scenes_block_pointer_guard_insert
        BEFORE INSERT ON story_scenes
        WHEN
    ((NEW.working_draft_id IS NOT NULL) AND (NOT EXISTS (
            SELECT 1 FROM scene_draft_block_sets AS block_set
            WHERE block_set.scene_draft_id = NEW.working_draft_id
              AND block_set.project_id = NEW.project_id
              AND block_set.story_scene_id = NEW.id
              AND block_set.block_count = (
                  SELECT count(*) FROM story_block_revisions AS revision
                  WHERE revision.scene_draft_id = block_set.scene_draft_id
              )
        ))) OR ((NEW.accepted_draft_id IS NOT NULL) AND (NOT EXISTS (
            SELECT 1 FROM scene_draft_block_sets AS block_set
            WHERE block_set.scene_draft_id = NEW.accepted_draft_id
              AND block_set.project_id = NEW.project_id
              AND block_set.story_scene_id = NEW.id
              AND block_set.block_count = (
                  SELECT count(*) FROM story_block_revisions AS revision
                  WHERE revision.scene_draft_id = block_set.scene_draft_id
              )
        )))
BEGIN
            SELECT RAISE(ABORT,
                'story_scenes: draft pointer requires a complete block projection');
        END""",
    """CREATE TRIGGER story_scenes_block_pointer_guard_update
        BEFORE UPDATE OF working_draft_id,accepted_draft_id ON story_scenes
        WHEN
    ((NEW.working_draft_id IS NOT NULL) AND (NOT EXISTS (
            SELECT 1 FROM scene_draft_block_sets AS block_set
            WHERE block_set.scene_draft_id = NEW.working_draft_id
              AND block_set.project_id = NEW.project_id
              AND block_set.story_scene_id = NEW.id
              AND block_set.block_count = (
                  SELECT count(*) FROM story_block_revisions AS revision
                  WHERE revision.scene_draft_id = block_set.scene_draft_id
              )
        ))) OR ((NEW.accepted_draft_id IS NOT NULL) AND (NOT EXISTS (
            SELECT 1 FROM scene_draft_block_sets AS block_set
            WHERE block_set.scene_draft_id = NEW.accepted_draft_id
              AND block_set.project_id = NEW.project_id
              AND block_set.story_scene_id = NEW.id
              AND block_set.block_count = (
                  SELECT count(*) FROM story_block_revisions AS revision
                  WHERE revision.scene_draft_id = block_set.scene_draft_id
              )
        )))
BEGIN
            SELECT RAISE(ABORT,
                'story_scenes: draft pointer requires a complete block projection');
        END""",
    """CREATE TRIGGER story_scenes_revision_delete
            AFTER DELETE ON story_scenes
            BEGIN
                INSERT INTO story_scene_revisions(scene_id, revision) VALUES (OLD.id, 1)
                ON CONFLICT(scene_id) DO UPDATE SET revision = revision + 1;
            END""",
    """CREATE TRIGGER story_scenes_revision_identity
        BEFORE UPDATE OF id ON story_scenes WHEN
    OLD.id IS NOT NEW.id
BEGIN SELECT RAISE(ABORT, 'story_scenes: identity is immutable'); END""",
    """CREATE TRIGGER story_scenes_revision_insert
            AFTER INSERT ON story_scenes
            BEGIN
                INSERT INTO story_scene_revisions(scene_id, revision) VALUES (NEW.id, 1)
                ON CONFLICT(scene_id) DO UPDATE SET revision = revision + 1;
            END""",
    """CREATE TRIGGER story_scenes_revision_update
            AFTER UPDATE ON story_scenes
            BEGIN
                INSERT INTO story_scene_revisions(scene_id, revision) VALUES (NEW.id, 1)
                ON CONFLICT(scene_id) DO UPDATE SET revision = revision + 1;
            END""",
    """CREATE TRIGGER video_prompt_bundles_validate_insert
        BEFORE INSERT ON video_prompt_bundles
        WHEN
    (NEW.status <> 'ready_draft') OR (json_extract(NEW.bundle_json, '$.schema_version') IS NOT
                NEW.schema_version) OR (json_extract(NEW.bundle_json, '$.project_id') IS NOT NEW.project_id) OR (json_extract(NEW.bundle_json, '$.prompt_project_id') IS NOT
                NEW.prompt_project_id) OR (json_extract(NEW.bundle_json, '$.prompt_project_version_id') IS NOT
                NEW.prompt_project_version_id) OR (json_extract(NEW.bundle_json, '$.content_mode') IS NOT NEW.content_mode) OR (json_extract(
                NEW.bundle_json, '$.parent_input_fingerprint') IS NOT
                NEW.parent_input_fingerprint) OR (json_extract(
                NEW.bundle_json, '$.source_input_fingerprint') IS NOT
                NEW.source_input_fingerprint) OR (json_extract(NEW.bundle_json, '$.input_fingerprint') IS NOT
                NEW.input_fingerprint) OR (json_extract(
                NEW.bundle_json, '$.participant_manifest_canonical_json') IS NOT
                NEW.participant_manifest_json) OR (json_extract(
                NEW.bundle_json, '$.participant_manifest_fingerprint') IS NOT
                NEW.participant_manifest_fingerprint) OR (json_extract(NEW.bundle_json, '$.created_at') IS NOT NEW.created_at) OR (json_extract(
                NEW.bundle_json, '$.eligibility_window_started_at') IS NOT
                NEW.eligibility_window_started_at) OR (julianday(NEW.eligibility_window_started_at) IS NULL) OR (julianday(NEW.created_at) IS NULL) OR (julianday(NEW.eligibility_window_started_at) >
                julianday(NEW.created_at)) OR (julianday(NEW.eligibility_window_started_at) <
                julianday(NEW.created_at, '-5 minutes')) OR (json(
                json_extract(
                    NEW.bundle_json,
                    '$.video_eligibility_evaluation_ids')) IS NOT
                json(NEW.eligibility_evaluation_ids_json)) OR (json_extract(
                NEW.bundle_json, '$.character_asset.media_target') IS NOT
                'character_video') OR (json_extract(
                NEW.bundle_json, '$.scene_asset.media_target') IS NOT
                'scene_video') OR (json_extract(
                NEW.bundle_json,
                '$.character_asset.ast.media_target') IS NOT 'character_video') OR (json_extract(
                NEW.bundle_json,
                '$.scene_asset.ast.media_target') IS NOT 'scene_video') OR (json_array_length(
                NEW.participant_manifest_json, '$.participants') IS NOT
                json_array_length(NEW.eligibility_evaluation_ids_json)) OR (json_array_length(
                NEW.participant_manifest_json, '$.participants') IS NOT
                json_array_length(
                    NEW.bundle_json, '$.character_asset.ast.subjects')) OR (json_array_length(
                NEW.participant_manifest_json, '$.participants') IS NOT
                json_array_length(
                    NEW.bundle_json, '$.scene_asset.ast.subjects')) OR (json_array_length(
                NEW.participant_manifest_json, '$.participants') IS NOT
                (SELECT COUNT(DISTINCT json_extract(pin.value, '$.slot_id'))
                 FROM json_each(
                    NEW.participant_manifest_json, '$.participants') AS pin)) OR (1 <> (
                SELECT COUNT(*)
                FROM json_each(
                    NEW.participant_manifest_json, '$.participants') AS pin
                WHERE json_extract(pin.value, '$.is_primary') = 1
          )) OR (EXISTS (
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
          )) OR (json_array_length(NEW.eligibility_evaluation_ids_json) IS NOT (
                SELECT COUNT(DISTINCT audit_id.value)
                FROM json_each(NEW.eligibility_evaluation_ids_json) AS audit_id
          )) OR (NOT EXISTS (
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
          )) OR (EXISTS (
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
          )) OR (EXISTS (
                SELECT 1
                FROM json_each(NEW.eligibility_evaluation_ids_json) AS audit_id
                LEFT JOIN character_validations AS audit
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
          ))
BEGIN
            SELECT RAISE(ABORT,
                'video prompt bundle provenance or VIDEO audits are invalid');
        END""",
    """CREATE TRIGGER video_prompt_bundles_write_once_delete
        BEFORE DELETE ON video_prompt_bundles
        BEGIN
            SELECT RAISE(ABORT, 'video prompt bundles are immutable');
        END""",
    """CREATE TRIGGER video_prompt_bundles_write_once_update
        BEFORE UPDATE ON video_prompt_bundles
        BEGIN
            SELECT RAISE(ABORT, 'video prompt bundles are immutable');
        END""",
    """CREATE TRIGGER world_seed_drafts_no_delete
        BEFORE DELETE ON world_seed_drafts
        BEGIN
            SELECT RAISE(ABORT, 'world_seed_drafts are archived, never deleted');
        END""",
    """CREATE TRIGGER world_seed_drafts_text_arrays_insert
        BEFORE INSERT ON world_seed_drafts
        WHEN
    (EXISTS (
            SELECT 1 FROM json_each(NEW.world_rules_json)
            WHERE type <> 'text' OR length(value) > 1000
        )) OR (EXISTS (
            SELECT 1 FROM json_each(NEW.locations_json)
            WHERE type <> 'text' OR length(value) > 200
        )) OR (EXISTS (
            SELECT 1 FROM json_each(NEW.themes_json)
            WHERE type <> 'text' OR length(value) > 200
        ))
BEGIN
            SELECT RAISE(ABORT,
                'world_seed_drafts JSON arrays require bounded text values');
        END""",
    """CREATE TRIGGER world_seed_drafts_text_arrays_update
        BEFORE UPDATE OF world_rules_json, locations_json, themes_json
        ON world_seed_drafts
        WHEN
    (EXISTS (
            SELECT 1 FROM json_each(NEW.world_rules_json)
            WHERE type <> 'text' OR length(value) > 1000
        )) OR (EXISTS (
            SELECT 1 FROM json_each(NEW.locations_json)
            WHERE type <> 'text' OR length(value) > 200
        )) OR (EXISTS (
            SELECT 1 FROM json_each(NEW.themes_json)
            WHERE type <> 'text' OR length(value) > 200
        ))
BEGIN
            SELECT RAISE(ABORT,
                'world_seed_drafts JSON arrays require bounded text values');
        END""",
)

_TABLE_NAMES = ('adaptation_acceptance_events', 'adaptation_generation_runs', 'adaptation_revisions', 'adaptations', 'ai_runtime_preferences', 'chapter_plan_versions', 'character_biography_drafts', 'character_versions', 'characters', 'checkpoint_registry', 'creative_candidate_actions', 'creative_generation_candidates', 'creative_generation_jobs', 'creative_generation_steps', 'creative_unit_author_decisions', 'generation_runs', 'character_validations', 'outfits', 'projects', 'prompt_experiment_logs', 'prompt_profile_snapshots', 'prompt_project_versions', 'prompt_projects', 'prompt_scratch_drafts', 'prompt_scratch_recovery_journals', 'prompt_variants', 'scene_card_participants', 'scene_card_versions', 'scene_draft_block_sets', 'scene_draft_summaries', 'scene_drafts', 'story_bible_versions', 'story_bibles', 'story_block_anchors', 'story_block_revisions', 'story_chapters', 'story_exports', 'story_fragment_drafts', 'story_memory_entries', 'story_memory_proposals', 'story_outline_versions', 'story_outlines', 'story_requirement_versions', 'story_requirements', 'story_scene_revisions', 'story_scenes', 'style_profile_versions', 'style_profiles', 'video_prompt_bundles', 'world_seed_drafts')

def upgrade() -> None:
    for statement in (*_TABLES, *_INDEXES, *_TRIGGERS):
        op.execute(statement)


def downgrade() -> None:
    for table_name in reversed(_TABLE_NAMES):
        op.drop_table(table_name)
