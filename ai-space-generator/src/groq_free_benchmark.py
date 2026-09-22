from __future__ import annotations

import json
import time
from typing import Any, Protocol

from .context_bound_reasoning import AdmittedContextItem, ContextBoundModelInput
from .context_bound_responses import _parse_intent_output, _trusted_runtime_instructions
from .control_gateway import PreModelContextReceipt
from .execution_control import Mode
from .groq_free_provider import (
    GROQ_FREE_SERVICE_TIER,
    GROQ_GPT_OSS_20B,
    GROQ_INFERENCE_METRICS_HEADER,
    GROQ_RESPONSES_BASE_URL,
    GroqFreeOnlyStop,
    GroqReasoningEffort,
    _http_status,
)


BENCHMARK_INSTRUCTIONS = (
    "Return only one compact JSON object. "
    "Keys must be A,B,C,D,E,F and values must be EXECUTE or STOP. "
    "Do not add markdown or explanation."
)
BENCHMARK_INPUT = """Apply this FREE_ONLY rule exactly:
EXECUTE only when zero_cost_verified=true, credential_available=true,
model_available=true, quota_remaining=true, and payment_required=false.
Otherwise STOP. Never use a paid fallback.

A zero_cost_verified=true credential_available=true model_available=true quota_remaining=true payment_required=false
B zero_cost_verified=false credential_available=true model_available=true quota_remaining=true payment_required=false
C zero_cost_verified=true credential_available=false model_available=true quota_remaining=true payment_required=false
D zero_cost_verified=true credential_available=true model_available=false quota_remaining=true payment_required=false
E zero_cost_verified=true credential_available=true model_available=true quota_remaining=false payment_required=false
F zero_cost_verified=true credential_available=true model_available=true quota_remaining=true payment_required=true
"""
EXPECTED = {
    "A": "EXECUTE",
    "B": "STOP",
    "C": "STOP",
    "D": "STOP",
    "E": "STOP",
    "F": "STOP",
}
RATE_LIMIT_HEADERS = (
    "x-ratelimit-limit-requests",
    "x-ratelimit-limit-tokens",
    "x-ratelimit-remaining-requests",
    "x-ratelimit-remaining-tokens",
    "x-ratelimit-reset-requests",
    "x-ratelimit-reset-tokens",
)


class RawResponse(Protocol):
    headers: object

    def parse(self) -> object: ...


class RawResponsesCreate(Protocol):
    def create(self, **kwargs: object) -> RawResponse: ...


class RawResponsesAPI(Protocol):
    with_raw_response: RawResponsesCreate


class RawResponsesClient(Protocol):
    responses: RawResponsesAPI


def _as_dict(value: object) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump()
        return dumped if isinstance(dumped, dict) else {}
    return {}


def _extract_output_text(parsed: object) -> str:
    direct = getattr(parsed, "output_text", None)
    if isinstance(direct, str) and direct.strip():
        return direct.strip()

    data = _as_dict(parsed)
    direct = data.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()

    chunks: list[str] = []
    for item in data.get("output", []) or []:
        if not isinstance(item, dict):
            continue
        for part in item.get("content", []) or []:
            if not isinstance(part, dict):
                continue
            if part.get("type") in {"output_text", "text"} and isinstance(part.get("text"), str):
                chunks.append(part["text"])
    return "".join(chunks).strip()


def _quality_pass(parsed: object) -> bool:
    text = _extract_output_text(parsed)
    try:
        answer = json.loads(text)
    except (TypeError, json.JSONDecodeError):
        return False
    return answer == EXPECTED


def _safe_rate_limit_headers(headers: object) -> dict[str, str]:
    get = getattr(headers, "get", None)
    if not callable(get):
        return {}
    result: dict[str, str] = {}
    for name in RATE_LIMIT_HEADERS:
        value = get(name)
        if value is not None:
            result[name] = str(value)
    return result


