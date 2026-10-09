"""Typed authoring contract over the unchanged candidate facade.

This module is deliberately additive. It compiles the author's explicit
length/context request, passes only the compiled context to the existing
``CreativeGenerationFacade``, and packages deterministic evidence around the
legacy candidate. Provider routing, credential handling, and runtime behavior
remain owned by the legacy facade and runtime.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from enum import StrEnum
from threading import Event
from typing import Literal, Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue, SecretStr, model_validator

from imaginarium_forge.application.services.generation.context_compiler import (
    CompiledContext,
    ContextCompilationReport,
    ContextCompilationSnapshot,
    ContextCompiler,
    ResolvedContextSource,
    SourceRepresentation,
    StoryContextProjection,
    StoryContextProjectionReport,
    StoryContextProjectionSnapshot,
)
from imaginarium_forge.application.services.generation.contracts import (
    GenerationFailure,
    GenerationOperation,
    GenerationProvenance,
    GenerationTask,
)
from imaginarium_forge.application.services.generation.facade import (
    CreativeCandidateAction,
    CreativeCandidateCommand,
    CreativeCandidateResponse,
    CreativeProsePipelineEvidence,
    OfflineCandidateFactory,
)
from imaginarium_forge.application.services.generation.output_measurement import (
    LengthBand,
    OutputLengthAssessment,
    OutputMeasurement,
    measure_output,
)
from imaginarium_forge.application.services.generation.runtime import TextDeltaCallback
from imaginarium_forge.application.services.generation.story_fragment_continuation import (
    StoryFragmentAppendReceipt,
    StoryFragmentContinuationBinding,
    StoryFragmentContinuationEvidence,
    StoryFragmentContinuationProjectionReport,
    StoryFragmentContinuationSource,
    build_story_fragment_append_receipt,
    inspect_story_fragment_suffix,
    prepare_story_fragment_continuation,
    revalidate_story_fragment_projection,
)
from imaginarium_forge.canonical import canonical_json, sha256_of_canonical
from imaginarium_forge.domain.generation.context_selection import ContextSelectionSpec
from imaginarium_forge.domain.generation.intent import GenerationIntent
from imaginarium_forge.domain.generation.length import GenerationLengthSpec
from imaginarium_forge.providers.ollama import (
    OllamaContextProfile,
    OllamaContextProfileDecision,
    resolve_ollama_context_profile,
)

AUTHORING_CONTRACT_INPUT_KEY = "_cwe_2_1_authoring"

STORY_FRAGMENT_PROJECTION_VERSION = "story-fragment-fragment-text-v1"
CHARACTER_DRAFT_PROJECTION_VERSION = "character-draft-personal-story-v1"
WORLD_SEED_PROJECTION_VERSION = "world-seed-plain-body-v1"
WORLD_SEED_PROSE_PROJECTION_VERSION = "world-seed-introduction-v1"

_SUPPORTED_TASKS = frozenset(
    {
        GenerationTask.STORY_FRAGMENT,
        GenerationTask.CHARACTER_DRAFT,
        GenerationTask.WORLD_SEED,
    }
)
_WORLD_BODY_FIELDS = (
    "setting",
    "time_period",
    "world_rules",
    "locations",
    "social_context",
    "technology_or_magic",
    "central_conflict",
    "themes",
)


class ContextInputMismatchError(ValueError):
    """Provider inputs would diverge from the authoritative compiled context."""


class CandidateProjectionError(ValueError):
    """A legacy candidate cannot be projected to the task's explicit prose body."""


class CandidateApplyMismatchError(ValueError):
    """Candidate or authoring evidence changed before the author applied it."""


class AuthoringContextKind(StrEnum):
    GENERIC = "generic"
    STORY_PROJECTION = "story_projection"


class AuthoringCandidateStatus(StrEnum):
    """Candidate readiness distinct from provider transport success."""

    CANDIDATE_READY = "candidate_ready"
    CANDIDATE_OUTSIDE_TARGET_RANGE = "candidate_outside_target_range"
    LENGTH_ACTION_REQUIRED = "length_action_required"
    FAILED = "failed"


