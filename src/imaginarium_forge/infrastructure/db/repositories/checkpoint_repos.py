"""Checkpoint registry + experiment log repositories (spec §33/§34)."""

from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from imaginarium_forge.domain.checkpoint.asset import CheckpointAsset
from imaginarium_forge.domain.checkpoint.classification import CheckpointStatus, MetadataSource
from imaginarium_forge.domain.prompt.experiments import ExperimentLog
from imaginarium_forge.infrastructure.db.models.orm import (
    CheckpointRegistryRow,
    PromptExperimentLogRow,
)


def _asset(row: CheckpointRegistryRow) -> CheckpointAsset:
    return CheckpointAsset(
        id=row.id,
        path=row.path,
        filename=row.filename,
        extension=row.extension,
        size_bytes=row.size_bytes,
        modified_at=row.modified_at,
        modified_at_ns=row.modified_at_ns,
        sha256=row.sha256,
        hash_cached_at=row.hash_cached_at,
        status=CheckpointStatus(row.status),
        display_name=row.display_name,
        base_model_hint=row.base_model_hint,
        metadata_source=MetadataSource(row.metadata_source),
        assigned_profile_id=row.assigned_profile_id,
        usage_status=row.usage_status,
        sha256_status=row.sha256_status,
        local_metadata_json=row.local_metadata_json,
        last_scanned_at=row.last_scanned_at,
        availability=row.availability,
        notes=row.notes,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class CheckpointRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, checkpoint_id: str) -> CheckpointAsset | None:
        row = self._session.get(CheckpointRegistryRow, checkpoint_id)
        return _asset(row) if row else None

    def get_by_path(self, path: str) -> CheckpointAsset | None:
        row = self._session.scalars(
            select(CheckpointRegistryRow).where(CheckpointRegistryRow.path == path)
        ).first()
        return _asset(row) if row else None

    def list_all(self) -> list[CheckpointAsset]:
        rows = self._session.scalars(
            select(CheckpointRegistryRow).order_by(CheckpointRegistryRow.filename)
        )
        return [_asset(row) for row in rows]

    def add(self, asset: CheckpointAsset) -> None:
        self._session.add(CheckpointRegistryRow(**asset.model_dump(mode="json")))

    def update_fields(self, checkpoint_id: str, **fields: str | int) -> bool:
        row = self._session.get(CheckpointRegistryRow, checkpoint_id)
        if row is None:
            return False
        for key, value in fields.items():
            setattr(row, key, value)
        return True


def _experiment(row: PromptExperimentLogRow) -> ExperimentLog:
    return ExperimentLog(
        id=row.id,
        checkpoint_id=row.checkpoint_id or "",
        checkpoint_sha256=row.checkpoint_sha256,
        prompt_project_id=row.prompt_project_id or "",
        prompt_variant_id=row.prompt_variant_id or "",
        resolved_profile_hash=row.resolved_profile_hash,
        positive_prompt=row.positive_prompt,
        negative_prompt=row.negative_prompt,
        sampler=row.sampler,
        scheduler=row.scheduler,
        steps=row.steps,
        cfg=row.cfg,
        width=row.width,
        height=row.height,
        seed=row.seed,
        lora_settings=tuple(json.loads(row.lora_settings_json)),
        overall_rating=row.result_rating,
        identity_score=row.identity_score,
        style_score=row.style_score,
        instruction_adherence=row.instruction_adherence,
        failure_tags=tuple(json.loads(row.failure_tags_json)),
        notes=row.notes,
        local_output_reference=row.local_output_reference,
        created_at=row.created_at,
    )


class ExperimentRepository:
    """Append + read. Experiments are records of what happened; no rewriting."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, log: ExperimentLog) -> None:
        data = log.model_dump(mode="json")
        self._session.add(
            PromptExperimentLogRow(
                id=data["id"],
                checkpoint_id=data["checkpoint_id"] or None,
                checkpoint_sha256=data["checkpoint_sha256"],
                prompt_project_id=data["prompt_project_id"] or None,
                prompt_variant_id=data["prompt_variant_id"] or None,
                resolved_profile_hash=data["resolved_profile_hash"],
                positive_prompt=data["positive_prompt"],
                negative_prompt=data["negative_prompt"],
                sampler=data["sampler"],
                scheduler=data["scheduler"],
                steps=data["steps"],
                cfg=data["cfg"],
                width=data["width"],
                height=data["height"],
                seed=data["seed"],
                lora_settings_json=json.dumps(data["lora_settings"], ensure_ascii=False),
                result_rating=data["overall_rating"],
                identity_score=data["identity_score"],
                style_score=data["style_score"],
                instruction_adherence=data["instruction_adherence"],
                failure_tags_json=json.dumps(data["failure_tags"], ensure_ascii=False),
                notes=data["notes"],
                local_output_reference=data["local_output_reference"],
                created_at=data["created_at"],
            )
        )

    def get(self, experiment_id: str) -> ExperimentLog | None:
        row = self._session.get(PromptExperimentLogRow, experiment_id)
        return _experiment(row) if row else None

    def list_for_checkpoint(self, checkpoint_id: str) -> list[ExperimentLog]:
        rows = self._session.scalars(
            select(PromptExperimentLogRow)
            .where(PromptExperimentLogRow.checkpoint_id == checkpoint_id)
            .order_by(PromptExperimentLogRow.created_at)
        )
        return [_experiment(row) for row in rows]
    def list_all(self) -> list[ExperimentLog]:
        rows = self._session.scalars(
            select(PromptExperimentLogRow).order_by(PromptExperimentLogRow.created_at)
        )
        return [_experiment(row) for row in rows]