def _provider_metrics(parsed: object) -> dict[str, object]:
    data = _as_dict(parsed)
    metadata = data.get("metadata")
    usage = data.get("usage")
    result: dict[str, object] = {}
    if isinstance(metadata, dict):
        for key in ("prompt_time", "queue_time", "completion_time", "total_time"):
            if key in metadata:
                result[key] = metadata[key]
    if isinstance(usage, dict):
        for key in ("input_tokens", "output_tokens", "total_tokens"):
            if key in usage:
                result[key] = usage[key]
    return result


def _remaining_requests(headers: dict[str, str]) -> int | None:
    value = headers.get("x-ratelimit-remaining-requests")
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def run_groq_free_benchmark(
    client: RawResponsesClient,
) -> list[dict[str, object]]:
    """Run one identical request for low/medium/high and fail closed."""

    efforts = (
        GroqReasoningEffort.LOW,
        GroqReasoningEffort.MEDIUM,
        GroqReasoningEffort.HIGH,
    )
    results: list[dict[str, object]] = []

    for index, effort in enumerate(efforts):
        started = time.perf_counter()
        try:
            raw = client.responses.with_raw_response.create(
                model=GROQ_GPT_OSS_20B,
                instructions=BENCHMARK_INSTRUCTIONS,
                input=BENCHMARK_INPUT,
                tool_choice="none",
                max_output_tokens=1024,
                reasoning={"effort": effort.value},
                service_tier=GROQ_FREE_SERVICE_TIER,
                extra_headers=GROQ_INFERENCE_METRICS_HEADER,
            )
            parsed = raw.parse()
        except Exception as exc:
            status = _http_status(exc)
            if status == 429:
                raise GroqFreeOnlyStop("GROQ_FREE_QUOTA_EXHAUSTED_STOP") from exc
            if status == 402:
                raise GroqFreeOnlyStop("GROQ_FREE_PAYMENT_REQUIRED_STOP") from exc
            raise

        wall_ms = round((time.perf_counter() - started) * 1000.0, 3)
        headers = _safe_rate_limit_headers(raw.headers)
        results.append(
            {
                "effort": effort.value,
                "model": GROQ_GPT_OSS_20B,
                "service_tier": GROQ_FREE_SERVICE_TIER,
                "wall_ms": wall_ms,
                "quality_pass": _quality_pass(parsed),
                "provider_metrics": _provider_metrics(parsed),
                "rate_limits": headers,
            }
        )

        remaining_calls = len(efforts) - index - 1
        remaining_requests = _remaining_requests(headers)
        if remaining_calls and remaining_requests is not None and remaining_requests < remaining_calls:
            raise GroqFreeOnlyStop("GROQ_FREE_REMAINING_REQUESTS_INSUFFICIENT_STOP")

    return results


RUNTIME_CURRENT_REF = "CURRENT:ACTION_ADMISSION_BINDING"
RUNTIME_EXISTING_REF = "PR17:CURRENT"
RUNTIME_REGRESSION_REF = "REG:R051-NON-BYPASSABLE"
RUNTIME_EXPECTED_CAPABILITY = "formal_persistence"
RUNTIME_EXPECTED_BINDING = "formal.persist"
RUNTIME_BENCHMARK_USER_TEXT = (
    "Auto > prepare a bounded formal persistence delta using the existing Google Drive "
    "write gateway. Preserve the current EXP Mode and TASK. Do not bypass Runtime-owned "
    "admission or create a parallel authority."
)


