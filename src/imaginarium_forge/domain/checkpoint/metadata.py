"""Filename inference — the LOWEST metadata-resolution priority .

Only conservative, non-invented hints: base-model family guessed from common
filename substrings. Anything else stays empty and MetadataSource reflects it.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping

_FAMILY_HINTS: tuple[tuple[str, str], ...] = (
    ("illustrious", "Illustrious (SDXL family)"),
    ("noobai", "NoobAI (SDXL family)"),
    ("pony", "Pony (SDXL family)"),
    ("sdxl", "SDXL"),
    ("xl", "SDXL family (filename hint)"),
    ("sd15", "SD 1.5"),
    ("sd21", "SD 2.1"),
)

_MAX_SAFE_METADATA_KEY_CHARS = 96
_MAX_SAFE_METADATA_VALUE_CHARS = 160
_SAFE_METADATA_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-\[\]]{0,95}$")
_BLOCKED_METADATA_KEY_RE = re.compile(
    r"(?i)(?:^|[._\-\[\]])(?:"
    r"api_?key|apikey|access_?token|refresh_?token|token|secret|password|passwd|"
    r"credential|authorization|auth|bearer|connection|dsn|url|uri|path|file|"
    r"folder|dir|directory|prompt|instruction|caption|description|comment|note|"
    r"text|dataset|workflow|query|sql|json|html"
    r")(?:$|[._\-\[\]])"
)
_SENSITIVE_METADATA_VALUE_RE = re.compile(
    r"(?i)(?:"
    r"\bbearer\s+[A-Za-z0-9._~+/=-]{8,}|"
    r"\b(?:sk|rk|pk)-(?:proj-)?[A-Za-z0-9_-]{8,}\b|"
    r"\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|password|passwd|"
    r"secret|credential|authorization)\b|"
    r"\b(?:private|system|user)[ _-]?prompt\b|"
    r"\bprompt[ _-]?(?:sentinel|snapshot|payload|text)\b"
    r")"
)
_CONNECTION_METADATA_RE = re.compile(
    r"(?i)(?:[A-Za-z][A-Za-z0-9+.-]*://|"
    r"\b(?:data\s+source|server|host|database|user\s+id|uid|pwd|dsn)\s*=)"
)
_SQL_METADATA_RE = re.compile(
    r"(?i)\b(?:select|insert|update|delete|create|alter|drop|pragma|attach|detach)\b"
)
_WINDOWS_ABSOLUTE_PATH_RE = re.compile(r"(?i)(?:^|\s)[A-Z]:[\\/]")
_UNC_PATH_RE = re.compile(r"(?:^|\s)\\\\[^\\\s]+\\")
_POSIX_ABSOLUTE_PATH_RE = re.compile(r"(?:^|\s)/(?:[^/\s]+/)+[^\s]*")
_HTML_RE = re.compile(r"<\s*/?\s*[A-Za-z!][^>]*>")


def infer_base_model_hint(filename: str) -> str:
    """Return a conservative family hint or empty string. Never invents versions."""
    lowered = filename.casefold()
    for needle, hint in _FAMILY_HINTS:
        if needle in lowered:
            return hint
    return ""


def safe_checkpoint_filename(value: object, *, fallback: str = "checkpoint") -> str:
    """Return a bounded basename suitable for normal UI labels.

    Scanner-owned filenames are already basenames. Re-applying this projection
    also protects old or externally modified rows from turning an absolute path
    into a normal product label.
    """

    raw = str(value or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
    normalized = re.sub(r"[\x00-\x1f\x7f<>]", "_", raw)
    normalized = re.sub(r"\s+", " ", normalized).strip(" .")
    if not normalized or normalized in {".", ".."}:
        return fallback
    return normalized[:120]


def is_safe_checkpoint_metadata_key(path: str) -> bool:
    """Allow only short identifier-like metadata paths with no sensitive role."""

    normalized = str(path or "").strip()
    if not normalized:
        return True
    if len(normalized) > _MAX_SAFE_METADATA_KEY_CHARS:
        return False
    return bool(_SAFE_METADATA_KEY_RE.fullmatch(normalized)) and not bool(
        _BLOCKED_METADATA_KEY_RE.search(normalized)
    )


def safe_checkpoint_metadata_text(path: str, value: object) -> str | None:
    """Return one short harmless scalar, or ``None`` to hide the whole row.

    Metadata is attacker-controlled file input. Redaction markers would still
    reveal sensitive field names and can miss novel credential formats, so the
    normal UI uses a conservative allow-list: unsafe rows are omitted entirely.
    """

    if not is_safe_checkpoint_metadata_key(path):
        return None
    raw = str(value or "")
    normalized = re.sub(r"\s+", " ", raw).strip()
    if len(normalized) > _MAX_SAFE_METADATA_VALUE_CHARS:
        return None
    if any(
        pattern.search(normalized)
        for pattern in (
            _SENSITIVE_METADATA_VALUE_RE,
            _CONNECTION_METADATA_RE,
            _SQL_METADATA_RE,
            _WINDOWS_ABSOLUTE_PATH_RE,
            _UNC_PATH_RE,
            _POSIX_ABSOLUTE_PATH_RE,
            _HTML_RE,
        )
    ):
        return None
    if normalized.startswith(("{", "[")):
        try:
            structured = json.loads(normalized)
        except (TypeError, ValueError):
            return None
        if isinstance(structured, dict | list):
            return None
    return normalized


def sanitize_checkpoint_metadata(metadata: Mapping[object, object]) -> dict[str, str]:
    """Drop unsafe safetensors metadata before it crosses persistence boundaries."""

    safe: dict[str, str] = {}
    for raw_key, raw_value in metadata.items():
        if not isinstance(raw_key, str) or not isinstance(raw_value, str):
            continue
        value = safe_checkpoint_metadata_text(raw_key, raw_value)
        if value is not None:
            safe[raw_key] = value
    return safe


__all__ = [
    "infer_base_model_hint",
    "is_safe_checkpoint_metadata_key",
    "safe_checkpoint_filename",
    "safe_checkpoint_metadata_text",
    "sanitize_checkpoint_metadata",
]
