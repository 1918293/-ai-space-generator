import json

import pytest

from src.action_catalog import ActionBinding, ActionCatalog, ModelActionIntent
from src.context_bound_reasoning import (
    AdmittedContextItem,
    ContextBoundPreModelGateway,
    ContextBoundReasoningIngress,
)
from src.context_bound_responses import ContextBoundResponsesIntentBoundary
from src.control_gateway import (
    ControlPlaneGateway,
    PreModelContextGateway,
    PreModelContextRequest,
    PreModelContextResolution,
    TaskExecutionPolicy,
)
from src.execution_control import ActionArchetype, ActionExternality, Mode
from src.operational_state import ActiveOperationalState, CommandActor


TASK = "Runtime v2 semantic binding"


def state():
    return ActiveOperationalState(Mode.EXP, TASK, 186, "EVENT-186")


def request():
    return PreModelContextRequest(
        "Auto > 根據建議執行",
        CommandActor.USER,
        "EVENT-186",
    )


class StructuralResolver:
    def resolve(self, current_state, current_request, checkpoint_cue):
        assert current_state.task == TASK
        assert current_request.actor == CommandActor.USER
        assert checkpoint_cue == ""
        return PreModelContextResolution(
            checkpoint_id="R186",
            task=TASK,
            operational_version=186,
            authority_refs=("CURRENT:PROJECT_HAO",),
            existing_work_refs=("PR17:CURRENT",),
            prior_attempt_refs=("INTAKE:FAIL-1",),
            regression_refs=("REG:R98",),
            existing_work_lookup_complete=True,
            prior_attempt_lookup_complete=True,
            regression_lookup_complete=True,
            reuse_disposition="REUSE",
        )


def semantic_items(
    *,
    prior_disposition="REFERENCE_ONLY",
    prior_binding_id="",
    current_disposition="APPLY",
    current_applicability="APPLICABLE",
):
    return (
        AdmittedContextItem(
            ref="CURRENT:PROJECT_HAO",
            kind="CURRENT_CONTROL",
            summary="Current requires Project/Current-first reasoning before material action.",
            source_version="CURRENT-v186",
            project_scope="PROJECT_HAO",
            applicability=current_applicability,
            disposition=current_disposition,
        ),
        AdmittedContextItem(
            ref="PR17:CURRENT",
            kind="EXISTING_WORK",
            summary="Reuse the current Runtime v2 control plane rather than build a parallel executor.",
            source_version="fb689db6",
            project_scope="PROJECT_HAO",
            disposition="REUSE",
        ),
        AdmittedContextItem(
            ref="INTAKE:FAIL-1",
            kind="PRIOR_ATTEMPT",
            summary="A prior same-shape path failed; unchanged mechanism must not be retried.",
            source_version="A510-current",
            project_scope="PROJECT_HAO",
            disposition=prior_disposition,
            binding_id=prior_binding_id,
        ),
        AdmittedContextItem(
            ref="REG:R98",
            kind="REGRESSION",
            summary="R98 showed that structural retrieval without first-model binding can still drift.",
            source_version="2026-09-04",
            project_scope="PROJECT_HAO",
            disposition="APPLY",
        ),
    )


class SemanticResolver:
    def __init__(self, items=None):
        self.items = semantic_items() if items is None else items
        self.calls = 0

    def resolve(self, current_state, current_request, receipt):
        self.calls += 1
        assert receipt.checkpoint_id == "R186"
        return self.items


class PolicyProvider:
    def resolve(self, current_state):
        assert current_state.task == TASK
        return TaskExecutionPolicy(
            goal_valid=True,
            acceptance_criteria=("use admitted Current and prior-work semantics",),
        )


def catalog():
    return ActionCatalog(
        (
            ActionBinding(
                "formal.persist",
                "formal_persistence",
                "google-drive",
                "update_cells",
                ActionArchetype.MUTATE,
                ActionExternality.PRIVATE_REVERSIBLE,
            ),
            ActionBinding(
                "formal.persist.legacy",
                "formal_persistence",
                "google-drive",
                "legacy_update_cells",
                ActionArchetype.MUTATE,
                ActionExternality.PRIVATE_REVERSIBLE,
            ),
        )
    )


