"""Shared candidate-first refinement controls and responsive comparison UI."""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import Any, cast

import streamlit as st
from pydantic import JsonValue, ValidationError

from imaginarium_forge.application.services.generation.candidate_application import (
    CandidateApplicationError,
    CandidateApplicationService,
)
from imaginarium_forge.application.services.generation.contracts import (
    GenerationErrorCategory,
    GenerationExecutionStatus,
    GenerationFailure,
)
from imaginarium_forge.application.services.generation.preference_routes import (
    refinement_route_preference,
)
from imaginarium_forge.application.services.generation.refinement import (
    InvalidRefinementCandidateError,
    LengthSpec,
    RefinementApplyReceipt,
    RefinementCandidate,
    RefinementMode,
    RefinementRequest,
    TextSpan,
    apply_all,
    apply_field,
    apply_selection,
    generation_task_for_artifact,
)
from imaginarium_forge.application.services.generation.refinement_candidate_persistence import (
    RefinementCandidateBinding,
    RefinementCandidatePersistenceError,
)
from imaginarium_forge.application.user_facing_errors import UserFacingError
from imaginarium_forge.domain.common.ids import new_id
from imaginarium_forge.domain.generation.durable import (
    ArtifactKind,
    CandidateAction,
    CandidateActionKind,
    CandidateActionStatus,
    CandidateStatus,
    GenerationCandidate,
    sha256_json,
    sha256_text,
)
from imaginarium_forge.domain.generation.preferences import AIProviderPreference
from imaginarium_forge.ui.error_presenter import (
    map_generation_failure,
    render_error_card,
)
from imaginarium_forge.ui.openai_session import (
    consume_openai_key_after_action,
    read_openai_session_settings,
)


class RefinementUIDecision(StrEnum):
    APPLY_ALL = "apply_all"
    APPLY_FIELD = "apply_field"
    APPLY_SELECTION = "apply_selection"
    SAVE_AS_NEW = "save_as_new"
    REJECT = "reject"


_MODE_LABELS: dict[RefinementMode, str] = {
    RefinementMode.POLISH: "語句潤飾",
    RefinementMode.EXPAND: "擴寫",
    RefinementMode.CONDENSE: "精簡",
    RefinementMode.EMOTION: "提升情緒張力",
    RefinementMode.DIALOGUE: "改善對話",
    RefinementMode.PACING: "改善節奏",
    RefinementMode.LOGIC_CONTINUITY: "修正邏輯／連貫性",
    RefinementMode.STYLE_REWRITE: "改寫風格",
    RefinementMode.SCREENPLAY_ADAPTATION: "轉成劇本",
    RefinementMode.CRITIQUE_ONLY: "只評論、不改寫",
}

_EVIDENCE_LABELS = {
    "actual_model": "實際模型",
    "actual_provider_id": "實際供應商",
    "attempt_number": "嘗試次數",
    "attempts": "執行嘗試",
    "credential_source": "憑證來源",
    "error_category": "錯誤類型",
    "fallback_used": "使用備援",
    "idempotency_key": "冪等識別碼",
    "latency_ms": "延遲毫秒",
    "lifecycle_status": "生命週期狀態",
    "offline_fallback_used": "使用離線處理",
    "requested_model": "請求模型",
    "requested_provider_id": "請求供應商",
    "retriable": "可重試",
    "route_source": "路由來源",
    "status": "狀態",
}


def _key(prefix: str, suffix: str) -> str:
    return f"{prefix}_refinement_{suffix}"


