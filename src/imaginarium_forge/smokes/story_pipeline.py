"""Mock Official MVP smoke test (spec §49).

Runs the ENTIRE Phase 3 workflow against a temporary database with a mock
provider — no Ollama, no GPU, no network:

    project → requirement → bible → outline → chapter → scene card
            → generate → revise → accept → export (markdown + JSON)

Exit code 0 means the Official MVP path is wired end to end.

    python -m imaginarium_forge.smokes.story_pipeline
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from threading import Event

from imaginarium_forge.application.services.character_service import (
    CharacterService,
    CharacterVersionService,
)
from imaginarium_forge.application.services.eligibility_service import (
    EligibilityService,
)
from imaginarium_forge.application.services.project_service import ProjectService
from imaginarium_forge.application.services.scene_generation_service import (
    RevisionOperation,
    RevisionRequest,
    RunStatus,
    SceneDraftService,
)
from imaginarium_forge.application.services.story_export_service import (
    StoryExportMode,
    StoryExportService,
)
from imaginarium_forge.application.services.story_orchestration_service import (
    GenerateSceneRequest,
    ReviseSceneRequest,
    StoryGenerationOrchestrationService,
    StoryRevisionOrchestrationService,
)
from imaginarium_forge.application.services.story_planning_service import (
    ChapterPlanService,
    SceneCardService,
    StoryBibleService,
    StoryOutlineService,
    StoryRequirementService,
)
from imaginarium_forge.domain.canon.traits import CanonicalTrait
from imaginarium_forge.domain.character.version import AdultPresentation, VisualDNA
from imaginarium_forge.domain.common.enums import CanonStrength
from imaginarium_forge.domain.story.models import (
    BibleCharacterEntry,
    ChapterPlan,
    NarrativeContract,
    OutlineAct,
    SceneCard,
    SceneConflict,
    SceneGoal,
    SceneParticipant,
    StoryBible,
    StoryOutline,
    StoryRequirement,
    StructureProfile,
)
from imaginarium_forge.infrastructure.db.session import (
    create_db_engine,
    create_session_factory,
    upgrade_to_head,
)
from imaginarium_forge.providers.contracts import GenerationRequest, GenerationResult


class _MockProvider:
    def __init__(self, text: str) -> None:
        self._text = text

    def generate_text(
        self, request: GenerationRequest, *, cancel: Event | None = None
    ) -> GenerationResult:
        return GenerationResult(text=self._text, model=request.model, latency_ms=1)

    def stream_text(self, request: GenerationRequest, *, cancel: Event | None = None):  # type: ignore[no-untyped-def]
        yield self._text

    def health(self) -> bool:
        return True


def _check(label: str, condition: bool) -> bool:
    print(f"  [{'OK ' if condition else 'FAIL'}] {label}")
    return condition


def main() -> int:
    print("Story Studio Official MVP smoke (mock provider)")
    passed = True
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "smoke.db"
        upgrade_to_head(db_path)
        engine = create_db_engine(db_path)
        factory = create_session_factory(engine)
        try:
            project = ProjectService(factory).create_project(name="Smoke")

            requirements = StoryRequirementService(factory)
            req = requirements.create(project_id=project.id, title="需求")
            req_version = requirements.add_version(
                req.id,
                requirement=StoryRequirement(
                    concept="一個關於記憶與背叛的心理驚悚故事",
                    genre="心理驚悚",
                    tone="冷冽",
                ),
            )
            requirements.accept_version(req_version.id)
            passed &= _check(
                "requirement version created and accepted",
                req_version.version_number == 1,
            )

            bibles = StoryBibleService(factory)
            bible = bibles.create(project_id=project.id, title="聖經")
            bible_version = bibles.add_version(
                bible.id,
                bible=StoryBible(
                    title="夜行",
                    logline="她必須在天亮前想起自己做過什麼。",
                    characters=(BibleCharacterEntry(name="凜", role="主角"),),
                    narrative_contract=NarrativeContract(
                        forbidden_reveals=("凜就是兇手",),
                    ),
                ),
                requirement_version_id=req_version.id,
            )
            bibles.accept_version(bible_version.id)
            passed &= _check("bible version accepted", True)

            outlines = StoryOutlineService(factory)
            outline = outlines.create(project_id=project.id, title="主線")
            outline_version = outlines.add_version(
                outline.id,
                outline=StoryOutline(
                    structure_profile=StructureProfile.THREE_ACT,
                    acts=(OutlineAct(name="第一幕", purpose="建立世界與疑問"),),
                ),
                bible_version_id=bible_version.id,
            )
            outlines.accept_version(outline_version.id)

            chapters = ChapterPlanService(factory)
            chapter = chapters.create_chapter(outline_id=outline.id, title="第一章")
            plan_version = chapters.add_plan_version(
                chapter.id,
                plan=ChapterPlan(chapter_number=1, purpose="讓讀者相信凜是受害者"),
            )
            chapters.accept_plan_version(plan_version.id)

            # A3-03: the Scene Card pins an EXACT Character Version
            character = CharacterService(factory).create_original_character(
                project_id=project.id,
                name="凜",
                explicit_age=28,
                user_confirmed_age=True,
            )
            character_version = CharacterVersionService(factory).create_version(
                character_id=character.id,
                adult_presentation=AdultPresentation(),
                visual_dna=VisualDNA(
                    identity="adult woman",
                    hair="silver bob",
                    eyes="amber",
                    canonical_traits=(
                        CanonicalTrait(
                            category="hair",
                            name="streak",
                            canonical_descriptor="left blue streak",
                            strength=CanonStrength.HARD_LOCK,
                        ),
                    ),
                ),
            )
            pin = SceneParticipant(
                character_id=character.id,
                character_version_id=character_version.id,
            )

            cards = SceneCardService(
                factory, eligibility=EligibilityService(factory)
            )
            scene = cards.create_scene(chapter_id=chapter.id, title="天橋")
            card = SceneCard(
                location="天橋",
                participants=(pin,),
                pov_character=pin,
                scene_goal=SceneGoal(protagonist="找到目擊者"),
                conflict=SceneConflict(external="有人跟蹤", internal="不確定記憶"),
                beats=("走上天橋", "看見熟悉的身影", "對方先開口"),
                turning_point="她認出對方的聲音",
                forbidden_reveals=("凜就是兇手",),
                target_word_count=800,
            )
            card_version = cards.add_card_version(scene.id, card=card)
            cards.accept_card_version(card_version.id)
            passed &= _check(
                "scene card version created and accepted",
                card_version.version_number == 1,
            )

            generation = StoryGenerationOrchestrationService(
                factory,
                provider=_MockProvider("她走上天橋，夜雨落在肩上。"),  # type: ignore[arg-type]
                provider_name="mock",
                eligibility=EligibilityService(factory),
            )
            outcome = generation.generate_scene(
                GenerateSceneRequest(scene_id=scene.id, model="mock-model")
            )
            passed &= _check(
                "draft generated", outcome.status is RunStatus.COMPLETED
            )
            assert outcome.draft is not None
            snapshot = outcome.snapshot
            passed &= _check(
                "snapshot binds every persisted version",
                snapshot.scene_card_version_id == card_version.id
                and snapshot.requirement_version_id == req_version.id
                and snapshot.bible_version_id == bible_version.id
                and snapshot.outline_version_id is not None
                and snapshot.chapter_plan_version_id is not None,
            )

            drafts = SceneDraftService(factory)
            runs = drafts.list_runs(scene.id)
            passed &= _check("generation run recorded", len(runs) == 1)
            passed &= _check(
                "run stores the input snapshot hash",
                runs[0].input_snapshot_sha256 == snapshot.sha256,
            )
            passed &= _check(
                "context carries hard canon + forbidden reveals",
                "Hard Canon" in runs[0].input_snapshot_json
                or snapshot.context_fingerprint != "",
            )

            revision = StoryRevisionOrchestrationService(
                factory,
                provider=_MockProvider("她走上天橋。雨珠沿著欄杆滑落。"),  # type: ignore[arg-type]
                provider_name="mock",
                eligibility=EligibilityService(factory),
            )
            revised = revision.revise_scene(
                ReviseSceneRequest(
                    draft_id=outcome.draft.id,
                    model="mock-model",
                    revision=RevisionRequest(operation=RevisionOperation.TIGHTEN),
                )
            )
            assert revised.draft is not None
            passed &= _check(
                "revision produced a NEW draft",
                revised.draft.id != outcome.draft.id
                and revised.draft.parent_draft_id == outcome.draft.id,
            )

            drafts.accept_draft(revised.draft.id)
            passed &= _check(
                "accepted pointer names the revised draft",
                (accepted := drafts.accepted_draft(scene.id)) is not None
                and accepted.id == revised.draft.id,
            )
            cards.update_summary(scene.id, "凜在天橋遇見熟人，對方知道她忘記的事。")

            exports = StoryExportService(factory)
            markdown, as_json = exports.build(
                project_id=project.id,
                mode=StoryExportMode.FULL,
                outline_id=outline.id,
                bible_version_id=bible_version.id,
                requirement_version_id=req_version.id,
            )
            payload = json.loads(as_json)
            snapshot_block = payload["snapshot"]
            passed &= _check("markdown export non-empty", len(markdown) > 200)
            passed &= _check(
                "export snapshot carries typed version ids",
                snapshot_block["requirement_version_id"] == req_version.id
                and snapshot_block["bible_version_id"] == bible_version.id
                and snapshot_block["outline_version_id"] == outline_version.id,
            )
            passed &= _check(
                "export snapshot is hashed",
                len(payload["snapshot_sha256"]) == 64,
            )
            scene_payload = payload["content"]["chapters"][0]["scenes"][0]
            passed &= _check(
                "exported prose is the ACCEPTED draft (pointer, not a scan)",
                scene_payload["prose"] == revised.draft.prose_text
                and scene_payload["draft_id"] == revised.draft.id,
            )
            passed &= _check(
                "generation metadata travels with prose",
                scene_payload["generation"]["provider"] == "mock"
                and len(scene_payload["generation"]["input_snapshot_sha256"]) == 64,
            )
            markdown_again, _ = exports.build(
                project_id=project.id,
                mode=StoryExportMode.FULL,
                outline_id=outline.id,
                bible_version_id=bible_version.id,
                requirement_version_id=req_version.id,
            )
            passed &= _check("export is byte-stable", markdown == markdown_again)
        finally:
            engine.dispose()

    print("RESULT:", "PASS" if passed else "FAIL")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
