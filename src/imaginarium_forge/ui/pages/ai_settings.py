"""Global AI preferences and secure credential management.

No network request is performed while this page renders. Connection probes
and model refreshes run from explicit buttons; selecting an installed model
explicitly revalidates its inventory and capabilities before use. Transitional session
keys stay compatible with older authoring pages without exposing an API key.
"""

from __future__ import annotations

from collections.abc import Callable

import streamlit as st
from pydantic import SecretStr

from imaginarium_forge.application.error_incidents import (
    map_and_record_user_facing_error,
)
from imaginarium_forge.application.errors import ConflictError, ValidationFailedError
from imaginarium_forge.application.ports.credentials import (
    OPENAI_CREDENTIAL_ID,
    CredentialSource,
    CredentialStatus,
)
from imaginarium_forge.application.services.ai_connection_service import ConnectionProbe
from imaginarium_forge.application.services.generation.ai_settings_service import (
    OllamaModelSelectionService,
    SelectActiveOllamaModelCommand,
)
from imaginarium_forge.application.services.ollama_model_service import (
    OllamaModelCatalogService,
    ollama_model_ids_match,
)
from imaginarium_forge.application.user_facing_errors import (
    ErrorDataSafety,
    ErrorMappingContext,
    UserFacingError,
)
from imaginarium_forge.domain.common.ids import utc_now_iso
from imaginarium_forge.domain.generation.preferences import (
    AIFallbackPolicy,
    AIProviderPreference,
    AIQualityProfile,
    AIRuntimePreferences,
    AIRuntimePreferenceUpdate,
    TaskRoutePreference,
)
from imaginarium_forge.ui.ai_runtime import OPENAI_MODEL_PRESETS, OpenAIModelPreset
from imaginarium_forge.ui.bootstrap import Services, get_services
from imaginarium_forge.ui.components import page_header
from imaginarium_forge.ui.endpoint_security import summarize_endpoint
from imaginarium_forge.ui.error_presenter import render_error_card
from imaginarium_forge.ui.ollama_session import (
    OLLAMA_INVENTORY_ERROR_SESSION_KEY,
    OLLAMA_INVENTORY_KNOWN_SESSION_KEY,
    OLLAMA_INVENTORY_NAMES_SESSION_KEY,
    render_ollama_model_selector,
)
from imaginarium_forge.ui.openai_session import (
    OPENAI_API_KEY_ENTRY_CLEAR_PENDING_KEY,
    OPENAI_API_KEY_ENTRY_KEY,
    OPENAI_CUSTOM_MODEL_ENTRY_KEY,
    OPENAI_CUSTOM_MODEL_SESSION_KEY,
    OPENAI_DEVICE_CREDENTIAL_PRESENT_SESSION_KEY,
    OPENAI_FORGET_PENDING_KEY,
    OPENAI_MODEL_PRESET_ENTRY_KEY,
    OPENAI_MODEL_PRESET_SESSION_KEY,
    OPENAI_REMEMBER_SESSION_ENTRY_KEY,
    OPENAI_SETTINGS_NAV_TARGET,
    OpenAIKeyRetention,
    apply_openai_session_state_transitions,
    read_openai_session_settings,
    set_openai_key_retention,
    store_openai_session_key,
)
from imaginarium_forge.ui.shell_state import bump_shell_popover_epoch
from imaginarium_forge.ui.writing_request_controls import (
    OLLAMA_CAPABILITY_SNAPSHOT_SESSION_KEY,
)

PAGE_KEY = OPENAI_SETTINGS_NAV_TARGET
PAGE_LABEL = "AI 設定"

_ERROR_KEY = "ai_settings_user_error"
_NOTICE_KEY = "shared_openai_settings_notice"
_CONNECTION_KEY = "ai_settings_connection_probe"
_OPENAI_MODELS_KEY = "ai_settings_openai_models"
_OLLAMA_MODELS_KEY = "ai_settings_ollama_models"
_OLLAMA_MODEL_DETAILS_KEY = "ai_settings_ollama_model_details"
_OLLAMA_REFRESHED_AT_KEY = "ai_settings_ollama_refreshed_at"
_OLLAMA_CATALOG_SERVICE_KEY = "ai_settings_ollama_catalog_service"
_OLLAMA_CHOICE_KEY = "ai_settings_ollama_installed_model"
_PREFERENCES_WIDGET_VERSION_KEY = "ai_settings_preferences_widget_version"
_PREFERENCES_WIDGET_REVISION_KEY = "ai_settings_preferences_widget_revision"
_PREFERENCES_CONFLICT_KEY = "ai_settings_preferences_conflict"
_PREFERENCES_RELOAD_PENDING_KEY = "ai_settings_preferences_reload_pending"
_PREFERENCES_ACTIVATION_SYNC_PENDING_KEY = "ai_settings_preferences_activation_sync_pending"
_OLLAMA_CATALOG_CLEANUP_ERROR_KEY = "ai_settings_ollama_catalog_cleanup_error"
_DEVICE_DELETE_CONFIRM_KEY = "ai_settings_device_delete_confirm"
_REMOTE_ACK_VERSION = "creative-engine-2.0-r2"

_TASK_LABELS = {
    "inspiration": "靈感",
    "general_draft": "一般草稿",
    "long_form": "長篇正文",
    "refinement": "修訂",
    "image_prompt": "圖片 Prompt",
}
_PROVIDER_LABELS = {
    AIProviderPreference.OFFLINE.value: "不使用 AI（Offline）",
    AIProviderPreference.OLLAMA.value: "Ollama（本機）",
    AIProviderPreference.OPENAI.value: "OpenAI（遠端）",
}
_INHERIT_PROVIDER_VALUE = ""
_TASK_PROVIDER_LABELS = {
    _INHERIT_PROVIDER_VALUE: "繼承全域 provider",
    **_PROVIDER_LABELS,
}
_QUALITY_LABELS = {
    AIQualityProfile.QUICK_IDEA.value: "快速靈感",
    AIQualityProfile.STANDARD_DRAFT.value: "標準草稿",
    AIQualityProfile.DEEP_LONG_FORM.value: "深度長篇",
    AIQualityProfile.POLISH_REVIEW.value: "潤飾審閱",
    AIQualityProfile.IMAGE_PROMPT.value: "圖片 Prompt",
}
_FALLBACK_LABELS = {
    AIFallbackPolicy.FAIL_CLOSED.value: "失敗即停止（不靜默替代）",
    AIFallbackPolicy.OLLAMA_THEN_FAIL.value: "明示改用 Ollama，否則停止",
    AIFallbackPolicy.OFFLINE_THEN_FAIL.value: "明示改用 Offline，否則停止",
}
_MODEL_LABELS = {
    OpenAIModelPreset.GPT_5_6.value: "GPT-5.6（預設／Sol）",
    OpenAIModelPreset.GPT_5_6_SOL.value: "GPT-5.6 Sol",
    OpenAIModelPreset.GPT_5_6_TERRA.value: "GPT-5.6 Terra",
    OpenAIModelPreset.GPT_5_6_LUNA.value: "GPT-5.6 Luna",
    OpenAIModelPreset.CUSTOM.value: "自訂模型 ID",
}


