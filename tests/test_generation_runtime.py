"""Credential boundaries exercised with an in-memory provider and dummy values."""

from __future__ import annotations

import json
import unittest

from pydantic import BaseModel

from imaginarium_forge.application.ports.credentials import CredentialResolver
from imaginarium_forge.application.services.generation.contracts import (
    GenerationErrorCategory,
    GenerationExecutionStatus,
    GenerationInvocation,
    GenerationRoute,
    GenerationRoutingTable,
    GenerationTask,
    ProviderCapabilities,
    ProviderCapability,
    ProviderDescriptor,
    ProviderRoute,
    RetryPolicy,
    TextStreamStatus,
)
from imaginarium_forge.application.services.generation.provider_registry import ProviderRegistry
from imaginarium_forge.application.services.generation.provider_router import ProviderRouter
from imaginarium_forge.application.services.generation.runtime import CreativeGenerationRuntime
from imaginarium_forge.infrastructure.credentials.keyring_store import DisabledCredentialStore
from imaginarium_forge.providers.contracts import GenerationInputItem, GenerationRequest
from imaginarium_forge.providers.errors import ProviderConfigurationError
from imaginarium_forge.providers.mock import MockProvider


class NestedAnswer(BaseModel):
    sections: dict[str, list[str]]


class GenerationRuntimeTests(unittest.TestCase):
    # This deliberately does not resemble a provider-issued credential.
    credential = "dummy-credential-for-unit-test"

    def _runtime(self, provider: MockProvider) -> CreativeGenerationRuntime:
        registry = ProviderRegistry()
        registry.register(
            ProviderDescriptor(
                provider_id="mock",
                display_name="In-memory provider",
                credential_id="unit-test-credential",
                capabilities=ProviderCapabilities(
                    supported=frozenset({
                        ProviderCapability.TEXT_GENERATION,
                        ProviderCapability.STRUCTURED_OUTPUT,
                        ProviderCapability.STREAMING,
                    })
                ),
            ),
            lambda request: provider,
        )
        router = ProviderRouter(
            registry,
            GenerationRoutingTable(
                global_route=GenerationRoute(
                    primary=ProviderRoute(provider_id="mock", model="offline-test")
                )
            ),
        )
        return CreativeGenerationRuntime(
            registry,
            router,
            CredentialResolver(DisabledCredentialStore(), environ={}),
            retry_policy=RetryPolicy(max_attempts=1),
        )

    @staticmethod
    def _invocation(request: GenerationRequest, identifier: str) -> GenerationInvocation:
        return GenerationInvocation(
            task=GenerationTask.GENERAL,
            action_id="test-generation",
            idempotency_key=identifier,
            request=request,
        )

    def test_active_credential_in_author_text_is_blocked_before_dispatch(self) -> None:
        requests = (
            GenerationRequest(model="offline-test", prompt=f"A note: {self.credential}"),
            GenerationRequest(model="offline-test", prompt="A story", system=self.credential),
            GenerationRequest(
                model="offline-test",
                prompt="A story",
                input_items=(GenerationInputItem(role="user", content=self.credential),),
            ),
        )
        for index, request in enumerate(requests):
            with self.subTest(field=index):
                provider = MockProvider(text_responses=["A quiet library."])
                runtime = self._runtime(provider)
                invocation = self._invocation(request, f"request-{index}")
                with self.assertRaises(ProviderConfigurationError):
                    runtime.assert_request_author_content_safe(
                        invocation, session_override=self.credential
                    )
                outcome = runtime.execute_text(invocation, session_override=self.credential)
                self.assertEqual(outcome.status, GenerationExecutionStatus.FAILED)
                self.assertEqual(outcome.failure.category, GenerationErrorCategory.CONFIGURATION)
                self.assertEqual(provider.requests, [])
                self.assertNotIn(self.credential, outcome.model_dump_json())

    def test_text_and_nested_structured_outputs_cannot_return_active_credential(self) -> None:
        request = GenerationRequest(model="offline-test", prompt="Describe a quiet library.")
        provider = MockProvider(text_responses=[f"A note contains {self.credential}."])
        outcome = self._runtime(provider).execute_text(
            self._invocation(request, "text-output"), session_override=self.credential
        )
        self.assertEqual(outcome.status, GenerationExecutionStatus.FAILED)
        self.assertEqual(outcome.failure.category, GenerationErrorCategory.REFUSAL)
        self.assertIsNone(outcome.result)
        self.assertNotIn(self.credential, outcome.model_dump_json())

        provider = MockProvider(
            structured_responses=[json.dumps({"sections": {"notes": [self.credential]}})]
        )
        structured = self._runtime(provider).execute_structured(
            self._invocation(request, "structured-output"),
            NestedAnswer,
            session_override=self.credential,
        )
        self.assertEqual(structured.status, GenerationExecutionStatus.FAILED)
        self.assertEqual(structured.failure.category, GenerationErrorCategory.REFUSAL)
        self.assertIsNone(structured.result)
        self.assertNotIn(self.credential, structured.model_dump_json())

    def test_stream_does_not_publish_credential_split_across_deltas(self) -> None:
        prefix = "The note says "
        provider = MockProvider(text_responses=[prefix + self.credential + "."])
        published: list[str] = []
        outcome = self._runtime(provider).execute_text_stream(
            self._invocation(
                GenerationRequest(model="offline-test", prompt="Describe a quiet library."),
                "stream-output",
            ),
            session_override=self.credential,
            on_delta=published.append,
        )
        self.assertEqual(outcome.status, TextStreamStatus.FAILED)
        self.assertEqual(outcome.failure.category, GenerationErrorCategory.REFUSAL)
        self.assertIsNone(outcome.result)
        self.assertEqual("".join(published), prefix)
        self.assertNotIn(self.credential, outcome.model_dump_json())


if __name__ == "__main__":
    unittest.main()
