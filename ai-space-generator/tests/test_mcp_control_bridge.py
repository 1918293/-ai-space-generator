import asyncio
from dataclasses import replace

import pytest

from src.execution_control import (
    ActionArchetype,
    ActionExternality,
    ActionProposal,
    ExecutionRecord,
    Mode,
    RunPhase,
)
from src.mcp_control_bridge import (
    HaoMCPIdentityPolicy,
    MCPControlBridge,
    MCPPrincipal,
    SQLiteMCPRunRegistry,
)
from src.operational_state import SQLiteOperationalStateStore
from src.production_execution import (
    PendingControlledRun,
    ProductionExecutionResult,
    ProductionSubmissionResult,
)


class Handle:
    def __init__(self, workflow_id):
        self._workflow_id = workflow_id

    @property
    def workflow_id(self):
        return self._workflow_id


class FakeProduction:
    def __init__(self):
        self.records = {}
        self.signals = []

    async def submit(self, state, request):
        workflow_id = request.run_id
        authorization_scope = ""
        phase = RunPhase.CLOSED
        if request.intent.authorization_target:
            authorization_scope = "SEND_EXTERNAL:" + request.intent.authorization_target
            phase = RunPhase.AWAITING_HAO
        action = ActionProposal(
            action_id=workflow_id + ":A0001",
            archetype=ActionArchetype.PUBLISH if authorization_scope else ActionArchetype.READ,
            externality=(
                ActionExternality.EXTERNAL_REVERSIBLE
                if authorization_scope
                else ActionExternality.READ_ONLY
            ),
            capability=request.intent.requested_capability,
            provider="fake",
            action_name="run",
            expected_state_delta=request.intent.expected_state_delta,
            authorization_scope=authorization_scope,
            idempotency_key=workflow_id if authorization_scope else "",
        )
        record = ExecutionRecord(
            run_id=workflow_id,
            task=state.task,
            mode=state.mode,
            goal_valid=True,
            acceptance_criteria=("verified",),
            phase=phase,
            action=action,
        )
        self.records[workflow_id] = record
        pending = PendingControlledRun(Handle(workflow_id), state.version)
        return ProductionSubmissionResult(pending, record, True, "CONTROLLED_RUN_SUBMITTED")

    async def resume(self, workflow_id, *, operational_version):
        return PendingControlledRun(Handle(workflow_id), operational_version)

    async def current_state(self, pending):
        return self.records[pending.handle.workflow_id]

    async def authorize(self, pending, *, scope, approved, reason=""):
        workflow_id = pending.handle.workflow_id
        self.signals.append((workflow_id, scope, approved, reason))
        current = self.records[workflow_id]
        self.records[workflow_id] = replace(
            current,
            phase=RunPhase.CLOSED if approved else RunPhase.BLOCKED,
        )

    async def finalize(self, pending, *, issued_at):
        current = self.records[pending.handle.workflow_id]
        return ProductionExecutionResult(
            current,
            current.phase == RunPhase.CLOSED,
            "AUTHORITATIVE_COMPLETION_COMMITTED" if current.phase == RunPhase.CLOSED else "NOT_CLOSED",
        )


def setup_bridge(tmp_path):
    state_store = SQLiteOperationalStateStore(str(tmp_path / "state.sqlite"))
    state_store.initialize(mode=Mode.EXP, task="MCP controlled task")
    production = FakeProduction()
    bridge = MCPControlBridge(
        production=production,
        operational_state=state_store,
        run_registry=SQLiteMCPRunRegistry(str(tmp_path / "mcp-runs.sqlite")),
        identity_policy=HaoMCPIdentityPolicy("hao-subject"),
    )
    return bridge, production


def hao(*scopes):
    return MCPPrincipal("hao-subject", frozenset(scopes))


def test_bridge_requires_exact_hao_identity_and_scope(tmp_path):
    bridge, _ = setup_bridge(tmp_path)
    with pytest.raises(PermissionError, match="HAO_IDENTITY_REQUIRED"):
        bridge.operational_context(MCPPrincipal("other", frozenset({"hao:read"})))
    with pytest.raises(PermissionError, match="MISSING_SCOPE:hao:read"):
        bridge.operational_context(hao())


def test_submit_uses_runtime_owned_mode_task_and_server_generated_run_identity(tmp_path):
    bridge, production = setup_bridge(tmp_path)

    async def scenario():
        view = await bridge.submit(
            hao("hao:execute"),
            requested_capability="research_read",
            binding_id="research.read",
        )
        assert view.workflow_id.startswith("RUN-MCP-")
        assert view.mode == "EXP"
        assert view.task == "MCP controlled task"
        assert production.records[view.workflow_id].mode == Mode.EXP
        assert production.records[view.workflow_id].task == "MCP controlled task"

    asyncio.run(scenario())


def test_workflow_identity_is_bound_to_authenticated_subject(tmp_path):
    bridge, _ = setup_bridge(tmp_path)

    async def scenario():
        view = await bridge.submit(
            hao("hao:execute"),
            requested_capability="research_read",
            binding_id="research.read",
        )
        with pytest.raises(PermissionError, match="HAO_IDENTITY_REQUIRED"):
            await bridge.status(
                MCPPrincipal("other", frozenset({"hao:read"})),
                workflow_id=view.workflow_id,
            )

    asyncio.run(scenario())


def test_authorization_requires_human_confirmation_and_exact_runtime_scope(tmp_path):
    bridge, production = setup_bridge(tmp_path)

    async def scenario():
        view = await bridge.submit(
            hao("hao:execute"),
            requested_capability="external_message",
            binding_id="message.send",
            expected_state_delta="send one message",
            authorization_target="recipient-1",
        )
        assert view.phase == RunPhase.AWAITING_HAO.value
        assert view.authorization_scope == "SEND_EXTERNAL:recipient-1"

        with pytest.raises(PermissionError, match="HUMAN_CONFIRMATION_REQUIRED"):
            await bridge.authorize_after_human_confirmation(
                hao("hao:approve", "hao:read"),
                workflow_id=view.workflow_id,
                scope=view.authorization_scope,
                approved=True,
                human_confirmed=False,
            )
        assert production.signals == []

        with pytest.raises(PermissionError, match="AUTHORIZATION_SCOPE_MISMATCH"):
            await bridge.authorize_after_human_confirmation(
                hao("hao:approve", "hao:read"),
                workflow_id=view.workflow_id,
                scope="SEND_EXTERNAL:someone-else",
                approved=True,
                human_confirmed=True,
            )
        assert production.signals == []

        status = await bridge.authorize_after_human_confirmation(
            hao("hao:approve", "hao:read"),
            workflow_id=view.workflow_id,
            scope=view.authorization_scope,
            approved=True,
            reason="confirmed by elicitation",
            human_confirmed=True,
        )
        assert len(production.signals) == 1
        assert status.phase == RunPhase.CLOSED.value

    asyncio.run(scenario())


def test_finalize_requires_owned_terminal_run_and_execute_scope(tmp_path):
    bridge, _ = setup_bridge(tmp_path)

    async def scenario():
        view = await bridge.submit(
            hao("hao:execute"),
            requested_capability="research_read",
            binding_id="research.read",
        )
        with pytest.raises(PermissionError, match="MISSING_SCOPE:hao:execute"):
            await bridge.finalize(hao("hao:read"), workflow_id=view.workflow_id)
        result = await bridge.finalize(
            hao("hao:execute"),
            workflow_id=view.workflow_id,
        )
        assert result.authoritative is True
        assert result.code == "AUTHORITATIVE_COMPLETION_COMMITTED"

    asyncio.run(scenario())
