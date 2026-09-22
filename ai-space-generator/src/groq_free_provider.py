from __future__ import annotations

import os
from enum import StrEnum
from typing import Protocol

from .control_gateway import VerifiedModelInput
from .responses_model_boundary import _trusted_runtime_instructions


GROQ_RESPONSES_BASE_URL = "https://api.groq.com/openai/v1"
GROQ_GPT_OSS_20B = "openai/gpt-oss-20b"
GROQ_INFERENCE_METRICS_HEADER = {"Groq-Beta": "inference-metrics"}
GROQ_FREE_SERVICE_TIER = "on_demand"


class GroqReasoningEffort(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class GroqFreeOnlyStop(RuntimeError):
    """Typed terminal stop for a FREE_ONLY Groq route.

    This boundary intentionally contains no paid or alternate-provider fallback.
    """


class ResponsesCreateAPI(Protocol):
    def create(self, **kwargs: object) -> object: ...


class ResponsesClient(Protocol):
    responses: ResponsesCreateAPI


def _http_status(exc: BaseException) -> int | None:
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return status
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    return status if isinstance(status, int) else None


class GroqFreeResponsesBoundary:
    """Single-provider Groq Responses adapter for Hao FREE_ONLY experiments."""

    def __init__(
        self,
        client: ResponsesClient,
        *,
        reasoning_effort: GroqReasoningEffort | str,
        model: str = GROQ_GPT_OSS_20B,
        max_output_tokens: int = 256,
    ) -> None:
        try:
            effort = GroqReasoningEffort(str(reasoning_effort).lower())
        except ValueError as exc:
            raise ValueError("GROQ_REASONING_EFFORT_MUST_BE_LOW_MEDIUM_HIGH") from exc
        normalized_model = model.strip()
        if normalized_model != GROQ_GPT_OSS_20B:
            raise ValueError("GROQ_FREE_BENCHMARK_MODEL_MUST_BE_GPT_OSS_20B")
        if max_output_tokens <= 0:
            raise ValueError("GROQ_MAX_OUTPUT_TOKENS_INVALID")
        self._client = client
        self._effort = effort
        self._model = normalized_model
        self._max_output_tokens = max_output_tokens

    @property
    def reasoning_effort(self) -> GroqReasoningEffort:
        return self._effort

    def invoke(self, model_input: VerifiedModelInput) -> object:
        try:
            return self._client.responses.create(
                model=self._model,
                instructions=_trusted_runtime_instructions(model_input.receipt),
                input=model_input.user_text,
                tool_choice="none",
                max_output_tokens=self._max_output_tokens,
                reasoning={"effort": self._effort.value},
                service_tier=GROQ_FREE_SERVICE_TIER,
                extra_headers=GROQ_INFERENCE_METRICS_HEADER,
            )
        except Exception as exc:
            status = _http_status(exc)
            if status == 429:
                raise GroqFreeOnlyStop("GROQ_FREE_QUOTA_EXHAUSTED_STOP") from exc
            if status == 402:
                raise GroqFreeOnlyStop("GROQ_FREE_PAYMENT_REQUIRED_STOP") from exc
            raise


def build_groq_free_responses_boundary(
    *,
    api_key: str,
    reasoning_effort: GroqReasoningEffort | str,
    max_output_tokens: int = 256,
) -> GroqFreeResponsesBoundary:
    """Build the OpenAI-compatible Groq client without exposing the API key."""

    key = api_key.strip()
    if not key:
        raise ValueError("GROQ_API_KEY_REQUIRED")

    from openai import OpenAI

    client = OpenAI(
        api_key=key,
        base_url=GROQ_RESPONSES_BASE_URL,
        max_retries=0,
        timeout=30.0,
    )
    return GroqFreeResponsesBoundary(
        client,
        reasoning_effort=reasoning_effort,
        max_output_tokens=max_output_tokens,
    )


def build_groq_free_responses_boundary_from_env(
    *,
    reasoning_effort: GroqReasoningEffort | str,
    max_output_tokens: int = 256,
) -> GroqFreeResponsesBoundary:
    return build_groq_free_responses_boundary(
        api_key=os.environ.get("GROQ_API_KEY", ""),
        reasoning_effort=reasoning_effort,
        max_output_tokens=max_output_tokens,
    )
