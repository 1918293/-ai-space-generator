from src.controlled_runner import (
    PreExecutionGateOutcome,
    ToolOutcome,
    VerificationOutcome,
    run_controlled_action,
)
from src.execution_control import (
    ActionArchetype,
    ActionExternality,
    ActionProposal,
    EvidenceKind,
    EvidenceOrigin,
    EvidenceReceipt,
    ExecutionRecord,
    FailureStage,
    Mode,
    RunPhase,
)


class CountingExecutor:
    def __init__(self, outcome=None, exc=None):
        self.calls = 0
        self.outcome = outcome or ToolOutcome(True, "TOOL-1", "fake-provider")
        self.exc = exc

    def execute(self, proposal):
        self.calls += 1
        if self.exc is not None:
            raise self.exc
        return self.outcome


class CountingVerifier:
    def __init__(self, outcome=None, exc=None):
        self.calls = 0
        self.outcome = outcome
        self.exc = exc

    def verify(self, record, proposal, tool_outcome):
        self.calls += 1
        if self.exc is not None:
            raise self.exc
        if self.outcome is not None:
            return self.outcome
        return VerificationOutcome(
            True,
            receipts=(
                EvidenceReceipt(
                    "READBACK-1",
                    EvidenceKind.STATE_READBACK,
                    True,
                    "fake-provider-readback",
                    claim_scope=proposal.action_id,
                ),
                EvidenceReceipt(
                    "VERIFY-1",
                    EvidenceKind.VERIFICATION_PASS,
                    True,
                    "fake-verifier",
                    claim_scope=proposal.action_id,
                ),
                EvidenceReceipt(
                    "ACCEPT-GATE-1",
                    EvidenceKind.ACCEPTANCE_GATE_PASS,
                    True,
                    "fake-verifier",
                    claim_scope=proposal.action_id,
                ),
            ),
        )


class CountingPreExecutionGateVerifier:
    def __init__(self, outcome=None, exc=None):
        self.calls = 0
        self.outcome = outcome
        self.exc = exc

    def verify(self, record, proposal):
        self.calls += 1
        if self.exc is not None:
            raise self.exc
        if self.outcome is not None:
            return self.outcome
        return PreExecutionGateOutcome(
            True,
            receipts=(
                EvidenceReceipt(
                    "PRE-GATE-1",
                    EvidenceKind.PRE_EXECUTION_GATE_PASS,
                    True,
                    "source-bound-gate-v3",
                    claim_scope=proposal.action_id,
                    origin=EvidenceOrigin.VERIFIER,
                    gate_id="GATE_V3_SOURCE_BOUND",
                ),
            ),
        )


def record(**overrides):
    values = dict(
        run_id="RUN-CTRL-1",
        task="Execution control",
        mode=Mode.EXP,
        goal_valid=True,
        acceptance_criteria=("verified outcome",),
        authority_refs=("AUTH-1",),
    )
    values.update(overrides)
    return ExecutionRecord(**values)


def read_action(**overrides):
    values = dict(
        action_id="ACT-1",
        archetype=ActionArchetype.READ,
        externality=ActionExternality.READ_ONLY,
        capability="read",
        provider="fake",
        action_name="read",
        required_authority_refs=("AUTH-1",),
    )
    values.update(overrides)
    return ActionProposal(**values)


def publish_action(**overrides):
    values = dict(
        action_id="ACT-PUBLISH-1",
        archetype=ActionArchetype.PUBLISH,
        externality=ActionExternality.EXTERNAL_REVERSIBLE,
        capability="publish",
        provider="fake",
        action_name="send",
        expected_state_delta="message sent",
        required_authority_refs=("AUTH-1",),
        authorization_scope="SEND_EXTERNAL",
        idempotency_key="RUN-CTRL-1:ACT-PUBLISH-1",
    )
    values.update(overrides)
    return ActionProposal(**values)


def scoped(kind, proposal, evidence_id):
    return EvidenceReceipt(
        evidence_id,
        kind,
        True,
        "fake-verifier",
        claim_scope=proposal.action_id,
    )


