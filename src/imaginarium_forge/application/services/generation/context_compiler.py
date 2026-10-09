"""Deterministic context compiler and inspectable evidence.

This compiler is for the new generic authoring contract. Existing Story
``ContextPackage`` values are already authoritative compiled payloads: callers
must project them with:meth:`ContextCompiler.project_story_context`, never run
them through generic selection or compaction a second time.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, Self, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from imaginarium_forge.canonical import canonical_json, sha256_of_canonical
from imaginarium_forge.domain.generation.context_selection import (
    ContextCompactionPolicy,
    ContextSelectionSpec,
    ContextSourceSpec,
    SourceKey,
)

CONTEXT_COMPILER_VERSION = "cwe-2.1-context-compiler-v1"
STORY_CONTEXT_PROJECTION_VERSION = "cwe-2.1-story-context-projection-v1"


class ContextCompilationError(ValueError):
    """Base class for fail-closed context compilation errors."""


class ContextBudgetExceededError(ContextCompilationError):
    """Non-negotiable content does not fit the author-selected budget."""


class ContextSourceResolutionError(ContextCompilationError):
    """Resolved source identities do not match the typed selection contract."""


class StoryContextAlreadyCompiledError(ContextCompilationError):
    """A Story ContextPackage was incorrectly sent to the generic compiler."""


class _StoryContextPackageView(Protocol):
    blocks: object
    excluded: object
    system_message: object
    user_message: object
    fingerprint: object


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _visible_chars(value: str) -> int:
    return sum(not character.isspace() for character in value)


def _slice_visible_chars(value: str, limit: int) -> str:
    """Keep at most ``limit`` non-whitespace code points without hiding edits."""

    if limit <= 0:
        return ""
    visible = 0
    result: list[str] = []
    for character in value:
        if not character.isspace():
            if visible >= limit:
                break
            visible += 1
        result.append(character)
    return "".join(result).rstrip()


class ResolvedContextSource(BaseModel):
    """One typed current source plus an optional persisted rolling summary."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    source_id: str
    source_type: str
    text: str
    persisted_summary: str | None = None

    @field_validator("source_id", "source_type")
    @classmethod
    def _source_component(cls, value: str) -> str:
        normalized = value.strip()
        if (
            not normalized
            or len(normalized) > 200
            or any(
                character.isspace() or ord(character) < 32 or ord(character) == 127
                for character in normalized
            )
        ):
            raise ValueError("source key components must be stable non-blank identifiers")
        return normalized

    @field_validator("text")
    @classmethod
    def _source_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("persisted_summary")
    @classmethod
    def _summary_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @property
    def source_key(self) -> SourceKey:
        return (self.source_type, self.source_id)


class SourceRepresentation(StrEnum):
    FULL = "full"
    PERSISTED_SUMMARY = "persisted_summary"
    TRUNCATED = "truncated"
    OMITTED = "omitted"


