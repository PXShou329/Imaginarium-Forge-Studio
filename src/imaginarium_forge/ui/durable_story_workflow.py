"""Story Studio panel for real DB-backed long-form workflow progress."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping
from functools import partial
from typing import Any

import streamlit as st
from pydantic import SecretStr

from imaginarium_forge.application.errors import ApplicationError
from imaginarium_forge.application.services.durable_generation.author_workspace import (
    AuthorUnitProgress,
    LongFormPlanDraft,
    LongFormWorkspaceMode,
    author_progress_message,
    build_local_long_form_plan,
    confirm_long_form_plan,
    project_continuity_issues,
    project_unit_progress,
)
from imaginarium_forge.application.services.durable_generation.contracts import (
    CandidateApplyCommand,
    DurableExecutionResult,
    ExecutionDisposition,
)
from imaginarium_forge.application.services.durable_generation.executor import (
    DurableWorkflowError,
)
from imaginarium_forge.application.services.durable_generation.runtime_step_runner import (
    DurableRouteConfigurationError,
    durable_route_from_preferences,
)
from imaginarium_forge.application.services.durable_generation.unit_resolution import (
    UnitResolutionService,
)
from imaginarium_forge.application.services.durable_generation.worker import (
    DurableWorkerSnapshot,
    DurableWorkerStatus,
)
from imaginarium_forge.application.user_facing_errors import ErrorDataSafety
from imaginarium_forge.domain.common.ids import utc_now_iso
from imaginarium_forge.domain.generation.durable import (
    ACTIVE_JOB_STATUSES,
    ArtifactKind,
    CandidateActionKind,
    GenerationCandidate,
    GenerationJob,
    GenerationStep,
    JobStatus,
    StepKind,
    StepStatus,
)
from imaginarium_forge.domain.generation.ollama_context import (
    OllamaContextProfile,
)
from imaginarium_forge.domain.prompt.content_mode import ContentMode
from imaginarium_forge.providers.errors import ProviderError
from imaginarium_forge.ui.durable_story_actions import (
    prepare_confirmed_long_form_workflow,
    prepare_story_durable_workflow,
)
from imaginarium_forge.ui.durable_story_undo import render_story_candidate_undo
from imaginarium_forge.ui.error_presenter import present_exception
from imaginarium_forge.ui.generation_actions import (
    background_provider_credential_available,
    provider_action_credential_available,
    run_provider_credential_action,
)
from imaginarium_forge.ui.unit_resolution_panel import (
    is_unit_candidate,
    render_unit_resolution_panel,
)

_SELECTED_JOB_KEY = "story_durable_selected_job"
_PENDING_SELECTED_JOB_KEY = "story_durable_pending_selected_job"
_CANCEL_CONFIRM_PREFIX = "story_durable_cancel_confirm"
_WORKSPACE_ENTRY_KEY = "story_longform_entry_intent"
_PLAN_DRAFT_KEY = "story_longform_plan_draft"
_CONFIRMED_PLAN_KEY = "story_longform_confirmed_plan"
_PLAN_PROJECT_KEY = "story_longform_plan_project"
_PLAN_EDITOR_REVISION_KEY = "story_longform_plan_editor_revision"
_CONTINUITY_FOCUS_KEY = "story_longform_continuity_focus"
_NEW_WORKFLOW_OPTION = "__new_long_form_workflow__"
_GUIDED_STEP_KEY = "story_longform_guided_step"
_GUIDED_CONFIRMED_KEY = "story_longform_guided_confirmed_steps"
_GUIDED_FINAL_KEY = "story_longform_guided_final_confirm"
_LONG_FORM_ENGINE_NOT_READY_MESSAGE = (
    "目前寫作引擎尚未就緒；可從「狀態」進入 AI 設定後再開始正文。"
)
_PINNED_OFFLINE_JOB_MESSAGE = (
    "這份既有工作固定使用不支援長篇正文的本機離線路線，"
    "因此無法繼續、重試或執行新的正文步驟。"
    "已保存的大綱、原稿、正文片段與進度都會保留。"
    "若要繼續生成，請先在「狀態」選擇支援長篇正文的寫作引擎，"
    "再回到這裡規劃一份新工作；系統不會替這份既有工作改路由。"
)
_GUIDED_ROUTE_KEY = "story_longform_guided_route"
_EDITOR_VALUES_KEY = "_story_longform_editor_values"
_GUIDED_STEPS = ("故事構想", "人物與世界約束", "全篇大綱", "章節與場景", "寫作引擎", "篇幅與上下文")

_AUTHOR_WORKSPACE_SERVICE_NAMES = (
    "creative_launch",
    "story_requirements",
    "story_bibles",
    "story_outlines",
    "chapter_plans",
    "scene_cards",
)
_PLAN_INPUT_KEYS = (
    "story_longform_mode",
    "story_longform_idea",
    "story_longform_genre",
    "story_longform_tone",
    "story_longform_total_target",
    "story_longform_auto_units",
    "story_longform_requested_units",
    "story_longform_character_world",
    "story_longform_author_constraints",
    "story_longform_max_attempts",
    "story_longform_context_profile",
    "story_longform_custom_num_ctx",
)

_JOB_STATUS_LABELS: dict[JobStatus, str] = {
    JobStatus.QUEUED: "等待執行",
    JobStatus.NORMALIZING_BRIEF: "整理創作需求",
    JobStatus.PLANNING: "規劃故事",
    JobStatus.DRAFTING: "撰寫正文",
    JobStatus.REVIEWING: "檢查品質",
    JobStatus.REVISING: "定點修訂",
    JobStatus.ASSEMBLING: "組裝長篇",
    JobStatus.COMPLETED: "已完成",
    JobStatus.PAUSED: "已暫停",
    JobStatus.CANCELLED: "已取消",
    JobStatus.FAILED: "執行失敗",
    JobStatus.NEEDS_AUTHOR_INPUT: "等待作者處理",
}

_STEP_KIND_LABELS: dict[StepKind, str] = {
    StepKind.BRIEF_NORMALIZATION: "整理創作需求",
    StepKind.STORY_PLAN: "故事總規劃",
    StepKind.CHARACTER_ARC_PLAN: "角色弧線規劃",
    StepKind.CHAPTER_OR_SCENE_PLAN: "章節／場景規劃",
    StepKind.PROSE_CHUNK: "正文分段",
    StepKind.ROLLING_SUMMARY: "滾動摘要",
    StepKind.CONTINUITY_CHECK: "連貫性檢查",
    StepKind.TARGETED_REVISION: "定點修訂",
    StepKind.LENGTH_ASSURANCE: "篇幅確認與續寫",
    StepKind.FINAL_ASSEMBLY: "全文組裝",
    StepKind.FINAL_QUALITY_CHECK: "最終品質檢查",
}

_STEP_STATUS_LABELS: dict[StepStatus, str] = {
    StepStatus.PENDING: "尚未執行",
    StepStatus.RUNNING: "執行中",
    StepStatus.COMPLETED: "已完成",
    StepStatus.INTERRUPTED: "意外中斷",
    StepStatus.PAUSED: "已暫停",
    StepStatus.CANCELLED: "已取消",
    StepStatus.FAILED: "執行失敗",
    StepStatus.NEEDS_AUTHOR_INPUT: "等待作者處理",
}

_DISPOSITION_LABELS: dict[ExecutionDisposition, str] = {
    ExecutionDisposition.STEP_COMPLETED: "步驟已完成",
    ExecutionDisposition.CONTINUATION_REQUIRED: "已保存 partial，準備從穩定邊界續寫",
    ExecutionDisposition.JOB_COMPLETED: "工作流已完成",
    ExecutionDisposition.PAUSED: "工作流已暫停",
    ExecutionDisposition.CANCELLED: "工作流已取消",
    ExecutionDisposition.FAILED: "步驟執行失敗",
    ExecutionDisposition.NEEDS_AUTHOR_INPUT: "等待作者處理",
    ExecutionDisposition.ALREADY_COMPLETE: "工作流先前已完成",
}

_DURABLE_UI_ERRORS = (
    ApplicationError,
    DurableWorkflowError,
    ProviderError,
    ValueError,
)


def durable_step_rows(steps: tuple[GenerationStep, ...]) -> list[dict[str, object]]:
    """Exact persisted progress rows; no derived or decorative percentage."""

    rows: list[dict[str, object]] = []
    for step in steps:
        planned = step.input_snapshot.get("planned")
        unit: object = planned.get("unit") if isinstance(planned, Mapping) else None
        unit_label = "全篇"
        if isinstance(unit, Mapping):
            title = unit.get("title")
            identifier = unit.get("id")
            unit_label = str(title or identifier or "未命名單元")
        rows.append(
            {
                "順序": step.ordinal + 1,
                "單元": unit_label,
                "步驟": (f"{_STEP_KIND_LABELS[step.step_kind]} ({step.step_kind.value})"),
                "狀態": f"{_STEP_STATUS_LABELS[step.status]} ({step.status.value})",
                "嘗試": step.attempts,
                "checkpoint": step.checkpoint_index,
                "partial": step.partial,
                "provider": step.provider or step.requested_provider or "—",
                "model": step.model or step.requested_model or "—",
            }
        )
    return rows


def run_bounded(
    service: Any,
    job_id: str,
    *,
    max_steps: int,
    session_override: SecretStr | str | None = None,
) -> DurableExecutionResult:
    """Run at most N committed steps and return the last real disposition."""

    if max_steps < 1:
        raise ValueError("max_steps must be positive")
    result: DurableExecutionResult | None = None
    for _ in range(max_steps):
        result = service.run_next(job_id, session_override=session_override)
        if result.disposition not in {
            ExecutionDisposition.STEP_COMPLETED,
            ExecutionDisposition.CONTINUATION_REQUIRED,
        }:
            return result
    assert result is not None
    return result


def _run_with_session_credential(
    services: Any,
    job: GenerationJob,
    action: Callable[[SecretStr | None], object],
) -> object:
    """Delegate one-action credential ownership to the shared UI boundary."""

    return run_provider_credential_action(
        services,
        st.session_state,
        provider_name=str(job.route.get("provider", "")),
        action=action,
    )


def render_durable_story_workflow(
    services: Any,
    *,
    project_id: str,
    scene_id: str,
) -> None:
    """Render the N4 workspace, retaining the old panel for narrow test doubles."""

    if all(hasattr(services, name) for name in _AUTHOR_WORKSPACE_SERVICE_NAMES):
        _render_author_long_form_workspace(
            services,
            project_id=project_id,
            scene_id=scene_id,
        )
        return
    _render_legacy_durable_story_workflow(
        services,
        project_id=project_id,
        scene_id=scene_id,
    )


def _render_legacy_durable_story_workflow(
    services: Any,
    *,
    project_id: str,
    scene_id: str,
) -> None:
    """Compatibility panel for integrations that only provide durable services."""

    with st.expander("耐久長篇工作流（可暫停、重啟與恢復）", expanded=False):
        st.caption(
            "每個規劃、正文、摘要、連貫檢查、修訂、篇幅與品質步驟都寫入本機資料庫；"
            "表格顯示真實 step/checkpoint/provider/model，不使用推測百分比。"
        )
        if not scene_id:
            st.info("請先選擇已有 Scene Card 正式版本的場景，作為版本與上下文錨點。")
            return

        _render_create(services, project_id=project_id, scene_id=scene_id)
        jobs = tuple(
            services.durable_generation.list_jobs(
                target_type="story_scene",
                target_id=scene_id,
            )
        )
        if not jobs:
            st.info("此場景尚無耐久長篇工作流。")
            return
        job = _select_job(jobs)
        _render_job(services, job)


def _render_author_long_form_workspace(
    services: Any,
    *,
    project_id: str,
    scene_id: str,
) -> None:
    """N4 humane entry, planner, prose reader, and writing assistant."""

    del scene_id  # A manually selected Scene is optional in the N4 entry path.
    _reset_plan_for_project(project_id)
    _preserve_workspace_editor_state()
    jobs = _project_long_form_jobs(services, project_id=project_id)
    _apply_workspace_entry_intent(jobs)

    workspace = st.container(key="story_longform_workspace")
    with workspace:
        job_by_id = {job.id: job for job in jobs}
        options = [_NEW_WORKFLOW_OPTION, *job_by_id]
        pending_job = st.session_state.pop(_PENDING_SELECTED_JOB_KEY, None)
        if isinstance(pending_job, str) and pending_job in job_by_id:
            # Start runs after the selector is mounted. Apply its one-shot
            # intent only here, before mounting, and only in the owning project.
            st.session_state[_SELECTED_JOB_KEY] = pending_job
        current = st.session_state.get(_SELECTED_JOB_KEY)
        if current not in options:
            st.session_state[_SELECTED_JOB_KEY] = (
                jobs[0].id if jobs else _NEW_WORKFLOW_OPTION
            )
        selected = st.selectbox(
            "目前工作",
            options=options,
            format_func=lambda value: (
                "規劃新的長篇"
                if value == _NEW_WORKFLOW_OPTION
                else _author_job_label(job_by_id[value])
            ),
            key=_SELECTED_JOB_KEY,
            help="關閉後重新開啟，從這裡選回已保存的工作即可繼續。",
        )
        if selected == _NEW_WORKFLOW_OPTION:
            _render_new_long_form_plan(services, project_id=project_id)
        else:
            _render_author_job(services, job_by_id[selected])


def _project_long_form_jobs(services: Any, *, project_id: str) -> tuple[GenerationJob, ...]:
    jobs = tuple(services.durable_generation.list_jobs(target_type="story_scene"))
    matching = [
        job
        for job in jobs
        if job.source_snapshot.get("project_id") == project_id
        and job.artifact_kind is ArtifactKind.LONG_STORY
    ]
    return tuple(sorted(matching, key=lambda job: (job.created_at, job.id), reverse=True))


def _apply_workspace_entry_intent(jobs: tuple[GenerationJob, ...]) -> None:
    intent = st.session_state.pop(_WORKSPACE_ENTRY_KEY, "")
    if intent == "new":
        st.session_state[_SELECTED_JOB_KEY] = _NEW_WORKFLOW_OPTION
    elif intent == "continue" and jobs:
        st.session_state[_SELECTED_JOB_KEY] = jobs[0].id


def _reset_plan_for_project(project_id: str) -> None:
    if st.session_state.get(_PLAN_PROJECT_KEY) == project_id:
        return
    st.session_state[_PLAN_PROJECT_KEY] = project_id
    _invalidate_workspace_plan()
    _reset_guided_progress()
    st.session_state.pop(_EDITOR_VALUES_KEY, None)
    st.session_state.pop(_PLAN_EDITOR_REVISION_KEY, None)
    for key in _PLAN_INPUT_KEYS:
        st.session_state.pop(key, None)
    for state_key in tuple(st.session_state):
        if str(state_key).startswith(
            (
                "story_longform_outline_",
                "story_longform_unit_title_",
                "story_longform_unit_summary_",
            )
        ):
            st.session_state.pop(state_key, None)


def _invalidate_workspace_plan() -> None:
    # Widget callbacks run before navigation rerenders the destination. Capture
    # the latest edit here even when this workspace will not mount again.
    _preserve_workspace_editor_state()
    st.session_state.pop(_PLAN_DRAFT_KEY, None)
    st.session_state.pop(_CONFIRMED_PLAN_KEY, None)
    _invalidate_guided_review()


def _invalidate_confirmed_plan() -> None:
    _preserve_workspace_editor_state()
    st.session_state.pop(_CONFIRMED_PLAN_KEY, None)
    _invalidate_guided_review()


def _preserve_workspace_editor_state() -> None:
    """Retain author values when a step or the whole workspace is unmounted."""

    saved_raw = st.session_state.get(_EDITOR_VALUES_KEY, {})
    revision = int(st.session_state.get(_PLAN_EDITOR_REVISION_KEY, 0))
    prefixes = (
        "story_longform_outline_", "story_longform_unit_title_", "story_longform_unit_summary_",
    )

    def current_key(key: str) -> bool:
        if key in _PLAN_INPUT_KEYS:
            return True
        return any(
            key == f"{prefix}{revision}" or key.startswith(f"{prefix}{revision}_")
            for prefix in prefixes
        )

    saved = (
        {str(key): value for key, value in saved_raw.items() if current_key(str(key))}
        if isinstance(saved_raw, dict) else {}
    )
    # Rebuilding a plan increments its revision before the next render. Only
    # discard superseded review widgets, never the current author's values.
    for key in tuple(st.session_state):
        if str(key).startswith(prefixes) and not current_key(str(key)):
            st.session_state.pop(key, None)
    for key, value in saved.items():
        if key not in st.session_state:
            st.session_state[key] = value
    for key in tuple(st.session_state):
        if current_key(str(key)):
            st.session_state[key] = st.session_state[key]
            saved[str(key)] = st.session_state[key]
    # This in-memory, project-scoped draft is not a durable save and never
    # contains runtime credentials. Final consent is intentionally not mirrored.
    st.session_state[_EDITOR_VALUES_KEY] = saved
    if _GUIDED_FINAL_KEY in st.session_state:
        st.session_state[_GUIDED_FINAL_KEY] = st.session_state[_GUIDED_FINAL_KEY]


def _reset_guided_progress() -> None:
    st.session_state[_GUIDED_STEP_KEY] = 0
    st.session_state[_GUIDED_CONFIRMED_KEY] = 0
    st.session_state.pop(_GUIDED_FINAL_KEY, None)
    st.session_state.pop(_GUIDED_ROUTE_KEY, None)


def _change_workspace_mode() -> None:
    _invalidate_workspace_plan()
    _reset_guided_progress()


def _invalidate_guided_review() -> None:
    step = int(st.session_state.get(_GUIDED_STEP_KEY, 0))
    previous = int(st.session_state.get(_GUIDED_CONFIRMED_KEY, 0))
    st.session_state[_GUIDED_CONFIRMED_KEY] = min(previous, step, 3)
    st.session_state.pop(_GUIDED_FINAL_KEY, None)
    st.session_state.pop(_GUIDED_ROUTE_KEY, None)


def _move_guided_step(step: int, *, confirm: bool = False, route: str = "") -> None:
    current = int(st.session_state.get(_GUIDED_STEP_KEY, 0))
    if confirm:
        previous = int(st.session_state.get(_GUIDED_CONFIRMED_KEY, 0))
        if previous < current:
            return
        st.session_state[_GUIDED_CONFIRMED_KEY] = max(previous, current + 1)
        if current == 4:
            st.session_state[_GUIDED_ROUTE_KEY] = route
    st.session_state[_GUIDED_STEP_KEY] = max(0, min(step, len(_GUIDED_STEPS) - 1))


def _guided_route_fingerprint(services: Any) -> str:
    """Bind review to non-secret global preferences, without resolving a key."""

    try:
        preferences = services.ai_runtime_preferences.get_preferences()
        route, _quality = durable_route_from_preferences(preferences)
    except ValueError:
        return ""
    identity = f"{preferences.updated_at}:{route.provider}:{route.model}"
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _long_form_route_readiness(services: Any) -> tuple[bool, str]:
    """Return a safe author-facing reason when durable prose cannot start."""

    try:
        preferences = services.ai_runtime_preferences.get_preferences()
        durable_route_from_preferences(preferences)
    except DurableRouteConfigurationError as exc:
        return False, str(exc)
    except ValueError:
        return False, _LONG_FORM_ENGINE_NOT_READY_MESSAGE
    return True, ""


def _stored_plan(key: str) -> LongFormPlanDraft | None:
    raw = st.session_state.get(key)
    if raw is None:
        return None
    try:
        return LongFormPlanDraft.model_validate(raw)
    except ValueError:
        st.session_state.pop(key, None)
        return None


def _render_new_long_form_plan(services: Any, *, project_id: str) -> None:
    mode_value = st.radio(
        "工作方式",
        options=[mode.value for mode in LongFormWorkspaceMode],
        format_func=lambda value: {
            LongFormWorkspaceMode.QUICK.value: "快速",
            LongFormWorkspaceMode.GUIDED.value: "引導",
            LongFormWorkspaceMode.ADVANCED.value: "進階",
        }[value],
        horizontal=True,
        key="story_longform_mode",
        on_change=_change_workspace_mode,
        help="快速只問必要資訊；引導增加創作限制；進階才顯示資源與重試設定。",
    )
    mode = LongFormWorkspaceMode(mode_value)
    if mode is LongFormWorkspaceMode.GUIDED:
        _render_guided_plan(services, project_id=project_id)
        return
    if mode is LongFormWorkspaceMode.QUICK:
        _render_quick_plan(services, project_id=project_id)
        return
    plan = _stored_plan(_PLAN_DRAFT_KEY)
    confirmed = _stored_plan(_CONFIRMED_PLAN_KEY)

    shell = st.container(key="story_longform_workspace_shell")
    outline_column, prose_column, assistant_column = shell.columns([1, 2, 1])
    with outline_column, st.container(key="story_longform_outline_panel", border=True):
        st.subheader("1. 你的故事")
        author_idea = st.text_area(
            "想寫什麼？",
            key="story_longform_idea",
            height=160,
            placeholder="一句話、幾段筆記都可以。先說你真正想寫的故事。",
            on_change=_invalidate_workspace_plan,
        )
        genre = st.text_input(
            "類型（可留白）",
            key="story_longform_genre",
            placeholder="奇幻、推理、文學……",
            on_change=_invalidate_workspace_plan,
        )
        tone = st.text_input(
            "語氣／氛圍（可留白）",
            key="story_longform_tone",
            placeholder="溫柔、緊張、冷峻……",
            on_change=_invalidate_workspace_plan,
        )
        # Seed only missing state; preserved author values own widget defaults.
        st.session_state.setdefault("story_longform_total_target", 20_000)
        total_target = int(
            st.number_input(
                "全文目標字數（可見字元）",
                min_value=100,
                max_value=1_000_000,
                step=1_000,
                key="story_longform_total_target",
                on_change=_invalidate_workspace_plan,
            )
        )
        st.session_state.setdefault("story_longform_auto_units", True)
        automatic_units = st.checkbox(
            "自動建議章數",
            key="story_longform_auto_units",
            on_change=_invalidate_workspace_plan,
        )
        requested_units = None
        if not automatic_units:
            st.session_state.setdefault("story_longform_requested_units", 4)
            requested_units = int(
                st.number_input(
                    "章數",
                    min_value=1,
                    max_value=100,
                    step=1,
                    key="story_longform_requested_units",
                    on_change=_invalidate_workspace_plan,
                )
            )
        character_world = ""
        author_constraints = ""
        if mode in {LongFormWorkspaceMode.GUIDED, LongFormWorkspaceMode.ADVANCED}:
            character_world = st.text_area(
                "人物與世界重點（可留白）",
                key="story_longform_character_world",
                height=100,
                on_change=_invalidate_workspace_plan,
            )
            author_constraints = st.text_area(
                "必須遵守／避免（可留白）",
                key="story_longform_author_constraints",
                height=100,
                on_change=_invalidate_workspace_plan,
            )

    with prose_column, st.container(key="story_longform_prose_panel", border=True):
        _render_plan_review(plan, confirmed=confirmed)

    with assistant_column, st.container(key="story_longform_assistant_panel", border=True):
        st.subheader("3. 開始正文")
        st.caption("整理大綱不會呼叫模型，也不會建立正文或改動現有草稿。")
        if st.button(
            "整理本機大綱",
            key="story_longform_build_plan",
            type="primary" if plan is None else "secondary",
            width="stretch",
        ):
            try:
                built = build_local_long_form_plan(
                    mode=mode,
                    author_idea=author_idea,
                    genre=genre,
                    tone=tone,
                    total_target_visible_chars=total_target,
                    requested_unit_count=requested_units,
                    character_world_constraints=character_world,
                    author_constraints=author_constraints,
                )
                revision = int(st.session_state.get(_PLAN_EDITOR_REVISION_KEY, 0)) + 1
                st.session_state[_PLAN_EDITOR_REVISION_KEY] = revision
                st.session_state[_PLAN_DRAFT_KEY] = built.model_dump(mode="json")
                st.session_state.pop(_CONFIRMED_PLAN_KEY, None)
                st.rerun()
            except _DURABLE_UI_ERRORS as exc:
                present_exception(
                    exc,
                    operation_zh_tw="整理長篇大綱",
                    key_prefix="story_longform_plan_error",
                    data_safety=ErrorDataSafety(
                        message_zh_tw="大綱尚未建立；你輸入的故事想法仍保留在畫面中。",
                        author_input_preserved=True,
                        original_preserved=True,
                    ),
                )

        max_attempts = 3
        profile = OllamaContextProfile.AUTO
        custom_num_ctx: int | None = None
        if mode is LongFormWorkspaceMode.GUIDED:
            _render_guided_runtime_summary(
                services,
                total_target_visible_chars=total_target,
                unit_count=(
                    len(plan.units)
                    if plan is not None
                    else requested_units
                ),
            )
        if mode is LongFormWorkspaceMode.ADVANCED:
            with st.expander("進階執行設定", expanded=True):
                st.session_state.setdefault("story_longform_max_attempts", 3)
                max_attempts = int(
                    st.number_input(
                        "每步最多嘗試",
                        min_value=1,
                        max_value=10,
                        step=1,
                        key="story_longform_max_attempts",
                        on_change=_preserve_workspace_editor_state,
                    )
                )
                profile = OllamaContextProfile(
                    st.selectbox(
                        "Ollama context profile",
                        options=[item.value for item in OllamaContextProfile],
                        key="story_longform_context_profile",
                        on_change=_preserve_workspace_editor_state,
                    )
                )
                if profile is OllamaContextProfile.CUSTOM:
                    st.session_state.setdefault("story_longform_custom_num_ctx", 8_192)
                    custom_num_ctx = int(
                        st.number_input(
                            "自訂 num_ctx",
                            min_value=1_024,
                            max_value=262_144,
                            step=1_024,
                            key="story_longform_custom_num_ctx",
                            on_change=_preserve_workspace_editor_state,
                        )
                    )
                st.caption("技術值只影響之後的執行，不會偷偷開始生成。")

        if confirmed is None:
            st.info("先在中間檢查並確認大綱，正文按鈕才會開放。")
        route_ready, route_message = (
            _long_form_route_readiness(services)
            if confirmed is not None
            else (True, "")
        )
        if route_message:
            st.warning(route_message)
        if st.button(
            "建立並開始正文工作",
            key="story_longform_start_prose",
            type="primary",
            disabled=confirmed is None or not route_ready,
            width="stretch",
        ):
            assert confirmed is not None
            _start_confirmed_long_form_job(
                services,
                project_id=project_id,
                plan=confirmed,
                max_attempts=max_attempts,
                context_profile=profile,
                custom_num_ctx=custom_num_ctx,
            )
        st.caption(
            "可以安全離開：只有按下開始後建立的規劃錨點與工作進度會保存；"
            "未確認的大綱仍只在目前視窗。"
        )


def _render_quick_plan(services: Any, *, project_id: str) -> None:
    """Give the author's idea and editable plan the whole planning width."""

    plan = _stored_plan(_PLAN_DRAFT_KEY)
    confirmed = _stored_plan(_CONFIRMED_PLAN_KEY)
    with st.container(key="story_longform_quick_planner"):
        author_idea = st.text_area(
            "想寫什麼？", key="story_longform_idea", height=130,
            placeholder="一句話、幾段筆記都可以。先說你真正想寫的故事。",
            on_change=_invalidate_workspace_plan,
        )
        target_column, build_column = st.columns([2, 1], vertical_alignment="bottom")
        with target_column:
            st.session_state.setdefault("story_longform_total_target", 20_000)
            total_target = int(
                st.number_input(
                    "全文目標字數（可見字元）", min_value=100, max_value=1_000_000,
                    step=1_000, key="story_longform_total_target",
                    on_change=_invalidate_workspace_plan,
                )
            )
        # Reserve the primary action's position before secondary controls, but
        # process it only after every current-rerun author value is available.
        build_slot = build_column.container()
        with st.expander("類型、語氣與章數（選填）", expanded=False):
            genre = st.text_input(
                "類型（可留白）", key="story_longform_genre",
                placeholder="奇幻、推理、文學……", on_change=_invalidate_workspace_plan,
            )
            tone = st.text_input(
                "語氣／氛圍（可留白）", key="story_longform_tone",
                placeholder="溫柔、緊張、冷峻……", on_change=_invalidate_workspace_plan,
            )
            st.session_state.setdefault("story_longform_auto_units", True)
            automatic_units = st.checkbox(
                "自動建議章數", key="story_longform_auto_units",
                on_change=_invalidate_workspace_plan,
            )
            requested_units = None
            if not automatic_units:
                st.session_state.setdefault("story_longform_requested_units", 4)
                requested_units = int(
                    st.number_input(
                        "章數", min_value=1, max_value=100, step=1,
                        key="story_longform_requested_units", on_change=_invalidate_workspace_plan,
                    )
                )
        with build_slot:
            if st.button(
                "整理本機大綱", key="story_longform_build_plan",
                type="primary" if plan is None else "secondary", width="stretch",
            ):
                try:
                    built = build_local_long_form_plan(
                        mode=LongFormWorkspaceMode.QUICK, author_idea=author_idea,
                        genre=genre, tone=tone, total_target_visible_chars=total_target,
                        requested_unit_count=requested_units,
                        character_world_constraints="", author_constraints="",
                    )
                    st.session_state[_PLAN_EDITOR_REVISION_KEY] = (
                        int(st.session_state.get(_PLAN_EDITOR_REVISION_KEY, 0)) + 1
                    )
                    st.session_state[_PLAN_DRAFT_KEY] = built.model_dump(mode="json")
                    st.session_state.pop(_CONFIRMED_PLAN_KEY, None)
                    st.rerun()
                except _DURABLE_UI_ERRORS as exc:
                    present_exception(
                        exc, operation_zh_tw="整理長篇大綱",
                        key_prefix="story_longform_plan_error",
                        data_safety=ErrorDataSafety(
                            message_zh_tw="大綱尚未建立；你輸入的故事想法仍保留在畫面中。",
                            author_input_preserved=True, original_preserved=True,
                        ),
                    )
        st.caption("整理大綱不會呼叫模型，也不會建立正文或改動現有草稿。")
        if plan is not None:
            with st.container(key="story_longform_quick_review", border=True):
                _render_plan_review(plan, confirmed=confirmed)
        if confirmed is None:
            st.caption("先整理並檢查大綱；確認後，正文按鈕才會開放。")
        route_ready, route_message = (
            _long_form_route_readiness(services)
            if confirmed is not None
            else (True, "")
        )
        if route_message:
            st.warning(route_message)
        if st.button(
            "建立並開始正文工作", key="story_longform_start_prose",
            type="primary",
            disabled=confirmed is None or not route_ready,
            width="stretch",
        ):
            assert confirmed is not None
            _start_confirmed_long_form_job(
                services, project_id=project_id, plan=confirmed, max_attempts=3,
                context_profile=OllamaContextProfile.AUTO, custom_num_ctx=None,
            )
        st.caption(
            "可以安全離開：只有按下開始後建立的規劃錨點與工作進度會保存；"
            "未確認的大綱仍只在目前視窗。"
        )


