import json

import pytest

import src.groq_free_provider as groq_provider

from src.context_bound_reasoning import AdmittedContextItem, ContextBoundModelInput
from src.control_gateway import PreModelContextReceipt, VerifiedModelInput
from src.execution_control import Mode
from src.groq_free_provider import (
    GROQ_FREE_SERVICE_TIER,
    GROQ_GPT_OSS_20B,
    GROQ_INFERENCE_METRICS_HEADER,
    GroqFreeContextBoundIntentBoundary,
    GroqFreeOnlyStop,
    GroqFreeResponsesBoundary,
    GroqReasoningEffort,
    build_groq_free_context_bound_intent_boundary,
    build_groq_free_responses_boundary,
    groq_api_key_secret_file_status,
    load_groq_api_key,
)


class FakeResponses:
    def __init__(self, *, output_text=None, status_code=None):
        self.calls = []
        self.output_text = output_text
        self.status_code = status_code

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.status_code is not None:
            exc = RuntimeError("provider failure")
            exc.status_code = self.status_code
            raise exc
        if self.output_text is not None:
            return {"id": "resp-groq", "output_text": self.output_text}
        return {"ok": True}


class FakeClient:
    def __init__(self, *, output_text=None, status_code=None):
        self.responses = FakeResponses(
            output_text=output_text,
            status_code=status_code,
        )


