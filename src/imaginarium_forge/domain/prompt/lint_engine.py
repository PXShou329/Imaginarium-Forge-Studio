"""Deterministic prompt linter (spec §8.1/§8.2).

Every rule is a pure check over already-computed inputs (AST, compiled
blocks, detected conflicts, resolved profile, coverage). No LLM, no I/O.

Severity policy (§8.1): policy/structural ERRORs block final compilation
acceptance (`LintReport.has_errors`); warnings and information never block.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from imaginarium_forge.domain.prompt.ast import PromptAST
from imaginarium_forge.domain.prompt.blocks import CompiledBlocks
from imaginarium_forge.domain.prompt.compilation import CharacterLockInput, StyleInput
from imaginarium_forge.domain.prompt.conflicts import (
    Conflict,
    ConflictCode,
    ConflictSeverity,
)
from imaginarium_forge.domain.prompt.lint import LintCheck, LintFinding, LintLevel, LintReport
from imaginarium_forge.domain.prompt.profiles import ResolvedProfile


class LintThresholds(BaseModel):
    model_config = ConfigDict(frozen=True)

    min_identity_coverage: float = 0.6
    min_style_coverage: float = 0.4
    max_quality_tags: int = 6
    max_positive_tags: int = 60
    max_background_ratio: float = 2.0  # environment tags vs subject-side tags


#: conflict code → (lint check, level). Errors mirror resolver severity.
_CONFLICT_TO_LINT: dict[ConflictCode, tuple[LintCheck, LintLevel]] = {
    ConflictCode.LEFT_RIGHT_ORIENTATION: (LintCheck.LEFT_RIGHT_CONFLICT, LintLevel.ERROR),
    ConflictCode.HAIR_COLOR: (LintCheck.HAIR_CONFLICT, LintLevel.ERROR),
    ConflictCode.HAIR_STREAK: (LintCheck.HAIR_CONFLICT, LintLevel.ERROR),
    ConflictCode.EYE_COLOR: (LintCheck.EYE_CONFLICT, LintLevel.ERROR),
    ConflictCode.DISTINGUISHING_FEATURE: (
        LintCheck.DISTINGUISHING_FEATURE_CONFLICT,
        LintLevel.ERROR,
    ),
    ConflictCode.PROHIBITED_CANON_MUTATION: (
        LintCheck.UNRESOLVED_HIGH_SEVERITY_CONFLICT,
        LintLevel.ERROR,
    ),
    ConflictCode.ADULT_CONTENT_POLICY: (
        LintCheck.ADULT_PRESENTATION_CONFLICT,
        LintLevel.ERROR,
    ),
    ConflictCode.AGE_PRESENTATION: (
        LintCheck.ADULT_PRESENTATION_CONFLICT,
        LintLevel.ERROR,
    ),
    ConflictCode.POSITIVE_NEGATIVE_OVERLAP: (
        LintCheck.POSITIVE_NEGATIVE_OVERLAP,
        LintLevel.WARNING,
    ),
    ConflictCode.UNSUPPORTED_PROFILE_SYNTAX: (
        LintCheck.UNSUPPORTED_WEIGHTING_SYNTAX,
        LintLevel.WARNING,
    ),
    ConflictCode.FULL_BODY_VS_CROP: (LintCheck.FULL_BODY_CROP_CONFLICT, LintLevel.WARNING),
    ConflictCode.CAMERA_FRAMING_CONTRADICTION: (
        LintCheck.MODEL_PROFILE_INCOMPATIBILITY,
        LintLevel.WARNING,
    ),
    ConflictCode.DAY_NIGHT: (LintCheck.DAY_NIGHT_CONFLICT, LintLevel.WARNING),
    ConflictCode.WEATHER: (LintCheck.WEATHER_CONFLICT, LintLevel.WARNING),
    ConflictCode.INCOMPATIBLE_STYLE_TERMS: (LintCheck.STYLE_CONTRADICTION, LintLevel.WARNING),
    ConflictCode.OUTFIT: (LintCheck.UNRESOLVED_HIGH_SEVERITY_CONFLICT, LintLevel.ERROR),
}


def _split_tags(joined: str) -> tuple[str, ...]:
    return tuple(t.strip() for t in joined.split(",") if t.strip())


def lint_prompt(
    *,
    ast: PromptAST,
    blocks: CompiledBlocks,
    conflicts: tuple[Conflict, ...],
    profile: ResolvedProfile,
    character: CharacterLockInput,
    style: StyleInput,
    checkpoint_known: bool = True,
    thresholds: LintThresholds | None = None,
) -> LintReport:
    th = thresholds or LintThresholds()
    findings: list[LintFinding] = []

    def add(check: LintCheck, level: LintLevel, message: str, detail: str = "") -> None:
        findings.append(
            LintFinding(check=check, level=level, message_zh_tw=message, detail=detail)
        )

    # -- structural -------------------------------------------------------
    if not ast.subjects:
        add(LintCheck.MISSING_SUBJECT, LintLevel.ERROR, "缺少主體：至少需要一位主角。")

    if character.prohibited_mutations and not character.lock_tags:
        add(
            LintCheck.MISSING_REQUIRED_HARD_LOCKS,
            LintLevel.ERROR,
            "此角色版本沒有任何 Hard Lock 身分特徵，無法保證身分一致性。",
        )

    cov = character.coverage
    identity_ratio = (cov.covered_weight / cov.required_weight) if cov.required_weight else 1.0
    if identity_ratio < th.min_identity_coverage:
        add(
            LintCheck.IDENTITY_COVERAGE_BELOW_THRESHOLD,
            LintLevel.WARNING,
            f"身分覆蓋率 {cov.display} 低於門檻 {th.min_identity_coverage:.0%}。",
            detail="、".join(cov.missing),
        )
    scov = style.coverage
    style_ratio = (scov.covered_weight / scov.required_weight) if scov.required_weight else 1.0
    if style_ratio < th.min_style_coverage:
        add(
            LintCheck.STYLE_COVERAGE_BELOW_THRESHOLD,
            LintLevel.WARNING,
            f"風格覆蓋率 {scov.display} 低於門檻 {th.min_style_coverage:.0%}。",
            detail="、".join(scov.missing),
        )

    # duplicate tags in RAW user intent (the compiler dedupes silently;
    # duplicates in must_include signal a noisy input worth surfacing)
    seen: set[str] = set()
    dups: list[str] = []
    for tag in ast.user_intent.must_include:
        key = " ".join(tag.casefold().split())
        if key in seen:
            dups.append(tag)
        seen.add(key)
    if dups:
        add(
            LintCheck.DUPLICATE_TAGS,
            LintLevel.INFORMATION,
            f"must_include 內含重複 tag：{'、'.join(dups)}（編譯時已自動去重）。",
        )

    # -- conflict-derived rules -------------------------------------------
    for conflict in conflicts:
        mapped = _CONFLICT_TO_LINT.get(conflict.code)
        if mapped is None:
            if conflict.severity is ConflictSeverity.ERROR:
                add(
                    LintCheck.UNRESOLVED_HIGH_SEVERITY_CONFLICT,
                    LintLevel.ERROR,
                    conflict.message_zh_tw,
                )
            continue
        check, level = mapped
        add(check, level, conflict.message_zh_tw, f"{conflict.source_a} ↔ {conflict.source_b}")

    # -- volume / balance --------------------------------------------------
    quality_count = len(_split_tags(blocks.quality_block))
    if quality_count > th.max_quality_tags:
        add(
            LintCheck.EXCESSIVE_QUALITY_TAGS,
            LintLevel.WARNING,
            f"品質 tag 共 {quality_count} 個，超過建議上限 {th.max_quality_tags}。",
        )
    positive_count = len(_split_tags(blocks.positive_prompt))
    if positive_count > th.max_positive_tags:
        add(
            LintCheck.EXCESSIVELY_LONG_PROMPT,
            LintLevel.WARNING,
            f"正向 prompt 共 {positive_count} 個 tag，超過建議上限 {th.max_positive_tags}。",
        )
    subject_side = len(
        _split_tags(blocks.character_lock_block)
        + _split_tags(blocks.outfit_block)
        + _split_tags(blocks.pose_expression_block)
    )
    background_side = len(_split_tags(blocks.environment_block))
    if subject_side and background_side / max(subject_side, 1) > th.max_background_ratio:
        add(
            LintCheck.BACKGROUND_OVERWHELMS_CHARACTER,
            LintLevel.WARNING,
            f"環境 tag（{background_side}）遠多於主體 tag（{subject_side}），主角可能被稀釋。",
        )

    # -- profile provenance ------------------------------------------------
    if not checkpoint_known:
        add(
            LintCheck.UNKNOWN_CHECKPOINT,
            LintLevel.WARNING,
            "此 checkpoint 沒有已知 profile：目前使用保守通用語法。",
        )
    if profile.experimental:
        add(
            LintCheck.EXPERIMENTAL_PROFILE,
            LintLevel.INFORMATION,
            "目前 profile 標記為 experimental，建議以實驗紀錄累積證據。",
        )

    return LintReport(findings=tuple(findings))
