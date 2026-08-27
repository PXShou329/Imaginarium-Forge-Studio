"""Persistence adapter for project-optional world seed drafts."""

from __future__ import annotations

import json
from typing import Any, cast

from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from imaginarium_forge.canonical import canonical_json
from imaginarium_forge.domain.common.enums import RecordStatus
from imaginarium_forge.domain.creative.world_seed_draft import (
    WorldSeedDraft,
    WorldSeedGenerationMode,
)
from imaginarium_forge.infrastructure.db.models.orm import WorldSeedDraftRow


def _decode_text_tuple(payload: str, *, field: str) -> tuple[str, ...]:
    value = json.loads(payload)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{field} 必須是字串 JSON array")
    return tuple(value)


def _to_domain(row: WorldSeedDraftRow) -> WorldSeedDraft:
    return WorldSeedDraft(
        id=row.id,
        project_id=row.project_id,
        title=row.title,
        setting=row.setting,
        time_period=row.time_period,
        world_rules=_decode_text_tuple(row.world_rules_json, field="world_rules_json"),
        locations=_decode_text_tuple(row.locations_json, field="locations_json"),
        social_context=row.social_context,
        technology_or_magic=row.technology_or_magic,
        central_conflict=row.central_conflict,
        themes=_decode_text_tuple(row.themes_json, field="themes_json"),
        generation_mode=WorldSeedGenerationMode(row.generation_mode),
        generation_seed=row.generation_seed,
        status=RecordStatus(row.status),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class WorldSeedDraftRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, draft: WorldSeedDraft) -> None:
        self._session.add(
            WorldSeedDraftRow(
                id=draft.id,
                project_id=draft.project_id,
                title=draft.title,
                setting=draft.setting,
                time_period=draft.time_period,
                world_rules_json=canonical_json(list(draft.world_rules)),
                locations_json=canonical_json(list(draft.locations)),
                social_context=draft.social_context,
                technology_or_magic=draft.technology_or_magic,
                central_conflict=draft.central_conflict,
                themes_json=canonical_json(list(draft.themes)),
                generation_mode=draft.generation_mode.value,
                generation_seed=draft.generation_seed,
                status=draft.status.value,
                created_at=draft.created_at,
                updated_at=draft.updated_at,
            )
        )

    def get(self, draft_id: str) -> WorldSeedDraft | None:
        row = self._session.get(WorldSeedDraftRow, draft_id)
        return _to_domain(row) if row is not None else None

    def list_all(
        self,
        *,
        project_id: str | None = None,
        standalone_only: bool = False,
        include_archived: bool = False,
    ) -> list[WorldSeedDraft]:
        stmt = select(WorldSeedDraftRow)
        if standalone_only:
            stmt = stmt.where(WorldSeedDraftRow.project_id.is_(None))
        elif project_id is not None:
            stmt = stmt.where(WorldSeedDraftRow.project_id == project_id)
        if not include_archived:
            stmt = stmt.where(WorldSeedDraftRow.status == RecordStatus.ACTIVE.value)
        stmt = stmt.order_by(
            WorldSeedDraftRow.updated_at.desc(),
            WorldSeedDraftRow.created_at.desc(),
            WorldSeedDraftRow.id,
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
                update(WorldSeedDraftRow)
                .where(
                    WorldSeedDraftRow.id == draft_id,
                    WorldSeedDraftRow.status == RecordStatus.ACTIVE.value,
                    WorldSeedDraftRow.updated_at == expected_updated_at,
                )
                .values(**fields)
            ),
        )
        return bool(result.rowcount == 1)


__all__ = ["WorldSeedDraftRepository"]
