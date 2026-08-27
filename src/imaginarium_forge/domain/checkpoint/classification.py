"""Checkpoint status + metadata-source enums (spec §33.4/§33.6)."""

from __future__ import annotations

from enum import StrEnum


class CheckpointStatus(StrEnum):
    UNIDENTIFIED = "unidentified"
    IDENTIFIED_BUT_UNTESTED = "identified_but_untested"
    TESTING = "testing"
    RECOMMENDED_PROFILE = "recommended_profile"
    PERSONAL_FAVORITE = "personal_favorite"
    UNSUITABLE = "unsuitable"


class MetadataSource(StrEnum):
    """Resolution priority: manual > safetensors metadata > sidecar JSON > filename > unknown."""

    MANUAL = "manual"
    SAFETENSORS_METADATA = "safetensors_metadata"
    SIDECAR_JSON = "sidecar_json"
    FILENAME_INFERENCE = "filename_inference"
    UNKNOWN = "unknown"


#: recommended_profile requires experiment evidence (spec §33.6)
STATUSES_REQUIRING_EVIDENCE: frozenset[CheckpointStatus] = frozenset(
    {CheckpointStatus.RECOMMENDED_PROFILE}
)