def _render_guided_plan(services: Any, *, project_id: str) -> None:
    """Six explicit review steps share the existing local plan/start contract."""

    step = int(st.session_state.get(_GUIDED_STEP_KEY, 0))
    route = _guided_route_fingerprint(services)
    if step == 5 and st.session_state.get(_GUIDED_ROUTE_KEY) != route:
        st.session_state[_GUIDED_STEP_KEY] = step = 4
        st.session_state[_GUIDED_CONFIRMED_KEY] = 4
        st.session_state.pop(_GUIDED_FINAL_KEY, None)
        st.info("全域寫作引擎已變更，請重新確認；大綱和作者輸入保持不變。")
    st.markdown(f"**第 {step + 1} / 6 步 · {_GUIDED_STEPS[step]}**")
    st.caption(" → ".join(f"{index}. {label}" for index, label in enumerate(_GUIDED_STEPS, 1)))
    navigation = st.container(key="story_longform_guided_navigation")
    plan = _stored_plan(_PLAN_DRAFT_KEY)
    confirmed = _stored_plan(_CONFIRMED_PLAN_KEY)
    total_target = int(st.session_state.get("story_longform_total_target", 20_000))
    automatic = bool(st.session_state.get("story_longform_auto_units", True))
    units = None if automatic else int(st.session_state.get("story_longform_requested_units", 4))
    can_continue = True

    if step == 0:
        st.text_area(
            "想寫什麼？", key="story_longform_idea", height=160,
            placeholder="一句話、幾段筆記都可以。先說你真正想寫的故事。",
            on_change=_invalidate_workspace_plan,
        )
        genre_column, tone_column = st.columns(2)
        genre_column.text_input(
            "類型（可留白）", key="story_longform_genre", on_change=_invalidate_workspace_plan,
        )
        tone_column.text_input(
            "語氣／氛圍（可留白）", key="story_longform_tone", on_change=_invalidate_workspace_plan,
        )
        st.session_state.setdefault("story_longform_total_target", 20_000)
        st.number_input(
            "全文目標字數（可見字元）", min_value=100, max_value=1_000_000,
            step=1_000, key="story_longform_total_target",
            on_change=_invalidate_workspace_plan,
        )
        st.session_state.setdefault("story_longform_auto_units", True)
        automatic = st.checkbox(
            "自動建議章數", key="story_longform_auto_units",
            on_change=_invalidate_workspace_plan,
        )
        if not automatic:
            st.session_state.setdefault("story_longform_requested_units", 4)
            st.number_input(
                "章數", min_value=1, max_value=100, step=1,
                key="story_longform_requested_units", on_change=_invalidate_workspace_plan,
            )
        can_continue = bool(str(st.session_state.get("story_longform_idea", "")).strip())
    elif step == 1:
        st.text_area(
            "人物與世界重點（可留白）", key="story_longform_character_world",
            height=140, on_change=_invalidate_workspace_plan,
        )
        st.text_area(
            "必須遵守／避免（可留白）", key="story_longform_author_constraints",
            height=140, on_change=_invalidate_workspace_plan,
        )
        st.caption("這些限制會完整保留供下一步檢查；不會因切步而保存作品或呼叫模型。")
    elif step == 2:
        if st.button(
            "整理本機大綱", key="story_longform_build_plan",
            type="primary" if plan is None else "secondary", width="stretch",
        ):
            try:
                built = build_local_long_form_plan(
                    mode=LongFormWorkspaceMode.GUIDED,
                    author_idea=str(st.session_state.get("story_longform_idea", "")),
                    genre=str(st.session_state.get("story_longform_genre", "")),
                    tone=str(st.session_state.get("story_longform_tone", "")),
                    total_target_visible_chars=total_target,
                    requested_unit_count=units,
                    character_world_constraints=str(
                        st.session_state.get("story_longform_character_world", "")
                    ),
                    author_constraints=str(
                        st.session_state.get("story_longform_author_constraints", "")
                    ),
                )
                _invalidate_guided_review()
                st.session_state[_PLAN_EDITOR_REVISION_KEY] = (
                    int(st.session_state.get(_PLAN_EDITOR_REVISION_KEY, 0)) + 1
                )
                st.session_state[_PLAN_DRAFT_KEY] = built.model_dump(mode="json")
                st.session_state.pop(_CONFIRMED_PLAN_KEY, None)
                st.rerun()
            except _DURABLE_UI_ERRORS as exc:
                present_exception(
                    exc, operation_zh_tw="整理長篇大綱",
                    key_prefix="story_longform_plan_error",
                    data_safety=ErrorDataSafety(
                        message_zh_tw="大綱尚未建立；你輸入的故事想法與限制仍保留。",
                        author_input_preserved=True, original_preserved=True,
                    ),
                )
        _render_plan_review(plan, confirmed=confirmed, review_step=2)
        can_continue = plan is not None
    elif step == 3:
        _render_plan_review(plan, confirmed=confirmed, review_step=3)
        can_continue = confirmed is not None
    else:
        _render_guided_runtime_summary(
            services, total_target_visible_chars=total_target,
            unit_count=len(plan.units) if plan is not None else units,
        )
        if step == 4:
            st.caption("確認目前全域 provider／model；這裡不修改 AI 設定，也不執行生成。")
            can_continue = bool(route) and confirmed is not None
        else:
            st.markdown("**本次上下文**")
            st.write("已確認的大綱、逐章方向、故事構想、人物／世界限制與作者必守事項。")
            st.caption(
                "以可見字元驗證篇幅；每步最多嘗試 3 次，上下文採自動配置。"
                "必要事實不能容納時會明確停止，不會靜默截斷；不會把未達標結果當作完整正文。"
            )
            reviewed = st.checkbox(
                "我已確認篇幅、上下文與全域寫作引擎",
                key=_GUIDED_FINAL_KEY,
            )
            ready = (
                reviewed and confirmed is not None and bool(route)
                and st.session_state.get(_GUIDED_ROUTE_KEY) == route
                and int(st.session_state.get(_GUIDED_CONFIRMED_KEY, 0)) >= 5
            )
            if st.button(
                "建立並開始正文工作", key="story_longform_start_prose",
                type="primary", disabled=not ready, width="stretch",
            ) and ready:
                assert confirmed is not None
                _start_confirmed_long_form_job(
                    services, project_id=project_id, plan=confirmed, max_attempts=3,
                    context_profile=OllamaContextProfile.AUTO, custom_num_ctx=None,
                )
    with navigation:
        back, next_column = st.columns(2)
        back.button(
            "返回上一步", key="story_longform_guided_back", disabled=step == 0,
            on_click=_move_guided_step, args=(step - 1,), width="stretch",
        )
        if step < 5:
            next_column.button(
                "確認此步，繼續", key="story_longform_guided_next",
                disabled=not can_continue, on_click=_move_guided_step,
                args=(step + 1,), kwargs={"confirm": True, "route": route}, width="stretch",
            )
    st.caption("切步與確認都不保存作品；只有最後明確開始，才建立可恢復的正文工作。")


