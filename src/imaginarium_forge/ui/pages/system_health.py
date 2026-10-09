"""System Health / Settings page — doctor output + backup."""

from __future__ import annotations

import streamlit as st

from imaginarium_forge.application.user_facing_errors import ErrorDataSafety
from imaginarium_forge.doctor.checks import CheckResult, run_all
from imaginarium_forge.ui.bootstrap import get_services
from imaginarium_forge.ui.components import page_header
from imaginarium_forge.ui.error_presenter import present_exception

_CHECK_LABELS: dict[str, str] = {
    "python": "Python 執行環境",
    "platform": "作業系統平台",
    "settings": "全域執行設定",
    "directories": "資料目錄",
    "sqlite_fts5": "SQLite 全文檢索",
    "database": "本機資料庫",
    "gpu": "GPU 加速",
    "ollama": "本機模型服務",
    "ollama_models": "本機模型清單",
    "ollama_loaded": "本機模型載入狀態",
}

_OK_COPY: dict[str, str] = {
    "python": "版本符合專案需求。",
    "platform": "平台資訊已取得。",
    "settings": "全域執行設定可讀取。",
    "directories": "應用程式資料目錄可寫入。",
    "sqlite_fts5": "全文檢索功能可用。",
    "database": "連線、外鍵與資料版本狀態正常。",
    "gpu": "已偵測到可用的 GPU 工具。",
    "ollama": "本機模型服務可連線。",
    "ollama_models": "本機模型清單可讀取。",
    "ollama_loaded": "本機模型載入狀態可讀取。",
}

_WARN_COPY: dict[str, str] = {
    "python": "版本與專案建議不同；部分行為尚未驗證。",
    "database": "尚待初始化或資料版本需要處理。",
    "gpu": "GPU 狀態暫時無法確認；不影響非 GPU 功能。",
    "ollama": "目前無法連線；非 AI 功能仍可使用。",
    "ollama_models": "尚無可用模型，或模型清單暫時無法讀取。",
    "ollama_loaded": "載入狀態暫時無法讀取。",
}

_SKIP_COPY: dict[str, str] = {
    "gpu": "未偵測到可選用的 GPU 工具；不影響非 GPU 功能。",
    "ollama_models": "本機模型服務不可用，已略過模型清單。",
    "ollama_loaded": "本機模型服務不可用，已略過載入狀態。",
}

_HEALTH_CHECK_DATA_SAFETY = ErrorDataSafety(
    message_zh_tw=(
        "系統檢查沒有修改故事、角色、世界觀或候選內容；既有正式內容仍保留。"
    ),
    original_preserved=True,
    completed_work_preserved=True,
)

_BACKUP_DATA_SAFETY = ErrorDataSafety(
    message_zh_tw=(
        "既有資料庫與正式內容未被覆寫；不完整的備份不會被標示為成功。"
    ),
    original_preserved=True,
    completed_work_preserved=True,
)


class _HealthCheckFailedError(RuntimeError):
    """Internal typed signal whose message never contains diagnostic payloads."""


def _format_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} B"
    if size_bytes < 1024**2:
        return f"{size_bytes / 1024:.1f} KiB"
    return f"{size_bytes / 1024**2:.1f} MiB"


def _check_label(name: str) -> str:
    """Return an allow-listed human label without reflecting diagnostic input."""

    return _CHECK_LABELS.get(name, "其他系統元件")


def _check_status_copy(result: CheckResult) -> str:
    """Project raw Doctor output into fixed, non-sensitive product copy."""

    if result.status == "ok":
        return _OK_COPY.get(result.name, "檢查已通過。")
    if result.status == "warn":
        return _WARN_COPY.get(result.name, "此項需要注意；其他功能仍可繼續使用。")
    return _SKIP_COPY.get(result.name, "此項為選用功能，本次已略過。")


def _render_check_result(result: CheckResult, *, index: int) -> None:
    label = _check_label(result.name)
    if result.status == "fail":
        present_exception(
            _HealthCheckFailedError(),
            operation_zh_tw=f"{label}健康檢查",
            key_prefix=f"system_health_check_{index}",
            data_safety=_HEALTH_CHECK_DATA_SAFETY,
        )
        return

    icon = {"ok": "✅", "warn": "⚠️", "skip": "⏭️"}.get(result.status, "⚠️")
    st.write(f"{icon} **{label}** — {_check_status_copy(result)}")


def render() -> None:
    page_header(
        "System Health｜系統狀態與備份",
        "檢查本機執行環境、模型端點與資料庫，並建立可驗證的 SQLite 安全備份。",
        eyebrow="Operations · Local only",
        badges=(("無自動下載", "teal"), ("備份完整性檢查", "")),
    )
    services = get_services()
    settings = services.settings

    st.subheader("環境檢查")
    st.caption("此頁不會自動下載任何模型。")
    if st.button("執行系統檢查", key="run_doctor_btn", type="primary"):
        try:
            results = run_all(settings)
        except Exception as exc:
            present_exception(
                exc,
                operation_zh_tw="執行系統健康檢查",
                key_prefix="system_health_doctor",
                data_safety=_HEALTH_CHECK_DATA_SAFETY,
            )
        else:
            for index, result in enumerate(results):
                _render_check_result(result, index=index)

    st.divider()
    st.subheader("資料庫備份")
    st.write("資料庫：本機 SQLite 資料庫（實際路徑不在一般介面顯示）")
    st.write("備份位置：應用程式管理的本機備份資料夾")
    if st.button("建立備份", key="create_backup_btn", type="primary"):
        try:
            manifest = services.backups.create_backup()
            if manifest.integrity_check != "ok":
                raise _HealthCheckFailedError()
            st.success(
                "已建立本機備份（完整性檢查已通過，"
                f"{_format_size(manifest.size_bytes)}）。"
            )
        except Exception as exc:
            present_exception(
                exc,
                operation_zh_tw="建立資料庫備份",
                key_prefix="system_health_backup",
                data_safety=_BACKUP_DATA_SAFETY,
            )
