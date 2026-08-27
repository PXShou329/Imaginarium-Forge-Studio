"""Deterministic prompt compiler (spec §30).

Pure function of (PromptAST, ResolvedProfile, resolution inputs): no I/O, no
randomness, no LLM. The LLM's job ended at parsing; everything here is Canon
merge, ordering, deduplication, and formatting.

Determinism contract (spec §38.5):

    same AST + same resolved profile + same compiler version
        → byte-identical CompiledBlocks
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from imaginarium_forge.domain.prompt.ast import PromptAST
from imaginarium_forge.domain.prompt.blocks import CompiledBlocks, CoverageReport
from imaginarium_forge.domain.prompt.conflicts import Conflict
from imaginarium_forge.domain.prompt.profiles import ProfileSyntax, ResolvedProfile


class CharacterLockInput(BaseModel):
    """What the Character Resolver hands the compiler (B3 fills this fully)."""

    model_config = ConfigDict(frozen=True)

    lock_tags: tuple[str, ...] = ()          # identity-critical tags, Canon order
    identity_tags: tuple[str, ...] = ()      # broader identity description
    face_hair_eyes_tags: tuple[str, ...] = ()
    distinguishing_tags: tuple[str, ...] = ()
    body_tags: tuple[str, ...] = ()
    outfit_tags: tuple[str, ...] = ()
    adult_presentation: bool = False
    prohibited_mutations: tuple[str, ...] = ()
    coverage: CoverageReport = CoverageReport()
    #: Gate A A-12 — (label, descriptor, weight) required for compiled coverage
    required_traits: tuple[tuple[str, str, int], ...] = ()


class StyleInput(BaseModel):
    """What the Style Resolver hands the compiler."""

    model_config = ConfigDict(frozen=True)

    style_tags: tuple[str, ...] = ()
    prohibited_traits: tuple[str, ...] = ()
    coverage: CoverageReport = CoverageReport()
    #: Gate A A-12 — (field name, DNA value) required for compiled coverage
    required_fields: tuple[tuple[str, str], ...] = ()


def _norm(tag: str) -> str:
    return " ".join(tag.strip().casefold().split())


def _dedupe_keep_first(tags: tuple[str, ...]) -> tuple[str, ...]:
    """Normalized-identity dedupe, stable first occurrence (spec §30.4)."""
    seen: set[str] = set()
    out: list[str] = []
    for tag in tags:
        cleaned = " ".join(tag.strip().split())
        key = _norm(cleaned)
        if key and key not in seen:
            seen.add(key)
            out.append(cleaned)
    return tuple(out)


def _join(tags: tuple[str, ...]) -> str:
    return ", ".join(tags)


def _subject_block(ast: PromptAST) -> tuple[str, ...]:
    count = len(ast.subjects)
    if count == 0:
        return ()
    if count == 1:
        return ("1girl",) if _looks_female(ast) else ("solo",)
    return (f"{count} subjects",)


def _looks_female(ast: PromptAST) -> bool:
    subject = ast.primary_subject
    if subject is None:
        return False
    haystack = " ".join(
        (subject.presentation, *subject.canonical_features, ast.user_intent.source_text)
    ).casefold()
    return any(marker in haystack for marker in ("girl", "woman", "female", "她", "少女"))



def _strip_weight_syntax(tag: str) -> str:
    """'(left blue streak:1.3)' → 'left blue streak' — for coverage matching only."""
    tag = tag.strip()
    if tag.startswith("(") and tag.endswith(")"):
        tag = tag[1:-1]
        if ":" in tag:
            head, _, weight = tag.rpartition(":")
            if weight.replace(".", "", 1).isdigit():
                tag = head
    return _norm(tag)


def _compiled_identity_coverage(
    character: CharacterLockInput, positive_tags: tuple[str, ...]
) -> CoverageReport:
    """Gate A A-12: verify required traits against the FINAL positive tags.

    Exact normalized-tag equality (after weight-syntax stripping) — never
    naive substring matching, so 'red' cannot match 'bored'. A trait the
    profile dropped or truncated therefore shows as missing here even when
    resolution-time coverage counted it.
    """
    final = {_strip_weight_syntax(tag) for tag in positive_tags}
    required_weight = sum(w for _, _, w in character.required_traits)
    missing: list[str] = []
    covered = 0
    for label, descriptor, weight in character.required_traits:
        if _norm(descriptor) in final:
            covered += weight
        else:
            missing.append(label)
    return CoverageReport(
        covered_weight=covered,
        required_weight=required_weight,
        missing=tuple(missing),
    )


def _compiled_style_coverage(
    style: StyleInput, positive_tags: tuple[str, ...]
) -> CoverageReport:
    final = {_strip_weight_syntax(tag) for tag in positive_tags}
    missing: list[str] = []
    for name, value in style.required_fields:
        if name == "prohibited":
            # prohibited traits are honored by ABSENCE from the final output
            leaked = [v for v in value.split() if _norm(v) in final]
            if leaked:
                missing.append(name)
            continue
        if _norm(value) not in final:
            missing.append(name)
    total = len(style.required_fields)
    return CoverageReport(
        covered_weight=total - len(missing),
        required_weight=total,
        missing=tuple(missing),
    )


def compile_prompt(
    ast: PromptAST,
    profile: ResolvedProfile,
    *,
    character: CharacterLockInput | None = None,
    style: StyleInput | None = None,
    conflicts: tuple[Conflict, ...] = (),
) -> CompiledBlocks:
    """Assemble the 14 output blocks deterministically."""
    character = character or CharacterLockInput()
    style = style or StyleInput()
    subject = ast.primary_subject

    # ---- per-block tag tuples (model-independent semantics → tags verbatim) ----
    blocks: dict[str, tuple[str, ...]] = {
        "quality_prefix": profile.quality_prefix,
        "subject_count": _subject_block(ast),
        "adult_presentation": ("adult",) if character.adult_presentation else (),
        "identity": _dedupe_keep_first(character.lock_tags + character.identity_tags),
        "face_hair_eyes": character.face_hair_eyes_tags,
        "distinguishing_features": character.distinguishing_tags,
        "body": character.body_tags,
        "outfit": character.outfit_tags,
        "pose_expression": tuple(
            t
            for t in (
                subject.pose if subject else "",
                subject.expression if subject else "",
                subject.emotional_subtext if subject else "",
                subject.gaze if subject else "",
                *(subject.motion_cues if subject else ()),
            )
            if t
        ),
        "camera_composition": tuple(
            t
            for t in (
                ast.camera.shot,
                ast.camera.angle,
                ast.camera.lens_intent,
                ast.camera.framing,
                ast.camera.subject_placement,
                ast.camera.depth_of_field,
            )
            if t
        ),
        "environment": tuple(
            t
            for t in (
                ast.environment.location,
                ast.environment.time_of_day,
                ast.environment.weather,
                *ast.environment.background_elements,
                ast.environment.atmosphere,
            )
            if t
        ),
        "lighting": tuple(
            t
            for t in (
                ast.lighting.key,
                ast.lighting.fill,
                ast.lighting.rim,
                ast.lighting.contrast,
                ast.lighting.color_temperature,
            )
            if t
        ),
        "style": _dedupe_keep_first(
            style.style_tags + ast.style.scene_mood_overrides
        ),
        "quality_suffix": profile.quality_suffix,
    }

    # required tags from the profile join the identity block tail (stable)
    if profile.required_tags:
        blocks["identity"] = _dedupe_keep_first(
            blocks["identity"] + profile.required_tags
        )

    # ---- positive prompt: profile block order, cross-block stable dedupe ----
    ordered: list[str] = []
    for name in profile.block_order:
        ordered.extend(blocks.get(name, ()))
    # must_include from user intent is honored at the tail (before suffix dedupe)
    ordered.extend(ast.user_intent.must_include)
    positive_tags = _dedupe_keep_first(tuple(ordered))
    positive = _join(positive_tags)

    # ---- negative prompt: profile + AST negative categories + must_avoid ----
    negative_tags = _dedupe_keep_first(
        profile.negative_tags
        + ast.negative.semantic
        + ast.negative.anatomy
        + ast.negative.identity
        + ast.negative.style
        + ast.negative.composition
        + ast.user_intent.must_avoid
    )
    negative = _join(negative_tags)

    # ---- English natural-language prompt (always produced; primary for NL syntax)
    natural = _natural_language(ast, character, style, positive_tags)

    # ---- zh-TW structural explanation ----
    explanation = _explanation_zh_tw(ast, profile, blocks, character, style)

    # ---- generation notes: profile notes + sampler recs + warnings ----
    notes_lines: list[str] = []
    if profile.experimental:
        notes_lines.append("⚠ 此 profile 標記為 experimental（尚未完整基準測試）。")
    if profile.low_confidence_warning:
        notes_lines.append(f"⚠ {profile.low_confidence_warning}")
    for key, value in sorted(profile.sampler_recommendations.items()):
        notes_lines.append(f"建議 {key}：{value}")
    for note in profile.notes:
        notes_lines.append(f"[{note.provenance.value}｜{note.source_layer}] {note.text}")
    generation_notes = "\n".join(notes_lines)

    # ---- conflict/warning report (compiler renders; detection is B3's job) ----
    conflict_lines = [
        f"[{c.severity.value}｜{c.code.value}] {c.message_zh_tw}"
        f"（{c.source_a} ↔ {c.source_b}）"
        for c in conflicts
    ]
    conflict_report = "\n".join(conflict_lines) if conflict_lines else "（無衝突）"

    return CompiledBlocks(
        explanation_zh_tw=explanation,
        character_lock_block=_join(
            _dedupe_keep_first(character.lock_tags)
        ),
        outfit_block=_join(character.outfit_tags),
        pose_expression_block=_join(blocks["pose_expression"]),
        camera_block=_join(blocks["camera_composition"]),
        environment_block=_join(blocks["environment"]),
        lighting_block=_join(blocks["lighting"]),
        style_block=_join(blocks["style"]),
        quality_block=_join(
            _dedupe_keep_first(profile.quality_prefix + profile.quality_suffix)
        ),
        positive_prompt=positive,
        negative_prompt=negative,
        natural_language_prompt=natural,
        generation_notes=generation_notes,
        conflict_warning_report=conflict_report,
        identity_coverage=character.coverage,
        style_coverage=style.coverage,
        # Gate A A-12: coverage recomputed against the FINAL positive tags
        identity_coverage_compiled=_compiled_identity_coverage(
            character, positive_tags
        ),
        style_coverage_compiled=_compiled_style_coverage(style, positive_tags),
    )


def _natural_language(
    ast: PromptAST,
    character: CharacterLockInput,
    style: StyleInput,
    positive_tags: tuple[str, ...],
) -> str:
    """Deterministic English prose. Template-based — no generation randomness."""
    subject = ast.primary_subject
    sentences: list[str] = []

    who = "An adult woman" if character.adult_presentation and _looks_female(ast) else (
        "An adult character" if character.adult_presentation else "A character"
    )
    identity_bits = ", ".join(
        _dedupe_keep_first(character.lock_tags + character.identity_tags)[:6]
    )
    sentences.append(f"{who}{f' with {identity_bits}' if identity_bits else ''}.")

    if subject and (subject.pose or subject.expression):
        pose_bits = ", ".join(t for t in (subject.pose, subject.expression, subject.gaze) if t)
        sentences.append(f"Pose and expression: {pose_bits}.")
    env_bits = ", ".join(
        t
        for t in (
            ast.environment.location,
            ast.environment.time_of_day,
            ast.environment.weather,
        )
        if t
    )
    if env_bits:
        sentences.append(f"Setting: {env_bits}.")
    cam_bits = ", ".join(t for t in (ast.camera.shot, ast.camera.angle) if t)
    if cam_bits:
        sentences.append(f"Camera: {cam_bits}.")
    light_bits = ", ".join(
        t for t in (ast.lighting.key, ast.lighting.color_temperature) if t
    )
    if light_bits:
        sentences.append(f"Lighting: {light_bits}.")
    if style.style_tags:
        sentences.append(f"Style: {', '.join(style.style_tags[:5])}.")
    if not sentences:
        sentences.append(f"Scene tags: {', '.join(positive_tags[:10])}.")
    return " ".join(sentences)


def _explanation_zh_tw(
    ast: PromptAST,
    profile: ResolvedProfile,
    blocks: dict[str, tuple[str, ...]],
    character: CharacterLockInput,
    style: StyleInput,
) -> str:
    lines = [
        f"【結構說明】方言：{profile.dialect_id}@{profile.dialect_version}"
        + (
            f"；checkpoint profile：{profile.checkpoint_profile_id}"
            if profile.checkpoint_profile_id
            else "；checkpoint profile：（無，使用通用方言）"
        )
        + (f"；preset：{profile.preset_id}" if profile.preset_id else ""),
        f"語法：{'tag 序列' if profile.syntax is ProfileSyntax.TAG_BASED else '自然語言為主'}；"
        f"編譯器：{profile.compiler_version}（同輸入必同輸出）",
    ]
    if character.lock_tags:
        lines.append(
            f"角色鎖定：{len(character.lock_tags)} 個身分關鍵特徵置於最前（Hard Lock 優先）。"
        )
    if blocks["outfit"]:
        lines.append(f"服裝：{len(blocks['outfit'])} 個特徵。")
    if blocks["environment"]:
        lines.append(f"場景：{ '、'.join(blocks['environment'][:3]) } 等。")
    if ast.negative.semantic or ast.user_intent.must_avoid:
        avoid = "、".join((*ast.negative.semantic, *ast.user_intent.must_avoid)[:4])
        lines.append(f"排除（負向）：{avoid}。")
    if style.prohibited_traits:
        lines.append(f"風格禁止：{'、'.join(style.prohibited_traits[:4])}（不會出現在正向）。")
    lines.append(
        f"身分覆蓋率 {character.coverage.display}；風格覆蓋率 {style.coverage.display}。"
    )
    return "\n".join(lines)
