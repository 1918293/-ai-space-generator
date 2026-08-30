import asyncio
import uuid

from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from src.action_catalog import ActionBinding, ActionCatalog, ModelActionIntent
from src.authoritative_completion import CompletionAttestor, SQLiteAuthoritativeCompletionStore
from src.control_gateway import ControlPlaneGateway, ModelIngressRequest, TaskExecutionPolicy
from src.controlled_runner import ToolOutcome, VerificationOutcome
from src.execution_control import (
    ActionArchetype,
    ActionExternality,
    EvidenceKind,
    EvidenceOrigin,
    EvidenceReceipt,
    Mode,
    RunPhase,
)
from src.operational_state import ActiveOperationalState
from src.production_execution import ProductionExecutionService
from src.temporal_client import TemporalWorkflowStarter
from src.temporal_control import ExecutionActivities, HaoExecutionControlWorkflow


SECRET = b"hao-temporal-production-secret-minimum-32-bytes"


class PolicyProvider:
    def resolve(self, state):
        return TaskExecutionPolicy(
            goal_valid=True,
            acceptance_criteria=("controlled production result",),
        )


def gateway():
    return ControlPlaneGateway(
        ActionCatalog(
            [
                ActionBinding(
                    binding_id="research.read",
                    capability="research_read",
                    provider="fake-provider",
                    action_name="read",
                    archetype=ActionArchetype.READ,
                    externality=ActionExternality.READ_ONLY,
                ),
                ActionBinding(
                    binding_id="message.send",
                    capability="external_message",
                    provider="fake-provider",
                    action_name="send",
                    archetype=ActionArchetype.PUBLISH,
                    externality=ActionExternality.EXTERNAL_REVERSIBLE,
                    authorization_scope_prefix="SEND_EXTERNAL",
                ),
            ]
        ),
        PolicyProvider(),
    )


def state():
    return ActiveOperationalState(Mode.EXP, "Production Temporal", 21, "EVT-21")


def read_request(run_id):
    return ModelIngressRequest(
        run_id,
        1,
        ModelActionIntent("I-READ", "research_read", "research.read"),
    )


def external_request(run_id):
    return ModelIngressRequest(
        run_id,
        1,
        ModelActionIntent(
            "I-SEND",
            "external_message",
            "message.send",
            expected_state_delta="send one controlled message",
            authorization_target="recipient-1",
        ),
    )


class Broker:
    def __init__(self):
        self.calls = 0

    async def execute(self, proposal):
        self.calls += 1
        return ToolOutcome(True, f"TOOL-{self.calls}", "fake-provider")


class Verifier:
    async def verify(self, current, proposal, tool_outcome):
        receipts = [
            EvidenceReceipt(
                "VERIFY-" + proposal.action_id,
                EvidenceKind.VERIFICATION_PASS,
                True,
                "fake-verifier",
                claim_scope=proposal.action_id,
                origin=EvidenceOrigin.VERIFIER,
            ),
            EvidenceReceipt(
                "GATE-" + proposal.action_id,
                EvidenceKind.ACCEPTANCE_GATE_PASS,
                True,
                "fake-verifier",
                claim_scope=proposal.action_id,
                origin=EvidenceOrigin.VERIFIER,
            ),
        ]
        if proposal.archetype in {ActionArchetype.MUTATE, ActionArchetype.PUBLISH}:
            receipts.append(
                EvidenceReceipt(
                    "READBACK-" + proposal.action_id,
                    EvidenceKind.STATE_READBACK,
                    True,
                    "fake-provider-readback",
                    claim_scope=proposal.action_id,
                    origin=EvidenceOrigin.PROVIDER,
                )
            )
        return VerificationOutcome(True, tuple(receipts))


async def make_service(env, tmp_path, broker, task_queue):
    activities = ExecutionActivities(broker, Verifier())
    worker = Worker(
        env.client,
        task_queue=task_queue,
        workflows=[HaoExecutionControlWorkflow],
        activities=[activities.execute_tool, activities.verify_outcome],
    )
    service = ProductionExecutionService(
        gateway=gateway(),
        starter=TemporalWorkflowStarter(env.client, task_queue=task_queue),
        attestor=CompletionAttestor(SECRET),
        completion_store=SQLiteAuthoritativeCompletionStore(
            str(tmp_path / "production-completion.sqlite")
        ),
    )
    return worker, service


def test_production_facade_real_temporal_read_path_commits_authoritative_completion(tmp_path):
    async def scenario():
        broker = Broker()
        task_queue = "prod-" + str(uuid.uuid4())
        async with await WorkflowEnvironment.start_time_skipping() as env:
            worker, service = await make_service(env, tmp_path, broker, task_queue)
            async with worker:
                run_id = "RUN-PROD-TEMP-" + str(uuid.uuid4())
                result = await service.execute(
                    state(),
                    read_request(run_id),
                    issued_at="2026-08-30T14:30:00+08:00",
                )
                assert result.authoritative is True
                assert result.record.phase == RunPhase.CLOSED
                assert result.attestation.run_id == run_id
                assert broker.calls == 1

    asyncio.run(scenario())


def test_production_facade_external_action_survives_submit_signal_finalize_boundary(tmp_path):
    async def scenario():
        broker = Broker()
        task_queue = "prod-" + str(uuid.uuid4())
        async with await WorkflowEnvironment.start_time_skipping() as env:
            worker, service = await make_service(env, tmp_path, broker, task_queue)
            async with worker:
                run_id = "RUN-PROD-EXT-" + str(uuid.uuid4())
                submission = await service.submit(state(), external_request(run_id))
                assert submission.accepted is True
                assert submission.pending.handle.workflow_id == run_id
                await service.authorize(
                    submission.pending,
                    scope="SEND_EXTERNAL:recipient-1",
                    approved=True,
                    reason="Hao approved exact scope",
                )
                result = await service.finalize(
                    submission.pending,
                    issued_at="2026-08-30T14:31:00+08:00",
                )
                assert result.authoritative is True
                assert result.record.phase == RunPhase.CLOSED
                assert broker.calls == 1

    asyncio.run(scenario())


def test_rejected_external_action_never_gets_attestation_or_provider_call(tmp_path):
    async def scenario():
        broker = Broker()
        task_queue = "prod-" + str(uuid.uuid4())
        async with await WorkflowEnvironment.start_time_skipping() as env:
            worker, service = await make_service(env, tmp_path, broker, task_queue)
            async with worker:
                run_id = "RUN-PROD-REJECT-" + str(uuid.uuid4())
                submission = await service.submit(state(), external_request(run_id))
                await service.authorize(
                    submission.pending,
                    scope="SEND_EXTERNAL:recipient-1",
                    approved=False,
                    reason="Hao rejected",
                )
                result = await service.finalize(
                    submission.pending,
                    issued_at="2026-08-30T14:32:00+08:00",
                )
                assert result.authoritative is False
                assert result.attestation is None
                assert result.record.phase == RunPhase.BLOCKED
                assert broker.calls == 0

    asyncio.run(scenario())
