"""Canonical, model-independent video prompt contracts (Phase 4.3).

These models preserve author intent and exact Canon provenance.  They do not
claim that a renderer followed the prompt, and they never execute a media
provider.  Image ``PromptAST`` and ``VideoPromptAST`` deliberately remain
separate contracts so changing temporal intent cannot silently change an image
compilation fingerprint.
"""

from __future__ import annotations

import hashlib
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from imaginarium_forge.canonical import canonical_json
from imaginarium_forge.domain.prompt.content_mode import ContentMode

VIDEO_PROMPT_AST_VERSION: Literal["1.0"] = "1.0"
VIDEO_PROMPT_BUNDLE_SCHEMA: Literal[
    "imaginarium-forge.video-prompt-bundle.v1"
] = "imaginarium-forge.video-prompt-bundle.v1"


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)


class MediaTarget(StrEnum):
    CHARACTER_VIDEO = "character_video"
    SCENE_VIDEO = "scene_video"


class VideoAspectRatio(StrEnum):
    LANDSCAPE = "16:9"
    PORTRAIT = "9:16"
    SQUARE = "1:1"
    CLASSIC_LANDSCAPE = "4:3"
    CLASSIC_PORTRAIT = "3:4"


class VideoSubject(BaseModel):
    """One exact participant pin plus a hash-bound identity feature set."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    slot_id: str
    character_id: str
    character_version_id: str
    role: str = ""
    is_primary: bool = False
    identity_features: tuple[str, ...] = Field(min_length=1, max_length=64)
    identity_features_sha256: str

    @field_validator("slot_id", "character_id", "character_version_id")
    @classmethod
    def _required_id(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("VideoSubject 的 slot、角色與版本 ID 不得空白")
        return value

    @field_validator("role")
    @classmethod
    def _strip_role(cls, value: str) -> str:
        return value.strip()

    @field_validator("identity_features")
    @classmethod
    def _identity_features_are_nonempty(
        cls, value: tuple[str, ...]
    ) -> tuple[str, ...]:
        normalized = tuple(feature.strip() for feature in value)
        if any(not feature for feature in normalized):
            raise ValueError("VideoSubject identity feature 不得空白")
        return normalized

    @model_validator(mode="after")
    def _identity_hash_matches(self) -> VideoSubject:
        payload = canonical_json(list(self.identity_features))
        if self.identity_features_sha256 != _sha256_text(payload):
            raise ValueError("VideoSubject identity features SHA-256 不符")
        return self


class VideoCamera(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    shot: str = ""
    angle: str = ""
    lens_intent: str = ""
    framing: str = ""
    movement: tuple[str, ...] = ()


class VideoTemporal(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    duration_seconds: int = Field(ge=1, le=60)
    fps: int = Field(ge=1, le=120)
    aspect_ratio: VideoAspectRatio
    loop: bool = False
    motion: tuple[str, ...] = ()
    continuity_constraints: tuple[str, ...] = ()
    start_state: str = ""
    end_state: str = ""


class VideoEnvironment(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    location: str = ""
    time_of_day: str = ""
    weather: str = ""
    atmosphere: str = ""
    lighting: str = ""
    background_elements: tuple[str, ...] = ()
    motion: tuple[str, ...] = ()


class VideoPromptSourceSnapshot(BaseModel):
    """Hash-bound persisted parent source, independent of its compiler kind."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    prompt_ast_sha256: str
    selection_snapshot_sha256: str
    parent_input_fingerprint: str

    @field_validator(
        "prompt_ast_sha256", "selection_snapshot_sha256", "parent_input_fingerprint"
    )
    @classmethod
    def _source_hashes_are_sha256(cls, value: str) -> str:
        if not _is_sha256(value):
            raise ValueError("Video source snapshot 欄位必須是小寫 SHA-256")
        return value

    def canonical(self) -> str:
        return canonical_json(self.model_dump(mode="json"))

    @property
    def fingerprint(self) -> str:
        return _sha256_text(self.canonical())


