"""Audited, deterministic Phase 4.3 video-prompt bundle workflow.

This boundary compiles text/JSON artifacts only.  It has no ComfyUI client,
HTTP/WebSocket/process/queue dependency, and never executes a media provider.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.orm import Session

from imaginarium_forge.application.errors import (
    CompilationBlockedError,
    NotFoundError,
    ValidationFailedError,
)
from imaginarium_forge.application.services.base import ServiceBase, SessionProvider
from imaginarium_forge.application.services.eligibility_service import EligibilityService
from imaginarium_forge.canonical import canonical_json
from imaginarium_forge.domain.character.presentation_cues import detect_presentation_cues
from imaginarium_forge.domain.common.enums import (
    ContentIntensity,
    ContentRating,
    RequestType,
)
from imaginarium_forge.domain.common.ids import new_id, utc_now_iso
from imaginarium_forge.domain.creative.models import ParticipantManifest
from imaginarium_forge.domain.eligibility.validator import EligibilityResult
from imaginarium_forge.domain.prompt.ast import PromptAST
from imaginarium_forge.domain.prompt.content_mode import (
    ContentMode,
    derives_adult,
    preflight_content_mode,
)
from imaginarium_forge.domain.prompt.input_snapshot import (
    PromptVersionSelectionSnapshot,
)
from imaginarium_forge.domain.prompt.video import (
    MediaTarget,
    VideoAspectRatio,
    VideoCamera,
    VideoEnvironment,
    VideoPromptAsset,
    VideoPromptAST,
    VideoPromptBundle,
    VideoPromptInputSnapshot,
    VideoPromptSourceSnapshot,
    VideoSubject,
    VideoTemporal,
)
from imaginarium_forge.infrastructure.db.repositories.characters import (
    CharacterRepository,
)
from imaginarium_forge.infrastructure.db.repositories.prompt_repos import (
    PromptProjectRecord,
    PromptProjectRepository,
    PromptProjectVersionRecord,
    PromptProjectVersionRepository,
)
from imaginarium_forge.infrastructure.db.repositories.video_prompt_repos import (
    VideoPromptBundleRecord,
    VideoPromptBundleRepository,
)

VIDEO_PROMPT_RENDERER_VERSION = "video-prompt-renderer-v1"


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _unique_texts(*values: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value.strip() for value in values if value.strip()))


class VideoPromptCompileOptions(BaseModel):
    """Author-controlled temporal options; converted to domain enums here."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    video_duration_seconds: int = Field(default=6, ge=1, le=60)
    video_fps: int = Field(default=24, ge=1, le=120)
    video_aspect: VideoAspectRatio = VideoAspectRatio.LANDSCAPE
    video_loop: bool = False


class VideoPromptCompileResult(BaseModel):
    """Typed outcome: blocked results intentionally contain no prompt payload."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    prompt_project_version_id: str
    parent_input_fingerprint: str
    source_input_fingerprint: str
    input_fingerprint: str
    allowed: bool
    video_eligibility_evaluation_ids: tuple[str, ...] = ()
    blocking_reasons: tuple[str, ...] = ()
    bundle_id: str | None = None
    bundle: VideoPromptBundle | None = None
    replayed: bool = False


class VideoPromptBundleSummary(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    bundle_id: str
    project_id: str
    prompt_project_id: str
    prompt_project_version_id: str
    content_mode: ContentMode
    parent_input_fingerprint: str
    source_input_fingerprint: str
    input_fingerprint: str
    bundle_sha256: str
    created_at: str


class VideoPromptPreparedExport(BaseModel):
    """Export bytes plus fresh authorization evidence kept outside the bytes."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    bundle_id: str
    bundle_sha256: str
    json_filename: str
    json_bytes: bytes
    text_filename: str
    text_bytes: bytes
    video_eligibility_evaluation_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _CompileContext:
    project: PromptProjectRecord
    version: PromptProjectVersionRecord
    ast: PromptAST
    selection: PromptVersionSelectionSnapshot
    manifest: ParticipantManifest
    source: VideoPromptSourceSnapshot

    @property
    def stable_source(self) -> tuple[str, ...]:
        return (
            self.version.id,
            self.version.prompt_project_id,
            self.version.input_fingerprint,
            self.version.selection_snapshot_sha256,
            self.source.fingerprint,
            self.ast.canonical_dump(),
            self.selection.canonical(),
            self.manifest.fingerprint,
        )