def _session_secret() -> SecretStr | None:
    return read_openai_session_settings(st.session_state).api_key


def _remember_notice(message: str) -> None:
    st.session_state[_NOTICE_KEY] = message


def _capture_error(error: BaseException, operation: str) -> None:
    st.session_state[_ERROR_KEY] = map_and_record_user_facing_error(
        error,
        context=ErrorMappingContext(
            operation_zh_tw=operation,
            provider_label="AI 設定",
            data_safety=ErrorDataSafety(
                message_zh_tw="既有草稿、全域設定與已保存憑證沒有被這次失敗覆寫。",
                author_input_preserved=True,
                original_preserved=True,
            ),
        ),
    )


def _capture_ollama_catalog_cleanup_error(error: BaseException) -> None:
    """Record a sanitized cleanup incident without replacing action outcome."""

    st.session_state[_OLLAMA_CATALOG_CLEANUP_ERROR_KEY] = map_and_record_user_facing_error(
        error,
        context=ErrorMappingContext(
            operation_zh_tw="關閉暫時性 Ollama 模型清單資源",
            provider_label="Ollama",
            data_safety=ErrorDataSafety(
                message_zh_tw=(
                    "既有設定或已提交的模型切換結果不會因資源關閉錯誤被覆寫；作者內容沒有被修改。"
                ),
                author_input_preserved=True,
                original_preserved=True,
            ),
        ),
    )


def _run_setting_action(operation: str, action: Callable[[], str]) -> None:
    try:
        _remember_notice(action())
        st.session_state.pop(_ERROR_KEY, None)
    except Exception as exc:
        _capture_error(exc, operation)


def _apply_entered_key() -> None:
    raw = st.session_state.get(OPENAI_API_KEY_ENTRY_KEY, "")
    remember = bool(st.session_state.get(OPENAI_REMEMBER_SESSION_ENTRY_KEY, True))
    retention = OpenAIKeyRetention.SESSION if remember else OpenAIKeyRetention.ONE_ACTION
    if store_openai_session_key(st.session_state, raw, retention=retention):
        _remember_notice(
            "API Key 已套用到目前工作階段。"
            if remember
            else "API Key 已套用；下一次 OpenAI 操作後會自動忘記。"
        )
        st.session_state.pop(_ERROR_KEY, None)
    else:
        _capture_error(ValueError("blank credential entry"), "套用工作階段 API Key")
    st.session_state[OPENAI_API_KEY_ENTRY_CLEAR_PENDING_KEY] = True


def _forget_entered_key() -> None:
    st.session_state[OPENAI_FORGET_PENDING_KEY] = True
    _remember_notice("已忘記手動輸入的工作階段 API Key。")


def _save_device_key() -> str:
    raw = st.session_state.get(OPENAI_API_KEY_ENTRY_KEY, "")
    normalized = str(raw).strip()
    if not normalized:
        raise ValueError("blank credential entry")
    get_services().credential_store.set(OPENAI_CREDENTIAL_ID, SecretStr(normalized))
    st.session_state[OPENAI_DEVICE_CREDENTIAL_PRESENT_SESSION_KEY] = True
    st.session_state[OPENAI_API_KEY_ENTRY_CLEAR_PENDING_KEY] = True
    return "API Key 已安全保存至這台裝置；完整內容不會回填到介面。"


def _delete_device_key() -> str:
    if not bool(st.session_state.get(_DEVICE_DELETE_CONFIRM_KEY, False)):
        raise ValueError("device credential deletion was not confirmed")
    get_services().credential_store.delete(OPENAI_CREDENTIAL_ID)
    st.session_state[OPENAI_DEVICE_CREDENTIAL_PRESENT_SESSION_KEY] = False
    st.session_state[_DEVICE_DELETE_CONFIRM_KEY] = False
    return "已刪除這台裝置保存的 API Key；環境變數與工作階段 Key 未被修改。"


def _apply_model_choice() -> None:
    raw = st.session_state.get(
        OPENAI_MODEL_PRESET_ENTRY_KEY,
        OpenAIModelPreset.GPT_5_6.value,
    )
    try:
        preset = OpenAIModelPreset(str(raw))
    except ValueError:
        preset = OpenAIModelPreset.GPT_5_6
    st.session_state[OPENAI_MODEL_PRESET_SESSION_KEY] = preset.value


def _apply_custom_model() -> None:
    st.session_state[OPENAI_CUSTOM_MODEL_SESSION_KEY] = str(
        st.session_state.get(OPENAI_CUSTOM_MODEL_ENTRY_KEY, "")
    ).strip()


def _apply_retention_choice() -> None:
    remember = bool(st.session_state.get(OPENAI_REMEMBER_SESSION_ENTRY_KEY, True))
    set_openai_key_retention(
        st.session_state,
        OpenAIKeyRetention.SESSION if remember else OpenAIKeyRetention.ONE_ACTION,
    )


def _model_from_legacy_controls() -> str:
    return read_openai_session_settings(st.session_state).model


def _sync_legacy_model(model: str, *, include_widget_entries: bool = True) -> None:
    preset_values = {item.value for item in OPENAI_MODEL_PRESETS}
    if model in preset_values and model != OpenAIModelPreset.CUSTOM.value:
        st.session_state[OPENAI_MODEL_PRESET_SESSION_KEY] = model
        if include_widget_entries:
            st.session_state[OPENAI_MODEL_PRESET_ENTRY_KEY] = model
        return
    st.session_state[OPENAI_MODEL_PRESET_SESSION_KEY] = OpenAIModelPreset.CUSTOM.value
    st.session_state[OPENAI_CUSTOM_MODEL_SESSION_KEY] = model
    if include_widget_entries:
        st.session_state[OPENAI_MODEL_PRESET_ENTRY_KEY] = OpenAIModelPreset.CUSTOM.value
        st.session_state[OPENAI_CUSTOM_MODEL_ENTRY_KEY] = model


