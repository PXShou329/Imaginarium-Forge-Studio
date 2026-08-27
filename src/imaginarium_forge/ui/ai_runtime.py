"""Provider-neutral AI runtime choices for author-facing UI controls.

This module deliberately has no Streamlit or provider imports.  Pages can use
the frozen contract to resolve a no-LLM, local-LLM, or OpenAI choice without
putting credentials in persistent application settings or serializable handoff
payloads.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

from imaginarium_forge.config.settings import DEFAULT_OPENAI_MODEL


class GenerationMode(StrEnum):
    """How an author wants a generation action to obtain its content."""

    NO_LLM = "no_llm"
    LOCAL = "local"
    OPENAI = "openai"


class OpenAIModelPreset(StrEnum):
    """Curated OpenAI choices plus an escape hatch for future model IDs."""

    GPT_5_6 = DEFAULT_OPENAI_MODEL
    GPT_5_6_SOL = "gpt-5.6-sol"
    GPT_5_6_TERRA = "gpt-5.6-terra"
    GPT_5_6_LUNA = "gpt-5.6-luna"
    CUSTOM = "custom"


OPENAI_MODEL_PRESETS: tuple[OpenAIModelPreset, ...] = tuple(OpenAIModelPreset)


def _normalized_model_id(value: str, *, field_name: str) -> str:
    model = value.strip()
    if not model:
        raise ValueError(f"{field_name} must not be blank")
    if len(model) > 256:
        raise ValueError(f"{field_name} must be at most 256 characters")
    if any(
        character.isspace() or ord(character) < 32 or ord(character) == 127 for character in model
    ):
        raise ValueError(f"{field_name} must be a single model identifier without whitespace")
    return model


def resolve_openai_model(
    preset: OpenAIModelPreset | str,
    *,
    custom_model: str = "",
) -> str:
    """Resolve a curated selection or a validated custom OpenAI model ID."""

    try:
        selected = OpenAIModelPreset(preset)
    except ValueError as exc:
        raise ValueError("unsupported OpenAI model preset; choose custom explicitly") from exc
    if selected is OpenAIModelPreset.CUSTOM:
        return _normalized_model_id(custom_model, field_name="custom OpenAI model")
    return selected.value


class GenerationRuntimeConfig(BaseModel):
    """Immutable, non-persistent settings captured for one generation action.

    ``openai_api_key`` is excluded from every normal Pydantic dump and repr.
    Callers may pass the resulting ``SecretStr`` directly to the provider, then
    discard this object when the action completes.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    mode: GenerationMode = GenerationMode.NO_LLM
    local_model: str = ""
    openai_model_preset: OpenAIModelPreset = OpenAIModelPreset.GPT_5_6
    custom_openai_model: str = ""
    openai_api_key: SecretStr | None = Field(default=None, exclude=True, repr=False)

    @field_validator("local_model", "custom_openai_model", mode="before")
    @classmethod
    def _strip_model_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("openai_api_key", mode="before")
    @classmethod
    def _runtime_secret(cls, value: object) -> object:
        if value is None:
            return None
        raw = value.get_secret_value() if isinstance(value, SecretStr) else str(value)
        stripped = raw.strip()
        return SecretStr(stripped) if stripped else None

    @model_validator(mode="after")
    def _active_choice_is_ready(self) -> GenerationRuntimeConfig:
        if self.mode is GenerationMode.LOCAL:
            _normalized_model_id(self.local_model, field_name="local model")
        elif self.mode is GenerationMode.OPENAI:
            resolve_openai_model(
                self.openai_model_preset,
                custom_model=self.custom_openai_model,
            )
        return self

    @property
    def uses_llm(self) -> bool:
        return self.mode is not GenerationMode.NO_LLM

    @property
    def provider_name(self) -> Literal["ollama", "openai"] | None:
        if self.mode is GenerationMode.LOCAL:
            return "ollama"
        if self.mode is GenerationMode.OPENAI:
            return "openai"
        return None

    @property
    def model(self) -> str:
        if self.mode is GenerationMode.NO_LLM:
            return ""
        if self.mode is GenerationMode.LOCAL:
            return _normalized_model_id(self.local_model, field_name="local model")
        return resolve_openai_model(
            self.openai_model_preset,
            custom_model=self.custom_openai_model,
        )

    @property
    def has_runtime_openai_key(self) -> bool:
        return bool(self.openai_api_key is not None and self.openai_api_key.get_secret_value())

    def api_key_for_provider(self) -> SecretStr | None:
        """Return the in-memory secret without converting it to plain text."""

        return self.openai_api_key


__all__ = [
    "OPENAI_MODEL_PRESETS",
    "GenerationMode",
    "GenerationRuntimeConfig",
    "OpenAIModelPreset",
    "resolve_openai_model",
]
