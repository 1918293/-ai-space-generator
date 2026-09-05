import asyncio

from src.controlled_runner import ToolOutcome, VerificationOutcome
from src.execution_control import (
    ActionArchetype,
    ActionExternality,
    ActionProposal,
    EvidenceKind,
    EvidenceReceipt,
    ExecutionRecord,
    Mode,
)
from src.temporal_control import (
    ApprovalSignal,
    DurableRunInput,
    ExecutionActivities,
    HaoExecutionControlWorkflow,
    VerificationRequest,
)


def record():
    return ExecutionRecord(
        run_id="RUN-TEMPORAL-1",
        task="Durable execution",
        mode=Mode.EXP,
        goal_valid=True,
        acceptance_criteria=("durable verified completion",),
        authority_refs=("AUTH-1",),
    )


def action():
    return ActionProposal(
        action_id="ACT-TEMPORAL-1",
        archetype=ActionArchetype.MUTATE,
        externality=ActionExternality.PRIVATE_REVERSIBLE,
        capability="formal_persistence",
        provider="fake-drive",
        action_name="update",
        expected_state_delta="write one verified delta",
        required_authority_refs=("AUTH-1",),
        idempotency_key="RUN-TEMPORAL-1:ACT-TEMPORAL-1",
        rollback_available=True,
    )


class Broker:
    def __init__(self):
        self.calls = 0

    async def execute(self, proposal):
        self.calls += 1
        assert proposal.idempotency_key
        return ToolOutcome(True, "TOOL-R1", "fake-drive")


class Verifier:
    def __init__(self):
        self.calls = 0

    async def verify(self, current, proposal, tool_outcome):
        self.calls += 1
        assert tool_outcome.receipt_id == "TOOL-R1"
        return VerificationOutcome(
            True,
            receipts=(
                EvidenceReceipt(
                    "READBACK-R1",
                    EvidenceKind.STATE_READBACK,
                    True,
                    "fake-drive-readback",
                    claim_scope=proposal.action_id,
                ),
                EvidenceReceipt(
                    "VERIFY-R1",
                    EvidenceKind.VERIFICATION_PASS,
                    True,
                    "fake-verifier",
                    claim_scope=proposal.action_id,
                ),
                EvidenceReceipt(
                    "GATE-R1",
                    EvidenceKind.ACCEPTANCE_GATE_PASS,
                    True,
                    "fake-verifier",
                    claim_scope=proposal.action_id,
                ),
            ),
        )


def test_temporal_workflow_definition_imports_with_pinned_sdk():
    assert HaoExecutionControlWorkflow.__name__ == "HaoExecutionControlWorkflow"
    run_input = DurableRunInput(record(), action())
    assert run_input.record.run_id == "RUN-TEMPORAL-1"
    assert ApprovalSignal("WRITE_HAO", True).approved is True


def test_temporal_activity_boundary_delegates_only_to_injected_broker_and_verifier():
    broker = Broker()
    verifier = Verifier()
    activities = ExecutionActivities(broker, verifier)

    tool_outcome = asyncio.run(activities.execute_tool(action()))
    assert broker.calls == 1
    assert tool_outcome.success is True

    verification = asyncio.run(
        activities.verify_outcome(
            VerificationRequest(record(), action(), tool_outcome)
        )
    )
    assert verifier.calls == 1
    assert verification.passed is True
    assert {receipt.kind for receipt in verification.receipts} >= {
        EvidenceKind.STATE_READBACK,
        EvidenceKind.VERIFICATION_PASS,
        EvidenceKind.ACCEPTANCE_GATE_PASS,
    }
    assert {receipt.claim_scope for receipt in verification.receipts} == {action().action_id}
