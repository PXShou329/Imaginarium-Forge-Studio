"""Story generation orchestration .

This is the single public business API for producing scene prose. It exists
because the previous design let a caller hand four unrelated arguments to the
low-level generator and get a "completed" run out of it: Scene 1 paired with
Scene 2's Scene Card or invented planning version IDs.

The fix is structural rather than defensive. The orchestrator:

1. loads the scene and derives the project through chapter → outline;
2. resolves every planning version from persisted data (accepted by default);
3. pins the exact Character Versions recorded on the Scene Card;
4. validates the selected mode and provider-visible content;
5. builds the context itself;
6. freezes everything into a ``GenerationInputSnapshot`` and hashes it;
7. commits a RUNNING run BEFORE any provider I/O;
8. calls the provider with no write transaction open;
9. finalizes the run exactly once.

There is no argument shape that can express a mismatched pair, so the class
of defect is closed rather than merely guarded.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, replace
from threading import Event

from imaginarium_forge.application.errors import (
    ConflictError,
    NotFoundError,
    SourcePlanningChainUnavailableError,
    ValidationFailedError,
)
from imaginarium_forge.application.services.base import ServiceBase, SessionProvider
from imaginarium_forge.application.services.generation.continuation_planner import (
    ContinuationPlan,
    ContinuationPlanner,
)
from imaginarium_forge.application.services.generation.length_assurance import (
    AssuranceAction,
    AssuranceLimits,
    AssuranceProgress,
    AssuranceReason,
    AssuranceStatus,
    LengthAssuranceDecision,
    LengthAssuranceService,
    ProviderTermination,
)
from imaginarium_forge.application.services.generation.protected_facts_guard import (
    ProtectedFactsGuard,
    ProtectedFactsReport,
)
from imaginarium_forge.application.services.generation.repetition_guard import (
    ContinuationRepetitionGuard,
)
from imaginarium_forge.application.services.scene_generation_service import (
    DraftOrigin,
    RevisionRequest,
    RunStatus,
    _word_count,
    build_revision_contract,
)
from imaginarium_forge.application.services.story_context_service import (
    BuildStoryContextRequest,
    ResolvedStoryContext,
    StoryContextService,
    StoryProtectedFactSet,
    build_story_protected_fact_set,
    render_story_protected_fact_prompt,
)
from imaginarium_forge.canonical import canonical_json
from imaginarium_forge.config.settings import AppSettings
from imaginarium_forge.domain.common.ids import utc_now_iso
from imaginarium_forge.domain.generation.durable import (
    CandidateActionKind,
    CandidateStatus,
    GenerationCandidate,
    GenerationJob,
    GenerationStep,
    JobStatus,
    StepStatus,
    sha256_json,
    sha256_text,
)
from imaginarium_forge.domain.generation.length import (
    GenerationLengthSpec,
    LengthStrictness,
    LengthUnit,
)
from imaginarium_forge.domain.prompt.content_mode import (
    ContentMode,
)
from imaginarium_forge.domain.story.context import (
    ContextBudgetSnapshot,
    ContextSourceRef,
    SourceEntityType,
)
from imaginarium_forge.domain.story.generation_input import (
    GenerationInputSnapshot,
    GenerationOptionsSnapshot,
    ParticipantPin,
    PlanningMode,
    ProviderOption,
)
from imaginarium_forge.domain.story.short_story import (
    CompleteStoryBrief,
    StoryGenerationPurpose,
)
from imaginarium_forge.infrastructure.db.adapters.story_candidate_undo_target import (
    imported_story_undo_snapshot,
    story_undo_snapshot,
)
from imaginarium_forge.infrastructure.db.repositories.story_repos import (
    DraftRecord,
    GenerationRunRecord,
    GenerationRunRepository,
    ParticipantRecord,
    SceneCardParticipantRepository,
    SceneCardVersionRepository,
    SceneDraftRepository,
    StorySceneRepository,
    VersionedEntityRepository,
)
from imaginarium_forge.providers.base import LLMProvider, TextStreamEventProvider
from imaginarium_forge.providers.contracts import (
    GenerationOptions,
    GenerationRequest,
    GenerationStreamDelta,
    GenerationStreamResult,
    general_audience_system,
)
from imaginarium_forge.providers.errors import (
    InvalidProviderResponseError,
    ModelNotFoundError,
    ProviderError,
    ProviderIncompleteError,
    ProviderRefusalError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    RequestCancelledError,
)


class ReasonCode:
    """Normalized failure codes stored on the run (/)."""

    NONE = ""
    EMPTY_OUTPUT = "empty_generation_output"
    PROVIDER_TIMEOUT = "provider_timeout"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    MODEL_NOT_FOUND = "model_not_found"
    CANCELLED = "cancelled_by_user"
    PROVIDER_ERROR = "provider_error"
    PROCESS_INTERRUPTED = "process_interrupted"
    PROVIDER_REFUSED = "provider_refused"
    LENGTH_ASSURANCE_PARTIAL = "length_assurance_partial"


_PARTIAL_DRAFT_REASON_CODES = frozenset(
    {
        ReasonCode.CANCELLED,
        ReasonCode.PROVIDER_TIMEOUT,
        ReasonCode.PROVIDER_UNAVAILABLE,
        ReasonCode.MODEL_NOT_FOUND,
        ReasonCode.PROVIDER_ERROR,
        ReasonCode.LENGTH_ASSURANCE_PARTIAL,
    }
)
_SAFE_PROVIDER_FINISH_REASON = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,99}$")
_STORY_PROVIDER_RUNTIME_ATTEMPT_CEILING = 3


def _bounded_runtime_attempts(retry_attempts: int) -> int:
    """Trust provider retry metadata only inside the configured hard ceiling."""

    runtime_attempts = retry_attempts + 1
    if runtime_attempts > _STORY_PROVIDER_RUNTIME_ATTEMPT_CEILING:
        error = InvalidProviderResponseError(
            "provider retry metadata exceeds the configured Story ceiling"
        )
        # Preserve the exact (already-consumed) count on the safe exception
        # path. The run must fail closed, but its evidence must not pretend a
        # provider contract breach only cost one runtime attempt.
        error._runtime_attempts_used = runtime_attempts  # type: ignore[attr-defined]
        raise error
    return runtime_attempts


def _provider_error_runtime_attempts(error: ProviderError) -> int:
    """Read adapter failure accounting without trusting an invalid value."""

    value = getattr(error, "_runtime_attempts_used", 1)
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        return 1
    return value


@dataclass(frozen=True, slots=True)
class GenerateSceneRequest:
    """``planning_mode`` decides whether draft planning may be used."""

    scene_id: str
    model: str
    scene_card_version_id: str | None = None
    requirement_version_id: str | None = None
    bible_version_id: str | None = None
    outline_version_id: str | None = None
    chapter_plan_version_id: str | None = None
    user_instruction: str = ""
    style_examples: tuple[str, ...] = ()
    temperature: float = 0.8
    timeout_s: float = 300.0
    max_context_chars: int | None = None
    stream: bool = False
    seed: int | None = None
    max_output_tokens: int | None = None
    top_p: float | None = None
    planning_mode: PlanningMode = PlanningMode.ACCEPTED
    preview_warning_acknowledged: bool = False
    context_budget: ContextBudgetSnapshot | None = None
    # Appended after every pre-R1 field for positional caller compatibility.
    generation_purpose: StoryGenerationPurpose = StoryGenerationPurpose.SCENE
    complete_story_brief: CompleteStoryBrief | None = None
    structured_must_avoid: tuple[str, ...] = ()
    # Optional for positional/backward compatibility. The service boundary
    # always resolves ``None`` to the purpose preset before provider I/O, so a
    # legacy caller cannot turn a short normal stop into ordinary success.
    length_spec: GenerationLengthSpec | None = None
    # Provider-neutral context window. The Story UI only supplies this for
    # an explicitly resolved Ollama profile; other providers are scrubbed in
    # ``_options`` so stale local capability state cannot leak across routes.
    num_ctx: int | None = None


@dataclass(frozen=True, slots=True)
class ReviseSceneRequest:
    """an ordinary revision replays the SOURCE RUN's planning chain.

    Re-deriving the chain from current accepted pointers was the defect:
    accepting new planning versions retroactively changed what an old draft
    had been revised against. If the source chain cannot be reconstructed,
    this fails closed — see ``RecoveryRebaseRequest`` for the audited escape.
    """

    draft_id: str
    model: str
    revision: RevisionRequest
    user_instruction: str = ""
    temperature: float = 0.7
    timeout_s: float = 300.0
    max_context_chars: int | None = None
    stream: bool = False
    seed: int | None = None
    max_output_tokens: int | None = None
    top_p: float | None = None
    context_budget: ContextBudgetSnapshot | None = None
    structured_must_avoid: tuple[str, ...] = ()
    length_spec: GenerationLengthSpec | None = None
    num_ctx: int | None = None


@dataclass(frozen=True, slots=True)
class RecoveryRebaseRequest:
    """the ONLY way to continue when the source chain is unavailable.

    This is not a revision. It produces a clearly-labelled Recovery Rebase
    draft under a NEW planning chain, records what was missing, and leaves the
    original draft and run untouched.
    """

    draft_id: str
    model: str
    rebase_reason: str
    revision: RevisionRequest
    scene_card_version_id: str | None = None
    chapter_plan_version_id: str | None = None
    outline_version_id: str | None = None
    bible_version_id: str | None = None
    requirement_version_id: str | None = None
    user_instruction: str = ""
    temperature: float = 0.7
    timeout_s: float = 300.0
    max_context_chars: int | None = None
    stream: bool = False
    context_budget: ContextBudgetSnapshot | None = None
    # Appended after every pre-R1 field for positional caller compatibility.
    generation_purpose: StoryGenerationPurpose | None = None
    complete_story_brief: CompleteStoryBrief | None = None
    structured_must_avoid: tuple[str, ...] = ()
    length_spec: GenerationLengthSpec | None = None
    num_ctx: int | None = None


@dataclass(slots=True)
class OrchestratedOutcome:
    run_id: str
    status: RunStatus
    snapshot: GenerationInputSnapshot
    draft: DraftRecord | None = None
    partial_draft: DraftRecord | None = None
    candidate_id: str | None = None
    reason_code: str = ReasonCode.NONE
    error_reason: str = ""
    planning_mode: PlanningMode = PlanningMode.ACCEPTED
    is_recovery_rebase: bool = False


@dataclass(frozen=True, slots=True)
class _ProviderCallOutcome:
    """One provider call, separated from domain-level length completion."""

    status: RunStatus
    text: str
    reason_code: str
    error_reason: str
    termination: ProviderTermination
    finish_reason: str
    runtime_attempts_used: int = 1
    reported_runtime_attempts: int | None = None
    runtime_attempt_ceiling_breached: bool = False

    def legacy_tuple(self) -> tuple[RunStatus, str, str, str]:
        return self.status, self.text, self.reason_code, self.error_reason


@dataclass(frozen=True, slots=True)
class _LengthAssuranceTrace:
    """In-memory call evidence; serialization is prose- and secret-free."""

    length_spec: GenerationLengthSpec
    decision: LengthAssuranceDecision
    progress: AssuranceProgress
    limits: AssuranceLimits
    runtime_provider_attempt_ceiling_per_logical_call: int
    runtime_provider_attempt_ceiling: int
    provider_calls: tuple[_ProviderCallOutcome, ...]
    selected_candidate: str
    preserved_overlength_text_sha256s: tuple[str, ...]
    output_text_sha256: str
    continuity_fact_report: ProtectedFactsReport | None = None
    literal_required_report: ProtectedFactsReport | None = None

    def canonical_json(self, *, partial_disposition: str) -> str:
        attempt_ledger = [
            {
                "logical_attempt_number": attempt_number,
                "runtime_provider_attempts": call.runtime_attempts_used,
                "reported_runtime_provider_attempts": (
                    call.reported_runtime_attempts
                ),
                "runtime_attempt_ceiling_breached": (
                    call.runtime_attempt_ceiling_breached
                ),
                "provider_termination": call.termination.value,
                "provider_finish_reason": _safe_provider_finish_reason(
                    call.finish_reason,
                    fallback=call.termination.value,
                ),
                "run_status": call.status.value,
                "reason_code": call.reason_code,
                "output_text_sha256": hashlib.sha256(
                    call.text.encode("utf-8")
                ).hexdigest(),
            }
            for attempt_number, call in enumerate(self.provider_calls, start=1)
        ]
        payload: dict[str, object] = {
            "policy_version": "story-length-assurance-v1",
            "length_spec": self.length_spec.model_dump(mode="json"),
            "length_spec_sha256": self.length_spec.sha256,
            "logical_provider_attempts": self.progress.provider_attempts,
            "runtime_provider_attempts": self.progress.runtime_provider_attempts,
            "continuations_used": self.progress.continuations_used,
            "repetition_retries_used": self.progress.repetition_retries_used,
            "logical_provider_attempt_ceiling": self.limits.max_total_attempts,
            "runtime_provider_attempt_ceiling_per_logical_call": (
                self.runtime_provider_attempt_ceiling_per_logical_call
            ),
            "runtime_provider_attempt_ceiling": self.runtime_provider_attempt_ceiling,
            "runtime_attempt_ceiling_breached": any(
                call.runtime_attempt_ceiling_breached for call in self.provider_calls
            ),
            "continuation_ceiling": self.length_spec.max_continuations,
            "repetition_retry_ceiling": self.limits.max_repetition_retries,
            "estimated_cost_microunits": self.progress.estimated_cost_microunits,
            "estimated_cost_ceiling_microunits": (
                self.limits.max_estimated_cost_microunits
            ),
            "provider_termination": self.decision.provider_termination.value,
            "provider_finish_reason": self.decision.provider_finish_reason,
            "assurance_status": self.decision.status.value,
            "assurance_action": self.decision.action.value,
            "assurance_reason": self.decision.reason.value,
            "provider_normal_stop": self.decision.provider_normal_stop,
            "domain_complete": self.decision.domain_complete,
            "selected_candidate": self.selected_candidate,
            "preserved_overlength_output_sha256s": list(
                self.preserved_overlength_text_sha256s
            ),
            "partial_disposition": partial_disposition,
            "measurement": self.decision.measurement.model_dump(mode="json"),
            "assessment": self.decision.assessment.model_dump(mode="json"),
            "output_text_sha256": self.output_text_sha256,
            "continuity_fact_report": (
                None
                if self.continuity_fact_report is None
                else self.continuity_fact_report.model_dump(mode="json")
            ),
            "literal_required_report": (
                None
                if self.literal_required_report is None
                else self.literal_required_report.model_dump(mode="json")
            ),
            "attempt_ledger": attempt_ledger,
            "attempt_ledger_sha256": hashlib.sha256(
                canonical_json(attempt_ledger).encode("utf-8")
            ).hexdigest(),
        }
        payload_json = canonical_json(payload)
        payload["evidence_payload_sha256"] = hashlib.sha256(
            payload_json.encode("utf-8")
        ).hexdigest()
        return canonical_json(payload)


@dataclass(frozen=True, slots=True)
class ImportDurableStoryCandidateRequest:
    """Reviewed durable candidate bound to one exact Story Scene snapshot."""

    candidate: GenerationCandidate
    job: GenerationJob
    step: GenerationStep
    action_kind: CandidateActionKind
    idempotency_key: str
    selected_prose: str
    before_snapshot: dict[str, object]
    selection: dict[str, object]
    content_mode: ContentMode
    expected_project_id: str
    expected_scene_id: str
    expected_scene_card_version_id: str
    expected_working_draft_id: str | None
    expected_accepted_draft_id: str | None
    expected_scene_revision: int | None = None


@dataclass(frozen=True, slots=True)
class ImportedDurableStoryCandidate:
    """Formal Story generation run and its unaccepted working draft."""

    run: GenerationRunRecord
    draft: DraftRecord
    after_snapshot: dict[str, object] | None = None


class _DurableStoryCandidateInputSnapshot(GenerationInputSnapshot):
    """Formal Story snapshot plus a canonical four-table receipt envelope.

    ``GenerationInputSnapshot`` readers can safely parse the common Story
    fields (Pydantic ignores this additive extension), while the raw immutable
    JSON retains exact durable job/step/candidate provenance for audit.
    """

    durable_candidate_receipt_json: str
    durable_candidate_receipt_sha256: str

    def model_post_init(self, __context: object) -> None:
        if (
            sha256_text(self.durable_candidate_receipt_json)
            != self.durable_candidate_receipt_sha256
        ):
            raise ValueError("durable candidate receipt hash 不一致")


class _OrchestrationBase(ServiceBase):
    def __init__(
        self,
        session_factory: SessionProvider,
        *,
        provider: LLMProvider,
        provider_name: str = "ollama",
        eligibility: object | None = None,
        settings: AppSettings | None = None,
    ) -> None:
        super().__init__(session_factory)
        self._provider = provider
        self._provider_name = provider_name
        self._eligibility = eligibility
        self._settings = settings
        self._context = StoryContextService(session_factory)
        self._length_assurance = LengthAssuranceService()
        self._continuation_planner = ContinuationPlanner()
        self._repetition_guard = ContinuationRepetitionGuard()
        self._protected_facts_guard = ProtectedFactsGuard()

    # ------------------------------------------------ shared context build
    def _build_bound_context(
        self,
        request: BuildStoryContextRequest,
    ) -> tuple[ResolvedStoryContext, tuple[str, ...]]:
        """Build fresh, ownership-checked context for generation and revision."""
        return self._context.build_for_scene(request), ()

    def _story_fact_set(
        self,
        resolved: ResolvedStoryContext,
    ) -> StoryProtectedFactSet:
        """Use the same typed projection as historical message replay."""

        with self._session_factory() as session:
            return build_story_protected_fact_set(session, resolved)

    # ------------------------------------------------------------- runs
    def _start_run(self, record: GenerationRunRecord) -> None:
        """commit RUNNING before provider I/O, so a crash still leaves
        evidence that the attempt happened."""
        with self._transaction() as session:
            GenerationRunRepository(session).add(record)

    @staticmethod
    def _provider_request(
        *,
        system: str,
        prompt: str,
        model: str,
        options: GenerationOptionsSnapshot,
    ) -> GenerationRequest:
        """Build the one exact request used by preflight and provider calls."""

        return GenerationRequest(
            model=model,
            system=system,
            prompt=prompt,
            options=GenerationOptions(
                temperature=options.temperature,
                top_p=options.top_p if options.top_p is not None else 0.9,
                seed=options.seed,
                num_predict=options.max_output_tokens,
                num_ctx=options.num_ctx,
            ),
            timeout_s=options.timeout_s,
        )

    def _assert_provider_request_author_content_safe(
        self,
        request: GenerationRequest,
    ) -> None:
        """Run an optional side-effect-free provider guard before persistence."""

        guard = getattr(self._provider, "assert_request_author_content_safe", None)
        if callable(guard):
            guard(request)

    def _call_provider(
        self,
        *,
        system: str,
        prompt: str,
        model: str,
        options: GenerationOptionsSnapshot,
        cancel: Event | None,
        on_chunk: Callable[[str], None] | None,
        content_mode: ContentMode,
        length_spec: GenerationLengthSpec | None,
        protected_facts: tuple[str, ...] = (),
        literal_required_facts: tuple[str, ...] = (),
    ) -> tuple[
        RunStatus,
        str,
        str,
        str,
        _LengthAssuranceTrace | None,
        tuple[str, ...],
    ]:
        """Return status/text/reason/error plus optional hash-only assurance evidence.

        No write transaction is open here . Cancellation keeps the
        chunks already received so the author never loses partial prose. A
        provider NORMAL_STOP is only one input to the optional domain length
        contract; it is never treated as proof that the requested prose unit
        is complete.
        """
        first = self._call_provider_once(
            system=system,
            prompt=prompt,
            model=model,
            options=options,
            cancel=cancel,
            on_chunk=on_chunk,
            content_mode=content_mode,
        )
        if length_spec is None:
            return *first.legacy_tuple(), None, ()
        return self._assure_provider_length(
            initial=first,
            system=system,
            base_prompt=prompt,
            model=model,
            options=options,
            cancel=cancel,
            on_chunk=on_chunk,
            content_mode=content_mode,
            length_spec=length_spec,
            protected_facts=protected_facts,
            literal_required_facts=literal_required_facts,
        )

    def _call_provider_once(
        self,
        *,
        system: str,
        prompt: str,
        model: str,
        options: GenerationOptionsSnapshot,
        cancel: Event | None,
        on_chunk: Callable[[str], None] | None,
        content_mode: ContentMode,
    ) -> _ProviderCallOutcome:
        """Make exactly one provider call and retain every received stream delta."""

        request = self._provider_request(
            model=model,
            system=system,
            prompt=prompt,
            options=options,
        )
        text = ""
        chunks: list[str] = []
        finish_reason = "normal_stop"
        runtime_attempts_used = 1
        try:
            if options.stream:
                live_generate = getattr(
                    self._provider,
                    "generate_text_with_deltas",
                    None,
                )
                if callable(live_generate):

                    def capture_delta(chunk: str) -> None:
                        if not chunk:
                            return
                        chunks.append(chunk)
                        if cancel is not None and cancel.is_set():
                            raise RequestCancelledError("使用者已取消生成")
                        if on_chunk is not None:
                            on_chunk(chunk)
                        if cancel is not None and cancel.is_set():
                            raise RequestCancelledError("使用者已取消生成")

                    result = live_generate(
                        request,
                        on_delta=capture_delta,
                        cancel=cancel,
                    )
                    if (
                        result.incomplete_reason == "content_filter"
                        or result.finish_reason == "content_filter"
                    ):
                        raise ProviderRefusalError("provider output was safety-filtered")
                    text = "".join(chunks)
                    if result.text != text:
                        if (
                            result.partial
                            and result.incomplete_reason == "cancelled"
                            and result.text.startswith(text)
                        ):
                            # The runtime preserves bytes already received at
                            # cancellation without publishing a new UI delta.
                            # Only this typed interruption permits an un-emitted
                            # suffix; completed output must still match exactly.
                            text = result.text
                        else:
                            raise InvalidProviderResponseError(
                                "stream callback text did not match its terminal result"
                            )
                    finish_reason = (
                        result.finish_reason or result.incomplete_reason or "stop"
                    )
                    runtime_attempts_used = _bounded_runtime_attempts(
                        result.retry_attempts
                    )
                    termination = _termination_from_provider_metadata(
                        partial=result.partial,
                        incomplete_reason=result.incomplete_reason,
                        finish_reason=result.finish_reason,
                    )
                elif isinstance(self._provider, TextStreamEventProvider):
                    terminal: GenerationStreamResult | None = None
                    for event in self._provider.stream_text_events(request, cancel=cancel):
                        if isinstance(event, GenerationStreamResult):
                            terminal = event
                            continue
                        if not isinstance(event, GenerationStreamDelta) or terminal is not None:
                            raise ProviderError("invalid provider stream event order")
                        chunks.append(event.text)
                        if cancel is not None and cancel.is_set():
                            raise RequestCancelledError("使用者已取消生成")
                        if on_chunk is not None:
                            on_chunk(event.text)
                        if cancel is not None and cancel.is_set():
                            raise RequestCancelledError("使用者已取消生成")
                    if terminal is None:
                        text = "".join(chunks)
                        return _ProviderCallOutcome(
                            status=RunStatus.FAILED,
                            text=text,
                            reason_code=ReasonCode.PROVIDER_ERROR,
                            error_reason="提供者串流在缺少終止資訊時中斷。",
                            termination=ProviderTermination.ERROR,
                            finish_reason="interrupted",
                        )
                    text = "".join(chunks)
                    if (
                        terminal.incomplete_reason == "content_filter"
                        or terminal.finish_reason == "content_filter"
                    ):
                        raise ProviderRefusalError("provider output was safety-filtered")
                    finish_reason = terminal.finish_reason or terminal.incomplete_reason or "stop"
                    runtime_attempts_used = _bounded_runtime_attempts(
                        terminal.retry_attempts
                    )
                    termination = _termination_from_provider_metadata(
                        partial=terminal.partial,
                        incomplete_reason=terminal.incomplete_reason,
                        finish_reason=terminal.finish_reason,
                    )
                else:
                    for chunk in self._provider.stream_text(request, cancel=cancel):
                        chunks.append(chunk)
                        if cancel is not None and cancel.is_set():
                            raise RequestCancelledError("使用者已取消生成")
                        if on_chunk is not None:
                            on_chunk(chunk)
                        if cancel is not None and cancel.is_set():
                            raise RequestCancelledError("使用者已取消生成")
                if not callable(live_generate):
                    text = "".join(chunks)
                if not callable(live_generate) and not isinstance(
                    self._provider,
                    TextStreamEventProvider,
                ):
                    termination = ProviderTermination.NORMAL_STOP
            else:
                result = self._provider.generate_text(request, cancel=cancel)
                if (
                    result.incomplete_reason == "content_filter"
                    or result.finish_reason == "content_filter"
                ):
                    raise ProviderRefusalError("provider output was safety-filtered")
                text = result.text
                finish_reason = result.finish_reason or result.incomplete_reason or "stop"
                runtime_attempts_used = _bounded_runtime_attempts(result.retry_attempts)
                termination = _termination_from_provider_metadata(
                    partial=result.partial,
                    incomplete_reason=result.incomplete_reason,
                    finish_reason=result.finish_reason,
                )
        except ProviderRefusalError as exc:
            return _refused_provider_call(_provider_error_runtime_attempts(exc))
        except RequestCancelledError:
            return _ProviderCallOutcome(
                status=RunStatus.CANCELLED,
                text="".join(chunks) if options.stream else text,
                reason_code=ReasonCode.CANCELLED,
                error_reason="使用者已取消生成",
                termination=ProviderTermination.CANCELLED,
                finish_reason="cancelled",
                runtime_attempts_used=runtime_attempts_used,
            )
        except ProviderTimeoutError:
            return _ProviderCallOutcome(
                status=RunStatus.TIMEOUT,
                text="".join(chunks) if options.stream else text,
                reason_code=ReasonCode.PROVIDER_TIMEOUT,
                error_reason="提供者請求逾時。",
                termination=ProviderTermination.TIMEOUT,
                finish_reason="timeout",
                runtime_attempts_used=runtime_attempts_used,
            )
        except ProviderIncompleteError as exc:
            if exc.reason == "content_filter":
                return _refused_provider_call(_provider_error_runtime_attempts(exc))
            partial_text = "".join(chunks) if options.stream else text
            token_limited = exc.reason == "max_output_tokens"
            return _ProviderCallOutcome(
                status=RunStatus.FAILED,
                text=partial_text,
                reason_code=ReasonCode.PROVIDER_ERROR,
                error_reason=(
                    "提供者因輸出上限停止；正文尚未完成。"
                    if token_limited
                    else "提供者回應不完整；已保留收到的正文。"
                ),
                termination=(
                    ProviderTermination.TOKEN_LIMIT
                    if token_limited
                    else ProviderTermination.ERROR
                ),
                finish_reason=exc.reason,
                runtime_attempts_used=runtime_attempts_used,
            )
        except ProviderUnavailableError:
            return _ProviderCallOutcome(
                status=RunStatus.FAILED,
                text="".join(chunks) if options.stream else text,
                reason_code=ReasonCode.PROVIDER_UNAVAILABLE,
                error_reason="提供者目前無法連線。",
                termination=ProviderTermination.ERROR,
                finish_reason="error",
                runtime_attempts_used=runtime_attempts_used,
            )
        except ModelNotFoundError:
            return _ProviderCallOutcome(
                status=RunStatus.FAILED,
                text="".join(chunks) if options.stream else text,
                reason_code=ReasonCode.MODEL_NOT_FOUND,
                error_reason="指定模型不存在或尚未安裝。",
                termination=ProviderTermination.ERROR,
                finish_reason="error",
                runtime_attempts_used=runtime_attempts_used,
            )
        except ProviderError as exc:
            reported_attempts = _provider_error_runtime_attempts(exc)
            ceiling_breached = (
                reported_attempts > _STORY_PROVIDER_RUNTIME_ATTEMPT_CEILING
            )
            return _ProviderCallOutcome(
                status=RunStatus.FAILED,
                text="".join(chunks) if options.stream else text,
                reason_code=ReasonCode.PROVIDER_ERROR,
                error_reason="提供者請求失敗。",
                termination=ProviderTermination.ERROR,
                finish_reason="error",
                runtime_attempts_used=max(
                    runtime_attempts_used,
                    min(
                        reported_attempts,
                        _STORY_PROVIDER_RUNTIME_ATTEMPT_CEILING,
                    ),
                ),
                reported_runtime_attempts=(
                    reported_attempts if ceiling_breached else None
                ),
                runtime_attempt_ceiling_breached=ceiling_breached,
            )

        # whitespace is not prose. A "completed" run with no draft was
        # the original defect — it looked successful and produced nothing.
        if not text.strip():
            return _ProviderCallOutcome(
                status=RunStatus.FAILED,
                text="",
                reason_code=ReasonCode.EMPTY_OUTPUT,
                error_reason="提供者回傳空白內容（無有效正文）",
                termination=termination,
                finish_reason=finish_reason,
                runtime_attempts_used=runtime_attempts_used,
            )
        if termination is not ProviderTermination.NORMAL_STOP:
            return _ProviderCallOutcome(
                status=RunStatus.FAILED,
                text=text,
                reason_code=ReasonCode.PROVIDER_ERROR,
                error_reason="提供者在正文完成前停止；已保留收到的正文。",
                termination=termination,
                finish_reason=finish_reason,
                runtime_attempts_used=runtime_attempts_used,
            )
        return _ProviderCallOutcome(
            status=RunStatus.COMPLETED,
            text=text,
            reason_code=ReasonCode.NONE,
            error_reason="",
            termination=termination,
            finish_reason=finish_reason,
            runtime_attempts_used=runtime_attempts_used,
        )

    def _assure_provider_length(
        self,
        *,
        initial: _ProviderCallOutcome,
        system: str,
        base_prompt: str,
        model: str,
        options: GenerationOptionsSnapshot,
        cancel: Event | None,
        on_chunk: Callable[[str], None] | None,
        content_mode: ContentMode,
        length_spec: GenerationLengthSpec,
        protected_facts: tuple[str, ...],
        literal_required_facts: tuple[str, ...],
    ) -> tuple[
        RunStatus,
        str,
        str,
        str,
        _LengthAssuranceTrace,
        tuple[str, ...],
    ]:
        """Apply bounded continuation without replacing the author's candidate."""

        estimated_call_cost = options.max_output_tokens or 0
        max_total_attempts = 1 + length_spec.max_continuations + 2
        max_runtime_attempts = (
            max_total_attempts * _STORY_PROVIDER_RUNTIME_ATTEMPT_CEILING
        )
        estimated_runtime_call_cost = (
            estimated_call_cost * _STORY_PROVIDER_RUNTIME_ATTEMPT_CEILING
        )
        limits = AssuranceLimits(
            max_total_attempts=max_total_attempts,
            max_repetition_retries=2,
            max_estimated_cost_microunits=(
                estimated_call_cost * max_runtime_attempts
                if estimated_call_cost
                else None
            ),
        )
        accumulated_text = initial.text
        last = initial
        provider_calls = [initial]
        preserved_overlength_texts: list[str] = []
        selected_candidate = "accumulated"
        progress = AssuranceProgress(
            provider_attempts=1,
            runtime_provider_attempts=initial.runtime_attempts_used,
            continuations_used=0,
            repetition_retries_used=0,
            estimated_cost_microunits=(
                estimated_call_cost * initial.runtime_attempts_used
            ),
            next_attempt_estimated_cost_microunits=estimated_runtime_call_cost,
        )
        decision = self._length_assurance.evaluate(
            text=accumulated_text,
            spec=length_spec,
            provider_termination=initial.termination,
            provider_finish_reason=initial.finish_reason,
            provider_error_recoverable=initial.status is not RunStatus.FAILED,
            progress=progress,
            limits=limits,
        )
        while decision.action in {
            AssuranceAction.CONTINUE,
            AssuranceAction.CREATE_CONDENSED_CANDIDATE,
        }:
            if decision.action is AssuranceAction.CREATE_CONDENSED_CANDIDATE:
                if (
                    accumulated_text.strip()
                    and accumulated_text not in preserved_overlength_texts
                ):
                    preserved_overlength_texts.append(accumulated_text)
                current = self._call_provider_once(
                    system=system,
                    prompt=_story_condensation_prompt(
                        base_prompt=base_prompt,
                        overlength_text=accumulated_text,
                        length_spec=length_spec,
                        protected_facts=protected_facts,
                    ),
                    model=model,
                    options=options,
                    cancel=cancel,
                    # A condensed candidate is separate from the streamed
                    # original. Publishing both through a string-only callback
                    # would visually concatenate them and violate author control.
                    on_chunk=None,
                    content_mode=content_mode,
                )
                last = current
                provider_calls.append(current)
                selected_candidate = "condensed"
                accumulated_text = current.text
                progress = progress.model_copy(
                    update={
                        "provider_attempts": progress.provider_attempts + 1,
                        "runtime_provider_attempts": (
                            progress.runtime_provider_attempts
                            + current.runtime_attempts_used
                        ),
                        "estimated_cost_microunits": (
                            progress.estimated_cost_microunits
                            + estimated_call_cost * current.runtime_attempts_used
                        ),
                    }
                )
                decision = self._length_assurance.evaluate(
                    text=accumulated_text,
                    spec=length_spec,
                    provider_termination=current.termination,
                    provider_finish_reason=current.finish_reason,
                    provider_error_recoverable=(
                        current.status is not RunStatus.FAILED
                    ),
                    progress=progress,
                    limits=limits,
                )
                continue

            plan = self._continuation_planner.plan(
                existing_text=accumulated_text,
                spec=length_spec,
                continuations_used=progress.continuations_used,
                protected_facts=protected_facts,
                provider_incomplete=last.termination
                in {ProviderTermination.TOKEN_LIMIT, ProviderTermination.TIMEOUT},
            )
            repetition_retry_number = 0
            while True:
                continuation_prompt = _story_continuation_prompt(
                    base_prompt=base_prompt,
                    plan=plan,
                    repetition_retry_number=repetition_retry_number,
                )
                current = self._call_provider_once(
                    system=system,
                    prompt=continuation_prompt,
                    model=model,
                    options=options,
                    cancel=cancel,
                    # Continuation/retry candidates are buffered. An
                    # append-only callback cannot retract duplicate/rejected
                    # text and would visually splice separate candidates into
                    # the initial live preview. The terminal outcome exposes
                    # the accepted merged candidate once assurance completes.
                    on_chunk=None,
                    content_mode=content_mode,
                )
                last = current
                provider_calls.append(current)
                progress = progress.model_copy(
                    update={
                        "provider_attempts": progress.provider_attempts + 1,
                        "runtime_provider_attempts": (
                            progress.runtime_provider_attempts
                            + current.runtime_attempts_used
                        ),
                        "estimated_cost_microunits": (
                            progress.estimated_cost_microunits
                            + estimated_call_cost * current.runtime_attempts_used
                        ),
                    }
                )

                if current.termination is ProviderTermination.CANCELLED:
                    if current.text.strip():
                        accumulated_text = _append_partial(accumulated_text, current.text)
                        progress = progress.model_copy(
                            update={"continuations_used": progress.continuations_used + 1}
                        )
                    decision = self._length_assurance.evaluate(
                        text=accumulated_text,
                        spec=length_spec,
                        provider_termination=current.termination,
                        provider_finish_reason=current.finish_reason,
                        progress=progress,
                        limits=limits,
                    )
                    break

                if current.text.strip():
                    merge = self._repetition_guard.merge(
                        existing_text=accumulated_text,
                        continuation_text=current.text,
                    )
                    if merge.accepted:
                        accumulated_text = merge.combined_text
                        progress = progress.model_copy(
                            update={"continuations_used": progress.continuations_used + 1}
                        )
                    else:
                        decision = self._length_assurance.repetition_rejected(
                            preserved_text=accumulated_text,
                            spec=length_spec,
                            progress=progress,
                            limits=limits,
                            provider_termination=current.termination,
                            provider_finish_reason=current.finish_reason,
                        )
                        if decision.action is AssuranceAction.CONTINUE:
                            repetition_retry_number += 1
                            progress = progress.model_copy(
                                update={
                                    "repetition_retries_used": (
                                        progress.repetition_retries_used + 1
                                    )
                                }
                            )
                            continue
                        break

                decision = self._length_assurance.evaluate(
                    text=accumulated_text,
                    spec=length_spec,
                    provider_termination=current.termination,
                    provider_finish_reason=current.finish_reason,
                    provider_error_recoverable=current.status is not RunStatus.FAILED,
                    progress=progress,
                    limits=limits,
                )
                break

        continuity_report = (
            self._protected_facts_guard.inspect(
                candidate_text=accumulated_text,
                protected_facts=protected_facts,
            )
            if protected_facts
            else None
        )
        literal_required_report = (
            self._protected_facts_guard.inspect(
                candidate_text=accumulated_text,
                protected_facts=literal_required_facts,
            )
            if literal_required_facts
            else None
        )
        if (
            decision.domain_complete
            and literal_required_report is not None
            and not literal_required_report.literal_gate_passed
        ):
                decision = self._length_assurance.protected_facts_unverified(
                    text=accumulated_text,
                    spec=length_spec,
                    provider_termination=last.termination,
                    provider_finish_reason=last.finish_reason,
                    progress=progress,
                    limits=limits,
                )

        trace = _LengthAssuranceTrace(
            length_spec=length_spec,
            decision=decision,
            progress=progress,
            limits=limits,
            runtime_provider_attempt_ceiling_per_logical_call=(
                _STORY_PROVIDER_RUNTIME_ATTEMPT_CEILING
            ),
            runtime_provider_attempt_ceiling=max_runtime_attempts,
            provider_calls=tuple(provider_calls),
            selected_candidate=selected_candidate,
            preserved_overlength_text_sha256s=tuple(
                hashlib.sha256(item.encode("utf-8")).hexdigest()
                for item in preserved_overlength_texts
            ),
            output_text_sha256=hashlib.sha256(
                accumulated_text.encode("utf-8")
            ).hexdigest(),
            continuity_fact_report=continuity_report,
            literal_required_report=literal_required_report,
        )

        if last.reason_code == ReasonCode.PROVIDER_REFUSED:
            # A refusal or output credential failure invalidates this candidate,
            # including a prior continuation/condensation prefix. Never recover
            # it through the generic transport-partial persistence path.
            return (
                RunStatus.FAILED,
                "",
                ReasonCode.PROVIDER_REFUSED,
                last.error_reason,
                trace,
                (),
            )
        if decision.domain_complete:
            return (
                RunStatus.COMPLETED,
                accumulated_text,
                ReasonCode.NONE,
                "",
                trace,
                tuple(preserved_overlength_texts),
            )
        if last.termination is ProviderTermination.CANCELLED:
            return (
                RunStatus.CANCELLED,
                accumulated_text,
                ReasonCode.CANCELLED,
                last.error_reason,
                trace,
                tuple(preserved_overlength_texts),
            )
        if last.termination is ProviderTermination.TIMEOUT:
            return (
                RunStatus.TIMEOUT,
                accumulated_text,
                ReasonCode.PROVIDER_TIMEOUT,
                last.error_reason,
                trace,
                tuple(preserved_overlength_texts),
            )
        if last.termination is ProviderTermination.ERROR:
            return (
                last.status,
                accumulated_text,
                last.reason_code,
                last.error_reason,
                trace,
                tuple(preserved_overlength_texts),
            )
        if not accumulated_text.strip():
            return *last.legacy_tuple(), trace, tuple(preserved_overlength_texts)
        actual = decision.measurement.actual
        minimum = length_spec.minimum
        if minimum is None:  # pragma: no cover - public validation fails closed first
            raise ValidationFailedError("Story Studio 長度合約缺少強制最低值。")
        return (
            RunStatus.FAILED,
            accumulated_text,
            ReasonCode.LENGTH_ASSURANCE_PARTIAL,
            "正文未符合已保存的長度合約"
            f"（目前 {actual:g} {length_spec.unit.value}；最低 {minimum:g}）；"
            "自動續寫上限已耗盡或仍需作者處理，已保留為不可接受的 partial 草稿。",
            trace,
            tuple(preserved_overlength_texts),
        )



    def _finalize(
        self,
        *,
        run_id: str,
        scene_id: str,
        project_id: str,
        card_version_id: str,
        status: RunStatus,
        reason_code: str,
        error_reason: str,
        text: str,
        started: float,
        origin: DraftOrigin,
        parent_draft_id: str | None = None,
        revision_json: str = "{}",
        revision_fingerprint: str | None = None,
        is_preview: bool = False,
        is_recovery_rebase: bool = False,
        length_assurance_json: str = "{}",
        preserved_candidate_texts: tuple[str, ...] = (),
    ) -> tuple[DraftRecord | None, DraftRecord | None, str | None]:
        """Finalize the run and persist any resulting draft in one transaction."""
        latency_ms = int((time.monotonic() - started) * 1000)
        draft: DraftRecord | None = None
        partial: DraftRecord | None = None
        candidate_id: str | None = None
        with self._transaction() as session:
            runs = GenerationRunRepository(session)
            drafts = SceneDraftRepository(session)
            scenes = StorySceneRepository(session)

            for preserved_text in preserved_candidate_texts:
                partial = DraftRecord(
                    id=str(uuid.uuid4()),
                    story_scene_id=scene_id,
                    project_id=project_id,
                    draft_number=drafts.next_draft_number(scene_id),
                    prose_text=preserved_text.strip(),
                    word_count=_word_count(preserved_text),
                    origin=origin.value,
                    draft_status="partial",
                    was_accepted=False,
                    accepted_at="",
                    is_preview=is_preview,
                    promoted_from_preview_draft_id=None,
                    is_recovery_rebase=is_recovery_rebase,
                    scene_card_version_id=card_version_id,
                    generation_run_id=run_id,
                    revision_request_json=revision_json,
                    revision_request_fingerprint=revision_fingerprint,
                    parent_draft_id=parent_draft_id,
                    created_at=utc_now_iso(),
                )
                drafts.add(partial)
                session.flush()

            if status is RunStatus.COMPLETED:
                draft = DraftRecord(
                    id=str(uuid.uuid4()),
                    story_scene_id=scene_id,
                    project_id=project_id,
                    draft_number=drafts.next_draft_number(scene_id),
                    prose_text=text.strip(),
                    word_count=_word_count(text),
                    origin=origin.value,
                    draft_status="complete",
                    was_accepted=False,
                    accepted_at="",
                    is_preview=is_preview,
                    promoted_from_preview_draft_id=None,
                    is_recovery_rebase=is_recovery_rebase,
                    scene_card_version_id=card_version_id,
                    generation_run_id=run_id,
                    revision_request_json=revision_json,
                    revision_request_fingerprint=revision_fingerprint,
                    parent_draft_id=parent_draft_id,
                    created_at=utc_now_iso(),
                )
                drafts.add(draft)
                session.flush()
            elif (
                text.strip()
                and reason_code in _PARTIAL_DRAFT_REASON_CODES
            ):
                # Cancelled, interrupted, provider-
                # failed, or length-exhausted prose is recoverable and clearly
                # labelled. A DB CHECK keeps every such partial unacceptable.
                partial = DraftRecord(
                    id=str(uuid.uuid4()),
                    story_scene_id=scene_id,
                    project_id=project_id,
                    draft_number=drafts.next_draft_number(scene_id),
                    prose_text=text.strip(),
                    word_count=_word_count(text),
                    origin=origin.value,
                    draft_status="partial",
                    was_accepted=False,
                    accepted_at="",
                    is_preview=is_preview,
                    promoted_from_preview_draft_id=None,
                    is_recovery_rebase=is_recovery_rebase,
                    scene_card_version_id=card_version_id,
                    generation_run_id=run_id,
                    revision_request_json=revision_json,
                    revision_request_fingerprint=revision_fingerprint,
                    parent_draft_id=parent_draft_id,
                    created_at=utc_now_iso(),
                )
                drafts.add(partial)

            # N3 lifecycle evidence is checked against the exact draft/block
            # projection by a BEFORE UPDATE
            # trigger. Those artifacts therefore have to exist first, while
            # remaining in the same atomic transaction as finalization.
            session.flush()
            runs.finalize(
                run_id,
                status=status.value,
                reason_code=reason_code,
                error_reason=error_reason,
                completed_at=utc_now_iso(),
                latency_ms=latency_ms,
                length_assurance_json=length_assurance_json,
            )
            session.flush()
            if draft is not None:
                scenes.update_fields(
                    scene_id,
                    working_draft_id=draft.id,
                    updated_at=utc_now_iso(),
                )
        return draft, partial, candidate_id

    # ------------------------------------------------------- shared engine
    def _execute(
        self,
        *,
        snapshot: GenerationInputSnapshot,
        resolved: ResolvedStoryContext,
        origin: DraftOrigin,
        system: str,
        prompt: str,
        options: GenerationOptionsSnapshot,
        length_spec: GenerationLengthSpec | None,
        cancel: Event | None,
        on_chunk: Callable[[str], None] | None,
        preview_ack: bool = False,
        parent_draft_id: str | None = None,
        revision_json: str = "{}",
        revision_fingerprint: str | None = None,
        rebase_reason: str = "",
    ) -> OrchestratedOutcome:
        """Commit RUNNING, call the provider, finalize exactly once."""
        if length_spec is None:
            raise ValidationFailedError(
                "Story Studio 正式正文路徑缺少已解析的長度合約。"
            )
        ContentMode(snapshot.content_mode)
        system = general_audience_system(system)
        fact_set = self._story_fact_set(resolved)
        fact_prompt = render_story_protected_fact_prompt(fact_set.continuity)
        if fact_prompt:
            prompt = f"{prompt}\n\n{fact_prompt}"
        # bind the snapshot to the EXACT provider-visible messages
        snapshot = snapshot.model_copy(
            update={
                "system_message_sha256": hashlib.sha256(system.encode("utf-8")).hexdigest(),
                "user_message_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
            }
        )
        # The mature Story boundary normally persists a RUNNING receipt before
        # provider I/O. Credential-in-author-content is the one fail-closed
        # exception: use the adapter's side-effect-free guard on the exact
        # request first so neither the provider nor SQLite can receive a pasted
        # active key. Legacy/test providers remain structurally compatible.
        self._assert_provider_request_author_content_safe(
            self._provider_request(
                system=system,
                prompt=prompt,
                model=snapshot.model,
                options=options,
            )
        )
        run_id = str(uuid.uuid4())
        started = time.monotonic()
        record = _run_record(run_id, snapshot, resolved, utc_now_iso())
        record.preview_warning_acknowledged = int(preview_ack)
        record.parent_draft_id = parent_draft_id
        record.revision_request_fingerprint = revision_fingerprint or ""
        record.rebase_reason = rebase_reason
        record.is_recovery_rebase = int(snapshot.is_recovery_rebase)
        record.missing_source_version_ids_json = json.dumps(
            list(snapshot.missing_source_version_ids), ensure_ascii=False
        )
        # hashes always; the rendered text only when the project allows
        store_raw = (
            True if self._settings is None else self._settings.store_rendered_generation_messages
        )
        record.rendered_message_storage_enabled = int(store_raw)
        record.system_message_byte_size = len(system.encode("utf-8"))
        record.user_message_byte_size = len(prompt.encode("utf-8"))
        if store_raw:
            record.rendered_system_message = system
            record.rendered_user_message = prompt
        self._start_run(record)
        assurance_trace: _LengthAssuranceTrace | None = None
        preserved_candidate_texts: tuple[str, ...] = ()
        (
            status,
            text,
            reason_code,
            error_reason,
            assurance_trace,
            preserved_candidate_texts,
        ) = self._call_provider(
            system=system,
            prompt=prompt,
            model=snapshot.model,
            options=options,
            cancel=cancel,
            on_chunk=on_chunk,
            content_mode=snapshot.content_mode,
            length_spec=length_spec,
            protected_facts=fact_set.continuity,
            literal_required_facts=fact_set.literal_required,
        )
        if (
            assurance_trace is not None
            and reason_code == ReasonCode.PROVIDER_REFUSED
        ):
            assurance_trace = _refused_assurance_trace(assurance_trace)
        if (
            status is RunStatus.COMPLETED
            and on_chunk is not None
            and not options.stream
        ):
            # Non-stream calls publish once after the complete output passes
            # policy. Stream calls already emitted each safe provider delta.
            on_chunk(text)
        length_assurance_json = (
            "{}"
            if assurance_trace is None
            else assurance_trace.canonical_json(
                partial_disposition=_length_assurance_disposition(
                    status=status,
                    reason_code=reason_code,
                    text=text,
                    preserved_candidate=bool(preserved_candidate_texts),
                )
            )
        )
        draft, partial, candidate_id = self._finalize(
            run_id=run_id,
            scene_id=resolved.scene_id,
            project_id=resolved.project_id,
            card_version_id=resolved.scene_card_version_id,
            status=status,
            reason_code=reason_code,
            error_reason=error_reason,
            text=text,
            started=started,
            origin=origin,
            parent_draft_id=parent_draft_id,
            revision_json=revision_json,
            revision_fingerprint=revision_fingerprint,
            is_preview=snapshot.planning_mode is PlanningMode.PREVIEW,
            is_recovery_rebase=snapshot.is_recovery_rebase,
            length_assurance_json=length_assurance_json,
            preserved_candidate_texts=preserved_candidate_texts,
        )
        return OrchestratedOutcome(
            run_id=run_id,
            status=status,
            snapshot=snapshot,
            draft=draft,
            partial_draft=partial,
            candidate_id=candidate_id,
            reason_code=reason_code,
            error_reason=error_reason,
            planning_mode=snapshot.planning_mode,
            is_recovery_rebase=snapshot.is_recovery_rebase,
        )

    def _options(self, request: object) -> GenerationOptionsSnapshot:
        """capture every behaviour-affecting option, not just one."""
        return GenerationOptionsSnapshot(
            temperature=getattr(request, "temperature", 0.8),
            timeout_s=getattr(request, "timeout_s", 300.0),
            stream=getattr(request, "stream", False),
            seed=getattr(request, "seed", None),
            max_output_tokens=getattr(request, "max_output_tokens", None),
            num_ctx=(
                getattr(request, "num_ctx", None)
                if self._provider_name.casefold() == "ollama"
                else None
            ),
            top_p=getattr(request, "top_p", None),
        )


