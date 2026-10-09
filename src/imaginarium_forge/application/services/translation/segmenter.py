"""Layout-aware segmentation and exact document reassembly."""

from __future__ import annotations

import re
from bisect import bisect_right
from collections.abc import Mapping
from dataclasses import dataclass, field

_LINE_ENDING = re.compile(r"(\r\n|\n|\r)$")
_MARKDOWN_PREFIX = re.compile(
    r"^(?P<prefix>\s*(?:(?:#{1,6}|>|\d+[.)])\s+|"
    r"[-+*]\s+(?:\[[ xX]\]\s+)?|(?:\[[ xX]\])\s+))"
)
_SAFE_BREAK = re.compile(r"(?<=[.!?。！？；;])\s+|\s+")
_ABBREVIATIONS = frozenset(
    {"mr", "mrs", "ms", "dr", "prof", "sr", "jr", "st", "vs", "etc", "e.g", "i.e", "fig", "no"}
)
_LATIN_SENTENCE_END = re.compile(r"[A-Za-z0-9][.!?][\"')\]]*$")
_LATIN_SENTENCE_START = re.compile(r"[\"'(\[]*[A-Za-z0-9]")


@dataclass(frozen=True, slots=True)
class TranslationSegment:
    segment_id: str
    text: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class DocumentPart:
    literal: str = field(default="", repr=False)
    segment_id: str | None = None
    soft_sentence_boundary: bool = False


@dataclass(frozen=True, slots=True)
class SegmentedDocument:
    parts: tuple[DocumentPart, ...]
    segments: tuple[TranslationSegment, ...]

    def reassemble(
        self,
        translated: Mapping[str, str],
        *,
        stop_at_first_missing: bool = False,
    ) -> str:
        chunks: list[str] = []
        source_segments = {segment.segment_id: segment.text for segment in self.segments}
        soft_boundary = False
        previous_source = ""
        previous_output = ""
        for part in self.parts:
            if part.segment_id is None:
                chunks.append(part.literal)
                soft_boundary = part.soft_sentence_boundary
                continue
            value = translated.get(part.segment_id)
            if value is None:
                if stop_at_first_missing:
                    break
                raise ValueError(f"missing translated segment: {part.segment_id}")
            source = source_segments[part.segment_id]
            if (
                soft_boundary
                and (value != source or previous_output != previous_source)
                and _LATIN_SENTENCE_END.search(previous_output)
                and _LATIN_SENTENCE_START.match(value)
            ):
                # CJK does not require a source-space between sentences. A
                # translated Latin sentence does; identity reconstruction is
                # nevertheless byte-for-byte, including mixed-language input.
                chunks.append(" ")
            chunks.append(value)
            previous_source = source
            previous_output = value
            soft_boundary = False
        return "".join(chunks)