def _initialize_legacy_model(preferences: AIRuntimePreferences) -> None:
    """Seed transitional controls from durable settings after a fresh restart."""

    if OPENAI_MODEL_PRESET_SESSION_KEY in st.session_state:
        return
    _sync_legacy_model(preferences.openai_model)


def _schedule_latest_preferences_reload() -> None:
    """Reload durable non-secret values before the next widget tree is built."""

    _advance_preference_widget_revision()
    st.session_state[_PREFERENCES_RELOAD_PENDING_KEY] = True
    st.session_state.pop(_PREFERENCES_CONFLICT_KEY, None)
    st.session_state.pop(_ERROR_KEY, None)


def _preference_widget_key(base: str) -> str:
    """Return a new frontend identity after a durable snapshot changes."""

    revision = int(st.session_state.get(_PREFERENCES_WIDGET_REVISION_KEY, 0))
    return base if revision <= 0 else f"{base}__snapshot_{revision}"


def _advance_preference_widget_revision() -> None:
    current = int(st.session_state.get(_PREFERENCES_WIDGET_REVISION_KEY, 0))
    st.session_state[_PREFERENCES_WIDGET_REVISION_KEY] = current + 1


def _load_latest_preference_widgets(preferences: AIRuntimePreferences) -> None:
    """Replace only persistent preference widgets with one fresh DB snapshot."""

    st.session_state[_PREFERENCES_WIDGET_VERSION_KEY] = preferences.updated_at
    st.session_state[_preference_widget_key("ai_settings_default_provider")] = (
        preferences.default_provider.value
    )
    _sync_legacy_model(preferences.openai_model)
    preset_values = {
        preset.value for preset in OPENAI_MODEL_PRESETS if preset is not OpenAIModelPreset.CUSTOM
    }
    if preferences.openai_model in preset_values:
        st.session_state.pop(OPENAI_CUSTOM_MODEL_SESSION_KEY, None)
        st.session_state.pop(OPENAI_CUSTOM_MODEL_ENTRY_KEY, None)

    st.session_state.pop(_preference_widget_key(_OLLAMA_CHOICE_KEY), None)
    st.session_state["ai_settings_quality"] = preferences.default_quality_profile.value
    st.session_state["ai_settings_language"] = preferences.default_language
    st.session_state["ai_settings_fallback"] = preferences.fallback_policy.value
    st.session_state["ai_settings_remote_enabled"] = preferences.remote_provider_enabled
    st.session_state["ai_settings_streaming"] = preferences.streaming_enabled
    st.session_state["ai_settings_auto_continue"] = preferences.auto_continue
    st.session_state["ai_settings_show_usage"] = preferences.show_token_usage

    for task_id in _TASK_LABELS:
        route = preferences.task_routes.get(task_id)
        st.session_state[f"ai_settings_route_{task_id}_enabled"] = route is not None
        st.session_state[f"ai_settings_route_{task_id}_provider"] = (
            "" if route is None or route.provider is None else route.provider.value
        )
        st.session_state[f"ai_settings_route_{task_id}_model"] = (
            "" if route is None or route.model is None else route.model
        )


def _source_label(source: CredentialSource, backend_name: str = "") -> str:
    labels = {
        CredentialSource.SESSION: "本次工作階段（內容已遮罩）",
        CredentialSource.DEVICE: backend_name or "作業系統安全憑證庫",
        CredentialSource.ENVIRONMENT: "環境變數 OPENAI_API_KEY",
        CredentialSource.UNAVAILABLE: "尚未設定",
    }
    return labels[source]


def _cache_device_credential_metadata(status: CredentialStatus) -> None:
    """Keep only a session boolean after an explicit Status-page inspection."""

    if status.available:
        st.session_state[OPENAI_DEVICE_CREDENTIAL_PRESENT_SESSION_KEY] = status.exists
    else:
        st.session_state.pop(OPENAI_DEVICE_CREDENTIAL_PRESENT_SESSION_KEY, None)


def _render_notices() -> None:
    notice = st.session_state.pop(_NOTICE_KEY, None)
    if isinstance(notice, str) and notice:
        st.success(notice)
    error = st.session_state.get(_ERROR_KEY)
    if isinstance(error, UserFacingError):
        render_error_card(error, key_prefix="ai_settings")
    cleanup_error = st.session_state.pop(_OLLAMA_CATALOG_CLEANUP_ERROR_KEY, None)
    if isinstance(cleanup_error, UserFacingError):
        st.warning(
            "暫時性模型清單資源未能正常關閉；已保存的設定與畫面同步不受影響。"
            f"去敏事件編號：{cleanup_error.incident_id}。"
        )


def _render_connection_status(services: Services) -> None:
    session = read_openai_session_settings(st.session_state)
    resolved = services.credential_resolver.resolve(
        OPENAI_CREDENTIAL_ID,
        session_override=session.api_key,
    )
    status = resolved.device_status or services.ai_connections.credential_status()
    _cache_device_credential_metadata(status)
    columns = st.columns(3)
    with columns[0].container(border=True):
        st.markdown("**OpenAI**")
        st.caption(f"憑證來源：{_source_label(resolved.source, status.backend_name)}")
        st.caption("只有明確按下測試或生成才會連線。")
    with columns[1].container(border=True):
        st.markdown("**Ollama**")
        endpoint = summarize_endpoint(services.settings.ollama_base_url)
        st.caption(f"端點：{endpoint.redacted_origin}")
        if not endpoint.valid:
            st.caption("端點設定含不支援的 URL 元件；測試與生成會安全停止，請修正設定。")
        st.caption("本機狀態只在明確測試時更新。")
    with columns[2].container(border=True):
        st.markdown("**Offline**")
        st.caption("可用｜不需 Key、網路或模型服務")
        st.caption("本機素材與非 AI 功能不受遠端設定影響。")

    probe = st.session_state.get(_CONNECTION_KEY)
    if isinstance(probe, ConnectionProbe):
        latency = "—" if probe.latency_ms is None else f"{probe.latency_ms} ms"
        st.info(
            f"最後測試：{probe.provider}｜模型 {probe.model or '未指定'}｜"
            f"{probe.checked_at}｜{latency}。下一步：確認模型可用後再到創作頁執行 AI 操作。"
        )


