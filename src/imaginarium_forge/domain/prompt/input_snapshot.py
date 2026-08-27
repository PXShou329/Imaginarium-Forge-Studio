"""Compilation input snapshot and fingerprint (Gate A A-01).

The stale-state defect: the Studio cached a resolution and reused it on
Compile, so old Canon/policy decisions could be combined with new inputs.

Fix contract:

- every Compile builds a fresh immutable snapshot of ALL policy-relevant
  inputs and re-runs eligibility → resolvers → conflicts → profile → compile
  → lint (no reuse path exists);
- the snapshot's SHA-256 fingerprint travels with the outcome and the
  persisted variant;
- save / accept / export / experiment REQUIRE the current fingerprint to
  equal the compiled outcome's fingerprint, else StaleCompilationInputError.
"""

from __future__ import annotations

import hashlib

from pydantic import BaseModel, ConfigDict, model_validator

from imaginarium_forge.canonical import canonical_json
from imaginarium_forge.domain.prompt.content_mode import ContentMode


class PromptCompilationInputSnapshot(BaseModel):
    """Every field that can change the meaning of a compilation (spec §4.2)."""

    model_config = ConfigDict(frozen=True)

    source_text: str = ""
    prompt_ast_canonical_json: str = ""
    character_id: str = ""
    character_version_id: str = ""
    outfit_id: str = ""
    style_profile_id: str = ""
    style_version_id: str = ""
    adult_content_requested: bool = False
    content_mode: ContentMode = ContentMode.GENERAL
    dialect_id: str = ""
    checkpoint_id: str = ""
    checkpoint_filename: str = ""
    checkpoint_profile_id: str = ""
    preset_id: str = ""
    compiler_version: str = ""

    def fingerprint(self) -> str:
        return hashlib.sha256(
            canonical_json(self.model_dump(mode="json")).encode("utf-8")
        ).hexdigest()


class PromptVersionSelectionSnapshot(BaseModel):
    """Immutable per-version record of what was selected (Gate A A-05).

    Opening an old prompt version must never consult mutable current project
    selections; this snapshot is the historical source of truth.
    """

    model_config = ConfigDict(frozen=True)

    project_id: str = ""
    character_id: str = ""
    character_version_id: str = ""
    outfit_id: str = ""
    style_profile_id: str = ""
    style_version_id: str = ""
    content_mode: ContentMode = ContentMode.GENERAL
    adult_content_requested: bool = False
    participant_manifest_canonical_json: str = ""
    participant_manifest_fingerprint: str = ""
    image_eligibility_evaluation_ids: tuple[str, ...] = ()
    source_text: str = ""
    created_at: str = ""

    @model_validator(mode="after")
    def _manifest_fingerprint_matches_payload(self) -> PromptVersionSelectionSnapshot:
        has_manifest = bool(self.participant_manifest_canonical_json)
        has_fingerprint = bool(self.participant_manifest_fingerprint)
        if has_manifest != has_fingerprint:
            raise ValueError("參與者 manifest canonical JSON 與 fingerprint 必須成對保存")
        if has_manifest:
            actual = hashlib.sha256(
                self.participant_manifest_canonical_json.encode("utf-8")
            ).hexdigest()
            if actual != self.participant_manifest_fingerprint:
                raise ValueError("參與者 manifest fingerprint 與 canonical JSON 不一致")
        return self

    def canonical(self) -> str:
        return canonical_json(self.model_dump(mode="json"))

    def sha256(self) -> str:
        return hashlib.sha256(self.canonical().encode("utf-8")).hexdigest()


class ResolutionInputSnapshot(BaseModel):
    """A2-02: the exact identity of what a resolution was computed FOR.

    ``PromptResolutionService.resolve`` freezes one of these into its outcome;
    ``PromptCompilationService.compile`` rebuilds it from the compile request
    and requires exact fingerprint equality, so a resolution can never be
    paired with unrelated IDs, another character's eligibility verdict, a
    changed AST, or a changed content mode.
    """

    model_config = ConfigDict(frozen=True)

    prompt_ast_canonical_json: str
    character_id: str = ""
    character_version_id: str = ""
    outfit_id: str = ""
    style_version_id: str = ""
    content_mode: ContentMode = ContentMode.GENERAL

    def fingerprint(self) -> str:
        payload = self.model_dump_json()
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()
