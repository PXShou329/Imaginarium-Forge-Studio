"""Filename inference — the LOWEST metadata-resolution priority (spec §33.4).

Only conservative, non-invented hints: base-model family guessed from common
filename substrings. Anything else stays empty and MetadataSource reflects it.
"""

from __future__ import annotations

_FAMILY_HINTS: tuple[tuple[str, str], ...] = (
    ("illustrious", "Illustrious (SDXL family)"),
    ("noobai", "NoobAI (SDXL family)"),
    ("pony", "Pony (SDXL family)"),
    ("sdxl", "SDXL"),
    ("xl", "SDXL family (filename hint)"),
    ("sd15", "SD 1.5"),
    ("sd21", "SD 2.1"),
)


def infer_base_model_hint(filename: str) -> str:
    """Return a conservative family hint or empty string. Never invents versions."""
    lowered = filename.casefold()
    for needle, hint in _FAMILY_HINTS:
        if needle in lowered:
            return hint
    return ""
