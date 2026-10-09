"""Thin application wrapper over the infrastructure BackupService."""

from __future__ import annotations

from pathlib import Path

from imaginarium_forge.config.settings import AppSettings
from imaginarium_forge.infrastructure.backup.service import (
    BackupManifest,
    BackupService,
    RestoreReport,
)
from imaginarium_forge.infrastructure.db.lifecycle import DatabaseLifecycleManager


class BackupAppService:
    def __init__(self, settings: AppSettings, lifecycle: DatabaseLifecycleManager) -> None:
        self._service = BackupService(settings.database_path, settings.backups_dir)
        self._lifecycle = lifecycle

    def create_backup(self) -> BackupManifest:
        return self._service.create_backup()

    def verify_backup(self, backup_path: Path) -> bool:
        return self._service.verify_backup(backup_path)

    def list_backups(self) -> list[Path]:
        backups_dir = self._service._backups_dir
        if not backups_dir.exists():
            return []
        return sorted(backups_dir.glob("backup_*.db"), reverse=True)

    def restore_backup(self, backup_path: Path, *, confirm: bool) -> RestoreReport:
        return self._service.restore_backup(
            backup_path, confirm=confirm, lifecycle=self._lifecycle
        )
