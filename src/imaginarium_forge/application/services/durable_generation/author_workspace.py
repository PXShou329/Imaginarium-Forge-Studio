"""Author-facing projections for the durable long-form workspace.

This module deliberately contains no provider or database behaviour. Quick
planning is a deterministic local draft, clearly identified as such, and a
durable workflow remains the sole source of truth after the author confirms
that draft.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from enum import StrEnum
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from imaginarium_forge.application.services.durable_generation.unit_author_policy import (
    effective_spec,
)
from imaginarium_forge.application.services.generation.output_measurement import (
    count_visible_characters,
)
from imaginarium_forge.canonical import canonical_json
from imaginarium_forge.domain.generation.durable import (
    ACTIVE_JOB_STATUSES,
    GenerationJob,
    GenerationStep,
    JobStatus,
    StepKind,
    StepStatus,
)
from imaginarium_forge.domain.generation.unit_author_decision import UnitAuthorDecision

WORKSPACE_PLAN_VERSION: Literal["cwe-2.1-long-form-author-plan-v1"] = (
    "cwe-2.1-long-form-author-plan-v1"
)
LOCAL_PLAN_ORIGIN: Literal["local_deterministic"] = "local_deterministic"
DEFAULT_UNIT_TARGET_VISIBLE_CHARS = 5_000
MAX_WORKSPACE_UNITS = 100


class LongFormWorkspaceMode(StrEnum):
    """Progressive-disclosure modes required by the R2 workspace."""

    QUICK = "quick"
    GUIDED = "guided"
    ADVANCED = "advanced"


class AuthorUnitStatus(StrEnum):
    """Stable author vocabulary projected from persisted executor states."""

    WAITING = "waiting"
    PLANNING = "planning"
    WRITING = "writing"
    CHECKING = "checking"
    REVISING = "revising"
    COMPLETE = "complete"
    RESUMABLE = "resumable"
    PAUSED = "paused"
    CANCELLED = "cancelled"
    FAILED = "failed"
    NEEDS_AUTHOR_INPUT = "needs_author_input"


class LongFormPlanUnit(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    id: str = Field(pattern=r"^unit-[0-9]{3}$")
    ordinal: int = Field(ge=1, le=MAX_WORKSPACE_UNITS)
    title: str = Field(min_length=1, max_length=200)
    summary: str = Field(min_length=1, max_length=4_000)
    target_visible_chars: int = Field(ge=100, le=100_000)


class LongFormPlanDraft(BaseModel):
    """Reviewable local plan; ``confirmed`` is an explicit author decision."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    contract_version: Literal["cwe-2.1-long-form-author-plan-v1"] = (
        WORKSPACE_PLAN_VERSION
    )
    origin: Literal["local_deterministic"] = LOCAL_PLAN_ORIGIN
    mode: LongFormWorkspaceMode
    title: str = Field(min_length=1, max_length=200)
    author_idea: str = Field(min_length=1, max_length=100_000)
    genre: str = Field(default="", max_length=200)
    tone: str = Field(default="", max_length=200)
    character_world_constraints: str = Field(default="", max_length=20_000)
    author_constraints: str = Field(default="", max_length=20_000)
    total_target_visible_chars: int = Field(ge=100, le=10_000_000)
    outline_summary: str = Field(min_length=1, max_length=100_000)
    units: tuple[LongFormPlanUnit, ...] = Field(
        min_length=1,
        max_length=MAX_WORKSPACE_UNITS,
    )
    confirmed: bool = False

    @model_validator(mode="after")
    def _coherent_plan(self) -> Self:
        if tuple(unit.ordinal for unit in self.units) != tuple(
            range(1, len(self.units) + 1)
        ):
            raise ValueError("long-form unit ordinals must be contiguous and one-based")
        if len({unit.id for unit in self.units}) != len(self.units):
            raise ValueError("long-form unit IDs must be unique")
        if len({unit.title.casefold() for unit in self.units}) != len(self.units):
            raise ValueError("long-form unit titles must be unique")
        if sum(unit.target_visible_chars for unit in self.units) != (
            self.total_target_visible_chars
        ):
            raise ValueError("long-form unit targets must add up to the total target")
        return self

    @property
    def fingerprint(self) -> str:
        payload = self.model_dump(mode="json", exclude={"confirmed"})
        return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