def _evidence_rows(
    value: JsonValue,
    *,
    path: tuple[str, ...] = (),
) -> list[tuple[str, str]]:
    """Flatten JSON-shaped evidence into localized, readable UI rows."""

    if isinstance(value, Mapping):
        rows: list[tuple[str, str]] = []
        for key in sorted(value):
            rows.extend(_evidence_rows(value[key], path=(*path, str(key))))
        return rows or [(_evidence_label(path), "—")]
    if isinstance(value, list):
        rows = []
        for index, item in enumerate(value, start=1):
            rows.extend(_evidence_rows(item, path=(*path, f"#{index}")))
        return rows or [(_evidence_label(path), "—")]
    if value is None:
        rendered = "—"
    elif isinstance(value, bool):
        rendered = "是" if value else "否"
    else:
        rendered = str(value)
    return [(_evidence_label(path), rendered)]


def _evidence_label(path: tuple[str, ...]) -> str:
    if not path:
        return "結果"
    return " / ".join(
        segment if segment.startswith("#") else _EVIDENCE_LABELS.get(segment, segment)
        for segment in path
    )


def _render_readable_evidence(value: Mapping[str, JsonValue]) -> None:
    for label, rendered in _evidence_rows(dict(value)):
        st.markdown(f"- **{label}**：{rendered}")


def candidate_state_key(prefix: str) -> str:
    return _key(prefix, "candidate")


def failure_state_key(prefix: str) -> str:
    return _key(prefix, "failure")


def busy_state_key(prefix: str) -> str:
    return _key(prefix, "busy")


def version_history_state_key(prefix: str) -> str:
    return _key(prefix, "version_history")


def durability_state_key(prefix: str) -> str:
    return _key(prefix, "durability")


def clear_refinement_state(prefix: str) -> None:
    st.session_state.pop(candidate_state_key(prefix), None)
    st.session_state.pop(failure_state_key(prefix), None)
    st.session_state.pop(durability_state_key(prefix), None)
    st.session_state.pop(_key(prefix, "request_id"), None)


def protected_facts_and_spans(
    original_text: str,
    facts: tuple[str, ...],
) -> tuple[tuple[str, ...], tuple[TextSpan, ...]]:
    """Return only authored facts that are present, plus every exact occurrence."""

    protected_facts: list[str] = []
    spans: list[TextSpan] = []
    seen: set[str] = set()
    for raw_fact in facts:
        fact = raw_fact.strip()
        if not fact or fact in seen or fact not in original_text:
            continue
        seen.add(fact)
        protected_facts.append(fact)
        start = 0
        while True:
            index = original_text.find(fact, start)
            if index < 0:
                break
            spans.append(TextSpan(start=index, end=index + len(fact), label="locked fact"))
            start = index + len(fact)
    return tuple(protected_facts), tuple(spans)


