"""Character and character-version repositories.

Immutability is structural here: there is NO update method for versions. A
version, once written, is only ever read. "Editing" a character means the
service writes a NEW version row and then switches current_version_id.
"""

from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from imaginarium_forge.domain.character.model import (
    AgeStatus,
    Character,
    CharacterProfile,
    OriginalityReview,
    SourceMetadata,
)
from imaginarium_forge.domain.character.version import (
    AdultPresentation,
    CharacterVersion,
    VisualDNA,
)
from imaginarium_forge.domain.common.enums import CharacterOrigin, RecordStatus
from imaginarium_forge.infrastructure.db.models.orm import CharacterRow, CharacterVersionRow


def _character_to_domain(row: CharacterRow) -> Character:
    review_raw = json.loads(row.originality_review_json) if row.originality_review_json else None
    source_raw = json.loads(row.source_metadata_json) if row.source_metadata_json else None
    return Character(
        id=row.id,
        project_id=row.project_id,
        name=row.name,
        character_origin=CharacterOrigin(row.character_origin),
        current_version_id=row.current_version_id,
        age_status=AgeStatus.model_validate_json(row.age_status_json),
        originality_review=OriginalityReview.model_validate(review_raw) if review_raw else None,
        source_metadata=SourceMetadata.model_validate(source_raw) if source_raw else None,
        profile=CharacterProfile.model_validate_json(row.profile_json),
        status=RecordStatus(row.status),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _version_to_domain(row: CharacterVersionRow) -> CharacterVersion:
    return CharacterVersion(
        id=row.id,
        character_id=row.character_id,
        version_number=row.version_number,
        visual_dna=VisualDNA.model_validate_json(row.visual_dna_json),
        adult_presentation=AdultPresentation.model_validate_json(row.adult_presentation_json),
        voice_profile=row.voice_profile,
        personality_profile=row.personality_profile,
        change_note=row.change_note,
        created_at=row.created_at,
    )


class CharacterRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, character: Character) -> None:
        review = character.originality_review
        source = character.source_metadata
        self._session.add(
            CharacterRow(
                id=character.id,
                project_id=character.project_id,
                name=character.name,
                character_origin=character.character_origin.value,
                current_version_id=character.current_version_id,
                age_status_json=character.age_status.model_dump_json(),
                originality_review_json=review.model_dump_json() if review else None,
                source_metadata_json=source.model_dump_json() if source else None,
                profile_json=character.profile.model_dump_json(),
                status=character.status.value,
                created_at=character.created_at,
                updated_at=character.updated_at,
            )
        )

    def get(self, character_id: str) -> Character | None:
        row = self._session.get(CharacterRow, character_id)
        return _character_to_domain(row) if row else None

    def list_for_project(
        self, project_id: str, *, include_archived: bool = True
    ) -> list[Character]:
        stmt = (
            select(CharacterRow)
            .where(CharacterRow.project_id == project_id)
            .order_by(CharacterRow.created_at)
        )
        rows = self._session.scalars(stmt).all()
        characters = [_character_to_domain(r) for r in rows]
        if include_archived:
            return characters
        return [c for c in characters if c.status is RecordStatus.ACTIVE]

    def update_identity_fields(self, character_id: str, **fields: str | None) -> bool:
        """Update identity-level fields ONLY (never version/presentation data)."""
        row = self._session.get(CharacterRow, character_id)
        if row is None:
            return False
        for key, value in fields.items():
            setattr(row, key, value)
        return True

    # --- versions: add + read ONLY (no update — immutability) ---------------

    def add_version(self, version: CharacterVersion) -> None:
        self._session.add(
            CharacterVersionRow(
                id=version.id,
                character_id=version.character_id,
                version_number=version.version_number,
                visual_dna_json=version.visual_dna.model_dump_json(),
                adult_presentation_json=version.adult_presentation.model_dump_json(),
                voice_profile=version.voice_profile,
                personality_profile=version.personality_profile,
                change_note=version.change_note,
                created_at=version.created_at,
            )
        )

    def get_version(self, version_id: str) -> CharacterVersion | None:
        row = self._session.get(CharacterVersionRow, version_id)
        return _version_to_domain(row) if row else None

    def list_versions(self, character_id: str) -> list[CharacterVersion]:
        stmt = (
            select(CharacterVersionRow)
            .where(CharacterVersionRow.character_id == character_id)
            .order_by(CharacterVersionRow.version_number)
        )
        return [_version_to_domain(r) for r in self._session.scalars(stmt).all()]

    def next_version_number(self, character_id: str) -> int:
        existing = self.list_versions(character_id)
        return (max((v.version_number for v in existing), default=0)) + 1