class CreativeAuthoringCommand(BaseModel):
    """R2 author intent carried without credentials or live provider objects."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    task: GenerationTask
    intent: GenerationIntent
    operation: GenerationOperation
    action: CreativeCandidateAction
    inputs: dict[str, JsonValue]
    instruction: str = Field(default="", max_length=4_000)
    length_spec: GenerationLengthSpec
    ollama_context_profile: OllamaContextProfileDecision = Field(
        default_factory=lambda: resolve_ollama_context_profile(OllamaContextProfile.AUTO)
    )
    context_spec: ContextSelectionSpec
    idempotency_key: str = Field(min_length=1, max_length=200)
    continuation: StoryFragmentContinuationBinding | None = None

    @model_validator(mode="after")
    def _legacy_adapter_boundary(self) -> Self:
        if self.task is GenerationTask.PROMPT_SCRATCH:
            raise ValueError("Prompt Scratch must bypass the typed long-form adapter")
        if self.task not in _SUPPORTED_TASKS:
            raise ValueError("legacy authoring adapter supports only migrated creative editors")
        if self.operation is not GenerationOperation.STRUCTURED:
            raise ValueError("legacy candidate generation must use the structured operation")
        if AUTHORING_CONTRACT_INPUT_KEY in self.inputs:
            raise ValueError("author inputs must not override the R2 authoring envelope")
        if self.action is CreativeCandidateAction.CONTINUE:
            if self.task is not GenerationTask.STORY_FRAGMENT or self.continuation is None:
                raise ValueError("continuation is story-only and requires a source binding")
        elif self.continuation is not None:
            raise ValueError("continuation binding requires the continuation action")
        return self

    @property
    def canonical_json(self) -> str:
        return canonical_json(
            self.model_dump(
                mode="json",
                exclude={"continuation"} if self.continuation is None else set(),
            )
        )

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.canonical_json.encode("utf-8")).hexdigest()


class CandidateOutputProjection(BaseModel):
    """Exact, versioned candidate body used for output measurement."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    version: str = Field(min_length=1, max_length=100)
    text: str
    text_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def _hash_matches_text(self) -> Self:
        if self.text_sha256 != _sha256_text(self.text):
            raise ValueError("candidate output projection hash mismatch")
        return self