def request_refinement(
    services: Any,
    *,
    prefix: str,
    request: RefinementRequest,
    mode: RefinementMode,
    durable_binding: RefinementCandidateBinding | None = None,
) -> bool:
    """Run only from an explicit UI action and retain an immutable candidate."""

    busy_key = busy_state_key(prefix)
    if bool(st.session_state.get(busy_key, False)):
        _store_failure(
            prefix,
            GenerationFailure(
                category=GenerationErrorCategory.DUPLICATE_IN_PROGRESS,
                safe_message="上一個精修請求仍在執行；沒有重複送出，原稿未變更。",
                retriable=True,
            ),
        )
        return False

    service = getattr(services, "content_refinement", None)
    refine = getattr(service, "refine", None)
    if not callable(refine):
        _store_failure(
            prefix,
            GenerationFailure(
                category=GenerationErrorCategory.CONFIGURATION,
                safe_message="共用精修服務尚未就緒；原稿與已保存版本均未變更。",
            ),
        )
        return False

    effective_binding = durable_binding
    persistence = getattr(services, "refinement_candidates", None)
    if effective_binding is not None:
        verify_target = getattr(persistence, "verify_target", None)
        if not callable(verify_target):
            effective_binding = None
        else:
            try:
                verify_target(effective_binding, original_text=request.original_text)
            except (RefinementCandidatePersistenceError, RuntimeError, TypeError, ValueError):
                # A dirty editor remains fully usable. It deliberately falls
                # back to the pre-existing session-only candidate path rather
                # than sending a provider result into a stale formal target.
                effective_binding = None

    request_key = _key(prefix, "request_id")
    request_id = str(
        st.session_state.setdefault(request_key, f"{prefix}:refine:{new_id()}")
    )
    st.session_state[busy_key] = True
    session_override = read_openai_session_settings(st.session_state).api_key
    consume_session_override = False
    try:
        preference_service = getattr(services, "ai_runtime_preferences", None)
        read_preferences = getattr(preference_service, "get_preferences", None)
        if callable(read_preferences):
            preferences = read_preferences()
            task_override = refinement_route_preference(
                preferences,
                generation_task_for_artifact(request.artifact_kind),
            )
            configured_provider = (
                task_override.provider
                if task_override is not None and task_override.provider is not None
                else preferences.default_provider
            )
            consume_session_override = configured_provider is AIProviderPreference.OPENAI
        with st.spinner("精修候選正在建立中；完成前不會改動原稿。"):
            outcome = refine(
                request,
                mode,
                idempotency_key=request_id,
                session_override=session_override,
            )
    except Exception:
        _store_failure(
            prefix,
            GenerationFailure(
                category=GenerationErrorCategory.PROVIDER_ERROR,
                safe_message="精修服務未完成候選；原稿與已保存版本均未變更。",
                retriable=True,
            ),
        )
        return False
    finally:
        if consume_session_override:
            consume_openai_key_after_action(st.session_state)
        session_override = None
        st.session_state[busy_key] = False
        st.session_state.pop(request_key, None)

    if outcome.status is not GenerationExecutionStatus.SUCCEEDED:
        _store_failure(
            prefix,
            outcome.failure
            or GenerationFailure(
                category=GenerationErrorCategory.PROVIDER_ERROR,
                safe_message="精修服務未完成候選；原稿與已保存版本均未變更。",
            ),
        )
        return False
    candidate = outcome.candidate
    if not isinstance(candidate, RefinementCandidate):
        _store_failure(
            prefix,
            GenerationFailure(
                category=GenerationErrorCategory.INVALID_STRUCTURED_OUTPUT,
                safe_message="精修候選格式無法安全使用；原稿未變更。",
            ),
        )
        return False
    st.session_state[candidate_state_key(prefix)] = candidate.model_dump(mode="json")
    durability: dict[str, object] = {
        "candidate_id": candidate.id,
        "status": "session_only",
        "message": (
            "目前編輯器尚未有完全相符的已保存版本；候選與復原只保留在本次工作階段，"
            "明確保存前不會寫入草稿。"
        ),
    }
    if effective_binding is not None:
        persist = getattr(persistence, "persist", None)
        if callable(persist):
            try:
                persist(candidate, effective_binding)
            except (RefinementCandidatePersistenceError, RuntimeError, TypeError, ValueError):
                pass
            else:
                durability = {
                    "candidate_id": candidate.id,
                    "status": "durable",
                    "binding": effective_binding.model_dump(mode="json"),
                    "message": (
                        "候選、來源、品質報告與目標版本已保存；套用會建立可在重啟後復原的版本收據。"
                    ),
                }
    st.session_state[durability_state_key(prefix)] = durability
    st.session_state.pop(failure_state_key(prefix), None)
    return True


