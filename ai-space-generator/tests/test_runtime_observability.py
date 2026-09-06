import asyncio
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from src.runtime_observability import RuntimeTelemetry, otlp_http_signal_endpoints
from src.runtime_observability_bridge import (
    ObservableMCPControlBridge,
    ObservableReconciliationBroker,
)


class Counter:
    def __init__(self):
        self.calls = []

    def add(self, value, attributes=None):
        self.calls.append((value, dict(attributes or {})))


class Tracer:
    def __init__(self):
        self.spans = []

    @contextmanager
    def start_as_current_span(self, name, attributes=None):
        self.spans.append((name, dict(attributes or {})))
        yield SimpleNamespace()


def telemetry():
    return RuntimeTelemetry(Tracer(), Counter(), Counter(), Counter(), Counter())


def test_otlp_endpoint_resolution_supports_base_trace_or_metric_endpoint():
    assert otlp_http_signal_endpoints("https://otel.example.com") == (
        "https://otel.example.com/v1/traces",
        "https://otel.example.com/v1/metrics",
    )
    assert otlp_http_signal_endpoints("https://otel.example.com/v1/traces") == (
        "https://otel.example.com/v1/traces",
        "https://otel.example.com/v1/metrics",
    )
    assert otlp_http_signal_endpoints("https://otel.example.com/v1/metrics") == (
        "https://otel.example.com/v1/traces",
        "https://otel.example.com/v1/metrics",
    )
    with pytest.raises(ValueError, match="OTEL_ENDPOINT_REQUIRED"):
        otlp_http_signal_endpoints(" ")


def test_run_id_is_trace_only_not_high_cardinality_metric_attribute():
    item = telemetry()
    item.record_run_event(
        "status",
        run_id="RUN-SECRETLY-HIGH-CARDINALITY",
        phase="VERIFIED",
        provider="google-drive",
        failure_stage="VERIFICATION",
        failure_code="READBACK_MISMATCH",
    )
    metric_attributes = item.run_events.calls[0][1]
    failure_attributes = item.failures.calls[0][1]
    span_attributes = item.tracer.spans[0][1]

    assert "hao.run.id" not in metric_attributes
    assert "hao.run.id" not in failure_attributes
    assert span_attributes["hao.run.id"] == "RUN-SECRETLY-HIGH-CARDINALITY"
    assert "arguments" not in span_attributes
    assert "payload" not in span_attributes


class Bridge:
    async def submit(self, principal, **kwargs):
        return SimpleNamespace(
            workflow_id="RUN-1", code="CONTROLLED_RUN_STARTED", phase="ADMITTED"
        )

    async def status(self, principal, *, workflow_id):
        return SimpleNamespace(
            workflow_id=workflow_id,
            phase="FAILED",
            failure_stage="VERIFICATION",
            failure_code="READBACK_MISMATCH",
        )

    async def authorize_after_human_confirmation(self, principal, **kwargs):
        return SimpleNamespace(
            workflow_id=kwargs["workflow_id"],
            phase="RESOLVED",
            failure_stage="",
            failure_code="",
        )

    async def finalize(self, principal, *, workflow_id):
        return SimpleNamespace(
            workflow_id=workflow_id,
            authoritative=True,
            code="AUTHORITATIVE_COMPLETION_COMMITTED",
            phase="CLOSED",
        )

    def operational_context(self, principal):
        return {"mode": "EXP", "task": "test", "operational_version": 1}

    def parent_start(self, principal, *, plan_id):
        return SimpleNamespace(task_run_id="TASK-1", phase="OPEN")

    async def parent_submit_child(self, principal, **kwargs):
        return SimpleNamespace(
            task_run_id=kwargs["task_run_id"],
            workflow_id="TASK-1:C001",
            accepted=True,
            phase="AWAITING_HAO",
            code="CONTROLLED_RUN_SUBMITTED",
        )

    async def parent_refresh(self, principal, *, task_run_id):
        return SimpleNamespace(
            task_run_id=task_run_id,
            phase="RECONCILIATION_REQUIRED",
            failure_code="CHILD_RECONCILIATION_REQUIRED",
        )

    async def parent_accept_after_human_confirmation(self, principal, **kwargs):
        return SimpleNamespace(
            task_run_id=kwargs["task_run_id"],
            phase="CLOSED",
            failure_code="",
        )

    def reconciliation_inspect(self, principal, *, case_id):
        return SimpleNamespace(
            case_id=case_id,
            run_id="RUN-RECON-ORIGINAL",
            phase="OPEN",
            resolution_code="",
        )

    def reconciliation_resolve_after_human_confirmation(self, principal, **kwargs):
        return SimpleNamespace(
            case_id=kwargs["case_id"],
            run_id="RUN-RECON-ORIGINAL",
            phase="RESOLVED",
            resolution_code="ADOPTED_VERIFIED_EXTERNAL_STATE",
        )

    async def reconciliation_retry_with_delta(self, principal, **kwargs):
        return SimpleNamespace(
            workflow_id="RUN-RETRY-1",
            phase="AWAITING_HAO",
            code="CONTROLLED_RUN_SUBMITTED",
        )


