"""Prompt Studio repositories (spec §35).

Semantics enforced here (in addition to DB constraints/triggers):
- prompt project versions have NO ast update method; accepted versions are
  immutable at the DB level (triggers) — edits create a new version;
- variants are write-once records of a compilation (no update/delete);
- profile snapshots dedupe by SHA-256 (same resolved profile ⇒ same row).
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from imaginarium_forge.infrastructure.db.models.orm import (
    PromptProfileSnapshotRow,
    PromptProjectRow,
    PromptProjectVersionRow,
    PromptVariantRow,
)


@dataclass(frozen=True)
class PromptProjectRecord:
    id: str
    project_id: str
    title: str
    source_text: str
    character_id: str | None
    character_version_id: str | None
    style_profile_id: str | None
    style_version_id: str | None
    status: str
    current_version_id: str | None
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class PromptProjectVersionRecord:
    id: str
    prompt_project_id: str
    version_number: int
    ast_json: str
    change_note: str
    accepted: bool
    created_at: str
    selection_snapshot_json: str = "{}"
    selection_snapshot_sha256: str = ""
    input_fingerprint: str = ""


@dataclass(frozen=True)
class PromptVariantRecord:
    id: str
    prompt_project_version_id: str
    prompt_project_id: str
    profile_snapshot_id: str
    positive_prompt: str
    negative_prompt: str
    natural_language_prompt: str
    blocks_json: str
    lint_json: str
    conflicts_json: str
    compiler_version: str
    created_at: str
    compilation_status: str = "valid"
    lint_status: str = "ok"
    input_fingerprint: str = ""
    checkpoint_id: str = ""
    checkpoint_filename_snapshot: str = ""
    checkpoint_sha256_snapshot: str = ""
    checkpoint_hash_status: str = "not_computed"
    dialect_id: str = ""
    checkpoint_profile_id: str = ""
    preset_id: str = ""


@dataclass(frozen=True)
class ProfileSnapshotRecord:
    id: str
    dialect_id: str
    dialect_version: str
    checkpoint_profile_id: str
    checkpoint_profile_version: str
    preset_id: str
    preset_version: str
    resolved_json: str
    sha256: str
    compiler_version: str
    created_at: str


def _project(row: PromptProjectRow) -> PromptProjectRecord:
    return PromptProjectRecord(
        id=row.id,
        project_id=row.project_id,
        title=row.title,
        source_text=row.source_text,
        character_id=row.character_id,
        character_version_id=row.character_version_id,
        style_profile_id=row.style_profile_id,
        style_version_id=row.style_version_id,
        status=row.status,
        current_version_id=row.current_version_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _version(row: PromptProjectVersionRow) -> PromptProjectVersionRecord:
    return PromptProjectVersionRecord(
        id=row.id,
        prompt_project_id=row.prompt_project_id,
        version_number=row.version_number,
        ast_json=row.ast_json,
        change_note=row.change_note,
        accepted=bool(row.accepted),
        created_at=row.created_at,
        selection_snapshot_json=row.selection_snapshot_json,
        selection_snapshot_sha256=row.selection_snapshot_sha256,
        input_fingerprint=row.input_fingerprint,
    )


def _variant(row: PromptVariantRow) -> PromptVariantRecord:
    return PromptVariantRecord(
        id=row.id,
        prompt_project_version_id=row.prompt_project_version_id,
        prompt_project_id=row.prompt_project_id,
        profile_snapshot_id=row.profile_snapshot_id,
        positive_prompt=row.positive_prompt,
        negative_prompt=row.negative_prompt,
        natural_language_prompt=row.natural_language_prompt,
        blocks_json=row.blocks_json,
        lint_json=row.lint_json,
        conflicts_json=row.conflicts_json,
        compiler_version=row.compiler_version,
        created_at=row.created_at,
        compilation_status=row.compilation_status,
        lint_status=row.lint_status,
        input_fingerprint=row.input_fingerprint,
        checkpoint_id=row.checkpoint_id,
        checkpoint_filename_snapshot=row.checkpoint_filename_snapshot,
        checkpoint_sha256_snapshot=row.checkpoint_sha256_snapshot,
        checkpoint_hash_status=row.checkpoint_hash_status,
        dialect_id=row.dialect_id,
        checkpoint_profile_id=row.checkpoint_profile_id,
        preset_id=row.preset_id,
    )


def _snapshot(row: PromptProfileSnapshotRow) -> ProfileSnapshotRecord:
    return ProfileSnapshotRecord(
        id=row.id,
        dialect_id=row.dialect_id,
        dialect_version=row.dialect_version,
        checkpoint_profile_id=row.checkpoint_profile_id,
        checkpoint_profile_version=row.checkpoint_profile_version,
        preset_id=row.preset_id,
        preset_version=row.preset_version,
        resolved_json=row.resolved_json,
        sha256=row.sha256,
        compiler_version=row.compiler_version,
        created_at=row.created_at,
    )


class PromptProjectRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, record: PromptProjectRecord) -> None:
        self._session.add(
            PromptProjectRow(
                id=record.id,
                project_id=record.project_id,
                title=record.title,
                source_text=record.source_text,
                character_id=record.character_id,
                character_version_id=record.character_version_id,
                style_profile_id=record.style_profile_id,
                style_version_id=record.style_version_id,
                status=record.status,
                current_version_id=record.current_version_id,
                created_at=record.created_at,
                updated_at=record.updated_at,
            )
        )

    def get(self, prompt_project_id: str) -> PromptProjectRecord | None:
        row = self._session.get(PromptProjectRow, prompt_project_id)
        return _project(row) if row else None

    def list_for_project(self, project_id: str) -> list[PromptProjectRecord]:
        rows = self._session.scalars(
            select(PromptProjectRow)
            .where(PromptProjectRow.project_id == project_id)
            .order_by(PromptProjectRow.created_at)
        )
        return [_project(row) for row in rows]

    def update_fields(self, prompt_project_id: str, **fields: str | None) -> bool:
        row = self._session.get(PromptProjectRow, prompt_project_id)
        if row is None:
            return False
        for key, value in fields.items():
            setattr(row, key, value)
        return True


class PromptProjectVersionRepository:
    """No ast update. Accepted versions immutable (DB triggers back this up)."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, record: PromptProjectVersionRecord) -> None:
        self._session.add(
            PromptProjectVersionRow(
                id=record.id,
                prompt_project_id=record.prompt_project_id,
                version_number=record.version_number,
                ast_json=record.ast_json,
                change_note=record.change_note,
                selection_snapshot_json=record.selection_snapshot_json,
                selection_snapshot_sha256=record.selection_snapshot_sha256,
                input_fingerprint=record.input_fingerprint,
                accepted=int(record.accepted),
                created_at=record.created_at,
            )
        )

    def get(self, version_id: str) -> PromptProjectVersionRecord | None:
        row = self._session.get(PromptProjectVersionRow, version_id)
        return _version(row) if row else None

    def list_versions(self, prompt_project_id: str) -> list[PromptProjectVersionRecord]:
        rows = self._session.scalars(
            select(PromptProjectVersionRow)
            .where(PromptProjectVersionRow.prompt_project_id == prompt_project_id)
            .order_by(PromptProjectVersionRow.version_number)
        )
        return [_version(row) for row in rows]

    def next_version_number(self, prompt_project_id: str) -> int:
        versions = self.list_versions(prompt_project_id)
        return (versions[-1].version_number + 1) if versions else 1

    def accept(self, version_id: str) -> bool:
        """One-way 0→1. The DB trigger blocks any change once accepted=1."""
        row = self._session.get(PromptProjectVersionRow, version_id)
        if row is None:
            return False
        if not row.accepted:
            row.accepted = 1
        return True