def render_refinement_controls(
    services: Any,
    *,
    prefix: str,
    artifact_kind: ArtifactKind,
    original_text: str,
    context_snapshot: Mapping[str, JsonValue] | None = None,
    selected_span: TextSpan | None = None,
    protected_spans: tuple[TextSpan, ...] = (),
    protected_facts: tuple[str, ...] = (),
    allow_selection: bool = True,
    durable_binding: RefinementCandidateBinding | None = None,
) -> bool:
    """Render one explicit action group; returns True when a candidate arrives."""

    st.markdown("#### AI 精修候選")
    st.caption(
        "精修只建立候選。原稿會保持不變，直到你在比較區明確按下套用。"
    )
    if durable_binding is None:
        st.caption("尚未綁定完全相符的已保存草稿：本次候選會使用工作階段套用／復原。")
    else:
        st.caption("若目前欄位仍與已保存版本一致，候選與後續套用收據會耐久保存。")
    mode = st.selectbox(
        "精修方式",
        list(RefinementMode),
        format_func=lambda item: _MODE_LABELS[item],
        key=_key(prefix, "mode"),
    )
    instruction = st.text_area(
        "補充指示",
        height=90,
        key=_key(prefix, "instruction"),
        placeholder="例如：保留冷靜語氣、對話不要變得太現代。",
    )
    desired_length: LengthSpec | None = None
    effective_selected_span = selected_span
    selection_error = ""
    with st.expander("進階限制", expanded=False):
        if allow_selection and selected_span is None:
            selection_enabled = st.checkbox(
                "只精修逐字選取的片段",
                key=_key(prefix, "selection_enabled"),
            )
            selected_source = st.text_area(
                "要精修的原文片段（逐字貼上，且須只出現一次）",
                height=90,
                disabled=not selection_enabled,
                key=_key(prefix, "selection_source"),
            )
            if selection_enabled:
                occurrences = _occurrence_offsets(original_text, selected_source)
                if not selected_source:
                    selection_error = "請逐字貼上要精修的原文片段。"
                elif not occurrences:
                    selection_error = "選取片段與目前原稿不一致。"
                elif len(occurrences) > 1:
                    selection_error = "這段文字在原稿中出現多次；請增加前後文字以唯一定位。"
                else:
                    start = occurrences[0]
                    effective_selected_span = TextSpan(
                        start=start,
                        end=start + len(selected_source),
                        label="author selection",
                    )
        minimum = int(
            st.number_input(
                "最少可見字元（不含空白；0 代表不限制）",
                min_value=0,
                max_value=500_000,
                value=0,
                step=100,
                key=_key(prefix, "minimum"),
            )
        )
        maximum = int(
            st.number_input(
                "最多可見字元（不含空白；0 代表不限制）",
                min_value=0,
                max_value=500_000,
                value=0,
                step=100,
                key=_key(prefix, "maximum"),
            )
        )
        if minimum or maximum:
            try:
                desired_length = LengthSpec(
                    minimum_characters=minimum or None,
                    maximum_characters=maximum or None,
                )
            except ValidationError:
                st.warning("最少字元不可大於最多字元；請調整後再建立候選。")

    disabled_reason = ""
    if not original_text.strip():
        disabled_reason = "請先輸入要精修的原稿。"
    elif (minimum or maximum) and desired_length is None:
        disabled_reason = "請先修正篇幅限制。"
    elif selection_error:
        disabled_reason = selection_error
    if disabled_reason:
        st.caption(disabled_reason)

    busy = bool(st.session_state.get(busy_state_key(prefix)))
    if busy:
        st.info("精修候選正在建立中；為避免重複送出，精修操作暫時停用。")

    actions = st.columns((3, 2))
    submitted = actions[0].button(
        "建立精修候選",
        type="primary",
        width="stretch",
        disabled=bool(disabled_reason) or busy,
        key=_key(prefix, "submit"),
    )
    cancelled = actions[1].button(
        "取消／清除精修",
        width="stretch",
        disabled=busy,
        key=_key(prefix, "cancel"),
    )
    if cancelled:
        clear_refinement_state(prefix)
        st.rerun()
    if not submitted:
        return False

    selected_text = ""
    if effective_selected_span is not None:
        selected_text = original_text[
            effective_selected_span.start : effective_selected_span.end
        ]
    try:
        request = RefinementRequest(
            artifact_kind=artifact_kind,
            original_text=original_text,
            selected_text=selected_text,
            selected_span=effective_selected_span,
            instruction=instruction,
            protected_facts=protected_facts,
            protected_spans=protected_spans,
            desired_length=desired_length,
            context_snapshot=dict(context_snapshot or {}),
        )
    except ValidationError:
        _store_failure(
            prefix,
            GenerationFailure(
                category=GenerationErrorCategory.INVALID_RESPONSE,
                safe_message="精修條件未通過驗證；原稿未變更。",
            ),
        )
        return False
    return request_refinement(
        services,
        prefix=prefix,
        request=request,
        mode=mode,
        durable_binding=durable_binding,
    )


