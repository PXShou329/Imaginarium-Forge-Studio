"""Explicit unit-only author decisions; this panel never invokes a provider."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from typing import Any

import streamlit as st

from imaginarium_forge.application.services.durable_generation.contracts import (
    LENGTH_ASSURANCE_STATE_KEY,
)
from imaginarium_forge.application.services.durable_generation.unit_resolution import (
    UNIT_RESOLUTION_KEY,
    AcceptUnitPartialCommand,
    AdjustUnitTargetCommand,
    GrantUnitContinuationCommand,
    UnitResolutionConflict,
    UnitResolutionService,
    unit_identity,
)
from imaginarium_forge.application.user_facing_errors import ErrorDataSafety
from imaginarium_forge.domain.generation.durable import (
    GenerationCandidate,
    GenerationJob,
    GenerationStep,
    JobStatus,
    StepStatus,
)


def is_unit_candidate(candidate: GenerationCandidate) -> bool:
    return UNIT_RESOLUTION_KEY in candidate.quality_report


def render_unit_resolution_panel(
    services: Any,
    job: GenerationJob,
    steps: tuple[GenerationStep, ...],
    candidates: tuple[GenerationCandidate, ...],
    selected_unit_id: str,
    *,
    run_action: Callable[..., Any],
) -> None:
    # Clear the complete decision surface when a command moves NEEDS_AUTHOR_INPUT to PAUSED.
    # Otherwise rerun deltas can retain widgets from the now-invalid prior state.
    with st.empty().container():
        _render_unit_resolution_controls(
            services,
            job,
            steps,
            candidates,
            selected_unit_id,
            run_action=run_action,
        )


def _render_unit_resolution_controls(
    services: Any,
    job: GenerationJob,
    steps: tuple[GenerationStep, ...],
    candidates: tuple[GenerationCandidate, ...],
    selected_unit_id: str,
    *,
    run_action: Callable[..., Any],
) -> None:
    resolution = UnitResolutionService(services.lifecycle)
    for decision in resolution.list_decisions(job.id):
        if decision.unit_id != selected_unit_id:
            continue
        if decision.kind == "adjust_target":
            st.info(
                f"作者目標修訂 #{decision.sequence}："
                f"此章目標 {decision.payload['target_visible_chars']:,} 字；"
                "原始章目標與整本合約仍保留，未將舊合約標為達標。"
            )
        else:
            st.caption(
                f"作者續寫授權 #{decision.sequence}："
                f"此步呼叫上限 {decision.payload['max_total_attempts']}；"
                "授權本身沒有呼叫模型，已消耗次數不重置。"
            )
    step = next((item for item in steps if item.status is not StepStatus.COMPLETED), None)
    if job.status is not JobStatus.NEEDS_AUTHOR_INPUT or step is None or not step.partial:
        return
    state = (step.output_json or {}).get(LENGTH_ASSURANCE_STATE_KEY)
    if not isinstance(state, dict) or state.get("scope") != "unit":
        return
    if state.get("next_action") != "save_partial" or state.get("unit_id") != selected_unit_id:
        return
    key = f"unit_resolution_{job.id}_{step.id}_{step.partial_sha256}"
    pin_key = key + "_target_pin"
    try:
        unit_id, title, _ = unit_identity(step)
        if pin_key not in st.session_state:
            st.session_state[pin_key] = resolution.target_snapshot(job.id, step.id)
    except UnitResolutionConflict:
        st.warning("此舊工作缺少可驗證的原場景對應；保存片段仍可查看，不會猜測目標或改寫正文。")
        return
    st.markdown(f"#### 處理已保存單章：{title}")
    st.warning("此章仍待作者決策；接受目前版本只保存這一章，不表示原篇幅合約或整本已完成。")
    reviewed = st.checkbox(
        "我已檢視此章，接受目前版本並保留原始長度與檢查證據",
        key=key + "_reviewed",
    )
    already_saved = any(
        isinstance((receipt := item.quality_report.get(UNIT_RESOLUTION_KEY)), dict)
        and receipt.get("partial_sha256") == step.partial_sha256
        and receipt.get("unit_id") == unit_id
        for item in candidates
    )
    if st.button(
        "接受目前版本（只保存此章候選）",
        key=key + "_accept",
        disabled=not reviewed or already_saved,
    ):
        digest = hashlib.sha256(f"{job.id}:{step.id}:{step.partial_sha256}".encode()).hexdigest()
        run_action(
            lambda: resolution.accept_current(
                AcceptUnitPartialCommand(
                    job_id=job.id,
                    step_id=step.id,
                    expected_job_version=job.state_version,
                    expected_step_version=step.state_version,
                    expected_partial_sha256=step.partial_sha256,
                    before_snapshot=st.session_state[pin_key],
                    idempotency_key="author-unit:" + digest,
                )
            ),
            operation_zh_tw="接受已保存的單章候選",
            key_prefix=key + "_error",
            job_id=job.id,
            retry_permitted=True,
            data_safety=ErrorDataSafety(
                message_zh_tw="原作者正文、已完成章節與保存片段不會因接受候選而改寫。",
                original_preserved=True,
                partial_preserved=True,
                completed_work_preserved=True,
            ),
        )
    if already_saved:
        st.caption("此片段已有單章候選；請於候選區明確套用到原對應場景，或保留候選。")

    st.caption(
        f"保存場景：{st.session_state[pin_key]['story_scene_id']}；套用時會再次核對場景版本。"
    )
    decision_key = key + f"_v{job.state_version}_{step.state_version}"
    pins = {
        "job_id": job.id,
        "step_id": step.id,
        "expected_job_version": job.state_version,
        "expected_step_version": step.state_version,
        "expected_partial_sha256": step.partial_sha256,
    }
    safety = ErrorDataSafety(
        message_zh_tw="保留作者正文、原合約、保存片段和已消耗次數；此操作不自動生成。",
        original_preserved=True,
        partial_preserved=True,
        completed_work_preserved=True,
    )
    used = state.get("continuations_used", 0)
    ledger = state.get("attempt_ledger", [])
    last = (
        ledger[-1] if isinstance(ledger, list) and ledger and isinstance(ledger[-1], dict) else {}
    )
    ceiling = last.get("runtime_provider_attempt_ceiling", "未提供")
    st.markdown("##### 再續寫（明確授權後仍需啟動）")
    st.caption(
        f"此步已使用 {step.attempts} 次應用層呼叫、{used} 次有效續寫。"
        f"本次只增加 1 次應用層呼叫，其內 provider 嘗試上限為 {ceiling}；"
        "總硬上限為 10 次呼叫／5 次有效續寫，原成本上限保留。"
    )
    grant_reviewed = st.checkbox(
        "我同意額外 1 次有界續寫，保留前文且不重跑完成章節",
        key=decision_key + "_grant_review",
    )
    st.button(
        "授權再續寫 1 次（不立即生成）",
        key=decision_key + "_grant",
        disabled=not grant_reviewed
        or step.attempts >= 10
        or (isinstance(used, int) and used >= 5)
        or step.requested_provider == "offline",
        on_click=lambda: run_action(
            lambda: resolution.grant_continuation(
                GrantUnitContinuationCommand.model_validate(
                    {
                        **pins,
                        "idempotency_key": "grant:"
                        + hashlib.sha256(decision_key.encode()).hexdigest(),
                    }
                )
            ),
            operation_zh_tw="保存單章額外續寫授權",
            key_prefix=decision_key + "_grant_error",
            job_id=job.id,
            retry_permitted=True,
            data_safety=safety,
            rerun=False,
        ),
    )
    st.markdown("##### 調整此章目標（保留原合約）")
    spec = resolution.effective_unit_spec(job.id, unit_id)
    st.caption(f"目前章目標：{spec.target:,} 字。只新增此章的作者修訂；整本原目標不變。")
    target = st.number_input(
        "新的單章目標（可見字元）",
        min_value=1,
        max_value=1_000_000,
        value=spec.target,
        step=100,
        key=decision_key + "_target",
    )
    target_reviewed = st.checkbox(
        "我確認只調整此章目標，不把原合約改成已達標，也不自動生成",
        key=decision_key + "_target_review",
    )
    st.button(
        "保存此章新目標並重新計量",
        key=decision_key + "_adjust",
        disabled=not target_reviewed or target == spec.target,
        on_click=lambda: run_action(
            lambda: resolution.adjust_target(
                AdjustUnitTargetCommand.model_validate(
                    {
                        **pins,
                        "target_visible_chars": int(target),
                        "idempotency_key": "target:"
                        + hashlib.sha256(f"{decision_key}:{target}".encode()).hexdigest(),
                    }
                )
            ),
            operation_zh_tw="保存單章目標修訂並本機重新計量",
            key_prefix=decision_key + "_target_error",
            job_id=job.id,
            retry_permitted=True,
            data_safety=safety,
            rerun=False,
        ),
    )
