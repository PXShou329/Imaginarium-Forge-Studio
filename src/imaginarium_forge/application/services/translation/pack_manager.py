"""Explicit, checksum-pinned, atomic installation of local translation packs."""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from threading import Event
from typing import Any
from urllib.parse import urljoin, urlsplit
from uuid import uuid4

from pydantic import BaseModel, ValidationError

from imaginarium_forge.application.services.translation.errors import (
    TranslationInstallCancelled,
    TranslationPackCorruptError,
    TranslationPackMissingError,
)
from imaginarium_forge.domain.translation import (
    InstalledTranslationPack,
    PackInspectionStatus,
    PackInstallProgress,
    TranslationDirection,
    TranslationInstallReceipt,
    TranslationPackFile,
    TranslationPackInspection,
    TranslationPackManifest,
)
from imaginarium_forge.domain.translation.pack import (
    ACTIVE_PACK_FILENAME,
    PACK_INSTALL_RECEIPT_FILENAME,
    PACK_MANIFEST_FILENAME,
    normalize_relative_pack_path,
)

PackInstallProgressCallback = Callable[[PackInstallProgress], None]
_COPY_CHUNK_BYTES = 1024 * 1024
_MAX_MANIFEST_BYTES = 2 * 1024 * 1024
_MAX_REDIRECTS = 5


