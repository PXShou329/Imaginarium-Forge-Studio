"""Validate character-version ownership for an exact creative request."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from imaginarium_forge.domain.character.model import Character
from imaginarium_forge.domain.character.version import CharacterVersion
from imaginarium_forge.domain.common.enums import EligibilityReason
from imaginarium_forge.domain.eligibility.projection import EligibilityRequestProjection

VALIDATOR_VERSION = "request-integrity-v1"


class EligibilityResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    allowed: bool
    reason_code: EligibilityReason
    message: str
    validator_version: str
    input_fingerprint: str


class RequestIntegrityValidator:
    """Only the requested character's own selected version can be used."""

    version = VALIDATOR_VERSION

    def evaluate(
        self,
        character: Character,
        selected_version: CharacterVersion | None,
        request: EligibilityRequestProjection,
    ) -> EligibilityResult:
        allowed = (
            selected_version is not None
            and selected_version.character_id == character.id
            and request.character_id == character.id
            and request.character_version_id == selected_version.id
        )
        return EligibilityResult(
            allowed=allowed,
            reason_code=(
                EligibilityReason.ALLOWED
                if allowed
                else EligibilityReason.INVALID_CHARACTER_VERSION
            ),
            message=(
                "角色與版本驗證通過。"
                if allowed
                else "角色與所選版本不一致。"
            ),
            validator_version=self.version,
            input_fingerprint=request.fingerprint(),
        )
