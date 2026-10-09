"""Non-secret global AI runtime preferences.

Credentials are deliberately absent from these models. They belong behind the
application credential port and must never enter SQLite, backups, normal model
dumps, or task-route JSON.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from imaginarium_forge.domain.common.ids import utc_now_iso

GLOBAL_AI_RUNTIME_PREFERENCES_ID: Literal["global"] = "global"
_TASK_ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_LANGUAGE_TAG_RE = re.compile(r"^[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*$")


class AIProviderPreference(StrEnum):
    """Author-selectable default provider; routing execution arrives in workflow."""

    OFFLINE = "offline"
    OLLAMA = "ollama"
    OPENAI = "openai"


class AIQualityProfile(StrEnum):
    """Product-level generation intent, independent of a concrete model."""

    QUICK_IDEA = "quick_idea"
    STANDARD_DRAFT = "standard_draft"
    DEEP_LONG_FORM = "deep_long_form"
    POLISH_REVIEW = "polish_review"
    IMAGE_PROMPT = "image_prompt"


class AIFallbackPolicy(StrEnum):
    """Explicit fallback preference; no service may silently invent one."""

    FAIL_CLOSED = "fail_closed"
    OLLAMA_THEN_FAIL = "ollama_then_fail"
    OFFLINE_THEN_FAIL = "offline_then_fail"


def _normalize_model_id(value: str | None, *, required: bool) -> str | None:
    if value is None:
        if required:
            raise ValueError("模型 ID 不可留白")
        return None
    normalized = value.strip()
    if not normalized:
        if required:
            raise ValueError("模型 ID 不可留白")
        return None
    if len(normalized) > 256:
        raise ValueError("模型 ID 最多 256 個字元")
    if any(character.isspace() or ord(character) < 32 for character in normalized):
        raise ValueError("模型 ID 必須是單一且不含空白的識別字")
    return normalized


class TaskRoutePreference(BaseModel):
    """One optional task override persisted as non-secret canonical JSON.

    This is configuration only. Provider registry/router execution remains a
    workflow responsibility.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    provider: AIProviderPreference | None = None
    model: str | None = None
    quality_profile: AIQualityProfile | None = None
    fallback_policy: AIFallbackPolicy | None = None

    @field_validator("model", mode="before")
    @classmethod
    def _model_id(cls, value: object) -> object:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("task route 模型 ID 必須是文字")
        return _normalize_model_id(value, required=False)

    @model_validator(mode="after")
    def _has_an_override(self) -> TaskRoutePreference:
        if not any(
            (
                self.provider is not None,
                self.model is not None,
                self.quality_profile is not None,
                self.fallback_policy is not None,
            )
        ):
            raise ValueError("task route 至少需要一個覆寫值")
        return self


