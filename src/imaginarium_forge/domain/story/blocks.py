"""Stable paragraph-block projection for immutable scene drafts.

The source of truth remains ``scene_drafts.prose_text``.  This module builds
an exact, deterministic projection that future media bindings can reference
without relying on line numbers or character offsets.  Ambiguous lineage is
deliberately rejected: a split, merge, or duplicate paragraph receives a new
logical ID instead of being silently rebound.
"""

from __future__ import annotations

import json
import re
import uuid
from collections import Counter
from dataclasses import dataclass
from hashlib import sha256
from typing import Literal

BLOCK_SCHEMA_VERSION = "story-block-v1"
SEGMENTATION_VERSION = "paragraph-v1"
MAPPING_VERSION = "parent-aware-v1"

_BLOCK_NAMESPACE = uuid.UUID("82131474-640f-5c99-a5d6-f7dbb53342dc")
_LINE_ENDING_RE = re.compile(r"(?:\r\n|\r|\n)$")
_SHA256_RE = re.compile(r"[0-9a-f]{64}")

LineageQuality = Literal["root", "exact", "conservative", "legacy_unlinked"]


def _sha256_text(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _canonical_sha256(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return _sha256_text(payload)


@dataclass(frozen=True, slots=True)
class SegmentedStoryBlock:
    """One paragraph and the exact whitespace that follows it."""

    text: str
    separator_after: str

    @property
    def text_sha256(self) -> str:
        return _sha256_text(self.text)

    @property
    def source_slice_sha256(self) -> str:
        return _canonical_sha256([self.text, self.separator_after])


@dataclass(frozen=True, slots=True)
class ParentStoryBlock:
    """The minimum immutable parent data needed for lineage mapping."""

    ordinal: int
    logical_block_id: str
    block_revision_id: str
    text_sha256: str


@dataclass(frozen=True, slots=True)
class ProjectedStoryBlock:
    ordinal: int
    logical_block_id: str
    block_revision_id: str
    parent_block_revision_id: str | None
    block_type: Literal["paragraph"]
    text: str
    separator_after: str
    text_sha256: str
    source_slice_sha256: str


@dataclass(frozen=True, slots=True)
class StoryBlockProjection:
    leading_text: str
    prose_sha256: str
    manifest_sha256: str
    lineage_quality: LineageQuality
    blocks: tuple[ProjectedStoryBlock, ...]

    def reconstruct(self) -> str:
        return self.leading_text + "".join(
            block.text + block.separator_after for block in self.blocks
        )


def segment_story_prose(prose_text: str) -> tuple[str, tuple[SegmentedStoryBlock, ...]]:
    """Split at blank lines while preserving every original code point.

    Internal single line breaks stay inside a paragraph.  Leading blank lines
    are stored once on the block set; the final line ending and every blank
    line after a paragraph live in ``separator_after``.  Reconstruction is
    therefore byte-identical for LF, CRLF, CJK, emoji, and whitespace-only
    inputs.
    """

    lines = prose_text.splitlines(keepends=True)
    if not lines:
        return prose_text, ()

    leading_parts: list[str] = []
    index = 0
    while index < len(lines) and not lines[index].strip():
        leading_parts.append(lines[index])
        index += 1

    blocks: list[SegmentedStoryBlock] = []
    while index < len(lines):
        paragraph_parts: list[str] = []
        while index < len(lines) and lines[index].strip():
            paragraph_parts.append(lines[index])
            index += 1

        if not paragraph_parts:
            # ``splitlines`` can expose a trailing whitespace-only fragment.
            if blocks:
                previous = blocks[-1]
                blocks[-1] = SegmentedStoryBlock(
                    previous.text,
                    previous.separator_after + "".join(lines[index:]),
                )
            else:
                leading_parts.extend(lines[index:])
            break


        paragraph = "".join(paragraph_parts)
        separator_parts: list[str] = []
        ending = _LINE_ENDING_RE.search(paragraph)
        if ending is not None:
            separator_parts.append(ending.group(0))
            paragraph = paragraph[: ending.start()]

        while index < len(lines) and not lines[index].strip():
            separator_parts.append(lines[index])
            index += 1
        blocks.append(SegmentedStoryBlock(paragraph, "".join(separator_parts)))

    return "".join(leading_parts), tuple(blocks)


def _logical_id(
    *, draft_id: str, ordinal: int, text_sha256: str, source_slice_sha256: str
) -> str:
    return str(
        uuid.uuid5(
            _BLOCK_NAMESPACE,
            f"logical:{draft_id}:{ordinal}:{text_sha256}:{source_slice_sha256}",
        )
    )


def _revision_id(
    *, draft_id: str, ordinal: int, logical_block_id: str, source_slice_sha256: str
) -> str:
    return str(
        uuid.uuid5(
            _BLOCK_NAMESPACE,
            f"revision:{draft_id}:{ordinal}:{logical_block_id}:{source_slice_sha256}",
        )
    )


def _lineage_matches(
    children: tuple[SegmentedStoryBlock, ...],
    parents: tuple[ParentStoryBlock, ...],
) -> tuple[dict[int, int], set[int]]:
    """Return child→parent matches and the conservatively matched children."""

    child_hashes = [block.text_sha256 for block in children]
    parent_hashes = [block.text_sha256 for block in parents]
    child_counts = Counter(child_hashes)
    parent_counts = Counter(parent_hashes)
    matches: dict[int, int] = {}
    used_parents: set[int] = set()

    parent_by_hash = {value: index for index, value in enumerate(parent_hashes)}
    for child_index, value in enumerate(child_hashes):
        if child_counts[value] == 1 and parent_counts[value] == 1:
            parent_index = parent_by_hash[value]
            matches[child_index] = parent_index
            used_parents.add(parent_index)

    conservative: set[int] = set()
    if len(children) == len(parents):
        unmatched_children = [i for i in range(len(children)) if i not in matches]
        unmatched_parents = [i for i in range(len(parents)) if i not in used_parents]
        if len(unmatched_children) == len(unmatched_parents) == 1:
            child_index = unmatched_children[0]
            parent_index = unmatched_parents[0]
            if child_index == parent_index:
                matches[child_index] = parent_index
                conservative.add(child_index)

    return matches, conservative


def _validate_parent_blocks(parents: tuple[ParentStoryBlock, ...]) -> None:
    if [block.ordinal for block in parents] != list(range(len(parents))):
        raise ValueError("parent block ordinals must be contiguous and zero-based")
    logical_ids = [block.logical_block_id for block in parents]
    revision_ids = [block.block_revision_id for block in parents]
    if any(not value.strip() for value in logical_ids + revision_ids):
        raise ValueError("parent block IDs must not be blank")
    if len(set(logical_ids)) != len(logical_ids):
        raise ValueError("parent logical block IDs must be unique")
    if len(set(revision_ids)) != len(revision_ids):
        raise ValueError("parent block revision IDs must be unique")
    if any(_SHA256_RE.fullmatch(block.text_sha256) is None for block in parents):
        raise ValueError("parent text_sha256 must be lowercase SHA-256")


def project_story_blocks(
    *,
    draft_id: str,
    prose_text: str,
    parent_blocks: tuple[ParentStoryBlock, ...] = (),
    parent_draft_id: str | None = None,
    parent_draft_available: bool | None = None,
) -> StoryBlockProjection:
    """Create a deterministic exact projection with conservative lineage."""

    if not draft_id.strip():
        raise ValueError("draft_id must not be blank")
    if parent_draft_id is not None and not parent_draft_id.strip():
        raise ValueError("parent_draft_id must not be blank")
    if parent_draft_id == draft_id:
        raise ValueError("child draft_id must differ from parent_draft_id")
    parent_requested = parent_draft_id is not None
    if parent_blocks and not parent_requested:
        raise ValueError("parent blocks require an explicit parent draft")
    leading_text, segmented = segment_story_prose(prose_text)
    ordered_parents = tuple(sorted(parent_blocks, key=lambda item: item.ordinal))
    _validate_parent_blocks(ordered_parents)
    parent_is_available = (
        bool(ordered_parents)
        if parent_draft_available is None
        else parent_draft_available
    )
    if parent_is_available and not parent_requested:
        raise ValueError("an available parent requires an explicit parent draft")
    if ordered_parents and not parent_is_available:
        raise ValueError("missing parent draft cannot supply parent blocks")
    matches, conservative_matches = _lineage_matches(segmented, ordered_parents)

    projected: list[ProjectedStoryBlock] = []
    exact_match_count = 0
    for ordinal, block in enumerate(segmented):
        parent_index = matches.get(ordinal)
        parent = None if parent_index is None else ordered_parents[parent_index]
        if parent is None:
            logical_id = _logical_id(
                draft_id=draft_id,
                ordinal=ordinal,
                text_sha256=block.text_sha256,
                source_slice_sha256=block.source_slice_sha256,
            )
        else:
            logical_id = parent.logical_block_id
            if ordinal not in conservative_matches:
                exact_match_count += 1
        revision_id = _revision_id(
            draft_id=draft_id,
            ordinal=ordinal,
            logical_block_id=logical_id,
            source_slice_sha256=block.source_slice_sha256,
        )
        projected.append(
            ProjectedStoryBlock(
                ordinal=ordinal,
                logical_block_id=logical_id,
                block_revision_id=revision_id,
                parent_block_revision_id=(
                    None if parent is None else parent.block_revision_id
                ),
                block_type="paragraph",
                text=block.text,
                separator_after=block.separator_after,
                text_sha256=block.text_sha256,
                source_slice_sha256=block.source_slice_sha256,
            )
        )

    if parent_requested and not parent_is_available:
        quality: LineageQuality = "legacy_unlinked"
    elif not parent_requested:
        quality = "root"
    elif (
        len(projected) == len(ordered_parents)
        and exact_match_count == len(projected)
        and not conservative_matches
    ):
        quality = "exact"
    else:
        quality = "conservative"

    manifest = {
        "schema_version": BLOCK_SCHEMA_VERSION,
        "segmentation_version": SEGMENTATION_VERSION,
        "mapping_version": MAPPING_VERSION,
        "lineage_quality": quality,
        "parent_draft_id": parent_draft_id,
        "parent_draft_available": parent_is_available,
        "leading_text_sha256": _sha256_text(leading_text),
        "prose_sha256": _sha256_text(prose_text),
        "blocks": [
            {
                "ordinal": block.ordinal,
                "logical_block_id": block.logical_block_id,
                "block_revision_id": block.block_revision_id,
                "parent_block_revision_id": block.parent_block_revision_id,
                "block_type": block.block_type,
                "text_sha256": block.text_sha256,
                "source_slice_sha256": block.source_slice_sha256,
            }
            for block in projected
        ],
    }
    result = StoryBlockProjection(
        leading_text=leading_text,
        prose_sha256=_sha256_text(prose_text),
        manifest_sha256=_canonical_sha256(manifest),
        lineage_quality=quality,
        blocks=tuple(projected),
    )
    if result.reconstruct() != prose_text:
        raise ValueError("story block projection failed exact prose reconstruction")
    return result


__all__ = [
    "BLOCK_SCHEMA_VERSION",
    "MAPPING_VERSION",
    "SEGMENTATION_VERSION",
    "ParentStoryBlock",
    "ProjectedStoryBlock",
    "SegmentedStoryBlock",
    "StoryBlockProjection",
    "project_story_blocks",
    "segment_story_prose",
]
