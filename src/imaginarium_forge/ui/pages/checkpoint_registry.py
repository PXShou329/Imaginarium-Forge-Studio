"""Checkpoint Registry page (Gate A A-07, spec §10).

Read-only scanning UI over the Phase 2 registry service:

- shows configured scan roots (``IMF_CHECKPOINT_ROOTS``);
- runs the read-only scan and reports added/updated/skipped/missing/errors;
- lists records with size, mtime, availability (present/missing/changed),
  safetensors metadata, and optional SHA-256 with progress;
- manual profile assignment and status/usage-status editing;
- experiment-evidence count per checkpoint;
- NEVER performs any online lookup (there is no code path for one).

A-10 applies: the record selector is ID-valued with
``filename — path — short id`` labels, so duplicate filenames across roots
can never hide records.
"""

from __future__ import annotations

import json

import streamlit as st

from imaginarium_forge.application.services.checkpoint_registry_service import (
    RegistryScanReport,
    StatusTransitionError,
)
from imaginarium_forge.domain.checkpoint.classification import CheckpointStatus
from imaginarium_forge.ui.bootstrap import get_services
from imaginarium_forge.ui.components import page_header


def _short(record_id: str) -> str:
    return record_id[:8]


def render() -> None:
    page_header(
        "Checkpoint Registry｜模型檔案庫",
        "唯讀盤點本機模型檔、雜湊、metadata 與提示 profile；不會下載或執行模型。",
        eyebrow="Local assets · Registry",
        badges=(("唯讀掃描", "teal"), ("SHA-256", "")),
    )
    services = get_services()

    # ------------------------------------------------------------- roots
    roots = services.settings.checkpoint_root_paths()
    st.subheader("掃描根目錄")
    if roots:
        for root in roots:
            st.caption(f"• `{root}`")
    else:
        st.warning(
            "尚未設定掃描根目錄。請設定環境變數 IMF_CHECKPOINT_ROOTS"
            "（多個路徑以系統路徑分隔符分隔）後重新啟動。"
        )

    report: RegistryScanReport | None
    if st.button("執行唯讀掃描", key="registry_scan_btn", disabled=not roots):
        report = services.checkpoints.scan_roots(roots)
        st.session_state["registry_report"] = report
        st.rerun()

    report = st.session_state.get("registry_report")
    if report is not None:
        st.subheader("掃描結果")
        r1, r2, r3, r4 = st.columns(4)
        r1.metric("新增", len(report.added))
        r2.metric("更新", len(report.updated))
        r3.metric("跳過", len(report.skipped_outside_root) + len(report.skipped_extension))
        r4.metric("遺失", len(report.missing))
        if report.missing:
            st.warning("下列紀錄的檔案已不存在（標記為 missing，未刪除紀錄）：")
            for name in report.missing:
                st.caption(f"• {name}")
        for err in report.errors:
            st.error(err)

    # ------------------------------------------------------------ records
    st.subheader("Checkpoint 紀錄")
    assets = services.checkpoints.list_all()
    if not assets:
        st.caption("目前沒有任何紀錄。設定根目錄後執行掃描。")
        return

    by_id = {a.id: a for a in assets}
    labels: dict[str | None, str] = {None: "（選擇一筆）"}
    labels.update(
        {a.id: f"{a.filename} — {a.path} — {_short(a.id)}" for a in assets}
    )
    selected_id = st.selectbox(
        "選擇紀錄",
        list(labels),
        format_func=lambda v: labels.get(v, str(v)),
        key="registry_select",
    )
    if not selected_id:
        availability_counts: dict[str, int] = {}
        for a in assets:
            availability_counts[a.availability] = (
                availability_counts.get(a.availability, 0) + 1
            )
        st.caption(
            "共 "
            + str(len(assets))
            + " 筆｜"
            + "、".join(f"{k}: {v}" for k, v in sorted(availability_counts.items()))
        )
        return

    asset = by_id[selected_id]
    st.markdown(f"### {asset.filename}")
    info1, info2 = st.columns(2)
    with info1:
        st.caption(f"路徑：`{asset.path}`")
        st.caption(f"大小：{asset.size_bytes:,} bytes")
        st.caption(f"修改時間：{asset.modified_at}")
        st.caption(f"最近掃描：{asset.last_scanned_at or '—'}")
    with info2:
        badge = {"present": "🟢", "missing": "🔴", "changed": "🟡"}.get(
            asset.availability, "·"
        )
        st.caption(f"可用性：{badge} {asset.availability}")
        st.caption(f"狀態：{asset.status.value}")
        st.caption(f"使用狀態：{asset.usage_status or '—'}")
        st.caption(f"metadata 來源：{asset.metadata_source.value}")

    metadata = json.loads(asset.local_metadata_json or "{}")
    with st.expander("safetensors metadata（不可信字串，僅顯示）", expanded=False):
        if metadata:
            st.json(metadata)
        else:
            st.caption("（無 header metadata）")

    # ------------------------------------------------------------- hash
    st.subheader("SHA-256（選配，本地計算，絕不上傳）")
    if asset.sha256:
        st.caption(f"`{asset.sha256}`（{asset.sha256_status}）")
    else:
        st.caption(f"尚未計算（{asset.sha256_status}）")
    if st.button("計算 / 更新 SHA-256", key="registry_hash_btn"):
        progress = st.progress(0.0, text="計算中…")

        def _cb(done: int, total: int) -> None:
            progress.progress(
                min(done / total, 1.0) if total else 1.0,
                text=f"計算中… {done:,}/{total:,} bytes",
            )

        try:
            digest = services.checkpoints.ensure_hash(asset.id, progress=_cb)
        except (OSError, StatusTransitionError) as exc:
            st.error(f"雜湊失敗：{exc}")
        else:
            progress.progress(1.0, text="完成")
            st.success(f"SHA-256：`{digest}`")
            st.rerun()

    # ------------------------------------------------- profile assignment
    st.subheader("Checkpoint Profile 指派")
    profiles = ["（無）", *(
        p.id for p in services.prompt_profiles.list_checkpoint_profiles()
    )]
    current = asset.assigned_profile_id or "（無）"
    chosen = st.selectbox(
        "指派 profile",
        profiles,
        index=profiles.index(current) if current in profiles else 0,
        key="registry_profile_select",
    )
    known_ids = tuple(
        p.id for p in services.prompt_profiles.list_checkpoint_profiles()
    )
    a1, a2 = st.columns(2)
    if a1.button("套用指派", key="registry_assign_btn"):
        try:
            if chosen == "（無）":
                st.error("請選擇一個 profile；移除指派請用右側按鈕。")
            else:
                services.checkpoints.assign_profile(
                    asset.id, chosen, known_profile_ids=known_ids
                )
                st.success("已更新指派。")
                st.rerun()
        except StatusTransitionError as exc:
            st.error(str(exc))
    if a2.button("移除指派", key="registry_clear_btn"):
        services.checkpoints.clear_profile_assignment(asset.id)
        st.success("已移除指派；狀態回到 unidentified。")
        st.rerun()

    # ------------------------------------------------------------ status
    st.subheader("狀態")
    evidence = services.experiments.list_experiments(checkpoint_id=asset.id)
    st.caption(f"實驗證據：{len(evidence)} 筆")
    status_options = [s.value for s in CheckpointStatus]
    new_status = st.selectbox(
        "checkpoint 狀態",
        status_options,
        index=status_options.index(asset.status.value),
        key="registry_status_select",
    )
    new_usage = st.text_input(
        "使用狀態（自由文字）", value=asset.usage_status, key="registry_usage_input"
    )
    confirm_reco = False
    if new_status == CheckpointStatus.RECOMMENDED_PROFILE.value:
        confirm_reco = st.checkbox(
            "我確認以實驗證據推薦此 profile（A2-14）",
            key="registry_confirm_reco",
        )
    if st.button("更新狀態", key="registry_status_btn"):
        try:
            services.checkpoints.set_status(
                asset.id,
                status=CheckpointStatus(new_status),
                usage_status=new_usage,
                user_confirmed_recommendation=confirm_reco,
            )
        except StatusTransitionError as exc:
            st.error(str(exc))
        else:
            st.success("狀態已更新。")
            st.rerun()
