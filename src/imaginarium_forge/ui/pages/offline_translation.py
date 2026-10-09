"""Direct, lazy and memory-only bilingual translation workspace.

Opening this route reads only settings and installed-pack metadata. Native model
initialization and installation are separate explicit background actions.
"""

from __future__ import annotations

from collections.abc import Callable
from threading import Event
from typing import TYPE_CHECKING, cast

import streamlit as st

from imaginarium_forge.application.services.translation_history_service import (
    TranslationHistoryEntry,
    TranslationHistoryError,
    TranslationHistorySensitiveContentError,
    TranslationHistoryService,
)
from imaginarium_forge.config.settings import load_settings
from imaginarium_forge.domain import translation as contracts
from imaginarium_forge.ui import offline_translation_jobs as jobs
from imaginarium_forge.ui.components import page_header
from imaginarium_forge.ui.copyable_prompt import build_uncached_copy_button_html
from imaginarium_forge.ui.translation_history import HISTORY_ENABLED_KEY, render_translation_history

if TYPE_CHECKING:
    from imaginarium_forge.application.services.translation.pack_manager import (
        TranslationPackManager,
    )

PAGE_KEY = "Offline Translation"
PAGE_LABEL = "離線翻譯工作台"

_PREFIX = "offline_translation"
_SOURCE = f"{_PREFIX}_source"
_TARGET = f"{_PREFIX}_target"
_DIRECTION = f"{_PREFIX}_direction"
_GLOSSARY = f"{_PREFIX}_glossary"
_JOB = f"{_PREFIX}_job"
_LAST = f"{_PREFIX}_last"
_ACTION = f"{_PREFIX}_pending_action"
_UNDO = f"{_PREFIX}_undo"
_ERROR = f"{_PREFIX}_error"
_RESULT = f"{_PREFIX}_result"
_SUBMITTED = f"{_PREFIX}_submitted"
_HISTORY_NOTICE = f"{_PREFIX}_history_notice"
_HISTORY_LOAD = f"{_PREFIX}_history_load"
_LABELS = {
    "zh_tw_to_en": "繁體中文 → English",
    "en_to_zh_tw": "English → 繁體中文",
}


class _TranslationTaskPendingError(RuntimeError):
    """A session still owns a running task or an unconsumed terminal result."""

    def __init__(self) -> None:
        super().__init__("local translation result is still pending")


def _manager() -> jobs.LocalTaskManager[contracts.OfflineTranslationResult]:
    current = st.session_state.get(_JOB)
    if not isinstance(current, jobs.LocalTaskManager):
        current = jobs.LocalTaskManager[contracts.OfflineTranslationResult]()
        st.session_state[_JOB] = current
    return cast("jobs.LocalTaskManager[contracts.OfflineTranslationResult]", current)


def _pack_manager() -> TranslationPackManager:
    from imaginarium_forge.application.services.translation.pack_manager import (
        TranslationPackManager,
    )

    return TranslationPackManager(load_settings().translation_packs_dir)


def _remember_text() -> None:
    for key in (_SOURCE, _TARGET, _DIRECTION, _GLOSSARY):
        if key in st.session_state:
            st.session_state[f"{key}_saved"] = st.session_state[key]


def _queue_action(action: str) -> None:
    _remember_text()
    st.session_state[_ACTION] = action


