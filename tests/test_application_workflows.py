"""Offline integration checks for navigation and versioned prompt workflows.

Run with: python -m unittest discover -s tests -v
No generation provider is invoked. All test data lives in a temporary directory.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from streamlit.testing.v1 import AppTest

from imaginarium_forge.config.settings import AppSettings
from imaginarium_forge.domain.character.gender import CharacterGender
from imaginarium_forge.domain.character.version import VisualDNA
from imaginarium_forge.domain.common.enums import EligibilityReason, RequestType
from imaginarium_forge.domain.eligibility.projection import EligibilityRequestProjection
from imaginarium_forge.domain.eligibility.validator import RequestIntegrityValidator
from imaginarium_forge.domain.prompt.ast import EnvironmentNode, PromptAST, SubjectNode
from imaginarium_forge.domain.prompt.input_snapshot import PromptVersionSelectionSnapshot
from imaginarium_forge.ui.bootstrap import build_services
from imaginarium_forge.ui.navigation import ROUTE_LABELS

ROOT = Path(__file__).resolve().parents[1]


class ApplicationWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.scratch = tempfile.TemporaryDirectory(prefix="forge-application-")
        cls.data_dir = Path(cls.scratch.name)
        cls.previous_data = os.environ.get("IMF_DATA_DIR")
        cls.previous_vault = os.environ.get("IMF_DEVICE_CREDENTIAL_STORE_ENABLED")
        os.environ["IMF_DATA_DIR"] = str(cls.data_dir)
        os.environ["IMF_DEVICE_CREDENTIAL_STORE_ENABLED"] = "false"
        cls.services = build_services(
            AppSettings(data_dir=cls.data_dir, device_credential_store_enabled=False),
            credential_environment={},
        )
        cls.project = cls.services.projects.create_project(name="圖書館奇幻故事")
        cls.character = cls.services.characters.create_original_character(
            project_id=cls.project.id, name="旅行圖書館員", explicit_age=25
        )
        cls.version = cls.services.versions.create_version(
            character_id=cls.character.id,
            visual_dna=VisualDNA(
                gender=CharacterGender.FEMALE,
                hair="short brown hair",
                eyes="green eyes",
                identity="traveling librarian wearing a long coat",
            ),
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.services.lifecycle.dispose()
        for key, value in (
            ("IMF_DATA_DIR", cls.previous_data),
            ("IMF_DEVICE_CREDENTIAL_STORE_ENABLED", cls.previous_vault),
        ):
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        cls.scratch.cleanup()

    def test_all_routes_with_a_selected_project(self) -> None:
        for route in ROUTE_LABELS:
            with self.subTest(route=route):
                at = AppTest.from_file(ROOT / "src/imaginarium_forge/main.py", default_timeout=30)
                at.query_params["route"] = route
                at.session_state["selected_project_id"] = self.project.id
                try:
                    at.run()
                    self.assertEqual([item.message for item in at.exception], [])
                    self.assertEqual([item.value for item in at.error], [])
                    self.assertEqual(at.session_state["active_nav_choice"], route)
                finally:
                    if "services" in at.session_state:
                        at.session_state["services"].lifecycle.dispose()

    def test_general_prompt_resolution_and_compilation(self) -> None:
        ast = PromptAST(
            subjects=(SubjectNode(
                character_id=self.character.id,
                character_version_id=self.version.id,
                canonical_features=("librarian",),
                pose="standing",
            ),),
            environment=EnvironmentNode(location="a quiet library"),
        )
        resolution = self.services.prompt_resolution.resolve(
            ast=ast,
            character_id=self.character.id,
            character_version_id=self.version.id,
        )
        outcome = self.services.prompt_compilation.compile(
            ast=ast,
            resolution=resolution,
            dialect_id="generic_tag_anime",
            character_id=self.character.id,
            character_version_id=self.version.id,
        )
        self.assertIn("library", outcome.blocks.positive_prompt)
        self.assertIn("brown hair", outcome.blocks.positive_prompt)
        self.assertTrue(outcome.input_fingerprint)
        prompt_project = self.services.prompt_projects.create(
            project_id=self.project.id,
            title="圖書館員立繪",
            character_id=self.character.id,
            character_version_id=self.version.id,
        )
        selection = PromptVersionSelectionSnapshot(
            project_id=self.project.id,
            character_id=self.character.id,
            character_version_id=self.version.id,
        )
        version = self.services.prompt_projects.add_version(
            prompt_project.id,
            ast=ast,
            selection_snapshot=selection,
            input_fingerprint=outcome.input_fingerprint,
        )
        variant = self.services.prompt_compilation.persist_variant(
            prompt_project_id=prompt_project.id,
            prompt_project_version_id=version.id,
            outcome=outcome,
        )
        bundle = self.services.prompt_export.build_bundle(
            variant.id, expected_project_id=self.project.id
        )
        self.assertIn("library", str(bundle))

    def test_character_version_ownership_is_required(self) -> None:
        validator = RequestIntegrityValidator()
        projection = EligibilityRequestProjection(
            request_type=RequestType.IMAGE_PROMPT_COMPILE,
            character_id=self.character.id,
            character_version_id=self.version.id,
        )
        allowed = validator.evaluate(self.character, self.version, projection)
        self.assertTrue(allowed.allowed)
        self.assertEqual(allowed.reason_code, EligibilityReason.ALLOWED)
        self.assertEqual(allowed.input_fingerprint, projection.fingerprint())
        other_character = self.character.model_copy(update={"id": "another-character"})
        other_version = self.version.model_copy(update={"id": "another-version"})
        for character, version in (
            (other_character, self.version),
            (self.character, other_version),
            (self.character, None),
        ):
            with self.subTest(character=character.id, version=version):
                result = validator.evaluate(character, version, projection)
                self.assertFalse(result.allowed)
                self.assertEqual(result.reason_code, EligibilityReason.INVALID_CHARACTER_VERSION)


if __name__ == "__main__":
    unittest.main()
