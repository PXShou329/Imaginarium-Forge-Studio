"""Project aggregate (execution spec §8.2)."""

from __future__ import annotations

from pydantic import BaseModel, field_validator

from imaginarium_forge.domain.common.enums import ProjectStatus


class Project(BaseModel):
    id: str
    name: str
    description: str = ""
    default_language: str = "zh-TW"
    status: ProjectStatus = ProjectStatus.ACTIVE
    created_at: str
    updated_at: str

    @field_validator("name")
    @classmethod
    def _name_required(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("專案名稱為必填")
        return value.strip()
