"""Candidate-first Streamlit bridge for the unified creative engine.

The bridge deliberately owns no provider and stores no credential. Pages call
``request_candidate`` only from an explicit button event, then render the
returned value as an uncommitted candidate until the author applies it.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from threading import Event
from typing import Any, Literal, cast

import streamlit as st
from pydantic import BaseModel, ConfigDict, JsonValue, SecretStr

from imaginarium_forge.application.ports.credentials import OPENAI_CREDENTIAL_ID
from imaginarium_forge.application.services.generation.authoring_facade import (
    AuthoringCandidateStatus,
    CandidateApplyMismatchError,
    CandidateApplyValidation,
    CreativeAuthoringCommand,
    CreativeAuthoringResponse,
    LegacyCreativeCandidateAdapter,
    revalidate_candidate_for_apply,
    revalidate_story_fragment_continuation_for_append,
)
from imaginarium_forge.application.services.generation.candidate_job import (
    CandidateGenerationJobManager,
    CandidateGenerationJobNotFoundError,
    CandidateGenerationJobStatus,
    CandidateGenerationJobTerminal,
    DuplicateCandidateGenerationJobError,
)
from imaginarium_forge.application.services.generation.context_compiler import (
    ContextCompilationReport,
    StoryContextProjectionReport,
)
from imaginarium_forge.application.services.generation.contracts import (
    GenerationErrorCategory,
    GenerationExecutionStatus,
    GenerationFailure,
    GenerationOperation,
    GenerationTask,
)
from imaginarium_forge.application.services.generation.facade import (
    CreativeCandidateAction,
    CreativeCandidateCommand,
    CreativeCandidateResponse,
    OfflineCandidateFactory,
)
from imaginarium_forge.application.services.generation.output_measurement import (
    LengthBand,
    measure_output,
)
from imaginarium_forge.application.services.generation.preference_routes import (
    task_route_preference,
)
from imaginarium_forge.application.services.generation.runtime import TextDeltaCallback
from imaginarium_forge.application.services.generation.story_fragment_continuation import (
    StoryFragmentContinuationSource,
    prepare_story_fragment_continuation,
)
from imaginarium_forge.application.services.ollama_model_service import (
    ollama_model_ids_match,
)
from imaginarium_forge.application.user_facing_errors import UserFacingError
from imaginarium_forge.canonical import sha256_of_canonical
from imaginarium_forge.domain.common.ids import new_id
from imaginarium_forge.domain.generation.context_selection import ContextSelectionSpec
from imaginarium_forge.domain.generation.length import GenerationLengthSpec
from imaginarium_forge.domain.generation.preferences import (
    AIFallbackPolicy,
    AIProviderPreference,
)
from imaginarium_forge.ui.error_presenter import (
    map_generation_failure,
    render_error_card,
)
from imaginarium_forge.ui.ollama_session import (
    OLLAMA_INVENTORY_ERROR_SESSION_KEY,
    OLLAMA_INVENTORY_KNOWN_SESSION_KEY,
    OLLAMA_INVENTORY_NAMES_SESSION_KEY,
)
from imaginarium_forge.ui.openai_session import (
    OPENAI_API_KEY_SESSION_KEY,
    SessionStateStore,
    consume_openai_key_after_action,
    read_openai_session_settings,
)
from imaginarium_forge.ui.writing_request_controls import WritingRequestSnapshot

CandidateDecision = Literal["apply", "discard"]

_PROVIDER_LABELS = {
    AIProviderPreference.OFFLINE: "Offline",
    AIProviderPreference.OLLAMA: "Ollama",
    AIProviderPreference.OPENAI: "OpenAI",
}
_BACKGROUND_AUTHORING_TASKS = frozenset(
    {
        GenerationTask.STORY_FRAGMENT,
        GenerationTask.CHARACTER_DRAFT,
        GenerationTask.WORLD_SEED,
    }
)
_FAST_BACKGROUND_RESULT_WAIT_SECONDS = 0.05
_LIVE_PREVIEW_MAX_CHARS = 8_000


class CandidateBackgroundJobMetadata(BaseModel):
    """Session-safe UI metadata; it deliberately carries no prose or credential."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    job_id: str
    task: GenerationTask
    action: CreativeCandidateAction
    apply_mode: str
    editor_state_sha256: str
    length_spec: GenerationLengthSpec


@dataclass(slots=True)
class _BackgroundAuthoringInvocation:
    """Transient worker input; the credential reference is cleared on return."""

    facade: Any = field(repr=False)
    command: CreativeAuthoringCommand = field(repr=False)
    context_sources: tuple[Any, ...] = field(repr=False)
    offline_candidate: OfflineCandidateFactory | None = field(default=None, repr=False)
    session_override: SecretStr | str | None = field(default=None, repr=False)
    continuation_source: StoryFragmentContinuationSource | None = field(default=None, repr=False)

    def __call__(
        self,
        cancel: Event,
        on_delta: TextDeltaCallback,
    ) -> CreativeAuthoringResponse:
        try:
            continuation_kwargs: dict[str, Any] = (
                {"continuation_source": self.continuation_source}
                if self.continuation_source is not None else {}
            )
            return LegacyCreativeCandidateAdapter(cast(Any, self.facade)).generate_candidate(
                self.command,
                context_sources=self.context_sources,
                offline_candidate=self.offline_candidate,
                session_override=self.session_override,
                on_delta=on_delta,
                cancel=cancel,
                **continuation_kwargs,
            )
        finally:
            self.session_override = None
            self.continuation_source = None
            self.context_sources = ()


@dataclass(frozen=True, slots=True)
class PassiveCreativeActionStatus:
    """Non-secret action readiness derived without a provider or network probe."""

    action: CreativeCandidateAction
    provider: AIProviderPreference | None
    model: str
    readiness: str
    ready: bool
    reason: str = ""

    @property
    def provider_label(self) -> str:
        if self.provider is None:
            return "未載入"
        return _PROVIDER_LABELS[self.provider]


def _passive_openai_credential_available(
    services: Any,
    state: SessionStateStore,
) -> bool:
    """Project credential availability from metadata without resolving a secret."""

    # Session keys are normalized to non-blank SecretStr values at the explicit
    # entry boundary. Type/presence is sufficient metadata here; calling
    # get_secret_value would unnecessarily materialize the credential.
    session_secret = state.get(OPENAI_API_KEY_SESSION_KEY)
    if isinstance(session_secret, SecretStr) or (
        isinstance(session_secret, str) and bool(session_secret)
    ):
        return True

    credential_store = getattr(services, "credential_store", None)
    read_status = getattr(credential_store, "status", None)
    if callable(read_status):
        try:
            device = read_status(OPENAI_CREDENTIAL_ID)
        except Exception:
            device = None
        if device is not None and device.available and device.exists:
            return True
    # Presence is the only passive environment metadata required. Exact
    # normalization and secret resolution remain at the explicit action edge.
    return bool(os.environ.get("OPENAI_API_KEY", "").strip())


