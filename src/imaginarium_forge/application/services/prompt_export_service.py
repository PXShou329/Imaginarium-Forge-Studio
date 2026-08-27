"""Persisted prompt export (Gate A2-05, review §4.1).

``PromptExportService.build_bundle(variant_id, expected_project_id=...)``
reconstructs an export bundle EXCLUSIVELY from persisted state and only
inside the caller's explicit top-level project scope:

- the write-once prompt variant (blocks / lint / conflicts / checkpoint
  identity / profile snapshot reference);
- its prompt project version (stored AST + frozen selection snapshot);
- the deduplicated profile snapshot's ``resolved_json`` — NOT the live
  profile YAML files;
- the referenced immutable Canon versions (character / outfit / style),
  re-resolved deterministically against the stored AST;
- project / prompt-project display names.

It must never touch ``studio_outcome``, ``studio_resolution``, current UI
selections, or the current checkpoint pick, so a historical export is
byte-stable across application restarts, profile-file edits, and any later
UI activity.
"""

from __future__ import annotations

from imaginarium_forge.application.errors import NotFoundError
from imaginarium_forge.application.services.base import ServiceBase, SessionProvider
from imaginarium_forge.domain.common.ids import utc_now_iso
from imaginarium_forge.domain.prompt.ast import PromptAST
from imaginarium_forge.domain.prompt.compilation import CharacterLockInput, StyleInput
from imaginarium_forge.domain.prompt.content_mode import derives_adult
from imaginarium_forge.domain.prompt.input_snapshot import (
    PromptVersionSelectionSnapshot,
)
from imaginarium_forge.domain.prompt.profiles import ResolvedProfile
from imaginarium_forge.domain.prompt.resolution import resolve_character, resolve_style
from imaginarium_forge.infrastructure.db.repositories.characters import (
    CharacterRepository,
)
from imaginarium_forge.infrastructure.db.repositories.outfits import OutfitRepository
from imaginarium_forge.infrastructure.db.repositories.projects import ProjectRepository
from imaginarium_forge.infrastructure.db.repositories.prompt_repos import (
    ProfileSnapshotRepository,
    PromptProjectRepository,
    PromptProjectVersionRepository,
    PromptVariantRepository,
)
from imaginarium_forge.infrastructure.db.repositories.styles import StyleRepository
from imaginarium_forge.infrastructure.export.prompt_export import (
    ExportBundle,
    ExportRefs,
    bundle_from_variant,
)


