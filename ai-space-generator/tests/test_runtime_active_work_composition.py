import json

import pytest

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
DOCUMENT_ID = "active-work-doc"


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


def active_work_index_source_json(**extra):
    payload = {
        "spreadsheet_id": "hao-system-sheet",
        "range_a1": "07_System_Index!A55:L55",
        "source_file_id": "hao-system-sheet",
    }
    payload.update(extra)
    return json.dumps(payload)


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


class CanonicalReader(Reader):
    def read_range(self, spreadsheet_id, range_a1):
        if spreadsheet_id == "hao-system-sheet":
            assert range_a1 == "07_System_Index!A55:L55"
            return [[
                "IDX-055",
                "Hao System｜Active Work Signal｜EXP_NON_AUTHORITY｜v0.1",
                DOCUMENT_ID,
                "RUNTIME_COORDINATION",
                "PERSISTENT_ACTIVE_WORK_SIGNAL",
                "NON_AUTHORITY",
                "ACTIVE",
                "DERIVED_RECORD",
                "KEEP_ACTIVE",
                f"https://docs.google.com/document/d/{DOCUMENT_ID}/edit",
                "coordination only",
                "2026-09-17T10:51:26+08:00",
            ]]
        return super().read_range(spreadsheet_id, range_a1)

    def source_version(self, file_id):
        if file_id == "hao-system-sheet":
            return "hao-system-v55"
        return super().source_version(file_id)


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


CONTROL = """HAO_ACTIVE_WORK_SIGNAL_V1
ROLE=NON_AUTHORITY_REBUILDABLE_RUNTIME_SIGNAL
PURPOSE=CROSS_CHAT_ACTIVE_WORK_COORDINATION_ONLY
FORMAL_AUTHORITY=GOOGLE_DRIVE_HAO_SYSTEM
HISTORY=NONE
PRIVATE_PAYLOAD=DENY
TASK_DATABASE=NO
WRITE_CONTROL=GOOGLE_DOC_REVISION_CAS
MAX_SLOTS=4
TTL_REQUIRED=TRUE
FULL_CAPACITY=FAIL_CLOSED_OR_WAIT
"""


def empty_slot(index):
    return f"""S{index}_STATUS=EMPTY
S{index}_GENERATION=0
S{index}_RUN_KEY=NONE
S{index}_OBJECTIVE=NONE
S{index}_TARGET=NONE
S{index}_EXECUTION_LANE=NONE
S{index}_WORK_STATE=NONE
S{index}_STARTED_AT=NONE
S{index}_UPDATED_AT=NONE
S{index}_EXPIRES_AT=NONE
S{index}_EXPECTED_DELTA=NONE
S{index}_READBACK_STATE=NONE
S{index}_OWNER=NONE
"""


def legacy_identityless_active_signal():
    s1 = """S1_STATUS=ACTIVE
S1_GENERATION=1
S1_RUN_KEY=RUN-LEGACY
S1_OBJECTIVE=OBJECTIVE_MUST_NOT_MINT_IDENTITY
S1_TARGET=TARGET_MUST_NOT_MINT_IDENTITY
S1_EXECUTION_LANE=CHATGPT_PRIVATE_LANE
S1_WORK_STATE=VERIFYING
S1_STARTED_AT=2026-09-17T11:22:28+08:00
S1_UPDATED_AT=2026-09-17T11:22:28+08:00
S1_EXPIRES_AT=2099-09-17T11:42:28+08:00
S1_EXPECTED_DELTA=BOUNDED_VERIFICATION
S1_READBACK_STATE=PENDING
S1_OWNER=CHATGPT_CURRENT_CHAT
"""
    return CONTROL + s1 + empty_slot(2) + empty_slot(3) + empty_slot(4)


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


def test_composition_auto_builds_active_work_only_through_canonical_idx055(monkeypatch):
    class FakeDocSource:
        def __init__(self, document_id):
            assert document_id == DOCUMENT_ID

        def read_text(self):
            return legacy_identityless_active_signal()

    monkeypatch.setattr(
        "src.active_work_canonical_locator.GoogleDocsActiveWorkSignalTextSource",
        FakeDocSource,
    )
    config = values()
    config["HAO_ACTIVE_WORK_INDEX_SOURCE_JSON"] = active_work_index_source_json()
    model = ForbiddenModel()
    consumer = build_runtime_reasoning_consumer(
        config,
        state_source=StateSource(),
        control_plane=ForbiddenControlPlane(),
        reader=CanonicalReader(),
        model=model,
    )

    result = consumer.prepare_user_turn(
        "Auto > continue bounded Requirement Verification",
        run_id="RUN-NEW",
        event_id="EVENT-RV-051",
    )

    assert result.action_selected is False
    assert result.code == "ACTIVE_WORK_IDENTITY_VISIBILITY_UNKNOWN"
    assert model.calls == 0


def test_composition_rejects_direct_document_id_in_active_work_locator_config():
    config = values()
    config["HAO_ACTIVE_WORK_INDEX_SOURCE_JSON"] = active_work_index_source_json(
        document_id=DOCUMENT_ID
    )

    with pytest.raises(ValueError, match="ACTIVE_WORK_INDEX_SOURCE_UNKNOWN_FIELD:document_id"):
        build_runtime_reasoning_consumer(
            config,
            state_source=StateSource(),
            control_plane=ForbiddenControlPlane(),
            reader=CanonicalReader(),
            model=ForbiddenModel(),
        )
