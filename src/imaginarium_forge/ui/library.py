"""Read-only bookshelf projections built from public application services.

This module deliberately lives in the UI layer: it combines existing service
results into small presentation summaries without introducing a repository or
raw-SQL shortcut.
"""

from __future__ import annotations

from dataclasses import dataclass

from imaginarium_forge.domain.project.model import Project
from imaginarium_forge.ui.bootstrap import Services


@dataclass(frozen=True, slots=True)
class BookProgress:
    """A creator-facing four-part summary for one project."""

    characters: int = 0
    worlds: int = 0
    stories: int = 0
    prompts: int = 0
    styles: int = 0
    updated_at: str = ""

    @property
    def started_sections(self) -> int:
        """Number of the four main book sections that already hold content."""

        return sum(
            count > 0
            for count in (self.characters, self.worlds, self.stories, self.prompts)
        )

    @property
    def content_total(self) -> int:
        """Visible content-item count, excluding supporting style profiles."""

        return self.characters + self.worlds + self.stories + self.prompts


def read_book_progress(services: Services, project: Project) -> BookProgress:
    """Build a bookshelf summary using only public application-service methods."""

    characters = services.characters.list_characters(
        project.id, include_archived=False
    )
    worlds = services.story_bibles.list_for_project(project.id)
    stories = services.story_outlines.list_for_project(project.id)
    prompts = services.prompt_projects.list_for_project(project.id)
    styles = services.styles.list_profiles(project.id, include_archived=False)
    requirements = services.story_requirements.list_for_project(project.id)

    timestamps = [project.updated_at]
    timestamps.extend(item.updated_at for item in characters)
    timestamps.extend(item.updated_at for item in worlds)
    timestamps.extend(item.updated_at for item in stories)
    timestamps.extend(item.updated_at for item in prompts)
    timestamps.extend(item.updated_at for item in styles)
    timestamps.extend(item.updated_at for item in requirements)

    return BookProgress(
        characters=len(characters),
        worlds=len(worlds),
        stories=len(stories),
        prompts=len(prompts),
        styles=len(styles),
        updated_at=max((value for value in timestamps if value), default=""),
    )