class CandidateOutputEvidence(BaseModel):
    """Reproducible projection, measurement, and exact length assessment."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    projection: CandidateOutputProjection
    measurement: OutputMeasurement
    measurement_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    assessment: OutputLengthAssessment

    @model_validator(mode="after")
    def _measurement_matches_projection(self) -> Self:
        if self.measurement.sha256 != self.measurement_sha256:
            raise ValueError("output measurement hash mismatch")
        expected = measure_output(self.projection.text, unit=self.measurement.unit)
        if self.measurement != expected:
            raise ValueError("output measurement does not match projected candidate text")
        return self


type AuthoringContextSnapshot = ContextCompilationSnapshot | StoryContextProjectionSnapshot
type AuthoringContextReport = ContextCompilationReport | StoryContextProjectionReport


class CreativeAuthoringProvenance(BaseModel):
    """Typed local evidence from the author request through candidate assessment."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    command_snapshot: CreativeAuthoringCommand
    command_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    task: GenerationTask
    intent: GenerationIntent
    operation: GenerationOperation
    action: CreativeCandidateAction
    length_spec: GenerationLengthSpec
    length_spec_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    ollama_context_profile: OllamaContextProfileDecision
    ollama_context_profile_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    context_spec: ContextSelectionSpec
    context_spec_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    context_kind: AuthoringContextKind
    context_snapshot: AuthoringContextSnapshot
    context_snapshot_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    context_report: AuthoringContextReport
    context_report_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    provider: GenerationProvenance
    candidate_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    output: CandidateOutputEvidence | None = None
    condensed_candidate_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    condensed_output: CandidateOutputEvidence | None = None
    prose_pipeline: CreativeProsePipelineEvidence | None = None
    continuation_report: StoryFragmentContinuationProjectionReport | None = None
    continuation_evidence: StoryFragmentContinuationEvidence | None = None

    @model_validator(mode="after")
    def _snapshots_are_self_consistent(self) -> Self:
        if self.command_sha256 != self.command_snapshot.sha256:
            raise ValueError("authoring command hash mismatch")
        if (
            self.task is not self.command_snapshot.task
            or self.intent is not self.command_snapshot.intent
            or self.operation is not self.command_snapshot.operation
            or self.action is not self.command_snapshot.action
            or self.length_spec != self.command_snapshot.length_spec
            or self.ollama_context_profile != self.command_snapshot.ollama_context_profile
            or self.context_spec != self.command_snapshot.context_spec
        ):
            raise ValueError("authoring provenance differs from its command snapshot")
        if self.length_spec_sha256 != self.length_spec.sha256:
            raise ValueError("length spec hash mismatch")
        if self.ollama_context_profile_sha256 != sha256_of_canonical(
            self.ollama_context_profile.model_dump(mode="json")
        ):
            raise ValueError("Ollama context profile hash mismatch")
        if self.context_spec_sha256 != self.context_spec.sha256:
            raise ValueError("context spec hash mismatch")
        if self.context_snapshot_sha256 != self.context_snapshot.sha256:
            raise ValueError("context snapshot hash mismatch")
        if self.context_report_sha256 != self.context_report.sha256:
            raise ValueError("context report hash mismatch")

        if self.context_kind is AuthoringContextKind.GENERIC:
            if not isinstance(self.context_snapshot, ContextCompilationSnapshot) or not isinstance(
                self.context_report, ContextCompilationReport
            ):
                raise ValueError("generic context kind requires generic snapshot and report")
            if self.continuation_report is not None:
                report = self.continuation_report
                if (
                    self.command_snapshot.continuation != report.binding
                    or self.context_snapshot.selection.sha256 != report.derived_context_spec_sha256
                    or self.context_spec_sha256 != report.original_context_spec_sha256
                    or self.length_spec_sha256 != report.length_spec_sha256
                ):
                    raise ValueError("continuation projection differs from author selection")
                tail = next(
                    (
                        section
                        for section in self.context_snapshot.sections
                        if section.source_key
                        == (
                            report.binding.source_type,
                            report.binding.source_id,
                        )
                    ),
                    None,
                )
                if tail is None or tail.text_sha256 != report.tail_sha256 or not tail.required:
                    raise ValueError("continuation tail differs from its compiled evidence")
            elif self.context_snapshot.selection != self.context_spec:
                raise ValueError("generic context snapshot uses a different selection spec")
            if self.context_snapshot.report != self.context_report:
                raise ValueError("generic context snapshot uses a different report")
        else:
            if not isinstance(
                self.context_snapshot, StoryContextProjectionSnapshot
            ) or not isinstance(self.context_report, StoryContextProjectionReport):
                raise ValueError("Story context kind requires projected snapshot and report")
            if self.context_snapshot.report != self.context_report:
                raise ValueError("Story context snapshot uses a different report")

        if (self.candidate_sha256 is None) != (self.output is None):
            raise ValueError("candidate hash and output evidence must be present together")
        if self.command_snapshot.continuation is not None:
            if self.continuation_report is None:
                raise ValueError("continuation projection evidence is missing")
            if self.output is not None and self.continuation_evidence is None:
                raise ValueError("continuation output lacks replay evidence")
        elif self.continuation_report is not None or self.continuation_evidence is not None:
            raise ValueError("non-continuation has continuation evidence")
        if self.continuation_evidence is not None:
            if self.continuation_evidence.binding != self.command_snapshot.continuation:
                raise ValueError("continuation evidence binding mismatch")
            for output, guard in (
                (self.output, self.continuation_evidence.accumulated_guard),
                (self.condensed_output, self.continuation_evidence.condensed_guard),
            ):
                if output is not None and (
                    guard is None
                    or not guard.accepted
                    or guard.suffix_sha256 != output.projection.text_sha256
                ):
                    raise ValueError("continuation output differs from replay evidence")
        if self.output is not None:
            if self.output.measurement.unit is not self.length_spec.unit:
                raise ValueError("output measurement uses a different length unit")
            if self.output.measurement.assess(self.length_spec) != self.output.assessment:
                raise ValueError("output assessment uses a different length spec")
        if (self.condensed_candidate_sha256 is None) != (self.condensed_output is None):
            raise ValueError("condensed candidate hash and output must be present together")
        if self.condensed_output is not None:
            if self.condensed_output.measurement.unit is not self.length_spec.unit:
                raise ValueError("condensed output uses a different length unit")
            if (
                self.condensed_output.measurement.assess(self.length_spec)
                != self.condensed_output.assessment
            ):
                raise ValueError("condensed assessment uses a different length spec")
        if self.prose_pipeline is not None:
            expected_literal_facts_policy = self.task is not GenerationTask.CHARACTER_DRAFT
            if (
                self.prose_pipeline.literal_protected_facts_required
                != expected_literal_facts_policy
            ):
                raise ValueError("prose literal-facts policy differs from its authoring task")
            if self.output is not None and (
                self.prose_pipeline.accumulated_text_sha256 != self.output.projection.text_sha256
            ):
                raise ValueError("prose pipeline belongs to another accumulated candidate")
            if self.condensed_output is not None and (
                self.prose_pipeline.condensed_text_sha256
                != self.condensed_output.projection.text_sha256
            ):
                raise ValueError("prose pipeline belongs to another condensed candidate")
        return self


class CreativeAuthoringResponse(BaseModel):
    """Candidate-first response that never equates provider STOP with length success."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    status: AuthoringCandidateStatus
    candidate: dict[str, JsonValue] | None = None
    condensed_candidate: dict[str, JsonValue] | None = None
    failure: GenerationFailure | None = None
    provenance: CreativeAuthoringProvenance

    @model_validator(mode="after")
    def _terminal_shape_and_candidate_hash(self) -> Self:
        if self.status is AuthoringCandidateStatus.FAILED:
            if (
                self.candidate is not None
                or self.condensed_candidate is not None
                or self.failure is None
            ):
                raise ValueError("failed authoring response requires only a failure")
            if self.provenance.candidate_sha256 is not None or self.provenance.output is not None:
                raise ValueError("failed authoring response cannot carry output evidence")
            return self

        if self.candidate is None or self.failure is not None or self.provenance.output is None:
            raise ValueError("candidate response requires candidate and output evidence")
        if sha256_of_canonical(self.candidate) != self.provenance.candidate_sha256:
            raise ValueError("candidate hash mismatch")
        if self.condensed_candidate is not None:
            if (
                sha256_of_canonical(self.condensed_candidate)
                != self.provenance.condensed_candidate_sha256
                or self.provenance.condensed_output is None
            ):
                raise ValueError("condensed candidate hash or evidence mismatch")
        elif self.provenance.condensed_candidate_sha256 is not None:
            raise ValueError("condensed candidate evidence has no candidate")
        expected_status = _status_for(
            self.provenance.output.assessment,
            self.provenance.prose_pipeline,
        )
        if self.status is not expected_status:
            raise ValueError("candidate status does not match its length assessment")
        return self


class CandidateApplyValidation(BaseModel):
    """Read-only evidence returned after an apply-time fail-closed revalidation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    candidate_variant: Literal["accumulated", "condensed"] = "accumulated"
    candidate_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    length_spec_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    context_spec_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    context_report_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    measurement: OutputMeasurement
    assessment: OutputLengthAssessment


