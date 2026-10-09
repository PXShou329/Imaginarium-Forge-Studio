"""Explicit model installation controls; passive render never downloads models."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from threading import Event
from typing import TYPE_CHECKING, cast

import streamlit as st

from imaginarium_forge.domain import translation as contracts
from imaginarium_forge.ui import offline_translation_jobs as jobs

if TYPE_CHECKING:
    from imaginarium_forge.application.services.translation.pack_manager import (
        TranslationPackManager,
    )

_JOB = "translation_install_job"
_PREVIEW = "translation_install_preview"
_SOURCE = "translation_install_source_path"
_NOTICE = "translation_install_notice"


def _manager() -> jobs.LocalTaskManager[str]:
    current = st.session_state.get(_JOB)
    if not isinstance(current, jobs.LocalTaskManager):
        current = jobs.LocalTaskManager[str]()
        st.session_state[_JOB] = current
    return cast("jobs.LocalTaskManager[str]", current)


def _start_install(
    manager: TranslationPackManager,
    manifest: contracts.TranslationPackManifest,
    source: Path | None,
) -> None:
    st.session_state.pop(_NOTICE, None)

    def run(
        cancel: Event,
        report: Callable[[jobs.LocalTaskProgress], None],
    ) -> jobs.LocalTaskResult[str]:
        from imaginarium_forge.application.services.translation.errors import (
            TranslationInstallCancelled,
        )

        def progress(update: contracts.PackInstallProgress) -> None:
            report(
                jobs.LocalTaskProgress(
                    completed=update.bytes_completed,
                    total=update.total_bytes,
                    stage="verifying" if update.stage == "verifying" else "installing",
                )
            )

        try:
            if source is None:
                manager.download_and_install(manifest, cancel=cancel, progress=progress)
            else:
                manager.install_local(
                    source,
                    expected_manifest=manifest,
                    cancel=cancel,
                    progress=progress,
                )
        except TranslationInstallCancelled:
            return jobs.LocalTaskResult("cancelled", cancelled=True)
        return jobs.LocalTaskResult("installed")

    _manager().start(run)


@st.fragment(run_every=0.5)
def _poll_install() -> None:
    snapshot = _manager().snapshot()
    if snapshot is None:
        return
    if not snapshot.active:
        st.rerun(scope="app")
    progress = snapshot.progress
    ratio = progress.completed / progress.total if progress.total else 0.0
    label = "正在驗證模型包" if progress.stage == "verifying" else "正在安裝模型包"
    if snapshot.status is jobs.LocalTaskStatus.CANCEL_REQUESTED:
        label = "正在停止安裝；上一個完整模型包保持不變"
    st.progress(ratio, text=label)
    if progress.total:
        st.caption(f"{progress.completed / 1048576:.1f} / {progress.total / 1048576:.1f} MiB")
    if st.button(
        "停止安裝",
        key="translation_install_stop",
        disabled=snapshot.status is jobs.LocalTaskStatus.CANCEL_REQUESTED,
    ):
        _manager().cancel()
        st.rerun(scope="fragment")


def _read_local_manifest(source_text: str) -> tuple[contracts.TranslationPackManifest, Path]:
    source = Path(source_text).expanduser()
    if not source.is_absolute() or str(source).startswith("\\\\"):
        raise ValueError("an absolute local folder is required")
    path = source / "manifest.json"
    if path.stat().st_size > 1_000_000:
        raise ValueError("manifest is too large")
    return contracts.TranslationPackManifest.model_validate_json(path.read_bytes()), source


def render_pack_installer(
    manager: TranslationPackManager,
    *,
    translation_active: bool,
) -> bool:
    """Return whether installation is active so translation cannot start midway."""
    terminal = _manager().consume()
    if terminal is not None:
        st.session_state[_NOTICE] = (terminal.status.value, terminal.incident_id)
    snapshot = _manager().snapshot()
    active = snapshot is not None and snapshot.active
    disabled = active or translation_active
    with st.expander("模型包安裝與完整性", expanded=active):
        notice = st.session_state.get(_NOTICE)
        if isinstance(notice, tuple):
            status, incident = notice
            if status == jobs.LocalTaskStatus.COMPLETED.value:
                st.success("模型包安裝完成並已通過 SHA-256 驗證；不會自動開始翻譯。")
            elif status == jobs.LocalTaskStatus.CANCELLED.value:
                st.info("安裝已停止，上一個完整模型包仍保留。")
            else:
                st.error("安裝未完成；請檢查磁碟空間、來源和檔案完整性後重試。")
                st.caption(f"上一個完整模型包不會被這次失敗覆蓋。事件編號：{incident}")
        if active:
            _poll_install()
        st.write("請使用附有來源、授權與 SHA-256 的模型包清單。模型權重不進作品備份或 Git。")
        st.caption(
            "安裝位置可用 IMF_TRANSLATION_MODEL_PACK_DIR 指定至另一個本機磁碟，不需搬動作品。"
        )
        # The manager already owns this configured path. Display it literally;
        # preview must not resolve, probe or create a destination directory.
        st.text(f"安裝目的地：{manager.root}")
        mode = st.radio(
            "安裝來源",
            ("從本機模型包資料夾匯入", "依模型包清單下載"),
            key="translation_install_mode",
            disabled=disabled,
            horizontal=True,
        )
        if mode == "從本機模型包資料夾匯入":
            source_text = st.text_input(
                "模型包資料夾完整路徑",
                key="translation_install_local_path",
                placeholder="包含 manifest.json 的本機資料夾",
                disabled=disabled,
            )
            if st.button(
                "讀取模型包資訊", key="translation_install_preview_local", disabled=disabled
            ):
                try:
                    manifest, source_path = _read_local_manifest(source_text)
                except (OSError, ValueError):
                    st.session_state.pop(_PREVIEW, None)
                    st.error("無法讀取有效清單；請確認完整路徑、manifest.json 格式與讀取權限。")
                else:
                    st.session_state[_PREVIEW] = manifest
                    st.session_state[_SOURCE] = str(source_path)
        else:
            uploaded = st.file_uploader(
                "模型包清單（manifest.json，不是模型權重）",
                type="json",
                key="translation_install_manifest_upload",
                disabled=disabled,
            )
            if st.button(
                "檢視下載內容",
                key="translation_install_preview_download",
                disabled=disabled or uploaded is None,
            ):
                try:
                    if uploaded is None or uploaded.size > 1_000_000:
                        raise ValueError("manifest size limit")
                    manifest = contracts.TranslationPackManifest.model_validate_json(
                        uploaded.getvalue()
                    )
                    if not all(entry.download_url for entry in manifest.files):
                        raise ValueError("every download file needs an explicit URL")
                except ValueError:
                    st.session_state.pop(_PREVIEW, None)
                    st.error("此清單無法下載；每個檔案都需明確的 HTTPS 來源與 SHA-256。")
                else:
                    st.session_state[_PREVIEW] = manifest
                    st.session_state[_SOURCE] = None
        preview = st.session_state.get(_PREVIEW)
        if isinstance(preview, contracts.TranslationPackManifest):
            preview_source = st.session_state.get(_SOURCE)
            expected_mode = (
                "從本機模型包資料夾匯入" if preview_source is not None else "依模型包清單下載"
            )
            if mode != expected_mode:
                st.caption("請先檢視目前所選來源，確認授權後才能安裝。")
                return active
            if preview_source is not None:
                current_path = str(
                    Path(
                        str(st.session_state.get("translation_install_local_path", ""))
                    ).expanduser()
                )
                if current_path != preview_source:
                    st.info("資料夾路徑已變更，請重新讀取模型包資訊。")
                    return active
                st.caption(f"已確認的本機來源：{preview_source}")
            st.markdown(f"**{preview.pack_id} · {preview.version}**")
            st.write(preview.license_summary)
            st.caption(
                f"模型大小 {preview.disk_bytes / 1048576:.1f} MiB · "
                f"最低記憶體 {preview.minimum_ram_bytes / 1073741824:.1f} GiB"
            )
            st.caption("安裝暫存區需要額外空間；成功前不會取代上一個完整模型包。")
            if st.checkbox("顯示清單與檔案 SHA-256", key="translation_install_show_hashes"):
                st.text(f"清單 SHA-256：{preview.sha256}")
                for entry in preview.files:
                    st.text(f"{entry.relative_path}\n{entry.sha256} · {entry.size_bytes:,} bytes")
            for url in preview.source_urls:
                st.text(url)
            agreement = st.checkbox(
                "我已確認這份清單的來源、授權和磁碟用量，允許安裝",
                key=f"translation_install_agree_{preview.sha256[:16]}",
                disabled=disabled,
            )
            if st.button(
                "安裝此模型包",
                key="translation_install_confirm",
                disabled=disabled or not agreement,
            ):
                _start_install(
                    manager,
                    preview,
                    Path(preview_source) if preview_source is not None else None,
                )
                st.rerun()
    return active
