import json

from src.active_work_identity_admission import (
    ActiveWorkIdentityAdmission,
    ActiveWorkIdentityDisposition,
)
from src.context_bound_reasoning import ContextBoundModelInput
from src.execution_control import Mode
from src.operational_state import ActiveOperationalState
from src.runtime_reasoning_composition import build_runtime_reasoning_consumer


TASK = "Requirement Verification production composition"
REF = "REQUIREMENTS:RV-051"


def values():
    return {
        "HAO_REASONING_MODEL": "not-called",
        "HAO_CONTEXT_REASONING_ROUTES_JSON": json.dumps(
            [
                {
                    "task": TASK,
                    "authority_refs": [REF],
                    "reuse_disposition": "ADMIT",
                }
            ]
        ),
        "HAO_CANONICAL_SEMANTIC_SOURCES_JSON": json.dumps(
            [
                {
                    "ref": REF,
                    "kind": "CURRENT_CONTROL",
                    "spreadsheet_id": "requirements-sheet",
                    "range_a1": "07_Requirement_Verification!A52:I52",
                    "source_file_id": "requirements-sheet",
                    "project_scope": "PROJECT_HAO",
                    "applicability": "APPLICABLE",
                    "disposition": "APPLY",
                }
            ]
        ),
    }


class StateSource:
    def __init__(self):
        self.current = ActiveOperationalState(Mode.EXP, TASK, 7, "EVENT-7")

    def get(self):
        return self.current


class Reader:
    def read_range(self, spreadsheet_id, range_a1):
        assert spreadsheet_id == "requirements-sheet"
        assert range_a1 == "07_Requirement_Verification!A52:I52"
        return [[
            "RV-051",
            "R-051",
            "N-302",
            "FUNCTIONAL",
            "Bind applicable controls before material action dispatch.",
            "Runtime v2 / action-admission control",
            "BYPASS + FAIL-CLOSED REGRESSION",
            "Attempt bypasses and verify fail-closed behavior before dispatch.",
            "0 sampled consequential hard-control bypasses.",
        ]]

    def source_version(self, file_id):
        assert file_id == "requirements-sheet"
        return "requirements-v7"


class CapturingActiveWorkResolver:
    def __init__(self):
        self.calls = []

    def resolve(self, *, work_key, intent_fingerprint):
        self.calls.append((work_key, intent_fingerprint))
        return ActiveWorkIdentityAdmission(
            False,
            "ACTIVE_WORK_IDENTITY_VISIBILITY_UNKNOWN",
            ActiveWorkIdentityDisposition.VISIBILITY_UNKNOWN,
        )


class ForbiddenModel:
    def __init__(self):
        self.calls = 0

    def invoke(self, model_input: ContextBoundModelInput):
        self.calls += 1
        raise AssertionError("ACTIVE_WORK_MUST_BLOCK_BEFORE_FIRST_MODEL")


class ForbiddenControlPlane:
    def prepare(self, state, request):
        raise AssertionError("ACTIVE_WORK_MUST_BLOCK_BEFORE_CONTROL_PLANE")


def test_production_composition_injects_active_work_after_rv_identity_hydration():
    active_work = CapturingActiveWorkResolver()
    model = ForbiddenModel()
    consumer = build_runtime_reasoning_consumer(
        values(),
        state_source=StateSource(),
        control_plane=ForbiddenControlPlane(),
        reader=Reader(),
        model=model,
        active_work_resolver=active_work,
    )

    result = consumer.prepare_user_turn(
        "Auto > continue bounded Requirement Verification",
        run_id="RUN-NEW",
        event_id="EVENT-RV-051",
    )

    assert result.action_selected is False
    assert result.code == "ACTIVE_WORK_IDENTITY_VISIBILITY_UNKNOWN"
    assert model.calls == 0
    assert len(active_work.calls) == 1
    work_key, intent_fingerprint = active_work.calls[0]
    assert work_key == "RV-051"
    assert intent_fingerprint.startswith("sha256:")
    assert len(intent_fingerprint) == 71