def _sync_state() -> None:
    for key, default in (
        (_SOURCE, ""),
        (_TARGET, ""),
        (_DIRECTION, "zh_tw_to_en"),
        (_GLOSSARY, ""),
    ):
        if key not in st.session_state:
            st.session_state[key] = st.session_state.get(f"{key}_saved", default)
    history_entry = st.session_state.pop(_HISTORY_LOAD, None)
    if isinstance(history_entry, TranslationHistoryEntry):
        st.session_state[_UNDO] = tuple(
            str(st.session_state[key]) for key in (_SOURCE, _TARGET, _DIRECTION)
        )
        st.session_state[_SOURCE] = history_entry.source_text
        st.session_state[_TARGET] = history_entry.target_text
        st.session_state[_DIRECTION] = history_entry.direction
        st.session_state.pop(_LAST, None)
        st.session_state.pop(_RESULT, None)
        st.session_state.pop(_ERROR, None)
    action = st.session_state.pop(_ACTION, None)
    if action:
        previous = tuple(str(st.session_state[key]) for key in (_SOURCE, _TARGET, _DIRECTION))
        if action == "undo":
            restored = st.session_state.pop(_UNDO, None)
            if isinstance(restored, tuple) and len(restored) == 3:
                (
                    st.session_state[_SOURCE],
                    st.session_state[_TARGET],
                    st.session_state[_DIRECTION],
                ) = restored
        else:
            st.session_state[_UNDO] = previous
            if action == "swap":
                st.session_state[_SOURCE], st.session_state[_TARGET] = previous[1], previous[0]
                st.session_state[_DIRECTION] = (
                    "en_to_zh_tw" if previous[2] == "zh_tw_to_en" else "zh_tw_to_en"
                )
            elif action == "clear":
                st.session_state[_SOURCE] = ""
                st.session_state[_TARGET] = ""
                st.session_state.pop(_LAST, None)
                st.session_state.pop(_RESULT, None)
                st.session_state.pop(_ERROR, None)
    terminal = _manager().consume()
    if terminal is not None:
        st.session_state[f"{_PREFIX}_replace_target"] = False
        st.session_state[_LAST] = terminal.status.value
        if terminal.result is not None:
            st.session_state[_RESULT] = terminal.result
            st.session_state[_TARGET] = terminal.result.translated_text
        elif terminal.progress.partial_text:
            st.session_state[_TARGET] = terminal.progress.partial_text
        if terminal.status is jobs.LocalTaskStatus.FAILED:
            st.session_state[_ERROR] = terminal.incident_id
        else:
            st.session_state.pop(_ERROR, None)
        submitted = st.session_state.pop(_SUBMITTED, None)
        if isinstance(submitted, tuple) and len(submitted) == 3 and submitted[2]:
            try:
                _history_service().save(
                    source_text=submitted[0],
                    direction=submitted[1],
                    target_text=(
                        terminal.result.translated_text
                        if terminal.result is not None
                        else terminal.progress.partial_text
                    ),
                    status=terminal.status.value,
                )
            except TranslationHistorySensitiveContentError:
                st.session_state[_HISTORY_NOTICE] = (
                    "文字疑似含有憑證；翻譯仍保留，但未寫入本機歷史。"
                )
            except TranslationHistoryError:
                st.session_state[_HISTORY_NOTICE] = (
                    "翻譯仍保留，但本機歷史保存失敗；請先下載需要的文字。"
                )
            else:
                st.session_state[_HISTORY_NOTICE] = "已依你的選擇保存本機翻譯歷史。"
    _remember_text()


def _history_service() -> TranslationHistoryService:
    return TranslationHistoryService(load_settings().data_dir / "translation_history")


def _load_history(entry: TranslationHistoryEntry) -> None:
    st.session_state[_HISTORY_LOAD] = entry


def _parse_terms(value: str) -> tuple[contracts.ProtectedTerm, ...]:
    terms: list[contracts.ProtectedTerm] = []
    for line in value.splitlines():
        if not line.strip():
            continue
        left, separator, right = line.partition("=")
        if not separator or not left.strip() or not right.strip():
            raise ValueError("invalid glossary entry")
        terms.append(contracts.ProtectedTerm(zh_tw=left.strip(), en=right.strip()))
    if len(terms) > 100:
        raise ValueError("glossary limit exceeded")
    return tuple(terms)


def _start_translation(
    request: contracts.OfflineTranslationRequest,
    pack_manager: TranslationPackManager,
) -> None:
    # Capture author-approved input on the script thread, not from the worker.
    manager = _manager()
    if manager.snapshot() is not None:
        raise _TranslationTaskPendingError()
    undo = tuple(str(st.session_state[key]) for key in (_SOURCE, _TARGET, _DIRECTION))
    submitted = (
        request.source_text,
        request.direction.value,
        bool(st.session_state.get(HISTORY_ENABLED_KEY)),
    )

    def run(
        cancel: Event,
        report: Callable[[jobs.LocalTaskProgress], None],
    ) -> jobs.LocalTaskResult[contracts.OfflineTranslationResult]:
        from imaginarium_forge.application.services.translation.facade import TranslationService
        from imaginarium_forge.infrastructure.translation.ctranslate2_backend import (
            CTranslate2BackendFactory,
        )
        from imaginarium_forge.infrastructure.translation.traditional_chinese_normalizer import (
            OpenCCTraditionalChineseNormalizer,
        )

        def progress(update: contracts.TranslationProgress) -> None:
            report(
                jobs.LocalTaskProgress(
                    completed=update.completed_segments,
                    total=update.total_segments,
                    partial_text=update.partial_text,
                    stage="translating",
                )
            )

        service = TranslationService(
            pack_manager,
            CTranslate2BackendFactory(device="cpu", compute_type="int8"),
            normalizer=OpenCCTraditionalChineseNormalizer(),
        )
        result = service.translate(request, cancel=cancel, progress=progress)
        return jobs.LocalTaskResult(
            result,
            cancelled=result.status is contracts.TranslationResultStatus.CANCELLED_WITH_PARTIAL,
        )

    # Admission can reject an occupied manager or fail unexpectedly. Preserve the
    # previous submission, consent, result and undo until this start is accepted.
    manager.start(run)
    st.session_state[_UNDO] = undo
    st.session_state[_SUBMITTED] = submitted
    st.session_state.pop(_ERROR, None)
    st.session_state.pop(_RESULT, None)
    st.session_state.pop(_HISTORY_NOTICE, None)


