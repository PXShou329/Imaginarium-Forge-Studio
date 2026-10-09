"""Explicit, receipt-backed Story undo; session state holds confirmation only."""

from __future__ import annotations

import hashlib

import streamlit as st

from imaginarium_forge.application.services.generation.candidate_application import (
    CandidateApplicationService,
    CandidateTargetStaleError,
)
from imaginarium_forge.application.user_facing_errors import ErrorDataSafety
from imaginarium_forge.domain.generation.durable import (
    CandidateAction,
    CandidateActionKind,
    CandidateActionStatus,
    CandidateStatus,
    GenerationCandidate,
)
from imaginarium_forge.infrastructure.db.adapters.story_candidate_undo_target import (
    supports_story_undo,
)
from imaginarium_forge.ui.error_presenter import present_exception


def render_story_candidate_undo(
    application: CandidateApplicationService | None,
    candidate: GenerationCandidate,
    *,
    key_prefix: str,
) -> None:
    """Read durable history and offer a separate, confirmed inverse operation."""
    if candidate.target_type != "story_scene" or candidate.status not in {
        CandidateStatus.APPLIED, CandidateStatus.ARCHIVED,
    }:
        return
    if application is None:
        st.warning("目前介面未載入安全復原服務，請重新開啟專案；不會直接改寫正文。")
        return
    try:
        actions = application.list_actions(candidate.id)
    except Exception as exc:
        present_exception(
            exc,
            operation_zh_tw="讀取套用與復原紀錄",
            key_prefix=f"{key_prefix}_history_error",
            data_safety=ErrorDataSafety(
                message_zh_tw="這次只讀取紀錄，沒有送出復原，也沒有改寫工作草稿。",
                original_preserved=True,
            ),
            job_id=candidate.job_id,
        )
        if st.button("重新讀取套用紀錄", key=f"{key_prefix}_reload"):
            st.rerun()
        return
    if any(
        item.candidate_id != candidate.id or item.target_type != "story_scene"
        or item.target_id != candidate.target_id for item in actions
    ):
        st.error("套用紀錄與目前候選不一致，已停用復原；不會猜測或覆寫正文。")
        return
    completed = tuple(item for item in actions if item.status is CandidateActionStatus.COMPLETED)
    undone = {
        item.undo_of_action_id for item in completed
        if item.action_kind is CandidateActionKind.UNDO
    }
    with st.expander("套用與復原紀錄", expanded=False):
        if not actions:
            st.caption("尚無可確認的套用紀錄。")
        for action in actions:
            label = "復原套用" if action.action_kind is CandidateActionKind.UNDO else "建立工作草稿"
            status = {
                CandidateActionStatus.PENDING: "尚未確認完成",
                CandidateActionStatus.COMPLETED: "已完成",
                CandidateActionStatus.FAILED: "未完成",
            }[action.status]
            st.caption(f"{label}｜{status}｜{action.completed_at or action.created_at}")
    if undone:
        st.info("已復原這次套用；原候選、生成草稿及操作紀錄仍保留，沒有自動接受正文。")
    if candidate.status is not CandidateStatus.APPLIED:
        return
    eligible = tuple(
        item for item in completed if item.id not in undone and supports_story_undo(item)
    )
    if len(eligible) != 1:
        st.warning(
            "這份候選沒有唯一且包含完整版本證據的可復原紀錄（可能是舊版套用）。"
            "安全復原不可用；原草稿與候選仍保留，不會猜測舊狀態。"
        )
        return
    _render_confirmation(application, candidate, eligible[0], key_prefix=key_prefix)


def _render_confirmation(
    application: CandidateApplicationService,
    candidate: GenerationCandidate,
    action: CandidateAction,
    *,
    key_prefix: str,
) -> None:
    confirm_key = f"{key_prefix}_pending_action"
    st.caption(
        "復原只將工作草稿切回這次套用前的位置；保留所有版本，不修改已接受正文。"
        "若之後有任何場景修改，系統會拒絕復原，保護較新的內容。"
    )
    if st.button("復原這次套用", key=f"{key_prefix}_request", width="stretch"):
        st.session_state[confirm_key] = action.id
        st.rerun()
    if st.session_state.get(confirm_key) != action.id:
        return
    st.warning(
        "確認復原這次套用？工作草稿將回到套用前的版本；若原本沒有工作草稿，"
        "將回到未指定狀態。候選、生成紀錄與所有草稿都會保留。"
    )
    cancel_column, confirm_column = st.columns(2)
    if cancel_column.button("取消復原", key=f"{key_prefix}_cancel", width="stretch"):
        st.session_state.pop(confirm_key, None)
        st.rerun()
    if confirm_column.button(
        "確認復原這次套用", key=f"{key_prefix}_confirm", width="stretch",
    ):
        digest = hashlib.sha256(f"{candidate.id}:{action.id}".encode()).hexdigest()
        try:
            application.undo(
                candidate_id=candidate.id,
                action_id=action.id,
                idempotency_key=f"story-durable-undo:{digest}",
            )
        except Exception as exc:
            if isinstance(exc, CandidateTargetStaleError):
                st.warning("場景在套用後已有較新修改，已拒絕復原；不會覆蓋較新的工作草稿。")
            present_exception(
                exc,
                operation_zh_tw="復原長篇候選套用",
                key_prefix=f"{key_prefix}_error",
                data_safety=ErrorDataSafety(
                    message_zh_tw=(
                        "這次復原未被確認為成功；候選、生成紀錄與各版本仍保留。"
                        "若場景已有較新修改，不會強制覆蓋。請重新讀取持久紀錄再判斷下一步。"
                    ),
                    completed_work_preserved=True,
                ),
                job_id=candidate.job_id,
            )
            return
        st.session_state.pop(confirm_key, None)
        st.rerun()


__all__ = ["render_story_candidate_undo"]
