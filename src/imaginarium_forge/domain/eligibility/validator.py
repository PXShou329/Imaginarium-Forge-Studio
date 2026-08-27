"""Deterministic mature-content eligibility validator (ADR-018, Addendum v1.1).

Properties, all of them non-negotiable:
- deterministic pure function of (character, selected_version, request projection);
- fail-closed: missing/unknown/disputed/unverifiable data blocks;
- no LLM anywhere in the decision path;
- age constants come from `domain.policy` (never re-declared here);
- for ORIGINAL characters the decision uses the explicit age itself, never a
  hand-editable classification field (closes the malformed-import bypass);
- Scene Overrides can never change age eligibility;
- prior audit rows never authorize anything (the service re-evaluates live).

Messages are user-facing Traditional Chinese; codes are stable English enums.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from imaginarium_forge.domain.character.model import Character
from imaginarium_forge.domain.character.presentation_cues import (
    detect_visual_dna_presentation_cues,
)
from imaginarium_forge.domain.character.version import CharacterVersion
from imaginarium_forge.domain.common.enums import (
    AgeClassification,
    CharacterOrigin,
    EligibilityReason,
)
from imaginarium_forge.domain.eligibility.projection import EligibilityRequestProjection
from imaginarium_forge.domain.policy import AGE_POLICY

VALIDATOR_VERSION = "phase4-eligibility-v2"

#: Flag value (set by callers) marking an attempt to change age via scene override.
SCENE_OVERRIDE_AGE_FLAG = "scene_override_age_change"


class EligibilityResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    allowed: bool
    reason_code: EligibilityReason
    message: str
    validator_version: str
    input_fingerprint: str


class MatureContentEligibilityValidator:
    """Single validator shared by every current and future generation workflow."""

    version = VALIDATOR_VERSION

    def evaluate(
        self,
        character: Character,
        selected_version: CharacterVersion | None,
        request: EligibilityRequestProjection,
    ) -> EligibilityResult:
        fingerprint = request.fingerprint()

        def blocked(code: EligibilityReason, message: str) -> EligibilityResult:
            return EligibilityResult(
                allowed=False,
                reason_code=code,
                message=message,
                validator_version=self.version,
                input_fingerprint=fingerprint,
            )

        def allowed(code: EligibilityReason, message: str) -> EligibilityResult:
            return EligibilityResult(
                allowed=True,
                reason_code=code,
                message=message,
                validator_version=self.version,
                input_fingerprint=fingerprint,
            )

        # 0) Adult eligibility is only required when adult content is requested.
        #    Dark / horror / violent NON-sexual fiction passes through here.
        if not request.adult_content_requested:
            return allowed(
                EligibilityReason.NOT_REQUIRED,
                "本請求未包含成人內容，無需成人資格驗證。",
            )

        # 1) Policy profile must be present (deterministic boundaries live there).
        if not request.policy_profile_id.strip():
            return blocked(
                EligibilityReason.POLICY_PROFILE_MISSING,
                "找不到有效的 policy profile；成人內容請求必須綁定明確的政策設定。",
            )

        # 2) The selected version must exist and belong to THIS character.
        if selected_version is None or selected_version.character_id != character.id:
            return blocked(
                EligibilityReason.INVALID_CHARACTER_VERSION,
                "選用的角色版本無效或不屬於此角色；請重新選擇版本。",
            )

        # 3) Scene overrides can never change age eligibility.
        if SCENE_OVERRIDE_AGE_FLAG in request.eligibility_relevant_flags:
            return blocked(
                EligibilityReason.INVALID_SCENE_AGE_OVERRIDE,
                "年齡資格不可經由 Scene Override 變更；此路徑一律封鎖。",
            )

        # 4) Defense-layer-3 contradiction guard (A-01): independently fail closed
        #    against malformed/legacy/imported records that bypassed model validation.
        age_status = character.age_status
        floor = AGE_POLICY.minimum_age_for_mature_content
        if age_status.explicit_age is not None:
            contradiction = (
                age_status.classification is AgeClassification.VERIFIED_ADULT
                and age_status.explicit_age < floor
            ) or (
                age_status.classification is AgeClassification.VERIFIED_MINOR
                and age_status.explicit_age >= floor
            )
            if contradiction:
                return blocked(
                    EligibilityReason.CONTRADICTORY_AGE_STATUS,
                    f"矛盾的年齡狀態：分類 {age_status.classification.value} 與明確年齡 "
                    f"{age_status.explicit_age} 不一致；依 fail-closed 原則封鎖。",
                )

        # 5) Origin-specific identity checks (fail-closed).
        if character.character_origin is CharacterOrigin.ORIGINAL:
            age = age_status.explicit_age
            if age is None:
                return blocked(
                    EligibilityReason.MISSING_EXPLICIT_ADULT_AGE,
                    "原創角色的成人內容需要明確的成人年齡；請先填寫並確認年齡。",
                )
            if age < floor:
                return blocked(
                    EligibilityReason.BELOW_ADULT_AGE_FLOOR,
                    f"角色明確年齡 {age} 低於成人內容門檻 {floor}；此請求被封鎖。",
                )
            # A-02: an explicit adult age must be explicitly confirmed by the user.
            if not age_status.user_confirmed:
                return blocked(
                    EligibilityReason.EXPLICIT_ADULT_AGE_UNCONFIRMED,
                    "原創角色的成人年齡尚未經使用者明確確認（預填值不算確認）；"
                    "請勾選年齡確認後再試。",
                )
            review = character.originality_review
            if review is not None and not review.satisfied:
                return blocked(
                    EligibilityReason.ORIGINALITY_REVIEW_REQUIRED,
                    "此原創角色標記為取材自既有靈感，原創性審查尚未完成；"
                    "完成審查前不得用於成人內容。",
                )
        else:  # EXISTING
            classification = age_status.classification
            if classification is AgeClassification.VERIFIED_MINOR:
                return blocked(
                    EligibilityReason.EXISTING_CHARACTER_MINOR,
                    "此既有角色在原作中為未成年，永久不適格於成人內容，"
                    "且不存在任何轉換路徑。可改用 Path B 建立獨立原創成人角色。",
                )
            if classification is AgeClassification.UNKNOWN:
                return blocked(
                    EligibilityReason.EXISTING_CHARACTER_AGE_UNKNOWN,
                    "此既有角色的原作年齡不明；依 fail-closed 原則封鎖成人內容。",
                )
            if classification is AgeClassification.DISPUTED:
                return blocked(
                    EligibilityReason.EXISTING_CHARACTER_AGE_DISPUTED,
                    "此既有角色的原作年齡有爭議；依 fail-closed 原則封鎖成人內容。",
                )
            # classification is VERIFIED_ADULT.
            # If a numeric age is present it already passed the contradiction guard
            # (age >= floor). If age is ABSENT, require COMPLETE source verification
            # (A-01 decision): title + character name + method + substantive note.
            if age_status.explicit_age is None:
                source = character.source_metadata
                if source is None or not source.is_complete_for_verified_adult():
                    return blocked(
                        EligibilityReason.EXISTING_CHARACTER_ADULT_STATUS_UNVERIFIED,
                        "既有角色以 verified_adult 但無明確年齡時，必須具備完整來源驗證"
                        "（作品名稱、角色名稱、驗證方式、以及清楚說明成人依據的驗證註記）；"
                        "補齊後再試。",
                    )

        # 5) Presentation checks on the SELECTED VERSION (version-level ownership).
        presentation = selected_version.adult_presentation
        visual = selected_version.visual_dna
        detected_cues = detect_visual_dna_presentation_cues(visual)
        if presentation.is_minor_era_design or detected_cues.minor_era:
            return blocked(
                EligibilityReason.MINOR_ERA_VERSION_SELECTED,
                "選用的角色版本為未成年期設計；請切換至成人呈現版本。",
            )
        if not presentation.is_valid_adult_presentation or detected_cues.childlike_flags:
            return blocked(
                EligibilityReason.CHILDLIKE_PRESENTATION_CONFLICT,
                "選用版本的視覺呈現不符成人呈現條件（臉部／身體成人呈現須成立，"
                "且不得帶孩童化特徵旗標）。",
            )

        return allowed(EligibilityReason.ALLOWED, "資格驗證通過：可用於成人內容請求。")