def passive_creative_action_status(
    services: Any,
    state: SessionStateStore,
    *,
    task: GenerationTask,
    action: CreativeCandidateAction,
) -> PassiveCreativeActionStatus:
    """Project only saved/session evidence; never instantiate or probe a provider."""

    preference_service = getattr(services, "ai_runtime_preferences", None)
    read_preferences = getattr(preference_service, "get_preferences", None)
    if not callable(read_preferences):
        return PassiveCreativeActionStatus(
            action=action,
            provider=None,
            model="未載入",
            readiness="不可用",
            ready=False,
            reason=(
                "無法讀取已保存的 AI route；請從頂部「狀態」檢查設定；"
                "手寫、保存與本機離線功能仍可使用。"
            ),
        )

    try:
        preferences = read_preferences()
        task_override = task_route_preference(
            preferences,
            task,
            action_id=action.value,
        )
        provider = (
            task_override.provider
            if task_override is not None and task_override.provider is not None
            else preferences.default_provider
        )
        configured_model = (
            task_override.model
            if task_override is not None and task_override.model is not None
            else ""
        )
    except Exception:
        return PassiveCreativeActionStatus(
            action=action,
            provider=None,
            model="未載入",
            readiness="不可用",
            ready=False,
            reason="無法讀取已保存的 AI route；請從頂部「狀態」檢查設定。",
        )

    if provider is AIProviderPreference.OFFLINE:
        if action is CreativeCandidateAction.REFINE:
            return PassiveCreativeActionStatus(
                action=action,
                provider=provider,
                model="離線靈感模板（快速短版）",
                readiness="不支援語意精修",
                ready=False,
                reason=(
                    "離線靈感模板不支援原文語意精修；仍可手寫與保存，"
                    "或自行從頂部「狀態」選擇 Ollama / OpenAI。"
                    "不會重抽其他故事，也不會自動切換 provider。"
                ),
            )
        if action is CreativeCandidateAction.CONTINUE:
            return PassiveCreativeActionStatus(
                action=action,
                provider=provider,
                model="離線靈感模板（快速短版）",
                readiness="不支援續寫",
                ready=False,
                reason="內建離線靈感不支援續寫原文；仍可手寫及保存，不會改用其他 provider。",
            )
        return PassiveCreativeActionStatus(
            action=action,
            provider=provider,
            model="離線靈感模板（快速短版）",
            readiness="可用",
            ready=True,
        )

    if provider is AIProviderPreference.OLLAMA:
        model = configured_model or preferences.ollama_model
        if not model:
            return PassiveCreativeActionStatus(
                action=action,
                provider=provider,
                model="尚未選擇",
                readiness="需要已安裝模型",
                ready=False,
                reason="尚未選擇 Ollama 模型；請從頂部「狀態」選擇已安裝模型。",
            )
        if OLLAMA_INVENTORY_ERROR_SESSION_KEY in state:
            return PassiveCreativeActionStatus(
                action=action,
                provider=provider,
                model=model,
                readiness="上次檢查失敗",
                ready=False,
                reason="Ollama 最近一次檢查失敗；請從頂部「狀態」重新刷新已安裝模型。",
            )
        inventory_known = bool(state.get(OLLAMA_INVENTORY_KNOWN_SESSION_KEY, False))
        raw_inventory = state.get(OLLAMA_INVENTORY_NAMES_SESSION_KEY, ())
        inventory = (
            tuple(str(item) for item in raw_inventory)
            if isinstance(raw_inventory, (list, tuple))
            else ()
        )
        if not inventory_known:
            return PassiveCreativeActionStatus(
                action=action,
                provider=provider,
                model=model,
                readiness="尚未檢查",
                ready=False,
                reason="Ollama 模型狀態尚未確認；請從頂部「狀態」刷新已安裝模型。",
            )
        if not any(ollama_model_ids_match(model, item) for item in inventory):
            return PassiveCreativeActionStatus(
                action=action,
                provider=provider,
                model=model,
                readiness="需要已安裝模型",
                ready=False,
                reason="選定模型不在最近一次模型清單；請從頂部「狀態」刷新或重新選模。",
            )
        return PassiveCreativeActionStatus(
            action=action,
            provider=provider,
            model=model,
            readiness="可用（最近一次手動檢查，可能已過期）",
            ready=True,
        )

    model = configured_model or preferences.openai_model
    if not preferences.remote_provider_enabled:
        return PassiveCreativeActionStatus(
            action=action,
            provider=provider,
            model=model,
            readiness="尚未啟用遠端傳送",
            ready=False,
            reason="OpenAI 遠端傳送尚未啟用；請從頂部「狀態」管理 AI 設定。",
        )
    try:
        credential_available = _passive_openai_credential_available(
            services,
            state,
        )
    except Exception:
        return PassiveCreativeActionStatus(
            action=action,
            provider=provider,
            model=model,
            readiness="無法確認 credential",
            ready=False,
            reason="無法確認 OpenAI credential 狀態；請從頂部「狀態」檢查設定。",
        )
    if not credential_available:
        return PassiveCreativeActionStatus(
            action=action,
            provider=provider,
            model=model,
            readiness="需要 credential",
            ready=False,
            reason="OpenAI 尚無可用 credential；請從頂部「狀態」管理 AI 設定。",
        )
    return PassiveCreativeActionStatus(
        action=action,
        provider=provider,
        model=model,
        readiness="可用（未主動連線檢查）",
        ready=True,
    )


def render_passive_creative_runtime_status(
    services: Any,
    *,
    task: GenerationTask,
) -> tuple[PassiveCreativeActionStatus, PassiveCreativeActionStatus]:
    """Render one passive status badge and at most one blocker line."""

    generate = passive_creative_action_status(
        services,
        st.session_state,
        task=task,
        action=CreativeCandidateAction.GENERATE,
    )
    refine = passive_creative_action_status(
        services,
        st.session_state,
        task=task,
        action=CreativeCandidateAction.REFINE,
    )

    def summary(status: PassiveCreativeActionStatus) -> str:
        return f"{status.provider_label} · {status.model} · {status.readiness}"

    same_route = (
        generate.provider,
        generate.model,
        generate.readiness,
        generate.ready,
    ) == (
        refine.provider,
        refine.model,
        refine.readiness,
        refine.ready,
    )
    if same_route:
        st.caption(f"AI 狀態｜生成／精修：{summary(generate)}")
    else:
        st.caption(f"AI 狀態｜生成：{summary(generate)}｜精修：{summary(refine)}")

    blockers: list[str] = []
    if not generate.ready:
        blockers.append(f"生成：{generate.reason}")
    if not refine.ready and refine.reason != generate.reason:
        blockers.append(f"精修：{refine.reason}")
    if blockers:
        st.caption(f"AI 動作暫不可用｜{'；'.join(blockers)}")
    if task in {
        GenerationTask.STORY_FRAGMENT,
        GenerationTask.CHARACTER_DRAFT,
        GenerationTask.WORLD_SEED,
    } and any(
        status.provider is AIProviderPreference.OFFLINE for status in (generate, refine)
    ):
        st.caption(
            "目前離線模式只提供短版靈感模板，不保證符合段落或長文篇幅；"
            "候選仍依原長度合約量測，未達標不會顯示為完成。"
            "可先整理大綱，或自行從頂部「狀態」切換 Ollama / OpenAI；"
            "不會自動切換，也不會改動原稿。"
        )
    return generate, refine


