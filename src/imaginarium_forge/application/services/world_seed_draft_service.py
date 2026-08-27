"""CRUD boundary for project-optional world seed drafts."""

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
from imaginarium_forge.domain.creative.world_seed_draft import (
    WorldSeedDraft,
    WorldSeedGenerationMode,
)
from imaginarium_forge.infrastructure.db.repositories.projects import ProjectRepository
from imaginarium_forge.infrastructure.db.repositories.world_seed_drafts import (
    WorldSeedDraftRepository,
)

EXPORT_SCHEMA_VERSION = "world-seed-draft-v1"


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


def _persisted_fields(draft: WorldSeedDraft) -> dict[str, object]:
    return {
        "project_id": draft.project_id,
        "title": draft.title,
        "setting": draft.setting,
        "time_period": draft.time_period,
        "world_rules_json": canonical_json(list(draft.world_rules)),
        "locations_json": canonical_json(list(draft.locations)),
        "social_context": draft.social_context,
        "technology_or_magic": draft.technology_or_magic,
        "central_conflict": draft.central_conflict,
        "themes_json": canonical_json(list(draft.themes)),
        "generation_mode": draft.generation_mode.value,
        "generation_seed": draft.generation_seed,
        "updated_at": draft.updated_at,
    }


class WorldSeedDraftService(ServiceBase):
    """Create and revise early world material without asserting World Bible Canon."""

    def create_draft(
        self,
        *,
        title: str,
        project_id: str | None = None,
        setting: str = "",
        time_period: str = "",
        world_rules: tuple[str, ...] | list[str] = (),
        locations: tuple[str, ...] | list[str] = (),
        social_context: str = "",
        technology_or_magic: str = "",
        central_conflict: str = "",
        themes: tuple[str, ...] | list[str] = (),
        generation_mode: WorldSeedGenerationMode | str = WorldSeedGenerationMode.MANUAL,
        generation_seed: str = "",
    ) -> WorldSeedDraft:
        now = utc_now_iso()
        try:
            draft = WorldSeedDraft.model_validate(
                {
                    "id": new_id(),
                    "project_id": project_id,
                    "title": title,
                    "setting": setting,
                    "time_period": time_period,
                    "world_rules": world_rules,
                    "locations": locations,
                    "social_context": social_context,
                    "technology_or_magic": technology_or_magic,
                    "central_conflict": central_conflict,
                    "themes": themes,
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
            WorldSeedDraftRepository(session).add(draft)
        return self.get_draft(draft.id)

    def get_draft(self, draft_id: str) -> WorldSeedDraft:
        draft = self._read_only(lambda session: WorldSeedDraftRepository(session).get(draft_id))
        if draft is None:
            raise NotFoundError(f"找不到世界種子草稿：{draft_id}")
        return draft

    def list_drafts(
        self,
        *,
        project_id: str | None = None,
        standalone_only: bool = False,
        include_archived: bool = False,
    ) -> list[WorldSeedDraft]:
        if standalone_only and project_id is not None:
            raise ValidationFailedError("project_id 與 standalone_only 不可同時指定")
        return self._read_only(
            lambda session: WorldSeedDraftRepository(session).list_all(
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
        setting: str | None = None,
        time_period: str | None = None,
        world_rules: tuple[str, ...] | list[str] | None = None,
        locations: tuple[str, ...] | list[str] | None = None,
        social_context: str | None = None,
        technology_or_magic: str | None = None,
        central_conflict: str | None = None,
        themes: tuple[str, ...] | list[str] | None = None,
        generation_mode: WorldSeedGenerationMode | str | None = None,
        generation_seed: str | None = None,
        expected_updated_at: str | None = None,
    ) -> WorldSeedDraft:
        current = self.get_draft(draft_id)
        if expected_updated_at is not None and current.updated_at != expected_updated_at:
            raise ConflictError("世界草稿已被其他編輯更新，未覆寫較新的內容")
        updates: dict[str, object] = {}
        if not isinstance(project_id, _UnsetProject):
            updates["project_id"] = project_id
        for field, value in (
            ("title", title),
            ("setting", setting),
            ("time_period", time_period),
            ("world_rules", world_rules),
            ("locations", locations),
            ("social_context", social_context),
            ("technology_or_magic", technology_or_magic),
            ("central_conflict", central_conflict),
            ("themes", themes),
            ("generation_mode", generation_mode),
            ("generation_seed", generation_seed),
        ):
            if value is not None:
                updates[field] = value
        if not updates:
            return current

        updates["updated_at"] = _next_updated_at(current.updated_at)
        try:
            revised = WorldSeedDraft.model_validate(
                {**current.model_dump(mode="python"), **updates}
            )
        except ValidationError as exc:
            raise _validation_failure(exc) from exc
        with self._transaction() as session:
            _require_project(session, revised.project_id)
            if not WorldSeedDraftRepository(session).update_active_cas(
                draft_id,
                expected_updated_at=current.updated_at,
                fields=_persisted_fields(revised),
            ):
                latest = WorldSeedDraftRepository(session).get(draft_id)
                if latest is None:
                    raise NotFoundError(f"找不到世界種子草稿：{draft_id}")
                raise ConflictError("世界草稿已被更新或封存，未覆寫較新的內容")
        return self.get_draft(draft_id)

    def archive_draft(self, draft_id: str) -> WorldSeedDraft:
        current = self.get_draft(draft_id)
        with self._transaction() as session:
            if not WorldSeedDraftRepository(session).update_active_cas(
                draft_id,
                expected_updated_at=current.updated_at,
                fields={
                    "status": RecordStatus.ARCHIVED.value,
                    "updated_at": _next_updated_at(current.updated_at),
                },
            ):
                latest = WorldSeedDraftRepository(session).get(draft_id)
                if latest is None:
                    raise NotFoundError(f"找不到世界種子草稿：{draft_id}")
                raise ConflictError("世界草稿已被更新或封存，未重複封存")
        return self.get_draft(draft_id)

    def export_draft_json(self, draft_id: str) -> str:
        """Return the deterministic UTF-8 JSON handoff for one exact readback."""

        draft = self.get_draft(draft_id)
        return canonical_json(
            {
                "schema_version": EXPORT_SCHEMA_VERSION,
                **draft.model_dump(mode="json"),
            }
        )


__all__ = ["EXPORT_SCHEMA_VERSION", "WorldSeedDraftService"]