class TextSegmenter:
    """Prefer lines/paragraphs, then split long prose at quote-safe boundaries."""

    def segment(
        self,
        text: str,
        *,
        max_chars: int,
        preserve_markdown: bool,
    ) -> SegmentedDocument:
        if max_chars < 1:
            raise ValueError("max_chars must be positive")
        parts: list[DocumentPart] = []
        segments: list[TranslationSegment] = []

        for line in text.splitlines(keepends=True):
            ending_match = _LINE_ENDING.search(line)
            ending = ending_match.group(0) if ending_match else ""
            content = line[: -len(ending)] if ending else line
            if not content.strip():
                parts.append(DocumentPart(literal=content + ending))
                continue

            prefix = ""
            if preserve_markdown:
                prefix_match = _MARKDOWN_PREFIX.match(content)
                if prefix_match:
                    prefix = prefix_match.group("prefix")
                    content = content[len(prefix) :]
            leading_count = len(content) - len(content.lstrip())
            trailing_count = len(content) - len(content.rstrip())
            leading = content[:leading_count]
            trailing = content[len(content) - trailing_count :] if trailing_count else ""
            core_end = len(content) - trailing_count if trailing_count else len(content)
            core = content[leading_count:core_end]

            parts.append(DocumentPart(literal=prefix + leading))
            chunks = self._split_long_prose(core, max_chars=max_chars)
            for chunk_index, (chunk, separator) in enumerate(chunks):
                if chunk:
                    segment_id = f"segment-{len(segments) + 1:06d}"
                    segments.append(TranslationSegment(segment_id=segment_id, text=chunk))
                    parts.append(DocumentPart(segment_id=segment_id))
                if separator:
                    parts.append(DocumentPart(literal=separator))
                elif chunk_index + 1 < len(chunks):
                    parts.append(DocumentPart(soft_sentence_boundary=True))
            parts.append(DocumentPart(literal=trailing + ending))

        if not text.endswith(("\n", "\r")) and not text.splitlines(keepends=True):
            # ``splitlines`` returns an empty list only for empty text; requests
            # reject that value, but keeping this method total helps unit tests.
            parts.append(DocumentPart(literal=text))
        if not segments:
            raise ValueError("document contains no translatable text")
        return SegmentedDocument(parts=tuple(parts), segments=tuple(segments))

    @staticmethod
    def _split_long_prose(text: str, *, max_chars: int) -> tuple[tuple[str, str], ...]:
        output: list[tuple[str, str]] = []
        start = 0
        for boundary, separator_end in TextSegmenter._sentence_breaks(text):
            chunks = list(TextSegmenter._split_at_size(text[start:boundary], max_chars=max_chars))
            if chunks:
                last, separator = chunks[-1]
                chunks[-1] = (last, separator + text[boundary:separator_end])
            output.extend(chunks)
            start = separator_end
        if start < len(text):
            output.extend(TextSegmenter._split_at_size(text[start:], max_chars=max_chars))
        return tuple(output)

    @staticmethod
    def _sentence_breaks(text: str) -> tuple[tuple[int, int], ...]:
        pairs = {"“": "”", "「": "」", "『": "』", '"': '"', "'": "'"}
        stack: list[str] = []
        boundaries: list[tuple[int, int]] = []
        escaped = False
        pending_quote_end = False
        for index, char in enumerate(text):
            if escaped:
                escaped = False
                continue
            if char == "\\":
                escaped = True
                continue
            is_end = False
            if stack and char == stack[-1]:
                stack.pop()
                is_end = pending_quote_end and not stack
            elif char in pairs:
                if char in {'"', "'"} and index > 0 and text[index - 1].isalnum():
                    continue
                stack.append(pairs[char])
            elif char in "。！？!?" or (char == "." and TextSegmenter._is_period_end(text, index)):
                if stack:
                    pending_quote_end = True
                else:
                    is_end = True
            elif not char.isspace():
                pending_quote_end = False
            if not is_end:
                continue
            end = index + 1
            # Consecutive punctuation belongs to one sentence, not a standalone
            # punctuation segment submitted to a generative translation model.
            if end < len(text) and text[end] in "。！？!?":
                continue
            separator_end = end
            while separator_end < len(text) and text[separator_end].isspace():
                separator_end += 1
            boundaries.append((end, separator_end))
            pending_quote_end = False
        return tuple(boundaries)

    @staticmethod
    def _is_period_end(text: str, index: int) -> bool:
        if (
            index + 1 < len(text)
            and not text[index + 1].isspace()
            and text[index + 1] not in "\"'”」』"
        ):
            return False
        word_match = re.search(r"[A-Za-z.]+$", text[: index + 1])
        if word_match:
            word = word_match.group(0)
            if word.rstrip(".").casefold() in _ABBREVIATIONS or re.fullmatch(
                r"(?:[A-Za-z]\.)+", word
            ):
                return False
        return True

    @staticmethod
    def _split_at_size(text: str, *, max_chars: int) -> tuple[tuple[str, str], ...]:
        if len(text) <= max_chars:
            return ((text, ""),)

        breaks = TextSegmenter._quote_safe_breaks(text)
        break_starts = [boundary for boundary, _separator_end in breaks]
        output: list[tuple[str, str]] = []
        start = 0
        while len(text) - start > max_chars:
            first = bisect_right(break_starts, start)
            after_budget = bisect_right(break_starts, start + max_chars)
            if after_budget > first:
                boundary, separator_end = breaks[after_budget - 1]
            elif after_budget < len(breaks):
                # Preserve an open dialogue span rather than cutting inside it.
                boundary, separator_end = breaks[after_budget]
            else:
                # A no-whitespace token is handed intact to the backend, which
                # applies the model's measured token budget without truncation.
                break
            output.append((text[start:boundary], text[boundary:separator_end]))
            start = separator_end
        if start < len(text):
            output.append((text[start:], ""))
        return tuple(output)

    @staticmethod
    def _quote_safe_breaks(text: str) -> list[tuple[int, int]]:
        pairs = {"“": "”", "「": "」", "『": "』", '"': '"', "'": "'"}
        closing = set(pairs.values())
        stack: list[str] = []
        safe_ranges: list[tuple[int, int]] = []
        cursor = 0
        escaped = False
        for match in _SAFE_BREAK.finditer(text):
            for index in range(cursor, match.start()):
                char = text[index]
                if escaped:
                    escaped = False
                    continue
                if char == "\\":
                    escaped = True
                    continue
                if stack and char == stack[-1]:
                    stack.pop()
                elif char in pairs:
                    if char in {'"', "'"} and index > 0 and text[index - 1].isalnum():
                        # Apostrophes in contractions/possessives are not quote
                        # delimiters. A matching straight quote already closed
                        # in the branch above.
                        continue
                    stack.append(pairs[char])
                elif char in closing:
                    continue
            if not stack:
                safe_ranges.append((match.start(), match.end()))
            # Consume the whitespace run so a preceding escape does not leak
            # into the next span.
            for char in text[match.start() : match.end()]:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
            cursor = match.end()
        return safe_ranges


__all__ = [
    "DocumentPart",
    "SegmentedDocument",
    "TextSegmenter",
    "TranslationSegment",
]