def test_successful_controlled_action_closes_only_after_verification_and_acceptance_gate():
    executor = CountingExecutor()
    verifier = CountingVerifier()
    result = run_controlled_action(record(), read_action(), executor=executor, verifier=verifier)
    assert executor.calls == 1
    assert verifier.calls == 1
    assert result.admission.allowed is True
    assert result.completion.allowed is True
    assert result.record.phase == RunPhase.CLOSED


def test_tool_failure_is_typed_and_verifier_never_runs():
    executor = CountingExecutor(
        ToolOutcome(False, error_code="QUOTA_BLOCKED", failure_stage=FailureStage.TOOL_EXECUTION)
    )
    verifier = CountingVerifier()
    result = run_controlled_action(record(), read_action(), executor=executor, verifier=verifier)
    assert executor.calls == 1
    assert verifier.calls == 0
    assert result.record.phase == RunPhase.FAILED
    assert result.record.failure_stage == FailureStage.TOOL_EXECUTION
    assert result.record.failure_code == "QUOTA_BLOCKED"


def test_executor_exception_becomes_loud_typed_failure():
    executor = CountingExecutor(exc=RuntimeError("boom"))
    verifier = CountingVerifier()
    result = run_controlled_action(record(), read_action(), executor=executor, verifier=verifier)
    assert verifier.calls == 0
    assert result.record.phase == RunPhase.FAILED
    assert result.record.failure_stage == FailureStage.TOOL_EXECUTION
    assert result.record.failure_code == "EXECUTOR_EXCEPTION:RuntimeError"


def test_success_without_receipt_is_not_allowed_to_become_observed_success():
    executor = CountingExecutor(ToolOutcome(True, "", ""))
    verifier = CountingVerifier()
    result = run_controlled_action(record(), read_action(), executor=executor, verifier=verifier)
    assert verifier.calls == 0
    assert result.record.phase == RunPhase.FAILED
    assert result.record.failure_stage == FailureStage.TOOL_OUTPUT
    assert result.record.failure_code == "TOOL_SUCCESS_WITHOUT_RECEIPT"


def test_verifier_failure_prevents_completion():
    executor = CountingExecutor()
    verifier = CountingVerifier(VerificationOutcome(False, error_code="READBACK_MISMATCH"))
    result = run_controlled_action(record(), read_action(), executor=executor, verifier=verifier)
    assert result.record.phase == RunPhase.FAILED
    assert result.record.failure_stage == FailureStage.VERIFICATION
    assert result.record.failure_code == "READBACK_MISMATCH"


def test_verifier_must_emit_action_scoped_verification_receipt():
    proposal = read_action()
    executor = CountingExecutor()
    verifier = CountingVerifier(
        VerificationOutcome(
            True,
            receipts=(
                EvidenceReceipt(
                    "VERIFY-WRONG-SCOPE",
                    EvidenceKind.VERIFICATION_PASS,
                    True,
                    "fake-verifier",
                    claim_scope="OLD-ACTION",
                ),
                scoped(EvidenceKind.ACCEPTANCE_GATE_PASS, proposal, "ACCEPT-GATE-1"),
            ),
        )
    )
    result = run_controlled_action(record(), proposal, executor=executor, verifier=verifier)
    assert result.record.phase == RunPhase.FAILED
    assert result.record.failure_code == "VERIFIER_DID_NOT_PRODUCE_ACTION_SCOPED_VERIFICATION_RECEIPT"


def test_missing_acceptance_gate_results_in_honest_verified_stall_not_false_done():
    proposal = read_action()
    executor = CountingExecutor()
    verifier = CountingVerifier(
        VerificationOutcome(
            True,
            receipts=(scoped(EvidenceKind.VERIFICATION_PASS, proposal, "VERIFY-1"),),
        )
    )
    result = run_controlled_action(record(), proposal, executor=executor, verifier=verifier)
    assert result.record.phase == RunPhase.VERIFIED
    assert result.completion.allowed is False
    assert result.completion.code.startswith("MISSING_EVIDENCE:")


def test_external_action_without_authorization_never_reaches_executor():
    executor = CountingExecutor()
    verifier = CountingVerifier()
    result = run_controlled_action(record(), publish_action(), executor=executor, verifier=verifier)
    assert executor.calls == 0
    assert verifier.calls == 0
    assert result.record.phase == RunPhase.AWAITING_HAO
    assert result.admission.requires_hao_authorization is True