def _content_intensity(mode: ContentMode) -> ContentIntensity:
    return {
        ContentMode.EXPLICIT_ADULT: ContentIntensity.EXPLICIT,
        ContentMode.SUGGESTIVE: ContentIntensity.SUGGESTIVE,
        ContentMode.VIOLENT: ContentIntensity.VIOLENT,
        ContentMode.HORROR: ContentIntensity.HORROR,
        ContentMode.DARK: ContentIntensity.DARK,
    }.get(mode, ContentIntensity.GENERAL)


def _positive_preflight_texts(ast: PromptAST) -> tuple[str, ...]:
    """Only positive persisted AST fields; never source/negative/must-avoid."""

    values: list[str] = []
    for subject in ast.subjects:
        values.extend(subject.canonical_features)
        values.extend((subject.pose, subject.expression, *subject.motion_cues))
    values.extend(
        (
            ast.camera.shot,
            ast.camera.angle,
            ast.camera.lens_intent,
            ast.camera.framing,
            ast.camera.subject_placement,
            ast.camera.depth_of_field,
            ast.environment.location,
            ast.environment.time_of_day,
            ast.environment.weather,
            ast.environment.atmosphere,
            *ast.environment.background_elements,
            ast.lighting.key,
            ast.lighting.fill,
            ast.lighting.rim,
            ast.lighting.contrast,
            ast.lighting.color_temperature,
            *ast.user_intent.must_include,
        )
    )
    return tuple(value for value in values if value.strip())


def _preflight_blocking_reasons(*, mode: ContentMode, ast: PromptAST) -> tuple[str, ...]:
    texts = _positive_preflight_texts(ast)
    preflight = preflight_content_mode(mode, *texts)
    if preflight.confirmation_required:
        return (preflight.message_zh_tw,)
    if derives_adult(mode):
        cues = detect_presentation_cues(*texts)
        if cues.has_conflict:
            flags: tuple[str, ...] = ("minor_era",) if cues.minor_era else ()
            flags += cues.childlike_flags
            return ("成人 Video AST 的正向欄位包含未成年期或孩童化呈現線索：" + "、".join(flags),)
    return ()


def _negative_constraints(ast: PromptAST) -> tuple[str, ...]:
    return _unique_texts(
        *ast.negative.semantic,
        *ast.negative.anatomy,
        *ast.negative.identity,
        *ast.negative.style,
        *ast.negative.composition,
        *ast.user_intent.must_avoid,
        "identity drift",
        "subject swap",
        "flicker",
        "text watermark",
    )


def _render_asset(ast: VideoPromptAST) -> VideoPromptAsset:
    subject_text = " ; ".join(
        " | ".join(
            value
            for value in (
                f"slot={subject.slot_id}",
                f"role={subject.role}" if subject.role else "",
                "primary" if subject.is_primary else "",
                "identity=" + ", ".join(subject.identity_features),
            )
            if value
        )
        for subject in ast.subjects
    )
    camera_text = ", ".join(
        _unique_texts(
            ast.camera.shot,
            ast.camera.angle,
            ast.camera.lens_intent,
            ast.camera.framing,
            *ast.camera.movement,
        )
    )
    environment_text = ", ".join(
        _unique_texts(
            ast.environment.location,
            ast.environment.time_of_day,
            ast.environment.weather,
            ast.environment.atmosphere,
            ast.environment.lighting,
            *ast.environment.background_elements,
            *ast.environment.motion,
        )
    )
    positive = "\n".join(
        (
            f"target: {ast.media_target.value}",
            f"subjects: {subject_text}",
            f"camera: {camera_text or 'stable framing'}",
            f"environment: {environment_text or 'preserve source environment'}",
            "temporal: "
            f"{ast.temporal.duration_seconds}s, {ast.temporal.fps}fps, "
            f"{ast.temporal.aspect_ratio.value}, motion="
            f"{', '.join(ast.temporal.motion) or 'subtle natural motion'}",
            "continuity: " + ", ".join(ast.temporal.continuity_constraints),
        )
    )
    negative = ", ".join(ast.negative_constraints)
    natural = (
        f"Create a {ast.temporal.duration_seconds}-second "
        f"{ast.media_target.value} at {ast.temporal.fps} fps in "
        f"{ast.temporal.aspect_ratio.value}. Preserve exactly these subjects: "
        f"{subject_text}. Use {camera_text or 'stable framing'} and "
        f"{environment_text or 'the persisted source environment'}. "
        f"Maintain {', '.join(ast.temporal.continuity_constraints)}."
    )
    return VideoPromptAsset(
        media_target=ast.media_target,
        ast=ast,
        ast_sha256=ast.sha256,
        positive_prompt=positive,
        positive_prompt_sha256=_sha256_text(positive),
        negative_prompt=negative,
        negative_prompt_sha256=_sha256_text(negative),
        natural_language_prompt=natural,
        natural_language_prompt_sha256=_sha256_text(natural),
    )


