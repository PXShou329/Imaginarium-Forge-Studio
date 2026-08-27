"""Prompt profile hierarchy with EXPLICIT merge semantics (spec §28).

Three layers, later wins field-by-field:

    Prompt Dialect  →  Checkpoint Profile  →  Personal Preset

No generic deep merge. Per-field behavior (spec §28.3):

    scalar fields                → replace (only when the higher layer sets them)
    sampler_recommendations map  → replace (whole mapping)
    block_order                  → replace
    quality_prefix / suffix      → declared mode: replace | prepend | append
    required_tags                → append + stable dedupe
    negative_tags                → append + stable dedupe
    prohibited_tags              → union (stable-order)
    notes                        → append with provenance

Resolved snapshots are hashed (SHA-256 over canonical JSON) so every generated
prompt can be reproduced after profiles change.
"""

from __future__ import annotations

import hashlib
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator

from imaginarium_forge.canonical import canonical_json

#: Compiler contract version — bump when compilation semantics change.
PROMPT_COMPILER_VERSION = "phase2-compiler-v1"


class ProfileKind(StrEnum):
    DIALECT = "dialect"
    CHECKPOINT = "checkpoint"
    PRESET = "preset"


class ProfileSyntax(StrEnum):
    TAG_BASED = "tag_based"
    NATURAL_LANGUAGE = "natural_language"


class ListMode(StrEnum):
    REPLACE = "replace"
    PREPEND = "prepend"
    APPEND = "append"


class NoteProvenance(StrEnum):
    CONFIRMED = "confirmed"
    INFERRED = "inferred"
    USER_OBSERVED = "user_observed"
    UNTESTED = "untested"


class ProfileNote(BaseModel):
    model_config = ConfigDict(frozen=True)

    text: str
    provenance: NoteProvenance = NoteProvenance.UNTESTED
    source_layer: str = ""  # filled during merge: "dialect:<id>" etc.


#: Canonical block names the compiler assembles into the positive prompt.
BLOCK_NAMES: tuple[str, ...] = (
    "quality_prefix",
    "subject_count",
    "adult_presentation",
    "identity",
    "face_hair_eyes",
    "distinguishing_features",
    "body",
    "outfit",
    "pose_expression",
    "camera_composition",
    "environment",
    "lighting",
    "style",
    "quality_suffix",
)


