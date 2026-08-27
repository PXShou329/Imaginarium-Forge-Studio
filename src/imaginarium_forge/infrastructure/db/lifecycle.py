"""Database lifecycle coordination (A-04, spec §8.4).

The manager owns the engine/session factory and is itself a callable session
provider, so application services can be constructed with either a plain
`sessionmaker` (tests) or a manager (the app). During maintenance mode any
attempt to open a new session fails fast with MaintenanceModeError — this is
what lets the restore lifecycle guarantee no new write transaction starts
while the database file is being replaced.

Engine disposal logic lives HERE, never in Streamlit page code.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from imaginarium_forge.infrastructure.db.session import (
    create_db_engine,
    create_session_factory,
)


class MaintenanceModeError(RuntimeError):
    """Raised when a session is requested while the database is under maintenance."""


class DatabaseLifecycleManager:
    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._engine: Engine | None = None
        self._factory: sessionmaker[Session] | None = None
        self._maintenance = False

    @property
    def db_path(self) -> Path:
        return self._db_path

    @property
    def in_maintenance(self) -> bool:
        return self._maintenance

    def _ensure_built(self) -> sessionmaker[Session]:
        if self._engine is None or self._factory is None:
            self._engine = create_db_engine(self._db_path)
            self._factory = create_session_factory(self._engine)
        return self._factory

    def __call__(self) -> Session:
        """Session provider (same call shape as sessionmaker)."""
        if self._maintenance:
            raise MaintenanceModeError(
                "資料庫維護中（還原進行中）；請稍候並重新整理後再試。"
            )
        return self._ensure_built()()

    def enter_maintenance_mode(self) -> None:
        """Block new sessions. Existing sessions must be closed by dispose()."""
        self._maintenance = True

    def exit_maintenance_mode(self) -> None:
        self._maintenance = False

    def dispose(self) -> None:
        """Close all pooled connections and drop the engine."""
        if self._engine is not None:
            self._engine.dispose()
        self._engine = None
        self._factory = None

    def rebuild(self) -> None:
        """Build a fresh engine over the (possibly replaced) database file."""
        self.dispose()
        self._ensure_built()