def test_external_action_with_exact_authorization_and_readback_can_close():
    executor = CountingExecutor()
    verifier = CountingVerifier()
    result = run_controlled_action(
        record(),
        publish_action(),
        executor=executor,
        verifier=verifier,
        hao_authorized_scopes={"SEND_EXTERNAL"},
    )
    assert executor.calls == 1
    assert verifier.calls == 1
    assert result.record.phase == RunPhase.CLOSED


def test_side_effect_success_without_action_scoped_readback_becomes_unsynced_not_done():
    proposal = publish_action()
    executor = CountingExecutor()
    verifier = CountingVerifier(
        VerificationOutcome(
            True,
            receipts=(
                scoped(EvidenceKind.VERIFICATION_PASS, proposal, "VERIFY-1"),
                scoped(EvidenceKind.ACCEPTANCE_GATE_PASS, proposal, "ACCEPT-GATE-1"),
                EvidenceReceipt(
                    "READBACK-OLD",
                    EvidenceKind.STATE_READBACK,
                    True,
                    "fake-provider-readback",
                    claim_scope="OLD-ACTION",
                ),
            ),
        )
    )
    result = run_controlled_action(
        record(),
        proposal,
        executor=executor,
        verifier=verifier,
        hao_authorized_scopes={"SEND_EXTERNAL"},
    )
    assert result.record.phase == RunPhase.UNSYNCED
    assert result.record.failure_stage == FailureStage.PERSISTENCE
    assert result.record.failure_code == "MUTATION_WITHOUT_ACTION_SCOPED_STATE_READBACK"
    assert result.completion.allowed is False


def test_side_effect_verification_failure_is_unsynced_to_prevent_duplicate_retry():
    executor = CountingExecutor()
    verifier = CountingVerifier(
        VerificationOutcome(False, error_code="READBACK_MISMATCH", failure_stage=FailureStage.PERSISTENCE)
    )
    result = run_controlled_action(
        record(),
        publish_action(),
        executor=executor,
        verifier=verifier,
        hao_authorized_scopes={"SEND_EXTERNAL"},
    )
    assert result.record.phase == RunPhase.UNSYNCED
    assert result.record.failure_code == "READBACK_MISMATCH"
    assert result.record.last_failure_mechanism == "readback_mismatch"


def pre_gate_receipt(
    proposal,
    *,
    gate_id="GATE_V3_SOURCE_BOUND",
    scope=None,
    origin=EvidenceOrigin.VERIFIER,
    kind=EvidenceKind.PRE_EXECUTION_GATE_PASS,
    passed=True,
    evidence_id="PRE-GATE-TEST",
):
    return EvidenceReceipt(
        evidence_id,
        kind,
        passed,
        "source-bound-gate-v3",
        claim_scope=proposal.action_id if scope is None else scope,
        origin=origin,
        gate_id=gate_id,
    )


def test_required_pre_execution_gate_without_verifier_blocks_before_executor():
    executor = CountingExecutor()
    verifier = CountingVerifier()
    result = run_controlled_action(
        record(required_pre_execution_gate_ids=("GATE_V3_SOURCE_BOUND",)),
        read_action(),
        executor=executor,
        verifier=verifier,
    )
    assert executor.calls == 0
    assert verifier.calls == 0
    assert result.record.phase == RunPhase.BLOCKED
    assert result.record.failure_code == "PRE_EXECUTION_GATE_VERIFIER_REQUIRED"


def test_failed_pre_execution_gate_blocks_before_executor():
    executor = CountingExecutor()
    verifier = CountingVerifier()
    gate = CountingPreExecutionGateVerifier(
        PreExecutionGateOutcome(False, error_code="GATE_V3_REJECTED")
    )
    result = run_controlled_action(
        record(required_pre_execution_gate_ids=("GATE_V3_SOURCE_BOUND",)),
        read_action(),
        executor=executor,
        verifier=verifier,
        pre_execution_gate_verifier=gate,
    )
    assert gate.calls == 1
    assert executor.calls == 0
    assert verifier.calls == 0
    assert result.record.phase == RunPhase.BLOCKED
    assert result.record.failure_code == "GATE_V3_REJECTED"


