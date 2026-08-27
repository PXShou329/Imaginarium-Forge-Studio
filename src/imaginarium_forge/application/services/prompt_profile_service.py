"""Prompt profile stack resolution (spec §6.3, §28).

`resolve_stack` is THE way the Studio obtains a ResolvedProfile:

- known checkpoint  → dialect + its checkpoint profile (+ optional preset);
- unknown checkpoint → dialect only, with a VISIBLE injected warning and a
  provenance-labeled note — never guessed score tags, never guessed family
  syntax (§6.3). The injected warning participates in the snapshot hash, so
  reproducibility records show the prompt was compiled under fallback.

Manual profile assignment stays possible: pass `checkpoint_profile_id`
explicitly and it wins over filename lookup.
"""

from __future__ import annotations

from imaginarium_forge.domain.prompt.profiles import (
    NoteProvenance,
    ProfileKind,
    ProfileNote,
    PromptProfileLayer,
    ResolvedProfile,
    resolve_profiles,
)
from imaginarium_forge.infrastructure.profiles.loader import ProfileLoader

UNKNOWN_CHECKPOINT_WARNING = (
    "此 checkpoint 尚無對應 profile：已改用保守通用語法（無模型專屬品質分數、"
    "無猜測家族語法）。可於 Checkpoint Registry 手動指派 profile，並以實驗紀錄累積證據。"
)


class ProfileResolutionError(RuntimeError):
    pass


class PromptProfileService:
    def __init__(self, loader: ProfileLoader) -> None:
        self._loader = loader

    # ---------------------------------------------------------------- lists
    def list_dialects(self) -> list[PromptProfileLayer]:
        return self._loader.list_layers(ProfileKind.DIALECT)

    def list_checkpoint_profiles(self) -> list[PromptProfileLayer]:
        return self._loader.list_layers(ProfileKind.CHECKPOINT)

    def list_presets(self) -> list[PromptProfileLayer]:
        return self._loader.list_layers(ProfileKind.PRESET)

    # -------------------------------------------------------------- resolve
    def resolve_stack(
        self,
        *,
        dialect_id: str,
        checkpoint_filename: str = "",
        checkpoint_profile_id: str = "",
        preset_id: str = "",
    ) -> tuple[ResolvedProfile, bool]:
        """Resolve the three-layer stack.

        Returns `(resolved, checkpoint_known)`. `checkpoint_known` is False
        exactly when a checkpoint filename was given but no profile matched
        and none was manually assigned — the §6.3 fallback case.
        """
        dialect = self._loader.get(ProfileKind.DIALECT, dialect_id)
        if dialect is None:
            raise ProfileResolutionError(f"找不到 dialect：{dialect_id}")

        checkpoint_layer: PromptProfileLayer | None = None
        if checkpoint_profile_id:  # manual assignment wins (§6.3, §7.6)
            checkpoint_layer = self._loader.get(
                ProfileKind.CHECKPOINT, checkpoint_profile_id
            )
            if checkpoint_layer is None:
                raise ProfileResolutionError(
                    f"找不到 checkpoint profile：{checkpoint_profile_id}"
                )
        elif checkpoint_filename:
            checkpoint_layer = self._loader.find_checkpoint_profile_for_filename(
                checkpoint_filename
            )

        preset_layer: PromptProfileLayer | None = None
        if preset_id:
            preset_layer = self._loader.get(ProfileKind.PRESET, preset_id)
            if preset_layer is None:
                raise ProfileResolutionError(f"找不到 preset：{preset_id}")

        resolved = resolve_profiles(dialect, checkpoint_layer, preset_layer)

        unknown = bool(checkpoint_filename) and checkpoint_layer is None
        if unknown:
            resolved = self._apply_unknown_fallback(resolved, checkpoint_filename)
        return resolved, not unknown

    @staticmethod
    def _apply_unknown_fallback(
        resolved: ResolvedProfile, filename: str
    ) -> ResolvedProfile:
        note = ProfileNote(
            text=f"checkpoint「{filename}」無已知 profile，套用保守通用語法。",
            provenance=NoteProvenance.CONFIRMED,
            source_layer="fallback:unknown_checkpoint",
        )
        return resolved.model_copy(
            update={
                "low_confidence_warning": UNKNOWN_CHECKPOINT_WARNING,
                "profile_status": "unidentified",
                "recommendation_status": "not_yet_recommended",
                "notes": (*resolved.notes, note),
            }
        )