def _render_key_management(services: Services) -> None:
    current = read_openai_session_settings(st.session_state)
    resolved = services.credential_resolver.resolve(
        OPENAI_CREDENTIAL_ID,
        session_override=current.api_key,
    )
    status = resolved.device_status or services.ai_connections.credential_status()
    _cache_device_credential_metadata(status)

    st.markdown("#### OpenAI API Key")
    st.caption(
        f"目前執行來源：{_source_label(resolved.source, status.backend_name)}。"
        "完整 Key 永不回顯，也不寫入 SQLite、作品、備份或一般診斷。"
    )
    if not status.available:
        st.info(
            "這台裝置沒有可核准的安全 credential backend；裝置保存已停用。"
            "仍可使用 session-only、OPENAI_API_KEY、Ollama 或 Offline，不會退回 plaintext。"
        )

    st.text_input(
        "貼上或替換個人 OpenAI API Key",
        type="password",
        placeholder="輸入後選擇保存到工作階段或裝置安全憑證庫",
        key=OPENAI_API_KEY_ENTRY_KEY,
        help="輸入值在動作完成後立即從 entry widget 清除；完整內容不會回填。",
    )
    st.session_state.setdefault(
        OPENAI_REMEMBER_SESSION_ENTRY_KEY,
        current.key_retention is OpenAIKeyRetention.SESSION,
    )
    st.checkbox(
        "在目前瀏覽器工作階段沿用",
        key=OPENAI_REMEMBER_SESSION_ENTRY_KEY,
        on_change=_apply_retention_choice,
        help="取消後只保留到下一次 OpenAI 操作完成或失敗。",
    )
    if bool(st.session_state.get(OPENAI_REMEMBER_SESSION_ENTRY_KEY, True)):
        st.caption("保留期限：只到這個瀏覽器工作階段結束；程式重啟後失效。")
    else:
        st.caption("保留期限：只到下一次 OpenAI 操作；成功或失敗後都會自動忘記。")

    actions = st.columns(4)
    actions[0].button(
        "套用到本工作階段",
        type="primary",
        width="stretch",
        key="shared_openai_apply_key",
        on_click=_apply_entered_key,
    )
    actions[1].button(
        "保存到這台裝置",
        width="stretch",
        key="ai_settings_save_device_key",
        disabled=not status.available or status.exists,
        on_click=lambda: _run_setting_action("保存裝置 API Key", _save_device_key),
    )
    actions[2].button(
        "替換裝置 Key",
        width="stretch",
        key="ai_settings_replace_device_key",
        disabled=not status.available or not status.exists,
        on_click=lambda: _run_setting_action("替換裝置 API Key", _save_device_key),
    )
    actions[3].button(
        "忘記工作階段 Key",
        width="stretch",
        key="shared_openai_forget_key",
        on_click=_forget_entered_key,
        disabled=not current.has_session_key,
    )

    with st.expander("刪除裝置保存的 Key", expanded=False):
        st.warning("只刪除作業系統憑證庫中的 Key；不會修改環境變數或作者內容。")
        st.checkbox("我了解並確認刪除裝置 Key", key=_DEVICE_DELETE_CONFIRM_KEY)
        st.button(
            "刪除裝置 Key",
            key="ai_settings_delete_device_key",
            disabled=not (status.available and status.exists),
            on_click=lambda: _run_setting_action("刪除裝置 API Key", _delete_device_key),
        )


def _run_probe(services: Services, provider: str) -> None:
    def action() -> str:
        model = _model_from_legacy_controls()
        if provider == "openai":
            probe = services.ai_connections.test_connection(
                model=model,
                session_override=_session_secret(),
            )
        else:
            preferences = services.ai_runtime_preferences.get_preferences()
            probe = services.ai_connections.test_ollama_connection(
                model=preferences.ollama_model,
            )
        st.session_state[_CONNECTION_KEY] = probe
        return f"{provider} 連線測試成功；保存設定與連線結果是兩個獨立狀態。"

    _run_setting_action(f"測試 {provider} 連線", action)


def _refresh_models(services: Services, provider: str) -> None:
    def action() -> str:
        if provider == "openai":
            models = services.ai_connections.refresh_models(session_override=_session_secret())
            st.session_state[_OPENAI_MODELS_KEY] = tuple(item.name for item in models)
        else:
            try:
                models = services.ai_connections.refresh_ollama_models()
            except Exception:
                for key in (
                    _OLLAMA_MODELS_KEY,
                    _OLLAMA_MODEL_DETAILS_KEY,
                    _OLLAMA_REFRESHED_AT_KEY,
                    _preference_widget_key(_OLLAMA_CHOICE_KEY),
                    OLLAMA_INVENTORY_NAMES_SESSION_KEY,
                    OLLAMA_CAPABILITY_SNAPSHOT_SESSION_KEY,
                ):
                    st.session_state.pop(key, None)
                st.session_state[OLLAMA_INVENTORY_KNOWN_SESSION_KEY] = False
                st.session_state[OLLAMA_INVENTORY_ERROR_SESSION_KEY] = True
                raise
            names = tuple(item.name for item in models)
            st.session_state[_OLLAMA_MODELS_KEY] = names
            st.session_state[_OLLAMA_MODEL_DETAILS_KEY] = {item.name: item for item in models}
            st.session_state[_OLLAMA_REFRESHED_AT_KEY] = utc_now_iso()
            st.session_state[OLLAMA_INVENTORY_NAMES_SESSION_KEY] = names
            st.session_state[OLLAMA_INVENTORY_KNOWN_SESSION_KEY] = True
            st.session_state.pop(OLLAMA_INVENTORY_ERROR_SESSION_KEY, None)
            st.session_state.pop(_preference_widget_key(_OLLAMA_CHOICE_KEY), None)
            # Inventory refresh is explicit but does not itself prove limits.
            # Drop the prior snapshot so it cannot be applied to a changed
            # model manifest; activation performs the explicit /api/show probe.
            st.session_state.pop(OLLAMA_CAPABILITY_SNAPSHOT_SESSION_KEY, None)
        return f"已重新整理 {provider} 可用模型；沒有送出作者內容。"

    _run_setting_action(f"重新整理 {provider} 模型", action)


