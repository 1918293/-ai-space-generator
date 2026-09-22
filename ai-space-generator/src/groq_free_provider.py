from __future__ import annotations

import os
from enum import StrEnum
from typing import Protocol

from .action_catalog import ModelActionIntent
from .context_bound_reasoning import ContextBoundModelInput
from .context_bound_responses import (
    _parse_intent_output,
    _response_output_text,
    _trusted_runtime_instructions as _context_bound_trusted_runtime_instructions,
)
from .control_gateway import VerifiedModelInput
from .responses_model_boundary import (
    _trusted_runtime_instructions as _legacy_trusted_runtime_instructions,
)


GROQ_RESPONSES_BASE_URL = "https://api.groq.com/openai/v1"
GROQ_GPT_OSS_20B = "openai/gpt-oss-20b"
GROQ_INFERENCE_METRICS_HEADER = {"Groq-Beta": "inference-metrics"}
GROQ_FREE_SERVICE_TIER = "on_demand"
GROQ_API_KEY_SECRET_PATH = "/etc/secrets/GROQ_API_KEY"


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


def _raise_free_only_stop(exc: BaseException) -> None:
    status = _http_status(exc)
    if status == 429:
        raise GroqFreeOnlyStop("GROQ_FREE_QUOTA_EXHAUSTED_STOP") from exc
    if status == 402:
        raise GroqFreeOnlyStop("GROQ_FREE_PAYMENT_REQUIRED_STOP") from exc


def _normalized_effort(
    reasoning_effort: GroqReasoningEffort | str,
) -> GroqReasoningEffort:
    try:
        return GroqReasoningEffort(str(reasoning_effort).lower())
    except ValueError as exc:
        raise ValueError("GROQ_REASONING_EFFORT_MUST_BE_LOW_MEDIUM_HIGH") from exc


def _validated_model_and_tokens(model: str, max_output_tokens: int) -> tuple[str, int]:
    normalized_model = model.strip()
    if normalized_model != GROQ_GPT_OSS_20B:
        raise ValueError("GROQ_FREE_BENCHMARK_MODEL_MUST_BE_GPT_OSS_20B")
    if max_output_tokens <= 0:
        raise ValueError("GROQ_MAX_OUTPUT_TOKENS_INVALID")
    return normalized_model, max_output_tokens


def _create_kwargs(
    *,
    model: str,
    instructions: str,
    user_text: str,
    reasoning_effort: GroqReasoningEffort,
    max_output_tokens: int,
) -> dict[str, object]:
    """Groq Responses request surface deliberately excludes unsupported fields."""
    return {
        "model": model,
        "instructions": instructions,
        "input": user_text,
        "tool_choice": "none",
        "max_output_tokens": max_output_tokens,
        "reasoning": {"effort": reasoning_effort.value},
        "service_tier": GROQ_FREE_SERVICE_TIER,
        "extra_headers": GROQ_INFERENCE_METRICS_HEADER,
    }


class GroqFreeResponsesBoundary:
    """Legacy structural-receipt adapter retained for bounded compatibility tests."""

    def __init__(
        self,
        client: ResponsesClient,
        *,
        reasoning_effort: GroqReasoningEffort | str,
        model: str = GROQ_GPT_OSS_20B,
        max_output_tokens: int = 256,
    ) -> None:
        self._effort = _normalized_effort(reasoning_effort)
        self._model, self._max_output_tokens = _validated_model_and_tokens(
            model,
            max_output_tokens,
        )
        self._client = client

    @property
    def reasoning_effort(self) -> GroqReasoningEffort:
        return self._effort

    def invoke(self, model_input: VerifiedModelInput) -> object:
        try:
            return self._client.responses.create(
                **_create_kwargs(
                    model=self._model,
                    instructions=_legacy_trusted_runtime_instructions(model_input.receipt),
                    user_text=model_input.user_text,
                    reasoning_effort=self._effort,
                    max_output_tokens=self._max_output_tokens,
                )
            )
        except Exception as exc:
            _raise_free_only_stop(exc)
            raise


