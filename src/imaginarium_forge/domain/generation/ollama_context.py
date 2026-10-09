"""Pure Ollama context contracts and model identifier validation."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

OLLAMA_CONTEXT_PROFILE_POLICY_VERSION: Literal["ollama-context-profile-v1"] = (
    "ollama-context-profile-v1"
)
OLLAMA_STANDARD_NUM_CTX = 8_192
OLLAMA_LONG_NUM_CTX = 32_768
OLLAMA_HIGH_RESOURCE_NUM_CTX = 16_384
OLLAMA_MODEL_CAPABILITY_POLICY_VERSION: Literal["ollama-model-capability-v1"] = (
    "ollama-model-capability-v1"
)


class OllamaContextProfile(StrEnum):
    """Author-facing context-window presets; never a length-satisfaction claim."""

    AUTO = "auto"
    STANDARD = "standard"
    LONG = "long"
    CUSTOM = "custom"


OllamaContextProfileRationale = Literal[
    "auto_known_model_and_hardware_limits",
    "auto_conservative_without_complete_probe",
    "custom_exact",
    "preset_constrained_by_capability",
    "preset_exact",
]


class OllamaModelCapabilitySnapshot(BaseModel):
    """Sanitized token-limit facts read by an explicit ``/api/show`` probe.

    ``None`` is deliberately preserved when Ollama does not report a fact.
    In particular, neither parameter count nor quantization is used to guess a
    context window or output limit.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    policy_version: Literal["ollama-model-capability-v1"] = OLLAMA_MODEL_CAPABILITY_POLICY_VERSION
    model: str
    source: Literal["ollama_api_show"] = "ollama_api_show"
    model_context_limit: int | None = Field(default=None, gt=0)
    model_output_limit: int | None = Field(default=None, gt=0)
    hardware_context_limit: int | None = Field(default=None, gt=0)
    context_metadata_key: str | None = None
    output_metadata_key: str | None = None
    hardware_limit_source: Literal["explicit_snapshot"] | None = None
    warning: str | None = None

    @field_validator("model")
    @classmethod
    def _model_id(cls, value: str) -> str:
        return normalize_ollama_model_id(value)

    @model_validator(mode="after")
    def _coherent_evidence(self) -> Self:
        if (self.model_context_limit is None) != (self.context_metadata_key is None):
            raise ValueError("model context limit and metadata key must be present together")
        if (self.model_output_limit is None) != (self.output_metadata_key is None):
            raise ValueError("model output limit and metadata key must be present together")
        if (self.hardware_context_limit is None) != (self.hardware_limit_source is None):
            raise ValueError("hardware context limit and source must be present together")
        if (
            self.model_context_limit is not None
            and self.model_output_limit is not None
            and self.model_output_limit > self.model_context_limit
        ):
            raise ValueError("model output limit cannot exceed model context limit")
        missing_fact = (
            self.model_context_limit is None
            or self.model_output_limit is None
            or self.hardware_context_limit is None
        )
        if missing_fact and not self.warning:
            raise ValueError("an incomplete capability snapshot must disclose a warning")
        return self


