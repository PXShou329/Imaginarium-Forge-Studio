"""Planning chain resolution (Gate A, A3-R02 §8 / A3-R12 §18).

The reproduced defect: the previous resolver checked only that each planning
version belonged to the same *project*. Two attacks were confirmed against the
delivered build — an Outline version from a DIFFERENT outline, and a Chapter
Plan version from a DIFFERENT chapter, both accepted and used for generation.

Ownership is not coherence. A valid chain satisfies every link:

    Scene            → Chapter → Outline entity
    Outline Version  → that same Outline entity
    Outline Version  → the selected Story Bible Version
    Bible Version    → the selected Story Requirement Version
    Chapter Plan Ver → the Scene's own Chapter
    Scene Card Ver   → the Scene itself

``PlanningMode`` then decides whether unaccepted versions may participate.
``accepted`` (the default) requires every link to be an accepted version.
``preview`` allows explicitly-selected drafts but still enforces every
coherence, ownership and eligibility rule, marks the run, and produces a
different fingerprint — it is a labelled experiment, not a loophole.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from imaginarium_forge.application.errors import (
    NotFoundError,
    PlanningChainMismatchError,
    UnacceptedPlanningError,
)
from imaginarium_forge.canonical import canonical_json
from imaginarium_forge.domain.story.generation_input import PlanningMode
from imaginarium_forge.infrastructure.db.repositories.story_repos import (
    SceneCardVersionRepository,
    StoryChapterRepository,
    StorySceneRepository,
    VersionedEntityRepository,
    VersionRecord,
)

#: bumped when the chain RULES change
PLANNING_CHAIN_CONTRACT_VERSION = "phase3-planning-chain-v1"


@dataclass(frozen=True, slots=True)
class ResolvedPlanningChain:
    """One validated chain. Every ID here is a true VERSION id."""

    project_id: str
    scene_id: str
    chapter_id: str
    outline_id: str
    scene_card_version_id: str
    chapter_plan_version_id: str | None
    outline_version_id: str | None
    bible_version_id: str | None
    requirement_version_id: str | None
    planning_mode: PlanningMode

    @property
    def fingerprint(self) -> str:
        """Identifies the exact chain, including the mode it was resolved under.

        A3-R12 requires preview and accepted runs to be distinguishable even
        when they happen to select the same version IDs.
        """
        payload = canonical_json(
            {
                "contract": PLANNING_CHAIN_CONTRACT_VERSION,
                "project_id": self.project_id,
                "scene_id": self.scene_id,
                "chapter_id": self.chapter_id,
                "outline_id": self.outline_id,
                "scene_card_version_id": self.scene_card_version_id,
                "chapter_plan_version_id": self.chapter_plan_version_id,
                "outline_version_id": self.outline_version_id,
                "bible_version_id": self.bible_version_id,
                "requirement_version_id": self.requirement_version_id,
                "planning_mode": self.planning_mode.value,
            }
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @property
    def version_ids(self) -> tuple[str, ...]:
        """Every non-empty version ID in the chain, for audit and diffing."""
        candidates = (
            self.scene_card_version_id,
            self.chapter_plan_version_id,
            self.outline_version_id,
            self.bible_version_id,
            self.requirement_version_id,
        )
        return tuple(v for v in candidates if v)


@dataclass(frozen=True, slots=True)
class PlanningSelection:
    """What the caller asked for. ``None`` means "derive it from the chain"."""

    scene_id: str
    scene_card_version_id: str | None = None
    chapter_plan_version_id: str | None = None
    outline_version_id: str | None = None
    bible_version_id: str | None = None
    requirement_version_id: str | None = None
    planning_mode: PlanningMode = PlanningMode.ACCEPTED


class PlanningChainResolver:
    """Resolves and validates one chain against persisted data.

    Constructed per-session by the caller; it does not own a transaction.
    """

    def __init__(self, session: object) -> None:
        self._session = session

    def resolve(self, selection: PlanningSelection) -> ResolvedPlanningChain:
        scenes = StorySceneRepository(self._session)  # type: ignore[arg-type]
        scene = scenes.get(selection.scene_id)
        if scene is None:
            raise NotFoundError(f"找不到場景：{selection.scene_id}")

        chapters = StoryChapterRepository(self._session)  # type: ignore[arg-type]
        chapter = chapters.get(scene.story_chapter_id)
        if chapter is None:
            raise NotFoundError(f"找不到章節：{scene.story_chapter_id}")
        project_id = scene.project_id
        outline_id = chapter.story_outline_id
        accepted_only = selection.planning_mode is PlanningMode.ACCEPTED

        # ---- Scene Card version → THIS scene ------------------------------
        card_version_id = selection.scene_card_version_id or (
            scene.accepted_card_version_id
            if accepted_only
            else (
                scene.accepted_card_version_id or scene.working_head_card_version_id
            )
        )
        if not card_version_id:
            raise NotFoundError(
                "此場景尚無可用的 Scene Card 版本"
                + ("（accepted 模式僅接受已接受版本）" if accepted_only else "")
            )
        card = SceneCardVersionRepository(self._session).get(  # type: ignore[arg-type]
            card_version_id
        )
        if card is None:
            raise NotFoundError(f"找不到 Scene Card 版本：{card_version_id}")
        self._require_project(card.project_id, project_id, "Scene Card 版本")
        if card.parent_id != scene.id:
            raise PlanningChainMismatchError(
                relationship="Scene Card 版本 → 場景",
                expected_parent=f"場景 {scene.id}",
                received_version=f"{card_version_id}（屬於場景 {card.parent_id}）",
            )
        self._require_accepted(card, accepted_only, "Scene Card")

        # ---- Chapter Plan version → THIS scene's chapter ------------------
        plan_version_id = selection.chapter_plan_version_id or (
            chapter.accepted_plan_version_id
            if accepted_only
            else (
                chapter.accepted_plan_version_id
                or chapter.working_head_plan_version_id
            )
        )
        plan = self._load(plan_version_id, "chapter_plan", "章節計畫")
        if plan is not None:
            self._require_project(plan.project_id, project_id, "章節計畫版本")
            if plan.parent_id != chapter.id:
                raise PlanningChainMismatchError(
                    relationship="章節計畫版本 → 章節",
                    expected_parent=f"章節 {chapter.id}（本場景所屬）",
                    received_version=f"{plan.id}（屬於章節 {plan.parent_id}）",
                )
            self._require_accepted(plan, accepted_only, "章節計畫")

        # ---- Outline version → THIS scene's outline entity ----------------
        outlines = VersionedEntityRepository(self._session, "outline")  # type: ignore[arg-type]
        outline_entity = outlines.get_entity(outline_id)
        if outline_entity is None:
            raise NotFoundError(f"找不到大綱：{outline_id}")
        outline_version_id = selection.outline_version_id or (
            outline_entity.accepted_version_id
            if accepted_only
            else (
                outline_entity.accepted_version_id
                or outline_entity.working_head_version_id
            )
        )
        outline_version = self._load(outline_version_id, "outline", "大綱")
        if outline_version is not None:
            self._require_project(outline_version.project_id, project_id, "大綱版本")
            if outline_version.parent_id != outline_id:
                raise PlanningChainMismatchError(
                    relationship="大綱版本 → 大綱",
                    expected_parent=f"大綱 {outline_id}（本場景所屬）",
                    received_version=(
                        f"{outline_version.id}"
                        f"（屬於大綱 {outline_version.parent_id}）"
                    ),
                )
            self._require_accepted(outline_version, accepted_only, "大綱")

        # ---- Bible version: named BY the outline version ------------------
        derived_bible = (
            str(outline_version.payload.get("bible_version_id") or "")
            if outline_version is not None
            else ""
        )
        bible_version_id = selection.bible_version_id or derived_bible or None
        if (
            selection.bible_version_id
            and derived_bible
            and selection.bible_version_id != derived_bible
        ):
            raise PlanningChainMismatchError(
                relationship="大綱版本 → 故事聖經版本",
                expected_parent=f"聖經版本 {derived_bible}（大綱版本所指定）",
                received_version=selection.bible_version_id,
            )
        bible_version = self._load(bible_version_id, "bible", "故事聖經")
        if bible_version is not None:
            self._require_project(bible_version.project_id, project_id, "故事聖經版本")
            self._require_accepted(bible_version, accepted_only, "故事聖經")

        # ---- Requirement version: named BY the bible version --------------
        derived_requirement = (
            str(bible_version.payload.get("requirement_version_id") or "")
            if bible_version is not None
            else ""
        )
        requirement_version_id = (
            selection.requirement_version_id or derived_requirement or None
        )
        if (
            selection.requirement_version_id
            and derived_requirement
            and selection.requirement_version_id != derived_requirement
        ):
            raise PlanningChainMismatchError(
                relationship="故事聖經版本 → 故事需求版本",
                expected_parent=f"需求版本 {derived_requirement}（聖經版本所指定）",
                received_version=selection.requirement_version_id,
            )
        requirement_version = self._load(
            requirement_version_id, "requirement", "故事需求"
        )
        if requirement_version is not None:
            self._require_project(
                requirement_version.project_id, project_id, "故事需求版本"
            )
            self._require_accepted(requirement_version, accepted_only, "故事需求")

        return ResolvedPlanningChain(
            project_id=project_id,
            scene_id=scene.id,
            chapter_id=chapter.id,
            outline_id=outline_id,
            scene_card_version_id=card_version_id,
            chapter_plan_version_id=plan.id if plan is not None else None,
            outline_version_id=(
                outline_version.id if outline_version is not None else None
            ),
            bible_version_id=bible_version.id if bible_version is not None else None,
            requirement_version_id=(
                requirement_version.id if requirement_version is not None else None
            ),
            planning_mode=selection.planning_mode,
        )

    # ------------------------------------------------------------ helpers
    def _load(
        self, version_id: str | None, kind: str, label: str
    ) -> VersionRecord | None:
        if not version_id:
            return None
        record = VersionedEntityRepository(self._session, kind).get_version(  # type: ignore[arg-type]
            version_id
        )
        if record is None:
            raise NotFoundError(f"找不到{label}版本：{version_id}")
        return record

    @staticmethod
    def _require_project(actual: str, expected: str, label: str) -> None:
        if actual != expected:
            raise PlanningChainMismatchError(
                relationship=f"{label} → 專案",
                expected_parent=f"專案 {expected}",
                received_version=f"屬於專案 {actual} 的版本",
            )

    @staticmethod
    def _require_accepted(
        record: VersionRecord, accepted_only: bool, label: str
    ) -> None:
        """A3-R12: accepted mode never silently uses draft planning material."""
        if accepted_only and not record.accepted:
            raise UnacceptedPlanningError(entity_label=label, version_id=record.id)
