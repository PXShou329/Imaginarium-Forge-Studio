"""Lazy CTranslate2/SentencePiece backend using only verified local files."""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from threading import Event
from typing import Any

from imaginarium_forge.application.services.translation.errors import (
    TranslationBackendError,
    TranslationCancelled,
)
from imaginarium_forge.application.services.translation.ports import (
    OfflineTranslationBackend,
)
from imaginarium_forge.domain.translation import (
    InstalledTranslationPack,
    TranslationDirection,
    TranslationModelSpec,
)

_PROTECTED_MARKER = re.compile(r"__IMF_[A-F0-9]{12}_\d{6}__")
_MARKER_CHAIN = re.compile(
    rf"(?:{_PROTECTED_MARKER.pattern})(?:[ \t]*(?:{_PROTECTED_MARKER.pattern}))*"
)
_SURROGATE = re.compile(r"ZXQ[0-9]+")
_OUTPUT_SURROGATE = re.compile(r"(?<![A-Za-z0-9_])ZXQ[0-9]+(?![A-Za-z0-9_])")
_RESERVED_NAMESPACE = re.compile(r"ZXQ", re.IGNORECASE)
_NATURAL_BREAK = re.compile(r"[\s。！？.!?；;，,]")


@dataclass(frozen=True, slots=True)
class _SourceChunk:
    text: str = field(repr=False)


class CTranslate2BackendFactory:
    """Construct a native backend only after pack checksum verification."""

    def __init__(
        self,
        *,
        device: str = "auto",
        compute_type: str = "auto",
        beam_size: int = 4,
    ) -> None:
        if device not in {"auto", "cpu", "cuda"}:
            raise ValueError("unsupported CTranslate2 device")
        if not 1 <= beam_size <= 8:
            raise ValueError("beam_size must be between 1 and 8")
        self._device = device
        self._compute_type = compute_type
        self._beam_size = beam_size

    def __call__(
        self,
        pack: InstalledTranslationPack,
        direction: TranslationDirection,
    ) -> OfflineTranslationBackend:
        if pack.manifest.backend != "ctranslate2":
            raise TranslationBackendError("translation_backend_manifest_mismatch")
        model = pack.manifest.model_for(direction)
        model_file = pack.resolve_file(model.model_path)
        source_tokenizer = pack.resolve_file(model.source_tokenizer)
        target_tokenizer = pack.resolve_file(model.target_tokenizer)
        config_relative = (PurePosixPath(model.model_path).parent / "config.json").as_posix()
        if config_relative not in {item.relative_path for item in pack.manifest.files}:
            raise TranslationBackendError("translation_backend_config_not_pinned")
        config_file = pack.resolve_file(config_relative)
        if (
            not model_file.is_file()
            or not source_tokenizer.is_file()
            or not target_tokenizer.is_file()
        ):
            raise TranslationBackendError("translation_backend_local_file_missing")
        try:
            if config_file.stat().st_size > 65_536:
                raise ValueError("model config exceeds metadata limit")
            config = json.loads(config_file.read_text(encoding="utf-8"))
            if not isinstance(config, dict):
                raise ValueError("model config must be an object")
            add_source_eos = config.get("add_source_eos")
            eos_token = config.get("eos_token")
            if (
                not isinstance(add_source_eos, bool)
                or not isinstance(eos_token, str)
                or not 1 <= len(eos_token) <= 64
                or any(char.isspace() or ord(char) < 32 for char in eos_token)
            ):
                raise ValueError("model config must declare its EOS contract")
        except Exception:
            raise TranslationBackendError("translation_backend_config_invalid") from None
        try:
            import ctranslate2  # type: ignore[import-untyped]
            import sentencepiece

            translator = ctranslate2.Translator(
                str(model_file.parent),
                device=self._device,
                compute_type=self._compute_type,
            )
            source_processor = sentencepiece.SentencePieceProcessor(
                model_file=str(source_tokenizer)
            )
            target_processor = sentencepiece.SentencePieceProcessor(
                model_file=str(target_tokenizer)
            )
        except Exception:
            raise TranslationBackendError("translation_backend_load_failed") from None
        return _CTranslate2Backend(
            translator=translator,
            source_processor=source_processor,
            target_processor=target_processor,
            model=model,
            direction=direction,
            beam_size=self._beam_size,
            add_source_eos=add_source_eos,
            eos_token=eos_token,
        )