class ContextSourceEvidence(BaseModel):
    """Per-source requested-versus-included reconciliation evidence."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    source_id: str
    source_type: str
    priority: int = Field(ge=0)
    required: bool
    requested_visible_chars: int = Field(ge=0)
    included_visible_chars: int = Field(ge=0)
    requested_sha256: str = Field(pattern=r"^(?:|[0-9a-f]{64})$")
    included_sha256: str = Field(pattern=r"^(?:|[0-9a-f]{64})$")
    representation: SourceRepresentation
    used_persisted_summary: bool = False
    reason: str = ""

    @model_validator(mode="after")
    def _representation_matches_counts_and_hashes(self) -> Self:
        requested_is_empty = self.requested_visible_chars == 0
        included_is_empty = self.included_visible_chars == 0
        if requested_is_empty != (self.requested_sha256 == ""):
            raise ValueError("requested source evidence count/hash mismatch")
        if included_is_empty != (self.included_sha256 == ""):
            raise ValueError("included source evidence count/hash mismatch")
        if self.included_visible_chars > self.requested_visible_chars:
            raise ValueError("included source evidence exceeds requested content")
        if self.required and self.representation is not SourceRepresentation.FULL:
            raise ValueError("required source evidence must remain full")

        if self.representation is SourceRepresentation.FULL:
            if (
                included_is_empty
                or self.included_visible_chars != self.requested_visible_chars
                or self.included_sha256 != self.requested_sha256
                or self.used_persisted_summary
                or self.reason
            ):
                raise ValueError("full source evidence is internally inconsistent")
        elif self.representation is SourceRepresentation.PERSISTED_SUMMARY:
            if (
                included_is_empty
                or self.included_visible_chars >= self.requested_visible_chars
                or not self.used_persisted_summary
                or not self.reason
            ):
                raise ValueError("persisted-summary source evidence is inconsistent")
        elif self.representation is SourceRepresentation.TRUNCATED:
            if (
                included_is_empty
                or self.included_visible_chars >= self.requested_visible_chars
                or not self.reason
            ):
                raise ValueError("truncated source evidence is internally inconsistent")
        elif not included_is_empty or self.included_sha256 or not self.reason:
            raise ValueError("omitted source evidence is internally inconsistent")
        return self

    @property
    def source_key(self) -> SourceKey:
        return (self.source_type, self.source_id)


class ContextCompilationReport(BaseModel):
    """Complete R2 requested/included/summarized/truncated evidence."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    compiler_version: str = CONTEXT_COMPILER_VERSION
    selection_sha256: str
    total_budget: int = Field(gt=0)
    budget_used: int = Field(ge=0)
    protected_facts_visible_chars: int = Field(ge=0)
    must_avoid_visible_chars: int = Field(ge=0)
    requested: tuple[ContextSourceEvidence, ...]
    included: tuple[ContextSourceEvidence, ...]
    summarized: tuple[ContextSourceEvidence, ...]
    truncated: tuple[ContextSourceEvidence, ...]

    @model_validator(mode="after")
    def _reconciles_subsets(self) -> Self:
        if self.budget_used > self.total_budget:
            raise ValueError("context report exceeds total_budget")
        requested_by_key = {entry.source_key: entry for entry in self.requested}
        if len(requested_by_key) != len(self.requested):
            raise ValueError("context report requested source keys must be unique")
        for label, entries in (
            ("included", self.included),
            ("summarized", self.summarized),
            ("truncated", self.truncated),
        ):
            if len({entry.source_key for entry in entries}) != len(entries):
                raise ValueError(f"context report {label} source keys must be unique")
            if any(requested_by_key.get(entry.source_key) != entry for entry in entries):
                raise ValueError(f"context report {label} entries must reconcile to requested")
        if any(entry.included_visible_chars == 0 for entry in self.included):
            raise ValueError("included entries must contain visible content")
        if any(not entry.used_persisted_summary for entry in self.summarized):
            raise ValueError("summarized entries must use a persisted summary")
        expected_included = tuple(
            entry for entry in self.requested if entry.included_visible_chars > 0
        )
        if self.included != expected_included:
            raise ValueError("included entries must exactly classify requested sources")
        expected_summarized = tuple(
            entry for entry in self.requested if entry.used_persisted_summary
        )
        if self.summarized != expected_summarized:
            raise ValueError("summarized entries must exactly classify requested sources")
        expected_truncated = tuple(
            entry
            for entry in self.requested
            if entry.representation
            in {SourceRepresentation.TRUNCATED, SourceRepresentation.OMITTED}
        )
        if self.truncated != expected_truncated:
            raise ValueError("truncated entries must exactly classify requested sources")
        expected_budget_used = (
            self.protected_facts_visible_chars
            + self.must_avoid_visible_chars
            + sum(entry.included_visible_chars for entry in self.included)
        )
        if self.budget_used != expected_budget_used:
            raise ValueError("context report budget_used does not match included evidence")
        return self

    def canonical_json(self) -> str:
        return canonical_json(self.model_dump(mode="json"))

    @property
    def sha256(self) -> str:
        return sha256_of_canonical(self.model_dump(mode="json"))