def receipt():
    return PreModelContextReceipt(
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


def legacy_model_input():
    return VerifiedModelInput(
        receipt=receipt(),
        user_text="same benchmark input",
    )


def context_bound_model_input():
    return ContextBoundModelInput(
        receipt=receipt(),
        admitted_context=(
            AdmittedContextItem(
                ref="CURRENT:COST_QUOTA_ADMISSION",
                kind="CURRENT_CONTROL",
                summary=(
                    "FREE_ONLY must stop on quota exhaustion when no verified "
                    "free fallback exists."
                ),
                source_version="06_Config:R540",
                project_scope="HAO_SYSTEM",
                disposition="APPLY",
            ),
        ),
        semantic_fingerprint="sha256:semantic-test",
        user_text="same benchmark input",
    )


def valid_intent_output():
    return json.dumps(
        {
            "requested_capability": "formal_persistence",
            "binding_id": "formal.persist",
            "expected_state_delta": "bounded formal delta",
            "authorization_target": "",
            "arguments": {},
            "model_reported_used_refs": ["CURRENT:COST_QUOTA_ADMISSION"],
        }
    )


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

    result = boundary.invoke(legacy_model_input())

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
    assert "truncation" not in request
    assert "include" not in request
    assert "prompt" not in request


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
        boundary.invoke(legacy_model_input())

    assert len(client.responses.calls) == 1


def test_missing_api_key_fails_before_client_construction():
    with pytest.raises(ValueError, match="GROQ_API_KEY_REQUIRED"):
        build_groq_free_responses_boundary(
            api_key="",
            reasoning_effort="low",
        )

    with pytest.raises(ValueError, match="GROQ_API_KEY_REQUIRED"):
        build_groq_free_context_bound_intent_boundary(
            api_key="",
            reasoning_effort="low",
        )


@pytest.mark.parametrize("effort", ["low", "medium", "high"])
def test_current_context_bound_runtime_seam_returns_model_action_intent(effort):
    client = FakeClient(output_text=valid_intent_output())
    boundary = GroqFreeContextBoundIntentBoundary(
        client,
        reasoning_effort=effort,
        max_output_tokens=128,
    )

    intent = boundary.invoke(context_bound_model_input())

    assert intent.intent_id.startswith("INTENT:")
    assert intent.requested_capability == "formal_persistence"
    assert intent.binding_id == "formal.persist"
    assert intent.model_reported_used_refs == ("CURRENT:COST_QUOTA_ADMISSION",)

    request = client.responses.calls[-1]
    assert request["model"] == GROQ_GPT_OSS_20B
    assert request["reasoning"] == {"effort": effort}
    assert request["tool_choice"] == "none"
    assert request["max_output_tokens"] == 128
    assert request["service_tier"] == GROQ_FREE_SERVICE_TIER
    assert request["extra_headers"] == GROQ_INFERENCE_METRICS_HEADER
    assert request["input"] == "same benchmark input"
    assert '"ref":"CURRENT:COST_QUOTA_ADMISSION"' in request["instructions"]
    assert "FREE_ONLY must stop on quota exhaustion" in request["instructions"]
    assert "model_reported_used_refs" in request["instructions"]
    assert "store" not in request
    assert "previous_response_id" not in request
    assert "prompt_cache_key" not in request


@pytest.mark.parametrize(
    ("status_code", "message"),
    [
        (429, "GROQ_FREE_QUOTA_EXHAUSTED_STOP"),
        (402, "GROQ_FREE_PAYMENT_REQUIRED_STOP"),
    ],
)
def test_current_context_bound_seam_free_only_stops_without_paid_fallback(
    status_code,
    message,
):
    client = FakeClient(status_code=status_code)
    boundary = GroqFreeContextBoundIntentBoundary(
        client,
        reasoning_effort="high",
    )

    with pytest.raises(GroqFreeOnlyStop, match=message):
        boundary.invoke(context_bound_model_input())

    assert len(client.responses.calls) == 1


def test_current_context_bound_seam_reuses_existing_parser_guards():
    payload = json.loads(valid_intent_output())
    payload["mode"] = "SYS"
    client = FakeClient(output_text=json.dumps(payload))
    boundary = GroqFreeContextBoundIntentBoundary(
        client,
        reasoning_effort="low",
    )

    with pytest.raises(
        ValueError,
        match="RESPONSES_INTENT_RUNTIME_FIELD_OR_UNKNOWN_KEY:mode",
    ):
        boundary.invoke(context_bound_model_input())



def test_load_groq_api_key_prefers_environment(monkeypatch, tmp_path):
    secret_path = tmp_path / "GROQ_API_KEY"
    secret_path.write_text("file-key\n", encoding="utf-8")
    monkeypatch.setattr(groq_provider, "GROQ_API_KEY_SECRET_PATH", str(secret_path))
    monkeypatch.setenv("GROQ_API_KEY", "env-key")

    key, source = load_groq_api_key()

    assert key == "env-key"
    assert source == "env"


def test_load_groq_api_key_falls_back_to_render_secret_file(monkeypatch, tmp_path):
    secret_path = tmp_path / "GROQ_API_KEY"
    secret_path.write_text("file-key\n", encoding="utf-8")
    monkeypatch.setattr(groq_provider, "GROQ_API_KEY_SECRET_PATH", str(secret_path))
    monkeypatch.delenv("GROQ_API_KEY", raising=False)

    key, source = load_groq_api_key()

    assert key == "file-key"
    assert source == "secret_file"


def test_load_groq_api_key_reports_none_when_env_and_secret_file_are_absent(monkeypatch, tmp_path):
    monkeypatch.setattr(
        groq_provider,
        "GROQ_API_KEY_SECRET_PATH",
        str(tmp_path / "missing"),
    )
    monkeypatch.delenv("GROQ_API_KEY", raising=False)

    key, source = load_groq_api_key()

    assert key == ""
    assert source == "none"



def test_groq_secret_file_status_reports_missing(monkeypatch, tmp_path):
    etc_path = tmp_path / "missing-etc"
    root_path = tmp_path / "missing-root"
    monkeypatch.setattr(groq_provider, "GROQ_API_KEY_SECRET_PATH", str(etc_path))
    monkeypatch.setattr(groq_provider, "GROQ_API_KEY_SERVICE_ROOT_PATH", str(root_path))

    status = groq_api_key_secret_file_status()

    assert status == {
        "etc_secrets": {"exists": False, "readable": False, "nonempty": False},
        "service_root": {"exists": False, "readable": False, "nonempty": False},
    }


def test_groq_secret_file_status_reports_empty_and_nonempty(monkeypatch, tmp_path):
    etc_path = tmp_path / "etc-key"
    root_path = tmp_path / "root-key"
    monkeypatch.setattr(groq_provider, "GROQ_API_KEY_SECRET_PATH", str(etc_path))
    monkeypatch.setattr(groq_provider, "GROQ_API_KEY_SERVICE_ROOT_PATH", str(root_path))

    etc_path.write_text("", encoding="utf-8")
    root_path.write_text("file-key\n", encoding="utf-8")
    status = groq_api_key_secret_file_status()

    assert status["etc_secrets"] == {"exists": True, "readable": True, "nonempty": False}
    assert status["service_root"] == {"exists": True, "readable": True, "nonempty": True}



def test_load_groq_api_key_falls_back_to_service_root_secret_file(monkeypatch, tmp_path):
    etc_path = tmp_path / "missing-etc"
    root_path = tmp_path / "GROQ_API_KEY"
    root_path.write_text("root-key\n", encoding="utf-8")
    monkeypatch.setattr(groq_provider, "GROQ_API_KEY_SECRET_PATH", str(etc_path))
    monkeypatch.setattr(groq_provider, "GROQ_API_KEY_SERVICE_ROOT_PATH", str(root_path))
    monkeypatch.delenv("GROQ_API_KEY", raising=False)

    key, source = load_groq_api_key()

    assert key == "root-key"
    assert source == "service_root_secret_file"
