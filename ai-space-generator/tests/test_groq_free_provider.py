import pytest

from src.control_gateway import PreModelContextReceipt, VerifiedModelInput
from src.execution_control import Mode
from src.groq_free_provider import (
    GROQ_FREE_SERVICE_TIER,
    GROQ_GPT_OSS_20B,
    GROQ_INFERENCE_METRICS_HEADER,
    GroqFreeOnlyStop,
    GroqFreeResponsesBoundary,
    GroqReasoningEffort,
    build_groq_free_responses_boundary,
)


class FakeResponses:
    def __init__(self, *, status_code=None):
        self.calls = []
        self.status_code = status_code

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.status_code is not None:
            exc = RuntimeError("provider failure")
            exc.status_code = self.status_code
            raise exc
        return {"ok": True}


class FakeClient:
    def __init__(self, *, status_code=None):
        self.responses = FakeResponses(status_code=status_code)


def model_input():
    receipt = PreModelContextReceipt(
        checkpoint_id="R1",
        mode=Mode.EXP,
        task="Groq free benchmark",
        operational_version=1,
        authority_refs=("CURRENT:COST_QUOTA_ADMISSION",),
        existing_work_refs=(),
        prior_attempt_refs=(),
        regression_refs=(),
        reuse_disposition="REUSE",
        context_fingerprint="sha256:test",
    )
    return VerifiedModelInput(receipt=receipt, user_text="same benchmark input")


@pytest.mark.parametrize(
    ("effort", "expected"),
    [
        ("low", "low"),
        ("medium", "medium"),
        ("high", "high"),
        (GroqReasoningEffort.LOW, "low"),
    ],
)
def test_only_supported_reasoning_efforts_are_forwarded(effort, expected):
    client = FakeClient()
    boundary = GroqFreeResponsesBoundary(client, reasoning_effort=effort)

    result = boundary.invoke(model_input())

    assert result == {"ok": True}
    request = client.responses.calls[-1]
    assert request["model"] == GROQ_GPT_OSS_20B
    assert request["reasoning"] == {"effort": expected}
    assert request["tool_choice"] == "none"
    assert request["service_tier"] == GROQ_FREE_SERVICE_TIER
    assert request["extra_headers"] == GROQ_INFERENCE_METRICS_HEADER
    assert "store" not in request
    assert "previous_response_id" not in request
    assert "prompt_cache_key" not in request


@pytest.mark.parametrize("effort", ["none", "minimal", "", "LOWER"])
def test_unsupported_reasoning_efforts_fail_before_provider_call(effort):
    client = FakeClient()
    with pytest.raises(
        ValueError,
        match="GROQ_REASONING_EFFORT_MUST_BE_LOW_MEDIUM_HIGH",
    ):
        GroqFreeResponsesBoundary(client, reasoning_effort=effort)
    assert client.responses.calls == []


def test_model_is_locked_to_gpt_oss_20b():
    client = FakeClient()
    with pytest.raises(
        ValueError,
        match="GROQ_FREE_BENCHMARK_MODEL_MUST_BE_GPT_OSS_20B",
    ):
        GroqFreeResponsesBoundary(
            client,
            reasoning_effort="low",
            model="openai/gpt-oss-120b",
        )
    assert client.responses.calls == []


@pytest.mark.parametrize(
    ("status_code", "message"),
    [
        (429, "GROQ_FREE_QUOTA_EXHAUSTED_STOP"),
        (402, "GROQ_FREE_PAYMENT_REQUIRED_STOP"),
    ],
)
def test_free_only_quota_or_payment_condition_stops_without_fallback(
    status_code,
    message,
):
    client = FakeClient(status_code=status_code)
    boundary = GroqFreeResponsesBoundary(client, reasoning_effort="medium")

    with pytest.raises(GroqFreeOnlyStop, match=message):
        boundary.invoke(model_input())

    assert len(client.responses.calls) == 1


def test_missing_api_key_fails_before_client_construction():
    with pytest.raises(ValueError, match="GROQ_API_KEY_REQUIRED"):
        build_groq_free_responses_boundary(
            api_key="",
            reasoning_effort="low",
        )
