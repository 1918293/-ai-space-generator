import asyncio
import uuid

from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, Worker

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
)
from src.operational_state import ActiveOperationalState
from src.production_execution import ProductionExecutionService
from src.temporal_client import TemporalWorkflowStarter
from src.temporal_control import ExecutionActivities, HaoExecutionControlWorkflow


SECRET = b"hao-temporal-replay-secret-minimum-32-bytes"


class PolicyProvider:
    def resolve(self, state):
        del state
        return TaskExecutionPolicy(
            goal_valid=True,
            acceptance_criteria=("replay compatible",),
        )


class Broker:
    async def execute(self, proposal):
        return ToolOutcome(True, "RECEIPT-REPLAY", proposal.provider)


class Verifier:
    async def verify(self, current, proposal, tool_outcome):
        del current, tool_outcome
        return VerificationOutcome(
            True,
            (
                EvidenceReceipt(
                    "VERIFY-REPLAY-" + proposal.action_id,
                    EvidenceKind.VERIFICATION_PASS,
                    True,
                    "runtime-verifier",
                    claim_scope=proposal.action_id,
                    origin=EvidenceOrigin.VERIFIER,
                ),
                EvidenceReceipt(
                    "GATE-REPLAY-" + proposal.action_id,
                    EvidenceKind.ACCEPTANCE_GATE_PASS,
                    True,
                    "runtime-verifier",
                    claim_scope=proposal.action_id,
                    origin=EvidenceOrigin.VERIFIER,
                ),
            ),
        )


def gateway():
    return ControlPlaneGateway(
        ActionCatalog(
            [
                ActionBinding(
                    binding_id="replay.read",
                    capability="research_read",
                    provider="fake-provider",
                    action_name="read",
                    archetype=ActionArchetype.READ,
                    externality=ActionExternality.READ_ONLY,
                )
            ]
        ),
        PolicyProvider(),
    )


def state():
    return ActiveOperationalState(Mode.EXP, "Temporal replay compatibility", 31, "EVT-31")


def request(run_id):
    return ModelIngressRequest(
        run_id,
        1,
        ModelActionIntent("I-REPLAY", "research_read", "replay.read"),
    )


def test_current_runtime_workflow_history_replays_without_nondeterminism(tmp_path):
    async def scenario():
        task_queue = "replay-" + str(uuid.uuid4())
        async with await WorkflowEnvironment.start_time_skipping() as env:
            activities = ExecutionActivities(Broker(), Verifier())
            worker = Worker(
                env.client,
                task_queue=task_queue,
                workflows=[HaoExecutionControlWorkflow],
                activities=[
                    activities.preflight_authority,
                    activities.execute_tool,
                    activities.verify_outcome,
                ],
            )
            service = ProductionExecutionService(
                gateway=gateway(),
                starter=TemporalWorkflowStarter(env.client, task_queue=task_queue),
                attestor=CompletionAttestor(SECRET),
                completion_store=SQLiteAuthoritativeCompletionStore(
                    str(tmp_path / "replay-completion.sqlite")
                ),
            )

            run_id = "RUN-REPLAY-" + str(uuid.uuid4())
            async with worker:
                result = await service.execute(
                    state(),
                    request(run_id),
                    issued_at="2026-09-05T14:30:00+08:00",
                )
                assert result.authoritative is True
                handle = env.client.get_workflow_handle(run_id)
                history = await handle.fetch_history()

            replay = await Replayer(
                workflows=[HaoExecutionControlWorkflow]
            ).replay_workflow(history)
            assert replay.replay_failure is None

    asyncio.run(scenario())