class _LegacyCandidateFacade(Protocol):
    def generate_candidate(
        self,
        command: CreativeCandidateCommand,
        *,
        offline_candidate: OfflineCandidateFactory | None = None,
        session_override: SecretStr | str | None = None,
        on_delta: TextDeltaCallback | None = None,
        cancel: Event | None = None,
        continuation_source: StoryFragmentContinuationSource | None = None,
    ) -> CreativeCandidateResponse: ...


@dataclass(frozen=True, slots=True)
class _PreparedContext:
    kind: AuthoringContextKind
    snapshot: AuthoringContextSnapshot
    report: AuthoringContextReport
    provider_payload: dict[str, JsonValue]
    continuation_report: StoryFragmentContinuationProjectionReport | None = None

    @property
    def snapshot_sha256(self) -> str:
        return self.snapshot.sha256

    @property
    def report_sha256(self) -> str:
        return self.report.sha256


class LegacyCreativeCandidateAdapter:
    """Compile N2 contracts, then delegate exactly once to the legacy facade."""

    def __init__(
        self,
        legacy_facade: _LegacyCandidateFacade,
        context_compiler: ContextCompiler | None = None,
    ) -> None:
        self._legacy_facade = legacy_facade
        self._context_compiler = context_compiler or ContextCompiler()

    def generate_candidate(
        self,
        command: CreativeAuthoringCommand,
        *,
        context_sources: Sequence[ResolvedContextSource] = (),
        story_context: object | None = None,
        offline_candidate: OfflineCandidateFactory | None = None,
        session_override: SecretStr | str | None = None,
        on_delta: TextDeltaCallback | None = None,
        cancel: Event | None = None,
        continuation_source: StoryFragmentContinuationSource | None = None,
    ) -> CreativeAuthoringResponse:
        """Generate one candidate without changing provider or runtime behavior."""

        prepared = self._prepare_context(
            command,
            context_sources=context_sources,
            story_context=story_context,
            continuation_source=continuation_source,
        )
        legacy_inputs = deepcopy(command.inputs)
        envelope: dict[str, JsonValue] = {
            "intent": command.intent.value,
            "operation": command.operation.value,
            "length_spec": command.length_spec.model_dump(mode="json"),
            "length_spec_sha256": command.length_spec.sha256,
            "ollama_context_profile": command.ollama_context_profile.model_dump(mode="json"),
            "ollama_context_profile_sha256": sha256_of_canonical(
                command.ollama_context_profile.model_dump(mode="json")
            ),
            "context_spec_sha256": command.context_spec.sha256,
            "protected_facts": list(command.context_spec.protected_facts),
            "must_avoid": list(command.context_spec.must_avoid),
            "context": prepared.provider_payload,
        }
        if prepared.continuation_report is not None:
            envelope["continuation"] = prepared.continuation_report.model_dump(mode="json")
        legacy_inputs[AUTHORING_CONTRACT_INPUT_KEY] = envelope
        legacy_command = CreativeCandidateCommand(
            task=command.task,
            action=command.action,
            inputs=legacy_inputs,
            instruction=command.instruction,
            idempotency_key=command.idempotency_key,
        )
        if command.action is CreativeCandidateAction.CONTINUE:
            legacy_response = self._legacy_facade.generate_candidate(
                legacy_command,
                offline_candidate=offline_candidate,
                session_override=session_override,
                on_delta=on_delta,
                cancel=cancel,
                continuation_source=continuation_source,
            )
        elif on_delta is None and cancel is None:
            legacy_response = self._legacy_facade.generate_candidate(
                legacy_command,
                offline_candidate=offline_candidate,
                session_override=session_override,
            )
        else:
            legacy_response = self._legacy_facade.generate_candidate(
                legacy_command,
                offline_candidate=offline_candidate,
                session_override=session_override,
                on_delta=on_delta,
                cancel=cancel,
            )

        if legacy_response.candidate is None:
            assert legacy_response.failure is not None
            provenance = self._provenance(
                command,
                prepared,
                provider=legacy_response.provenance,
                prose_pipeline=legacy_response.prose_pipeline,
                continuation_evidence=legacy_response.continuation_evidence,
            )
            return CreativeAuthoringResponse(
                status=AuthoringCandidateStatus.FAILED,
                failure=legacy_response.failure,
                provenance=provenance,
            )

        candidate = deepcopy(legacy_response.candidate)
        output = _candidate_output_evidence(
            command.task,
            candidate,
            length_spec=command.length_spec,
            world_seed_prose=legacy_response.prose_pipeline is not None,
        )
        assessment = output.assessment
        condensed_candidate = (
            deepcopy(legacy_response.condensed_candidate)
            if legacy_response.condensed_candidate is not None
            else None
        )
        condensed_output = (
            _candidate_output_evidence(
                command.task,
                condensed_candidate,
                length_spec=command.length_spec,
                world_seed_prose=legacy_response.prose_pipeline is not None,
            )
            if condensed_candidate is not None
            else None
        )
        provenance = self._provenance(
            command,
            prepared,
            provider=legacy_response.provenance,
            candidate_sha256=sha256_of_canonical(candidate),
            output=output,
            condensed_candidate_sha256=(
                sha256_of_canonical(condensed_candidate)
                if condensed_candidate is not None
                else None
            ),
            condensed_output=condensed_output,
            prose_pipeline=legacy_response.prose_pipeline,
            continuation_evidence=legacy_response.continuation_evidence,
        )
        return CreativeAuthoringResponse(
            status=_status_for(assessment, legacy_response.prose_pipeline),
            candidate=candidate,
            condensed_candidate=condensed_candidate,
            provenance=provenance,
        )

    def _prepare_context(
        self,
        command: CreativeAuthoringCommand,
        *,
        context_sources: Sequence[ResolvedContextSource],
        story_context: object | None,
        continuation_source: StoryFragmentContinuationSource | None = None,
    ) -> _PreparedContext:
        if command.action is CreativeCandidateAction.CONTINUE:
            if (
                continuation_source is None
                or command.continuation != continuation_source.binding
                or story_context is not None
            ):
                raise ContextInputMismatchError("continuation source or context binding mismatch")
            prepared = prepare_story_fragment_continuation(
                continuation_source,
                length_spec=command.length_spec,
                context_spec=command.context_spec,
                context_sources=context_sources,
            )
            raw = continuation_source.text.strip()
            if any(
                raw in leaf for leaf in _provider_text_leaves(command.inputs, command.instruction)
            ):
                raise ContextInputMismatchError("raw continuation original must not enter inputs")
            compiled = self._context_compiler.compile(
                prepared.context_spec,
                prepared.context_sources,
            )
            _reject_reintroduced_compacted_sources(
                command.inputs,
                command.instruction,
                compiled,
                prepared.context_sources,
            )
            generic = _prepared_generic_context(compiled)
            return _PreparedContext(
                kind=generic.kind,
                snapshot=generic.snapshot,
                report=generic.report,
                provider_payload=generic.provider_payload,
                continuation_report=prepared.report,
            )
        if continuation_source is not None:
            raise ContextInputMismatchError("source provided for a non-continuation action")
        if story_context is None:
            compiled = self._context_compiler.compile(command.context_spec, context_sources)
            _reject_reintroduced_compacted_sources(
                command.inputs,
                command.instruction,
                compiled,
                context_sources,
            )
            return _prepared_generic_context(compiled)

        if context_sources:
            raise ContextInputMismatchError(
                "Story ContextPackage cannot be combined with parallel generic sources"
            )
        package_budget = getattr(story_context, "max_chars", None)
        if package_budget is not None and package_budget != command.context_spec.total_budget:
            raise ContextInputMismatchError(
                "Story ContextPackage budget differs from the typed context budget"
            )
        _reject_reintroduced_story_exclusions(
            command.inputs,
            command.instruction,
            story_context,
        )
        projection = self._context_compiler.project_story_context(story_context)
        return _prepared_story_context(projection)

    @staticmethod
    def _provenance(
        command: CreativeAuthoringCommand,
        prepared: _PreparedContext,
        *,
        provider: GenerationProvenance,
        candidate_sha256: str | None = None,
        output: CandidateOutputEvidence | None = None,
        condensed_candidate_sha256: str | None = None,
        condensed_output: CandidateOutputEvidence | None = None,
        prose_pipeline: CreativeProsePipelineEvidence | None = None,
        continuation_evidence: StoryFragmentContinuationEvidence | None = None,
    ) -> CreativeAuthoringProvenance:
        return CreativeAuthoringProvenance(
            command_snapshot=command,
            command_sha256=command.sha256,
            task=command.task,
            intent=command.intent,
            operation=command.operation,
            action=command.action,
            length_spec=command.length_spec,
            length_spec_sha256=command.length_spec.sha256,
            ollama_context_profile=command.ollama_context_profile,
            ollama_context_profile_sha256=sha256_of_canonical(
                command.ollama_context_profile.model_dump(mode="json")
            ),
            context_spec=command.context_spec,
            context_spec_sha256=command.context_spec.sha256,
            context_kind=prepared.kind,
            context_snapshot=prepared.snapshot,
            context_snapshot_sha256=prepared.snapshot_sha256,
            context_report=prepared.report,
            context_report_sha256=prepared.report_sha256,
            provider=provider,
            candidate_sha256=candidate_sha256,
            output=output,
            condensed_candidate_sha256=condensed_candidate_sha256,
            condensed_output=condensed_output,
            prose_pipeline=prose_pipeline,
            continuation_report=prepared.continuation_report,
            continuation_evidence=continuation_evidence,
        )