def test_mcp_observer_records_state_but_never_model_arguments():
    async def scenario():
        item = telemetry()
        bridge = ObservableMCPControlBridge(Bridge(), item)
        await bridge.submit(
            object(),
            requested_capability="formal_persistence",
            binding_id="formal.write",
            arguments={"values_json": "[[\"private content\"]]"},
        )
        await bridge.status(object(), workflow_id="RUN-1")
        await bridge.finalize(object(), workflow_id="RUN-1")
        bridge.parent_start(object(), plan_id="formal.plan")
        await bridge.parent_submit_child(
            object(),
            task_run_id="TASK-1",
            slot_id="write",
            arguments={"values_json": "[[\"parent private content\"]]"},
        )
        await bridge.parent_refresh(object(), task_run_id="TASK-1")
        bridge.reconciliation_inspect(object(), case_id="RECON-1")
        bridge.reconciliation_resolve_after_human_confirmation(
            object(),
            case_id="RECON-1",
            disposition="ADOPT_VERIFIED_STATE",
            human_confirmed=True,
        )
        await bridge.reconciliation_retry_with_delta(
            object(),
            case_id="RECON-1",
            expected_state_delta="private changed delta",
            arguments={"values_json": "[[\"reconciliation private content\"]]"},
        )
        return item

    item = asyncio.run(scenario())
    all_attributes = [attributes for _, attributes in item.tracer.spans]
    assert any(attrs.get("hao.run.id") == "RUN-1" for attrs in all_attributes)
    assert any(attrs.get("hao.run.id") == "TASK-1" for attrs in all_attributes)
    assert any(attrs.get("hao.run.id") == "RUN-RECON-ORIGINAL" for attrs in all_attributes)
    assert any(attrs.get("hao.run.id") == "RUN-RETRY-1" for attrs in all_attributes)
    serialized = repr(all_attributes)
    assert "private content" not in serialized
    assert "parent private content" not in serialized
    assert "reconciliation private content" not in serialized
    assert "private changed delta" not in serialized
    assert item.authoritative_completions.calls == [(1, {})]


class ReconciliationStore:
    def __init__(self):
        self.current = None

    def get_by_action(self, action_id):
        return self.current


class BrokerOpeningOneCase:
    def __init__(self, store):
        self.store = store

    async def execute(self, proposal):
        self.store.current = SimpleNamespace(kind=SimpleNamespace(value="UNKNOWN_EFFECT"), run_id="RUN-2")
        return SimpleNamespace(
            success=False,
            error_code="PROVIDER_EXCEPTION_EFFECT_UNKNOWN",
            failure_stage=SimpleNamespace(value="PERSISTENCE"),
        )