class PromptProfileLayer(BaseModel):
    """One YAML-defined layer. Unset optional scalars mean "inherit lower layer"."""

    model_config = ConfigDict(frozen=True)

    id: str
    version: str
    kind: ProfileKind
    label: str = ""
    syntax: ProfileSyntax | None = None
    #: for kind=checkpoint: the exact filename this profile targets
    checkpoint_filename: str = ""
    experimental: bool = False
    low_confidence_warning: str = ""
    #: §6.1 checkpoint-status trio (informational; registry enum is authoritative)
    profile_status: str = ""
    usage_status: str = ""
    recommendation_status: str = ""

    block_order: tuple[str, ...] = ()
    quality_prefix: tuple[str, ...] = ()
    quality_prefix_mode: ListMode = ListMode.REPLACE
    quality_suffix: tuple[str, ...] = ()
    quality_suffix_mode: ListMode = ListMode.REPLACE
    required_tags: tuple[str, ...] = ()
    negative_tags: tuple[str, ...] = ()
    prohibited_tags: tuple[str, ...] = ()
    sampler_recommendations: dict[str, str] = Field(default_factory=dict)
    notes: tuple[ProfileNote, ...] = ()

    @field_validator("block_order")
    @classmethod
    def _known_blocks(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        unknown = [b for b in value if b not in BLOCK_NAMES]
        if unknown:
            raise ValueError(f"block_order 含未知區塊：{unknown}；允許：{list(BLOCK_NAMES)}")
        return value


def _stable_dedupe(items: tuple[str, ...]) -> tuple[str, ...]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        key = item.strip().casefold()
        if key and key not in seen:
            seen.add(key)
            out.append(item.strip())
    return tuple(out)


def _merge_ordered(
    base: tuple[str, ...], layer: tuple[str, ...], mode: ListMode
) -> tuple[str, ...]:
    if not layer:
        return base
    if mode is ListMode.REPLACE:
        return layer
    if mode is ListMode.PREPEND:
        return _stable_dedupe(layer + base)
    return _stable_dedupe(base + layer)  # APPEND


class ResolvedProfile(BaseModel):
    """The deterministic merge of dialect → checkpoint → preset."""

    model_config = ConfigDict(frozen=True)

    dialect_id: str
    dialect_version: str
    checkpoint_profile_id: str = ""
    checkpoint_profile_version: str = ""
    preset_id: str = ""
    preset_version: str = ""

    label: str = ""
    syntax: ProfileSyntax = ProfileSyntax.TAG_BASED
    checkpoint_filename: str = ""
    experimental: bool = False
    low_confidence_warning: str = ""
    profile_status: str = ""
    usage_status: str = ""
    recommendation_status: str = ""
    block_order: tuple[str, ...] = BLOCK_NAMES
    quality_prefix: tuple[str, ...] = ()
    quality_suffix: tuple[str, ...] = ()
    required_tags: tuple[str, ...] = ()
    negative_tags: tuple[str, ...] = ()
    prohibited_tags: tuple[str, ...] = ()
    sampler_recommendations: dict[str, str] = Field(default_factory=dict)
    notes: tuple[ProfileNote, ...] = ()
    compiler_version: str = PROMPT_COMPILER_VERSION

    def canonical_dump(self) -> str:
        return canonical_json(self.model_dump(mode="json"))

    def sha256(self) -> str:
        return hashlib.sha256(self.canonical_dump().encode("utf-8")).hexdigest()


def resolve_profiles(
    dialect: PromptProfileLayer,
    checkpoint: PromptProfileLayer | None = None,
    preset: PromptProfileLayer | None = None,
) -> ResolvedProfile:
    """Deterministic three-layer merge. Precedence: preset > checkpoint > dialect."""
    if dialect.kind is not ProfileKind.DIALECT:
        raise ValueError(f"第一層必須是 dialect，收到：{dialect.kind.value}")
    if checkpoint is not None and checkpoint.kind is not ProfileKind.CHECKPOINT:
        raise ValueError(f"第二層必須是 checkpoint profile，收到：{checkpoint.kind.value}")
    if preset is not None and preset.kind is not ProfileKind.PRESET:
        raise ValueError(f"第三層必須是 preset，收到：{preset.kind.value}")

    layers = [layer for layer in (dialect, checkpoint, preset) if layer is not None]

    syntax = ProfileSyntax.TAG_BASED
    label = ""
    checkpoint_filename = ""
    experimental = False
    warning = ""
    profile_status = ""
    usage_status = ""
    recommendation_status = ""
    block_order: tuple[str, ...] = BLOCK_NAMES
    quality_prefix: tuple[str, ...] = ()
    quality_suffix: tuple[str, ...] = ()
    required: tuple[str, ...] = ()
    negative: tuple[str, ...] = ()
    prohibited: tuple[str, ...] = ()
    sampler: dict[str, str] = {}
    notes: list[ProfileNote] = []

    for layer in layers:
        origin = f"{layer.kind.value}:{layer.id}@{layer.version}"
        # scalars: replace when the layer sets them
        if layer.syntax is not None:
            syntax = layer.syntax
        if layer.label:
            label = layer.label
        if layer.checkpoint_filename:
            checkpoint_filename = layer.checkpoint_filename
        if layer.experimental:
            experimental = True  # experimental status never silently downgrades
        if layer.low_confidence_warning:
            warning = layer.low_confidence_warning
        if layer.profile_status:
            profile_status = layer.profile_status
        if layer.usage_status:
            usage_status = layer.usage_status
        if layer.recommendation_status:
            recommendation_status = layer.recommendation_status
        # ordered block list: replace when set
        if layer.block_order:
            block_order = layer.block_order
        # ordered lists with declared mode
        quality_prefix = _merge_ordered(
            quality_prefix, layer.quality_prefix, layer.quality_prefix_mode
        )
        quality_suffix = _merge_ordered(
            quality_suffix, layer.quality_suffix, layer.quality_suffix_mode
        )
        # append + stable dedupe
        required = _stable_dedupe(required + layer.required_tags)
        negative = _stable_dedupe(negative + layer.negative_tags)
        # union (stable order = first occurrence wins)
        prohibited = _stable_dedupe(prohibited + layer.prohibited_tags)
        # sampler recommendations: replace whole mapping when the layer sets one
        if layer.sampler_recommendations:
            sampler = dict(layer.sampler_recommendations)
        # notes: append with provenance + source layer
        notes.extend(
            note.model_copy(update={"source_layer": origin}) for note in layer.notes
        )

    return ResolvedProfile(
        dialect_id=dialect.id,
        dialect_version=dialect.version,
        checkpoint_profile_id=checkpoint.id if checkpoint else "",
        checkpoint_profile_version=checkpoint.version if checkpoint else "",
        preset_id=preset.id if preset else "",
        preset_version=preset.version if preset else "",
        label=label,
        syntax=syntax,
        checkpoint_filename=checkpoint_filename,
        experimental=experimental,
        low_confidence_warning=warning,
        profile_status=profile_status,
        usage_status=usage_status,
        recommendation_status=recommendation_status,
        block_order=block_order,
        quality_prefix=quality_prefix,
        quality_suffix=quality_suffix,
        required_tags=required,
        negative_tags=negative,
        prohibited_tags=prohibited,
        sampler_recommendations=sampler,
        notes=tuple(notes),
    )
