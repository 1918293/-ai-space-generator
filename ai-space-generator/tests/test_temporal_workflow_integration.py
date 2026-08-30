import asyncio
import uuid

from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from src.controlled_runner import ToolOutcome, VerificationOutcome
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
from src.temporal_control import (
    ApprovalSignal,
    DurableRunInput,
    ExecutionActivities,
    HaoExecutionControlWorkflow,
)


def record(run_id):
    return ExecutionRecord(
        run_id=run_id,
        task="Temporal E2E",
        mode=Mode.EXP,
        goal_valid=True,
        acceptance_criteria=("verified durable completion",),
        authority_refs=("AUTH-1",),
    )


def read_action():
    return ActionProposal(
        action_id="ACT-READ",
        archetype=ActionArchetype.READ,
        externality=ActionExternality.READ_ONLY,
        capability="read",
        provider="fake",
        action_name="read",
        required_authority_refs=("AUTH-1",),
    )


def external_action():
    return ActionProposal(
        action_id="ACT-EXT",
        archetype=ActionArchetype.PUBLISH,
        externality=ActionExternality.EXTERNAL_REVERSIBLE,
        capability="publish",
        provider="fake",
        action_name="send",
        expected_state_delta="send one message",
        required_authority_refs=("AUTH-1",),
        authorization_scope="SEND_EXTERNAL",
        idempotency_key="TEMPORAL:E2E:ACT-EXT",
    )


class Broker:
    def __init__(self):
        self.calls = 0

    async def execute(self, proposal):
        self.calls += 1
        return ToolOutcome(True, f"TOOL-{self.calls}", "fake-provider")


class Verifier:
    def __init__(self, *, fail=False):
        self.calls = 0
        self.fail = fail

    async def verify(self, current, proposal, tool_outcome):
        self.calls += 1
        if self.fail:
            return VerificationOutcome(
                False,
                error_code="READBACK_MISMATCH",
                failure_stage=FailureStage.PERSISTENCE,
            )
        receipts = [
            EvidenceReceipt(
                f"VERIFY-{self.calls}",
                EvidenceKind.VERIFICATION_PASS,
                True,
                "fake-verifier",
            ),
            EvidenceReceipt(
                f"GATE-{self.calls}",
                EvidenceKind.ACCEPTANCE_GATE_PASS,
                True,
                "fake-verifier",
            ),
        ]
        if proposal.archetype in {ActionArchetype.MUTATE, ActionArchetype.PUBLISH}:
            receipts.append(
                EvidenceReceipt(
                    f"READBACK-{self.calls}",
                    EvidenceKind.STATE_READBACK,
                    True,
                    "fake-provider-readback",
                )
            )
        return VerificationOutcome(True, receipts=tuple(receipts))


async def run_workflow(proposal, *, verifier=None, authorize=False):
    broker = Broker()
    verifier = verifier or Verifier()
    activities = ExecutionActivities(broker, verifier)
    task_queue = f"hao-control-{uuid.uuid4()}"
    workflow_id = f"hao-control-{uuid.uuid4()}"

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=task_queue,
            workflows=[HaoExecutionControlWorkflow],
            activities=[activities.execute_tool, activities.verify_outcome],
        ):
            handle = await env.client.start_workflow(
                HaoExecutionControlWorkflow.run,
                DurableRunInput(record(workflow_id), proposal),
                id=workflow_id,
                task_queue=task_queue,
            )
            if authorize:
                await handle.signal(
                    HaoExecutionControlWorkflow.authorization,
                    ApprovalSignal("SEND_EXTERNAL", True),
                )
            result = await handle.result()
            return result, broker, verifier


def test_temporal_e2e_read_action_reaches_closed_from_real_worker():
    result, broker, verifier = asyncio.run(run_workflow(read_action()))
    assert result.record.phase == RunPhase.CLOSED
    assert result.completion.allowed is True
    assert broker.calls == 1
    assert verifier.calls == 1


def test_temporal_e2e_external_action_waits_for_signal_then_executes_once():
    result, broker, verifier = asyncio.run(
        run_workflow(external_action(), authorize=True)
    )
    assert result.record.phase == RunPhase.CLOSED
    assert result.completion.allowed is True
    assert broker.calls == 1
    assert verifier.calls == 1


def test_temporal_e2e_side_effect_verification_failure_persists_unsynced_state():
    result, broker, verifier = asyncio.run(
        run_workflow(external_action(), verifier=Verifier(fail=True), authorize=True)
    )
    assert broker.calls == 1
    assert verifier.calls == 1
    assert result.record.phase == RunPhase.UNSYNCED
    assert result.record.failure_stage == FailureStage.PERSISTENCE
    assert result.record.failure_code == "READBACK_MISMATCH"
    assert result.completion.allowed is False