def test_reconciliation_observer_counts_only_new_case_transition():
    async def scenario():
        item = telemetry()
        store = ReconciliationStore()
        wrapper = ObservableReconciliationBroker(BrokerOpeningOneCase(store), store, item)
        proposal = SimpleNamespace(action_id="RUN-2:A0001:x", provider="google-drive")
        await wrapper.execute(proposal)
        await wrapper.execute(proposal)
        return item

    item = asyncio.run(scenario())
    assert len(item.reconciliation_cases.calls) == 1


def test_decision_provenance_is_trace_only_and_summarizes_evidence_without_payloads():
    from src.execution_control import (
        ActionArchetype,
        ActionExternality,
        ActionProposal,
        EvidenceKind,
        EvidenceOrigin,
        EvidenceReceipt,
        ExecutionRecord,
        Mode,
        RunPhase,
    )

    action = ActionProposal(
        action_id="RUN-DECISION:A0001:formal.write",
        archetype=ActionArchetype.MUTATE,
        externality=ActionExternality.PRIVATE_REVERSIBLE,
        capability="formal_persistence",
        provider="google-drive",
        action_name="update",
        expected_state_delta="PRIVATE-EXPECTED-DELTA",
        arguments=(("payload", "PRIVATE-MODEL-ARGUMENT"),),
    )
    record = ExecutionRecord(
        run_id="RUN-DECISION-HIGH-CARDINALITY",
        task="Decision provenance telemetry",
        mode=Mode.EXP,
        goal_valid=True,
        acceptance_criteria=("verified",),
        policy_fingerprint="sha256:POLICY-HIGH-CARDINALITY",
        decision_id="DECISION:HIGH-CARDINALITY",
        phase=RunPhase.CLOSED,
        action=action,
        evidence=(
            EvidenceReceipt(
                "PRIVATE-TOOL-RECEIPT-ID",
                EvidenceKind.TOOL_RECEIPT,
                True,
                "PRIVATE-PROVIDER-SOURCE",
                claim_scope=action.action_id,
                origin=EvidenceOrigin.PROVIDER,
            ),
            EvidenceReceipt(
                "PRIVATE-READBACK-ID",
                EvidenceKind.STATE_READBACK,
                True,
                "PRIVATE-READBACK-SOURCE",
                claim_scope=action.action_id,
                origin=EvidenceOrigin.PROVIDER,
            ),
            EvidenceReceipt(
                "PRIVATE-VERIFY-ID",
                EvidenceKind.VERIFICATION_PASS,
                True,
                "PRIVATE-VERIFIER-SOURCE",
                claim_scope=action.action_id,
                origin=EvidenceOrigin.VERIFIER,
            ),
        ),
    )
    item = telemetry()
    item.record_decision_event(
        "finalization",
        record,
        admission_code="ADMITTED",
        completion_code="AUTHORITATIVE_COMPLETION_COMMITTED",
        authoritative=True,
    )

    assert item.run_events.calls == []
    assert item.failures.calls == []
    assert item.authoritative_completions.calls == []
    assert item.reconciliation_cases.calls == []

    name, attributes = item.tracer.spans[-1]
    assert name == "hao.runtime.decision.finalization"
    assert attributes["hao.run.id"] == record.run_id
    assert attributes["hao.decision.id"] == record.decision_id
    assert attributes["hao.policy.fingerprint"] == record.policy_fingerprint
    assert attributes["hao.admission.code"] == "ADMITTED"
    assert attributes["hao.completion.code"] == "AUTHORITATIVE_COMPLETION_COMMITTED"
    assert attributes["hao.completion.authoritative"] is True
    assert attributes["hao.evidence.tool_receipt.count"] == 1
    assert attributes["hao.evidence.state_readback.count"] == 1
    assert attributes["hao.evidence.verification_pass.count"] == 1

    serialized = repr(attributes)
    assert "PRIVATE-TOOL-RECEIPT-ID" not in serialized
    assert "PRIVATE-READBACK-ID" not in serialized
    assert "PRIVATE-VERIFY-ID" not in serialized
    assert "PRIVATE-PROVIDER-SOURCE" not in serialized
    assert "PRIVATE-MODEL-ARGUMENT" not in serialized
    assert "PRIVATE-EXPECTED-DELTA" not in serialized
