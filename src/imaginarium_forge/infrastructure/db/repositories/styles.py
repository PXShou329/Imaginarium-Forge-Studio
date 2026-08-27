"""Style profile + style-version repositories (versions are add/read only)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from imaginarium_forge.domain.common.enums import RecordStatus
from imaginarium_forge.domain.style.model import StyleDNA, StyleProfile, StyleProfileVersion
from imaginarium_forge.infrastructure.db.models.orm import (
    StyleProfileRow,
    StyleProfileVersionRow,
)


def _profile_to_domain(row: StyleProfileRow) -> StyleProfile:
    return StyleProfile(
        id=row.id,
        project_id=row.project_id,
        name=row.name,
        current_version_id=row.current_version_id,
        status=RecordStatus(row.status),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _version_to_domain(row: StyleProfileVersionRow) -> StyleProfileVersion:
    return StyleProfileVersion(
        id=row.id,
        style_profile_id=row.style_profile_id,
        version_number=row.version_number,
        style_dna=StyleDNA.model_validate_json(row.style_dna_json),
        change_note=row.change_note,
        created_at=row.created_at,
    )


class StyleRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, profile: StyleProfile) -> None:
        self._session.add(
            StyleProfileRow(
                id=profile.id,
                project_id=profile.project_id,
                name=profile.name,
                current_version_id=profile.current_version_id,
                status=profile.status.value,
                created_at=profile.created_at,
                updated_at=profile.updated_at,
            )
        )

    def get(self, profile_id: str) -> StyleProfile | None:
        row = self._session.get(StyleProfileRow, profile_id)
        return _profile_to_domain(row) if row else None

    def list_for_project(
        self, project_id: str, *, include_archived: bool = True
    ) -> list[StyleProfile]:
        stmt = (
            select(StyleProfileRow)
            .where(StyleProfileRow.project_id == project_id)
            .order_by(StyleProfileRow.created_at)
        )
        rows = self._session.scalars(stmt).all()
        profiles = [_profile_to_domain(r) for r in rows]
        if include_archived:
            return profiles
        return [p for p in profiles if p.status is RecordStatus.ACTIVE]

    def update_fields(self, profile_id: str, **fields: str | None) -> bool:
        row = self._session.get(StyleProfileRow, profile_id)
        if row is None:
            return False
        for key, value in fields.items():
            setattr(row, key, value)
        return True

    # --- versions: add + read only ------------------------------------------

    def add_version(self, version: StyleProfileVersion) -> None:
        self._session.add(
            StyleProfileVersionRow(
                id=version.id,
                style_profile_id=version.style_profile_id,
                version_number=version.version_number,
                style_dna_json=version.style_dna.model_dump_json(),
                change_note=version.change_note,
                created_at=version.created_at,
            )
        )

    def get_version(self, version_id: str) -> StyleProfileVersion | None:
        row = self._session.get(StyleProfileVersionRow, version_id)
        return _version_to_domain(row) if row else None

    def list_versions(self, profile_id: str) -> list[StyleProfileVersion]:
        stmt = (
            select(StyleProfileVersionRow)
            .where(StyleProfileVersionRow.style_profile_id == profile_id)
            .order_by(StyleProfileVersionRow.version_number)
        )
        return [_version_to_domain(r) for r in self._session.scalars(stmt).all()]

    def next_version_number(self, profile_id: str) -> int:
        existing = self.list_versions(profile_id)
        return (max((v.version_number for v in existing), default=0)) + 1