def _render_guided_runtime_summary(
    services: Any,
    *,
    total_target_visible_chars: int,
    unit_count: int | None,
) -> None:
    """Show the route/context checkpoint without turning it into settings UI."""

    st.markdown("##### 執行前確認")
    try:
        preferences = services.ai_runtime_preferences.get_preferences()
        route, _quality = durable_route_from_preferences(preferences)
        st.write(f"寫作引擎：**{_author_provider_label(route.provider, route.model)}**")
    except DurableRouteConfigurationError as exc:
        st.warning(str(exc))
    except ValueError:
        st.warning(_LONG_FORM_ENGINE_NOT_READY_MESSAGE)
    units_copy = f"，分成 {unit_count} 章" if unit_count is not None else "，章數由系統建議"
    st.write(f"篇幅：**{total_target_visible_chars:,} 個可見字元**{units_copy}")
    st.caption(
        "開始時會把已確認大綱、角色／世界限制與正式規劃錨點保存為同一份上下文；"
        "整理大綱本身不會呼叫模型。"
    )


def _render_plan_review(
    plan: LongFormPlanDraft | None,
    *,
    confirmed: LongFormPlanDraft | None,
    review_step: int | None = None,
) -> None:
    st.subheader("2. 檢查大綱")
    if plan is None:
        st.info("填寫故事構想後按「整理本機大綱」，這裡會出現可編輯的章名與章節方向。")
        return
    revision = int(st.session_state.get(_PLAN_EDITOR_REVISION_KEY, 0))
    outline_key = f"story_longform_outline_{revision}"
    outline = str(st.session_state.get(outline_key, plan.outline_summary))
    if review_step != 3:
        st.session_state.setdefault(outline_key, plan.outline_summary)
        outline = st.text_area(
            "全篇方向", key=outline_key,
            height=120, on_change=_invalidate_confirmed_plan,
        )
    if review_step == 2:
        return
    titles: list[str] = []
    summaries: list[str] = []
    for unit in plan.units:
        st.markdown(
            f"**第 {unit.ordinal} 章｜目標 {unit.target_visible_chars:,} 個可見字元**"
        )
        title_key = f"story_longform_unit_title_{revision}_{unit.ordinal}"
        summary_key = f"story_longform_unit_summary_{revision}_{unit.ordinal}"
        st.session_state.setdefault(title_key, unit.title)
        st.session_state.setdefault(summary_key, unit.summary)
        titles.append(
            st.text_input(
                f"第 {unit.ordinal} 章章名",
                key=title_key,
                on_change=_invalidate_confirmed_plan,
            )
        )
        summaries.append(
            st.text_area(
                f"第 {unit.ordinal} 章方向",
                key=summary_key,
                height=90,
                on_change=_invalidate_confirmed_plan,
            )
        )
    if confirmed is not None:
        st.success("你已確認這份大綱；仍可修改，修改後需要再次確認。")
    if st.button(
        "我已檢查，確認這份大綱",
        key=f"story_longform_confirm_plan_{revision}",
        type="primary" if confirmed is None else "secondary",
        width="stretch",
    ):
        try:
            accepted = confirm_long_form_plan(
                plan,
                outline_summary=outline,
                unit_titles=titles,
                unit_summaries=summaries,
            )
            st.session_state[_CONFIRMED_PLAN_KEY] = accepted.model_dump(mode="json")
            st.success("大綱已確認；尚未呼叫 provider，也尚未建立正文。")
            st.rerun()
        except ValueError as exc:
            present_exception(
                exc,
                operation_zh_tw="確認長篇大綱",
                key_prefix="story_longform_confirm_error",
                data_safety=ErrorDataSafety(
                    message_zh_tw="大綱尚未確認；所有編輯內容仍保留在目前畫面。",
                    author_input_preserved=True,
                    original_preserved=True,
                ),
            )


