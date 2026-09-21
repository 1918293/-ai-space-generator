import pytest

from src.control_gateway import PreModelContextReceipt, VerifiedModelInput
from src.execution_control import Mode
from src.responses_model_boundary import ResponsesModelBoundary


class CapturingResponses:
    def __init__(self):
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return {"ok": True, "request": kwargs}


class FakeClient:
    def __init__(self):
        self.responses = CapturingResponses()


def model_input():
    receipt = PreModelContextReceipt(
        checkpoint_id="R1",
        mode=Mode.EXP,
        task="reasoning-effort benchmark",
        operational_version=1,
        authority_refs=("CURRENT:BENCHMARK",),
        existing_work_refs=("PR17:MERGED",),
        prior_attempt_refs=(),
        regression_refs=("REG:R051",),
        reuse_disposition="REUSE",
        context_fingerprint="sha256:benchmark",
    )
    return VerifiedModelInput(receipt=receipt, user_text="Auto > benchmark")


def test_default_request_shape_remains_backward_compatible():
    client = FakeClient()
    boundary = ResponsesModelBoundary(client, model="gpt-test")

    result = boundary.invoke(model_input())

    assert result["ok"] is True
    assert len(client.responses.calls) == 1
    request = client.responses.calls[0]
    assert request["model"] == "gpt-test"
    assert request["input"] == "Auto > benchmark"
    assert request["store"] is False
    assert request["tool_choice"] == "none"
    assert request["max_output_tokens"] == 256
    assert request["reasoning"] == {"context": "current_turn"}
    assert "text" not in request


@pytest.mark.parametrize("effort", ["none", "low", "medium"])
def test_reasoning_profiles_change_only_the_declared_effort(effort):
    client = FakeClient()
    boundary = ResponsesModelBoundary(
        client,
        model="gpt-test",
        reasoning_effort=effort,
        text_verbosity="low",
    )

    boundary.invoke(model_input())
    request = client.responses.calls[0]

    assert request["reasoning"] == {
        "context": "current_turn",
        "effort": effort,
    }
    assert request["text"] == {"verbosity": "low"}
    assert request["input"] == "Auto > benchmark"
    assert request["tool_choice"] == "none"
    assert request["store"] is False


@pytest.mark.parametrize("effort", ["", "minimal", "high"])
def test_out_of_scope_reasoning_effort_fails_closed(effort):
    with pytest.raises(ValueError, match="RESPONSES_REASONING_EFFORT_INVALID"):
        ResponsesModelBoundary(
            FakeClient(),
            model="gpt-test",
            reasoning_effort=effort,
        )


@pytest.mark.parametrize("verbosity", ["", "quiet", "max"])
def test_invalid_text_verbosity_fails_closed(verbosity):
    with pytest.raises(ValueError, match="RESPONSES_TEXT_VERBOSITY_INVALID"):
        ResponsesModelBoundary(
            FakeClient(),
            model="gpt-test",
            text_verbosity=verbosity,
        )
