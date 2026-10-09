"""Idempotent planning anchors for the author-facing long-form workspace.

The durable executor still requires an accepted Story context. This adapter
uses the existing public planning services to create that context only after
the author confirms a local outline. It never calls a generation provider and
never creates or accepts prose.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from imaginarium_forge.application.errors import ConflictError, ValidationFailedError
from imaginarium_forge.application.services.durable_generation.author_workspace import (
    LongFormPlanDraft,
    LongFormPlanUnit,
    long_form_plan_author_texts,
)
from imaginarium_forge.domain.creative.models import (
    CreationMode,
    CreativeLaunchRequest,
    GenreFamily,
)
from imaginarium_forge.domain.prompt.content_mode import ContentMode
from imaginarium_forge.domain.story.models import (
    ChapterPlan,
    SceneCard,
    SceneConflict,
    SceneGoal,
)

_ANCHOR_MARKER = "story-workspace"


class AuthorWorkspacePlanningServices(Protocol):
    creative_launch: Any
    story_requirements: Any
    story_bibles: Any
    story_outlines: Any
    chapter_plans: Any
    scene_cards: Any


class LongFormAnchorUnit(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    unit_id: str
    chapter_id: str
    chapter_plan_version_id: str
    scene_id: str
    scene_card_version_id: str


class LongFormAnchorBundle(BaseModel):
    """Exact accepted Story references backing one confirmed workspace plan."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    project_id: str
    plan_fingerprint: str = Field(min_length=64, max_length=64)
    requirement_id: str
    requirement_version_id: str
    bible_id: str
    bible_version_id: str
    outline_id: str
    outline_version_id: str
    units: tuple[LongFormAnchorUnit, ...] = Field(min_length=1, max_length=100)

    @property
    def primary_scene_id(self) -> str:
        return self.units[0].scene_id

    @property
    def scene_ids(self) -> tuple[str, ...]:
        return tuple(unit.scene_id for unit in self.units)


def ensure_long_form_story_anchors(
    services: AuthorWorkspacePlanningServices,
    *,
    project_id: str,
    plan: LongFormPlanDraft,
    content_preflight: Callable[[tuple[str, ...]], None],
) -> LongFormAnchorBundle:
    """Create or exactly replay accepted planning anchors for ``plan``.

    A process interruption can occur between the existing application-service
    transactions. Every generated entity carries the plan fingerprint in its
    title, and a retry only resumes an exact matching version. A conflicting
    partial aggregate fails closed instead of overwriting author work.
    """

    if not plan.confirmed:
        raise ValidationFailedError("作者尚未確認長篇大綱；不會建立正文工作流")
    normalized_project_id = project_id.strip()
    if not normalized_project_id:
        raise ValidationFailedError("長篇工作區需要一本已選取的作品")
    # This call is deliberately before even the idempotent Foundation write.
    # The callback resolves session/device/environment credentials and rejects
    # known secret material without receiving or returning the secret itself.
    content_preflight(long_form_plan_author_texts(plan))

    request = _launch_request(normalized_project_id, plan)
    foundation = _ensure_foundation(services, request=request)
    units = tuple(
        _ensure_unit(
            services,
            outline_id=foundation["outline_id"],
            project_id=normalized_project_id,
            plan=plan,
            unit=unit,
        )
        for unit in plan.units
    )
    return LongFormAnchorBundle(
        project_id=normalized_project_id,
        plan_fingerprint=plan.fingerprint,
        requirement_id=foundation["requirement_id"],
        requirement_version_id=foundation["requirement_version_id"],
        bible_id=foundation["bible_id"],
        bible_version_id=foundation["bible_version_id"],
        outline_id=foundation["outline_id"],
        outline_version_id=foundation["outline_version_id"],
        units=units,
    )


def _launch_request(project_id: str, plan: LongFormPlanDraft) -> CreativeLaunchRequest:
    constraint_lines = tuple(
        line.strip()[:200]
        for line in plan.author_constraints.splitlines()
        if line.strip()
    )[:24]
    return CreativeLaunchRequest(
        project_id=project_id,
        mode=CreationMode.SERIES_STORY,
        title=_foundation_title(plan),
        concept=plan.author_idea[:4_000],
        story_logline=plan.author_idea[:1_000],
        story_synopsis=plan.outline_summary[:4_000],
        primary_genre=GenreFamily.HYBRID_CUSTOM,
        custom_genre=plan.genre[:200],
        target_length=f"{plan.total_target_visible_chars} 個可見字元",
        tone=plan.tone[:200],
        setting=plan.character_world_constraints[:1_000],
        must_avoid=constraint_lines,
        central_conflict=plan.author_idea[:1_000],
        direction=plan.outline_summary[:1_000],
        ending_preference=plan.units[-1].summary[:1_000],
        content_mode=ContentMode.GENERAL,
    )