class GroqFreeContextBoundIntentBoundary:
    """Current Runtime first-model seam for Groq Free -> GPT-OSS 20B.

    The provider can only return a non-authoritative ModelActionIntent. Existing
    ContextBoundReasoningIngress and ControlPlaneGateway retain runtime-owned
    admission, trusted binding, Authority and execution semantics.
    """

    def __init__(
        self,
        client: ResponsesClient,
        *,
        reasoning_effort: GroqReasoningEffort | str,
        model: str = GROQ_GPT_OSS_20B,
        max_output_tokens: int = 512,
    ) -> None:
        self._effort = _normalized_effort(reasoning_effort)
        self._model, self._max_output_tokens = _validated_model_and_tokens(
            model,
            max_output_tokens,
        )
        self._client = client

    @property
    def reasoning_effort(self) -> GroqReasoningEffort:
        return self._effort

    def invoke(self, model_input: ContextBoundModelInput) -> ModelActionIntent:
        try:
            response = self._client.responses.create(
                **_create_kwargs(
                    model=self._model,
                    instructions=_context_bound_trusted_runtime_instructions(model_input),
                    user_text=model_input.user_text,
                    reasoning_effort=self._effort,
                    max_output_tokens=self._max_output_tokens,
                )
            )
        except Exception as exc:
            _raise_free_only_stop(exc)
            raise
        return _parse_intent_output(model_input, _response_output_text(response))


def load_groq_api_key() -> tuple[str, str]:
    """Return the Groq key and its non-secret source label.

    Environment variables remain authoritative. Render Secret Files are a
    bounded fallback for deployments where a service-level secret is mounted
    at /etc/secrets/GROQ_API_KEY instead of injected into the process env.
    """

    key = os.environ.get("GROQ_API_KEY", "").strip()
    if key:
        return key, "env"

    try:
        with open(GROQ_API_KEY_SECRET_PATH, encoding="utf-8") as secret_file:
            key = secret_file.read().strip()
    except OSError:
        key = ""

    if key:
        return key, "secret_file"
    return "", "none"


def _build_groq_openai_client(*, api_key: str):
    key = api_key.strip()
    if not key:
        raise ValueError("GROQ_API_KEY_REQUIRED")

    from openai import OpenAI

    return OpenAI(
        api_key=key,
        base_url=GROQ_RESPONSES_BASE_URL,
        max_retries=0,
        timeout=30.0,
    )


def build_groq_free_responses_boundary(
    *,
    api_key: str,
    reasoning_effort: GroqReasoningEffort | str,
    max_output_tokens: int = 256,
) -> GroqFreeResponsesBoundary:
    """Build the legacy structural adapter without exposing the API key."""

    return GroqFreeResponsesBoundary(
        _build_groq_openai_client(api_key=api_key),
        reasoning_effort=reasoning_effort,
        max_output_tokens=max_output_tokens,
    )


def build_groq_free_context_bound_intent_boundary(
    *,
    api_key: str,
    reasoning_effort: GroqReasoningEffort | str,
    max_output_tokens: int = 512,
) -> GroqFreeContextBoundIntentBoundary:
    """Build the current Runtime intent adapter without exposing the API key."""

    return GroqFreeContextBoundIntentBoundary(
        _build_groq_openai_client(api_key=api_key),
        reasoning_effort=reasoning_effort,
        max_output_tokens=max_output_tokens,
    )


def build_groq_free_responses_boundary_from_env(
    *,
    reasoning_effort: GroqReasoningEffort | str,
    max_output_tokens: int = 256,
) -> GroqFreeResponsesBoundary:
    api_key, _ = load_groq_api_key()
    return build_groq_free_responses_boundary(
        api_key=api_key,
        reasoning_effort=reasoning_effort,
        max_output_tokens=max_output_tokens,
    )


def build_groq_free_context_bound_intent_boundary_from_env(
    *,
    reasoning_effort: GroqReasoningEffort | str,
    max_output_tokens: int = 512,
) -> GroqFreeContextBoundIntentBoundary:
    api_key, _ = load_groq_api_key()
    return build_groq_free_context_bound_intent_boundary(
        api_key=api_key,
        reasoning_effort=reasoning_effort,
        max_output_tokens=max_output_tokens,
    )