def _key(prefix: str, suffix: str) -> str:
    return f"{prefix}_ai_{suffix}"


def candidate_state_key(prefix: str) -> str:
    return _key(prefix, "candidate")


def candidate_error_state_key(prefix: str) -> str:
    return _key(prefix, "error")


def candidate_busy_state_key(prefix: str) -> str:
    return _key(prefix, "busy")


def candidate_apply_validation_state_key(prefix: str) -> str:
    return _key(prefix, "apply_validation")


def candidate_job_manager_state_key(prefix: str) -> str:
    return _key(prefix, "job_manager")


def candidate_job_metadata_state_key(prefix: str) -> str:
    return _key(prefix, "job_metadata")


def candidate_job_notice_state_key(prefix: str) -> str:
    return _key(prefix, "job_notice")


def _candidate_job_manager(prefix: str) -> CandidateGenerationJobManager:
    key = candidate_job_manager_state_key(prefix)
    existing = st.session_state.get(key)
    if isinstance(existing, CandidateGenerationJobManager):
        existing.cleanup_consumed()
        return existing
    manager = CandidateGenerationJobManager()
    st.session_state[key] = manager
    return manager


def _candidate_job_metadata(prefix: str) -> CandidateBackgroundJobMetadata | None:
    raw = st.session_state.get(candidate_job_metadata_state_key(prefix))
    if isinstance(raw, CandidateBackgroundJobMetadata):
        return raw
    if isinstance(raw, Mapping):
        try:
            return CandidateBackgroundJobMetadata.model_validate(raw)
        except ValueError:
            pass
    return None


def _sync_candidate_background_job(prefix: str) -> bool:
    """Consume a terminal worker only on the Streamlit script thread."""

    metadata = _candidate_job_metadata(prefix)
    manager = st.session_state.get(candidate_job_manager_state_key(prefix))
    if metadata is None or not isinstance(manager, CandidateGenerationJobManager):
        return bool(st.session_state.get(candidate_busy_state_key(prefix), False))
    try:
        snapshot = manager.get(metadata.job_id)
    except CandidateGenerationJobNotFoundError:
        st.session_state[candidate_busy_state_key(prefix)] = False
        st.session_state.pop(candidate_job_metadata_state_key(prefix), None)
        st.session_state.pop(_key(prefix, "request_id"), None)
        _store_failure(
            prefix,
            GenerationFailure(
                category=GenerationErrorCategory.PROVIDER_ERROR,
                safe_message="AI 候選背景工作已遺失；原稿完全沒有變更。",
                retriable=True,
            ),
        )
        return False
    if snapshot.is_active:
        st.session_state[candidate_busy_state_key(prefix)] = True
        return True

    terminal = manager.consume_terminal(metadata.job_id)
    st.session_state[candidate_busy_state_key(prefix)] = False
    st.session_state.pop(candidate_job_metadata_state_key(prefix), None)
    st.session_state.pop(_key(prefix, "request_id"), None)
    if terminal is None:
        return False
    _retain_candidate_response(
        prefix,
        task=metadata.task,
        action=metadata.action,
        apply_mode=metadata.apply_mode,
        editor_state_sha256=metadata.editor_state_sha256,
        response=terminal.response,
        background_terminal=terminal,
    )
    manager.cleanup_consumed()
    return False


def request_candidate_cancel(prefix: str) -> bool:
    """Set the active worker's real cancellation Event exactly once."""

    metadata = _candidate_job_metadata(prefix)
    manager = st.session_state.get(candidate_job_manager_state_key(prefix))
    if metadata is None or not isinstance(manager, CandidateGenerationJobManager):
        return False
    try:
        snapshot = manager.cancel(metadata.job_id)
    except CandidateGenerationJobNotFoundError:
        return False
    return snapshot.status is CandidateGenerationJobStatus.CANCEL_REQUESTED


@st.fragment(run_every=0.4)
def _render_candidate_job_fragment(prefix: str) -> None:
    """Timed polling and cancellation without blocking the full page script."""

    was_busy = bool(st.session_state.get(candidate_busy_state_key(prefix), False))
    busy = _sync_candidate_background_job(prefix)
    if was_busy and not busy:
        st.rerun()
    metadata = _candidate_job_metadata(prefix)
    manager = st.session_state.get(candidate_job_manager_state_key(prefix))
    if metadata is None or not isinstance(manager, CandidateGenerationJobManager):
        return
    try:
        snapshot = manager.get(metadata.job_id)
    except CandidateGenerationJobNotFoundError:
        return

    if snapshot.status is CandidateGenerationJobStatus.CANCEL_REQUESTED:
        st.info("已送出取消要求；正在等待 provider 確認停止，尚未宣稱取消完成。")
    else:
        st.info("AI 候選正在背景建立；可以繼續查看本頁，完成前不會改動原稿。")

    if snapshot.partial_text:
        measurement = measure_output(
            snapshot.partial_text,
            unit=metadata.length_spec.unit,
        )
        ratio = min(1.0, float(measurement.actual) / float(metadata.length_spec.target))
        st.progress(
            ratio,
            text=(
                f"暫存進度 {measurement.actual:g} / {metadata.length_spec.target:g} "
                f"{metadata.length_spec.unit.value}"
            ),
        )
        preview = snapshot.partial_text[-_LIVE_PREVIEW_MAX_CHARS:]
        st.caption(
            "即時 partial 預覽（尚未成為候選、不可套用）"
            + (
                f"；只顯示最後 {_LIVE_PREVIEW_MAX_CHARS:,} 字元，完整內容仍在本次工作記憶體中。"
                if len(snapshot.partial_text) > _LIVE_PREVIEW_MAX_CHARS
                else ""
            )
        )
        st.code(preview, language=None, wrap_lines=True)
    else:
        st.progress(0, text="正在整理上下文與結構化計畫")

    if st.button(
        "停止這次生成",
        key=_key(prefix, "cancel"),
        disabled=snapshot.status is CandidateGenerationJobStatus.CANCEL_REQUESTED,
        width="stretch",
    ):
        request_candidate_cancel(prefix)
        st.rerun()


