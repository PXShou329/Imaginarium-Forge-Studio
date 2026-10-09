"""Atomic resolve-and-compile orchestration.

``compile_current`` owns the ENTIRE chain for one compilation:

    load current prompt-project selections
    → validate project ownership
    → validate the selected creative mode
    → run eligibility (inside resolve)
    → resolve Character / Outfit / Style Canon
    → detect conflicts
    → resolve Prompt Profile stack
    → compile + lint
    → one immutable input snapshot + fingerprint

Callers (the Studio UI and any future page/import/test) therefore can never
pair a free-standing ``ResolutionOutcome`` with unrelated IDs. The low-level
``PromptCompilationService.compile`` additionally verifies resolution-input
equality itself (`ResolutionInputMismatchError`), so even direct bypass
attempts are rejected — this service is the convenient front door, not the
only lock.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from imaginarium_forge.application.errors import (
    NotFoundError,
    ValidationFailedError,
)
from imaginarium_forge.application.services.base import ServiceBase, SessionProvider
from imaginarium_forge.application.services.prompt_studio_service import (
    CompilationOutcome,
    PromptCompilationService,
    PromptResolutionService,
    _validate_project_ownership,
)
from imaginarium_forge.domain.prompt.ast import PromptAST
from imaginarium_forge.domain.prompt.content_mode import (
    ContentMode,
)
from imaginarium_forge.infrastructure.db.repositories.prompt_repos import (
    PromptProjectRepository,
)


class CompileCurrentPromptRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    prompt_project_id: str
    ast: PromptAST
    source_text: str = ""
    content_mode: ContentMode = ContentMode.GENERAL
    dialect_id: str = ""
    checkpoint_profile_id: str = ""
    preset_id: str = ""
    checkpoint_id: str = ""
    checkpoint_filename: str = ""
    checkpoint_sha256: str = ""
    checkpoint_hash_status: str = "not_computed"


class PromptOrchestrationService(ServiceBase):
    def __init__(
        self,
        session_factory: SessionProvider,
        *,
        resolution: PromptResolutionService,
        compilation: PromptCompilationService,
    ) -> None:
        super().__init__(session_factory)
        self._resolution = resolution
        self._compilation = compilation

    def compile_current(
        self, request: CompileCurrentPromptRequest
    ) -> CompilationOutcome:
        if len(request.ast.subjects) > 1:
            raise ValidationFailedError(
                "多人 Prompt AST 尚未支援 Prompt Studio 編譯；"
                "已明確阻擋 primary-only 路徑"
            )
        # ---- load the prompt project's CURRENT selections ----------------
        with self._transaction() as session:
            record = PromptProjectRepository(session).get(request.prompt_project_id)
            if record is None:
                raise NotFoundError(
                    f"找不到提示專案：{request.prompt_project_id}"
                )
            # ---- ownership before anything else --------------------
            _validate_project_ownership(
                session,
                project_id=record.project_id,
                character_id=record.character_id,
                style_profile_id=record.style_profile_id,
            )
        character_id = record.character_id
        character_version_id = record.character_version_id
        outfit_id = None  # outfit selection is per-compile UI state (AST carries it)
        subject = request.ast.primary_subject
        if subject is not None and subject.outfit_id:
            outfit_id = subject.outfit_id
        style_profile_id = record.style_profile_id
        style_version_id = record.style_version_id

        # ---- resolve (eligibility inside) → compile (fingerprint-bound) ---
        resolution = self._resolution.resolve(
            ast=request.ast,
            character_id=character_id,
            character_version_id=character_version_id,
            outfit_id=outfit_id,
            style_version_id=style_version_id,
            content_mode=request.content_mode,
        )
        return self._compilation.compile(
            ast=request.ast,
            resolution=resolution,
            dialect_id=request.dialect_id,
            checkpoint_profile_id=request.checkpoint_profile_id,
            preset_id=request.preset_id,
            checkpoint_filename=request.checkpoint_filename,
            checkpoint_id=request.checkpoint_id,
            checkpoint_sha256=request.checkpoint_sha256,
            checkpoint_hash_status=request.checkpoint_hash_status,
            source_text=request.source_text,
            character_id=character_id,
            character_version_id=character_version_id,
            outfit_id=outfit_id,
            style_profile_id=style_profile_id,
            style_version_id=style_version_id,
        )
