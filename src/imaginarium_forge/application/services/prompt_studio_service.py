"""Visual Prompt Studio application services — Gate A hardened.

Three thin orchestrators; every rule they enforce lives in the domain layer
or the DB. Gate A additions (spec §4–§9):

- A-01: every resolution/compilation carries an ``input_fingerprint`` derived
  from an immutable :class:`PromptCompilationInputSnapshot`; ``compile()``
  ALWAYS re-resolves (callers pass fresh resolutions) and actions that persist
  or export must present the current fingerprint.
- A-02: adult content requires a selected, versioned, eligibility-verified
  character; otherwise resolution yields a blocking conflict.
- A-04: blocked outcomes cannot be persisted; version acceptance requires a
  linked variant whose fingerprint matches the version's stored fingerprint.
- A-05: every prompt version stores an immutable selection snapshot.
- A-06: every persisted variant stores checkpoint identity (id/filename/hash).

None of these services write Canon tables (§10.6).
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict

from imaginarium_forge.application.errors import (
    CompilationBlockedError,
    NotFoundError,
    NoValidVariantError,
    ResolutionInputMismatchError,
    StaleCompilationInputError,
    ValidationFailedError,
)
from imaginarium_forge.application.services.base import ServiceBase, SessionProvider
from imaginarium_forge.application.services.eligibility_service import EligibilityService
from imaginarium_forge.application.services.prompt_profile_service import (
    PromptProfileService,
)
from imaginarium_forge.domain.common.ids import utc_now_iso
from imaginarium_forge.domain.prompt.ast import PromptAST
from imaginarium_forge.domain.prompt.blocks import CompiledBlocks
from imaginarium_forge.domain.prompt.compilation import (
    CharacterLockInput,
    StyleInput,
    compile_prompt,
)
from imaginarium_forge.domain.prompt.conflicts import (
    Conflict,
    ConflictCode,
    ConflictSeverity,
)
from imaginarium_forge.domain.prompt.content_mode import ContentMode, derives_adult
from imaginarium_forge.domain.prompt.input_snapshot import (
    PromptCompilationInputSnapshot,
    PromptVersionSelectionSnapshot,
    ResolutionInputSnapshot,
)
from imaginarium_forge.domain.prompt.lint import LintLevel, LintReport
from imaginarium_forge.domain.prompt.lint_engine import lint_prompt
from imaginarium_forge.domain.prompt.profiles import (
    PROMPT_COMPILER_VERSION,
    ResolvedProfile,
)
from imaginarium_forge.domain.prompt.resolution import (
    CharacterResolution,
    StyleResolution,
    detect_cross_conflicts,
    resolve_character,
    resolve_style,
)
from imaginarium_forge.infrastructure.db.repositories.prompt_repos import (
    ProfileSnapshotRecord,
    ProfileSnapshotRepository,
    PromptProjectRecord,
    PromptProjectRepository,
    PromptProjectVersionRecord,
    PromptProjectVersionRepository,
    PromptVariantRecord,
    PromptVariantRepository,
)


def _validate_project_ownership(
    session: object,
    *,
    project_id: str,
    character_id: str | None,
    style_profile_id: str | None,
) -> None:
    """A2-03: a prompt project under Project X may only reference Canon owned
    by Project X. The composite DB FKs are the last line of defense; this is
    the first, with normalized zh-TW errors."""
    from imaginarium_forge.infrastructure.db.repositories.characters import (
        CharacterRepository,
    )
    from imaginarium_forge.infrastructure.db.repositories.styles import (
        StyleRepository,
    )

    if character_id:
        character = CharacterRepository(session).get(character_id)  # type: ignore[arg-type]
        if character is None:
            raise NotFoundError(f"找不到角色：{character_id}")
        if character.project_id != project_id:
            raise ValidationFailedError("所選角色不屬於此專案（跨專案 Canon 被拒絕）")
    if style_profile_id:
        style = StyleRepository(session).get(style_profile_id)  # type: ignore[arg-type]
        if style is None:
            raise NotFoundError(f"找不到 Style DNA：{style_profile_id}")
        if style.project_id != project_id:
            raise ValidationFailedError(
                "所選 Style DNA 不屬於此專案（跨專案 Canon 被拒絕）"
            )


# --------------------------------------------------------------- projects


class PromptProjectService(ServiceBase):
    """Prompt project + version lifecycle (spec §35 + Gate A A-04/A-05)."""

    def create(
        self,
        *,
        project_id: str,
        title: str,
        source_text: str = "",
        character_id: str | None = None,
        character_version_id: str | None = None,
        style_profile_id: str | None = None,
        style_version_id: str | None = None,
    ) -> PromptProjectRecord:
        if not title.strip():
            raise ValidationFailedError("提示專案標題為必填")
        record = PromptProjectRecord(
            id=str(uuid.uuid4()),
            project_id=project_id,
            title=title.strip(),
            source_text=source_text,
            character_id=character_id,
            character_version_id=character_version_id,
            style_profile_id=style_profile_id,
            style_version_id=style_version_id,
            status="draft",
            current_version_id=None,
            created_at=utc_now_iso(),
            updated_at=utc_now_iso(),
        )
        with self._transaction() as session:
            _validate_project_ownership(
                session,
                project_id=project_id,
                character_id=character_id,
                style_profile_id=style_profile_id,
            )
            PromptProjectRepository(session).add(record)
        return record

    def get(self, prompt_project_id: str) -> PromptProjectRecord:
        record = self._read_only(
            lambda s: PromptProjectRepository(s).get(prompt_project_id)
        )
        if record is None:
            raise NotFoundError(f"找不到提示專案：{prompt_project_id}")
        return record

    def list_for_project(self, project_id: str) -> list[PromptProjectRecord]:
        return self._read_only(
            lambda s: PromptProjectRepository(s).list_for_project(project_id)
        )

    def update_selections(self, prompt_project_id: str, **fields: str | None) -> None:
        allowed = {
            "title", "source_text", "character_id", "character_version_id",
            "style_profile_id", "style_version_id", "status",
        }
        unknown = set(fields) - allowed
        if unknown:
            raise ValidationFailedError(f"不允許更新欄位：{sorted(unknown)}")
        with self._transaction() as session:
            repo = PromptProjectRepository(session)
            existing = repo.get(prompt_project_id)
            if existing is None:
                raise NotFoundError(f"找不到提示專案：{prompt_project_id}")
            _validate_project_ownership(
                session,
                project_id=existing.project_id,
                character_id=fields.get("character_id", existing.character_id),
                style_profile_id=fields.get(
                    "style_profile_id", existing.style_profile_id
                ),
            )
            repo.update_fields(
                prompt_project_id, updated_at=utc_now_iso(), **fields
            )

    # ------------------------------------------------------------- versions
    def add_version(
        self,
        prompt_project_id: str,
        *,
        ast: PromptAST,
        selection_snapshot: PromptVersionSelectionSnapshot,
        input_fingerprint: str,
        change_note: str = "",
    ) -> PromptProjectVersionRecord:
        """Append-only; the new version becomes current.

        Gate A A-05: the selection snapshot (and its SHA-256) is frozen into
        the version row; historical reads never consult the mutable project.
        Gate A A-04 §7.4: the compiling fingerprint is stored so acceptance
        can later require a matching persisted variant.
        """
        with self._transaction() as session:
            projects = PromptProjectRepository(session)
            if projects.get(prompt_project_id) is None:
                raise NotFoundError(f"找不到提示專案：{prompt_project_id}")
            versions = PromptProjectVersionRepository(session)
            record = PromptProjectVersionRecord(
                id=str(uuid.uuid4()),
                prompt_project_id=prompt_project_id,
                version_number=versions.next_version_number(prompt_project_id),
                ast_json=ast.canonical_dump(),
                change_note=change_note,
                accepted=False,
                created_at=utc_now_iso(),
                selection_snapshot_json=selection_snapshot.canonical(),
                selection_snapshot_sha256=selection_snapshot.sha256(),
                input_fingerprint=input_fingerprint,
            )
            versions.add(record)
            session.flush()  # version row must exist before the ownership FK check
            projects.update_fields(
                prompt_project_id,
                current_version_id=record.id,
                updated_at=utc_now_iso(),
            )
        return record

    def get_version(
        self, version_id: str
    ) -> tuple[PromptProjectVersionRecord, PromptAST]:
        """Historical read: stored AST + stored snapshot only (A-05 §8.4)."""
        record = self._read_only(
            lambda s: PromptProjectVersionRepository(s).get(version_id)
        )
        if record is None:
            raise NotFoundError(f"找不到提示版本：{version_id}")
        return record, PromptAST.model_validate_json(record.ast_json)

    def get_version_selection(
        self, version_id: str
    ) -> PromptVersionSelectionSnapshot:
        record, _ = self.get_version(version_id)
        return PromptVersionSelectionSnapshot.model_validate_json(
            record.selection_snapshot_json
        )

    def list_versions(
        self, prompt_project_id: str
    ) -> list[PromptProjectVersionRecord]:
        return self._read_only(
            lambda s: PromptProjectVersionRepository(s).list_versions(prompt_project_id)
        )

    def accept_version(self, version_id: str) -> None:
        """One-way accept, guarded by Gate A A-04 §7.4:

        at least one linked variant exists AND some linked variant's
        ``input_fingerprint`` equals the version's stored fingerprint.
        (Blocked outcomes can never persist, so linked variants are valid.)
        """
        with self._transaction() as session:
            versions = PromptProjectVersionRepository(session)
            record = versions.get(version_id)
            if record is None:
                raise NotFoundError(f"找不到提示版本：{version_id}")
            variants = PromptVariantRepository(session).list_for_version(version_id)
            if not variants:
                raise NoValidVariantError(
                    "接受版本前需要至少一個已編譯的 variant"
                )
            if not any(
                v.input_fingerprint == record.input_fingerprint
                and v.compilation_status == "valid"
                for v in variants
            ):
                raise StaleCompilationInputError(
                    "輸入在編譯後已變更；請重新編譯並儲存後再接受此版本"
                )
            versions.accept(version_id)

    def set_current(self, prompt_project_id: str, version_id: str) -> None:
        with self._transaction() as session:
            versions = PromptProjectVersionRepository(session)
            version = versions.get(version_id)
            if version is None or version.prompt_project_id != prompt_project_id:
                raise ValidationFailedError("指定版本不存在或不屬於此提示專案")
            PromptProjectRepository(session).update_fields(
                prompt_project_id,
                current_version_id=version_id,
                updated_at=utc_now_iso(),
            )


# -------------------------------------------------------------- resolution


class ResolutionOutcome(BaseModel):
    model_config = ConfigDict(frozen=True)

    character: CharacterLockInput
    style: StyleInput
    conflicts: tuple[Conflict, ...] = ()
    content_mode: ContentMode = ContentMode.GENERAL
    adult_requested: bool = False
    adult_allowed: bool = False
    eligibility_message: str = ""
    #: A2-02 — identity of exactly what was resolved; compile() must match it
    input_fingerprint: str = ""

    @property
    def has_blocking_conflicts(self) -> bool:
        return any(c.severity is ConflictSeverity.ERROR for c in self.conflicts)


def _adult_requires_character_conflict() -> Conflict:
    return Conflict(
        code=ConflictCode.ADULT_CONTENT_REQUIRES_VERIFIED_CHARACTER,
        severity=ConflictSeverity.ERROR,
        source_a="content_mode",
        source_b="selection",
        message_zh_tw=(
            "成人內容提示需要選定「已建檔、已選版本、且通過資格驗證」的成人角色；"
            "自由描述主體不可用於成人內容。"
        ),
        suggested_actions=("select_verified_character", "change_content_mode"),
    )


class PromptResolutionService:
    """Load Canon, gate adult content via Phase 1 eligibility, resolve.

    Gate A A-02: adult content (content_mode ∈ {suggestive, explicit_adult})
    REQUIRES character_id + character_version_id + allowed verdict — a missing
    selection is a blocking conflict, not a silent pass. The eligibility
    service remains the only policy authority (§4.2); this class transports
    its verdict and never re-implements policy.
    """

    def __init__(
        self,
        session_factory: SessionProvider,
        *,
        characters: object,
        versions: object,
        outfits: object,
        styles: object,
        eligibility: EligibilityService,
    ) -> None:
        self._characters = characters
        self._versions = versions
        self._outfits = outfits
        self._styles = styles
        self._eligibility = eligibility
        _ = session_factory  # reserved for future read paths

    def resolve(
        self,
        *,
        ast: PromptAST,
        character_id: str | None,
        character_version_id: str | None,
        outfit_id: str | None = None,
        style_version_id: str | None = None,
        content_mode: ContentMode = ContentMode.GENERAL,
    ) -> ResolutionOutcome:
        if len(ast.subjects) > 1:
            raise ValidationFailedError(
                "多人 Prompt AST 尚未支援 Canon resolution；"
                "不得只解析 primary subject"
            )
        adult_requested = derives_adult(content_mode)
        conflicts: list[Conflict] = []

        # ---- A-02 gate: adult ⇒ selected + versioned + verified ----------
        if adult_requested and not (character_id and character_version_id):
            conflicts.append(_adult_requires_character_conflict())

        # ---- character side --------------------------------------------
        character_input = CharacterLockInput()
        adult_allowed = False
        eligibility_message = ""
        char_res: CharacterResolution | None = None
        if character_id and character_version_id:
            character = self._characters.get_character(character_id)  # type: ignore[attr-defined]
            version = self._versions.get_version(character_version_id)  # type: ignore[attr-defined]
            if version.character_id != character_id:
                raise ValidationFailedError("角色版本不屬於所選角色")
            outfit_canonical: tuple[str, ...] = ()
            outfit_optional: tuple[str, ...] = ()
            outfit_prohibited: tuple[str, ...] = ()
            if outfit_id:
                outfit = self._outfits.get_outfit(outfit_id)  # type: ignore[attr-defined]
                if outfit.character_id != character_id:
                    raise ValidationFailedError("服裝不屬於所選角色")
                outfit_canonical = outfit.canonical_traits
                outfit_optional = outfit.optional_traits
                outfit_prohibited = outfit.prohibited_traits
            if adult_requested:
                verdict = self._eligibility.evaluate(
                    character_id=character_id,
                    version_id=character_version_id,
                    adult_content_requested=True,
                )
                adult_allowed = verdict.allowed
                eligibility_message = verdict.message
            char_res = resolve_character(
                character=character,
                version=version,
                ast=ast,
                outfit_canonical=outfit_canonical,
                outfit_optional=outfit_optional,
                outfit_prohibited=outfit_prohibited,
                adult_requested=adult_requested,
                adult_allowed=adult_allowed,
            )
            character_input = char_res.lock
            conflicts.extend(char_res.conflicts)

        # ---- style side -------------------------------------------------
        style_input = StyleInput()
        style_res: StyleResolution | None = None
        if style_version_id:
            style_version = self._styles.get_version(style_version_id)  # type: ignore[attr-defined]
            style_res = resolve_style(version=style_version, ast=ast)
            style_input = style_res.style
            conflicts.extend(style_res.conflicts)

        resolution_snapshot = ResolutionInputSnapshot(
            prompt_ast_canonical_json=ast.canonical_dump(),
            character_id=character_id or "",
            character_version_id=character_version_id or "",
            outfit_id=outfit_id or "",
            style_version_id=style_version_id or "",
            content_mode=content_mode,
        )
        return ResolutionOutcome(
            character=character_input,
            style=style_input,
            conflicts=tuple(conflicts),
            content_mode=content_mode,
            adult_requested=adult_requested,
            adult_allowed=adult_allowed,
            eligibility_message=eligibility_message,
            input_fingerprint=resolution_snapshot.fingerprint(),
        )


# ------------------------------------------------------------- compilation


class CompilationOutcome(BaseModel):
    model_config = ConfigDict(frozen=True)

    blocks: CompiledBlocks
    lint: LintReport
    conflicts: tuple[Conflict, ...]
    profile: ResolvedProfile
    snapshot_id: str
    checkpoint_known: bool
    # Gate A A-01/A-06: identity of THIS compilation's inputs
    input_snapshot: PromptCompilationInputSnapshot
    input_fingerprint: str
    checkpoint_sha256_snapshot: str = ""
    checkpoint_hash_status: str = "not_computed"

    @property
    def blocked(self) -> bool:
        """§8.1: policy/structural lint errors block final compilation output."""
        return self.lint.has_errors


class PromptCompilationService(ServiceBase):
    def __init__(
        self, session_factory: SessionProvider, profiles: PromptProfileService
    ) -> None:
        super().__init__(session_factory)
        self._profiles = profiles

    def compile(
        self,
        *,
        ast: PromptAST,
        resolution: ResolutionOutcome,
        dialect_id: str,
        checkpoint_profile_id: str = "",
        preset_id: str = "",
        checkpoint_filename: str = "",
        checkpoint_id: str = "",
        checkpoint_sha256: str = "",
        checkpoint_hash_status: str = "not_computed",
        source_text: str = "",
        character_id: str | None = None,
        character_version_id: str | None = None,
        outfit_id: str | None = None,
        style_profile_id: str | None = None,
        style_version_id: str | None = None,
    ) -> CompilationOutcome:
        """Deterministic pipeline over a FRESH resolution (A-01 §4.3).

        The caller must pass the resolution produced for exactly these inputs;
        the input snapshot freezes every meaning-bearing field and its
        fingerprint travels with the outcome so save/accept/export/experiment
        can verify nothing changed in between.
        """
        if len(ast.subjects) > 1:
            raise ValidationFailedError(
                "多人 Prompt AST 尚未支援 deterministic compilation；"
                "不得只編譯 primary subject"
            )
        # A2-02: the resolution must have been computed for EXACTLY these
        # inputs — foreign resolutions, reused eligibility verdicts, changed
        # ASTs, or changed content modes are all rejected here.
        expected = ResolutionInputSnapshot(
            prompt_ast_canonical_json=ast.canonical_dump(),
            character_id=character_id or "",
            character_version_id=character_version_id or "",
            outfit_id=outfit_id or "",
            style_version_id=style_version_id or "",
            content_mode=resolution.content_mode,
        )
        if resolution.input_fingerprint != expected.fingerprint():
            raise ResolutionInputMismatchError(
                "解析結果與編譯請求的輸入不一致；請以相同的角色/版本/服裝/風格/"
                "AST/內容模式重新解析後再編譯。"
            )
        profile, checkpoint_known = self._profiles.resolve_stack(
            dialect_id=dialect_id,
            checkpoint_filename=checkpoint_filename,
            checkpoint_profile_id=checkpoint_profile_id,
            preset_id=preset_id,
        )
        input_snapshot = PromptCompilationInputSnapshot(
            source_text=source_text,
            prompt_ast_canonical_json=ast.canonical_dump(),
            character_id=character_id or "",
            character_version_id=character_version_id or "",
            outfit_id=outfit_id or "",
            style_profile_id=style_profile_id or "",
            style_version_id=style_version_id or "",
            adult_content_requested=resolution.adult_requested,
            content_mode=resolution.content_mode,
            dialect_id=dialect_id,
            checkpoint_id=checkpoint_id,
            checkpoint_filename=checkpoint_filename,
            checkpoint_profile_id=checkpoint_profile_id,
            preset_id=preset_id,
            compiler_version=PROMPT_COMPILER_VERSION,
        )
        snapshot_id = self._persist_snapshot(profile)
        conflicts = resolution.conflicts + detect_cross_conflicts(
            ast, profile, resolution.character, resolution.style
        )
        blocks = compile_prompt(
            ast,
            profile,
            character=resolution.character,
            style=resolution.style,
            conflicts=conflicts,
        )
        lint = lint_prompt(
            ast=ast,
            blocks=blocks,
            conflicts=conflicts,
            profile=profile,
            character=resolution.character,
            style=resolution.style,
            checkpoint_known=checkpoint_known,
        )
        return CompilationOutcome(
            blocks=blocks,
            lint=lint,
            conflicts=conflicts,
            profile=profile,
            snapshot_id=snapshot_id,
            checkpoint_known=checkpoint_known,
            input_snapshot=input_snapshot,
            input_fingerprint=input_snapshot.fingerprint(),
            checkpoint_sha256_snapshot=checkpoint_sha256,
            checkpoint_hash_status=checkpoint_hash_status,
        )

    def persist_variant(
        self,
        *,
        prompt_project_id: str,
        prompt_project_version_id: str,
        outcome: CompilationOutcome,
        current_input_fingerprint: str | None = None,
    ) -> PromptVariantRecord:
        """Write-once record of a VALID compilation.

        Gate A A-04: blocked outcomes raise ``CompilationBlockedError``.
        Gate A A-01 §4.5: when the caller supplies the fingerprint of the
        CURRENT inputs, a mismatch raises ``StaleCompilationInputError``.
        Gate A A-06: checkpoint identity is frozen into the row.
        """
        if outcome.blocked:
            raise CompilationBlockedError(
                "阻斷級錯誤存在；此編譯結果不可保存為 variant"
            )
        if (
            current_input_fingerprint is not None
            and current_input_fingerprint != outcome.input_fingerprint
        ):
            raise StaleCompilationInputError(
                "輸入在編譯後已變更；請重新編譯"
            )
        lint_status = (
            "warnings" if outcome.lint.by_level(LintLevel.WARNING) else "ok"
        )
        record = PromptVariantRecord(
            id=str(uuid.uuid4()),
            prompt_project_version_id=prompt_project_version_id,
            prompt_project_id=prompt_project_id,
            profile_snapshot_id=outcome.snapshot_id,
            positive_prompt=outcome.blocks.positive_prompt,
            negative_prompt=outcome.blocks.negative_prompt,
            natural_language_prompt=outcome.blocks.natural_language_prompt,
            blocks_json=outcome.blocks.model_dump_json(),
            lint_json=outcome.lint.model_dump_json(),
            conflicts_json="["
            + ",".join(c.model_dump_json() for c in outcome.conflicts)
            + "]",
            compiler_version=PROMPT_COMPILER_VERSION,
            created_at=utc_now_iso(),
            compilation_status="valid",
            lint_status=lint_status,
            input_fingerprint=outcome.input_fingerprint,
            checkpoint_id=outcome.input_snapshot.checkpoint_id,
            checkpoint_filename_snapshot=outcome.input_snapshot.checkpoint_filename,
            checkpoint_sha256_snapshot=outcome.checkpoint_sha256_snapshot,
            checkpoint_hash_status=outcome.checkpoint_hash_status,
            dialect_id=outcome.input_snapshot.dialect_id,
            checkpoint_profile_id=outcome.input_snapshot.checkpoint_profile_id,
            preset_id=outcome.input_snapshot.preset_id,
        )
        with self._transaction() as session:
            PromptVariantRepository(session).add(record)
        return record

    def list_variants_for_version(
        self, version_id: str
    ) -> list[PromptVariantRecord]:
        """A2-04: the variants linked to one stored version (historical view)."""
        return self._read_only(
            lambda s: PromptVariantRepository(s).list_for_version(version_id)
        )

    def get_variant(self, variant_id: str) -> PromptVariantRecord:
        record = self._read_only(
            lambda s: PromptVariantRepository(s).get(variant_id)
        )
        if record is None:
            raise NotFoundError(f"找不到 variant：{variant_id}")
        return record

    # ------------------------------------------------------------ internals
    def _persist_snapshot(self, profile: ResolvedProfile) -> str:
        """SHA-256 dedupe: identical resolved profiles share one snapshot row."""
        digest = profile.sha256()
        with self._transaction() as session:
            repo = ProfileSnapshotRepository(session)
            existing = repo.get_by_sha256(digest)
            if existing is not None:
                return existing.id
            snapshot = ProfileSnapshotRecord(
                id=str(uuid.uuid4()),
                dialect_id=profile.dialect_id,
                dialect_version=profile.dialect_version,
                checkpoint_profile_id=profile.checkpoint_profile_id,
                checkpoint_profile_version=profile.checkpoint_profile_version,
                preset_id=profile.preset_id,
                preset_version=profile.preset_version,
                resolved_json=profile.canonical_dump(),
                sha256=digest,
                compiler_version=profile.compiler_version,
                created_at=utc_now_iso(),
            )
            repo.add(snapshot)
            return snapshot.id