def _activate_ollama_model(
    services: Services,
    *,
    model_id: str,
    expected_preferences_version: str,
) -> None:
    st.session_state.pop(_OLLAMA_CATALOG_CLEANUP_ERROR_KEY, None)
    catalog = st.session_state.get(_OLLAMA_CATALOG_SERVICE_KEY)
    owned = False
    capability = None
    capability_probe_failed = False
    try:
        if catalog is None:
            catalog = OllamaModelCatalogService(services.settings)
            owned = True
        selected = OllamaModelSelectionService(
            services.lifecycle,
            catalog,
        ).select_active(
            SelectActiveOllamaModelCommand(
                model_id=model_id,
                expected_preferences_version=expected_preferences_version,
            )
        )
        inspect_capabilities = getattr(catalog, "inspect_capabilities", None)
        if callable(inspect_capabilities):
            try:
                capability = inspect_capabilities(selected.ollama_model)
            except Exception:
                # The CAS selection already succeeded. Preserve it and make
                # the missing capability evidence visible instead of falsely
                # claiming that activation failed or reusing a stale snapshot.
                capability_probe_failed = True
    except ConflictError as exc:
        st.session_state[_PREFERENCES_CONFLICT_KEY] = True
        _capture_error(exc, "切換 Ollama 模型")
        return
    except Exception as exc:
        _capture_error(exc, "切換 Ollama 模型")
        return
    finally:
        if owned and catalog is not None:
            try:
                catalog.close()
            except Exception as exc:
                _capture_ollama_catalog_cleanup_error(exc)

    st.session_state.pop(_ERROR_KEY, None)
    st.session_state.pop(_PREFERENCES_CONFLICT_KEY, None)
    known_models = tuple(
        str(item) for item in st.session_state.get(_OLLAMA_MODELS_KEY, ()) if str(item).strip()
    )
    selected_models = (
        known_models
        if any(ollama_model_ids_match(selected.ollama_model, item) for item in known_models)
        else (*known_models, selected.ollama_model)
    )
    st.session_state[_OLLAMA_MODELS_KEY] = selected_models
    st.session_state[OLLAMA_INVENTORY_NAMES_SESSION_KEY] = selected_models
    st.session_state[OLLAMA_INVENTORY_KNOWN_SESSION_KEY] = True
    st.session_state.pop(OLLAMA_INVENTORY_ERROR_SESSION_KEY, None)
    st.session_state[_OLLAMA_REFRESHED_AT_KEY] = utc_now_iso()
    if capability is None:
        st.session_state.pop(OLLAMA_CAPABILITY_SNAPSHOT_SESSION_KEY, None)
    else:
        st.session_state[OLLAMA_CAPABILITY_SNAPSHOT_SESSION_KEY] = capability.model_dump(
            mode="json"
        )
    # A browser can replay the old value for an existing widget identity after
    # a callback. Defer snapshot loading to the normal rerun and version the
    # affected widget keys so the frontend must mount the committed CAS state.
    _advance_preference_widget_revision()
    st.session_state[_PREFERENCES_ACTIVATION_SYNC_PENDING_KEY] = True
    bump_shell_popover_epoch(st.session_state)
    st.session_state[_NOTICE_KEY] = (
        f"已切換至本機 Ollama 模型 {selected.ollama_model}；"
        "設定已原子保存，沒有開始生成。"
        + (
            " 模型能力探測未完成；創作要求會使用明示的保守 AUTO 與未知能力警告。"
            if capability_probe_failed or capability is None
            else " 已保存 /api/show 能力快照供創作要求使用。"
        )
    )


def _activate_selected_ollama_model(
    services: Services,
    *,
    choice_key: str,
    expected_preferences_version: str,
) -> None:
    """Read the event's current widget value, preserving the rendered CAS version."""

    model_id = str(st.session_state.get(choice_key, "")).strip()
    if not model_id:
        return
    _activate_ollama_model(
        services,
        model_id=model_id,
        expected_preferences_version=expected_preferences_version,
    )


def _render_connection_actions(services: Services) -> None:
    st.markdown("#### 明確連線操作")
    st.caption(
        "保存不等於測試成功；下列按鈕會發出連線／模型清單請求。"
        "明確選模也會重新核對已安裝清單與模型能力；這些操作都不會生成內容。"
    )
    actions = st.columns(4)
    actions[0].button(
        "測試 OpenAI 連線",
        key="ai_settings_test_openai",
        type="primary",
        width="stretch",
        on_click=lambda: _run_probe(services, "openai"),
    )
    actions[1].button(
        "刷新 OpenAI 模型",
        key="ai_settings_refresh_openai_models",
        width="stretch",
        on_click=lambda: _refresh_models(services, "openai"),
    )
    actions[2].button(
        "測試 Ollama 連線",
        key="ai_settings_test_ollama",
        width="stretch",
        on_click=lambda: _run_probe(services, "ollama"),
    )
    actions[3].button(
        "刷新 Ollama 模型",
        key="ai_settings_refresh_ollama_models",
        width="stretch",
        on_click=lambda: _refresh_models(services, "ollama"),
    )
    openai_models = st.session_state.get(_OPENAI_MODELS_KEY, ())
    ollama_models = st.session_state.get(_OLLAMA_MODELS_KEY, ())
    if openai_models:
        st.caption(f"OpenAI 可用模型：{', '.join(openai_models[:12])}")
    if ollama_models:
        st.caption(f"Ollama 已安裝模型：{', '.join(ollama_models[:12])}")


