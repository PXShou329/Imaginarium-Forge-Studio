"""Provider-neutral, bounded length assurance for prose generation.

Provider adapters report how a call ended. This module independently decides
whether the author's length contract is complete, needs another bounded call,
or must remain a preserved partial. It deliberately contains no provider
client, credential, persistence, or UI concerns.
"""

from __future__ import annotations

import hashlib
import re
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from imaginarium_forge.application.services.generation.output_measurement import (
    LengthBand,
    OutputLengthAssessment,
    OutputMeasurement,
    measure_output,
)
from imaginarium_forge.domain.generation.length import (
    GenerationLengthSpec,
    LengthStrictness,
)

_SAFE_FINISH_REASON = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,99}$")


class ProviderTermination(StrEnum):
    """A provider call's terminal condition, not a domain completion claim."""

    NORMAL_STOP = "normal_stop"
    TOKEN_LIMIT = "token_limit"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    ERROR = "error"


class AssuranceStatus(StrEnum):
    """Author-facing domain status, richer than provider success/failure."""

    SUCCEEDED_SATISFIED = "succeeded_satisfied"
    SUCCEEDED_ADVISORY_MISS = "succeeded_advisory_miss"
    CONTINUE_REQUIRED = "continue_required"
    PARTIAL_NEEDS_ACTION = "partial_needs_action"
    CANCELLED_WITH_PARTIAL = "cancelled_with_partial"
    CANCELLED = "cancelled"
    FAILED_RECOVERABLE = "failed_recoverable"
    FAILED_TERMINAL = "failed_terminal"


class AssuranceAction(StrEnum):
    """The next application action; candidate creation remains outside this service."""

    COMPLETE = "complete"
    CONTINUE = "continue"
    SAVE_PARTIAL = "save_partial"
    CREATE_CONDENSED_CANDIDATE = "create_condensed_candidate"
    CANCEL = "cancel"
    FAIL = "fail"


class AssuranceReason(StrEnum):
    """Stable, non-sensitive explanation codes for persistence and telemetry."""

    LENGTH_SATISFIED = "length_satisfied"
    ADVISORY_MISS = "advisory_miss"
    BELOW_MINIMUM = "below_minimum"
    ABOVE_MAXIMUM = "above_maximum"
    TOKEN_LIMIT_PARTIAL = "token_limit_partial"
    TIMEOUT_PARTIAL = "timeout_partial"
    PROVIDER_ERROR = "provider_error"
    EMPTY_OUTPUT = "empty_output"
    CANCELLED_BY_AUTHOR = "cancelled_by_author"
    REPETITION_RETRY_REQUIRED = "repetition_retry_required"
    REPETITION_RETRY_LIMIT_REACHED = "repetition_retry_limit_reached"
    CONTINUATION_LIMIT_REACHED = "continuation_limit_reached"
    ATTEMPT_LIMIT_REACHED = "attempt_limit_reached"
    COST_LIMIT_REACHED = "cost_limit_reached"
    PROTECTED_FACTS_UNVERIFIED = "protected_facts_unverified"
    PROVIDER_REFUSED = "provider_refused"


class BudgetBlockReason(StrEnum):
    """Why another provider call is not permitted."""

    CONTINUATION_LIMIT = "continuation_limit"
    ATTEMPT_LIMIT = "attempt_limit"
    COST_LIMIT = "cost_limit"
    REPETITION_RETRY_LIMIT = "repetition_retry_limit"


class AssuranceLimits(BaseModel):
    """Logical-call guardrails independent of provider-native retry settings."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    max_total_attempts: int = Field(default=8, ge=1, le=50)
    max_repetition_retries: int = Field(default=2, ge=0, le=5)
    max_estimated_cost_microunits: int | None = Field(default=None, ge=0)


DEFAULT_ASSURANCE_LIMITS = AssuranceLimits()


class AssuranceProgress(BaseModel):
    """Consumed logical calls, underlying runtime attempts, and cost evidence.

    ``provider_attempts`` counts calls issued by the application pipeline. One
    such callback may contain multiple provider-route/retry/fallback attempts;
    their truthful aggregate is recorded separately in
    ``runtime_provider_attempts``. A zero runtime count is permitted for a
    deterministic fake or for evidence that made no provider attempt.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    provider_attempts: int = Field(default=1, ge=0, le=50)
    runtime_provider_attempts: int = Field(default=0, ge=0, le=2_500)
    continuations_used: int = Field(default=0, ge=0, le=5)
    repetition_retries_used: int = Field(default=0, ge=0, le=5)
    estimated_cost_microunits: int = Field(default=0, ge=0)
    next_attempt_estimated_cost_microunits: int = Field(default=0, ge=0)


