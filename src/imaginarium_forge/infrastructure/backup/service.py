"""Database backup and restore (A-04, spec §8 — WAL/Windows-safe lifecycle).

Backups use SQLite's online backup API (consolidates WAL correctly). Restore
follows a coordinated lifecycle that never overwrites the active database
while pooled connections remain open and never leaves the previous database
unrecoverable:

    verify requested backup integrity (against a TEMP copy)
    → enter maintenance mode (block new sessions)
    → create pre-restore safety backup via SQLite backup API (never copy2)
    → dispose SQLAlchemy engine (close pooled connections)
    → restore into a temporary path next to the live database
    → integrity_check the temporary database
    → verify the Alembic revision of the temporary database
    → atomically replace the live database (os.replace, same filesystem)
    → remove stale -wal / -shm sidecar files
    → rebuild the engine
    → post-restore integrity_check through the new engine
    → exit maintenance mode

Every failure before the atomic replace leaves the live database untouched;
a failure at or after the replace leaves the pre-restore safety backup on
disk and reported. Windows-specific file-locking behavior remains PENDING
local validation (see WINDOWS_VALIDATION_CHECKLIST.md).
"""

from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from imaginarium_forge import __version__
from imaginarium_forge.canonical import canonical_json
from imaginarium_forge.infrastructure.db.lifecycle import DatabaseLifecycleManager


@dataclass(frozen=True)
class BackupManifest:
    created_at: str
    source_path: str
    backup_path: str
    size_bytes: int
    sha256: str
    app_version: str
    integrity_check: str


@dataclass
class RestoreReport:
    """Structured record of a restore operation (spec §8.3: record status/errors)."""

    status: str = "not_started"  # not_started | succeeded | aborted | failed
    steps_completed: list[str] = field(default_factory=list)
    error: str | None = None
    safety_backup_path: str | None = None
    restart_required: bool = False

    def step(self, name: str) -> None:
        self.steps_completed.append(name)


def _sha256_of_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _integrity_check(db_path: Path) -> str:
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute("PRAGMA integrity_check").fetchone()
        return str(row[0]) if row else "unknown"
    except sqlite3.DatabaseError as exc:
        return f"not a database: {exc}"
    finally:
        conn.close()


def _sqlite_backup(source: Path, dest: Path) -> None:
    """Consolidating copy via SQLite's online backup API (WAL-safe)."""
    src_conn = sqlite3.connect(str(source))
    try:
        dest_conn = sqlite3.connect(str(dest))
        try:
            src_conn.backup(dest_conn)
        finally:
            dest_conn.close()
    finally:
        src_conn.close()


def _alembic_revision_of(db_path: Path) -> str | None:
    conn = sqlite3.connect(str(db_path))
    try:
        try:
            row = conn.execute("SELECT version_num FROM alembic_version").fetchone()
        except sqlite3.OperationalError:
            return None
        return str(row[0]) if row else None
    finally:
        conn.close()


def _expected_head_revision() -> str | None:
    from alembic.script import ScriptDirectory

    from imaginarium_forge.infrastructure.db.session import alembic_config_for

    # script location is path-independent; any db path yields the same head
    script = ScriptDirectory.from_config(alembic_config_for(Path("unused.db")))
    return script.get_current_head()


