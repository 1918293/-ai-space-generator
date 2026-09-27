import hashlib
import hmac
import json

from src.field_consumer_exp import FieldRuntime, EXPECTED_CODE
from src.mcp_control_bridge import MCPPrincipal, SCOPE_EXECUTE


def runtime(*, stale=False, linear_webhook_secret=""):
    return FieldRuntime.build(
        token="test-field-token",
        expected_subject="hao-field-exp",
        linear_webhook_secret=linear_webhook_secret,
        stale=stale,
    )


def test_authorized_raw_turn_reaches_existing_context_bound_control_plane_without_mutation():
    current = runtime()
    status, body = current.handle(
        {"authorization": "Bearer test-field-token"},
        {"user_text": "Auto > deployed consumer proof"},
    )

    assert status == 200
    assert body["ok"] is True
    assert body["code"] == EXPECTED_CODE
    assert body["action_selected"] is True
    assert body["provider_mutation_performed"] is False
    assert body["decision_id"].startswith("DECISION:")
    assert ":formal.persist" in body["action_id"]
    assert current.model.calls == 1


def test_http_contract_blocks_caller_runtime_and_binding_spoofing_before_model():
    current = runtime()
    for payload in (
        {"user_text": "Auto", "mode": "SYS"},
        {"user_text": "Auto", "task": "forged"},
        {"user_text": "Auto", "binding_id": "formal.persist.legacy"},
        {"user_text": "Auto", "authority_refs": ["forged"]},
    ):
        status, body = current.handle(
            {"authorization": "Bearer test-field-token"},
            payload,
        )
        assert status == 400
        assert body["code"] == "REQUEST_SCHEMA_EXACT_USER_TEXT_ONLY"

    assert current.model.calls == 0


def test_authentication_and_subject_are_fail_closed():
    current = runtime()
    status, body = current.handle({}, {"user_text": "Auto"})
    assert status == 401
    assert body["code"] == "AUTHENTICATION_REQUIRED"
    assert current.model.calls == 0

    try:
        current.ingress.prepare_user_turn(
            MCPPrincipal("wrong-subject", frozenset({SCOPE_EXECUTE})),
            user_text="Auto",
        )
    except PermissionError as exc:
        assert str(exc) == "HAO_IDENTITY_REQUIRED"
    else:
        raise AssertionError("wrong subject must fail closed")

    assert current.model.calls == 0


def test_stale_admitted_semantics_block_before_model_and_control_plane_selection():
    current = runtime(stale=True)
    view = current.ingress.prepare_user_turn(
        MCPPrincipal("hao-field-exp", frozenset({SCOPE_EXECUTE})),
        user_text="Auto > stale semantic proof",
    )

    assert view.code == "PRE_MODEL_SEMANTIC_STALE"
    assert view.action_selected is False
    assert view.decision_id == ""
    assert view.action_id == ""
    assert current.model.calls == 0



def linear_event(*, timestamp=1_700_000_000_000, event_type="Issue", action="update"):
    payload = {
        "action": action,
        "type": event_type,
        "webhookTimestamp": timestamp,
        "data": {"id": "2e6ea5c1-40a1-4c00-9a98-4f511d9fcbde"},
        "updatedFrom": {"priority": 0},
    }
    return json.dumps(payload, separators=(",", ":")).encode("utf-8")


def linear_headers(raw_body, *, secret="test-linear-secret", event_type="Issue"):
    signature = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return {
        "linear-signature": signature,
        "linear-delivery": "234d1a4e-b617-4388-90fe-adc3633d6b72",
        "linear-event": event_type,
    }


def test_linear_webhook_shadow_verifies_signature_and_stops_before_model_or_mutation():
    current = runtime(linear_webhook_secret="test-linear-secret")
    now_ms = 1_700_000_000_000
    raw_body = linear_event(timestamp=now_ms)

    status, body = current.handle_linear_webhook(
        linear_headers(raw_body),
        raw_body,
        now_ms=now_ms,
    )

    assert status == 200
    assert body["code"] == "LINEAR_WEBHOOK_VERIFIED_SHADOW_ONLY"
    assert body["event_type"] == "Issue"
    assert body["event_action"] == "update"
    assert body["changed_fields"] == ["priority"]
    assert body["action_selected"] is False
    assert body["provider_mutation_performed"] is False
    assert current.model.calls == 0


def test_linear_webhook_shadow_fails_closed_on_bad_signature_stale_event_and_unsupported_type():
    current = runtime(linear_webhook_secret="test-linear-secret")
    now_ms = 1_700_000_000_000

    raw_body = linear_event(timestamp=now_ms)
    bad_headers = linear_headers(raw_body)
    bad_headers["linear-signature"] = "0" * 64
    status, body = current.handle_linear_webhook(bad_headers, raw_body, now_ms=now_ms)
    assert status == 401
    assert body["code"] == "LINEAR_WEBHOOK_SIGNATURE_INVALID"

    stale_body = linear_event(timestamp=now_ms - 60_001)
    status, body = current.handle_linear_webhook(
        linear_headers(stale_body),
        stale_body,
        now_ms=now_ms,
    )
    assert status == 401
    assert body["code"] == "LINEAR_WEBHOOK_TIMESTAMP_STALE"

    other_body = linear_event(timestamp=now_ms, event_type="Project")
    status, body = current.handle_linear_webhook(
        linear_headers(other_body, event_type="Project"),
        other_body,
        now_ms=now_ms,
    )
    assert status == 422
    assert body["code"] == "LINEAR_WEBHOOK_EVENT_NOT_ADMITTED"
    assert current.model.calls == 0


def test_linear_webhook_shadow_requires_explicit_secret_configuration():
    current = runtime()
    raw_body = linear_event()
    status, body = current.handle_linear_webhook({}, raw_body, now_ms=1_700_000_000_000)

    assert status == 503
    assert body["code"] == "LINEAR_WEBHOOK_SECRET_REQUIRED"
    assert current.model.calls == 0
