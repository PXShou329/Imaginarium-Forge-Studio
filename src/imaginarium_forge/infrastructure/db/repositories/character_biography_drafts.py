"""Persistence for standalone character-biography drafts."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from imaginarium_forge.domain.character.biography_draft import (
    CharacterBiographyDraft,
)
from imaginarium_forge.domain.character.gender import CharacterGender
from imaginarium_forge.domain.common.enums import RecordStatus
from imaginarium_forge.infrastructure.db.models.orm import (
    CharacterBiographyDraftRow,
)


def _to_domain(row: CharacterBiographyDraftRow) -> CharacterBiographyDraft:
    return CharacterBiographyDraft(
        id=row.id,
        project_id=row.project_id,
        title=row.title,
        character_name=row.character_name,
        gender=CharacterGender(row.gender),
        character_details=row.character_details,
        character_image_prompt_en=row.character_image_prompt_en,
        personal_story=row.personal_story,
        background_image_prompt_en=row.background_image_prompt_en,
        notes=row.notes,
        status=RecordStatus(row.status),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class CharacterBiographyDraftRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, draft: CharacterBiographyDraft) -> None:
        self._session.add(
            CharacterBiographyDraftRow(
                id=draft.id,
                project_id=draft.project_id,
                title=draft.title,
                character_name=draft.character_name,
                gender=draft.gender.value,
                character_details=draft.character_details,
                character_image_prompt_en=draft.character_image_prompt_en,
                personal_story=draft.personal_story,
                background_image_prompt_en=draft.background_image_prompt_en,
                notes=draft.notes,
                status=draft.status.value,
                created_at=draft.created_at,
                updated_at=draft.updated_at,
            )
        )

    def get(self, draft_id: str) -> CharacterBiographyDraft | None:
        row = self._session.get(CharacterBiographyDraftRow, draft_id)
        return _to_domain(row) if row is not None else None

    def list_all(
        self,
        *,
        project_id: str | None = None,
        standalone_only: bool = False,
        include_archived: bool = False,
    ) -> list[CharacterBiographyDraft]:
        stmt = select(CharacterBiographyDraftRow)
        if standalone_only:
            stmt = stmt.where(CharacterBiographyDraftRow.project_id.is_(None))
        elif project_id is not None:
            stmt = stmt.where(CharacterBiographyDraftRow.project_id == project_id)
        if not include_archived:
            stmt = stmt.where(
                CharacterBiographyDraftRow.status == RecordStatus.ACTIVE.value
            )
        stmt = stmt.order_by(
            CharacterBiographyDraftRow.updated_at.desc(),
            CharacterBiographyDraftRow.created_at.desc(),
            CharacterBiographyDraftRow.id,
        )
        return [_to_domain(row) for row in self._session.scalars(stmt).all()]

    def update_fields(self, draft_id: str, **fields: str | None) -> bool:
        row = self._session.get(CharacterBiographyDraftRow, draft_id)
        if row is None:
            return False
        for key, value in fields.items():
            setattr(row, key, value)
        return True


__all__ = ["CharacterBiographyDraftRepository"]
