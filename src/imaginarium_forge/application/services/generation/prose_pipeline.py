"""Provider-neutral orchestration for bounded prose generation.

The pipeline owns application semantics: measurement, continuation planning,
repetition rejection, bounded retry/cost accounting, and separate condensation
candidates. A provider callback owns only one call and returns a typed
termination/result. Raw prompts and prose are excluded from every normal
Pydantic serialization; durable provenance contains hashes and safe metadata.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from enum import StrEnum
from typing import Literal, Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from imaginarium_forge.application.services.generation.continuation_planner import (
    ContinuationPlan,
    ContinuationPlanner,
    _length_progress_instruction,
)
from imaginarium_forge.application.services.generation.length_assurance import (
    DEFAULT_ASSURANCE_LIMITS,
    AssuranceAction,
    AssuranceLimits,
    AssuranceProgress,
    LengthAssuranceDecision,
    LengthAssuranceService,
    ProviderTermination,
)
from imaginarium_forge.application.services.generation.output_measurement import (
    OutputLengthAssessment,
    OutputMeasurement,
    measure_output,
)
from imaginarium_forge.application.services.generation.protected_facts_guard import (
    ProtectedFactsGuard,
    ProtectedFactsReport,
)
from imaginarium_forge.application.services.generation.repetition_guard import (
    ContinuationRepetitionGuard,
    RepetitionGuardReport,
)
from imaginarium_forge.canonical import canonical_json
from imaginarium_forge.domain.generation.length import GenerationLengthSpec, LengthUnit

PROSE_PIPELINE_POLICY_VERSION: Literal["prose-generation-pipeline-v1"] = (
    "prose-generation-pipeline-v1"
)
_SAFE_OPERATION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_SAFE_METADATA = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,255}$")
_WHITESPACE = re.compile(r"\s+")
_API_KEY_PATTERN = re.compile(
    r"(?:^|[^a-z0-9])(?:"
    r"api[-_]?key|gh[pousr]_|github_pat_|sk-[a-z0-9]|xox[baprs]-|aiza[a-z0-9_-]{10}"
    r")",
    re.IGNORECASE,
)
_SAFE_FINISH_REASON_CODES = frozenset(
    {
        "attempts_exhausted_partial",
        "canceled",
        "cancelled",
        "completed",
        "content_filter",
        "deterministic_assembly",
        "error",
        "interrupted",
        "length",
        "max_output_tokens",
        "max_tokens",
        "needs_author_input",
        "normal_stop",
        "offline_fixture",
        "partial_needs_action",
        "paused",
        "provider_partial",
        "repetition_rejected",
        "stop",
        "timeout",
        "token_limit",
        "tool_calls",
    }
)
_SHA256_PATTERN = r"^[0-9a-f]{64}$"


class ProseCallKind(StrEnum):
    """One provider-neutral call role in a prose operation."""

    INITIAL = "initial"
    CONTINUATION = "continuation"
    REPETITION_RETRY = "repetition_retry"
    CONDENSATION = "condensation"


class ProseCandidateKind(StrEnum):
    """Candidate roles; condensation never replaces the accumulated candidate."""

    ACCUMULATED = "accumulated"
    CONDENSED = "condensed"


class ProsePipelineRequest(BaseModel):
    """One author request plus explicit attempt/cost guardrails.

    Author prose, summaries and protected facts remain accessible to the live
    pipeline but are excluded from dumps and reprs. The result retains only
    their hashes in provenance.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    operation_id: str = Field(min_length=1, max_length=128)
    length_spec: GenerationLengthSpec
    initial_instruction: str = Field(min_length=1, exclude=True, repr=False)
    context_text: str = Field(default="", exclude=True, repr=False)
    rolling_summary: str = Field(default="", exclude=True, repr=False)
    protected_facts: tuple[str, ...] = Field(default=(), exclude=True, repr=False)
    require_literal_protected_facts: bool = True
    limits: AssuranceLimits = DEFAULT_ASSURANCE_LIMITS
    estimated_call_cost_microunits: int = Field(default=0, ge=0)

    @field_validator("operation_id")
    @classmethod
    def _safe_operation_id(cls, value: str) -> str:
        normalized = value.strip()
        if not _SAFE_OPERATION_ID.fullmatch(normalized):
            raise ValueError("operation_id must be a non-sensitive stable identifier")
        if _looks_like_api_key(normalized):
            raise ValueError("operation_id must be a non-sensitive stable identifier")
        return normalized

    @field_validator("initial_instruction")
    @classmethod
    def _non_blank_instruction(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("initial instruction must not be blank")
        return value

    @field_validator("protected_facts")
    @classmethod
    def _non_blank_protected_facts(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not fact.strip() for fact in value):
            raise ValueError("protected facts must not be blank")
        normalized = tuple(_normalize_fact(fact) for fact in value)
        if len(normalized) != len(set(normalized)):
            raise ValueError("protected facts must be unique after normalization")
        return value


class ProseCallRequest(BaseModel):
    """Typed provider callback input with prose-free default serialization."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    policy_version: Literal["prose-generation-pipeline-v1"] = PROSE_PIPELINE_POLICY_VERSION
    operation_id: str = Field(min_length=1, max_length=128)
    kind: ProseCallKind
    attempt_number: int = Field(ge=1, le=50)
    continuation_number: int = Field(default=0, ge=0, le=5)
    repetition_retry_number: int = Field(default=0, ge=0, le=5)
    length_spec_sha256: str = Field(pattern=_SHA256_PATTERN)
    unit: LengthUnit
    requested_additional: int | float = Field(gt=0, allow_inf_nan=False)
    instruction: str = Field(min_length=1, exclude=True, repr=False)
    context_text: str = Field(default="", exclude=True, repr=False)
    rolling_summary: str = Field(default="", exclude=True, repr=False)
    protected_facts: tuple[str, ...] = Field(default=(), exclude=True, repr=False)
    instruction_sha256: str = Field(pattern=_SHA256_PATTERN)
    context_text_sha256: str = Field(pattern=_SHA256_PATTERN)
    rolling_summary_sha256: str = Field(pattern=_SHA256_PATTERN)
    protected_fact_sha256: tuple[str, ...] = ()
    stable_boundary_offset: int = Field(default=0, ge=0)
    stable_prefix_sha256: str = Field(pattern=_SHA256_PATTERN)
    cost_ceiling_microunits: int | None = Field(default=None, ge=0)
    idempotency_key: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def _hashes_match_live_inputs(self) -> Self:
        if self.instruction_sha256 != _sha256_text(self.instruction):
            raise ValueError("instruction hash does not match live callback input")
        if self.context_text_sha256 != _sha256_text(self.context_text):
            raise ValueError("context hash does not match live callback input")
        if self.rolling_summary_sha256 != _sha256_text(self.rolling_summary):
            raise ValueError("rolling summary hash does not match live callback input")
        expected_fact_hashes = tuple(_sha256_text(fact) for fact in self.protected_facts)
        if self.protected_fact_sha256 != expected_fact_hashes:
            raise ValueError("protected-fact hashes do not match live callback input")
        if self.idempotency_key != _derive_idempotency_key(
            operation_id=self.operation_id,
            kind=self.kind,
            attempt_number=self.attempt_number,
            continuation_number=self.continuation_number,
            repetition_retry_number=self.repetition_retry_number,
            length_spec_sha256=self.length_spec_sha256,
            instruction_sha256=self.instruction_sha256,
            context_text_sha256=self.context_text_sha256,
            rolling_summary_sha256=self.rolling_summary_sha256,
            protected_fact_sha256=self.protected_fact_sha256,
            stable_boundary_offset=self.stable_boundary_offset,
            stable_prefix_sha256=self.stable_prefix_sha256,
        ):
            raise ValueError("idempotency key does not match call evidence")
        return self

    @property
    def evidence_sha256(self) -> str:
        """Hash the serialized, prose-free callback envelope."""

        return _sha256_text(canonical_json(self.model_dump(mode="json")))


class ProseProviderResult(BaseModel):
    """Normalized result returned by a fake or production provider callback.

    ``text`` is the exact raw prose received so far, including on token limit,
    timeout, cancellation, or recoverable provider error. The callback reports
    only provider termination; application-owned length assurance decides
    whether the author request is complete.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    text: str = Field(default="", exclude=True, repr=False)
    provider: str = Field(min_length=1, max_length=256)
    model: str = Field(min_length=1, max_length=256)
    termination: ProviderTermination
    finish_reason: str = Field(default="", max_length=500, exclude=True, repr=False)
    response_id: str = Field(default="", max_length=500, exclude=True, repr=False)
    prompt_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    estimated_cost_microunits: int = Field(default=0, ge=0)
    runtime_attempts_used: int = Field(default=1, ge=0, le=50)
    error_recoverable: bool = True

    @field_validator("provider", "model")
    @classmethod
    def _safe_metadata(cls, value: str) -> str:
        normalized = value.strip()
        if not _SAFE_METADATA.fullmatch(normalized):
            raise ValueError("provider/model must be a safe identifier")
        if _looks_like_api_key(normalized):
            raise ValueError("provider/model must be a non-sensitive identifier")
        return normalized


class ProseCallExecutor(Protocol):
    """Production/fake seam for one raw-prose streaming call.

    A production callback must honor ``idempotency_key`` and any non-null
    ``cost_ceiling_microunits``, aggregate stream deltas into the exact partial
    ``text``, and normalize token-limit/timeout/cancel/error outcomes into a
    ``ProseProviderResult``. It must also report the number of underlying
    route/retry/fallback attempts in ``runtime_attempts_used``; this is distinct
    from the one logical callback counted by the pipeline. Provider exception
    messages and credentials must never be copied into identifiers or finish
    reasons. Raising is reserved for failures where the callback has no
    recoverable result or partial prose.
    """

    def __call__(self, request: ProseCallRequest) -> ProseProviderResult: ...


class ProseCallProvenance(BaseModel):
    """Hash-only evidence for one logical call and its runtime attempts."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    policy_version: Literal["prose-generation-pipeline-v1"] = PROSE_PIPELINE_POLICY_VERSION
    kind: ProseCallKind
    attempt_number: int = Field(ge=1, le=50)
    continuation_number: int = Field(ge=0, le=5)
    repetition_retry_number: int = Field(ge=0, le=5)
    idempotency_key: str = Field(pattern=_SHA256_PATTERN)
    request_evidence_sha256: str = Field(pattern=_SHA256_PATTERN)
    output_text_sha256: str = Field(pattern=_SHA256_PATTERN)
    provider: str = Field(min_length=1, max_length=256)
    model: str = Field(min_length=1, max_length=256)
    provider_termination: ProviderTermination
    provider_finish_reason: str = Field(min_length=1, max_length=100)
    provider_response_id_sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    prompt_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    estimated_cost_microunits: int = Field(ge=0)
    runtime_attempts_used: int = Field(ge=0, le=50)
    accepted_into_candidate: bool
    repetition_report: RepetitionGuardReport | None = None


class ProseCandidate(BaseModel):
    """An author-reviewable candidate whose text never enters telemetry dumps."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    kind: ProseCandidateKind
    text: str = Field(min_length=1, exclude=True, repr=False)
    text_sha256: str = Field(pattern=_SHA256_PATTERN)
    source_candidate_sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    measurement: OutputMeasurement
    assessment: OutputLengthAssessment
    protected_facts_report: ProtectedFactsReport

    @model_validator(mode="after")
    def _candidate_evidence_matches(self) -> Self:
        if self.text_sha256 != _sha256_text(self.text):
            raise ValueError("candidate text hash does not match")
        if self.protected_facts_report.candidate_text_sha256 != self.text_sha256:
            raise ValueError("protected-facts report belongs to another candidate")
        if self.kind is ProseCandidateKind.CONDENSED:
            if self.source_candidate_sha256 is None:
                raise ValueError("condensed candidate requires its source candidate hash")
        elif self.source_candidate_sha256 is not None:
            raise ValueError("accumulated candidate cannot claim a condensation source")
        return self


class ProsePipelineResult(BaseModel):
    """Final bounded state; candidate prose is live-only and excluded from dumps."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    policy_version: Literal["prose-generation-pipeline-v1"] = PROSE_PIPELINE_POLICY_VERSION
    decision: LengthAssuranceDecision
    accumulated_candidate: ProseCandidate | None = None
    condensed_candidate: ProseCandidate | None = None
    condensation_decision: LengthAssuranceDecision | None = None
    literal_protected_facts_required: bool = True
    calls: tuple[ProseCallProvenance, ...]
    progress: AssuranceProgress

    @model_validator(mode="after")
    def _coherent_result(self) -> Self:
        if self.progress.provider_attempts != len(self.calls):
            raise ValueError("logical provider-call count does not match call provenance")
        if self.progress.runtime_provider_attempts != sum(
            call.runtime_attempts_used for call in self.calls
        ):
            raise ValueError("runtime provider-attempt count does not match call provenance")
        expected_attempts = tuple(range(1, len(self.calls) + 1))
        if tuple(call.attempt_number for call in self.calls) != expected_attempts:
            raise ValueError("call provenance attempt numbers must be contiguous")
        if len({call.idempotency_key for call in self.calls}) != len(self.calls):
            raise ValueError("every provider attempt requires a unique idempotency key")
        if self.progress.estimated_cost_microunits != sum(
            call.estimated_cost_microunits for call in self.calls
        ):
            raise ValueError("estimated cost total does not match call provenance")
        accepted_continuations = sum(
            call.accepted_into_candidate
            for call in self.calls
            if call.kind in {ProseCallKind.CONTINUATION, ProseCallKind.REPETITION_RETRY}
        )
        if self.progress.continuations_used != accepted_continuations:
            raise ValueError("accepted continuation count does not match call provenance")
        repetition_retry_calls = sum(
            call.kind is ProseCallKind.REPETITION_RETRY for call in self.calls
        )
        if self.progress.repetition_retries_used != repetition_retry_calls:
            raise ValueError("repetition retry count does not match call provenance")

        self._candidate_matches_decision(
            candidate=self.accumulated_candidate,
            decision=self.decision,
            label="accumulated",
            literal_protected_facts_required=self.literal_protected_facts_required,
        )
        if self.condensed_candidate is not None:
            if self.accumulated_candidate is None or self.condensation_decision is None:
                raise ValueError("condensation requires both source and decision evidence")
            if (
                self.condensed_candidate.source_candidate_sha256
                != self.accumulated_candidate.text_sha256
            ):
                raise ValueError("condensed candidate source hash does not match")
            self._candidate_matches_decision(
                candidate=self.condensed_candidate,
                decision=self.condensation_decision,
                label="condensed",
                literal_protected_facts_required=self.literal_protected_facts_required,
            )
        elif self.condensation_decision is not None:
            # A failed/empty condensation call can still have a typed decision.
            if self.decision.action is not AssuranceAction.CREATE_CONDENSED_CANDIDATE:
                raise ValueError("orphaned condensation decision")
        return self

    @staticmethod
    def _candidate_matches_decision(
        *,
        candidate: ProseCandidate | None,
        decision: LengthAssuranceDecision,
        label: str,
        literal_protected_facts_required: bool,
    ) -> None:
        if candidate is None:
            if decision.partial_evidence is not None:
                raise ValueError(f"{label} partial evidence requires a candidate")
            return
        if candidate.measurement != decision.measurement:
            raise ValueError(f"{label} candidate measurement does not match decision")
        if candidate.assessment != decision.assessment:
            raise ValueError(f"{label} candidate assessment does not match decision")
        if (
            literal_protected_facts_required
            and not candidate.protected_facts_report.literal_gate_passed
            and decision.domain_complete
        ):
            raise ValueError(f"{label} candidate with missing protected facts cannot be complete")
        if (
            decision.partial_evidence is not None
            and decision.partial_evidence.text_sha256 != candidate.text_sha256
        ):
            raise ValueError(f"{label} partial evidence belongs to another candidate")


class ProseGenerationPipeline:
    """Run an initial call and bounded application-owned length assurance."""

    def __init__(
        self,
        *,
        assurance: LengthAssuranceService | None = None,
        planner: ContinuationPlanner | None = None,
        repetition_guard: ContinuationRepetitionGuard | None = None,
        protected_facts_guard: ProtectedFactsGuard | None = None,
    ) -> None:
        self._assurance = assurance or LengthAssuranceService()
        self._planner = planner or ContinuationPlanner()
        self._repetition_guard = repetition_guard or ContinuationRepetitionGuard()
        self._protected_facts_guard = protected_facts_guard or ProtectedFactsGuard()

    def run(
        self,
        request: ProsePipelineRequest,
        *,
        execute: ProseCallExecutor,
    ) -> ProsePipelineResult:
        """Run to a satisfied, partial, cancelled, failed, or condensation state."""

        if not _call_budget_available(
            attempts_used=0,
            estimated_cost_used=0,
            estimated_next_cost=request.estimated_call_cost_microunits,
            limits=request.limits,
        ):
            raise ValueError("initial provider call is blocked by attempt/cost limits")

        calls: list[ProseCallProvenance] = []
        progress = _progress(
            provider_attempts=0,
            runtime_provider_attempts=0,
            continuations_used=0,
            repetition_retries_used=0,
            estimated_cost_microunits=0,
            request=request,
        )
        initial_instruction = request.initial_instruction
        initial_guidance = initial_length_progress_instruction(spec=request.length_spec)
        if initial_guidance:
            initial_instruction += "\n\n" + initial_guidance
        initial_call = _build_call_request(
            request=request,
            kind=ProseCallKind.INITIAL,
            attempt_number=1,
            instruction=initial_instruction,
            context_text=request.context_text,
            rolling_summary=request.rolling_summary,
            protected_facts=request.protected_facts,
            requested_additional=request.length_spec.segment_target or request.length_spec.target,
            estimated_cost_used=progress.estimated_cost_microunits,
        )
        initial_result = execute(initial_call)
        progress = _after_provider_call(progress, initial_result, request=request)
        accumulated_text = initial_result.text
        calls.append(
            _provenance(
                call=initial_call,
                result=initial_result,
                accepted_into_candidate=bool(accumulated_text.strip()),
            )
        )
        initial_finish_reason = _safe_finish_reason(
            initial_result.finish_reason,
            fallback=initial_result.termination.value,
        )
        decision = self._assurance.evaluate(
            text=accumulated_text,
            spec=request.length_spec,
            provider_termination=initial_result.termination,
            provider_finish_reason=initial_finish_reason,
            provider_error_recoverable=initial_result.error_recoverable,
            progress=progress,
            limits=request.limits,
        )
        last_termination = initial_result.termination

        while decision.action is AssuranceAction.CONTINUE:
            plan = self._planner.plan(
                existing_text=accumulated_text,
                spec=request.length_spec,
                continuations_used=progress.continuations_used,
                rolling_summary=request.rolling_summary,
                protected_facts=request.protected_facts,
                provider_incomplete=last_termination
                in {ProviderTermination.TOKEN_LIMIT, ProviderTermination.TIMEOUT},
            )
            continuation_result = self._run_continuation_attempts(
                request=request,
                execute=execute,
                plan=plan,
                accumulated_text=accumulated_text,
                progress=progress,
                calls=calls,
            )
            accumulated_text = continuation_result.accumulated_text
            progress = continuation_result.progress
            decision = continuation_result.decision
            last_termination = continuation_result.last_termination

        accumulated_candidate = self._candidate(
            kind=ProseCandidateKind.ACCUMULATED,
            text=accumulated_text,
            request=request,
        )
        decision = self._apply_protected_facts_gate(
            candidate=accumulated_candidate,
            decision=decision,
            request=request,
            progress=progress,
        )
        condensed_candidate: ProseCandidate | None = None
        condensation_decision: LengthAssuranceDecision | None = None
        if (
            decision.action is AssuranceAction.CREATE_CONDENSED_CANDIDATE
            and accumulated_candidate is not None
        ):
            condensation = self._run_condensation(
                request=request,
                execute=execute,
                source=accumulated_candidate,
                progress=progress,
                calls=calls,
            )
            progress = condensation.progress
            condensed_candidate = condensation.candidate
            condensation_decision = condensation.decision

        return ProsePipelineResult(
            decision=decision,
            accumulated_candidate=accumulated_candidate,
            condensed_candidate=condensed_candidate,
            condensation_decision=condensation_decision,
            literal_protected_facts_required=request.require_literal_protected_facts,
            calls=tuple(calls),
            progress=progress,
        )

    def _run_continuation_attempts(
        self,
        *,
        request: ProsePipelineRequest,
        execute: ProseCallExecutor,
        plan: ContinuationPlan,
        accumulated_text: str,
        progress: AssuranceProgress,
        calls: list[ProseCallProvenance],
    ) -> _ContinuationOutcome:
        repetition_retry_number = 0
        while True:
            kind = (
                ProseCallKind.REPETITION_RETRY
                if repetition_retry_number
                else ProseCallKind.CONTINUATION
            )
            instruction = plan.instruction
            if repetition_retry_number:
                instruction = (
                    f"{instruction} A prior continuation was rejected for repetition. "
                    "Write a materially new continuation; do not reuse its wording."
                )
            call = _build_call_request(
                request=request,
                kind=kind,
                attempt_number=progress.provider_attempts + 1,
                continuation_number=plan.continuation_number,
                repetition_retry_number=repetition_retry_number,
                instruction=instruction,
                context_text=plan.tail_excerpt,
                rolling_summary=plan.rolling_summary,
                protected_facts=plan.protected_facts,
                requested_additional=plan.requested_additional,
                stable_boundary_offset=plan.stable_boundary_offset,
                stable_prefix_sha256=plan.stable_prefix_sha256,
                estimated_cost_used=progress.estimated_cost_microunits,
            )
            result = execute(call)
            progress = _after_provider_call(progress, result, request=request)
            if result.termination is ProviderTermination.CANCELLED:
                accepted = bool(result.text.strip())
                if accepted:
                    accumulated_text = _append_received_partial(
                        accumulated_text,
                        result.text,
                    )
                    progress = progress.model_copy(
                        update={"continuations_used": progress.continuations_used + 1}
                    )
                calls.append(
                    _provenance(
                        call=call,
                        result=result,
                        accepted_into_candidate=accepted,
                    )
                )
                safe_finish_reason = _safe_finish_reason(
                    result.finish_reason,
                    fallback=result.termination.value,
                )
                decision = self._assurance.evaluate(
                    text=accumulated_text,
                    spec=request.length_spec,
                    provider_termination=result.termination,
                    provider_finish_reason=safe_finish_reason,
                    provider_error_recoverable=result.error_recoverable,
                    progress=progress,
                    limits=request.limits,
                )
                return _ContinuationOutcome(
                    accumulated_text=accumulated_text,
                    progress=progress,
                    decision=decision,
                    last_termination=result.termination,
                )

            merge_report: RepetitionGuardReport | None = None
            accepted = False
            if result.text.strip():
                merge = self._repetition_guard.merge(
                    existing_text=accumulated_text,
                    continuation_text=result.text,
                )
                merge_report = merge.report
                accepted = merge.accepted
                if accepted:
                    accumulated_text = merge.combined_text
                    progress = progress.model_copy(
                        update={"continuations_used": progress.continuations_used + 1}
                    )
            calls.append(
                _provenance(
                    call=call,
                    result=result,
                    accepted_into_candidate=accepted,
                    repetition_report=merge_report,
                )
            )

            if merge_report is not None and not merge_report.accepted:
                safe_finish_reason = _safe_finish_reason(
                    result.finish_reason,
                    fallback=result.termination.value,
                )
                decision = self._assurance.repetition_rejected(
                    preserved_text=accumulated_text,
                    spec=request.length_spec,
                    progress=progress,
                    limits=request.limits,
                    provider_termination=result.termination,
                    provider_finish_reason=safe_finish_reason,
                )
                if decision.action is AssuranceAction.CONTINUE:
                    repetition_retry_number += 1
                    progress = progress.model_copy(
                        update={"repetition_retries_used": (progress.repetition_retries_used + 1)}
                    )
                    continue
                return _ContinuationOutcome(
                    accumulated_text=accumulated_text,
                    progress=progress,
                    decision=decision,
                    last_termination=result.termination,
                )

            safe_finish_reason = _safe_finish_reason(
                result.finish_reason,
                fallback=result.termination.value,
            )
            decision = self._assurance.evaluate(
                text=accumulated_text,
                spec=request.length_spec,
                provider_termination=result.termination,
                provider_finish_reason=safe_finish_reason,
                provider_error_recoverable=result.error_recoverable,
                progress=progress,
                limits=request.limits,
            )
            return _ContinuationOutcome(
                accumulated_text=accumulated_text,
                progress=progress,
                decision=decision,
                last_termination=result.termination,
            )

    def _run_condensation(
        self,
        *,
        request: ProsePipelineRequest,
        execute: ProseCallExecutor,
        source: ProseCandidate,
        progress: AssuranceProgress,
        calls: list[ProseCallProvenance],
    ) -> _CondensationOutcome:
        instruction = (
            "Create a separate condensed candidate from the supplied prose. "
            "Preserve names, chronology, protected facts, voice, and the complete ending. "
            f"Aim for {request.length_spec.target:g} {request.length_spec.unit.value} and "
            "remain within the saved minimum/maximum. Return raw prose only. "
            "Never truncate or overwrite the source candidate."
        )
        call = _build_call_request(
            request=request,
            kind=ProseCallKind.CONDENSATION,
            attempt_number=progress.provider_attempts + 1,
            instruction=instruction,
            context_text=source.text,
            protected_facts=request.protected_facts,
            requested_additional=request.length_spec.target,
            stable_prefix_sha256=source.text_sha256,
            estimated_cost_used=progress.estimated_cost_microunits,
        )
        result = execute(call)
        progress = _after_provider_call(progress, result, request=request)
        calls.append(
            _provenance(
                call=call,
                result=result,
                accepted_into_candidate=bool(result.text.strip()),
            )
        )
        safe_finish_reason = _safe_finish_reason(
            result.finish_reason,
            fallback=result.termination.value,
        )
        decision = self._assurance.evaluate(
            text=result.text,
            spec=request.length_spec,
            provider_termination=result.termination,
            provider_finish_reason=safe_finish_reason,
            provider_error_recoverable=result.error_recoverable,
            progress=progress,
            limits=request.limits,
        )
        candidate = self._candidate(
            kind=ProseCandidateKind.CONDENSED,
            text=result.text,
            request=request,
            source_candidate_sha256=source.text_sha256,
        )
        decision = self._apply_protected_facts_gate(
            candidate=candidate,
            decision=decision,
            request=request,
            progress=progress,
        )
        return _CondensationOutcome(
            progress=progress,
            candidate=candidate,
            decision=decision,
        )

    def _candidate(
        self,
        *,
        kind: ProseCandidateKind,
        text: str,
        request: ProsePipelineRequest,
        source_candidate_sha256: str | None = None,
    ) -> ProseCandidate | None:
        if not text.strip():
            return None
        measurement = measure_output(text, unit=request.length_spec.unit)
        return ProseCandidate(
            kind=kind,
            text=text,
            text_sha256=_sha256_text(text),
            source_candidate_sha256=source_candidate_sha256,
            measurement=measurement,
            assessment=measurement.assess(request.length_spec),
            protected_facts_report=self._protected_facts_guard.inspect(
                candidate_text=text,
                protected_facts=request.protected_facts,
            ),
        )

    def _apply_protected_facts_gate(
        self,
        *,
        candidate: ProseCandidate | None,
        decision: LengthAssuranceDecision,
        request: ProsePipelineRequest,
        progress: AssuranceProgress,
    ) -> LengthAssuranceDecision:
        if (
            candidate is None
            or not request.require_literal_protected_facts
            or candidate.protected_facts_report.literal_gate_passed
            or not decision.domain_complete
        ):
            return decision
        return self._assurance.protected_facts_unverified(
            text=candidate.text,
            spec=request.length_spec,
            provider_termination=decision.provider_termination,
            provider_finish_reason=decision.provider_finish_reason,
            progress=progress,
            limits=request.limits,
        )


class _ContinuationOutcome(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    accumulated_text: str = Field(exclude=True, repr=False)
    progress: AssuranceProgress
    decision: LengthAssuranceDecision
    last_termination: ProviderTermination


class _CondensationOutcome(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    progress: AssuranceProgress
    candidate: ProseCandidate | None
    decision: LengthAssuranceDecision


def initial_length_progress_instruction(*, spec: GenerationLengthSpec) -> str:
    """Return application-owned guidance from typed bounds, never author text."""

    if spec.segment_target is None or spec.segment_target == spec.target:
        return ""
    return _length_progress_instruction(
        spec=spec,
        actual=0,
        requested_additional=spec.segment_target,
        continuations_remaining=spec.max_continuations,
        initial=True,
    )


def _build_call_request(
    *,
    request: ProsePipelineRequest,
    kind: ProseCallKind,
    attempt_number: int,
    instruction: str,
    context_text: str,
    requested_additional: int | float,
    continuation_number: int = 0,
    repetition_retry_number: int = 0,
    rolling_summary: str = "",
    protected_facts: tuple[str, ...] = (),
    stable_boundary_offset: int = 0,
    stable_prefix_sha256: str | None = None,
    estimated_cost_used: int = 0,
) -> ProseCallRequest:
    instruction_sha256 = _sha256_text(instruction)
    context_text_sha256 = _sha256_text(context_text)
    rolling_summary_sha256 = _sha256_text(rolling_summary)
    protected_fact_sha256 = tuple(_sha256_text(fact) for fact in protected_facts)
    stable_prefix_hash = stable_prefix_sha256 or _sha256_text("")
    idempotency_key = _derive_idempotency_key(
        operation_id=request.operation_id,
        kind=kind,
        attempt_number=attempt_number,
        continuation_number=continuation_number,
        repetition_retry_number=repetition_retry_number,
        length_spec_sha256=request.length_spec.sha256,
        instruction_sha256=instruction_sha256,
        context_text_sha256=context_text_sha256,
        rolling_summary_sha256=rolling_summary_sha256,
        protected_fact_sha256=protected_fact_sha256,
        stable_boundary_offset=stable_boundary_offset,
        stable_prefix_sha256=stable_prefix_hash,
    )
    return ProseCallRequest(
        operation_id=request.operation_id,
        kind=kind,
        attempt_number=attempt_number,
        continuation_number=continuation_number,
        repetition_retry_number=repetition_retry_number,
        length_spec_sha256=request.length_spec.sha256,
        unit=request.length_spec.unit,
        requested_additional=requested_additional,
        instruction=instruction,
        context_text=context_text,
        rolling_summary=rolling_summary,
        protected_facts=protected_facts,
        instruction_sha256=instruction_sha256,
        context_text_sha256=context_text_sha256,
        rolling_summary_sha256=rolling_summary_sha256,
        protected_fact_sha256=protected_fact_sha256,
        stable_boundary_offset=stable_boundary_offset,
        stable_prefix_sha256=stable_prefix_hash,
        cost_ceiling_microunits=_remaining_cost_ceiling(
            request=request,
            estimated_cost_used=estimated_cost_used,
        ),
        idempotency_key=idempotency_key,
    )


def _provenance(
    *,
    call: ProseCallRequest,
    result: ProseProviderResult,
    accepted_into_candidate: bool,
    repetition_report: RepetitionGuardReport | None = None,
) -> ProseCallProvenance:
    return ProseCallProvenance(
        kind=call.kind,
        attempt_number=call.attempt_number,
        continuation_number=call.continuation_number,
        repetition_retry_number=call.repetition_retry_number,
        idempotency_key=call.idempotency_key,
        request_evidence_sha256=call.evidence_sha256,
        output_text_sha256=_sha256_text(result.text),
        provider=result.provider,
        model=result.model,
        provider_termination=result.termination,
        provider_finish_reason=_safe_finish_reason(
            result.finish_reason,
            fallback=result.termination.value,
        ),
        provider_response_id_sha256=(
            _sha256_text(result.response_id) if result.response_id else None
        ),
        prompt_tokens=result.prompt_tokens,
        output_tokens=result.output_tokens,
        estimated_cost_microunits=result.estimated_cost_microunits,
        runtime_attempts_used=result.runtime_attempts_used,
        accepted_into_candidate=accepted_into_candidate,
        repetition_report=repetition_report,
    )


def _progress(
    *,
    provider_attempts: int,
    runtime_provider_attempts: int,
    continuations_used: int,
    repetition_retries_used: int,
    estimated_cost_microunits: int,
    request: ProsePipelineRequest,
) -> AssuranceProgress:
    return AssuranceProgress(
        provider_attempts=provider_attempts,
        runtime_provider_attempts=runtime_provider_attempts,
        continuations_used=continuations_used,
        repetition_retries_used=repetition_retries_used,
        estimated_cost_microunits=estimated_cost_microunits,
        next_attempt_estimated_cost_microunits=request.estimated_call_cost_microunits,
    )


def _after_provider_call(
    progress: AssuranceProgress,
    result: ProseProviderResult,
    *,
    request: ProsePipelineRequest,
) -> AssuranceProgress:
    return _progress(
        provider_attempts=progress.provider_attempts + 1,
        runtime_provider_attempts=(
            progress.runtime_provider_attempts + result.runtime_attempts_used
        ),
        continuations_used=progress.continuations_used,
        repetition_retries_used=progress.repetition_retries_used,
        estimated_cost_microunits=(
            progress.estimated_cost_microunits + result.estimated_cost_microunits
        ),
        request=request,
    )


def _append_received_partial(existing_text: str, received_text: str) -> str:
    """Preserve a cancelled continuation exactly and stop orchestration."""

    if not existing_text:
        return received_text
    return f"{existing_text}\n\n{received_text}"


def _call_budget_available(
    *,
    attempts_used: int,
    estimated_cost_used: int,
    estimated_next_cost: int,
    limits: AssuranceLimits,
) -> bool:
    if attempts_used >= limits.max_total_attempts:
        return False
    if limits.max_estimated_cost_microunits is None:
        return True
    return estimated_cost_used + estimated_next_cost <= limits.max_estimated_cost_microunits


def _remaining_cost_ceiling(
    *,
    request: ProsePipelineRequest,
    estimated_cost_used: int,
) -> int | None:
    maximum = request.limits.max_estimated_cost_microunits
    if maximum is None:
        return None
    return max(0, maximum - estimated_cost_used)


def _derive_idempotency_key(
    *,
    operation_id: str,
    kind: ProseCallKind,
    attempt_number: int,
    continuation_number: int,
    repetition_retry_number: int,
    length_spec_sha256: str,
    instruction_sha256: str,
    context_text_sha256: str,
    rolling_summary_sha256: str,
    protected_fact_sha256: tuple[str, ...],
    stable_boundary_offset: int,
    stable_prefix_sha256: str,
) -> str:
    evidence = {
        "policy_version": PROSE_PIPELINE_POLICY_VERSION,
        "operation_id": operation_id,
        "kind": kind.value,
        "attempt_number": attempt_number,
        "continuation_number": continuation_number,
        "repetition_retry_number": repetition_retry_number,
        "length_spec_sha256": length_spec_sha256,
        "instruction_sha256": instruction_sha256,
        "context_text_sha256": context_text_sha256,
        "rolling_summary_sha256": rolling_summary_sha256,
        "protected_fact_sha256": protected_fact_sha256,
        "stable_boundary_offset": stable_boundary_offset,
        "stable_prefix_sha256": stable_prefix_sha256,
    }
    return _sha256_text(canonical_json(evidence))


def _safe_finish_reason(value: str, *, fallback: str) -> str:
    normalized = value.strip().casefold()
    return normalized if normalized in _SAFE_FINISH_REASON_CODES else fallback


def _normalize_fact(text: str) -> str:
    return _WHITESPACE.sub(" ", unicodedata.normalize("NFKC", text).casefold()).strip()


def _looks_like_api_key(value: str) -> bool:
    return _API_KEY_PATTERN.search(value) is not None


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


__all__ = [
    "PROSE_PIPELINE_POLICY_VERSION",
    "ProseCallExecutor",
    "ProseCallKind",
    "ProseCallProvenance",
    "ProseCallRequest",
    "ProseCandidate",
    "ProseCandidateKind",
    "ProseGenerationPipeline",
    "ProsePipelineRequest",
    "ProsePipelineResult",
    "ProseProviderResult",
    "initial_length_progress_instruction",
]
