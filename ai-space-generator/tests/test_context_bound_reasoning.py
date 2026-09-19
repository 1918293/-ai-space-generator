import json

import pytest

from src.action_catalog import ActionBinding, ActionCatalog, ModelActionIntent
from src.active_work_identity_admission import (
    ActiveWorkIdentityAdmission,
    ActiveWorkIdentityDisposition,
)
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
from src.resolved_work_identity import (
    CanonicalWorkIdentitySeed,
    direct_hao_intent_ref,
    resolve_work_identity_projection,
)


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


def generic_work_identity():
    intent_ref = direct_hao_intent_ref(
        user_text=request().user_text,
        actor=CommandActor.USER,
        event_id=request().event_id,
    )
    seed = CanonicalWorkIdentitySeed(
        work_key="PROJECT_HAO:GENERIC_NON_RV_WORK",
        project_scope="PROJECT_HAO",
        logical_target="runtime-v2/generic-current-work",
        objective="Reuse verified Current identity for non-RV Active Work coordination",
        deliverable_identity="BOUNDED_GENERIC_IDENTITY_BINDING",
        acceptance_identity="ACTIVE_WORK_RESOLVER_SEES_TRUSTED_IDENTITY",
        field_sources=(
            ("work_key", "CURRENT_AUTHORITY", "CURRENT:PROJECT_HAO"),
            ("project_scope", "CURRENT_AUTHORITY", "CURRENT:PROJECT_HAO"),
            ("logical_target", "CURRENT_AUTHORITY", "CURRENT:PROJECT_HAO"),
            ("objective", "CURRENT_AUTHORITY", "CURRENT:PROJECT_HAO"),
            ("deliverable_identity", "CURRENT_AUTHORITY", "CURRENT:PROJECT_HAO"),
            ("acceptance_identity", "CURRENT_AUTHORITY", "CURRENT:PROJECT_HAO"),
        ),
    )
    return resolve_work_identity_projection(
        seed,
        checkpoint_id="R186",
        task=TASK,
        operational_version=186,
        authority_refs=("CURRENT:PROJECT_HAO",),
        intent_refs=(intent_ref,),
    )


class StructuralResolverWithGenericIdentity(StructuralResolver):
    def resolve(self, current_state, current_request, checkpoint_cue):
        resolution = super().resolve(current_state, current_request, checkpoint_cue)
        return PreModelContextResolution(
            checkpoint_id=resolution.checkpoint_id,
            task=resolution.task,
            operational_version=resolution.operational_version,
            authority_refs=resolution.authority_refs,
            existing_work_refs=resolution.existing_work_refs,
            prior_attempt_refs=resolution.prior_attempt_refs,
            regression_refs=resolution.regression_refs,
            existing_work_lookup_complete=resolution.existing_work_lookup_complete,
            prior_attempt_lookup_complete=resolution.prior_attempt_lookup_complete,
            regression_lookup_complete=resolution.regression_lookup_complete,
            reuse_disposition=resolution.reuse_disposition,
            work_identity=generic_work_identity(),
        )


class CapturingActiveWorkResolver:
    def __init__(self):
        self.calls = []

    def resolve(self, *, work_key, intent_fingerprint):
        self.calls.append((work_key, intent_fingerprint))
        return ActiveWorkIdentityAdmission(
            True,
            "ACTIVE_WORK_IDENTITY_CLEAR",
            ActiveWorkIdentityDisposition.CLEAR,
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
    def __init__(
        self,
        binding_id="formal.persist",
        used_refs=("CURRENT:PROJECT_HAO", "PR17:CURRENT"),
    ):
        self.binding_id = binding_id
        self.used_refs = used_refs
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
            model_reported_used_refs=self.used_refs,
        )