def _occurrence_offsets(text: str, fragment: str) -> tuple[int, ...]:
    if not fragment:
        return ()
    offsets: list[int] = []
    start = 0
    while True:
        index = text.find(fragment, start)
        if index < 0:
            return tuple(offsets)
        offsets.append(index)
        start = index + 1


def render_refinement_failure(prefix: str) -> None:
    value = st.session_state.get(failure_state_key(prefix))
    if not isinstance(value, Mapping):
        return
    try:
        error = UserFacingError.model_validate(value)
    except ValidationError:
        legacy = GenerationFailure.model_validate(value)
        error = map_generation_failure(legacy, operation_zh_tw="建立精修候選")
        st.session_state[failure_state_key(prefix)] = error.model_dump(mode="json")
    render_error_card(error, key_prefix=f"{prefix}_refinement_error")


def render_refinement_candidate(
    prefix: str,
    *,
    field_name: str = "",
) -> tuple[RefinementUIDecision, RefinementCandidate] | None:
    """Show responsive evidence and return only an explicit author decision."""

    raw = st.session_state.get(candidate_state_key(prefix))
    if not isinstance(raw, Mapping):
        return None
    try:
        candidate = RefinementCandidate.model_validate(raw)
    except ValidationError:
        clear_refinement_state(prefix)
        _store_failure(
            prefix,
            GenerationFailure(
                category=GenerationErrorCategory.INVALID_STRUCTURED_OUTPUT,
                safe_message="暫存候選已失效；原稿未變更，請重新建立候選。",
            ),
        )
        return None

    provider = (
        candidate.provenance.actual_provider_id
        or candidate.provenance.requested_provider_id
        or "offline"
    )
    model = (
        candidate.provenance.actual_model
        or candidate.provenance.requested_model
        or "offline-refinement"
    )
    durability = st.session_state.get(durability_state_key(prefix))
    with st.container(border=True, key=_key(prefix, "candidate_card")):
        st.markdown("#### 精修候選（尚未套用）")
        st.caption(
            f"來源：{provider} · 模型：{model} · Template "
            f"{candidate.template_id}@{candidate.template_version}"
        )
        st.info("目前顯示的是候選；原稿仍是編輯器與已保存版本的唯一有效內容。")
        if isinstance(durability, Mapping) and durability.get("candidate_id") == candidate.id:
            message = str(durability.get("message", ""))
            if durability.get("status") == "durable":
                st.success(message)
            elif message:
                st.warning(message)
        compare_tab, diff_tab, quality_tab = st.tabs(("原稿／候選", "逐行差異", "品質與來源"))
        with compare_tab:
            original_column, candidate_column = st.columns(2)
            original_column.caption("原稿（唯讀）")
            original_column.code(
                candidate.original_text,
                language=None,
                wrap_lines=True,
            )
            candidate_column.caption("候選（尚未套用）")
            candidate_column.code(
                candidate.candidate_text,
                language=None,
                wrap_lines=True,
            )
            st.caption("寬螢幕並排、窄螢幕自然堆疊；兩側程式碼框都提供複製控制。")
        with diff_tab:
            if candidate.unified_diff:
                st.code(candidate.unified_diff, language="diff", wrap_lines=True)
            else:
                st.info("候選與原稿相同，沒有文字差異。")
        with quality_tab:
            st.markdown("**變更摘要**")
            for item in candidate.change_summary:
                st.markdown(f"- {item}")
            if candidate.warnings:
                st.warning("\n".join(candidate.warnings))
            st.markdown("**品質檢查**")
            if candidate.quality_report:
                _render_readable_evidence(candidate.quality_report)
            else:
                st.caption("這個候選沒有額外品質檢查結果。")
            st.markdown("**來源與版本**")
            _render_readable_evidence(
                {
                    "candidate_id": candidate.id,
                    "template": f"{candidate.template_id}@{candidate.template_version}",
                    **candidate.provenance.model_dump(mode="json"),
                }
            )
            st.caption(f"候選識別碼：`{candidate.id}`")

        if candidate.mode is RefinementMode.CRITIQUE_ONLY:
            if st.button(
                "讀完並關閉評論",
                type="primary",
                width="stretch",
                key=_key(prefix, "reject_critique"),
            ):
                return RefinementUIDecision.REJECT, candidate
            return None

        primary_label = "套用選取範圍" if candidate.selected_span else "套用全部候選"
        primary_decision = (
            RefinementUIDecision.APPLY_SELECTION
            if candidate.selected_span
            else (
                RefinementUIDecision.APPLY_FIELD
                if field_name
                else RefinementUIDecision.APPLY_ALL
            )
        )
        if st.button(
            primary_label,
            type="primary",
            width="stretch",
            key=_key(prefix, "primary_apply"),
        ):
            return primary_decision, candidate
        secondary = st.columns(2)
        if secondary[0].button(
            "另存為新草稿",
            width="stretch",
            key=_key(prefix, "save_new"),
        ):
            return RefinementUIDecision.SAVE_AS_NEW, candidate
        if secondary[1].button(
            "拒絕候選",
            width="stretch",
            key=_key(prefix, "reject"),
        ):
            return RefinementUIDecision.REJECT, candidate
    return None


