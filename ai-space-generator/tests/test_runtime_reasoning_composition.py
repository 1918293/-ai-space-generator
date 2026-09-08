import json

import pytest

from src.action_catalog import ActionBinding, ActionCatalog, ModelActionIntent
from src.context_bound_reasoning import AdmittedContextItem
from src.control_gateway import ControlPlaneGateway, TaskExecutionPolicy
from src.execution_control import ActionArchetype, ActionExternality, Mode
from src.operational_state import ActiveOperationalState
from src.runtime_reasoning_composition import (
    build_runtime_reasoning_consumer,
    load_context_reasoning_routes_json,
)


TASK = "Runtime v2 formal persistence"


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
                    "range_a1": "01_Intake!A5192:V5192",
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
    def __init__(self, task=TASK):
        self.current = ActiveOperationalState(Mode.EXP, task, 188, "EVENT-188")

    def get(self):
        return self.current


class Reader:
    def __init__(self):
        self.reads = []
        self.versions = []

    def read_range(self, spreadsheet_id, range_a1):
        self.reads.append((spreadsheet_id, range_a1))
        return [[range_a1, "fresh canonical semantic"]]

    def source_version(self, file_id):
        self.versions.append(file_id)
        return "provider-version-188"


class Model:
    def __init__(self):
        self.calls = []

    def invoke(self, model_input):
        self.calls.append(model_input)
        refs = tuple(item.ref for item in model_input.admitted_context)
        assert refs == (
            "CURRENT:A540",
            "PR17:HEAD",
            "FAIL:LEGACY",
            "REG:R98",
        )
        return ModelActionIntent(
            "INTENT-RUNTIME-COMPOSITION",
            "formal_persistence",
            "formal.persist",
            model_reported_used_refs=("CURRENT:A540", "PR17:HEAD"),
        )


class Policy:
    def resolve(self, state):
        assert state.task == TASK
        return TaskExecutionPolicy(
            goal_valid=True,
            acceptance_criteria=("context-bound action selection",),
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


def test_runtime_composition_forces_raw_user_turn_through_fresh_semantics_before_action_selection():
    reader = Reader()
    model = Model()
    consumer = build_runtime_reasoning_consumer(
        values(),
        state_source=StateSource(),
        control_plane=control_plane(),
        reader=reader,
        model=model,
    )

    result = consumer.prepare_user_turn(
        "Auto > continue",
        run_id="RUN-188",
        event_id="EVENT-188",
    )

    assert result.code == "MODEL_INTENT_RESOLVED_TO_TRUSTED_BINDING"
    assert result.action_selected is True
    assert result.action_id == "RUN-188:A0001:formal.persist"
    assert len(model.calls) == 1
    assert len(reader.reads) == 4
    assert reader.versions == ["sheet-main"] * 4
    model_input = model.calls[0]
    assert model_input.receipt.task == TASK
    assert model_input.receipt.operational_version == 188
    assert model_input.receipt.checkpoint_id == "R188"
    assert model_input.user_text == "Auto > continue"


def test_route_ref_without_semantic_source_fails_at_composition_time():
    config = values()
    route = json.loads(config["HAO_CONTEXT_REASONING_ROUTES_JSON"])
    route[0]["regression_refs"] = ["REG:MISSING"]
    config["HAO_CONTEXT_REASONING_ROUTES_JSON"] = json.dumps(route)

    with pytest.raises(ValueError, match="CONTEXT_ROUTE_SEMANTIC_SOURCE_MISSING:REG:MISSING"):
        build_runtime_reasoning_consumer(
            config,
            state_source=StateSource(),
            control_plane=control_plane(),
            reader=Reader(),
            model=Model(),
        )


def test_unconfigured_runtime_task_fails_before_semantic_read_or_model():
    reader = Reader()
    model = Model()
    consumer = build_runtime_reasoning_consumer(
        values(),
        state_source=StateSource("PROJECT_ARIEL unrelated task"),
        control_plane=control_plane(),
        reader=reader,
        model=model,
    )

    result = consumer.prepare_user_turn("Auto", run_id="RUN-OTHER")

    assert result.code == "PRE_MODEL_CURRENT_UNRESOLVED"
    assert result.action_selected is False
    assert reader.reads == []
    assert model.calls == []


def test_route_config_rejects_non_exact_and_duplicate_task_metadata():
    with pytest.raises(ValueError, match="CONTEXT_REASONING_ROUTE_TASK_EXACT_REQUIRED"):
        load_context_reasoning_routes_json(
            json.dumps([{"task": " task ", "authority_refs": ["CURRENT:A540"]}])
        )

    with pytest.raises(ValueError, match="CONTEXT_REASONING_ROUTE_TASK_DUPLICATE"):
        load_context_reasoning_routes_json(
            json.dumps(
                [
                    {"task": TASK, "authority_refs": ["CURRENT:A540"]},
                    {"task": TASK, "authority_refs": ["CURRENT:A540"]},
                ]
            )
        )