def _start_confirmed_long_form_job(
    services: Any,
    *,
    project_id: str,
    plan: LongFormPlanDraft,
    max_attempts: int,
    context_profile: OllamaContextProfile,
    custom_num_ctx: int | None,
) -> None:
    try:
        request = prepare_confirmed_long_form_workflow(
            services,
            st.session_state,
            project_id=project_id,
            plan=plan,
            max_attempts=max_attempts,
            context_profile=context_profile,
            custom_num_ctx=custom_num_ctx,
        )
        job = services.durable_generation.create_workflow(request)
        worker = getattr(services, "durable_generation_worker", None)
        credential_available = background_provider_credential_available(
            services,
            provider_name=str(job.route.get("provider", "")),
        )
        if worker is not None and credential_available:
            worker.submit(job.id, max_steps=100)
            st.success("正文工作已建立並在背景開始；大綱與每章保存位置都已固定。")
        else:
            st.success("正文工作已建立並保存；目前尚未呼叫 provider。")
        st.session_state[_PENDING_SELECTED_JOB_KEY] = job.id
        st.rerun()
    except _DURABLE_UI_ERRORS as exc:
        present_exception(
            exc,
            operation_zh_tw="依確認的大綱建立長篇正文工作",
            key_prefix="story_longform_start_error",
            data_safety=ErrorDataSafety(
                message_zh_tw=(
                    "正文工作未被標示為成功；作者輸入與確認的大綱仍保留。"
                    "可能已完成的本機規劃錨點可由同一份大綱安全重試，正式正文未被覆寫。"
                ),
                author_input_preserved=True,
                original_preserved=True,
                completed_work_preserved=True,
            ),
            retry_permitted=True,
            fallback_permitted=False,
        )