@st.fragment(run_every=0.4)
def _poll_translation() -> None:
    snapshot = _manager().snapshot()
    if snapshot is None:
        return
    if not snapshot.active:
        st.rerun(scope="app")
    update = snapshot.progress
    ratio = update.completed / update.total if update.total else 0.0
    label = (
        f"正在翻譯：{update.completed} / {update.total} 段"
        if update.total
        else "正在驗證並載入本機模型，原文保持不變"
    )
    if snapshot.status is jobs.LocalTaskStatus.CANCEL_REQUESTED:
        label = "正在停止，會保留已完成的譯文"
    st.progress(ratio, text=label)
    if st.button(
        "停止翻譯",
        key=f"{_PREFIX}_stop",
        disabled=snapshot.status is jobs.LocalTaskStatus.CANCEL_REQUESTED,
    ):
        _manager().cancel()
        st.rerun(scope="fragment")
    if update.partial_text:
        with st.expander("查看已完成的部分", expanded=False):
            st.code(update.partial_text, language=None, wrap_lines=True)


def _render_outcome() -> None:
    status = st.session_state.get(_LAST)
    incident = st.session_state.get(_ERROR)
    if incident:
        with st.container(border=True, key="translation_error_card"):
            st.error("本機翻譯未完成；請檢查模型包與本機執行套件後重試。")
            st.write("原文保持不變，已回傳的部分譯文仍可編輯或下載。沒有改用其他翻譯服務。")
            st.caption(f"事件編號：{incident} · 若已有譯文，重試前可先下載或復原。")
            with st.expander("技術資訊（已去敏）", expanded=False):
                st.code("LOCAL_TRANSLATION_FAILED", language=None)
                st.caption("未保留原始例外、作者文字或環境內容；重新安裝有效模型包後可再次翻譯。")
    elif status == jobs.LocalTaskStatus.CANCELLED.value:
        st.warning("翻譯已停止；目前是部分譯文，不是完整結果。")
    elif status == jobs.LocalTaskStatus.COMPLETED.value:
        result = st.session_state.get(_RESULT)
        if isinstance(result, contracts.OfflineTranslationResult) and result.warnings:
            st.warning("翻譯已完成，但有需要你檢查的段落。")
        else:
            st.success("翻譯完成，可以直接編輯或複製譯文。")
    result = st.session_state.get(_RESULT)
    if isinstance(result, contracts.OfflineTranslationResult):
        if result.warnings:
            with st.expander("翻譯品質提醒", expanded=True):
                for warning in result.warnings:
                    st.write(warning.message)
        st.caption(
            f"上次翻譯 {_LABELS[result.direction.value]} · 本機模型包：{result.pack_id} · "
            f"{result.completed_segments} / "
            f"{result.total_segments} 段 · {result.elapsed_ms / 1000:.1f} 秒"
        )