def _task_route_controls(
    preferences: AIRuntimePreferences,
) -> None:
    with st.expander("進階任務路由", expanded=False):
        st.caption("只有啟用的任務會覆寫全域 provider/model；未啟用者繼承全域設定。")
        for task_id, label in _TASK_LABELS.items():
            current = preferences.task_routes.get(task_id)
            enabled_key = f"ai_settings_route_{task_id}_enabled"
            provider_key = f"ai_settings_route_{task_id}_provider"
            model_key = f"ai_settings_route_{task_id}_model"
            st.session_state.setdefault(enabled_key, current is not None)
            enabled = st.checkbox(f"覆寫：{label}", key=enabled_key)
            route_columns = st.columns((1, 1.5))
            if provider_key in st.session_state:
                route_columns[0].selectbox(
                    f"{label} provider",
                    options=tuple(_TASK_PROVIDER_LABELS),
                    format_func=lambda value: _TASK_PROVIDER_LABELS[value],
                    key=provider_key,
                    disabled=not enabled,
                )
            else:
                route_columns[0].selectbox(
                    f"{label} provider",
                    options=tuple(_TASK_PROVIDER_LABELS),
                    format_func=lambda value: _TASK_PROVIDER_LABELS[value],
                    key=provider_key,
                    index=(
                        list(_TASK_PROVIDER_LABELS).index(current.provider.value)
                        if current is not None and current.provider is not None
                        else 0
                    ),
                    disabled=not enabled,
                )
            if model_key in st.session_state:
                route_columns[1].text_input(
                    f"{label} model（可留白繼承）",
                    key=model_key,
                    disabled=not enabled,
                )
            else:
                route_columns[1].text_input(
                    f"{label} model（可留白繼承）",
                    value=("" if current is None or current.model is None else current.model),
                    key=model_key,
                    disabled=not enabled,
                )


def _task_routes_from_widget_state(
    preferences: AIRuntimePreferences,
) -> dict[str, TaskRoutePreference]:
    """Read only the rendered route keys while preserving hidden extensions."""

    routes = {
        task_id: route
        for task_id, route in preferences.task_routes.items()
        if task_id not in _TASK_LABELS
    }
    for task_id in _TASK_LABELS:
        current = preferences.task_routes.get(task_id)
        if not bool(
            st.session_state.get(
                f"ai_settings_route_{task_id}_enabled",
                current is not None,
            )
        ):
            continue
        raw_provider = str(st.session_state.get(f"ai_settings_route_{task_id}_provider", ""))
        provider_override = (
            None if raw_provider == _INHERIT_PROVIDER_VALUE else AIProviderPreference(raw_provider)
        )
        model_override = (
            str(st.session_state.get(f"ai_settings_route_{task_id}_model", "")).strip() or None
        )
        quality_override = None if current is None else current.quality_profile
        fallback_override = None if current is None else current.fallback_policy
        if any(
            value is not None
            for value in (
                provider_override,
                model_override,
                quality_override,
                fallback_override,
            )
        ):
            routes[task_id] = TaskRoutePreference(
                provider=provider_override,
                model=model_override,
                quality_profile=quality_override,
                fallback_policy=fallback_override,
            )
    return routes


def _save_persistent_preferences(services: Services) -> None:
    """Commit one current widget snapshot before the full app rerun begins."""

    try:
        preferences = services.ai_runtime_preferences.get_preferences()
        provider = AIProviderPreference(
            str(
                st.session_state.get(
                    _preference_widget_key("ai_settings_default_provider"),
                    preferences.default_provider.value,
                )
            )
        )
        if (
            provider is AIProviderPreference.OLLAMA
            and preferences.default_provider is not AIProviderPreference.OLLAMA
        ):
            raise ValidationFailedError("請使用已安裝模型下拉來切換 Ollama provider")
        remote_enabled = bool(
            st.session_state.get(
                "ai_settings_remote_enabled",
                preferences.remote_provider_enabled,
            )
        )
        updated = services.ai_runtime_preferences.save_preferences(
            AIRuntimePreferenceUpdate(
                default_provider=provider,
                openai_model=_model_from_legacy_controls(),
                ollama_model=preferences.ollama_model,
                default_quality_profile=AIQualityProfile(
                    str(
                        st.session_state.get(
                            "ai_settings_quality",
                            preferences.default_quality_profile.value,
                        )
                    )
                ),
                default_language=str(
                    st.session_state.get(
                        "ai_settings_language",
                        preferences.default_language,
                    )
                ),
                fallback_policy=AIFallbackPolicy(
                    str(
                        st.session_state.get(
                            "ai_settings_fallback",
                            preferences.fallback_policy.value,
                        )
                    )
                ),
                remote_provider_enabled=remote_enabled,
                remote_ack_version=_REMOTE_ACK_VERSION if remote_enabled else "",
                streaming_enabled=bool(
                    st.session_state.get(
                        "ai_settings_streaming",
                        preferences.streaming_enabled,
                    )
                ),
                auto_continue=bool(
                    st.session_state.get(
                        "ai_settings_auto_continue",
                        preferences.auto_continue,
                    )
                ),
                show_token_usage=bool(
                    st.session_state.get(
                        "ai_settings_show_usage",
                        preferences.show_token_usage,
                    )
                ),
                task_routes=_task_routes_from_widget_state(preferences),
            ),
            expected_updated_at=str(
                st.session_state.get(
                    _PREFERENCES_WIDGET_VERSION_KEY,
                    preferences.updated_at,
                )
            ),
        )
        st.session_state[_PREFERENCES_WIDGET_VERSION_KEY] = updated.updated_at
        _sync_legacy_model(updated.openai_model, include_widget_entries=False)
        st.session_state.pop(_ERROR_KEY, None)
        st.session_state.pop(_PREFERENCES_CONFLICT_KEY, None)
        bump_shell_popover_epoch(st.session_state)
        st.session_state[_NOTICE_KEY] = "全域 AI 設定已保存；這不代表任何 provider 已通過連線測試。"
    except ConflictError as exc:
        st.session_state[_PREFERENCES_CONFLICT_KEY] = True
        _capture_error(exc, "儲存全域 AI 設定")
    except Exception as exc:
        _capture_error(exc, "儲存全域 AI 設定")


