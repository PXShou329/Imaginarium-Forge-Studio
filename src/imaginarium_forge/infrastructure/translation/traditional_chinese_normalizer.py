"""Lazy, local OpenCC adapter for English-to-Taiwan-Traditional output."""

from __future__ import annotations

from threading import RLock
from typing import Any

from imaginarium_forge.application.services.translation.errors import (
    TranslationBackendError,
)


class OpenCCTraditionalChineseNormalizer:
    """Load OpenCC on first explicit translation, never during route rendering."""

    def __init__(self, config: str = "s2twp") -> None:
        if config not in {"s2t", "s2tw", "s2twp"}:
            raise ValueError("unsupported Traditional Chinese normalization config")
        self._config = config
        self._converter: Any | None = None
        self._lock = RLock()

    def normalize(self, text: str) -> str:
        converter = self._get_converter()
        try:
            return str(converter.convert(text))
        except Exception:
            raise TranslationBackendError("translation_normalizer_failed") from None

    def _get_converter(self) -> Any:
        with self._lock:
            if self._converter is not None:
                return self._converter
            try:
                from opencc import OpenCC  # type: ignore[import-untyped]

                converter = OpenCC(self._config)
            except Exception:
                raise TranslationBackendError("translation_normalizer_unavailable") from None
            self._converter = converter
            return converter


__all__ = ["OpenCCTraditionalChineseNormalizer"]