def render_candidate_action_state(prefix: str) -> bool:
    """Render one consistent running state and return whether actions are busy."""

    _sync_candidate_background_job(prefix)
    notice = st.session_state.get(candidate_job_notice_state_key(prefix))
    if notice == "cancelled_partial":
        st.warning(
            "生成已由 provider 確認取消；收到的 partial 已保留為未套用候選，"
            "必須由作者明確檢視與決定。"
        )
    elif notice == "completed_after_cancel_request":
        st.info("取消要求送達前生成已完成；結果仍只是未套用候選。")
    busy = bool(st.session_state.get(candidate_busy_state_key(prefix), False))
    if busy:
        metadata = _candidate_job_metadata(prefix)
        if metadata is not None:
            _render_candidate_job_fragment(prefix)
        else:
            st.info("AI 候選正在建立中；為避免重複送出，生成與精修操作暫時停用。")
    return busy


def clear_candidate_state(prefix: str) -> None:
    """Drop only replaceable UI state; authored editor values are untouched."""

    st.session_state.pop(candidate_state_key(prefix), None)
    st.session_state.pop(candidate_error_state_key(prefix), None)
    st.session_state.pop(candidate_apply_validation_state_key(prefix), None)
    st.session_state.pop(candidate_job_notice_state_key(prefix), None)
    st.session_state.pop(_key(prefix, "request_id"), None)
    st.session_state.pop(_key(prefix, "append_receipt"), None)


def candidate_apply_validation(prefix: str) -> CandidateApplyValidation | None:
    """Return the last exact apply-time contract check for this candidate."""

    value = st.session_state.get(candidate_apply_validation_state_key(prefix))
    if not isinstance(value, Mapping):
        return None
    try:
        return CandidateApplyValidation.model_validate(value)
    except ValueError:
        st.session_state.pop(candidate_apply_validation_state_key(prefix), None)
        return None


def candidate_authoring_response(prefix: str) -> CreativeAuthoringResponse | None:
    """Read the immutable typed envelope retained for explicit apply."""

    envelope = st.session_state.get(candidate_state_key(prefix))
    if not isinstance(envelope, Mapping):
        return None
    value = envelope.get("authoring_response")
    if value is None:
        return None
    try:
        return CreativeAuthoringResponse.model_validate(value)
    except ValueError:
        return None


def authoring_editor_state_sha256(
    *,
    task: GenerationTask,
    inputs: Mapping[str, object],
    instruction: str,
    writing_request: WritingRequestSnapshot,
    editor_identity: str = "",
    continuation_source: StoryFragmentContinuationSource | None = None,
) -> str:
    """Fingerprint the live editor state without retaining another prose copy."""

    snapshot: dict[str, object] = {
            "task": task.value,
            "editor_identity": editor_identity,
            "inputs": dict(inputs),
            "instruction": instruction,
            "intent": writing_request.intent.value,
            "length_spec": writing_request.length_spec.model_dump(mode="json"),
            "ollama_context_profile": writing_request.ollama_context_profile.model_dump(
                mode="json"
            ),
            "context_spec": writing_request.context_spec.model_dump(mode="json"),
            "resolved_sources": [
                source.model_dump(mode="json") for source in writing_request.resolved_sources
            ],
    }
    if continuation_source is not None:
        snapshot["continuation_binding"] = continuation_source.binding.model_dump(mode="json")
    return sha256_of_canonical(snapshot)


def candidate_value_replaces_current(
    candidate_value: object,
    current_value: object,
    *,
    apply_mode: str,
) -> bool:
    """Return whether a generated value may replace one author-controlled field.

    Empty model values never erase authored content. Clearing a field remains
    available through the editor, where it is an explicit author action rather
    than an implicit consequence of a partial provider response.
    """

    # Append requires the story-specific raw-source compare-and-swap boundary.
    if apply_mode == "append":
        return False
    if isinstance(candidate_value, (list, tuple)):
        candidate_has_content = any(str(item).strip() for item in candidate_value)
    else:
        candidate_has_content = bool(str(candidate_value or "").strip())
    if not candidate_has_content:
        return False
    return not (apply_mode == "fill" and str(current_value or "").strip())


def _action_execution_requirements(
    services: Any,
    *,
    task: GenerationTask,
    action: CreativeCandidateAction,
) -> tuple[bool, bool]:
    """Return whether to consume a session key and snapshot an offline fallback."""

    preference_service = getattr(services, "ai_runtime_preferences", None)
    read_preferences = getattr(preference_service, "get_preferences", None)
    if not callable(read_preferences):
        return False, False
    try:
        preferences = read_preferences()
        task_override = task_route_preference(
            preferences,
            task,
            action_id=action.value,
        )
        provider = (
            task_override.provider
            if task_override is not None and task_override.provider is not None
            else preferences.default_provider
        )
        fallback_policy = (
            task_override.fallback_policy
            if task_override is not None and task_override.fallback_policy is not None
            else preferences.fallback_policy
        )
    except Exception:
        return False, False
    needs_offline_snapshot = action is CreativeCandidateAction.GENERATE and (
        provider is AIProviderPreference.OFFLINE or (
            provider is not AIProviderPreference.OFFLINE
            and fallback_policy is AIFallbackPolicy.OFFLINE_THEN_FAIL
        )
    )
    return provider is AIProviderPreference.OPENAI, needs_offline_snapshot


def _snapshot_offline_candidate(
    factory: OfflineCandidateFactory | None,
) -> OfflineCandidateFactory | None:
    """Evaluate Streamlit-dependent local input on the script thread exactly once."""

    if factory is None:
        return None
    value = factory()
    if isinstance(value, BaseModel):
        snapshot = value.model_copy(deep=True)

        def build_model() -> BaseModel:
            return snapshot.model_copy(deep=True)

        return build_model
    if isinstance(value, Mapping):
        snapshot_mapping = deepcopy(dict(value))

        def build_mapping() -> Mapping[str, object]:
            return deepcopy(snapshot_mapping)

        return build_mapping
    raise TypeError("offline candidate factory returned an unsupported value")


