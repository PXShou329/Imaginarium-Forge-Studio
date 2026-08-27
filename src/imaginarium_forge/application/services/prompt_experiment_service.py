"""Prompt experiment service (spec §9).

Records MANUAL ComfyUI generation experiments. This service never talks to
ComfyUI, never generates images, and never claims same-seed cross-checkpoint
scientific equivalence — comparison output is aligned records plus the list
of differing fields, nothing more.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from imaginarium_forge.application.services.base import ServiceBase
from imaginarium_forge.domain.prompt.experiments import ExperimentLog
from imaginarium_forge.infrastructure.db.repositories.checkpoint_repos import (
    ExperimentRepository,
)
from imaginarium_forge.infrastructure.db.repositories.prompt_repos import (
    ProfileSnapshotRepository,
    PromptVariantRepository,
)


#: the single-variable fields duplicate_with_change accepts (§9.3)
class _Unset:
    """A2-06 sentinel: distinguishes "omitted → preserve" from an explicit
    None/empty value, which now means "clear"."""

    def __repr__(self) -> str:  # pragma: no cover — debug aid
        return "UNSET"


UNSET: Any = _Unset()

#: A2-07 — documented ComfyUI-compatible seed range (unsigned 64-bit)
SEED_MAX = 2**64 - 1


def _validate_params(
    *,
    steps: int | None,
    cfg: float | None,
    width: int | None,
    height: int | None,
    seed: int | None,
) -> None:
    """A2-07: reject structurally invalid generation settings — no silent
    coercion (review §3.6 accepted steps=-1, cfg=-3, width=0)."""
    if steps is not None and steps <= 0:
        raise ExperimentServiceError(f"steps 必須為正整數，收到 {steps}")
    if cfg is not None and cfg < 0:
        raise ExperimentServiceError(f"CFG 必須 >= 0，收到 {cfg}")
    if width is not None and width <= 0:
        raise ExperimentServiceError(f"width 必須為正整數，收到 {width}")
    if height is not None and height <= 0:
        raise ExperimentServiceError(f"height 必須為正整數，收到 {height}")
    if seed is not None and not 0 <= seed <= SEED_MAX:
        raise ExperimentServiceError(
            f"seed 必須介於 0 與 {SEED_MAX}（無號 64 位元），收到 {seed}"
        )


_MUTABLE_FIELDS = frozenset(
    {
        "sampler",
        "scheduler",
        "steps",
        "cfg",
        "width",
        "height",
        "seed",
        "checkpoint_id",
        "checkpoint_sha256",
        "notes",
    }
)


class ExperimentComparison(BaseModel):
    model_config = ConfigDict(frozen=True)

    experiments: tuple[ExperimentLog, ...] = ()
    differing_fields: tuple[str, ...] = ()


class ExperimentServiceError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class PromptExperimentService(ServiceBase):
    # ---------------------------------------------------------------- create
    def create_from_variant(
        self,
        variant_id: str,
        *,
        checkpoint_id: str = "",
        checkpoint_sha256: str = "",
        sampler: str = "",
        scheduler: str = "",
        steps: int | None = None,
        cfg: float | None = None,
        width: int | None = None,
        height: int | None = None,
        seed: int | None = None,
        notes: str = "",
    ) -> ExperimentLog:
        """§9.3: an experiment starts from a compiled prompt variant, pulling
        prompts, project linkage, and the resolved-profile hash from it."""
        with self._session_factory() as session:
            variant = PromptVariantRepository(session).get(variant_id)
            if variant is None:
                raise ExperimentServiceError(f"找不到 prompt variant：{variant_id}")
            snapshot = ProfileSnapshotRepository(session).get(variant.profile_snapshot_id)
            profile_hash = snapshot.sha256 if snapshot else ""
            # Gate A A-06: default to the variant's frozen checkpoint identity
            log = ExperimentLog(
                id=str(uuid.uuid4()),
                checkpoint_id=checkpoint_id or variant.checkpoint_id,
                checkpoint_sha256=(
                    checkpoint_sha256 or variant.checkpoint_sha256_snapshot
                ),
                prompt_project_id=variant.prompt_project_id,
                prompt_variant_id=variant.id,
                resolved_profile_hash=profile_hash,
                positive_prompt=variant.positive_prompt,
                negative_prompt=variant.negative_prompt,
                sampler=sampler,
                scheduler=scheduler,
                steps=steps,
                cfg=cfg,
                width=width,
                height=height,
                seed=seed,
                notes=notes,
                created_at=_now(),
            )
            ExperimentRepository(session).add(log)
            session.commit()
            return log

    # ------------------------------------------------------------------ rate
    def update_result_assessment(
        self,
        experiment_id: str,
        *,
        sampler: str | Any | None = UNSET,
        scheduler: str | Any | None = UNSET,
        steps: int | Any | None = UNSET,
        cfg: float | Any | None = UNSET,
        width: int | Any | None = UNSET,
        height: int | Any | None = UNSET,
        seed: int | Any | None = UNSET,
        overall_rating: int | Any | None = UNSET,
        identity_score: int | Any | None = UNSET,
        style_score: int | Any | None = UNSET,
        instruction_adherence: int | Any | None = UNSET,
        failure_tags: tuple[str, ...] | Any = UNSET,
        notes: str | Any = UNSET,
        local_output_reference: str | Any = UNSET,
    ) -> ExperimentLog:
        """A2-06 patch semantics (review §3.4):

        - omitted (UNSET)      → preserve the existing value;
        - explicit None/empty  → CLEAR the value;
        - a concrete value     → replace (after A2-07 validation).
        """
        updates: dict[str, object] = {}
        for name, value in (
            ("sampler", sampler), ("scheduler", scheduler),
            ("steps", steps), ("cfg", cfg), ("width", width),
            ("height", height), ("seed", seed),
            ("overall_rating", overall_rating),
            ("identity_score", identity_score),
            ("style_score", style_score),
            ("instruction_adherence", instruction_adherence),
            ("failure_tags", failure_tags),
            ("notes", notes),
            ("local_output_reference", local_output_reference),
        ):
            if value is not UNSET:
                updates[name] = value
        # normalize EXPLICIT clears (None → empty) — an omitted field is not
        # in `updates` at all and must stay untouched
        for text_field in ("sampler", "scheduler", "notes", "local_output_reference"):
            if text_field in updates and updates[text_field] is None:
                updates[text_field] = ""
        if "failure_tags" in updates and updates["failure_tags"] is None:
            updates["failure_tags"] = ()
        for label in (
            "overall_rating", "identity_score",
            "style_score", "instruction_adherence",
        ):
            score = updates.get(label)
            if score is not None and label in updates and not 1 <= score <= 5:  # type: ignore[operator]
                raise ExperimentServiceError(f"{label} 必須介於 1–5，收到 {score}")
        _validate_params(
            steps=updates.get("steps"),  # type: ignore[arg-type]
            cfg=updates.get("cfg"),  # type: ignore[arg-type]
            width=updates.get("width"),  # type: ignore[arg-type]
            height=updates.get("height"),  # type: ignore[arg-type]
            seed=updates.get("seed"),  # type: ignore[arg-type]
        )
        with self._session_factory() as session:
            repo = ExperimentRepository(session)
            existing = repo.get(experiment_id)
            if existing is None:
                raise ExperimentServiceError(f"找不到實驗：{experiment_id}")
            updated = existing.model_copy(update=updates)
            self._update_row(session, updated)
            session.commit()
            return updated

    # ------------------------------------------------------------- duplicate
    def duplicate_with_change(
        self, experiment_id: str, *, field: str, value: Any
    ) -> ExperimentLog:
        """§9.3: duplicate an experiment changing EXACTLY one variable."""
        if field not in _MUTABLE_FIELDS:
            raise ExperimentServiceError(
                f"不允許以「{field}」作為單一變因；允許：{sorted(_MUTABLE_FIELDS)}"
            )
        # A2-07: numeric variables must be structurally valid
        if field in {"steps", "width", "height", "seed", "cfg"}:
            _validate_params(
                steps=value if field == "steps" else None,
                cfg=value if field == "cfg" else None,
                width=value if field == "width" else None,
                height=value if field == "height" else None,
                seed=value if field == "seed" else None,
            )
        with self._session_factory() as session:
            repo = ExperimentRepository(session)
            source = repo.get(experiment_id)
            if source is None:
                raise ExperimentServiceError(f"找不到實驗：{experiment_id}")
            # A2-08: "exactly one changed variable" requires the value to
            # actually differ from the source (review §4.8)
            if getattr(source, field) == value:
                raise ExperimentServiceError(
                    f"單一變因複製要求新值必須不同：{field} 目前即為 {value!r}"
                )
            # A2-08: checkpoint changes must reference an EXISTING registry
            # row, reported as a normalized error — not a raw IntegrityError
            # escaping to the UI (review §3.5)
            if field == "checkpoint_id" and value:
                from imaginarium_forge.infrastructure.db.repositories.checkpoint_repos import (
                    CheckpointRepository,
                )

                if CheckpointRepository(session).get(str(value)) is None:
                    raise ExperimentServiceError(
                        f"找不到 checkpoint：{value}。"
                        "變更 checkpoint 後，原 resolved profile 可能不再對應；"
                        "請於 Registry 確認並重新評估。"
                    )
            duplicate = source.model_copy(
                update={
                    "id": str(uuid.uuid4()),
                    field: value,
                    # ratings describe a PAST result; a new run starts unrated
                    "overall_rating": None,
                    "identity_score": None,
                    "style_score": None,
                    "instruction_adherence": None,
                    "failure_tags": (),
                    "local_output_reference": "",
                    "created_at": _now(),
                }
            )
            repo.add(duplicate)
            session.commit()
            return duplicate

    # -------------------------------------------------------------- compare
    def compare(self, experiment_ids: tuple[str, ...]) -> ExperimentComparison:
        with self._session_factory() as session:
            repo = ExperimentRepository(session)
            logs: list[ExperimentLog] = []
            for experiment_id in experiment_ids:
                log = repo.get(experiment_id)
                if log is None:
                    raise ExperimentServiceError(f"找不到實驗：{experiment_id}")
                logs.append(log)
        if len(logs) < 2:
            return ExperimentComparison(experiments=tuple(logs))
        dumps = [log.model_dump(mode="json") for log in logs]
        ignore = {"id", "created_at"}
        differing = tuple(
            sorted(
                key
                for key in dumps[0]
                if key not in ignore
                and any(d[key] != dumps[0][key] for d in dumps[1:])
            )
        )
        return ExperimentComparison(experiments=tuple(logs), differing_fields=differing)

    # ----------------------------------------------------------------- lists
    def list_experiments(
        self,
        *,
        checkpoint_id: str = "",
        profile_hash: str = "",
        prompt_project_id: str = "",
    ) -> list[ExperimentLog]:
        with self._session_factory() as session:
            repo = ExperimentRepository(session)
            logs = (
                repo.list_for_checkpoint(checkpoint_id)
                if checkpoint_id
                else repo.list_all()
            )
        if profile_hash:
            logs = [log for log in logs if log.resolved_profile_hash == profile_hash]
        if prompt_project_id:
            logs = [
                log for log in logs if log.prompt_project_id == prompt_project_id
            ]
        return logs

    # ------------------------------------------------------------- internals
    def _update_row(self, session, log: ExperimentLog) -> None:  # type: ignore[no-untyped-def]
        """Write assessment fields onto the existing row (A-08 §11.4: no
        assert-based control flow; missing rows raise a normalized error)."""
        from imaginarium_forge.infrastructure.db.models.orm import PromptExperimentLogRow

        row = session.get(PromptExperimentLogRow, log.id)
        if row is None:
            raise ExperimentServiceError(f"找不到實驗：{log.id}")
        row.result_rating = log.overall_rating
        row.identity_score = log.identity_score
        row.style_score = log.style_score
        row.instruction_adherence = log.instruction_adherence
        row.failure_tags_json = json.dumps(list(log.failure_tags), ensure_ascii=False)
        row.notes = log.notes
        row.local_output_reference = log.local_output_reference
        row.sampler = log.sampler
        row.scheduler = log.scheduler
        row.steps = log.steps
        row.cfg = log.cfg
        row.width = log.width
        row.height = log.height
        row.seed = log.seed
