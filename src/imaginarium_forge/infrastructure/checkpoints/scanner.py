"""Read-only checkpoint scanner (spec §7).

Safety rules enforced here (§7.2):

- stays inside the configured root: every candidate is `resolve()`d and must
  remain within the resolved root, so symlinks/`..` pointing outside are
  SKIPPED (recorded, never followed);
- `.ckpt` files are inventoried by stat() only — their pickle payload is
  NEVER opened or deserialized (this module imports neither pickle nor torch);
- `.safetensors` reads are header-only: 8-byte little-endian length + JSON
  header, bounded by `_MAX_HEADER_BYTES`; tensor data is never touched;
- all embedded metadata is treated as untrusted opaque strings;
- no network, no writes, no renames, no deletes.

Hashing (§7.5) is OPTIONAL and separate from scanning; cache invalidation is
the caller's job (registry service) keyed on path + size + mtime.
"""

from __future__ import annotations

import hashlib
import json
import struct
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from imaginarium_forge.domain.checkpoint.asset import ALLOWED_EXTENSIONS

#: safetensors headers are small (KBs); 16 MiB is a generous DoS bound.
_MAX_HEADER_BYTES = 16 * 1024 * 1024
_HASH_CHUNK = 1024 * 1024


class ScanRootError(RuntimeError):
    """The configured root is missing or not a directory."""


class ScannedCheckpoint(BaseModel):
    model_config = ConfigDict(frozen=True)

    path: str            # absolute, inside the root
    filename: str
    extension: str
    size_bytes: int
    modified_at: str     # ISO-8601 UTC
    modified_at_ns: int  # st_mtime_ns — high-resolution cache identity (A2-09)
    header_metadata: dict[str, str]  # safetensors __metadata__ verbatim; {} for .ckpt
    header_ok: bool      # False → malformed/absent header (still inventoried)


class ScanResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    root: str
    checkpoints: tuple[ScannedCheckpoint, ...] = ()
    skipped_outside_root: tuple[str, ...] = ()
    skipped_extension: tuple[str, ...] = ()


def _mtime_iso(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat(
        timespec="seconds"
    )


def read_safetensors_header(path: Path) -> tuple[dict[str, str], bool]:
    """Header-only read. Returns (metadata, header_ok). Never touches tensors."""
    try:
        with path.open("rb") as handle:
            prefix = handle.read(8)
            if len(prefix) != 8:
                return {}, False
            (header_len,) = struct.unpack("<Q", prefix)
            if header_len <= 0 or header_len > _MAX_HEADER_BYTES:
                return {}, False
            if header_len > max(path.stat().st_size - 8, 0):
                return {}, False
            header = json.loads(handle.read(header_len).decode("utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return {}, False
    if not isinstance(header, dict):
        return {}, False
    metadata = header.get("__metadata__", {})
    if not isinstance(metadata, dict):
        return {}, True  # header valid; metadata section malformed → ignore it
    # untrusted: keep only str→str pairs verbatim, no interpretation
    return {
        str(key): str(value)
        for key, value in metadata.items()
        if isinstance(key, str) and isinstance(value, str)
    }, True


def scan_root(root: Path) -> ScanResult:
    """Inventory one root, read-only. Raises ScanRootError for a bad root."""
    try:
        resolved_root = root.resolve(strict=True)
    except OSError as exc:
        raise ScanRootError(f"掃描根目錄不存在：{root}") from exc
    if not resolved_root.is_dir():
        raise ScanRootError(f"掃描根目錄不是資料夾：{root}")

    found: list[ScannedCheckpoint] = []
    outside: list[str] = []
    wrong_ext: list[str] = []

    for candidate in sorted(resolved_root.rglob("*")):
        if not candidate.is_file():
            continue
        resolved = candidate.resolve()
        if not resolved.is_relative_to(resolved_root):
            outside.append(str(candidate))  # symlink/traversal escape → skip
            continue
        extension = resolved.suffix.lower()
        if extension not in ALLOWED_EXTENSIONS:
            wrong_ext.append(str(resolved))
            continue
        if extension == ".safetensors":
            metadata, header_ok = read_safetensors_header(resolved)
        else:  # .ckpt: stat-only inventory; pickle payload never opened
            metadata, header_ok = {}, False
        found.append(
            ScannedCheckpoint(
                path=str(resolved),
                filename=resolved.name,
                extension=extension,
                size_bytes=resolved.stat().st_size,
                modified_at=_mtime_iso(resolved),
                modified_at_ns=resolved.stat().st_mtime_ns,
                header_metadata=metadata,
                header_ok=header_ok,
            )
        )

    return ScanResult(
        root=str(resolved_root),
        checkpoints=tuple(found),
        skipped_outside_root=tuple(outside),
        skipped_extension=tuple(wrong_ext),
    )


def sha256_file(
    path: Path, *, progress: Callable[[int, int], None] | None = None
) -> str:
    """Streaming SHA-256. Read-only; optional progress(bytes_done, total)."""
    total = path.stat().st_size
    digest = hashlib.sha256()
    done = 0
    with path.open("rb") as handle:
        while chunk := handle.read(_HASH_CHUNK):
            digest.update(chunk)
            done += len(chunk)
            if progress is not None:
                progress(done, total)
    return digest.hexdigest()
