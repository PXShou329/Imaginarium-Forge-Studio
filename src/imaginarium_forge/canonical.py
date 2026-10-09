"""Canonical JSON serialization for deterministic hashing.

Rules (normative for every fingerprint/manifest in this project):
sorted keys, compact separators, UTF-8, ensure_ascii=False. The same logical
object must always produce byte-identical output.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_json(obj: Any) -> str:
    """Serialize `obj` deterministically (sorted keys, compact separators)."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_of_canonical(obj: Any) -> str:
    """SHA-256 hex digest of the canonical JSON form of `obj`."""
    return hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()