def test_non_rv_trusted_current_identity_is_preserved_and_checked_by_active_work():
    active_work = CapturingActiveWorkResolver()
    admission = ContextBoundPreModelGateway(
        PreModelContextGateway(StructuralResolverWithGenericIdentity()),
        SemanticResolver(),
        active_work_resolver=active_work,
    ).admit(state(), request())

    assert admission.allowed is True
    assert admission.model_input is not None
    assert admission.model_input.work_identity is not None
    assert admission.model_input.work_identity.work_key == "PROJECT_HAO:GENERIC_NON_RV_WORK"
    assert active_work.calls == [
        (
            admission.model_input.work_identity.work_key,
            admission.model_input.work_identity.intent_fingerprint,
        )
    ]


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
    model = IntentModel(
        binding_id="formal.persist.legacy",
        used_refs=("CURRENT:PROJECT_HAO", "INTAKE:FAIL-1"),
    )
    ingress = ContextBoundReasoningIngress(
        pre_model=semantic_gateway(blocked),
        model=model,
        control_plane=ControlPlaneGateway(catalog(), PolicyProvider()),
    )

    result = ingress.prepare(state(), request(), run_id="RUN-SEM-5")

    assert result.code == "PRE_MODEL_KNOWN_FAILURE_REPEAT_BLOCKED"
    assert model.calls == 1
    assert result.intent is not None
    assert result.intent.model_reported_used_refs == (
        "CURRENT:PROJECT_HAO",
        "INTAKE:FAIL-1",
    )
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
    assert result.intent is not None
    assert result.intent.model_reported_used_refs == (
        "CURRENT:PROJECT_HAO",
        "PR17:CURRENT",
    )
    assert result.prepared is not None
    assert result.prepared.record is not None
    assert result.prepared.record.mode == Mode.EXP
    assert result.prepared.record.task == TASK
    assert result.prepared.resolution.proposal is not None
    assert result.prepared.resolution.proposal.provider == "google-drive"
    assert result.code == "MODEL_INTENT_RESOLVED_TO_TRUSTED_BINDING"


def test_model_reported_usage_is_required_before_control_plane():
    model = IntentModel(used_refs=())
    ingress = ContextBoundReasoningIngress(
        pre_model=semantic_gateway(),
        model=model,
        control_plane=ControlPlaneGateway(catalog(), PolicyProvider()),
    )

    result = ingress.prepare(state(), request(), run_id="RUN-SEM-USED-1")

    assert result.code == "PRE_MODEL_REPORTED_USED_REFS_REQUIRED"
    assert result.intent is not None
    assert result.prepared is None


def test_model_reported_usage_must_be_exact_admitted_ref():
    model = IntentModel(used_refs=(" CURRENT:PROJECT_HAO",))
    ingress = ContextBoundReasoningIngress(
        pre_model=semantic_gateway(),
        model=model,
        control_plane=ControlPlaneGateway(catalog(), PolicyProvider()),
    )

    result = ingress.prepare(state(), request(), run_id="RUN-SEM-USED-2")

    assert result.code == "PRE_MODEL_REPORTED_USED_REF_EXACT_REQUIRED"
    assert result.prepared is None


def test_model_reported_usage_rejects_unadmitted_ref():
    model = IntentModel(used_refs=("CURRENT:PROJECT_HAO", "UNBOUND:OTHER"))
    ingress = ContextBoundReasoningIngress(
        pre_model=semantic_gateway(),
        model=model,
        control_plane=ControlPlaneGateway(catalog(), PolicyProvider()),
    )

    result = ingress.prepare(state(), request(), run_id="RUN-SEM-USED-3")

    assert result.code == "PRE_MODEL_REPORTED_USED_REF_UNADMITTED:UNBOUND:OTHER"
    assert result.prepared is None


def test_model_reported_usage_rejects_duplicate_ref():
    model = IntentModel(used_refs=("CURRENT:PROJECT_HAO", "CURRENT:PROJECT_HAO"))
    ingress = ContextBoundReasoningIngress(
        pre_model=semantic_gateway(),
        model=model,
        control_plane=ControlPlaneGateway(catalog(), PolicyProvider()),
    )

    result = ingress.prepare(state(), request(), run_id="RUN-SEM-USED-4")

    assert result.code == "PRE_MODEL_REPORTED_USED_REF_DUPLICATE:CURRENT:PROJECT_HAO"
    assert result.prepared is None


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


def valid_response_payload(**overrides):
    payload = {
        "requested_capability": "formal_persistence",
        "binding_id": "formal.persist",
        "expected_state_delta": "bounded formal delta",
        "authorization_target": "",
        "arguments": {},
        "model_reported_used_refs": ["CURRENT:PROJECT_HAO", "PR17:CURRENT"],
    }
    payload.update(overrides)
    return payload


