"""Strict, path-safe model-pack metadata for the offline translation runtime."""

from __future__ import annotations

import hashlib
import ipaddress
import re
from datetime import datetime
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Self
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from imaginarium_forge.domain.translation.direction import TranslationDirection

PACK_MANIFEST_SCHEMA = "imaginarium-translation-pack-v1"
PACK_INSTALL_RECEIPT_SCHEMA = "imaginarium-translation-install-v1"
PACK_MANIFEST_FILENAME = "manifest.json"
PACK_INSTALL_RECEIPT_FILENAME = "install-receipt.json"
ACTIVE_PACK_FILENAME = "active-pack.json"

_SAFE_ID_PATTERN = r"^[a-z0-9][a-z0-9._-]{0,127}$"
_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_VERSION_PATTERN = r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.-]+)?$"
_CREDENTIAL_LIKE = re.compile(
    r"(?i)(?:\bsk-[A-Za-z0-9_-]{16,}|\b(?:api[-_ ]?key|authorization)\s*[:=]|"
    r"\bbearer\s+[A-Za-z0-9._~-]{12,})"
)
_PUBLIC_DNS_NAME = re.compile(
    r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$"
)
_WINDOWS_RESERVED_NAMES = frozenset(
    {"con", "prn", "aux", "nul", "conin$", "conout$"}
    | {f"com{index}" for index in range(1, 10)}
    | {f"lpt{index}" for index in range(1, 10)}
)


def normalize_relative_pack_path(value: str) -> str:
    """Return one portable relative path or fail closed on traversal/ambiguity."""

    if not value or "\x00" in value or "\\" in value or ":" in value:
        raise ValueError("pack paths must be non-empty portable relative paths")
    candidate = PurePosixPath(value)
    if candidate.is_absolute() or any(part in {"", ".", ".."} for part in candidate.parts):
        raise ValueError("pack paths must not be absolute or traverse directories")
    normalized = candidate.as_posix()
    if normalized != value:
        raise ValueError("pack paths must be normalized POSIX relative paths")
    for part in candidate.parts:
        if len(part) > 240 or any(char in '<>"|?*' for char in part):
            raise ValueError("pack paths must use portable filename components")
        if part.endswith((" ", ".")):
            raise ValueError("pack paths must not use Windows-trimmed names")
        stem = part.split(".", maxsplit=1)[0].rstrip(" .").casefold()
        if stem in _WINDOWS_RESERVED_NAMES:
            raise ValueError("pack paths must not use reserved device names")
        if any(ord(char) < 32 for char in part):
            raise ValueError("pack paths must not contain control characters")
    return normalized


def _validate_public_host(hostname: str) -> str:
    normalized = hostname.rstrip(".").casefold()
    if (
        not normalized
        or normalized == "localhost"
        or normalized.endswith((".localhost", ".local", ".internal"))
    ):
        raise ValueError("pack URLs must not target local hosts")
    try:
        address = ipaddress.ip_address(normalized)
    except ValueError:
        if not _PUBLIC_DNS_NAME.fullmatch(normalized):
            raise ValueError("pack URLs must use a public fully-qualified host") from None
    else:
        if not address.is_global:
            raise ValueError("pack URLs must not target private or reserved addresses")
    return normalized


