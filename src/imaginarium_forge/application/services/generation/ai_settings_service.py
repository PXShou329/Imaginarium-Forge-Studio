"""Commands and queries for non-secret global AI runtime preferences."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from pydantic import ValidationError

from imaginarium_forge.application.errors import ConflictError, ValidationFailedError
from imaginarium_forge.application.services.base import ServiceBase, SessionProvider
from imaginarium_forge.application.services.ollama_model_service import (
    OllamaModelInventory,
)
from imaginarium_forge.domain.common.ids import utc_now_iso
from imaginarium_forge.domain.generation.preferences import (
    AIProviderPreference,
    AIRuntimePreferences,
    AIRuntimePreferenceUpdate,
)
from imaginarium_forge.infrastructure.db.repositories.ai_preferences import (
    AIRuntimePreferenceRepository,
)


class _InstalledOllamaModelCatalog(Protocol):
    """Narrow catalog port used by the atomic selection command."""

    def list_installed(self) -> OllamaModelInventory: ...


@dataclass(frozen=True, slots=True)
class SelectActiveOllamaModelCommand:
    """Select one installed model against an observed preferences version."""

    model_id: str
    expected_preferences_version: str


def _next_updated_at(base_updated_at: str) -> str:
    candidate = utc_now_iso()
    try:
        base = datetime.fromisoformat(base_updated_at.replace("Z", "+00:00"))
        current = datetime.fromisoformat(candidate)
    except ValueError:
        return candidate if candidate != base_updated_at else f"{base_updated_at}:next"
    return (base + timedelta(microseconds=1)).isoformat() if current <= base else candidate


def _validation_failure(exc: ValidationError) -> ValidationFailedError:
    errors = exc.errors()
    message = str(errors[0].get("msg", exc)) if errors else str(exc)
    return ValidationFailedError(message)


class AIRuntimePreferenceService(ServiceBase):
    """Own the one global, non-secret settings row.

    Credential resolution and provider routing are intentionally not part of
    this data slice.
    """

    def get_preferences(self) -> AIRuntimePreferences:
        current = self._read_only(
            lambda session: AIRuntimePreferenceRepository(session).get_global()
        )
        if current is not None:
            return current

        # Defensive parity for metadata-created/test databases. Migration
        # 0014 normally inserts this row, but the service still restores its
        # invariant without involving any credential store.
        defaults = AIRuntimePreferences()
        with self._transaction() as session:
            repository = AIRuntimePreferenceRepository(session)
            current = repository.get_global()
            if current is None:
                repository.add(defaults)
                return defaults
            return current

    def save_preferences(
        self,
        values: AIRuntimePreferenceUpdate | Mapping[str, object],
        *,
        expected_updated_at: str | None = None,
    ) -> AIRuntimePreferences:
        """Replace all editable values with optimistic-concurrency protection."""

        try:
            update_values = (
                values
                if isinstance(values, AIRuntimePreferenceUpdate)
                else AIRuntimePreferenceUpdate.model_validate(values)
            )
        except ValidationError as exc:
            raise _validation_failure(exc) from exc

        current = self.get_preferences()
        if expected_updated_at is not None and expected_updated_at != current.updated_at:
            raise ConflictError("AI 設定已被另一個工作階段更新，未覆寫較新的設定")

        try:
            revised = AIRuntimePreferences.model_validate(
                {
                    **update_values.model_dump(mode="python"),
                    "id": current.id,
                    "created_at": current.created_at,
                    "updated_at": _next_updated_at(current.updated_at),
                }
            )
        except ValidationError as exc:  # defensive: update model already validates values
            raise _validation_failure(exc) from exc

        with self._transaction() as session:
            if not AIRuntimePreferenceRepository(session).update_global_cas(
                revised,
                expected_updated_at=current.updated_at,
            ):
                raise ConflictError("AI 設定已被更新，未覆寫較新的設定")
        return self.get_preferences()


class OllamaModelSelectionService(ServiceBase):
    """Atomically switch both the Ollama model and active provider.

    The catalog lookup is explicit and may contact only the configured local
    Ollama endpoint. It never generates text. The database command remains a
    single compare-and-swap, so a stale settings page cannot update one field
    without the other.
    """

    def __init__(
        self,
        session_factory: SessionProvider,
        catalog: _InstalledOllamaModelCatalog,
    ) -> None:
        super().__init__(session_factory)
        self._catalog = catalog

    def select_active(
        self,
        command: SelectActiveOllamaModelCommand,
    ) -> AIRuntimePreferences:
        inventory = self._catalog.list_installed()
        installed = inventory.matching(command.model_id)
        model_name = "" if installed is None else installed.name.strip()
        if not model_name:
            raise ValidationFailedError("所選 Ollama 模型未安裝；請先明確刷新已安裝模型清單")

        with self._transaction() as session:
            repository = AIRuntimePreferenceRepository(session)
            current = repository.get_global()
            if current is None:
                raise ConflictError("AI 設定尚未初始化，未切換 Ollama 模型")
            if command.expected_preferences_version != current.updated_at:
                raise ConflictError("AI 設定已被另一個工作階段更新，未切換 Ollama 模型")

            values = current.as_update().model_copy(
                update={
                    "default_provider": AIProviderPreference.OLLAMA,
                    "ollama_model": model_name,
                }
            )
            try:
                revised = AIRuntimePreferences.model_validate(
                    {
                        **values.model_dump(mode="python"),
                        "id": current.id,
                        "created_at": current.created_at,
                        "updated_at": _next_updated_at(current.updated_at),
                    }
                )
            except ValidationError as exc:
                raise _validation_failure(exc) from exc
            if not repository.update_global_cas(
                revised,
                expected_updated_at=current.updated_at,
            ):
                raise ConflictError("AI 設定已被更新，未切換 Ollama 模型")
            return revised


__all__ = [
    "AIRuntimePreferenceService",
    "OllamaModelSelectionService",
    "SelectActiveOllamaModelCommand",
]