def _fixed_lf_export(bundle_id: str, bundle: VideoPromptBundle) -> bytes:
    lines = [
        "IMAGINARIUM FORGE VIDEO PROMPT BUNDLE",
        f"schema: {bundle.schema_version}",
        f"bundle_id: {bundle_id}",
        f"bundle_sha256: {bundle.sha256}",
        f"project_id: {bundle.project_id}",
        f"prompt_project_id: {bundle.prompt_project_id}",
        f"prompt_project_version_id: {bundle.prompt_project_version_id}",
        f"content_mode: {bundle.content_mode.value}",
        f"parent_input_fingerprint: {bundle.parent_input_fingerprint}",
        f"source_input_fingerprint: {bundle.source_input_fingerprint}",
        f"input_fingerprint: {bundle.input_fingerprint}",
        f"participant_manifest_fingerprint: {bundle.participant_manifest_fingerprint}",
        "",
    ]
    for asset in (bundle.character_asset, bundle.scene_asset):
        lines.extend(
            (
                f"[{asset.media_target.value}]",
                f"ast_sha256: {asset.ast_sha256}",
                "positive_prompt:",
                asset.positive_prompt,
                "negative_prompt:",
                asset.negative_prompt,
                "natural_language_prompt:",
                asset.natural_language_prompt,
                "",
            )
        )
    return ("\n".join(lines) + "\n").encode("utf-8")