class StoryGenerationOrchestrationService(_OrchestrationBase):
    """The public API for generating a scene ."""

    @staticmethod
    def durable_candidate_run_id(idempotency_key: str) -> str:
        normalized = idempotency_key.strip()
        if not normalized or len(normalized) > 200:
            raise ValidationFailedError("Story 候選套用冪等鍵必須是 1 到 200 字元")
        return str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"imaginarium-forge:story-generation-run:durable:{normalized}",
            )
        )

    @staticmethod
    def durable_candidate_draft_id(idempotency_key: str) -> str:
        normalized = idempotency_key.strip()
        if not normalized or len(normalized) > 200:
            raise ValidationFailedError("Story 候選套用冪等鍵必須是 1 到 200 字元")
        return str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"imaginarium-forge:story-draft:durable:{normalized}",
            )
        )

    @classmethod
    def import_durable_candidate(
        cls,
        session_factory: SessionProvider,
        request: ImportDurableStoryCandidateRequest,
    ) -> ImportedDurableStoryCandidate:
        """Atomically import one reviewed four-table candidate as a Story draft.

        The durable workflow already performed provider I/O. This boundary
        creates an immutable Story ``generation_run`` receipt and an
        unaccepted generated draft in one transaction; it never calls a
        provider and never updates Story Memory or the accepted pointer.
        """

        cls._validate_durable_candidate_import(request)
        run_id = cls.durable_candidate_run_id(request.idempotency_key)
        draft_id = cls.durable_candidate_draft_id(request.idempotency_key)
        now = utc_now_iso()
        session = session_factory()
        try:
            with session.begin():
                runs = GenerationRunRepository(session)
                drafts = SceneDraftRepository(session)
                scenes = StorySceneRepository(session)
                scene = scenes.get(request.expected_scene_id)
                if scene is None:
                    raise NotFoundError(
                        f"找不到場景：{request.expected_scene_id}"
                    )
                if scene.project_id != request.expected_project_id:
                    raise ConflictError("Story 候選的專案與目前 Scene 不一致")
                card = SceneCardVersionRepository(session).get(
                    request.expected_scene_card_version_id
                )
                if (
                    card is None
                    or card.parent_id != scene.id
                    or card.project_id != scene.project_id
                ):
                    raise ValidationFailedError(
                        "Story 候選的 Scene Card 版本不存在或歸屬不一致"
                    )
                try:
                    ContentMode(
                        str(card.payload.get("content_mode", "general"))
                    )
                except ValueError as exc:
                    raise ValidationFailedError(
                        "Story 候選的 Scene Card content mode 無效"
                    ) from exc

                participant_rows = SceneCardParticipantRepository(
                    session
                ).list_for_card_version(request.expected_scene_card_version_id)
                existing_run = runs.get(run_id)
                existing_draft = drafts.get(draft_id)
                if (existing_run is None) != (existing_draft is None):
                    raise ConflictError(
                        "Story 候選套用的 run/draft 原子收據不完整；沒有重建或覆寫。"
                    )
                expected_revision = scenes.get_revision(scene.id)
                after_snapshot = (
                    imported_story_undo_snapshot(existing_run)
                    if existing_run is not None else story_undo_snapshot(
                        replace(scene, working_draft_id=draft_id, updated_at=now),
                        revision=expected_revision + 1,
                        scene_card_version_id=request.expected_scene_card_version_id,
                        candidate_id=request.candidate.id,
                        generation_run_id=run_id,
                    )
                )
                input_snapshot = cls._durable_candidate_input_snapshot(
                    request,
                    participant_rows=participant_rows,
                    target_after=after_snapshot,
                )
                input_sha256 = input_snapshot.sha256
                if existing_run is not None or existing_draft is not None:
                    if existing_run is None or existing_draft is None:
                        raise ConflictError(
                            "Story 候選套用的 run/draft 原子收據不完整；沒有重建或覆寫。"
                        )
                    cls._assert_durable_import_replay(
                        request,
                        existing_run,
                        existing_draft,
                        input_sha256=input_sha256,
                    )
                    return ImportedDurableStoryCandidate(
                        run=existing_run,
                        draft=existing_draft,
                        after_snapshot=after_snapshot,
                    )

                if (
                    scene.working_draft_id != request.expected_working_draft_id
                    or scene.accepted_draft_id != request.expected_accepted_draft_id
                ):
                    raise ConflictError(
                        "Story Scene 的 working/accepted draft 已變更；沒有建立 run 或草稿。"
                    )
                if (
                    request.expected_scene_revision is not None
                    and expected_revision != request.expected_scene_revision
                ):
                    raise ConflictError("單章確認後場景 revision 已變更；沒有建立 run 或草稿。")
                if request.expected_scene_card_version_id not in {
                    scene.accepted_card_version_id,
                    scene.working_head_card_version_id,
                }:
                    raise ConflictError(
                        "Story 候選的 Scene Card 已不再是目前版本"
                    )
                if request.expected_working_draft_id is not None:
                    parent = drafts.get(request.expected_working_draft_id)
                    if (
                        parent is None
                        or parent.story_scene_id != scene.id
                        or parent.project_id != scene.project_id
                        or parent.scene_card_version_id
                        != request.expected_scene_card_version_id
                    ):
                        raise ConflictError(
                            "Story 候選的來源 working draft 已失效或歸屬不一致"
                        )

                context = request.job.context_snapshot
                provider = request.candidate.provenance.provider
                model = request.candidate.provenance.model
                assert provider is not None and model is not None
                system_message = cls._required_context_message(
                    context,
                    "system_message",
                )
                user_message = cls._required_context_message(
                    context,
                    "user_message",
                )
                planning_mode = input_snapshot.planning_mode.value
                run = GenerationRunRecord(
                    id=run_id,
                    story_scene_id=scene.id,
                    project_id=scene.project_id,
                    scene_card_version_id=request.expected_scene_card_version_id,
                    started_at=now,
                    run_kind="generation",
                    provider=provider,
                    model=model,
                    options_snapshot_json=input_snapshot.options.model_dump_json(),
                    options_snapshot_sha256=input_snapshot.options.sha256,
                    context_fingerprint=input_snapshot.context_fingerprint,
                    planning_chain_fingerprint=(
                        input_snapshot.planning_chain_fingerprint
                    ),
                    planning_mode=planning_mode,
                    preview_warning_acknowledged=int(planning_mode == "preview"),
                    renderer_version=input_snapshot.renderer_version,
                    context_schema_version=input_snapshot.context_schema_version,
                    context_contract_version=input_snapshot.context_contract_version,
                    context_budget_policy_version=(
                        input_snapshot.context_budget_policy_version
                    ),
                    eligibility_fingerprint=input_snapshot.eligibility_fingerprint,
                    eligibility_evaluation_ids_json=canonical_json(
                        list(input_snapshot.eligibility_evaluation_ids)
                    ),
                    input_snapshot_json=input_snapshot.canonical_payload(),
                    input_snapshot_sha256=input_sha256,
                    requirement_version_id=input_snapshot.requirement_version_id,
                    bible_version_id=input_snapshot.bible_version_id,
                    outline_version_id=input_snapshot.outline_version_id,
                    chapter_plan_version_id=(
                        input_snapshot.chapter_plan_version_id
                    ),
                    pov_character_version_id=(
                        input_snapshot.pov_character_version_id
                    ),
                    character_version_ids_json=canonical_json(
                        list(input_snapshot.character_version_ids)
                    ),
                    parent_draft_id=request.expected_working_draft_id,
                    system_message_sha256=input_snapshot.system_message_sha256,
                    user_message_sha256=input_snapshot.user_message_sha256,
                    system_message_byte_size=len(system_message.encode("utf-8")),
                    user_message_byte_size=len(user_message.encode("utf-8")),
                    rendered_system_message=None,
                    rendered_user_message=None,
                    rendered_message_storage_enabled=0,
                    content_mode=request.content_mode.value,
                    status=RunStatus.RUNNING.value,
                )
                draft = DraftRecord(
                    id=draft_id,
                    story_scene_id=scene.id,
                    project_id=scene.project_id,
                    draft_number=drafts.next_draft_number(scene.id),
                    prose_text=request.selected_prose.strip(),
                    word_count=_word_count(request.selected_prose),
                    origin=DraftOrigin.GENERATED.value,
                    draft_status="complete",
                    was_accepted=False,
                    accepted_at="",
                    is_preview=planning_mode == "preview",
                    promoted_from_preview_draft_id=None,
                    is_recovery_rebase=False,
                    scene_card_version_id=request.expected_scene_card_version_id,
                    generation_run_id=run.id,
                    revision_request_json="{}",
                    revision_request_fingerprint=None,
                    parent_draft_id=request.expected_working_draft_id,
                    created_at=now,
                )
                runs.add(run)
                session.flush()
                drafts.add(draft)
                # Import receipts obey the same finalize-once lifecycle as
                # generated runs. Persist the exact draft/block projection
                # before finalization, while keeping every write in this
                # transaction so a failed transition or CAS leaves no orphan.
                session.flush()
                if not runs.finalize(
                    run.id,
                    status=RunStatus.COMPLETED.value,
                    completed_at=now,
                    latency_ms=request.step.latency_ms or 0,
                ):
                    raise ConflictError("Story 候選套用的 run 無法完成；所有寫入均已回滾。")
                session.flush()
                completed_run = runs.get(run.id)
                if completed_run is None or completed_run.status != RunStatus.COMPLETED.value:
                    raise ConflictError("Story 候選套用缺少完成收據；所有寫入均已回滾。")
                if not scenes.advance_working_draft_cas(
                    scene.id,
                    expected_working_draft_id=request.expected_working_draft_id,
                    expected_accepted_draft_id=request.expected_accepted_draft_id,
                    working_draft_id=draft.id,
                    updated_at=now,
                    expected_revision=expected_revision,
                ):
                    raise ConflictError(
                        "Story Scene 在候選套用期間已變更；run 與草稿均已回滾。"
                    )
                if scenes.get_revision(scene.id) != expected_revision + 1:
                    raise ConflictError("Story Scene revision 未原子性保存；所有寫入均已回滾。")
                return ImportedDurableStoryCandidate(
                    run=completed_run, draft=draft, after_snapshot=after_snapshot
                )
        finally:
            session.close()

    @staticmethod
    def _validate_durable_candidate_import(
        request: ImportDurableStoryCandidateRequest,
    ) -> None:
        candidate = request.candidate
        job = request.job
        step = request.step
        if request.action_kind not in {
            CandidateActionKind.APPLY_ALL,
            CandidateActionKind.APPLY_SELECTION,
            CandidateActionKind.SAVE_AS_NEW,
        }:
            raise ValidationFailedError("此 Story 候選操作尚未支援")
        ContentMode(request.content_mode)
        if not request.selected_prose.strip():
            raise ValidationFailedError("Story 候選正文不可留白")
        if (
            candidate.status is not CandidateStatus.PROPOSED
            or job.status is not JobStatus.COMPLETED
            or step.status is not StepStatus.COMPLETED
        ):
            raise ConflictError("Story 候選、job 或 producing step 不是可套用狀態")
        if (
            candidate.job_id != job.id
            or candidate.producing_step_id != step.id
            or step.job_id != job.id
            or candidate.provenance.job_id != job.id
            or candidate.provenance.step_id != step.id
        ):
            raise ConflictError("Story 候選的 job/step provenance 不一致")
        if (
            candidate.target_type != "story_scene"
            or candidate.target_id != request.expected_scene_id
            or job.target_type != "story_scene"
            or job.target_id != request.expected_scene_id
        ):
            raise ConflictError("Story 候選與 durable job 的目標 Scene 不一致")
        if (
            candidate.base_snapshot != job.source_snapshot
            or request.before_snapshot != candidate.base_snapshot
        ):
            raise ConflictError("Story 候選 immutable source snapshot 不一致")
        if (
            step.output_sha256 != candidate.content_sha256
            or candidate.provenance.output_sha256 != candidate.content_sha256
            or request.candidate.provenance.provider != step.provider
            or request.candidate.provenance.model != step.model
        ):
            raise ConflictError("Story 候選 output/provider/model provenance 不一致")
        context_mode = request.job.context_snapshot.get("content_mode", "general")
        if context_mode != request.content_mode.value:
            raise ConflictError("Story 候選 content mode 與 durable context 不一致")
        candidate_mode = (
            None
            if candidate.content_json is None
            else candidate.content_json.get("content_mode")
        )
        if candidate_mode is not None and candidate_mode != request.content_mode.value:
            raise ConflictError("Story 候選 content mode 與 candidate receipt 不一致")
        source = request.before_snapshot
        if (
            source.get("project_id") != request.expected_project_id
            or source.get("story_scene_id") != request.expected_scene_id
            or source.get("scene_card_version_id")
            != request.expected_scene_card_version_id
            or source.get("working_draft_id")
            != request.expected_working_draft_id
            or source.get("accepted_draft_id")
            != request.expected_accepted_draft_id
        ):
            raise ConflictError("Story 候選 target snapshot 欄位不一致")

    @classmethod
    def _durable_candidate_input_snapshot(
        cls,
        request: ImportDurableStoryCandidateRequest,
        *,
        participant_rows: list[ParticipantRecord],
        target_after: dict[str, object] | None = None,
    ) -> _DurableStoryCandidateInputSnapshot:
        candidate = request.candidate
        job = request.job
        step = request.step
        evidence: dict[str, object] = {
            "schema_version": "durable-story-candidate-receipt-v1",
            "target_before": request.before_snapshot,
            "application": {
                "action_kind": request.action_kind.value,
                "idempotency_key": request.idempotency_key,
                "selection": request.selection,
                "selected_prose_sha256": sha256_text(
                    request.selected_prose.strip()
                ),
            },
            "durable_job": {
                "id": job.id,
                "artifact_kind": job.artifact_kind.value,
                "operation": job.operation.value,
                "route": job.route,
                "quality_profile": job.quality_profile,
                "length_spec": job.length_spec,
                "source_snapshot": job.source_snapshot,
                "source_snapshot_sha256": job.source_snapshot_sha256,
                "context_snapshot": job.context_snapshot,
                "context_snapshot_sha256": job.context_snapshot_sha256,
                "workflow_version": job.workflow_version,
                "created_at": job.created_at,
                "completed_at": job.completed_at,
            },
            "producing_step": {
                "id": step.id,
                "job_id": step.job_id,
                "ordinal": step.ordinal,
                "step_kind": step.step_kind.value,
                "template_id": step.template_id,
                "template_version": step.template_version,
                "requested_provider": step.requested_provider,
                "requested_model": step.requested_model,
                "provider": step.provider,
                "model": step.model,
                "fallback_used": step.fallback_used,
                "input_snapshot_sha256": step.input_snapshot_sha256,
                "output_sha256": step.output_sha256,
                "attempts": step.attempts,
                "finish_reason": step.finish_reason,
                "completed_at": step.completed_at,
            },
            "candidate": {
                "id": candidate.id,
                "candidate_version": candidate.candidate_version,
                "content_sha256": candidate.content_sha256,
                "quality_report": candidate.quality_report,
                "provenance": candidate.provenance.model_dump(mode="json"),
            },
        }
        if target_after is not None:
            evidence["target_after"] = target_after
        receipt_json = canonical_json(evidence)
        receipt_sha256 = sha256_text(receipt_json)
        context = job.context_snapshot
        version_ids = cls._story_version_ids(context)
        system_message = cls._required_context_message(context, "system_message")
        user_message = cls._required_context_message(context, "user_message")
        try:
            planning_mode = PlanningMode(
                str(context.get("planning_mode", PlanningMode.ACCEPTED.value))
            )
            budget_snapshot = ContextBudgetSnapshot.model_validate(
                context.get("context_budget_snapshot", {})
            )
        except ValueError as exc:
            raise ValidationFailedError(
                "durable Story planning mode 或 context budget snapshot 無效"
            ) from exc
        source_refs = cls._context_source_refs(context.get("source_refs", []))
        participant_pins = tuple(
            ParticipantPin(
                character_id=row.character_id,
                character_version_id=row.character_version_id,
                role=row.role,
                is_pov=row.is_pov,
            )
            for row in participant_rows
        )
        declared_character_ids = tuple(
            cls._string_list(context.get("character_version_ids"))
        )
        actual_character_ids = tuple(
            pin.character_version_id for pin in participant_pins
        )
        if declared_character_ids != actual_character_ids:
            raise ConflictError(
                "durable Story context 的 Character Version pins 與 Scene Card 不一致"
            )
        declared_pov = cls._optional_text(context.get("pov_character_version_id"))
        actual_pov = next(
            (
                pin.character_version_id
                for pin in participant_pins
                if pin.is_pov
            ),
            None,
        )
        if declared_pov != actual_pov:
            raise ConflictError(
                "durable Story context 的 POV Character Version 與 Scene Card 不一致"
            )
        provider = candidate.provenance.provider
        model = candidate.provenance.model
        assert provider is not None and model is not None
        options = GenerationOptionsSnapshot(
            temperature=0.0,
            timeout_s=0.0,
            stream=False,
            provider_options=tuple(
                ProviderOption(key=key, value=value)
                for key, value in {
                    "durable.action_kind": request.action_kind.value,
                    "durable.candidate_content_sha256": candidate.content_sha256,
                    "durable.candidate_id": candidate.id,
                    "durable.candidate_receipt_sha256": receipt_sha256,
                    "durable.context_snapshot_sha256": job.context_snapshot_sha256,
                    "durable.job_id": job.id,
                    "durable.job_snapshot_sha256": sha256_json(
                        job.model_dump(mode="json")
                    ),
                    "durable.quality_report_sha256": sha256_json(
                        candidate.quality_report
                    ),
                    "durable.route_sha256": sha256_json(job.route),
                    "durable.selected_prose_sha256": sha256_text(
                        request.selected_prose.strip()
                    ),
                    "durable.source_snapshot_sha256": job.source_snapshot_sha256,
                    "durable.step_id": step.id,
                    "durable.step_snapshot_sha256": sha256_json(
                        step.model_dump(mode="json")
                    ),
                    "durable.workflow_version": job.workflow_version,
                }.items()
            ),
        )
        return _DurableStoryCandidateInputSnapshot(
            schema_version="phase6-durable-story-candidate-import-v1",
            project_id=request.expected_project_id,
            story_scene_id=request.expected_scene_id,
            scene_card_version_id=request.expected_scene_card_version_id,
            requirement_version_id=version_ids.get("story_requirement_version"),
            bible_version_id=version_ids.get("story_bible_version"),
            outline_version_id=version_ids.get("story_outline_version"),
            chapter_plan_version_id=version_ids.get("chapter_plan_version"),
            participants=participant_pins,
            pov_character_version_id=actual_pov,
            content_mode=request.content_mode,
            eligibility_evaluation_ids=tuple(
                cls._string_list(context.get("eligibility_evaluation_ids"))
            ),
            eligibility_fingerprint=str(
                context.get("eligibility_fingerprint", "")
            ),
            context_fingerprint=str(
                context.get("context_fingerprint")
                or job.context_snapshot_sha256
            ),
            context_source_refs=source_refs,
            context_schema_version=str(
                context.get("context_schema_version", "durable-workflow-v1")
            ),
            context_contract_version=str(
                context.get("context_contract_version", "durable-workflow-v1")
            ),
            context_budget_policy_version=str(
                context.get("context_budget_policy_version", "")
            ),
            context_budget_snapshot=budget_snapshot,
            memory_projection_fingerprint=str(
                context.get("memory_fingerprint", "")
            ),
            memory_proposal_ids=tuple(
                cls._string_list(context.get("memory_proposal_ids"))
            ),
            memory_entry_ids=tuple(
                cls._string_list(context.get("memory_entry_ids"))
            ),
            memory_gap_scene_ids=tuple(
                cls._string_list(context.get("memory_gap_scene_ids"))
            ),
            stale_memory_proposal_ids=tuple(
                cls._string_list(context.get("stale_memory_proposal_ids"))
            ),
            structured_must_avoid=tuple(
                cls._string_list(context.get("structured_must_avoid"))
            ),
            output_contract_version=f"durable:{job.workflow_version}",
            provider=provider,
            model=model,
            options=options,
            planning_mode=planning_mode,
            planning_chain_fingerprint=str(
                context.get("planning_chain_fingerprint", "")
            ),
            renderer_version=str(
                context.get("context_renderer_version", "durable-workflow")
            ),
            system_message_sha256=sha256_text(system_message),
            user_message_sha256=sha256_text(user_message),
            run_kind="generation",
            parent_draft_id=request.expected_working_draft_id,
            durable_candidate_receipt_json=receipt_json,
            durable_candidate_receipt_sha256=receipt_sha256,
        )

    @staticmethod
    def _assert_durable_import_replay(
        request: ImportDurableStoryCandidateRequest,
        run: GenerationRunRecord,
        draft: DraftRecord,
        *,
        input_sha256: str,
    ) -> None:
        if (
            run.input_snapshot_sha256 != input_sha256
            or run.status != RunStatus.COMPLETED.value
            or draft.generation_run_id != run.id
            or draft.story_scene_id != request.expected_scene_id
            or draft.project_id != request.expected_project_id
            or draft.scene_card_version_id
            != request.expected_scene_card_version_id
            or draft.parent_draft_id != request.expected_working_draft_id
            or draft.prose_text != request.selected_prose.strip()
            or draft.origin != DraftOrigin.GENERATED.value
            or draft.draft_status != "complete"
        ):
            raise ConflictError(
                "相同 Story 候選套用冪等鍵已對應不同 run 或草稿內容"
            )

    @staticmethod
    def _story_version_ids(context: dict[str, object]) -> dict[str, str]:
        raw = context.get("version_references", [])
        if not isinstance(raw, list):
            raise ValidationFailedError("durable Story version references 無效")
        result: dict[str, str] = {}
        aliases = {
            "requirement": "story_requirement_version",
            "story_requirement_version": "story_requirement_version",
            "bible": "story_bible_version",
            "story_bible_version": "story_bible_version",
            "outline": "story_outline_version",
            "story_outline_version": "story_outline_version",
            "chapter_plan": "chapter_plan_version",
            "chapter_plan_version": "chapter_plan_version",
        }
        for item in raw:
            if not isinstance(item, dict):
                raise ValidationFailedError("durable Story version reference 無效")
            entity_type = item.get("entity_type")
            entity_id = item.get("entity_id")
            normalized_type = aliases.get(entity_type) if isinstance(entity_type, str) else None
            if normalized_type is not None and isinstance(entity_id, str) and entity_id:
                prior = result.setdefault(normalized_type, entity_id)
                if prior != entity_id:
                    raise ConflictError("durable Story version reference 重複且不一致")
        return result

    @staticmethod
    def _context_source_refs(value: object) -> tuple[ContextSourceRef, ...]:
        if not isinstance(value, list):
            raise ValidationFailedError("durable Story context source refs 無效")
        try:
            return tuple(ContextSourceRef.model_validate(item) for item in value)
        except ValueError as exc:
            raise ValidationFailedError(
                "durable Story context source refs 無效"
            ) from exc

    @staticmethod
    def _optional_text(value: object) -> str | None:
        return value if isinstance(value, str) and value else None

    @staticmethod
    def _required_context_message(
        context: dict[str, object],
        key: str,
    ) -> str:
        value = context.get(key)
        if not isinstance(value, str):
            raise ValidationFailedError(
                f"durable Story context 缺少 immutable {key}"
            )
        declared_hash = context.get(f"{key}_sha256")
        if declared_hash is not None and declared_hash != sha256_text(value):
            raise ConflictError(f"durable Story context 的 {key} hash 不一致")
        declared_size = context.get(f"{key}_byte_size")
        if declared_size is not None and declared_size != len(value.encode("utf-8")):
            raise ConflictError(f"durable Story context 的 {key} byte size 不一致")
        return value

    @staticmethod
    def _string_list(value: object) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list) or not all(
            isinstance(item, str) and item for item in value
        ):
            raise ValidationFailedError("durable Story character version ids 無效")
        return list(value)

    def generate_scene(
        self,
        request: GenerateSceneRequest,
        *,
        cancel: Event | None = None,
        on_chunk: Callable[[str], None] | None = None,
    ) -> OrchestratedOutcome:
        _validate_purpose_brief(
            request.generation_purpose,
            request.complete_story_brief,
        )
        length_spec = _validated_story_length_spec(request.length_spec)
        if (
            request.planning_mode is PlanningMode.PREVIEW
            and not request.preview_warning_acknowledged
        ):
            raise ValidationFailedError(
                "Preview 模式使用尚未接受的規劃版本，"
                "必須先確認警告（preview_warning_acknowledged）才能生成。"
            )
        resolved, evaluation_ids = self._build_bound_context(
            BuildStoryContextRequest(
                scene_id=request.scene_id,
                generation_purpose=request.generation_purpose,
                complete_story_brief=request.complete_story_brief,
                scene_card_version_id=request.scene_card_version_id,
                requirement_version_id=request.requirement_version_id,
                bible_version_id=request.bible_version_id,
                outline_version_id=request.outline_version_id,
                chapter_plan_version_id=request.chapter_plan_version_id,
                user_instruction=request.user_instruction,
                structured_must_avoid=request.structured_must_avoid,
                style_examples=request.style_examples,
                max_chars=request.max_context_chars,
                budget_snapshot=request.context_budget,
                planning_mode=request.planning_mode,
            )
        )
        if length_spec is None:
            length_spec = _default_story_length_spec(
                purpose=request.generation_purpose,
                complete_story_brief=request.complete_story_brief,
                scene_target_hint=resolved.scene_card.target_word_count,
            )
        options = _options_with_story_length_cap(self._options(request), length_spec)
        snapshot = _snapshot_from(
            resolved,
            provider=self._provider_name,
            model=request.model,
            options=options,
            eligibility_fingerprint=resolved.package.eligibility_fingerprint,
            evaluation_ids=evaluation_ids,
            run_kind="generation",
            complete_story_brief=request.complete_story_brief,
            length_spec=length_spec,
        )
        return self._execute(
            snapshot=snapshot,
            resolved=resolved,
            origin=DraftOrigin.GENERATED,
            system=resolved.package.system_message,
            prompt=resolved.package.user_message,
            options=options,
            length_spec=length_spec,
            cancel=cancel,
            on_chunk=on_chunk,
            preview_ack=request.preview_warning_acknowledged,
        )