class CompiledContextSection(BaseModel):
    """Exact source text included in a generic context snapshot."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    source_id: str
    source_type: str
    priority: int = Field(ge=0)
    required: bool
    representation: SourceRepresentation
    text: str
    visible_chars: int = Field(gt=0)
    text_sha256: str

    @property
    def source_key(self) -> SourceKey:
        return (self.source_type, self.source_id)

    @model_validator(mode="after")
    def _content_matches_evidence(self) -> Self:
        if self.representation is SourceRepresentation.OMITTED:
            raise ValueError("compiled sections cannot be omitted")
        if self.visible_chars != _visible_chars(self.text):
            raise ValueError("compiled section visible-char count mismatch")
        if self.text_sha256 != _sha256_text(self.text):
            raise ValueError("compiled section hash mismatch")
        return self


class ContextCompilationSnapshot(BaseModel):
    """Durable, canonical snapshot sufficient to reproduce provider context."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    compiler_version: str = CONTEXT_COMPILER_VERSION
    selection: ContextSelectionSpec
    selection_sha256: str
    sections: tuple[CompiledContextSection, ...]
    report: ContextCompilationReport
    rendered_text_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def _selection_and_report_match(self) -> Self:
        if self.selection_sha256 != self.selection.sha256:
            raise ValueError("selection snapshot hash mismatch")
        if self.report.selection_sha256 != self.selection_sha256:
            raise ValueError("report selection hash mismatch")
        if self.report.total_budget != self.selection.total_budget:
            raise ValueError("context snapshot report differs from selection budget")
        protected_chars = sum(_visible_chars(value) for value in self.selection.protected_facts)
        must_avoid_chars = sum(_visible_chars(value) for value in self.selection.must_avoid)
        if (
            self.report.protected_facts_visible_chars != protected_chars
            or self.report.must_avoid_visible_chars != must_avoid_chars
        ):
            raise ValueError("context snapshot report differs from selection constraints")

        ordered_sources = _ordered_specs(self.selection)
        expected_requested_keys = tuple(source.source_key for source in ordered_sources)
        requested_keys = tuple(entry.source_key for entry in self.report.requested)
        if requested_keys != expected_requested_keys:
            raise ValueError("context snapshot report differs from selection sources")
        for source, evidence in zip(ordered_sources, self.report.requested, strict=True):
            if source.priority != evidence.priority or source.required != evidence.required:
                raise ValueError("context snapshot report differs from selection source policy")

        section_keys = tuple(section.source_key for section in self.sections)
        if len(section_keys) != len(set(section_keys)):
            raise ValueError("context snapshot section source keys must be unique")
        included_keys = tuple(entry.source_key for entry in self.report.included)
        if section_keys != included_keys:
            raise ValueError("context snapshot sections do not match report included sources")
        for section, evidence in zip(self.sections, self.report.included, strict=True):
            if (
                section.priority != evidence.priority
                or section.required != evidence.required
                or section.representation is not evidence.representation
                or section.visible_chars != evidence.included_visible_chars
                or section.text_sha256 != evidence.included_sha256
            ):
                raise ValueError("context snapshot section differs from report evidence")

        reconstructed = _render_context(self.selection, self.sections)
        if _sha256_text(reconstructed) != self.rendered_text_sha256:
            raise ValueError("context snapshot rendered text hash mismatch")
        return self

    def canonical_json(self) -> str:
        return canonical_json(self.model_dump(mode="json"))

    @property
    def sha256(self) -> str:
        return sha256_of_canonical(self.model_dump(mode="json"))


