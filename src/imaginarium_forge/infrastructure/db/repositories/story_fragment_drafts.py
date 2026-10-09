"""Persistence adapter for project-optional story fragment drafts."""

from __future__ import annotations

import json
from typing import Any, cast

from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from imaginarium_forge.canonical import canonical_json
from imaginarium_forge.domain.common.enums import RecordStatus
from imaginarium_forge.domain.story.fragment_draft import (
    StoryFragmentDraft,
    StoryFragmentGenerationMode,
    StoryFragmentKind,
    normalize_story_fragment_tags,
)
from imaginarium_forge.infrastructure.db.models.orm import StoryFragmentDraftRow


def _decode_tags(payload: str) -> tuple[str, ...]:
    value = json.loads(payload)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError("tags_json 必須是字串 JSON array")
    canonical = normalize_story_fragment_tags(value)
    if tuple(value) != canonical:
        raise ValueError("tags_json 必須使用 canonical 標籤格式")
    return canonical


def _to_domain(row: StoryFragmentDraftRow) -> StoryFragmentDraft:
    return StoryFragmentDraft(
        id=row.id,
        project_id=row.project_id,
        title=row.title,
        fragment_kind=StoryFragmentKind(row.fragment_kind),
        fragment_text=row.fragment_text,
        context_notes=row.context_notes,
        tags=_decode_tags(row.tags_json),
        generation_mode=StoryFragmentGenerationMode(row.generation_mode),
        generation_seed=row.generation_seed,
        status=RecordStatus(row.status),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class StoryFragmentDraftRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, draft: StoryFragmentDraft) -> None:
        self._session.add(
            StoryFragmentDraftRow(
                id=draft.id,
                project_id=draft.project_id,
                title=draft.title,
                fragment_kind=draft.fragment_kind.value,
                fragment_text=draft.fragment_text,
                context_notes=draft.context_notes,
                tags_json=canonical_json(list(draft.tags)),
                generation_mode=draft.generation_mode.value,
                generation_seed=draft.generation_seed,
                status=draft.status.value,
                created_at=draft.created_at,
                updated_at=draft.updated_at,
            )
        )

    def get(self, draft_id: str) -> StoryFragmentDraft | None:
        row = self._session.get(StoryFragmentDraftRow, draft_id)
        return _to_domain(row) if row is not None else None

    def list_all(
        self,
        *,
        project_id: str | None = None,
        standalone_only: bool = False,
        include_archived: bool = False,
    ) -> list[StoryFragmentDraft]:
        stmt = select(StoryFragmentDraftRow)
        if standalone_only:
            stmt = stmt.where(StoryFragmentDraftRow.project_id.is_(None))
        elif project_id is not None:
            stmt = stmt.where(StoryFragmentDraftRow.project_id == project_id)
        if not include_archived:
            stmt = stmt.where(StoryFragmentDraftRow.status == RecordStatus.ACTIVE.value)
        stmt = stmt.order_by(
            StoryFragmentDraftRow.updated_at.desc(),
            StoryFragmentDraftRow.created_at.desc(),
            StoryFragmentDraftRow.id,
        )
        return [_to_domain(row) for row in self._session.scalars(stmt).all()]

    def update_active_cas(
        self,
        draft_id: str,
        *,
        expected_updated_at: str,
        fields: dict[str, object],
    ) -> bool:
        result = cast(
            CursorResult[Any],
            self._session.execute(
                update(StoryFragmentDraftRow)
                .where(
                    StoryFragmentDraftRow.id == draft_id,
                    StoryFragmentDraftRow.status == RecordStatus.ACTIVE.value,
                    StoryFragmentDraftRow.updated_at == expected_updated_at,
                )
                .values(**fields)
            ),
        )
        return bool(result.rowcount == 1)


__all__ = ["StoryFragmentDraftRepository"]