def semantic_gateway(items=None):
    return ContextBoundPreModelGateway(
        PreModelContextGateway(StructuralResolver()),
        SemanticResolver(items),
    )


class IntentModel:
    def __init__(self, binding_id="formal.persist"):
        self.binding_id = binding_id
        self.calls = 0
        self.inputs = []

    def invoke(self, model_input):
        self.calls += 1
        self.inputs.append(model_input)
        return ModelActionIntent(
            "INTENT-TEST",
            "formal_persistence",
            self.binding_id,
            expected_state_delta="bounded formal delta",
        )


def test_missing_regression_semantics_blocks_before_first_model():
    incomplete = semantic_items()[:-1]
    model = IntentModel()
    ingress = ContextBoundReasoningIngress(
        pre_model=semantic_gateway(incomplete),
        model=model,
        control_plane=ControlPlaneGateway(catalog(), PolicyProvider()),
    )

    result = ingress.prepare(state(), request(), run_id="RUN-SEM-1")

    assert result.code == "PRE_MODEL_REGRESSION_SEMANTIC_COVERAGE_REQUIRED"
    assert model.calls == 0
    assert result.prepared is None


def test_stale_semantic_context_fails_closed_before_model():
    stale = semantic_items(current_applicability="STALE")
    model = IntentModel()
    ingress = ContextBoundReasoningIngress(
        pre_model=semantic_gateway(stale),
        model=model,
        control_plane=ControlPlaneGateway(catalog(), PolicyProvider()),
    )

    result = ingress.prepare(state(), request(), run_id="RUN-SEM-2")

    assert result.code == "PRE_MODEL_SEMANTIC_STALE"
    assert model.calls == 0


def test_unbound_semantic_ref_is_rejected():
    invalid = semantic_items() + (
        AdmittedContextItem(
            ref="UNBOUND:OTHER_PROJECT",
            kind="EXISTING_WORK",
            summary="This must never be admitted because structural lookup did not select it.",
            source_version="v1",
            project_scope="PROJECT_ARIEL",
            disposition="REUSE",
        ),
    )
    model = IntentModel()
    ingress = ContextBoundReasoningIngress(
        pre_model=semantic_gateway(invalid),
        model=model,
        control_plane=ControlPlaneGateway(catalog(), PolicyProvider()),
    )

    result = ingress.prepare(state(), request(), run_id="RUN-SEM-3")

    assert result.code == "PRE_MODEL_SEMANTIC_REF_UNBOUND"
    assert model.calls == 0


def test_no_action_semantic_disposition_skips_first_model_call():
    no_action = semantic_items(current_disposition="NO_ACTION")
    model = IntentModel()
    ingress = ContextBoundReasoningIngress(
        pre_model=semantic_gateway(no_action),
        model=model,
        control_plane=ControlPlaneGateway(catalog(), PolicyProvider()),
    )

    result = ingress.prepare(state(), request(), run_id="RUN-SEM-4")

    assert result.code == "PRE_MODEL_CONTEXT_NO_ACTION"
    assert result.admission.allowed is True
    assert model.calls == 0
    assert result.intent is None
    assert result.prepared is None


def test_known_failure_disposition_blocks_same_binding_before_control_plane():
    blocked = semantic_items(
        prior_disposition="DO_NOT_REPEAT",
        prior_binding_id="formal.persist.legacy",
    )
    model = IntentModel(binding_id="formal.persist.legacy")
    ingress = ContextBoundReasoningIngress(
        pre_model=semantic_gateway(blocked),
        model=model,
        control_plane=ControlPlaneGateway(catalog(), PolicyProvider()),
    )

    result = ingress.prepare(state(), request(), run_id="RUN-SEM-5")

    assert result.code == "PRE_MODEL_KNOWN_FAILURE_REPEAT_BLOCKED"
    assert model.calls == 1
    assert result.intent is not None
    assert result.prepared is None