class CompiledContext(BaseModel):
    """Rendered generic context, immutable snapshot, and the same report."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    text: str
    snapshot: ContextCompilationSnapshot
    report: ContextCompilationReport

    @model_validator(mode="after")
    def _snapshot_matches_text(self) -> Self:
        if self.report != self.snapshot.report:
            raise ValueError("compiled context report differs from snapshot")
        if _sha256_text(self.text) != self.snapshot.rendered_text_sha256:
            raise ValueError("compiled context rendered text hash mismatch")
        return self

    @property
    def snapshot_sha256(self) -> str:
        return self.snapshot.sha256


@dataclass(slots=True)
class _WorkingSource:
    spec: ContextSourceSpec
    requested_text: str
    persisted_summary: str | None
    included_text: str
    representation: SourceRepresentation
    used_persisted_summary: bool = False
    reason: str = ""

    @property
    def requested_visible_chars(self) -> int:
        return _visible_chars(self.requested_text)

    @property
    def included_visible_chars(self) -> int:
        return _visible_chars(self.included_text)

    def evidence(self) -> ContextSourceEvidence:
        return ContextSourceEvidence(
            source_id=self.spec.source_id,
            source_type=self.spec.source_type,
            priority=self.spec.priority,
            required=self.spec.required,
            requested_visible_chars=self.requested_visible_chars,
            included_visible_chars=self.included_visible_chars,
            requested_sha256=(_sha256_text(self.requested_text) if self.requested_text else ""),
            included_sha256=(_sha256_text(self.included_text) if self.included_text else ""),
            representation=self.representation,
            used_persisted_summary=self.used_persisted_summary,
            reason=self.reason,
        )


class StoryContextBlockEvidence(BaseModel):
    """Read-only evidence projected from one existing Story context block."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    label: str
    priority: int
    source_keys: tuple[SourceKey, ...]
    requested_visible_chars: int = Field(ge=0)
    included_visible_chars: int = Field(ge=0)
    body_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    included: bool
    summarized: bool
    truncated: bool
    reason: str = ""

    @model_validator(mode="after")
    def _status_matches_projected_block(self) -> Self:
        if self.included_visible_chars > self.requested_visible_chars:
            raise ValueError("included story context evidence exceeds requested content")
        if self.included:
            if self.included_visible_chars == 0:
                raise ValueError("included story context evidence must contain content")
            if not self.truncated and (self.included_visible_chars != self.requested_visible_chars):
                raise ValueError("untruncated story context evidence must remain complete")
        elif self.included_visible_chars != 0:
            raise ValueError("excluded story context evidence cannot contain included content")

        expected_summarized = any(
            source_type == "scene_draft_summary" for source_type, _source_id in self.source_keys
        )
        if self.summarized != expected_summarized:
            raise ValueError("story context summarized state differs from source identity")
        expected_truncated = not self.included or bool(self.reason)
        if self.truncated != expected_truncated:
            raise ValueError("story context truncated state differs from inclusion reason")
        if self.truncated and not self.reason:
            raise ValueError("truncated story context evidence requires a reason")
        if not self.truncated and self.reason:
            raise ValueError("untruncated story context evidence cannot have a reason")
        return self


class StoryContextProjectionReport(BaseModel):
    """Existing Story package evidence, without selection or re-rendering."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    requested: tuple[StoryContextBlockEvidence, ...]
    included: tuple[StoryContextBlockEvidence, ...]
    summarized: tuple[StoryContextBlockEvidence, ...]
    truncated: tuple[StoryContextBlockEvidence, ...]

    @model_validator(mode="after")
    def _collections_match_requested_statuses(self) -> Self:
        expected_included = tuple(entry for entry in self.requested if entry.included)
        if self.included != expected_included:
            raise ValueError("story context requested/included classification mismatch")
        expected_summarized = tuple(entry for entry in self.requested if entry.summarized)
        if self.summarized != expected_summarized:
            raise ValueError("story context summarized classification differs from requested")
        expected_truncated = tuple(entry for entry in self.requested if entry.truncated)
        if self.truncated != expected_truncated:
            raise ValueError("story context truncated classification differs from requested")
        return self

    def canonical_json(self) -> str:
        return canonical_json(self.model_dump(mode="json"))

    @property
    def sha256(self) -> str:
        return sha256_of_canonical(self.model_dump(mode="json"))


class StoryContextProjectionSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    projection_version: str = STORY_CONTEXT_PROJECTION_VERSION
    source_fingerprint: str
    system_message_sha256: str
    user_message_sha256: str
    report: StoryContextProjectionReport

    def canonical_json(self) -> str:
        return canonical_json(self.model_dump(mode="json"))

    @property
    def sha256(self) -> str:
        return sha256_of_canonical(self.model_dump(mode="json"))


class StoryContextProjection(BaseModel):
    """Exact existing Story messages plus a non-mutating inspection snapshot."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    system_message: str
    user_message: str
    source_fingerprint: str
    report: StoryContextProjectionReport
    snapshot: StoryContextProjectionSnapshot

    @model_validator(mode="after")
    def _snapshot_matches_messages(self) -> Self:
        if self.source_fingerprint != self.snapshot.source_fingerprint:
            raise ValueError("story context source fingerprint mismatch")
        if _sha256_text(self.system_message) != self.snapshot.system_message_sha256:
            raise ValueError("story context system message hash mismatch")
        if _sha256_text(self.user_message) != self.snapshot.user_message_sha256:
            raise ValueError("story context user message hash mismatch")
        if self.report != self.snapshot.report:
            raise ValueError("story context report mismatch")
        return self

    @property
    def snapshot_sha256(self) -> str:
        return self.snapshot.sha256


