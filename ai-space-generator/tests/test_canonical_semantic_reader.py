import json

import pytest

from src.canonical_semantic_reader import (
    CanonicalSemanticRangeSource,
    ConfiguredCanonicalSemanticsResolver,
    load_canonical_semantic_sources_json,
)
from src.context_bound_reasoning import ContextBoundPreModelGateway
from src.control_gateway import (
    PreModelContextGateway,
    PreModelContextRequest,
    PreModelContextResolution,
)
from src.execution_control import Mode
from src.operational_state import ActiveOperationalState, CommandActor


TASK = "Runtime v2 semantic reader"


def state():
    return ActiveOperationalState(Mode.EXP, TASK, 187, "EVENT-187")


def request():
    return PreModelContextRequest("Auto > continue", CommandActor.USER, "EVENT-187")


class StructuralResolver:
    def resolve(self, current_state, current_request, checkpoint_cue):
        assert current_state.task == TASK
        assert current_request.actor == CommandActor.USER
        assert checkpoint_cue == ""
        return PreModelContextResolution(
            checkpoint_id="R187",
            task=TASK,
            operational_version=187,
            authority_refs=("CURRENT:A540",),
            existing_work_refs=("PR17:HEAD",),
            prior_attempt_refs=("FAIL:OUT_OF_GRID_SEARCH",),
            regression_refs=("REG:NO_FALSE_COMPLETION",),
            existing_work_lookup_complete=True,
            prior_attempt_lookup_complete=True,
            regression_lookup_complete=True,
            reuse_disposition="REUSE",
        )


class RangeReader:
    def __init__(self, values=None, versions=None):
        self.values = values or {
            ("hao", "06_Config!A540:F540"): [["A540", "CURRENT", "action admission binding"]],
            ("repo", "PR17!A1:B1"): [["128138e", "reuse current control plane"]],
            ("hao", "04_Verification_Log!A5228:N5228"): [["VERIFY-1", "same-shape retry denied"]],
            ("hao", "01_Intake!A1:V1"): [["REG", "no false completion"]],
        }
        self.versions = versions or {"hao": "5190", "repo": "128138e"}
        self.reads = []
        self.version_reads = []

    def read_range(self, spreadsheet_id, range_a1):
        self.reads.append((spreadsheet_id, range_a1))
        return self.values.get((spreadsheet_id, range_a1))

    def source_version(self, file_id):
        self.version_reads.append(file_id)
        return self.versions.get(file_id, "")


def sources():
    return (
        CanonicalSemanticRangeSource(
            ref="CURRENT:A540",
            kind="CURRENT_CONTROL",
            spreadsheet_id="hao",
            range_a1="06_Config!A540:F540",
            source_file_id="hao",
            project_scope="PROJECT_HAO",
            disposition="APPLY",
        ),
        CanonicalSemanticRangeSource(
            ref="PR17:HEAD",
            kind="EXISTING_WORK",
            spreadsheet_id="repo",
            range_a1="PR17!A1:B1",
            source_file_id="repo",
            project_scope="PROJECT_HAO",
            disposition="REUSE",
        ),
        CanonicalSemanticRangeSource(
            ref="FAIL:OUT_OF_GRID_SEARCH",
            kind="PRIOR_ATTEMPT",
            spreadsheet_id="hao",
            range_a1="04_Verification_Log!A5228:N5228",
            source_file_id="hao",
            project_scope="PROJECT_HAO",
            disposition="DO_NOT_REPEAT",
            binding_id="legacy.search.unbounded",
        ),
        CanonicalSemanticRangeSource(
            ref="REG:NO_FALSE_COMPLETION",
            kind="REGRESSION",
            spreadsheet_id="hao",
            range_a1="01_Intake!A1:V1",
            source_file_id="hao",
            project_scope="PROJECT_HAO",
            disposition="APPLY",
        ),
    )


def gateway(reader, configured_sources=None):
    semantic = ConfiguredCanonicalSemanticsResolver(
        reader,
        sources() if configured_sources is None else configured_sources,
    )
    return ContextBoundPreModelGateway(
        PreModelContextGateway(StructuralResolver()),
        semantic,
    )