def _author_job_label(job: GenerationJob) -> str:
    plan = job.source_snapshot.get("workspace_plan")
    title = plan.get("title") if isinstance(plan, Mapping) else None
    author_title = str(title).strip() if title else "長篇工作"
    return f"{author_title}｜{_JOB_STATUS_LABELS[job.status]}｜{job.created_at[:16]}"


def _author_provider_label(provider: object, model: object) -> str:
    provider_id = str(provider).strip().casefold()
    model_id = str(model).strip()
    if provider_id == "offline":
        return "本機離線工作流"
    if provider_id == "ollama":
        return f"Ollama（{model_id or '未選模型'}）"
    if provider_id == "openai":
        return f"OpenAI（{model_id or '未選模型'}）"
    return f"{provider_id or '未知引擎'}（{model_id or '未選模型'}）"


def _render_author_job(services: Any, job: GenerationJob) -> None:
    steps = tuple(services.durable_generation.get_steps(job.id))
    candidates = tuple(services.durable_generation.list_candidates(job.id))
    focus = _take_continuity_focus(job.id)
    mode = _job_workspace_mode(job)
    worker = getattr(services, "durable_generation_worker", None)
    worker_snapshot = None if worker is None else worker.poll(job.id)
    units = project_unit_progress(
        job, steps,
        worker_active=bool(worker_snapshot is not None and worker_snapshot.is_active),
        author_decisions=UnitResolutionService(services.lifecycle).list_decisions(job.id),
    )
    pinned_offline = _is_pinned_offline_job(job)
    credential_available = True
    if not pinned_offline:
        credential_available = (
            background_provider_credential_available(
                services,
                provider_name=str(job.route.get("provider", "")),
            )
            if worker is not None
            else provider_action_credential_available(
                services,
                st.session_state,
                provider_name=str(job.route.get("provider", "")),
            )
        )

    shell = st.container(key="story_longform_workspace_shell")
    # Existing-work DOM is prose first so narrow screens and keyboard users
    # reach the manuscript before supporting drawers. Desktop CSS reorders
    # the keyed columns to outline / prose / assistant at 25 / 50 / 25.
    prose_column, outline_column, assistant_column = shell.columns([2, 1, 1])
    with outline_column, st.container(key="story_longform_outline_panel", border=True):
        st.subheader("大綱與章節")
        with st.expander("顯示／收合大綱", expanded=True):
            _render_author_outline(job, units)
    with prose_column, st.container(key="story_longform_prose_panel", border=True):
        _render_author_prose(
            services,
            job,
            steps,
            units,
            candidates,
            focus=focus,
            advanced=mode is LongFormWorkspaceMode.ADVANCED,
        )
    with assistant_column, st.container(key="story_longform_assistant_panel", border=True):
        st.subheader("寫作助理")
        with st.expander("顯示／收合助理", expanded=True):
            _render_author_assistant(
                services,
                job,
                steps,
                units,
                worker_snapshot=worker_snapshot,
                credential_available=credential_available,
                advanced=mode is LongFormWorkspaceMode.ADVANCED,
            )


def _job_workspace_mode(job: GenerationJob) -> LongFormWorkspaceMode:
    raw = job.source_snapshot.get("workspace_mode", LongFormWorkspaceMode.ADVANCED.value)
    try:
        return LongFormWorkspaceMode(str(raw))
    except ValueError:
        return LongFormWorkspaceMode.ADVANCED


def _is_pinned_offline_job(job: GenerationJob) -> bool:
    """Use only the job's frozen route when gating unsupported execution."""

    return str(job.route.get("provider", "")).strip().casefold() == "offline"


def _render_author_outline(
    job: GenerationJob,
    units: tuple[AuthorUnitProgress, ...],
) -> None:
    plan = job.source_snapshot.get("workspace_plan")
    if isinstance(plan, Mapping) and str(plan.get("outline_summary", "")).strip():
        st.caption(str(plan["outline_summary"]))
    completed = sum(unit.status.value == "complete" for unit in units)
    st.write(f"已完成 **{completed} / {len(units)}** 章")
    for unit in units:
        status_label = {
            "waiting": "等待",
            "planning": "規劃中",
            "writing": "撰寫中",
            "checking": "檢查中",
            "revising": "修訂中",
            "complete": "完成",
            "paused": "已暫停",
            "resumable": "可繼續",
            "cancelled": "已取消",
            "failed": "需要重試",
            "needs_author_input": "等你確認",
        }[unit.status.value]
        st.markdown(f"**{unit.ordinal}. {unit.title}**")
        st.caption(
            f"{status_label}｜已保存 {unit.visible_chars:,} / "
            f"{unit.target_visible_chars:,} 個可見字元"
        )
    st.caption("已完成章節不會因繼續、重試或重新開啟而重新生成。")


def _take_continuity_focus(job_id: str) -> dict[str, object] | None:
    raw = st.session_state.pop(_CONTINUITY_FOCUS_KEY, None)
    if not isinstance(raw, Mapping) or raw.get("job_id") != job_id:
        return None
    unit_id = raw.get("unit_id")
    if isinstance(unit_id, str):
        st.session_state[f"story_longform_prose_unit_{job_id}"] = unit_id
    return dict(raw)


def _render_author_prose(
    services: Any,
    job: GenerationJob,
    steps: tuple[GenerationStep, ...],
    units: tuple[AuthorUnitProgress, ...],
    candidates: tuple[GenerationCandidate, ...],
    *,
    focus: dict[str, object] | None,
    advanced: bool,
) -> None:
    st.subheader("正文")
    if units:
        unit_by_id = {unit.unit_id: unit for unit in units}
        selected_id = st.selectbox(
            "查看章節",
            options=list(unit_by_id),
            format_func=lambda unit_id: (
                f"{unit_by_id[unit_id].ordinal}. {unit_by_id[unit_id].title}"
            ),
            key=f"story_longform_prose_unit_{job.id}",
        )
        selected = unit_by_id[selected_id]
        if selected.saved_text:
            st.text_area(
                "已保存正文（唯讀）",
                value=selected.saved_text,
                height=430,
                disabled=True,
                key=f"story_longform_saved_prose_{job.id}_{selected.unit_id}",
            )
        else:
            st.info("這一章還沒有已保存的正文；大綱與工作位置已經保留。")
        if focus is not None and focus.get("unit_id") == selected.unit_id:
            _render_focused_paragraph(selected.saved_text, focus.get("paragraph_index"))
        render_unit_resolution_panel(
            services, job, steps, candidates, selected.unit_id, run_action=_run_action,
        )
    else:
        st.info("這個舊工作沒有可辨識的章節資料；進階診斷仍可查看原始步驟。")

    if candidates:
        if advanced:
            _render_candidate(services, candidates)
        else:
            _render_author_candidate(services, candidates)
    else:
        st.caption("完整候選會在所有章節通過篇幅與品質檢查後出現在這裡。")


def _render_focused_paragraph(text: str, raw_index: object) -> None:
    paragraph_index = raw_index if isinstance(raw_index, int) else None
    if paragraph_index is None:
        st.success("已定位到連貫性問題所在章節。")
        return
    paragraphs = [paragraph.strip() for paragraph in text.split("\n\n") if paragraph.strip()]
    if 1 <= paragraph_index <= len(paragraphs):
        st.success(f"已定位到第 {paragraph_index} 段：")
        st.code(paragraphs[paragraph_index - 1], language=None)
    else:
        st.info(f"問題指向第 {paragraph_index} 段；目前保存文字尚無可顯示的對應段落。")


