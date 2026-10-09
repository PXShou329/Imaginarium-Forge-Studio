"""Story-only exact-source tail projection and explicit append contracts.

Raw author text is transient. Only bounded tail context reaches generation;
only hash/count/identity evidence may enter durable command snapshots.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from imaginarium_forge.application.services.generation.context_compiler import ResolvedContextSource
from imaginarium_forge.application.services.generation.repetition_guard import (
    ContinuationRepetitionGuard,
    RepetitionGuardReport,
)
from imaginarium_forge.canonical import sha256_of_canonical
from imaginarium_forge.domain.generation.context_selection import ContextSelectionSpec
from imaginarium_forge.domain.generation.length import GenerationLengthSpec, LengthUnit

_Hash = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
_Identity = Annotated[str, Field(min_length=1, max_length=200)]
MAX_COMBINED_RAW_CHARS = 500_000
MAX_SUFFIX_RAW_CHARS = 100_000
MAX_TAIL_RAW_CHARS = 12_000
MAX_TAIL_UTF8_BYTES = 24_000


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _visible(text: str) -> int:
    return sum(not character.isspace() for character in text)


class _Evidence(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)


class StoryFragmentContinuationBinding(_Evidence):
    source_sha256: _Hash
    source_raw_chars: int = Field(gt=0, le=MAX_COMBINED_RAW_CHARS)
    editor_identity: _Identity
    editor_instance_id: _Identity
    source_id: _Identity = "fragment_text"
    source_type: _Identity = "story_fragment_field"


class StoryFragmentContinuationSource(_Evidence):
    text: str = Field(exclude=True, repr=False, min_length=1, max_length=MAX_COMBINED_RAW_CHARS)
    binding: StoryFragmentContinuationBinding

    @classmethod
    def capture(
        cls,
        text: str,
        *,
        editor_identity: str,
        editor_instance_id: str,
        source_id: str = "fragment_text",
        source_type: str = "story_fragment_field",
    ) -> Self:
        return cls(
            text=text,
            binding=StoryFragmentContinuationBinding(
                source_sha256=_sha(text),
                source_raw_chars=len(text),
                editor_identity=editor_identity,
                editor_instance_id=editor_instance_id,
                source_id=source_id,
                source_type=source_type,
            ),
        )

    @model_validator(mode="after")
    def _exact_source(self) -> Self:
        if (
            not self.text.strip()
            or _sha(self.text) != self.binding.source_sha256
            or len(self.text) != self.binding.source_raw_chars
        ):
            raise ValueError("continuation source binding mismatch or empty source")
        return self


class StoryFragmentContinuationProjectionReport(_Evidence):
    policy_version: Literal["story-fragment-tail-v1"] = "story-fragment-tail-v1"
    binding: StoryFragmentContinuationBinding
    original_context_spec_sha256: _Hash
    derived_context_spec_sha256: _Hash
    length_spec_sha256: _Hash
    tail_sha256: _Hash
    raw_tail_sha256: _Hash
    compiled_tail_sha256: _Hash
    trim_policy: Literal["strip-boundary-whitespace-v1"] = "strip-boundary-whitespace-v1"
    trimmed_prefix_chars: int = Field(ge=0)
    trimmed_suffix_chars: int = Field(ge=0)
    tail_start: int = Field(ge=0)
    tail_end: int = Field(gt=0)
    requested_visible_chars: int = Field(gt=0)
    included_visible_chars: int = Field(gt=0)
    omitted_prefix_chars: int = Field(ge=0)
    max_tail_raw_chars: Literal[12000] = 12000
    max_tail_utf8_bytes: Literal[24000] = 24000
    tail_utf8_bytes: int = Field(gt=0, le=MAX_TAIL_UTF8_BYTES)
    reason: Literal["full_source_fits", "explicit_tail_projection"]

    @model_validator(mode="after")
    def _offsets(self) -> Self:
        if (
            self.tail_end != self.binding.source_raw_chars
            or self.tail_start != self.omitted_prefix_chars
            or not 0 <= self.tail_start < self.tail_end
            or self.tail_end - self.tail_start > self.max_tail_raw_chars
            or self.tail_sha256 != self.compiled_tail_sha256
            or self.included_visible_chars > self.requested_visible_chars
        ):
            raise ValueError("continuation tail report offsets or bounds mismatch")
        return self

    @property
    def sha256(self) -> str:
        return sha256_of_canonical(self.model_dump(mode="json"))


@dataclass(frozen=True)
class PreparedStoryFragmentContinuation:
    report: StoryFragmentContinuationProjectionReport
    context_spec: ContextSelectionSpec
    context_sources: tuple[ResolvedContextSource, ...]


def prepare_story_fragment_continuation(
    source: StoryFragmentContinuationSource,
    *,
    length_spec: GenerationLengthSpec,
    context_spec: ContextSelectionSpec,
    context_sources: Sequence[ResolvedContextSource] = (),
) -> PreparedStoryFragmentContinuation:
    """Reserve a required, explicitly disclosed tail before optional notes.

    UTF-8 bytes provide a conservative byte-token upper bound, not a tokenizer
    estimate. Raw and byte caps also reject pathological whitespace tails.
    """
    source = StoryFragmentContinuationSource(text=source.text, binding=source.binding)
    amount = max(length_spec.target, length_spec.minimum or 0)
    if length_spec.unit is LengthUnit.VISIBLE_CHARS:
        minimum_raw_chars = amount
    elif length_spec.unit is LengthUnit.WORDS:
        minimum_raw_chars = 2 * amount - 1
    else:
        minimum_raw_chars = 2 * amount * 250 - 1
    if minimum_raw_chars > min(MAX_SUFFIX_RAW_CHARS, MAX_COMBINED_RAW_CHARS - len(source.text)):
        raise ValueError("requested continuation cannot fit suffix or editor capacity")
    key = (source.binding.source_type, source.binding.source_id)
    selections = [item for item in context_spec.sources if item.source_key == key]
    if len(selections) != 1:
        raise ValueError("continuation requires an explicitly selected current field")
    selected = selections[0]
    resolved = {item.source_key: item for item in context_sources}
    if len(resolved) != len(context_sources):
        raise ValueError("continuation context contains duplicate source keys")
    supplied = resolved.get(key)
    if supplied is not None and supplied.text != source.text.strip():
        raise ValueError("continuation context is stale")
    reserved = sum(
        _visible(item)
        for item in (
            *context_spec.protected_facts,
            *context_spec.must_avoid,
        )
    )
    for item in context_spec.sources:
        if item.source_key != key and item.required:
            value = resolved.get(item.source_key)
            if value is None:
                raise ValueError("continuation required context is missing")
            reserved += _visible(value.text)
    budget = min(selected.max_visible_chars, context_spec.total_budget - reserved)
    if budget <= 0:
        raise ValueError("continuation tail has no protected context budget")
    start, visible, byte_count = len(source.text), 0, 0
    for character in reversed(source.text):
        char_visible = int(not character.isspace())
        char_bytes = len(character.encode("utf-8"))
        if (
            visible + char_visible > budget
            or len(source.text) - start >= MAX_TAIL_RAW_CHARS
            or byte_count + char_bytes > MAX_TAIL_UTF8_BYTES
        ):
            break
        start -= 1
        visible += char_visible
        byte_count += char_bytes
    if visible == 0 or (selected.required and start != 0):
        raise ValueError("required original or usable tail exceeds continuation budget")
    tail = source.text[start:]
    derived = context_spec.model_copy(
        update={
            "sources": tuple(
                item.model_copy(update={"required": True, "max_visible_chars": budget})
                if item.source_key == key
                else item
                for item in context_spec.sources
            )
        }
    )
    tail_source = ResolvedContextSource(source_id=key[1], source_type=key[0], text=tail)
    report = StoryFragmentContinuationProjectionReport(
        binding=source.binding,
        original_context_spec_sha256=context_spec.sha256,
        derived_context_spec_sha256=derived.sha256,
        length_spec_sha256=length_spec.sha256,
        tail_sha256=_sha(tail_source.text),
        tail_start=start,
        tail_end=len(source.text),
        raw_tail_sha256=_sha(tail),
        compiled_tail_sha256=_sha(tail_source.text),
        trimmed_prefix_chars=len(tail) - len(tail.lstrip()),
        trimmed_suffix_chars=len(tail) - len(tail.rstrip()),
        requested_visible_chars=_visible(source.text),
        included_visible_chars=visible,
        omitted_prefix_chars=start,
        tail_utf8_bytes=byte_count,
        reason="explicit_tail_projection" if start else "full_source_fits",
    )
    return PreparedStoryFragmentContinuation(
        report=report,
        context_spec=derived,
        context_sources=(
            tail_source,
            *(item for item in context_sources if item.source_key != key),
        ),
    )


class StoryFragmentSuffixGuardReport(_Evidence):
    source_binding: StoryFragmentContinuationBinding
    suffix_sha256: _Hash
    suffix_raw_chars: int = Field(ge=0)
    repetition: RepetitionGuardReport | None
    exact_source_excerpt_replay: bool
    accepted: bool

    @model_validator(mode="after")
    def _guard_integrity(self) -> Self:
        expected = (
            self.repetition is not None
            and self.repetition.accepted
            and not self.exact_source_excerpt_replay
            and 0 < self.suffix_raw_chars <= MAX_SUFFIX_RAW_CHARS
        )
        if self.accepted != expected or (
            self.repetition is not None
            and (
                self.repetition.existing_text_sha256 != self.source_binding.source_sha256
                or self.repetition.continuation_text_sha256 != self.suffix_sha256
            )
        ):
            raise ValueError("continuation replay evidence mismatch")
        return self


def inspect_story_fragment_suffix(
    source: StoryFragmentContinuationSource,
    suffix_text: str,
) -> StoryFragmentSuffixGuardReport:
    source = StoryFragmentContinuationSource(text=source.text, binding=source.binding)
    repetition = (
        ContinuationRepetitionGuard().inspect(
            existing_text=source.text,
            continuation_text=suffix_text,
        )
        if suffix_text.strip()
        else None
    )

    def normalize(text: str) -> str:
        return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text).casefold()).strip()

    suffix_normalized = normalize(suffix_text)
    replay = bool(suffix_normalized) and suffix_normalized in normalize(source.text)
    return StoryFragmentSuffixGuardReport(
        source_binding=source.binding,
        suffix_sha256=_sha(suffix_text),
        suffix_raw_chars=len(suffix_text),
        repetition=repetition,
        exact_source_excerpt_replay=replay,
        accepted=(
            repetition is not None
            and repetition.accepted
            and not replay
            and 0 < len(suffix_text) <= MAX_SUFFIX_RAW_CHARS
        ),
    )


def revalidate_story_fragment_projection(
    source: StoryFragmentContinuationSource,
    report: StoryFragmentContinuationProjectionReport,
) -> None:
    """Recompute every source-dependent field without serializing the original."""
    source = StoryFragmentContinuationSource(text=source.text, binding=source.binding)
    report = StoryFragmentContinuationProjectionReport.model_validate(report.model_dump())
    tail = source.text[report.tail_start : report.tail_end]
    if (
        source.binding != report.binding
        or not tail.strip()
        or _sha(tail) != report.raw_tail_sha256
        or _sha(tail.strip()) != report.compiled_tail_sha256
        or _visible(source.text) != report.requested_visible_chars
        or _visible(tail) != report.included_visible_chars
        or len(tail.encode("utf-8")) != report.tail_utf8_bytes
        or len(tail) - len(tail.lstrip()) != report.trimmed_prefix_chars
        or len(tail) - len(tail.rstrip()) != report.trimmed_suffix_chars
        or report.reason
        != ("explicit_tail_projection" if report.tail_start else "full_source_fits")
    ):
        raise ValueError("continuation original-to-tail projection changed")


class StoryFragmentContinuationEvidence(_Evidence):
    binding: StoryFragmentContinuationBinding
    call_guards: tuple[StoryFragmentSuffixGuardReport, ...] = ()
    accumulated_guard: StoryFragmentSuffixGuardReport | None = None
    condensed_guard: StoryFragmentSuffixGuardReport | None = None

    @model_validator(mode="after")
    def _bindings(self) -> Self:
        for item in (*self.call_guards, self.accumulated_guard, self.condensed_guard):
            if item is not None and item.source_binding != self.binding:
                raise ValueError("continuation guard belongs to another source")
        return self


class StoryFragmentAppendReceipt(_Evidence):
    source_binding: StoryFragmentContinuationBinding
    candidate_sha256: _Hash
    suffix_sha256: _Hash
    combined_sha256: _Hash
    separator: Literal["", " ", "\n\n"]
    suffix_text: str = Field(exclude=True, repr=False)
    combined_text: str = Field(exclude=True, repr=False)
    guard: StoryFragmentSuffixGuardReport


def _separator(original: str, suffix: str) -> Literal["", " ", "\n\n"]:
    # Preserve author whitespace; unfinished ASCII words need one word boundary.
    if original[-1].isspace() or suffix[0].isspace():
        return ""
    if original[-1] in ".!?。！？…」』":
        return "\n\n"
    if (
        original[-1].isascii()
        and original[-1].isalnum()
        and suffix[0].isascii()
        and suffix[0].isalnum()
    ):
        return " "
    return ""


def build_story_fragment_append_receipt(
    source: StoryFragmentContinuationSource,
    *,
    suffix_text: str,
    candidate_sha256: str,
) -> StoryFragmentAppendReceipt:
    guard = inspect_story_fragment_suffix(source, suffix_text)
    if not guard.accepted:
        raise ValueError("continuation suffix requires review and cannot be appended")
    separator = _separator(source.text, suffix_text)
    combined = source.text + separator + suffix_text
    if len(combined) > MAX_COMBINED_RAW_CHARS:
        raise ValueError("continuation append exceeds editor capacity")
    return StoryFragmentAppendReceipt(
        source_binding=source.binding,
        candidate_sha256=candidate_sha256,
        suffix_sha256=_sha(suffix_text),
        combined_sha256=_sha(combined),
        separator=separator,
        suffix_text=suffix_text,
        combined_text=combined,
        guard=guard,
    )


def revalidate_story_fragment_pending_append(
    receipt: StoryFragmentAppendReceipt,
    *,
    current_source: StoryFragmentContinuationSource,
) -> str:
    if receipt.source_binding != current_source.binding:
        raise ValueError("continuation source or editor instance changed")
    expected = build_story_fragment_append_receipt(
        current_source,
        suffix_text=receipt.suffix_text,
        candidate_sha256=receipt.candidate_sha256,
    )
    if expected != receipt:
        raise ValueError("pending continuation receipt changed")
    return expected.combined_text
