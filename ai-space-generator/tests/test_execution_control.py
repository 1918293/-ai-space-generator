import pytest

from src.execution_control import (
    ActionArchetype,
    ActionExternality,
    ActionProposal,
    CompletionClaim,
    EvidenceKind,
    EvidenceReceipt,
    ExecutionRecord,
    FailureStage,
    Mode,
    RunPhase,
    add_evidence,
    admit_action,
    can_claim,
    can_retry,
    close_run,
    mark_unsynced,
    record_failure,
    render_header,
    transition,
)


def base_record(**overrides):
    values = {
        "run_id": "RUN-001",
        "task": "Hao System Execution Control 系統性重構",
        "mode": Mode.EXP,
        "goal_valid": True,
        "acceptance_criteria": ("verified outcome",),
        "authority_refs": ("AUTH-HAO",),
    }
    values.update(overrides)
    return ExecutionRecord(**values)


def read_action(**overrides):
    values = {
        "action_id": "ACT-READ-001",
        "archetype": ActionArchetype.READ,
        "externality": ActionExternality.READ_ONLY,
        "capability": "authority_read",
        "provider": "google_drive",
        "action_name": "read",
        "required_authority_refs": ("AUTH-HAO",),
    }
    values.update(overrides)
    return ActionProposal(**values)


def mutate_action(**overrides):
    values = {
        "action_id": "ACT-MUTATE-001",
        "archetype": ActionArchetype.MUTATE,
        "externality": ActionExternality.PRIVATE_REVERSIBLE,
        "capability": "formal_persistence",
        "provider": "google_drive",
        "action_name": "update",
        "expected_state_delta": "append verified record",
        "required_authority_refs": ("AUTH-HAO",),
        "idempotency_key": "RUN-001:ACT-MUTATE-001",
        "rollback_available": True,
    }
    values.update(overrides)
    return ActionProposal(**values)


def receipt(kind, evidence_id=None):
    return EvidenceReceipt(
        evidence_id=evidence_id or f"E-{kind.value}",
        kind=kind,
        passed=True,
        source="direct-test",
    )


def test_read_only_action_is_admitted_without_hao_authorization():
    updated, decision = admit_action(base_record(), read_action())
    assert decision.allowed is True
    assert updated.phase == RunPhase.ADMITTED


def test_invalid_goal_blocks_before_tool_routing():
    updated, decision = admit_action(base_record(goal_valid=False), read_action())
    assert decision.allowed is False
    assert decision.failed_at == FailureStage.INTENT
    assert updated.phase == RunPhase.BLOCKED


def test_missing_authority_blocks_before_execution():
    updated, decision = admit_action(
        base_record(authority_refs=()),
        read_action(required_authority_refs=("AUTH-HAO",)),
    )
    assert decision.allowed is False
    assert decision.failed_at == FailureStage.AUTHORITY
    assert updated.phase == RunPhase.BLOCKED


def test_external_action_requires_explicit_hao_scope():
    action = mutate_action(
        archetype=ActionArchetype.PUBLISH,
        externality=ActionExternality.EXTERNAL_REVERSIBLE,
        authorization_scope="SEND_EXTERNAL_MESSAGE",
    )
    updated, decision = admit_action(base_record(), action)
    assert decision.allowed is False
    assert decision.requires_hao_authorization is True
    assert updated.phase == RunPhase.AWAITING_HAO


def test_external_action_is_admitted_when_exact_scope_is_authorized():
    action = mutate_action(
        archetype=ActionArchetype.PUBLISH,
        externality=ActionExternality.EXTERNAL_REVERSIBLE,
        authorization_scope="SEND_EXTERNAL_MESSAGE",
    )
    updated, decision = admit_action(
        base_record(),
        action,
        hao_authorized_scopes={"SEND_EXTERNAL_MESSAGE"},
    )
    assert decision.allowed is True
    assert updated.phase == RunPhase.ADMITTED


def test_mutation_requires_expected_delta_and_idempotency_key():
    updated, decision = admit_action(
        base_record(),
        mutate_action(expected_state_delta=""),
    )
    assert decision.allowed is False
    assert decision.code == "MISSING_EXPECTED_STATE_DELTA"
    assert updated.phase == RunPhase.BLOCKED

    updated, decision = admit_action(
        base_record(),
        mutate_action(idempotency_key=""),
    )
    assert decision.allowed is False
    assert decision.code == "MISSING_IDEMPOTENCY_KEY"


def test_publish_cannot_claim_read_only_externality():
    updated, decision = admit_action(
        base_record(),
        mutate_action(
            archetype=ActionArchetype.PUBLISH,
            externality=ActionExternality.READ_ONLY,
        ),
    )
    assert decision.allowed is False
    assert decision.failed_at == FailureStage.POLICY


def test_state_machine_does_not_allow_skipping_observation_and_verification():
    admitted, _ = admit_action(base_record(), read_action())
    executing = transition(admitted, RunPhase.EXECUTING)
    with pytest.raises(ValueError, match="ILLEGAL_TRANSITION"):
        transition(executing, RunPhase.VERIFIED)
    observed = transition(executing, RunPhase.OBSERVED)
    verified = transition(observed, RunPhase.VERIFIED)
    assert verified.phase == RunPhase.VERIFIED


