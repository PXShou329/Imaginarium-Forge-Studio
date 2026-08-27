"""Outfit repository — outfits belong to a character; archive not hard-delete."""

from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from imaginarium_forge.domain.common.enums import RecordStatus
from imaginarium_forge.domain.outfit.model import OutfitProfile
from imaginarium_forge.infrastructure.db.models.orm import OutfitRow


def _to_domain(row: OutfitRow) -> OutfitProfile:
    return OutfitProfile(
        id=row.id,
        character_id=row.character_id,
        name=row.name,
        description=row.description,
        canonical_traits=tuple(json.loads(row.canonical_traits_json)),
        optional_traits=tuple(json.loads(row.optional_traits_json)),
        prohibited_traits=tuple(json.loads(row.prohibited_traits_json)),
        status=RecordStatus(row.status),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class OutfitRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, outfit: OutfitProfile) -> None:
        self._session.add(
            OutfitRow(
                id=outfit.id,
                character_id=outfit.character_id,
                name=outfit.name,
                description=outfit.description,
                canonical_traits_json=json.dumps(list(outfit.canonical_traits)),
                optional_traits_json=json.dumps(list(outfit.optional_traits)),
                prohibited_traits_json=json.dumps(list(outfit.prohibited_traits)),
                status=outfit.status.value,
                created_at=outfit.created_at,
                updated_at=outfit.updated_at,
            )
        )

    def get(self, outfit_id: str) -> OutfitProfile | None:
        row = self._session.get(OutfitRow, outfit_id)
        return _to_domain(row) if row else None

    def list_for_character(
        self, character_id: str, *, include_archived: bool = True
    ) -> list[OutfitProfile]:
        stmt = (
            select(OutfitRow)
            .where(OutfitRow.character_id == character_id)
            .order_by(OutfitRow.created_at)
        )
        rows = self._session.scalars(stmt).all()
        outfits = [_to_domain(r) for r in rows]
        if include_archived:
            return outfits
        return [o for o in outfits if o.status is RecordStatus.ACTIVE]

    def update_status(self, outfit_id: str, status: RecordStatus, updated_at: str) -> bool:
        row = self._session.get(OutfitRow, outfit_id)
        if row is None:
            return False
        row.status = status.value
        row.updated_at = updated_at
        return True
