import pytest

from src.groq_free_benchmark import (
    BENCHMARK_INPUT,
    BENCHMARK_INSTRUCTIONS,
    EXPECTED,
    build_groq_free_benchmark_client,
    run_groq_free_benchmark,
)
from src.groq_free_provider import (
    GROQ_FREE_SERVICE_TIER,
    GROQ_GPT_OSS_20B,
    GroqFreeOnlyStop,
)


class Headers(dict):
    pass


class FakeRawResponse:
    def __init__(self, *, remaining="50"):
        self.headers = Headers(
            {
                "x-ratelimit-limit-requests": "1000",
                "x-ratelimit-limit-tokens": "8000",
                "x-ratelimit-remaining-requests": remaining,
                "x-ratelimit-remaining-tokens": "7900",
                "x-ratelimit-reset-requests": "1h",
                "x-ratelimit-reset-tokens": "1s",
            }
        )

    def parse(self):
        return {
            "output_text": __import__("json").dumps(EXPECTED, separators=(",", ":")),
            "metadata": {
                "prompt_time": "0.010",
                "queue_time": "0.002",
                "completion_time": "0.100",
                "total_time": "0.110",
            },
            "usage": {
                "input_tokens": 100,
                "output_tokens": 20,
                "total_tokens": 120,
            },
        }


class FakeCreate:
    def __init__(self, *, remaining="50", status_code=None):
        self.calls = []
        self.remaining = remaining
        self.status_code = status_code

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.status_code is not None:
            exc = RuntimeError("provider failure")
            exc.status_code = self.status_code
            raise exc
        return FakeRawResponse(remaining=self.remaining)


class FakeRawResponses:
    def __init__(self, create):
        self.with_raw_response = create


class FakeClient:
    def __init__(self, *, remaining="50", status_code=None):
        self.create = FakeCreate(remaining=remaining, status_code=status_code)
        self.responses = FakeRawResponses(self.create)


def test_benchmark_uses_same_input_and_only_varies_reasoning_effort():
    client = FakeClient()

    results = run_groq_free_benchmark(client)

    assert [item["effort"] for item in results] == ["low", "medium", "high"]
    assert all(item["quality_pass"] is True for item in results)
    assert all(item["model"] == GROQ_GPT_OSS_20B for item in results)
    assert all(item["service_tier"] == GROQ_FREE_SERVICE_TIER for item in results)
    assert len(client.create.calls) == 3

    for call, effort in zip(client.create.calls, ("low", "medium", "high")):
        assert call["input"] == BENCHMARK_INPUT
        assert call["instructions"] == BENCHMARK_INSTRUCTIONS
        assert call["model"] == GROQ_GPT_OSS_20B
        assert call["reasoning"] == {"effort": effort}
        assert call["service_tier"] == "on_demand"
        assert call["tool_choice"] == "none"
        assert "store" not in call
        assert "previous_response_id" not in call
        assert "prompt_cache_key" not in call


def test_benchmark_captures_provider_metrics_and_rate_limit_headers():
    result = run_groq_free_benchmark(FakeClient())[0]

    assert result["provider_metrics"]["prompt_time"] == "0.010"
    assert result["provider_metrics"]["queue_time"] == "0.002"
    assert result["provider_metrics"]["completion_time"] == "0.100"
    assert result["provider_metrics"]["total_time"] == "0.110"
    assert result["provider_metrics"]["output_tokens"] == 20
    assert result["rate_limits"]["x-ratelimit-remaining-requests"] == "50"


def test_insufficient_free_remaining_requests_stops_before_next_effort():
    client = FakeClient(remaining="1")

    with pytest.raises(
        GroqFreeOnlyStop,
        match="GROQ_FREE_REMAINING_REQUESTS_INSUFFICIENT_STOP",
    ):
        run_groq_free_benchmark(client)

    assert len(client.create.calls) == 1


@pytest.mark.parametrize(
    ("status_code", "message"),
    [
        (429, "GROQ_FREE_QUOTA_EXHAUSTED_STOP"),
        (402, "GROQ_FREE_PAYMENT_REQUIRED_STOP"),
    ],
)
def test_provider_quota_or_payment_condition_stops_without_fallback(
    status_code,
    message,
):
    client = FakeClient(status_code=status_code)

    with pytest.raises(GroqFreeOnlyStop, match=message):
        run_groq_free_benchmark(client)

    assert len(client.create.calls) == 1


def test_missing_key_blocks_live_client_construction():
    with pytest.raises(ValueError, match="GROQ_API_KEY_REQUIRED"):
        build_groq_free_benchmark_client("")