def _is_link_or_reparse(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except OSError:
        return False
    if stat.S_ISLNK(metadata.st_mode):
        return True
    attributes = getattr(metadata, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(attributes & reparse_flag)


def _byte_progress_reporter(
    report: PackInstallProgressCallback,
    *,
    completed_files: int,
    total_files: int,
    relative_path: str,
    base_bytes: int,
    total_bytes: int,
    stage: str,
) -> Callable[[int], None]:
    def on_bytes(amount: int) -> None:
        report(
            PackInstallProgress(
                completed_files=completed_files,
                total_files=total_files,
                relative_path=relative_path,
                bytes_completed=base_bytes + amount,
                total_bytes=total_bytes,
                stage=stage,
            )
        )

    return on_bytes


def load_translation_pack_manifest(path: Path) -> TranslationPackManifest:
    """Read one bounded local manifest without creating files or directories."""

    try:
        if _is_link_or_reparse(path) or not path.is_file():
            raise TranslationPackCorruptError("translation_manifest_not_regular")
        size = path.stat().st_size
        if size <= 0 or size > _MAX_MANIFEST_BYTES:
            raise TranslationPackCorruptError("translation_manifest_size_invalid")
        payload = path.read_bytes()
        return TranslationPackManifest.model_validate_json(payload)
    except TranslationPackCorruptError:
        raise
    except (OSError, ValidationError, ValueError):
        raise TranslationPackCorruptError("translation_manifest_invalid") from None


class TranslationPackManager:
    """Manage pack metadata separately from translation execution.

    Construction and default inspection are side-effect free. Directory creation,
    network access, hashing of large files, and activation happen only in explicit
    install/verify/translation actions.
    """

    def __init__(self, pack_root: Path) -> None:
        self._root = pack_root

    @property
    def root(self) -> Path:
        return self._root

    def inspect(
        self,
        pack_id: str | None = None,
        *,
        verify_checksums: bool = False,
    ) -> TranslationPackInspection:
        """Inspect lightweight metadata; checksum scanning is opt-in."""

        try:
            installed = self._load_active_pack()
            if pack_id is not None and installed.manifest.pack_id != pack_id:
                return TranslationPackInspection(
                    status=PackInspectionStatus.MODEL_PACK_MISSING,
                    reason_code="requested_pack_not_active",
                )
            self._verify_release(installed, verify_checksums=verify_checksums)
        except TranslationPackMissingError:
            return TranslationPackInspection(
                status=PackInspectionStatus.MODEL_PACK_MISSING,
                reason_code="no_active_pack",
            )
        except TranslationPackCorruptError as exc:
            return TranslationPackInspection(
                status=PackInspectionStatus.CORRUPT,
                reason_code=exc.code,
            )
        except (OSError, ValidationError, ValueError):
            return TranslationPackInspection(
                status=PackInspectionStatus.INCOMPATIBLE,
                reason_code="pack_metadata_incompatible",
            )

        manifest = installed.manifest
        return TranslationPackInspection(
            status=PackInspectionStatus.READY,
            pack_id=manifest.pack_id,
            version=manifest.version,
            backend=manifest.backend,
            directions=manifest.directions,
            disk_bytes=manifest.disk_bytes,
            minimum_ram_bytes=manifest.minimum_ram_bytes,
            license_summary=manifest.license_summary,
            source_urls=manifest.source_urls,
            checksums_verified=verify_checksums,
        )

    def resolve_for_translation(
        self,
        direction: TranslationDirection,
    ) -> InstalledTranslationPack:
        """Resolve and fully re-hash the active pack before native model loading."""

        installed = self._load_active_pack()
        if direction not in installed.manifest.directions:
            raise TranslationPackCorruptError("translation_direction_not_installed")
        self._verify_release(installed, verify_checksums=True)
        return installed

    def install_local(
        self,
        source_dir: Path,
        *,
        expected_manifest: TranslationPackManifest | None = None,
        cancel: Event | None = None,
        progress: PackInstallProgressCallback | None = None,
    ) -> InstalledTranslationPack:
        """Explicitly import a directory that contains ``manifest.json``."""

        source_candidate = source_dir.absolute()
        if str(source_candidate).startswith("\\\\"):
            raise TranslationPackCorruptError("translation_source_network_path_rejected")
        if self._is_link_or_reparse(source_candidate):
            raise TranslationPackCorruptError("translation_source_link_rejected")
        source_root = source_candidate.resolve()
        manifest = load_translation_pack_manifest(source_root / PACK_MANIFEST_FILENAME)
        if expected_manifest is not None and manifest.sha256 != expected_manifest.sha256:
            raise TranslationPackCorruptError("translation_manifest_mismatch")

        def populate(stage: Path, report: PackInstallProgressCallback) -> None:
            bytes_completed = 0
            total_bytes = self._declared_bytes(manifest)
            for index, descriptor in enumerate(manifest.files):
                self._raise_if_cancelled(cancel)
                source = self._safe_source_file(source_root, descriptor.relative_path)
                destination = self._stage_file(stage, descriptor.relative_path)
                digest, copied = self._copy_and_hash(
                    source,
                    destination,
                    descriptor=descriptor,
                    cancel=cancel,
                    on_bytes=_byte_progress_reporter(
                        report,
                        completed_files=index,
                        total_files=len(manifest.files),
                        relative_path=descriptor.relative_path,
                        base_bytes=bytes_completed,
                        total_bytes=total_bytes,
                        stage="copying",
                    ),
                )
                self._assert_file_evidence(descriptor, digest=digest, size=copied)
                bytes_completed += copied
                report(
                    PackInstallProgress(
                        completed_files=index + 1,
                        total_files=len(manifest.files),
                        relative_path=descriptor.relative_path,
                        bytes_completed=bytes_completed,
                        total_bytes=total_bytes,
                        stage="verifying",
                    )
                )

        return self._install(
            manifest,
            install_source="local",
            populate=populate,
            cancel=cancel,
            progress=progress,
        )

    def download_and_install(
        self,
        manifest: TranslationPackManifest,
        *,
        cancel: Event | None = None,
        progress: PackInstallProgressCallback | None = None,
    ) -> InstalledTranslationPack:
        """Explicitly download only HTTPS URLs and hashes pinned in ``manifest``."""

        if any(descriptor.download_url is None for descriptor in manifest.files):
            raise TranslationPackCorruptError("translation_download_url_missing")

        def populate(stage: Path, report: PackInstallProgressCallback) -> None:
            try:
                import httpx
            except ImportError:
                raise TranslationPackCorruptError("translation_download_runtime_missing") from None

            bytes_completed = 0
            total_bytes = self._declared_bytes(manifest)
            try:
                with httpx.Client(
                    follow_redirects=False,
                    trust_env=False,
                    timeout=httpx.Timeout(connect=20.0, read=120.0, write=20.0, pool=20.0),
                ) as client:
                    for index, descriptor in enumerate(manifest.files):
                        self._raise_if_cancelled(cancel)
                        destination = self._stage_file(stage, descriptor.relative_path)
                        digest, copied = self._download_and_hash(
                            client,
                            descriptor,
                            destination,
                            cancel=cancel,
                            on_bytes=_byte_progress_reporter(
                                report,
                                completed_files=index,
                                total_files=len(manifest.files),
                                relative_path=descriptor.relative_path,
                                base_bytes=bytes_completed,
                                total_bytes=total_bytes,
                                stage="downloading",
                            ),
                        )
                        self._assert_file_evidence(descriptor, digest=digest, size=copied)
                        bytes_completed += copied
                        report(
                            PackInstallProgress(
                                completed_files=index + 1,
                                total_files=len(manifest.files),
                                relative_path=descriptor.relative_path,
                                bytes_completed=bytes_completed,
                                total_bytes=total_bytes,
                                stage="verifying",
                            )
                        )
            except (TranslationInstallCancelled, TranslationPackCorruptError):
                raise
            except Exception:
                raise TranslationPackCorruptError("translation_download_failed") from None

        return self._install(
            manifest,
            install_source="download",
            populate=populate,
            cancel=cancel,
            progress=progress,
        )

    def _install(
        self,
        manifest: TranslationPackManifest,
        *,
        install_source: str,
        populate: Callable[[Path, PackInstallProgressCallback], None],
        cancel: Event | None,
        progress: PackInstallProgressCallback | None,
    ) -> InstalledTranslationPack:
        report = progress or (lambda _snapshot: None)
        if str(self._root.absolute()).startswith("\\\\"):
            raise TranslationPackCorruptError("translation_pack_root_network_path_rejected")
        if self._root.exists() and self._is_link_or_reparse(self._root.absolute()):
            raise TranslationPackCorruptError("translation_pack_root_link_rejected")
        self._root.mkdir(parents=True, exist_ok=True)
        stage = self._root / f".part-{uuid4().hex}"
        stage.mkdir(mode=0o700)
        installed_at = datetime.now(UTC)
        release_relative_path = f"{manifest.pack_id}/{manifest.version}"
        receipt = TranslationInstallReceipt(
            pack_id=manifest.pack_id,
            version=manifest.version,
            manifest_sha256=manifest.sha256,
            installed_at=installed_at,
            install_source=install_source,
            release_relative_path=release_relative_path,
        )
        try:
            populate(stage, report)
            self._raise_if_cancelled(cancel)
            self._write_model(stage / PACK_MANIFEST_FILENAME, manifest)
            self._write_model(stage / PACK_INSTALL_RECEIPT_FILENAME, receipt)
            staged_pack = InstalledTranslationPack(root=stage, manifest=manifest, receipt=receipt)
            self._verify_release(staged_pack, verify_checksums=True)

            target = self._safe_root_path(release_relative_path)
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                existing = self._load_release(target)
                if existing.manifest.sha256 != manifest.sha256:
                    raise TranslationPackCorruptError("translation_pack_version_conflict")
                self._verify_release(existing, verify_checksums=True)
                self._remove_staging(stage)
                installed = existing
            else:
                os.replace(stage, target)
                installed = InstalledTranslationPack(
                    root=target,
                    manifest=manifest,
                    receipt=receipt,
                )
            report(
                PackInstallProgress(
                    completed_files=len(manifest.files),
                    total_files=len(manifest.files),
                    bytes_completed=self._declared_bytes(manifest),
                    total_bytes=self._declared_bytes(manifest),
                    stage="activating",
                )
            )
            self._activate(installed.receipt)
            return installed
        except Exception:
            if stage.exists():
                self._remove_staging(stage)
            raise

    def _load_active_pack(self) -> InstalledTranslationPack:
        pointer = self._root / ACTIVE_PACK_FILENAME
        if not pointer.exists():
            raise TranslationPackMissingError()
        try:
            receipt = TranslationInstallReceipt.model_validate_json(
                self._read_bounded_json(pointer)
            )
            release = self._safe_root_path(receipt.release_relative_path)
            installed = self._load_release(release)
            if installed.receipt != receipt:
                raise TranslationPackCorruptError("translation_active_receipt_mismatch")
            return installed
        except TranslationPackCorruptError:
            raise
        except (OSError, ValidationError, ValueError):
            raise TranslationPackCorruptError("translation_active_receipt_invalid") from None

    def _load_release(self, release: Path) -> InstalledTranslationPack:
        try:
            if self._is_link_or_reparse(release) or not release.is_dir():
                raise TranslationPackCorruptError("translation_release_not_regular")
            manifest = load_translation_pack_manifest(release / PACK_MANIFEST_FILENAME)
            receipt = TranslationInstallReceipt.model_validate_json(
                self._read_bounded_json(release / PACK_INSTALL_RECEIPT_FILENAME)
            )
            if (
                receipt.pack_id != manifest.pack_id
                or receipt.version != manifest.version
                or receipt.manifest_sha256 != manifest.sha256
            ):
                raise TranslationPackCorruptError("translation_receipt_manifest_mismatch")
            return InstalledTranslationPack(root=release, manifest=manifest, receipt=receipt)
        except TranslationPackCorruptError:
            raise
        except (OSError, ValidationError, ValueError):
            raise TranslationPackCorruptError("translation_release_invalid") from None

    def _verify_release(
        self,
        installed: InstalledTranslationPack,
        *,
        verify_checksums: bool,
    ) -> None:
        for descriptor in installed.manifest.files:
            path = self._safe_release_file(installed.root, descriptor.relative_path)
            if self._is_link_or_reparse(path) or not path.is_file():
                raise TranslationPackCorruptError("translation_pack_file_missing")
            try:
                size = path.stat().st_size
            except OSError:
                raise TranslationPackCorruptError("translation_pack_file_unreadable") from None
            if size != descriptor.size_bytes:
                raise TranslationPackCorruptError("translation_pack_file_size_mismatch")
            if verify_checksums and self._sha256_file(path) != descriptor.sha256:
                raise TranslationPackCorruptError("translation_pack_checksum_mismatch")

    def _activate(self, receipt: TranslationInstallReceipt) -> None:
        pointer = self._root / ACTIVE_PACK_FILENAME
        temporary = self._root / f".{ACTIVE_PACK_FILENAME}.{uuid4().hex}.part"
        try:
            self._write_model(temporary, receipt)
            os.replace(temporary, pointer)
        finally:
            temporary.unlink(missing_ok=True)

    def _safe_root_path(self, relative_path: str) -> Path:
        normalized = normalize_relative_pack_path(relative_path)
        root = self._root.resolve()
        unresolved = root / Path(*PurePosixPath(normalized).parts)
        if self._path_chain_has_link(unresolved, root):
            raise TranslationPackCorruptError("translation_pack_path_link_rejected")
        candidate = unresolved.resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            raise TranslationPackCorruptError("translation_pack_path_escape") from None
        return candidate

    @staticmethod
    def _safe_release_file(release_root: Path, relative_path: str) -> Path:
        normalized = normalize_relative_pack_path(relative_path)
        root = release_root.resolve()
        unresolved = root / Path(*PurePosixPath(normalized).parts)
        if TranslationPackManager._path_chain_has_link(unresolved, root):
            raise TranslationPackCorruptError("translation_pack_file_link_rejected")
        candidate = unresolved.resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            raise TranslationPackCorruptError("translation_pack_file_path_escape") from None
        return candidate

    @staticmethod
    def _safe_source_file(source_root: Path, relative_path: str) -> Path:
        normalized = normalize_relative_pack_path(relative_path)
        unresolved = source_root / Path(*PurePosixPath(normalized).parts)
        if TranslationPackManager._path_chain_has_link(unresolved, source_root):
            raise TranslationPackCorruptError("translation_source_link_rejected")
        candidate = unresolved.resolve()
        try:
            candidate.relative_to(source_root)
        except ValueError:
            raise TranslationPackCorruptError("translation_source_path_escape") from None
        if not candidate.is_file():
            raise TranslationPackCorruptError("translation_source_file_missing")
        return candidate

    @staticmethod
    def _stage_file(stage: Path, relative_path: str) -> Path:
        destination = stage / Path(*PurePosixPath(relative_path).parts)
        destination.parent.mkdir(parents=True, exist_ok=True)
        return destination

    @staticmethod
    def _copy_and_hash(
        source: Path,
        destination: Path,
        *,
        descriptor: TranslationPackFile,
        cancel: Event | None,
        on_bytes: Callable[[int], None],
    ) -> tuple[str, int]:
        digest = hashlib.sha256()
        copied = 0
        try:
            with source.open("rb") as reader, destination.open("xb") as writer:
                while chunk := reader.read(_COPY_CHUNK_BYTES):
                    TranslationPackManager._raise_if_cancelled(cancel)
                    copied += len(chunk)
                    if copied > descriptor.size_bytes:
                        raise TranslationPackCorruptError("translation_pack_file_size_mismatch")
                    digest.update(chunk)
                    writer.write(chunk)
                    on_bytes(copied)
                writer.flush()
                os.fsync(writer.fileno())
        except (TranslationInstallCancelled, TranslationPackCorruptError):
            raise
        except OSError:
            raise TranslationPackCorruptError("translation_pack_copy_failed") from None
        return digest.hexdigest(), copied

    def _download_and_hash(
        self,
        client: Any,
        descriptor: TranslationPackFile,
        destination: Path,
        *,
        cancel: Event | None,
        on_bytes: Callable[[int], None],
    ) -> tuple[str, int]:
        import httpx

        if descriptor.download_url is None:
            raise TranslationPackCorruptError("translation_download_url_missing")
        response = self._open_pinned_download(client, descriptor)
        digest = hashlib.sha256()
        copied = 0
        try:
            with destination.open("xb") as writer:
                for chunk in response.iter_raw(_COPY_CHUNK_BYTES):
                    self._raise_if_cancelled(cancel)
                    copied += len(chunk)
                    if copied > descriptor.size_bytes:
                        raise TranslationPackCorruptError("translation_pack_file_size_mismatch")
                    digest.update(chunk)
                    writer.write(chunk)
                    on_bytes(copied)
                writer.flush()
                os.fsync(writer.fileno())
        except (TranslationInstallCancelled, TranslationPackCorruptError):
            raise
        except (OSError, httpx.HTTPError):
            raise TranslationPackCorruptError("translation_download_failed") from None
        finally:
            # Closing a consumed response is cleanup only; never replace a
            # verified result with a transport exception containing a URL.
            with suppress(Exception):
                response.close()
        return digest.hexdigest(), copied

    @staticmethod
    def _open_pinned_download(client: Any, descriptor: TranslationPackFile) -> Any:
        if descriptor.download_url is None:
            raise TranslationPackCorruptError("translation_download_url_missing")
        initial_host = urlsplit(descriptor.download_url).hostname
        if initial_host is None:
            raise TranslationPackCorruptError("translation_download_url_invalid")
        allowed_hosts = {
            initial_host.rstrip(".").casefold(),
            *descriptor.allowed_redirect_hosts,
        }
        current = descriptor.download_url
        for _ in range(_MAX_REDIRECTS + 1):
            cookies = getattr(client, "cookies", None)
            clear_cookies = getattr(cookies, "clear", None)
            if callable(clear_cookies):
                clear_cookies()
            request = client.build_request("GET", current)
            response = client.send(request, stream=True)
            if response.status_code == 200:
                return response
            if response.status_code not in {301, 302, 303, 307, 308}:
                with suppress(Exception):
                    response.close()
                raise TranslationPackCorruptError("translation_download_http_failure")
            location = response.headers.get("location")
            with suppress(Exception):
                response.close()
            if not location:
                raise TranslationPackCorruptError("translation_download_redirect_invalid")
            current = urljoin(current, location)
            parsed = urlsplit(current)
            if (
                parsed.scheme.lower() != "https"
                or not parsed.hostname
                or parsed.username is not None
                or parsed.password is not None
                or parsed.fragment
                or parsed.hostname.rstrip(".").casefold() not in allowed_hosts
            ):
                raise TranslationPackCorruptError("translation_download_redirect_rejected")
        raise TranslationPackCorruptError("translation_download_redirect_limit")

    @staticmethod
    def _assert_file_evidence(
        descriptor: TranslationPackFile,
        *,
        digest: str,
        size: int,
    ) -> None:
        if size != descriptor.size_bytes:
            raise TranslationPackCorruptError("translation_pack_file_size_mismatch")
        if digest != descriptor.sha256:
            raise TranslationPackCorruptError("translation_pack_checksum_mismatch")

    @staticmethod
    def _write_model(path: Path, model: BaseModel) -> None:
        payload = model.model_dump_json(exclude_none=True).encode("utf-8")
        with path.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())

    @staticmethod
    def _read_bounded_json(path: Path) -> bytes:
        if TranslationPackManager._is_link_or_reparse(path) or not path.is_file():
            raise TranslationPackCorruptError("translation_metadata_not_regular")
        if not 0 < path.stat().st_size <= _MAX_MANIFEST_BYTES:
            raise TranslationPackCorruptError("translation_metadata_size_invalid")
        return path.read_bytes()

    @staticmethod
    def _sha256_file(path: Path) -> str:
        digest = hashlib.sha256()
        try:
            with path.open("rb") as handle:
                while chunk := handle.read(_COPY_CHUNK_BYTES):
                    digest.update(chunk)
        except OSError:
            raise TranslationPackCorruptError("translation_pack_file_unreadable") from None
        return digest.hexdigest()

    @staticmethod
    def _declared_bytes(manifest: TranslationPackManifest) -> int:
        return sum(descriptor.size_bytes for descriptor in manifest.files)

    @staticmethod
    def _raise_if_cancelled(cancel: Event | None) -> None:
        if cancel is not None and cancel.is_set():
            raise TranslationInstallCancelled()

    @staticmethod
    def _is_link_or_reparse(path: Path) -> bool:
        return _is_link_or_reparse(path)

    @staticmethod
    def _path_chain_has_link(path: Path, root: Path) -> bool:
        current = path
        while True:
            if TranslationPackManager._is_link_or_reparse(current):
                return True
            if current == root:
                return False
            if root not in current.parents:
                return True
            current = current.parent

    def _remove_staging(self, stage: Path) -> None:
        root = self._root.resolve()
        resolved = stage.resolve()
        try:
            resolved.relative_to(root)
        except ValueError:
            raise TranslationPackCorruptError("translation_staging_path_escape") from None
        if not resolved.name.startswith(".part-"):
            raise TranslationPackCorruptError("translation_staging_name_invalid")
        shutil.rmtree(resolved)


__all__ = [
    "PackInstallProgressCallback",
    "TranslationPackManager",
    "load_translation_pack_manifest",
]
