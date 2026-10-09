"""CRUD and export boundary for project-optional prompt scratch drafts."""

from __future__ import annotations

import json
from datetime import datetime, timedelta

from pydantic import ValidationError
from sqlalchemy.orm import Session

from imaginarium_forge.application.errors import (
    ConflictError,
    NotFoundError,
    ValidationFailedError,
)
from imaginarium_forge.application.services.base import ServiceBase
from imaginarium_forge.domain.character.gender import CharacterGender
from imaginarium_forge.domain.common.enums import RecordStatus
from imaginarium_forge.domain.common.ids import new_id, utc_now_iso
from imaginarium_forge.domain.prompt.scratch_draft import (
    PromptScratchDraft,
    PromptScratchEditorKind,
)
from imaginarium_forge.infrastructure.db.repositories.projects import ProjectRepository
from imaginarium_forge.infrastructure.db.repositories.prompt_scratch_drafts import (
    PromptScratchDraftRepository,
)

EXPORT_SCHEMA_VERSION = "prompt-scratch-draft-v1"


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


class PromptScratchDraftService(ServiceBase):
    """Keep image prompts without forcing a project or a character record."""

    def create_draft(
        self,
        *,
        project_id: str | None = None,
        title: str = "",
        character_name: str = "",
        character_image_prompt_en: str = "",
        background_image_prompt_en: str = "",
        notes: str = "",
        editor_kind: PromptScratchEditorKind | str = PromptScratchEditorKind.CHARACTER,
        editor_gender: CharacterGender | str = CharacterGender.FEMALE,
    ) -> PromptScratchDraft:
        now = utc_now_iso()
        try:
            draft = PromptScratchDraft.model_validate(
                {
                    "id": new_id(),
                    "project_id": project_id,
                    "title": title,
                    "character_name": character_name,
                    "character_image_prompt_en": character_image_prompt_en,
                    "background_image_prompt_en": background_image_prompt_en,
                    "notes": notes,
                    "editor_kind": editor_kind,
                    "editor_gender": editor_gender,
                    "created_at": now,
                    "updated_at": now,
                }
            )
        except ValidationError as exc:
            raise _validation_failure(exc) from exc
        with self._transaction() as session:
            _require_project(session, draft.project_id)
            PromptScratchDraftRepository(session).add(draft)
        return draft

    def get_draft(self, draft_id: str) -> PromptScratchDraft:
        draft = self._read_only(lambda session: PromptScratchDraftRepository(session).get(draft_id))
        if draft is None:
            raise NotFoundError(f"找不到提示詞草稿：{draft_id}")
        return draft

    def list_drafts(
        self,
        *,
        project_id: str | None = None,
        standalone_only: bool = False,
        include_archived: bool = False,
    ) -> list[PromptScratchDraft]:
        if standalone_only and project_id is not None:
            raise ValidationFailedError("project_id 與 standalone_only 不可同時指定")
        return self._read_only(
            lambda session: PromptScratchDraftRepository(session).list_all(
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
        character_image_prompt_en: str | None = None,
        background_image_prompt_en: str | None = None,
        notes: str | None = None,
        editor_kind: PromptScratchEditorKind | str | None = None,
        editor_gender: CharacterGender | str | None = None,
        expected_updated_at: str | None = None,
    ) -> PromptScratchDraft:
        current = self.get_draft(draft_id)
        if expected_updated_at is not None and current.updated_at != expected_updated_at:
            raise ConflictError("提示詞草稿已被其他編輯更新，未覆寫較新的內容")
        updates: dict[str, object] = {}
        if not isinstance(project_id, _UnsetProject):
            updates["project_id"] = project_id
        for field, value in (
            ("title", title),
            ("character_name", character_name),
            ("character_image_prompt_en", character_image_prompt_en),
            ("background_image_prompt_en", background_image_prompt_en),
            ("notes", notes),
            ("editor_kind", editor_kind),
            ("editor_gender", editor_gender),
        ):
            if value is not None:
                updates[field] = value
        if not updates:
            return current

        updates["updated_at"] = _next_updated_at(current.updated_at)
        try:
            revised = PromptScratchDraft.model_validate(
                {**current.model_dump(mode="python"), **updates}
            )
        except ValidationError as exc:
            raise _validation_failure(exc) from exc

        with self._transaction() as session:
            _require_project(session, revised.project_id)
            persisted_fields = revised.model_dump(mode="json")
            for immutable_field in ("id", "created_at", "status"):
                persisted_fields.pop(immutable_field)
            if not PromptScratchDraftRepository(session).update_active_cas(
                draft_id,
                expected_updated_at=current.updated_at,
                fields=persisted_fields,
            ):
                latest = PromptScratchDraftRepository(session).get(draft_id)
                if latest is None:
                    raise NotFoundError(f"找不到提示詞草稿：{draft_id}")
                raise ConflictError("提示詞草稿已被更新或收起，未覆寫較新的內容")
        return self.get_draft(draft_id)

    def archive_draft(self, draft_id: str) -> PromptScratchDraft:
        current = self.get_draft(draft_id)
        with self._transaction() as session:
            if not PromptScratchDraftRepository(session).update_active_cas(
                draft_id,
                expected_updated_at=current.updated_at,
                fields={
                    "status": RecordStatus.ARCHIVED.value,
                    "updated_at": _next_updated_at(current.updated_at),
                },
            ):
                latest = PromptScratchDraftRepository(session).get(draft_id)
                if latest is None:
                    raise NotFoundError(f"找不到提示詞草稿：{draft_id}")
                raise ConflictError("提示詞草稿已被更新或收起，未重複收起")
        return self.get_draft(draft_id)

    def export_draft_json(self, draft_id: str) -> str:
        """Return a stable, human-readable UTF-8 JSON payload."""

        draft = self.get_draft(draft_id)
        payload = {
            "schema_version": EXPORT_SCHEMA_VERSION,
            **draft.model_dump(mode="json"),
        }
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2)

    def export_draft_txt(self, draft_id: str) -> str:
        """Return a compact text handoff containing only populated author fields."""

        draft = self.get_draft(draft_id)
        lines: list[str] = []
        if draft.title:
            lines.extend(("提示詞草稿", draft.title, ""))
        if draft.character_name:
            lines.extend(("角色名稱", draft.character_name, ""))
        if draft.character_image_prompt_en:
            lines.extend(("角色圖片提示詞（英文）", draft.character_image_prompt_en, ""))
        if draft.background_image_prompt_en:
            lines.extend(("背景圖片提示詞（英文）", draft.background_image_prompt_en, ""))
        if draft.notes:
            lines.extend(("靈感備註", draft.notes, ""))
        return "\n".join(lines).rstrip() + "\n"


__all__ = ["EXPORT_SCHEMA_VERSION", "PromptScratchDraftService"]