def render() -> None:
    """Render passive metadata and explicit, nonblocking local actions."""
    page_header(
        PAGE_LABEL,
        "繁體中文 ↔ English · 完全離線翻譯，不需要 API Key",
        eyebrow="LOCAL LANGUAGE DESK",
    )
    _sync_state()
    pack_manager = _pack_manager()
    inspection = pack_manager.inspect()
    ready = inspection.status is contracts.PackInspectionStatus.READY
    current = _manager().snapshot()
    # A worker may finish after _sync_state checked consume. Its retained
    # terminal result still owns the editor until the script consumes it. The
    # polling fragment reruns the app before any author controls are re-enabled.
    active = current is not None
    with st.container(border=True, key="translation_pack_status"):
        if ready:
            st.caption(f"本機模型包已安裝：{inspection.pack_id} · {inspection.version}")
        elif inspection.status is contracts.PackInspectionStatus.CORRUPT:
            st.warning("本機模型包完整性有問題。請重新匯入有效模型包；原文仍可編輯。")
        elif inspection.status is contracts.PackInspectionStatus.INCOMPATIBLE:
            st.warning("本機模型包格式不相容。請匯入此版本支援的模型包；不會自動下載。")
        else:
            st.info("本機模型包尚未就緒。原文可先編輯；安裝完成後才能翻譯。")
        st.caption("不自動下載、不讀取 AI 憑證，歷史預設關閉。未啟用歷史時，關閉前請自行下載文字。")
    install_active = _render_install(pack_manager, active=active)
    if active:
        _poll_translation()
    else:
        _render_outcome()
    if notice := st.session_state.get(_HISTORY_NOTICE):
        st.caption(str(notice))

    with st.container(key="offline_translation_workspace"):
        direction = st.selectbox(
            "翻譯方向",
            options=tuple(_LABELS),
            format_func=lambda value: _LABELS[value],
            key=_DIRECTION,
            disabled=active,
            on_change=_remember_text,
        )
        direction_supported = contracts.TranslationDirection(direction) in inspection.directions
        if ready and not direction_supported:
            st.warning("目前的模型包不支援這個方向，請安裝包含此方向的模型包。")
        left, right = st.columns([1, 1], gap="large")
        with left:
            source = st.text_area(
                "原文",
                key=_SOURCE,
                height=360,
                max_chars=100_000,
                placeholder="貼上要翻譯的段落、對話或 Markdown…",
                disabled=active,
                on_change=_remember_text,
            )
            st.caption(f"原文 {len(source):,} 字元 · 翻譯不會改寫此欄")
            st.download_button(
                "下載原文",
                data=source,
                file_name="translation-source.txt",
                mime="text/plain",
                disabled=not bool(source),
                key=f"{_PREFIX}_download_source",
            )
        with right:
            target = st.text_area(
                "譯文（可直接編輯）",
                key=_TARGET,
                height=360,
                placeholder="譯文會顯示在這裡",
                disabled=active,
                on_change=_remember_text,
            )
            st.caption(f"譯文 {len(target):,} 字元")
            copy_col, download_col = st.columns([1, 1])
            with copy_col:
                st.iframe(
                    build_uncached_copy_button_html(
                        target,
                        key="translation_result_copy",
                        button_label="複製譯文",
                        help_text="複製目前編輯後的譯文",
                        plain_text=True,
                    ),
                    height=48,
                    tab_index=0,
                )
            with download_col:
                st.download_button(
                    "下載譯文",
                    data=target,
                    file_name="translation.txt",
                    mime="text/plain",
                    disabled=not bool(target),
                    key=f"{_PREFIX}_download",
                    width="stretch",
                )
        with st.expander("專有名詞與格式", expanded=False):
            glossary = st.text_area(
                "固定詞彙（每行：繁體中文=English）",
                key=_GLOSSARY,
                placeholder="星港=Starhaven\n艾琳=Irene",
                disabled=active,
                max_chars=10_000,
                on_change=_remember_text,
            )
            st.caption("程式碼、網址、電子郵件、路徑與數字會受到保護。最多 100 組固定詞彙。")
        overwrite = True
        if target.strip():
            overwrite = st.checkbox(
                "新譯文完成後替換目前譯文（可復原；原文不變）",
                key=f"{_PREFIX}_replace_target",
                disabled=active,
            )
        start_col, swap_col, clear_col, undo_col = st.columns([1.5, 1, 1, 1])
        with start_col:
            if st.button(
                "開始翻譯",
                key=f"{_PREFIX}_start",
                type="primary",
                width="stretch",
                disabled=(
                    active
                    or install_active
                    or not ready
                    or not direction_supported
                    or not source.strip()
                    or not overwrite
                ),
            ):
                try:
                    request = contracts.OfflineTranslationRequest(
                        direction=contracts.TranslationDirection(direction),
                        source_text=source,
                        protected_terms=_parse_terms(glossary),
                        preserve_markdown=True,
                    )
                    _start_translation(request, pack_manager)
                except _TranslationTaskPendingError:
                    st.warning("上一個翻譯工作仍在處理或交接結果，請稍候再試。原文與譯文保持不變。")
                except (ValueError, TypeError):
                    st.error("請檢查原文長度與詞彙格式。每行需包含一組「繁體中文=English」。")
                else:
                    st.rerun()
        with swap_col:
            st.button(
                "交換原文與譯文",
                key=f"{_PREFIX}_swap",
                width="stretch",
                disabled=active,
                on_click=_queue_action,
                args=("swap",),
            )
        with clear_col:
            st.button(
                "清空（可復原）",
                key=f"{_PREFIX}_clear",
                width="stretch",
                disabled=active,
                on_click=_queue_action,
                args=("clear",),
            )
        with undo_col:
            st.button(
                "復原上次操作",
                key=f"{_PREFIX}_undo_button",
                width="stretch",
                disabled=active or _UNDO not in st.session_state,
                on_click=_queue_action,
                args=("undo",),
            )
    _remember_text()
    render_translation_history(_history_service(), active=active, on_load=_load_history)


def _render_install(pack_manager: TranslationPackManager, *, active: bool) -> bool:
    from imaginarium_forge.ui.translation_pack_installer import render_pack_installer

    return render_pack_installer(pack_manager, translation_active=active)


__all__ = ["PAGE_KEY", "PAGE_LABEL", "render"]
