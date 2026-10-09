"""Explicit, bounded local translation history, separate from the execution path.

The default UI never invokes save/read. No SQLite, credentials, provider, network,
logging or backup integration exists here. Credential-like content is refused
before any filesystem write, with a fixed error that contains no author text.
"""

from __future__ import annotations

import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

_LOCK = RLock()
_MAX_BYTES = 2_000_000
_MAX_ENTRIES = 20
_SECRET_PATTERN = re.compile(
    r"sk-[A-Za-z0-9_-]{12,}|-----BEGIN [A-Z ]*PRIVATE KEY-----|"
    r"(?:api[_ -]?key|authorization|access[_ -]?token|secret[_ -]?key)"
    r"\s*[:=]\s*[\"']?[^\s\"']{8,}|\bBearer\s+[A-Za-z0-9._~+/-]{12,}",
    re.IGNORECASE,
)


class TranslationHistoryError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("translation history operation did not complete")


class TranslationHistorySensitiveContentError(TranslationHistoryError):
    """The requested entry contained credential-like data; no write occurred."""


class TranslationHistoryEntry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    entry_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    created_at: datetime
    direction: str = Field(pattern=r"^(zh_tw_to_en|en_to_zh_tw)$")
    source_text: str = Field(min_length=1, max_length=100_000, repr=False)
    target_text: str = Field(max_length=250_000, repr=False)
    status: str = Field(pattern=r"^(completed|cancelled|failed)$")


class TranslationHistoryService:
    def __init__(self, directory: Path) -> None:
        self._directory = directory
        self._path = directory / "history.json"

    def _assert_local_paths(self) -> None:
        # Refuse linked parents as well as the leaf; never follow a junction to
        # a different data owner while saving or clearing author-approved text.
        for path in (self._path, self._directory, *self._directory.parents):
            if path.is_symlink() or path.is_junction():
                raise TranslationHistoryError()

    def list_entries(self) -> tuple[TranslationHistoryEntry, ...]:
        with _LOCK:
            try:
                self._assert_local_paths()
                if not self._path.exists():
                    return ()
                if self._path.stat().st_size > _MAX_BYTES:
                    raise TranslationHistoryError()
                value = json.loads(self._path.read_bytes())
                if not isinstance(value, dict) or value.get("schema") != "translation-history-v1":
                    raise TranslationHistoryError()
                raw = value.get("entries")
                if not isinstance(raw, list) or len(raw) > _MAX_ENTRIES:
                    raise TranslationHistoryError()
                entries = tuple(TranslationHistoryEntry.model_validate(entry) for entry in raw)
                if len({entry.entry_id for entry in entries}) != len(entries):
                    raise TranslationHistoryError()
                if any(
                    _SECRET_PATTERN.search(entry.source_text)
                    or _SECRET_PATTERN.search(entry.target_text)
                    for entry in entries
                ):
                    raise TranslationHistorySensitiveContentError()
                return entries
            except (OSError, ValueError):
                raise TranslationHistoryError() from None

    def save(self, *, source_text: str, target_text: str, direction: str, status: str) -> None:
        if _SECRET_PATTERN.search(source_text) or _SECRET_PATTERN.search(target_text):
            raise TranslationHistorySensitiveContentError()
        try:
            entry = TranslationHistoryEntry(
                entry_id=uuid4().hex,
                created_at=datetime.now(UTC),
                direction=direction,
                source_text=source_text,
                target_text=target_text,
                status=status,
            )
        except ValueError:
            raise TranslationHistoryError() from None
        with _LOCK:
            entries = (entry, *self.list_entries())[:_MAX_ENTRIES]
            payload = self._serialize(entries)
            while len(payload) > _MAX_BYTES and len(entries) > 1:
                entries = entries[:-1]
                payload = self._serialize(entries)
            if len(payload) > _MAX_BYTES:
                raise TranslationHistoryError()
            self._write(payload)

    def clear(self) -> None:
        """An explicit clear writes an empty history, never keeps a hidden backup."""
        with _LOCK:
            try:
                self._assert_local_paths()
                if self._path.exists():
                    self._write(self._serialize(()))
            except OSError:
                raise TranslationHistoryError() from None

    @staticmethod
    def _serialize(entries: tuple[TranslationHistoryEntry, ...]) -> bytes:
        try:
            return json.dumps(
                {
                    "schema": "translation-history-v1",
                    "entries": [entry.model_dump(mode="json") for entry in entries],
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        except (ValueError, TypeError):
            raise TranslationHistoryError() from None

    def _write(self, payload: bytes) -> None:
        temporary = self._directory / f".history-{uuid4().hex}.part"
        try:
            self._assert_local_paths()
            self._directory.mkdir(parents=True, exist_ok=True)
            with temporary.open("xb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self._path)
        except OSError:
            raise TranslationHistoryError() from None
        finally:
            try:
                if temporary.exists():
                    temporary.unlink()
            except OSError:
                raise TranslationHistoryError() from None