def _retain_candidate_response(
    prefix: str,
    *,
    task: GenerationTask,
    action: CreativeCandidateAction,
    apply_mode: str,
    editor_state_sha256: str,
    response: CreativeCandidateResponse | CreativeAuthoringResponse | None,
    background_terminal: CandidateGenerationJobTerminal | None = None,
) -> bool:
    """Move one terminal typed response into candidate state on the UI thread."""

    if response is None:
        _store_failure(
            prefix,
            GenerationFailure(
                category=GenerationErrorCategory.PROVIDER_ERROR,
                safe_message=(
                    background_terminal.safe_error
                    if background_terminal is not None and background_terminal.safe_error
                    else "AI 服務未完成這次候選稿；原稿完全沒有變更。"
                ),
                retriable=True,
            ),
        )
        return False

    authoring_response = response if isinstance(response, CreativeAuthoringResponse) else None
    failed = (
        response.status is AuthoringCandidateStatus.FAILED
        if authoring_response is not None
        else response.status is not GenerationExecutionStatus.SUCCEEDED
    )
    if failed:
        failure = response.failure or GenerationFailure(
            category=GenerationErrorCategory.PROVIDER_ERROR,
            safe_message="AI 服務未完成這次候選稿；原稿完全沒有變更。",
        )
        _store_failure(prefix, failure)
        return False

    candidate = response.candidate
    if not isinstance(candidate, dict):
        _store_failure(
            prefix,
            GenerationFailure(
                category=GenerationErrorCategory.INVALID_STRUCTURED_OUTPUT,
                safe_message="AI 回傳的候選稿格式無法使用；原稿完全沒有變更。",
            ),
        )
        return False
    provider_provenance = (
        authoring_response.provenance.provider
        if authoring_response is not None
        else response.provenance
    )
    envelope: dict[str, object] = {
        "task": task.value,
        "action": action.value,
        "apply_mode": apply_mode,
        "candidate": dict(candidate),
        "provenance": provider_provenance.model_dump(mode="json"),
    }
    if authoring_response is not None:
        envelope["authoring_response"] = authoring_response.model_dump(mode="json")
        envelope["authoring_command_sha256"] = authoring_response.provenance.command_sha256
        envelope["authoring_length_spec"] = authoring_response.provenance.length_spec.model_dump(
            mode="json"
        )
        envelope["authoring_ollama_context_profile"] = (
            authoring_response.provenance.ollama_context_profile.model_dump(mode="json")
        )
        envelope["authoring_ollama_context_profile_sha256"] = (
            authoring_response.provenance.ollama_context_profile_sha256
        )
        envelope["authoring_context_spec"] = authoring_response.provenance.context_spec.model_dump(
            mode="json"
        )
        envelope["authoring_context_report_sha256"] = (
            authoring_response.provenance.context_report_sha256
        )
        envelope["authoring_editor_state_sha256"] = editor_state_sha256
        if authoring_response.condensed_candidate is not None:
            envelope["condensed_candidate"] = dict(authoring_response.condensed_candidate)
    if background_terminal is not None:
        envelope["background_job_status"] = background_terminal.status.value
        if background_terminal.status is CandidateGenerationJobStatus.CANCELLED:
            st.session_state[candidate_job_notice_state_key(prefix)] = "cancelled_partial"
        elif (
            background_terminal.cancel_was_requested
            and background_terminal.status is CandidateGenerationJobStatus.COMPLETED
        ):
            st.session_state[candidate_job_notice_state_key(prefix)] = (
                "completed_after_cancel_request"
            )
    st.session_state[candidate_state_key(prefix)] = envelope
    st.session_state.pop(_key(prefix, "partial_review"), None)
    st.session_state.pop(_key(prefix, "append_receipt"), None)
    st.session_state.pop(candidate_apply_validation_state_key(prefix), None)
    st.session_state.pop(candidate_error_state_key(prefix), None)
    return True