class BackupService:
    def __init__(self, database_path: Path, backups_dir: Path) -> None:
        self._db_path = database_path
        self._backups_dir = backups_dir

    # ------------------------------------------------------------------ backup

    def create_backup(self) -> BackupManifest:
        """Create a timestamped, integrity-checked backup + manifest."""
        if not self._db_path.exists():
            raise FileNotFoundError(f"資料庫不存在，無法備份：{self._db_path}")
        self._backups_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        backup_path = self._backups_dir / f"backup_{stamp}.db"
        _sqlite_backup(self._db_path, backup_path)

        manifest = BackupManifest(
            created_at=datetime.now(UTC).isoformat(),
            source_path=str(self._db_path),
            backup_path=str(backup_path),
            size_bytes=backup_path.stat().st_size,
            sha256=_sha256_of_file(backup_path),
            app_version=__version__,
            integrity_check=_integrity_check(backup_path),
        )
        manifest_path = backup_path.with_suffix(".manifest.json")
        manifest_path.write_text(canonical_json(asdict(manifest)), encoding="utf-8")
        return manifest

    def verify_backup(self, backup_path: Path) -> bool:
        """Restore the backup into a TEMP path and integrity-check it there."""
        if not backup_path.exists():
            raise FileNotFoundError(f"備份檔不存在：{backup_path}")
        with tempfile.TemporaryDirectory() as tmp:
            temp_db = Path(tmp) / "verify.db"
            shutil.copy2(backup_path, temp_db)  # backup file is quiescent
            return _integrity_check(temp_db) == "ok"

    # ----------------------------------------------------------------- restore

    def restore_backup(
        self,
        backup_path: Path,
        *,
        confirm: bool,
        lifecycle: DatabaseLifecycleManager,
    ) -> RestoreReport:
        """Coordinated restore lifecycle (spec §8.2). Requires explicit confirm."""
        report = RestoreReport()
        if not confirm:
            raise ValueError("還原為破壞性操作，必須顯式確認（confirm=True）")
        if not backup_path.exists():
            raise FileNotFoundError(f"備份檔不存在：{backup_path}")

        # 0) verify the REQUESTED backup before touching anything live
        if not self.verify_backup(backup_path):
            report.status = "aborted"
            report.error = "備份完整性檢查未通過，已中止還原（正式庫未被更動）"
            return report
        report.step("verify_requested_backup")

        lifecycle.enter_maintenance_mode()
        report.step("enter_maintenance_mode")
        temp_restore = self._db_path.with_name(self._db_path.name + ".restore-tmp")
        try:
            # 1) pre-restore safety backup of the CURRENT live db (backup API)
            if self._db_path.exists():
                self._backups_dir.mkdir(parents=True, exist_ok=True)
                stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
                safety = self._backups_dir / f"pre_restore_{stamp}.db"
                _sqlite_backup(self._db_path, safety)
                if _integrity_check(safety) != "ok":
                    report.status = "aborted"
                    report.error = "還原前安全備份完整性檢查失敗，已中止（正式庫未被更動）"
                    return report
                report.safety_backup_path = str(safety)
                report.step("pre_restore_safety_backup")

            # 2) close pooled connections before file replacement
            lifecycle.dispose()
            report.step("dispose_engine")

            # 3) restore into a TEMP path next to the live db (same filesystem)
            shutil.copy2(backup_path, temp_restore)
            report.step("restore_to_temp")

            # 4) integrity_check the temporary database
            if _integrity_check(temp_restore) != "ok":
                report.status = "aborted"
                report.error = "暫存還原庫完整性檢查失敗，已中止（正式庫未被更動）"
                return report
            report.step("temp_integrity_check")

            # 5) verify Alembic revision
            expected = _expected_head_revision()
            actual = _alembic_revision_of(temp_restore)
            if actual != expected:
                report.status = "aborted"
                report.error = (
                    f"備份的 migration 版本（{actual}）與目前程式（{expected}）不一致；"
                    "已中止還原。請先確認備份世代或執行相容的升級流程。"
                )
                return report
            report.step("verify_alembic_revision")

            # 6) atomic replacement (os.replace: atomic on same filesystem)
            os.replace(temp_restore, self._db_path)
            report.step("atomic_replace")

            # 7) reconcile stale WAL/SHM sidecars from the previous database
            for suffix in ("-wal", "-shm"):
                stale = self._db_path.with_name(self._db_path.name + suffix)
                if stale.exists():
                    stale.unlink()
            report.step("reconcile_stale_wal_shm")

            # 8) rebuild engine and run the post-restore integrity check
            lifecycle.rebuild()
            report.step("rebuild_engine")
            if _integrity_check(self._db_path) != "ok":
                report.status = "failed"
                report.error = (
                    "還原後完整性檢查失敗。請以還原前安全備份"
                    f"（{report.safety_backup_path}）回復。"
                )
                return report
            report.step("post_restore_integrity_check")

            report.status = "succeeded"
            report.restart_required = True  # Streamlit sessions must refresh services
            return report
        except Exception as exc:
            report.status = "failed"
            report.error = (
                f"{type(exc).__name__}: {exc}。正式庫可由還原前安全備份"
                f"（{report.safety_backup_path or '尚未建立'}）回復。"
            )
            return report
        finally:
            if temp_restore.exists():  # never leave the temp file behind
                temp_restore.unlink()
            lifecycle.exit_maintenance_mode()
