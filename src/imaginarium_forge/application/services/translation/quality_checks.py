"""Non-destructive QA checks for locally translated documents."""

from __future__ import annotations

import re
from collections import Counter

from imaginarium_forge.application.services.translation.protected_term_service import (
    PathRestorationEvidence,
    ProtectedTermService,
)
from imaginarium_forge.domain.translation import (
    TranslationDirection,
    TranslationWarning,
    TranslationWarningCode,
)

_CODE_FENCE = re.compile(r"```[^\n]*\n.*?```|~~~[^\n]*\n.*?~~~", re.DOTALL)
_INLINE_CODE = re.compile(r"`[^`\r\n]+`")
_SIMPLIFIED_ONLY_HINTS = frozenset(
    "这为发么们个来时说对会还从过两种样应与并网软数开关复线译显设历经实达长门风"
)


class TranslationQualityChecks:
    """Return actionable warnings without deleting or rewriting output."""

    def evaluate(
        self,
        *,
        source_text: str,
        translated_text: str,
        direction: TranslationDirection,
        prior_warnings: tuple[TranslationWarning, ...] = (),
        empty_segment_ids: tuple[str, ...] = (),
        path_evidence: PathRestorationEvidence | None = None,
    ) -> tuple[TranslationWarning, ...]:
        warnings = list(prior_warnings)
        warnings.extend(
            TranslationWarning(
                code=TranslationWarningCode.EMPTY_SEGMENT,
                segment_id=segment_id,
                message="有一個段落沒有產生譯文，原文與其他結果仍保留。",
            )
            for segment_id in empty_segment_ids
        )

        checks: tuple[tuple[frozenset[str], TranslationWarningCode, str], ...] = (
            (
                frozenset({"number", "identifier"}),
                TranslationWarningCode.NUMBER_CHANGED,
                "譯文中的數字、日期、量值或識別碼可能有變動。",
            ),
            (frozenset({"url"}), TranslationWarningCode.URL_CHANGED, "譯文中的網址可能有變動。"),
            (
                frozenset({"email"}),
                TranslationWarningCode.EMAIL_CHANGED,
                "譯文中的電子郵件可能有變動。",
            ),
            (
                frozenset({"path", "filename"}),
                TranslationWarningCode.PATH_CHANGED,
                "譯文中的檔案路徑或檔名可能有變動。",
            ),
        )
        for kinds, code, message in checks:
            source_values = Counter(ProtectedTermService.literal_values(source_text, kinds=kinds))
            target_values = Counter(
                ProtectedTermService.literal_values(translated_text, kinds=kinds)
            )
            boundary_changed = False
            if (
                code is TranslationWarningCode.PATH_CHANGED
                and path_evidence is not None
                and path_evidence.source_text == source_text
                and path_evidence.output_text == translated_text
                and Counter(path_evidence.source_values) == source_values
                and not any(
                    warning.code
                    in {
                        TranslationWarningCode.PROTECTED_TOKEN_MISSING,
                        TranslationWarningCode.PROTECTED_TOKEN_DUPLICATED,
                        TranslationWarningCode.PROTECTED_TOKEN_REMAINING,
                    }
                    for warning in prior_warnings
                )
            ):
                # Masking can expose a pre-existing /suffix as a separate path.
                # Compare both residuals, not an artificially empty baseline.
                source_values += Counter(
                    ProtectedTermService.literal_values(
                        path_evidence.source_residual_text, kinds=kinds
                    )
                )
                target_values = Counter(path_evidence.restored_values) + Counter(
                    ProtectedTermService.literal_values(path_evidence.residual_text, kinds=kinds)
                )
                boundary_changed = path_evidence.boundary_changed
            if source_values != target_values or boundary_changed:
                warnings.append(TranslationWarning(code=code, message=message))

        source_code = _CODE_FENCE.findall(source_text) + _INLINE_CODE.findall(source_text)
        target_code = _CODE_FENCE.findall(translated_text) + _INLINE_CODE.findall(translated_text)
        if Counter(source_code) != Counter(target_code):
            warnings.append(
                TranslationWarning(
                    code=TranslationWarningCode.CODE_CHANGED,
                    message="譯文中的程式碼區塊或行內程式碼可能有變動。",
                )
            )

        if self._layout_signature(source_text) != self._layout_signature(translated_text):
            warnings.append(
                TranslationWarning(
                    code=TranslationWarningCode.LAYOUT_CHANGED,
                    message="譯文的段落、列表或 Markdown 結構可能有變動。",
                )
            )

        if self._has_large_source_leak(source_text, translated_text):
            warnings.append(
                TranslationWarning(
                    code=TranslationWarningCode.SOURCE_LEAKAGE,
                    message="譯文可能仍包含一段未翻譯的原文。",
                )
            )

        if direction is TranslationDirection.EN_TO_ZH_TW:
            drift = sum(translated_text.count(char) for char in _SIMPLIFIED_ONLY_HINTS)
            cjk = sum("\u3400" <= char <= "\u9fff" for char in translated_text)
            if drift and (cjk == 0 or drift / max(cjk, 1) > 0.002):
                warnings.append(
                    TranslationWarning(
                        code=TranslationWarningCode.SIMPLIFIED_CHINESE_DRIFT,
                        message="譯文可能仍含有簡體中文用字，請核對。",
                    )
                )

        paragraphs = [
            re.sub(r"\s+", " ", paragraph).strip().casefold()
            for paragraph in re.split(r"(?:\r\n|\n|\r){2,}", translated_text)
            if len(paragraph.strip()) >= 30
        ]
        if any(count > 1 for count in Counter(paragraphs).values()):
            warnings.append(
                TranslationWarning(
                    code=TranslationWarningCode.REPEATED_PARAGRAPH,
                    message="譯文中出現明顯重複的段落。",
                )
            )
        return self._deduplicate(tuple(warnings))

    @staticmethod
    def _layout_signature(
        text: str,
    ) -> tuple[tuple[str, ...], tuple[str, ...], tuple[tuple[str, str | None], ...]]:
        line_endings = tuple(re.findall(r"\r\n|\n|\r", text))
        markdown = tuple(
            match.group(0)
            for match in re.finditer(
                r"(?m)^\s*(?:#{1,6}\s+|>\s+|[-+*]\s+|\d+[.)]\s+|\[[ xX]\]\s+)",
                text,
            )
        )
        return line_endings, markdown, ProtectedTermService.markdown_signature(text)

    @staticmethod
    def _has_large_source_leak(source_text: str, translated_text: str) -> bool:
        source_without_literals = source_text
        for value in ProtectedTermService.literal_values(
            source_text,
            kinds=frozenset({"code", "url", "email", "path", "filename", "number", "identifier"}),
        ):
            source_without_literals = source_without_literals.replace(value, " ")
        target_compact = re.sub(r"\s+", " ", translated_text).casefold()
        for paragraph in re.split(r"(?:\r\n|\n|\r)+", source_without_literals):
            compact = re.sub(r"\s+", " ", paragraph).strip().casefold()
            if len(compact) >= 60 and compact in target_compact:
                return True
        return False

    @staticmethod
    def _deduplicate(
        warnings: tuple[TranslationWarning, ...],
    ) -> tuple[TranslationWarning, ...]:
        seen: set[tuple[TranslationWarningCode, str | None]] = set()
        output: list[TranslationWarning] = []
        for warning in warnings:
            key = warning.code, warning.segment_id
            if key in seen:
                continue
            seen.add(key)
            output.append(warning)
        return tuple(output)


__all__ = ["TranslationQualityChecks"]