def test_responses_boundary_receives_semantics_not_only_refs():
    client = FakeResponsesClient(json.dumps(valid_response_payload()))
    boundary = ContextBoundResponsesIntentBoundary(
        client,
        model="gpt-5.6-luna",
        max_output_tokens=128,
    )

    intent = boundary.invoke(admitted_model_input())

    assert intent.intent_id.startswith("INTENT:")
    assert intent.binding_id == "formal.persist"
    assert intent.model_reported_used_refs == (
        "CURRENT:PROJECT_HAO",
        "PR17:CURRENT",
    )
    assert len(client.responses.calls) == 1
    call = client.responses.calls[0]
    assert call["tool_choice"] == "none"
    assert call["store"] is False
    instructions = call["instructions"]
    assert '"mode":"EXP"' in instructions
    assert f'"task":"{TASK}"' in instructions
    assert '"ref":"PR17:CURRENT"' in instructions
    assert '"ref":"INTAKE:FAIL-1"' in instructions
    assert "Reuse the current Runtime v2 control plane" in instructions
    assert "A prior same-shape path failed" in instructions
    assert "model_reported_used_refs" in instructions
    assert '"checkpoint_id"' not in instructions
    assert '"operational_version"' not in instructions
    assert '"source_version"' not in instructions
    assert '"structural_context_fingerprint"' not in instructions
    assert '"semantic_context_fingerprint"' not in instructions
    assert '"existing_work_refs"' not in instructions
    assert '"prior_attempt_refs"' not in instructions
    assert '"regression_refs"' not in instructions
    assert instructions.index("<model_intent_shape>") < instructions.index("<hao_runtime_context>")


def test_responses_boundary_hidden_semantic_fingerprint_still_binds_intent_identity():
    first_input = admitted_model_input()
    first_boundary = ContextBoundResponsesIntentBoundary(
        FakeResponsesClient(json.dumps(valid_response_payload())),
        model="gpt-5.6-luna",
    )
    first_intent = first_boundary.invoke(first_input)

    changed_items = list(semantic_items())
    changed_items[0] = AdmittedContextItem(
        ref=changed_items[0].ref,
        kind=changed_items[0].kind,
        summary=changed_items[0].summary + " Material semantic delta.",
        source_version=changed_items[0].source_version,
        project_scope=changed_items[0].project_scope,
        applicability=changed_items[0].applicability,
        disposition=changed_items[0].disposition,
        binding_id=changed_items[0].binding_id,
    )
    changed_admission = semantic_gateway(tuple(changed_items)).admit(state(), request())
    assert changed_admission.allowed is True
    assert changed_admission.model_input is not None
    second_client = FakeResponsesClient(json.dumps(valid_response_payload()))
    second_boundary = ContextBoundResponsesIntentBoundary(
        second_client,
        model="gpt-5.6-luna",
    )
    second_intent = second_boundary.invoke(changed_admission.model_input)

    assert first_intent.intent_id != second_intent.intent_id
    assert '"semantic_context_fingerprint"' not in second_client.responses.calls[0]["instructions"]

def test_responses_boundary_requires_reported_used_refs():
    payload = valid_response_payload()
    payload.pop("model_reported_used_refs")
    boundary = ContextBoundResponsesIntentBoundary(
        FakeResponsesClient(json.dumps(payload)),
        model="gpt-5.6-luna",
    )

    with pytest.raises(ValueError, match="RESPONSES_INTENT_REPORTED_USED_REFS_LIST_REQUIRED"):
        boundary.invoke(admitted_model_input())


def test_responses_boundary_rejects_non_exact_reported_ref():
    boundary = ContextBoundResponsesIntentBoundary(
        FakeResponsesClient(
            json.dumps(
                valid_response_payload(
                    model_reported_used_refs=[" CURRENT:PROJECT_HAO"]
                )
            )
        ),
        model="gpt-5.6-luna",
    )

    with pytest.raises(ValueError, match="RESPONSES_INTENT_REPORTED_USED_REF_EXACT_REQUIRED"):
        boundary.invoke(admitted_model_input())


def test_responses_boundary_rejects_unadmitted_reported_ref():
    boundary = ContextBoundResponsesIntentBoundary(
        FakeResponsesClient(
            json.dumps(
                valid_response_payload(model_reported_used_refs=["UNBOUND:OTHER"])
            )
        ),
        model="gpt-5.6-luna",
    )

    with pytest.raises(ValueError, match="RESPONSES_INTENT_REPORTED_USED_REF_UNADMITTED:UNBOUND:OTHER"):
        boundary.invoke(admitted_model_input())


def test_responses_boundary_rejects_model_attempt_to_author_runtime_state():
    payload = valid_response_payload(mode="SYS")
    boundary = ContextBoundResponsesIntentBoundary(
        FakeResponsesClient(json.dumps(payload)),
        model="gpt-5.6-luna",
    )

    with pytest.raises(ValueError, match="RESPONSES_INTENT_RUNTIME_FIELD_OR_UNKNOWN_KEY:mode"):
        boundary.invoke(admitted_model_input())