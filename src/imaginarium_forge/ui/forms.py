"""Structured form helpers for the Streamlit UI (A-05/A-07).

Canonical traits are edited as one trait per line in a fixed, validated format:

    category | name | descriptor | strength

strength ∈ {hard_lock, soft_canon, preference} — scene_override is rejected for
base versions (canon rule). Parsing errors carry the offending line number so
the UI can show precise Traditional Chinese messages. This is the "structured
and validated" primary workflow the spec requires; raw JSON is not exposed.
"""

from __future__ import annotations

from imaginarium_forge.domain.canon.traits import CanonicalTrait
from imaginarium_forge.domain.common.enums import CanonStrength

_ALLOWED_STRENGTHS = {
    CanonStrength.HARD_LOCK.value,
    CanonStrength.SOFT_CANON.value,
    CanonStrength.PREFERENCE.value,
}


def parse_trait_lines(raw: str) -> tuple[CanonicalTrait, ...]:
    """Parse the line format into validated traits. Raises ValueError with zh-TW detail."""
    traits: list[CanonicalTrait] = []
    for line_number, raw_line in enumerate(raw.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [part.strip() for part in line.split("|")]
        if len(parts) != 4:
            raise ValueError(
                f"第 {line_number} 行格式錯誤：需要「分類 | 名稱 | 描述 | 強度」四段，"
                f"目前有 {len(parts)} 段"
            )
        category, name, descriptor, strength = parts
        if strength == CanonStrength.SCENE_OVERRIDE.value:
            raise ValueError(
                f"第 {line_number} 行：scene_override（場景暫時覆寫）不得存入固定特徵"
                "（場景暫時覆寫不會改變固定特徵）"
            )
        if strength not in _ALLOWED_STRENGTHS:
            raise ValueError(
                f"第 {line_number} 行：未知強度「{strength}」；"
                f"允許：{', '.join(sorted(_ALLOWED_STRENGTHS))}"
            )
        try:
            traits.append(
                CanonicalTrait(
                    category=category,
                    name=name,
                    canonical_descriptor=descriptor,
                    strength=CanonStrength(strength),
                )
            )
        except ValueError as exc:
            raise ValueError(f"第 {line_number} 行：{exc}") from exc
    return tuple(traits)


def format_trait_lines(traits: tuple[CanonicalTrait, ...]) -> str:
    """Inverse of parse_trait_lines for prefilling the editor."""
    return "\n".join(
        f"{t.category} | {t.name} | {t.canonical_descriptor} | {t.strength.value}"
        for t in traits
    )


def parse_comma_list(raw: str) -> tuple[str, ...]:
    """Comma/newline separated list → trimmed non-empty tuple."""
    items: list[str] = []
    for chunk in raw.replace("\n", ",").split(","):
        item = chunk.strip()
        if item:
            items.append(item)
    return tuple(items)