def _candidate_output_evidence(
    task: GenerationTask,
    candidate: Mapping[str, object],
    *,
    length_spec: GenerationLengthSpec,
    world_seed_prose: bool = False,
) -> CandidateOutputEvidence:
    projection = project_candidate_output(
        task,
        candidate,
        world_seed_prose=world_seed_prose,
    )
    measurement = measure_output(projection.text, unit=length_spec.unit)
    return CandidateOutputEvidence(
        projection=projection,
        measurement=measurement,
        measurement_sha256=measurement.sha256,
        assessment=measurement.assess(length_spec),
    )


def project_candidate_output(
    task: GenerationTask,
    candidate: Mapping[str, object],
    *,
    world_seed_prose: bool = False,
) -> CandidateOutputProjection:
    """Project the task's explicit prose body without counting schema decoration."""

    if task is GenerationTask.STORY_FRAGMENT:
        text = _required_candidate_text(candidate, "fragment_text")
        version = STORY_FRAGMENT_PROJECTION_VERSION
    elif task is GenerationTask.CHARACTER_DRAFT:
        text = _required_candidate_text(candidate, "personal_story")
        version = CHARACTER_DRAFT_PROJECTION_VERSION
    elif task is GenerationTask.WORLD_SEED:
        if world_seed_prose:
            text = _required_candidate_text(candidate, "setting")
            version = WORLD_SEED_PROSE_PROJECTION_VERSION
        else:
            text = _world_plain_text_body(candidate)
            version = WORLD_SEED_PROJECTION_VERSION
    else:
        raise CandidateProjectionError(f"task has no N2 output projection: {task.value}")
    return CandidateOutputProjection(
        version=version,
        text=text,
        text_sha256=_sha256_text(text),
    )


