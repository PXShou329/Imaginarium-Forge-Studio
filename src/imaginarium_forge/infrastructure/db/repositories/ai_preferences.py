"""Persistence adapter for the single non-secret AI runtime preference row."""

from __future__ import annotations

import json
from typing import Any, cast

from sqlalchemy import update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from imaginarium_forge.canonical import canonical_json
from imaginarium_forge.domain.generation.preferences import (
    GLOBAL_AI_RUNTIME_PREFERENCES_ID,
    AIRuntimePreferences,
)
from imaginarium_forge.infrastructure.db.models.orm import AIRuntimePreferenceRow


def _task_routes_json(preferences: AIRuntimePreferences) -> str:
    routes = preferences.model_dump(mode="json", exclude_none=True)["task_routes"]
    return canonical_json(routes)


def _to_domain(row: AIRuntimePreferenceRow) -> AIRuntimePreferences:
    routes = json.loads(row.task_routes_json)
    if not isinstance(routes, dict):
        raise ValueError("task_routes_json 必須是 JSON object")
    return AIRuntimePreferences.model_validate(
        {
            "id": row.id,
            "default_provider": row.default_provider,
            "openai_model": row.openai_model,
            "ollama_model": row.ollama_model,
            "default_quality_profile": row.default_quality_profile,
            "default_language": row.default_language,
            "fallback_policy": row.fallback_policy,
            "remote_provider_enabled": bool(row.remote_provider_enabled),
            "remote_ack_version": row.remote_ack_version,
            "streaming_enabled": bool(row.streaming_enabled),
            "auto_continue": bool(row.auto_continue),
            "show_token_usage": bool(row.show_token_usage),
            "task_routes": routes,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }
    )


def _row_values(preferences: AIRuntimePreferences) -> dict[str, object]:
    return {
        "default_provider": preferences.default_provider.value,
        "openai_model": preferences.openai_model,
        "ollama_model": preferences.ollama_model,
        "default_quality_profile": preferences.default_quality_profile.value,
        "default_language": preferences.default_language,
        "fallback_policy": preferences.fallback_policy.value,
        "remote_provider_enabled": int(preferences.remote_provider_enabled),
        "remote_ack_version": preferences.remote_ack_version,
        "streaming_enabled": int(preferences.streaming_enabled),
        "auto_continue": int(preferences.auto_continue),
        "show_token_usage": int(preferences.show_token_usage),
        "task_routes_json": _task_routes_json(preferences),
        "updated_at": preferences.updated_at,
    }


class AIRuntimePreferenceRepository:
    """Read/write the fixed ``global`` row; credentials are outside this adapter."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_global(self) -> AIRuntimePreferences | None:
        row = self._session.get(AIRuntimePreferenceRow, GLOBAL_AI_RUNTIME_PREFERENCES_ID)
        return _to_domain(row) if row is not None else None

    def add(self, preferences: AIRuntimePreferences) -> None:
        self._session.add(
            AIRuntimePreferenceRow(
                id=preferences.id,
                created_at=preferences.created_at,
                **_row_values(preferences),
            )
        )

    def update_global_cas(
        self,
        preferences: AIRuntimePreferences,
        *,
        expected_updated_at: str,
    ) -> bool:
        result = cast(
            CursorResult[Any],
            self._session.execute(
                update(AIRuntimePreferenceRow)
                .where(
                    AIRuntimePreferenceRow.id == GLOBAL_AI_RUNTIME_PREFERENCES_ID,
                    AIRuntimePreferenceRow.updated_at == expected_updated_at,
                )
                .values(**_row_values(preferences))
            ),
        )
        return bool(result.rowcount == 1)


__all__ = ["AIRuntimePreferenceRepository"]