def _looks_like_story_context_package(value: object) -> bool:
    return all(
        hasattr(value, attribute)
        for attribute in (
            "blocks",
            "excluded",
            "system_message",
            "user_message",
            "fingerprint",
        )
    )


def _source_index(
    spec: ContextSelectionSpec,
    sources: Sequence[ResolvedContextSource],
) -> dict[SourceKey, ResolvedContextSource]:
    requested_keys = {source.source_key for source in spec.sources}
    resolved: dict[SourceKey, ResolvedContextSource] = {}
    for source in sources:
        key = source.source_key
        if key in resolved:
            raise ContextSourceResolutionError(f"duplicate resolved source: {key!r}")
        if key not in requested_keys:
            raise ContextSourceResolutionError(f"resolved source was not requested: {key!r}")
        resolved[key] = source
    return resolved


def _ordered_specs(spec: ContextSelectionSpec) -> tuple[ContextSourceSpec, ...]:
    original_position = {source.source_key: index for index, source in enumerate(spec.sources)}
    return tuple(
        sorted(
            spec.sources,
            key=lambda source: (source.priority, original_position[source.source_key]),
        )
    )


def _render_context(
    spec: ContextSelectionSpec,
    sections: Sequence[CompiledContextSection],
) -> str:
    parts: list[str] = []
    if spec.protected_facts:
        protected = "\n".join(f"- {fact}" for fact in spec.protected_facts)
        parts.append(f"[PROTECTED FACTS]\n{protected}")
    if spec.must_avoid:
        parts.append("[MUST AVOID]\n" + "\n".join(f"- {item}" for item in spec.must_avoid))
    for section in sections:
        required = "true" if section.required else "false"
        parts.append(
            "[CONTEXT "
            f"{section.source_type}:{section.source_id} "
            f"priority={section.priority} required={required} "
            f"representation={section.representation.value}]\n"
            f"{section.text}"
        )
    return "\n\n".join(parts)


def _story_source_keys(block: object) -> tuple[SourceKey, ...]:
    raw_refs = getattr(block, "source_refs", ())
    if not isinstance(raw_refs, (list, tuple)):
        raise StoryContextAlreadyCompiledError("story context source_refs must be ordered")
    keys: set[SourceKey] = set()
    for reference in raw_refs:
        entity_type = getattr(reference, "entity_type", None)
        entity_id = getattr(reference, "entity_id", None)
        if entity_type is None or not isinstance(entity_id, str):
            raise StoryContextAlreadyCompiledError("story context source ref is malformed")
        raw_type = getattr(entity_type, "value", entity_type)
        source_type = str(raw_type)
        keys.add((source_type, entity_id))
    return tuple(sorted(keys))


def _story_evidence(block: object, *, included: bool) -> StoryContextBlockEvidence:
    label = getattr(block, "label", None)
    body = getattr(block, "body", None)
    priority = getattr(block, "priority", None)
    if not isinstance(label, str) or not isinstance(body, str):
        raise StoryContextAlreadyCompiledError("story context block text is malformed")
    if not isinstance(priority, int):
        raise StoryContextAlreadyCompiledError("story context priority is malformed")
    numeric_priority = int(priority)
    truncation_reason = getattr(block, "truncation_reason", "")
    exclusion_reason = getattr(block, "exclusion_reason", "")
    if not isinstance(truncation_reason, str) or not isinstance(exclusion_reason, str):
        raise StoryContextAlreadyCompiledError("story context block reason is malformed")
    source_keys = _story_source_keys(block)
    summarized = any(source_type == "scene_draft_summary" for source_type, _ in source_keys)
    reason = truncation_reason if included else exclusion_reason
    requested_chars = _visible_chars(body)
    return StoryContextBlockEvidence(
        label=label,
        priority=numeric_priority,
        source_keys=source_keys,
        requested_visible_chars=requested_chars,
        included_visible_chars=requested_chars if included else 0,
        body_sha256=_sha256_text(body),
        included=included,
        summarized=summarized,
        truncated=bool(truncation_reason) if included else True,
        reason=reason,
    )


