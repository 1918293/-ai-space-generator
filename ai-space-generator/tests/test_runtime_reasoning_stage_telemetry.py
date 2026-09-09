from contextlib import contextmanager
import json
from types import SimpleNamespace

import pytest

from src.action_catalog import ActionBinding, ActionCatalog, ModelActionIntent
from src.control_gateway import ControlPlaneGateway, TaskExecutionPolicy
from src.execution_control import ActionArchetype, ActionExternality, Mode
from src.operational_state import ActiveOperationalState
from src.runtime_observability import RuntimeTelemetry
from src.runtime_reasoning_composition import build_runtime_reasoning_consumer


TASK = "Runtime v2 reasoning telemetry"


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


def test_context_reasoning_stage_metrics_are_content_free_and_run_id_is_trace_only():
    item = telemetry()
    observation = SimpleNamespace(
        run_id="RUN-CONTEXT-HIGH-CARDINALITY",
        code="MODEL_INTENT_RESOLVED_TO_TRUSTED_BINDING",
        structural_ref_count=4,
        admitted_ref_count=3,
        presented_to_model=True,
        model_reported_used_count=2,
        deterministic_used_count=1,
        action_selected=True,
        user_text="PRIVATE HAO USER TEXT",
        summary="PRIVATE CANONICAL SEMANTIC SUMMARY",
        model_reported_used_refs=("PRIVATE:REF:1",),
        structural_fingerprint="PRIVATE-STRUCTURAL-FINGERPRINT",
        semantic_fingerprint="PRIVATE-SEMANTIC-FINGERPRINT",
    )

    item.record_context_reasoning_observation(observation)

    assert item.run_events.calls == [
        (4, {"hao.event": "context_retrieved", "hao.run.phase": "REASONING"}),
        (3, {"hao.event": "context_admitted", "hao.run.phase": "REASONING"}),
        (1, {"hao.event": "context_presented", "hao.run.phase": "REASONING"}),
        (
            2,
            {
                "hao.event": "context_model_reported_used",
                "hao.run.phase": "REASONING",
            },
        ),
        (
            1,
            {
                "hao.event": "context_deterministic_used",
                "hao.run.phase": "REASONING",
            },
        ),
        (
            1,
            {"hao.event": "context_action_selected", "hao.run.phase": "REASONING"},
        ),
    ]
    for _, attributes in item.run_events.calls:
        assert "hao.run.id" not in attributes

    assert len(item.tracer.spans) == 1
    name, attributes = item.tracer.spans[0]
    assert name == "hao.runtime.context_reasoning"
    assert attributes["hao.run.id"] == "RUN-CONTEXT-HIGH-CARDINALITY"
    assert attributes["hao.context.retrieved.count"] == 4
    assert attributes["hao.context.admitted.count"] == 3
    assert attributes["hao.context.presented"] is True
    assert attributes["hao.context.model_reported_used.count"] == 2
    assert attributes["hao.context.deterministic_used.count"] == 1
    assert attributes["hao.context.action_selected"] is True

    serialized = repr((item.run_events.calls, item.tracer.spans))
    assert "PRIVATE HAO USER TEXT" not in serialized
    assert "PRIVATE CANONICAL SEMANTIC SUMMARY" not in serialized
    assert "PRIVATE:REF:1" not in serialized
    assert "PRIVATE-STRUCTURAL-FINGERPRINT" not in serialized
    assert "PRIVATE-SEMANTIC-FINGERPRINT" not in serialized


def values():
    return {
        "HAO_REASONING_MODEL": "gpt-5.6-luna",
        "HAO_CONTEXT_REASONING_ROUTES_JSON": json.dumps(
            [
                {
                    "task": TASK,
                    "authority_refs": ["CURRENT:A540"],
                    "existing_work_refs": ["PR17:HEAD"],
                    "prior_attempt_refs": ["FAIL:LEGACY"],
                    "regression_refs": ["REG:R98"],
                    "reuse_disposition": "REUSE",
                }
            ]
        ),
        "HAO_CANONICAL_SEMANTIC_SOURCES_JSON": json.dumps(
            [
                {
                    "ref": "CURRENT:A540",
                    "kind": "CURRENT_CONTROL",
                    "spreadsheet_id": "sheet-main",
                    "range_a1": "06_Config!A540:F540",
                    "source_file_id": "sheet-main",
                    "project_scope": "PROJECT_HAO",
                    "disposition": "APPLY",
                },
                {
                    "ref": "PR17:HEAD",
                    "kind": "EXISTING_WORK",
                    "spreadsheet_id": "sheet-main",
                    "range_a1": "01_Intake!A5194:V5194",
                    "source_file_id": "sheet-main",
                    "project_scope": "PROJECT_HAO",
                    "disposition": "REUSE",
                },
                {
                    "ref": "FAIL:LEGACY",
                    "kind": "PRIOR_ATTEMPT",
                    "spreadsheet_id": "sheet-main",
                    "range_a1": "01_Intake!A5000:V5000",
                    "source_file_id": "sheet-main",
                    "project_scope": "PROJECT_HAO",
                    "disposition": "REFERENCE_ONLY",
                },
                {
                    "ref": "REG:R98",
                    "kind": "REGRESSION",
                    "spreadsheet_id": "sheet-main",
                    "range_a1": "01_Intake!A5001:V5001",
                    "source_file_id": "sheet-main",
                    "project_scope": "PROJECT_HAO",
                    "disposition": "APPLY",
                },
            ]
        ),
    }


