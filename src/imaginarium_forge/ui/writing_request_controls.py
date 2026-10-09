"""Shared writing-request controls.

The renderer returns typed contracts only when every visible value validates.
Resolved source text is carried separately from ``ContextSelectionSpec`` so a
selection snapshot cannot silently become a second, hidden content store.
"""

from __future__ import annotations

import hashlib
import re
from enum import StrEnum
from typing import Literal, Self, TypedDict, cast

import streamlit as st
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from streamlit.delta_generator import DeltaGenerator

from imaginarium_forge.application.services.generation.context_compiler import (
    ContextCompilationError,
    ContextCompiler,
    ResolvedContextSource,
)
from imaginarium_forge.application.services.generation.output_measurement import (
    SCREENPLAY_PAGE_POLICY_VERSION,
    SCREENPLAY_WORDS_PER_PAGE,
)
from imaginarium_forge.domain.generation.context_selection import (
    ContextCompactionPolicy,
    ContextScope,
    ContextSelectionSpec,
    ContextSourceSpec,
    SourceKey,
)
from imaginarium_forge.domain.generation.intent import GenerationIntent
from imaginarium_forge.domain.generation.length import (
    GenerationLengthSpec,
    LengthStrictness,
    LengthUnit,
)
from imaginarium_forge.domain.generation.ollama_context import (
    OLLAMA_STANDARD_NUM_CTX,
    OllamaContextProfile,
    OllamaContextProfileDecision,
    OllamaModelCapabilitySnapshot,
    resolve_ollama_context_profile,
)

_KEY_COMPONENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")
_FIELD_KEY_RE = re.compile(r"^[a-z0-9_]+$")
OLLAMA_CAPABILITY_SNAPSHOT_SESSION_KEY = "shared_ollama_capability_snapshot"


class _CapabilityProfileInputs(TypedDict):
    model_context_limit: int | None
    model_output_limit: int | None
    hardware_context_limit: int | None
    capability_model: str | None
    capability_source: Literal["ollama_api_show"] | None


def _capability_profile_inputs(
    snapshot: OllamaModelCapabilitySnapshot | None,
) -> _CapabilityProfileInputs:
    if snapshot is None:
        return {
            "model_context_limit": None,
            "model_output_limit": None,
            "hardware_context_limit": None,
            "capability_model": None,
            "capability_source": None,
        }
    return {
        "model_context_limit": snapshot.model_context_limit,
        "model_output_limit": snapshot.model_output_limit,
        "hardware_context_limit": snapshot.hardware_context_limit,
        "capability_model": snapshot.model,
        "capability_source": snapshot.source,
    }


_INTENT_LABELS = {
    GenerationIntent.IDEA: "靈感",
    GenerationIntent.PASSAGE: "段落",
    GenerationIntent.SCENE: "場景",
    GenerationIntent.SHORT_STORY: "短篇故事",
    GenerationIntent.CHAPTER: "章節",
    GenerationIntent.LONG_STORY: "長篇故事",
    GenerationIntent.CHARACTER_BACKSTORY: "角色背景",
    GenerationIntent.SCRIPT_SCENE: "劇本場景",
    GenerationIntent.SCREENPLAY: "長劇本",
}
_UNIT_LABELS = {
    LengthUnit.VISIBLE_CHARS: "可見字元",
    LengthUnit.WORDS: "英文 words",
    LengthUnit.SCREENPLAY_PAGES: "劇本頁數（估算）",
}
_STRICTNESS_LABELS = {
    LengthStrictness.ADVISORY: "建議值",
    LengthStrictness.MINIMUM_REQUIRED: "至少達到最少值",
    LengthStrictness.BOUNDED: "必須落在範圍內",
}
_SCOPE_LABELS = {
    ContextScope.CURRENT_FIELD: "目前欄位",
    ContextScope.CURRENT_DRAFT: "目前草稿",
    ContextScope.CURRENT_SCENE: "目前場景",
    ContextScope.CURRENT_CHAPTER: "目前章節",
    ContextScope.PROJECT_BIBLE: "專案設定集",
    ContextScope.CUSTOM: "自訂來源",
}
_COMPACTION_LABELS = {
    ContextCompactionPolicy.PERSISTED_SUMMARY_THEN_PRIORITY: "先用已保存摘要，再按優先順序縮減",
    ContextCompactionPolicy.PRIORITY_ONLY: "直接按優先順序縮減",
}
_OLLAMA_CONTEXT_PROFILE_LABELS = {
    OllamaContextProfile.AUTO: "自動（依模型與硬體）",
    OllamaContextProfile.STANDARD: "標準上下文",
    OllamaContextProfile.LONG: "長上下文",
    OllamaContextProfile.CUSTOM: "自訂（進階）",
}


