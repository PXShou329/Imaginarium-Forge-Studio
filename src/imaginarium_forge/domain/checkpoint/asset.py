"""Checkpoint asset model — read-only scanning results (spec §33)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from imaginarium_forge.domain.checkpoint.classification import CheckpointStatus, MetadataSource

#: extension allowlist; .ckpt is listed but its pickle payload is NEVER deserialized
ALLOWED_EXTENSIONS: tuple[str, ...] = (".safetensors", ".ckpt")


class CheckpointAsset(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    path: str                # absolute path INSIDE a configured scan root
    filename: str
    extension: str
    size_bytes: int
    modified_at: str         # ISO timestamp of file mtime
    modified_at_ns: int = 0  # st_mtime_ns — cache identity (A2-09)
    sha256: str = ""         # optional; computed on demand, cached
    hash_cached_at: str = ""
    status: CheckpointStatus = CheckpointStatus.UNIDENTIFIED
    display_name: str = ""
    base_model_hint: str = ""    # e.g. "SDXL / Illustrious" — never invented
    metadata_source: MetadataSource = MetadataSource.UNKNOWN
    assigned_profile_id: str = ""
    #: user-facing usage note (e.g. "current_main_use_candidate"); free-form
    usage_status: str = ""
    #: not_computed | computed | stale (size/mtime changed since hashing)
    sha256_status: str = "not_computed"
    #: safetensors header __metadata__ verbatim (UNTRUSTED strings), JSON text
    local_metadata_json: str = "{}"
    last_scanned_at: str = ""
    #: present | missing | changed — reconciled on every rescan (A-07 §10.3)
    availability: str = "present"
    notes: str = ""
    created_at: str = ""
    updated_at: str = ""
