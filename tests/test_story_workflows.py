"""Offline regression checks for story, screenplay, export and backup workflows."""

from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path
from threading import Event

from pydantic import ValidationError

from imaginarium_forge.application.services.creative_automation_service import (
    AutomationBrief,
    AutomationKind,
)
from imaginarium_forge.application.services.scene_generation_service import (
    RevisionOperation,
    RevisionRequest,
)
from imaginarium_forge.application.services.screenplay_adaptation_service import (
    GenerateScreenplayRequest,
    ScreenplayGenerationService,
)
from imaginarium_forge.application.services.story_context_service import (
    HistoricalContextDisplayMode,
)
from imaginarium_forge.application.services.story_export_service import StoryExportMode
from imaginarium_forge.application.services.story_orchestration_service import (
    GenerateSceneRequest,
    ReviseSceneRequest,
    StoryGenerationOrchestrationService,
    StoryRevisionOrchestrationService,
)
from imaginarium_forge.config.settings import AppSettings
from imaginarium_forge.domain.generation.length import (
    GenerationLengthSpec,
    LengthStrictness,
    LengthUnit,
)
from imaginarium_forge.domain.prompt.content_mode import ContentMode
from imaginarium_forge.domain.story.models import (
    ChapterPlan,
    IntensityLevel,
    SceneCard,
    StoryBible,
    StoryOutline,
    StoryRequirement,
)
from imaginarium_forge.infrastructure.db.repositories.story_repos import (
    DraftRecord,
    GenerationRunRepository,
)
from imaginarium_forge.providers.contracts import (
    GenerationInputItem,
    GenerationRequest,
    GenerationResult,
)
from imaginarium_forge.providers.errors import ProviderRefusalError
from imaginarium_forge.providers.mock import MockProvider
from imaginarium_forge.ui.bootstrap import build_services


class GenerationContractTests(unittest.TestCase):
    def test_narrative_tones_and_intensity_validate_author_choices(self) -> None:
        concept = "A detective follows a clue through the rainy city."
        for mode in ContentMode:
            requirement = StoryRequirement(
                concept=concept,
                content_mode=mode,
                horror_intensity=IntensityLevel.EXTREME,
            )
            self.assertEqual(requirement.concept, concept)
            self.assertEqual(requirement.content_mode, mode)
            self.assertEqual(requirement.horror_intensity, IntensityLevel.EXTREME)
        with self.assertRaises(ValidationError):
            StoryRequirement(content_mode="unknown-tone")

    def test_generation_keeps_author_instructions_and_audience_guidance(self) -> None:
        system = "Write concise Traditional Chinese dialogue."
        prompt = "A librarian finds a lost map."
        for items in (
            (),
            (GenerationInputItem(role="user", content=prompt),),
            (
                GenerationInputItem(role="system", content=system),
                GenerationInputItem(role="user", content=prompt),
            ),
        ):
            with self.subTest(items=items):
                request = GenerationRequest(
                    model="offline-test", prompt=prompt, system=system, input_items=items
                )
                self.assertEqual(request.prompt, prompt)
                self.assertIn(system, request.system)
                self.assertRegex(request.system.lower(), r"general[ -]audience")
                if items:
                    self.assertEqual(request.input_items[0].role, "system")
                    self.assertRegex(request.input_items[0].content.lower(), r"general[ -]audience")
                    self.assertIn(GenerationInputItem(role="user", content=prompt), request.input_items)
                reparsed = GenerationRequest.model_validate(request.model_dump())
                self.assertEqual(reparsed.model_dump(), request.model_dump())
        brief = AutomationBrief(project_id="test", kind=AutomationKind.STORY, clue="雨夜的偵探")
        self.assertEqual(brief.clue, "雨夜的偵探")


class StoryWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory(prefix="forge-story-")
        self.settings = AppSettings(
            data_dir=Path(self._temporary.name),
            device_credential_store_enabled=False,
            store_rendered_generation_messages=True,
        )
        self.services = build_services(
            self.settings,
            credential_environment={},
        )
        services = self.services
        project = services.projects.create_project(name="Offline regression")
        requirement = services.story_requirements.create(project_id=project.id, title="Requirement")
        requirement_version = services.story_requirements.add_version(
            requirement.id, requirement=StoryRequirement(concept="A rainstorm passes over the city.")
        )
        services.story_requirements.accept_version(requirement_version.id)
        bible = services.story_bibles.create(project_id=project.id, title="Bible")
        bible_version = services.story_bibles.add_version(
            bible.id, bible=StoryBible(), requirement_version_id=requirement_version.id
        )
        services.story_bibles.accept_version(bible_version.id)
        outline = services.story_outlines.create(project_id=project.id, title="Outline")
        outline_version = services.story_outlines.add_version(
            outline.id, outline=StoryOutline(), bible_version_id=bible_version.id
        )
        services.story_outlines.accept_version(outline_version.id)
        chapter = services.chapter_plans.create_chapter(outline_id=outline.id, title="Chapter")
        plan = services.chapter_plans.add_plan_version(chapter.id, plan=ChapterPlan())
        services.chapter_plans.accept_plan_version(plan.id)
        scene = services.scene_cards.create_scene(chapter_id=chapter.id, title="Rain")
        card = services.scene_cards.add_card_version(scene.id, card=SceneCard())
        services.scene_cards.accept_card_version(card.id)
        self.scene_id = scene.id
        self.project_id = project.id
        self.outline_id = outline.id

    def tearDown(self) -> None:
        self.services.lifecycle.dispose()
        self._temporary.cleanup()

    def _generate_story(self) -> DraftRecord:
        provider = MockProvider(
            text_responses=[
                "Rain tapped on the station roof. The last train left the platform. "
                "The old clock struck midnight, and the lights faded in the quiet hall."
            ]
        )
        service = StoryGenerationOrchestrationService(
            self.services.lifecycle, provider=provider, provider_name="mock"
        )
        outcome = service.generate_scene(
            GenerateSceneRequest(
                scene_id=self.scene_id,
                model="offline-test",
                length_spec=GenerationLengthSpec(
                    unit=LengthUnit.WORDS,
                    minimum=10,
                    target=20,
                    maximum=100,
                    segment_target=None,
                    max_continuations=0,
                    strictness=LengthStrictness.BOUNDED,
                ),
            )
        )
        self.assertEqual(outcome.status.value, "completed")
        self.assertIsNotNone(outcome.draft)
        self.assertEqual(len(provider.requests), 1)
        with self.services.lifecycle() as session:
            run = GenerationRunRepository(session).get(outcome.run_id)
        sent_system = provider.requests[0].system
        self.assertEqual(run.rendered_system_message, sent_system)
        self.assertEqual(
            run.system_message_sha256, hashlib.sha256(sent_system.encode("utf-8")).hexdigest()
        )
        return outcome.draft

    def test_story_generation_acceptance_and_reproducible_export(self) -> None:
        draft = self._generate_story()
        self.services.story_drafts.accept_draft(draft.id)
        accepted = self.services.story_drafts.accepted_draft(self.scene_id)
        self.assertEqual(accepted.id, draft.id)
        self.assertEqual(accepted.prose_text, draft.prose_text)
        markdown, json_text = self.services.story_exports.build(
            project_id=self.project_id,
            mode=StoryExportMode.PROSE,
            outline_id=self.outline_id,
        )
        self.assertIn(draft.prose_text, markdown)
        export = self.services.story_exports.list_exports(self.project_id)[0]
        reproduced = self.services.story_exports.rebuild_from_export(
            str(export["id"]), expected_project_id=self.project_id
        )
        self.assertEqual(reproduced.markdown, markdown)
        self.assertEqual(reproduced.json_text, json_text)

    def test_provider_refusal_never_creates_a_draft(self) -> None:
        class RefusingProvider(MockProvider):
            def generate_text(
                self, request: GenerationRequest, *, cancel: Event | None = None
            ) -> GenerationResult:
                self.requests.append(request)
                raise ProviderRefusalError("Simulated provider refusal.")

        provider = RefusingProvider()
        service = StoryGenerationOrchestrationService(
            self.services.lifecycle, provider=provider, provider_name="mock"
        )
        outcome = service.generate_scene(
            GenerateSceneRequest(scene_id=self.scene_id, model="offline-test")
        )
        self.assertEqual(outcome.status.value, "failed")
        self.assertIsNone(outcome.draft)
        self.assertIsNone(outcome.partial_draft)
        self.assertEqual(len(provider.requests), 1)

    def test_context_reconstructs_generation_and_revision_when_raw_storage_is_disabled(self) -> None:
        settings = self.settings.model_copy(update={"store_rendered_generation_messages": False})
        provider = MockProvider(text_responses=[
            "Rain tapped on the station roof. The last train left the platform. "
            "The old clock struck midnight, and the lights faded in the quiet hall.",
            "Rain drummed softly on the station roof as the last train left the platform. "
            "At midnight, the old clock sounded and the quiet hall grew dim.",
        ])
        generated = StoryGenerationOrchestrationService(
            self.services.lifecycle, provider=provider, provider_name="mock", settings=settings
        ).generate_scene(
            GenerateSceneRequest(
                scene_id=self.scene_id,
                model="offline-test",
                length_spec=GenerationLengthSpec(
                    unit=LengthUnit.WORDS,
                    minimum=10,
                    target=20,
                    maximum=100,
                    segment_target=None,
                    max_continuations=0,
                    strictness=LengthStrictness.BOUNDED,
                ),
            )
        )
        self.assertEqual(generated.status.value, "completed")
        self.assertIsNotNone(generated.draft)
        revised = StoryRevisionOrchestrationService(
            self.services.lifecycle, provider=provider, provider_name="mock", settings=settings
        ).revise_scene(
            ReviseSceneRequest(
                draft_id=generated.draft.id,
                model="offline-test",
                revision=RevisionRequest(operation=RevisionOperation.POLISH_PROSE),
            )
        )
        self.assertEqual(revised.status.value, "completed")
        self.assertEqual(len(provider.requests), 2)
        for outcome, request in zip((generated, revised), provider.requests, strict=True):
            with self.subTest(run_id=outcome.run_id):
                with self.services.lifecycle() as session:
                    run = GenerationRunRepository(session).get(outcome.run_id)
                self.assertFalse(run.rendered_message_storage_enabled)
                self.assertIsNone(run.rendered_system_message)
                self.assertIsNone(run.rendered_user_message)
                inspection = self.services.story_context.inspect_generation_run(
                    outcome.run_id, expected_project_id=self.project_id
                )
                self.assertEqual(
                    inspection.display_mode, HistoricalContextDisplayMode.RECONSTRUCTED_MATCH
                )
                self.assertTrue(inspection.reconstruction_matches)
                self.assertEqual(inspection.system_message, request.system)
                self.assertEqual(inspection.user_message, request.prompt)
                self.assertEqual(
                    run.system_message_sha256,
                    hashlib.sha256(inspection.system_message.encode("utf-8")).hexdigest(),
                )
                self.assertEqual(
                    run.user_message_sha256,
                    hashlib.sha256(inspection.user_message.encode("utf-8")).hexdigest(),
                )

    def test_screenplay_acceptance_preserves_the_source_story(self) -> None:
        draft = self._generate_story()
        self.services.story_drafts.accept_draft(draft.id)
        screenplay = (
            "EXT. STATION - NIGHT\n\n"
            + "Rain taps on the station roof as the last train leaves the empty platform. " * 300
            + "\n\nFADE OUT."
        )
        provider = MockProvider(text_responses=[screenplay])
        service = ScreenplayGenerationService(
            self.services.lifecycle,
            provider=provider,
            provider_name="ollama",
        )
        outcome = service.generate(
            GenerateScreenplayRequest(
                scene_id=self.scene_id,
                model="offline-test",
                length_spec=GenerationLengthSpec(
                    unit=LengthUnit.SCREENPLAY_PAGES,
                    minimum=1,
                    target=5,
                    maximum=50,
                    segment_target=None,
                    max_continuations=0,
                    strictness=LengthStrictness.BOUNDED,
                ),
            )
        )
        self.assertEqual(outcome.run.status, "succeeded")
        self.assertIsNotNone(outcome.revision)
        self.assertEqual(outcome.revision.screenplay_text, screenplay.strip())
        self.assertEqual(len(provider.requests), 1)
        sent_system = provider.requests[0].system
        self.assertEqual(outcome.run.rendered_system_message, sent_system)
        self.assertEqual(
            outcome.run.system_message_sha256,
            hashlib.sha256(sent_system.encode("utf-8")).hexdigest(),
        )
        accepted = self.services.screenplay_adaptations.accept_revision(
            outcome.adaptation.id,
            outcome.revision.id,
            expected_accepted_revision_id=None,
        )
        self.assertEqual(accepted.adaptation.accepted_revision_id, outcome.revision.id)
        source = self.services.story_drafts.accepted_draft(self.scene_id)
        self.assertEqual(source.id, draft.id)
        self.assertEqual(source.prose_text, draft.prose_text)

    def test_backup_restore_and_reopen_preserve_saved_work(self) -> None:
        draft = self._generate_story()
        self.services.story_drafts.accept_draft(draft.id)
        manifest = self.services.backups.create_backup()
        backup_path = Path(manifest.backup_path)
        self.assertEqual(manifest.integrity_check, "ok")
        self.assertTrue(backup_path.with_suffix(".manifest.json").is_file())
        self.assertTrue(self.services.backups.verify_backup(backup_path))

        later = self.services.projects.create_project(name="Work created after backup")
        report = self.services.backups.restore_backup(backup_path, confirm=True)
        self.assertEqual(report.status, "succeeded")
        self.assertTrue(Path(report.safety_backup_path).is_file())
        self.assertNotIn(later.id, {item.id for item in self.services.projects.list_projects()})
        restored = self.services.story_drafts.accepted_draft(self.scene_id)
        self.assertEqual(restored.prose_text, draft.prose_text)

        self.services.lifecycle.dispose()
        self.services = build_services(self.settings, credential_environment={})
        reopened = self.services.story_drafts.accepted_draft(self.scene_id)
        self.assertEqual(reopened.id, draft.id)
        self.assertEqual(reopened.prose_text, draft.prose_text)


if __name__ == "__main__":
    unittest.main()