def _foundation_title(plan: LongFormPlanDraft) -> str:
    # Keep the complete digest in every idempotency marker. A shortened
    # display digest would make a rare prefix collision indistinguishable
    # from an exact replay at the persistence boundary.
    return f"{plan.title[:100]}｜{_ANCHOR_MARKER}-{plan.fingerprint}"


def _ensure_foundation(
    services: AuthorWorkspacePlanningServices,
    *,
    request: CreativeLaunchRequest,
) -> dict[str, str]:
    expected_titles = {
        "requirement": f"{request.title}｜故事需求",
        "bible": f"{request.title}｜故事聖經",
        "outline": f"{request.title}｜故事大綱",
    }
    service_by_kind = {
        "requirement": services.story_requirements,
        "bible": services.story_bibles,
        "outline": services.story_outlines,
    }
    found: dict[str, Any] = {}
    for kind, service in service_by_kind.items():
        matches = [
            entity
            for entity in service.list_for_project(request.project_id)
            if entity.title == expected_titles[kind]
        ]
        if len(matches) > 1:
            raise ConflictError("同一長篇規劃找到多組 Story 基礎；拒絕猜測或覆寫")
        if matches:
            found[kind] = matches[0]

    if found and len(found) != 3:
        raise ConflictError("長篇規劃的 Story 基礎只存在一部分；請保留資料並進行診斷")
    if not found:
        result = services.creative_launch.save_story_foundation(request)
        found = {
            "requirement": services.story_requirements.get(result.requirement_id),
            "bible": services.story_bibles.get(result.bible_id),
            "outline": services.story_outlines.get(result.outline_id),
        }

    version_ids: dict[str, str] = {}
    for kind, service in service_by_kind.items():
        entity = found[kind]
        version_id = str(entity.working_head_version_id or "")
        if not version_id:
            raise ConflictError(f"長篇規劃的 {kind} 缺少 working 版本")
        version = service.get_version(version_id)
        if version.parent_id != entity.id or version.project_id != request.project_id:
            raise ConflictError(f"長篇規劃的 {kind} 版本來源不一致")
        if version.version_number != 1 or version.change_note != "創作起點建立的工作草稿":
            raise ConflictError(f"長篇規劃的 {kind} 已被修改；拒絕自動接受或覆寫")
        if (
            kind == "requirement"
            and version.payload.get("source_text") != request.concept
        ):
            raise ConflictError("長篇規劃的需求文字與確認內容不同；拒絕自動接受")
        if entity.accepted_version_id not in {None, version_id}:
            raise ConflictError(f"長篇規劃的 {kind} 已接受其他版本；拒絕移動正式指標")
        if entity.accepted_version_id != version_id:
            service.accept_version(version_id)
        version_ids[kind] = version_id

    bible_version = services.story_bibles.get_version(version_ids["bible"])
    outline_version = services.story_outlines.get_version(version_ids["outline"])
    if bible_version.payload.get("requirement_version_id") != version_ids["requirement"]:
        raise ConflictError("長篇故事聖經未連結此規劃的需求版本")
    if outline_version.payload.get("bible_version_id") != version_ids["bible"]:
        raise ConflictError("長篇大綱未連結此規劃的故事聖經版本")

    return {
        "requirement_id": str(found["requirement"].id),
        "requirement_version_id": version_ids["requirement"],
        "bible_id": str(found["bible"].id),
        "bible_version_id": version_ids["bible"],
        "outline_id": str(found["outline"].id),
        "outline_version_id": version_ids["outline"],
    }


