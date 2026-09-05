import asyncio
from dataclasses import replace
from types import SimpleNamespace

import pytest

from src.action_catalog import ModelActionIntent
from src.control_gateway import ModelIngressRequest
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
from src.reconciliation import (
    ReconciliationCase,
    ReconciliationDisposition,
    ReconciliationEvidence,
    ReconciliationEvidenceKind,
    ReconciliationKind,
    ReconciliationPhase,
    add_reconciliation_evidence,
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
        self.submit_calls = 0

    async def submit(self, state, request):
        self.submit_calls += 1
        workflow_id = request.run_id
        authorization_scope = ""
        phase = RunPhase.CLOSED
        if request.intent.authorization_target:
            authorization_scope = "SEND_EXTERNAL:" + request.intent.authorization_target
            phase = RunPhase.AWAITING_HAO
        action = ActionProposal(
            action_id=workflow_id + ":A0001:" + request.intent.binding_id,
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
            "AUTHORITATIVE_COMPLETION_COMMITTED"
            if current.phase == RunPhase.CLOSED
            else "NOT_CLOSED",
        )


class FakeParentTasks:
    def __init__(self, production):
        self.production = production

    def start(self, state, *, plan_id):
        assert plan_id == "parent.send"
        assert state.task == "MCP controlled task"
        return SimpleNamespace(
            task_run_id="TASK-PARENT-1",
            phase=SimpleNamespace(value="OPEN"),
            child_slots=(SimpleNamespace(slot_id="send"),),
        )

    async def submit_child(
        self,
        state,
        *,
        task_run_id,
        slot_id,
        expected_state_delta="",
        arguments=None,
    ):
        assert task_run_id == "TASK-PARENT-1"
        assert slot_id == "send"
        run_id = task_run_id + ":C001"
        submission = await self.production.submit(
            state,
            ModelIngressRequest(
                run_id=run_id,
                sequence=1,
                intent=ModelActionIntent(
                    intent_id="INTENT-PARENT-1",
                    requested_capability="external_message",
                    binding_id="message.send",
                    expected_state_delta=expected_state_delta,
                    authorization_target="recipient-parent",
                    arguments=tuple(
                        (str(k), str(v)) for k, v in (arguments or {}).items()
                    ),
                ),
            ),
        )
        return SimpleNamespace(
            task_run_id=task_run_id,
            slot_id=slot_id,
            workflow_id=submission.pending.handle.workflow_id,
            action_id=run_id + ":A0001:message.send",
            accepted=True,
            code=submission.code,
        )


class FakeReconciliationStore:
    def __init__(self):
        self.cases = {}

    def get(self, case_id):
        return self.cases.get(case_id)

    def save(self, case):
        self.cases[case.case_id] = case
        return case


class FakeRetryStore:
    def __init__(self):
        self.reservations = {}

    def reserve(self, *, case, owner_ref, request_fingerprint):
        prior = self.reservations.get(case.case_id)
        if prior is not None:
            prior_owner, prior_fingerprint, run_id = prior
            if prior_owner != owner_ref:
                raise PermissionError("RECONCILIATION_RETRY_OWNER_CONFLICT")
            if prior_fingerprint != request_fingerprint:
                raise ValueError("RECONCILIATION_RETRY_REQUEST_CONFLICT")
            return SimpleNamespace(retry_run_id=run_id, created=False)
        run_id = "RUN-RETRY-" + case.case_id
        self.reservations[case.case_id] = (owner_ref, request_fingerprint, run_id)
        return SimpleNamespace(retry_run_id=run_id, created=True)


def setup_bridge(
    tmp_path,
    *,
    with_parent_tasks=False,
    reconciliation_store=None,
    reconciliation_retry_store=None,
):
    state_store = SQLiteOperationalStateStore(str(tmp_path / "state.sqlite"))
    state_store.initialize(mode=Mode.EXP, task="MCP controlled task")
    production = FakeProduction()
    bridge = MCPControlBridge(
        production=production,
        operational_state=state_store,
        run_registry=SQLiteMCPRunRegistry(str(tmp_path / "mcp-runs.sqlite")),
        identity_policy=HaoMCPIdentityPolicy("hao-subject"),
        parent_tasks=FakeParentTasks(production) if with_parent_tasks else None,
        reconciliation_store=reconciliation_store,
        reconciliation_retry_store=(
            reconciliation_retry_store
            if reconciliation_retry_store is not None
            else (FakeRetryStore() if reconciliation_store is not None else None)
        ),
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
                hao("hao:approve"),
                workflow_id=view.workflow_id,
                scope=view.authorization_scope,
                approved=True,
                human_confirmed=False,
            )
        assert production.signals == []

        with pytest.raises(PermissionError, match="AUTHORIZATION_SCOPE_MISMATCH"):
            await bridge.authorize_after_human_confirmation(
                hao("hao:approve"),
                workflow_id=view.workflow_id,
                scope="SEND_EXTERNAL:someone-else",
                approved=True,
                human_confirmed=True,
            )
        assert production.signals == []

        status = await bridge.authorize_after_human_confirmation(
            hao("hao:approve"),
            workflow_id=view.workflow_id,
            scope=view.authorization_scope,
            approved=True,
            reason="confirmed by elicitation",
            human_confirmed=True,
        )
        assert len(production.signals) == 1
        assert status.phase == RunPhase.CLOSED.value

    asyncio.run(scenario())


def test_parent_child_registers_shared_workflow_and_reuses_existing_authorization_path(tmp_path):
    bridge, production = setup_bridge(tmp_path, with_parent_tasks=True)

    async def scenario():
        opened = bridge.parent_start(hao("hao:execute"), plan_id="parent.send")
        assert opened.task_run_id == "TASK-PARENT-1"
        assert opened.phase == "OPEN"
        assert opened.child_slots == ("send",)

        child = await bridge.parent_submit_child(
            hao("hao:execute"),
            task_run_id=opened.task_run_id,
            slot_id="send",
            expected_state_delta="send one parent-controlled message",
        )
        assert child.accepted is True
        assert child.workflow_id == "TASK-PARENT-1:C001"
        assert child.phase == RunPhase.AWAITING_HAO.value
        assert child.authorization_scope == "SEND_EXTERNAL:recipient-parent"

        status = await bridge.authorize_after_human_confirmation(
            hao("hao:approve"),
            workflow_id=child.workflow_id,
            scope=child.authorization_scope,
            approved=True,
            human_confirmed=True,
        )
        assert status.phase == RunPhase.CLOSED.value
        assert production.signals == [
            (
                "TASK-PARENT-1:C001",
                "SEND_EXTERNAL:recipient-parent",
                True,
                "",
            )
        ]

    asyncio.run(scenario())


def test_reconciliation_is_owner_bound_evidence_gated_and_retry_is_stable(tmp_path):
    reconciliation = FakeReconciliationStore()
    retry_store = FakeRetryStore()
    bridge, production = setup_bridge(
        tmp_path,
        reconciliation_store=reconciliation,
        reconciliation_retry_store=retry_store,
    )

    async def scenario():
        original = await bridge.submit(
            hao("hao:execute"),
            requested_capability="external_message",
            binding_id="message.send",
            expected_state_delta="old expected state",
            authorization_target="recipient-1",
        )
        original_record = production.records[original.workflow_id]
        production.records[original.workflow_id] = replace(
            original_record,
            phase=RunPhase.UNSYNCED,
            failure_code="UNKNOWN_EFFECT_REQUIRES_RECONCILIATION",
        )
        case = ReconciliationCase(
            case_id="RECON-MCP-1",
            run_id=original.workflow_id,
            action_id=original_record.action.action_id,
            kind=ReconciliationKind.UNKNOWN_EFFECT,
            effect_may_have_occurred=True,
        )
        reconciliation.save(case)

        inspected = bridge.reconciliation_inspect(hao("hao:read"), case_id=case.case_id)
        assert inspected.phase == ReconciliationPhase.OPEN.value
        assert inspected.effect_may_have_occurred is True

        with pytest.raises(ValueError, match="RECONCILIATION_NOT_RESOLVED"):
            await bridge.reconciliation_retry_with_delta(
                hao("hao:execute"),
                case_id=case.case_id,
                expected_state_delta="new expected state",
            )

        with pytest.raises(PermissionError, match="HUMAN_CONFIRMATION_REQUIRED"):
            bridge.reconciliation_resolve_after_human_confirmation(
                hao("hao:approve"),
                case_id=case.case_id,
                disposition="ADOPT_VERIFIED_STATE",
                human_confirmed=False,
            )

        insufficient = bridge.reconciliation_resolve_after_human_confirmation(
            hao("hao:approve"),
            case_id=case.case_id,
            disposition="ADOPT_VERIFIED_STATE",
            human_confirmed=True,
        )
        assert insufficient.phase == ReconciliationPhase.OPEN.value
        assert insufficient.resolution_code == "ADOPTION_REQUIRES_VERIFIED_READBACK"

        verified = reconciliation.get(case.case_id)
        verified = add_reconciliation_evidence(
            verified,
            ReconciliationEvidence(
                "READBACK-1",
                ReconciliationEvidenceKind.STATE_READBACK,
                True,
                "trusted-provider-readback",
            ),
        )
        verified = add_reconciliation_evidence(
            verified,
            ReconciliationEvidence(
                "VERIFY-1",
                ReconciliationEvidenceKind.VERIFICATION_PASS,
                True,
                "runtime-verifier",
            ),
        )
        reconciliation.save(verified)

        resolved = bridge.reconciliation_resolve_after_human_confirmation(
            hao("hao:approve"),
            case_id=case.case_id,
            disposition=ReconciliationDisposition.ADOPT_VERIFIED_STATE.value,
            human_confirmed=True,
        )
        assert resolved.phase == ReconciliationPhase.RESOLVED.value

        with pytest.raises(ValueError, match="RECONCILIATION_RETRY_REQUIRES_CHANGED_DELTA"):
            await bridge.reconciliation_retry_with_delta(
                hao("hao:execute"),
                case_id=case.case_id,
                expected_state_delta="old expected state",
            )

        with pytest.raises(PermissionError, match="RECONCILIATION_AUTHORIZATION_TARGET_MISMATCH"):
            await bridge.reconciliation_retry_with_delta(
                hao("hao:execute"),
                case_id=case.case_id,
                expected_state_delta="new expected state",
                authorization_target="recipient-2",
            )

        retried = await bridge.reconciliation_retry_with_delta(
            hao("hao:execute"),
            case_id=case.case_id,
            expected_state_delta="new expected state",
        )
        retried_record = production.records[retried.workflow_id]
        assert retried.workflow_id != original.workflow_id
        assert retried.phase == RunPhase.AWAITING_HAO.value
        assert retried_record.action.capability == original_record.action.capability
        assert retried_record.action.action_id.endswith(":message.send")
        assert retried_record.action.expected_state_delta == "new expected state"
        assert retried.authorization_scope == "SEND_EXTERNAL:recipient-1"

        duplicate = await bridge.reconciliation_retry_with_delta(
            hao("hao:execute"),
            case_id=case.case_id,
            expected_state_delta="new expected state",
        )
        assert duplicate.workflow_id == retried.workflow_id
        assert duplicate.code == "RECONCILIATION_RETRY_ALREADY_SUBMITTED"
        assert production.submit_calls == 2  # original + exactly one retry run

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