def _render_author_candidate(
    services: Any,
    candidates: tuple[GenerationCandidate, ...],
) -> None:
    candidate_ids = [candidate.id for candidate in candidates]
    candidate_id = st.selectbox(
        "候選版本",
        options=candidate_ids,
        format_func=lambda value: f"候選 {candidate_ids.index(value) + 1}",
        key="story_longform_author_candidate",
    )
    candidate = next(item for item in candidates if item.id == candidate_id)
    st.markdown("#### 已確認單章候選" if is_unit_candidate(candidate) else "#### 完整長篇候選")
    if is_unit_candidate(candidate):
        st.warning("這是作者確認的單章片段，並非完整長篇；套用只會建立原對應場景的新工作草稿。")
    st.text_area(
        _candidate_preview_label(candidate),
        value=candidate.content_text,
        height=500,
        disabled=True,
        key=f"story_longform_author_candidate_text_{candidate.id}",
    )
    quality_passed = candidate.quality_report.get("passed")
    if quality_passed is True:
        st.success("完整候選已通過保存的最終品質檢查；套用與復原狀態以持久紀錄為準。")
    elif quality_passed is False:
        st.warning("品質檢查仍有未通過項目；候選已保留，不會自動套用。")
    else:
        st.caption("品質結果已隨候選保存；進階模式可查看完整檢查資料。")
    _render_candidate_apply(services, candidate, author_facing=True)


def _render_author_assistant(
    services: Any,
    job: GenerationJob,
    steps: tuple[GenerationStep, ...],
    units: tuple[AuthorUnitProgress, ...],
    *,
    worker_snapshot: DurableWorkerSnapshot | None,
    credential_available: bool,
    advanced: bool,
) -> None:
    st.info(author_progress_message(job, units))
    raw_target_visible_chars = job.length_spec.get("target_visible_chars", 0)
    target_visible_chars = (
        raw_target_visible_chars
        if isinstance(raw_target_visible_chars, int)
        and not isinstance(raw_target_visible_chars, bool)
        and raw_target_visible_chars >= 0
        else 0
    )
    st.caption(
        "本次寫作引擎："
        f"{_author_provider_label(job.route.get('provider'), job.route.get('model'))}｜"
        f"全文目標 {target_visible_chars:,} "
        "個可見字元"
    )
    if worker_snapshot is not None and worker_snapshot.is_active:
        st.caption("背景撰寫中；新內容會在安全保存點逐步出現。")
        if st.button(
            "更新進度",
            key=f"story_longform_refresh_{job.id}",
            width="stretch",
        ):
            st.rerun()
    provider_name = str(job.route.get("provider", ""))
    if provider_name == "openai" and not credential_available:
        st.warning(
            "此工作使用的 OpenAI Key 若只存在目前視窗，背景工作無法讀取。"
            "作品、大綱與進度已保存，不會偽裝成功或改用短版離線輸出；"
            "需要時可從「狀態」進入 AI 設定。"
        )

    if advanced:
        with st.expander("進階控制、保存點與來源", expanded=True):
            st.caption(
                f"job `{job.id}`｜provider `{provider_name}`｜"
                f"model `{job.route.get('model', '—')}`｜"
                f"保存序號 `{job.checkpoint_sequence}`"
            )
            _render_controls(
                services,
                job,
                steps,
                worker_snapshot=worker_snapshot,
                credential_available=credential_available,
            )
            st.dataframe(durable_step_rows(steps), width="stretch", hide_index=True)
            with st.expander("內容來源快照"):
                st.json(
                    {
                        "workspace_plan_fingerprint": job.source_snapshot.get(
                            "workspace_plan_fingerprint", ""
                        ),
                        "context_fingerprint": job.source_snapshot.get(
                            "context_fingerprint", ""
                        ),
                        "scene_card_version_id": job.source_snapshot.get(
                            "scene_card_version_id", ""
                        ),
                        "measurement_unit": job.length_spec.get("measurement_unit", ""),
                        "target_visible_chars": job.length_spec.get(
                            "target_visible_chars", ""
                        ),
                        "ollama_context_profile": job.length_spec.get(
                            "ollama_context_profile", {}
                        ),
                    }
                )
    else:
        _render_author_controls(
            services,
            job,
            steps,
            worker_snapshot=worker_snapshot,
            credential_available=credential_available,
        )

    issues = project_continuity_issues(steps)
    if issues:
        st.markdown("##### 連貫性提醒")
        for index, issue in enumerate(issues, start=1):
            location = (
                f"第 {issue.paragraph_index} 段"
                if issue.paragraph_index is not None
                else "本章"
            )
            st.warning(f"〈{issue.unit_title}〉{location}｜{issue.message}")
            st.button(
                "在正文中定位",
                key=f"story_longform_focus_{job.id}_{index}",
                on_click=_queue_continuity_focus,
                args=(job.id, issue.unit_id, issue.paragraph_index),
                width="stretch",
            )
    st.caption(
        "可以安全關閉或離開：已完成章節、未完成但已保存的片段，"
        "以及最後安全保存位置都在本機。"
        "重新開啟後選「繼續上次工作」；繼續或重試不會重寫已完成章節。"
    )


def _queue_continuity_focus(
    job_id: str,
    unit_id: str,
    paragraph_index: int | None,
) -> None:
    st.session_state[_CONTINUITY_FOCUS_KEY] = {
        "job_id": job_id,
        "unit_id": unit_id,
        "paragraph_index": paragraph_index,
    }


def _render_author_controls(
    services: Any,
    job: GenerationJob,
    steps: tuple[GenerationStep, ...],
    *,
    worker_snapshot: DurableWorkerSnapshot | None,
    credential_available: bool,
) -> None:
    worker = getattr(services, "durable_generation_worker", None)
    worker_active = bool(worker_snapshot is not None and worker_snapshot.is_active)
    pinned_offline = _is_pinned_offline_job(job)
    if pinned_offline:
        st.warning(_PINNED_OFFLINE_JOB_MESSAGE)
    resumable = job.status in {
        JobStatus.PAUSED,
        JobStatus.NEEDS_AUTHOR_INPUT,
        JobStatus.FAILED,
    }
    terminal = job.status in {JobStatus.COMPLETED, JobStatus.CANCELLED}
    if job.status is JobStatus.FAILED:
        continue_label = "只重試失敗章節"
    elif resumable or (
        not worker_active and any(step.status is StepStatus.INTERRUPTED for step in steps)
    ):
        continue_label = "從保存位置繼續"
    else:
        continue_label = "繼續生成"
    if st.button(
        continue_label,
        key=f"story_longform_continue_{job.id}",
        type="primary",
        disabled=pinned_offline or terminal or worker_active or not credential_available,
        width="stretch",
    ):
        continue_action: Callable[[], object]
        if job.status is JobStatus.FAILED:
            continue_action = partial(
                _resume_or_retry_and_submit,
                services,
                job,
                worker=worker,
                retry=True,
            )
        elif resumable:
            continue_action = partial(
                _resume_or_retry_and_submit,
                services,
                job,
                worker=worker,
                retry=False,
            )
        elif worker is not None:
            continue_action = partial(worker.submit, job.id, max_steps=100)
        else:
            continue_action = partial(
                _run_with_session_credential,
                services,
                job,
                lambda secret: services.durable_generation.run_next(
                    job.id,
                    session_override=secret,
                ),
            )
        _run_action(
            continue_action,
            operation_zh_tw=continue_label,
            key_prefix=f"story_longform_continue_error_{job.id}",
            data_safety=_author_progress_safety(),
            job_id=job.id,
            step_id=_next_step_id(steps),
            retry_permitted=True,
        )

    pause_column, cancel_column = st.columns(2)
    if pause_column.button(
        "暫停",
        key=f"story_longform_pause_{job.id}",
        disabled=job.status not in ACTIVE_JOB_STATUSES,
        width="stretch",
    ):
        pause_action: Callable[[], object] = (
            (lambda: worker.request_pause(job.id))
            if worker_active and worker is not None
            else (lambda: services.durable_generation.request_pause(job.id))
        )
        _run_action(
            pause_action,
            operation_zh_tw="在安全保存點暫停",
            key_prefix=f"story_longform_pause_error_{job.id}",
            data_safety=_author_progress_safety(),
            job_id=job.id,
            step_id=_next_step_id(steps),
            retry_permitted=True,
        )
    confirm_key = f"story_longform_cancel_confirm_{job.id}"
    if cancel_column.button(
        "取消…",
        key=f"story_longform_cancel_{job.id}",
        disabled=terminal,
        width="stretch",
    ):
        st.session_state[confirm_key] = True
        st.rerun()
    if bool(st.session_state.get(confirm_key)):
        st.warning("取消後不再寫新內容；所有已完成章節與已保存片段仍會保留。")
        if st.button(
            "確認取消，保留已完成內容",
            key=f"story_longform_cancel_yes_{job.id}",
            width="stretch",
        ):
            cancel_action: Callable[[], object] = (
                (lambda: worker.request_cancel(job.id))
                if worker_active and worker is not None
                else (lambda: services.durable_generation.request_cancel(job.id))
            )
            _run_action(
                cancel_action,
                operation_zh_tw="取消長篇工作",
                key_prefix=f"story_longform_cancel_error_{job.id}",
                data_safety=_author_progress_safety(),
                job_id=job.id,
                step_id=_next_step_id(steps),
                retry_permitted=True,
                clear_state_keys=(confirm_key,),
            )
        if st.button(
            "返回，不取消",
            key=f"story_longform_cancel_no_{job.id}",
            width="stretch",
        ):
            st.session_state.pop(confirm_key, None)
            st.rerun()


def _resume_or_retry_and_submit(
    services: Any,
    job: GenerationJob,
    *,
    worker: Any,
    retry: bool,
) -> object:
    outcome: object
    if retry:
        outcome = services.durable_generation.retry(job.id)
    else:
        outcome = services.durable_generation.resume(job.id)
    if worker is not None:
        submitted: object = worker.submit(job.id, max_steps=100)
        return submitted
    return outcome


