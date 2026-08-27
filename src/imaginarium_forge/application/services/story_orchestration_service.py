"""Story generation orchestration (Gate A, A3-01/A3-02/A3-09/A3-10).

This is the single public business API for producing scene prose. It exists
because the previous design let a caller hand four unrelated arguments to the
low-level generator and get a "completed" run out of it: Scene 1 paired with
Scene 2's Scene Card, invented planning version IDs, an explicit-adult
context with no eligibility evaluation at all.

The fix is structural rather than defensive. The orchestrator:

1. loads the scene and derives the project through chapter → outline;
2. resolves every planning version from persisted data (accepted by default);
3. pins the exact Character Versions recorded on the Scene Card;
4. re-evaluates adult eligibility against those exact versions;
5. builds the context itself;
6. freezes everything into a ``GenerationInputSnapshot`` and hashes it;
7. commits a RUNNING run BEFORE any provider I/O;
8. calls the provider with no write transaction open;
9. finalizes the run exactly once.

There is no argument shape that can express a mismatched pair, so the class
of defect is closed rather than merely guarded.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, replace
from threading import Event

from imaginarium_forge.application.errors import (
    NotFoundError,
    SourcePlanningChainUnavailableError,
    ValidationFailedError,
)
from imaginarium_forge.application.services.adult_output_review_service import (
    assert_adult_audit_coverage,
    assert_adult_draft_reviewed,
    build_adult_participant_manifest,
    build_adult_story_provider_contract,
)
from imaginarium_forge.application.services.base import ServiceBase, SessionProvider
from imaginarium_forge.application.services.scene_generation_service import (
    DraftOrigin,
    RevisionRequest,
    RunStatus,
    _word_count,
    build_revision_contract,
)
from imaginarium_forge.application.services.story_context_service import (
    BuildStoryContextRequest,
    ResolvedStoryContext,
    StoryContextService,
)
from imaginarium_forge.canonical import canonical_json
from imaginarium_forge.config.settings import AppSettings
from imaginarium_forge.domain.character.presentation_cues import detect_presentation_cues
from imaginarium_forge.domain.common.ids import utc_now_iso
from imaginarium_forge.domain.creative.models import ParticipantManifest
from imaginarium_forge.domain.prompt.content_mode import derives_adult, preflight_content_mode
from imaginarium_forge.domain.story.context import (
    ContextBudgetSnapshot,
    ContextPriority,
    ContextSourceRef,
    SourceEntityType,
)
from imaginarium_forge.domain.story.generation_input import (
    GenerationInputSnapshot,
    GenerationOptionsSnapshot,
    ParticipantPin,
    PlanningMode,
)
from imaginarium_forge.domain.story.models import (
    IntensityLevel,
    SceneCard,
    StoryRequirement,
)
from imaginarium_forge.domain.story.output_envelope import (
    ADULT_STORY_OUTPUT_ENVELOPE_VERSION,
    AdultStoryOutputEnvelope,
)
from imaginarium_forge.domain.story.short_story import (
    CompleteStoryBrief,
    StoryGenerationPurpose,
)
from imaginarium_forge.infrastructure.db.repositories.adult_output_repos import (
    AdultOutputCandidateRecord,
    AdultOutputCandidateRepository,
)
from imaginarium_forge.infrastructure.db.repositories.story_repos import (
    DraftRecord,
    GenerationRunRecord,
    GenerationRunRepository,
    SceneCardVersionRepository,
    SceneDraftRepository,
    StorySceneRepository,
    VersionedEntityRepository,
)
from imaginarium_forge.providers.base import LLMProvider
from imaginarium_forge.providers.contracts import GenerationOptions, GenerationRequest
from imaginarium_forge.providers.errors import (
    InvalidStructuredOutputError,
    ModelNotFoundError,
    ProviderError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    RequestCancelledError,
)
from imaginarium_forge.providers.structured_repair import generate_with_repair


class ReasonCode:
    """Normalized failure codes stored on the run (A3-09/A3-10)."""

    NONE = ""
    EMPTY_OUTPUT = "empty_generation_output"
    PROVIDER_TIMEOUT = "provider_timeout"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    MODEL_NOT_FOUND = "model_not_found"
    CANCELLED = "cancelled_by_user"
    PROVIDER_ERROR = "provider_error"
    PROCESS_INTERRUPTED = "process_interrupted"
    OUTPUT_RECLASSIFICATION_REQUIRED = "output_reclassification_required"
    OUTPUT_POLICY_QUARANTINED = "output_policy_quarantined"
    ADULT_STRUCTURED_OUTPUT_INVALID = "adult_structured_output_invalid"
    ADULT_ENVELOPE_MISMATCH = "adult_output_envelope_mismatch"
    ADULT_OUTPUT_PENDING_REVIEW = "adult_output_pending_review"


@dataclass(frozen=True, slots=True)
class GenerateSceneRequest:
    """A3-R12: ``planning_mode`` decides whether draft planning may be used."""

    scene_id: str
    model: str
    scene_card_version_id: str | None = None
    requirement_version_id: str | None = None
    bible_version_id: str | None = None
    outline_version_id: str | None = None
    chapter_plan_version_id: str | None = None
    user_instruction: str = ""
    style_examples: tuple[str, ...] = ()
    temperature: float = 0.8
    timeout_s: float = 300.0
    max_context_chars: int | None = None
    stream: bool = False
    seed: int | None = None
    max_output_tokens: int | None = None
    top_p: float | None = None
    planning_mode: PlanningMode = PlanningMode.ACCEPTED
    preview_warning_acknowledged: bool = False
    context_budget: ContextBudgetSnapshot | None = None
    # Appended after every pre-R1 field for positional caller compatibility.
    generation_purpose: StoryGenerationPurpose = StoryGenerationPurpose.SCENE
    complete_story_brief: CompleteStoryBrief | None = None
    structured_must_avoid: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ReviseSceneRequest:
    """A3-R03: an ordinary revision replays the SOURCE RUN's planning chain.

    Re-deriving the chain from current accepted pointers was the defect:
    accepting new planning versions retroactively changed what an old draft
    had been revised against. If the source chain cannot be reconstructed,
    this fails closed — see ``RecoveryRebaseRequest`` for the audited escape.
    """

    draft_id: str
    model: str
    revision: RevisionRequest
    user_instruction: str = ""
    temperature: float = 0.7
    timeout_s: float = 300.0
    max_context_chars: int | None = None
    stream: bool = False
    seed: int | None = None
    max_output_tokens: int | None = None
    top_p: float | None = None
    context_budget: ContextBudgetSnapshot | None = None
    structured_must_avoid: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RecoveryRebaseRequest:
    """A3-R03: the ONLY way to continue when the source chain is unavailable.

    This is not a revision. It produces a clearly-labelled Recovery Rebase
    draft under a NEW planning chain, records what was missing, and leaves the
    original draft and run untouched.
    """

    draft_id: str
    model: str
    rebase_reason: str
    revision: RevisionRequest
    scene_card_version_id: str | None = None
    chapter_plan_version_id: str | None = None
    outline_version_id: str | None = None
    bible_version_id: str | None = None
    requirement_version_id: str | None = None
    user_instruction: str = ""
    temperature: float = 0.7
    timeout_s: float = 300.0
    max_context_chars: int | None = None
    stream: bool = False
    context_budget: ContextBudgetSnapshot | None = None
    # Appended after every pre-R1 field for positional caller compatibility.
    generation_purpose: StoryGenerationPurpose | None = None
    complete_story_brief: CompleteStoryBrief | None = None
    structured_must_avoid: tuple[str, ...] = ()


@dataclass(slots=True)
class OrchestratedOutcome:
    run_id: str
    status: RunStatus
    snapshot: GenerationInputSnapshot
    draft: DraftRecord | None = None
    partial_draft: DraftRecord | None = None
    candidate_id: str | None = None
    reason_code: str = ReasonCode.NONE
    error_reason: str = ""
    planning_mode: PlanningMode = PlanningMode.ACCEPTED
    is_recovery_rebase: bool = False


class _OrchestrationBase(ServiceBase):
    def __init__(
        self,
        session_factory: SessionProvider,
        *,
        provider: LLMProvider,
        provider_name: str = "ollama",
        eligibility: object | None = None,
        settings: AppSettings | None = None,
    ) -> None:
        super().__init__(session_factory)
        self._provider = provider
        self._provider_name = provider_name
        self._eligibility = eligibility
        self._settings = settings
        self._context = StoryContextService(session_factory)

    # ------------------------------------------------ shared context build
    def _build_bound_context(
        self,
        request: BuildStoryContextRequest,
        *,
        additional_positive_texts: tuple[str, ...] = (),
    ) -> tuple[ResolvedStoryContext, tuple[str, ...]]:
        """A3-R04: ONE context-building path for generation AND revision.

        The revision path used to evaluate eligibility, record its fingerprint
        on the snapshot, and then send a context built WITHOUT it — so the run
        looked eligibility-bound while the provider saw a context whose
        fingerprint matched the non-adult build. That is worse than not doing
        it at all, because the record looked correct. Both paths now run the
        same two-pass build: resolve → evaluate → rebind → rebuild.
        """
        resolved = self._context.build_for_scene(request)
        fingerprint, evaluation_ids = self._evaluate_eligibility(
            resolved,
            additional_positive_texts=additional_positive_texts,
        )
        if not fingerprint:
            return resolved, evaluation_ids
        rebound = self._context.build_for_scene(
            replace(request, eligibility_fingerprint=fingerprint)
        )
        return rebound, evaluation_ids

    # -------------------------------------------------------- eligibility
    def _evaluate_eligibility(
        self,
        resolved: ResolvedStoryContext,
        *,
        additional_positive_texts: tuple[str, ...] = (),
    ) -> tuple[str, tuple[str, ...]]:
        """A3-01 §7.2: adult content is re-evaluated at generation time
        against the EXACT versions pinned on the Scene Card. An empty
        eligibility fingerprint can no longer accompany an adult run."""
        requirement = resolved.requirement
        card = resolved.scene_card
        # Scan provider-visible positive context. Structured models with both
        # positive and negative fields are read through their typed boundary;
        # rendered-label prefix filtering is unsafe because a positive string
        # may itself contain a newline followed by a forged label.
        positive_context: list[str] = []
        for block in resolved.package.blocks:
            if block.priority in {
                ContextPriority.FORBIDDEN_REVEALS,
                ContextPriority.AUTHOR_PROHIBITIONS,
            }:
                continue
            if block.priority is ContextPriority.SCENE_CARD:
                positive_context.extend(_scene_card_positive_texts(card))
                continue
            elif block.priority is ContextPriority.STORY_REQUIREMENT:
                if requirement is not None:
                    positive_context.extend(
                        _story_requirement_positive_texts(requirement)
                    )
                continue
            elif block.priority is ContextPriority.COMPLETE_STORY_BRIEF:
                brief = resolved.complete_story_brief
                if brief is None:
                    raise ValidationFailedError(
                        "完整短篇上下文缺少 typed brief，無法安全判定成人意圖。"
                    )
                positive_context.extend(_complete_story_positive_texts(brief))
                continue
            positive_context.append(block.body)
        # Revision source prose is appended to the provider prompt after the
        # context package is built. It must enter the same eligibility scan,
        # without creating a second persisted copy or changing generation
        # message bytes.
        positive_context.extend(
            text.strip() for text in additional_positive_texts if text.strip()
        )
        preflight = preflight_content_mode(card.content_mode, *positive_context)
        requirement_is_adult = requirement is not None and (
            derives_adult(requirement.content_mode)
            or requirement.intimacy_intensity is IntensityLevel.EXPLICIT
        )
        adult_intent = (
            derives_adult(card.content_mode)
            or requirement_is_adult
            or preflight.confirmation_required
        )
        if not adult_intent:
            return "", ()
        presentation_cues = detect_presentation_cues(*positive_context)
        if presentation_cues.has_conflict:
            raise ValidationFailedError(
                "成人性內容的故事上下文包含明確未成年期或孩童化線索；"
                "請移除該呈現，且所有參與者都必須綁定可驗證的成人角色版本。"
            )
        if not derives_adult(card.content_mode):
            raise ValidationFailedError(
                "故事需求或場景文字包含成人性內容，但 Scene Card 尚未明確設為成人模式；"
                "請先重新分類並綁定所有參與角色。"
            )
        if self._eligibility is None:
            raise ValidationFailedError("成人內容生成需要資格驗證服務；目前未提供。")
        if not resolved.participants:
            raise ValidationFailedError("成人內容場景必須有參與角色，且每一位都需通過資格驗證。")
        allowed, results, evaluation_ids = self._eligibility.evaluate_many_audited(  # type: ignore[attr-defined]
            participants=[(p.character_id, p.character_version_id) for p in resolved.participants],
            adult_content_requested=True,
        )
        if not allowed:
            blockers = "、".join(r.message for r in results if not r.allowed)
            raise ValidationFailedError(f"成人內容資格驗證未通過：{blockers or '未提供理由'}")
        combined = ";".join(sorted(r.input_fingerprint for r in results))
        return combined, tuple(evaluation_ids)

    # ------------------------------------------------------------- runs
    def _start_run(self, record: GenerationRunRecord) -> None:
        """A3-10: commit RUNNING before provider I/O, so a crash still leaves
        evidence that the attempt happened."""
        with self._transaction() as session:
            GenerationRunRepository(session).add(record)

    def _call_provider(
        self,
        *,
        system: str,
        prompt: str,
        model: str,
        options: GenerationOptionsSnapshot,
        cancel: Event | None,
        on_chunk: Callable[[str], None] | None,
    ) -> tuple[RunStatus, str, str, str]:
        """Returns (status, text, reason_code, error_reason).

        No write transaction is open here (spec §39). Cancellation keeps the
        chunks already received so the author never loses partial prose.
        """
        request = GenerationRequest(
            model=model,
            system=system,
            prompt=prompt,
            options=GenerationOptions(
                temperature=options.temperature,
                top_p=options.top_p if options.top_p is not None else 0.9,
                seed=options.seed,
                num_predict=options.max_output_tokens,
            ),
            timeout_s=options.timeout_s,
        )
        text = ""
        try:
            if options.stream:
                chunks: list[str] = []
                for chunk in self._provider.stream_text(request, cancel=cancel):
                    chunks.append(chunk)
                    if cancel is not None and cancel.is_set():
                        raise RequestCancelledError("使用者已取消生成")
                text = "".join(chunks)
            else:
                text = self._provider.generate_text(request, cancel=cancel).text
        except RequestCancelledError as exc:
            return (
                RunStatus.CANCELLED,
                "".join(chunks) if options.stream else text,
                ReasonCode.CANCELLED,
                str(exc),
            )
        except ProviderTimeoutError as exc:
            return RunStatus.TIMEOUT, text, ReasonCode.PROVIDER_TIMEOUT, str(exc)
        except ProviderUnavailableError as exc:
            return RunStatus.FAILED, text, ReasonCode.PROVIDER_UNAVAILABLE, str(exc)
        except ModelNotFoundError as exc:
            return RunStatus.FAILED, text, ReasonCode.MODEL_NOT_FOUND, str(exc)
        except ProviderError as exc:
            return RunStatus.FAILED, text, ReasonCode.PROVIDER_ERROR, str(exc)

        # A3-09: whitespace is not prose. A "completed" run with no draft was
        # the original defect — it looked successful and produced nothing.
        if not text.strip():
            return (
                RunStatus.FAILED,
                "",
                ReasonCode.EMPTY_OUTPUT,
                "提供者回傳空白內容（無有效正文）",
            )
        return RunStatus.COMPLETED, text, ReasonCode.NONE, ""

    def _call_adult_provider(
        self,
        *,
        system: str,
        prompt: str,
        model: str,
        options: GenerationOptionsSnapshot,
        cancel: Event | None,
    ) -> tuple[
        RunStatus,
        AdultStoryOutputEnvelope | None,
        str,
        str,
    ]:
        """Generate one schema-validated adult envelope without streaming.

        No provider text is surfaced as a draft or callback.  Even a valid
        envelope remains quarantined until a separate durable review receipt.
        """
        request = GenerationRequest(
            model=model,
            system=system,
            prompt=prompt,
            options=GenerationOptions(
                temperature=options.temperature,
                top_p=options.top_p if options.top_p is not None else 0.9,
                seed=options.seed,
                num_predict=options.max_output_tokens,
            ),
            timeout_s=options.timeout_s,
        )
        if cancel is not None and cancel.is_set():
            return (
                RunStatus.CANCELLED,
                None,
                ReasonCode.CANCELLED,
                "使用者已取消生成",
            )
        try:
            result = generate_with_repair(
                self._provider,
                request,
                AdultStoryOutputEnvelope,
                cancel=cancel,
            )
            envelope = AdultStoryOutputEnvelope.model_validate(result.data)
        except RequestCancelledError as exc:
            return RunStatus.CANCELLED, None, ReasonCode.CANCELLED, str(exc)
        except ProviderTimeoutError as exc:
            return RunStatus.TIMEOUT, None, ReasonCode.PROVIDER_TIMEOUT, str(exc)
        except ProviderUnavailableError as exc:
            return RunStatus.FAILED, None, ReasonCode.PROVIDER_UNAVAILABLE, str(exc)
        except ModelNotFoundError as exc:
            return RunStatus.FAILED, None, ReasonCode.MODEL_NOT_FOUND, str(exc)
        except InvalidStructuredOutputError as exc:
            return (
                RunStatus.FAILED,
                None,
                ReasonCode.ADULT_STRUCTURED_OUTPUT_INVALID,
                f"成人結構化輸出在修復次數用盡後仍無效：{exc}",
            )
        except ValueError as exc:
            return (
                RunStatus.FAILED,
                None,
                ReasonCode.ADULT_STRUCTURED_OUTPUT_INVALID,
                f"成人結構化輸出無法通過 envelope 驗證：{exc}",
            )
        except ProviderError as exc:
            return RunStatus.FAILED, None, ReasonCode.PROVIDER_ERROR, str(exc)
        if cancel is not None and cancel.is_set():
            return (
                RunStatus.CANCELLED,
                None,
                ReasonCode.CANCELLED,
                "使用者已取消生成",
            )
        if not envelope.prose_text.strip():
            return (
                RunStatus.FAILED,
                None,
                ReasonCode.EMPTY_OUTPUT,
                "成人結構化輸出沒有有效正文",
            )
        return RunStatus.COMPLETED, envelope, ReasonCode.NONE, ""

    def _finalize(
        self,
        *,
        run_id: str,
        scene_id: str,
        project_id: str,
        card_version_id: str,
        status: RunStatus,
        reason_code: str,
        error_reason: str,
        text: str,
        started: float,
        origin: DraftOrigin,
        parent_draft_id: str | None = None,
        revision_json: str = "{}",
        revision_fingerprint: str | None = None,
        is_preview: bool = False,
        is_recovery_rebase: bool = False,
        adult_mode: bool = False,
        adult_envelope: AdultStoryOutputEnvelope | None = None,
        adult_manifest: ParticipantManifest | None = None,
    ) -> tuple[DraftRecord | None, DraftRecord | None, str | None]:
        """Finalize the run and persist any resulting draft in one transaction."""
        latency_ms = int((time.monotonic() - started) * 1000)
        draft: DraftRecord | None = None
        partial: DraftRecord | None = None
        candidate_id: str | None = None
        with self._transaction() as session:
            runs = GenerationRunRepository(session)
            runs.finalize(
                run_id,
                status=status.value,
                reason_code=reason_code,
                error_reason=error_reason,
                completed_at=utc_now_iso(),
                latency_ms=latency_ms,
            )
            drafts = SceneDraftRepository(session)
            scenes = StorySceneRepository(session)

            if adult_mode:
                if status is RunStatus.COMPLETED and (
                    adult_envelope is None or adult_manifest is None
                ):
                    raise ValidationFailedError("成人 run 不可在缺少有效 envelope/manifest 時完成")
                if adult_envelope is not None and adult_manifest is not None:
                    candidate_id = str(uuid.uuid4())
                    created_at = utc_now_iso()
                    candidate_repo = AdultOutputCandidateRepository(session)
                    candidate_repo.add(
                        AdultOutputCandidateRecord(
                            id=candidate_id,
                            generation_run_id=run_id,
                            project_id=project_id,
                            story_scene_id=scene_id,
                            scene_card_version_id=card_version_id,
                            prose_text=adult_envelope.prose_text,
                            prose_sha256=adult_envelope.prose_sha256,
                            envelope_json=adult_envelope.canonical(),
                            envelope_sha256=adult_envelope.sha256,
                            participant_manifest_json=canonical_json(
                                adult_manifest.model_dump(mode="json")
                            ),
                            participant_manifest_sha256=adult_manifest.fingerprint,
                            status="pending",
                            reason=ReasonCode.ADULT_OUTPUT_PENDING_REVIEW,
                            created_at=created_at,
                        )
                    )
                    session.flush()
                    if status is not RunStatus.COMPLETED:
                        candidate_repo.reject(
                            candidate_id,
                            reason=f"{reason_code}: {error_reason}".strip(),
                            rejected_at=created_at,
                        )
            elif status is RunStatus.COMPLETED:
                draft = DraftRecord(
                    id=str(uuid.uuid4()),
                    story_scene_id=scene_id,
                    project_id=project_id,
                    draft_number=drafts.next_draft_number(scene_id),
                    prose_text=text.strip(),
                    word_count=_word_count(text),
                    origin=origin.value,
                    draft_status="complete",
                    was_accepted=False,
                    accepted_at="",
                    is_preview=is_preview,
                    promoted_from_preview_draft_id=None,
                    is_recovery_rebase=is_recovery_rebase,
                    scene_card_version_id=card_version_id,
                    generation_run_id=run_id,
                    revision_request_json=revision_json,
                    revision_request_fingerprint=revision_fingerprint,
                    parent_draft_id=parent_draft_id,
                    created_at=utc_now_iso(),
                )
                drafts.add(draft)
                session.flush()
                scenes.update_fields(scene_id, working_draft_id=draft.id, updated_at=utc_now_iso())
            elif status is RunStatus.CANCELLED and text.strip():
                # A3-10 §16.3: partial output is recoverable, clearly labelled,
                # and can never be accepted (a DB CHECK enforces that).
                partial = DraftRecord(
                    id=str(uuid.uuid4()),
                    story_scene_id=scene_id,
                    project_id=project_id,
                    draft_number=drafts.next_draft_number(scene_id),
                    prose_text=text.strip(),
                    word_count=_word_count(text),
                    origin=origin.value,
                    draft_status="partial",
                    was_accepted=False,
                    accepted_at="",
                    is_preview=is_preview,
                    promoted_from_preview_draft_id=None,
                    is_recovery_rebase=is_recovery_rebase,
                    scene_card_version_id=card_version_id,
                    generation_run_id=run_id,
                    revision_request_json=revision_json,
                    revision_request_fingerprint=revision_fingerprint,
                    parent_draft_id=parent_draft_id,
                    created_at=utc_now_iso(),
                )
                drafts.add(partial)
        return draft, partial, candidate_id

    # ------------------------------------------------------- shared engine
    def _execute(
        self,
        *,
        snapshot: GenerationInputSnapshot,
        resolved: ResolvedStoryContext,
        origin: DraftOrigin,
        system: str,
        prompt: str,
        options: GenerationOptionsSnapshot,
        cancel: Event | None,
        on_chunk: Callable[[str], None] | None,
        preview_ack: bool = False,
        parent_draft_id: str | None = None,
        revision_json: str = "{}",
        revision_fingerprint: str | None = None,
        rebase_reason: str = "",
    ) -> OrchestratedOutcome:
        """Commit RUNNING, call the provider, finalize exactly once."""
        adult_mode = derives_adult(snapshot.content_mode)
        adult_manifest: ParticipantManifest | None = None
        if adult_mode:
            options = options.model_copy(update={"stream": False, "structured_mode": True})
            adult_manifest = build_adult_participant_manifest(snapshot)
            snapshot = snapshot.model_copy(
                update={
                    "options": options,
                    "output_contract_version": ADULT_STORY_OUTPUT_ENVELOPE_VERSION,
                }
            )
            with self._session_factory() as session:
                assert_adult_audit_coverage(
                    session,
                    audit_ids=tuple(snapshot.eligibility_evaluation_ids),
                    manifest=adult_manifest,
                    label="生成當下",
                )
            structured_contract = build_adult_story_provider_contract(snapshot)
            prompt = f"{prompt}\n\n{structured_contract}"
        # A3-R10: bind the snapshot to the EXACT provider-visible messages
        snapshot = snapshot.model_copy(
            update={
                "system_message_sha256": hashlib.sha256(system.encode("utf-8")).hexdigest(),
                "user_message_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
            }
        )
        run_id = str(uuid.uuid4())
        started = time.monotonic()
        record = _run_record(run_id, snapshot, resolved, utc_now_iso())
        record.preview_warning_acknowledged = int(preview_ack)
        record.parent_draft_id = parent_draft_id
        record.revision_request_fingerprint = revision_fingerprint or ""
        record.rebase_reason = rebase_reason
        record.is_recovery_rebase = int(snapshot.is_recovery_rebase)
        record.missing_source_version_ids_json = json.dumps(
            list(snapshot.missing_source_version_ids), ensure_ascii=False
        )
        # A3-R10: hashes always; the rendered text only when the project allows
        store_raw = (
            True if self._settings is None else self._settings.store_rendered_generation_messages
        )
        record.rendered_message_storage_enabled = int(store_raw)
        record.system_message_byte_size = len(system.encode("utf-8"))
        record.user_message_byte_size = len(prompt.encode("utf-8"))
        if store_raw:
            record.rendered_system_message = system
            record.rendered_user_message = prompt
        self._start_run(record)
        adult_envelope: AdultStoryOutputEnvelope | None = None
        if adult_mode:
            # Adult prose is schema-only and never streams to callbacks.  The
            # output contract and structured flag are part of the run hash.
            assert adult_manifest is not None
            status, adult_envelope, reason_code, error_reason = self._call_adult_provider(
                system=system,
                prompt=prompt,
                model=snapshot.model,
                options=options,
                cancel=cancel,
            )
            text = adult_envelope.prose_text if adult_envelope is not None else ""
            if (
                status is RunStatus.COMPLETED
                and adult_envelope is not None
                and (
                    adult_envelope.schema_version != ADULT_STORY_OUTPUT_ENVELOPE_VERSION
                    or adult_envelope.expected_participant_pins != adult_manifest.participants
                )
            ):
                status = RunStatus.FAILED
                reason_code = ReasonCode.ADULT_ENVELOPE_MISMATCH
                error_reason = (
                    "成人輸出 envelope 的 contract 或 expected participant pins "
                    "與 generation snapshot 不一致；已隔離且未建立草稿。"
                )
                # The durable candidate contract itself correctly refuses an
                # envelope claiming a different roster. Finalize a failed run
                # without attempting to persist invalid evidence.
                adult_envelope = None
                text = ""
        else:
            status, text, reason_code, error_reason = self._call_provider(
                system=system,
                prompt=prompt,
                model=snapshot.model,
                options=options,
                cancel=cancel,
                on_chunk=on_chunk,
            )
        if text.strip() and status in {RunStatus.COMPLETED, RunStatus.CANCELLED}:
            output_preflight = preflight_content_mode(snapshot.content_mode, text)
            if output_preflight.confirmation_required:
                status = RunStatus.FAILED
                reason_code = ReasonCode.OUTPUT_RECLASSIFICATION_REQUIRED
                error_reason = "模型輸出出現未在輸入中授權的成人性內容；已隔離且未建立草稿。"
            elif derives_adult(snapshot.content_mode):
                output_cues = detect_presentation_cues(text)
                if output_cues.has_conflict:
                    status = RunStatus.FAILED
                    reason_code = ReasonCode.OUTPUT_POLICY_QUARANTINED
                    error_reason = "模型輸出出現明確未成年期或孩童化線索；已隔離且未建立草稿。"
        if status is RunStatus.COMPLETED and on_chunk is not None and not adult_mode:
            # Buffer until the complete output has passed policy checks.
            on_chunk(text)
        if status is RunStatus.COMPLETED and adult_mode:
            reason_code = ReasonCode.ADULT_OUTPUT_PENDING_REVIEW
            error_reason = "成人輸出已完成並隔離保存，待作者完整審閱確認。"
        draft, partial, candidate_id = self._finalize(
            run_id=run_id,
            scene_id=resolved.scene_id,
            project_id=resolved.project_id,
            card_version_id=resolved.scene_card_version_id,
            status=status,
            reason_code=reason_code,
            error_reason=error_reason,
            text=text,
            started=started,
            origin=origin,
            parent_draft_id=parent_draft_id,
            revision_json=revision_json,
            revision_fingerprint=revision_fingerprint,
            is_preview=snapshot.planning_mode is PlanningMode.PREVIEW,
            is_recovery_rebase=snapshot.is_recovery_rebase,
            adult_mode=adult_mode,
            adult_envelope=adult_envelope,
            adult_manifest=adult_manifest,
        )
        return OrchestratedOutcome(
            run_id=run_id,
            status=status,
            snapshot=snapshot,
            draft=draft,
            partial_draft=partial,
            candidate_id=candidate_id,
            reason_code=reason_code,
            error_reason=error_reason,
            planning_mode=snapshot.planning_mode,
            is_recovery_rebase=snapshot.is_recovery_rebase,
        )

    def _options(self, request: object) -> GenerationOptionsSnapshot:
        """A3-R13: capture every behaviour-affecting option, not just one."""
        return GenerationOptionsSnapshot(
            temperature=getattr(request, "temperature", 0.8),
            timeout_s=getattr(request, "timeout_s", 300.0),
            stream=getattr(request, "stream", False),
            seed=getattr(request, "seed", None),
            max_output_tokens=getattr(request, "max_output_tokens", None),
            top_p=getattr(request, "top_p", None),
        )


class StoryGenerationOrchestrationService(_OrchestrationBase):
    """The public API for generating a scene (A3-01 §7.2)."""

    def generate_scene(
        self,
        request: GenerateSceneRequest,
        *,
        cancel: Event | None = None,
        on_chunk: Callable[[str], None] | None = None,
    ) -> OrchestratedOutcome:
        _validate_purpose_brief(
            request.generation_purpose,
            request.complete_story_brief,
        )
        if (
            request.planning_mode is PlanningMode.PREVIEW
            and not request.preview_warning_acknowledged
        ):
            raise ValidationFailedError(
                "Preview 模式使用尚未接受的規劃版本，"
                "必須先確認警告（preview_warning_acknowledged）才能生成。"
            )
        options = self._options(request)
        resolved, evaluation_ids = self._build_bound_context(
            BuildStoryContextRequest(
                scene_id=request.scene_id,
                generation_purpose=request.generation_purpose,
                complete_story_brief=request.complete_story_brief,
                scene_card_version_id=request.scene_card_version_id,
                requirement_version_id=request.requirement_version_id,
                bible_version_id=request.bible_version_id,
                outline_version_id=request.outline_version_id,
                chapter_plan_version_id=request.chapter_plan_version_id,
                user_instruction=request.user_instruction,
                structured_must_avoid=request.structured_must_avoid,
                style_examples=request.style_examples,
                max_chars=request.max_context_chars,
                budget_snapshot=request.context_budget,
                planning_mode=request.planning_mode,
            )
        )
        snapshot = _snapshot_from(
            resolved,
            provider=self._provider_name,
            model=request.model,
            options=options,
            eligibility_fingerprint=resolved.package.eligibility_fingerprint,
            evaluation_ids=evaluation_ids,
            run_kind="generation",
            complete_story_brief=request.complete_story_brief,
        )
        return self._execute(
            snapshot=snapshot,
            resolved=resolved,
            origin=DraftOrigin.GENERATED,
            system=resolved.package.system_message,
            prompt=resolved.package.user_message,
            options=options,
            cancel=cancel,
            on_chunk=on_chunk,
            preview_ack=request.preview_warning_acknowledged,
        )


class StoryRevisionOrchestrationService(_OrchestrationBase):
    """Revision and recovery rebase (A3-R03 §9)."""

    def revise_scene(
        self,
        request: ReviseSceneRequest,
        *,
        cancel: Event | None = None,
        on_chunk: Callable[[str], None] | None = None,
    ) -> OrchestratedOutcome:
        """An ordinary revision replays the source run's exact planning chain.

        Fails closed when that chain cannot be reconstructed. Substituting the
        latest accepted Requirement/Bible/Outline/Plan/Card would silently
        change the meaning and provenance of the revision.
        """
        source, source_run = self._load_source(request.draft_id)
        if source_run is None:
            raise SourcePlanningChainUnavailableError(
                missing_version_ids=("（來源草稿沒有對應的生成紀錄）",)
            )
        source_snapshot = self._verified_source_snapshot(source_run, source)
        missing = self._missing_versions(source_run, source.scene_card_version_id)
        if missing:
            raise SourcePlanningChainUnavailableError(missing_version_ids=missing)

        options = self._options(request)
        # Replay the source run's own mode as well as its exact IDs.  A preview
        # source may legitimately pin unaccepted versions and must stay
        # preview; an accepted source only pins versions that were accepted
        # and its revision must remain eligible for author acceptance even if
        # newer accepted pointers exist today.
        source_planning_mode = PlanningMode(source_run.planning_mode)
        previous_summary_source_refs = _ordinary_revision_summary_refs(
            source_snapshot
        )
        resolved, evaluation_ids = self._build_bound_context(
            BuildStoryContextRequest(
                scene_id=source.story_scene_id,
                generation_purpose=source_snapshot.generation_purpose,
                complete_story_brief=source_snapshot.complete_story_brief,
                scene_card_version_id=source.scene_card_version_id,
                requirement_version_id=source_run.requirement_version_id,
                bible_version_id=source_run.bible_version_id,
                outline_version_id=source_run.outline_version_id,
                chapter_plan_version_id=source_run.chapter_plan_version_id,
                user_instruction=request.user_instruction,
                structured_must_avoid=request.structured_must_avoid,
                max_chars=request.max_context_chars,
                budget_snapshot=request.context_budget,
                planning_mode=source_planning_mode,
                memory_proposal_ids=source_snapshot.memory_proposal_ids,
                memory_gap_scene_ids=source_snapshot.memory_gap_scene_ids,
                stale_memory_proposal_ids=(source_snapshot.stale_memory_proposal_ids),
                expected_memory_fingerprint=(source_snapshot.memory_projection_fingerprint),
                previous_summary_source_refs=previous_summary_source_refs,
            ),
            additional_positive_texts=(
                source.prose_text,
                request.revision.instruction,
            ),
        )
        return self._run_revision(
            source=source,
            resolved=resolved,
            evaluation_ids=evaluation_ids,
            options=options,
            model=request.model,
            revision=request.revision,
            generation_purpose=source_snapshot.generation_purpose,
            complete_story_brief=source_snapshot.complete_story_brief,
            cancel=cancel,
            on_chunk=on_chunk,
        )

    def recover_by_rebase(
        self,
        request: RecoveryRebaseRequest,
        *,
        cancel: Event | None = None,
        on_chunk: Callable[[str], None] | None = None,
    ) -> OrchestratedOutcome:
        """A3-R03 §9: the audited escape when history is genuinely damaged.

        Produces a clearly-labelled Recovery Rebase draft under a NEW accepted
        chain. The original draft and run are never modified.
        """
        if not request.rebase_reason.strip():
            raise ValidationFailedError(
                "復原 rebase 會改用新的規劃鏈，必須提供書面理由以留下稽核紀錄。"
            )
        source, source_run = self._load_source(request.draft_id)
        source_snapshot: GenerationInputSnapshot | None = None
        if source_run is not None:
            try:
                source_snapshot = self._verified_source_snapshot(source_run, source)
            except SourcePlanningChainUnavailableError:
                if request.generation_purpose is None:
                    raise ValidationFailedError(
                        "來源快照無法驗證，Recovery Rebase 必須明確指定生成用途；"
                        "系統不會猜測是場景或完整短篇。"
                    ) from None

        if (
            source_snapshot is not None
            and source_snapshot.generation_purpose
            is StoryGenerationPurpose.COMPLETE_SHORT_STORY
            and request.generation_purpose is None
        ):
            raise ValidationFailedError(
                "完整短篇 Recovery Rebase 必須明確帶入生成用途與完整故事 brief。"
            )
        generation_purpose = (
            request.generation_purpose or StoryGenerationPurpose.SCENE
        )
        if (
            source_snapshot is not None
            and generation_purpose is not source_snapshot.generation_purpose
        ):
            raise ValidationFailedError(
                "Recovery Rebase 不可把來源草稿由場景改成完整短篇，或反向改變生成用途。"
            )
        _validate_purpose_brief(generation_purpose, request.complete_story_brief)
        missing = (
            self._missing_versions(source_run, source.scene_card_version_id)
            if source_run is not None
            else ("（來源草稿沒有對應的生成紀錄）",)
        )
        options = self._options(request)
        resolved, evaluation_ids = self._build_bound_context(
            BuildStoryContextRequest(
                scene_id=source.story_scene_id,
                generation_purpose=generation_purpose,
                complete_story_brief=request.complete_story_brief,
                scene_card_version_id=request.scene_card_version_id,
                requirement_version_id=request.requirement_version_id,
                bible_version_id=request.bible_version_id,
                outline_version_id=request.outline_version_id,
                chapter_plan_version_id=request.chapter_plan_version_id,
                user_instruction=request.user_instruction,
                structured_must_avoid=request.structured_must_avoid,
                max_chars=request.max_context_chars,
                budget_snapshot=request.context_budget,
                planning_mode=PlanningMode.ACCEPTED,
            ),
            additional_positive_texts=(
                source.prose_text,
                request.revision.instruction,
            ),
        )
        return self._run_revision(
            source=source,
            resolved=resolved,
            evaluation_ids=evaluation_ids,
            options=options,
            model=request.model,
            revision=request.revision,
            generation_purpose=generation_purpose,
            complete_story_brief=request.complete_story_brief,
            cancel=cancel,
            on_chunk=on_chunk,
            rebased_from_run_id=(source_run.id if source_run is not None else None),
            rebase_reason=request.rebase_reason.strip(),
            missing_source_version_ids=missing,
            is_recovery=True,
        )

    # ------------------------------------------------------------ helpers
    def _load_source(self, draft_id: str) -> tuple[DraftRecord, GenerationRunRecord | None]:
        with self._session_factory() as session:
            drafts = SceneDraftRepository(session)
            runs = GenerationRunRepository(session)
            source = drafts.get(draft_id)
            if source is None:
                raise NotFoundError(f"找不到草稿：{draft_id}")
            if source.draft_status == "partial":
                raise ValidationFailedError("不可修訂未完成（partial）的草稿")

            current = source
            visited: set[str] = set()
            while True:
                if current.id in visited:
                    raise SourcePlanningChainUnavailableError(
                        missing_version_ids=("draft_lineage（偵測到循環）",)
                    )
                visited.add(current.id)
                if (
                    current.story_scene_id != source.story_scene_id
                    or current.project_id != source.project_id
                ):
                    raise SourcePlanningChainUnavailableError(
                        missing_version_ids=(
                            "draft_lineage（來源鏈跨越場景或專案）",
                        )
                    )
                if current.scene_card_version_id != source.scene_card_version_id:
                    raise SourcePlanningChainUnavailableError(
                        missing_version_ids=(
                            "draft_lineage（來源鏈切換 Scene Card 版本）",
                        )
                    )
                if current.draft_status == "partial":
                    raise SourcePlanningChainUnavailableError(
                        missing_version_ids=(
                            "draft_lineage（來源鏈包含 partial 草稿）",
                        )
                    )
                if current.generation_run_id:
                    run = runs.get(current.generation_run_id)
                    if run is None:
                        raise SourcePlanningChainUnavailableError(
                            missing_version_ids=(
                                f"generation_run={current.generation_run_id}",
                            )
                        )
                    if (
                        run.story_scene_id != current.story_scene_id
                        or run.project_id != current.project_id
                        or run.scene_card_version_id
                        != current.scene_card_version_id
                    ):
                        raise SourcePlanningChainUnavailableError(
                            missing_version_ids=(
                                "draft_lineage（generation run ownership 不一致）",
                            )
                        )
                    # A manual child does not reuse the run ID, but an adult
                    # ancestor must still have its durable review receipt.
                    assert_adult_draft_reviewed(
                        session,
                        current,
                        action_label="修訂草稿來源鏈",
                    )
                    return source, run
                if current.parent_draft_id is None:
                    return source, None
                parent = drafts.get(current.parent_draft_id)
                if parent is None:
                    raise SourcePlanningChainUnavailableError(
                        missing_version_ids=(
                            f"parent_draft={current.parent_draft_id}",
                        )
                    )
                current = parent

    @staticmethod
    def _verified_source_snapshot(
        run: GenerationRunRecord,
        source: DraftRecord,
    ) -> GenerationInputSnapshot:
        """Load the immutable source context, including its exact memory state."""

        digest = hashlib.sha256(run.input_snapshot_json.encode("utf-8")).hexdigest()
        if digest != run.input_snapshot_sha256:
            raise SourcePlanningChainUnavailableError(
                missing_version_ids=("input_snapshot（SHA-256 完整性驗證失敗）",)
            )
        try:
            snapshot = GenerationInputSnapshot.model_validate_json(run.input_snapshot_json)
        except ValueError as exc:
            raise SourcePlanningChainUnavailableError(
                missing_version_ids=("input_snapshot（內容無法解析）",)
            ) from exc
        mismatched = [
            name
            for name, snapshot_value, stored_value in (
                ("project_id", snapshot.project_id, run.project_id),
                ("source_project_id", snapshot.project_id, source.project_id),
                ("story_scene_id", snapshot.story_scene_id, run.story_scene_id),
                (
                    "source_story_scene_id",
                    snapshot.story_scene_id,
                    source.story_scene_id,
                ),
                (
                    "scene_card_version_id",
                    snapshot.scene_card_version_id,
                    source.scene_card_version_id,
                ),
                (
                    "requirement_version_id",
                    snapshot.requirement_version_id,
                    run.requirement_version_id,
                ),
                (
                    "bible_version_id",
                    snapshot.bible_version_id,
                    run.bible_version_id,
                ),
                (
                    "outline_version_id",
                    snapshot.outline_version_id,
                    run.outline_version_id,
                ),
                (
                    "chapter_plan_version_id",
                    snapshot.chapter_plan_version_id,
                    run.chapter_plan_version_id,
                ),
            )
            if snapshot_value != stored_value
        ]
        if mismatched:
            raise SourcePlanningChainUnavailableError(
                missing_version_ids=(
                    "input_snapshot（與來源紀錄不一致：" + "、".join(mismatched) + "）",
                )
            )
        return snapshot

    def _missing_versions(self, run: GenerationRunRecord, card_version_id: str) -> tuple[str, ...]:
        """Which of the source run's planning versions can no longer be read.

        Archived-but-readable versions are fine; only genuinely unresolvable
        ones block an ordinary revision.
        """
        checks: list[tuple[str, str | None]] = [
            ("scene_card", card_version_id),
            ("requirement", run.requirement_version_id),
            ("bible", run.bible_version_id),
            ("outline", run.outline_version_id),
            ("chapter_plan", run.chapter_plan_version_id),
        ]
        missing: list[str] = []
        with self._session_factory() as session:
            cards = SceneCardVersionRepository(session)
            for kind, version_id in checks:
                if not version_id:
                    continue
                found = (
                    cards.get(version_id)
                    if kind == "scene_card"
                    else VersionedEntityRepository(session, kind).get_version(version_id)
                )
                if found is None:
                    missing.append(f"{kind}={version_id}")
        return tuple(missing)

    def _run_revision(
        self,
        *,
        source: DraftRecord,
        resolved: ResolvedStoryContext,
        evaluation_ids: tuple[str, ...],
        options: GenerationOptionsSnapshot,
        model: str,
        revision: RevisionRequest,
        generation_purpose: StoryGenerationPurpose,
        complete_story_brief: CompleteStoryBrief | None,
        cancel: Event | None,
        on_chunk: Callable[[str], None] | None,
        rebased_from_run_id: str | None = None,
        rebase_reason: str = "",
        missing_source_version_ids: tuple[str, ...] = (),
        is_recovery: bool = False,
    ) -> OrchestratedOutcome:
        _validate_purpose_brief(generation_purpose, complete_story_brief)
        if resolved.package.generation_purpose is not generation_purpose:
            raise ValidationFailedError(
                "修訂用途與已解析的故事上下文用途不一致"
            )
        revision_fingerprint = _revision_fingerprint(revision)
        revision_json = canonical_json(revision.model_dump(mode="json"))
        snapshot = _snapshot_from(
            resolved,
            provider=self._provider_name,
            model=model,
            options=options,
            eligibility_fingerprint=resolved.package.eligibility_fingerprint,
            evaluation_ids=evaluation_ids,
            run_kind="revision",
            parent_draft_id=source.id,
            revision_request_json=revision_json,
            revision_request_fingerprint=revision_fingerprint,
            rebased_from_generation_run_id=rebased_from_run_id,
            is_recovery_rebase=is_recovery,
            missing_source_version_ids=missing_source_version_ids,
            complete_story_brief=complete_story_brief,
        )
        prompt = (
            f"{resolved.package.user_message}\n\n## 目前草稿（要修訂的對象）\n{source.prose_text}"
        )
        return self._execute(
            snapshot=snapshot,
            resolved=resolved,
            origin=DraftOrigin.REVISED,
            system=build_revision_contract(
                revision,
                purpose=generation_purpose,
            ),
            prompt=prompt,
            options=options,
            cancel=cancel,
            on_chunk=on_chunk,
            parent_draft_id=source.id,
            revision_json=revision_json,
            revision_fingerprint=revision_fingerprint,
            rebase_reason=rebase_reason,
        )


def _nonblank_texts(*values: str) -> tuple[str, ...]:
    return tuple(value for value in values if value.strip())


def _scene_card_positive_texts(card: SceneCard) -> tuple[str, ...]:
    """Typed provider-visible Scene Card fields, excluding prohibitions."""

    return _nonblank_texts(
        card.location,
        card.start_time,
        card.pov_character_id,
        card.scene_goal.protagonist,
        card.scene_goal.narrative,
        card.conflict.external,
        card.conflict.internal,
        card.entry_state.emotion,
        *card.entry_state.known_facts,
        *card.beats,
        card.turning_point,
        card.exit_state.emotion,
        *card.exit_state.new_knowledge,
        *card.must_include,
    )


def _story_requirement_positive_texts(
    requirement: StoryRequirement,
) -> tuple[str, ...]:
    """Typed positive requirement fields; dials and must-avoid are separate."""

    return _nonblank_texts(
        requirement.concept,
        requirement.logline,
        requirement.synopsis,
        requirement.opening_hook,
        requirement.genre,
        requirement.target_length,
        requirement.tone,
        requirement.audience,
        requirement.setting,
        requirement.time_period,
        requirement.protagonist_notes,
        requirement.antagonist_notes,
        requirement.central_conflict,
        *requirement.themes,
        *requirement.must_include,
        requirement.pov.value,
        requirement.tense.value,
        requirement.pacing,
        requirement.prose_style_notes,
        requirement.dialogue_density,
        requirement.ending_preference,
        requirement.inspiration_notes,
    )


def _complete_story_positive_texts(
    brief: CompleteStoryBrief,
) -> tuple[str, ...]:
    """Typed positive complete-story fields; ``must_avoid`` never enters."""

    return _nonblank_texts(
        brief.world_premise,
        brief.story_seed,
        brief.structure_outline,
        brief.additional_direction,
        *brief.must_include,
        brief.pacing,
    )


def _validate_purpose_brief(
    purpose: StoryGenerationPurpose,
    brief: CompleteStoryBrief | None,
) -> None:
    if purpose is StoryGenerationPurpose.SCENE:
        if brief is not None:
            raise ValidationFailedError("單一場景生成不可帶入完整故事 brief")
        return
    if purpose is StoryGenerationPurpose.COMPLETE_SHORT_STORY:
        if brief is None:
            raise ValidationFailedError("完整短篇生成必須帶入完整故事 brief")
        return
    raise ValidationFailedError(f"不支援的故事生成用途：{purpose}")


def _ordinary_revision_summary_refs(
    snapshot: GenerationInputSnapshot,
) -> tuple[ContextSourceRef, ...] | None:
    """Return immutable previous-summary pins for an ordinary revision.

    Typed-source snapshots prove absence as well as presence, so an empty
    tuple is meaningful.  Historical v8/v9 summary refs did not pin the
    mutable summary row body.  Replaying those refs after an upsert would
    silently substitute today's text; ordinary revision must instead direct
    the author to the explicit Recovery Rebase workflow.
    """

    if not snapshot.context_source_refs:
        match = re.search(r"-v(\d+)$", snapshot.schema_version)
        if match is not None and int(match.group(1)) >= 3:
            return ()
        raise SourcePlanningChainUnavailableError(
            missing_version_ids=(
                "context_source_refs（legacy snapshot 無法證明當時是否存在前情摘要）",
            )
        )
    refs = tuple(
        ref
        for ref in snapshot.context_source_refs
        if ref.entity_type
        in {
            SourceEntityType.SCENE_DRAFT_SUMMARY,
            SourceEntityType.SCENE_DRAFT,
        }
    )
    if not refs:
        return ()
    summary_refs = tuple(
        ref
        for ref in refs
        if ref.entity_type is SourceEntityType.SCENE_DRAFT_SUMMARY
    )
    draft_refs = tuple(
        ref for ref in refs if ref.entity_type is SourceEntityType.SCENE_DRAFT
    )
    digest = summary_refs[0].version_id if len(summary_refs) == 1 else None
    if (
        len(refs) != 2
        or len(summary_refs) != 1
        or len(draft_refs) != 1
        or digest is None
        or re.fullmatch(r"sha256:[0-9a-f]{64}", digest) is None
    ):
        raise SourcePlanningChainUnavailableError(
            missing_version_ids=(
                "previous_summary（缺少可驗證的 SHA-256 內容 pin）",
            )
        )
    return refs


def _revision_fingerprint(revision: RevisionRequest) -> str:
    import hashlib

    from imaginarium_forge.canonical import canonical_json

    payload = canonical_json(revision.model_dump(mode="json"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _snapshot_from(
    resolved: ResolvedStoryContext,
    *,
    provider: str,
    model: str,
    options: GenerationOptionsSnapshot,
    eligibility_fingerprint: str,
    evaluation_ids: tuple[str, ...],
    run_kind: str,
    parent_draft_id: str | None = None,
    revision_request_json: str = "{}",
    revision_request_fingerprint: str = "",
    rebased_from_generation_run_id: str | None = None,
    is_recovery_rebase: bool = False,
    missing_source_version_ids: tuple[str, ...] = (),
    complete_story_brief: CompleteStoryBrief | None = None,
) -> GenerationInputSnapshot:
    package = resolved.package
    return GenerationInputSnapshot(
        generation_purpose=package.generation_purpose,
        complete_story_brief=complete_story_brief,
        structured_must_avoid=package.structured_must_avoid,
        complete_story_brief_sha256=(
            "" if complete_story_brief is None else complete_story_brief.sha256
        ),
        project_id=resolved.project_id,
        story_scene_id=resolved.scene_id,
        scene_card_version_id=resolved.scene_card_version_id,
        requirement_version_id=resolved.requirement_version_id,
        bible_version_id=resolved.bible_version_id,
        outline_version_id=resolved.outline_version_id,
        chapter_plan_version_id=resolved.chapter_plan_version_id,
        participants=tuple(
            ParticipantPin(
                character_id=p.character_id,
                character_version_id=p.character_version_id,
                role=p.role,
                is_pov=p.is_pov,
            )
            for p in resolved.participants
        ),
        pov_character_version_id=resolved.pov_character_version_id,
        content_mode=resolved.scene_card.content_mode,
        eligibility_evaluation_ids=evaluation_ids,
        eligibility_fingerprint=eligibility_fingerprint,
        context_fingerprint=package.fingerprint,
        # A3-S8.1 §15: the typed sources travel inside the immutable
        # snapshot, so the run's own SHA-256 already covers provenance.
        context_source_refs=package.source_refs,
        context_schema_version=package.schema_version,
        context_contract_version=package.contract_version,
        context_budget_policy_version=package.budget_policy_version,
        context_budget_snapshot=package.budget_snapshot,
        memory_projection_fingerprint=package.memory_fingerprint,
        memory_proposal_ids=package.memory_proposal_ids,
        memory_entry_ids=package.memory_entry_ids,
        memory_gap_scene_ids=package.memory_gap_scene_ids,
        stale_memory_proposal_ids=package.stale_memory_proposal_ids,
        provider=provider,
        model=model,
        options=options,
        renderer_version=package.renderer_version,
        # NOTE: filled in by ``_execute`` from the messages actually sent. A
        # revision replaces the system message with the revision contract and
        # appends the source prose to the user message, so hashing the context
        # package's own defaults here would record something the provider
        # never saw — exactly the mismatch A3-R10 exists to prevent.
        system_message_sha256="",
        user_message_sha256="",
        planning_mode=package.planning_mode,  # type: ignore[arg-type]
        planning_chain_fingerprint=package.planning_chain_fingerprint,
        run_kind=run_kind,
        parent_draft_id=parent_draft_id,
        revision_request_json=revision_request_json,
        revision_request_fingerprint=revision_request_fingerprint,
        rebased_from_generation_run_id=rebased_from_generation_run_id,
        is_recovery_rebase=is_recovery_rebase,
        missing_source_version_ids=missing_source_version_ids,
    )


def _run_record(
    run_id: str,
    snapshot: GenerationInputSnapshot,
    resolved: ResolvedStoryContext,
    started_at: str,
) -> GenerationRunRecord:
    return GenerationRunRecord(
        id=run_id,
        story_scene_id=resolved.scene_id,
        project_id=resolved.project_id,
        scene_card_version_id=resolved.scene_card_version_id,
        started_at=started_at,
        run_kind=snapshot.run_kind,
        provider=snapshot.provider,
        model=snapshot.model,
        options_snapshot_json=snapshot.options.model_dump_json(),
        options_snapshot_sha256=snapshot.options.sha256,
        renderer_version=snapshot.renderer_version,
        planning_mode=snapshot.planning_mode.value,
        planning_chain_fingerprint=snapshot.planning_chain_fingerprint,
        system_message_sha256=snapshot.system_message_sha256,
        user_message_sha256=snapshot.user_message_sha256,
        context_fingerprint=snapshot.context_fingerprint,
        context_schema_version=snapshot.context_schema_version,
        context_contract_version=snapshot.context_contract_version,
        context_budget_policy_version=snapshot.context_budget_policy_version,
        eligibility_fingerprint=snapshot.eligibility_fingerprint,
        eligibility_evaluation_ids_json=json.dumps(
            list(snapshot.eligibility_evaluation_ids), ensure_ascii=False
        ),
        input_snapshot_json=snapshot.canonical_payload(),
        input_snapshot_sha256=snapshot.sha256,
        requirement_version_id=snapshot.requirement_version_id,
        bible_version_id=snapshot.bible_version_id,
        outline_version_id=snapshot.outline_version_id,
        chapter_plan_version_id=snapshot.chapter_plan_version_id,
        pov_character_version_id=snapshot.pov_character_version_id,
        character_version_ids_json=json.dumps(
            list(snapshot.character_version_ids), ensure_ascii=False
        ),
        content_mode=snapshot.content_mode.value,
        status=RunStatus.RUNNING.value,
        rebased_from_generation_run_id=snapshot.rebased_from_generation_run_id,
    )