class WritingRequestSurface(StrEnum):
    """The three free-writing surfaces sharing this N2 control contract."""

    STORY_FRAGMENT = "story_fragment"
    CHARACTER = "character"
    WORLD_SEED = "world_seed"


class WritingContextSourceInput(BaseModel):
    """Resolved material plus editable selection defaults for one source."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    source_id: str
    source_type: str
    label: str
    text: str
    persisted_summary: str | None = None
    included: bool = True
    max_visible_chars: int = Field(gt=0)
    priority: int = Field(ge=0)
    required: bool = False

    @field_validator("source_id")
    @classmethod
    def _source_id(cls, value: str) -> str:
        probe = ContextSourceSpec(
            source_id=value,
            source_type="probe",
            max_visible_chars=1,
            priority=0,
        )
        return probe.source_id

    @field_validator("source_type")
    @classmethod
    def _source_type(cls, value: str) -> str:
        probe = ContextSourceSpec(
            source_id="probe",
            source_type=value,
            max_visible_chars=1,
            priority=0,
        )
        return probe.source_type

    @field_validator("label")
    @classmethod
    def _label(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized or len(normalized) > 200:
            raise ValueError("source label must contain 1-200 characters")
        return normalized

    @property
    def source_key(self) -> SourceKey:
        return (self.source_type, self.source_id)


class WritingContextSourceControlValues(BaseModel):
    """Raw widget values retained until typed snapshot construction succeeds."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    material: WritingContextSourceInput
    included: bool
    max_visible_chars: int
    priority: int
    required: bool


class WritingRequestControlValues(BaseModel):
    """Complete raw widget projection used by the fail-closed builder."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    intent: GenerationIntent
    unit: LengthUnit
    minimum: int | None
    target: int
    maximum: int | None
    segment_target: int | None
    max_continuations: int
    strictness: LengthStrictness
    ollama_context_profile: OllamaContextProfile = OllamaContextProfile.AUTO
    ollama_custom_num_ctx: int | None = Field(default=None, gt=0)
    ollama_capability_snapshot: OllamaModelCapabilitySnapshot | None = None
    scope: ContextScope
    total_budget: int
    compaction_policy: ContextCompactionPolicy
    sources: tuple[WritingContextSourceControlValues, ...]
    protected_facts: tuple[str, ...] = ()
    must_avoid: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _valid_ollama_context_selection(self) -> Self:
        resolve_ollama_context_profile(
            self.ollama_context_profile,
            custom_num_ctx=self.ollama_custom_num_ctx,
            **_capability_profile_inputs(self.ollama_capability_snapshot),
        )
        return self


class WritingRequestDefaults(BaseModel):
    """Validated defaults supplied by one authoring surface."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    intent: GenerationIntent
    length_spec: GenerationLengthSpec
    ollama_context_profile: OllamaContextProfile = OllamaContextProfile.AUTO
    ollama_custom_num_ctx: int | None = Field(default=None, gt=0)
    ollama_capability_snapshot: OllamaModelCapabilitySnapshot | None = None
    scope: ContextScope
    total_budget: int = Field(gt=0)
    compaction_policy: ContextCompactionPolicy
    sources: tuple[WritingContextSourceInput, ...] = ()
    protected_facts: tuple[str, ...] = ()
    must_avoid: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _unique_source_keys_and_valid_context(self) -> Self:
        keys = [source.source_key for source in self.sources]
        if len(keys) != len(set(keys)):
            raise ValueError("writing source keys must be unique")
        ContextSelectionSpec(
            scope=self.scope,
            sources=tuple(
                ContextSourceSpec(
                    source_id=source.source_id,
                    source_type=source.source_type,
                    max_visible_chars=source.max_visible_chars,
                    priority=source.priority,
                    required=source.required,
                )
                for source in self.sources
                if source.included
            ),
            protected_facts=self.protected_facts,
            must_avoid=self.must_avoid,
            total_budget=self.total_budget,
            compaction_policy=self.compaction_policy,
        )
        resolve_ollama_context_profile(
            self.ollama_context_profile,
            custom_num_ctx=self.ollama_custom_num_ctx,
            **_capability_profile_inputs(self.ollama_capability_snapshot),
        )
        return self

    def to_control_values(self) -> WritingRequestControlValues:
        """Project defaults without adding, omitting, or transforming author values."""

        return WritingRequestControlValues(
            intent=self.intent,
            unit=self.length_spec.unit,
            minimum=self.length_spec.minimum,
            target=self.length_spec.target,
            maximum=self.length_spec.maximum,
            segment_target=self.length_spec.segment_target,
            max_continuations=self.length_spec.max_continuations,
            strictness=self.length_spec.strictness,
            ollama_context_profile=self.ollama_context_profile,
            ollama_custom_num_ctx=self.ollama_custom_num_ctx,
            ollama_capability_snapshot=self.ollama_capability_snapshot,
            scope=self.scope,
            total_budget=self.total_budget,
            compaction_policy=self.compaction_policy,
            sources=tuple(
                WritingContextSourceControlValues(
                    material=source,
                    included=source.included,
                    max_visible_chars=source.max_visible_chars,
                    priority=source.priority,
                    required=source.required,
                )
                for source in self.sources
            ),
            protected_facts=self.protected_facts,
            must_avoid=self.must_avoid,
        )


