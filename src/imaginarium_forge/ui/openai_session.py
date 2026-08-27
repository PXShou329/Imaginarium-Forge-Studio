"""Session-only OpenAI credential and model selection for authoring pages.

The API key deliberately lives as :class:`pydantic.SecretStr` in Streamlit
session state.  It is never copied into application settings, persistence
models, exports, URLs, logs, or ordinary Pydantic dumps.  The password-entry
widget has a separate short-lived key which is cleared immediately after the
author explicitly applies it.
"""

from __future__ import annotations

import os
from collections.abc import Mapping, MutableMapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Final, Protocol

import streamlit as st
from pydantic import SecretStr
from streamlit.runtime.state.session_state_proxy import SessionStateProxy

from imaginarium_forge.ui.ai_runtime import (
    GenerationMode,
    GenerationRuntimeConfig,
    OpenAIModelPreset,
    resolve_openai_model,
)

SessionStateStore = MutableMapping[str, Any] | SessionStateProxy


class SessionStateWriter(Protocol):
    """Small structural contract shared by live and AppTest session state."""

    def __setitem__(self, key: str, value: Any) -> None: ...


OPENAI_API_KEY_SESSION_KEY: Final = "shared_openai_api_key"
OPENAI_API_KEY_ENTRY_KEY: Final = "shared_openai_api_key_entry"
OPENAI_API_KEY_ENTRY_CLEAR_PENDING_KEY: Final = "shared_openai_api_key_entry_clear_pending"
OPENAI_MODEL_PRESET_SESSION_KEY: Final = "shared_openai_model_preset"
OPENAI_MODEL_PRESET_ENTRY_KEY: Final = "shared_openai_model_preset_entry"
OPENAI_CUSTOM_MODEL_SESSION_KEY: Final = "shared_openai_custom_model"
OPENAI_CUSTOM_MODEL_ENTRY_KEY: Final = "shared_openai_custom_model_entry"
OPENAI_FORGET_PENDING_KEY: Final = "shared_openai_forget_pending"
OPENAI_KEY_RETENTION_SESSION_KEY: Final = "shared_openai_key_retention"
OPENAI_REMEMBER_SESSION_ENTRY_KEY: Final = "shared_openai_remember_for_session"
OPENAI_SETTINGS_NAV_TARGET: Final = "AI Settings"


class OpenAIKeyRetention(StrEnum):
    """How long a manually entered credential may remain in this UI session."""

    ONE_ACTION = "one_action"
    SESSION = "session"

_LEGACY_API_KEY_KEYS: Final = (
    "story_openai_api_key",
    "character_draft_box_openai_api_key",
    "inspiration_desk_openai_api_key",
    "prompt_scratchpad_openai_api_key",
)
_LEGACY_MODEL_PRESET_KEYS: Final = (
    "story_openai_model_preset",
    "character_draft_box_openai_model_preset",
    "inspiration_desk_openai_model_preset",
    "prompt_scratchpad_openai_model_preset",
)
_LEGACY_CUSTOM_MODEL_KEYS: Final = (
    "story_openai_custom_model",
    "character_draft_box_openai_custom_model",
    "inspiration_desk_openai_custom_model",
    "prompt_scratchpad_openai_custom_model",
)


def _secret_from(value: object) -> SecretStr | None:
    if isinstance(value, SecretStr):
        raw = value.get_secret_value()
    elif isinstance(value, str):
        raw = value
    else:
        return None
    normalized = raw.strip()
    return SecretStr(normalized) if normalized else None


def _first_non_blank(state: SessionStateStore, keys: tuple[str, ...]) -> object | None:
    for key in keys:
        value = state.get(key)
        if isinstance(value, SecretStr) and value.get_secret_value().strip():
            return value
        if isinstance(value, str) and value.strip():
            return value
    return None