def _render_create(services: Any, *, project_id: str, scene_id: str) -> None:
    st.markdown("#### 建立新工作流")
    brief = st.text_area(
        "長篇 brief",
        key="story_durable_brief",
        placeholder="描述主線、衝突、必須保留與不得出現的內容。",
        height=120,
    )
    titles_raw = st.text_area(
        "單元／章節標題（每行一個）",
        value="第一章\n第二章",
        key="story_durable_unit_titles",
        height=90,
    )
    left, right = st.columns(2)
    target_words = int(
        left.number_input(
            "每單元目標字數",
            min_value=100,
            max_value=100_000,
            value=1_500,
            step=100,
            key="story_durable_target_words",
        )
    )
    max_attempts = int(
        right.number_input(
            "每步最多嘗試",
            min_value=1,
            max_value=10,
            value=3,
            step=1,
            key="story_durable_max_attempts",
        )
    )
    if st.button(
        "建立耐久長篇工作流",
        key="story_durable_create",
        type="primary",
    ):
        try:
            request = prepare_story_durable_workflow(
                services,
                st.session_state,
                project_id=project_id,
                scene_id=scene_id,
                author_brief=brief,
                unit_titles=tuple(line.strip() for line in titles_raw.splitlines() if line.strip()),
                target_words_per_unit=target_words,
                max_attempts_per_step=max_attempts,
            )
            job = services.durable_generation.create_workflow(request)
            st.session_state[_SELECTED_JOB_KEY] = job.id
            st.success("已建立；尚未呼叫 provider。可先檢查完整 step graph。")
            st.rerun()
        except _DURABLE_UI_ERRORS as exc:
            present_exception(
                exc,
                operation_zh_tw="建立耐久長篇工作流",
                key_prefix="story_durable_create_error",
                data_safety=ErrorDataSafety(
                    message_zh_tw=(
                        "這次沒有建立新工作流；畫面中的 brief 與章節標題仍保留，"
                        "既有工作流、正式 Scene 與已完成步驟均未變更。"
                    ),
                    author_input_preserved=True,
                    original_preserved=True,
                    completed_work_preserved=True,
                ),
                retry_permitted=False,
            )


def _select_job(jobs: tuple[GenerationJob, ...]) -> GenerationJob:
    options = [job.id for job in jobs]
    current = st.session_state.get(_SELECTED_JOB_KEY)
    if current not in options:
        st.session_state[_SELECTED_JOB_KEY] = options[0]
    selected = st.selectbox(
        "耐久工作流",
        options=options,
        format_func=lambda job_id: _job_label(next(job for job in jobs if job.id == job_id)),
        key=_SELECTED_JOB_KEY,
    )
    return next(job for job in jobs if job.id == selected)


def _job_label(job: GenerationJob) -> str:
    return (
        f"{_JOB_STATUS_LABELS[job.status]} ({job.status.value})｜"
        f"{job.current_step_index}/{job.total_steps_hint}｜{job.id[-12:]}"
    )


def _render_job(services: Any, job: GenerationJob) -> None:
    st.markdown("#### 真實執行狀態")
    st.write(
        f"狀態 **{_JOB_STATUS_LABELS[job.status]}** (`{job.status.value}`)｜"
        f"已提交步驟 `{job.current_step_index}` / "
        f"`{job.total_steps_hint}`｜DB checkpoint `{job.checkpoint_sequence}`"
    )
    if job.last_checkpoint_step_id:
        st.caption(
            f"最後 checkpoint：{job.last_checkpoint_step_id}｜"
            f"{job.last_checkpoint_at or '時間未記錄'}"
        )
    worker = getattr(services, "durable_generation_worker", None)
    worker_snapshot: DurableWorkerSnapshot | None = None if worker is None else worker.poll(job.id)
    if worker_snapshot is not None:
        if worker_snapshot.is_active:
            st.info(
                "背景執行中；每個串流 delta 都會寫入 partial checkpoint。"
                "可用下方控制要求暫停／取消，或更新畫面查看最新持久狀態。"
            )
        elif worker_snapshot.status is DurableWorkerStatus.FAILED:
            present_exception(
                DurableWorkflowError(worker_snapshot.safe_error_message),
                operation_zh_tw="背景執行耐久長篇工作流",
                key_prefix=f"story_durable_worker_error_{job.id}",
                data_safety=_durable_progress_safety(),
                provider_label="長篇背景執行器",
                retry_permitted=True,
                fallback_permitted=False,
                job_id=job.id,
            )
        elif worker_snapshot.result is not None:
            st.caption(
                f"上次背景工作結果：{_DISPOSITION_LABELS[worker_snapshot.result.disposition]}"
            )
    provider_name = str(job.route.get("provider", ""))
    pinned_offline = _is_pinned_offline_job(job)
    credential_available = True
    if not pinned_offline:
        credential_available = (
            background_provider_credential_available(
                services,
                provider_name=provider_name,
            )
            if worker is not None
            else provider_action_credential_available(
                services,
                st.session_state,
                provider_name=provider_name,
            )
        )
    if provider_name == "openai" and not credential_available:
        if worker is not None:
            st.warning(
                "此工作流使用 OpenAI，但 durable job 與背景執行器都不保存 session "
                "API Key。背景執行請使用裝置安全保存／OPENAI_API_KEY；"
                "在此之前執行按鈕會停用，已完成 step 不會重跑。"
            )
        else:
            st.warning(
                "此工作流使用 OpenAI，但目前沒有可用憑證。"
                "請先在全域 AI 設定提供本次 action 的 Key，或使用裝置安全保存／"
                "OPENAI_API_KEY；已完成 step 不會重跑。"
            )
    steps = services.durable_generation.get_steps(job.id)
    _render_controls(
        services,
        job,
        steps,
        worker_snapshot=worker_snapshot,
        credential_available=credential_available,
    )
    st.dataframe(
        durable_step_rows(steps),
        width="stretch",
        hide_index=True,
    )
    for step in steps:
        if step.partial and step.partial_text:
            st.warning(
                f"partial checkpoint：step {step.ordinal + 1} "
                f"{_STEP_KIND_LABELS[step.step_kind]} ({step.step_kind.value})｜"
                f"stable offset {step.stable_boundary_offset}"
            )
            st.code(step.partial_text, language=None)
    candidates = services.durable_generation.list_candidates(job.id)
    if candidates:
        _render_candidate(services, candidates)


def _render_controls(
    services: Any,
    job: GenerationJob,
    steps: tuple[GenerationStep, ...],
    *,
    worker_snapshot: DurableWorkerSnapshot | None,
    credential_available: bool,
) -> None:
    worker = getattr(services, "durable_generation_worker", None)
    worker_active = bool(worker_snapshot is not None and worker_snapshot.is_active)
    pinned_offline = _is_pinned_offline_job(job)
    if pinned_offline:
        st.warning(_PINNED_OFFLINE_JOB_MESSAGE)
    if worker_snapshot is not None and st.button(
        "更新背景執行狀態",
        key=f"story_durable_worker_refresh_{job.id}",
        type="secondary",
    ):
        st.rerun()
    run_next_col, bounded_col, budget_col = st.columns([1, 1, 0.8])
    blocked = (
        pinned_offline
        or job.status
        in {
            JobStatus.COMPLETED,
            JobStatus.CANCELLED,
            JobStatus.PAUSED,
            JobStatus.FAILED,
            JobStatus.NEEDS_AUTHOR_INPUT,
        }
        or worker_active
        or not credential_available
    )
    next_step_id = _next_step_id(steps)
    if run_next_col.button(
        "執行下一步",
        key=f"story_durable_next_{job.id}",
        disabled=blocked,
        type="primary" if not blocked else "secondary",
        help="只執行並提交下一個尚未完成的持久步驟。",
    ):
        run_action: Callable[[], object]
        if worker is not None:
            run_action = partial(worker.submit, job.id, max_steps=1)
        else:

            def foreground_next_action() -> object:
                return _run_with_session_credential(
                    services,
                    job,
                    lambda secret: services.durable_generation.run_next(
                        job.id, session_override=secret
                    ),
                )

            run_action = foreground_next_action
        _run_action(
            run_action,
            operation_zh_tw="執行下一個耐久步驟",
            key_prefix=f"story_durable_run_error_{job.id}",
            data_safety=_durable_progress_safety(),
            job_id=job.id,
            step_id=next_step_id,
            retry_permitted=True,
        )
    max_steps = int(
        budget_col.number_input(
            "本次步數",
            min_value=1,
            max_value=100,
            value=5,
            step=1,
            key=f"story_durable_budget_{job.id}",
        )
    )
    if bounded_col.button(
        "有界執行多步",
        key=f"story_durable_bounded_{job.id}",
        disabled=blocked,
        type="secondary",
        help="最多執行指定步數；遇到暫停、取消、失敗或完成便停止。",
    ):
        bounded_action: Callable[[], object]
        if worker is not None:
            bounded_action = partial(worker.submit, job.id, max_steps=max_steps)
        else:

            def foreground_bounded_action() -> object:
                return _run_with_session_credential(
                    services,
                    job,
                    lambda secret: run_bounded(
                        services.durable_generation,
                        job.id,
                        max_steps=max_steps,
                        session_override=secret,
                    ),
                )

            bounded_action = foreground_bounded_action
        _run_action(
            bounded_action,
            operation_zh_tw="有界執行耐久步驟",
            key_prefix=f"story_durable_bounded_error_{job.id}",
            data_safety=_durable_progress_safety(),
            job_id=job.id,
            step_id=next_step_id,
            retry_permitted=True,
        )

    pause_col, cancel_col, resume_col, recover_col = st.columns(4)
    if pause_col.button(
        "要求暫停",
        key=f"story_durable_pause_{job.id}",
        disabled=job.status not in ACTIVE_JOB_STATUSES,
        type="secondary",
        help="目前 provider 呼叫結束後，在下一個安全 checkpoint 暫停。",
    ):
        pause_action: Callable[[], object]
        if worker_active and worker is not None:
            pause_action = partial(worker.request_pause, job.id)
        else:
            pause_action = partial(services.durable_generation.request_pause, job.id)
        _run_action(
            pause_action,
            operation_zh_tw="要求暫停耐久工作流",
            key_prefix=f"story_durable_pause_error_{job.id}",
            data_safety=_durable_progress_safety(),
            job_id=job.id,
            step_id=next_step_id,
            retry_permitted=True,
        )
    cancel_confirmation_key = f"{_CANCEL_CONFIRM_PREFIX}_{job.id}"
    if cancel_col.button(
        "取消工作流…",
        key=f"story_durable_cancel_{job.id}",
        disabled=job.status in {JobStatus.COMPLETED, JobStatus.CANCELLED},
        type="secondary",
        help="需再確認一次；已完成 step、checkpoint 與 partial 仍會保留。",
    ):
        st.session_state[cancel_confirmation_key] = True
        st.rerun()
    resumable = job.status in {
        JobStatus.PAUSED,
        JobStatus.NEEDS_AUTHOR_INPUT,
        JobStatus.FAILED,
    }
    resume_label = "重試失敗步驟" if job.status is JobStatus.FAILED else "繼續工作流"
    if resume_col.button(
        resume_label,
        key=f"story_durable_resume_{job.id}",
        disabled=pinned_offline or not resumable,
        type="primary" if resumable and not pinned_offline else "secondary",
    ):
        action = (
            services.durable_generation.retry
            if job.status is JobStatus.FAILED
            else services.durable_generation.resume
        )
        _run_action(
            lambda: action(job.id),
            operation_zh_tw=(
                "重試失敗的耐久步驟" if job.status is JobStatus.FAILED else "繼續耐久工作流"
            ),
            key_prefix=f"story_durable_resume_error_{job.id}",
            data_safety=_durable_progress_safety(),
            job_id=job.id,
            step_id=next_step_id,
            retry_permitted=True,
        )
    if recover_col.button(
        "掃描中斷狀態",
        key=f"story_durable_recover_{job.id}",
        type="secondary",
        help=(
            "只把啟動前遺留的 RUNNING step／attempt 標為 INTERRUPTED；"
            "不重跑 completed step，也不自動呼叫 provider。"
        ),
    ):
        _run_action(
            services.durable_generation.recover_startup,
            operation_zh_tw="掃描並標記啟動前中斷步驟",
            key_prefix=f"story_durable_recover_error_{job.id}",
            data_safety=_durable_progress_safety(),
            job_id=job.id,
            step_id=next_step_id,
            retry_permitted=True,
        )
    st.caption(
        "「掃描中斷狀態」只處理啟動前遺留的 `RUNNING` 記錄並標為 "
        "`INTERRUPTED`；不會重跑 `COMPLETED` 步驟。"
    )

    if bool(st.session_state.get(cancel_confirmation_key)):
        st.warning(
            "確認取消此工作流？尚未執行的步驟不會再開始；"
            "已完成步驟、checkpoint 與 partial 內容仍保留在本機資料庫，"
            "也不會改寫正式 Scene。"
        )
        confirm_col, keep_col = st.columns(2)
        if confirm_col.button(
            "確認取消並保留已完成資料",
            key=f"story_durable_cancel_confirm_button_{job.id}",
            type="secondary",
        ):
            cancel_action: Callable[[], object]
            if worker_active and worker is not None:
                cancel_action = partial(worker.request_cancel, job.id)
            else:
                cancel_action = partial(
                    services.durable_generation.request_cancel,
                    job.id,
                )
            _run_action(
                cancel_action,
                operation_zh_tw="取消耐久工作流",
                key_prefix=f"story_durable_cancel_error_{job.id}",
                data_safety=_durable_progress_safety(),
                job_id=job.id,
                step_id=next_step_id,
                retry_permitted=True,
                clear_state_keys=(cancel_confirmation_key,),
            )
        if keep_col.button(
            "返回，不取消",
            key=f"story_durable_cancel_keep_{job.id}",
            type="secondary",
        ):
            st.session_state.pop(cancel_confirmation_key, None)
            st.rerun()