class WritingRequestSnapshot(BaseModel):
    """Exact typed request plus separately resolved source materials."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    intent: GenerationIntent
    length_spec: GenerationLengthSpec
    ollama_context_profile: OllamaContextProfileDecision
    context_spec: ContextSelectionSpec
    resolved_sources: tuple[ResolvedContextSource, ...]

    @model_validator(mode="after")
    def _resolved_sources_match_selection(self) -> Self:
        selected_keys = tuple(source.source_key for source in self.context_spec.sources)
        resolved_keys = tuple(source.source_key for source in self.resolved_sources)
        if resolved_keys != selected_keys:
            raise ValueError("resolved source order and identities must match context selection")
        return self


def writing_widget_key(key_prefix: str, field: str) -> str:
    """Build a stable page-owned key and reject ambiguous prefixes."""

    if not _KEY_COMPONENT_RE.fullmatch(key_prefix):
        raise ValueError("writing control key_prefix must be a stable identifier")
    if not _FIELD_KEY_RE.fullmatch(field):
        raise ValueError(
            "writing control field key must use lowercase letters, digits, or underscore"
        )
    return f"{key_prefix}__writing_request__{field}"


def writing_source_widget_key(
    key_prefix: str,
    source: WritingContextSourceInput,
    field: str,
) -> str:
    """Build an order-independent widget key from the complete typed source identity."""

    if not _FIELD_KEY_RE.fullmatch(field):
        raise ValueError(
            "writing source field key must use lowercase letters, digits, or underscore"
        )
    identity = f"{source.source_type}\0{source.source_id}".encode()
    token = hashlib.sha256(identity).hexdigest()
    return writing_widget_key(key_prefix, f"source_{token}_{field}")


def build_writing_request_defaults(
    surface: WritingRequestSurface | str,
    *,
    sources: tuple[WritingContextSourceInput, ...] = (),
    protected_facts: tuple[str, ...] = (),
    must_avoid: tuple[str, ...] = (),
    ollama_capability_snapshot: OllamaModelCapabilitySnapshot | None = None,
) -> WritingRequestDefaults:
    """Return the R2 surface preset without choosing or probing a provider."""

    selected_surface = WritingRequestSurface(surface)
    if selected_surface is WritingRequestSurface.STORY_FRAGMENT:
        intent = GenerationIntent.PASSAGE
        minimum = 500
        target = 700
        maximum = 1_000
        max_continuations = 1
        scope = ContextScope.CURRENT_DRAFT
        total_budget = 8_000
    elif selected_surface is WritingRequestSurface.CHARACTER:
        intent = GenerationIntent.CHARACTER_BACKSTORY
        minimum = 1_800
        target = 2_500
        maximum = 3_500
        max_continuations = 3
        scope = ContextScope.CURRENT_FIELD
        total_budget = 8_000
    else:
        intent = GenerationIntent.IDEA
        minimum = 120
        target = 200
        maximum = 400
        max_continuations = 0
        scope = ContextScope.CURRENT_FIELD
        total_budget = 4_000

    return WritingRequestDefaults(
        intent=intent,
        length_spec=GenerationLengthSpec(
            unit=LengthUnit.VISIBLE_CHARS,
            minimum=minimum,
            target=target,
            maximum=maximum,
            segment_target=None,
            max_continuations=max_continuations,
            strictness=LengthStrictness.MINIMUM_REQUIRED,
        ),
        scope=scope,
        total_budget=total_budget,
        compaction_policy=ContextCompactionPolicy.PERSISTED_SUMMARY_THEN_PRIORITY,
        ollama_capability_snapshot=ollama_capability_snapshot,
        sources=sources,
        protected_facts=protected_facts,
        must_avoid=must_avoid,
    )


def build_writing_request_snapshot(
    controls: WritingRequestControlValues,
) -> WritingRequestSnapshot:
    """Validate all visible values atomically; invalid input yields no partial snapshot."""

    length_spec = GenerationLengthSpec(
        unit=controls.unit,
        minimum=controls.minimum,
        target=controls.target,
        maximum=controls.maximum,
        segment_target=controls.segment_target,
        max_continuations=controls.max_continuations,
        strictness=controls.strictness,
    )
    ollama_context_profile = resolve_ollama_context_profile(
        controls.ollama_context_profile,
        custom_num_ctx=controls.ollama_custom_num_ctx,
        **_capability_profile_inputs(controls.ollama_capability_snapshot),
    )
    selected_controls = tuple(source for source in controls.sources if source.included)
    context_spec = ContextSelectionSpec(
        scope=controls.scope,
        sources=tuple(
            ContextSourceSpec(
                source_id=source.material.source_id,
                source_type=source.material.source_type,
                max_visible_chars=source.max_visible_chars,
                priority=source.priority,
                required=source.required,
            )
            for source in selected_controls
        ),
        protected_facts=controls.protected_facts,
        must_avoid=controls.must_avoid,
        total_budget=controls.total_budget,
        compaction_policy=controls.compaction_policy,
    )
    resolved_sources = tuple(
        ResolvedContextSource(
            source_id=source.material.source_id,
            source_type=source.material.source_type,
            text=source.material.text,
            persisted_summary=source.material.persisted_summary,
        )
        for source in selected_controls
    )
    return WritingRequestSnapshot(
        intent=controls.intent,
        length_spec=length_spec,
        ollama_context_profile=ollama_context_profile,
        context_spec=context_spec,
        resolved_sources=resolved_sources,
    )


def _optional_integer(value: int | float | None) -> int | None:
    return None if value is None else int(value)


def _measurement_caption(unit: LengthUnit) -> str:
    common = "混合語言不會自動更換單位；實際輸出會依作者目前選擇量測。"
    if unit is LengthUnit.VISIBLE_CHARS:
        return f"量測：可見字元（不含空白）。{common}"
    if unit is LengthUnit.WORDS:
        return f"量測：英文 words（標點不單獨計數）。{common}"
    return (
        "量測：劇本頁數為估算；"
        f"{SCREENPLAY_WORDS_PER_PAGE} words/page，政策 {SCREENPLAY_PAGE_POLICY_VERSION}。"
        f"實際 words 與可見字元會一併保存。{common}"
    )


def _render_ollama_context_decision(
    decision: OllamaContextProfileDecision,
) -> None:
    """Disclose the exact local decision without probing or starting a model."""

    requested = "auto" if decision.requested_num_ctx is None else f"{decision.requested_num_ctx:,}"
    st.caption(
        f"實際 num_ctx={decision.num_ctx:,}｜requested={requested}｜"
        f"num_predict cap={decision.num_predict_cap:,}｜"
        f"rationale={decision.rationale}｜policy={decision.policy_version}"
    )
    if decision.capability_model is not None:
        st.caption(
            f"能力快照：{decision.capability_model}｜source={decision.capability_source}｜"
            f"model context={decision.model_context_limit or 'unknown'}｜"
            f"hardware context={decision.hardware_context_limit or 'unknown'}｜"
            f"model output={decision.model_output_limit or 'unknown'}"
        )
    if decision.warning is None:
        st.caption("warning=無")
    else:
        st.warning(f"warning={decision.warning}")
    st.caption("此處只建立本次要求的不可變決策；不會連線、探測、載入模型或開始生成。")


def _render_preflight_inspector(
    snapshot: WritingRequestSnapshot,
    defaults: WritingRequestDefaults,
) -> bool:
    """Compile locally and show non-secret inclusion evidence before any provider call."""

    try:
        compiled = ContextCompiler().compile(
            snapshot.context_spec,
            snapshot.resolved_sources,
        )
    except ContextCompilationError:
        st.error("上下文預算不足，必要來源或作者鎖定內容無法安全納入；已阻止建立生成要求。")
        return False

    labels = {source.source_key: source.label for source in defaults.sources}
    report = compiled.report
    with st.expander("送出前查看長度與上下文對帳", expanded=False):
        st.caption(
            f"長度｜最少 {snapshot.length_spec.minimum} · "
            f"目標 {snapshot.length_spec.target} · "
            f"最多 {snapshot.length_spec.maximum} {snapshot.length_spec.unit.value} · "
            f"{snapshot.length_spec.strictness.value}"
        )
        st.caption(
            f"上下文｜預算 {report.total_budget:,} · 實際納入 {report.budget_used:,} "
            "可見字元；下列只顯示來源對帳，不重複顯示原文。"
        )
        st.caption("作者鎖定事實（不可壓縮）")
        if snapshot.context_spec.protected_facts:
            for fact in snapshot.context_spec.protected_facts:
                st.caption(f"• {fact}")
        else:
            st.caption("• 無")
        st.caption("必須避免")
        if snapshot.context_spec.must_avoid:
            for item in snapshot.context_spec.must_avoid:
                st.caption(f"• {item}")
        else:
            st.caption("• 無")
        st.caption("來源摘要")
        if not report.requested:
            st.caption("• 本次沒有選取一般上下文來源。")
        for entry in report.requested:
            label = labels.get(entry.source_key, f"{entry.source_type}:{entry.source_id}")
            reason = f" · {entry.reason}" if entry.reason else ""
            st.caption(
                f"• {label}｜requested {entry.requested_visible_chars:,} · "
                f"included {entry.included_visible_chars:,} · "
                f"{entry.representation.value}{reason}"
            )
    return True


def _control_slots(count: int, *, narrow: bool) -> list[DeltaGenerator]:
    """Stack full-width controls in narrow assistants, preserving default rows."""

    if narrow:
        return [st.container() for _ in range(count)]
    return st.columns(count)


def render_writing_request_controls(
    defaults: WritingRequestDefaults,
    *,
    key_prefix: str,
    narrow: bool = False,
) -> WritingRequestSnapshot | None:
    """Render validated controls; narrow changes layout, never keys or values."""

    capability_snapshot = defaults.ollama_capability_snapshot
    if capability_snapshot is None:
        raw_capability = st.session_state.get(OLLAMA_CAPABILITY_SNAPSHOT_SESSION_KEY)
        if raw_capability is not None:
            try:
                capability_snapshot = OllamaModelCapabilitySnapshot.model_validate(raw_capability)
            except (ValidationError, ValueError):
                # Session data can be stale across app upgrades. Ignore it and
                # let AUTO disclose the conservative unknown-capability state.
                capability_snapshot = None

    intent_options = tuple(GenerationIntent)
    unit_options = tuple(LengthUnit)
    intent = cast(
        GenerationIntent,
        st.selectbox(
            "創作目的",
            intent_options,
            index=intent_options.index(defaults.intent),
            format_func=_INTENT_LABELS.__getitem__,
            key=writing_widget_key(key_prefix, "intent"),
        ),
    )
    basic_unit, basic_target = _control_slots(2, narrow=narrow)
    unit = cast(
        LengthUnit,
        basic_unit.selectbox(
            "長度單位",
            unit_options,
            index=unit_options.index(defaults.length_spec.unit),
            format_func=_UNIT_LABELS.__getitem__,
            key=writing_widget_key(key_prefix, "unit"),
        ),
    )
    target = int(
        basic_target.number_input(
            "目標長度",
            min_value=1,
            value=defaults.length_spec.target,
            step=1,
            key=writing_widget_key(key_prefix, "target"),
        )
    )
    st.caption(_measurement_caption(unit))

    with st.expander("進階長度與續寫", expanded=False):
        bound_left, bound_right = _control_slots(2, narrow=narrow)
        minimum = _optional_integer(
            bound_left.number_input(
                "最少長度（可留空）",
                min_value=0,
                value=defaults.length_spec.minimum,
                step=1,
                key=writing_widget_key(key_prefix, "minimum"),
            )
        )
        maximum = _optional_integer(
            bound_right.number_input(
                "最多長度（可留空）",
                min_value=1,
                value=defaults.length_spec.maximum,
                step=1,
                key=writing_widget_key(key_prefix, "maximum"),
            )
        )
        strictness_options = tuple(LengthStrictness)
        strictness = cast(
            LengthStrictness,
            st.selectbox(
                "長度要求",
                strictness_options,
                index=strictness_options.index(defaults.length_spec.strictness),
                format_func=_STRICTNESS_LABELS.__getitem__,
                key=writing_widget_key(key_prefix, "strictness"),
            ),
        )
        segment_col, continuation_col = _control_slots(2, narrow=narrow)
        segment_target = _optional_integer(
            segment_col.number_input(
                "每段目標（可留空）",
                min_value=1,
                value=defaults.length_spec.segment_target,
                step=1,
                key=writing_widget_key(key_prefix, "segment_target"),
            )
        )
        max_continuations = int(
            continuation_col.number_input(
                "最多自動續寫次數",
                min_value=0,
                max_value=5,
                value=defaults.length_spec.max_continuations,
                step=1,
                key=writing_widget_key(key_prefix, "max_continuations"),
            )
        )

    with st.expander("Ollama 上下文（進階）", expanded=False):
        ollama_profile_options = tuple(OllamaContextProfile)
        ollama_context_profile = cast(
            OllamaContextProfile,
            st.selectbox(
                "Ollama context profile",
                ollama_profile_options,
                index=ollama_profile_options.index(defaults.ollama_context_profile),
                format_func=_OLLAMA_CONTEXT_PROFILE_LABELS.__getitem__,
                key=writing_widget_key(key_prefix, "ollama_context_profile"),
            ),
        )
        ollama_custom_num_ctx: int | None = None
        if ollama_context_profile is OllamaContextProfile.CUSTOM:
            ollama_custom_num_ctx = int(
                st.number_input(
                    "自訂 num_ctx",
                    min_value=1,
                    value=(
                        defaults.ollama_custom_num_ctx
                        if defaults.ollama_context_profile is OllamaContextProfile.CUSTOM
                        and defaults.ollama_custom_num_ctx is not None
                        else OLLAMA_STANDARD_NUM_CTX
                    ),
                    step=1_024,
                    key=writing_widget_key(key_prefix, "ollama_custom_num_ctx"),
                )
            )
        try:
            ollama_context_preview = resolve_ollama_context_profile(
                ollama_context_profile,
                custom_num_ctx=ollama_custom_num_ctx,
                **_capability_profile_inputs(capability_snapshot),
            )
        except (ValidationError, ValueError):
            ollama_context_preview = None
            st.error("Ollama 上下文設定無效；已阻止建立生成要求，且不會偷偷降低 num_ctx。")
        if ollama_context_preview is not None:
            _render_ollama_context_decision(ollama_context_preview)

    source_controls: list[WritingContextSourceControlValues] = []
    with st.expander("上下文範圍與來源", expanded=False):
        scope_options = tuple(ContextScope)
        scope = cast(
            ContextScope,
            st.selectbox(
                "上下文範圍",
                scope_options,
                index=scope_options.index(defaults.scope),
                format_func=_SCOPE_LABELS.__getitem__,
                key=writing_widget_key(key_prefix, "scope"),
            ),
        )
        context_left, context_right = _control_slots(2, narrow=narrow)
        total_budget = int(
            context_left.number_input(
                "上下文總預算（可見字元）",
                min_value=1,
                value=defaults.total_budget,
                step=100,
                key=writing_widget_key(key_prefix, "total_budget"),
            )
        )
        policy_options = tuple(ContextCompactionPolicy)
        compaction_policy = cast(
            ContextCompactionPolicy,
            context_right.selectbox(
                "縮減方式",
                policy_options,
                index=policy_options.index(defaults.compaction_policy),
                format_func=_COMPACTION_LABELS.__getitem__,
                key=writing_widget_key(key_prefix, "compaction_policy"),
            ),
        )
        st.caption("數字越小，來源優先順序越高；required 來源不會被靜默縮減。")

        for source in defaults.sources:
            st.caption(source.label)
            included = st.checkbox(
                "納入這個來源",
                value=source.included,
                key=writing_source_widget_key(key_prefix, source, "included"),
            )
            budget_col, priority_col, required_col = _control_slots(3, narrow=narrow)
            source_budget = int(
                budget_col.number_input(
                    "來源預算",
                    min_value=1,
                    value=source.max_visible_chars,
                    step=100,
                    disabled=not included,
                    key=writing_source_widget_key(key_prefix, source, "max_visible_chars"),
                )
            )
            priority = int(
                priority_col.number_input(
                    "優先序",
                    min_value=0,
                    value=source.priority,
                    step=1,
                    disabled=not included,
                    key=writing_source_widget_key(key_prefix, source, "priority"),
                )
            )
            required = required_col.checkbox(
                "必要來源",
                value=source.required,
                disabled=not included,
                key=writing_source_widget_key(key_prefix, source, "required"),
            )
            source_controls.append(
                WritingContextSourceControlValues(
                    material=source,
                    included=included,
                    max_visible_chars=source_budget,
                    priority=priority,
                    required=required,
                )
            )

    try:
        controls = WritingRequestControlValues(
            intent=intent,
            unit=unit,
            minimum=minimum,
            target=target,
            maximum=maximum,
            segment_target=segment_target,
            max_continuations=max_continuations,
            strictness=strictness,
            ollama_context_profile=ollama_context_profile,
            ollama_custom_num_ctx=ollama_custom_num_ctx,
            ollama_capability_snapshot=capability_snapshot,
            scope=scope,
            total_budget=total_budget,
            compaction_policy=compaction_policy,
            sources=tuple(source_controls),
            protected_facts=defaults.protected_facts,
            must_avoid=defaults.must_avoid,
        )
        snapshot = build_writing_request_snapshot(controls)
    except (ValidationError, ValueError):
        st.error(
            "長度或上下文設定無效，已阻止建立生成要求。"
            "請確認最少 ≤ 目標 ≤ 最多，所有預算大於 0，且來源設定不重複。"
        )
        return None
    if not _render_preflight_inspector(snapshot, defaults):
        return None
    return snapshot


__all__ = [
    "OLLAMA_CAPABILITY_SNAPSHOT_SESSION_KEY",
    "WritingContextSourceControlValues",
    "WritingContextSourceInput",
    "WritingRequestControlValues",
    "WritingRequestDefaults",
    "WritingRequestSnapshot",
    "WritingRequestSurface",
    "build_writing_request_defaults",
    "build_writing_request_snapshot",
    "render_writing_request_controls",
    "writing_source_widget_key",
    "writing_widget_key",
]
