"""Single session-scoped OpenAI settings page."""

from __future__ import annotations

import streamlit as st

from imaginarium_forge.ui.ai_runtime import OPENAI_MODEL_PRESETS, OpenAIModelPreset
from imaginarium_forge.ui.components import page_header
from imaginarium_forge.ui.openai_session import (
    OPENAI_API_KEY_ENTRY_CLEAR_PENDING_KEY,
    OPENAI_API_KEY_ENTRY_KEY,
    OPENAI_CUSTOM_MODEL_ENTRY_KEY,
    OPENAI_CUSTOM_MODEL_SESSION_KEY,
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

PAGE_KEY = OPENAI_SETTINGS_NAV_TARGET
PAGE_LABEL = "AI 設定"

_MODEL_LABELS = {
    OpenAIModelPreset.GPT_5_6.value: "GPT-5.6（預設／Sol）",
    OpenAIModelPreset.GPT_5_6_SOL.value: "GPT-5.6 Sol",
    OpenAIModelPreset.GPT_5_6_TERRA.value: "GPT-5.6 Terra",
    OpenAIModelPreset.GPT_5_6_LUNA.value: "GPT-5.6 Luna",
    OpenAIModelPreset.CUSTOM.value: "自訂模型 ID",
}


def _apply_entered_key() -> None:
    raw = st.session_state.get(OPENAI_API_KEY_ENTRY_KEY, "")
    remember = bool(st.session_state.get(OPENAI_REMEMBER_SESSION_ENTRY_KEY, True))
    retention = (
        OpenAIKeyRetention.SESSION if remember else OpenAIKeyRetention.ONE_ACTION
    )
    if store_openai_session_key(st.session_state, raw, retention=retention):
        st.session_state["shared_openai_settings_notice"] = (
            "API Key 已套用；此瀏覽器工作階段的創作頁可重複沿用。"
            if remember
            else "API Key 已套用；下一次 OpenAI 操作完成或失敗後會自動忘記。"
        )
    else:
        st.session_state["shared_openai_settings_error"] = "請先貼上有效的 API Key。"
    st.session_state[OPENAI_API_KEY_ENTRY_CLEAR_PENDING_KEY] = True


def _forget_entered_key() -> None:
    st.session_state[OPENAI_FORGET_PENDING_KEY] = True
    st.session_state["shared_openai_settings_notice"] = "已忘記手動輸入的 API Key。"


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


def render() -> None:
    """Render model and masked credential controls; never performs an API call."""

    apply_openai_session_state_transitions(st.session_state)
    page_header(
        PAGE_LABEL,
        "API Key 與模型只要在這裡設定一次，故事、角色、靈感與圖片 Prompt 生成都能沿用。",
        eyebrow="只留在目前工作階段",
        badges=(("不寫入作品或資料庫", "teal"), ("不會在這裡呼叫 API", "amber")),
    )

    notice = st.session_state.pop("shared_openai_settings_notice", None)
    error = st.session_state.pop("shared_openai_settings_error", None)
    if isinstance(notice, str) and notice:
        st.success(notice)
    if isinstance(error, str) and error:
        st.error(error)

    with st.container(border=True):
        st.subheader("預設模型")
        st.session_state.setdefault(
            OPENAI_MODEL_PRESET_ENTRY_KEY,
            st.session_state.get(
                OPENAI_MODEL_PRESET_SESSION_KEY,
                OpenAIModelPreset.GPT_5_6.value,
            ),
        )
        preset_raw = st.selectbox(
            "OpenAI 模型",
            options=tuple(preset.value for preset in OPENAI_MODEL_PRESETS),
            format_func=lambda value: _MODEL_LABELS[value],
            key=OPENAI_MODEL_PRESET_ENTRY_KEY,
            on_change=_apply_model_choice,
            help="其他生成頁會沿用這個選擇；每次送出前仍會另外要求你同意傳送內容。",
        )
        preset = OpenAIModelPreset(preset_raw)
        if preset is OpenAIModelPreset.CUSTOM:
            st.session_state.setdefault(
                OPENAI_CUSTOM_MODEL_ENTRY_KEY,
                st.session_state.get(OPENAI_CUSTOM_MODEL_SESSION_KEY, ""),
            )
            st.text_input(
                "自訂 OpenAI 模型 ID",
                placeholder="例如：gpt-5.6",
                key=OPENAI_CUSTOM_MODEL_ENTRY_KEY,
                on_change=_apply_custom_model,
            )

    with st.container(border=True):
        st.subheader("API Key")
        current = read_openai_session_settings(st.session_state)
        if current.has_session_key:
            st.success("工作階段 API Key 已設定；內容保持遮罩，無法從介面讀回。")
        elif current.environment_key_available:
            st.success("已偵測到系統環境變數 OPENAI_API_KEY；不必再貼一次。")
        else:
            st.info("尚未設定 API Key。你仍可使用完全離線或本機 Ollama 功能。")

        st.text_input(
            "貼上或替換個人 OpenAI API Key",
            type="password",
            placeholder="輸入內容只用來套用到目前工作階段",
            key=OPENAI_API_KEY_ENTRY_KEY,
            help=(
                "按下套用後會以遮罩方式留在目前瀏覽器工作階段；"
                "不寫入本機資料庫、備份、執行紀錄、網址、作品或匯出檔。"
            ),
        )
        st.session_state.setdefault(
            OPENAI_REMEMBER_SESSION_ENTRY_KEY,
            current.key_retention is OpenAIKeyRetention.SESSION,
        )
        st.checkbox(
            "在目前這個瀏覽器工作階段的所有創作頁記住我的 OpenAI API Key",
            key=OPENAI_REMEMBER_SESSION_ENTRY_KEY,
            on_change=_apply_retention_choice,
            help=(
                "勾選後可在本工作階段重複沿用；取消勾選則只供下一次 OpenAI 操作，"
                "不論成功或失敗都會自動忘記。兩者都只存在記憶體。"
            ),
        )
        if bool(st.session_state.get(OPENAI_REMEMBER_SESSION_ENTRY_KEY, True)):
            st.caption("保留期限：只到這個瀏覽器工作階段結束；關閉或重啟程式後失效。")
        else:
            st.caption("保留期限：只到下一次 OpenAI 操作；Ollama 與離線操作不會消耗它。")
        actions = st.columns(2, vertical_alignment="bottom")
        actions[0].button(
            "套用到本工作階段",
            type="primary",
            use_container_width=True,
            key="shared_openai_apply_key",
            on_click=_apply_entered_key,
        )
        actions[1].button(
            "忘記手動輸入的 API Key",
            use_container_width=True,
            key="shared_openai_forget_key",
            on_click=_forget_entered_key,
            disabled=not current.has_session_key,
        )
        st.caption("若使用系統環境變數，本頁只會顯示已偵測；清除按鈕不會修改 Windows 環境變數。")

    configured = read_openai_session_settings(st.session_state)
    try:
        model = configured.model
    except ValueError:
        st.error("請填入有效且不含空白的自訂模型 ID。")
    else:
        st.caption(f"目前共用模型：{model}｜API Key：{configured.key_source_label}")
    st.warning(
        "設定 Key 不會自動傳送任何文字。只有你在生成頁按下操作，並勾選該次傳送同意後，"
        "該次創作上下文才會送往 OpenAI。"
    )


__all__ = ["PAGE_KEY", "PAGE_LABEL", "render"]