class PromptExportService(ServiceBase):
    def __init__(self, session_factory: SessionProvider) -> None:
        super().__init__(session_factory)

    def build_bundle(
        self,
        variant_id: str,
        *,
        expected_project_id: str,
        created_at: str | None = None,
    ) -> ExportBundle:
        """``created_at`` defaults to now; tests may pin it for byte-stable
        comparisons — everything else is fully persisted state.

        ``expected_project_id`` is mandatory because variant IDs can remain
        in UI session state after a project switch.  A foreign ID and a
        nonexistent ID deliberately produce the same error.

        This is historical/archival reconstruction, not a fresh content-
        eligibility verdict or permission to execute a downstream generator.
        Any future executor must enforce its own current, media-specific gate.
        """
        with self._session_factory() as session:
            if not expected_project_id:
                raise NotFoundError("找不到 prompt export")
            variant = PromptVariantRepository(session).get_for_project(
                variant_id, expected_project_id
            )
            if variant is None:
                raise NotFoundError("找不到 prompt export")
            version = PromptProjectVersionRepository(session).get(
                variant.prompt_project_version_id
            )
            if (
                version is None
                or version.prompt_project_id != variant.prompt_project_id
            ):
                raise NotFoundError("找不到 prompt export")
            prompt_project = PromptProjectRepository(session).get(
                variant.prompt_project_id
            )
            if (
                prompt_project is None
                or prompt_project.project_id != expected_project_id
            ):
                raise NotFoundError("找不到 prompt export")
            project = ProjectRepository(session).get(expected_project_id)
            if project is None:
                raise NotFoundError("找不到 prompt export")
            snapshot = ProfileSnapshotRepository(session).get(
                variant.profile_snapshot_id
            )
            if snapshot is None:
                raise NotFoundError("找不到 prompt export")

            # ---- persisted-only inputs -----------------------------------
            try:
                ast = PromptAST.model_validate_json(version.ast_json)
                selection = PromptVersionSelectionSnapshot.model_validate_json(
                    version.selection_snapshot_json
                )
                profile = ResolvedProfile.model_validate_json(snapshot.resolved_json)
            except ValueError:
                raise NotFoundError("找不到 prompt export") from None

            if (
                selection.project_id != expected_project_id
                or selection.sha256() != version.selection_snapshot_sha256
                or variant.input_fingerprint != version.input_fingerprint
                or profile.sha256() != snapshot.sha256
                or variant.dialect_id != profile.dialect_id
                or (
                    bool(variant.checkpoint_profile_id)
                    and variant.checkpoint_profile_id
                    != profile.checkpoint_profile_id
                )
                or (bool(variant.preset_id) and variant.preset_id != profile.preset_id)
                or variant.compiler_version != profile.compiler_version
            ):
                raise NotFoundError("找不到 prompt export")

            character_input, style_input = self._rebuild_canon_inputs(
                session,
                ast=ast,
                selection=selection,
                expected_project_id=expected_project_id,
            )

        refs = ExportRefs(
            project_name=project.name,
            prompt_project_title=prompt_project.title,
            checkpoint_filename=variant.checkpoint_filename_snapshot,
        )
        return bundle_from_variant(
            variant_json_blocks=variant.blocks_json,
            variant_lint_json=variant.lint_json,
            variant_conflicts_json=variant.conflicts_json,
            source_text=selection.source_text,
            refs=refs,
            ast=ast,
            profile=profile,
            character=character_input,
            style=style_input,
            created_at=created_at or utc_now_iso(),
        )

    # ------------------------------------------------------------ internals
    @staticmethod
    def _rebuild_canon_inputs(  # type: ignore[no-untyped-def]
        session,
        *,
        ast: PromptAST,
        selection: PromptVersionSelectionSnapshot,
        expected_project_id: str,
    ) -> tuple[CharacterLockInput, StyleInput]:
        """Deterministic re-resolution against IMMUTABLE Canon versions.

        Canon character/style versions are append-only, so resolving the
        stored version IDs against the stored AST reproduces exactly the
        inputs the original compilation used — no live UI state involved.

        Empty inputs are valid only when the historical snapshot intentionally
        selected no corresponding Canon.  A referenced row that is missing,
        belongs to another project/parent, or is only partially identified
        fails closed instead of silently becoming an execution-ready empty
        Canon input.
        """
        character_input = CharacterLockInput()
        style_input = StyleInput()
        has_character = bool(selection.character_id)
        has_character_version = bool(selection.character_version_id)
        subject = ast.primary_subject
        if has_character != has_character_version:
            raise NotFoundError("找不到 prompt export")
        if selection.outfit_id and not has_character:
            raise NotFoundError("找不到 prompt export")
        if has_character:
            if (
                subject is None
                or subject.character_id != selection.character_id
                or subject.character_version_id != selection.character_version_id
                or subject.outfit_id != selection.outfit_id
                or any(
                    extra.character_id
                    or extra.character_version_id
                    or extra.outfit_id
                    for extra in ast.subjects[1:]
                )
            ):
                raise NotFoundError("找不到 prompt export")
        elif any(
            item.character_id or item.character_version_id or item.outfit_id
            for item in ast.subjects
        ):
            raise NotFoundError("找不到 prompt export")
        if has_character:
            characters = CharacterRepository(session)
            character = characters.get(selection.character_id)
            char_version = characters.get_version(selection.character_version_id)
            if (
                character is None
                or char_version is None
                or character.project_id != expected_project_id
                or char_version.character_id != character.id
            ):
                raise NotFoundError("找不到 prompt export")
            outfit_canonical: tuple[str, ...] = ()
            outfit_optional: tuple[str, ...] = ()
            outfit_prohibited: tuple[str, ...] = ()
            if selection.outfit_id:
                outfit = OutfitRepository(session).get(selection.outfit_id)
                if outfit is None or outfit.character_id != character.id:
                    raise NotFoundError("找不到 prompt export")
                outfit_canonical = outfit.canonical_traits
                outfit_optional = outfit.optional_traits
                outfit_prohibited = outfit.prohibited_traits
            adult = derives_adult(selection.content_mode)
            char_res = resolve_character(
                character=character,
                version=char_version,
                ast=ast,
                outfit_canonical=outfit_canonical,
                outfit_optional=outfit_optional,
                outfit_prohibited=outfit_prohibited,
                adult_requested=adult,
                # historical rendering context only; the persisted variant
                # text is authoritative and policy was enforced at save
                adult_allowed=adult,
            )
            character_input = char_res.lock

        has_style = bool(selection.style_profile_id)
        has_style_version = bool(selection.style_version_id)
        if has_style != has_style_version:
            raise NotFoundError("找不到 prompt export")
        if (
            ast.style.style_profile_id != selection.style_profile_id
            or ast.style.style_version_id != selection.style_version_id
        ):
            raise NotFoundError("找不到 prompt export")
        if has_style:
            styles = StyleRepository(session)
            style = styles.get(selection.style_profile_id)
            style_version = styles.get_version(selection.style_version_id)
            if (
                style is None
                or style_version is None
                or style.project_id != expected_project_id
                or style_version.style_profile_id != style.id
            ):
                raise NotFoundError("找不到 prompt export")
            style_res = resolve_style(version=style_version, ast=ast)
            style_input = style_res.style
        return character_input, style_input
