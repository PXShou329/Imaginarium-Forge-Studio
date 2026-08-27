"""SQLite engine/session factory with the Phase 1 connection policy.

Connection policy (schema decision note §1.6, execution spec §7.3):
- every connection: PRAGMA foreign_keys = ON; PRAGMA busy_timeout = 5000
- journal_mode = WAL and synchronous = NORMAL are ENABLED after evaluation:
  single local user, Streamlit reruns issue overlapping reads, and the backup
  path uses SQLite's backup API (not naive file copy), which consolidates the
  WAL correctly. Implication recorded in docs/architecture/sqlite-settings.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from alembic.config import Config as AlembicConfig
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

_MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


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
    engine = create_db_engine(db_path)
    try:
        with engine.connect() as conn:
            current = MigrationContext.configure(conn).get_current_revision()
    finally:
        engine.dispose()
    return MigrationStatus(current, head, current == head and head is not None, True)


def initialize_database(db_path: Path) -> MigrationStatus:
    """Startup initialization (walking skeleton entry).

    Fresh database → upgrade to head. Existing database behind head → do NOT
    auto-migrate (safety: user must back up first; see WINDOWS_VALIDATION_CHECKLIST);
    the status is returned so the UI can show precise instructions.
    """
    from alembic import command  # local import: alembic is heavy at import time

    db_path.parent.mkdir(parents=True, exist_ok=True)
    status = get_migration_status(db_path)
    if not status.database_exists:
        command.upgrade(alembic_config_for(db_path), "head")
        return get_migration_status(db_path)
    return status


def upgrade_to_head(db_path: Path) -> MigrationStatus:
    """Explicit migration (used by tests and by a deliberate operator action)."""
    from alembic import command

    db_path.parent.mkdir(parents=True, exist_ok=True)
    command.upgrade(alembic_config_for(db_path), "head")
    return get_migration_status(db_path)


def downgrade_to_base(db_path: Path) -> MigrationStatus:
    """Explicit full downgrade (tests / rollback drills)."""
    from alembic import command

    command.downgrade(alembic_config_for(db_path), "base")
    return get_migration_status(db_path)