class StateSource:
    def get(self):
        return ActiveOperationalState(Mode.EXP, TASK, 191, "EVENT-191")


class Reader:
    def read_range(self, spreadsheet_id, range_a1):
        return [[spreadsheet_id, range_a1, "fresh semantic"]]

    def source_version(self, file_id):
        return "provider-version-191"


class Model:
    def invoke(self, model_input):
        return ModelActionIntent(
            "INTENT-RUNTIME-TELEMETRY",
            "formal_persistence",
            "formal.persist",
            model_reported_used_refs=("CURRENT:A540", "PR17:HEAD"),
        )


class Policy:
    def resolve(self, state):
        return TaskExecutionPolicy(
            goal_valid=True,
            acceptance_criteria=("reasoning telemetry",),
        )


def control_plane():
    return ControlPlaneGateway(
        ActionCatalog(
            (
                ActionBinding(
                    "formal.persist",
                    "formal_persistence",
                    "google-drive",
                    "update_cells",
                    ActionArchetype.MUTATE,
                    ActionExternality.PRIVATE_REVERSIBLE,
                ),
            )
        ),
        Policy(),
    )


def test_composition_emits_exactly_one_reasoning_observation_per_user_turn():
    item = telemetry()
    consumer = build_runtime_reasoning_consumer(
        values(),
        state_source=StateSource(),
        control_plane=control_plane(),
        reader=Reader(),
        model=Model(),
        telemetry=item,
    )

    result = consumer.prepare_user_turn(
        "Auto > continue",
        run_id="RUN-191",
        event_id="EVENT-191",
    )

    assert result.action_selected is True
    reasoning_spans = [
        attributes
        for name, attributes in item.tracer.spans
        if name == "hao.runtime.context_reasoning"
    ]
    assert len(reasoning_spans) == 1
    assert reasoning_spans[0]["hao.run.id"] == "RUN-191"
    assert reasoning_spans[0]["hao.context.retrieved.count"] == 4
    assert reasoning_spans[0]["hao.context.admitted.count"] == 4
    assert reasoning_spans[0]["hao.context.presented"] is True
    assert reasoning_spans[0]["hao.context.model_reported_used.count"] == 2
    assert reasoning_spans[0]["hao.context.deterministic_used.count"] == 0
    assert reasoning_spans[0]["hao.context.action_selected"] is True

    event_names = [attributes["hao.event"] for _, attributes in item.run_events.calls]
    assert event_names.count("context_retrieved") == 1
    assert event_names.count("context_admitted") == 1
    assert event_names.count("context_presented") == 1
    assert event_names.count("context_model_reported_used") == 1
    assert event_names.count("context_deterministic_used") == 0
    assert event_names.count("context_action_selected") == 1


def test_configured_reasoning_telemetry_fails_closed_if_process_telemetry_missing(monkeypatch):
    import src.runtime_reasoning_composition as composition

    config = values()
    config["HAO_OTEL_ENDPOINT"] = "https://otel.example.com"
    monkeypatch.setattr(composition, "active_runtime_telemetry", lambda: None)

    with pytest.raises(ValueError, match="RUNTIME_REASONING_TELEMETRY_NOT_CONFIGURED"):
        build_runtime_reasoning_consumer(
            config,
            state_source=StateSource(),
            control_plane=control_plane(),
            reader=Reader(),
            model=Model(),
        )


def test_configured_reasoning_telemetry_reuses_active_runtime_instance(monkeypatch):
    import src.runtime_reasoning_composition as composition

    config = values()
    config["HAO_OTEL_ENDPOINT"] = "https://otel.example.com"
    item = telemetry()
    monkeypatch.setattr(composition, "active_runtime_telemetry", lambda: item)

    consumer = build_runtime_reasoning_consumer(
        config,
        state_source=StateSource(),
        control_plane=control_plane(),
        reader=Reader(),
        model=Model(),
    )
    consumer.prepare_user_turn("Auto", run_id="RUN-191-ACTIVE")

    reasoning_spans = [
        attributes
        for name, attributes in item.tracer.spans
        if name == "hao.runtime.context_reasoning"
    ]
    assert len(reasoning_spans) == 1
    assert reasoning_spans[0]["hao.run.id"] == "RUN-191-ACTIVE"