class AuthorUnitProgress(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    unit_id: str
    ordinal: int = Field(ge=1)
    title: str
    status: AuthorUnitStatus
    visible_chars: int = Field(ge=0)
    target_visible_chars: int = Field(ge=0)
    saved_text: str = ""


class ContinuityIssueLocation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    unit_id: str
    unit_title: str
    paragraph_index: int | None = Field(default=None, ge=1)
    message: str = Field(min_length=1, max_length=20_000)


_PARAGRAPH_LOCATION = re.compile(
    r"(?:paragraph|para|段落)\s*(?:#|:|：)?\s*(?P<index>[1-9][0-9]*)",
    flags=re.IGNORECASE,
)


def suggested_unit_count(
    total_target_visible_chars: int,
    *,
    preferred_unit_target: int = DEFAULT_UNIT_TARGET_VISIBLE_CHARS,
) -> int:
    """Choose a bounded chapter count without claiming model intelligence."""

    if total_target_visible_chars < 100:
        raise ValueError("total target must be at least 100 visible characters")
    if preferred_unit_target < 100:
        raise ValueError("preferred unit target must be at least 100")
    quotient, remainder = divmod(total_target_visible_chars, preferred_unit_target)
    count = quotient + int(bool(remainder))
    return min(MAX_WORKSPACE_UNITS, max(1, count))


def build_local_long_form_plan(
    *,
    mode: LongFormWorkspaceMode,
    author_idea: str,
    genre: str,
    tone: str,
    total_target_visible_chars: int,
    requested_unit_count: int | None = None,
    character_world_constraints: str = "",
    author_constraints: str = "",
) -> LongFormPlanDraft:
    """Build a deterministic, offline plan draft for explicit author review."""

    idea = author_idea.strip()
    if not idea:
        raise ValueError("請先寫下故事想法")
    if total_target_visible_chars < 100:
        raise ValueError("目標總字數至少為 100 個可見字元")
    unit_count = (
        requested_unit_count
        if requested_unit_count is not None
        else suggested_unit_count(total_target_visible_chars)
    )
    if not 1 <= unit_count <= MAX_WORKSPACE_UNITS:
        raise ValueError(f"章數必須介於 1 與 {MAX_WORKSPACE_UNITS} 之間")
    if total_target_visible_chars < unit_count * 100:
        raise ValueError("目標總字數不足以為每章配置至少 100 個可見字元")

    title = _plan_title(idea)
    targets = _distribute_target(total_target_visible_chars, unit_count)
    units = tuple(
        LongFormPlanUnit(
            id=f"unit-{ordinal:03d}",
            ordinal=ordinal,
            title=_unit_title(ordinal, unit_count),
            summary=_unit_summary(ordinal, unit_count, idea),
            target_visible_chars=targets[ordinal - 1],
        )
        for ordinal in range(1, unit_count + 1)
    )
    genre_text = genre.strip() or "未指定類型"
    tone_text = tone.strip() or "未指定語氣"
    outline_summary = (
        f"《{title}》將以「{genre_text}」與「{tone_text}」為方向，"
        f"分成 {unit_count} 章推進。這是本機依作者輸入整理的規劃草稿，"
        "尚未呼叫任何 AI 模型服務；正文開始前仍須由作者確認。"
    )
    return LongFormPlanDraft(
        mode=mode,
        title=title,
        author_idea=idea,
        genre=genre.strip(),
        tone=tone.strip(),
        character_world_constraints=character_world_constraints.strip(),
        author_constraints=author_constraints.strip(),
        total_target_visible_chars=total_target_visible_chars,
        outline_summary=outline_summary,
        units=units,
    )


def confirm_long_form_plan(
    draft: LongFormPlanDraft,
    *,
    outline_summary: str,
    unit_titles: Sequence[str],
    unit_summaries: Sequence[str] | None = None,
) -> LongFormPlanDraft:
    """Return a newly validated plan carrying the author's confirmation."""

    outline = outline_summary.strip()
    if not outline:
        raise ValueError("故事大綱不可留白")
    titles = tuple(value.strip() for value in unit_titles)
    summaries = (
        tuple(value.strip() for value in unit_summaries)
        if unit_summaries is not None
        else tuple(unit.summary for unit in draft.units)
    )
    if len(titles) != len(draft.units) or len(summaries) != len(draft.units):
        raise ValueError("章名與章節方向數量必須和規劃章數一致")
    if any(not value for value in (*titles, *summaries)):
        raise ValueError("章名與章節方向不可留白")
    units = [
        {
            **unit.model_dump(mode="json"),
            "title": title,
            "summary": summary,
        }
        for unit, title, summary in zip(draft.units, titles, summaries, strict=True)
    ]
    # ``model_copy(update=...)`` intentionally skips validation in Pydantic.
    # Re-parse the complete payload so duplicate titles, text bounds, and the
    # exact total target contract cannot slip through the confirmation gate.
    return LongFormPlanDraft.model_validate(
        {
            **draft.model_dump(mode="json"),
            "outline_summary": outline,
            "units": units,
            "confirmed": True,
        }
    )


def project_unit_progress(
    job: GenerationJob,
    steps: Sequence[GenerationStep],
    *,
    worker_active: bool = False,
    author_decisions: Sequence[UnitAuthorDecision] = (),
) -> tuple[AuthorUnitProgress, ...]:
    """Aggregate executor rows into one honest row per author-visible unit."""

    units = _job_units(job)
    grouped: dict[str, list[GenerationStep]] = {str(unit["id"]): [] for unit in units}
    for step in steps:
        unit = _step_unit(step)
        if unit is not None and str(unit.get("id", "")) in grouped:
            grouped[str(unit["id"])].append(step)

    projected: list[AuthorUnitProgress] = []
    for ordinal, raw_unit in enumerate(units, start=1):
        unit_id = str(raw_unit["id"])
        unit_steps = grouped[unit_id]
        saved_text = _latest_saved_text(unit_steps)
        raw_target = raw_unit.get("target_words", 0)
        target = (
            raw_target
            if isinstance(raw_target, int)
            and not isinstance(raw_target, bool)
            and raw_target >= 0
            else 0
        )
        if any(
            item.unit_id == unit_id and item.kind == "adjust_target" for item in author_decisions
        ):
            target = effective_spec(job, unit_id, "", author_decisions).target
        metadata = raw_unit.get("metadata")
        raw_ordinal = metadata.get("ordinal") if isinstance(metadata, Mapping) else None
        safe_ordinal = (
            raw_ordinal
            if isinstance(raw_ordinal, int)
            and not isinstance(raw_ordinal, bool)
            and raw_ordinal >= 1
            else ordinal
        )
        visible_chars = count_visible_characters(saved_text)
        projected.append(
            AuthorUnitProgress(
                unit_id=unit_id,
                ordinal=safe_ordinal,
                title=str(raw_unit.get("title", f"第 {ordinal} 章")),
                status=_project_unit_status(
                    job,
                    unit_steps,
                    worker_active=worker_active,
                    visible_chars=visible_chars,
                    target_visible_chars=target,
                ),
                visible_chars=visible_chars,
                target_visible_chars=target,
                saved_text=saved_text,
            )
        )
    return tuple(projected)


def project_continuity_issues(
    steps: Sequence[GenerationStep],
) -> tuple[ContinuityIssueLocation, ...]:
    """Attach every reported continuity item to at least its durable unit."""

    issues: list[ContinuityIssueLocation] = []
    for step in steps:
        if step.step_kind is not StepKind.CONTINUITY_CHECK or step.output_json is None:
            continue
        unit = _step_unit(step)
        if unit is None:
            continue
        passed = step.output_json.get("passed")
        raw_items = step.output_json.get("items")
        if passed is True or not isinstance(raw_items, (list, tuple)):
            continue
        for raw in raw_items:
            message = str(raw).strip()
            if not message:
                continue
            match = _PARAGRAPH_LOCATION.search(message)
            issues.append(
                ContinuityIssueLocation(
                    unit_id=str(unit.get("id", "")),
                    unit_title=str(unit.get("title", "未命名章節")),
                    paragraph_index=(int(match.group("index")) if match else None),
                    message=message,
                )
            )
    return tuple(issues)


def author_progress_message(
    job: GenerationJob,
    units: Sequence[AuthorUnitProgress],
) -> str:
    """Describe persisted progress without executor/checkpoint vocabulary."""

    if job.status is JobStatus.COMPLETED:
        return "長篇候選已完成，等你檢視與決定是否套用。"
    if job.status is JobStatus.CANCELLED:
        return "這次生成已取消；先前完成的章節與草稿仍已保存。"
    if job.status is JobStatus.PAUSED:
        return "生成已暫停；可以從已保存的位置繼續。"
    if job.status is JobStatus.FAILED:
        return "目前章節未能完成；已保存內容仍在，可只重試失敗章節。"
    if job.status is JobStatus.NEEDS_AUTHOR_INPUT:
        return "需要你確認目前方向後再繼續，已保存內容不會被改寫。"
    current = next(
        (unit for unit in units if unit.status is not AuthorUnitStatus.COMPLETE),
        None,
    )
    if current is None:
        return "正在整理完整長篇候選。"
    if current.status is AuthorUnitStatus.RESUMABLE:
        return (
            f"〈{current.title}〉先前生成已中斷；目前已保存 "
            f"{current.visible_chars:,} / {current.target_visible_chars:,} 字。"
            "可繼續：由你決定何時從保存位置接續。"
        )
    if current.status is AuthorUnitStatus.PLANNING:
        return f"正在規劃〈{current.title}〉。"
    if current.status is AuthorUnitStatus.WRITING:
        return (
            f"正在撰寫〈{current.title}〉；目前已保存 "
            f"{current.visible_chars:,} / {current.target_visible_chars:,} 字。"
        )
    if current.status is AuthorUnitStatus.CHECKING:
        return f"正在檢查〈{current.title}〉的篇幅與連貫性。"
    if current.status is AuthorUnitStatus.REVISING:
        return f"正在定點修訂〈{current.title}〉。"
    return f"〈{current.title}〉等待開始。"


def long_form_plan_author_texts(plan: LongFormPlanDraft) -> tuple[str, ...]:
    """Return every plan string that can cross a durable persistence boundary."""

    return tuple(
        value
        for value in (
            plan.title,
            plan.author_idea,
            plan.genre,
            plan.tone,
            plan.character_world_constraints,
            plan.author_constraints,
            plan.outline_summary,
            *(unit.title for unit in plan.units),
            *(unit.summary for unit in plan.units),
        )
        if value
    )


def long_form_job_id(project_id: str, plan: LongFormPlanDraft) -> str:
    """Return the stable idempotency identity for one confirmed project plan."""

    normalized_project_id = project_id.strip()
    if not normalized_project_id:
        raise ValueError("project ID must not be blank")
    digest = hashlib.sha256(
        f"{normalized_project_id}:{plan.fingerprint}".encode()
    ).hexdigest()
    return f"story-long-n4-{digest[:48]}"


def _plan_title(idea: str) -> str:
    first_line = next((line.strip() for line in idea.splitlines() if line.strip()), idea)
    sentence = re.split(r"[。！？!?]", first_line, maxsplit=1)[0].strip()
    return (sentence or "未命名長篇")[:80]


def _distribute_target(total: int, count: int) -> tuple[int, ...]:
    base, remainder = divmod(total, count)
    return tuple(base + int(index < remainder) for index in range(count))


def _unit_title(ordinal: int, total: int) -> str:
    if total == 1:
        phase = "完整故事"
    elif ordinal == 1:
        phase = "起點"
    elif ordinal == total:
        phase = "收束"
    elif ordinal <= max(2, total // 2):
        phase = "推進"
    else:
        phase = "轉折"
    return f"第 {ordinal} 章｜{phase}"


def _unit_summary(ordinal: int, total: int, idea: str) -> str:
    if total == 1:
        direction = "建立核心承諾、推進衝突，並完成有代價的收束"
    elif ordinal == 1:
        direction = "建立人物、世界與核心故事承諾"
    elif ordinal == total:
        direction = "兌現故事承諾，處理核心衝突與角色選擇的代價"
    elif ordinal <= max(2, total // 2):
        direction = "擴大阻力與關係，讓角色的選擇推動故事"
    else:
        direction = "讓既有選擇產生不可逆後果，推向最終決定"
    idea_hint = re.sub(r"\s+", " ", idea).strip()[:240]
    return f"{direction}。承接作者想法：{idea_hint}"


def _job_units(job: GenerationJob) -> tuple[dict[str, object], ...]:
    raw = job.length_spec.get("units")
    if not isinstance(raw, list):
        return ()
    return tuple(
        dict(item)
        for item in raw
        if isinstance(item, Mapping)
        and isinstance(item.get("id"), str)
        and isinstance(item.get("title"), str)
    )


def _step_unit(step: GenerationStep) -> Mapping[str, object] | None:
    planned = step.input_snapshot.get("planned")
    unit = planned.get("unit") if isinstance(planned, Mapping) else None
    return unit if isinstance(unit, Mapping) else None


def _latest_saved_text(steps: Sequence[GenerationStep]) -> str:
    preference = {
        StepKind.LENGTH_ASSURANCE: 4,
        StepKind.TARGETED_REVISION: 3,
        StepKind.PROSE_CHUNK: 2,
    }
    candidates: list[tuple[int, int, str]] = []
    for step in steps:
        text = step.output_text or step.partial_text
        if text:
            candidates.append((preference.get(step.step_kind, 1), step.ordinal, text))
    return max(candidates, default=(0, 0, ""))[2]


def _project_unit_status(
    job: GenerationJob,
    steps: Sequence[GenerationStep],
    *,
    worker_active: bool,
    visible_chars: int,
    target_visible_chars: int,
) -> AuthorUnitStatus:
    statuses = {step.status for step in steps}
    if StepStatus.FAILED in statuses:
        return AuthorUnitStatus.FAILED
    if StepStatus.NEEDS_AUTHOR_INPUT in statuses:
        return AuthorUnitStatus.NEEDS_AUTHOR_INPUT
    # A terminal job state describes what may happen next, not what already
    # happened to every unit. Preserve visibly completed chapters when a
    # later unit is paused or the overall job is cancelled.
    length_steps = [step for step in steps if step.step_kind is StepKind.LENGTH_ASSURANCE]
    if (
        length_steps
        and all(step.status is StepStatus.COMPLETED for step in length_steps)
        and visible_chars >= target_visible_chars
    ):
        return AuthorUnitStatus.COMPLETE
    if job.status is JobStatus.FAILED:
        return AuthorUnitStatus.FAILED
    if job.status is JobStatus.NEEDS_AUTHOR_INPUT:
        return AuthorUnitStatus.NEEDS_AUTHOR_INPUT
    if StepStatus.CANCELLED in statuses or job.status is JobStatus.CANCELLED:
        return AuthorUnitStatus.CANCELLED
    if StepStatus.PAUSED in statuses or job.status is JobStatus.PAUSED:
        return AuthorUnitStatus.PAUSED
    running = next((step for step in steps if step.status is StepStatus.RUNNING), None)
    if running is not None:
        if running.step_kind in {StepKind.CHAPTER_OR_SCENE_PLAN}:
            return AuthorUnitStatus.PLANNING
        if running.step_kind in {StepKind.PROSE_CHUNK, StepKind.LENGTH_ASSURANCE}:
            return AuthorUnitStatus.WRITING
        if running.step_kind in {StepKind.TARGETED_REVISION}:
            return AuthorUnitStatus.REVISING
        return AuthorUnitStatus.CHECKING
    # A normal bounded continuation can also persist INTERRUPTED between
    # attempts. Only offer author-driven recovery when no worker is active;
    # neither this projection nor a retained job phase starts new execution.
    if (
        StepStatus.INTERRUPTED in statuses
        and job.status in ACTIVE_JOB_STATUSES
        and not worker_active
    ):
        return AuthorUnitStatus.RESUMABLE
    completed_kinds = {
        step.step_kind for step in steps if step.status is StepStatus.COMPLETED
    }
    if StepKind.TARGETED_REVISION in completed_kinds:
        return AuthorUnitStatus.REVISING
    if StepKind.PROSE_CHUNK in completed_kinds:
        return AuthorUnitStatus.CHECKING
    if StepKind.CHAPTER_OR_SCENE_PLAN in completed_kinds:
        return AuthorUnitStatus.WRITING
    if steps:
        return AuthorUnitStatus.PLANNING
    return AuthorUnitStatus.WAITING


__all__ = [
    "DEFAULT_UNIT_TARGET_VISIBLE_CHARS",
    "LOCAL_PLAN_ORIGIN",
    "MAX_WORKSPACE_UNITS",
    "WORKSPACE_PLAN_VERSION",
    "AuthorUnitProgress",
    "AuthorUnitStatus",
    "ContinuityIssueLocation",
    "LongFormPlanDraft",
    "LongFormPlanUnit",
    "LongFormWorkspaceMode",
    "author_progress_message",
    "build_local_long_form_plan",
    "confirm_long_form_plan",
    "long_form_job_id",
    "long_form_plan_author_texts",
    "project_continuity_issues",
    "project_unit_progress",
    "suggested_unit_count",
]
