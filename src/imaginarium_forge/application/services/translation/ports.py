"""Ports that keep native inference and normalization out of the domain layer."""

from __future__ import annotations

from threading import Event
from typing import Protocol

from imaginarium_forge.domain.translation import (
    InstalledTranslationPack,
    TranslationDirection,
)


class OfflineTranslationBackend(Protocol):
    """One already-loaded, direction-specific local translation engine."""

    @property
    def model_id(self) -> str: ...

    def translate_segment(self, text: str, *, cancel: Event | None = None) -> str: ...

    def close(self) -> None: ...


class OfflineTranslationBackendFactory(Protocol):
    """Lazy backend constructor called only by an explicit translate action."""

    def __call__(
        self,
        pack: InstalledTranslationPack,
        direction: TranslationDirection,
    ) -> OfflineTranslationBackend: ...


class TraditionalChineseNormalizer(Protocol):
    """Local script/locale normalizer used only for English to zh-TW."""

    def normalize(self, text: str) -> str: ...


class IdentityTraditionalChineseNormalizer:
    """Explicit no-op for packs proven to emit reliable Traditional Chinese."""

    def normalize(self, text: str) -> str:
        return text


__all__ = [
    "IdentityTraditionalChineseNormalizer",
    "OfflineTranslationBackend",
    "OfflineTranslationBackendFactory",
    "TraditionalChineseNormalizer",
]
