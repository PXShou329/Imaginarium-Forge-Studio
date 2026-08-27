"""Mock parser smoke (§11.2): `python -m imaginarium_forge.smokes.parse_pipeline`.

Runs the REAL VisualSceneParsingService over a scripted MockProvider —
no Ollama, no network — and verifies:

1. a valid draft parses to an OK result with field states;
2. bounded repair recovers from one malformed response;
3. exhausted repair falls back with the source text preserved.

Exit 0 on success, 1 with a message on failure. Windows-safe (pure Python).
"""

from __future__ import annotations

import json
import sys

from imaginarium_forge.application.services.scene_parsing_service import (
    ParsingErrorReason,
    ParsingStatus,
    VisualSceneParsingRequest,
    VisualSceneParsingService,
)
from imaginarium_forge.providers.mock import MockProvider

_VALID = json.dumps(
    {
        "subjects": [
            {
                "presentation": "adult woman",
                "pose": "standing on a bridge",
                "expression": "calm",
                "emotional_subtext": "holding_back_tears",
            }
        ],
        "camera_shot": "full body",
        "camera_angle": "low angle",
        "negative_semantic": ["cyberpunk"],
        "field_states": {"camera.angle": "confirmed"},
    },
    ensure_ascii=False,
)

SOURCE = "她站在東京夜晚的天橋上，全身低角度鏡頭，不要賽博龐克。"


def _request() -> VisualSceneParsingRequest:
    return VisualSceneParsingRequest(source_text=SOURCE, model="mock-model")


def main() -> int:
    checks: list[tuple[str, bool]] = []

    ok = VisualSceneParsingService(
        MockProvider(structured_responses=[_VALID]), provider_name="mock"
    ).parse(_request())
    checks.append(("valid draft → OK", ok.status is ParsingStatus.OK))
    checks.append(("subtext separated", ok.ast.primary_subject is not None
                   and ok.ast.primary_subject.emotional_subtext == "holding_back_tears"))
    checks.append(("negation → negative", "cyberpunk" in ok.ast.negative.semantic))
    checks.append(("field state carried", "camera.angle" in ok.ast.field_states))

    repaired = VisualSceneParsingService(
        MockProvider(structured_responses=["not json", _VALID]), provider_name="mock"
    ).parse(_request())
    checks.append(("one repair recovers", repaired.status is ParsingStatus.OK
                   and repaired.repair_attempts == 1))

    fallen = VisualSceneParsingService(
        MockProvider(structured_responses=["x", "y", "z"]), provider_name="mock"
    ).parse(_request())
    checks.append(("bounded repair → fallback",
                   fallen.status is ParsingStatus.FALLBACK
                   and fallen.error_reason
                   is ParsingErrorReason.INVALID_STRUCTURED_OUTPUT))
    checks.append(("fallback keeps source text",
                   fallen.ast.user_intent.source_text == SOURCE))

    failed = [name for name, passed in checks if not passed]
    for name, passed in checks:
        print(f"[{'OK' if passed else 'FAIL'}] {name}")
    if failed:
        print(f"mock parser smoke FAILED: {failed}", file=sys.stderr)
        return 1
    print("mock parser smoke passed (7 checks)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
