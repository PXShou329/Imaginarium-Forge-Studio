"""Checkpoint registry service (spec §7).

Bridges the read-only scanner to the DB registry:

- `scan_roots` upserts by absolute path; rescans refresh size/mtime and
  `last_scanned_at`, mark a cached hash `stale` when the file changed, and
  NEVER overwrite user-owned fields (status, display name, notes, assigned
  profile, usage status);
- `ensure_hash` implements the §7.5 cache: path + size + mtime unchanged and
  status `computed` → reuse; otherwise recompute (locally; never transmitted);
- `set_status` enforces §7.6: `recommended_profile` requires at least one
  experiment log as evidence.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from imaginarium_forge.application.services.base import ServiceBase
from imaginarium_forge.domain.checkpoint.asset import CheckpointAsset
from imaginarium_forge.domain.checkpoint.classification import (
    CheckpointStatus,
    MetadataSource,
)
from imaginarium_forge.infrastructure.checkpoints.scanner import (
    ScanResult,
    scan_root,
    sha256_file,
)
from imaginarium_forge.infrastructure.db.repositories.checkpoint_repos import (
    CheckpointRepository,
    ExperimentRepository,
)


class RegistryScanReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    scanned_roots: tuple[str, ...] = ()
    added: tuple[str, ...] = ()          # filenames
    updated: tuple[str, ...] = ()
    skipped_outside_root: tuple[str, ...] = ()
    skipped_extension: tuple[str, ...] = ()
    #: A-07 §10.3 — records under scanned roots whose file no longer exists
    missing: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()


class StatusTransitionError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class CheckpointRegistryService(ServiceBase):
    # ------------------------------------------------------------------ scan
    def scan_roots(self, roots: tuple[Path, ...]) -> RegistryScanReport:
        added: list[str] = []
        updated: list[str] = []
        outside: list[str] = []
        wrong_ext: list[str] = []
        errors: list[str] = []
        scanned: list[str] = []

        results: list[ScanResult] = []
        for root in roots:
            try:
                results.append(scan_root(root))
                scanned.append(str(root))
            except Exception as exc:  # ScanRootError et al. → reported, not fatal
                errors.append(str(exc))

        with self._session_factory() as session:
            repo = CheckpointRepository(session)
            for result in results:
                outside.extend(result.skipped_outside_root)
                wrong_ext.extend(result.skipped_extension)
                for item in result.checkpoints:
                    existing = repo.get_by_path(item.path)
                    if existing is None:
                        repo.add(self._new_asset(item))
                        added.append(item.filename)
                    else:
                        self._refresh_existing(repo, existing, item)
                        updated.append(item.filename)
            missing = self._reconcile_missing(repo, results)
            session.commit()

        return RegistryScanReport(
            scanned_roots=tuple(scanned),
            added=tuple(added),
            updated=tuple(updated),
            skipped_outside_root=tuple(outside),
            skipped_extension=tuple(wrong_ext),
            missing=missing,
            errors=tuple(errors),
        )

    @staticmethod
    def _reconcile_missing(
        repo: CheckpointRepository, results: list[ScanResult]
    ) -> tuple[str, ...]:
        """A-07 §10.3: mark DB records under successfully scanned roots whose
        file was NOT seen this round as 'missing'. Never deletes anything;
        a later scan that finds the file again restores 'present'/'changed'."""
        seen_paths = {item.path for result in results for item in result.checkpoints}
        scanned_roots = [str(result.root) for result in results]
        missing: list[str] = []
        for asset in repo.list_all():
            under_scanned = any(
                asset.path.startswith(root.rstrip("/\\") + sep)
                for root in scanned_roots
                for sep in ("/", "\\")
            )
            if not under_scanned or asset.path in seen_paths:
                continue
            if asset.availability != "missing":
                repo.update_fields(
                    asset.id, availability="missing", updated_at=_now()
                )
            missing.append(asset.filename)
        return tuple(missing)

    @staticmethod
    def _new_asset(item) -> CheckpointAsset:  # type: ignore[no-untyped-def]
        metadata_source = (
            MetadataSource.SAFETENSORS_METADATA
            if item.header_metadata
            else MetadataSource.UNKNOWN
        )
        now = _now()
        return CheckpointAsset(
            id=str(uuid.uuid4()),
            path=item.path,
            filename=item.filename,
            extension=item.extension,
            size_bytes=item.size_bytes,
            modified_at=item.modified_at,
            modified_at_ns=item.modified_at_ns,
            metadata_source=metadata_source,
            local_metadata_json=json.dumps(item.header_metadata, ensure_ascii=False),
            last_scanned_at=now,
            created_at=now,
            updated_at=now,
        )

    @staticmethod
    def _refresh_existing(  # type: ignore[no-untyped-def]
        repo: CheckpointRepository, existing: CheckpointAsset, item
    ) -> None:
        # A2-09: nanosecond identity — a same-size rewrite within one second
        # must still be detected as a change
        changed = (
            existing.size_bytes != item.size_bytes
            or existing.modified_at_ns != item.modified_at_ns
        )
        fields: dict[str, str | int] = {
            "size_bytes": item.size_bytes,
            "modified_at": item.modified_at,
            "modified_at_ns": item.modified_at_ns,
            "local_metadata_json": json.dumps(item.header_metadata, ensure_ascii=False),
            "last_scanned_at": _now(),
            "updated_at": _now(),
            # A-07 §10.3: 'changed' for one scan round when size/mtime moved;
            # an unchanged follow-up scan restores 'present'.
            "availability": "changed" if changed else "present",
        }
        if changed and existing.sha256:
            fields["sha256_status"] = "stale"  # cache invalidation (§7.5)
        repo.update_fields(existing.id, **fields)

    # ------------------------------------------------------------------ hash
    def ensure_hash(
        self,
        checkpoint_id: str,
        *,
        progress: Callable[[int, int], None] | None = None,
    ) -> str:
        """Compute-or-reuse per the §7.5 cache key. Never transmitted anywhere."""
        with self._session_factory() as session:
            repo = CheckpointRepository(session)
            asset = repo.get(checkpoint_id)
            if asset is None:
                raise StatusTransitionError(f"找不到 checkpoint：{checkpoint_id}")
            path = Path(asset.path)
            stat = path.stat()
            # A2-09 cache key: path + size + st_mtime_ns (whole-second identity
            # allowed a same-size rewrite within one second to return a stale
            # hash — review §3.3)
            unchanged = (
                stat.st_size == asset.size_bytes
                and stat.st_mtime_ns == asset.modified_at_ns
            )
            if asset.sha256 and asset.sha256_status == "computed" and unchanged:
                return asset.sha256
            digest = sha256_file(path, progress=progress)
            repo.update_fields(
                checkpoint_id,
                sha256=digest,
                sha256_status="computed",
                hash_cached_at=_now(),
                # bind the fresh hash to the exact bytes just hashed
                size_bytes=stat.st_size,
                modified_at=datetime.fromtimestamp(stat.st_mtime, UTC).isoformat(
                    timespec="seconds"
                ),
                modified_at_ns=stat.st_mtime_ns,
                updated_at=_now(),
            )
            session.commit()
            return digest

    # ---------------------------------------------------------------- status
    def assign_profile(
        self,
        checkpoint_id: str,
        profile_id: str,
        *,
        known_profile_ids: tuple[str, ...],
    ) -> None:
        """A2-13: only an EXISTING checkpoint profile may be assigned; a
        profile ID is never arbitrary user text (review §4.7)."""
        if not profile_id:
            raise StatusTransitionError(
                "profile_id 不可為空；若要移除指派請使用 clear_profile_assignment。"
            )
        if profile_id not in known_profile_ids:
            raise StatusTransitionError(
                f"找不到 checkpoint profile：{profile_id}（僅能指派既有 profile）"
            )
        with self._session_factory() as session:
            repo = CheckpointRepository(session)
            if repo.get(checkpoint_id) is None:
                raise StatusTransitionError(f"找不到 checkpoint：{checkpoint_id}")
            repo.update_fields(
                checkpoint_id,
                assigned_profile_id=profile_id,
                status=CheckpointStatus.IDENTIFIED_BUT_UNTESTED.value,
                updated_at=_now(),
            )
            session.commit()

    def clear_profile_assignment(self, checkpoint_id: str) -> None:
        """A2-13: clearing is a distinct operation and returns the record to
        `unidentified` — never leaving a stale "identified" status behind."""
        with self._session_factory() as session:
            repo = CheckpointRepository(session)
            if repo.get(checkpoint_id) is None:
                raise StatusTransitionError(f"找不到 checkpoint：{checkpoint_id}")
            repo.update_fields(
                checkpoint_id,
                assigned_profile_id="",
                status=CheckpointStatus.UNIDENTIFIED.value,
                updated_at=_now(),
            )
            session.commit()

    def set_status(
        self,
        checkpoint_id: str,
        status: CheckpointStatus,
        *,
        usage_status: str = "",
        user_confirmed_recommendation: bool = False,
    ) -> None:
        with self._session_factory() as session:
            repo = CheckpointRepository(session)
            if repo.get(checkpoint_id) is None:
                raise StatusTransitionError(f"找不到 checkpoint：{checkpoint_id}")
            if status is CheckpointStatus.RECOMMENDED_PROFILE:
                # A2-14: a blank experiment row is NOT evidence (review §4.9)
                evidence = ExperimentRepository(session).list_for_checkpoint(
                    checkpoint_id
                )
                qualified = [
                    e
                    for e in evidence
                    if e.overall_rating is not None
                    and e.instruction_adherence is not None
                ]
                if not qualified:
                    raise StatusTransitionError(
                        "recommended_profile 需要至少一筆「已填整體評分＋指令遵循」"
                        "的實驗紀錄作為證據。"
                    )
                if not user_confirmed_recommendation:
                    raise StatusTransitionError(
                        "recommended_profile 需要使用者明確確認（"
                        "user_confirmed_recommendation=True）。"
                    )
            fields: dict[str, str | int] = {
                "status": status.value,
                "updated_at": _now(),
            }
            if usage_status:
                fields["usage_status"] = usage_status
            repo.update_fields(checkpoint_id, **fields)
            session.commit()

    # ------------------------------------------------------------------ read
    def list_all(self) -> list[CheckpointAsset]:
        with self._session_factory() as session:
            return CheckpointRepository(session).list_all()
