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
        return item

    item = asyncio.run(scenario())
    all_attributes = [attributes for _, attributes in item.tracer.spans]
    assert any(attrs.get("hao.run.id") == "RUN-1" for attrs in all_attributes)
    serialized = repr(all_attributes)
    assert "private content" not in serialized
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
