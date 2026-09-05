import asyncio
import sqlite3
import uuid
from dataclasses import replace
from pathlib import Path

import pytest
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
    ExecutionRecord,
    Mode,
    RunPhase,
)
from src.operational_state import ActiveOperationalState
from src.parent_task_production import (
    ParentChildPlan,
    ParentTaskPlan,
    ParentTaskPlanCatalog,
    PostgresParentTaskStore,
    ProductionParentTaskService,
)
from src.production_execution import ProductionExecutionService
from src.task_runtime import ParentTaskPhase
from src.temporal_client import TemporalWorkflowStarter
from src.temporal_control import ApprovalSignal, ExecutionActivities, HaoExecutionControlWorkflow


SECRET = b"lane-e-parent-distributed-secret-minimum-32-bytes"


class PolicyProvider:
    def resolve(self, state):
        return TaskExecutionPolicy(
            goal_valid=True,
            acceptance_criteria=("distributed verification",),
        )


def gateway():
    return ControlPlaneGateway(
        ActionCatalog(
            [
                ActionBinding(
                    binding_id="research.read",
                    capability="research_read",
                    provider="lane-e-fake",
                    action_name="read",
                    archetype=ActionArchetype.READ,
                    externality=ActionExternality.READ_ONLY,
                ),
                ActionBinding(
                    binding_id="message.send",
                    capability="external_message",
                    provider="lane-e-fake",
                    action_name="send",
                    archetype=ActionArchetype.PUBLISH,
                    externality=ActionExternality.EXTERNAL_REVERSIBLE,
                    authorization_scope_prefix="SEND_EXTERNAL",
                ),
            ]
        ),
        PolicyProvider(),
    )


def state(version=31):
    return ActiveOperationalState(Mode.EXP, "Lane E distributed verification", version, "EVT-31")


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
    def __init__(self, *, fail=False):
        self.calls = 0
        self.fail = fail

    async def execute(self, proposal):
        self.calls += 1
        if self.fail:
            raise RuntimeError("INJECTED_PROVIDER_CRASH")
        return ToolOutcome(True, f"TOOL-{self.calls}", "lane-e-fake")


class Verifier:
    async def verify(self, current, proposal, tool_outcome):
        receipts = [
            EvidenceReceipt(
                "VERIFY-" + proposal.action_id,
                EvidenceKind.VERIFICATION_PASS,
                True,
                "lane-e-verifier",
                claim_scope=proposal.action_id,
                origin=EvidenceOrigin.VERIFIER,
            ),
            EvidenceReceipt(
                "GATE-" + proposal.action_id,
                EvidenceKind.ACCEPTANCE_GATE_PASS,
                True,
                "lane-e-verifier",
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
                    "lane-e-provider-readback",
                    claim_scope=proposal.action_id,
                    origin=EvidenceOrigin.PROVIDER,
                )
            )
        return VerificationOutcome(True, tuple(receipts))


async def make_service(env, tmp_path, broker, task_queue):
    activities = ExecutionActivities(broker, Verifier())
    service = ProductionExecutionService(
        gateway=gateway(),
        starter=TemporalWorkflowStarter(env.client, task_queue=task_queue),
        attestor=CompletionAttestor(SECRET),
        completion_store=SQLiteAuthoritativeCompletionStore(
            str(tmp_path / (task_queue + "-completion.sqlite"))
        ),
    )
    return activities, service


def worker(env, task_queue, activities):
    return Worker(
        env.client,
        task_queue=task_queue,
        workflows=[HaoExecutionControlWorkflow],
        activities=[
            activities.preflight_authority,
            activities.execute_tool,
            activities.verify_outcome,
        ],
    )


