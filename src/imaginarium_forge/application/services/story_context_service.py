"""Story context service (Gate A, A3-05 §11).

The original defect: the Story Studio page called the pure ``build_context``
helper with a Scene Card and one entity ID, so the context that actually
reached the model contained no Hard Canon, no character voice notes, no
Story Requirement, no Story Bible contract, no Chapter Plan, and no previous
scene summary — while the UI implied otherwise. Worse, it stored an Outline
*entity* ID in a field named ``outline_version_id``.

This service loads every record itself from persisted data. Callers name a
scene and (optionally) which planning versions to use; they cannot inject
content that was never saved.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from imaginarium_forge.application.errors import (
    ApplicationError,
    NotFoundError,
    ValidationFailedError,
)
from imaginarium_forge.application.services.adult_output_review_service import (
    build_adult_story_provider_contract,
)
from imaginarium_forge.application.services.base import ServiceBase
from imaginarium_forge.application.services.planning_chain import (
    PlanningChainResolver,
    PlanningSelection,
)
from imaginarium_forge.application.services.scene_generation_service import (
    RevisionRequest,
    build_revision_contract,
)
from imaginarium_forge.application.services.story_memory_service import (
    resolve_story_memory_projection,
)
from imaginarium_forge.canonical import canonical_json
from imaginarium_forge.domain.canon.traits import CanonicalTrait
from imaginarium_forge.domain.common.enums import CanonStrength
from imaginarium_forge.domain.prompt.content_mode import derives_adult
from imaginarium_forge.domain.story.context import (
    CharacterContribution,
    ContextBudgetSnapshot,
    ContextPackage,
    ContextSourceRef,
    SourceEntityType,
    VersionReference,
    build_context,
)
from imaginarium_forge.domain.story.generation_input import (
    GENERATION_SNAPSHOT_SCHEMA_VERSION,
    GenerationInputSnapshot,
    GenerationOptionsSnapshot,
    PlanningMode,
)
from imaginarium_forge.domain.story.memory import StoryMemoryProjection
from imaginarium_forge.domain.story.models import (
    ChapterPlan,
    SceneCard,
    StoryBible,
    StoryOutline,
    StoryRequirement,
)
from imaginarium_forge.domain.story.output_envelope import (
    ADULT_STORY_OUTPUT_ENVELOPE_VERSION,
)
from imaginarium_forge.domain.story.short_story import (
    CompleteStoryBrief,
    StoryGenerationPurpose,
)
from imaginarium_forge.infrastructure.db.repositories.characters import (
    CharacterRepository,
)
from imaginarium_forge.infrastructure.db.repositories.story_repos import (
    AcceptedSummaryProjectionRepository,
    GenerationRunRecord,
    GenerationRunRepository,
    ParticipantRecord,
    SceneCardParticipantRepository,
    SceneCardVersionRepository,
    SceneDraftRepository,
    SceneDraftSummaryRepository,
    StoryChapterRepository,
    StorySceneRepository,
    VersionedEntityRepository,
)

_ModelT = TypeVar("_ModelT", bound=BaseModel)


@dataclass(frozen=True, slots=True)
class BuildStoryContextRequest:
    """What the caller may choose — and nothing more.

    Planning versions default to each entity's ACCEPTED version (A3-07).
    Naming an unaccepted version is allowed but must be explicit, and the
    resulting run records which versions were actually used.
    """

    scene_id: str
    scene_card_version_id: str | None = None
    requirement_version_id: str | None = None
    bible_version_id: str | None = None
    outline_version_id: str | None = None
    chapter_plan_version_id: str | None = None
    user_instruction: str = ""
    style_examples: tuple[str, ...] = ()
    include_previous_summary: bool = True
    max_chars: int | None = None
    eligibility_fingerprint: str = ""
    #: A3-R12: replaces the former ``allow_unaccepted_planning`` boolean, which
    #: was declared here and never read by anything. ``accepted`` requires every
    #: planning link to be an accepted version; ``preview`` permits explicitly
    #: selected drafts but still enforces the full chain, ownership and
    #: eligibility, and marks the resulting run.
    planning_mode: PlanningMode = PlanningMode.ACCEPTED
    budget_snapshot: ContextBudgetSnapshot | None = None
    #: Internal historical-reconstruction pins. ``None`` follows current
    #: accepted-draft pointers; a tuple (including empty) replays only these
    #: immutable proposals and the exact recorded diagnostics.
    memory_proposal_ids: tuple[str, ...] | None = None
    memory_gap_scene_ids: tuple[str, ...] = ()
    stale_memory_proposal_ids: tuple[str, ...] = ()
    expected_memory_fingerprint: str = ""
    #: Internal historical-reconstruction pins. ``None`` follows the current
    #: accepted-draft pointer for legacy snapshots; an empty tuple proves that
    #: no previous summary was used; the non-empty form must be the exact
    #: SCENE_DRAFT_SUMMARY + SCENE_DRAFT pair saved in typed source refs.
    previous_summary_source_refs: tuple[ContextSourceRef, ...] | None = None
    # Appended after every pre-R1 field for positional caller compatibility.
    generation_purpose: StoryGenerationPurpose = StoryGenerationPurpose.SCENE
    complete_story_brief: CompleteStoryBrief | None = None
    structured_must_avoid: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ResolvedStoryContext:
    """The context plus the exact IDs it was built from (A3-01 needs these)."""

    package: ContextPackage
    project_id: str
    scene_id: str
    scene_card: SceneCard
    requirement: StoryRequirement | None
    bible: StoryBible | None
    outline: StoryOutline | None
    memory_projection: StoryMemoryProjection
    scene_card_version_id: str
    participants: tuple[ParticipantRecord, ...]
    requirement_version_id: str | None
    bible_version_id: str | None
    outline_version_id: str | None
    chapter_plan_version_id: str | None
    pov_character_version_id: str | None
    planning_mode: PlanningMode
    planning_chain_fingerprint: str
    complete_story_brief: CompleteStoryBrief | None = None


class HistoricalContextDisplayMode(StrEnum):
    """A3-R10 §10.4: how much the app may honestly claim about a run's text.

    The first three are the specified normal outcomes. ``STORED_INTEGRITY_ERROR``
    is not a fourth way to show history — it exists so that stored text which
    disagrees with its own stored hash can never be labelled exact.
    """

    STORED_EXACT = "stored_exact"
    RECONSTRUCTED_MATCH = "reconstructed_match"
    RECONSTRUCTION_MISMATCH = "reconstruction_mismatch"
    STORED_INTEGRITY_ERROR = "stored_integrity_error"


@dataclass(frozen=True, slots=True)
class HistoricalContextInspection:
    """A3-R10 §10.3/§10.5: everything the inspector may display for one run.

    Every field comes from the run's own persisted row or from data reachable
    through the exact IDs that row pinned. Nothing here is read from the
    current UI selection, the current accepted pointers, or ``versions[-1]``.
    """

    run_id: str
    scene_id: str
    run_kind: str
    status: str
    planning_mode: str
    content_mode: str
    provider: str
    model: str
    renderer_version: str
    context_schema_version: str
    context_contract_version: str
    context_budget_policy_version: str
    context_budget_snapshot: dict[str, Any]
    context_fingerprint: str
    planning_chain_fingerprint: str
    eligibility_fingerprint: str
    system_message_sha256: str
    user_message_sha256: str
    system_message_byte_size: int
    user_message_byte_size: int
    rendered_message_storage_enabled: bool
    display_mode: HistoricalContextDisplayMode
    system_message: str
    user_message: str
    reconstruction_matches: bool | None
    version_references: tuple[VersionReference, ...]
    character_version_ids: tuple[str, ...]
    pov_character_version_id: str
    generation_options_snapshot: dict[str, Any] | None
    generation_options_raw_json: str
    input_snapshot_sha256_verified: bool
    #: A3-S8.1 §17: the typed sources the run itself recorded. Never
    #: reconstructed from the CURRENT database or current display names.
    context_source_refs: tuple[ContextSourceRef, ...]
    context_source_refs_verified: bool
    stored_message_hashes_verified: bool
    started_at: str
    completed_at: str
    #: Only ever shown under an explicit "not the historical original" label.
    diagnostic_system_message: str
    diagnostic_user_message: str
    warnings: tuple[str, ...]

    @property
    def is_exact_history(self) -> bool:
        """True only when the displayed text IS the stored provider text."""
        return self.display_mode is HistoricalContextDisplayMode.STORED_EXACT


class StoryContextService(ServiceBase):
    def build_for_scene(
        self, request: BuildStoryContextRequest
    ) -> ResolvedStoryContext:
        with self._session_factory() as session:
            scenes = StorySceneRepository(session)
            scene = scenes.get(request.scene_id)
            if scene is None:
                raise NotFoundError(f"找不到場景：{request.scene_id}")

            chapters = StoryChapterRepository(session)
            chapter = chapters.get(scene.story_chapter_id)
            if chapter is None:
                raise NotFoundError(f"找不到章節：{scene.story_chapter_id}")
            project_id = scene.project_id

            # ---- planning chain FIRST (A3-R02 / A3-R12) -------------------
            # Every downstream lookup derives from the resolved chain. The card
            # must not be resolved separately: doing so would select a version
            # the chain policy rejects, then load participants from it.
            chain = PlanningChainResolver(session).resolve(
                PlanningSelection(
                    scene_id=request.scene_id,
                    scene_card_version_id=request.scene_card_version_id,
                    chapter_plan_version_id=request.chapter_plan_version_id,
                    outline_version_id=request.outline_version_id,
                    bible_version_id=request.bible_version_id,
                    requirement_version_id=request.requirement_version_id,
                    planning_mode=request.planning_mode,
                )
            )
            card_version_id = chain.scene_card_version_id
            card_record = SceneCardVersionRepository(session).get(card_version_id)
            if card_record is None:
                raise NotFoundError(f"找不到 Scene Card 版本：{card_version_id}")
            card = SceneCard.model_validate_json(str(card_record.payload["card_json"]))

            # ---- participants: the EXACT pinned versions (A3-03) -----------
            participants = tuple(
                SceneCardParticipantRepository(session).list_for_card_version(
                    card_version_id
                )
            )
            char_repo = CharacterRepository(session)
            # A3-S8.1: contributions carry the character AND version IDs, so
            # a block source never has to be reconstructed from a name.
            character_locks: list[CharacterContribution] = []
            voice_notes: list[CharacterContribution] = []
            character_version_ids: list[str] = []
            pov_version_id: str | None = None
            for participant in participants:
                character = char_repo.get(participant.character_id)
                version = char_repo.get_version(participant.character_version_id)
                if character is None or version is None:
                    raise NotFoundError(
                        f"角色或版本已不存在：{participant.character_id}"
                    )
                character_version_ids.append(version.id)
                if participant.is_pov:
                    pov_version_id = version.id
                locks = _hard_locks(version.visual_dna.canonical_traits)
                if locks:
                    character_locks.append(
                        CharacterContribution(
                            character_id=character.id,
                            character_version_id=version.id,
                            name=character.name,
                            text=locks,
                        )
                    )
                notes = "；".join(
                    part
                    for part in (version.voice_profile, version.personality_profile)
                    if part
                )
                if notes:
                    voice_notes.append(
                        CharacterContribution(
                            character_id=character.id,
                            character_version_id=version.id,
                            name=character.name,
                            text=notes,
                        )
                    )

            # ---- parse the already-validated planning versions -----------
            requirement = self._load_planning(
                session,
                kind="requirement",
                version_id=chain.requirement_version_id,
                model=StoryRequirement,
                payload_key="requirement_json",
            )
            bible = self._load_planning(
                session,
                kind="bible",
                version_id=chain.bible_version_id,
                model=StoryBible,
                payload_key="bible_json",
            )
            if bible is not None:
                # Treat persisted/imported planning JSON as untrusted at the
                # provider boundary.  New writes are validated by
                # StoryBibleService, but legacy rows or out-of-band database
                # changes must not turn ghost/cross-project IDs into typed
                # provenance merely because they predate that write guard.
                for entry in bible.characters:
                    character_id = entry.canon_character_id.strip()
                    canon_version_id = entry.canon_character_version_id.strip()
                    if not character_id and not canon_version_id:
                        continue
                    character = char_repo.get(character_id)
                    version = char_repo.get_version(canon_version_id)
                    if character is None or version is None:
                        raise ValidationFailedError(
                            f"故事聖經角色「{entry.name}」的 Canon 來源已不存在"
                        )
                    if character.project_id != project_id:
                        raise ValidationFailedError(
                            f"故事聖經角色「{entry.name}」引用了其他專案的 Canon 來源"
                        )
                    if version.character_id != character.id:
                        raise ValidationFailedError(
                            f"故事聖經角色「{entry.name}」的 Canon 版本不屬於指定角色"
                        )
            outline = self._load_planning(
                session,
                kind="outline",
                version_id=chain.outline_version_id,
                model=StoryOutline,
                payload_key="outline_json",
            )
            plan = self._load_planning(
                session,
                kind="chapter_plan",
                version_id=chain.chapter_plan_version_id,
                model=ChapterPlan,
                payload_key="plan_json",
            )

            # ---- previous ACCEPTED scene summary ---------------------------
            # A3-R05: a summary is established history ONLY when the scene
            # actually accepted a draft AND the summary belongs to that draft.
            # The previous rule used any free-floating scene note, so a scene
            # with no accepted draft leaked its working note forward as fact.
            previous_summary = ""
            previous_summary_record_id = ""
            previous_summary_draft_id = ""
            if request.include_previous_summary:
                summaries = SceneDraftSummaryRepository(session)
                drafts = SceneDraftRepository(session)
                ordered_scenes = AcceptedSummaryProjectionRepository(
                    session
                ).list_for_outline(chapter.story_outline_id)
                ordered_scene_ids = [item.scene_id for item in ordered_scenes]
                if len(ordered_scene_ids) != len(set(ordered_scene_ids)):
                    raise ValidationFailedError(
                        "故事大綱的確定性場景順序包含重複場景"
                    )
                target_indices = [
                    index
                    for index, ordered_scene in enumerate(ordered_scenes)
                    if ordered_scene.scene_id == scene.id
                ]
                if not target_indices:
                    raise ValidationFailedError(
                        "目前場景不在所屬故事大綱的確定性場景順序中"
                    )
                if len(target_indices) != 1:
                    raise ValidationFailedError(
                        "目前場景在所屬故事大綱的確定性場景順序中重複出現"
                    )
                target_index = target_indices[0]
                target = ordered_scenes[target_index]
                if (
                    target.scene_id != scene.id
                    or target.story_chapter_id != chapter.id
                    or target.scene_project_id != project_id
                    or target.chapter_project_id != project_id
                    or target.story_outline_id != chapter.story_outline_id
                ):
                    raise ValidationFailedError(
                        "目前場景的專案、章節或故事大綱歸屬與場景順序不一致"
                    )

                pinned_refs = request.previous_summary_source_refs
                if pinned_refs is not None:
                    # Historical v8 replay follows the run's immutable typed
                    # refs, not today's accepted pointer.  Empty means the run
                    # had no previous-summary block.  The non-empty form is a
                    # closed pair so a partial/ambiguous pin fails closed.
                    summary_refs = tuple(
                        ref
                        for ref in pinned_refs
                        if ref.entity_type is SourceEntityType.SCENE_DRAFT_SUMMARY
                    )
                    draft_refs = tuple(
                        ref
                        for ref in pinned_refs
                        if ref.entity_type is SourceEntityType.SCENE_DRAFT
                    )
                    if pinned_refs and (
                        len(pinned_refs) != 2
                        or len(summary_refs) != 1
                        or len(draft_refs) != 1
                    ):
                        raise ValidationFailedError(
                            "歷史前情來源必須是唯一的摘要紀錄與場景草稿配對"
                        )
                    if pinned_refs:
                        summary_ref = summary_refs[0]
                        draft_ref = draft_refs[0]
                        if (
                            not summary_ref.entity_id.strip()
                            or not draft_ref.entity_id.strip()
                        ):
                            raise ValidationFailedError(
                                "歷史前情來源的摘要紀錄或場景草稿識別碼不可空白"
                            )
                        record = summaries.get(summary_ref.entity_id)
                        draft = drafts.get(draft_ref.entity_id)
                        if record is None or draft is None:
                            raise ValidationFailedError(
                                "歷史前情來源的摘要紀錄或場景草稿已不存在"
                            )
                        source_index = next(
                            (
                                index
                                for index, ordered_scene in enumerate(ordered_scenes)
                                if ordered_scene.scene_id == draft.story_scene_id
                            ),
                            None,
                        )
                        if source_index is None or source_index >= target_index:
                            raise ValidationFailedError(
                                "歷史前情來源不在目標場景之前的故事大綱順序中"
                            )
                        source_scene = ordered_scenes[source_index]
                        if (
                            source_scene.scene_project_id != project_id
                            or source_scene.chapter_project_id != project_id
                            or source_scene.story_outline_id
                            != chapter.story_outline_id
                            or draft.project_id != project_id
                            or draft.story_scene_id != source_scene.scene_id
                        ):
                            raise ValidationFailedError(
                                "歷史前情草稿的專案、場景或故事大綱歸屬不一致"
                            )
                        if (
                            record.id != summary_ref.entity_id
                            or record.scene_draft_id != draft.id
                            or record.story_scene_id != source_scene.scene_id
                            or record.project_id != project_id
                        ):
                            raise ValidationFailedError(
                                "歷史前情摘要的草稿、場景或專案歸屬不一致"
                            )
                        normalized_summary = record.summary_text.strip()
                        if not normalized_summary:
                            raise ValidationFailedError("歷史前情摘要內容已不存在")
                        if summary_ref.version_id is not None:
                            expected_digest = "sha256:" + hashlib.sha256(
                                normalized_summary.encode("utf-8")
                            ).hexdigest()
                            if summary_ref.version_id != expected_digest:
                                raise ValidationFailedError(
                                    "歷史前情摘要內容與生成當時的 SHA-256 pin 不一致"
                                )
                        previous_summary = normalized_summary
                        previous_summary_record_id = record.id
                        previous_summary_draft_id = draft.id
                else:
                    # The nearest established summary may live in an earlier
                    # chapter.  Walk the outline's one deterministic order in
                    # reverse and accept only provenance that still matches
                    # every ownership edge. Missing/blank summaries are
                    # ordinary gaps; contradictory records fail closed.
                    for candidate in reversed(ordered_scenes[:target_index]):
                        if (
                            candidate.scene_project_id != project_id
                            or candidate.chapter_project_id != project_id
                            or candidate.story_outline_id != chapter.story_outline_id
                        ):
                            raise ValidationFailedError(
                                "先前場景的專案、章節或故事大綱歸屬與場景順序不一致"
                            )
                        accepted_id = candidate.accepted_draft_id
                        if not accepted_id:
                            joined_values = (
                                candidate.draft_id,
                                candidate.draft_story_scene_id,
                                candidate.draft_project_id,
                                candidate.summary_id,
                                candidate.summary_scene_draft_id,
                                candidate.summary_story_scene_id,
                                candidate.summary_project_id,
                                candidate.summary_text,
                            )
                            if any(value is not None for value in joined_values):
                                raise ValidationFailedError(
                                    "未接受草稿的先前場景出現不一致的草稿或摘要投影"
                                )
                            continue
                        if candidate.draft_id is None:
                            raise ValidationFailedError(
                                "先前場景的已接受草稿指標找不到對應草稿"
                            )
                        if (
                            candidate.draft_id != accepted_id
                            or candidate.draft_story_scene_id != candidate.scene_id
                            or candidate.draft_project_id != project_id
                        ):
                            raise ValidationFailedError(
                                "先前場景的已接受草稿歸屬與場景或專案不一致"
                            )
                        summary_values = (
                            candidate.summary_scene_draft_id,
                            candidate.summary_story_scene_id,
                            candidate.summary_project_id,
                            candidate.summary_text,
                        )
                        if candidate.summary_id is None:
                            if any(value is not None for value in summary_values):
                                raise ValidationFailedError(
                                    "先前場景摘要紀錄欄位不完整"
                                )
                            continue
                        if not candidate.summary_id.strip():
                            raise ValidationFailedError(
                                "先前場景摘要紀錄缺少可用識別碼"
                            )
                        if (
                            candidate.summary_scene_draft_id != accepted_id
                            or candidate.summary_story_scene_id
                            != candidate.scene_id
                            or candidate.summary_project_id != project_id
                        ):
                            raise ValidationFailedError(
                                "先前場景摘要的草稿、場景或專案歸屬不一致"
                            )
                        if candidate.summary_text is None:
                            raise ValidationFailedError(
                                "先前場景摘要紀錄欄位不完整"
                            )
                        summary_text = candidate.summary_text.strip()
                        if not summary_text:
                            continue
                        previous_summary = summary_text
                        # §16: keep the EXACT summary record and the draft that
                        # was accepted at the time. A scene ID alone cannot say
                        # which draft this history came from.
                        previous_summary_record_id = candidate.summary_id
                        previous_summary_draft_id = accepted_id
                        break

            memory_projection = resolve_story_memory_projection(
                session,
                scene.id,
                exact_proposal_ids=request.memory_proposal_ids,
                exact_gap_scene_ids=request.memory_gap_scene_ids,
                exact_stale_proposal_ids=request.stale_memory_proposal_ids,
            )
            if (
                request.expected_memory_fingerprint
                and memory_projection.fingerprint
                != request.expected_memory_fingerprint
            ):
                raise ValidationFailedError(
                    "故事記憶歷史快照的 fingerprint 與不可變記錄不一致"
                )

        # ---- assemble (outside the session; pure function) -----------------
        references: list[VersionReference] = [
            VersionReference("scene_card", chain.scene_card_version_id)
        ]
        for entity_type, version_id in (
            ("requirement", chain.requirement_version_id),
            ("bible", chain.bible_version_id),
            ("outline", chain.outline_version_id),
            ("chapter_plan", chain.chapter_plan_version_id),
        ):
            if version_id:
                references.append(VersionReference(entity_type, version_id))

        package = build_context(
            card=card,
            generation_purpose=request.generation_purpose,
            complete_story_brief=request.complete_story_brief,
            bible=bible,
            requirement=requirement,
            outline=outline,
            chapter_plan=plan,
            memory_projection=memory_projection,
            character_locks=tuple(character_locks),
            character_voice_notes=tuple(voice_notes),
            previous_scene_summary=previous_summary,
            previous_summary_record_id=previous_summary_record_id,
            previous_summary_draft_id=previous_summary_draft_id,
            user_instruction=request.user_instruction,
            structured_must_avoid=request.structured_must_avoid,
            style_examples=request.style_examples,
            version_references=tuple(references),
            character_version_ids=tuple(character_version_ids),
            pov_character_version_id=pov_version_id or "",
            eligibility_fingerprint=request.eligibility_fingerprint,
            planning_mode=chain.planning_mode.value,
            planning_chain_fingerprint=chain.fingerprint,
            scene_card_version_id=chain.scene_card_version_id,
            requirement_version_id=chain.requirement_version_id or "",
            bible_version_id=chain.bible_version_id or "",
            outline_version_id=chain.outline_version_id or "",
            chapter_plan_version_id=chain.chapter_plan_version_id or "",
            max_chars=request.max_chars,
            budget_snapshot=request.budget_snapshot,
        )
        return ResolvedStoryContext(
            package=package,
            project_id=project_id,
            scene_id=scene.id,
            scene_card=card,
            requirement=requirement,
            bible=bible,
            outline=outline,
            memory_projection=memory_projection,
            scene_card_version_id=chain.scene_card_version_id,
            participants=participants,
            requirement_version_id=chain.requirement_version_id,
            bible_version_id=chain.bible_version_id,
            outline_version_id=chain.outline_version_id,
            chapter_plan_version_id=chain.chapter_plan_version_id,
            pov_character_version_id=pov_version_id,
            planning_mode=chain.planning_mode,
            planning_chain_fingerprint=chain.fingerprint,
            complete_story_brief=request.complete_story_brief,
        )

    # ============================================ A3-R10 historical inspection
    def inspect_generation_run(
        self,
        run_id: str,
        *,
        expected_project_id: str,
    ) -> HistoricalContextInspection:
        """A3-R10 §10: inspect ONE historical run, by its exact run ID.

        The Context tab used to rebuild a context from the CURRENT scene and
        the CURRENT input box, then present it as if it were the context a
        past run had used. This method never consults the current selection:
        every value comes from the run's own row and the exact version IDs it
        pinned, and what the caller may claim about the message text is
        decided solely by stored bytes and SHA-256 evidence.
        """
        warnings: list[str] = []
        with self._session_factory() as session:
            run = GenerationRunRepository(session).get(run_id)
            # A cross-project run and a non-existent run return the SAME error:
            # distinguishing them would confirm that some other project holds
            # this run ID.
            if run is None or run.project_id != expected_project_id:
                raise NotFoundError(f"找不到生成紀錄：{run_id}")
            scene = StorySceneRepository(session).get(run.story_scene_id)
            if scene is None or scene.project_id != expected_project_id:
                raise NotFoundError(f"找不到生成紀錄：{run_id}")

            snapshot, snapshot_verified = self._verify_input_snapshot(run, warnings)
            options_snapshot, options_verified = self._parse_options_snapshot(
                run, warnings
            )
            if not options_verified:
                warnings.append(
                    "generation options 快照無法驗證；以下僅顯示原始 JSON 供診斷。"
                )

            references = _version_references_of(run)
            character_version_ids = _character_version_ids_of(run, warnings)

            stored_ok, stored_problems = _verify_stored_messages(run)
            storage_enabled = bool(run.rendered_message_storage_enabled)

            system_text = ""
            user_text = ""
            diagnostic_system = ""
            diagnostic_user = ""
            reconstruction_matches: bool | None = None

            if storage_enabled and stored_ok:
                mode = HistoricalContextDisplayMode.STORED_EXACT
                system_text = run.rendered_system_message or ""
                user_text = run.rendered_user_message or ""
            elif storage_enabled:
                # Raw storage was on, yet the stored text is absent or
                # disagrees with its own hash/byte size. It must never be
                # labelled exact history.
                mode = HistoricalContextDisplayMode.STORED_INTEGRITY_ERROR
                diagnostic_system = run.rendered_system_message or ""
                diagnostic_user = run.rendered_user_message or ""
                warnings.extend(stored_problems)
            else:
                candidate = self._reconstruct_messages(
                    session, run, warnings, snapshot=snapshot
                )
                if candidate is None:
                    mode = HistoricalContextDisplayMode.RECONSTRUCTION_MISMATCH
                    reconstruction_matches = False
                else:
                    candidate_system, candidate_user = candidate
                    reconstruction_matches = (
                        _sha256(candidate_system) == run.system_message_sha256
                        and _sha256(candidate_user) == run.user_message_sha256
                    )
                    if reconstruction_matches and snapshot_verified:
                        mode = HistoricalContextDisplayMode.RECONSTRUCTED_MATCH
                        system_text = candidate_system
                        user_text = candidate_user
                    else:
                        mode = HistoricalContextDisplayMode.RECONSTRUCTION_MISMATCH
                        diagnostic_system = candidate_system
                        diagnostic_user = candidate_user
                        if reconstruction_matches and not snapshot_verified:
                            # Hashes agree but the immutable snapshot does not
                            # verify, so the run's own provenance is in doubt.
                            reconstruction_matches = False
                            warnings.append(
                                "重建訊息的 SHA-256 與原始雜湊相符，但不可變快照"
                                "本身未通過完整性驗證，因此不宣稱可信重現。"
                            )

        return HistoricalContextInspection(
            run_id=run.id,
            scene_id=run.story_scene_id,
            run_kind=run.run_kind,
            status=run.status,
            planning_mode=run.planning_mode,
            content_mode=run.content_mode,
            provider=run.provider,
            model=run.model,
            renderer_version=run.renderer_version,
            context_schema_version=run.context_schema_version,
            context_contract_version=run.context_contract_version,
            context_budget_policy_version=run.context_budget_policy_version,
            context_budget_snapshot=(
                snapshot.context_budget_snapshot.model_dump(mode="json")
                if snapshot is not None
                else {}
            ),
            context_fingerprint=run.context_fingerprint,
            planning_chain_fingerprint=run.planning_chain_fingerprint,
            eligibility_fingerprint=run.eligibility_fingerprint,
            system_message_sha256=run.system_message_sha256,
            user_message_sha256=run.user_message_sha256,
            system_message_byte_size=run.system_message_byte_size,
            user_message_byte_size=run.user_message_byte_size,
            rendered_message_storage_enabled=storage_enabled,
            display_mode=mode,
            system_message=system_text,
            user_message=user_text,
            reconstruction_matches=reconstruction_matches,
            version_references=references,
            character_version_ids=character_version_ids,
            pov_character_version_id=run.pov_character_version_id or "",
            generation_options_snapshot=options_snapshot,
            generation_options_raw_json=run.options_snapshot_json,
            input_snapshot_sha256_verified=snapshot_verified,
            context_source_refs=(
                snapshot.context_source_refs if snapshot is not None else ()
            ),
            # Refs are trustworthy only when the snapshot that carries them
            # verified. An unverified snapshot may still be shown raw for
            # diagnosis, but its refs are not "the sources used at the time".
            context_source_refs_verified=snapshot_verified,
            stored_message_hashes_verified=storage_enabled and stored_ok,
            started_at=run.started_at,
            completed_at=run.completed_at,
            diagnostic_system_message=diagnostic_system,
            diagnostic_user_message=diagnostic_user,
            warnings=tuple(warnings),
        )

    # ---------------------------------------------- A3-R10 inspection helpers
    @staticmethod
    def _verify_input_snapshot(
        run: GenerationRunRecord, warnings: list[str]
    ) -> tuple[GenerationInputSnapshot | None, bool]:
        """§14.2: the immutable snapshot must hash to its stored digest AND
        agree with the run row. A mismatch is reported, never repaired."""
        if _sha256(run.input_snapshot_json) != run.input_snapshot_sha256:
            warnings.append(
                "不可變輸入快照的 SHA-256 與紀錄不符；此 run 的來源證據不完整，"
                "重建結果不得視為可信。"
            )
            return None, False
        try:
            snapshot = GenerationInputSnapshot.model_validate_json(
                run.input_snapshot_json
            )
        except ValidationError:
            warnings.append("不可變輸入快照無法解析；重建結果不得視為可信。")
            return None, False
        mismatched = [
            name
            for name, snapshot_value, run_value in (
                ("project_id", snapshot.project_id, run.project_id),
                ("story_scene_id", snapshot.story_scene_id, run.story_scene_id),
                (
                    "scene_card_version_id",
                    snapshot.scene_card_version_id,
                    run.scene_card_version_id,
                ),
                (
                    "requirement_version_id",
                    snapshot.requirement_version_id,
                    run.requirement_version_id,
                ),
                (
                    "bible_version_id",
                    snapshot.bible_version_id,
                    run.bible_version_id,
                ),
                (
                    "outline_version_id",
                    snapshot.outline_version_id,
                    run.outline_version_id,
                ),
                (
                    "chapter_plan_version_id",
                    snapshot.chapter_plan_version_id,
                    run.chapter_plan_version_id,
                ),
            )
            if snapshot_value != run_value
        ]
        if mismatched:
            warnings.append(
                "不可變快照與生成紀錄的欄位不一致："
                f"{'、'.join(mismatched)}；重建結果不得視為可信。"
            )
            # The snapshot parsed, so it can still be shown for diagnosis, but
            # its refs must NOT be presented as the run's verified sources.
            return snapshot, False
        return snapshot, True

    @staticmethod
    def _parse_options_snapshot(
        run: GenerationRunRecord, warnings: list[str]
    ) -> tuple[dict[str, Any] | None, bool]:
        """§14.3: verify the options snapshot; parse failure must never crash.

        The stored JSON is ``model_dump_json()`` while the stored digest is
        over the CANONICAL serialization, so hashing the raw column text would
        never match. The content is parsed first and the model's own canonical
        digest is what gets compared.
        """
        try:
            options = GenerationOptionsSnapshot.model_validate_json(
                run.options_snapshot_json
            )
        except ValidationError:
            return None, False
        if options.sha256 != run.options_snapshot_sha256:
            warnings.append("generation options 快照的 SHA-256 與紀錄不符。")
            return None, False
        parsed = json.loads(run.options_snapshot_json)
        if not isinstance(parsed, dict):
            return None, False
        return parsed, True

    def _reconstruct_messages(
        self,
        session: Any,
        run: GenerationRunRecord,
        warnings: list[str],
        *,
        snapshot: GenerationInputSnapshot | None,
    ) -> tuple[str, str] | None:
        """§16: a best-effort deterministic candidate from the run's exact IDs.

        Whether the candidate is actually what was sent is decided afterwards,
        by comparing SHA-256 against the stored hashes — never by how plausible
        the candidate looks. Returning ``None`` means no candidate could be
        built at all.
        """
        previous_summary_source_refs: tuple[ContextSourceRef, ...] | None = None
        typed_ref_schema = (
            None
            if snapshot is None
            else re.search(r"-v(\d+)$", snapshot.schema_version)
        )
        if snapshot is not None and (
            snapshot.context_source_refs
            or (
                typed_ref_schema is not None
                and int(typed_ref_schema.group(1)) >= 3
            )
        ):
            # A typed-ref snapshot is a complete source manifest: no matching
            # refs means the original run had no previous-summary block.  Only
            # snapshots predating typed refs retain the legacy current-pointer
            # candidate, which remains protected by the final message hashes.
            previous_summary_source_refs = tuple(
                ref
                for ref in snapshot.context_source_refs
                if ref.entity_type
                in {
                    SourceEntityType.SCENE_DRAFT_SUMMARY,
                    SourceEntityType.SCENE_DRAFT,
                }
            )
        try:
            resolved = self.build_for_scene(
                BuildStoryContextRequest(
                    scene_id=run.story_scene_id,
                    scene_card_version_id=run.scene_card_version_id,
                    requirement_version_id=run.requirement_version_id,
                    bible_version_id=run.bible_version_id,
                    outline_version_id=run.outline_version_id,
                    chapter_plan_version_id=run.chapter_plan_version_id,
                    eligibility_fingerprint=run.eligibility_fingerprint,
                    budget_snapshot=(
                        snapshot.context_budget_snapshot
                        if snapshot is not None
                        else None
                    ),
                    memory_proposal_ids=(
                        snapshot.memory_proposal_ids
                        if snapshot is not None
                        else ()
                    ),
                    memory_gap_scene_ids=(
                        snapshot.memory_gap_scene_ids
                        if snapshot is not None
                        else ()
                    ),
                    stale_memory_proposal_ids=(
                        snapshot.stale_memory_proposal_ids
                        if snapshot is not None
                        else ()
                    ),
                    expected_memory_fingerprint=(
                        snapshot.memory_projection_fingerprint
                        if snapshot is not None
                        else ""
                    ),
                    previous_summary_source_refs=previous_summary_source_refs,
                    generation_purpose=(
                        snapshot.generation_purpose
                        if snapshot is not None
                        else StoryGenerationPurpose.SCENE
                    ),
                    complete_story_brief=(
                        snapshot.complete_story_brief
                        if snapshot is not None
                        else None
                    ),
                    structured_must_avoid=(
                        snapshot.structured_must_avoid
                        if snapshot is not None
                        else ()
                    ),
                    # §16.3: PREVIEW loads the EXACT pinned versions and still
                    # enforces ownership and chain coherence. ACCEPTED would
                    # reject or silently re-target a version that is no longer
                    # the accepted one — the substitution this must prevent.
                    planning_mode=PlanningMode.PREVIEW,
                )
            )
        except NotFoundError as exc:
            warnings.append(
                f"無法以歷史版本重建：{exc}。原始 SHA-256 仍保留，但當時的原文未儲存。"
            )
            return None
        except ApplicationError as exc:
            warnings.append(f"無法以歷史版本重建：{exc}")
            return None

        base_user = resolved.package.user_message
        if run.run_kind == "generation":
            user = self._reconstruct_adult_prompt(snapshot, base_user, warnings)
            if user is None:
                return None
            return resolved.package.system_message, user
        if run.run_kind != "revision":
            warnings.append(f"不支援的 run_kind={run.run_kind}，無法重建訊息。")
            return None

        # §16.6: modern snapshots carry the source draft and canonical
        # revision request even when provider failure/adult quarantine means
        # no produced SceneDraft exists.  A produced-draft lookup is only a
        # compatibility fallback for older snapshots lacking those fields.
        parent_id = ""
        revision_json = ""
        if snapshot is not None and (
            snapshot.parent_draft_id is not None
            and snapshot.revision_request_json != "{}"
            and bool(snapshot.revision_request_fingerprint)
        ):
            if snapshot.run_kind != run.run_kind:
                warnings.append("修訂 snapshot 與 run 的 run_kind 不一致。")
                return None
            if run.parent_draft_id != snapshot.parent_draft_id:
                warnings.append("修訂 snapshot 與 run 的來源草稿不一致。")
                return None
            if run.revision_request_fingerprint != snapshot.revision_request_fingerprint:
                warnings.append("修訂 snapshot 與 run 的請求指紋不一致。")
                return None
            parent_id = snapshot.parent_draft_id
            revision_json = snapshot.revision_request_json
        elif (
            snapshot is not None
            and snapshot.schema_version == GENERATION_SNAPSHOT_SCHEMA_VERSION
        ):
            warnings.append("新版修訂 snapshot 缺少完整的來源草稿或修訂請求。")
            return None
        else:
            draft = SceneDraftRepository(session).get_for_generation_run(run.id)
            if draft is None or not draft.parent_draft_id:
                warnings.append(
                    "此 legacy 修訂 run 沒有可回溯的產出草稿或來源草稿，"
                    "無法重建當時的訊息。"
                )
                return None
            if (
                draft.story_scene_id != run.story_scene_id
                or draft.project_id != run.project_id
                or draft.scene_card_version_id != run.scene_card_version_id
                or (run.parent_draft_id and run.parent_draft_id != draft.parent_draft_id)
                or (
                    run.revision_request_fingerprint
                    and run.revision_request_fingerprint
                    != (draft.revision_request_fingerprint or "")
                )
            ):
                warnings.append("legacy 修訂產出草稿與 run provenance 不一致。")
                return None
            parent_id = draft.parent_draft_id
            revision_json = draft.revision_request_json

        parent = SceneDraftRepository(session).get(parent_id)
        if parent is None:
            warnings.append("此修訂 run 的來源草稿已不存在，無法重建當時的訊息。")
            return None
        if (
            parent.story_scene_id != run.story_scene_id
            or parent.project_id != run.project_id
            or parent.scene_card_version_id != run.scene_card_version_id
        ):
            warnings.append("修訂 run 的來源草稿場景、專案或 Scene Card 歸屬不一致。")
            return None
        try:
            revision_payload = json.loads(revision_json)
            revision = RevisionRequest.model_validate(revision_payload)
        except (json.JSONDecodeError, ValidationError):
            warnings.append("此修訂 run 的修訂請求無法解析，無法重建當時的訊息。")
            return None
        canonical_revision = canonical_json(revision.model_dump(mode="json"))
        revision_fingerprint = _sha256(canonical_revision)
        expected_fingerprint = (
            snapshot.revision_request_fingerprint
            if snapshot is not None
            and snapshot.parent_draft_id == parent_id
            and snapshot.revision_request_json == revision_json
            else run.revision_request_fingerprint
        )
        if (
            canonical_revision != revision_json
            or not expected_fingerprint
            or revision_fingerprint != expected_fingerprint
            or (
                run.revision_request_fingerprint
                and revision_fingerprint != run.revision_request_fingerprint
            )
        ):
            warnings.append("修訂請求的 canonical JSON 或 SHA-256 指紋不一致。")
            return None
        revision_purpose = (
            snapshot.generation_purpose
            if snapshot is not None
            else StoryGenerationPurpose.SCENE
        )
        system = build_revision_contract(revision, purpose=revision_purpose)
        user = (
            f"{base_user}\n\n"
            f"## 目前草稿（要修訂的對象）\n{parent.prose_text}"
        )
        user = self._reconstruct_adult_prompt(snapshot, user, warnings)
        if user is None:
            return None
        return system, user

    @staticmethod
    def _reconstruct_adult_prompt(
        snapshot: GenerationInputSnapshot | None,
        user_message: str,
        warnings: list[str],
    ) -> str | None:
        if snapshot is None or not derives_adult(snapshot.content_mode):
            return user_message
        if (
            snapshot.output_contract_version
            != ADULT_STORY_OUTPUT_ENVELOPE_VERSION
            or not snapshot.options.structured_mode
            or snapshot.options.stream
        ):
            warnings.append("成人 run snapshot 未綁定正確的 structured output contract。")
            return None
        try:
            contract = build_adult_story_provider_contract(snapshot)
        except ApplicationError as exc:
            warnings.append(f"無法重建成人 structured output contract：{exc}")
            return None
        return f"{user_message}\n\n{contract}"

    # ------------------------------------------------------------- helpers
    @staticmethod
    def _load_planning(
        session: object,
        *,
        kind: str,
        version_id: str | None,
        model: type[_ModelT],
        payload_key: str,
    ) -> _ModelT | None:
        """Parse one already-validated planning version."""
        if not version_id:
            return None
        record = VersionedEntityRepository(session, kind).get_version(  # type: ignore[arg-type]
            version_id
        )
        if record is None:
            raise NotFoundError(f"找不到版本：{version_id}")
        return model.model_validate_json(str(record.payload[payload_key]))


def _hard_locks(traits: tuple[CanonicalTrait, ...]) -> str:
    """Hard-locked canonical traits, rendered for the never-trimmed block."""
    locked = [
        f"{t.category}／{t.name}：{t.canonical_descriptor}"
        for t in traits
        if t.strength is CanonStrength.HARD_LOCK
    ]
    return "；".join(locked)


def _sha256(text: str) -> str:
    """A3-R10 §15.1: hashes are over UTF-8 BYTES, never character counts."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _verify_stored_messages(
    run: GenerationRunRecord,
) -> tuple[bool, list[str]]:
    """§15.1/§15.2: stored text counts as exact history only when it is
    present AND reproduces both its stored hash and its stored byte size."""
    problems: list[str] = []
    system = run.rendered_system_message
    user = run.rendered_user_message
    if system is None or user is None:
        problems.append(
            "此 run 標示為已儲存原始訊息，但資料庫中缺少 system 或 user 原文；"
            "無法宣稱顯示的是當時原文。"
        )
        return False, problems
    if _sha256(system) != run.system_message_sha256:
        problems.append(
            "已儲存的 system 訊息與其 SHA-256 不符；內容可能已被竄改或損壞。"
        )
    if _sha256(user) != run.user_message_sha256:
        problems.append(
            "已儲存的 user 訊息與其 SHA-256 不符；內容可能已被竄改或損壞。"
        )
    if len(system.encode("utf-8")) != run.system_message_byte_size:
        problems.append("已儲存的 system 訊息位元組大小與紀錄不符。")
    if len(user.encode("utf-8")) != run.user_message_byte_size:
        problems.append("已儲存的 user 訊息位元組大小與紀錄不符。")
    return not problems, problems


def _version_references_of(
    run: GenerationRunRecord,
) -> tuple[VersionReference, ...]:
    """§14.4: typed refs carrying stable version IDs only — no display names."""
    references = [VersionReference("scene_card", run.scene_card_version_id)]
    for entity_type, version_id in (
        ("requirement", run.requirement_version_id),
        ("bible", run.bible_version_id),
        ("outline", run.outline_version_id),
        ("chapter_plan", run.chapter_plan_version_id),
    ):
        if version_id:
            references.append(VersionReference(entity_type, version_id))
    return tuple(references)


def _character_version_ids_of(
    run: GenerationRunRecord, warnings: list[str]
) -> tuple[str, ...]:
    """§14.4: the EXACT participating Character Version IDs the run pinned."""
    try:
        parsed = json.loads(run.character_version_ids_json)
    except json.JSONDecodeError:
        warnings.append("參與角色版本清單無法解析。")
        return ()
    if not isinstance(parsed, list):
        warnings.append("參與角色版本清單格式不正確。")
        return ()
    return tuple(str(item) for item in parsed)