def _render_persistent_preferences(services: Services) -> None:
    preferences = services.ai_runtime_preferences.get_preferences()
    reloaded_latest = bool(st.session_state.pop(_PREFERENCES_RELOAD_PENDING_KEY, False))
    if reloaded_latest:
        _load_latest_preference_widgets(preferences)
    widget_preferences_version = str(
        st.session_state.setdefault(
            _PREFERENCES_WIDGET_VERSION_KEY,
            preferences.updated_at,
        )
    )
    _initialize_legacy_model(preferences)
    st.markdown("#### 全域預設與生成偏好")
    st.caption("這些 non-secret 設定會保存到 SQLite；API Key 不屬於此資料模型。")
    if reloaded_latest:
        st.success("已載入資料庫中的最新 AI 設定；尚未自動保存或呼叫 provider。")

    provider_widget_key = _preference_widget_key("ai_settings_default_provider")
    if provider_widget_key in st.session_state:
        st.selectbox(
            "全域預設 provider",
            options=tuple(_PROVIDER_LABELS),
            format_func=lambda value: _PROVIDER_LABELS[value],
            key=provider_widget_key,
        )
    else:
        st.selectbox(
            "全域預設 provider",
            options=tuple(_PROVIDER_LABELS),
            format_func=lambda value: _PROVIDER_LABELS[value],
            index=list(_PROVIDER_LABELS).index(preferences.default_provider.value),
            key=provider_widget_key,
        )
    st.session_state.setdefault(
        OPENAI_MODEL_PRESET_ENTRY_KEY,
        st.session_state.get(
            OPENAI_MODEL_PRESET_SESSION_KEY,
            OpenAIModelPreset.GPT_5_6.value,
        ),
    )
    model_columns = st.columns(2)
    preset_raw = model_columns[0].selectbox(
        "OpenAI model",
        options=tuple(preset.value for preset in OPENAI_MODEL_PRESETS),
        format_func=lambda value: _MODEL_LABELS[value],
        key=OPENAI_MODEL_PRESET_ENTRY_KEY,
        on_change=_apply_model_choice,
    )
    if OpenAIModelPreset(preset_raw) is OpenAIModelPreset.CUSTOM:
        st.session_state.setdefault(
            OPENAI_CUSTOM_MODEL_ENTRY_KEY,
            st.session_state.get(
                OPENAI_CUSTOM_MODEL_SESSION_KEY,
                preferences.openai_model,
            ),
        )
        model_columns[0].text_input(
            "自訂 OpenAI model ID",
            key=OPENAI_CUSTOM_MODEL_ENTRY_KEY,
            on_change=_apply_custom_model,
        )
    inventory_failed = OLLAMA_INVENTORY_ERROR_SESSION_KEY in st.session_state
    raw_installed = (
        ()
        if inventory_failed
        else (
            *st.session_state.get(_OLLAMA_MODELS_KEY, ()),
            *st.session_state.get(OLLAMA_INVENTORY_NAMES_SESSION_KEY, ()),
        )
    )
    inventory_known = not inventory_failed and (
        _OLLAMA_MODELS_KEY in st.session_state
        or st.session_state.get(OLLAMA_INVENTORY_KNOWN_SESSION_KEY, False) is True
    )
    installed_models: tuple[str, ...] = ()
    for raw_model in raw_installed:
        candidate = str(raw_model).strip()
        if candidate and not any(
            ollama_model_ids_match(candidate, current) for current in installed_models
        ):
            installed_models = (*installed_models, candidate)
    current_installed = next(
        (
            model
            for model in installed_models
            if ollama_model_ids_match(model, preferences.ollama_model)
        ),
        "",
    )
    saved_ollama_model = preferences.ollama_model.strip()
    if not inventory_known:
        empty_ollama_choice = "請先刷新已安裝模型"
    elif not installed_models:
        empty_ollama_choice = "本機無已安裝模型"
    elif saved_ollama_model and not current_installed:
        empty_ollama_choice = "目前保存模型未安裝，請選擇已安裝模型"
    else:
        empty_ollama_choice = "請選擇已安裝模型"
    ollama_choice_key = _preference_widget_key(_OLLAMA_CHOICE_KEY)
    if inventory_failed:
        st.session_state.pop(ollama_choice_key, None)
    if ollama_choice_key not in st.session_state:
        st.session_state[ollama_choice_key] = current_installed
    ollama_choice = model_columns[1].selectbox(
        "已安裝的 Ollama model",
        options=("", *installed_models),
        format_func=lambda value: value or empty_ollama_choice,
        key=ollama_choice_key,
        on_change=_activate_selected_ollama_model,
        args=(services,),
        kwargs={
            "choice_key": ollama_choice_key,
            "expected_preferences_version": widget_preferences_version,
        },
        help="只列出最近一次明確刷新取得的本機模型；選取後會再次驗證並原子保存，不會生成。",
    )
    model_columns[1].button(
        "啟用此 Ollama 模型",
        key="ai_settings_activate_ollama_model",
        disabled=not bool(ollama_choice),
        width="stretch",
        on_click=_activate_selected_ollama_model,
        args=(services,),
        kwargs={
            "choice_key": ollama_choice_key,
            "expected_preferences_version": widget_preferences_version,
        },
    )
    if not inventory_known:
        model_columns[1].caption("按上方「刷新 Ollama 模型」後，已安裝模型才會出現在此處。")
    elif not installed_models:
        model_columns[1].caption("刷新完成：本機無已安裝的 Ollama 模型。")
    else:
        refreshed_at = str(st.session_state.get(_OLLAMA_REFRESHED_AT_KEY, "")).strip()
        refresh_copy = f"最近刷新：{refreshed_at}" if refreshed_at else "最近刷新：本工作階段"
        model_columns[1].caption(
            f"已安裝模型：{len(installed_models)} 個｜{refresh_copy}。"
            "選模會原子切換 provider 與 model；不會自動生成。"
        )
    if inventory_known and saved_ollama_model and not current_installed:
        model_columns[1].warning(
            f"目前保存模型：{saved_ollama_model}｜需要已安裝模型。"
            "請從清單明確選擇已安裝模型；選取後會保存並切換，但不會開始生成。"
        )
    if ollama_choice:
        details = st.session_state.get(_OLLAMA_MODEL_DETAILS_KEY, {})
        info = (
            next(
                (
                    item
                    for name, item in details.items()
                    if ollama_model_ids_match(name, ollama_choice)
                ),
                None,
            )
            if isinstance(details, dict)
            else None
        )
        size_bytes = getattr(info, "size_bytes", None)
        size_copy = (
            "Ollama API 未回報"
            if not isinstance(size_bytes, int)
            else f"{size_bytes / (1024**3):.2f} GiB"
        )
        probe = st.session_state.get(_CONNECTION_KEY)
        probe_copy = (
            f"{probe.checked_at}｜{probe.latency_ms if probe.latency_ms is not None else '—'} ms"
            if isinstance(probe, ConnectionProbe)
            and probe.provider == "ollama"
            and ollama_model_ids_match(probe.model, ollama_choice)
            else "尚未對此模型執行連線測試"
        )
        model_columns[1].caption(f"大小：{size_copy}｜最近 probe：{probe_copy}")

    with st.expander("Advanced · 自訂或下載 Ollama 模型", expanded=False):
        st.caption(
            "自訂 ID、需要下載的建議模型與明確下載確認都收在此處；一般選模只顯示已安裝模型。"
        )
        advanced_selection = render_ollama_model_selector(
            services.settings,
            key_prefix="ai_settings_download",
        )
        if advanced_selection.model and advanced_selection.installed:
            st.button(
                "啟用這個已安裝模型",
                key="ai_settings_activate_advanced_ollama_model",
                on_click=lambda: _activate_ollama_model(
                    services,
                    model_id=advanced_selection.model,
                    expected_preferences_version=widget_preferences_version,
                ),
            )

    preference_columns = st.columns(3)
    if "ai_settings_quality" in st.session_state:
        preference_columns[0].selectbox(
            "預設品質",
            options=tuple(_QUALITY_LABELS),
            format_func=lambda value: _QUALITY_LABELS[value],
            key="ai_settings_quality",
        )
    else:
        preference_columns[0].selectbox(
            "預設品質",
            options=tuple(_QUALITY_LABELS),
            format_func=lambda value: _QUALITY_LABELS[value],
            index=list(_QUALITY_LABELS).index(preferences.default_quality_profile.value),
            key="ai_settings_quality",
        )
    if "ai_settings_language" in st.session_state:
        preference_columns[1].text_input(
            "預設語言",
            key="ai_settings_language",
        )
    else:
        preference_columns[1].text_input(
            "預設語言",
            value=preferences.default_language,
            key="ai_settings_language",
        )
    if "ai_settings_fallback" in st.session_state:
        preference_columns[2].selectbox(
            "Fallback policy",
            options=tuple(_FALLBACK_LABELS),
            format_func=lambda value: _FALLBACK_LABELS[value],
            key="ai_settings_fallback",
        )
    else:
        preference_columns[2].selectbox(
            "Fallback policy",
            options=tuple(_FALLBACK_LABELS),
            format_func=lambda value: _FALLBACK_LABELS[value],
            index=list(_FALLBACK_LABELS).index(preferences.fallback_policy.value),
            key="ai_settings_fallback",
        )
    if "ai_settings_remote_enabled" in st.session_state:
        remote_enabled = st.checkbox(
            "啟用遠端 OpenAI provider",
            key="ai_settings_remote_enabled",
            help=(
                "啟用後，只有按下 AI 操作才會送出該次內容；創作途中不會重複要求授權。"
            ),
        )
    else:
        remote_enabled = st.checkbox(
            "啟用遠端 OpenAI provider",
            value=preferences.remote_provider_enabled,
            key="ai_settings_remote_enabled",
            help=(
                "啟用後，只有按下 AI 操作才會送出該次內容；創作途中不會重複要求授權。"
            ),
        )
    if remote_enabled:
        st.warning(
            "OpenAI 是遠端服務，送出內容可能包含你選定的創作上下文並可能產生費用。"
            "保存這項設定不會自動送出任何內容。"
        )
    toggle_columns = st.columns(3)
    if "ai_settings_streaming" in st.session_state:
        toggle_columns[0].checkbox("串流顯示", key="ai_settings_streaming")
    else:
        toggle_columns[0].checkbox(
            "串流顯示",
            value=preferences.streaming_enabled,
            key="ai_settings_streaming",
        )
    if "ai_settings_auto_continue" in st.session_state:
        toggle_columns[1].checkbox(
            "截斷時允許明示續寫",
            key="ai_settings_auto_continue",
        )
    else:
        toggle_columns[1].checkbox(
            "截斷時允許明示續寫",
            value=preferences.auto_continue,
            key="ai_settings_auto_continue",
        )
    if "ai_settings_show_usage" in st.session_state:
        toggle_columns[2].checkbox("顯示 token usage", key="ai_settings_show_usage")
    else:
        toggle_columns[2].checkbox(
            "顯示 token usage",
            value=preferences.show_token_usage,
            key="ai_settings_show_usage",
        )
    _task_route_controls(preferences)

    st.button(
        "儲存全域 AI 設定",
        type="primary",
        key="ai_settings_save_preferences",
        width="stretch",
        on_click=_save_persistent_preferences,
        args=(services,),
    )

    if bool(st.session_state.get(_PREFERENCES_CONFLICT_KEY, False)):
        st.warning(
            "畫面中的設定版本已過期。請先載入最新設定；系統不會自動覆寫另一個工作階段的變更。"
        )
        st.button(
            "載入最新設定",
            key="ai_settings_reload_latest_preferences",
            on_click=_schedule_latest_preferences_reload,
        )