class AssuranceBudgetState(BaseModel):
    """Exact remaining attempt/cost budgets used to justify a continuation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    continuations_remaining: int = Field(ge=0)
    provider_attempts_remaining: int = Field(ge=0)
    repetition_retries_remaining: int = Field(ge=0)
    estimated_cost_remaining_microunits: int | None = Field(default=None, ge=0)
    can_issue_continuation: bool
    continuation_block_reason: BudgetBlockReason | None = None
    can_issue_condensation: bool
    condensation_block_reason: BudgetBlockReason | None = None
    can_retry_repetition: bool
    repetition_block_reason: BudgetBlockReason | None = None

    @model_validator(mode="after")
    def _coherent_permissions(self) -> Self:
        if self.can_issue_continuation == (self.continuation_block_reason is not None):
            raise ValueError("continuation permission and block reason disagree")
        if self.can_retry_repetition == (self.repetition_block_reason is not None):
            raise ValueError("repetition retry permission and block reason disagree")
        if self.can_issue_condensation == (self.condensation_block_reason is not None):
            raise ValueError("condensation permission and block reason disagree")
        return self


class PartialEvidence(BaseModel):
    """Serializable proof of a partial without duplicating the prose into telemetry."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    text_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    measurement: OutputMeasurement
    assessment: OutputLengthAssessment
    provider_termination: ProviderTermination
    provider_finish_reason: str = Field(min_length=1, max_length=100)
    provider_attempts: int = Field(ge=0)
    runtime_provider_attempts: int = Field(ge=0)
    continuations_used: int = Field(ge=0)
    repetition_retries_used: int = Field(ge=0)
    estimated_cost_microunits: int = Field(ge=0)