class ContextCompiler:
    """Compile one explicit selection without provider- or UI-specific state."""

    def compile(
        self,
        spec: ContextSelectionSpec,
        sources: Sequence[ResolvedContextSource],
    ) -> CompiledContext:
        if _looks_like_story_context_package(sources):
            raise StoryContextAlreadyCompiledError(
                "Story ContextPackage is already compiled; use project_story_context"
            )
        resolved_by_key = _source_index(spec, sources)
        working: list[_WorkingSource] = []
        for source_spec in _ordered_specs(spec):
            resolved = resolved_by_key.get(source_spec.source_key)
            if resolved is None:
                if source_spec.required:
                    raise ContextSourceResolutionError(
                        f"required source is unavailable: {source_spec.source_key!r}"
                    )
                working.append(
                    _WorkingSource(
                        spec=source_spec,
                        requested_text="",
                        persisted_summary=None,
                        included_text="",
                        representation=SourceRepresentation.OMITTED,
                        reason="source_unavailable",
                    )
                )
                continue

            requested_chars = _visible_chars(resolved.text)
            if source_spec.required and requested_chars == 0:
                raise ContextSourceResolutionError(
                    f"required source is blank: {source_spec.source_key!r}"
                )
            if requested_chars == 0:
                working.append(
                    _WorkingSource(
                        spec=source_spec,
                        requested_text="",
                        persisted_summary=resolved.persisted_summary,
                        included_text="",
                        representation=SourceRepresentation.OMITTED,
                        reason="source_blank",
                    )
                )
                continue
            if source_spec.required and requested_chars > source_spec.max_visible_chars:
                raise ContextBudgetExceededError(
                    f"required source exceeds max_visible_chars: {source_spec.source_key!r}"
                )
            included_text = _slice_visible_chars(
                resolved.text,
                source_spec.max_visible_chars,
            )
            was_capped = requested_chars > source_spec.max_visible_chars
            working.append(
                _WorkingSource(
                    spec=source_spec,
                    requested_text=resolved.text,
                    persisted_summary=resolved.persisted_summary,
                    included_text=included_text,
                    representation=(
                        SourceRepresentation.TRUNCATED if was_capped else SourceRepresentation.FULL
                    ),
                    reason="source_max_visible_chars" if was_capped else "",
                )
            )

        protected_chars = sum(_visible_chars(value) for value in spec.protected_facts)
        must_avoid_chars = sum(_visible_chars(value) for value in spec.must_avoid)
        reserved_chars = protected_chars + must_avoid_chars
        if reserved_chars > spec.total_budget:
            raise ContextBudgetExceededError("protected_facts and must_avoid exceed total_budget")
        required_chars = sum(
            source.included_visible_chars for source in working if source.spec.required
        )
        if reserved_chars + required_chars > spec.total_budget:
            raise ContextBudgetExceededError("required sources exceed remaining total_budget")

        def budget_used() -> int:
            return reserved_chars + sum(source.included_visible_chars for source in working)

        # R2 compaction order is normative: persisted summaries are considered
        # before any budget truncation, starting with the lowest-priority source.
        if (
            budget_used() > spec.total_budget
            and spec.compaction_policy is ContextCompactionPolicy.PERSISTED_SUMMARY_THEN_PRIORITY
        ):
            for source in reversed(working):
                if source.spec.required or source.persisted_summary is None:
                    continue
                summary = _slice_visible_chars(
                    source.persisted_summary,
                    source.spec.max_visible_chars,
                )
                if _visible_chars(summary) >= source.included_visible_chars:
                    continue
                source.included_text = summary
                source.representation = (
                    SourceRepresentation.PERSISTED_SUMMARY
                    if summary
                    else SourceRepresentation.OMITTED
                )
                source.used_persisted_summary = True
                source.reason = "persisted_summary"
                if budget_used() <= spec.total_budget:
                    break

        if budget_used() > spec.total_budget:
            for source in reversed(working):
                if source.spec.required or not source.included_text:
                    continue
                overflow = budget_used() - spec.total_budget
                if overflow <= 0:
                    break
                keep = max(0, source.included_visible_chars - overflow)
                source.included_text = _slice_visible_chars(source.included_text, keep)
                source.representation = (
                    SourceRepresentation.TRUNCATED
                    if source.included_text
                    else SourceRepresentation.OMITTED
                )
                source.reason = (
                    "total_budget_after_persisted_summary"
                    if source.used_persisted_summary
                    else "total_budget_low_priority"
                )

        final_budget_used = budget_used()
        if final_budget_used > spec.total_budget:  # pragma: no cover - defensive invariant
            raise ContextBudgetExceededError("context compaction did not satisfy total_budget")

        evidence = tuple(source.evidence() for source in working)
        included = tuple(entry for entry in evidence if entry.included_visible_chars > 0)
        summarized = tuple(entry for entry in evidence if entry.used_persisted_summary)
        truncated = tuple(
            entry
            for entry in evidence
            if entry.representation
            in {SourceRepresentation.TRUNCATED, SourceRepresentation.OMITTED}
        )
        report = ContextCompilationReport(
            selection_sha256=spec.sha256,
            total_budget=spec.total_budget,
            budget_used=final_budget_used,
            protected_facts_visible_chars=protected_chars,
            must_avoid_visible_chars=must_avoid_chars,
            requested=evidence,
            included=included,
            summarized=summarized,
            truncated=truncated,
        )
        sections = tuple(
            CompiledContextSection(
                source_id=source.spec.source_id,
                source_type=source.spec.source_type,
                priority=source.spec.priority,
                required=source.spec.required,
                representation=source.representation,
                text=source.included_text,
                visible_chars=source.included_visible_chars,
                text_sha256=_sha256_text(source.included_text),
            )
            for source in working
            if source.included_text
        )
        text = _render_context(spec, sections)
        snapshot = ContextCompilationSnapshot(
            selection=spec,
            selection_sha256=spec.sha256,
            sections=sections,
            report=report,
            rendered_text_sha256=_sha256_text(text),
        )
        return CompiledContext(text=text, snapshot=snapshot, report=report)

    def project_story_context(self, package: object) -> StoryContextProjection:
        """Project an existing Story package without recompiling or re-rendering it."""

        if not _looks_like_story_context_package(package):
            raise StoryContextAlreadyCompiledError("value is not a Story ContextPackage view")
        view = cast(_StoryContextPackageView, package)
        raw_blocks = view.blocks
        raw_excluded = view.excluded
        system_message = view.system_message
        user_message = view.user_message
        source_fingerprint = view.fingerprint
        if not isinstance(raw_blocks, (list, tuple)) or not isinstance(raw_excluded, (list, tuple)):
            raise StoryContextAlreadyCompiledError("story context blocks must be ordered")
        if (
            not isinstance(system_message, str)
            or not isinstance(user_message, str)
            or not isinstance(source_fingerprint, str)
        ):
            raise StoryContextAlreadyCompiledError("story context messages are malformed")

        included = tuple(_story_evidence(block, included=True) for block in raw_blocks)
        excluded = tuple(_story_evidence(block, included=False) for block in raw_excluded)
        requested = included + excluded
        summarized = tuple(entry for entry in requested if entry.summarized)
        truncated = tuple(entry for entry in requested if entry.truncated)
        report = StoryContextProjectionReport(
            requested=requested,
            included=included,
            summarized=summarized,
            truncated=truncated,
        )
        snapshot = StoryContextProjectionSnapshot(
            source_fingerprint=source_fingerprint,
            system_message_sha256=_sha256_text(system_message),
            user_message_sha256=_sha256_text(user_message),
            report=report,
        )
        return StoryContextProjection(
            system_message=system_message,
            user_message=user_message,
            source_fingerprint=source_fingerprint,
            report=report,
            snapshot=snapshot,
        )


__all__ = [
    "CONTEXT_COMPILER_VERSION",
    "STORY_CONTEXT_PROJECTION_VERSION",
    "CompiledContext",
    "CompiledContextSection",
    "ContextBudgetExceededError",
    "ContextCompilationError",
    "ContextCompilationReport",
    "ContextCompilationSnapshot",
    "ContextCompiler",
    "ContextSourceEvidence",
    "ContextSourceResolutionError",
    "ResolvedContextSource",
    "SourceRepresentation",
    "StoryContextAlreadyCompiledError",
    "StoryContextBlockEvidence",
    "StoryContextProjection",
    "StoryContextProjectionReport",
    "StoryContextProjectionSnapshot",
]