def refinement_receipt(
    decision: RefinementUIDecision,
    candidate: RefinementCandidate,
    *,
    current_text: str,
    field_name: str = "",
) -> RefinementApplyReceipt:
    """Apply only the explicitly selected scope, preserving every stale guard."""

    if decision is RefinementUIDecision.APPLY_SELECTION:
        return apply_selection(candidate, current_text)
    if decision is RefinementUIDecision.APPLY_FIELD:
        if not field_name:
            raise InvalidRefinementCandidateError("欄位套用需要明確欄位名稱")
        _, receipt = apply_field(candidate, {field_name: current_text}, field_name)
        return receipt
    if decision is RefinementUIDecision.APPLY_ALL:
        return apply_all(candidate, current_text)
    raise InvalidRefinementCandidateError("這個作者決定不會套用候選")


def candidate_receipt_for_save_as_new(
    candidate: RefinementCandidate,
    *,
    current_text: str,
    field_name: str = "",
) -> RefinementApplyReceipt:
    """Validate a save-as-new copy without mutating the mounted editor."""

    if candidate.selected_span is not None:
        return apply_selection(candidate, current_text)
    if field_name:
        _, receipt = apply_field(candidate, {field_name: current_text}, field_name)
        return receipt
    return apply_all(candidate, current_text)


def durable_binding_for_candidate(
    prefix: str,
    candidate: RefinementCandidate,
) -> RefinementCandidateBinding | None:
    """Return the persisted binding only for this exact session candidate."""

    raw = st.session_state.get(durability_state_key(prefix))
    if (
        not isinstance(raw, Mapping)
        or raw.get("status") != "durable"
        or raw.get("candidate_id") != candidate.id
        or not isinstance(raw.get("binding"), Mapping)
    ):
        return None
    try:
        return RefinementCandidateBinding.model_validate(raw["binding"])
    except ValidationError:
        st.session_state.pop(durability_state_key(prefix), None)
        return None


