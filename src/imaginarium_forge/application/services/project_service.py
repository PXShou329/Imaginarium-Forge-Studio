"""ProjectService — create/list/open/update/archive/restore (no hard delete)."""

from __future__ import annotations

from pydantic import ValidationError

from imaginarium_forge.application.errors import NotFoundError, ValidationFailedError
from imaginarium_forge.application.services.base import ServiceBase
from imaginarium_forge.domain.common.enums import ProjectStatus
from imaginarium_forge.domain.common.ids import new_id, utc_now_iso
from imaginarium_forge.domain.project.model import Project
from imaginarium_forge.infrastructure.db.repositories.projects import ProjectRepository


class ProjectService(ServiceBase):
    def create_project(
        self, *, name: str, description: str = "", default_language: str = "zh-TW"
    ) -> Project:
        now = utc_now_iso()
        try:
            project = Project(
                id=new_id(),
                name=name,
                description=description,
                default_language=default_language,
                created_at=now,
                updated_at=now,
            )
        except ValidationError as exc:
            raise ValidationFailedError(str(exc.errors()[0].get("msg", exc))) from exc
        with self._transaction() as session:
            ProjectRepository(session).add(project)
        return project

    def get_project(self, project_id: str) -> Project:
        project = self._read_only(lambda s: ProjectRepository(s).get(project_id))
        if project is None:
            raise NotFoundError(f"找不到專案：{project_id}")
        return project

    def list_projects(self, *, include_archived: bool = True) -> list[Project]:
        return self._read_only(
            lambda s: ProjectRepository(s).list_all(include_archived=include_archived)
        )

    def update_metadata(
        self,
        project_id: str,
        *,
        name: str | None = None,
        description: str | None = None,
        default_language: str | None = None,
    ) -> Project:
        if name is not None and not name.strip():
            raise ValidationFailedError("專案名稱為必填")
        if default_language is not None and not default_language.strip():
            raise ValidationFailedError("預設語言為必填")
        with self._transaction() as session:
            repo = ProjectRepository(session)
            fields: dict[str, str] = {"updated_at": utc_now_iso()}
            if name is not None:
                fields["name"] = name.strip()
            if description is not None:
                fields["description"] = description
            if default_language is not None:
                fields["default_language"] = default_language.strip()
            if not repo.update_fields(project_id, **fields):
                raise NotFoundError(f"找不到專案：{project_id}")
        return self.get_project(project_id)

    def _set_status(self, project_id: str, status: ProjectStatus) -> Project:
        with self._transaction() as session:
            ok = ProjectRepository(session).update_fields(
                project_id, status=status.value, updated_at=utc_now_iso()
            )
            if not ok:
                raise NotFoundError(f"找不到專案：{project_id}")
        return self.get_project(project_id)

    def archive_project(self, project_id: str) -> Project:
        return self._set_status(project_id, ProjectStatus.ARCHIVED)

    def restore_project(self, project_id: str) -> Project:
        return self._set_status(project_id, ProjectStatus.ACTIVE)
