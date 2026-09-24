from src.execution_guardrail import evaluate_execution_transition


def test_read_only_action_can_be_admitted_without_mutation_authorization():
    result = evaluate_execution_transition(
        {"name": "search_authority", "externality": "READ_ONLY"}
    )

    assert result["transition_pass"] is True
    assert result["failed_at"] == ""


def test_private_reversible_write_requires_current_scope_authorization():
    result = evaluate_execution_transition(
        {"name": "persist_record", "externality": "PRIVATE_REVERSIBLE_WRITE"}
    )

    assert result["transition_pass"] is False
    assert result["failed_at"] == "ADMIT"
    assert result["issues"][0]["code"] == "ACTION_SCOPE_NOT_AUTHORIZED"


def test_external_or_irreversible_action_requires_explicit_authorization():
    result = evaluate_execution_transition(
        {
            "name": "publish_result",
            "externality": "EXTERNAL_OR_IRREVERSIBLE",
            "scope_authorized": True,
        }
    )

    assert result["transition_pass"] is False
    assert result["failed_at"] == "ADMIT"
    assert result["issues"][0]["code"] == "EXPLICIT_AUTHORIZATION_REQUIRED"


def test_tool_success_cannot_substitute_for_persistence_evidence_floor():
    result = evaluate_execution_transition(
        {
            "name": "persist_record",
            "externality": "PRIVATE_REVERSIBLE_WRITE",
            "scope_authorized": True,
        },
        claim={
            "type": "PERSISTED",
            "evidence": {
                "tool_call_success": True,
                "action_executed": True,
            },
        },
    )

    assert result["transition_pass"] is False
    assert result["failed_at"] == "VERIFY"
    assert result["missing_evidence"] == ["readback_ok", "verification_pass"]


def test_verified_persistence_can_close_when_evidence_floor_is_met():
    result = evaluate_execution_transition(
        {
            "name": "persist_record",
            "externality": "PRIVATE_REVERSIBLE_WRITE",
            "scope_authorized": True,
        },
        claim={
            "type": "PERSISTED",
            "evidence": {
                "action_executed": True,
                "readback_ok": True,
                "verification_pass": True,
            },
        },
    )

    assert result["transition_pass"] is True
    assert result["failed_at"] == ""


def test_executed_claim_fails_at_observe_when_output_was_not_directly_observed():
    result = evaluate_execution_transition(
        {
            "name": "edit_asset",
            "externality": "PRIVATE_REVERSIBLE_WRITE",
            "scope_authorized": True,
        },
        claim={
            "type": "EXECUTED",
            "evidence": {"action_executed": True},
        },
    )

    assert result["transition_pass"] is False
    assert result["failed_at"] == "OBSERVE"
    assert result["missing_evidence"] == ["direct_output_observed"]


def test_acceptance_is_not_implied_by_technical_verification():
    result = evaluate_execution_transition(
        {"name": "present_candidate", "externality": "READ_ONLY"},
        claim={
            "type": "ACCEPTED",
            "evidence": {"verification_pass": True},
        },
    )

    assert result["transition_pass"] is False
    assert result["failed_at"] == "CLOSE"
    assert result["missing_evidence"] == ["explicit_user_acceptance"]


def test_unknown_externality_fails_closed_at_resolve():
    result = evaluate_execution_transition(
        {"name": "mystery_action", "externality": "UNKNOWN"}
    )

    assert result["transition_pass"] is False
    assert result["failed_at"] == "RESOLVE"
    assert result["issues"][0]["code"] == "ACTION_EXTERNALITY_UNRESOLVED"