class StoryRevisionOrchestrationService(_OrchestrationBase):
    """Revision and recovery rebase ."""

    def revise_scene(
        self,
        request: ReviseSceneRequest,
        *,
        cancel: Event | None = None,
        on_chunk: Callable[[str], None] | None = None,
    ) -> OrchestratedOutcome:
        """An ordinary revision replays the source run's exact planning chain.

        Fails closed when that chain cannot be reconstructed. Substituting the
        latest accepted Requirement/Bible/Outline/Plan/Card would silently
        change the meaning and provenance of the revision.
        """
        length_spec = _validated_story_length_spec(request.length_spec)
        source, source_run = self._load_source(request.draft_id)
        if source_run is None:
            raise SourcePlanningChainUnavailableError(
                missing_version_ids=("（來源草稿沒有對應的生成紀錄）",)
            )
        source_snapshot = self._verified_source_snapshot(source_run, source)
        if length_spec is None:
            length_spec = source_snapshot.length_spec
        missing = self._missing_versions(source_run, source.scene_card_version_id)
        if missing:
            raise SourcePlanningChainUnavailableError(missing_version_ids=missing)

        # Replay the source run's own mode as well as its exact IDs. A preview
        # source may legitimately pin unaccepted versions and must stay
        # preview; an accepted source only pins versions that were accepted
        # and its revision must remain eligible for author acceptance even if
        # newer accepted pointers exist today.
        source_planning_mode = PlanningMode(source_run.planning_mode)
        previous_summary_source_refs = _ordinary_revision_summary_refs(
            source_snapshot
        )
        resolved, evaluation_ids = self._build_bound_context(
            BuildStoryContextRequest(
                scene_id=source.story_scene_id,
                generation_purpose=source_snapshot.generation_purpose,
                complete_story_brief=source_snapshot.complete_story_brief,
                scene_card_version_id=source.scene_card_version_id,
                requirement_version_id=source_run.requirement_version_id,
                bible_version_id=source_run.bible_version_id,
                outline_version_id=source_run.outline_version_id,
                chapter_plan_version_id=source_run.chapter_plan_version_id,
                user_instruction=request.user_instruction,
                structured_must_avoid=request.structured_must_avoid,
                max_chars=request.max_context_chars,
                budget_snapshot=request.context_budget,
                planning_mode=source_planning_mode,
                memory_proposal_ids=source_snapshot.memory_proposal_ids,
                memory_gap_scene_ids=source_snapshot.memory_gap_scene_ids,
                stale_memory_proposal_ids=(source_snapshot.stale_memory_proposal_ids),
                expected_memory_fingerprint=(source_snapshot.memory_projection_fingerprint),
                previous_summary_source_refs=previous_summary_source_refs,
            ),
        )
        if length_spec is None:
            length_spec = _default_story_length_spec(
                purpose=source_snapshot.generation_purpose,
                complete_story_brief=source_snapshot.complete_story_brief,
                scene_target_hint=resolved.scene_card.target_word_count,
            )
        options = _options_with_story_length_cap(self._options(request), length_spec)
        return self._run_revision(
            source=source,
            resolved=resolved,
            evaluation_ids=evaluation_ids,
            options=options,
            model=request.model,
            revision=request.revision,
            generation_purpose=source_snapshot.generation_purpose,
            complete_story_brief=source_snapshot.complete_story_brief,
            length_spec=length_spec,
            cancel=cancel,
            on_chunk=on_chunk,
        )

    def recover_by_rebase(
        self,
        request: RecoveryRebaseRequest,
        *,
        cancel: Event | None = None,
        on_chunk: Callable[[str], None] | None = None,
    ) -> OrchestratedOutcome:
        """the audited escape when history is genuinely damaged.

        Produces a clearly-labelled Recovery Rebase draft under a NEW accepted
        chain. The original draft and run are never modified.
        """
        length_spec = _validated_story_length_spec(request.length_spec)
        if not request.rebase_reason.strip():
            raise ValidationFailedError(
                "復原 rebase 會改用新的規劃鏈，必須提供書面理由以留下稽核紀錄。"
            )
        source, source_run = self._load_source(request.draft_id)
        source_snapshot: GenerationInputSnapshot | None = None
        if source_run is not None:
            try:
                source_snapshot = self._verified_source_snapshot(source_run, source)
            except SourcePlanningChainUnavailableError:
                if request.generation_purpose is None:
                    raise ValidationFailedError(
                        "來源快照無法驗證，Recovery Rebase 必須明確指定生成用途；"
                        "系統不會猜測是場景或完整短篇。"
                    ) from None

        if (
            source_snapshot is not None
            and source_snapshot.generation_purpose
            is StoryGenerationPurpose.COMPLETE_SHORT_STORY
            and request.generation_purpose is None
        ):
            raise ValidationFailedError(
                "完整短篇 Recovery Rebase 必須明確帶入生成用途與完整故事 brief。"
            )
        generation_purpose = (
            request.generation_purpose or StoryGenerationPurpose.SCENE
        )
        if (
            source_snapshot is not None
            and generation_purpose is not source_snapshot.generation_purpose
        ):
            raise ValidationFailedError(
                "Recovery Rebase 不可把來源草稿由場景改成完整短篇，或反向改變生成用途。"
            )
        _validate_purpose_brief(generation_purpose, request.complete_story_brief)
        if length_spec is None and source_snapshot is not None:
            length_spec = source_snapshot.length_spec
        missing = (
            self._missing_versions(source_run, source.scene_card_version_id)
            if source_run is not None
            else ("（來源草稿沒有對應的生成紀錄）",)
        )
        resolved, evaluation_ids = self._build_bound_context(
            BuildStoryContextRequest(
                scene_id=source.story_scene_id,
                generation_purpose=generation_purpose,
                complete_story_brief=request.complete_story_brief,
                scene_card_version_id=request.scene_card_version_id,
                requirement_version_id=request.requirement_version_id,
                bible_version_id=request.bible_version_id,
                outline_version_id=request.outline_version_id,
                chapter_plan_version_id=request.chapter_plan_version_id,
                user_instruction=request.user_instruction,
                structured_must_avoid=request.structured_must_avoid,
                max_chars=request.max_context_chars,
                budget_snapshot=request.context_budget,
                planning_mode=PlanningMode.ACCEPTED,
            ),
        )
        if length_spec is None:
            length_spec = _default_story_length_spec(
                purpose=generation_purpose,
                complete_story_brief=request.complete_story_brief,
                scene_target_hint=resolved.scene_card.target_word_count,
            )
        options = _options_with_story_length_cap(self._options(request), length_spec)
        return self._run_revision(
            source=source,
            resolved=resolved,
            evaluation_ids=evaluation_ids,
            options=options,
            model=request.model,
            revision=request.revision,
            generation_purpose=generation_purpose,
            complete_story_brief=request.complete_story_brief,
            length_spec=length_spec,
            cancel=cancel,
            on_chunk=on_chunk,
            rebased_from_run_id=(source_run.id if source_run is not None else None),
            rebase_reason=request.rebase_reason.strip(),
            missing_source_version_ids=missing,
            is_recovery=True,
        )

    # ------------------------------------------------------------ helpers
    def _load_source(self, draft_id: str) -> tuple[DraftRecord, GenerationRunRecord | None]:
        with self._session_factory() as session:
            drafts = SceneDraftRepository(session)
            runs = GenerationRunRepository(session)
            source = drafts.get(draft_id)
            if source is None:
                raise NotFoundError(f"找不到草稿：{draft_id}")
            if source.draft_status == "partial":
                raise ValidationFailedError("不可修訂未完成（partial）的草稿")

            current = source
            visited: set[str] = set()
            while True:
                if current.id in visited:
                    raise SourcePlanningChainUnavailableError(
                        missing_version_ids=("draft_lineage（偵測到循環）",)
                    )
                visited.add(current.id)
                if (
                    current.story_scene_id != source.story_scene_id
                    or current.project_id != source.project_id
                ):
                    raise SourcePlanningChainUnavailableError(
                        missing_version_ids=(
                            "draft_lineage（來源鏈跨越場景或專案）",
                        )
                    )
                if current.scene_card_version_id != source.scene_card_version_id:
                    raise SourcePlanningChainUnavailableError(
                        missing_version_ids=(
                            "draft_lineage（來源鏈切換 Scene Card 版本）",
                        )
                    )
                if current.draft_status == "partial":
                    raise SourcePlanningChainUnavailableError(
                        missing_version_ids=(
                            "draft_lineage（來源鏈包含 partial 草稿）",
                        )
                    )
                if current.generation_run_id:
                    run = runs.get(current.generation_run_id)
                    if run is None:
                        raise SourcePlanningChainUnavailableError(
                            missing_version_ids=(
                                f"generation_run={current.generation_run_id}",
                            )
                        )
                    if (
                        run.story_scene_id != current.story_scene_id
                        or run.project_id != current.project_id
                        or run.scene_card_version_id
                        != current.scene_card_version_id
                    ):
                        raise SourcePlanningChainUnavailableError(
                            missing_version_ids=(
                                "draft_lineage（generation run ownership 不一致）",
                            )
                        )
                    return source, run
                if current.parent_draft_id is None:
                    return source, None
                parent = drafts.get(current.parent_draft_id)
                if parent is None:
                    raise SourcePlanningChainUnavailableError(
                        missing_version_ids=(
                            f"parent_draft={current.parent_draft_id}",
                        )
                    )
                current = parent

    @staticmethod
    def _verified_source_snapshot(
        run: GenerationRunRecord,
        source: DraftRecord,
    ) -> GenerationInputSnapshot:
        """Load the immutable source context, including its exact memory state."""

        digest = hashlib.sha256(run.input_snapshot_json.encode("utf-8")).hexdigest()
        if digest != run.input_snapshot_sha256:
            raise SourcePlanningChainUnavailableError(
                missing_version_ids=("input_snapshot（SHA-256 完整性驗證失敗）",)
            )
        try:
            snapshot = GenerationInputSnapshot.model_validate_json(run.input_snapshot_json)
        except ValueError as exc:
            raise SourcePlanningChainUnavailableError(
                missing_version_ids=("input_snapshot（內容無法解析）",)
            ) from exc
        mismatched = [
            name
            for name, snapshot_value, stored_value in (
                ("project_id", snapshot.project_id, run.project_id),
                ("source_project_id", snapshot.project_id, source.project_id),
                ("story_scene_id", snapshot.story_scene_id, run.story_scene_id),
                (
                    "source_story_scene_id",
                    snapshot.story_scene_id,
                    source.story_scene_id,
                ),
                (
                    "scene_card_version_id",
                    snapshot.scene_card_version_id,
                    source.scene_card_version_id,
                ),
                (
                    "requirement_version_id",
                    snapshot.requirement_version_id,
                    run.requirement_version_id,
                ),
                (
                    "bible_version_id",
                    snapshot.bible_version_id,
                    run.bible_version_id,
                ),
                (
                    "outline_version_id",
                    snapshot.outline_version_id,
                    run.outline_version_id,
                ),
                (
                    "chapter_plan_version_id",
                    snapshot.chapter_plan_version_id,
                    run.chapter_plan_version_id,
                ),
            )
            if snapshot_value != stored_value
        ]
        if mismatched:
            raise SourcePlanningChainUnavailableError(
                missing_version_ids=(
                    "input_snapshot（與來源紀錄不一致：" + "、".join(mismatched) + "）",
                )
            )
        return snapshot

    def _missing_versions(self, run: GenerationRunRecord, card_version_id: str) -> tuple[str, ...]:
        """Which of the source run's planning versions can no longer be read.

        Archived-but-readable versions are fine; only genuinely unresolvable
        ones block an ordinary revision.
        """
        checks: list[tuple[str, str | None]] = [
            ("scene_card", card_version_id),
            ("requirement", run.requirement_version_id),
            ("bible", run.bible_version_id),
            ("outline", run.outline_version_id),
            ("chapter_plan", run.chapter_plan_version_id),
        ]
        missing: list[str] = []
        with self._session_factory() as session:
            cards = SceneCardVersionRepository(session)
            for kind, version_id in checks:
                if not version_id:
                    continue
                found = (
                    cards.get(version_id)
                    if kind == "scene_card"
                    else VersionedEntityRepository(session, kind).get_version(version_id)
                )
                if found is None:
                    missing.append(f"{kind}={version_id}")
        return tuple(missing)

    def _run_revision(
        self,
        *,
        source: DraftRecord,
        resolved: ResolvedStoryContext,
        evaluation_ids: tuple[str, ...],
        options: GenerationOptionsSnapshot,
        model: str,
        revision: RevisionRequest,
        generation_purpose: StoryGenerationPurpose,
        complete_story_brief: CompleteStoryBrief | None,
        length_spec: GenerationLengthSpec | None,
        cancel: Event | None,
        on_chunk: Callable[[str], None] | None,
        rebased_from_run_id: str | None = None,
        rebase_reason: str = "",
        missing_source_version_ids: tuple[str, ...] = (),
        is_recovery: bool = False,
    ) -> OrchestratedOutcome:
        _validate_purpose_brief(generation_purpose, complete_story_brief)
        if resolved.package.generation_purpose is not generation_purpose:
            raise ValidationFailedError(
                "修訂用途與已解析的故事上下文用途不一致"
            )
        revision_fingerprint = _revision_fingerprint(revision)
        revision_json = canonical_json(revision.model_dump(mode="json"))
        snapshot = _snapshot_from(
            resolved,
            provider=self._provider_name,
            model=model,
            options=options,
            eligibility_fingerprint=resolved.package.eligibility_fingerprint,
            evaluation_ids=evaluation_ids,
            run_kind="revision",
            parent_draft_id=source.id,
            revision_request_json=revision_json,
            revision_request_fingerprint=revision_fingerprint,
            rebased_from_generation_run_id=rebased_from_run_id,
            is_recovery_rebase=is_recovery,
            missing_source_version_ids=missing_source_version_ids,
            complete_story_brief=complete_story_brief,
            length_spec=length_spec,
        )
        prompt = (
            f"{resolved.package.user_message}\n\n## 目前草稿（要修訂的對象）\n{source.prose_text}"
        )
        return self._execute(
            snapshot=snapshot,
            resolved=resolved,
            origin=DraftOrigin.REVISED,
            system=build_revision_contract(
                revision,
                purpose=generation_purpose,
            ),
            prompt=prompt,
            options=options,
            length_spec=length_spec,
            cancel=cancel,
            on_chunk=on_chunk,
            parent_draft_id=source.id,
            revision_json=revision_json,
            revision_fingerprint=revision_fingerprint,
            rebase_reason=rebase_reason,
        )


