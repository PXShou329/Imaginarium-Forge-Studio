"""Deterministic request/action/task/global provider routing."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from imaginarium_forge.application.services.generation.contracts import (
    FallbackMode,
    GenerationErrorCategory,
    GenerationInvocation,
    GenerationRoute,
    GenerationRoutingTable,
    GenerationTask,
    ProviderCapability,
    ProviderRoute,
    RouteSource,
)
from imaginarium_forge.application.services.generation.provider_registry import (
    ProviderRegistry,
    UnknownProviderError,
)


class RouteResolutionError(RuntimeError):
    """A route is missing, unregistered, or lacks a required capability."""


class RouteResolution(BaseModel):
    """Serializable route selected for one invocation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    primary: ProviderRoute
    source: RouteSource
    fallback_mode: FallbackMode
    fallback: ProviderRoute | None = None
    fallback_on: frozenset[GenerationErrorCategory] = Field(default_factory=frozenset)
    required_capabilities: frozenset[ProviderCapability] = Field(default_factory=frozenset)


class ProviderRouter:
    """Resolve explicit override > action > task > global route."""

    def __init__(
        self,
        registry: ProviderRegistry,
        routing_table: GenerationRoutingTable,
    ) -> None:
        self._registry = registry
        self._routing_table = routing_table

    def resolve(
        self,
        invocation: GenerationInvocation,
        *,
        additional_capabilities: frozenset[ProviderCapability] = frozenset(),
    ) -> RouteResolution:
        route, source = self._select_route(invocation)
        if (
            invocation.task is GenerationTask.FINAL_WRITING
            and route.fallback_mode is not FallbackMode.FAIL_CLOSED
        ):
            raise RouteResolutionError("final writing routes must fail closed")
        required = invocation.required_capabilities | additional_capabilities
        self._require_capabilities(route.primary, required)
        if route.fallback_mode is FallbackMode.PROVIDER:
            if route.fallback is None:  # Defensive against unvalidated construction.
                raise RouteResolutionError("provider fallback route is missing")
            self._require_capabilities(route.fallback, required)
        return RouteResolution(
            primary=route.primary,
            source=source,
            fallback_mode=route.fallback_mode,
            fallback=route.fallback,
            fallback_on=route.fallback_on,
            required_capabilities=required,
        )

    def _select_route(
        self,
        invocation: GenerationInvocation,
    ) -> tuple[GenerationRoute, RouteSource]:
        if invocation.route_override is not None:
            return invocation.route_override, RouteSource.REQUEST_OVERRIDE
        for action_rule in self._routing_table.action_routes:
            if (
                action_rule.task is invocation.task
                and action_rule.action_id == invocation.action_id
            ):
                return action_rule.route, RouteSource.ACTION
        for task_rule in self._routing_table.task_routes:
            if task_rule.task is invocation.task:
                return task_rule.route, RouteSource.TASK
        return self._routing_table.global_route, RouteSource.GLOBAL

    def _require_capabilities(
        self,
        route: ProviderRoute,
        required: frozenset[ProviderCapability],
    ) -> None:
        try:
            descriptor = self._registry.descriptor(route.provider_id)
        except UnknownProviderError as exc:
            raise RouteResolutionError(
                f"route selects an unregistered provider: {route.provider_id}"
            ) from exc
        if descriptor.capabilities.supports(required):
            return
        missing = sorted(
            capability.value for capability in required - descriptor.capabilities.supported
        )
        raise RouteResolutionError(
            f"provider {route.provider_id} does not satisfy required capabilities: "
            f"{', '.join(missing)}"
        )


__all__ = ["ProviderRouter", "RouteResolution", "RouteResolutionError"]
