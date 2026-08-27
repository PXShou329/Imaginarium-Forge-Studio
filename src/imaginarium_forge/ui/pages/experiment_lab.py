"""Experiment Lab page (Gate A A-08, spec §11).

Complete manual-ComfyUI experiment workflow over the Phase 2 service:

- list + ID-valued selection (A-10 labels);
- edit generation parameters (sampler/scheduler/steps/CFG/size/seed/checkpoint);
- four 1–5 scores + failure tags + notes + local output reference;
- duplicate with EXACTLY one changed variable;
- compare two or more experiments (differing fields highlighted);
- filter by checkpoint / profile hash / prompt project.

This page records; it never generates. §11.3 disclaimer is shown verbatim:
same seed across different checkpoints is not scientific equivalence.
"""

from __future__ import annotations

import streamlit as st

from imaginarium_forge.application.services.prompt_experiment_service import (
    ExperimentServiceError,
)
from imaginarium_forge.ui.bootstrap import get_services
from imaginarium_forge.ui.components import page_header

_DUPLICATE_FIELDS = (
    "sampler", "scheduler", "steps", "cfg", "width", "height",
    "seed", "checkpoint_id", "notes",
)


def _short(record_id: str) -> str:
    return record_id[:8]


def _int_or_none(raw: str) -> int | None:
    raw = raw.strip()
    return int(raw) if raw else None


def _float_or_none(raw: str) -> float | None:
    raw = raw.strip()
    return float(raw) if raw else None