def migrate_legacy_openai_session_state(state: SessionStateStore) -> None:
    """Move pre-dev13 page-local choices into the shared in-memory slot once."""

    shared_secret = _secret_from(state.get(OPENAI_API_KEY_SESSION_KEY))
    if shared_secret is not None:
        # Callers and older integrations may still inject a plain string via
        # the compatibility key.  Normalize it on every read/transition so a
        # credential can never linger in ``repr(st.session_state)``.
        state[OPENAI_API_KEY_SESSION_KEY] = shared_secret
    else:
        legacy_secret = _secret_from(_first_non_blank(state, _LEGACY_API_KEY_KEYS))
        if legacy_secret is not None:
            state[OPENAI_API_KEY_SESSION_KEY] = legacy_secret
    if OPENAI_MODEL_PRESET_SESSION_KEY not in state:
        legacy_preset = _first_non_blank(state, _LEGACY_MODEL_PRESET_KEYS)
        if legacy_preset is not None:
            state[OPENAI_MODEL_PRESET_SESSION_KEY] = str(legacy_preset).strip()
    if OPENAI_CUSTOM_MODEL_SESSION_KEY not in state:
        legacy_custom = _first_non_blank(state, _LEGACY_CUSTOM_MODEL_KEYS)
        if legacy_custom is not None:
            state[OPENAI_CUSTOM_MODEL_SESSION_KEY] = str(legacy_custom).strip()

    # Removing the old plain-string widget values prevents credentials from
    # lingering in ``repr(st.session_state)`` after an upgrade.
    for key in (*_LEGACY_API_KEY_KEYS, *_LEGACY_MODEL_PRESET_KEYS, *_LEGACY_CUSTOM_MODEL_KEYS):
        state.pop(key, None)


def _key_retention(value: object) -> OpenAIKeyRetention:
    try:
        return OpenAIKeyRetention(str(value))
    except ValueError:
        return OpenAIKeyRetention.SESSION


def set_openai_key_retention(
    state: SessionStateWriter,
    value: OpenAIKeyRetention | str,
) -> OpenAIKeyRetention:
    """Store a validated, non-secret retention preference in session state."""

    retention = _key_retention(value)
    state[OPENAI_KEY_RETENTION_SESSION_KEY] = retention.value
    return retention


def store_openai_session_key(
    state: SessionStateWriter,
    value: object,
    *,
    retention: OpenAIKeyRetention | str | None = None,
) -> bool:
    """Store one normalized secret and return whether a usable key was supplied."""

    secret = _secret_from(value)
    if secret is None:
        return False
    state[OPENAI_API_KEY_SESSION_KEY] = secret
    if retention is not None:
        set_openai_key_retention(state, retention)
    return True


def forget_openai_session_key(state: SessionStateStore) -> None:
    """Forget only the manually entered key; environment configuration is untouched."""

    state.pop(OPENAI_API_KEY_SESSION_KEY, None)
    state.pop(OPENAI_API_KEY_ENTRY_KEY, None)


def consume_openai_key_after_action(state: SessionStateStore) -> bool:
    """Forget a one-action manual key after an OpenAI attempt.

    Environment credentials are intentionally unaffected because they are not
    copied into session state and cannot be changed safely from this UI.
    """

    retention = _key_retention(
        state.get(OPENAI_KEY_RETENTION_SESSION_KEY, OpenAIKeyRetention.SESSION.value)
    )
    if retention is not OpenAIKeyRetention.ONE_ACTION:
        return False
    existed = _secret_from(state.get(OPENAI_API_KEY_SESSION_KEY)) is not None
    forget_openai_session_key(state)
    return existed


def apply_openai_session_state_transitions(state: SessionStateStore) -> None:
    """Apply pending widget-safe secret cleanup before controls are instantiated."""

    migrate_legacy_openai_session_state(state)
    if state.pop(OPENAI_FORGET_PENDING_KEY, False):
        forget_openai_session_key(state)
    if state.pop(OPENAI_API_KEY_ENTRY_CLEAR_PENDING_KEY, False):
        state.pop(OPENAI_API_KEY_ENTRY_KEY, None)


