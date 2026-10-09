"""Scan local checkpoint metadata and manage prompt-profile assignments.

Scanning reads local files and supports optional SHA-256 verification.
Selectors retain record IDs so duplicate filenames remain distinguishable."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence

import streamlit as st

from imaginarium_forge.application.services.checkpoint_registry_service import (
    RegistryScanReport,
    StatusTransitionError,
)
from imaginarium_forge.domain.checkpoint.classification import CheckpointStatus
from imaginarium_forge.domain.checkpoint.metadata import (
    is_safe_checkpoint_metadata_key,
    safe_checkpoint_filename,
    safe_checkpoint_metadata_text,
)
from imaginarium_forge.ui.bootstrap import get_services
from imaginarium_forge.ui.components import page_header
from imaginarium_forge.ui.error_presenter import present_exception


def _short(record_id: str) -> str:
    return record_id[:8]


def readable_metadata_rows(
    metadata: object,
    *,
    max_rows: int = 200,
    max_value_chars: int = 500,
) -> tuple[list[dict[str, str]], bool]:
    """Flatten untrusted checkpoint metadata into bounded, readable rows.

    The registry intentionally exposes local safetensors metadata, but a raw
    JSON renderer makes nested, attacker-controlled values look like product
    diagnostics. A bounded table keeps the information inspectable without
    presenting an unstructured payload or allowing it to dominate the page.
    """

    if max_rows < 1 or max_value_chars < 1:
        raise ValueError("metadata display bounds must be positive")

    rows: list[dict[str, str]] = []
    truncated = False
    row_limit_reached = False

    def append(path: str, kind: str, value: str) -> None:
        nonlocal row_limit_reached, truncated
        if len(rows) >= max_rows:
            truncated = True
            row_limit_reached = True
            return
        safe_value = safe_checkpoint_metadata_text(path, value)
        if safe_value is None:
            truncated = True
            return
        normalized = safe_value.replace("\r", " ").replace("\n", " ")
        if len(normalized) > max_value_chars:
            normalized = f"{normalized[:max_value_chars]}…"
        rows.append({"欄位": path or "（根節點）", "類型": kind, "值": normalized})

    def visit(value: object, path: str) -> None:
        nonlocal truncated
        if row_limit_reached:
            return
        if isinstance(value, Mapping):
            if not value:
                append(path, "物件", "（空）")
                return
            for key in sorted(value, key=lambda item: str(item)):
                child = f"{path}.{key}" if path else str(key)
                if not is_safe_checkpoint_metadata_key(child):
                    truncated = True
                    continue
                visit(value[key], child)
            return
        if isinstance(value, Sequence) and not isinstance(
            value,
            (str, bytes, bytearray),
        ):
            if not value:
                append(path, "清單", "（空）")
                return
            for index, item in enumerate(value):
                visit(item, f"{path}[{index}]")
            return
        if value is None:
            append(path, "空值", "—")
        elif isinstance(value, bool):
            append(path, "布林", "是" if value else "否")
        elif isinstance(value, (int, float)):
            append(path, "數值", str(value))
        else:
            append(path, "文字", str(value))

    visit(metadata, "")
    return rows, truncated


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
        st.caption(
            f"已設定 {len(roots)} 個本機掃描根目錄；為保護本機資訊，不顯示完整路徑。"
        )
    else:
        st.warning(
            "尚未設定掃描根目錄。請設定環境變數 IMF_CHECKPOINT_ROOTS"
            "（多個路徑以系統路徑分隔符分隔）後重新啟動。"
        )

    report: RegistryScanReport | None
    if st.button(
        "執行唯讀掃描",
        key="registry_scan_btn",
        type="primary",
        disabled=not roots,
    ):
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
        for error_index, _error in enumerate(report.errors):
            # The legacy scan report only carries exception text. Never echo
            # that path-bearing string; present a new sanitized UI incident.
            present_exception(
                RuntimeError(),
                operation_zh_tw="掃描模型檔案",
                key_prefix=f"checkpoint_scan_{error_index}",
            )

    # ------------------------------------------------------------ records
    st.subheader("Checkpoint 紀錄")
    assets = services.checkpoints.list_all()
    if not assets:
        st.caption("目前沒有任何紀錄。設定根目錄後執行掃描。")
        return

    by_id = {a.id: a for a in assets}
    labels: dict[str | None, str] = {None: "（選擇一筆）"}
    labels.update(
        {
            a.id: f"{safe_checkpoint_filename(a.filename)} — {_short(a.id)}"
            for a in assets
        }
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
    st.markdown(f"### {safe_checkpoint_filename(asset.filename)}")
    info1, info2 = st.columns(2)
    with info1:
        st.caption(f"紀錄識別碼：`{_short(asset.id)}`")
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
            metadata_rows, metadata_truncated = readable_metadata_rows(metadata)
            st.dataframe(metadata_rows, width="stretch", hide_index=True)
            if metadata_truncated:
                st.caption(
                    "部分 metadata 因安全規則、長度或數量限制未顯示；"
                    "此處只保留簡短且非敏感的欄位。"
                )
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
            present_exception(
                exc,
                operation_zh_tw="計算模型檔雜湊",
                key_prefix="checkpoint_hash",
            )
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
            present_exception(
                exc,
                operation_zh_tw="更新模型指派",
                key_prefix="checkpoint_assignment",
            )
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
            "我確認以實驗證據推薦此 profile",
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
            present_exception(
                exc,
                operation_zh_tw="更新模型狀態",
                key_prefix="checkpoint_status",
            )
        else:
            st.success("狀態已更新。")
            st.rerun()