def render() -> None:
    page_header(
        "生成實驗室",
        "記錄外部生成結果、評分與單一變因比較；實驗證據不會被當成科學等價保證。",
        eyebrow="人工紀錄與比較",
        badges=(("單一變因比較", "teal"), ("人工評分", "")),
    )
    st.caption("⚠ 不同 checkpoint 之間即使 seed 相同，也不構成科學等價比較。")
    services = get_services()

    # ------------------------------------------------------------ filters
    with st.expander("篩選", expanded=False):
        checkpoints = services.checkpoints.list_all()
        ck_labels: dict[str | None, str] = {None: "（全部）"}
        ck_labels.update(
            {c.id: f"{c.filename} — {_short(c.id)}" for c in checkpoints}
        )
        filter_ck = st.selectbox(
            "Checkpoint",
            list(ck_labels),
            format_func=lambda v: ck_labels.get(v, str(v)),
            key="lab_filter_ck",
        )
        project_id = st.session_state.get("selected_project_id")
        pp_labels: dict[str | None, str] = {None: "（全部）"}
        if project_id:
            pp_labels.update(
                {
                    p.id: f"{p.title} — {_short(p.id)}"
                    for p in services.prompt_projects.list_for_project(project_id)
                }
            )
        filter_pp = st.selectbox(
            "提示專案",
            list(pp_labels),
            format_func=lambda v: pp_labels.get(v, str(v)),
            key="lab_filter_pp",
        )
        filter_hash = st.text_input("Profile SHA-256（完整值）", key="lab_filter_hash")

    logs = services.experiments.list_experiments(
        checkpoint_id=filter_ck or "",
        profile_hash=filter_hash.strip(),
        prompt_project_id=filter_pp or "",
    )
    st.caption(f"共 {len(logs)} 筆實驗紀錄")
    if not logs:
        st.info("尚無實驗。於 Prompt Studio 儲存版本後按「建立實驗紀錄」。")
        return

    labels: dict[str | None, str] = {None: "（選擇一筆）"}
    labels.update(
        {
            log.id: (
                f"{log.created_at[:16]} — "
                f"{log.sampler or '未設定'}/seed {log.seed if log.seed is not None else '—'}"
                f" — {_short(log.id)}"
            )
            for log in logs
        }
    )
    selected_id = st.selectbox(
        "選擇實驗",
        list(labels),
        format_func=lambda v: labels.get(v, str(v)),
        key="lab_select",
    )
    if not selected_id:
        return
    log = next(x for x in logs if x.id == selected_id)

    st.markdown(f"### 實驗 {_short(log.id)}")
    st.caption(
        f"提示專案：{_short(log.prompt_project_id)}｜編譯結果："
        f"{_short(log.prompt_variant_id)}｜設定檔 SHA-256："
        f"`{log.resolved_profile_hash[:12]}…`"
    )
    with st.expander("提示內容（唯讀快照）", expanded=False):
        st.code(log.positive_prompt or "（空）", language="text")
        st.code(log.negative_prompt or "（空）", language="text")

    # ----------------------------------------------------- parameter edit
    st.subheader("生成參數與結果評估")
    p1, p2 = st.columns(2)
    sampler = p1.text_input("sampler", value=log.sampler, key="lab_sampler")
    scheduler = p2.text_input("scheduler", value=log.scheduler, key="lab_scheduler")
    p3, p4, p5 = st.columns(3)
    steps = p3.text_input(
        "steps", value="" if log.steps is None else str(log.steps), key="lab_steps"
    )
    cfg = p4.text_input(
        "CFG", value="" if log.cfg is None else str(log.cfg), key="lab_cfg"
    )
    seed = p5.text_input(
        "seed", value="" if log.seed is None else str(log.seed), key="lab_seed"
    )
    p6, p7 = st.columns(2)
    width = p6.text_input(
        "width", value="" if log.width is None else str(log.width), key="lab_width"
    )
    height = p7.text_input(
        "height", value="" if log.height is None else str(log.height), key="lab_height"
    )

    score_cols = st.columns(4)
    score_names = (
        ("overall_rating", "整體"),
        ("identity_score", "身分"),
        ("style_score", "風格"),
        ("instruction_adherence", "指令遵循"),
    )
    scores: dict[str, int | None] = {}
    for col, (field, label) in zip(score_cols, score_names, strict=True):
        current = getattr(log, field)
        choice = col.selectbox(
            f"{label}（1–5）",
            ["—", 1, 2, 3, 4, 5],
            index=0 if current is None else current,
            key=f"lab_score_{field}",
        )
        scores[field] = None if choice == "—" else int(str(choice))

    failure_tags = st.text_input(
        "失敗標籤（逗號分隔）",
        value=", ".join(log.failure_tags),
        key="lab_failure_tags",
    )
    notes = st.text_area("備註", value=log.notes, key="lab_notes", height=80)
    output_ref = st.text_input(
        "本地輸出參照（相對路徑或檔名）",
        value=log.local_output_reference,
        key="lab_output_ref",
    )

    if st.button("儲存評估", key="lab_save_btn", type="primary"):
        try:
            services.experiments.update_result_assessment(
                log.id,
                sampler=sampler,
                scheduler=scheduler,
                steps=_int_or_none(steps),
                cfg=_float_or_none(cfg),
                width=_int_or_none(width),
                height=_int_or_none(height),
                seed=_int_or_none(seed),
                overall_rating=scores["overall_rating"],
                identity_score=scores["identity_score"],
                style_score=scores["style_score"],
                instruction_adherence=scores["instruction_adherence"],
                failure_tags=tuple(
                    t.strip() for t in failure_tags.split(",") if t.strip()
                ),
                notes=notes,
                local_output_reference=output_ref,
            )
        except (ExperimentServiceError, ValueError) as exc:
            st.error(str(exc))
        else:
            st.success("已儲存。")
            st.rerun()

    # --------------------------------------------------------- duplicate
    st.subheader("單一變因複製")
    d1, d2, d3 = st.columns([1, 1, 1])
    dup_field = d1.selectbox("變因欄位", _DUPLICATE_FIELDS, key="lab_dup_field")
    dup_value = d2.text_input("新值", key="lab_dup_value")
    if d3.button("複製", key="lab_dup_btn"):
        value: object = dup_value
        if dup_field in {"steps", "width", "height", "seed"}:
            value = _int_or_none(dup_value)
        elif dup_field == "cfg":
            value = _float_or_none(dup_value)
        try:
            dup = services.experiments.duplicate_with_change(
                log.id, field=dup_field, value=value
            )
        except (ExperimentServiceError, ValueError) as exc:
            st.error(str(exc))
        else:
            st.success(f"已建立複製：{_short(dup.id)}（僅 {dup_field} 不同）")
            st.rerun()

    # ------------------------------------------------------------ compare
    st.subheader("比較")
    other_labels = {
        other.id: labels[other.id] for other in logs if other.id != log.id
    }
    compare_ids = st.multiselect(
        "與此實驗比較（可多選）",
        list(other_labels),
        format_func=lambda v: other_labels.get(v, str(v)),
        key="lab_compare_ids",
    )
    if compare_ids and st.button("執行比較", key="lab_compare_btn"):
        try:
            comparison = services.experiments.compare((log.id, *compare_ids))
        except ExperimentServiceError as exc:
            st.error(str(exc))
        else:
            st.caption(
                "差異欄位：" + ("、".join(comparison.differing_fields) or "（完全相同）")
            )
            rows = []
            fields = ("sampler", "scheduler", "steps", "cfg", "seed",
                      "checkpoint_id", "overall_rating")
            for exp in comparison.experiments:
                rows.append(
                    {"id": _short(exp.id)}
                    | {f: getattr(exp, f) for f in fields}
                )
            st.dataframe(rows, use_container_width=True)
