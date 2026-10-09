"""SQLite connections, schema inspection, and explicit migration operations."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory
from alembic.script.revision import ResolutionError
from alembic.util.exc import CommandError
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

_MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


class UnsupportedDatabaseError(RuntimeError):
    """The selected database belongs to an incompatible schema lineage."""


_UNSUPPORTED_DATABASE_MESSAGE = (
    "此資料庫格式與目前版本不相容，原檔不會被修改。"
    "請先備份原資料庫，再將 .env 中的 IMF_DATA_DIR 設為新的空資料夾後重新啟動。"
)


def create_db_engine(db_path: Path) -> Engine:
    """Engine with the mandatory per-connection PRAGMAs attached."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{db_path}", future=True)

    @event.listens_for(engine, "connect")
    def _set_pragmas(dbapi_conn: Any, _record: Any) -> None:
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.execute("PRAGMA busy_timeout = 5000")
        cursor.execute("PRAGMA journal_mode = WAL")
        cursor.execute("PRAGMA synchronous = NORMAL")
        cursor.close()

    return engine


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


def alembic_config_for(db_path: Path) -> AlembicConfig:
    """Programmatic Alembic config bound to this repo's migrations directory."""
    cfg = AlembicConfig()
    cfg.set_main_option("script_location", str(_MIGRATIONS_DIR))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    return cfg


@dataclass(frozen=True)
class MigrationStatus:
    """Current vs head revision for doctor/System Health."""

    current_revision: str | None
    head_revision: str | None
    is_current: bool
    database_exists: bool


def get_migration_status(db_path: Path) -> MigrationStatus:
    cfg = alembic_config_for(db_path)
    script = ScriptDirectory.from_config(cfg)
    head = script.get_current_head()
    if not db_path.exists():
        return MigrationStatus(None, head, False, database_exists=False)
    with closing(sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)) as connection:
        has_version_table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'alembic_version'"
        ).fetchone()
        revisions = (
            connection.execute("SELECT version_num FROM alembic_version").fetchall()
            if has_version_table else []
        )
    if len(revisions) > 1:
        raise UnsupportedDatabaseError(_UNSUPPORTED_DATABASE_MESSAGE)
    current = revisions[0][0] if revisions else None
    return MigrationStatus(current, head, current == head and head is not None, True)


def _validate_database_lineage(db_path: Path, status: MigrationStatus) -> None:
    if not status.database_exists:
        return
    if status.current_revision is not None:
        script = ScriptDirectory.from_config(alembic_config_for(db_path))
        try:
            script.get_revision(status.current_revision)
        except (ResolutionError, CommandError, KeyError) as exc:
            raise UnsupportedDatabaseError(_UNSUPPORTED_DATABASE_MESSAGE) from exc
        return
    with closing(sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)) as connection:
        has_data_tables = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' "
            "AND name NOT IN ('alembic_version', 'sqlite_sequence') LIMIT 1"
        ).fetchone()
    if has_data_tables:
        raise UnsupportedDatabaseError(_UNSUPPORTED_DATABASE_MESSAGE)


def initialize_database(db_path: Path) -> MigrationStatus:
    """Initialize an empty database; preserve existing data without auto-migration."""
    from alembic import command  # local import: alembic is heavy at import time

    db_path.parent.mkdir(parents=True, exist_ok=True)
    status = get_migration_status(db_path)
    _validate_database_lineage(db_path, status)
    if not status.database_exists or status.current_revision is None:
        command.upgrade(alembic_config_for(db_path), "head")
        return get_migration_status(db_path)
    return status


def upgrade_to_head(db_path: Path) -> MigrationStatus:
    """Explicit migration (used by tests and by a deliberate operator action)."""
    from alembic import command

    db_path.parent.mkdir(parents=True, exist_ok=True)
    _validate_database_lineage(db_path, get_migration_status(db_path))
    command.upgrade(alembic_config_for(db_path), "head")
    return get_migration_status(db_path)


def downgrade_to_base(db_path: Path) -> MigrationStatus:
    """Explicit full downgrade (tests / rollback drills)."""
    from alembic import command

    _validate_database_lineage(db_path, get_migration_status(db_path))
    command.downgrade(alembic_config_for(db_path), "base")
    return get_migration_status(db_path)