@dataclass(frozen=True, slots=True)
class OpenAISessionSettings:
    """A non-persistent view of the current shared OpenAI selection."""

    preset: OpenAIModelPreset
    custom_model: str = ""
    api_key: SecretStr | None = field(default=None, repr=False)
    environment_key_available: bool = False
    key_retention: OpenAIKeyRetention = OpenAIKeyRetention.SESSION

    @property
    def model(self) -> str:
        return resolve_openai_model(self.preset, custom_model=self.custom_model)

    @property
    def has_session_key(self) -> bool:
        return bool(self.api_key is not None and self.api_key.get_secret_value())

    @property
    def configured(self) -> bool:
        return self.has_session_key or self.environment_key_available

    @property
    def key_source_label(self) -> str:
        if self.has_session_key:
            if self.key_retention is OpenAIKeyRetention.ONE_ACTION:
                return "下一次 OpenAI 操作後自動忘記（內容已遮罩）"
            return "此瀏覽器工作階段可沿用（內容已遮罩）"
        if self.environment_key_available:
            return "由系統環境變數 OPENAI_API_KEY 提供"
        return "尚未設定"

    def runtime_config(self) -> GenerationRuntimeConfig:
        return GenerationRuntimeConfig(
            mode=GenerationMode.OPENAI,
            openai_model_preset=self.preset,
            custom_openai_model=self.custom_model,
            openai_api_key=self.api_key,
        )


def read_openai_session_settings(
    state: SessionStateStore,
    *,
    environ: Mapping[str, str] | None = None,
) -> OpenAISessionSettings:
    """Read the shared selection without making an API call or exposing the secret."""

    migrate_legacy_openai_session_state(state)
    raw_preset = state.get(
        OPENAI_MODEL_PRESET_SESSION_KEY,
        OpenAIModelPreset.GPT_5_6.value,
    )
    try:
        preset = OpenAIModelPreset(str(raw_preset))
    except ValueError:
        preset = OpenAIModelPreset.GPT_5_6
    custom_model = str(state.get(OPENAI_CUSTOM_MODEL_SESSION_KEY, "")).strip()
    environment = os.environ if environ is None else environ
    return OpenAISessionSettings(
        preset=preset,
        custom_model=custom_model,
        api_key=_secret_from(state.get(OPENAI_API_KEY_SESSION_KEY)),
        environment_key_available=bool(environment.get("OPENAI_API_KEY", "").strip()),
        key_retention=_key_retention(
            state.get(OPENAI_KEY_RETENTION_SESSION_KEY, OpenAIKeyRetention.SESSION.value)
        ),
    )


def schedule_openai_settings_navigation() -> None:
    """Navigate on the next main render without coupling feature pages to ``main``."""

    st.session_state["pending_nav"] = OPENAI_SETTINGS_NAV_TARGET


def render_openai_session_summary(
    *,
    key_prefix: str,
    disabled: bool = False,
) -> OpenAISessionSettings:
    """Render a compact, masked summary plus a link to the single settings page."""

    settings = read_openai_session_settings(st.session_state)
    with st.container(border=True):
        st.markdown("**OpenAI 共用設定**")
        try:
            model = settings.model
        except ValueError:
            model = "自訂模型尚未填完整"
        st.caption(f"模型：{model}｜API Key：{settings.key_source_label}")
        if not settings.configured:
            st.info("先到「AI 設定」放入 API Key；設定一次後，這個工作階段的生成頁都能沿用。")
        st.button(
            "前往 AI 設定",
            key=f"{key_prefix}_openai_settings",
            on_click=schedule_openai_settings_navigation,
            disabled=disabled,
        )
    return settings


__all__ = [
    "OPENAI_API_KEY_ENTRY_CLEAR_PENDING_KEY",
    "OPENAI_API_KEY_ENTRY_KEY",
    "OPENAI_API_KEY_SESSION_KEY",
    "OPENAI_CUSTOM_MODEL_ENTRY_KEY",
    "OPENAI_CUSTOM_MODEL_SESSION_KEY",
    "OPENAI_FORGET_PENDING_KEY",
    "OPENAI_KEY_RETENTION_SESSION_KEY",
    "OPENAI_MODEL_PRESET_ENTRY_KEY",
    "OPENAI_MODEL_PRESET_SESSION_KEY",
    "OPENAI_REMEMBER_SESSION_ENTRY_KEY",
    "OPENAI_SETTINGS_NAV_TARGET",
    "OpenAIKeyRetention",
    "OpenAISessionSettings",
    "apply_openai_session_state_transitions",
    "consume_openai_key_after_action",
    "forget_openai_session_key",
    "migrate_legacy_openai_session_state",
    "read_openai_session_settings",
    "render_openai_session_summary",
    "schedule_openai_settings_navigation",
    "set_openai_key_retention",
    "store_openai_session_key",
]