class LengthAssuranceDecision(BaseModel):
    """Mechanical result for one accumulated prose buffer."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: AssuranceStatus
    action: AssuranceAction
    reason: AssuranceReason
    measurement: OutputMeasurement
    assessment: OutputLengthAssessment
    provider_termination: ProviderTermination
    provider_finish_reason: str = Field(min_length=1, max_length=100)
    provider_normal_stop: bool
    domain_complete: bool
    preserve_partial: bool
    original_candidate_preserved: bool = False
    budget: AssuranceBudgetState
    partial_evidence: PartialEvidence | None = None

    @model_validator(mode="after")
    def _coherent_decision(self) -> Self:
        complete_statuses = {
            AssuranceStatus.SUCCEEDED_SATISFIED,
            AssuranceStatus.SUCCEEDED_ADVISORY_MISS,
        }
        if self.domain_complete != (self.status in complete_statuses):
            raise ValueError("domain_complete disagrees with assurance status")
        if self.provider_normal_stop != (
            self.provider_termination is ProviderTermination.NORMAL_STOP
        ):
            raise ValueError("provider_normal_stop disagrees with provider termination")
        if self.preserve_partial != (self.partial_evidence is not None):
            raise ValueError("preserved partial requires exactly one partial evidence record")
        if self.action is AssuranceAction.COMPLETE and not self.domain_complete:
            raise ValueError("complete action requires a domain-complete result")
        if self.action is AssuranceAction.CONTINUE:
            if self.status is not AssuranceStatus.CONTINUE_REQUIRED:
                raise ValueError("continue action requires continue-required status")
            if not self.preserve_partial or not self.budget.can_issue_continuation:
                raise ValueError("continuation requires a saved partial and available budget")
        if self.action is AssuranceAction.CREATE_CONDENSED_CANDIDATE and (
            not self.original_candidate_preserved
            or self.domain_complete
            or not self.budget.can_issue_condensation
        ):
            raise ValueError("condensation must preserve the original and have attempt/cost budget")
        return self


class LengthAssuranceService:
    """Assess accumulated prose without trusting a provider finish reason."""

    def budget_state(
        self,
        *,
        spec: GenerationLengthSpec,
        progress: AssuranceProgress,
        limits: AssuranceLimits,
    ) -> AssuranceBudgetState:
        """Calculate bounded continuation and duplicate-retry permissions."""

        continuations_remaining = max(0, spec.max_continuations - progress.continuations_used)
        provider_attempts_remaining = max(0, limits.max_total_attempts - progress.provider_attempts)
        repetition_retries_remaining = max(
            0, limits.max_repetition_retries - progress.repetition_retries_used
        )

        cost_remaining: int | None = None
        cost_blocked = False
        if limits.max_estimated_cost_microunits is not None:
            cost_remaining = max(
                0,
                limits.max_estimated_cost_microunits - progress.estimated_cost_microunits,
            )
            cost_blocked = (
                progress.estimated_cost_microunits >= limits.max_estimated_cost_microunits
                or progress.next_attempt_estimated_cost_microunits > cost_remaining
            )

        continuation_block = self._call_block_reason(
            continuations_remaining=continuations_remaining,
            provider_attempts_remaining=provider_attempts_remaining,
            cost_blocked=cost_blocked,
        )
        condensation_block = self._attempt_cost_block_reason(
            provider_attempts_remaining=provider_attempts_remaining,
            cost_blocked=cost_blocked,
        )
        repetition_block = continuation_block
        if repetition_block is None and repetition_retries_remaining == 0:
            repetition_block = BudgetBlockReason.REPETITION_RETRY_LIMIT

        return AssuranceBudgetState(
            continuations_remaining=continuations_remaining,
            provider_attempts_remaining=provider_attempts_remaining,
            repetition_retries_remaining=repetition_retries_remaining,
            estimated_cost_remaining_microunits=cost_remaining,
            can_issue_continuation=continuation_block is None,
            continuation_block_reason=continuation_block,
            can_issue_condensation=condensation_block is None,
            condensation_block_reason=condensation_block,
            can_retry_repetition=repetition_block is None,
            repetition_block_reason=repetition_block,
        )

    def evaluate(
        self,
        *,
        text: str,
        spec: GenerationLengthSpec,
        provider_termination: ProviderTermination,
        progress: AssuranceProgress,
        limits: AssuranceLimits = DEFAULT_ASSURANCE_LIMITS,
        provider_finish_reason: str = "",
        provider_error_recoverable: bool = True,
    ) -> LengthAssuranceDecision:
        """Return a bounded next action for the exact accumulated prose.

        ``NORMAL_STOP`` only states that the provider ended normally. A result
        below a required minimum still returns ``CONTINUE_REQUIRED`` or a saved
        partial when guardrails are exhausted.
        """

        if progress.continuations_used > spec.max_continuations:
            raise ValueError("continuations_used exceeds the saved length contract")

        measurement = measure_output(text, unit=spec.unit)
        assessment = measurement.assess(spec)
        budget = self.budget_state(spec=spec, progress=progress, limits=limits)
        safe_finish_reason = self._safe_finish_reason(
            provider_finish_reason, fallback=provider_termination.value
        )

        if provider_termination is ProviderTermination.CANCELLED:
            if text.strip():
                return self._decision(
                    status=AssuranceStatus.CANCELLED_WITH_PARTIAL,
                    action=AssuranceAction.SAVE_PARTIAL,
                    reason=AssuranceReason.CANCELLED_BY_AUTHOR,
                    text=text,
                    measurement=measurement,
                    assessment=assessment,
                    provider_termination=provider_termination,
                    provider_finish_reason=safe_finish_reason,
                    progress=progress,
                    budget=budget,
                    preserve_partial=True,
                )
            return self._decision(
                status=AssuranceStatus.CANCELLED,
                action=AssuranceAction.CANCEL,
                reason=AssuranceReason.CANCELLED_BY_AUTHOR,
                text=text,
                measurement=measurement,
                assessment=assessment,
                provider_termination=provider_termination,
                provider_finish_reason=safe_finish_reason,
                progress=progress,
                budget=budget,
            )

        if provider_termination is ProviderTermination.ERROR:
            return self._decision(
                status=(
                    AssuranceStatus.FAILED_RECOVERABLE
                    if provider_error_recoverable
                    else AssuranceStatus.FAILED_TERMINAL
                ),
                action=AssuranceAction.SAVE_PARTIAL if text.strip() else AssuranceAction.FAIL,
                reason=AssuranceReason.PROVIDER_ERROR,
                text=text,
                measurement=measurement,
                assessment=assessment,
                provider_termination=provider_termination,
                provider_finish_reason=safe_finish_reason,
                progress=progress,
                budget=budget,
                preserve_partial=bool(text.strip()),
            )

        if not text.strip():
            return self._decision(
                status=AssuranceStatus.FAILED_RECOVERABLE,
                action=AssuranceAction.FAIL,
                reason=AssuranceReason.EMPTY_OUTPUT,
                text=text,
                measurement=measurement,
                assessment=assessment,
                provider_termination=provider_termination,
                provider_finish_reason=safe_finish_reason,
                progress=progress,
                budget=budget,
            )

        if provider_termination in {
            ProviderTermination.TOKEN_LIMIT,
            ProviderTermination.TIMEOUT,
        }:
            reason = (
                AssuranceReason.TOKEN_LIMIT_PARTIAL
                if provider_termination is ProviderTermination.TOKEN_LIMIT
                else AssuranceReason.TIMEOUT_PARTIAL
            )
            if assessment.band is LengthBand.ABOVE:
                return self._decision(
                    status=AssuranceStatus.PARTIAL_NEEDS_ACTION,
                    action=AssuranceAction.SAVE_PARTIAL,
                    reason=AssuranceReason.ABOVE_MAXIMUM,
                    text=text,
                    measurement=measurement,
                    assessment=assessment,
                    provider_termination=provider_termination,
                    provider_finish_reason=safe_finish_reason,
                    progress=progress,
                    budget=budget,
                    preserve_partial=True,
                    original_candidate_preserved=True,
                )
            return self._partial_or_continue(
                text=text,
                measurement=measurement,
                assessment=assessment,
                provider_termination=provider_termination,
                provider_finish_reason=safe_finish_reason,
                progress=progress,
                budget=budget,
                reason=reason,
            )

        if assessment.band is LengthBand.ABOVE:
            if not budget.can_issue_condensation:
                return self._decision(
                    status=AssuranceStatus.PARTIAL_NEEDS_ACTION,
                    action=AssuranceAction.SAVE_PARTIAL,
                    reason=self._condensation_budget_reason(budget),
                    text=text,
                    measurement=measurement,
                    assessment=assessment,
                    provider_termination=provider_termination,
                    provider_finish_reason=safe_finish_reason,
                    progress=progress,
                    budget=budget,
                    preserve_partial=True,
                    original_candidate_preserved=True,
                )
            return self._decision(
                status=AssuranceStatus.PARTIAL_NEEDS_ACTION,
                action=AssuranceAction.CREATE_CONDENSED_CANDIDATE,
                reason=AssuranceReason.ABOVE_MAXIMUM,
                text=text,
                measurement=measurement,
                assessment=assessment,
                provider_termination=provider_termination,
                provider_finish_reason=safe_finish_reason,
                progress=progress,
                budget=budget,
                original_candidate_preserved=True,
            )

        if assessment.satisfied:
            advisory_miss = (
                spec.strictness is LengthStrictness.ADVISORY
                and assessment.band is not LengthBand.WITHIN
            )
            return self._decision(
                status=(
                    AssuranceStatus.SUCCEEDED_ADVISORY_MISS
                    if advisory_miss
                    else AssuranceStatus.SUCCEEDED_SATISFIED
                ),
                action=AssuranceAction.COMPLETE,
                reason=(
                    AssuranceReason.ADVISORY_MISS
                    if advisory_miss
                    else AssuranceReason.LENGTH_SATISFIED
                ),
                text=text,
                measurement=measurement,
                assessment=assessment,
                provider_termination=provider_termination,
                provider_finish_reason=safe_finish_reason,
                progress=progress,
                budget=budget,
                domain_complete=True,
            )

        return self._partial_or_continue(
            text=text,
            measurement=measurement,
            assessment=assessment,
            provider_termination=provider_termination,
            provider_finish_reason=safe_finish_reason,
            progress=progress,
            budget=budget,
            reason=AssuranceReason.BELOW_MINIMUM,
        )

    def repetition_rejected(
        self,
        *,
        preserved_text: str,
        spec: GenerationLengthSpec,
        progress: AssuranceProgress,
        limits: AssuranceLimits = DEFAULT_ASSURANCE_LIMITS,
        provider_termination: ProviderTermination = ProviderTermination.NORMAL_STOP,
        provider_finish_reason: str = "repetition_rejected",
    ) -> LengthAssuranceDecision:
        """Return a typed retry/partial decision after a guard rejects prose.

        ``preserved_text`` is the unchanged accepted prefix. The rejected
        provider output remains evidenced separately by the repetition report;
        it is never merged here.
        """

        if not preserved_text:
            raise ValueError("a repetition rejection requires preserved prose")
        if progress.continuations_used > spec.max_continuations:
            raise ValueError("continuations_used exceeds the saved length contract")

        measurement = measure_output(preserved_text, unit=spec.unit)
        assessment = measurement.assess(spec)
        budget = self.budget_state(spec=spec, progress=progress, limits=limits)
        safe_finish_reason = self._safe_finish_reason(
            provider_finish_reason, fallback="repetition_rejected"
        )
        if budget.can_retry_repetition:
            return self._decision(
                status=AssuranceStatus.CONTINUE_REQUIRED,
                action=AssuranceAction.CONTINUE,
                reason=AssuranceReason.REPETITION_RETRY_REQUIRED,
                text=preserved_text,
                measurement=measurement,
                assessment=assessment,
                provider_termination=provider_termination,
                provider_finish_reason=safe_finish_reason,
                progress=progress,
                budget=budget,
                preserve_partial=True,
            )

        if budget.repetition_block_reason is BudgetBlockReason.REPETITION_RETRY_LIMIT:
            reason = AssuranceReason.REPETITION_RETRY_LIMIT_REACHED
        else:
            reason = self._budget_reason(budget)
        return self._decision(
            status=AssuranceStatus.PARTIAL_NEEDS_ACTION,
            action=AssuranceAction.SAVE_PARTIAL,
            reason=reason,
            text=preserved_text,
            measurement=measurement,
            assessment=assessment,
            provider_termination=provider_termination,
            provider_finish_reason=safe_finish_reason,
            progress=progress,
            budget=budget,
            preserve_partial=True,
        )

    def protected_facts_unverified(
        self,
        *,
        text: str,
        spec: GenerationLengthSpec,
        provider_termination: ProviderTermination,
        progress: AssuranceProgress,
        limits: AssuranceLimits = DEFAULT_ASSURANCE_LIMITS,
        provider_finish_reason: str = "protected_facts_unverified",
    ) -> LengthAssuranceDecision:
        """Fail closed when deterministic evidence shows a protected fact is absent.

        The candidate remains author-reviewable, but a mechanically satisfied
        length contract cannot make it domain-complete while a protected fact
        is unverified.
        """

        if not text.strip():
            raise ValueError("protected-fact review requires preserved prose")
        measurement = measure_output(text, unit=spec.unit)
        assessment = measurement.assess(spec)
        budget = self.budget_state(spec=spec, progress=progress, limits=limits)
        safe_finish_reason = self._safe_finish_reason(
            provider_finish_reason,
            fallback=AssuranceReason.PROTECTED_FACTS_UNVERIFIED.value,
        )
        return self._decision(
            status=AssuranceStatus.PARTIAL_NEEDS_ACTION,
            action=AssuranceAction.SAVE_PARTIAL,
            reason=AssuranceReason.PROTECTED_FACTS_UNVERIFIED,
            text=text,
            measurement=measurement,
            assessment=assessment,
            provider_termination=provider_termination,
            provider_finish_reason=safe_finish_reason,
            progress=progress,
            budget=budget,
            preserve_partial=True,
        )

    @staticmethod
    def _call_block_reason(
        *,
        continuations_remaining: int,
        provider_attempts_remaining: int,
        cost_blocked: bool,
    ) -> BudgetBlockReason | None:
        if continuations_remaining == 0:
            return BudgetBlockReason.CONTINUATION_LIMIT
        if provider_attempts_remaining == 0:
            return BudgetBlockReason.ATTEMPT_LIMIT
        if cost_blocked:
            return BudgetBlockReason.COST_LIMIT
        return None

    @staticmethod
    def _attempt_cost_block_reason(
        *,
        provider_attempts_remaining: int,
        cost_blocked: bool,
    ) -> BudgetBlockReason | None:
        if provider_attempts_remaining == 0:
            return BudgetBlockReason.ATTEMPT_LIMIT
        if cost_blocked:
            return BudgetBlockReason.COST_LIMIT
        return None

    @staticmethod
    def _safe_finish_reason(value: str, *, fallback: str) -> str:
        normalized = value.strip().casefold()
        if _SAFE_FINISH_REASON.fullmatch(normalized):
            return normalized
        return fallback

    def _partial_or_continue(
        self,
        *,
        text: str,
        measurement: OutputMeasurement,
        assessment: OutputLengthAssessment,
        provider_termination: ProviderTermination,
        provider_finish_reason: str,
        progress: AssuranceProgress,
        budget: AssuranceBudgetState,
        reason: AssuranceReason,
    ) -> LengthAssuranceDecision:
        if not text.strip():
            return self._decision(
                status=AssuranceStatus.FAILED_RECOVERABLE,
                action=AssuranceAction.FAIL,
                reason=reason,
                text=text,
                measurement=measurement,
                assessment=assessment,
                provider_termination=provider_termination,
                provider_finish_reason=provider_finish_reason,
                progress=progress,
                budget=budget,
            )
        if budget.can_issue_continuation:
            return self._decision(
                status=AssuranceStatus.CONTINUE_REQUIRED,
                action=AssuranceAction.CONTINUE,
                reason=reason,
                text=text,
                measurement=measurement,
                assessment=assessment,
                provider_termination=provider_termination,
                provider_finish_reason=provider_finish_reason,
                progress=progress,
                budget=budget,
                preserve_partial=True,
            )

        return self._decision(
            status=AssuranceStatus.PARTIAL_NEEDS_ACTION,
            action=AssuranceAction.SAVE_PARTIAL,
            reason=self._budget_reason(budget),
            text=text,
            measurement=measurement,
            assessment=assessment,
            provider_termination=provider_termination,
            provider_finish_reason=provider_finish_reason,
            progress=progress,
            budget=budget,
            preserve_partial=True,
        )

    @staticmethod
    def _budget_reason(budget: AssuranceBudgetState) -> AssuranceReason:
        if budget.continuation_block_reason is BudgetBlockReason.CONTINUATION_LIMIT:
            return AssuranceReason.CONTINUATION_LIMIT_REACHED
        if budget.continuation_block_reason is BudgetBlockReason.ATTEMPT_LIMIT:
            return AssuranceReason.ATTEMPT_LIMIT_REACHED
        if budget.continuation_block_reason is BudgetBlockReason.COST_LIMIT:
            return AssuranceReason.COST_LIMIT_REACHED
        raise ValueError("a blocked continuation must carry a known block reason")

    @staticmethod
    def _condensation_budget_reason(budget: AssuranceBudgetState) -> AssuranceReason:
        if budget.condensation_block_reason is BudgetBlockReason.ATTEMPT_LIMIT:
            return AssuranceReason.ATTEMPT_LIMIT_REACHED
        if budget.condensation_block_reason is BudgetBlockReason.COST_LIMIT:
            return AssuranceReason.COST_LIMIT_REACHED
        raise ValueError("a blocked condensation must carry an attempt/cost block reason")

    @staticmethod
    def _decision(
        *,
        status: AssuranceStatus,
        action: AssuranceAction,
        reason: AssuranceReason,
        text: str,
        measurement: OutputMeasurement,
        assessment: OutputLengthAssessment,
        provider_termination: ProviderTermination,
        provider_finish_reason: str,
        progress: AssuranceProgress,
        budget: AssuranceBudgetState,
        domain_complete: bool = False,
        preserve_partial: bool = False,
        original_candidate_preserved: bool = False,
    ) -> LengthAssuranceDecision:
        partial_evidence = None
        if preserve_partial:
            partial_evidence = PartialEvidence(
                text_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
                measurement=measurement,
                assessment=assessment,
                provider_termination=provider_termination,
                provider_finish_reason=provider_finish_reason,
                provider_attempts=progress.provider_attempts,
                runtime_provider_attempts=progress.runtime_provider_attempts,
                continuations_used=progress.continuations_used,
                repetition_retries_used=progress.repetition_retries_used,
                estimated_cost_microunits=progress.estimated_cost_microunits,
            )
        return LengthAssuranceDecision(
            status=status,
            action=action,
            reason=reason,
            measurement=measurement,
            assessment=assessment,
            provider_termination=provider_termination,
            provider_finish_reason=provider_finish_reason,
            provider_normal_stop=provider_termination is ProviderTermination.NORMAL_STOP,
            domain_complete=domain_complete,
            preserve_partial=preserve_partial,
            original_candidate_preserved=original_candidate_preserved,
            budget=budget,
            partial_evidence=partial_evidence,
        )


__all__ = [
    "DEFAULT_ASSURANCE_LIMITS",
    "AssuranceAction",
    "AssuranceBudgetState",
    "AssuranceLimits",
    "AssuranceProgress",
    "AssuranceReason",
    "AssuranceStatus",
    "BudgetBlockReason",
    "LengthAssuranceDecision",
    "LengthAssuranceService",
    "PartialEvidence",
    "ProviderTermination",
]
