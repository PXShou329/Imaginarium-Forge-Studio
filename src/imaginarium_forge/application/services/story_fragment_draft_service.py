"""CRUD boundary for project-optional standalone story fragments."""

from __future__ import annotations

from datetime import datetime, timedelta

from pydantic import ValidationError
from sqlalchemy.orm import Session

from imaginarium_forge.application.errors import (
    ConflictError,
    NotFoundError,
    ValidationFailedError,
)
from imaginarium_forge.application.services.base import ServiceBase
from imaginarium_forge.canonical import canonical_json
from imaginarium_forge.domain.common.enums import RecordStatus
from imaginarium_forge.domain.common.ids import new_id, utc_now_iso
from imaginarium_forge.domain.story.fragment_draft import (
    StoryFragmentDraft,
    StoryFragmentGenerationMode,
    StoryFragmentKind,
)
from imaginarium_forge.infrastructure.db.repositories.projects import ProjectRepository
from imaginarium_forge.infrastructure.db.repositories.story_fragment_drafts import (
    StoryFragmentDraftRepository,
)

EXPORT_SCHEMA_VERSION = "story-fragment-draft-v1"


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


def _next_updated_at(base_updated_at: str) -> str:
    candidate = utc_now_iso()
    try:
        base = datetime.fromisoformat(base_updated_at)
        current = datetime.fromisoformat(candidate)
    except ValueError:
        return candidate if candidate != base_updated_at else f"{base_updated_at}:next"
    return (base + timedelta(microseconds=1)).isoformat() if current <= base else candidate


def _persisted_fields(draft: StoryFragmentDraft) -> dict[str, object]:
    return {
        "project_id": draft.project_id,
        "title": draft.title,
        "fragment_kind": draft.fragment_kind.value,
        "fragment_text": draft.fragment_text,
        "context_notes": draft.context_notes,
        "tags_json": canonical_json(list(draft.tags)),
        "generation_mode": draft.generation_mode.value,
        "generation_seed": draft.generation_seed,
        "updated_at": draft.updated_at,
    }


class StoryFragmentDraftService(ServiceBase):
    """Create and revise fragment scratch-space without creating Story state."""

    def create_draft(
        self,
        *,
        title: str,
        fragment_text: str,
        project_id: str | None = None,
        fragment_kind: StoryFragmentKind | str = StoryFragmentKind.NARRATIVE,
        context_notes: str = "",
        tags: tuple[str, ...] | list[str] = (),
        generation_mode: StoryFragmentGenerationMode | str = StoryFragmentGenerationMode.MANUAL,
        generation_seed: str = "",
    ) -> StoryFragmentDraft:
        now = utc_now_iso()
        try:
            draft = StoryFragmentDraft.model_validate(
                {
                    "id": new_id(),
                    "project_id": project_id,
                    "title": title,
                    "fragment_kind": fragment_kind,
                    "fragment_text": fragment_text,
                    "context_notes": context_notes,
                    "tags": tags,
                    "generation_mode": generation_mode,
                    "generation_seed": generation_seed,
                    "created_at": now,
                    "updated_at": now,
                }
            )
        except ValidationError as exc:
            raise _validation_failure(exc) from exc
        with self._transaction() as session:
            _require_project(session, draft.project_id)
            StoryFragmentDraftRepository(session).add(draft)
        return self.get_draft(draft.id)

    def get_draft(self, draft_id: str) -> StoryFragmentDraft:
        draft = self._read_only(lambda session: StoryFragmentDraftRepository(session).get(draft_id))
        if draft is None:
            raise NotFoundError(f"找不到故事片段草稿：{draft_id}")
        return draft

    def list_drafts(
        self,
        *,
        project_id: str | None = None,
        standalone_only: bool = False,
        include_archived: bool = False,
    ) -> list[StoryFragmentDraft]:
        if standalone_only and project_id is not None:
            raise ValidationFailedError("project_id 與 standalone_only 不可同時指定")
        return self._read_only(
            lambda session: StoryFragmentDraftRepository(session).list_all(
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
        fragment_kind: StoryFragmentKind | str | None = None,
        fragment_text: str | None = None,
        context_notes: str | None = None,
        tags: tuple[str, ...] | list[str] | None = None,
        generation_mode: StoryFragmentGenerationMode | str | None = None,
        generation_seed: str | None = None,
        expected_updated_at: str | None = None,
    ) -> StoryFragmentDraft:
        current = self.get_draft(draft_id)
        if expected_updated_at is not None and current.updated_at != expected_updated_at:
            raise ConflictError("故事片段已被其他編輯更新，未覆寫較新的內容")
        updates: dict[str, object] = {}
        if not isinstance(project_id, _UnsetProject):
            updates["project_id"] = project_id
        for field, value in (
            ("title", title),
            ("fragment_kind", fragment_kind),
            ("fragment_text", fragment_text),
            ("context_notes", context_notes),
            ("tags", tags),
            ("generation_mode", generation_mode),
            ("generation_seed", generation_seed),
        ):
            if value is not None:
                updates[field] = value
        if not updates:
            return current

        updates["updated_at"] = _next_updated_at(current.updated_at)
        try:
            revised = StoryFragmentDraft.model_validate(
                {**current.model_dump(mode="python"), **updates}
            )
        except ValidationError as exc:
            raise _validation_failure(exc) from exc
        with self._transaction() as session:
            _require_project(session, revised.project_id)
            if not StoryFragmentDraftRepository(session).update_active_cas(
                draft_id,
                expected_updated_at=current.updated_at,
                fields=_persisted_fields(revised),
            ):
                latest = StoryFragmentDraftRepository(session).get(draft_id)
                if latest is None:
                    raise NotFoundError(f"找不到故事片段草稿：{draft_id}")
                raise ConflictError("故事片段已被更新或封存，未覆寫較新的內容")
        return self.get_draft(draft_id)

    def archive_draft(
        self,
        draft_id: str,
        *,
        expected_updated_at: str | None = None,
    ) -> StoryFragmentDraft:
        current = self.get_draft(draft_id)
        if expected_updated_at is not None and current.updated_at != expected_updated_at:
            raise ConflictError("故事片段已被其他編輯更新，未封存較新的內容")
        with self._transaction() as session:
            if not StoryFragmentDraftRepository(session).update_active_cas(
                draft_id,
                expected_updated_at=current.updated_at,
                fields={
                    "status": RecordStatus.ARCHIVED.value,
                    "updated_at": _next_updated_at(current.updated_at),
                },
            ):
                latest = StoryFragmentDraftRepository(session).get(draft_id)
                if latest is None:
                    raise NotFoundError(f"找不到故事片段草稿：{draft_id}")
                raise ConflictError("故事片段已被更新或封存，未重複封存")
        return self.get_draft(draft_id)

    def export_draft_json(self, draft_id: str) -> str:
        """Return deterministic UTF-8 JSON for one exact persisted fragment."""

        draft = self.get_draft(draft_id)
        return canonical_json(
            {
                "schema_version": EXPORT_SCHEMA_VERSION,
                **draft.model_dump(mode="json"),
            }
        )


__all__ = ["EXPORT_SCHEMA_VERSION", "StoryFragmentDraftService"]