def _validate_https_url(value: str) -> str:
    parsed = urlsplit(value)
    try:
        port = parsed.port
    except ValueError:
        raise ValueError("pack URLs must contain a valid port") from None
    if port == 0:
        raise ValueError("pack URLs must contain a valid port")
    if (
        parsed.scheme.lower() != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("pack URLs must be credential-free HTTPS URLs")
    _validate_public_host(parsed.hostname)
    return value


class TranslationPackFile(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    relative_path: str = Field(min_length=1, max_length=500)
    sha256: str = Field(pattern=_SHA256_PATTERN)
    size_bytes: int = Field(ge=0)
    download_url: str | None = Field(default=None, max_length=2_000)
    allowed_redirect_hosts: tuple[str, ...] = ()

    @field_validator("relative_path")
    @classmethod
    def _safe_path(cls, value: str) -> str:
        return normalize_relative_pack_path(value)

    @field_validator("sha256")
    @classmethod
    def _lowercase_digest(cls, value: str) -> str:
        return value.lower()

    @field_validator("download_url")
    @classmethod
    def _https_download(cls, value: str | None) -> str | None:
        return None if value is None else _validate_https_url(value)

    @field_validator("allowed_redirect_hosts")
    @classmethod
    def _public_redirect_hosts(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(_validate_public_host(host) for host in value)
        if len(set(normalized)) != len(normalized):
            raise ValueError("redirect host allow-list must be unique")
        return normalized


class TranslationModelSpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    model_id: str = Field(pattern=_SAFE_ID_PATTERN)
    direction: TranslationDirection
    model_path: str = Field(min_length=1, max_length=500)
    source_tokenizer: str = Field(min_length=1, max_length=500)
    target_tokenizer: str = Field(min_length=1, max_length=500)
    source_prefix: str = Field(default="", pattern=r"^(|>>cmn_Hant<<|>>cmn_Hans<<)$")
    max_input_tokens: int = Field(default=480, ge=64, le=4_096)
    max_decoding_tokens: int = Field(default=768, ge=64, le=8_192)

    @field_validator("model_path", "source_tokenizer", "target_tokenizer")
    @classmethod
    def _safe_file_reference(cls, value: str) -> str:
        return normalize_relative_pack_path(value)

    @model_validator(mode="after")
    def _ctranslate2_model_file_is_explicit(self) -> Self:
        if PurePosixPath(self.model_path).name.casefold() != "model.bin":
            raise ValueError("CTranslate2 model_path must identify model.bin")
        if self.direction is TranslationDirection.ZH_TW_TO_EN and self.source_prefix:
            raise ValueError("Chinese target prefixes require the English to Chinese direction")
        return self


class TranslationPackManifest(BaseModel):
    """Canonical manifest consumed by both local and explicit-download installs."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    manifest_schema: str = Field(default=PACK_MANIFEST_SCHEMA)
    pack_id: str = Field(pattern=_SAFE_ID_PATTERN)
    version: str = Field(pattern=_VERSION_PATTERN)
    directions: tuple[TranslationDirection, ...] = Field(min_length=1)
    backend: str = Field(pattern=r"^ctranslate2$")
    models: tuple[TranslationModelSpec, ...] = Field(min_length=1)
    files: tuple[TranslationPackFile, ...] = Field(min_length=1)
    license_summary: str = Field(min_length=1, max_length=4_000)
    source_urls: tuple[str, ...] = ()
    disk_bytes: int = Field(ge=0)
    minimum_ram_bytes: int = Field(ge=0)
    created_at: datetime

    @field_validator("source_urls")
    @classmethod
    def _https_sources(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        validated = tuple(_validate_https_url(url) for url in value)
        if len(set(validated)) != len(validated):
            raise ValueError("pack source URLs must be unique")
        return validated

    @field_validator("license_summary")
    @classmethod
    def _license_is_nonsecret_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("license_summary must not be blank")
        if _CREDENTIAL_LIKE.search(value):
            raise ValueError("license_summary must not contain credentials")
        return value.strip()

    @field_validator("created_at")
    @classmethod
    def _created_at_has_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("created_at must include a timezone")
        return value

    @model_validator(mode="after")
    def _cross_validate_manifest(self) -> Self:
        if self.manifest_schema != PACK_MANIFEST_SCHEMA:
            raise ValueError("unsupported translation pack manifest schema")
        if len(set(self.directions)) != len(self.directions):
            raise ValueError("pack directions must be unique")
        paths = [entry.relative_path for entry in self.files]
        if len({path.casefold() for path in paths}) != len(paths):
            raise ValueError("pack file paths must be unique")
        reserved_metadata = {
            ACTIVE_PACK_FILENAME,
            PACK_INSTALL_RECEIPT_FILENAME,
            PACK_MANIFEST_FILENAME,
        }
        if any(path.casefold() in reserved_metadata for path in paths):
            raise ValueError("pack payload must not replace managed metadata")
        file_paths = set(paths)
        file_sizes = {entry.relative_path: entry.size_bytes for entry in self.files}
        model_ids: set[str] = set()
        model_directions: set[TranslationDirection] = set()
        for model in self.models:
            if model.model_id in model_ids:
                raise ValueError("model IDs must be unique")
            if model.direction in model_directions:
                raise ValueError("each pack direction must resolve to exactly one model")
            model_ids.add(model.model_id)
            model_directions.add(model.direction)
            required = {model.model_path, model.source_tokenizer, model.target_tokenizer}
            if not required.issubset(file_paths):
                raise ValueError("every model and tokenizer path must be checksum-pinned")
            if any(file_sizes[path] <= 0 for path in required):
                raise ValueError("model and tokenizer files must not be empty")
        if model_directions != set(self.directions):
            raise ValueError("pack directions and model directions must match")
        if sum(entry.size_bytes for entry in self.files) > self.disk_bytes:
            raise ValueError("disk_bytes must cover every declared file")
        return self

    def model_for(self, direction: TranslationDirection) -> TranslationModelSpec:
        for model in self.models:
            if model.direction is direction:
                return model
        raise ValueError(f"pack does not support direction {direction.value}")

    def canonical_bytes(self) -> bytes:
        return self.model_dump_json(exclude_none=True, by_alias=True).encode("utf-8")

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.canonical_bytes()).hexdigest()


class TranslationInstallReceipt(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    receipt_schema: str = Field(default=PACK_INSTALL_RECEIPT_SCHEMA)
    pack_id: str = Field(pattern=_SAFE_ID_PATTERN)
    version: str = Field(pattern=_VERSION_PATTERN)
    manifest_sha256: str = Field(pattern=_SHA256_PATTERN)
    installed_at: datetime
    install_source: str = Field(pattern=r"^(local|download)$")
    release_relative_path: str = Field(min_length=1, max_length=500)

    @field_validator("release_relative_path")
    @classmethod
    def _safe_release_path(cls, value: str) -> str:
        return normalize_relative_pack_path(value)

    @model_validator(mode="after")
    def _schema_supported(self) -> Self:
        if self.receipt_schema != PACK_INSTALL_RECEIPT_SCHEMA:
            raise ValueError("unsupported translation install receipt schema")
        if self.installed_at.tzinfo is None or self.installed_at.utcoffset() is None:
            raise ValueError("installed_at must include a timezone")
        if self.release_relative_path != f"{self.pack_id}/{self.version}":
            raise ValueError("install receipt release path must match pack identity")
        return self


class PackInspectionStatus(StrEnum):
    MODEL_PACK_MISSING = "model_pack_missing"
    READY = "ready"
    CORRUPT = "corrupt"
    INCOMPATIBLE = "incompatible"


class TranslationPackInspection(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    status: PackInspectionStatus
    pack_id: str | None = None
    version: str | None = None
    backend: str | None = None
    directions: tuple[TranslationDirection, ...] = ()
    disk_bytes: int = Field(default=0, ge=0)
    minimum_ram_bytes: int = Field(default=0, ge=0)
    license_summary: str = ""
    source_urls: tuple[str, ...] = ()
    checksums_verified: bool = False
    reason_code: str = Field(default="", max_length=100)


class InstalledTranslationPack(BaseModel):
    """Verified local release passed to a backend factory only on translate."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        arbitrary_types_allowed=True,
        hide_input_in_errors=True,
    )

    root: Path = Field(repr=False)
    manifest: TranslationPackManifest
    receipt: TranslationInstallReceipt

    def resolve_file(self, relative_path: str) -> Path:
        normalized = normalize_relative_pack_path(relative_path)
        root = self.root.resolve()
        candidate = (root / Path(*PurePosixPath(normalized).parts)).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            raise ValueError("pack file escapes the installed release") from None
        return candidate


class PackInstallProgress(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    completed_files: int = Field(default=0, ge=0)
    total_files: int = Field(ge=1)
    relative_path: str = Field(default="", max_length=500)
    bytes_completed: int = Field(default=0, ge=0)
    total_bytes: int = Field(ge=0)
    stage: str = Field(pattern=r"^(copying|downloading|verifying|activating)$")

    @model_validator(mode="after")
    def _counts_valid(self) -> Self:
        if self.completed_files > self.total_files:
            raise ValueError("completed_files must not exceed total_files")
        if self.bytes_completed > self.total_bytes:
            raise ValueError("bytes_completed must not exceed total_bytes")
        return self


__all__ = [
    "ACTIVE_PACK_FILENAME",
    "PACK_INSTALL_RECEIPT_FILENAME",
    "PACK_INSTALL_RECEIPT_SCHEMA",
    "PACK_MANIFEST_FILENAME",
    "PACK_MANIFEST_SCHEMA",
    "InstalledTranslationPack",
    "PackInspectionStatus",
    "PackInstallProgress",
    "TranslationInstallReceipt",
    "TranslationModelSpec",
    "TranslationPackFile",
    "TranslationPackInspection",
    "TranslationPackManifest",
    "normalize_relative_pack_path",
]
