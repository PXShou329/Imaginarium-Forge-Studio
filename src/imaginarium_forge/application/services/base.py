"""Shared service plumbing: one transaction per user command."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import TypeVar

from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from imaginarium_forge.application.errors import ApplicationError, ConflictError

T = TypeVar("T")

#: Anything that yields a Session when called: a plain sessionmaker (tests) or a
#: DatabaseLifecycleManager (the app; may raise MaintenanceModeError).
SessionProvider = Callable[[], Session]


class ServiceBase:
    """A command either succeeds completely or rolls back (spec §7.2)."""

    def __init__(self, session_factory: SessionProvider) -> None:
        self._session_factory = session_factory

    @contextmanager
    def _transaction(self) -> Iterator[Session]:
        session = self._session_factory()
        try:
            yield session
            session.commit()
        except IntegrityError as exc:
            session.rollback()
            raise ConflictError(f"資料完整性衝突：{exc.orig}") from exc
        except ApplicationError:
            session.rollback()
            raise
        except SQLAlchemyError as exc:
            session.rollback()
            raise ApplicationError(f"資料庫操作失敗：{type(exc).__name__}") from exc
        finally:
            session.close()

    def _read_only(self, fn: Callable[[Session], T]) -> T:
        session = self._session_factory()
        try:
            return fn(session)
        finally:
            session.close()
