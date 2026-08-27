"""Shared Ollama inventory, selection, and explicit pull controls."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

import streamlit as st

from imaginarium_forge.application.services.ollama_model_service import (
    OllamaModelInventory,
    OllamaModelService,
    ollama_model_ids_match,
)
from imaginarium_forge.config.settings import AppSettings
from imaginarium_forge.providers.errors import ProviderError
from imaginarium_forge.providers.ollama import normalize_ollama_model_id
from imaginarium_forge.ui.provider_diagnostics import diagnose_provider_error

OLLAMA_MODEL_SESSION_KEY: Final = "shared_ollama_model"
OLLAMA_INVENTORY_NAMES_SESSION_KEY: Final = "shared_ollama_inventory_names"
OLLAMA_INVENTORY_KNOWN_SESSION_KEY: Final = "shared_ollama_inventory_known"
OLLAMA_INVENTORY_ERROR_SESSION_KEY: Final = "shared_ollama_inventory_error"
OLLAMA_MODEL_SERVICE_SESSION_KEY: Final = "shared_ollama_model_service"

RECOMMENDED_OLLAMA_MODELS: Final = (
    "qwen3:4b-instruct-2507-q4_K_M",
    "qwen3:8b-q4_K_M",
)

_CUSTOM_CHOICE: Final = "__custom_ollama_model__"


@dataclass(frozen=True, slots=True)
class OllamaModelSelection:
    model: str
    ready: bool
    installed: bool
    inventory_known: bool


def _dedupe_models(values: tuple[str, ...]) -> tuple[str, ...]:
    result: list[str] = []
    for value in values:
        model = value.strip()
        if model and not any(ollama_model_ids_match(model, existing) for existing in result):
            result.append(model)
    return tuple(result)


def _inventory_from_state() -> OllamaModelInventory:
    raw = st.session_state.get(OLLAMA_INVENTORY_NAMES_SESSION_KEY, ())
    names = tuple(str(item) for item in raw) if isinstance(raw, (list, tuple)) else ()
    from imaginarium_forge.providers.contracts import ModelInfo

    return OllamaModelInventory(tuple(ModelInfo(name=name) for name in names if name.strip()))


def _service(settings: AppSettings) -> tuple[Any, bool]:
    injected = st.session_state.get(OLLAMA_MODEL_SERVICE_SESSION_KEY)
    if injected is not None:
        return injected, False
    return OllamaModelService(settings), True


def _close_service(service: Any, *, owned: bool) -> None:
    if owned:
        close = getattr(service, "close", None)
        if callable(close):
            close()


def _scan_inventory(settings: AppSettings) -> None:
    service, owned = _service(settings)
    try:
        inventory = service.inventory()
    except (ProviderError, RuntimeError, TypeError, ValueError) as exc:
        diagnostic = diagnose_provider_error(exc, provider_label="本機 Ollama")
        st.session_state[OLLAMA_INVENTORY_KNOWN_SESSION_KEY] = False
        st.session_state[OLLAMA_INVENTORY_ERROR_SESSION_KEY] = diagnostic.message_zh_tw
    else:
        st.session_state[OLLAMA_INVENTORY_NAMES_SESSION_KEY] = inventory.names
        st.session_state[OLLAMA_INVENTORY_KNOWN_SESSION_KEY] = True
        st.session_state.pop(OLLAMA_INVENTORY_ERROR_SESSION_KEY, None)
    finally:
        _close_service(service, owned=owned)


def _pull_selected_model(settings: AppSettings, model: str) -> None:
    service, owned = _service(settings)
    progress = st.progress(0, text=f"準備下載 {model}…")

    def update(event: Any) -> None:
        completed = getattr(event, "completed_bytes", None)
        total = getattr(event, "total_bytes", None)
        status = str(getattr(event, "status", "下載中"))
        ratio = min(1.0, completed / total) if completed and total else 0.05
        progress.progress(ratio, text=f"{status} · {model}")

    try:
        installed = service.pull_model(model, on_progress=update)
    except (ProviderError, RuntimeError, TypeError, ValueError) as exc:
        diagnostic = diagnose_provider_error(exc, provider_label="本機 Ollama")
        st.session_state[OLLAMA_INVENTORY_ERROR_SESSION_KEY] = diagnostic.message_zh_tw
    else:
        current = _inventory_from_state().names
        st.session_state[OLLAMA_INVENTORY_NAMES_SESSION_KEY] = _dedupe_models(
            (*current, str(installed.name))
        )
        st.session_state[OLLAMA_INVENTORY_KNOWN_SESSION_KEY] = True
        st.session_state.pop(OLLAMA_INVENTORY_ERROR_SESSION_KEY, None)
        st.session_state["shared_ollama_download_notice"] = (
            f"{installed.name} 已由 Ollama 下載並重新驗證。"
            "請再按一次生成；系統不會自動送出創作內容。"
        )
        progress.progress(1.0, text=f"{installed.name} 下載完成")
    finally:
        _close_service(service, owned=owned)


def render_ollama_model_selector(
    settings: AppSettings,
    *,
    key_prefix: str,
    disabled: bool = False,
    allow_unverified: bool = False,
) -> OllamaModelSelection:
    """Render a reusable selector; downloads occur only on an explicit Yes."""

    notice = st.session_state.pop("shared_ollama_download_notice", None)
    if isinstance(notice, str) and notice:
        st.success(notice)

    reset_key = f"{key_prefix}_ollama_reset_pending"
    choice_key = f"{key_prefix}_ollama_model_choice"
    custom_key = f"{key_prefix}_ollama_custom_model"
    if st.session_state.pop(reset_key, False):
        st.session_state[OLLAMA_MODEL_SESSION_KEY] = ""
        st.session_state[choice_key] = ""
        st.session_state.pop(custom_key, None)

    inventory = _inventory_from_state()
    known = bool(st.session_state.get(OLLAMA_INVENTORY_KNOWN_SESSION_KEY, False))
    configured = str(st.session_state.get(OLLAMA_MODEL_SESSION_KEY, "")).strip()
    if not configured and settings.default_model.strip():
        configured = settings.default_model.strip()
        st.session_state[OLLAMA_MODEL_SESSION_KEY] = configured

    options = _dedupe_models((*inventory.names, *RECOMMENDED_OLLAMA_MODELS))
    if configured and not any(ollama_model_ids_match(configured, item) for item in options):
        options = (*options, configured)
    choice_options = ("", *options, _CUSTOM_CHOICE)
    if choice_key not in st.session_state:
        st.session_state[choice_key] = next(
            (item for item in options if ollama_model_ids_match(configured, item)),
            "" if not configured else _CUSTOM_CHOICE,
        )
        if configured and st.session_state[choice_key] == _CUSTOM_CHOICE:
            st.session_state[custom_key] = configured
    else:
        existing_choice = str(st.session_state.get(choice_key, ""))
        if (
            existing_choice
            and existing_choice != _CUSTOM_CHOICE
            and existing_choice not in choice_options
        ):
            canonical_choice = next(
                (
                    item
                    for item in options
                    if ollama_model_ids_match(existing_choice, item)
                ),
                None,
            )
            if canonical_choice is not None:
                st.session_state[choice_key] = canonical_choice

    def label(value: str) -> str:
        if not value:
            return "請選擇模型"
        if value == _CUSTOM_CHOICE:
            return "輸入其他模型 ID…"
        if not known:
            return value
        return f"{value}（{'已安裝' if inventory.installed(value) else '需要下載'}）"

    choice = st.selectbox(
        "Ollama 模型",
        options=choice_options,
        format_func=label,
        key=choice_key,
        disabled=disabled,
    )
    raw_model = choice
    if choice == _CUSTOM_CHOICE:
        raw_model = st.text_input(
            "自訂 Ollama 模型 ID",
            key=custom_key,
            placeholder="例如：qwen3:8b",
            disabled=disabled,
        )

    model = ""
    if str(raw_model).strip():
        try:
            model = normalize_ollama_model_id(raw_model)
        except ValueError:
            st.error("模型 ID 不可留白、含空白／控制字元，且最多 256 字元。")
        else:
            st.session_state[OLLAMA_MODEL_SESSION_KEY] = model
    else:
        st.session_state[OLLAMA_MODEL_SESSION_KEY] = ""

    scan_columns = st.columns((2, 3), vertical_alignment="center")
    if scan_columns[0].button(
        "掃描／重新整理本機模型",
        key=f"{key_prefix}_ollama_scan",
        disabled=disabled,
        use_container_width=True,
    ):
        with st.spinner("正在詢問本機 Ollama 已安裝哪些模型……"):
            _scan_inventory(settings)
        st.rerun()
    scan_columns[1].caption("掃描只讀取 Ollama 清單；不會下載、啟動服務或送出創作內容。")

    error = st.session_state.get(OLLAMA_INVENTORY_ERROR_SESSION_KEY)
    if isinstance(error, str) and error:
        st.error(error)
    if not model:
        return OllamaModelSelection("", False, False, known)
    if not known:
        if allow_unverified:
            st.caption("目前使用指定模型，尚未檢查它是否已安裝在本機。")
            return OllamaModelSelection(model, True, False, False)
        st.info("先按「掃描／重新整理本機模型」確認模型是否已安裝。")
        return OllamaModelSelection(model, False, False, False)

    if inventory.installed(model):
        st.caption(f"已可使用：本機 Ollama · {model}")
        return OllamaModelSelection(model, True, True, True)

    with st.container(border=True):
        st.warning(f"使用者本機沒有模型「{model}」。是否要進行下載？")
        st.caption(
            "只有按「是，交給 Ollama 下載」才會開始；下載由外部 Ollama 保存，"
            "可能耗用大量時間、網路與磁碟空間。"
        )
        yes, no = st.columns(2)
        if yes.button(
            "是，交給 Ollama 下載",
            key=f"{key_prefix}_ollama_pull_yes",
            type="primary",
            disabled=disabled,
            use_container_width=True,
        ):
            with st.spinner(f"Ollama 正在下載 {model}；請保持程式開啟……"):
                _pull_selected_model(settings, model)
            st.rerun()
        if no.button(
            "否，取消",
            key=f"{key_prefix}_ollama_pull_no",
            disabled=disabled,
            use_container_width=True,
        ):
            st.session_state[reset_key] = True
            st.rerun()
    return OllamaModelSelection(model, False, False, True)


__all__ = [
    "OLLAMA_INVENTORY_KNOWN_SESSION_KEY",
    "OLLAMA_INVENTORY_NAMES_SESSION_KEY",
    "OLLAMA_MODEL_SERVICE_SESSION_KEY",
    "OLLAMA_MODEL_SESSION_KEY",
    "RECOMMENDED_OLLAMA_MODELS",
    "OllamaModelSelection",
    "render_ollama_model_selector",
]