def revalidate_candidate_for_apply(
    response: CreativeAuthoringResponse,
    *,
    expected_command_sha256: str,
    expected_length_spec: GenerationLengthSpec,
    expected_context_spec: ContextSelectionSpec,
    expected_context_report_sha256: str,
    candidate_variant: Literal["accumulated", "condensed"] = "accumulated",
) -> CandidateApplyValidation:
    """Fail closed if apply-time candidate or contract evidence has drifted."""

    # Re-parse the complete response so ``model_copy(update=...)`` cannot bypass
    # either terminal-shape or nested evidence validators.
    try:
        validated_response = CreativeAuthoringResponse.model_validate(
            response.model_dump(mode="python")
        )
    except ValueError as error:
        raise CandidateApplyMismatchError("authoring response is invalid") from error
    if validated_response.candidate is None or validated_response.provenance.output is None:
        raise CandidateApplyMismatchError("candidate is unavailable for apply")
    provenance = validated_response.provenance

    if provenance.command_sha256 != expected_command_sha256:
        raise CandidateApplyMismatchError("authoring command changed before apply")
    if provenance.length_spec != expected_length_spec:
        raise CandidateApplyMismatchError("length spec changed before apply")
    if provenance.context_spec != expected_context_spec:
        raise CandidateApplyMismatchError("context spec changed before apply")
    if provenance.context_report_sha256 != expected_context_report_sha256:
        raise CandidateApplyMismatchError("context report changed before apply")
    if provenance.context_report.sha256 != expected_context_report_sha256:
        raise CandidateApplyMismatchError("context report hash is invalid")
    selected_candidate = (
        validated_response.condensed_candidate
        if candidate_variant == "condensed"
        else validated_response.candidate
    )
    output = provenance.condensed_output if candidate_variant == "condensed" else provenance.output
    expected_candidate_sha256 = (
        provenance.condensed_candidate_sha256
        if candidate_variant == "condensed"
        else provenance.candidate_sha256
    )
    if selected_candidate is None or output is None or expected_candidate_sha256 is None:
        raise CandidateApplyMismatchError("selected candidate variant is unavailable for apply")
    if output is None:  # pragma: no cover - guarded by response shape and re-parse
        raise CandidateApplyMismatchError("candidate output evidence is unavailable")

    candidate_sha256 = sha256_of_canonical(selected_candidate)
    if candidate_sha256 != expected_candidate_sha256:
        raise CandidateApplyMismatchError("candidate hash changed before apply")
    try:
        projection = project_candidate_output(
            provenance.task,
            selected_candidate,
            world_seed_prose=(
                provenance.task is GenerationTask.WORLD_SEED
                and provenance.prose_pipeline is not None
            ),
        )
    except (TypeError, ValueError) as error:
        raise CandidateApplyMismatchError("candidate output projection is invalid") from error
    if projection != output.projection:
        raise CandidateApplyMismatchError("candidate output projection changed before apply")
    measurement = measure_output(projection.text, unit=expected_length_spec.unit)
    if measurement != output.measurement:
        raise CandidateApplyMismatchError("candidate measurement changed before apply")
    if measurement.sha256 != output.measurement_sha256:
        raise CandidateApplyMismatchError("candidate measurement hash is invalid")
    assessment = measurement.assess(expected_length_spec)
    if assessment != output.assessment:
        raise CandidateApplyMismatchError("candidate assessment changed before apply")
    if candidate_variant == "accumulated" and validated_response.status is not _status_for(
        assessment,
        provenance.prose_pipeline,
    ):
        raise CandidateApplyMismatchError("candidate status changed before apply")

    return CandidateApplyValidation(
        candidate_variant=candidate_variant,
        candidate_sha256=candidate_sha256,
        length_spec_sha256=expected_length_spec.sha256,
        context_spec_sha256=expected_context_spec.sha256,
        context_report_sha256=expected_context_report_sha256,
        measurement=measurement,
        assessment=assessment,
    )