def test_malformed_pre_execution_gate_receipts_fail_closed_before_executor():
    proposal = read_action()
    cases = (
        (
            pre_gate_receipt(proposal, scope="OTHER-ACTION"),
            "PRE_EXECUTION_GATE_RECEIPT_SCOPE_MISMATCH",
        ),
        (
            pre_gate_receipt(proposal, gate_id="OTHER-GATE"),
            "PRE_EXECUTION_GATE_RECEIPT_UNEXPECTED:OTHER-GATE",
        ),
        (
            pre_gate_receipt(proposal, origin=EvidenceOrigin.RUNTIME),
            "PRE_EXECUTION_GATE_RECEIPT_ORIGIN_INVALID",
        ),
        (
            pre_gate_receipt(proposal, kind=EvidenceKind.ACCEPTANCE_GATE_PASS),
            "PRE_EXECUTION_GATE_RECEIPT_KIND_INVALID",
        ),
    )
    for receipt, expected_code in cases:
        executor = CountingExecutor()
        verifier = CountingVerifier()
        gate = CountingPreExecutionGateVerifier(
            PreExecutionGateOutcome(True, receipts=(receipt,))
        )
        result = run_controlled_action(
            record(required_pre_execution_gate_ids=("GATE_V3_SOURCE_BOUND",)),
            proposal,
            executor=executor,
            verifier=verifier,
            pre_execution_gate_verifier=gate,
        )
        assert executor.calls == 0
        assert verifier.calls == 0
        assert result.record.phase == RunPhase.BLOCKED
        assert result.record.failure_code == expected_code


def test_pre_execution_gate_exception_blocks_before_executor():
    executor = CountingExecutor()
    verifier = CountingVerifier()
    gate = CountingPreExecutionGateVerifier(exc=RuntimeError("gate boom"))
    result = run_controlled_action(
        record(required_pre_execution_gate_ids=("GATE_V3_SOURCE_BOUND",)),
        read_action(),
        executor=executor,
        verifier=verifier,
        pre_execution_gate_verifier=gate,
    )
    assert executor.calls == 0
    assert verifier.calls == 0
    assert result.record.phase == RunPhase.BLOCKED
    assert result.record.failure_code == "PRE_EXECUTION_GATE_EXCEPTION:RuntimeError"


def test_passing_pre_execution_gate_allows_execution_but_does_not_substitute_for_post_effect_acceptance():
    proposal = read_action()
    executor = CountingExecutor()
    gate = CountingPreExecutionGateVerifier()
    verifier = CountingVerifier(
        VerificationOutcome(
            True,
            receipts=(
                EvidenceReceipt(
                    "VERIFY-ONLY",
                    EvidenceKind.VERIFICATION_PASS,
                    True,
                    "post-effect-verifier",
                    claim_scope=proposal.action_id,
                    origin=EvidenceOrigin.VERIFIER,
                ),
            ),
        )
    )
    result = run_controlled_action(
        record(required_pre_execution_gate_ids=("GATE_V3_SOURCE_BOUND",)),
        proposal,
        executor=executor,
        verifier=verifier,
        pre_execution_gate_verifier=gate,
    )
    assert gate.calls == 1
    assert executor.calls == 1
    assert verifier.calls == 1
    assert result.record.phase == RunPhase.VERIFIED
    assert result.completion.allowed is False
    assert "ACCEPTANCE_GATE_PASS" in result.completion.code
    assert any(
        item.kind == EvidenceKind.PRE_EXECUTION_GATE_PASS
        and item.gate_id == "GATE_V3_SOURCE_BOUND"
        and item.claim_scope == proposal.action_id
        for item in result.record.evidence
    )


def test_passing_pre_execution_gate_preserves_existing_success_path_when_post_effect_acceptance_passes():
    executor = CountingExecutor()
    verifier = CountingVerifier()
    gate = CountingPreExecutionGateVerifier()
    result = run_controlled_action(
        record(required_pre_execution_gate_ids=("GATE_V3_SOURCE_BOUND",)),
        read_action(),
        executor=executor,
        verifier=verifier,
        pre_execution_gate_verifier=gate,
    )
    assert gate.calls == 1
    assert executor.calls == 1
    assert verifier.calls == 1
    assert result.record.phase == RunPhase.CLOSED
    assert result.completion.allowed is True
