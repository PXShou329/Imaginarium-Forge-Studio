"""System Health / Settings page — doctor output + backup."""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from imaginarium_forge.doctor.checks import run_all
from imaginarium_forge.ui.bootstrap import get_services
from imaginarium_forge.ui.components import page_header


def _format_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} B"
    if size_bytes < 1024**2:
        return f"{size_bytes / 1024:.1f} KiB"
    return f"{size_bytes / 1024**2:.1f} MiB"


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
    if st.button("執行系統檢查", key="run_doctor_btn"):
        results = run_all(settings)
        for result in results:
            icon = {"ok": "✅", "warn": "⚠️", "skip": "⏭️", "fail": "⛔"}.get(result.status, "•")
            st.write(f"{icon} **{result.name}** — {result.detail}")

    st.divider()
    st.subheader("資料庫備份")
    st.write(f"資料庫：`{settings.database_path}`")
    st.write(f"備份目錄：`{settings.backups_dir}`")
    if st.button("建立備份", key="create_backup_btn"):
        try:
            manifest = services.backups.create_backup()
            st.success(
                f"已建立備份：`{Path(manifest.backup_path).name}` "
                f"（完整性：{manifest.integrity_check}，"
                f"{_format_size(manifest.size_bytes)}）"
            )
        except FileNotFoundError as exc:
            st.warning(str(exc))