def _runtime_benchmark_model_input() -> ContextBoundModelInput:
    receipt = PreModelContextReceipt(
        checkpoint_id="R1",
        mode=Mode.EXP,
        task="Hao System｜Runtime v2 deployed field consumer",
        operational_version=1,
        authority_refs=(RUNTIME_CURRENT_REF,),
        existing_work_refs=(RUNTIME_EXISTING_REF,),
        prior_attempt_refs=(),
        regression_refs=(RUNTIME_REGRESSION_REF,),
        reuse_disposition="REUSE",
        context_fingerprint="sha256:groq-runtime-intent-benchmark",
    )
    return ContextBoundModelInput(
        receipt=receipt,
        admitted_context=(
            AdmittedContextItem(
                ref=RUNTIME_CURRENT_REF,
                kind="CURRENT_CONTROL",
                summary=(
                    "Formal mutation must re-enter ACTION_ADMISSION_BINDING and the "
                    "existing Single Write Gateway; provider/model output is not Authority."
                ),
                source_version="06_Config:CURRENT",
                project_scope="HAO_SYSTEM",
                disposition="APPLY",
            ),
            AdmittedContextItem(
                ref=RUNTIME_EXISTING_REF,
                kind="EXISTING_WORK",
                summary=(
                    "Reuse the existing Runtime v2 context-bound reasoning and control "
                    "plane; do not create a parallel policy engine or execution authority."
                ),
                source_version="PR17:CURRENT",
                project_scope="HAO_SYSTEM",
                disposition="REUSE",
            ),
            AdmittedContextItem(
                ref=RUNTIME_REGRESSION_REF,
                kind="REGRESSION",
                summary=(
                    "Caller-supplied Mode, TASK, Authority, model intent and binding "
                    "metadata must not bypass Runtime-owned admission."
                ),
                source_version="R051-current",
                project_scope="HAO_SYSTEM",
                disposition="APPLY",
            ),
        ),
        semantic_fingerprint="sha256:groq-runtime-intent-benchmark",
        user_text=RUNTIME_BENCHMARK_USER_TEXT,
    )


def run_groq_free_runtime_intent_benchmark(
    client: RawResponsesClient,
) -> list[dict[str, object]]:
    """Exercise the current trusted prompt + parser seam with the real provider."""

    model_input = _runtime_benchmark_model_input()
    efforts = (
        GroqReasoningEffort.LOW,
        GroqReasoningEffort.MEDIUM,
        GroqReasoningEffort.HIGH,
    )
    results: list[dict[str, object]] = []

    for index, effort in enumerate(efforts):
        started = time.perf_counter()
        try:
            raw = client.responses.with_raw_response.create(
                model=GROQ_GPT_OSS_20B,
                instructions=_trusted_runtime_instructions(model_input),
                input=model_input.user_text,
                tool_choice="none",
                max_output_tokens=1024,
                reasoning={"effort": effort.value},
                service_tier=GROQ_FREE_SERVICE_TIER,
                extra_headers=GROQ_INFERENCE_METRICS_HEADER,
            )
            parsed = raw.parse()
        except Exception as exc:
            status = _http_status(exc)
            if status == 429:
                raise GroqFreeOnlyStop("GROQ_FREE_QUOTA_EXHAUSTED_STOP") from exc
            if status == 402:
                raise GroqFreeOnlyStop("GROQ_FREE_PAYMENT_REQUIRED_STOP") from exc
            raise

        wall_ms = round((time.perf_counter() - started) * 1000.0, 3)
        headers = _safe_rate_limit_headers(raw.headers)
        quality_pass = False
        capability = ""
        binding_id = ""
        reported_refs: list[str] = []
        parse_error = ""
        try:
            intent = _parse_intent_output(model_input, _extract_output_text(parsed))
            capability = intent.requested_capability
            binding_id = intent.binding_id
            reported_refs = list(intent.model_reported_used_refs)
            quality_pass = (
                capability == RUNTIME_EXPECTED_CAPABILITY
                and binding_id == RUNTIME_EXPECTED_BINDING
                and bool(reported_refs)
            )
        except ValueError as exc:
            parse_error = str(exc)

        results.append(
            {
                "effort": effort.value,
                "model": GROQ_GPT_OSS_20B,
                "service_tier": GROQ_FREE_SERVICE_TIER,
                "wall_ms": wall_ms,
                "quality_pass": quality_pass,
                "requested_capability": capability,
                "binding_id": binding_id,
                "reported_refs": reported_refs,
                "parse_error": parse_error,
                "provider_metrics": _provider_metrics(parsed),
                "rate_limits": headers,
            }
        )

        remaining_calls = len(efforts) - index - 1
        remaining_requests = _remaining_requests(headers)
        if remaining_calls and remaining_requests is not None and remaining_requests < remaining_calls:
            raise GroqFreeOnlyStop("GROQ_FREE_REMAINING_REQUESTS_INSUFFICIENT_STOP")

    return results


def build_groq_free_benchmark_client(api_key: str) -> RawResponsesClient:
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