def revalidate_story_fragment_continuation_for_append(
    response: CreativeAuthoringResponse,
    *,
    source: StoryFragmentContinuationSource,
    expected_command_sha256: str,
    expected_length_spec: GenerationLengthSpec,
    expected_context_spec: ContextSelectionSpec,
    expected_context_report_sha256: str,
    candidate_variant: Literal["accumulated", "condensed"] = "accumulated",
    accept_partial: bool = False,
) -> StoryFragmentAppendReceipt:
    """Revalidate only a new suffix; never apply metadata or save the draft."""
    validation = revalidate_candidate_for_apply(
        response,
        expected_command_sha256=expected_command_sha256,
        expected_length_spec=expected_length_spec,
        expected_context_spec=expected_context_spec,
        expected_context_report_sha256=expected_context_report_sha256,
        candidate_variant=candidate_variant,
    )
    provenance = response.provenance
    evidence = provenance.continuation_evidence
    pipeline = provenance.prose_pipeline
    if (
        provenance.action is not CreativeCandidateAction.CONTINUE
        or provenance.task is not GenerationTask.STORY_FRAGMENT
        or provenance.command_snapshot.continuation != source.binding
        or evidence is None
        or evidence.binding != source.binding
        or pipeline is None
    ):
        raise CandidateApplyMismatchError("continuation source or evidence changed")
    if provenance.continuation_report is None:
        raise CandidateApplyMismatchError("continuation projection evidence is missing")
    revalidate_story_fragment_projection(source, provenance.continuation_report)
    condensed = candidate_variant == "condensed"
    output = provenance.condensed_output if condensed else provenance.output
    guard = evidence.condensed_guard if condensed else evidence.accumulated_guard
    facts = (
        pipeline.condensed_protected_facts if condensed else pipeline.accumulated_protected_facts
    )
    decision = pipeline.condensation_decision if condensed else pipeline.decision
    if (
        output is None
        or guard is None
        or decision is None
        or facts is None
        or not facts.literal_gate_passed
        or not guard.accepted
    ):
        raise CandidateApplyMismatchError("continuation requires protected-facts or replay review")
    if inspect_story_fragment_suffix(source, output.projection.text) != guard:
        raise CandidateApplyMismatchError("continuation replay evidence changed")
    complete = decision.domain_complete and validation.assessment.band is LengthBand.WITHIN
    if not complete and not accept_partial:
        raise CandidateApplyMismatchError(
            "partial continuation requires distinct explicit acceptance"
        )
    return build_story_fragment_append_receipt(
        source,
        suffix_text=output.projection.text,
        candidate_sha256=validation.candidate_sha256,
    )


def _prepared_generic_context(compiled: CompiledContext) -> _PreparedContext:
    return _PreparedContext(
        kind=AuthoringContextKind.GENERIC,
        snapshot=compiled.snapshot,
        report=compiled.report,
        provider_payload={
            "kind": AuthoringContextKind.GENERIC.value,
            "compiled_text": compiled.text,
            "snapshot_sha256": compiled.snapshot_sha256,
            "report_sha256": compiled.report.sha256,
        },
    )