class _AIRuntimePreferenceValues(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    default_provider: AIProviderPreference = AIProviderPreference.OFFLINE
    openai_model: str = "gpt-5.6"
    ollama_model: str = ""
    default_quality_profile: AIQualityProfile = AIQualityProfile.STANDARD_DRAFT
    default_language: str = "zh-TW"
    fallback_policy: AIFallbackPolicy = AIFallbackPolicy.FAIL_CLOSED
    remote_provider_enabled: bool = False
    remote_ack_version: str = ""
    streaming_enabled: bool = True
    auto_continue: bool = True
    show_token_usage: bool = True
    task_routes: dict[str, TaskRoutePreference] = Field(default_factory=dict)

    @field_validator("openai_model", mode="before")
    @classmethod
    def _openai_model_id(cls, value: object) -> object:
        if not isinstance(value, str):
            raise ValueError("OpenAI 模型 ID 必須是文字")
        return _normalize_model_id(value, required=True)

    @field_validator("ollama_model", mode="before")
    @classmethod
    def _ollama_model_id(cls, value: object) -> object:
        if not isinstance(value, str):
            raise ValueError("Ollama 模型 ID 必須是文字")
        return _normalize_model_id(value, required=False) or ""

    @field_validator("remote_ack_version", mode="before")
    @classmethod
    def _ack_version(cls, value: object) -> object:
        if not isinstance(value, str):
            raise ValueError("遠端傳送告知版本必須是文字")
        normalized = value.strip()
        if len(normalized) > 100 or any(ord(character) < 32 for character in normalized):
            raise ValueError("遠端傳送告知版本格式無效")
        return normalized

    @field_validator("default_language", mode="before")
    @classmethod
    def _language_tag(cls, value: object) -> object:
        if not isinstance(value, str):
            raise ValueError("預設語言必須是 BCP47-like 文字標籤")
        normalized = value.strip()
        if len(normalized) > 32 or not _LANGUAGE_TAG_RE.fullmatch(normalized):
            raise ValueError("預設語言必須是有效且不超過 32 字元的語言標籤")
        parts = normalized.split("-")
        canonical = [parts[0].lower()]
        for part in parts[1:]:
            if len(part) == 2 and part.isalpha():
                canonical.append(part.upper())
            elif len(part) == 4 and part.isalpha():
                canonical.append(part.title())
            else:
                canonical.append(part.lower())
        return "-".join(canonical)

    @field_validator("task_routes", mode="before")
    @classmethod
    def _canonical_task_routes(cls, value: object) -> object:
        if value is None:
            return {}
        if not isinstance(value, Mapping):
            raise ValueError("task routes 必須是 JSON object")
        routes: dict[str, object] = {}
        for raw_key, route in value.items():
            if not isinstance(raw_key, str):
                raise ValueError("task route 識別字必須是文字")
            key = raw_key.strip().casefold()
            if not _TASK_ID_RE.fullmatch(key):
                raise ValueError("task route 識別字只允許小寫英數字與底線")
            if key in routes:
                raise ValueError("正規化後的 task route 識別字不可重複")
            routes[key] = route
        return dict(sorted(routes.items()))

    @model_validator(mode="after")
    def _remote_and_default_provider_are_coherent(self) -> _AIRuntimePreferenceValues:
        if self.remote_provider_enabled and not self.remote_ack_version:
            raise ValueError("啟用 OpenAI 前必須記錄遠端傳送告知版本")
        if (
            self.default_provider is AIProviderPreference.OPENAI
            and not self.remote_provider_enabled
        ):
            raise ValueError("OpenAI 作為預設 provider 前必須先啟用遠端 provider")
        if self.default_provider is AIProviderPreference.OLLAMA and not self.ollama_model:
            raise ValueError("Ollama 作為預設 provider 時必須選擇模型")
        return self


class AIRuntimePreferenceUpdate(_AIRuntimePreferenceValues):
    """Complete non-secret preference values accepted by the save command."""


class AIRuntimePreferences(_AIRuntimePreferenceValues):
    """The one persisted global preference row."""

    id: Literal["global"] = GLOBAL_AI_RUNTIME_PREFERENCES_ID
    created_at: str = Field(default_factory=utc_now_iso, min_length=1, max_length=64)
    updated_at: str = Field(default_factory=utc_now_iso, min_length=1, max_length=64)

    @field_validator("created_at", "updated_at")
    @classmethod
    def _timestamp_not_padded(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("設定時間不可留白")
        return normalized

    def as_update(self) -> AIRuntimePreferenceUpdate:
        """Return the editable values without persistence identity/timestamps."""

        return AIRuntimePreferenceUpdate.model_validate(
            self.model_dump(
                mode="python",
                exclude={"id", "created_at", "updated_at"},
            )
        )


__all__ = [
    "GLOBAL_AI_RUNTIME_PREFERENCES_ID",
    "AIFallbackPolicy",
    "AIProviderPreference",
    "AIQualityProfile",
    "AIRuntimePreferenceUpdate",
    "AIRuntimePreferences",
    "TaskRoutePreference",
]
