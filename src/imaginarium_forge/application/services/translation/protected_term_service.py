"""Protect layout-sensitive literals and author terminology before inference."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal
from unicodedata import category
from uuid import uuid4

from imaginarium_forge.domain.translation import (
    ProtectedTerm,
    TranslationDirection,
    TranslationWarning,
    TranslationWarningCode,
)


@dataclass(frozen=True, slots=True)
class ProtectedToken:
    marker: str
    replacement: str = field(repr=False)
    source_value: str = field(repr=False)
    kind: str
    markdown_inner_edge: Literal["left", "right"] | None = None


@dataclass(frozen=True, slots=True)
class ProtectedDocument:
    text: str = field(repr=False)
    tokens: tuple[ProtectedToken, ...]


@dataclass(frozen=True, slots=True)
class PathRestorationEvidence:
    source_text: str = field(repr=False)
    output_text: str = field(repr=False)
    source_values: tuple[str, ...] = field(repr=False)
    restored_values: tuple[str, ...] = field(repr=False)
    source_residual_text: str = field(repr=False)
    residual_text: str = field(repr=False)
    boundary_changed: bool


@dataclass(frozen=True, slots=True)
class RestoredDocument:
    text: str = field(repr=False)
    warnings: tuple[TranslationWarning, ...]
    path_evidence: PathRestorationEvidence | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class _ProtectedSpan:
    start: int
    end: int
    source: str = field(repr=False)
    replacement: str = field(repr=False)
    kind: str
    priority: int
    markdown_inner_edge: Literal["left", "right"] | None = None


_DIGITS = r"[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
_TIME = r"\d{1,2}:\d{2}(?::\d{2})?"
_UNITS = r"(?:°[CF]|km|cm|mm|kg|mg|mL|ml|GB|MB|KB|GiB|MiB|KiB|Hz|kHz|MHz|GHz|ms|m|g|L|s)"
_NUMBER_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_])(?:"
    r"\d{4}[-/]\d{1,2}[-/]\d{1,2}"
    rf"|{_TIME}(?:[ \t]*[–—-][ \t]*{_TIME})?"
    rf"|(?:[A-Z]{{2,3}}\$|[$€£¥₩]|(?:USD|EUR|GBP|JPY|TWD|CNY)[ \t]+){_DIGITS}"
    rf"|{_DIGITS}(?:[ \t]*{_UNITS}|[%％])?"
    r")(?![A-Za-z0-9_])"
)
_ORDERED_PREFIX = re.compile(r"(?m)^[ \t]*\d+[.)][ \t]+")
_MARKDOWN_LINK = re.compile(r"!?\[(?P<label>[^\]\r\n]+)\]\((?P<target>[^\s()]+)\)")
_MARKDOWN_EMPHASIS = (
    re.compile(r"(?<!\*)(\*\*)(?=\S)[^\r\n]+?(?<=\S)\1(?!\*)"),
    re.compile(r"(?<![\w_])(__)(?=\S)[^\r\n]+?(?<=\S)\1(?![\w_])"),
    re.compile(r"(?<![\w*])(\*)(?!\*)(?=\S)[^\r\n]+?(?<=\S)\1(?![\w*])"),
    re.compile(r"(?<![\w_])(_)(?!_)(?=\S)[^\r\n]+?(?<=\S)\1(?![\w_])"),
    re.compile(r"(?<!~)(~~)(?=\S)[^\r\n]+?(?<=\S)\1(?!~)"),
)

_BUILTIN_PATTERNS: tuple[tuple[str, int, re.Pattern[str]], ...] = (
    ("code", 0, re.compile(r"```[^\n]*\n.*?```|~~~[^\n]*\n.*?~~~", re.DOTALL)),
    ("code", 1, re.compile(r"`[^`\r\n]+`")),
    ("url", 2, re.compile(r"https?://[^\s<>()`\[\]。！？，；：「」『』]+", re.IGNORECASE)),
    (
        "email",
        3,
        re.compile(r"(?<![\w.+-])[\w.+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}(?![\w.-])"),
    ),
    (
        "path",
        4,
        re.compile(
            r"(?<!\w)[A-Za-z]:\\(?:[^\\\s/:*?\"<>|\r\n]+\\)*"
            r"[^\\\s/:*?\"<>|\r\n]+?"
            r"(?=(?:[。！？，,；;!?]|\.(?=\s|$)|\s|$))"
        ),
    ),
    (
        "path",
        5,
        re.compile(
            r"(?<![\w:/])/(?:[^/\s<>]+/)*[^/\s<>]+?"
            r"(?=(?:[。！？，,；;!?]|\.(?=\s|$)|\s|$))"
        ),
    ),
    (
        "filename",
        6,
        re.compile(
            r"(?<![A-Za-z0-9_.-])[A-Za-z0-9_.-]+\."
            r"(?:txt|md|json|ya?ml|toml|csv|tsv|py|js|ts|html|css|pdf|docx?|"
            r"xlsx?|png|jpe?g|gif|webp|bin|zip|tar|gz|7z|exe|dll|spm)(?![A-Za-z0-9_-])",
            re.IGNORECASE,
        ),
    ),
    (
        "filename",
        6,
        re.compile(
            r"(?<![\w.-])[\w.-]+\."
            r"(?:txt|md|json|ya?ml|toml|csv|tsv|py|js|ts|html|css|pdf|docx?|"
            r"xlsx?|png|jpe?g|gif|webp|bin|zip|tar|gz|7z|exe|dll|spm)(?![A-Za-z0-9_-])",
            re.IGNORECASE,
        ),
    ),
    (
        "identifier",
        7,
        re.compile(r"(?<![A-Za-z0-9_])ISBN(?:-1[03])?[ \t]+[\dXx][\dXx -]+[\dXx](?![A-Za-z0-9_])"),
    ),
    (
        "identifier",
        7,
        re.compile(r"(?<![A-Za-z0-9_])v?\d+(?:\.\d+){2,}(?:-[A-Za-z0-9.-]+)?(?![A-Za-z0-9_])"),
    ),
    (
        "identifier",
        7,
        re.compile(
            r"(?<![A-Za-z0-9_])[A-Za-z][A-Za-z0-9]*(?:[-_][A-Za-z0-9]+)*(?:[-_]\d[A-Za-z0-9]*)(?![A-Za-z0-9_])"
        ),
    ),
    ("number", 8, _NUMBER_PATTERN),
)
_GENERATED_MARKER = re.compile(r"__IMF_[A-F0-9]{12}_\d{6}__")
_PATH_KINDS = frozenset({"path", "filename"})
_PATH_AFFIX_LEFT = re.compile(r"[A-Za-z0-9_./\\:-]+$")
_PATH_AFFIX_RIGHT = re.compile(r"^[A-Za-z0-9_./\\:-]+")


class ProtectedTermService:
    """Replace exact protected spans with collision-resistant inert markers."""

    def protect(
        self,
        text: str,
        *,
        direction: TranslationDirection,
        terms: tuple[ProtectedTerm, ...],
    ) -> ProtectedDocument:
        spans = self._builtin_spans(text)

        source_attr, target_attr = (
            ("zh_tw", "en") if direction is TranslationDirection.ZH_TW_TO_EN else ("en", "zh_tw")
        )
        for term in sorted(
            terms,
            key=lambda item: len(getattr(item, source_attr)),
            reverse=True,
        ):
            source = getattr(term, source_attr)
            replacement = getattr(term, target_attr)
            flags = re.IGNORECASE if source.isascii() else 0
            boundaries = r"(?<![A-Za-z0-9_]){}(?![A-Za-z0-9_])" if source.isascii() else r"{}"
            pattern = re.compile(boundaries.format(re.escape(source)), flags)
            spans.extend(
                _ProtectedSpan(
                    start=match.start(),
                    end=match.end(),
                    source=match.group(0),
                    replacement=replacement,
                    kind="glossary",
                    # Built-in literals win an exact-start tie. A glossary term
                    # inside a code block, URL, path, or filename must not punch
                    # a hole through the larger protected span.
                    priority=10,
                )
                for match in pattern.finditer(text)
            )

        selected = self._select_non_overlapping(spans)
        nonce = self._unused_nonce(text)
        tokens: list[ProtectedToken] = []
        chunks: list[str] = []
        offset = 0
        for index, span in enumerate(selected, start=1):
            marker = f"__IMF_{nonce}_{index:06d}__"
            chunks.append(text[offset : span.start])
            chunks.append(marker)
            tokens.append(
                ProtectedToken(
                    marker=marker,
                    replacement=span.replacement,
                    source_value=span.source,
                    kind=span.kind,
                    markdown_inner_edge=span.markdown_inner_edge,
                )
            )
            offset = span.end
        chunks.append(text[offset:])
        return ProtectedDocument(text="".join(chunks), tokens=tuple(tokens))

    @staticmethod
    def literal_values(text: str, *, kinds: frozenset[str]) -> tuple[str, ...]:
        """Use exactly the same atomic extraction for protection and QA."""

        return tuple(
            span.source
            for span in ProtectedTermService._select_non_overlapping(
                ProtectedTermService._builtin_spans(text)
            )
            if span.kind in kinds
        )

    @staticmethod
    def markdown_signature(text: str) -> tuple[tuple[str, str | None], ...]:
        """Describe the supported inline subset, including delimiter roles."""

        return tuple(
            (span.source, span.markdown_inner_edge)
            for span in ProtectedTermService._select_non_overlapping(
                ProtectedTermService._builtin_spans(text)
            )
            if span.kind == "markdown"
        )

    @staticmethod
    def _builtin_spans(text: str) -> list[_ProtectedSpan]:
        spans: list[_ProtectedSpan] = []
        ordered_prefixes = tuple(
            (match.start(), match.end()) for match in _ORDERED_PREFIX.finditer(text)
        )
        for kind, priority, pattern in _BUILTIN_PATTERNS:
            for match in pattern.finditer(text):
                # Layout is removed by TextSegmenter after protection. Numbering
                # must remain visible to it instead of becoming an opaque token.
                if kind == "number" and any(
                    start <= match.start() < end for start, end in ordered_prefixes
                ):
                    continue
                value = match.group(0)
                if kind == "url":
                    value = value.rstrip(".,;:!?")
                spans.append(
                    _ProtectedSpan(
                        match.start(), match.start() + len(value), value, value, kind, priority
                    )
                )
        for match in _MARKDOWN_LINK.finditer(text):
            ranges = (
                (match.start(), match.start("label")),
                (match.end("label"), match.start("target")),
                (match.end("target"), match.end()),
            )
            for start, end in ranges:
                value = text[start:end]
                spans.append(_ProtectedSpan(start, end, value, value, "markdown", 9))
        for pattern in _MARKDOWN_EMPHASIS:
            for match in pattern.finditer(text):
                length = len(match.group(1))
                opener = match.start(), match.start() + length
                closer = match.end() - length, match.end()
                if not ProtectedTermService._valid_emphasis_pair(text, opener, closer):
                    continue
                edges: tuple[tuple[tuple[int, int], Literal["left", "right"]], ...] = (
                    (opener, "right"),
                    (closer, "left"),
                )
                for (start, end), inner_edge in edges:
                    value = text[start:end]
                    spans.append(
                        _ProtectedSpan(start, end, value, value, "markdown", 9, inner_edge)
                    )
        return spans

    @staticmethod
    def _valid_emphasis_pair(text: str, opener: tuple[int, int], closer: tuple[int, int]) -> bool:
        """Check flanking/escapes for bounded, single-line delimiter pairs.

        This is not a full CommonMark parser: arbitrary delimiter runs,
        cross-line emphasis, HTML and reference links remain outside this
        structural repair. Those inputs still retain exact source identity.
        """

        def flanking(start: int, end: int) -> tuple[bool, bool]:
            previous = text[start - 1] if start else " "
            following = text[end] if end < len(text) else " "
            previous_punctuation = category(previous).startswith(("P", "S"))
            following_punctuation = category(following).startswith(("P", "S"))
            left = not following.isspace() and (
                not following_punctuation or previous.isspace() or previous_punctuation
            )
            right = not previous.isspace() and (
                not previous_punctuation or following.isspace() or following_punctuation
            )
            if text[start] == "_":
                return left and (not right or previous_punctuation), right and (
                    not left or following_punctuation
                )
            return left, right

        for start, _end in (opener, closer):
            backslashes = 0
            cursor = start - 1
            while cursor >= 0 and text[cursor] == "\\":
                backslashes += 1
                cursor -= 1
            if backslashes % 2:
                return False
        return flanking(*opener)[0] and flanking(*closer)[1]

    def restore(
        self,
        text: str,
        tokens: tuple[ProtectedToken, ...],
        *,
        expected_markers: frozenset[str] | None = None,
        protected_source_text: str | None = None,
    ) -> RestoredDocument:
        restored = text
        warnings: list[TranslationWarning] = []
        # Repair only exact known marker edges before expanding any protected
        # values. This cannot trim spaces authored inside code/glossary payloads,
        # and [ \t] intentionally never consumes or crosses a newline.
        for token in tokens:
            if expected_markers is not None and token.marker not in expected_markers:
                continue
            if token.markdown_inner_edge == "right":
                restored = re.sub(re.escape(token.marker) + r"[ \t]+", token.marker, restored)
            elif token.markdown_inner_edge == "left":
                restored = re.sub(r"[ \t]+" + re.escape(token.marker), token.marker, restored)
        selected = {
            token.marker: token
            for token in tokens
            if expected_markers is None or token.marker in expected_markers
        }
        marked_text = restored
        for token in selected.values():
            count = marked_text.count(token.marker)
            if count == 0:
                warnings.append(
                    TranslationWarning(
                        code=TranslationWarningCode.PROTECTED_TOKEN_MISSING,
                        message="有一個受保護項目未能自動復原，請核對譯文。",
                    )
                )
                continue
            if count > 1:
                warnings.append(
                    TranslationWarning(
                        code=TranslationWarningCode.PROTECTED_TOKEN_DUPLICATED,
                        message="有一個受保護項目在譯文中重複出現，請核對譯文。",
                    )
                )
        marker_pattern = (
            re.compile("|".join(re.escape(marker) for marker in selected)) if selected else None
        )
        if marker_pattern is not None:
            # One pass prevents protected payloads containing marker-like text
            # from being reinterpreted as instructions to expand other tokens.
            restored = marker_pattern.sub(
                lambda match: selected[match.group(0)].replacement, marked_text
            )
        if _GENERATED_MARKER.search(restored):
            warnings.append(
                TranslationWarning(
                    code=TranslationWarningCode.PROTECTED_TOKEN_REMAINING,
                    message="譯文仍包含未復原的保護標記，請核對譯文。",
                )
            )
        path_evidence = None
        if (
            protected_source_text is not None
            and expected_markers is None
            and not warnings
            and marker_pattern is not None
            and all(protected_source_text.count(marker) == 1 for marker in selected)
            and all(
                token.source_value == token.replacement
                for token in tokens
                if token.kind in _PATH_KINDS
            )
        ):
            source_text = marker_pattern.sub(
                lambda match: selected[match.group(0)].source_value, protected_source_text
            )
            source_values = tuple(
                token.source_value for token in tokens if token.kind in _PATH_KINDS
            )
            restored_values = tuple(
                selected[match.group(0)].replacement
                for match in marker_pattern.finditer(marked_text)
                if selected[match.group(0)].kind in _PATH_KINDS
            )
            residual = marker_pattern.sub(
                lambda match: (
                    " "
                    if selected[match.group(0)].kind in _PATH_KINDS
                    else selected[match.group(0)].replacement
                ),
                marked_text,
            )
            source_residual = marker_pattern.sub(
                lambda match: (
                    " "
                    if selected[match.group(0)].kind in _PATH_KINDS
                    else selected[match.group(0)].source_value
                ),
                protected_source_text,
            )
            path_evidence = PathRestorationEvidence(
                source_text=source_text,
                output_text=restored,
                source_values=source_values,
                restored_values=restored_values,
                source_residual_text=source_residual,
                residual_text=residual,
                boundary_changed=self._path_boundaries(
                    protected_source_text, marker_pattern, selected, source=True
                )
                != self._path_boundaries(marked_text, marker_pattern, selected),
            )
        return RestoredDocument(
            text=restored, warnings=tuple(warnings), path_evidence=path_evidence
        )

    @staticmethod
    def _path_boundaries(
        text: str,
        marker_pattern: re.Pattern[str],
        tokens: dict[str, ProtectedToken],
        *,
        source: bool = False,
    ) -> dict[str, tuple[str, str]]:
        """Compare plain ASCII affixes without reading through other markers."""

        # A glossary expansion can itself add an ASCII filename affix. Expand
        # non-path payloads for this comparison; other path atoms stay opaque.
        text = marker_pattern.sub(
            lambda match: (
                match.group(0)
                if tokens[match.group(0)].kind in _PATH_KINDS
                else (
                    tokens[match.group(0)].source_value
                    if source
                    else tokens[match.group(0)].replacement
                )
            ),
            text,
        )
        matches = list(marker_pattern.finditer(text))
        output: dict[str, tuple[str, str]] = {}
        for index, match in enumerate(matches):
            if tokens[match.group(0)].kind not in _PATH_KINDS:
                continue
            left_start = matches[index - 1].end() if index else 0
            right_end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
            left = _PATH_AFFIX_LEFT.search(text[left_start : match.start()])
            right = _PATH_AFFIX_RIGHT.match(text[match.end() : right_end])
            left_value = left.group(0) if left else ""
            right_value = right.group(0) if right else ""
            # A bare terminal period/colon is sentence punctuation, whereas
            # .bak, /subpath and renamed- are material filename/path affixes.
            if right_value in {".", ":"}:
                right_value = ""
            output[match.group(0)] = left_value, right_value
        return output

    @staticmethod
    def _select_non_overlapping(spans: list[_ProtectedSpan]) -> tuple[_ProtectedSpan, ...]:
        ordered = sorted(spans, key=lambda item: (item.start, item.priority, -item.end))
        selected: list[_ProtectedSpan] = []
        for span in ordered:
            if span.start == span.end:
                continue
            # ``ordered`` is monotonic by start and ``selected`` never
            # overlaps, so only the most recent interval can intersect.
            if selected and span.start < selected[-1].end:
                continue
            selected.append(span)
        return tuple(sorted(selected, key=lambda item: item.start))

    @staticmethod
    def _unused_nonce(text: str) -> str:
        while True:
            nonce = uuid4().hex[:12].upper()
            if f"__IMF_{nonce}_" not in text:
                return nonce


__all__ = [
    "PathRestorationEvidence",
    "ProtectedDocument",
    "ProtectedTermService",
    "ProtectedToken",
    "RestoredDocument",
]