def _ensure_unit(
    services: AuthorWorkspacePlanningServices,
    *,
    outline_id: str,
    project_id: str,
    plan: LongFormPlanDraft,
    unit: LongFormPlanUnit,
) -> LongFormAnchorUnit:
    marker = f"{_ANCHOR_MARKER}-{plan.fingerprint}-{unit.id}"
    chapter_title = f"{unit.title[:100]}｜{marker}"
    chapter_matches = [
        chapter
        for chapter in services.chapter_plans.list_chapters(outline_id)
        if chapter.title == chapter_title
    ]
    if len(chapter_matches) > 1:
        raise ConflictError(f"〈{unit.title}〉找到重複章節錨點")
    chapter = (
        chapter_matches[0]
        if chapter_matches
        else services.chapter_plans.create_chapter(
            outline_id=outline_id,
            title=chapter_title,
            chapter_number=unit.ordinal,
        )
    )
    if chapter.project_id != project_id or chapter.chapter_number != unit.ordinal:
        raise ConflictError(f"〈{unit.title}〉章節錨點與確認的大綱不一致")

    expected_plan = ChapterPlan(
        chapter_number=unit.ordinal,
        title=unit.title,
        purpose=unit.summary[:1_000],
        goal=plan.author_idea[:1_000],
        conflict=plan.author_constraints[:1_000],
        outcome=(
            plan.units[unit.ordinal].summary[:1_000]
            if unit.ordinal < len(plan.units)
            else ""
        ),
        scene_intentions=(unit.summary[:1_000],),
        target_word_count=unit.target_visible_chars,
    )
    plan_version_id = _ensure_chapter_plan(services, chapter=chapter, expected=expected_plan)

    scene_title = f"{unit.title[:90]}｜正文錨點｜{marker}"
    scene_matches = [
        scene
        for scene in services.scene_cards.list_scenes(chapter.id)
        if scene.title == scene_title
    ]
    if len(scene_matches) > 1:
        raise ConflictError(f"〈{unit.title}〉找到重複場景錨點")
    scene = (
        scene_matches[0]
        if scene_matches
        else services.scene_cards.create_scene(
            chapter_id=chapter.id,
            title=scene_title,
            scene_number=1,
        )
    )
    if scene.project_id != project_id or scene.story_chapter_id != chapter.id:
        raise ConflictError(f"〈{unit.title}〉場景錨點與確認的大綱不一致")

    expected_card = SceneCard(
        scene_goal=SceneGoal(
            protagonist=plan.author_idea[:1_000],
            narrative=unit.summary[:1_000],
        ),
        conflict=SceneConflict(external=plan.author_constraints[:1_000]),
        beats=(unit.summary[:1_000],),
        turning_point=(
            plan.units[unit.ordinal].summary[:1_000]
            if unit.ordinal < len(plan.units)
            else "完成本章承諾並留下可追蹤的結果"
        ),
        must_include=(
            (plan.character_world_constraints[:1_000],)
            if plan.character_world_constraints
            else ()
        ),
        avoid=((plan.author_constraints[:1_000],) if plan.author_constraints else ()),
        target_word_count=unit.target_visible_chars,
        content_mode=ContentMode.GENERAL,
    )
    card_version_id = _ensure_scene_card(services, scene=scene, expected=expected_card)
    return LongFormAnchorUnit(
        unit_id=unit.id,
        chapter_id=chapter.id,
        chapter_plan_version_id=plan_version_id,
        scene_id=scene.id,
        scene_card_version_id=card_version_id,
    )


def _ensure_chapter_plan(
    services: AuthorWorkspacePlanningServices,
    *,
    chapter: Any,
    expected: ChapterPlan,
) -> str:
    version_id = str(chapter.working_head_plan_version_id or "")
    if version_id:
        stored = services.chapter_plans.load_plan(version_id)
        if stored != expected:
            raise ConflictError("既有章節錨點內容與確認的大綱不同；拒絕覆寫")
    else:
        version = services.chapter_plans.add_plan_version(
            chapter.id,
            plan=expected,
            change_note="作者確認長篇規劃後建立；尚未生成正文",
        )
        version_id = version.id
    refreshed = services.chapter_plans.get_chapter(chapter.id)
    if refreshed.accepted_plan_version_id not in {None, version_id}:
        raise ConflictError("章節錨點已接受其他計畫版本；拒絕移動正式指標")
    if refreshed.accepted_plan_version_id != version_id:
        services.chapter_plans.accept_plan_version(version_id)
    return version_id


def _ensure_scene_card(
    services: AuthorWorkspacePlanningServices,
    *,
    scene: Any,
    expected: SceneCard,
) -> str:
    version_id = str(scene.working_head_card_version_id or "")
    if version_id:
        stored = services.scene_cards.load_card(version_id)
        if stored != expected:
            raise ConflictError("既有場景錨點內容與確認的大綱不同；拒絕覆寫")
    else:
        version = services.scene_cards.add_card_version(
            scene.id,
            card=expected,
            change_note="作者確認長篇規劃後建立；尚未生成正文",
        )
        version_id = version.id
    refreshed = services.scene_cards.get_scene(scene.id)
    if refreshed.accepted_card_version_id not in {None, version_id}:
        raise ConflictError("場景錨點已接受其他 Scene Card；拒絕移動正式指標")
    if refreshed.accepted_card_version_id != version_id:
        services.scene_cards.accept_card_version(version_id)
    return version_id


__all__ = [
    "AuthorWorkspacePlanningServices",
    "LongFormAnchorBundle",
    "LongFormAnchorUnit",
    "ensure_long_form_story_anchors",
]