def _validated_story_length_spec(
    spec: GenerationLengthSpec | None,
) -> GenerationLengthSpec | None:
    """Require an enforceable prose minimum when a caller opts into N3."""

    if spec is None:
        return None
    if not isinstance(spec, GenerationLengthSpec):
        raise ValidationFailedError("Story Studio 長度合約必須是 GenerationLengthSpec。")
    if spec.minimum is None or spec.strictness is LengthStrictness.ADVISORY:
        raise ValidationFailedError(
            "Story Studio 長度合約必須提供強制最低值；advisory 不可宣告正文完成。"
        )
    if spec.unit is LengthUnit.SCREENPLAY_PAGES:
        raise ValidationFailedError("劇本頁數合約只可由劇本改編流程使用。")
    return spec


def _default_story_length_spec(
    *,
    purpose: StoryGenerationPurpose,
    complete_story_brief: CompleteStoryBrief | None,
    scene_target_hint: int = 0,
) -> GenerationLengthSpec:
    """Resolve the R2 purpose preset when a legacy caller omits the contract.

    ``None`` remains accepted at the dataclass edge for source compatibility,
    but it never reaches a provider call or persisted run. These are the R2
     prose presets, not token limits: Scene target 1,800 and short-story
    target 5,000, with the same visible-character bounds presented by the UI.
    """

    if purpose is StoryGenerationPurpose.COMPLETE_SHORT_STORY:
        if complete_story_brief is None:
            raise ValidationFailedError("完整短篇必須提供 typed brief。")
        return GenerationLengthSpec(
            unit=LengthUnit.VISIBLE_CHARS,
            minimum=complete_story_brief.min_visible_chars,
            target=complete_story_brief.max_visible_chars,
            maximum=complete_story_brief.max_visible_chars,
            segment_target=None,
            max_continuations=3,
            strictness=LengthStrictness.BOUNDED,
        )

    target = scene_target_hint if scene_target_hint > 0 else 1_800
    return GenerationLengthSpec(
        unit=LengthUnit.VISIBLE_CHARS,
        minimum=max(100, round(target * 0.75)),
        target=target,
        maximum=max(target, round(target * 1.25)),
        segment_target=None,
        max_continuations=1,
        strictness=LengthStrictness.BOUNDED,
    )


