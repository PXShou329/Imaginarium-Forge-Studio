"""Orchestrate protected, segmented, entirely local document translation."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import suppress
from threading import Event
from time import perf_counter
from typing import Never

from imaginarium_forge.application.services.translation.errors import (
    TranslationCancelled,
    TranslationExecutionError,
)
from imaginarium_forge.application.services.translation.pack_manager import (
    TranslationPackManager,
)
from imaginarium_forge.application.services.translation.ports import (
    OfflineTranslationBackendFactory,
    TraditionalChineseNormalizer,
)
from imaginarium_forge.application.services.translation.protected_term_service import (
    ProtectedDocument,
    ProtectedTermService,
    RestoredDocument,
)
from imaginarium_forge.application.services.translation.quality_checks import (
    TranslationQualityChecks,
)
from imaginarium_forge.application.services.translation.segmenter import (
    SegmentedDocument,
    TextSegmenter,
)
from imaginarium_forge.domain.translation import (
    OfflineTranslationRequest,
    OfflineTranslationResult,
    TranslationDirection,
    TranslationProgress,
    TranslationResultStatus,
    TranslationWarning,
)

TranslationProgressCallback = Callable[[TranslationProgress], None]


class TranslationService:
    """Stateless controller used by one explicit, memory-only UI task."""

    def __init__(
        self,
        pack_manager: TranslationPackManager,
        backend_factory: OfflineTranslationBackendFactory,
        normalizer: TraditionalChineseNormalizer,
        *,
        protected_terms: ProtectedTermService | None = None,
        segmenter: TextSegmenter | None = None,
        quality_checks: TranslationQualityChecks | None = None,
    ) -> None:
        self._pack_manager = pack_manager
        self._backend_factory = backend_factory
        self._normalizer = normalizer
        self._protected_terms = protected_terms or ProtectedTermService()
        self._segmenter = segmenter or TextSegmenter()
        self._quality_checks = quality_checks or TranslationQualityChecks()

    def translate(
        self,
        request: OfflineTranslationRequest,
        *,
        cancel: Event | None = None,
        progress: TranslationProgressCallback | None = None,
    ) -> OfflineTranslationResult:
        """Translate locally; cancel returns a partial and failures report it first."""

        started = perf_counter()
        raw_report = progress or (lambda _snapshot: None)

        def report(snapshot: TranslationProgress) -> None:
            try:
                raw_report(snapshot)
            except Exception:
                # UI progress is observational. A rendering callback must not
                # turn a local translation into a leaked or false failure.
                return

        pack = self._pack_manager.resolve_for_translation(request.direction)
        model = pack.manifest.model_for(request.direction)
        protected = self._protected_terms.protect(
            request.source_text,
            direction=request.direction,
            terms=request.protected_terms,
        )
        document = self._segmenter.segment(
            protected.text,
            max_chars=request.effective_segment_size,
            preserve_markdown=request.preserve_markdown,
        )
        total = len(document.segments)
        translated: dict[str, str] = {}
        empty_segments: list[str] = []
        report(TranslationProgress(total_segments=total))

        if cancel is not None and cancel.is_set():
            return self._cancelled_result(
                request=request,
                protected=protected,
                document=document,
                translated=translated,
                pack_id=pack.manifest.pack_id,
                model_id=model.model_id,
                started=started,
                report=report,
            )
        try:
            backend = self._backend_factory(pack, request.direction)
        except Exception:
            self._raise_execution_failure(
                request=request,
                protected=protected,
                document=document,
                translated=translated,
                report=report,
                code="translation_backend_load_failed",
            )
        try:
            for segment in document.segments:
                if cancel is not None and cancel.is_set():
                    return self._cancelled_result(
                        request=request,
                        protected=protected,
                        document=document,
                        translated=translated,
                        pack_id=pack.manifest.pack_id,
                        model_id=model.model_id,
                        started=started,
                        report=report,
                    )
                try:
                    output = backend.translate_segment(segment.text, cancel=cancel)
                except TranslationCancelled:
                    return self._cancelled_result(
                        request=request,
                        protected=protected,
                        document=document,
                        translated=translated,
                        pack_id=pack.manifest.pack_id,
                        model_id=model.model_id,
                        started=started,
                        report=report,
                    )
                except Exception:
                    self._raise_execution_failure(
                        request=request,
                        protected=protected,
                        document=document,
                        translated=translated,
                        report=report,
                        code="translation_backend_execution_failed",
                    )
                translated[segment.segment_id] = output
                if not output.strip():
                    empty_segments.append(segment.segment_id)
                try:
                    partial, warnings = self._render_partial(
                        request=request,
                        protected=protected,
                        document=document,
                        translated=translated,
                    )
                except Exception:
                    self._raise_execution_failure(
                        request=request,
                        protected=protected,
                        document=document,
                        translated=translated,
                        report=report,
                        code="translation_normalization_failed",
                    )
                report(
                    TranslationProgress(
                        completed_segments=len(translated),
                        total_segments=total,
                        partial_text=partial,
                        warnings=warnings,
                    )
                )
        finally:
            # Cleanup is not translation output and must not replace a
            # completed or already-sanitized execution result.
            with suppress(Exception):
                backend.close()

        try:
            restored = self._render_complete(
                request=request,
                protected=protected,
                document=document,
                translated=translated,
            )
            translated_text = restored.text
            warnings = self._quality_checks.evaluate(
                source_text=request.source_text,
                translated_text=translated_text,
                direction=request.direction,
                prior_warnings=restored.warnings,
                empty_segment_ids=tuple(empty_segments),
                path_evidence=restored.path_evidence,
            )
        except Exception:
            self._raise_execution_failure(
                request=request,
                protected=protected,
                document=document,
                translated=translated,
                report=report,
                code="translation_postprocess_failed",
            )
        if not translated_text.strip():
            report(
                TranslationProgress(
                    completed_segments=total,
                    total_segments=total,
                    partial_text=translated_text,
                    warnings=warnings,
                )
            )
            raise TranslationExecutionError(
                partial_text=translated_text,
                completed_segments=total,
                total_segments=total,
                code="translation_output_empty",
            )
        status = (
            TranslationResultStatus.COMPLETED_WITH_WARNINGS
            if warnings
            else TranslationResultStatus.COMPLETED
        )
        result = OfflineTranslationResult(
            status=status,
            translated_text=translated_text,
            direction=request.direction,
            pack_id=pack.manifest.pack_id,
            model_ids=(model.model_id,),
            source_chars=len(request.source_text),
            output_chars=len(translated_text),
            warnings=warnings,
            elapsed_ms=max(0, int((perf_counter() - started) * 1_000)),
            completed_segments=total,
            total_segments=total,
        )
        report(
            TranslationProgress(
                completed_segments=total,
                total_segments=total,
                partial_text=translated_text,
                warnings=warnings,
            )
        )
        return result

    def _cancelled_result(
        self,
        *,
        request: OfflineTranslationRequest,
        protected: ProtectedDocument,
        document: SegmentedDocument,
        translated: Mapping[str, str],
        pack_id: str,
        model_id: str,
        started: float,
        report: TranslationProgressCallback,
    ) -> OfflineTranslationResult:
        partial, warnings = self._best_effort_partial(
            request=request,
            protected=protected,
            document=document,
            translated=translated,
        )
        total = len(document.segments)
        snapshot = TranslationProgress(
            completed_segments=len(translated),
            total_segments=total,
            partial_text=partial,
            warnings=warnings,
        )
        report(snapshot)
        return OfflineTranslationResult(
            status=TranslationResultStatus.CANCELLED_WITH_PARTIAL,
            translated_text=partial,
            direction=request.direction,
            pack_id=pack_id,
            model_ids=(model_id,),
            source_chars=len(request.source_text),
            output_chars=len(partial),
            warnings=warnings,
            elapsed_ms=max(0, int((perf_counter() - started) * 1_000)),
            completed_segments=len(translated),
            total_segments=total,
        )

    def _render_partial(
        self,
        *,
        request: OfflineTranslationRequest,
        protected: ProtectedDocument,
        document: SegmentedDocument,
        translated: Mapping[str, str],
    ) -> tuple[str, tuple[TranslationWarning, ...]]:
        raw = document.reassemble(translated, stop_at_first_missing=True)
        normalized = self._normalize_if_needed(raw, request.direction)
        expected = frozenset(token.marker for token in protected.tokens if token.marker in raw)
        restored = self._protected_terms.restore(
            normalized,
            protected.tokens,
            expected_markers=expected,
        )
        return restored.text, restored.warnings

    def _render_complete(
        self,
        *,
        request: OfflineTranslationRequest,
        protected: ProtectedDocument,
        document: SegmentedDocument,
        translated: Mapping[str, str],
    ) -> RestoredDocument:
        raw = document.reassemble(translated)
        normalized = self._normalize_if_needed(raw, request.direction)
        return self._protected_terms.restore(
            normalized, protected.tokens, protected_source_text=protected.text
        )

    def _best_effort_partial(
        self,
        *,
        request: OfflineTranslationRequest,
        protected: ProtectedDocument,
        document: SegmentedDocument,
        translated: Mapping[str, str],
    ) -> tuple[str, tuple[TranslationWarning, ...]]:
        if not translated:
            # The source remains in the caller-owned editor. Do not initialize
            # normalization merely to render structural prefixes before the
            # first translated segment exists.
            return "", ()
        try:
            return self._render_partial(
                request=request,
                protected=protected,
                document=document,
                translated=translated,
            )
        except Exception:
            raw = document.reassemble(translated, stop_at_first_missing=True)
            expected = frozenset(token.marker for token in protected.tokens if token.marker in raw)
            try:
                restored = self._protected_terms.restore(
                    raw,
                    protected.tokens,
                    expected_markers=expected,
                )
            except Exception:
                return raw, ()
            return restored.text, restored.warnings

    def _raise_execution_failure(
        self,
        *,
        request: OfflineTranslationRequest,
        protected: ProtectedDocument,
        document: SegmentedDocument,
        translated: Mapping[str, str],
        report: TranslationProgressCallback,
        code: str,
    ) -> Never:
        partial, warnings = self._best_effort_partial(
            request=request,
            protected=protected,
            document=document,
            translated=translated,
        )
        completed = len(translated)
        total = len(document.segments)
        report(
            TranslationProgress(
                completed_segments=completed,
                total_segments=total,
                partial_text=partial,
                warnings=warnings,
            )
        )
        raise TranslationExecutionError(
            partial_text=partial,
            completed_segments=completed,
            total_segments=total,
            code=code,
        ) from None

    def _normalize_if_needed(self, text: str, direction: TranslationDirection) -> str:
        if direction is TranslationDirection.EN_TO_ZH_TW:
            return self._normalizer.normalize(text)
        return text


__all__ = ["TranslationProgressCallback", "TranslationService"]