def test_tool_receipt_is_enough_only_for_executed_claim():
    record = add_evidence(base_record(), receipt(EvidenceKind.TOOL_RECEIPT))
    assert can_claim(record, CompletionClaim.EXECUTED).allowed is True
    assert can_claim(record, CompletionClaim.PERSISTED).allowed is False
    assert can_claim(record, CompletionClaim.COMPLETED).allowed is False


def test_persistence_requires_write_readback_and_verification_evidence():
    record = base_record()
    record = add_evidence(record, receipt(EvidenceKind.TOOL_RECEIPT))
    record = add_evidence(record, receipt(EvidenceKind.STATE_READBACK))
    assert can_claim(record, CompletionClaim.PERSISTED).allowed is False
    record = add_evidence(record, receipt(EvidenceKind.VERIFICATION_PASS))
    assert can_claim(record, CompletionClaim.PERSISTED).allowed is True


def test_hao_acceptance_is_distinct_from_technical_verification():
    record = add_evidence(base_record(), receipt(EvidenceKind.VERIFICATION_PASS))
    assert can_claim(record, CompletionClaim.ACCEPTED).allowed is False
    record = add_evidence(record, receipt(EvidenceKind.HAO_ACCEPTANCE))
    assert can_claim(record, CompletionClaim.ACCEPTED).allowed is True


def test_read_completion_requires_verification_and_acceptance_gate():
    record = add_evidence(base_record(), receipt(EvidenceKind.VERIFICATION_PASS))
    assert can_claim(record, CompletionClaim.COMPLETED).allowed is False
    record = add_evidence(record, receipt(EvidenceKind.ACCEPTANCE_GATE_PASS))
    assert can_claim(record, CompletionClaim.COMPLETED).allowed is True


def test_mutation_completion_also_requires_tool_receipt_and_state_readback():
    record = base_record(action=mutate_action())
    record = add_evidence(record, receipt(EvidenceKind.VERIFICATION_PASS))
    record = add_evidence(record, receipt(EvidenceKind.ACCEPTANCE_GATE_PASS))
    decision = can_claim(record, CompletionClaim.COMPLETED)
    assert decision.allowed is False
    assert "STATE_READBACK" in decision.code
    assert "TOOL_RECEIPT" in decision.code
    record = add_evidence(record, receipt(EvidenceKind.TOOL_RECEIPT))
    record = add_evidence(record, receipt(EvidenceKind.STATE_READBACK))
    assert can_claim(record, CompletionClaim.COMPLETED).allowed is True


def test_close_run_is_a_completion_firewall():
    record = base_record(phase=RunPhase.VERIFIED)
    with pytest.raises(ValueError, match="MISSING_EVIDENCE"):
        close_run(record)
    record = add_evidence(record, receipt(EvidenceKind.VERIFICATION_PASS))
    record = add_evidence(record, receipt(EvidenceKind.ACCEPTANCE_GATE_PASS))
    closed = close_run(record)
    assert closed.phase == RunPhase.CLOSED


def test_failure_is_loud_and_attributed_to_a_stage():
    failed = record_failure(
        base_record(phase=RunPhase.EXECUTING),
        stage=FailureStage.TOOL_EXECUTION,
        code="PROVIDER_QUOTA",
        mechanism="quota",
    )
    assert failed.phase == RunPhase.FAILED
    assert failed.failure_stage == FailureStage.TOOL_EXECUTION
    assert failed.failure_code == "PROVIDER_QUOTA"


def test_same_failure_retry_is_blocked_without_material_delta():
    failed = record_failure(
        base_record(phase=RunPhase.EXECUTING),
        stage=FailureStage.BINDING,
        code="INTER_ACTION_ID_NOT_FOUND",
        mechanism="id-binding",
    )
    decision = can_retry(
        failed,
        mechanism="id-binding",
        material_delta=False,
        retry_basis="",
    )
    assert decision.allowed is False
    assert decision.code == "NO_DELTA_RETRY_BLOCKED"


def test_unsynced_state_also_blocks_same_mechanism_retry_without_delta():
    observed = base_record(phase=RunPhase.OBSERVED, action=mutate_action())
    unsynced = mark_unsynced(
        observed,
        code="READBACK_MISMATCH",
        mechanism="readback-mismatch",
    )
    assert unsynced.phase == RunPhase.UNSYNCED
    decision = can_retry(
        unsynced,
        mechanism="readback-mismatch",
        material_delta=False,
        retry_basis="",
    )
    assert decision.allowed is False
    assert decision.code == "NO_DELTA_RETRY_BLOCKED"


def test_retry_is_allowed_only_when_material_delta_has_a_basis():
    failed = record_failure(
        base_record(phase=RunPhase.EXECUTING),
        stage=FailureStage.BINDING,
        code="INTER_ACTION_ID_NOT_FOUND",
        mechanism="id-binding",
    )
    missing_basis = can_retry(
        failed,
        mechanism="id-binding",
        material_delta=True,
        retry_basis="",
    )
    assert missing_basis.allowed is False

    changed = can_retry(
        failed,
        mechanism="different-provider-binding",
        material_delta=True,
        retry_basis="provider/action binding changed after live discovery",
    )
    assert changed.allowed is True


def test_header_is_rendered_from_control_state_not_model_free_text():
    record = base_record(mode=Mode.EXP, task="Stable Task")
    assert render_header(
        record,
        date="2026-08-30",
        time_with_offset="09:15+08:00",
    ) == (
        "[MODE=EXP][TASK=Stable Task]\n"
        "[DATE=2026-08-30][TIME=09:15+08:00]"
    )
