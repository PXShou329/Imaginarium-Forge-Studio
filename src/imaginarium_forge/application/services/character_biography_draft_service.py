"""CRUD application boundary for standalone character-biography drafts."""

from __future__ import annotations

from pydantic import ValidationError
from sqlalchemy.orm import Session

from imaginarium_forge.application.errors import NotFoundError, ValidationFailedError
from imaginarium_forge.application.services.base import ServiceBase
from imaginarium_forge.domain.character.biography_draft import (
    CharacterBiographyDraft,
)
from imaginarium_forge.domain.character.gender import CharacterGender
from imaginarium_forge.domain.common.enums import RecordStatus
from imaginarium_forge.domain.common.ids import new_id, utc_now_iso
from imaginarium_forge.infrastructure.db.repositories.character_biography_drafts import (
    CharacterBiographyDraftRepository,
)
from imaginarium_forge.infrastructure.db.repositories.projects import ProjectRepository


class _UnsetProject:
    __slots__ = ()


_UNSET_PROJECT = _UnsetProject()


def _validation_failure(exc: ValidationError) -> ValidationFailedError:
    errors = exc.errors()
    message = str(errors[0].get("msg", exc)) if errors else str(exc)
    return ValidationFailedError(message)


def _require_project(session: Session, project_id: str | None) -> None:
    if project_id is not None and ProjectRepository(session).get(project_id) is None:
        raise NotFoundError(f"找不到專案：{project_id}")


class CharacterBiographyDraftService(ServiceBase):
    """Create and edit character biographies without requiring a project."""

    def create_draft(
        self,
        *,
        title: str,
        character_name: str,
        gender: CharacterGender | str,
        project_id: str | None = None,
        character_details: str = "",
        character_image_prompt_en: str = "",
        personal_story: str = "",
        background_image_prompt_en: str = "",
        notes: str = "",
    ) -> CharacterBiographyDraft:
        now = utc_now_iso()
        try:
            draft = CharacterBiographyDraft.model_validate(
                {
                    "id": new_id(),
                    "project_id": project_id,
                    "title": title,
                    "character_name": character_name,
                    "gender": gender,
                    "character_details": character_details,
                    "character_image_prompt_en": character_image_prompt_en,
                    "personal_story": personal_story,
                    "background_image_prompt_en": background_image_prompt_en,
                    "notes": notes,
                    "created_at": now,
                    "updated_at": now,
                }
            )
        except ValidationError as exc:
            raise _validation_failure(exc) from exc
        with self._transaction() as session:
            _require_project(session, draft.project_id)
            CharacterBiographyDraftRepository(session).add(draft)
        return draft

    def get_draft(self, draft_id: str) -> CharacterBiographyDraft:
        draft = self._read_only(
            lambda session: CharacterBiographyDraftRepository(session).get(draft_id)
        )
        if draft is None:
            raise NotFoundError(f"找不到角色小傳草稿：{draft_id}")
        return draft

    def list_drafts(
        self,
        *,
        project_id: str | None = None,
        standalone_only: bool = False,
        include_archived: bool = False,
    ) -> list[CharacterBiographyDraft]:
        if standalone_only and project_id is not None:
            raise ValidationFailedError("project_id 與 standalone_only 不可同時指定")
        return self._read_only(
            lambda session: CharacterBiographyDraftRepository(session).list_all(
                project_id=project_id,
                standalone_only=standalone_only,
                include_archived=include_archived,
            )
        )

    def update_draft(
        self,
        draft_id: str,
        *,
        project_id: str | _UnsetProject | None = _UNSET_PROJECT,
        title: str | None = None,
        character_name: str | None = None,
        gender: CharacterGender | str | None = None,
        character_details: str | None = None,
        character_image_prompt_en: str | None = None,
        personal_story: str | None = None,
        background_image_prompt_en: str | None = None,
        notes: str | None = None,
    ) -> CharacterBiographyDraft:
        current = self.get_draft(draft_id)
        updates: dict[str, object] = {}
        if not isinstance(project_id, _UnsetProject):
            updates["project_id"] = project_id
        for field, value in (
            ("title", title),
            ("character_name", character_name),
            ("gender", gender),
            ("character_details", character_details),
            ("character_image_prompt_en", character_image_prompt_en),
            ("personal_story", personal_story),
            ("background_image_prompt_en", background_image_prompt_en),
            ("notes", notes),
        ):
            if value is not None:
                updates[field] = value
        if not updates:
            return current

        updates["updated_at"] = utc_now_iso()
        try:
            revised = CharacterBiographyDraft.model_validate(
                {**current.model_dump(mode="python"), **updates}
            )
        except ValidationError as exc:
            raise _validation_failure(exc) from exc

        with self._transaction() as session:
            _require_project(session, revised.project_id)
            persisted_fields = revised.model_dump(mode="json")
            for immutable_field in ("id", "created_at", "status"):
                persisted_fields.pop(immutable_field)
            if not CharacterBiographyDraftRepository(session).update_fields(
                draft_id, **persisted_fields
            ):
                raise NotFoundError(f"找不到角色小傳草稿：{draft_id}")
        return self.get_draft(draft_id)

    def archive_draft(self, draft_id: str) -> CharacterBiographyDraft:
        with self._transaction() as session:
            if not CharacterBiographyDraftRepository(session).update_fields(
                draft_id,
                status=RecordStatus.ARCHIVED.value,
                updated_at=utc_now_iso(),
            ):
                raise NotFoundError(f"找不到角色小傳草稿：{draft_id}")
        return self.get_draft(draft_id)


__all__ = ["CharacterBiographyDraftService"]
