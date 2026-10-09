"""Minimal structured incident logging for user-facing failures.

Only the correlation ID, normalized error code, and exception type are logged.
The exception message, traceback, request/response data, prompt, paths, and
provider payload are deliberately excluded.
"""

from __future__ import annotations

import logging
import re

from pydantic import BaseModel, ConfigDict, Field

from imaginarium_forge.application.user_facing_errors import (
    ErrorMappingContext,
    UserFacingError,
    map_user_facing_error,
)
from imaginarium_forge.logging_setup import get_logger


class ErrorIncident(BaseModel):
    """The complete allow-list for one logged product incident."""

    model_config = ConfigDict(frozen=True)

    incident_id: str = Field(pattern=r"^IF-[A-F0-9]{12}$")
    error_code: str = Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")
    exception_type: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")


def record_error_incident(
    error: BaseException,
    *,
    user_error: UserFacingError,
    logger: logging.Logger | None = None,
) -> ErrorIncident:
    """Write a sanitized incident record without consuming the exception."""

    incident = ErrorIncident(
        incident_id=user_error.incident_id,
        error_code=user_error.code,
        exception_type=_safe_exception_type(type(error).__name__),
    )
    target = logger or get_logger("imaginarium_forge.error_incidents")
    target.error(
        "user_facing_error",
        extra={"extra_fields": incident.model_dump(mode="json")},
    )
    return incident


def map_and_record_user_facing_error(
    error: BaseException,
    *,
    context: ErrorMappingContext,
    logger: logging.Logger | None = None,
    incident_id: str | None = None,
) -> UserFacingError:
    """Map and record one failure while returning it to the caller unchanged."""

    user_error = map_user_facing_error(error, context=context, incident_id=incident_id)
    record_error_incident(error, user_error=user_error, logger=logger)
    return user_error


def _safe_exception_type(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_]", "_", str(value or "Exception"))[:128]
    if not normalized or not re.match(r"^[A-Za-z_]", normalized):
        return "Exception"
    return normalized


__all__ = [
    "ErrorIncident",
    "map_and_record_user_facing_error",
    "record_error_incident",
]