class _CTranslate2Backend:
    def __init__(
        self,
        *,
        translator: Any,
        source_processor: Any,
        target_processor: Any,
        model: TranslationModelSpec,
        direction: TranslationDirection,
        beam_size: int,
        add_source_eos: bool,
        eos_token: str,
    ) -> None:
        self._translator = translator
        self._source_processor = source_processor
        self._target_processor = target_processor
        self._model = model
        self._direction = direction
        self._beam_size = beam_size
        self._add_source_eos = add_source_eos
        self._eos_token = eos_token

    @property
    def model_id(self) -> str:
        return self._model.model_id

    def translate_segment(self, text: str, *, cancel: Event | None = None) -> str:
        try:
            self._raise_if_cancelled(cancel)
            if self._translator is None:
                raise TranslationBackendError("translation_backend_closed")
            if _RESERVED_NAMESPACE.search(text):
                raise TranslationBackendError("translation_marker_namespace_collision")
            visible_text = _PROTECTED_MARKER.sub("", text)
            if "__IMF_" in visible_text:
                raise TranslationBackendError("translation_marker_integrity_failed")
            if not any(char.isalnum() for char in visible_text):
                return text

            # Only inert markers reach this adapter. Actual protected values stay
            # in the application facade and are restored after normalization.
            # Normal SP encoding preserves sentence context; copying is verified,
            # never assumed from replace_unknowns or guessed from attention.
            represented, mapping = self._represent_markers(text)
            translated = self._translate_plain_text(represented, cancel=cancel)
            restored = self._restore_markers(translated, represented, mapping)
            if Counter(_PROTECTED_MARKER.findall(restored)) != Counter(
                _PROTECTED_MARKER.findall(text)
            ):
                raise TranslationBackendError("translation_marker_integrity_failed")
            self._raise_if_cancelled(cancel)
            return restored
        except TranslationCancelled:
            raise
        except TranslationBackendError:
            raise
        except Exception:
            raise TranslationBackendError("translation_backend_execution_failed") from None

    @staticmethod
    def _represent_markers(text: str) -> tuple[str, dict[str, str]]:
        chain_codes: dict[str, str] = {}
        mapping: dict[str, str] = {}

        def replace_chain(match: re.Match[str]) -> str:
            chain = match.group(0)
            if chain not in chain_codes:
                code = f"ZXQ{len(chain_codes) + 1:02d}"
                chain_codes[chain] = code
                mapping[code] = chain
            return chain_codes[chain]

        return _MARKER_CHAIN.sub(replace_chain, text), mapping

    @staticmethod
    def _validate_surrogates(translated: str, source: str) -> None:
        expected = Counter(_SURROGATE.findall(source))
        observed = Counter(_OUTPUT_SURROGATE.findall(translated))
        remainder = _OUTPUT_SURROGATE.sub("", translated)
        if expected != observed or _RESERVED_NAMESPACE.search(remainder) or "__IMF_" in translated:
            raise TranslationBackendError("translation_marker_integrity_failed")

    @classmethod
    def _restore_markers(cls, translated: str, source: str, mapping: dict[str, str]) -> str:
        cls._validate_surrogates(translated, source)
        return _OUTPUT_SURROGATE.sub(lambda match: mapping[match.group(0)], translated)

    def close(self) -> None:
        translator = self._translator
        self._translator = None
        if translator is None:
            return
        try:
            unload = getattr(translator, "unload_model", None)
            if callable(unload):
                unload()
        except Exception:
            # Cleanup must not replace a completed result with a native error that
            # could contain local paths or source fragments.
            return

    def _translate_one(self, text: str) -> str:
        source_tokens = list(self._source_processor.encode(text, out_type=str))
        prefix = self._model.source_prefix
        if prefix:
            source_tokens.insert(0, prefix)
        if not source_tokens:
            return ""
        # Converted Marian configs do not necessarily insert EOS.  Respect the
        # checksum-pinned config and account for exactly one source EOS either way.
        if not self._add_source_eos:
            source_tokens.append(self._eos_token)
        if len(source_tokens) + int(self._add_source_eos) > self._model.max_input_tokens:
            raise TranslationBackendError("translation_input_token_budget_exceeded")
        try:
            results = self._translator.translate_batch(
                [source_tokens],
                beam_size=self._beam_size,
                max_input_length=0,
                max_decoding_length=self._model.max_decoding_tokens,
                return_scores=False,
                return_end_token=True,
                replace_unknowns=True,
            )
            if len(results) != 1 or not results[0].hypotheses:
                raise TranslationBackendError("translation_backend_empty_result")
            hypothesis = results[0].hypotheses[0]
            if not isinstance(hypothesis, list) or any(
                not isinstance(token, str) for token in hypothesis
            ):
                raise TranslationBackendError("translation_backend_invalid_result")
            if (
                not hypothesis
                or len(hypothesis) > self._model.max_decoding_tokens
                or hypothesis[-1] != self._eos_token
                or self._eos_token in hypothesis[:-1]
            ):
                raise TranslationBackendError("translation_output_token_budget_exceeded")
            decoded = self._target_processor.decode(hypothesis[:-1])
            if not isinstance(decoded, str) or not decoded.strip():
                raise TranslationBackendError("translation_backend_empty_result")
            return decoded
        except TranslationBackendError:
            raise
        except Exception:
            raise TranslationBackendError("translation_backend_execution_failed") from None

    def _translate_plain_text(self, text: str, *, cancel: Event | None) -> str:
        if not text or not text.strip():
            return text
        # Formatting-only spans have no lexical meaning to infer.  Neural
        # translation of an isolated full stop can hallucinate entire clauses.
        if not any(char.isalnum() for char in text):
            return text
        leading_count = len(text) - len(text.lstrip())
        trailing_count = len(text) - len(text.rstrip())
        leading = text[:leading_count]
        trailing = text[len(text) - trailing_count :] if trailing_count else ""
        core_end = len(text) - trailing_count if trailing_count else len(text)
        core = text[leading_count:core_end]
        chunks = self._split_for_token_budget(core)
        outputs: list[str] = []
        for chunk in chunks:
            self._raise_if_cancelled(cancel)
            # A chunk containing only protected values and punctuation is an
            # identity operation, not a failed-inference fallback.
            if not any(char.isalnum() for char in _SURROGATE.sub("", chunk.text)):
                translated = chunk.text
            else:
                translated = self._translate_one(chunk.text)
            self._raise_if_cancelled(cancel)
            self._validate_surrogates(translated, chunk.text)
            outputs.append(translated)
        if all(output == chunk.text for output, chunk in zip(outputs, chunks, strict=True)):
            return text
        return leading + self._join_outputs(outputs) + trailing

    def _split_for_token_budget(self, text: str) -> tuple[_SourceChunk, ...]:
        prefix_and_eos_cost = 1 + (1 if self._model.source_prefix else 0)
        budget = self._model.max_input_tokens - prefix_and_eos_cost
        if budget < 1:
            raise TranslationBackendError("translation_input_token_budget_invalid")
        if self._token_count(text) <= budget:
            return (_SourceChunk(text=text),)

        chunks: list[_SourceChunk] = []
        start = 0
        while start < len(text):
            end = self._largest_prefix_within_budget(text, start=start, budget=budget)
            if end <= start:
                raise TranslationBackendError("translation_input_cannot_be_segmented")
            if end < len(text):
                end = self._preferred_boundary(text, start=start, hard_end=end)
                # ASCII surrogates are indivisible even when their SP encoding
                # spans several pieces. Never submit half a marker to inference.
                for marker in _SURROGATE.finditer(text, start):
                    if marker.start() >= end:
                        break
                    if marker.start() < end < marker.end():
                        end = marker.start()
                        break
            chunk = text[start:end]
            if not chunk or self._token_count(chunk) > budget:
                raise TranslationBackendError("translation_input_cannot_be_segmented")
            chunks.append(_SourceChunk(text=chunk))
            start = end
        if "".join(chunk.text for chunk in chunks) != text:
            raise TranslationBackendError("translation_input_segmentation_loss")
        return tuple(chunks)

    def _largest_prefix_within_budget(self, text: str, *, start: int, budget: int) -> int:
        low = start + 1
        high = len(text)
        best = start
        while low <= high:
            middle = (low + high) // 2
            count = self._token_count(text[start:middle])
            if count <= budget:
                best = middle
                low = middle + 1
            else:
                high = middle - 1
        return best

    def _token_count(self, text: str) -> int:
        return len(self._source_processor.encode(text, out_type=str))

    @staticmethod
    def _preferred_boundary(text: str, *, start: int, hard_end: int) -> int:
        minimum = start + max(1, (hard_end - start) // 2)
        candidates = [match.end() for match in _NATURAL_BREAK.finditer(text, minimum, hard_end)]
        if candidates:
            return candidates[-1]
        return hard_end

    def _join_outputs(self, outputs: list[str]) -> str:
        if self._direction is TranslationDirection.ZH_TW_TO_EN:
            return " ".join(output.strip() for output in outputs if output.strip())
        return "".join(output.strip() for output in outputs if output.strip())

    @staticmethod
    def _raise_if_cancelled(cancel: Event | None) -> None:
        if cancel is not None and cancel.is_set():
            raise TranslationCancelled()


__all__ = ["CTranslate2BackendFactory"]