def _run_action(
    action: Any,
    *,
    operation_zh_tw: str,
    key_prefix: str,
    data_safety: ErrorDataSafety,
    job_id: str = "",
    step_id: str = "",
    retry_permitted: bool = False,
    clear_state_keys: tuple[str, ...] = (),
    rerun: bool = True,
) -> None:
    try:
        outcome = action()
        if isinstance(outcome, DurableExecutionResult):
            st.success(
                f"已提交：{_DISPOSITION_LABELS[outcome.disposition]} "
                f"(`{outcome.disposition.value}`)"
            )
        else:
            st.success("已提交至本機耐久狀態。")
        for state_key in clear_state_keys:
            st.session_state.pop(state_key, None)
        if rerun:
            st.rerun()
    except _DURABLE_UI_ERRORS as exc:
        present_exception(
            exc,
            operation_zh_tw=operation_zh_tw,
            key_prefix=key_prefix,
            data_safety=data_safety,
            provider_label="長篇生成服務",
            retry_permitted=retry_permitted,
            fallback_permitted=False,
            job_id=job_id,
            step_id=step_id,
        )


def _next_step_id(steps: tuple[GenerationStep, ...]) -> str:
    for step in steps:
        if step.status is not StepStatus.COMPLETED:
            return step.id
    return ""


def _durable_progress_safety() -> ErrorDataSafety:
    return ErrorDataSafety(
        message_zh_tw=(
            "這次操作未被標示為成功；正式 Scene 與已接受版本未被改寫。"
            "先前已提交的步驟、checkpoint 與 partial 仍保留，可重新載入後從持久狀態判斷下一步。"
        ),
        original_preserved=True,
        partial_preserved=True,
        completed_work_preserved=True,
    )


def _author_progress_safety() -> ErrorDataSafety:
    return ErrorDataSafety(
        message_zh_tw=(
            "這次操作未被標示為成功；正式正文與已接受版本未被改寫。"
            "先前完成的章節、未完成但已保存的片段，以及最後安全保存位置仍在本機。"
        ),
        original_preserved=True,
        partial_preserved=True,
        completed_work_preserved=True,
    )


def _render_candidate(
    services: Any,
    candidates: tuple[GenerationCandidate, ...],
) -> None:
    st.markdown("#### 候選預覽與套用")
    candidate_ids = [candidate.id for candidate in candidates]
    selected_id = st.selectbox(
        "長篇候選",
        options=candidate_ids,
        format_func=lambda candidate_id: candidate_id[-20:],
        key="story_durable_candidate",
    )
    candidate = next(item for item in candidates if item.id == selected_id)
    st.text_area(
        _candidate_preview_label(candidate),
        value=candidate.content_text,
        height=320,
        disabled=True,
        key=f"story_durable_candidate_text_{candidate.id}",
    )
    st.dataframe(
        [
            {"品質檢查": str(name), "結果": _readable_quality_value(value)}
            for name, value in sorted(candidate.quality_report.items())
        ],
        width="stretch",
        hide_index=True,
    )
    with st.expander("候選來源與驗證"):
        st.json(candidate.provenance.model_dump(mode="json"))
    _render_candidate_apply(services, candidate, author_facing=False)


def _render_candidate_apply(
    services: Any,
    candidate: GenerationCandidate,
    *,
    author_facing: bool,
) -> None:
    """Apply either the full candidate or one unit; full is the safe default."""

    units = _candidate_units(candidate)
    unit_ids = [str(unit["id"]) for unit in units]
    key_prefix = "story_longform_author" if author_facing else "story_durable"
    unit_only = is_unit_candidate(candidate)
    selected_unit = st.selectbox(
        "套用範圍",
        options=unit_ids if unit_only else ["", *unit_ids],
        format_func=lambda unit_id: (
            "整份長篇"
            if not unit_id
            else next(str(unit["title"]) for unit in units if unit["id"] == unit_id)
        ),
        key=f"{key_prefix}_apply_unit_{candidate.id}",
        help=(
            "此單章只會建立原對應場景的新工作草稿；保留原文與復原收據。"
            if unit_only else "整份長篇會建立一份新的工作草稿；不會直接接受或覆寫正式版本。"
        ),
    )
    content_mode = (
        candidate.content_json.get("content_mode", "general")
        if candidate.content_json is not None
        else "general"
    )
    unsupported_mode = content_mode not in {mode.value for mode in ContentMode}
    if unsupported_mode:
        st.error("此候選使用不支援的內容模式，請重新產生候選。")
    if st.button(
        "建立新的工作草稿（不自動接受）",
        key=f"{key_prefix}_apply_{candidate.id}",
        disabled=unsupported_mode or candidate.status.value != "proposed",
        type="primary",
        width="stretch",
    ):
        digest = hashlib.sha256(
            f"{candidate.id}:{candidate.target_id}:{selected_unit or 'all'}".encode()
        ).hexdigest()
        _run_action(
            lambda: services.durable_generation.apply_candidate(
                CandidateApplyCommand(
                    candidate_id=candidate.id,
                    action_kind=(
                        CandidateActionKind.APPLY_SELECTION
                        if selected_unit
                        else CandidateActionKind.APPLY_ALL
                    ),
                    idempotency_key=f"story-durable-apply:{digest}",
                    selection=({"unit_id": selected_unit} if selected_unit else {}),
                    before_snapshot=candidate.base_snapshot,
                    base_version_ref=(
                        str(candidate.base_snapshot["working_draft_id"])
                        if candidate.base_snapshot.get("working_draft_id") is not None
                        else None
                    ),
                    created_at=utc_now_iso(),
                )
            ),
            operation_zh_tw="將耐久長篇候選套用為 Story Scene 新草稿",
            key_prefix=f"story_durable_apply_error_{candidate.id}",
            data_safety=ErrorDataSafety(
                message_zh_tw=(
                    "這次套用尚未被標示為成功；候選與已接受的正式草稿仍保留。"
                    "冪等的新工作草稿可能已建立，重新載入後可安全用同一操作重試，"
                    "不會建立重複草稿。"
                ),
                original_preserved=True,
                completed_work_preserved=True,
            ),
            job_id=candidate.job_id,
            retry_permitted=True,
        )


    render_story_candidate_undo(
        getattr(services, "story_candidate_application", None),
        candidate,
        key_prefix=f"{key_prefix}_undo_{candidate.id}",
    )


def _candidate_preview_label(candidate: GenerationCandidate) -> str:
    if candidate.status.value == "applied":
        return "候選全文（已建立工作草稿，未自動接受）"
    if candidate.status.value == "archived":
        return "候選全文（已封存，保留原候選）"
    return "候選全文（尚未套用）"


def _candidate_units(candidate: GenerationCandidate) -> list[dict[str, object]]:
    if candidate.content_json is None:
        return []
    raw_units = candidate.content_json.get("units")
    if not isinstance(raw_units, list):
        return []
    return [
        dict(unit)
        for unit in raw_units
        if isinstance(unit, Mapping)
        and isinstance(unit.get("id"), str)
        and isinstance(unit.get("title"), str)
        and isinstance(unit.get("text"), str)
    ]


def _readable_quality_value(value: object) -> str:
    if isinstance(value, bool):
        return "通過" if value else "未通過"
    if value is None:
        return "—"
    if isinstance(value, list):
        return "、".join(str(item) for item in value) or "—"
    if isinstance(value, Mapping):
        return "；".join(f"{key}: {item}" for key, item in value.items()) or "—"
    return str(value)


__all__ = [
    "durable_step_rows",
    "render_durable_story_workflow",
    "run_bounded",
]
