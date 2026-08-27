"""Stable identifiers and timestamps (UUID4 strings; timezone-aware UTC)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime


def new_id() -> str:
    return uuid.uuid4().hex


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()
