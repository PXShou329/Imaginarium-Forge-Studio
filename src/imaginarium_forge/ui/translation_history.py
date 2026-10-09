"""Opt-in, clearable local history controls; no history is read while disabled."""

from collections.abc import Callable

import streamlit as st

from imaginarium_forge.application.services.translation_history_service import (
    TranslationHistoryEntry,
    TranslationHistoryError,
    TranslationHistoryService,
)

HISTORY_ENABLED_KEY = "offline_translation_history_enabled"
_SAVED_ENABLED_KEY = "offline_translation_history_enabled_saved"


def _remember_enabled() -> None:
    st.session_state[_SAVED_ENABLED_KEY] = bool(st.session_state.get(HISTORY_ENABLED_KEY))


def render_translation_history(
    service: TranslationHistoryService,
    *,
    active: bool,
    on_load: Callable[[TranslationHistoryEntry], None],
) -> None:
    if HISTORY_ENABLED_KEY not in st.session_state:
        st.session_state[HISTORY_ENABLED_KEY] = bool(
            st.session_state.get(_SAVED_ENABLED_KEY, False)
        )
    with st.expander("本機翻譯歷史（預設關閉）", expanded=False):
        enabled = st.checkbox(
            "保存本機歷史：原文與譯文會寫入此電腦（最多 20 筆／2 MB）",
            key=HISTORY_ENABLED_KEY,
            disabled=active,
            on_change=_remember_enabled,
        )
        st.caption("關閉只停止新增記錄；清除可移除既有歷史。歷史不包含在普通作品備份內。")
        if st.button("清除全部本機翻譯歷史", key="translation_history_clear", disabled=active):
            try:
                service.clear()
            except TranslationHistoryError:
                st.error("未能清除本機歷史，請檢查檔案權限；目前編輯文字保持不變。")
            else:
                st.success("本機翻譯歷史已清除；目前編輯文字保持不變。")
        if not enabled:
            return
        try:
            entries = service.list_entries()
        except TranslationHistoryError:
            st.warning("本機歷史無法安全讀取；不會顯示或覆寫有問題的歷史內容。")
            return
        if not entries:
            st.caption("尚無記錄；啟用後的下一次翻譯才會保存。")
            return
        by_id = {entry.entry_id: entry for entry in entries}

        def label(entry_id: str) -> str:
            entry = by_id[entry_id]
            direction = "中→英" if entry.direction == "zh_tw_to_en" else "英→中"
            status = {"completed": "完成", "cancelled": "部分譯文", "failed": "未完成"}[
                entry.status
            ]
            return (
                f"{entry.created_at.astimezone():%m/%d %H:%M} · {direction} · {status} · "
                f"{entry.source_text[:25]}"
            )

        selected = st.selectbox(
            "選擇記錄", tuple(by_id), format_func=label, key="translation_history_choice"
        )
        if st.button(
            "載入選取記錄（替換兩側文字，可復原）",
            key="translation_history_load",
            disabled=active,
        ):
            on_load(by_id[selected])
            st.rerun()
