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
        self.outcome = outcome or VerificationOutcome(
            True,
            receipts=(
                EvidenceReceipt(
                    "VERIFY-1",
                    EvidenceKind.VERIFICATION_PASS,
                    True,
                    "fake-verifier",
                ),
                EvidenceReceipt(
                    "ACCEPT-GATE-1",
                    EvidenceKind.ACCEPTANCE_GATE_PASS,
                    True,
                    "fake-verifier",
                ),
            ),
        )
        self.exc = exc

    def verify(self, record, proposal, tool_outcome):
        self.calls += 1
        if self.exc is not None:
            raise self.exc
        return self.outcome


def record():
    return ExecutionRecord(
        run_id="RUN-CTRL-1",
        task="Execution control",
        mode=Mode.EXP,
        goal_valid=True,
        acceptance_criteria=("verified outcome",),
        authority_refs=("AUTH-1",),
    )


def read_action():
    return ActionProposal(
        action_id="ACT-1",
        archetype=ActionArchetype.READ,
        externality=ActionExternality.READ_ONLY,
        capability="read",
        provider="fake",
        action_name="read",
        required_authority_refs=("AUTH-1",),
    )


def publish_action():
    return ActionProposal(
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


def test_successful_controlled_action_closes_only_after_verification_and_acceptance_gate():
    executor = CountingExecutor()
    verifier = CountingVerifier()
    result = run_controlled_action(
        record(),
        read_action(),
        executor=executor,
        verifier=verifier,
    )
    assert executor.calls == 1
    assert verifier.calls == 1
    assert result.admission.allowed is True
    assert result.completion.allowed is True
    assert result.record.phase == RunPhase.CLOSED


def test_tool_failure_is_typed_and_verifier_never_runs():
    executor = CountingExecutor(
        ToolOutcome(
            False,
            error_code="QUOTA_BLOCKED",
            failure_stage=FailureStage.TOOL_EXECUTION,
        )
    )
    verifier = CountingVerifier()
    result = run_controlled_action(
        record(),
        read_action(),
        executor=executor,
        verifier=verifier,
    )
    assert executor.calls == 1
    assert verifier.calls == 0
    assert result.record.phase == RunPhase.FAILED
    assert result.record.failure_stage == FailureStage.TOOL_EXECUTION
    assert result.record.failure_code == "QUOTA_BLOCKED"


def test_executor_exception_becomes_loud_typed_failure():
    executor = CountingExecutor(exc=RuntimeError("boom"))
    verifier = CountingVerifier()
    result = run_controlled_action(
        record(),
        read_action(),
        executor=executor,
        verifier=verifier,
    )
    assert verifier.calls == 0
    assert result.record.phase == RunPhase.FAILED
    assert result.record.failure_stage == FailureStage.TOOL_EXECUTION
    assert result.record.failure_code == "EXECUTOR_EXCEPTION:RuntimeError"


def test_success_without_receipt_is_not_allowed_to_become_observed_success():
    executor = CountingExecutor(ToolOutcome(True, "", ""))
    verifier = CountingVerifier()
    result = run_controlled_action(
        record(),
        read_action(),
        executor=executor,
        verifier=verifier,
    )
    assert verifier.calls == 0
    assert result.record.phase == RunPhase.FAILED
    assert result.record.failure_stage == FailureStage.TOOL_OUTPUT
    assert result.record.failure_code == "TOOL_SUCCESS_WITHOUT_RECEIPT"


def test_verifier_failure_prevents_completion():
    executor = CountingExecutor()
    verifier = CountingVerifier(VerificationOutcome(False, error_code="READBACK_MISMATCH"))
    result = run_controlled_action(
        record(),
        read_action(),
        executor=executor,
        verifier=verifier,
    )
    assert result.record.phase == RunPhase.FAILED
    assert result.record.failure_stage == FailureStage.VERIFICATION
    assert result.record.failure_code == "READBACK_MISMATCH"


def test_verifier_must_emit_direct_verification_receipt():
    executor = CountingExecutor()
    verifier = CountingVerifier(
        VerificationOutcome(
            True,
            receipts=(
                EvidenceReceipt(
                    "ACCEPT-GATE-1",
                    EvidenceKind.ACCEPTANCE_GATE_PASS,
                    True,
                    "fake-verifier",
                ),
            ),
        )
    )
    result = run_controlled_action(
        record(),
        read_action(),
        executor=executor,
        verifier=verifier,
    )
    assert result.record.phase == RunPhase.FAILED
    assert result.record.failure_code == "VERIFIER_DID_NOT_PRODUCE_VERIFICATION_RECEIPT"


def test_missing_acceptance_gate_results_in_honest_verified_stall_not_false_done():
    executor = CountingExecutor()
    verifier = CountingVerifier(
        VerificationOutcome(
            True,
            receipts=(
                EvidenceReceipt(
                    "VERIFY-1",
                    EvidenceKind.VERIFICATION_PASS,
                    True,
                    "fake-verifier",
                ),
            ),
        )
    )
    result = run_controlled_action(
        record(),
        read_action(),
        executor=executor,
        verifier=verifier,
    )
    assert result.record.phase == RunPhase.VERIFIED
    assert result.completion.allowed is False
    assert result.completion.code.startswith("MISSING_EVIDENCE:")


def test_external_action_without_authorization_never_reaches_executor():
    executor = CountingExecutor()
    verifier = CountingVerifier()
    result = run_controlled_action(
        record(),
        publish_action(),
        executor=executor,
        verifier=verifier,
    )
    assert executor.calls == 0
    assert verifier.calls == 0
    assert result.record.phase == RunPhase.AWAITING_HAO
    assert result.admission.requires_hao_authorization is True


def test_external_action_with_exact_authorization_scope_can_execute():
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
