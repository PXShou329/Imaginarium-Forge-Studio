"""Append-only persistence for canonical Phase 4.3 video prompt bundles."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from imaginarium_forge.canonical import canonical_json
from imaginarium_forge.domain.prompt.video import (
    VIDEO_PROMPT_BUNDLE_SCHEMA,
    VideoPromptBundle,
)
from imaginarium_forge.infrastructure.db.models.orm import VideoPromptBundleRow


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _canonical_payload(raw: str, digest: str, *, label: str) -> object:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label} 必須是有效 JSON") from exc
    canonical = canonical_json(value)
    if raw != canonical:
        raise ValueError(f"{label} 必須使用 canonical JSON")
    if digest != _sha256_text(canonical):
        raise ValueError(f"{label} SHA-256 不符")
    return value


@dataclass(frozen=True, slots=True)
class VideoPromptBundleRecord:
    id: str
    project_id: str
    prompt_project_id: str
    prompt_project_version_id: str
    status: str
    content_mode: str
    schema_version: str
    parent_input_fingerprint: str
    source_input_fingerprint: str
    input_fingerprint: str
    bundle_json: str
    bundle_sha256: str
    participant_manifest_json: str
    participant_manifest_fingerprint: str
    eligibility_evaluation_ids_json: str
    eligibility_evaluation_ids_sha256: str
    eligibility_window_started_at: str
    created_at: str

    def __post_init__(self) -> None:
        required = (
            self.id,
            self.project_id,
            self.prompt_project_id,
            self.prompt_project_version_id,
            self.parent_input_fingerprint,
            self.source_input_fingerprint,
            self.input_fingerprint,
            self.eligibility_window_started_at,
            self.created_at,
        )
        if not all(value.strip() for value in required):
            raise ValueError("video prompt bundle ownership/fingerprint/time 不得空白")
        if self.status != "ready_draft":
            raise ValueError("只有 ready_draft video prompt bundle 可持久化")
        if self.schema_version != VIDEO_PROMPT_BUNDLE_SCHEMA:
            raise ValueError("video prompt bundle schema version 無效")

        _canonical_payload(self.bundle_json, self.bundle_sha256, label="video bundle")
        _canonical_payload(
            self.participant_manifest_json,
            self.participant_manifest_fingerprint,
            label="video participant manifest",
        )
        audit_payload = _canonical_payload(
            self.eligibility_evaluation_ids_json,
            self.eligibility_evaluation_ids_sha256,
            label="video eligibility audit IDs",
        )
        if not isinstance(audit_payload, list) or not audit_payload:
            raise ValueError("video eligibility audit IDs 必須是非空陣列")

        bundle = VideoPromptBundle.model_validate_json(self.bundle_json)
        if bundle.canonical() != self.bundle_json or bundle.sha256 != self.bundle_sha256:
            raise ValueError("video bundle domain canonical/hash 不符")
        expected_columns = (
            (self.project_id, bundle.project_id, "project_id"),
            (self.prompt_project_id, bundle.prompt_project_id, "prompt_project_id"),
            (
                self.prompt_project_version_id,
                bundle.prompt_project_version_id,
                "prompt_project_version_id",
            ),
            (self.content_mode, bundle.content_mode.value, "content_mode"),
            (self.schema_version, bundle.schema_version, "schema_version"),
            (
                self.parent_input_fingerprint,
                bundle.parent_input_fingerprint,
                "parent_input_fingerprint",
            ),
            (
                self.source_input_fingerprint,
                bundle.source_input_fingerprint,
                "source_input_fingerprint",
            ),
            (self.input_fingerprint, bundle.input_fingerprint, "input_fingerprint"),
            (self.created_at, bundle.created_at, "created_at"),
            (
                self.eligibility_window_started_at,
                bundle.eligibility_window_started_at,
                "eligibility_window_started_at",
            ),
            (
                self.participant_manifest_json,
                bundle.participant_manifest_canonical_json,
                "participant_manifest_json",
            ),
            (
                self.participant_manifest_fingerprint,
                bundle.participant_manifest_fingerprint,
                "participant_manifest_fingerprint",
            ),
        )
        for stored, bundled, label in expected_columns:
            if stored != bundled:
                raise ValueError(f"video bundle column 與 payload {label} 不一致")
        if tuple(audit_payload) != bundle.video_eligibility_evaluation_ids:
            raise ValueError("video eligibility audit IDs 與 bundle 不一致")

    @classmethod
    def from_bundle(
        cls, *, bundle_id: str, bundle: VideoPromptBundle
    ) -> VideoPromptBundleRecord:
        manifest_json = bundle.participant_manifest_canonical_json
        audit_json = canonical_json(list(bundle.video_eligibility_evaluation_ids))
        return cls(
            id=bundle_id,
            project_id=bundle.project_id,
            prompt_project_id=bundle.prompt_project_id,
            prompt_project_version_id=bundle.prompt_project_version_id,
            status="ready_draft",
            content_mode=bundle.content_mode.value,
            schema_version=bundle.schema_version,
            parent_input_fingerprint=bundle.parent_input_fingerprint,
            source_input_fingerprint=bundle.source_input_fingerprint,
            input_fingerprint=bundle.input_fingerprint,
            bundle_json=bundle.canonical(),
            bundle_sha256=bundle.sha256,
            participant_manifest_json=manifest_json,
            participant_manifest_fingerprint=bundle.participant_manifest_fingerprint,
            eligibility_evaluation_ids_json=audit_json,
            eligibility_evaluation_ids_sha256=_sha256_text(audit_json),
            eligibility_window_started_at=bundle.eligibility_window_started_at,
            created_at=bundle.created_at,
        )

    @property
    def bundle(self) -> VideoPromptBundle:
        return VideoPromptBundle.model_validate_json(self.bundle_json)


def _record(row: VideoPromptBundleRow) -> VideoPromptBundleRecord:
    return VideoPromptBundleRecord(
        id=row.id,
        project_id=row.project_id,
        prompt_project_id=row.prompt_project_id,
        prompt_project_version_id=row.prompt_project_version_id,
        status=row.status,
        content_mode=row.content_mode,
        schema_version=row.schema_version,
        parent_input_fingerprint=row.parent_input_fingerprint,
        source_input_fingerprint=row.source_input_fingerprint,
        input_fingerprint=row.input_fingerprint,
        bundle_json=row.bundle_json,
        bundle_sha256=row.bundle_sha256,
        participant_manifest_json=row.participant_manifest_json,
        participant_manifest_fingerprint=row.participant_manifest_fingerprint,
        eligibility_evaluation_ids_json=row.eligibility_evaluation_ids_json,
        eligibility_evaluation_ids_sha256=row.eligibility_evaluation_ids_sha256,
        eligibility_window_started_at=row.eligibility_window_started_at,
        created_at=row.created_at,
    )


class VideoPromptBundleRepository:
    """No update/delete methods: bundle rows are immutable evidence."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, record: VideoPromptBundleRecord) -> None:
        self._session.add(
            VideoPromptBundleRow(
                id=record.id,
                project_id=record.project_id,
                prompt_project_id=record.prompt_project_id,
                prompt_project_version_id=record.prompt_project_version_id,
                status=record.status,
                content_mode=record.content_mode,
                schema_version=record.schema_version,
                parent_input_fingerprint=record.parent_input_fingerprint,
                source_input_fingerprint=record.source_input_fingerprint,
                input_fingerprint=record.input_fingerprint,
                bundle_json=record.bundle_json,
                bundle_sha256=record.bundle_sha256,
                participant_manifest_json=record.participant_manifest_json,
                participant_manifest_fingerprint=(
                    record.participant_manifest_fingerprint
                ),
                eligibility_evaluation_ids_json=(
                    record.eligibility_evaluation_ids_json
                ),
                eligibility_evaluation_ids_sha256=(
                    record.eligibility_evaluation_ids_sha256
                ),
                eligibility_window_started_at=(
                    record.eligibility_window_started_at
                ),
                created_at=record.created_at,
            )
        )

    def get(self, bundle_id: str) -> VideoPromptBundleRecord | None:
        row = self._session.get(VideoPromptBundleRow, bundle_id)
        return _record(row) if row is not None else None

    def list_for_version(
        self, prompt_project_version_id: str
    ) -> list[VideoPromptBundleRecord]:
        rows = self._session.scalars(
            select(VideoPromptBundleRow)
            .where(
                VideoPromptBundleRow.prompt_project_version_id
                == prompt_project_version_id
            )
            .order_by(VideoPromptBundleRow.created_at, VideoPromptBundleRow.id)
        ).all()
        return [_record(row) for row in rows]

    def list_for_project(self, project_id: str) -> list[VideoPromptBundleRecord]:
        rows = self._session.scalars(
            select(VideoPromptBundleRow)
            .where(VideoPromptBundleRow.project_id == project_id)
            .order_by(VideoPromptBundleRow.created_at, VideoPromptBundleRow.id)
        ).all()
        return [_record(row) for row in rows]
