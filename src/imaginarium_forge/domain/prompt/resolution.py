"""Canon and Style resolution (spec §26/§27/§32).

Precedence (spec §26.2):

    Hard Locks > permitted scene override > selected version > outfit
              > parsed scene description > profile defaults

A scene override that collides with a Hard Lock never silently mutates Canon —
it becomes a PROHIBITED_CANON_MUTATION (or category-specific) ERROR conflict
whose suggested_actions are exactly the three decisions from §26.2.

Eligibility is NOT re-implemented here: the caller runs the Phase 1 validator
and passes its verdict in; adult content requested without an allowed verdict
becomes an ADULT_CONTENT_POLICY error.

Detection is deterministic keyword/lexicon matching — honest about being
heuristic (documented in KNOWN_ISSUES); no LLM participates.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict

from imaginarium_forge.domain.canon.traits import CanonicalTrait
from imaginarium_forge.domain.character.model import Character
from imaginarium_forge.domain.character.version import CharacterVersion
from imaginarium_forge.domain.common.enums import CanonStrength
from imaginarium_forge.domain.prompt.ast import PromptAST
from imaginarium_forge.domain.prompt.blocks import CoverageReport
from imaginarium_forge.domain.prompt.compilation import CharacterLockInput, StyleInput
from imaginarium_forge.domain.prompt.conflicts import (
    HARD_LOCK_DECISIONS,
    Conflict,
    ConflictCode,
    ConflictSeverity,
)
from imaginarium_forge.domain.prompt.profiles import ProfileSyntax, ResolvedProfile
from imaginarium_forge.domain.style.model import StyleProfileVersion

#: strength → coverage weight (spec §32.1 example scaled to Canon strengths)
_STRENGTH_WEIGHT: dict[CanonStrength, int] = {
    CanonStrength.HARD_LOCK: 5,
    CanonStrength.SOFT_CANON: 3,
    CanonStrength.PREFERENCE: 1,
}

_COLOR_WORDS: frozenset[str] = frozenset(
    {
        "silver", "white", "black", "blonde", "blond", "brown", "red", "pink",
        "blue", "green", "amber", "gold", "golden", "purple", "violet", "grey",
        "gray", "teal", "orange",
    }
)

_NIGHT_WORDS = frozenset({"night", "midnight", "夜", "夜晚"})
_DAY_WORDS = frozenset({"day", "daytime", "noon", "morning", "白天", "中午", "早晨"})
_RAIN_WORDS = frozenset({"rain", "rainy", "raining", "雨", "下雨"})
_CLEAR_WORDS = frozenset({"sunny", "clear sky", "晴", "晴天"})
_FULLBODY_WORDS = frozenset({"full body", "fullbody", "全身"})
_CROP_WORDS = frozenset({"portrait", "close-up", "closeup", "bust", "半身", "特寫"})
_LOW_ANGLE = frozenset({"low angle", "低角度"})
_HIGH_ANGLE = frozenset({"bird's eye", "birds eye", "high angle", "俯視", "俯瞰"})
_CHILDLIKE_WORDS = frozenset({"child", "childlike", "loli", "小孩", "孩童", "幼"})

#: mutually exclusive style-term groups (each pair across groups conflicts)
_STYLE_EXCLUSIVE: tuple[tuple[frozenset[str], frozenset[str]], ...] = (
    (frozenset({"photorealistic", "photoreal", "realistic photo"}),
     frozenset({"anime", "cel shading", "cel-shaded"})),
    (frozenset({"monochrome", "black and white"}),
     frozenset({"vibrant colors", "colorful"})),
    (frozenset({"watercolor"}), frozenset({"oil painting"})),
)

_WEIGHT_SYNTAX = re.compile(r"\([^()]+:\d+(?:\.\d+)?\)")


def _norm(text: str) -> str:
    return " ".join(text.strip().casefold().split())


def _contains_any(haystack: str, words: frozenset[str]) -> bool:
    lowered = _norm(haystack)
    return any(word in lowered for word in words)


def _color_in(text: str) -> str | None:
    lowered = _norm(text)
    for word in _COLOR_WORDS:
        if re.search(rf"\b{word}\b", lowered):
            return word
    return None


class CharacterResolution(BaseModel):
    model_config = ConfigDict(frozen=True)

    lock: CharacterLockInput
    conflicts: tuple[Conflict, ...] = ()
    permitted_overrides: tuple[str, ...] = ()


class StyleResolution(BaseModel):
    model_config = ConfigDict(frozen=True)

    style: StyleInput
    conflicts: tuple[Conflict, ...] = ()


def _hard_lock_decision_conflict(
    code: ConflictCode, lock_text: str, override: str, category_zh: str
) -> Conflict:
    return Conflict(
        code=code,
        severity=ConflictSeverity.ERROR,
        source_a=f"canon:{lock_text}",
        source_b=f"scene_override:{override}",
        message_zh_tw=(
            f"場景覆寫「{override}」與 Hard Lock「{lock_text}」在{category_zh}上衝突；"
            "Canon 不會被靜默改寫，請明確選擇處理方式。"
        ),
        suggested_actions=HARD_LOCK_DECISIONS,
        auto_fix_safe=False,
    )


def resolve_character(
    *,
    character: Character,
    version: CharacterVersion,
    ast: PromptAST,
    outfit_canonical: tuple[str, ...] = (),
    outfit_optional: tuple[str, ...] = (),
    outfit_prohibited: tuple[str, ...] = (),
    adult_requested: bool = False,
    adult_allowed: bool = False,
) -> CharacterResolution:
    """Resolve Canon into compiler input, honoring precedence and Hard Locks."""
    dna = version.visual_dna
    subject = ast.primary_subject
    overrides = tuple(subject.scene_overrides) if subject else ()
    conflicts: list[Conflict] = []
    suppressed_traits: set[str] = set()
    permitted: list[str] = []

    hard_locks = tuple(
        t for t in dna.canonical_traits if t.strength is CanonStrength.HARD_LOCK
    )
    softer = tuple(
        t for t in dna.canonical_traits if t.strength is not CanonStrength.HARD_LOCK
    )

    # ---- scene overrides vs Canon (precedence §26.2) ----
    for override in overrides:
        override_norm = _norm(override)
        # prohibited mutations are absolute
        hit_prohibited = next(
            (p for p in dna.prohibited_mutations if _norm(p) and _norm(p) in override_norm),
            None,
        )
        if hit_prohibited:
            conflicts.append(
                Conflict(
                    code=ConflictCode.PROHIBITED_CANON_MUTATION,
                    severity=ConflictSeverity.ERROR,
                    source_a=f"canon:prohibited:{hit_prohibited}",
                    source_b=f"scene_override:{override}",
                    message_zh_tw=(
                        f"場景覆寫「{override}」觸犯此角色的禁止變異「{hit_prohibited}」。"
                    ),
                    suggested_actions=HARD_LOCK_DECISIONS,
                    auto_fix_safe=False,
                )
            )
            continue
        collided = _collide_with_hard_lock(override, override_norm, hard_locks, conflicts)
        if not collided:
            # permitted scene override: outranks softer canon of the same category
            for trait in softer:
                if trait.category and _norm(trait.category) in override_norm:
                    suppressed_traits.add(trait.trait_id)
            permitted.append(override)

    # ---- adult policy gate (never re-evaluated here; verdict passed in) ----
    presentation = version.adult_presentation
    if adult_requested and not adult_allowed:
        conflicts.append(
            Conflict(
                code=ConflictCode.ADULT_CONTENT_POLICY,
                severity=ConflictSeverity.ERROR,
                source_a="request:adult_content",
                source_b="eligibility:blocked",
                message_zh_tw="此請求包含成人內容，但資格驗證未通過；不得編譯成人向提示。",
                suggested_actions=("移除成人內容需求", "先於角色頁補齊資格條件"),
                auto_fix_safe=False,
            )
        )
    if subject and _contains_any(subject.presentation, _CHILDLIKE_WORDS):
        conflicts.append(
            Conflict(
                code=ConflictCode.AGE_PRESENTATION,
                severity=ConflictSeverity.ERROR,
                source_a="canon:adult_presentation",
                source_b=f"scene:presentation:{subject.presentation}",
                message_zh_tw="場景描述要求孩童化呈現，與成人呈現 Canon 衝突；已封鎖。",
                suggested_actions=("移除孩童化描述",),
                auto_fix_safe=False,
            )
        )

    # ---- emit tags (Canon order; hard locks first) ----
    lock_tags = tuple(t.canonical_descriptor for t in hard_locks)
    soft_tags = tuple(
        t.canonical_descriptor for t in softer if t.trait_id not in suppressed_traits
    )
    gender_tag = dna.gender.prompt_token if dna.gender is not None else ""
    identity_tags = (
        tuple(x for x in (gender_tag, dna.identity) if x)
        + soft_tags
        + tuple(permitted)
    )
    face_hair_eyes = tuple(x for x in (dna.face, dna.hair, dna.eyes) if x)
    outfit_tags = outfit_canonical + outfit_optional

    # outfit prohibited requested anywhere in scene → OUTFIT conflict
    scene_text = _norm(
        " ".join((*(overrides), *(ast.user_intent.must_include), ast.user_intent.source_text))
    )
    for banned in outfit_prohibited:
        if _norm(banned) and _norm(banned) in scene_text:
            conflicts.append(
                Conflict(
                    code=ConflictCode.OUTFIT,
                    severity=ConflictSeverity.ERROR,
                    source_a=f"outfit:prohibited:{banned}",
                    source_b="scene:request",
                    message_zh_tw=f"場景要求「{banned}」，但所選服裝將其列為禁止特徵。",
                    suggested_actions=("換一套服裝", "移除該場景要求"),
                    auto_fix_safe=False,
                )
            )

    coverage = _identity_coverage(dna.canonical_traits, suppressed_traits)
    # Gate A A-12: hand the compiler the weighted required-trait list so it can
    # recompute coverage against the FINAL positive output
    required_traits = tuple(
        (f"{t.category}:{t.canonical_descriptor}", t.canonical_descriptor,
         _STRENGTH_WEIGHT[t.strength])
        for t in dna.canonical_traits
        if t.strength in (CanonStrength.HARD_LOCK, CanonStrength.SOFT_CANON)
        and t.trait_id not in suppressed_traits
    )

    lock = CharacterLockInput(
        lock_tags=lock_tags,
        identity_tags=identity_tags,
        face_hair_eyes_tags=face_hair_eyes,
        distinguishing_tags=tuple(dna.distinguishing_features),
        body_tags=tuple(x for x in (dna.body,) if x),
        outfit_tags=outfit_tags,
        adult_presentation=presentation.is_valid_adult_presentation,
        prohibited_mutations=tuple(dna.prohibited_mutations),
        coverage=coverage,
        required_traits=required_traits,
    )
    _ = character  # identity layer available for future use; version carries Canon
    return CharacterResolution(
        lock=lock, conflicts=tuple(conflicts), permitted_overrides=tuple(permitted)
    )


def _collide_with_hard_lock(
    override: str,
    override_norm: str,
    hard_locks: tuple[CanonicalTrait, ...],
    conflicts: list[Conflict],
) -> bool:
    """Category-aware Hard-Lock collision. Returns True when a conflict was added."""
    for trait in hard_locks:
        category = _norm(trait.category)
        descriptor = _norm(trait.canonical_descriptor)
        if not category or category not in override_norm:
            continue
        # left/right orientation on the same category (e.g. hair streak side)
        sides = {"left": "right", "right": "left", "左": "右", "右": "左"}
        for side, opposite in sides.items():
            if side in descriptor and opposite in override_norm:
                conflicts.append(
                    _hard_lock_decision_conflict(
                        ConflictCode.LEFT_RIGHT_ORIENTATION,
                        trait.canonical_descriptor,
                        override,
                        "左右方位",
                    )
                )
                return True
        # color collision on hair/eye categories
        lock_color = _color_in(descriptor)
        override_color = _color_in(override_norm)
        if lock_color and override_color and lock_color != override_color:
            code = (
                ConflictCode.EYE_COLOR
                if "eye" in category or "眼" in category
                else ConflictCode.HAIR_STREAK
                if "streak" in descriptor or "挑染" in trait.canonical_descriptor
                else ConflictCode.HAIR_COLOR
            )
            conflicts.append(
                _hard_lock_decision_conflict(
                    code, trait.canonical_descriptor, override, "顏色"
                )
            )
            return True
        # generic same-category contradiction against a distinguishing hard lock
        if descriptor and descriptor not in override_norm and any(
            neg in override_norm for neg in ("no ", "without ", "remove", "沒有", "去掉")
        ):
            conflicts.append(
                _hard_lock_decision_conflict(
                    ConflictCode.DISTINGUISHING_FEATURE,
                    trait.canonical_descriptor,
                    override,
                    "辨識特徵",
                )
            )
            return True
    return False


def _identity_coverage(
    traits: tuple[CanonicalTrait, ...], suppressed: set[str]
) -> CoverageReport:
    """covered weight / required weight over hard+soft canon (spec §32.1)."""
    required = [
        t for t in traits if t.strength in (CanonStrength.HARD_LOCK, CanonStrength.SOFT_CANON)
    ]
    required_weight = sum(_STRENGTH_WEIGHT[t.strength] for t in required)
    missing = tuple(
        f"{t.category}:{t.canonical_descriptor}" for t in required if t.trait_id in suppressed
    )
    covered_weight = sum(
        _STRENGTH_WEIGHT[t.strength] for t in required if t.trait_id not in suppressed
    )
    return CoverageReport(
        covered_weight=covered_weight, required_weight=required_weight, missing=missing
    )


# ------------------------------------------------------------------ style


def resolve_style(
    *,
    version: StyleProfileVersion,
    ast: PromptAST,
) -> StyleResolution:
    """Resolve Style DNA + scene mood into a model-independent Style Block."""
    dna = version.style_dna
    conflicts: list[Conflict] = []
    mood = tuple(ast.style.scene_mood_overrides)

    # scene mood must not smuggle a prohibited trait in
    kept_mood: list[str] = []
    for item in mood:
        banned = next(
            (p for p in dna.prohibited_traits if _norm(p) and _norm(p) in _norm(item)),
            None,
        )
        if banned:
            conflicts.append(
                Conflict(
                    code=ConflictCode.INCOMPATIBLE_STYLE_TERMS,
                    severity=ConflictSeverity.ERROR,
                    source_a=f"style:prohibited:{banned}",
                    source_b=f"scene_mood:{item}",
                    message_zh_tw=f"場景氛圍「{item}」包含此風格的禁止特徵「{banned}」。",
                    suggested_actions=("移除該氛圍描述", "改用其他風格設定"),
                    auto_fix_safe=False,
                )
            )
        else:
            kept_mood.append(item)

    field_tags = tuple(
        value
        for value in (
            dna.medium, dna.linework, dna.color, dna.shading, dna.lighting,
            dna.face_rendering, dna.background, dna.composition, dna.post_processing,
        )
        if value
    )
    trait_tags = tuple(t.canonical_descriptor for t in dna.canonical_traits)
    style_tags = field_tags + trait_tags + tuple(kept_mood)

    # mutually exclusive style terms among the EMITTED tags
    joined = _norm(" | ".join(style_tags))
    for group_a, group_b in _STYLE_EXCLUSIVE:
        hit_a = next((w for w in group_a if w in joined), None)
        hit_b = next((w for w in group_b if w in joined), None)
        if hit_a and hit_b:
            conflicts.append(
                Conflict(
                    code=ConflictCode.INCOMPATIBLE_STYLE_TERMS,
                    severity=ConflictSeverity.WARNING,
                    source_a=f"style:{hit_a}",
                    source_b=f"style:{hit_b}",
                    message_zh_tw=f"「{hit_a}」與「{hit_b}」互斥，同時出現通常互相抵銷。",
                    suggested_actions=("擇一保留",),
                    auto_fix_safe=False,
                )
            )

    coverage = _style_coverage(dna)
    required_fields = tuple(
        (name, value)
        for name, value in (
            ("medium", dna.medium), ("linework", dna.linework),
            ("shading", dna.shading), ("palette", dna.color),
            ("lighting", dna.lighting), ("composition", dna.composition),
            ("prohibited", " ".join(dna.prohibited_traits)),
        )
        if value.strip()
    )
    return StyleResolution(
        style=StyleInput(
            style_tags=style_tags,
            prohibited_traits=tuple(dna.prohibited_traits),
            coverage=coverage,
            required_fields=required_fields,
        ),
        conflicts=tuple(conflicts),
    )


def _style_coverage(dna) -> CoverageReport:  # type: ignore[no-untyped-def]
    """spec §32.2: medium/linework/shading/palette/lighting/composition/prohibited."""
    measured = {
        "medium": dna.medium,
        "linework": dna.linework,
        "shading": dna.shading,
        "palette": dna.color,
        "lighting": dna.lighting,
        "composition": dna.composition,
        "prohibited": " ".join(dna.prohibited_traits),
    }
    missing = tuple(name for name, value in measured.items() if not value.strip())
    return CoverageReport(
        covered_weight=len(measured) - len(missing),
        required_weight=len(measured),
        missing=missing,
    )


# --------------------------------------------------------- cross-domain


def detect_cross_conflicts(
    ast: PromptAST,
    profile: ResolvedProfile,
    character: CharacterLockInput,
    style: StyleInput,
) -> tuple[Conflict, ...]:
    """Deterministic cross-domain checks (spec §27 remaining categories)."""
    conflicts: list[Conflict] = []
    env = ast.environment
    cam_text = _norm(
        " ".join(
            (ast.camera.shot, ast.camera.angle, ast.camera.framing, ast.camera.lens_intent)
        )
    )

    # day/night
    day_night_text = _norm(" ".join((env.time_of_day, ast.lighting.key, env.atmosphere)))
    if _contains_any(day_night_text, _NIGHT_WORDS) and _contains_any(
        day_night_text, _DAY_WORDS
    ):
        conflicts.append(
            Conflict(
                code=ConflictCode.DAY_NIGHT,
                severity=ConflictSeverity.WARNING,
                source_a=f"environment:{env.time_of_day}",
                source_b=f"lighting:{ast.lighting.key}",
                message_zh_tw="日/夜描述同時出現，光照結果通常不穩定。",
                suggested_actions=("擇一時間設定",),
                auto_fix_safe=False,
            )
        )

    # weather
    weather_text = _norm(" ".join((env.weather, env.atmosphere)))
    if _contains_any(weather_text, _RAIN_WORDS) and _contains_any(
        weather_text, _CLEAR_WORDS
    ):
        conflicts.append(
            Conflict(
                code=ConflictCode.WEATHER,
                severity=ConflictSeverity.WARNING,
                source_a=f"weather:{env.weather}",
                source_b=f"atmosphere:{env.atmosphere}",
                message_zh_tw="雨天與晴朗描述同時出現。",
                suggested_actions=("擇一天氣",),
                auto_fix_safe=False,
            )
        )

    # full-body vs crop
    if _contains_any(cam_text, _FULLBODY_WORDS) and _contains_any(cam_text, _CROP_WORDS):
        conflicts.append(
            Conflict(
                code=ConflictCode.FULL_BODY_VS_CROP,
                severity=ConflictSeverity.WARNING,
                source_a=f"camera:{ast.camera.shot}",
                source_b=f"camera:{ast.camera.framing}",
                message_zh_tw="全身構圖與近距裁切同時被要求。",
                suggested_actions=("擇一構圖",),
                auto_fix_safe=False,
            )
        )

    # camera angle contradiction
    if _contains_any(cam_text, _LOW_ANGLE) and _contains_any(cam_text, _HIGH_ANGLE):
        conflicts.append(
            Conflict(
                code=ConflictCode.CAMERA_FRAMING_CONTRADICTION,
                severity=ConflictSeverity.WARNING,
                source_a="camera:low angle",
                source_b="camera:high angle",
                message_zh_tw="低角度與俯視同時被要求，鏡頭語言矛盾。",
                suggested_actions=("擇一視角",),
                auto_fix_safe=False,
            )
        )

    # positive/negative overlap (character+style emitted vs negatives)
    negatives = {
        _norm(t)
        for t in (
            *profile.negative_tags,
            *ast.negative.semantic,
            *ast.negative.style,
            *ast.user_intent.must_avoid,
        )
    }
    positives = (
        *character.lock_tags, *character.identity_tags, *character.face_hair_eyes_tags,
        *character.outfit_tags, *style.style_tags, *ast.user_intent.must_include,
    )
    for tag in positives:
        if _norm(tag) in negatives:
            conflicts.append(
                Conflict(
                    code=ConflictCode.POSITIVE_NEGATIVE_OVERLAP,
                    severity=ConflictSeverity.WARNING,
                    source_a=f"positive:{tag}",
                    source_b="negative:same tag",
                    message_zh_tw=f"「{tag}」同時出現在正向與負向，將互相抵銷。",
                    suggested_actions=("自負向移除", "自正向移除"),
                    auto_fix_safe=True,
                )
            )

    # unsupported weighting syntax under natural-language profiles
    if profile.syntax is ProfileSyntax.NATURAL_LANGUAGE:
        weighted = [
            t for t in (*ast.user_intent.must_include,) if _WEIGHT_SYNTAX.search(t)
        ]
        for tag in weighted:
            conflicts.append(
                Conflict(
                    code=ConflictCode.UNSUPPORTED_PROFILE_SYNTAX,
                    severity=ConflictSeverity.WARNING,
                    source_a=f"must_include:{tag}",
                    source_b=f"profile:{profile.dialect_id}(natural_language)",
                    message_zh_tw="自然語言 profile 不支援 (tag:weight) 權重語法。",
                    suggested_actions=("移除權重語法",),
                    auto_fix_safe=True,
                )
            )

    return tuple(conflicts)