def test_duplicate_hao_authorization_signal_does_not_duplicate_effect(tmp_path):
    async def scenario():
        broker = Broker()
        queue = "lane-e-dup-signal-" + str(uuid.uuid4())
        async with await WorkflowEnvironment.start_time_skipping() as env:
            activities, service = await make_service(env, tmp_path, broker, queue)
            async with worker(env, queue, activities):
                run_id = "RUN-DUP-SIGNAL-" + str(uuid.uuid4())
                submission = await service.submit(state(), external_request(run_id))
                assert submission.accepted is True
                scope = "SEND_EXTERNAL:recipient-1"
                await submission.pending.handle.signal(
                    HaoExecutionControlWorkflow.authorization,
                    ApprovalSignal(scope, True, "Hao approved exact scope"),
                )
                await submission.pending.handle.signal(
                    HaoExecutionControlWorkflow.authorization,
                    ApprovalSignal(scope, True, "duplicate delivery"),
                )
                result = await service.finalize(
                    submission.pending,
                    issued_at="2026-09-05T14:00:00+08:00",
                )
                return result, broker.calls

    result, calls = asyncio.run(scenario())
    assert result.authoritative is True
    assert result.record.phase == RunPhase.CLOSED
    assert calls == 1


def test_out_of_order_unrelated_signal_cannot_unlock_effect(tmp_path):
    async def scenario():
        broker = Broker()
        queue = "lane-e-ooo-signal-" + str(uuid.uuid4())
        async with await WorkflowEnvironment.start_time_skipping() as env:
            activities, service = await make_service(env, tmp_path, broker, queue)
            async with worker(env, queue, activities):
                run_id = "RUN-OOO-SIGNAL-" + str(uuid.uuid4())
                submission = await service.submit(state(), external_request(run_id))
                await submission.pending.handle.signal(
                    HaoExecutionControlWorkflow.authorization,
                    ApprovalSignal("UNRELATED_SCOPE", True, "out of order"),
                )
                await asyncio.sleep(0)
                assert broker.calls == 0
                await service.authorize(
                    submission.pending,
                    scope="SEND_EXTERNAL:recipient-1",
                    approved=True,
                    reason="Hao approved exact scope",
                )
                result = await service.finalize(
                    submission.pending,
                    issued_at="2026-09-05T14:01:00+08:00",
                )
                return result, broker.calls

    result, calls = asyncio.run(scenario())
    assert result.authoritative is True
    assert calls == 1


def test_worker_restart_preserves_workflow_identity_and_executes_once(tmp_path):
    async def scenario():
        broker = Broker()
        queue = "lane-e-worker-restart-" + str(uuid.uuid4())
        async with await WorkflowEnvironment.start_time_skipping() as env:
            activities, service = await make_service(env, tmp_path, broker, queue)
            run_id = "RUN-WORKER-RESTART-" + str(uuid.uuid4())
            first_worker = worker(env, queue, activities)
            async with first_worker:
                submission = await service.submit(state(), external_request(run_id))
                assert submission.pending.handle.workflow_id == run_id
            assert broker.calls == 0

            second_worker = worker(env, queue, activities)
            async with second_worker:
                resumed = await service.resume(run_id, operational_version=state().version)
                assert resumed.handle.workflow_id == run_id
                await service.authorize(
                    resumed,
                    scope="SEND_EXTERNAL:recipient-1",
                    approved=True,
                    reason="Hao approved after worker replacement",
                )
                result = await service.finalize(
                    resumed,
                    issued_at="2026-09-05T14:02:00+08:00",
                )
                return result, broker.calls

    result, calls = asyncio.run(scenario())
    assert result.authoritative is True
    assert result.record.phase == RunPhase.CLOSED
    assert calls == 1


def test_two_workers_same_queue_do_not_duplicate_one_child_effect(tmp_path):
    async def scenario():
        broker = Broker()
        queue = "lane-e-two-workers-" + str(uuid.uuid4())
        async with await WorkflowEnvironment.start_time_skipping() as env:
            activities, service = await make_service(env, tmp_path, broker, queue)
            worker_a = worker(env, queue, activities)
            worker_b = worker(env, queue, activities)
            async with worker_a, worker_b:
                run_id = "RUN-TWO-WORKERS-" + str(uuid.uuid4())
                result = await service.execute(
                    state(),
                    read_request(run_id),
                    issued_at="2026-09-05T14:03:00+08:00",
                )
                return result, broker.calls

    result, calls = asyncio.run(scenario())
    assert result.authoritative is True
    assert result.record.phase == RunPhase.CLOSED
    assert calls == 1