class OllamaContextProfileDecision(BaseModel):
    """Explainable, non-secret result of selecting an Ollama context profile."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    policy_version: Literal["ollama-context-profile-v1"] = OLLAMA_CONTEXT_PROFILE_POLICY_VERSION
    profile: OllamaContextProfile
    requested_num_ctx: int | None = Field(default=None, gt=0)
    num_ctx: int = Field(gt=0)
    model_context_limit: int | None = Field(default=None, gt=0)
    model_output_limit: int | None = Field(default=None, gt=0)
    hardware_context_limit: int | None = Field(default=None, gt=0)
    num_predict_cap: int = Field(gt=0)
    capability_model: str | None = None
    capability_source: Literal["ollama_api_show"] | None = None
    rationale: OllamaContextProfileRationale
    warning: str | None = None

    @model_validator(mode="after")
    def _coherent_decision(self) -> Self:
        custom_num_ctx = (
            self.requested_num_ctx if self.profile is OllamaContextProfile.CUSTOM else None
        )
        expected = _resolved_profile_fields(
            self.profile,
            model_context_limit=self.model_context_limit,
            model_output_limit=self.model_output_limit,
            hardware_context_limit=self.hardware_context_limit,
            custom_num_ctx=custom_num_ctx,
            capability_model=self.capability_model,
            capability_source=self.capability_source,
        )
        actual = self.model_dump(
            mode="python",
            exclude={"policy_version", "profile"},
        )
        if actual != expected:
            raise ValueError("Ollama context profile decision fields are not coherent")
        return self


def _resolved_profile_fields(
    profile: OllamaContextProfile,
    *,
    model_context_limit: int | None,
    model_output_limit: int | None,
    hardware_context_limit: int | None,
    custom_num_ctx: int | None,
    capability_model: str | None,
    capability_source: Literal["ollama_api_show"] | None,
) -> dict[str, Any]:
    for name, value in (
        ("model_context_limit", model_context_limit),
        ("model_output_limit", model_output_limit),
        ("hardware_context_limit", hardware_context_limit),
    ):
        if value is not None and value <= 0:
            raise ValueError(f"{name} must be positive when supplied")
    if (
        model_context_limit is not None
        and model_output_limit is not None
        and model_output_limit > model_context_limit
    ):
        raise ValueError("model_output_limit cannot exceed model_context_limit")
    normalized_model = (
        normalize_ollama_model_id(capability_model) if capability_model is not None else None
    )
    if capability_source is not None and normalized_model is None:
        raise ValueError("capability_source requires capability_model")

    known_limits = tuple(
        value for value in (model_context_limit, hardware_context_limit) if value is not None
    )
    effective_limit = min(known_limits) if known_limits else None
    complete_context_probe = model_context_limit is not None and hardware_context_limit is not None
    warning_parts: list[str] = []

    if profile is OllamaContextProfile.CUSTOM:
        if custom_num_ctx is None or custom_num_ctx <= 0:
            raise ValueError("custom_num_ctx must be positive for the custom profile")
        if effective_limit is not None and custom_num_ctx > effective_limit:
            raise ValueError(
                "custom_num_ctx exceeds a known model or hardware context limit; "
                "the value was not silently lowered"
            )
        requested_num_ctx = custom_num_ctx
        resolved = custom_num_ctx
        rationale: OllamaContextProfileRationale = "custom_exact"
        if not complete_context_probe:
            warning_parts.append(
                "尚未取得完整模型／硬體能力快照；自訂值未被降低，但尚未證明可安全載入。"
            )
    elif custom_num_ctx is not None:
        raise ValueError("custom_num_ctx is only valid for the custom profile")
    elif profile is OllamaContextProfile.AUTO:
        desired = (
            OLLAMA_LONG_NUM_CTX
            if complete_context_probe
            and effective_limit is not None
            and effective_limit >= OLLAMA_LONG_NUM_CTX
            else OLLAMA_STANDARD_NUM_CTX
        )
        requested_num_ctx = None
        resolved = min(desired, effective_limit) if effective_limit is not None else desired
        rationale = (
            "auto_known_model_and_hardware_limits"
            if complete_context_probe
            else "auto_conservative_without_complete_probe"
        )
        if not complete_context_probe:
            warning_parts.append("尚未取得完整模型／硬體能力快照；AUTO 使用保守 context window。")
        if resolved < desired:
            warning_parts.append("AUTO 受已知能力上限限制；實際 num_ctx 已明確列出。")
    else:
        desired = (
            OLLAMA_LONG_NUM_CTX if profile is OllamaContextProfile.LONG else OLLAMA_STANDARD_NUM_CTX
        )
        requested_num_ctx = desired
        resolved = min(desired, effective_limit) if effective_limit is not None else desired
        constrained = resolved < desired
        rationale = "preset_constrained_by_capability" if constrained else "preset_exact"
        if not complete_context_probe:
            warning_parts.append("尚未取得完整模型／硬體能力快照；此 preset 尚未完成資源驗證。")
        if constrained:
            warning_parts.append("此 profile 受已知模型／硬體上限限制；未使用原預設值。")

    num_predict_cap = min(resolved, model_output_limit or resolved)
    if model_output_limit is None:
        warning_parts.append(
            "模型未回報獨立輸出 token 上限；num_predict cap 僅以實際 num_ctx 為上界。"
        )
    if resolved >= OLLAMA_HIGH_RESOURCE_NUM_CTX:
        warning_parts.append("長 context window 可能使用大量 VRAM/RAM。")

    return {
        "requested_num_ctx": requested_num_ctx,
        "num_ctx": resolved,
        "model_context_limit": model_context_limit,
        "model_output_limit": model_output_limit,
        "hardware_context_limit": hardware_context_limit,
        "num_predict_cap": num_predict_cap,
        "capability_model": normalized_model,
        "capability_source": capability_source,
        "rationale": rationale,
        "warning": " ".join(warning_parts) or None,
    }


def resolve_ollama_context_profile(
    profile: OllamaContextProfile | str,
    *,
    model_context_limit: int | None = None,
    model_output_limit: int | None = None,
    hardware_context_limit: int | None = None,
    custom_num_ctx: int | None = None,
    capability_model: str | None = None,
    capability_source: Literal["ollama_api_show"] | None = None,
) -> OllamaContextProfileDecision:
    """Resolve an explicit profile without silently lowering custom values.

    AUTO may choose a lower known-safe value because that is the meaning of
    the profile. STANDARD/LONG surface any capability constraint as a
    warning. CUSTOM either preserves the exact requested value or fails when
    a known model/hardware limit proves it unsupported.
    """

    selected = OllamaContextProfile(profile)
    return OllamaContextProfileDecision(
        profile=selected,
        **_resolved_profile_fields(
            selected,
            model_context_limit=model_context_limit,
            model_output_limit=model_output_limit,
            hardware_context_limit=hardware_context_limit,
            custom_num_ctx=custom_num_ctx,
            capability_model=capability_model,
            capability_source=capability_source,
        ),
    )


def normalize_ollama_model_id(value: object) -> str:
    """Normalize one model identifier before placing it in a JSON request."""

    model = str(value).strip()
    if not model:
        raise ValueError("Ollama model ID must not be blank")
    if len(model) > 256:
        raise ValueError("Ollama model ID must not exceed 256 characters")
    if any(
        character.isspace() or ord(character) < 32 or ord(character) == 127 for character in model
    ):
        raise ValueError("Ollama model ID must not contain whitespace or control characters")
    return model