class VideoPromptBundleService(ServiceBase):
    """Compile, inspect and export immutable text-only video prompt bundles."""

    def __init__(
        self,
        session_factory: SessionProvider,
        *,
        eligibility: EligibilityService | None = None,
    ) -> None:
        super().__init__(session_factory)
        self._eligibility = eligibility or EligibilityService(session_factory)

    def _load_context(
        self,
        session: Session,
        *,
        prompt_version_id: str,
        expected_project_id: str,
    ) -> _CompileContext:
        version = PromptProjectVersionRepository(session).get(prompt_version_id)
        if version is None:
            raise NotFoundError(f"找不到提示版本：{prompt_version_id}")
        project = PromptProjectRepository(session).get(version.prompt_project_id)
        if project is None or project.project_id != expected_project_id:
            raise NotFoundError(f"找不到此專案的提示版本：{prompt_version_id}")
        try:
            ast = PromptAST.model_validate_json(version.ast_json)
            if ast.canonical_dump() != version.ast_json:
                raise ValueError("Prompt AST 不是 canonical JSON")
            selection = PromptVersionSelectionSnapshot.model_validate_json(
                version.selection_snapshot_json
            )
            if selection.canonical() != version.selection_snapshot_json:
                raise ValueError("selection snapshot 不是 canonical JSON")
            if selection.sha256() != version.selection_snapshot_sha256:
                raise ValueError("selection snapshot SHA-256 不符")
            if selection.project_id != expected_project_id:
                raise ValueError("selection snapshot project ownership 不符")
            if not selection.participant_manifest_canonical_json:
                raise ValueError("Video bundle 需要 exact participant manifest")
            manifest = ParticipantManifest.model_validate_json(
                selection.participant_manifest_canonical_json
            )
            manifest_json = canonical_json(manifest.model_dump(mode="json"))
            if manifest_json != selection.participant_manifest_canonical_json:
                raise ValueError("participant manifest 不是 canonical JSON")
            if manifest.fingerprint != selection.participant_manifest_fingerprint:
                raise ValueError("participant manifest fingerprint 不符")
            if not manifest.participants[0].is_primary:
                raise ValueError("participant manifest 必須使用 canonical primary-first order")
            if selection.adult_content_requested != derives_adult(selection.content_mode):
                raise ValueError("selection 的成人模式衍生值不一致")
            primary = manifest.primary
            if (
                selection.character_id != primary.character_id
                or selection.character_version_id != primary.character_version_id
            ):
                raise ValueError("selection primary compatibility IDs 與 manifest.primary 不符")
            source = VideoPromptSourceSnapshot(
                prompt_ast_sha256=_sha256_text(ast.canonical_dump()),
                selection_snapshot_sha256=version.selection_snapshot_sha256,
                parent_input_fingerprint=version.input_fingerprint,
            )

            expected_subjects = tuple(
                (
                    pin.slot_id,
                    pin.character_id,
                    pin.character_version_id,
                )
                for pin in manifest.participants
            )
            actual_subjects = tuple(
                (
                    subject.subject_id,
                    subject.character_id,
                    subject.character_version_id,
                )
                for subject in ast.subjects
            )
            if actual_subjects != expected_subjects:
                raise ValueError("Prompt AST subjects 必須與 exact manifest/order 一致")

            characters = CharacterRepository(session)
            for pin, subject in zip(manifest.participants, ast.subjects, strict=True):
                character = characters.get(pin.character_id)
                selected_version = characters.get_version(pin.character_version_id)
                if character is None or character.project_id != expected_project_id:
                    raise ValueError("manifest 角色遺失或跨專案")
                if selected_version is None or selected_version.character_id != pin.character_id:
                    raise ValueError("manifest 角色版本遺失或 ownership 不符")
                if not _unique_texts(*subject.canonical_features):
                    raise ValueError("每位 Video subject 必須有 persisted canonical features")
        except (ValidationError, ValueError) as exc:
            raise ValidationFailedError(f"Video bundle 來源完整性驗證失敗：{exc}") from exc
        return _CompileContext(project, version, ast, selection, manifest, source)

    def _load_bundle_context(
        self,
        session: Session,
        *,
        bundle_id: str,
        expected_project_id: str,
    ) -> tuple[VideoPromptBundleRecord, _CompileContext]:
        try:
            record = VideoPromptBundleRepository(session).get(bundle_id)
        except (ValidationError, ValueError) as exc:
            raise ValidationFailedError(f"Video bundle 持久化完整性驗證失敗：{exc}") from exc
        if record is None or record.project_id != expected_project_id:
            raise NotFoundError(f"找不到此專案的 Video bundle：{bundle_id}")
        context = self._load_context(
            session,
            prompt_version_id=record.prompt_project_version_id,
            expected_project_id=expected_project_id,
        )
        bundle = record.bundle
        if bundle.sha256 != record.bundle_sha256:
            raise ValidationFailedError("Video bundle 持久化完整性 SHA-256 不符")
        expected = (
            (bundle.project_id, expected_project_id),
            (bundle.prompt_project_id, context.project.id),
            (bundle.prompt_project_version_id, context.version.id),
            (bundle.parent_input_fingerprint, context.version.input_fingerprint),
            (bundle.source_input_fingerprint, context.source.fingerprint),
            (bundle.selection_snapshot_sha256, context.version.selection_snapshot_sha256),
            (bundle.content_mode, context.selection.content_mode),
            (
                bundle.participant_manifest_canonical_json,
                context.selection.participant_manifest_canonical_json,
            ),
            (
                bundle.participant_manifest_fingerprint,
                context.manifest.fingerprint,
            ),
        )
        if any(actual != wanted for actual, wanted in expected):
            raise ValidationFailedError("Video bundle ownership/provenance 與來源版本不一致")
        return record, context

    def _audit(
        self, context: _CompileContext
    ) -> tuple[bool, tuple[EligibilityResult, ...], tuple[str, ...]]:
        adult = derives_adult(context.selection.content_mode)
        allowed, results, ids = self._eligibility.evaluate_many_audited(
            participants=list(context.manifest.exact_pairs),
            request_type=RequestType.VIDEO_PROMPT_COMPILE,
            content_rating=ContentRating.MATURE if adult else ContentRating.GENERAL,
            content_intensity=_content_intensity(context.selection.content_mode),
            adult_content_requested=adult,
        )
        return allowed, tuple(results), tuple(ids)

    @staticmethod
    def _build_assets(
        context: _CompileContext,
        options: VideoPromptCompileOptions,
    ) -> tuple[VideoPromptAsset, VideoPromptAsset]:
        source_sha = _sha256_text(context.ast.canonical_dump())
        subjects: list[VideoSubject] = []
        for pin, source in zip(context.manifest.participants, context.ast.subjects, strict=True):
            features = _unique_texts(*source.canonical_features)
            subjects.append(
                VideoSubject(
                    slot_id=pin.slot_id,
                    character_id=pin.character_id,
                    character_version_id=pin.character_version_id,
                    role=pin.role,
                    is_primary=pin.is_primary,
                    identity_features=features,
                    identity_features_sha256=_sha256_text(canonical_json(list(features))),
                )
            )
        source_directions = _unique_texts(
            *(
                value
                for subject in context.ast.subjects
                for value in (subject.pose, subject.expression, *subject.motion_cues)
            ),
            *context.ast.user_intent.must_include,
        )
        lighting = ", ".join(
            _unique_texts(
                context.ast.lighting.key,
                context.ast.lighting.fill,
                context.ast.lighting.rim,
                context.ast.lighting.contrast,
                context.ast.lighting.color_temperature,
            )
        )
        camera = VideoCamera(
            shot=context.ast.camera.shot,
            angle=context.ast.camera.angle,
            lens_intent=context.ast.camera.lens_intent,
            framing=", ".join(
                _unique_texts(
                    context.ast.camera.framing,
                    context.ast.camera.subject_placement,
                    context.ast.camera.depth_of_field,
                )
            ),
        )
        environment = VideoEnvironment(
            location=context.ast.environment.location,
            time_of_day=context.ast.environment.time_of_day,
            weather=context.ast.environment.weather,
            atmosphere=context.ast.environment.atmosphere,
            lighting=lighting,
            background_elements=context.ast.environment.background_elements,
            motion=source_directions,
        )
        negatives = _negative_constraints(context.ast)

        def make(target: MediaTarget) -> VideoPromptAsset:
            continuity = ["preserve exact participant identities", "no subject swap"]
            if target is MediaTarget.SCENE_VIDEO:
                continuity.append("preserve spatial relationships and environment")
            if options.video_loop:
                continuity.append("seamless loop")
            temporal = VideoTemporal(
                duration_seconds=options.video_duration_seconds,
                fps=options.video_fps,
                aspect_ratio=options.video_aspect,
                loop=options.video_loop,
                motion=source_directions,
                continuity_constraints=tuple(continuity),
                start_state="loop anchor" if options.video_loop else "persisted scene start",
                end_state="loop anchor" if options.video_loop else "continuous scene end",
            )
            video_ast = VideoPromptAST(
                media_target=target,
                subjects=tuple(subjects),
                camera=camera,
                temporal=temporal,
                environment=environment,
                negative_constraints=negatives,
                source_prompt_ast_sha256=source_sha,
                participant_manifest_fingerprint=context.manifest.fingerprint,
                renderer_version=VIDEO_PROMPT_RENDERER_VERSION,
            )
            return _render_asset(video_ast)

        return make(MediaTarget.CHARACTER_VIDEO), make(MediaTarget.SCENE_VIDEO)

    @staticmethod
    def _video_input(
        *,
        source_input_fingerprint: str,
        options: VideoPromptCompileOptions,
    ) -> VideoPromptInputSnapshot:
        return VideoPromptInputSnapshot(
            source_input_fingerprint=source_input_fingerprint,
            renderer_version=VIDEO_PROMPT_RENDERER_VERSION,
            duration_seconds=options.video_duration_seconds,
            fps=options.video_fps,
            aspect_ratio=options.video_aspect,
            loop=options.video_loop,
        )

    @staticmethod
    def _options_match_bundle(
        options: VideoPromptCompileOptions, bundle: VideoPromptBundle
    ) -> bool:
        for asset in (bundle.character_asset, bundle.scene_asset):
            temporal = asset.ast.temporal
            if (
                temporal.duration_seconds != options.video_duration_seconds
                or temporal.fps != options.video_fps
                or temporal.aspect_ratio is not options.video_aspect
                or temporal.loop != options.video_loop
                or asset.ast.renderer_version != VIDEO_PROMPT_RENDERER_VERSION
            ):
                return False
        return True

    def compile_from_version(
        self,
        *,
        prompt_version_id: str,
        expected_project_id: str,
        video_duration_seconds: int = 6,
        video_fps: int = 24,
        video_aspect: VideoAspectRatio | str = VideoAspectRatio.LANDSCAPE,
        video_loop: bool = False,
    ) -> VideoPromptCompileResult:
        options = VideoPromptCompileOptions.model_validate(
            {
                "video_duration_seconds": video_duration_seconds,
                "video_fps": video_fps,
                "video_aspect": video_aspect,
                "video_loop": video_loop,
            }
        )
        context = self._read_only(
            lambda session: self._load_context(
                session,
                prompt_version_id=prompt_version_id,
                expected_project_id=expected_project_id,
            )
        )
        video_input = self._video_input(
            source_input_fingerprint=context.source.fingerprint,
            options=options,
        )
        reasons = _preflight_blocking_reasons(mode=context.selection.content_mode, ast=context.ast)
        if reasons:
            return VideoPromptCompileResult(
                prompt_project_version_id=context.version.id,
                parent_input_fingerprint=context.version.input_fingerprint,
                source_input_fingerprint=context.source.fingerprint,
                input_fingerprint=video_input.fingerprint,
                allowed=False,
                blocking_reasons=reasons,
            )

        existing = self._read_only(
            lambda session: VideoPromptBundleRepository(session).list_for_version(
                context.version.id
            )
        )
        matching = next(
            (
                record
                for record in existing
                if record.input_fingerprint == video_input.fingerprint
            ),
            None,
        )
        if matching is not None and not self._options_match_bundle(options, matching.bundle):
            raise ValidationFailedError("Video input fingerprint 發生 deterministic collision")

        eligibility_window_started_at = utc_now_iso()
        allowed, evaluations, evaluation_ids = self._audit(context)
        if not allowed:
            blocked = tuple(result.message for result in evaluations if not result.allowed)
            return VideoPromptCompileResult(
                prompt_project_version_id=context.version.id,
                parent_input_fingerprint=context.version.input_fingerprint,
                source_input_fingerprint=context.source.fingerprint,
                input_fingerprint=video_input.fingerprint,
                allowed=False,
                video_eligibility_evaluation_ids=evaluation_ids,
                blocking_reasons=blocked or ("VIDEO 資格驗證未通過",),
            )

        fresh = self._read_only(
            lambda session: self._load_context(
                session,
                prompt_version_id=prompt_version_id,
                expected_project_id=expected_project_id,
            )
        )
        if fresh.stable_source != context.stable_source:
            raise ValidationFailedError("提示版本或 selection 在稽核期間發生變更")
        fresh_video_input = self._video_input(
            source_input_fingerprint=fresh.source.fingerprint,
            options=options,
        )
        if fresh_video_input != video_input:
            raise ValidationFailedError("Video input 在稽核期間發生變更")
        character_asset, scene_asset = self._build_assets(fresh, options)

        if matching is not None:
            stored = matching.bundle
            if stored.character_asset != character_asset or stored.scene_asset != scene_asset:
                raise ValidationFailedError("既有 Video bundle 不符合 deterministic renderer")
            return VideoPromptCompileResult(
                prompt_project_version_id=fresh.version.id,
                parent_input_fingerprint=fresh.version.input_fingerprint,
                source_input_fingerprint=fresh.source.fingerprint,
                input_fingerprint=fresh_video_input.fingerprint,
                allowed=True,
                video_eligibility_evaluation_ids=evaluation_ids,
                bundle_id=matching.id,
                bundle=stored,
                replayed=True,
            )

        bundle = VideoPromptBundle(
            project_id=expected_project_id,
            prompt_project_id=fresh.project.id,
            prompt_project_version_id=fresh.version.id,
            content_mode=fresh.selection.content_mode,
            parent_input_fingerprint=fresh.version.input_fingerprint,
            source_input_fingerprint=fresh.source.fingerprint,
            input_fingerprint=fresh_video_input.fingerprint,
            selection_snapshot_sha256=fresh.version.selection_snapshot_sha256,
            participant_manifest_canonical_json=(
                fresh.selection.participant_manifest_canonical_json
            ),
            participant_manifest_fingerprint=fresh.manifest.fingerprint,
            video_eligibility_evaluation_ids=evaluation_ids,
            character_asset=character_asset,
            scene_asset=scene_asset,
            eligibility_window_started_at=eligibility_window_started_at,
            created_at=utc_now_iso(),
        )
        bundle_id = new_id()
        with self._transaction() as session:
            VideoPromptBundleRepository(session).add(
                VideoPromptBundleRecord.from_bundle(bundle_id=bundle_id, bundle=bundle)
            )
            session.flush()
        return VideoPromptCompileResult(
            prompt_project_version_id=fresh.version.id,
            parent_input_fingerprint=fresh.version.input_fingerprint,
            source_input_fingerprint=fresh.source.fingerprint,
            input_fingerprint=fresh_video_input.fingerprint,
            allowed=True,
            video_eligibility_evaluation_ids=evaluation_ids,
            bundle_id=bundle_id,
            bundle=bundle,
        )

    def get(self, bundle_id: str, *, expected_project_id: str) -> VideoPromptBundle:
        record, _context = self._read_only(
            lambda session: self._load_bundle_context(
                session,
                bundle_id=bundle_id,
                expected_project_id=expected_project_id,
            )
        )
        return record.bundle

    def list_summaries(self, *, expected_project_id: str) -> list[VideoPromptBundleSummary]:
        try:
            records = self._read_only(
                lambda session: VideoPromptBundleRepository(session).list_for_project(
                    expected_project_id
                )
            )
        except (ValidationError, ValueError) as exc:
            raise ValidationFailedError(f"Video bundle 清單完整性驗證失敗：{exc}") from exc
        return [
            VideoPromptBundleSummary(
                bundle_id=record.id,
                project_id=record.project_id,
                prompt_project_id=record.prompt_project_id,
                prompt_project_version_id=record.prompt_project_version_id,
                content_mode=record.bundle.content_mode,
                parent_input_fingerprint=record.parent_input_fingerprint,
                source_input_fingerprint=record.source_input_fingerprint,
                input_fingerprint=record.input_fingerprint,
                bundle_sha256=record.bundle_sha256,
                created_at=record.created_at,
            )
            for record in records
        ]

    def prepare_export(
        self, bundle_id: str, *, expected_project_id: str
    ) -> VideoPromptPreparedExport:
        record, context = self._read_only(
            lambda session: self._load_bundle_context(
                session,
                bundle_id=bundle_id,
                expected_project_id=expected_project_id,
            )
        )
        reasons = _preflight_blocking_reasons(mode=context.selection.content_mode, ast=context.ast)
        if reasons:
            raise CompilationBlockedError("；".join(reasons))
        allowed, evaluations, evaluation_ids = self._audit(context)
        if not allowed:
            reasons = tuple(result.message for result in evaluations if not result.allowed)
            raise CompilationBlockedError("；".join(reasons) or "VIDEO 資格驗證未通過")

        fresh_record, fresh_context = self._read_only(
            lambda session: self._load_bundle_context(
                session,
                bundle_id=bundle_id,
                expected_project_id=expected_project_id,
            )
        )
        if fresh_context.stable_source != context.stable_source:
            raise ValidationFailedError("提示版本或 selection 在匯出稽核期間發生變更")
        if fresh_record != record:
            raise ValidationFailedError("Video bundle 在匯出稽核期間發生變更")
        record, context = fresh_record, fresh_context
        bundle = record.bundle
        options = VideoPromptCompileOptions(
            video_duration_seconds=(bundle.character_asset.ast.temporal.duration_seconds),
            video_fps=bundle.character_asset.ast.temporal.fps,
            video_aspect=bundle.character_asset.ast.temporal.aspect_ratio,
            video_loop=bundle.character_asset.ast.temporal.loop,
        )
        character_asset, scene_asset = self._build_assets(context, options)
        if bundle.character_asset != character_asset or bundle.scene_asset != scene_asset:
            raise ValidationFailedError("Video bundle 不符合 deterministic renderer")
        json_bytes = (bundle.canonical() + "\n").encode("utf-8")
        return VideoPromptPreparedExport(
            bundle_id=bundle_id,
            bundle_sha256=bundle.sha256,
            json_filename=f"video-prompt-bundle-{bundle_id}.json",
            json_bytes=json_bytes,
            text_filename=f"video-prompt-bundle-{bundle_id}.txt",
            text_bytes=_fixed_lf_export(bundle_id, bundle),
            video_eligibility_evaluation_ids=evaluation_ids,
        )