def request_candidate(
    services: Any,
    *,
    prefix: str,
    task: GenerationTask,
    action: CreativeCandidateAction,
    inputs: Mapping[str, Any],
    instruction: str = "",
    apply_mode: str = "rewrite",
    offline_candidate: OfflineCandidateFactory | None = None,
    writing_request: WritingRequestSnapshot | None = None,
    editor_identity: str = "",
    continuation_source: StoryFragmentContinuationSource | None = None,
) -> bool:
    """Execute one explicit action; prose authoring runs in a cancellable worker."""

    busy_key = candidate_busy_state_key(prefix)
    if bool(st.session_state.get(busy_key, False)):
        _store_failure(
            prefix,
            GenerationFailure(
                category=GenerationErrorCategory.DUPLICATE_IN_PROGRESS,
                safe_message="上一個 AI 請求仍在處理中，未再送出重複請求。",
                retriable=True,
            ),
        )
        return False

    facade = getattr(services, "creative_generation", None)
    generate = getattr(facade, "generate_candidate", None)
    if not callable(generate):
        _store_failure(
            prefix,
            GenerationFailure(
                category=GenerationErrorCategory.CONFIGURATION,
                safe_message="統一 AI 生成服務尚未就緒；原稿完全沒有變更。",
            ),
        )
        return False

    if action is CreativeCandidateAction.CONTINUE:
        try:
            if (
                task is not GenerationTask.STORY_FRAGMENT or apply_mode != "append"
                or continuation_source is None or writing_request is None
            ):
                raise ValueError("continuation requires an exact story source and append mode")
            prepare_story_fragment_continuation(
                continuation_source,
                length_spec=writing_request.length_spec,
                context_spec=writing_request.context_spec,
                context_sources=writing_request.resolved_sources,
            )
        except ValueError:
            _store_failure(prefix, GenerationFailure(
                category=GenerationErrorCategory.CONFIGURATION,
                safe_message="續寫原文、上下文或篇幅合約無法驗證；未開始生成，原稿保留。",
            ))
            return False

    request_key = _key(prefix, "request_id")
    idempotency_key = str(
        st.session_state.setdefault(request_key, f"{prefix}:{action.value}:{new_id()}")
    )
    session_override = read_openai_session_settings(st.session_state).api_key
    consume_session_override, needs_offline_snapshot = _action_execution_requirements(
        services,
        task=task,
        action=action,
    )
    editor_state_sha256 = ""

    if writing_request is not None and task in _BACKGROUND_AUTHORING_TASKS:
        try:
            editor_state_sha256 = authoring_editor_state_sha256(
                task=task,
                inputs=inputs,
                instruction=instruction,
                writing_request=writing_request,
                editor_identity=editor_identity,
                continuation_source=continuation_source,
            )
            authoring_command = CreativeAuthoringCommand(
                task=task,
                intent=writing_request.intent,
                operation=GenerationOperation.STRUCTURED,
                action=action,
                inputs=dict(inputs),
                instruction=instruction,
                length_spec=writing_request.length_spec,
                ollama_context_profile=writing_request.ollama_context_profile,
                context_spec=writing_request.context_spec,
                idempotency_key=idempotency_key,
                continuation=(
                    continuation_source.binding if continuation_source is not None else None
                ),
            )
            worker_offline_candidate = (
                _snapshot_offline_candidate(offline_candidate)
                if needs_offline_snapshot
                else None
            )
            invocation = _BackgroundAuthoringInvocation(
                facade=facade,
                command=authoring_command,
                context_sources=tuple(writing_request.resolved_sources),
                offline_candidate=worker_offline_candidate,
                session_override=session_override,
                continuation_source=continuation_source,
            )
            manager = _candidate_job_manager(prefix)
            started = manager.start(invocation)
        except DuplicateCandidateGenerationJobError:
            _store_failure(
                prefix,
                GenerationFailure(
                    category=GenerationErrorCategory.DUPLICATE_IN_PROGRESS,
                    safe_message="上一個 AI 背景工作尚未結束，沒有送出重複請求。",
                    retriable=True,
                ),
            )
            return False
        except Exception:
            _store_failure(
                prefix,
                GenerationFailure(
                    category=GenerationErrorCategory.PROVIDER_ERROR,
                    safe_message="AI 背景工作無法啟動；原稿完全沒有變更。",
                    retriable=True,
                ),
            )
            st.session_state.pop(request_key, None)
            return False

        st.session_state[candidate_job_metadata_state_key(prefix)] = (
            CandidateBackgroundJobMetadata(
                job_id=started.job_id,
                task=task,
                action=action,
                apply_mode=apply_mode,
                editor_state_sha256=editor_state_sha256,
                length_spec=writing_request.length_spec,
            )
        )
        st.session_state[busy_key] = True
        st.session_state.pop(candidate_job_notice_state_key(prefix), None)
        st.session_state.pop(candidate_error_state_key(prefix), None)
        if consume_session_override:
            consume_openai_key_after_action(st.session_state)
        session_override = None
        if manager.wait_for_terminal(
            started.job_id,
            timeout=_FAST_BACKGROUND_RESULT_WAIT_SECONDS,
        ):
            _sync_candidate_background_job(prefix)
        return True

    st.session_state[busy_key] = True
    response: CreativeCandidateResponse | CreativeAuthoringResponse
    try:
        with st.spinner("AI 候選正在建立中；完成前不會改動原稿。"):
            if writing_request is None:
                response = generate(
                    CreativeCandidateCommand(
                        task=task,
                        action=action,
                        inputs=dict(inputs),
                        instruction=instruction,
                        idempotency_key=idempotency_key,
                    ),
                    offline_candidate=offline_candidate,
                    session_override=session_override,
                )
            else:
                editor_state_sha256 = authoring_editor_state_sha256(
                    task=task,
                    inputs=inputs,
                    instruction=instruction,
                    writing_request=writing_request,
                    editor_identity=editor_identity,
                )
                response = LegacyCreativeCandidateAdapter(cast(Any, facade)).generate_candidate(
                    CreativeAuthoringCommand(
                        task=task,
                        intent=writing_request.intent,
                        operation=GenerationOperation.STRUCTURED,
                        action=action,
                        inputs=dict(inputs),
                        instruction=instruction,
                        length_spec=writing_request.length_spec,
                        ollama_context_profile=writing_request.ollama_context_profile,
                        context_spec=writing_request.context_spec,
                        idempotency_key=idempotency_key,
                    ),
                    context_sources=writing_request.resolved_sources,
                    offline_candidate=offline_candidate,
                    session_override=session_override,
                )
    except Exception:
        _store_failure(
            prefix,
            GenerationFailure(
                category=GenerationErrorCategory.PROVIDER_ERROR,
                safe_message="AI 服務未完成這次候選稿；原稿完全沒有變更。",
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

    return _retain_candidate_response(
        prefix,
        task=task,
        action=action,
        apply_mode=apply_mode,
        editor_state_sha256=editor_state_sha256,
        response=response,
    )


def _store_failure(prefix: str, failure: GenerationFailure) -> None:
    error = map_generation_failure(failure, operation_zh_tw="建立 AI 候選稿")
    st.session_state[candidate_error_state_key(prefix)] = error.model_dump(mode="json")


def render_candidate_error(prefix: str) -> None:
    """Render one typed, sanitized failure card without consuming it."""

    value = st.session_state.get(candidate_error_state_key(prefix))
    if not isinstance(value, Mapping):
        return
    try:
        error = UserFacingError.model_validate(value)
    except ValueError:
        legacy = GenerationFailure.model_validate(value)
        error = map_generation_failure(legacy, operation_zh_tw="建立 AI 候選稿")
        st.session_state[candidate_error_state_key(prefix)] = error.model_dump(mode="json")
    render_error_card(error, key_prefix=f"{prefix}_candidate_error")


def render_candidate_card(
    prefix: str,
    *,
    field_labels: Mapping[str, str],
    current_authoring_state_sha256: str | None = None,
    current_continuation_source: StoryFragmentContinuationSource | None = None,
) -> tuple[CandidateDecision, dict[str, JsonValue], str] | None:
    """Preview a candidate and return an explicit author decision, if any."""

    envelope = st.session_state.get(candidate_state_key(prefix))
    if not isinstance(envelope, Mapping):
        return None
    raw_candidate = envelope.get("candidate")
    if not isinstance(raw_candidate, Mapping):
        clear_candidate_state(prefix)
        return None
    candidate = {str(key): value for key, value in raw_candidate.items()}
    raw_condensed_candidate = envelope.get("condensed_candidate")
    condensed_candidate = (
        {str(key): value for key, value in raw_condensed_candidate.items()}
        if isinstance(raw_condensed_candidate, Mapping)
        else None
    )
    authoring_response: CreativeAuthoringResponse | None = None
    expected_length_spec: GenerationLengthSpec | None = None
    expected_context_spec: ContextSelectionSpec | None = None
    expected_context_report_sha256 = ""
    authoring_state_is_current = True
    raw_authoring_response = envelope.get("authoring_response")
    if raw_authoring_response is not None:
        authoring_response = candidate_authoring_response(prefix)
        if authoring_response is None:
            _store_failure(
                prefix,
                GenerationFailure(
                    category=GenerationErrorCategory.INVALID_STRUCTURED_OUTPUT,
                    safe_message=("候選稿的長度或上下文證據已失效；未套用，原稿完全沒有變更。"),
                ),
            )
            st.error("候選稿的合約證據無法驗證；已阻止套用，原稿沒有變更。")
            return None
        if authoring_response.candidate != candidate:
            _store_failure(
                prefix,
                GenerationFailure(
                    category=GenerationErrorCategory.INVALID_STRUCTURED_OUTPUT,
                    safe_message=("候選稿內容與合約證據不一致；未套用，原稿完全沒有變更。"),
                ),
            )
            st.error("候選稿內容與合約證據不一致；已阻止套用，原稿沒有變更。")
            return None
        if authoring_response.condensed_candidate != condensed_candidate:
            _store_failure(
                prefix,
                GenerationFailure(
                    category=GenerationErrorCategory.INVALID_STRUCTURED_OUTPUT,
                    safe_message=("收斂候選與合約證據不一致；未套用，原稿完全沒有變更。"),
                ),
            )
            st.error("收斂候選與合約證據不一致；已阻止套用，原稿沒有變更。")
            return None
        try:
            expected_length_spec = GenerationLengthSpec.model_validate(
                envelope.get("authoring_length_spec")
            )
            expected_context_spec = ContextSelectionSpec.model_validate(
                envelope.get("authoring_context_spec")
            )
            expected_context_report_sha256 = str(
                envelope.get("authoring_context_report_sha256", "")
            )
            if (
                expected_length_spec != authoring_response.provenance.length_spec
                or expected_context_spec != authoring_response.provenance.context_spec
                or expected_context_report_sha256
                != authoring_response.provenance.context_report_sha256
                or envelope.get("authoring_ollama_context_profile")
                != authoring_response.provenance.ollama_context_profile.model_dump(mode="json")
                or envelope.get("authoring_ollama_context_profile_sha256")
                != authoring_response.provenance.ollama_context_profile_sha256
            ):
                raise ValueError("authoring evidence copy mismatch")
        except (TypeError, ValueError):
            st.error("候選稿的獨立合約對帳已失效；已阻止套用，原稿沒有變更。")
            return None
        authoring_state_is_current = (
            bool(current_authoring_state_sha256)
            and envelope.get("authoring_editor_state_sha256") == current_authoring_state_sha256
        )
    is_continuation = envelope.get("action") == CreativeCandidateAction.CONTINUE.value or (
        authoring_response is not None
        and authoring_response.provenance.action is CreativeCandidateAction.CONTINUE
    )
    if is_continuation and (
        authoring_response is None
        or authoring_response.provenance.action is not CreativeCandidateAction.CONTINUE
        or envelope.get("action") != CreativeCandidateAction.CONTINUE.value
        or envelope.get("apply_mode") != "append"
    ):
        st.error("續寫候選與追加合約不一致；原稿沒有變更。")
        return None
    provider = "offline"
    model = "offline-template"
    provenance: object = None
    if authoring_response is not None:
        typed_provider = authoring_response.provenance.provider
        provider = (
            typed_provider.actual_provider_id or typed_provider.requested_provider_id or provider
        )
        model = typed_provider.actual_model or typed_provider.requested_model or model
    else:
        provenance = envelope.get("provenance")
    if authoring_response is None and isinstance(provenance, Mapping):
        provider = str(
            provenance.get("actual_provider_id")
            or provenance.get("requested_provider_id")
            or provider
        )
        model = str(provenance.get("actual_model") or provenance.get("requested_model") or model)

    with st.container(border=True):
        st.markdown("#### AI 續寫候選（僅新增正文，尚未追加）" if is_continuation
                    else "#### AI 候選稿（尚未套用）")
        st.caption(
            (f"來源：{provider} · 模型：{model}。下方只是預覽；明確追加前，"
             if is_continuation else
             f"來源：{provider} · 模型：{model}。下方只是預覽；按『套用候選稿』前，")
            +
            "原稿不會被改動，也不會自動保存。"
        )
        candidate_variant: Literal["accumulated", "condensed"] = "accumulated"
        selected_candidate = candidate
        if condensed_candidate is not None:
            candidate_variant = cast(
                Literal["accumulated", "condensed"],
                st.radio(
                    "候選版本",
                    options=("accumulated", "condensed"),
                    format_func=lambda value: (
                        "原始完整候選（保留）" if value == "accumulated" else "另存收斂候選"
                    ),
                    horizontal=True,
                    key=_key(prefix, "candidate_variant"),
                ),
            )
            selected_candidate = (
                condensed_candidate if candidate_variant == "condensed" else candidate
            )
            st.caption("收斂版本是獨立候選；原始完整候選仍保留，切換不會覆寫原稿。")
        if authoring_response is not None:
            _render_authoring_contract_evidence(
                authoring_response,
                candidate_variant=candidate_variant,
            )
            if not authoring_state_is_current:
                st.warning(
                    "候選建立後，目前草稿、控制值或來源已改變。候選仍保留供比較，"
                    "但不可套用；請重新建立候選或捨棄舊候選。"
                )
        for field, label in field_labels.items():
            if is_continuation and field != "fragment_text":
                continue
            if field not in selected_candidate:
                continue
            rendered = _render_value(selected_candidate[field])
            st.caption(label)
            st.code(rendered, language=None, wrap_lines=True)
        partial_reviewed = False
        accept_partial = False
        append_allowed = True
        append_label = "追加續寫到編輯器"
        if is_continuation and authoring_response is not None:
            output = (authoring_response.provenance.condensed_output
                      if candidate_variant == "condensed" else authoring_response.provenance.output)
            partial = output is None or output.assessment.band is not LengthBand.WITHIN or (
                candidate_variant == "accumulated"
                and authoring_response.status is not AuthoringCandidateStatus.CANDIDATE_READY
            )
            if partial:
                append_label = "接受未達標部分並追加"
                review_subject = sha256_of_canonical({
                    "variant": candidate_variant, "candidate": selected_candidate,
                })
                if st.session_state.get(_key(prefix, "partial_review_subject")) != review_subject:
                    st.session_state.pop(_key(prefix, "partial_review"), None)
                    st.session_state[_key(prefix, "partial_review_subject")] = review_subject
                st.warning("這是未達標或中止的新增正文，不代表本次續寫已完成。")
                partial_reviewed = st.checkbox(
                    "我已檢查未達標續寫，仍要追加", key=_key(prefix, "partial_review"),
                )
                accept_partial = partial_reviewed
            else:
                partial_reviewed = True
            try:
                if (current_continuation_source is None or expected_length_spec is None
                    or expected_context_spec is None):
                    raise ValueError("current continuation source is unavailable")
                revalidate_story_fragment_continuation_for_append(
                    authoring_response,
                    source=current_continuation_source,
                    expected_command_sha256=str(envelope.get("authoring_command_sha256", "")),
                    expected_length_spec=expected_length_spec,
                    expected_context_spec=expected_context_spec,
                    expected_context_report_sha256=expected_context_report_sha256,
                    candidate_variant=candidate_variant,
                    accept_partial=True,
                )
            except ValueError:
                append_allowed = False
                st.warning("原文綁定或續寫檢查未通過；候選保留供檢視，不可追加。")
        actions = st.columns(2)
        if actions[0].button(
            append_label if is_continuation else "套用候選稿到編輯器",
            type="primary",
            width="stretch",
            key=_key(prefix, "apply"),
            disabled=not authoring_state_is_current or (
                is_continuation and (not append_allowed or not partial_reviewed)
            ),
        ):
            if authoring_response is not None:
                assert expected_length_spec is not None
                assert expected_context_spec is not None
                try:
                    validation = revalidate_candidate_for_apply(
                        authoring_response,
                        expected_command_sha256=str(envelope.get("authoring_command_sha256", "")),
                        expected_length_spec=expected_length_spec,
                        expected_context_spec=expected_context_spec,
                        expected_context_report_sha256=expected_context_report_sha256,
                        candidate_variant=candidate_variant,
                    )
                    if is_continuation:
                        assert current_continuation_source is not None
                        receipt = revalidate_story_fragment_continuation_for_append(
                            authoring_response,
                            source=current_continuation_source,
                            expected_command_sha256=str(
                                envelope.get("authoring_command_sha256", "")
                            ),
                            expected_length_spec=expected_length_spec,
                            expected_context_spec=expected_context_spec,
                            expected_context_report_sha256=expected_context_report_sha256,
                            candidate_variant=candidate_variant,
                            accept_partial=accept_partial,
                        )
                        st.session_state[_key(prefix, "append_receipt")] = receipt
                except (CandidateApplyMismatchError, ValueError):
                    _store_failure(
                        prefix,
                        GenerationFailure(
                            category=GenerationErrorCategory.INVALID_STRUCTURED_OUTPUT,
                            safe_message=("候選稿或合約證據已改變；未套用，原稿完全沒有變更。"),
                        ),
                    )
                    st.error("候選稿驗證失敗；已阻止套用，原稿沒有變更。")
                    return None
                st.session_state[candidate_apply_validation_state_key(prefix)] = (
                    validation.model_dump(mode="json")
                )
            return "apply", selected_candidate, str(envelope.get("apply_mode", "rewrite"))
        if actions[1].button(
            "捨棄候選稿",
            width="stretch",
            key=_key(prefix, "discard"),
        ):
            return "discard", selected_candidate, str(envelope.get("apply_mode", "rewrite"))
    return None


def _render_value(value: JsonValue) -> str:
    if isinstance(value, list):
        return "\n".join(str(item) for item in value)
    if value is None:
        return ""
    return str(value)


def _render_authoring_contract_evidence(
    response: CreativeAuthoringResponse,
    *,
    candidate_variant: Literal["accumulated", "condensed"] = "accumulated",
) -> None:
    """Render exact saved request and measurement values without recomputation."""

    output = (
        response.provenance.condensed_output
        if candidate_variant == "condensed"
        else response.provenance.output
    )
    if output is None:
        return
    prose = response.provenance.prose_pipeline
    pipeline_decision = (
        prose.condensation_decision
        if prose is not None and candidate_variant == "condensed"
        else prose.decision
        if prose is not None
        else None
    )
    spec = response.provenance.length_spec
    assessment = output.assessment
    actual = output.measurement.actual
    minimum = "未設定" if spec.minimum is None else f"{spec.minimum:,}"
    maximum = "未設定" if spec.maximum is None else f"{spec.maximum:,}"
    message = (
        f"長度合約｜最少 {minimum} · 目標 {spec.target:,} · 最多 {maximum} "
        f"{spec.unit.value} · 實際 {actual:g} {spec.unit.value} · "
        f"要求 {spec.strictness.value} · 判定 {assessment.band.value}"
    )
    if (pipeline_decision is not None and not pipeline_decision.domain_complete) or (
        not assessment.satisfied and spec.strictness.value != "advisory"
    ):
        st.warning(f"{message}。目前只是未達標候選，不會顯示為完成。")
    elif assessment.band.value != "within":
        st.info(f"{message}。這是 advisory 差異，已保留供作者判斷。")
    else:
        st.success(message)

    provider_id = (
        response.provenance.provider.actual_provider_id
        or response.provenance.provider.requested_provider_id
    )
    if provider_id == "ollama":
        profile = response.provenance.ollama_context_profile
        st.caption(
            "Ollama context｜"
            f"{profile.profile.value} · num_ctx {profile.num_ctx:,} · "
            f"{profile.rationale}"
        )
        if profile.warning:
            st.warning(profile.warning)

    if prose is not None:
        accepted_continuations = sum(
            1
            for call in prose.calls
            if call.kind.value in {"continuation", "repetition_retry"}
            and call.accepted_into_candidate
        )
        finish_reasons = " → ".join(call.provider_finish_reason for call in prose.calls)
        st.caption(
            "正文長度保證｜"
            f"logical calls {len(prose.calls)}/{prose.max_logical_calls} · "
            f"runtime attempts {prose.total_runtime_provider_attempts}/"
            f"{prose.max_runtime_provider_attempts} · accepted continuations "
            f"{accepted_continuations} · finish {finish_reasons}"
        )
        if prose.total_max_estimated_cost_microunits is not None:
            st.caption(
                "有界 token-equivalent 成本｜"
                f"已計 {prose.total_estimated_cost_microunits:,} / "
                f"上限 {prose.total_max_estimated_cost_microunits:,}"
            )
        rejected_repetitions = sum(
            1
            for call in prose.calls
            if call.repetition_report is not None and not call.repetition_report.accepted
        )
        if rejected_repetitions:
            st.warning(f"已拒絕 {rejected_repetitions} 段重複續寫；重複內容未併入候選。")

        protected_report = (
            prose.condensed_protected_facts
            if candidate_variant == "condensed"
            else prose.accumulated_protected_facts
        )
        if protected_report is not None and protected_report.fact_count:
            st.caption(
                "鎖定事實檢查｜"
                f"literal {protected_report.literal_present_count}/"
                f"{protected_report.fact_count} · semantic review required"
            )
            if not protected_report.literal_gate_passed:
                st.warning(
                    f"有 {protected_report.unverified_count} 項鎖定事實未在候選中逐字出現；"
                    "尚未驗證語意保留，套用前請作者確認。"
                )
            else:
                st.info("鎖定事實已逐字出現；語意一致性仍需作者確認。")

    report = response.provenance.context_report
    st.caption(
        "上下文對帳｜"
        f"requested {len(report.requested)} · included {len(report.included)} · "
        f"summarized {len(report.summarized)} · truncated {len(report.truncated)}"
    )
    with st.expander("查看上下文納入明細", expanded=False):
        if isinstance(report, ContextCompilationReport):
            st.caption(
                f"總預算 {report.total_budget:,} 可見字元 · "
                f"實際納入 {report.budget_used:,} · "
                f"protected facts {report.protected_facts_visible_chars:,} · "
                f"must avoid {report.must_avoid_visible_chars:,}"
            )
            if not report.requested:
                st.caption("本次沒有選取一般上下文來源。")
            for source_entry in report.requested:
                st.caption(
                    f"{source_entry.source_type}:{source_entry.source_id}｜"
                    f"requested {source_entry.requested_visible_chars:,} · "
                    f"included {source_entry.included_visible_chars:,} · "
                    f"{source_entry.representation.value}"
                )
        elif isinstance(report, StoryContextProjectionReport):
            if not report.requested:
                st.caption("Story Context 本次沒有可投影來源。")
            for story_entry in report.requested:
                state = "included" if story_entry.included else "excluded"
                st.caption(
                    f"{story_entry.label}｜"
                    f"requested {story_entry.requested_visible_chars:,} · "
                    f"included {story_entry.included_visible_chars:,} · {state}"
                )


def model_candidate_factory(
    model_type: type[BaseModel],
    values: Callable[[], Mapping[str, object]],
) -> OfflineCandidateFactory:
    """Validate a deterministic local fallback with the same candidate schema."""

    def build() -> BaseModel:
        return model_type.model_validate(values())

    return build


__all__ = [
    "CandidateBackgroundJobMetadata",
    "CandidateDecision",
    "PassiveCreativeActionStatus",
    "authoring_editor_state_sha256",
    "candidate_apply_validation",
    "candidate_apply_validation_state_key",
    "candidate_authoring_response",
    "candidate_busy_state_key",
    "candidate_error_state_key",
    "candidate_job_manager_state_key",
    "candidate_job_metadata_state_key",
    "candidate_job_notice_state_key",
    "candidate_state_key",
    "candidate_value_replaces_current",
    "clear_candidate_state",
    "model_candidate_factory",
    "passive_creative_action_status",
    "render_candidate_action_state",
    "render_candidate_card",
    "render_candidate_error",
    "render_passive_creative_runtime_status",
    "request_candidate",
    "request_candidate_cancel",
]