def render() -> None:
    """Render all AI settings without implicit credential or network activity."""

    apply_openai_session_state_transitions(st.session_state)
    services = get_services()
    if bool(st.session_state.pop(_PREFERENCES_ACTIVATION_SYNC_PENDING_KEY, False)):
        _load_latest_preference_widgets(services.ai_runtime_preferences.get_preferences())
        # Keep the activation notice pending, and rebuild the entire shell once
        # after the versioned widget snapshot is installed. The pending flag
        # was already consumed, so this cannot loop.
        st.rerun()
    page_header(
        PAGE_LABEL,
        "集中管理安全憑證、provider 路由與生成偏好；本頁載入不會自動呼叫模型服務。",
        eyebrow="Creative Writing Engine 2.1",
        badges=(("Key 永不回顯", "teal"), ("連線操作需明確按下", "amber")),
    )
    _render_notices()

    with st.container(border=True):
        st.subheader("連線狀態")
        _render_connection_status(services)
        _render_connection_actions(services)

    with st.container(border=True):
        st.subheader("連線與 Key")
        _render_key_management(services)

    with st.container(border=True):
        st.subheader("預設模型與路由")
        _render_persistent_preferences(services)

    st.caption(
        "Credential precedence：session → OS credential store → environment → unavailable。"
        "OpenAI 關閉或不可用時，Ollama、Offline 與所有非 AI 功能仍可使用。"
    )


__all__ = ["PAGE_KEY", "PAGE_LABEL", "render"]