def _options_with_story_length_cap(
    options: GenerationOptionsSnapshot,
    spec: GenerationLengthSpec | None,
) -> GenerationOptionsSnapshot:
    """Persist an explicit per-call cap whenever length assurance is active."""

    if spec is None or options.max_output_tokens is not None:
        return options
    return options.model_copy(update={"max_output_tokens": _story_length_token_cap(spec)})


def _story_length_token_cap(spec: GenerationLengthSpec) -> int:
    requested_unit = spec.segment_target or spec.target
    # A conservative provider-neutral ceiling: English prose can use more
    # than one token per word, while CJK visible characters are commonly near
    # one token each. The exact derived value is stored in the run snapshot;
    # it is a cost/output ceiling, never evidence that the target was met.
    token_multiplier = 2
    return max(256, requested_unit * token_multiplier)


def interrupted_story_length_assurance_json(input_snapshot_json: str) -> str:
    """Build truthful zero-attempt evidence for a stale pre-finalization run."""

    try:
        snapshot = json.loads(input_snapshot_json)
        raw_spec = snapshot.get("length_spec")
        if raw_spec is None:
            return "{}"
        spec = GenerationLengthSpec.model_validate(raw_spec)
        if snapshot.get("length_spec_sha256") != spec.sha256:
            raise ValueError("length spec hash mismatch")
        raw_options = snapshot.get("options")
        saved_cap = (
            raw_options.get("max_output_tokens")
            if isinstance(raw_options, dict)
            else None
        )
        output_cap = (
            saved_cap
            if isinstance(saved_cap, int) and not isinstance(saved_cap, bool) and saved_cap > 0
            else _story_length_token_cap(spec)
        )
    except (AttributeError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValidationFailedError(
            "無法為中斷的 Story generation run 重建長度合約證據。"
        ) from exc

    max_total_attempts = 1 + spec.max_continuations + 2
    max_runtime_attempts = (
        max_total_attempts * _STORY_PROVIDER_RUNTIME_ATTEMPT_CEILING
    )
    limits = AssuranceLimits(
        max_total_attempts=max_total_attempts,
        max_repetition_retries=2,
        max_estimated_cost_microunits=output_cap * max_runtime_attempts,
    )
    progress = AssuranceProgress(
        provider_attempts=0,
        runtime_provider_attempts=0,
        estimated_cost_microunits=0,
        next_attempt_estimated_cost_microunits=(
            output_cap * _STORY_PROVIDER_RUNTIME_ATTEMPT_CEILING
        ),
    )
    decision = LengthAssuranceService().evaluate(
        text="",
        spec=spec,
        provider_termination=ProviderTermination.ERROR,
        provider_finish_reason=ReasonCode.PROCESS_INTERRUPTED,
        provider_error_recoverable=False,
        progress=progress,
        limits=limits,
    )
    trace = _LengthAssuranceTrace(
        length_spec=spec,
        decision=decision,
        progress=progress,
        limits=limits,
        runtime_provider_attempt_ceiling_per_logical_call=(
            _STORY_PROVIDER_RUNTIME_ATTEMPT_CEILING
        ),
        runtime_provider_attempt_ceiling=max_runtime_attempts,
        provider_calls=(),
        selected_candidate="none",
        preserved_overlength_text_sha256s=(),
        output_text_sha256=hashlib.sha256(b"").hexdigest(),
    )
    return trace.canonical_json(partial_disposition="no_candidate")


def _termination_from_provider_metadata(
    *,
    partial: bool,
    incomplete_reason: str | None,
    finish_reason: str | None,
) -> ProviderTermination:
    reason = (incomplete_reason or finish_reason or "").strip().casefold()
    if reason in {"timeout", "timed_out"}:
        return ProviderTermination.TIMEOUT
    if reason in {"cancelled", "canceled", "cancelled_by_user", "canceled_by_user"}:
        return ProviderTermination.CANCELLED
    if reason in {"length", "max_output", "max_output_tokens", "max_tokens"}:
        return ProviderTermination.TOKEN_LIMIT
    if partial:
        return ProviderTermination.ERROR
    return ProviderTermination.NORMAL_STOP


def _safe_provider_finish_reason(value: str, *, fallback: str) -> str:
    normalized = value.strip().casefold()
    return (
        normalized
        if _SAFE_PROVIDER_FINISH_REASON.fullmatch(normalized) is not None
        else fallback
    )


def _refused_provider_call(runtime_attempts: int) -> _ProviderCallOutcome:
    """A terminal safety failure is never a recoverable transport partial."""
    return _ProviderCallOutcome(
        status=RunStatus.FAILED,
        text="",
        reason_code=ReasonCode.PROVIDER_REFUSED,
        error_reason="提供者拒絕回應；未建立草稿或 partial。",
        termination=ProviderTermination.ERROR,
        finish_reason="content_filter",
        runtime_attempts_used=min(
            max(1, runtime_attempts), _STORY_PROVIDER_RUNTIME_ATTEMPT_CEILING
        ),
        reported_runtime_attempts=(
            runtime_attempts
            if runtime_attempts > _STORY_PROVIDER_RUNTIME_ATTEMPT_CEILING
            else None
        ),
        runtime_attempt_ceiling_breached=(
            runtime_attempts > _STORY_PROVIDER_RUNTIME_ATTEMPT_CEILING
        ),
    )


def _length_assurance_disposition(
    *,
    status: RunStatus,
    reason_code: str,
    text: str,
    preserved_candidate: bool = False,
) -> str:
    """Describe where the assessed prose went without copying it into evidence."""

    if reason_code == ReasonCode.PROVIDER_REFUSED:
        return "no_candidate"
    if status is RunStatus.COMPLETED and preserved_candidate:
        return "condensed_candidate_with_preserved_original"
    if status is RunStatus.COMPLETED:
        return "complete_unaccepted_candidate"
    if text.strip() and reason_code in _PARTIAL_DRAFT_REASON_CODES:
        return "saved_unacceptable_partial"
    return "no_candidate"


def _refused_assurance_trace(
    trace: _LengthAssuranceTrace,
) -> _LengthAssuranceTrace:
    """Record a provider refusal as terminal, retaining measurement metadata."""

    decision = trace.decision.model_copy(
        update={
            "status": AssuranceStatus.FAILED_TERMINAL,
            "action": AssuranceAction.FAIL,
            "reason": AssuranceReason.PROVIDER_REFUSED,
            "domain_complete": False,
            "preserve_partial": False,
        }
    )
    return replace(trace, decision=decision)


def _append_partial(existing_text: str, received_text: str) -> str:
    if not existing_text:
        return received_text
    if not received_text:
        return existing_text
    separator = "" if existing_text[-1].isspace() or received_text[0].isspace() else "\n\n"
    return f"{existing_text}{separator}{received_text}"




def _story_continuation_prompt(
    *,
    base_prompt: str,
    plan: ContinuationPlan,
    repetition_retry_number: int,
) -> str:
    facts = (
        "\n".join(f"- {fact}" for fact in plan.protected_facts)
        if plan.protected_facts
        else "（無額外逐字保護事項）"
    )
    retry = (
        "\n先前的續寫因重複而被拒絕；請提供實質不同的新續文。"
        if repetition_retry_number
        else ""
    )
    return (
        f"{base_prompt}\n\n"
        "## 已保存正文尾端（只可接續，不可重寫）\n"
        f"{plan.tail_excerpt}\n\n"
        "## 必須保留的作者事實\n"
        f"{facts}\n\n"
        "## 本次續寫指令\n"
        f"{plan.instruction}{retry}"
    )


def _story_condensation_prompt(
    *,
    base_prompt: str,
    overlength_text: str,
    length_spec: GenerationLengthSpec,
    protected_facts: tuple[str, ...],
) -> str:
    """Request a separate bounded candidate; never truncate the original."""

    facts = (
        "\n".join(f"- {fact}" for fact in protected_facts)
        if protected_facts
        else "（無額外逐字保護事項）"
    )
    maximum = length_spec.maximum or length_spec.target
    return (
        f"{base_prompt}\n\n"
        "## 超出上限的原候選（已另行保留，不可覆寫）\n"
        f"{overlength_text}\n\n"
        "## 必須保留的作者事實\n"
        f"{facts}\n\n"
        "## 建立獨立收斂版候選\n"
        f"請保留情節、人物、時序與作者事實，將正文收斂至最多 {maximum:g} "
        f"{length_spec.unit.value}。只輸出新的完整正文；不可截斷句子，"
        "不可宣稱已改寫或覆蓋原候選。"
    )


def _validate_purpose_brief(
    purpose: StoryGenerationPurpose,
    brief: CompleteStoryBrief | None,
) -> None:
    if purpose is StoryGenerationPurpose.SCENE:
        if brief is not None:
            raise ValidationFailedError("單一場景生成不可帶入完整故事 brief")
        return
    if purpose is StoryGenerationPurpose.COMPLETE_SHORT_STORY:
        if brief is None:
            raise ValidationFailedError("完整短篇生成必須帶入完整故事 brief")
        return
    raise ValidationFailedError(f"不支援的故事生成用途：{purpose}")


def _ordinary_revision_summary_refs(
    snapshot: GenerationInputSnapshot,
) -> tuple[ContextSourceRef, ...] | None:
    """Return immutable previous-summary pins for an ordinary revision.

    Typed-source snapshots prove absence as well as presence, so an empty
    tuple is meaningful. Historical v8/v9 summary refs did not pin the
    mutable summary row body. Replaying those refs after an upsert would
    silently substitute today's text; ordinary revision must instead direct
    the author to the explicit Recovery Rebase workflow.
    """

    if not snapshot.context_source_refs:
        match = re.search(r"-v(\d+)$", snapshot.schema_version)
        if match is not None and int(match.group(1)) >= 3:
            return ()
        raise SourcePlanningChainUnavailableError(
            missing_version_ids=(
                "context_source_refs（legacy snapshot 無法證明當時是否存在前情摘要）",
            )
        )
    refs = tuple(
        ref
        for ref in snapshot.context_source_refs
        if ref.entity_type
        in {
            SourceEntityType.SCENE_DRAFT_SUMMARY,
            SourceEntityType.SCENE_DRAFT,
        }
    )
    if not refs:
        return ()
    summary_refs = tuple(
        ref
        for ref in refs
        if ref.entity_type is SourceEntityType.SCENE_DRAFT_SUMMARY
    )
    draft_refs = tuple(
        ref for ref in refs if ref.entity_type is SourceEntityType.SCENE_DRAFT
    )
    digest = summary_refs[0].version_id if len(summary_refs) == 1 else None
    if (
        len(refs) != 2
        or len(summary_refs) != 1
        or len(draft_refs) != 1
        or digest is None
        or re.fullmatch(r"sha256:[0-9a-f]{64}", digest) is None
    ):
        raise SourcePlanningChainUnavailableError(
            missing_version_ids=(
                "previous_summary（缺少可驗證的 SHA-256 內容 pin）",
            )
        )
    return refs


def _revision_fingerprint(revision: RevisionRequest) -> str:
    import hashlib

    from imaginarium_forge.canonical import canonical_json

    payload = canonical_json(revision.model_dump(mode="json"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _snapshot_from(
    resolved: ResolvedStoryContext,
    *,
    provider: str,
    model: str,
    options: GenerationOptionsSnapshot,
    eligibility_fingerprint: str,
    evaluation_ids: tuple[str, ...],
    run_kind: str,
    parent_draft_id: str | None = None,
    revision_request_json: str = "{}",
    revision_request_fingerprint: str = "",
    rebased_from_generation_run_id: str | None = None,
    is_recovery_rebase: bool = False,
    missing_source_version_ids: tuple[str, ...] = (),
    complete_story_brief: CompleteStoryBrief | None = None,
    length_spec: GenerationLengthSpec | None = None,
) -> GenerationInputSnapshot:
    package = resolved.package
    return GenerationInputSnapshot(
        generation_purpose=package.generation_purpose,
        complete_story_brief=complete_story_brief,
        structured_must_avoid=package.structured_must_avoid,
        length_spec=length_spec,
        length_spec_sha256="" if length_spec is None else length_spec.sha256,
        complete_story_brief_sha256=(
            "" if complete_story_brief is None else complete_story_brief.sha256
        ),
        project_id=resolved.project_id,
        story_scene_id=resolved.scene_id,
        scene_card_version_id=resolved.scene_card_version_id,
        requirement_version_id=resolved.requirement_version_id,
        bible_version_id=resolved.bible_version_id,
        outline_version_id=resolved.outline_version_id,
        chapter_plan_version_id=resolved.chapter_plan_version_id,
        participants=tuple(
            ParticipantPin(
                character_id=p.character_id,
                character_version_id=p.character_version_id,
                role=p.role,
                is_pov=p.is_pov,
            )
            for p in resolved.participants
        ),
        pov_character_version_id=resolved.pov_character_version_id,
        content_mode=resolved.scene_card.content_mode,
        eligibility_evaluation_ids=evaluation_ids,
        eligibility_fingerprint=eligibility_fingerprint,
        context_fingerprint=package.fingerprint,
        # the typed sources travel inside the immutable
        # snapshot, so the run's own SHA-256 already covers provenance.
        context_source_refs=package.source_refs,
        context_schema_version=package.schema_version,
        context_contract_version=package.contract_version,
        context_budget_policy_version=package.budget_policy_version,
        context_budget_snapshot=package.budget_snapshot,
        memory_projection_fingerprint=package.memory_fingerprint,
        memory_proposal_ids=package.memory_proposal_ids,
        memory_entry_ids=package.memory_entry_ids,
        memory_gap_scene_ids=package.memory_gap_scene_ids,
        stale_memory_proposal_ids=package.stale_memory_proposal_ids,
        provider=provider,
        model=model,
        options=options,
        renderer_version=package.renderer_version,
        # NOTE: filled in by ``_execute`` from the messages actually sent. A
        # revision replaces the system message with the revision contract and
        # appends the source prose to the user message, so hashing the context
        # package's own defaults here would record something the provider
        # never saw — exactly the mismatch exists to prevent.
        system_message_sha256="",
        user_message_sha256="",
        planning_mode=package.planning_mode,  # type: ignore[arg-type]
        planning_chain_fingerprint=package.planning_chain_fingerprint,
        run_kind=run_kind,
        parent_draft_id=parent_draft_id,
        revision_request_json=revision_request_json,
        revision_request_fingerprint=revision_request_fingerprint,
        rebased_from_generation_run_id=rebased_from_generation_run_id,
        is_recovery_rebase=is_recovery_rebase,
        missing_source_version_ids=missing_source_version_ids,
    )


def _run_record(
    run_id: str,
    snapshot: GenerationInputSnapshot,
    resolved: ResolvedStoryContext,
    started_at: str,
) -> GenerationRunRecord:
    return GenerationRunRecord(
        id=run_id,
        story_scene_id=resolved.scene_id,
        project_id=resolved.project_id,
        scene_card_version_id=resolved.scene_card_version_id,
        started_at=started_at,
        run_kind=snapshot.run_kind,
        provider=snapshot.provider,
        model=snapshot.model,
        options_snapshot_json=snapshot.options.model_dump_json(),
        options_snapshot_sha256=snapshot.options.sha256,
        renderer_version=snapshot.renderer_version,
        planning_mode=snapshot.planning_mode.value,
        planning_chain_fingerprint=snapshot.planning_chain_fingerprint,
        system_message_sha256=snapshot.system_message_sha256,
        user_message_sha256=snapshot.user_message_sha256,
        context_fingerprint=snapshot.context_fingerprint,
        context_schema_version=snapshot.context_schema_version,
        context_contract_version=snapshot.context_contract_version,
        context_budget_policy_version=snapshot.context_budget_policy_version,
        eligibility_fingerprint=snapshot.eligibility_fingerprint,
        eligibility_evaluation_ids_json=json.dumps(
            list(snapshot.eligibility_evaluation_ids), ensure_ascii=False
        ),
        input_snapshot_json=snapshot.canonical_payload(),
        input_snapshot_sha256=snapshot.sha256,
        requirement_version_id=snapshot.requirement_version_id,
        bible_version_id=snapshot.bible_version_id,
        outline_version_id=snapshot.outline_version_id,
        chapter_plan_version_id=snapshot.chapter_plan_version_id,
        pov_character_version_id=snapshot.pov_character_version_id,
        character_version_ids_json=json.dumps(
            list(snapshot.character_version_ids), ensure_ascii=False
        ),
        content_mode=snapshot.content_mode.value,
        status=RunStatus.RUNNING.value,
        rebased_from_generation_run_id=snapshot.rebased_from_generation_run_id,
    )
