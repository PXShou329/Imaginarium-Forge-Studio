"""YAML prompt-profile loader (spec §28.2).

Normative profile definitions live in version-controlled YAML under
`config/prompt_profiles/{dialects,checkpoints,presets}/`. The database stores
only resolved snapshots + hashes, never duplicated rule tables.

Safety: `yaml.safe_load` only; a malformed file raises ProfileLoadError with
the offending path — it never half-loads.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import ValidationError

from imaginarium_forge.domain.prompt.profiles import ProfileKind, PromptProfileLayer

_KIND_DIRS: dict[ProfileKind, str] = {
    ProfileKind.DIALECT: "dialects",
    ProfileKind.CHECKPOINT: "checkpoints",
    ProfileKind.PRESET: "presets",
}


class ProfileLoadError(RuntimeError):
    pass


class ProfileLoader:
    def __init__(self, root: Path) -> None:
        self._root = root

    def _dir(self, kind: ProfileKind) -> Path:
        return self._root / _KIND_DIRS[kind]

    def load_file(self, path: Path) -> PromptProfileLayer:
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            raise ProfileLoadError(f"無法讀取 profile YAML：{path}（{exc}）") from exc
        if not isinstance(raw, dict):
            raise ProfileLoadError(f"profile YAML 內容必須是 mapping：{path}")
        try:
            return PromptProfileLayer.model_validate(raw)
        except ValidationError as exc:
            first = exc.errors()[0]
            raise ProfileLoadError(
                f"profile 驗證失敗：{path} — {first.get('loc')}: {first.get('msg')}"
            ) from exc

    def list_layers(self, kind: ProfileKind) -> list[PromptProfileLayer]:
        directory = self._dir(kind)
        if not directory.exists():
            return []
        layers: list[PromptProfileLayer] = []
        for path in sorted(directory.glob("*.yaml")):
            layer = self.load_file(path)
            if layer.kind is not kind:
                raise ProfileLoadError(
                    f"{path} 宣告 kind={layer.kind.value}，但放在 {kind.value} 目錄"
                )
            layers.append(layer)
        return layers

    def get(self, kind: ProfileKind, layer_id: str) -> PromptProfileLayer | None:
        for layer in self.list_layers(kind):
            if layer.id == layer_id:
                return layer
        return None

    def find_checkpoint_profile_for_filename(
        self, filename: str
    ) -> PromptProfileLayer | None:
        """Match a checkpoint file to its declared profile (exact filename)."""
        for layer in self.list_layers(ProfileKind.CHECKPOINT):
            if layer.checkpoint_filename == filename:
                return layer
        return None