def test_consequential_activity_failure_is_not_automatically_retried_across_workers(tmp_path):
    async def scenario():
        broker = Broker(fail=True)
        queue = "lane-e-no-retry-" + str(uuid.uuid4())
        async with await WorkflowEnvironment.start_time_skipping() as env:
            activities, service = await make_service(env, tmp_path, broker, queue)
            worker_a = worker(env, queue, activities)
            worker_b = worker(env, queue, activities)
            async with worker_a, worker_b:
                run_id = "RUN-NO-RETRY-" + str(uuid.uuid4())
                submission = await service.submit(state(), external_request(run_id))
                await service.authorize(
                    submission.pending,
                    scope="SEND_EXTERNAL:recipient-1",
                    approved=True,
                    reason="Hao approved exact scope",
                )
                result = await service.finalize(
                    submission.pending,
                    issued_at="2026-09-05T14:04:00+08:00",
                )
                return result, broker.calls

    result, calls = asyncio.run(scenario())
    assert result.authoritative is False
    assert result.record.phase == RunPhase.FAILED
    assert calls == 1


class SqlitePostgresCompatConnection:
    def __init__(self, path: Path):
        self._conn = sqlite3.connect(path, timeout=30, isolation_level=None)
        self._conn.row_factory = sqlite3.Row

    def execute(self, sql, params=()):
        normalized = sql.strip()
        if normalized == "BEGIN ISOLATION LEVEL SERIALIZABLE":
            return self._conn.execute("BEGIN IMMEDIATE")
        normalized = normalized.replace(" FOR UPDATE", "").replace("%s", "?")
        return self._conn.execute(normalized, params)

    def close(self):
        self._conn.close()


def parent_store(tmp_path):
    db = tmp_path / "lane-e-parent.sqlite3"
    factory = lambda: SqlitePostgresCompatConnection(db)
    return PostgresParentTaskStore(
        "postgresql://runtime-v2/lane-e",
        connect_factory=factory,
    )


def parent_plan(*, acceptance=False):
    return ParentTaskPlanCatalog(
        [
            ParentTaskPlan(
                plan_id="lane-e.parent",
                task="Lane E distributed verification",
                children=(ParentChildPlan("child", "research_read", "research.read"),),
                hao_acceptance_required=acceptance,
            )
        ]
    )


class NullCurrentProduction:
    async def resume(self, workflow_id, *, operational_version):
        return object()

    async def current_state(self, pending):
        return None


@pytest.mark.xfail(
    strict=True,
    reason="Known Lane E seam: missing child workflow state is silently treated as RUNNING instead of UNKNOWN/reconciliation",
)
def test_missing_child_state_must_be_loud_reconciliation_not_silent_running(tmp_path):
    store = parent_store(tmp_path)
    service = ProductionParentTaskService(
        production=NullCurrentProduction(),
        store=store,
        plans=parent_plan(),
    )
    opened = service.start(state(), plan_id="lane-e.parent", task_run_id="TASK-UNKNOWN")
    assert opened.phase == ParentTaskPhase.OPEN
    store.bind_workflow(
        task_run_id="TASK-UNKNOWN",
        slot_index=0,
        workflow_id="TASK-UNKNOWN:C001",
        operational_version=state().version,
    )

    refreshed = asyncio.run(service.refresh(state(), task_run_id="TASK-UNKNOWN"))
    assert refreshed.phase == ParentTaskPhase.RECONCILIATION_REQUIRED
    assert refreshed.failure_code == "CHILD_STATE_UNKNOWN"


@pytest.mark.xfail(
    strict=True,
    reason="Known Lane E seam: parent store has no row revision/CAS so a stale multi-instance save can erase Hao acceptance or gate state",
)
def test_stale_parent_writer_cannot_erase_newer_hao_acceptance(tmp_path):
    store = parent_store(tmp_path)
    service = ProductionParentTaskService(
        production=NullCurrentProduction(),
        store=store,
        plans=parent_plan(acceptance=True),
    )
    service.start(state(), plan_id="lane-e.parent", task_run_id="TASK-CAS")
    stale = store.get("TASK-CAS")
    assert stale is not None

    accepted = service.record_hao_acceptance(task_run_id="TASK-CAS", accepted=True)
    assert accepted.hao_accepted is True

    with pytest.raises(ValueError, match="PARENT_TASK_CHANGED_OR_MISSING"):
        store.save(replace(stale, phase=ParentTaskPhase.RUNNING))
    assert store.get("TASK-CAS").hao_accepted is True