def test_valid_semantic_context_reaches_existing_control_plane():
    model = IntentModel(binding_id="formal.persist")
    ingress = ContextBoundReasoningIngress(
        pre_model=semantic_gateway(),
        model=model,
        control_plane=ControlPlaneGateway(catalog(), PolicyProvider()),
    )

    result = ingress.prepare(state(), request(), run_id="RUN-SEM-6")

    assert result.admission.allowed is True
    assert result.admission.code == "PRE_MODEL_SEMANTIC_CONTEXT_ADMITTED"
    assert result.admission.model_input is not None
    assert result.admission.model_input.semantic_fingerprint.startswith("sha256:")
    assert model.calls == 1
    assert result.prepared is not None
    assert result.prepared.record is not None
    assert result.prepared.record.mode == Mode.EXP
    assert result.prepared.record.task == TASK
    assert result.prepared.resolution.proposal is not None
    assert result.prepared.resolution.proposal.provider == "google-drive"
    assert result.code == "MODEL_INTENT_RESOLVED_TO_TRUSTED_BINDING"


def test_semantic_fingerprint_changes_when_admitted_meaning_changes():
    first = semantic_gateway().admit(state(), request())
    changed_items = list(semantic_items())
    changed_items[1] = AdmittedContextItem(
        ref="PR17:CURRENT",
        kind="EXISTING_WORK",
        summary="Same source ref, materially different admitted meaning.",
        source_version="fb689db6",
        project_scope="PROJECT_HAO",
        disposition="REUSE",
    )
    second = semantic_gateway(tuple(changed_items)).admit(state(), request())

    assert first.allowed is True
    assert second.allowed is True
    assert first.model_input is not None
    assert second.model_input is not None
    assert first.model_input.receipt.context_fingerprint == second.model_input.receipt.context_fingerprint
    assert first.model_input.semantic_fingerprint != second.model_input.semantic_fingerprint


class FakeResponsesCreate:
    def __init__(self, output_text):
        self.output_text = output_text
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return {"id": "resp-context-bound", "output_text": self.output_text}


class FakeResponsesClient:
    def __init__(self, output_text):
        self.responses = FakeResponsesCreate(output_text)


def admitted_model_input():
    admission = semantic_gateway().admit(state(), request())
    assert admission.allowed is True
    assert admission.model_input is not None
    return admission.model_input


def test_responses_boundary_receives_semantics_not_only_refs():
    client = FakeResponsesClient(
        json.dumps(
            {
                "requested_capability": "formal_persistence",
                "binding_id": "formal.persist",
                "expected_state_delta": "bounded formal delta",
                "authorization_target": "",
                "arguments": {},
            }
        )
    )
    boundary = ContextBoundResponsesIntentBoundary(
        client,
        model="gpt-5.6-luna",
        max_output_tokens=128,
    )

    intent = boundary.invoke(admitted_model_input())

    assert intent.intent_id.startswith("INTENT:")
    assert intent.binding_id == "formal.persist"
    assert len(client.responses.calls) == 1
    call = client.responses.calls[0]
    assert call["tool_choice"] == "none"
    assert call["store"] is False
    instructions = call["instructions"]
    assert '"existing_work_refs":["PR17:CURRENT"]' in instructions
    assert '"prior_attempt_refs":["INTAKE:FAIL-1"]' in instructions
    assert '"semantic_context_fingerprint":"sha256:' in instructions
    assert "Reuse the current Runtime v2 control plane" in instructions
    assert "A prior same-shape path failed" in instructions


def test_responses_boundary_rejects_model_attempt_to_author_runtime_state():
    client = FakeResponsesClient(
        json.dumps(
            {
                "requested_capability": "formal_persistence",
                "binding_id": "formal.persist",
                "arguments": {},
                "mode": "SYS",
            }
        )
    )
    boundary = ContextBoundResponsesIntentBoundary(
        client,
        model="gpt-5.6-luna",
    )

    with pytest.raises(ValueError, match="RESPONSES_INTENT_RUNTIME_FIELD_OR_UNKNOWN_KEY:mode"):
        boundary.invoke(admitted_model_input())
