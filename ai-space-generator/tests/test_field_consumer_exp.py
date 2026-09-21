from src.field_consumer_exp import FieldRuntime, EXPECTED_CODE
from src.mcp_control_bridge import MCPPrincipal, SCOPE_EXECUTE


def runtime(*, stale=False):
    return FieldRuntime.build(
        token="test-field-token",
        expected_subject="hao-field-exp",
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