def test_fresh_exact_canonical_ranges_become_model_usable_semantics():
    reader = RangeReader()

    admission = gateway(reader).admit(state(), request())

    assert admission.allowed is True
    assert admission.code == "PRE_MODEL_SEMANTIC_CONTEXT_ADMITTED"
    assert admission.model_input is not None
    items = {item.ref: item for item in admission.model_input.admitted_context}
    assert set(items) == {
        "CURRENT:A540",
        "PR17:HEAD",
        "FAIL:OUT_OF_GRID_SEARCH",
        "REG:NO_FALSE_COMPLETION",
    }
    assert json.loads(items["CURRENT:A540"].summary)[0][2] == "action admission binding"
    assert items["CURRENT:A540"].source_version == "5190"
    assert items["FAIL:OUT_OF_GRID_SEARCH"].disposition == "DO_NOT_REPEAT"
    assert items["FAIL:OUT_OF_GRID_SEARCH"].binding_id == "legacy.search.unbounded"
    assert len(reader.reads) == 4
    assert len(reader.version_reads) == 4


def test_missing_configured_prior_attempt_range_fails_closed_before_model():
    configured = tuple(source for source in sources() if source.kind != "PRIOR_ATTEMPT")
    reader = RangeReader()

    admission = gateway(reader, configured).admit(state(), request())

    assert admission.allowed is False
    assert admission.code == "PRE_MODEL_SEMANTICS_UNRESOLVED"
    assert reader.reads == []


def test_provider_read_failure_fails_closed_without_partial_semantic_admission():
    reader = RangeReader()
    del reader.values[("hao", "04_Verification_Log!A5228:N5228")]

    admission = gateway(reader).admit(state(), request())

    assert admission.allowed is False
    assert admission.code == "PRE_MODEL_SEMANTICS_UNRESOLVED"
    assert admission.model_input is None


def test_overbroad_canonical_range_is_not_truncated_into_fake_semantics():
    reader = RangeReader()
    reader.values[("hao", "06_Config!A540:F540")] = [["x" * 1900]]

    admission = gateway(reader).admit(state(), request())

    assert admission.allowed is False
    assert admission.code == "PRE_MODEL_SEMANTICS_UNRESOLVED"


def test_source_version_failure_blocks_semantic_admission():
    reader = RangeReader(versions={"hao": "", "repo": "128138e"})

    admission = gateway(reader).admit(state(), request())

    assert admission.allowed is False
    assert admission.code == "PRE_MODEL_SEMANTICS_UNRESOLVED"


def test_semantic_config_contains_locations_not_canonical_content():
    raw = json.dumps(
        [
            {
                "ref": "CURRENT:A540",
                "kind": "CURRENT_CONTROL",
                "spreadsheet_id": "hao",
                "range_a1": "06_Config!A540:F540",
                "source_file_id": "hao",
                "project_scope": "PROJECT_HAO",
                "disposition": "APPLY",
            }
        ]
    )

    loaded = load_canonical_semantic_sources_json(raw)

    assert loaded == (
        CanonicalSemanticRangeSource(
            ref="CURRENT:A540",
            kind="CURRENT_CONTROL",
            spreadsheet_id="hao",
            range_a1="06_Config!A540:F540",
            source_file_id="hao",
            project_scope="PROJECT_HAO",
            disposition="APPLY",
        ),
    )


def test_duplicate_semantic_route_is_rejected():
    raw = json.dumps(
        [
            {
                "ref": "CURRENT:A540",
                "kind": "CURRENT_CONTROL",
                "spreadsheet_id": "hao",
                "range_a1": "06_Config!A540:F540",
                "source_file_id": "hao",
            },
            {
                "ref": "CURRENT:A540",
                "kind": "CURRENT_CONTROL",
                "spreadsheet_id": "other",
                "range_a1": "A1:B1",
                "source_file_id": "other",
            },
        ]
    )

    with pytest.raises(ValueError, match="CANONICAL_SEMANTIC_SOURCE_DUPLICATE"):
        load_canonical_semantic_sources_json(raw)