class PromptVariantRepository:
    """Write-once compilation records."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, record: PromptVariantRecord) -> None:
        self._session.add(
            PromptVariantRow(
                id=record.id,
                prompt_project_version_id=record.prompt_project_version_id,
                prompt_project_id=record.prompt_project_id,
                profile_snapshot_id=record.profile_snapshot_id,
                positive_prompt=record.positive_prompt,
                negative_prompt=record.negative_prompt,
                natural_language_prompt=record.natural_language_prompt,
                blocks_json=record.blocks_json,
                lint_json=record.lint_json,
                conflicts_json=record.conflicts_json,
                compiler_version=record.compiler_version,
                created_at=record.created_at,
                compilation_status=record.compilation_status,
                lint_status=record.lint_status,
                input_fingerprint=record.input_fingerprint,
                checkpoint_id=record.checkpoint_id,
                checkpoint_filename_snapshot=record.checkpoint_filename_snapshot,
                checkpoint_sha256_snapshot=record.checkpoint_sha256_snapshot,
                checkpoint_hash_status=record.checkpoint_hash_status,
                dialect_id=record.dialect_id,
                checkpoint_profile_id=record.checkpoint_profile_id,
                preset_id=record.preset_id,
            )
        )

    def get(self, variant_id: str) -> PromptVariantRecord | None:
        row = self._session.get(PromptVariantRow, variant_id)
        return _variant(row) if row else None

    def get_for_project(
        self, variant_id: str, project_id: str
    ) -> PromptVariantRecord | None:
        """Return a variant only through its owning top-level project.

        Export callers carry stale IDs in UI session state, so an unscoped
        primary-key lookup is not an authorization boundary.  Joining through
        ``prompt_projects`` keeps foreign and nonexistent IDs indistinguishable.
        """
        row = self._session.scalars(
            select(PromptVariantRow)
            .join(
                PromptProjectRow,
                PromptProjectRow.id == PromptVariantRow.prompt_project_id,
            )
            .where(
                PromptVariantRow.id == variant_id,
                PromptProjectRow.project_id == project_id,
            )
        ).first()
        return _variant(row) if row else None

    def list_for_version(self, version_id: str) -> list[PromptVariantRecord]:
        rows = self._session.scalars(
            select(PromptVariantRow)
            .where(PromptVariantRow.prompt_project_version_id == version_id)
            .order_by(PromptVariantRow.created_at)
        )
        return [_variant(row) for row in rows]


class ProfileSnapshotRepository:
    """Deduped by SHA-256: identical resolved profiles share one snapshot row."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_by_sha256(self, sha256: str) -> ProfileSnapshotRecord | None:
        row = self._session.scalars(
            select(PromptProfileSnapshotRow).where(
                PromptProfileSnapshotRow.sha256 == sha256
            )
        ).first()
        return _snapshot(row) if row else None

    def get(self, snapshot_id: str) -> ProfileSnapshotRecord | None:
        row = self._session.get(PromptProfileSnapshotRow, snapshot_id)
        return _snapshot(row) if row else None

    def add(self, record: ProfileSnapshotRecord) -> None:
        self._session.add(
            PromptProfileSnapshotRow(
                id=record.id,
                dialect_id=record.dialect_id,
                dialect_version=record.dialect_version,
                checkpoint_profile_id=record.checkpoint_profile_id,
                checkpoint_profile_version=record.checkpoint_profile_version,
                preset_id=record.preset_id,
                preset_version=record.preset_version,
                resolved_json=record.resolved_json,
                sha256=record.sha256,
                compiler_version=record.compiler_version,
                created_at=record.created_at,
            )
        )
