from src.controlled_runner import ToolOutcome, VerificationOutcome, run_controlled_action
from src.execution_control import (
    ActionArchetype,
    ActionExternality,
    ActionProposal,
    EvidenceKind,
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
