"""Database integrity checks using temporary SQLite files and real services."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from sqlalchemy.exc import IntegrityError

from imaginarium_forge.application.services.character_service import (
    CharacterService,
    CharacterVersionService,
)
from imaginarium_forge.application.services.project_service import ProjectService
from imaginarium_forge.domain.character.model import Character
from imaginarium_forge.domain.character.version import CharacterVersion
from imaginarium_forge.infrastructure.db.models.orm import Base
from imaginarium_forge.infrastructure.db.repositories.eligibility_audit import (
    EligibilityAuditRecord,
    EligibilityAuditRepository,
)
from imaginarium_forge.infrastructure.db.session import (
    UnsupportedDatabaseError,
    create_db_engine,
    create_session_factory,
    downgrade_to_base,
    initialize_database,
    upgrade_to_head,
)


class DatabaseSchemaChecks(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="forge-database-check-")
        self.path = Path(self.temporary.name) / "project.db"
        initialize_database(self.path)
        self.engine = create_db_engine(self.path)
        self.sessions = create_session_factory(self.engine)

    def tearDown(self) -> None:
        self.engine.dispose()
        self.temporary.cleanup()

    def _character(self, *, name: str = "Archivist") -> tuple[Character, CharacterVersion]:
        project = ProjectService(self.sessions).create_project(name="Library")
        character = CharacterService(self.sessions).create_original_character(
            project_id=project.id, name=name
        )
        version = CharacterVersionService(self.sessions).create_version(
            character_id=character.id, design_note="A weathered coat and a brass compass."
        )
        return character, version

    def test_fresh_schema_matches_runtime_models_and_has_valid_foreign_keys(self) -> None:
        with self.engine.connect() as connection:
            tables = {
                row[0] for row in connection.exec_driver_sql(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            } - {"alembic_version"}
            self.assertEqual(tables, set(Base.metadata.tables) | {"story_scene_revisions"})
            for name, table in Base.metadata.tables.items():
                columns = {
                    row[1] for row in connection.exec_driver_sql(f'PRAGMA table_info("{name}")')
                }
                self.assertEqual(columns, set(table.columns.keys()), name)
            self.assertEqual(connection.exec_driver_sql("PRAGMA integrity_check").scalar(), "ok")
            self.assertEqual(connection.exec_driver_sql("PRAGMA foreign_key_check").all(), [])

    def test_empty_database_can_rollback_and_reinitialize(self) -> None:
        self.engine.dispose()
        self.assertIsNone(downgrade_to_base(self.path).current_revision)
        self.assertTrue(initialize_database(self.path).is_current)
        self.assertTrue(upgrade_to_head(self.path).is_current)

    def test_existing_empty_file_initializes(self) -> None:
        empty = Path(self.temporary.name) / "empty.db"
        empty.touch()
        self.assertTrue(initialize_database(empty).is_current)

    def test_design_note_round_trip_preserves_version_history(self) -> None:
        character, first = self._character()
        versions = CharacterVersionService(self.sessions)
        second = versions.create_version(
            character_id=character.id, design_note="A blue scarf and a travel journal."
        )
        self.assertEqual(versions.get_version(first.id).design_note, first.design_note)
        self.assertEqual(versions.get_version(second.id).design_note, second.design_note)
        self.assertEqual(second.version_number, first.version_number + 1)
        self.assertEqual(
            CharacterService(self.sessions).get_character(character.id).current_version_id,
            second.id,
        )

    def test_current_version_must_belong_to_its_character(self) -> None:
        character, _ = self._character()
        _, other_version = self._character(name="Cartographer")
        with self.assertRaises(IntegrityError):
            with self.engine.begin() as connection:
                connection.exec_driver_sql(
                    "UPDATE characters SET current_version_id = ? WHERE id = ?",
                    (other_version.id, character.id),
                )

    def test_request_validation_audit_is_append_only(self) -> None:
        character, version = self._character()
        record = EligibilityAuditRecord(
            id="validation-record", character_id=character.id,
            character_version_id=version.id, requested_character_version_id=version.id,
            request_type="story_generation", allowed=True, reason_code="allowed",
            validator_version="request-integrity-v1", input_fingerprint="a" * 64,
            details_json="{}", evaluated_at=version.created_at,
        )
        with self.sessions.begin() as session:
            EligibilityAuditRepository(session).add(record)
        for statement in (
            "UPDATE character_validations SET allowed = 0 WHERE id = 'validation-record'",
            "DELETE FROM character_validations WHERE id = 'validation-record'",
        ):
            with self.assertRaises(IntegrityError):
                with self.engine.begin() as connection:
                    connection.exec_driver_sql(statement)
        with self.sessions() as session:
            self.assertEqual(
                EligibilityAuditRepository(session).list_for_character(character.id), [record]
            )

    def test_incompatible_database_is_rejected_without_modification(self) -> None:
        path = Path(self.temporary.name) / "incompatible.db"
        with closing(sqlite3.connect(path)) as connection:
            with connection:
                connection.execute("CREATE TABLE alembic_version (version_num TEXT)")
                connection.execute("INSERT INTO alembic_version VALUES ('unrecognized_revision')")
                connection.execute("CREATE TABLE notes (content TEXT)")
                connection.execute("INSERT INTO notes VALUES ('preserved')")
        original = path.read_bytes()
        for operation in (initialize_database, upgrade_to_head, downgrade_to_base):
            with self.assertRaisesRegex(UnsupportedDatabaseError, "IMF_DATA_DIR"):
                operation(path)
            self.assertEqual(path.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
