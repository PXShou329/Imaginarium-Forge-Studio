"""Bounded local manifest reader; it performs no discovery or network access."""

from __future__ import annotations

from pathlib import Path

from imaginarium_forge.application.services.translation.pack_manager import (
    load_translation_pack_manifest,
)
from imaginarium_forge.domain.translation import TranslationPackManifest


class PackManifestRepository:
    """Load an explicitly selected manifest file without side effects."""

    def load(self, path: Path) -> TranslationPackManifest:
        return load_translation_pack_manifest(path)


__all__ = ["PackManifestRepository"]
