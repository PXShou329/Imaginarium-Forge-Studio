"""Application workflow behind the Creative Launchpad.

The service intentionally composes existing versioned aggregates instead of
creating a second source of truth. Preview is pure. Character, story and prompt
saves are separate commands; the three aggregates (six parent/version rows)
inside one story foundation are committed in one transaction.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from pydantic import ValidationError

from imaginarium_forge.application.errors import ValidationFailedError
from imaginarium_forge.application.services.base import ServiceBase, SessionProvider
from imaginarium_forge.application.services.character_service import (
    CharacterService,
    CharacterVersionService,
)
from imaginarium_forge.application.services.eligibility_service import EligibilityService
from imaginarium_forge.application.services.project_service import ProjectService
from imaginarium_forge.application.services.prompt_studio_service import PromptProjectService
from imaginarium_forge.canonical import canonical_json
from imaginarium_forge.domain.canon.traits import CanonicalTrait
from imaginarium_forge.domain.character.gender import (
    CharacterGender,
    apply_gender_prompt_token,
)
from imaginarium_forge.domain.character.model import (
    AgeStatus,
    Character,
    CharacterProfile,
    derive_original_age_classification,
)
from imaginarium_forge.domain.character.presentation_cues import (
    PresentationCueResult,
    detect_presentation_cues,
)
from imaginarium_forge.domain.character.version import (
    AdultPresentation,
    CharacterVersion,
    VisualDNA,
)
from imaginarium_forge.domain.common.enums import (
    CanonStrength,
    CharacterOrigin,
    ContentIntensity,
    ContentRating,
    RequestType,
)
from imaginarium_forge.domain.common.ids import new_id, utc_now_iso
from imaginarium_forge.domain.creative.models import (
    CharacterBlueprint,
    CharacterLaunchResult,
    CreationMode,
    CreativeLaunchPreview,
    CreativeLaunchRequest,
    CreativeParticipantDraft,
    CreativeParticipantSource,
    ParticipantLaunchResult,
    ParticipantManifest,
    ParticipantPin,
    PromptDraftBundle,
    PromptLaunchResult,
    StoryFoundationResult,
    WorldFoundationResult,
)
from imaginarium_forge.domain.prompt.ast import (
    AstMetadata,
    CameraNode,
    EnvironmentNode,
    LightingNode,
    NegativeNode,
    PromptAST,
    SubjectNode,
    UserIntent,
)
from imaginarium_forge.domain.prompt.content_mode import derives_adult
from imaginarium_forge.domain.prompt.input_snapshot import (
    PromptVersionSelectionSnapshot,
    ResolutionInputSnapshot,
)
from imaginarium_forge.domain.story.models import (
    BibleCharacterEntry,
    NarrativeContract,
    OutlineAct,
    StoryBible,
    StoryOutline,
    StoryRequirement,
    StructureProfile,
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
from imaginarium_forge.infrastructure.db.repositories.story_repos import (
    EntityRecord,
    VersionedEntityRepository,
    VersionRecord,
)

_MODE_SUMMARIES = {
    CreationMode.SERIES_STORY: "系列故事：建立需求、世界觀聖經與長篇大綱草稿",
    CreationMode.CHARACTER_STORY: "角色＋背景故事：保存角色設定，並建立可延伸的故事基礎",
    CreationMode.CHARACTER_ONLY: "僅角色：保存角色設定、視覺風格與提示詞草稿",
    CreationMode.WORLD_ONLY: "僅世界觀：建立需求與世界觀聖經，不預設角色或故事結構",
}


@dataclass(frozen=True)
class _ResolvedParticipant:
    draft: CreativeParticipantDraft
    pin: ParticipantPin
    character: Character
    version: CharacterVersion


def _items(*values: str) -> tuple[str, ...]:
    return tuple(value.strip() for value in values if value.strip())


def _world_source_text(request: CreativeLaunchRequest) -> str:
    """Canonical human-readable source for a world-only Requirement version."""
    sections = (
        request.concept,
        request.setting,
        request.time_period,
        *request.world_rules,
        *request.locations,
        request.social_context,
        request.technology_or_magic,
    )
    return "\n".join(value.strip() for value in sections if value.strip())


def _content_intensity(request: CreativeLaunchRequest) -> ContentIntensity:
    if request.content_mode.value == "explicit_adult":
        return ContentIntensity.EXPLICIT
    if request.content_mode.value == "suggestive":
        return ContentIntensity.SUGGESTIVE
    if request.content_mode.value == "violent":
        return ContentIntensity.VIOLENT
    if request.content_mode.value == "horror":
        return ContentIntensity.HORROR
    if request.content_mode.value == "dark":
        return ContentIntensity.DARK
    return ContentIntensity.GENERAL


def _character_presentation_cues(
    request: CreativeLaunchRequest,
) -> PresentationCueResult:
    blueprint = request.character
    if blueprint is None:
        return detect_presentation_cues()
    return detect_presentation_cues(
        blueprint.identity,
        blueprint.face,
        blueprint.hair,
        blueprint.eyes,
        blueprint.body,
        blueprint.action,
        blueprint.expression,
        *blueprint.distinguishing_features,
    )


def _blueprint_presentation_cues(blueprint: CharacterBlueprint) -> PresentationCueResult:
    return detect_presentation_cues(
        blueprint.identity,
        blueprint.face,
        blueprint.hair,
        blueprint.eyes,
        blueprint.body,
        blueprint.action,
        blueprint.expression,
        *blueprint.distinguishing_features,
    )


def _build_new_character_aggregate(
    *,
    project_id: str,
    request_fingerprint: str,
    blueprint: CharacterBlueprint,
) -> tuple[Character, CharacterVersion]:
    """Build a complete unsaved original-character aggregate."""
    cues = _blueprint_presentation_cues(blueprint)
    profile = CharacterProfile(
        biography=blueprint.biography,
        personality_notes=blueprint.personality,
        voice_notes=blueprint.voice,
        motivation=blueprint.motivation,
        fear=blueprint.fear,
        secret=blueprint.secret,
        internal_conflict=blueprint.internal_conflict,
        relationship_hooks=blueprint.relationship_hooks,
        arc_start=blueprint.arc_start,
        arc_turning_points=blueprint.arc_turning_points,
        arc_end=blueprint.arc_end,
        freeform_notes=f"由創作起點建立｜藍圖 {request_fingerprint}",
    )
    traits = tuple(
        CanonicalTrait(
            category=category,
            name=category,
            canonical_descriptor=value,
            strength=(
                CanonStrength.HARD_LOCK
                if category in {"identity", "gender"}
                else CanonStrength.SOFT_CANON
            ),
            source="creative_launchpad",
        )
        for category, value in (
            ("gender", blueprint.gender.value if blueprint.gender is not None else ""),
            ("identity", blueprint.identity),
            ("face", blueprint.face),
            ("hair", blueprint.hair),
            ("eyes", blueprint.eyes),
            ("body", blueprint.body),
        )
        if value.strip()
    )
    visual_dna = VisualDNA(
        identity=blueprint.identity,
        gender=blueprint.gender,
        face=blueprint.face,
        hair=blueprint.hair,
        eyes=blueprint.eyes,
        body=blueprint.body,
        distinguishing_features=blueprint.distinguishing_features,
        prohibited_mutations=blueprint.prohibited_mutations,
        canonical_traits=traits,
    ).validated_for_base_version()
    presentation = AdultPresentation(
        adult_face_presentation=blueprint.adult_presentation_confirmed,
        adult_body_presentation=blueprint.adult_presentation_confirmed,
        is_minor_era_design=cues.minor_era,
        childlike_presentation_flags=cues.childlike_flags,
        selected_design_note=(
            "使用者於創作起點明確確認此角色版本為成人呈現"
            if blueprint.adult_presentation_confirmed
            else ""
        ),
    )
    now = utc_now_iso()
    character = Character(
        id=new_id(),
        project_id=project_id,
        name=blueprint.name,
        character_origin=CharacterOrigin.ORIGINAL,
        age_status=AgeStatus(
            classification=derive_original_age_classification(blueprint.explicit_age),
            explicit_age=blueprint.explicit_age,
            user_confirmed=blueprint.user_confirmed_age,
        ),
        profile=profile,
        created_at=now,
        updated_at=now,
    )
    version = CharacterVersion(
        id=new_id(),
        character_id=character.id,
        version_number=1,
        visual_dna=visual_dna,
        adult_presentation=presentation,
        voice_profile=blueprint.voice,
        personality_profile=blueprint.personality,
        change_note="創作起點建立的初始版本",
        created_at=now,
    )
    return character, version


def _all_positive_presentation_cues(
    request: CreativeLaunchRequest,
) -> PresentationCueResult:
    return detect_presentation_cues(*request.positive_texts)


def _assert_safe_adult_presentation(
    request: CreativeLaunchRequest,
) -> PresentationCueResult:
    cues = _all_positive_presentation_cues(request)
    if derives_adult(request.content_mode) and cues.has_conflict:
        raise ValidationFailedError("成人性內容不得包含未綁定的未成年期、未成年人物或孩童化呈現")
    return cues


def _validated_request(request: CreativeLaunchRequest) -> CreativeLaunchRequest:
    """Re-run Pydantic invariants at every public service trust boundary."""
    try:
        return CreativeLaunchRequest.model_validate(request.model_dump(mode="json"))
    except ValidationError as exc:
        first = exc.errors(include_url=False)[0]
        raise ValidationFailedError(str(first.get("msg", "創作藍圖驗證失敗"))) from exc


def _validated_manifest(manifest: ParticipantManifest) -> ParticipantManifest:
    """Re-run immutable roster invariants at every command boundary."""
    try:
        validated = ParticipantManifest.model_validate(manifest.model_dump(mode="json"))
        return ParticipantManifest(
            participants=tuple(
                sorted(
                    validated.participants,
                    key=lambda participant: not participant.is_primary,
                )
            )
        )
    except ValidationError as exc:
        first = exc.errors(include_url=False)[0]
        raise ValidationFailedError(str(first.get("msg", "participant manifest 驗證失敗"))) from exc


def _assert_p41_single_character_request(request: CreativeLaunchRequest) -> None:
    """Fail closed until the P4.2 participant application workflow is wired."""
    if request.participants:
        raise ValidationFailedError("P4.2 多人 application 尚未完成")


class CreativeLaunchService(ServiceBase):
    def __init__(
        self,
        *,
        session_factory: SessionProvider,
        projects: ProjectService,
        characters: CharacterService,
        versions: CharacterVersionService,
        eligibility: EligibilityService,
        prompt_projects: PromptProjectService,
    ) -> None:
        super().__init__(session_factory)
        self._projects = projects
        self._characters = characters
        self._versions = versions
        self._eligibility = eligibility
        self._prompt_projects = prompt_projects

    # -------------------------------------------------------------- preview
    def preview(self, request: CreativeLaunchRequest) -> CreativeLaunchPreview:
        request = _validated_request(request)
        self._projects.get_project(request.project_id)
        character = request.character
        character_summary = ""
        if character is not None:
            age = str(character.explicit_age) if character.explicit_age is not None else "未設定"
            character_summary = (
                f"{character.name}｜年齡 {age}｜{character.identity or '身分待補'}\n"
                f"個性：{character.personality or '待補'}\n"
                f"背景：{character.biography or '待補'}"
            )
        elif request.participants:
            summaries: list[str] = []
            for draft in request.participants:
                if draft.source is CreativeParticipantSource.NEW_BLUEPRINT:
                    blueprint = draft.blueprint
                    if blueprint is None:  # guarded by the domain contract
                        continue
                    age = (
                        str(blueprint.explicit_age)
                        if blueprint.explicit_age is not None
                        else "未設定"
                    )
                    summaries.append(
                        f"{blueprint.name}｜{draft.role or '角色'}｜年齡 {age}｜"
                        f"{'主要角色' if draft.is_primary else '參與角色'}"
                    )
                else:
                    existing, version = self._require_character_link(
                        request.project_id,
                        draft.character_id,
                        draft.character_version_id,
                    )
                    summaries.append(
                        f"{existing.name}｜{draft.role or '角色'}｜"
                        f"Canon v{version.version_number}｜"
                        f"{'主要角色' if draft.is_primary else '參與角色'}"
                    )
            character_summary = "\n".join(summaries)
        story_summary = (
            f"{request.title}\n類型：{request.genre_text}\n"
            f"概念：{request.concept or '（此路線不需要故事概念）'}\n"
            f"世界：{request.setting or '待補'}\n走向：{request.direction or '待補'}\n"
            f"人稱／時態：{request.pov.value}／{request.tense.value}"
        )
        if derives_adult(request.content_mode):
            bundle = PromptDraftBundle(
                status_message="成人提示詞會在角色保存並完成即時資格驗證後產生。"
            )
        else:
            bundle = self._build_prompt_bundle(request)
        return CreativeLaunchPreview(
            request_fingerprint=request.fingerprint,
            route_summary=_MODE_SUMMARIES[request.mode],
            story_summary=story_summary,
            character_summary=character_summary,
            prompt_bundle=bundle,
        )

    # ------------------------------------------------------------ character
    def save_character(self, request: CreativeLaunchRequest) -> CharacterLaunchResult:
        _assert_p41_single_character_request(request)
        request = _validated_request(request)
        _assert_p41_single_character_request(request)
        self._projects.get_project(request.project_id)
        blueprint = request.character
        if blueprint is None:
            raise ValidationFailedError("此創作藍圖沒有可儲存的角色")
        _assert_safe_adult_presentation(request)
        cues = _character_presentation_cues(request)

        # Construct every validated value before the first write.  Provider or
        # model output never gets to set either confirmation flag.
        profile = CharacterProfile(
            biography=blueprint.biography,
            personality_notes=blueprint.personality,
            voice_notes=blueprint.voice,
            motivation=blueprint.motivation,
            fear=blueprint.fear,
            secret=blueprint.secret,
            internal_conflict=blueprint.internal_conflict,
            relationship_hooks=blueprint.relationship_hooks,
            arc_start=blueprint.arc_start,
            arc_turning_points=blueprint.arc_turning_points,
            arc_end=blueprint.arc_end,
            freeform_notes=f"由創作起點建立｜藍圖 {request.fingerprint}",
        )
        traits = tuple(
            CanonicalTrait(
                category=category,
                name=category,
                canonical_descriptor=value,
                strength=(
                    CanonStrength.HARD_LOCK
                    if category in {"identity", "gender"}
                    else CanonStrength.SOFT_CANON
                ),
                source="creative_launchpad",
            )
            for category, value in (
                ("gender", blueprint.gender.value if blueprint.gender is not None else ""),
                ("identity", blueprint.identity),
                ("face", blueprint.face),
                ("hair", blueprint.hair),
                ("eyes", blueprint.eyes),
                ("body", blueprint.body),
            )
            if value.strip()
        )
        visual_dna = VisualDNA(
            identity=blueprint.identity,
            gender=blueprint.gender,
            face=blueprint.face,
            hair=blueprint.hair,
            eyes=blueprint.eyes,
            body=blueprint.body,
            distinguishing_features=blueprint.distinguishing_features,
            prohibited_mutations=blueprint.prohibited_mutations,
            canonical_traits=traits,
        ).validated_for_base_version()
        presentation = AdultPresentation(
            adult_face_presentation=blueprint.adult_presentation_confirmed,
            adult_body_presentation=blueprint.adult_presentation_confirmed,
            is_minor_era_design=cues.minor_era,
            childlike_presentation_flags=cues.childlike_flags,
            selected_design_note=(
                "使用者於創作起點明確確認此角色版本為成人呈現"
                if blueprint.adult_presentation_confirmed
                else ""
            ),
        )

        # Build the complete aggregate before the first write, then persist the
        # identity, immutable base version, and current-version pointer as one
        # command.  A failed version insert/flush therefore leaves no parent.
        now = utc_now_iso()
        character = Character(
            id=new_id(),
            project_id=request.project_id,
            name=blueprint.name,
            character_origin=CharacterOrigin.ORIGINAL,
            age_status=AgeStatus(
                classification=derive_original_age_classification(blueprint.explicit_age),
                explicit_age=blueprint.explicit_age,
                user_confirmed=blueprint.user_confirmed_age,
            ),
            profile=profile,
            created_at=now,
            updated_at=now,
        )
        version = CharacterVersion(
            id=new_id(),
            character_id=character.id,
            version_number=1,
            visual_dna=visual_dna,
            adult_presentation=presentation,
            voice_profile=blueprint.voice,
            personality_profile=blueprint.personality,
            change_note="創作起點建立的初始版本",
            created_at=now,
        )
        with self._transaction() as session:
            repo = CharacterRepository(session)
            repo.add(character)
            session.flush()
            repo.add_version(version)
            session.flush()
            pointer_updated = repo.update_identity_fields(
                character.id,
                current_version_id=version.id,
                updated_at=now,
            )
            if not pointer_updated:
                raise ValidationFailedError("角色初始版本無法設為目前使用版本")
            session.flush()

        evaluation_id = ""
        if derives_adult(request.content_mode):
            # Eligibility evaluations are append-only audit records owned by a
            # separate service transaction.  They intentionally happen only
            # after the Canon aggregate above has committed successfully.
            evaluation, evaluation_id = self._eligibility.evaluate_audited(
                character_id=character.id,
                version_id=version.id,
                request_type=(
                    RequestType.IMAGE_PROMPT_COMPILE
                    if request.mode is CreationMode.CHARACTER_ONLY
                    else RequestType.STORY_GENERATION
                ),
                content_rating=ContentRating.MATURE,
                content_intensity=_content_intensity(request),
                adult_content_requested=True,
            )
            if not evaluation.allowed:
                raise ValidationFailedError(evaluation.message)
        return CharacterLaunchResult(
            request_fingerprint=request.fingerprint,
            character_id=character.id,
            character_version_id=version.id,
            eligibility_evaluation_id=evaluation_id,
        )

    def save_participants(self, request: CreativeLaunchRequest) -> ParticipantLaunchResult:
        """Resolve a P4.2 roster into one exact, atomically saved manifest.

        Existing slots are checked before the first insert.  Every new
        Character, immutable v1, and current-version pointer then commits in
        the same transaction; a failure in any slot rolls back every new slot.
        No eligibility decision is made here — adult commands re-evaluate all
        exact pins live for their own request type.
        """
        request = _validated_request(request)
        self._projects.get_project(request.project_id)
        if not request.participants:
            raise ValidationFailedError(
                "save_participants 只接受 P4.2 participants 名單；"
                "P4.1 單角色請繼續使用 save_character"
            )
        _assert_safe_adult_presentation(request)

        prepared_new: dict[str, tuple[Character, CharacterVersion]] = {}
        for draft in request.participants:
            if draft.source is CreativeParticipantSource.NEW_BLUEPRINT:
                blueprint = draft.blueprint
                if blueprint is None:  # guarded by the domain contract
                    raise ValidationFailedError("新角色參與者缺少 CharacterBlueprint")
                prepared_new[draft.slot_id] = _build_new_character_aggregate(
                    project_id=request.project_id,
                    request_fingerprint=request.fingerprint,
                    blueprint=blueprint,
                )

        resolved: dict[str, tuple[Character, CharacterVersion]] = {}
        with self._transaction() as session:
            repo = CharacterRepository(session)
            # Foreign, missing, or mismatched existing pins are rejected before
            # any new character is inserted.
            for draft in request.participants:
                if draft.source is not CreativeParticipantSource.EXISTING_CANON:
                    continue
                character = repo.get(draft.character_id)
                version = repo.get_version(draft.character_version_id)
                if character is None or version is None:
                    raise ValidationFailedError("既有 Canon 角色或精確版本不存在")
                if character.project_id != request.project_id:
                    raise ValidationFailedError("不可跨專案使用參與角色")
                if version.character_id != character.id:
                    raise ValidationFailedError("參與角色版本不屬於指定角色")
                resolved[draft.slot_id] = (character, version)

            for draft in request.participants:
                if draft.source is not CreativeParticipantSource.NEW_BLUEPRINT:
                    continue
                character, version = prepared_new[draft.slot_id]
                repo.add(character)
                session.flush()
                repo.add_version(version)
                session.flush()
                pointer_updated = repo.update_identity_fields(
                    character.id,
                    current_version_id=version.id,
                    updated_at=character.updated_at,
                )
                if not pointer_updated:
                    raise ValidationFailedError("角色初始版本無法設為目前使用版本")
                session.flush()
                resolved[draft.slot_id] = (character, version)

        manifest = ParticipantManifest(
            participants=tuple(
                ParticipantPin(
                    slot_id=draft.slot_id,
                    character_id=resolved[draft.slot_id][0].id,
                    character_version_id=resolved[draft.slot_id][1].id,
                    role=draft.role,
                    is_primary=draft.is_primary,
                )
                for draft in sorted(request.participants, key=lambda item: not item.is_primary)
            )
        )
        return ParticipantLaunchResult(
            request_fingerprint=request.fingerprint,
            manifest=manifest,
        )

    # --------------------------------------------------------------- story
    def save_story_foundation(
        self,
        request: CreativeLaunchRequest,
        *,
        participant_manifest: ParticipantManifest | None = None,
        character_id: str = "",
        character_version_id: str = "",
    ) -> StoryFoundationResult:
        request = _validated_request(request)
        _assert_safe_adult_presentation(request)
        if request.mode in (CreationMode.CHARACTER_ONLY, CreationMode.WORLD_ONLY):
            raise ValidationFailedError("此創作路線不建立包含大綱的故事基礎")
        self._projects.get_project(request.project_id)
        if not request.concept.strip():
            raise ValidationFailedError("故事概念仍是空白；請自行填寫，或先讓本機模型補完藍圖")
        manifest, resolved_participants = self._resolve_command_participants(
            request,
            participant_manifest=participant_manifest,
            character_id=character_id,
            character_version_id=character_version_id,
            required=derives_adult(request.content_mode),
        )
        evaluation_ids: tuple[str, ...] = ()
        if derives_adult(request.content_mode):
            if manifest is None or not resolved_participants:
                raise ValidationFailedError("成人故事必須先保存並綁定通過資格的角色版本")
            allowed, results, ids = self._eligibility.evaluate_many_audited(
                participants=list(manifest.exact_pairs),
                request_type=RequestType.STORY_GENERATION,
                content_rating=ContentRating.MATURE,
                content_intensity=_content_intensity(request),
                adult_content_requested=True,
            )
            if not allowed:
                raise ValidationFailedError("；".join(result.message for result in results))
            evaluation_ids = tuple(ids)

        requirement_payload = self._build_requirement(request, participants=resolved_participants)
        bible_payload = self._build_bible(
            request,
            participants=resolved_participants,
            character_id=character_id,
            character_version_id=character_version_id,
        )
        outline_payload = self._build_outline(request)

        now = utc_now_iso()
        requirement = EntityRecord(
            id=new_id(),
            project_id=request.project_id,
            title=f"{request.title}｜故事需求",
            working_head_version_id=None,
            accepted_version_id=None,
            created_at=now,
            updated_at=now,
        )
        requirement_version = VersionRecord(
            id=new_id(),
            parent_id=requirement.id,
            project_id=request.project_id,
            version_number=1,
            change_note="創作起點建立的工作草稿",
            accepted=False,
            created_at=now,
            payload={
                "requirement_json": requirement_payload.model_dump_json(),
                "source_text": request.concept,
                "structured_mode": 1,
            },
        )
        bible = EntityRecord(
            id=new_id(),
            project_id=request.project_id,
            title=f"{request.title}｜故事聖經",
            working_head_version_id=None,
            accepted_version_id=None,
            created_at=now,
            updated_at=now,
        )
        bible_version = VersionRecord(
            id=new_id(),
            parent_id=bible.id,
            project_id=request.project_id,
            version_number=1,
            change_note="創作起點建立的工作草稿",
            accepted=False,
            created_at=now,
            payload={
                "bible_json": bible_payload.model_dump_json(),
                "requirement_version_id": requirement_version.id,
            },
        )
        outline = EntityRecord(
            id=new_id(),
            project_id=request.project_id,
            title=f"{request.title}｜故事大綱",
            working_head_version_id=None,
            accepted_version_id=None,
            created_at=now,
            updated_at=now,
        )
        outline_version = VersionRecord(
            id=new_id(),
            parent_id=outline.id,
            project_id=request.project_id,
            version_number=1,
            change_note="創作起點建立的工作草稿",
            accepted=False,
            created_at=now,
            payload={
                "outline_json": outline_payload.model_dump_json(),
                "structure_profile": outline_payload.structure_profile.value,
                "bible_version_id": bible_version.id,
            },
        )
        with self._transaction() as session:
            for kind, entity, version in (
                ("requirement", requirement, requirement_version),
                ("bible", bible, bible_version),
                ("outline", outline, outline_version),
            ):
                repo = VersionedEntityRepository(session, kind)
                repo.add_entity(entity)
                session.flush()
                repo.add_version(version)
                session.flush()
                if not repo.set_working_head(entity.id, version.id, now):
                    raise ValidationFailedError(f"{kind} 工作版本無法設為 working head")
        return StoryFoundationResult(
            request_fingerprint=request.fingerprint,
            requirement_id=requirement.id,
            requirement_version_id=requirement_version.id,
            bible_id=bible.id,
            bible_version_id=bible_version.id,
            outline_id=outline.id,
            outline_version_id=outline_version.id,
            eligibility_evaluation_ids=evaluation_ids,
        )

    def save_world_foundation(self, request: CreativeLaunchRequest) -> WorldFoundationResult:
        """Atomically persist a Requirement/Bible pair for a world-only brief.

        This command deliberately stops before Outline, Chapter, Scene, Draft,
        provider, eligibility, or media-prompt workflows.  Later story design
        may reference these exact working versions without pretending a plot or
        cast was already chosen.
        """
        request = _validated_request(request)
        if request.mode is not CreationMode.WORLD_ONLY:
            raise ValidationFailedError("save_world_foundation 只接受僅世界觀路線")
        if derives_adult(request.content_mode):
            raise ValidationFailedError("僅世界觀路線不可使用成人性內容模式")
        self._projects.get_project(request.project_id)
        source_text = _world_source_text(request)
        if not source_text:
            raise ValidationFailedError(
                "世界觀內容仍是空白；請至少填寫概念、時空背景、世界規則、地點、社會脈絡或技術／魔法"
            )

        requirement_payload = self._build_requirement(request)
        bible_payload = self._build_bible(
            request,
            character_id="",
            character_version_id="",
        )
        now = utc_now_iso()
        requirement = EntityRecord(
            id=new_id(),
            project_id=request.project_id,
            title=f"{request.title}｜世界觀需求",
            working_head_version_id=None,
            accepted_version_id=None,
            created_at=now,
            updated_at=now,
        )
        requirement_version = VersionRecord(
            id=new_id(),
            parent_id=requirement.id,
            project_id=request.project_id,
            version_number=1,
            change_note="僅世界觀起點建立的工作草稿",
            accepted=False,
            created_at=now,
            payload={
                "requirement_json": requirement_payload.model_dump_json(),
                "source_text": source_text,
                "structured_mode": 1,
            },
        )
        bible = EntityRecord(
            id=new_id(),
            project_id=request.project_id,
            title=f"{request.title}｜世界觀聖經",
            working_head_version_id=None,
            accepted_version_id=None,
            created_at=now,
            updated_at=now,
        )
        bible_version = VersionRecord(
            id=new_id(),
            parent_id=bible.id,
            project_id=request.project_id,
            version_number=1,
            change_note="僅世界觀起點建立的工作草稿",
            accepted=False,
            created_at=now,
            payload={
                "bible_json": bible_payload.model_dump_json(),
                "requirement_version_id": requirement_version.id,
            },
        )
        with self._transaction() as session:
            for kind, entity, version in (
                ("requirement", requirement, requirement_version),
                ("bible", bible, bible_version),
            ):
                repo = VersionedEntityRepository(session, kind)
                repo.add_entity(entity)
                session.flush()
                repo.add_version(version)
                session.flush()
                if not repo.set_working_head(entity.id, version.id, now):
                    raise ValidationFailedError(f"{kind} 工作版本無法設為 working head")

        return WorldFoundationResult(
            request_fingerprint=request.fingerprint,
            requirement_id=requirement.id,
            requirement_version_id=requirement_version.id,
            bible_id=bible.id,
            bible_version_id=bible_version.id,
        )

    # -------------------------------------------------------------- prompt
    def save_visual_prompt(
        self,
        request: CreativeLaunchRequest,
        *,
        participant_manifest: ParticipantManifest | None = None,
        character_id: str = "",
        character_version_id: str = "",
    ) -> PromptLaunchResult:
        request = _validated_request(request)
        _assert_safe_adult_presentation(request)
        self._projects.get_project(request.project_id)
        manifest, resolved_participants = self._resolve_command_participants(
            request,
            participant_manifest=participant_manifest,
            character_id=character_id,
            character_version_id=character_version_id,
            required=True,
        )
        if manifest is None:  # required=True guarantees this branch is unreachable
            raise ValidationFailedError("視覺提示必須綁定精確參與角色 manifest")
        adult = derives_adult(request.content_mode)
        allowed, evaluations, evaluation_ids = self._eligibility.evaluate_many_audited(
            participants=list(manifest.exact_pairs),
            request_type=RequestType.IMAGE_PROMPT_COMPILE,
            content_rating=ContentRating.MATURE if adult else ContentRating.GENERAL,
            content_intensity=_content_intensity(request),
            adult_content_requested=adult,
        )
        if not allowed:
            raise ValidationFailedError(
                "；".join(result.message for result in evaluations if not result.allowed)
            )

        bundle = self._build_prompt_bundle(request, participants=resolved_participants)
        prompt_source = self._bundle_source_text(bundle)
        ast = self._build_prompt_ast(
            request,
            participants=resolved_participants,
            source_text=prompt_source,
        )
        primary = manifest.primary
        now = utc_now_iso()
        project = PromptProjectRecord(
            id=str(uuid.uuid4()),
            project_id=request.project_id,
            title=f"{request.title}｜角色與場景視覺提示",
            source_text=prompt_source,
            character_id=primary.character_id,
            character_version_id=primary.character_version_id,
            style_profile_id=None,
            style_version_id=None,
            status="draft",
            current_version_id=None,
            created_at=now,
            updated_at=now,
        )
        snapshot = PromptVersionSelectionSnapshot(
            project_id=request.project_id,
            character_id=primary.character_id,
            character_version_id=primary.character_version_id,
            content_mode=request.content_mode,
            adult_content_requested=adult,
            participant_manifest_canonical_json=canonical_json(manifest.model_dump(mode="json")),
            participant_manifest_fingerprint=manifest.fingerprint,
            image_eligibility_evaluation_ids=tuple(evaluation_ids),
            source_text=ast.user_intent.source_text,
            created_at=now,
        )
        input_fingerprint = ResolutionInputSnapshot(
            prompt_ast_canonical_json=ast.canonical_dump(),
            character_id=primary.character_id,
            character_version_id=primary.character_version_id,
            content_mode=request.content_mode,
        ).fingerprint()
        version = PromptProjectVersionRecord(
            id=str(uuid.uuid4()),
            prompt_project_id=project.id,
            version_number=1,
            ast_json=ast.canonical_dump(),
            change_note="創作起點建立的提示詞工作草稿",
            accepted=False,
            created_at=now,
            selection_snapshot_json=snapshot.canonical(),
            selection_snapshot_sha256=snapshot.sha256(),
            input_fingerprint=input_fingerprint,
        )
        with self._transaction() as session:
            projects = PromptProjectRepository(session)
            versions = PromptProjectVersionRepository(session)
            projects.add(project)
            session.flush()
            versions.add(version)
            session.flush()
            pointer_updated = projects.update_fields(
                project.id,
                current_version_id=version.id,
                updated_at=now,
            )
            if not pointer_updated:
                raise ValidationFailedError("提示詞版本無法設為目前使用版本")
            session.flush()
        return PromptLaunchResult(
            request_fingerprint=request.fingerprint,
            prompt_project_id=project.id,
            prompt_version_id=version.id,
            eligibility_evaluation_id=(evaluation_ids[0] if evaluation_ids else ""),
            image_eligibility_evaluation_ids=tuple(evaluation_ids),
            prompt_bundle=bundle,
        )

    # ------------------------------------------------------------- builders
    @staticmethod
    def _build_requirement(
        request: CreativeLaunchRequest,
        *,
        participants: tuple[_ResolvedParticipant, ...] = (),
    ) -> StoryRequirement:
        character = request.character
        protagonist = ""
        if participants:
            protagonist = "\n".join(
                "；".join(
                    value
                    for value in (
                        participant.character.name,
                        participant.pin.role,
                        participant.character.profile.biography,
                        participant.character.profile.personality_notes,
                    )
                    if value.strip()
                )
                for participant in participants
            )
        elif character is not None:
            protagonist = "；".join(
                value
                for value in (
                    character.name,
                    character.biography,
                    character.personality,
                )
                if value.strip()
            )
        return StoryRequirement(
            concept=request.concept,
            logline=request.story_logline,
            synopsis=request.story_synopsis,
            opening_hook=request.story_opening_hook,
            genre=request.genre_text,
            target_length=request.target_length,
            tone=request.tone,
            audience=request.audience,
            setting=request.setting,
            time_period=request.time_period,
            protagonist_notes=protagonist,
            central_conflict=request.central_conflict or request.direction,
            themes=request.themes,
            must_include=request.must_include,
            must_avoid=request.must_avoid,
            pov=request.pov,
            tense=request.tense,
            pacing=request.pacing,
            prose_style_notes=request.prose_style_notes,
            dialogue_density=request.dialogue_density,
            violence_intensity=request.violence_intensity,
            horror_intensity=request.horror_intensity,
            intimacy_intensity=request.intimacy_intensity,
            ending_preference=request.ending_preference,
            content_mode=request.content_mode,
            inspiration_notes=request.direction,
        )

    @staticmethod
    def _build_bible(
        request: CreativeLaunchRequest,
        *,
        participants: tuple[_ResolvedParticipant, ...] = (),
        character_id: str,
        character_version_id: str,
    ) -> StoryBible:
        characters: tuple[BibleCharacterEntry, ...] = ()
        if participants:
            characters = tuple(
                BibleCharacterEntry(
                    name=participant.character.name,
                    role=(
                        participant.pin.role
                        or ("主角" if participant.pin.is_primary else "參與角色")
                    ),
                    canon_character_id=participant.pin.character_id,
                    canon_character_version_id=participant.pin.character_version_id,
                    biography=participant.character.profile.biography,
                    goal=request.direction,
                    motivation=participant.character.profile.motivation,
                    fear=participant.character.profile.fear,
                    secret=participant.character.profile.secret,
                    internal_conflict=(
                        participant.character.profile.internal_conflict
                    ),
                    voice_notes=(
                        participant.version.voice_profile
                        or participant.character.profile.voice_notes
                    ),
                    relationships=participant.character.profile.relationship_hooks,
                    arc_start=participant.character.profile.arc_start,
                    arc_turning_points=(
                        participant.character.profile.arc_turning_points
                    ),
                    arc_end=participant.character.profile.arc_end,
                )
                for participant in participants
            )
        elif request.character is not None:
            blueprint = request.character
            characters = (
                BibleCharacterEntry(
                    name=blueprint.name,
                    role="主角",
                    canon_character_id=character_id,
                    canon_character_version_id=character_version_id,
                    biography=blueprint.biography,
                    goal=request.direction,
                    motivation=blueprint.motivation,
                    fear=blueprint.fear,
                    secret=blueprint.secret,
                    internal_conflict=blueprint.internal_conflict,
                    voice_notes=blueprint.voice,
                    relationships=blueprint.relationship_hooks,
                    arc_start=blueprint.arc_start,
                    arc_turning_points=blueprint.arc_turning_points,
                    arc_end=blueprint.arc_end,
                ),
            )
        return StoryBible(
            title=request.title,
            logline=request.story_logline or request.central_conflict or request.concept,
            genre=request.genre_text,
            tone=request.tone,
            world_rules=request.world_rules,
            locations=request.locations,
            social_context=request.social_context,
            technology_or_magic=request.technology_or_magic,
            characters=characters,
            narrative_contract=NarrativeContract(
                tone_rules=_items(request.tone),
                pov_rules=_items(request.pov.value),
                style_rules=_items(request.prose_style_notes),
                forbidden_reveals=request.must_avoid,
            ),
            story_promise=request.direction,
            reader_experience_goal=request.audience,
        )

    @staticmethod
    def _build_outline(request: CreativeLaunchRequest) -> StoryOutline:
        three_acts = (
            OutlineAct(
                act_number=1,
                name="建立",
                purpose="建立世界、角色與故事承諾",
                goal=request.concept,
                turning_point=request.central_conflict,
            ),
            OutlineAct(
                act_number=2,
                name="推進",
                purpose="讓衝突升高並逼迫角色做出選擇",
                goal=request.direction,
                turning_point="主要選擇改變後續走向",
            ),
            OutlineAct(
                act_number=3,
                name="收束",
                purpose="兌現故事承諾並處理核心衝突",
                goal=request.ending_preference,
                turning_point="最終決定與代價",
            ),
        )
        acts: tuple[OutlineAct, ...]
        if request.structure_profile is StructureProfile.THREE_ACT:
            acts = three_acts
        elif request.structure_profile is StructureProfile.FOUR_ACT:
            acts = (
                three_acts[0],
                OutlineAct(
                    act_number=2,
                    name="發展",
                    purpose="擴大世界、關係與初步阻力",
                    goal=request.direction,
                    turning_point="中段事件改變角色理解",
                ),
                OutlineAct(
                    act_number=3,
                    name="危機",
                    purpose="讓代價與衝突升至最高點",
                    goal=request.central_conflict,
                    turning_point="角色做出不可逆選擇",
                ),
                three_acts[2].model_copy(update={"act_number": 4}),
            )
        else:
            # These profiles need their own reviewed beat templates.  Preserve
            # the selected profile without pretending a three-act draft fits.
            acts = ()
        return StoryOutline(
            structure_profile=request.structure_profile,
            acts=acts,
            arc_beats=_items(request.direction),
            ending_state=request.ending_preference,
        )

    @staticmethod
    def _build_prompt_bundle(
        request: CreativeLaunchRequest,
        *,
        participants: tuple[_ResolvedParticipant, ...] = (),
    ) -> PromptDraftBundle:
        specs: list[tuple[str, str, str, str, tuple[str, ...]]] = []
        single_gender: CharacterGender | None = None
        if participants:
            ordered = sorted(participants, key=lambda item: not item.pin.is_primary)
            if len(ordered) == 1:
                single_gender = ordered[0].version.visual_dna.gender
            for participant in ordered:
                dna = participant.version.visual_dna
                blueprint = participant.draft.blueprint
                identity = ", ".join(
                    value
                    for value in (
                        participant.character.name,
                        dna.identity,
                        dna.face,
                        dna.hair,
                        dna.eyes,
                        dna.body,
                        *dna.distinguishing_features,
                    )
                    if value.strip()
                )
                specs.append(
                    (
                        participant.character.name,
                        identity,
                        blueprint.action if blueprint is not None else "",
                        blueprint.expression if blueprint is not None else "",
                        dna.prohibited_mutations,
                    )
                )
        else:
            effective_drafts = request.effective_participant_drafts
            if len(effective_drafts) == 1 and effective_drafts[0].blueprint is not None:
                single_gender = effective_drafts[0].blueprint.gender
            for draft in effective_drafts:
                blueprint = draft.blueprint
                if blueprint is None:
                    # Preview remains honest about an existing exact Canon slot
                    # without pretending its mutable display label is identity.
                    label = draft.role or "既有 Canon 角色"
                    identity = f"{label}（精確版本 {draft.character_version_id}）"
                    specs.append((label, identity, "", "", ()))
                    continue
                identity = ", ".join(
                    value
                    for value in (
                        blueprint.name,
                        blueprint.identity,
                        blueprint.face,
                        blueprint.hair,
                        blueprint.eyes,
                        blueprint.body,
                        *blueprint.distinguishing_features,
                    )
                    if value.strip()
                )
                specs.append(
                    (
                        blueprint.name,
                        identity,
                        blueprint.action,
                        blueprint.expression,
                        blueprint.prohibited_mutations,
                    )
                )
        if not specs:
            return PromptDraftBundle(status_message="先加入角色，才能建立角色視覺提示詞。")

        identity = "; ".join(spec[1] for spec in specs if spec[1])
        actions = "; ".join(
            f"{name}: {action}"
            for name, _identity, action, _expression, _negative in specs
            if action
        )
        expressions = "; ".join(
            f"{name}: {expression}"
            for name, _identity, _action, expression, _negative in specs
            if expression
        )
        scene = ", ".join(
            value
            for value in (
                request.scene_location or request.setting,
                request.scene_time,
                request.scene_weather,
                request.scene_atmosphere,
                request.scene_lighting,
                request.scene_camera,
            )
            if value.strip()
        )
        if len(specs) == 1:
            _name, _identity, action, expression, _negative = specs[0]
            character_image = ", ".join(value for value in (identity, expression, action) if value)
            scene_image = ", ".join(
                value for value in (identity, action, scene, request.tone) if value
            )
            character_video = (
                f"主體：{identity}；動作：{action or '自然呼吸與細微表情'}；"
                f"鏡頭：{request.scene_camera or '穩定中景'}；時長："
                f"{request.video_duration_seconds} 秒；保持角色身分與外觀一致。"
            )
            scene_video = (
                f"場景：{scene or request.setting}；主體動作：{action or '依情境移動'}；"
                f"環境／鏡頭運動：{request.scene_motion or '細微環境動態，鏡頭穩定'}；"
                f"時長：{request.video_duration_seconds} 秒；首尾狀態連續、無外觀漂移。"
            )
        else:
            character_image = ", ".join(
                value for value in (identity, expressions, actions) if value
            )
            scene_image = ", ".join(
                value for value in (identity, actions, scene, request.tone) if value
            )
            character_video = (
                f"主體：{identity}；動作：{actions or '依角色關係自然互動'}；"
                f"鏡頭：{request.scene_camera or '穩定中景'}；時長："
                f"{request.video_duration_seconds} 秒；"
                "保持每位角色身分、外觀與空間關係一致。"
            )
            scene_video = (
                f"場景：{scene or request.setting}；主體動作：{actions or '依情境移動'}；"
                f"環境／鏡頭運動：{request.scene_motion or '細微環境動態，鏡頭穩定'}；"
                f"時長：{request.video_duration_seconds} 秒；"
                "首尾狀態連續、無角色互換或外觀漂移。"
            )
        if derives_adult(request.content_mode):
            # IMAGE save remains IMAGE-only.  Formal adult video prompts are
            # produced only by the separately audited Video bundle workflow.
            character_video = ""
            scene_video = ""
        negatives = ", ".join(
            dict.fromkeys(
                (
                    *(item for spec in specs for item in spec[4]),
                    *request.must_avoid,
                    "identity drift",
                    "inconsistent face",
                    "extra limbs",
                    "text watermark",
                )
            )
        )
        english_keywords = list(request.english_character_keywords)
        if single_gender is not None:
            english_keywords = list(
                apply_gender_prompt_token(english_keywords, single_gender)
            )
        return PromptDraftBundle(
            character_image_prompt=character_image,
            character_prompt_en=", ".join(english_keywords),
            character_video_prompt=character_video,
            scene_image_prompt=scene_image,
            scene_video_prompt=scene_video,
            negative_prompt=negatives,
            status_message=(
                "成人影片預覽不在圖片保存流程產生；請另建立正式影片 Prompt 套件，"
                "並重新檢查所有角色的內容資格。"
                if derives_adult(request.content_mode)
                else "影片文字僅供預覽；正式影片提示請另建立有修改歷史的影片 Prompt 套件。"
                "目前不會呼叫或啟動 ComfyUI。"
            ),
        )

    @staticmethod
    def _build_prompt_ast(
        request: CreativeLaunchRequest,
        *,
        participants: tuple[_ResolvedParticipant, ...],
        source_text: str,
    ) -> PromptAST:
        if not participants:
            raise ValidationFailedError("缺少角色設定，無法建立提示詞 AST")
        # ``save_participants`` and ``_resolve_command_participants`` already
        # establish the canonical primary-first manifest order.  Preserve it
        # byte-for-byte so PromptAST and the exact manifest cannot diverge.
        ordered = participants
        subjects = tuple(
            SubjectNode(
                subject_id=participant.pin.slot_id,
                character_id=participant.pin.character_id,
                character_version_id=participant.pin.character_version_id,
                origin=participant.character.character_origin.value,
                presentation=(
                    "adult"
                    if participant.version.adult_presentation.adult_face_presentation
                    and participant.version.adult_presentation.adult_body_presentation
                    else ""
                ),
                canonical_features=tuple(
                    value
                    for value in (
                        (
                            participant.version.visual_dna.gender.prompt_token
                            if participant.version.visual_dna.gender is not None
                            else ""
                        ),
                        participant.version.visual_dna.identity,
                        participant.version.visual_dna.face,
                        participant.version.visual_dna.hair,
                        participant.version.visual_dna.eyes,
                        participant.version.visual_dna.body,
                        *participant.version.visual_dna.distinguishing_features,
                    )
                    if value.strip()
                ),
                pose=(
                    participant.draft.blueprint.action
                    if participant.draft.blueprint is not None
                    else ""
                ),
                expression=(
                    participant.draft.blueprint.expression
                    if participant.draft.blueprint is not None
                    else ""
                ),
                motion_cues=_items(request.scene_motion),
                preserve_identity=True,
            )
            for participant in ordered
        )
        prohibited_mutations = tuple(
            dict.fromkeys(
                mutation
                for participant in ordered
                for mutation in participant.version.visual_dna.prohibited_mutations
            )
        )
        return PromptAST(
            subjects=subjects,
            camera=CameraNode(shot=request.scene_camera),
            environment=EnvironmentNode(
                location=request.scene_location or request.setting,
                time_of_day=request.scene_time,
                weather=request.scene_weather,
                atmosphere=request.scene_atmosphere,
            ),
            lighting=LightingNode(key=request.scene_lighting),
            negative=NegativeNode(
                semantic=request.must_avoid,
                anatomy=("extra limbs",),
                identity=prohibited_mutations,
                composition=("text watermark",),
            ),
            user_intent=UserIntent(
                source_language="zh-TW",
                source_text=source_text,
                must_include=request.must_include,
                must_avoid=request.must_avoid,
            ),
            metadata=AstMetadata(
                parser_provider="creative_launchpad",
                parser_model="deterministic",
                created_at=utc_now_iso(),
                parser_contract_version="phase4-creative-launch-v2",
                schema_version="phase4-creative-launch-v2",
                structured_mode=True,
            ),
        )

    @staticmethod
    def _bundle_source_text(bundle: PromptDraftBundle) -> str:
        """Persist every text deliverable in the existing prompt project."""
        sections = (
            ("English character prompt", bundle.character_prompt_en),
            ("角色圖片提示", bundle.character_image_prompt),
            ("場景圖片提示", bundle.scene_image_prompt),
            ("角色影片動態指引", bundle.character_video_prompt),
            ("場景影片動態指引", bundle.scene_video_prompt),
            ("負向提示", bundle.negative_prompt),
        )
        return "\n\n".join(f"## {label}\n{text}" for label, text in sections if text.strip())

    def _require_character_link(
        self, project_id: str, character_id: str, character_version_id: str
    ) -> tuple[Character, CharacterVersion]:
        if not character_id or not character_version_id:
            raise ValidationFailedError("角色與角色版本必須成對提供")
        character = self._characters.get_character(character_id)
        version = self._versions.get_version(character_version_id)
        if character.project_id != project_id:
            raise ValidationFailedError("不可跨專案使用角色")
        if version.character_id != character.id:
            raise ValidationFailedError("角色版本不屬於指定角色")
        return character, version

    def _resolve_command_participants(
        self,
        request: CreativeLaunchRequest,
        *,
        participant_manifest: ParticipantManifest | None,
        character_id: str,
        character_version_id: str,
        required: bool,
    ) -> tuple[ParticipantManifest | None, tuple[_ResolvedParticipant, ...]]:
        """Match an exact saved manifest to every author roster slot."""
        has_legacy_pair = bool(character_id or character_version_id)
        if bool(character_id) != bool(character_version_id):
            raise ValidationFailedError("角色與角色版本必須成對提供")
        if participant_manifest is not None and has_legacy_pair:
            raise ValidationFailedError("participant_manifest 與 P4.1 角色參數不可同時提供")
        if participant_manifest is not None:
            participant_manifest = _validated_manifest(participant_manifest)
        if request.participants and participant_manifest is None:
            raise ValidationFailedError("P4.2 多人命令必須提供 save_participants 產生的 manifest")
        if participant_manifest is None and has_legacy_pair:
            participant_manifest = ParticipantManifest(
                participants=(
                    ParticipantPin(
                        slot_id="legacy-primary",
                        character_id=character_id,
                        character_version_id=character_version_id,
                        is_primary=True,
                    ),
                )
            )
        if participant_manifest is None:
            if required:
                raise ValidationFailedError("此命令必須綁定至少一個精確角色版本")
            return None, ()

        drafts = request.effective_participant_drafts
        if not drafts:
            raise ValidationFailedError("沒有角色草稿可與 participant manifest 對應")
        draft_by_slot = {draft.slot_id: draft for draft in drafts}
        manifest_slots = {participant.slot_id for participant in participant_manifest.participants}
        if set(draft_by_slot) != manifest_slots:
            raise ValidationFailedError("participant manifest 的 slots 與創作藍圖不一致")

        resolved: list[_ResolvedParticipant] = []
        for pin in participant_manifest.participants:
            draft = draft_by_slot[pin.slot_id]
            if pin.role != draft.role or pin.is_primary != draft.is_primary:
                raise ValidationFailedError(
                    f"participant manifest slot {draft.slot_id} 的 role/primary 不一致"
                )
            if draft.source is CreativeParticipantSource.EXISTING_CANON and (
                pin.character_id != draft.character_id
                or pin.character_version_id != draft.character_version_id
            ):
                raise ValidationFailedError(
                    f"participant manifest slot {draft.slot_id} 未綁定草稿指定的精確 Canon"
                )
            character, version = self._require_character_link(
                request.project_id,
                pin.character_id,
                pin.character_version_id,
            )
            if draft.source is CreativeParticipantSource.NEW_BLUEPRINT:
                blueprint = draft.blueprint
                if blueprint is None:  # guarded by the domain contract
                    raise ValidationFailedError("新角色參與者缺少 CharacterBlueprint")
                self._assert_character_matches_blueprint(
                    blueprint=blueprint,
                    character=character,
                    version=version,
                )
            resolved.append(
                _ResolvedParticipant(
                    draft=draft,
                    pin=pin,
                    character=character,
                    version=version,
                )
            )
        return participant_manifest, tuple(resolved)

    @staticmethod
    def _assert_character_matches_blueprint(
        *,
        blueprint: CharacterBlueprint,
        character: Character,
        version: CharacterVersion,
    ) -> None:
        """Prove a new blueprint is the source of this exact Canon version."""
        cues = _blueprint_presentation_cues(blueprint)
        expected_presentation = AdultPresentation(
            adult_face_presentation=blueprint.adult_presentation_confirmed,
            adult_body_presentation=blueprint.adult_presentation_confirmed,
            is_minor_era_design=cues.minor_era,
            childlike_presentation_flags=cues.childlike_flags,
            selected_design_note=(
                "使用者於創作起點明確確認此角色版本為成人呈現"
                if blueprint.adult_presentation_confirmed
                else ""
            ),
        )
        expected_age_classification = derive_original_age_classification(blueprint.explicit_age)
        dna = version.visual_dna
        checks = (
            ("角色來源", character.character_origin is CharacterOrigin.ORIGINAL),
            ("名稱", character.name == blueprint.name),
            ("明確年齡", character.age_status.explicit_age == blueprint.explicit_age),
            (
                "年齡分類",
                character.age_status.classification is expected_age_classification,
            ),
            (
                "年齡確認",
                character.age_status.user_confirmed == blueprint.user_confirmed_age,
            ),
            ("背景", character.profile.biography == blueprint.biography),
            (
                "角色個性",
                character.profile.personality_notes == blueprint.personality,
            ),
            ("角色聲線", character.profile.voice_notes == blueprint.voice),
            ("角色動機", character.profile.motivation == blueprint.motivation),
            ("角色恐懼", character.profile.fear == blueprint.fear),
            ("角色祕密", character.profile.secret == blueprint.secret),
            (
                "內在衝突",
                character.profile.internal_conflict == blueprint.internal_conflict,
            ),
            (
                "人際鉤子",
                character.profile.relationship_hooks == blueprint.relationship_hooks,
            ),
            ("角色弧起點", character.profile.arc_start == blueprint.arc_start),
            (
                "角色弧轉折",
                character.profile.arc_turning_points == blueprint.arc_turning_points,
            ),
            ("角色弧終點", character.profile.arc_end == blueprint.arc_end),
            ("VisualDNA.gender", dna.gender == blueprint.gender),
            ("VisualDNA.identity", dna.identity == blueprint.identity),
            ("VisualDNA.face", dna.face == blueprint.face),
            ("VisualDNA.hair", dna.hair == blueprint.hair),
            ("VisualDNA.eyes", dna.eyes == blueprint.eyes),
            ("VisualDNA.body", dna.body == blueprint.body),
            (
                "VisualDNA.distinguishing_features",
                dna.distinguishing_features == blueprint.distinguishing_features,
            ),
            (
                "VisualDNA.prohibited_mutations",
                dna.prohibited_mutations == blueprint.prohibited_mutations,
            ),
            ("版本聲線", version.voice_profile == blueprint.voice),
            ("版本個性", version.personality_profile == blueprint.personality),
            ("成人呈現", version.adult_presentation == expected_presentation),
        )
        mismatches = [label for label, matches in checks if not matches]
        if mismatches:
            raise ValidationFailedError(
                "創作藍圖與指定 Canon 角色版本不一致：" + "、".join(mismatches)
            )

    def _assert_canonical_character_matches_request(
        self,
        request: CreativeLaunchRequest,
        *,
        character_id: str,
        character_version_id: str,
    ) -> None:
        """Prove the singular blueprint belongs to the supplied exact Canon.

        Action, expression, and scene direction are deliberately excluded:
        they are per-output intent rather than immutable character Canon.
        """
        blueprint = request.character
        if blueprint is None:
            raise ValidationFailedError("指定 Canon 角色時必須提供 P4.1 單角色藍圖")
        character, version = self._require_character_link(
            request.project_id, character_id, character_version_id
        )
        self._assert_character_matches_blueprint(
            blueprint=blueprint,
            character=character,
            version=version,
        )