class VideoPromptInputSnapshot(BaseModel):
    """Every author/renderer option that identifies one Video compilation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[
        "imaginarium-forge.video-prompt-bundle.v1"
    ] = VIDEO_PROMPT_BUNDLE_SCHEMA
    source_input_fingerprint: str
    renderer_version: str
    duration_seconds: int = Field(ge=1, le=60)
    fps: int = Field(ge=1, le=120)
    aspect_ratio: VideoAspectRatio
    loop: bool = False

    @field_validator("source_input_fingerprint")
    @classmethod
    def _source_fingerprint_is_sha256(cls, value: str) -> str:
        if not _is_sha256(value):
            raise ValueError("Video source input fingerprint 必須是小寫 SHA-256")
        return value

    @field_validator("renderer_version")
    @classmethod
    def _input_renderer_required(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Video input renderer_version 不得空白")
        return value

    def canonical(self) -> str:
        return canonical_json(self.model_dump(mode="json"))

    @property
    def fingerprint(self) -> str:
        return _sha256_text(self.canonical())


class VideoPromptAST(BaseModel):
    """Canonical temporal intent for exactly one video media target."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    video_prompt_ast_version: Literal["1.0"] = VIDEO_PROMPT_AST_VERSION
    media_target: MediaTarget
    subjects: tuple[VideoSubject, ...] = Field(min_length=1, max_length=24)
    camera: VideoCamera = Field(default_factory=VideoCamera)
    temporal: VideoTemporal
    environment: VideoEnvironment = Field(default_factory=VideoEnvironment)
    negative_constraints: tuple[str, ...] = ()
    source_prompt_ast_sha256: str
    participant_manifest_fingerprint: str
    renderer_version: str

    @field_validator("source_prompt_ast_sha256", "participant_manifest_fingerprint")
    @classmethod
    def _required_sha256(cls, value: str) -> str:
        if not _is_sha256(value):
            raise ValueError("VideoPromptAST provenance 必須是小寫 SHA-256")
        return value

    @field_validator("renderer_version")
    @classmethod
    def _renderer_required(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("VideoPromptAST renderer_version 不得空白")
        return value

    @model_validator(mode="after")
    def _subject_roster_is_unique(self) -> VideoPromptAST:
        slot_ids = [subject.slot_id for subject in self.subjects]
        exact_pairs = [
            (subject.character_id, subject.character_version_id)
            for subject in self.subjects
        ]
        if len(slot_ids) != len(set(slot_ids)):
            raise ValueError("VideoPromptAST subject slot_id 不可重複")
        if len(exact_pairs) != len(set(exact_pairs)):
            raise ValueError("VideoPromptAST 角色／版本 exact pair 不可重複")
        if sum(subject.is_primary for subject in self.subjects) != 1:
            raise ValueError("VideoPromptAST 必須且只能有一位 primary subject")
        return self

    def canonical(self) -> str:
        return canonical_json(self.model_dump(mode="json"))

    @property
    def sha256(self) -> str:
        return _sha256_text(self.canonical())


class VideoPromptAsset(BaseModel):
    """One target's canonical AST and deterministic rendered prompt strings."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    media_target: MediaTarget
    ast: VideoPromptAST
    ast_sha256: str
    positive_prompt: str
    positive_prompt_sha256: str
    negative_prompt: str = ""
    negative_prompt_sha256: str
    natural_language_prompt: str
    natural_language_prompt_sha256: str

    @model_validator(mode="after")
    def _target_and_hashes_match(self) -> VideoPromptAsset:
        if self.ast.media_target is not self.media_target:
            raise ValueError("VideoPromptAsset target 與 AST target 不一致")
        if not self.positive_prompt.strip() or not self.natural_language_prompt.strip():
            raise ValueError("ready video asset 必須包含 positive 與 natural-language prompt")
        expected = (
            (self.ast_sha256, self.ast.sha256, "AST"),
            (
                self.positive_prompt_sha256,
                _sha256_text(self.positive_prompt),
                "positive prompt",
            ),
            (
                self.negative_prompt_sha256,
                _sha256_text(self.negative_prompt),
                "negative prompt",
            ),
            (
                self.natural_language_prompt_sha256,
                _sha256_text(self.natural_language_prompt),
                "natural-language prompt",
            ),
        )
        for actual, digest, label in expected:
            if actual != digest:
                raise ValueError(f"VideoPromptAsset {label} SHA-256 不符")
        return self


class VideoPromptBundle(BaseModel):
    """Durable, exportable evidence for both canonical video targets."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[
        "imaginarium-forge.video-prompt-bundle.v1"
    ] = VIDEO_PROMPT_BUNDLE_SCHEMA
    project_id: str
    prompt_project_id: str
    prompt_project_version_id: str
    content_mode: ContentMode
    parent_input_fingerprint: str
    source_input_fingerprint: str
    input_fingerprint: str
    selection_snapshot_sha256: str
    participant_manifest_canonical_json: str
    participant_manifest_fingerprint: str
    video_eligibility_evaluation_ids: tuple[str, ...] = Field(
        min_length=1, max_length=24
    )
    character_asset: VideoPromptAsset
    scene_asset: VideoPromptAsset
    eligibility_window_started_at: str
    created_at: str

    @field_validator(
        "project_id",
        "prompt_project_id",
        "prompt_project_version_id",
        "eligibility_window_started_at",
        "created_at",
    )
    @classmethod
    def _required_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("VideoPromptBundle ownership/time 不得空白")
        return value

    @field_validator(
        "parent_input_fingerprint",
        "source_input_fingerprint",
        "input_fingerprint",
        "selection_snapshot_sha256",
    )
    @classmethod
    def _bundle_hash_field(cls, value: str) -> str:
        if not _is_sha256(value):
            raise ValueError("VideoPromptBundle fingerprint 必須是小寫 SHA-256")
        return value

    @field_validator("video_eligibility_evaluation_ids")
    @classmethod
    def _audit_ids_unique(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(audit_id.strip() for audit_id in value)
        if any(not audit_id for audit_id in normalized):
            raise ValueError("VIDEO eligibility audit ID 不得空白")
        if len(normalized) != len(set(normalized)):
            raise ValueError("VIDEO eligibility audit ID 不可重複")
        return normalized

    @model_validator(mode="after")
    def _manifest_subjects_and_targets_match(self) -> VideoPromptBundle:
        # Local import keeps ``creative.models`` free to expose author-facing
        # string/Literal video controls later without creating an import cycle.
        from imaginarium_forge.domain.creative.models import ParticipantManifest

        manifest = ParticipantManifest.model_validate_json(
            self.participant_manifest_canonical_json
        )
        canonical_manifest = canonical_json(manifest.model_dump(mode="json"))
        if canonical_manifest != self.participant_manifest_canonical_json:
            raise ValueError("VideoPromptBundle participant manifest 必須是 canonical JSON")
        if manifest.fingerprint != self.participant_manifest_fingerprint:
            raise ValueError("VideoPromptBundle participant manifest fingerprint 不符")
        if not manifest.participants[0].is_primary:
            raise ValueError("VideoPromptBundle manifest 必須使用 canonical primary-first order")
        if len(self.video_eligibility_evaluation_ids) != len(manifest.participants):
            raise ValueError("VIDEO eligibility audits 必須逐一覆蓋 participant manifest")

        expected_subjects = tuple(
            (
                pin.slot_id,
                pin.character_id,
                pin.character_version_id,
                pin.role,
                pin.is_primary,
            )
            for pin in manifest.participants
        )
        assets = (
            (self.character_asset, MediaTarget.CHARACTER_VIDEO),
            (self.scene_asset, MediaTarget.SCENE_VIDEO),
        )
        for asset, expected_target in assets:
            if asset.media_target is not expected_target:
                raise ValueError("VideoPromptBundle 必須各含一個 character/scene target")
            ast = asset.ast
            actual_subjects = tuple(
                (
                    subject.slot_id,
                    subject.character_id,
                    subject.character_version_id,
                    subject.role,
                    subject.is_primary,
                )
                for subject in ast.subjects
            )
            if actual_subjects != expected_subjects:
                raise ValueError("VideoPromptAST subjects 必須與 manifest exact/order 相等")
            if ast.participant_manifest_fingerprint != manifest.fingerprint:
                raise ValueError("VideoPromptAST manifest fingerprint 不符")

        character_ast = self.character_asset.ast
        scene_ast = self.scene_asset.ast
        if character_ast.subjects != scene_ast.subjects:
            raise ValueError("兩個 video targets 必須綁定完全相同的 VideoSubject roster")
        if (
            character_ast.source_prompt_ast_sha256
            != scene_ast.source_prompt_ast_sha256
            or character_ast.renderer_version != scene_ast.renderer_version
        ):
            raise ValueError("兩個 video targets 必須綁定相同來源 AST 與 renderer version")
        expected_source = VideoPromptSourceSnapshot(
            prompt_ast_sha256=character_ast.source_prompt_ast_sha256,
            selection_snapshot_sha256=self.selection_snapshot_sha256,
            parent_input_fingerprint=self.parent_input_fingerprint,
        )
        if self.source_input_fingerprint != expected_source.fingerprint:
            raise ValueError("VideoPromptBundle source fingerprint 與 persisted parent 不符")
        character_temporal = character_ast.temporal
        scene_temporal = scene_ast.temporal
        character_options = (
            character_temporal.duration_seconds,
            character_temporal.fps,
            character_temporal.aspect_ratio,
            character_temporal.loop,
        )
        scene_options = (
            scene_temporal.duration_seconds,
            scene_temporal.fps,
            scene_temporal.aspect_ratio,
            scene_temporal.loop,
        )
        if character_options != scene_options:
            raise ValueError("兩個 video targets 必須使用相同 temporal options")
        expected_input = VideoPromptInputSnapshot(
            source_input_fingerprint=self.source_input_fingerprint,
            renderer_version=character_ast.renderer_version,
            duration_seconds=character_temporal.duration_seconds,
            fps=character_temporal.fps,
            aspect_ratio=character_temporal.aspect_ratio,
            loop=character_temporal.loop,
        )
        if self.input_fingerprint != expected_input.fingerprint:
            raise ValueError("VideoPromptBundle input fingerprint 與來源／options 不符")
        return self

    def canonical(self) -> str:
        return canonical_json(self.model_dump(mode="json"))

    @property
    def sha256(self) -> str:
        return _sha256_text(self.canonical())
