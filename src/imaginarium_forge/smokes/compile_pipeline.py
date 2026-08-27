"""Mock compiler smoke (§11.2): `python -m imaginarium_forge.smokes.compile_pipeline`.

Exercises the deterministic half of the Studio with ZERO providers:
shipped dialect YAML → Canon resolution (Hard Lock leading) → style merge →
cross-domain conflicts → compile — twice — and asserts byte-identical output
plus the §12 covered surfaces. Exit 0 on success, 1 on failure.
"""

from __future__ import annotations

import sys
from pathlib import Path

from imaginarium_forge.domain.canon.traits import CanonicalTrait
from imaginarium_forge.domain.character.model import AgeStatus, Character
from imaginarium_forge.domain.character.version import (
    AdultPresentation,
    CharacterVersion,
    VisualDNA,
)
from imaginarium_forge.domain.common.enums import (
    AgeClassification,
    CanonStrength,
    CharacterOrigin,
)
from imaginarium_forge.domain.prompt.ast import (
    CameraNode,
    EnvironmentNode,
    NegativeNode,
    PromptAST,
    StyleNode,
    SubjectNode,
    UserIntent,
)
from imaginarium_forge.domain.prompt.blocks import CompiledBlocks
from imaginarium_forge.domain.prompt.compilation import compile_prompt
from imaginarium_forge.domain.prompt.profiles import ProfileKind, resolve_profiles
from imaginarium_forge.domain.prompt.resolution import (
    detect_cross_conflicts,
    resolve_character,
    resolve_style,
)
from imaginarium_forge.domain.style.model import StyleDNA, StyleProfileVersion
from imaginarium_forge.infrastructure.profiles.loader import ProfileLoader


def _profiles_root() -> Path:
    # repo layout: src/imaginarium_forge/smokes/ → repo root is parents[3]
    root = Path(__file__).resolve().parents[3] / "config" / "prompt_profiles"
    if root.exists():
        return root
    return Path("config/prompt_profiles")


def _fixture() -> tuple[Character, CharacterVersion, StyleProfileVersion, PromptAST]:
    character = Character(
        id="c1", project_id="p1", name="凜",
        character_origin=CharacterOrigin.ORIGINAL,
        age_status=AgeStatus(
            classification=AgeClassification.VERIFIED_ADULT,
            explicit_age=25, user_confirmed=True,
        ),
        created_at="t", updated_at="t",
    )
    version = CharacterVersion(
        id="v1", character_id="c1", version_number=1,
        visual_dna=VisualDNA(
            identity="tall composed adult woman",
            hair="silver bob cut", eyes="amber eyes",
            canonical_traits=(
                CanonicalTrait(
                    category="hair streak", name="side",
                    canonical_descriptor="left blue streak",
                    strength=CanonStrength.HARD_LOCK,
                ),
            ),
        ),
        adult_presentation=AdultPresentation(
            adult_face_presentation=True, adult_body_presentation=True
        ),
        created_at="t",
    )
    style = StyleProfileVersion(
        id="sv1", style_profile_id="s1", version_number=1,
        style_dna=StyleDNA(
            medium="anime film", linework="clean lineart",
            lighting="cinematic lighting",
        ),
        created_at="t",
    )
    ast = PromptAST(
        subjects=(
            SubjectNode(
                character_id="c1", presentation="adult woman",
                pose="standing on a bridge", expression="calm",
            ),
        ),
        camera=CameraNode(shot="full body", angle="low angle"),
        environment=EnvironmentNode(
            location="tokyo pedestrian bridge", time_of_day="night", weather="rain"
        ),
        style=StyleNode(scene_mood_overrides=("melancholic",)),
        negative=NegativeNode(semantic=("cyberpunk",)),
        user_intent=UserIntent(source_text="她站在東京夜晚的天橋上", must_avoid=("neon",)),
    )
    return character, version, style, ast


def _run_once() -> CompiledBlocks:
    character, version, style_version, ast = _fixture()
    dialect = ProfileLoader(_profiles_root()).get(ProfileKind.DIALECT, "generic_tag_anime")
    if dialect is None:
        raise RuntimeError("shipped dialect generic_tag_anime not found")
    profile = resolve_profiles(dialect)
    char_res = resolve_character(
        character=character, version=version, ast=ast,
        adult_requested=True, adult_allowed=True,
    )
    style_res = resolve_style(version=style_version, ast=ast)
    conflicts = (
        char_res.conflicts
        + style_res.conflicts
        + detect_cross_conflicts(ast, profile, char_res.lock, style_res.style)
    )
    return compile_prompt(
        ast, profile, character=char_res.lock, style=style_res.style,
        conflicts=conflicts,
    )


def main() -> int:
    one, two = _run_once(), _run_once()
    checks = [
        ("byte-identical across runs", one == two),
        ("positive present", bool(one.positive_prompt)),
        ("negative present", "cyberpunk" in one.negative_prompt),
        ("hard lock leads identity", "left blue streak" in one.character_lock_block),
        ("style block present", "anime film" in one.style_block),
        ("NL prompt present", one.natural_language_prompt.startswith("An adult woman")),
        ("zh-TW explanation present", "結構說明" in one.explanation_zh_tw),
        ("snapshot hash stable",
         resolve_profiles(
             ProfileLoader(_profiles_root()).get(ProfileKind.DIALECT, "generic_tag_anime")  # type: ignore[arg-type]
         ).sha256()
         == resolve_profiles(
             ProfileLoader(_profiles_root()).get(ProfileKind.DIALECT, "generic_tag_anime")  # type: ignore[arg-type]
         ).sha256()),
    ]
    failed = [name for name, passed in checks if not passed]
    for name, passed in checks:
        print(f"[{'OK' if passed else 'FAIL'}] {name}")
    if failed:
        print(f"mock compiler smoke FAILED: {failed}", file=sys.stderr)
        return 1
    print("mock compiler smoke passed (8 checks)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
