"""Project repository — thin SQLAlchemy access; transactions owned by services."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from imaginarium_forge.domain.common.enums import ProjectStatus
from imaginarium_forge.domain.project.model import Project
from imaginarium_forge.infrastructure.db.models.orm import ProjectRow


def _to_domain(row: ProjectRow) -> Project:
    return Project(
        id=row.id,
        name=row.name,
        description=row.description,
        default_language=row.default_language,
        status=ProjectStatus(row.status),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class ProjectRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, project: Project) -> None:
        self._session.add(
            ProjectRow(
                id=project.id,
                name=project.name,
                description=project.description,
                default_language=project.default_language,
                status=project.status.value,
                created_at=project.created_at,
                updated_at=project.updated_at,
            )
        )

    def get(self, project_id: str) -> Project | None:
        row = self._session.get(ProjectRow, project_id)
        return _to_domain(row) if row else None

    def list_all(self, *, include_archived: bool = True) -> list[Project]:
        stmt = select(ProjectRow).order_by(ProjectRow.created_at)
        rows = self._session.scalars(stmt).all()
        projects = [_to_domain(r) for r in rows]
        if include_archived:
            return projects
        return [p for p in projects if p.status is ProjectStatus.ACTIVE]

    def update_fields(self, project_id: str, **fields: str) -> bool:
        row = self._session.get(ProjectRow, project_id)
        if row is None:
            return False
        for key, value in fields.items():
            setattr(row, key, value)
        return True