def _prepared_story_context(projection: StoryContextProjection) -> _PreparedContext:
    return _PreparedContext(
        kind=AuthoringContextKind.STORY_PROJECTION,
        snapshot=projection.snapshot,
        report=projection.report,
        provider_payload={
            "kind": AuthoringContextKind.STORY_PROJECTION.value,
            "system_message": projection.system_message,
            "user_message": projection.user_message,
            "source_fingerprint": projection.source_fingerprint,
            "snapshot_sha256": projection.snapshot_sha256,
            "report_sha256": projection.report.sha256,
        },
    )


def _reject_reintroduced_compacted_sources(
    inputs: Mapping[str, JsonValue],
    instruction: str,
    compiled: CompiledContext,
    sources: Sequence[ResolvedContextSource],
) -> None:
    leaves = _provider_text_leaves(inputs, instruction)
    evidence_by_key = {entry.source_key: entry for entry in compiled.report.requested}
    for source in sources:
        evidence = evidence_by_key[source.source_key]
        if evidence.representation is SourceRepresentation.FULL:
            continue
        raw_text = _normalized_leaf(source.text)
        if raw_text and any(raw_text in leaf for leaf in leaves):
            raise ContextInputMismatchError(
                "raw source compacted by the compiled context is still present in legacy inputs"
            )


def _reject_reintroduced_story_exclusions(
    inputs: Mapping[str, JsonValue],
    instruction: str,
    story_context: object,
) -> None:
    leaves = _provider_text_leaves(inputs, instruction)
    excluded = getattr(story_context, "excluded", ())
    if not isinstance(excluded, (list, tuple)):
        raise ContextInputMismatchError("Story ContextPackage exclusions are malformed")
    for block in excluded:
        body = getattr(block, "body", None)
        raw_text = _normalized_leaf(body) if isinstance(body, str) else ""
        if raw_text and any(raw_text in leaf for leaf in leaves):
            raise ContextInputMismatchError(
                "raw source excluded by Story context is still present in legacy inputs"
            )


def _json_text_leaves(value: Mapping[str, JsonValue] | JsonValue) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from _json_text_leaves(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from _json_text_leaves(item)


def _provider_text_leaves(
    inputs: Mapping[str, JsonValue],
    instruction: str,
) -> tuple[str, ...]:
    return tuple(_normalized_leaf(value) for value in (*_json_text_leaves(inputs), instruction))


def _normalized_leaf(value: str) -> str:
    return value.strip()


def _required_candidate_text(candidate: Mapping[str, object], field_name: str) -> str:
    value = candidate.get(field_name)
    if not isinstance(value, str):
        raise CandidateProjectionError(f"candidate field is not text: {field_name}")
    return value


def _world_plain_text_body(candidate: Mapping[str, object]) -> str:
    lines: list[str] = []
    for field_name in _WORLD_BODY_FIELDS:
        value = candidate.get(field_name)
        if isinstance(value, str):
            normalized = _one_line(value)
            if normalized:
                lines.append(normalized)
            continue
        if isinstance(value, (list, tuple)):
            for item in value:
                if not isinstance(item, str):
                    raise CandidateProjectionError(
                        f"world candidate list contains non-text: {field_name}"
                    )
                normalized = _one_line(item)
                if normalized:
                    lines.append(normalized)
            continue
        if value is not None:
            raise CandidateProjectionError(f"world candidate field has invalid type: {field_name}")
    return "\n".join(lines)


def _one_line(value: str) -> str:
    return " ".join(value.split())


def _status_for(
    assessment: OutputLengthAssessment,
    prose_pipeline: CreativeProsePipelineEvidence | None = None,
) -> AuthoringCandidateStatus:
    if prose_pipeline is not None and not prose_pipeline.decision.domain_complete:
        return AuthoringCandidateStatus.LENGTH_ACTION_REQUIRED
    if assessment.band is LengthBand.WITHIN:
        return AuthoringCandidateStatus.CANDIDATE_READY
    if not assessment.satisfied:
        return AuthoringCandidateStatus.LENGTH_ACTION_REQUIRED
    return AuthoringCandidateStatus.CANDIDATE_OUTSIDE_TARGET_RANGE


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


__all__ = [
    "AUTHORING_CONTRACT_INPUT_KEY",
    "CHARACTER_DRAFT_PROJECTION_VERSION",
    "STORY_FRAGMENT_PROJECTION_VERSION",
    "WORLD_SEED_PROJECTION_VERSION",
    "WORLD_SEED_PROSE_PROJECTION_VERSION",
    "AuthoringCandidateStatus",
    "AuthoringContextKind",
    "CandidateApplyMismatchError",
    "CandidateApplyValidation",
    "CandidateOutputEvidence",
    "CandidateOutputProjection",
    "CandidateProjectionError",
    "ContextInputMismatchError",
    "CreativeAuthoringCommand",
    "CreativeAuthoringProvenance",
    "CreativeAuthoringResponse",
    "LegacyCreativeCandidateAdapter",
    "project_candidate_output",
    "revalidate_candidate_for_apply",
    "revalidate_story_fragment_continuation_for_append",
]
