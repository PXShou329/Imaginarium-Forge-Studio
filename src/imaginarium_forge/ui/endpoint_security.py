"""Compatibility import for UI code using the shared endpoint security boundary."""

from imaginarium_forge.config.endpoint_security import (
    EndpointSummary,
    summarize_endpoint,
)

__all__ = ["EndpointSummary", "summarize_endpoint"]