def commit_durable_refinement_decision(
    services: Any,
    *,
    prefix: str,
    decision: RefinementUIDecision,
    candidate: RefinementCandidate,
) -> CandidateAction | GenerationCandidate | None:
    """Commit one explicit decision when the candidate has a durable binding.

    ``None`` means the caller must retain the existing session-only path.
    Every durable action uses a deterministic idempotency key, so a Streamlit
    rerun cannot append the same version twice.
    """

    binding = durable_binding_for_candidate(prefix, candidate)
    if binding is None:
        return None
    application = cast(
        CandidateApplicationService | None,
        getattr(services, "candidate_application", None),
    )
    if application is None:
        raise RefinementCandidatePersistenceError(
            "候選版本服務尚未就緒；原稿與已保存版本均未變更。"
        )
    if decision is RefinementUIDecision.REJECT:
        return application.reject(candidate.id)

    selection: dict[str, object] = {"field": binding.field_name}
    if candidate.selected_span is not None:
        selection.update(
            {
                "start": candidate.selected_span.start,
                "end": candidate.selected_span.end,
            }
        )
    action_key = (
        f"{candidate.id}:{decision.value}:"
        f"{sha256_json(selection)[:16]}"
    )
    if decision is RefinementUIDecision.APPLY_ALL:
        return application.apply_all(candidate.id, idempotency_key=action_key)
    if decision is RefinementUIDecision.APPLY_FIELD:
        return application.apply_field(
            candidate.id,
            field_name=binding.field_name,
            idempotency_key=action_key,
        )
    if decision is RefinementUIDecision.APPLY_SELECTION:
        return application.apply_selection(
            candidate.id,
            selection=selection,
            idempotency_key=action_key,
        )
    if decision is RefinementUIDecision.SAVE_AS_NEW:
        return application.save_as_new(candidate.id, idempotency_key=action_key)
    raise InvalidRefinementCandidateError("未知的精修候選決定")


def render_durable_refinement_history(
    services: Any,
    *,
    prefix: str,
    binding: RefinementCandidateBinding | None,
) -> CandidateAction | None:
    """Render restart-readable receipts and offer one guarded durable undo."""

    if binding is None or binding.target_id is None:
        return None
    persistence = getattr(services, "refinement_candidates", None)
    list_history = getattr(persistence, "list_history", None)
    if not callable(list_history):
        return None
    try:
        history = list_history(binding)
    except (RefinementCandidatePersistenceError, RuntimeError, TypeError, ValueError):
        return None
    if not history:
        return None

    undoable: tuple[GenerationCandidate, CandidateAction] | None = None
    with st.expander("精修版本紀錄（已保存）", expanded=False):
        st.caption("以下候選、來源、品質與套用收據由本機資料庫保存，重新啟動後仍可讀。")
        for item in history[:5]:
            candidate = item.candidate
            provider = candidate.provenance.provider or "offline"
            model = candidate.provenance.model or "offline-refinement"
            completed = [
                action
                for action in item.actions
                if action.status is CandidateActionStatus.COMPLETED
            ]
            st.markdown(
                f"- `{candidate.id}` · {candidate.status.value} · "
                f"{provider}/{model} · {len(completed)} 筆完成收據"
            )
            if candidate.quality_report:
                st.caption(
                    f"品質報告：{', '.join(sorted(candidate.quality_report))}"
                )
            undone_ids = {
                action.undo_of_action_id
                for action in completed
                if action.action_kind is CandidateActionKind.UNDO
            }
            if candidate.status is CandidateStatus.APPLIED and undoable is None:
                original_actions = [
                    action
                    for action in completed
                    if action.action_kind
                    not in {
                        CandidateActionKind.SAVE_AS_NEW,
                        CandidateActionKind.UNDO,
                    }
                    and action.id not in undone_ids
                ]
                if original_actions:
                    undoable = (candidate, original_actions[-1])

        if undoable is None:
            st.caption("目前沒有可安全復原的已套用版本。")
            return None
        candidate, action = undoable
        if st.button(
            "復原最近一次已保存精修",
            key=_key(prefix, "durable_undo"),
            width="stretch",
        ):
            application = cast(
                CandidateApplicationService | None,
                getattr(services, "candidate_application", None),
            )
            if application is None:
                return None
            try:
                return application.undo(
                    candidate.id,
                    action_id=action.id,
                    idempotency_key=f"{candidate.id}:undo:{action.id}",
                )
            except (CandidateApplicationError, RuntimeError, TypeError, ValueError):
                _store_failure(
                    prefix,
                    GenerationFailure(
                        category=GenerationErrorCategory.IDEMPOTENCY_CONFLICT,
                        safe_message=(
                            "已保存草稿在套用後又有變更；為避免覆寫新內容，這次沒有復原。"
                        ),
                    ),
                )
    return None


def record_refinement_apply(
    prefix: str,
    receipt: RefinementApplyReceipt,
    candidate: RefinementCandidate,
) -> None:
    """Append a bounded session-only version path after a successful apply."""

    raw_history = st.session_state.get(version_history_state_key(prefix), [])
    history = list(raw_history) if isinstance(raw_history, list) else []
    history.append(
        {
            "receipt": receipt.model_dump(mode="json"),
            "candidate_id": candidate.id,
            "mode": candidate.mode.value,
            "provenance": candidate.provenance.model_dump(mode="json"),
            "quality_report": dict(candidate.quality_report),
        }
    )
    st.session_state[version_history_state_key(prefix)] = history[-20:]


def render_refinement_undo(prefix: str, *, current_text: str) -> str | None:
    """Offer an explicit, hash-guarded undo for the latest session apply."""

    raw_history = st.session_state.get(version_history_state_key(prefix), [])
    if not isinstance(raw_history, list) or not raw_history:
        return None
    last = raw_history[-1]
    if not isinstance(last, Mapping) or not isinstance(last.get("receipt"), Mapping):
        st.session_state.pop(version_history_state_key(prefix), None)
        return None
    try:
        receipt = RefinementApplyReceipt.model_validate(last["receipt"])
    except ValidationError:
        st.session_state.pop(version_history_state_key(prefix), None)
        return None
    mode = str(last.get("mode", "unknown"))
    st.caption(
        f"本次工作階段保留 {len(raw_history)} 筆精修套用路徑；最新模式：`{mode}`。"
    )
    current_is_latest = sha256_text(current_text) == receipt.after_sha256
    if not current_is_latest:
        st.caption("目前內容已在套用後再次修改；為避免覆寫新文字，復原已鎖定。")
    if st.button(
        "復原上次精修套用",
        key=_key(prefix, "undo"),
        disabled=not current_is_latest,
    ):
        st.session_state[version_history_state_key(prefix)] = raw_history[:-1]
        return receipt.before_text
    return None


def set_refinement_failure(prefix: str, failure: GenerationFailure) -> None:
    """Expose sanitized failure storage to page-level apply/save boundaries."""

    _store_failure(prefix, failure)


def _store_failure(prefix: str, failure: GenerationFailure) -> None:
    error = map_generation_failure(failure, operation_zh_tw="建立或套用精修候選")
    st.session_state[failure_state_key(prefix)] = error.model_dump(mode="json")


__all__ = [
    "RefinementUIDecision",
    "busy_state_key",
    "candidate_receipt_for_save_as_new",
    "candidate_state_key",
    "clear_refinement_state",
    "commit_durable_refinement_decision",
    "durability_state_key",
    "durable_binding_for_candidate",
    "failure_state_key",
    "protected_facts_and_spans",
    "record_refinement_apply",
    "refinement_receipt",
    "render_durable_refinement_history",
    "render_refinement_candidate",
    "render_refinement_controls",
    "render_refinement_failure",
    "render_refinement_undo",
    "request_refinement",
    "set_refinement_failure",
    "version_history_state_key",
]
