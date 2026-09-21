import json

from src.context_bound_reasoning import (
    AdmittedContextItem,
    ContextBoundPreModelGateway,
)
from src.control_gateway import (
    PreModelContextGateway,
    PreModelContextRequest,
    PreModelContextResolution,
)
from src.execution_control import Mode
from src.operational_state import ActiveOperationalState, CommandActor
from src.resolved_work_identity import WorkIdentityRelation, compare_work_identity


TASK = "Requirement Verification identity projection"


def state():
    return ActiveOperationalState(Mode.EXP, TASK, 7, "EVENT-7")


def request(*, event_id="EVENT-RV-051"):
    return PreModelContextRequest(
        "Auto > continue bounded Requirement Verification",
        CommandActor.USER,
        event_id,
    )


def rv_summary(
    row_id="RV-051",
    *,
    requirement_id="R-051",
    objective="Bind applicable controls before material action dispatch.",
    allocated_to="Runtime v2 / action-admission control",
    verification_method="BYPASS + FAIL-CLOSED REGRESSION",
    verification_procedure="Attempt bypasses and verify fail-closed behavior before dispatch.",
    acceptance="0 sampled consequential hard-control bypasses.",
):
    return json.dumps(
        [
            [
                row_id,
                requirement_id,
                "N-302",
                "FUNCTIONAL",
                objective,
                allocated_to,
                verification_method,
                verification_procedure,
                acceptance,
            ]
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    )


class StructuralResolver:
    def __init__(self, authority_ref):
        self.authority_ref = authority_ref

    def resolve(self, current_state, current_request, checkpoint_cue):
        assert current_state.task == TASK
        assert current_request.actor == CommandActor.USER
        assert checkpoint_cue == ""
        return PreModelContextResolution(
            checkpoint_id="R7",
            task=TASK,
            operational_version=7,
            authority_refs=(self.authority_ref,),
            existing_work_lookup_complete=True,
            prior_attempt_lookup_complete=True,
            regression_lookup_complete=True,
            reuse_disposition="ADMIT",
        )


class SemanticResolver:
    def __init__(self, item):
        self.item = item

    def resolve(self, current_state, current_request, receipt):
        assert current_state.task == TASK
        assert receipt.checkpoint_id == "R7"
        return (self.item,)


def rv_gateway(
    *,
    ref="REQUIREMENTS:RV-051",
    row_id="RV-051",
    objective="Bind applicable controls before material action dispatch.",
):
    item = AdmittedContextItem(
        ref=ref,
        kind="CURRENT_CONTROL",
        summary=rv_summary(row_id, objective=objective),
        source_version="requirements-baseline-v1",
        project_scope="PROJECT_HAO",
        applicability="APPLICABLE",
        disposition="APPLY",
    )
    return ContextBoundPreModelGateway(
        PreModelContextGateway(StructuralResolver(ref)),
        SemanticResolver(item),
    )


def test_rv_identity_is_projected_only_after_canonical_semantic_hydration():
    admission = rv_gateway().admit(state(), request())

    assert admission.allowed is True
    assert admission.model_input is not None
    identity = admission.model_input.work_identity
    assert identity is not None
    assert identity.work_key == "RV-051"
    assert identity.semantic_context_fingerprint == admission.model_input.semantic_fingerprint
    assert identity.binding_fingerprint.startswith("sha256:")
    assert identity.projection.project_scope == "PROJECT_HAO"
    assert identity.projection.logical_target.startswith("R-051|")


def test_same_rv_row_with_changed_material_intent_is_same_work_changed_intent():
    baseline = rv_gateway().admit(state(), request(event_id="EVENT-RV-051-A"))
    changed = rv_gateway(
        objective="Revalidate dynamic tool delegation before enablement."
    ).admit(state(), request(event_id="EVENT-RV-051-B"))

    assert baseline.allowed is True
    assert changed.allowed is True
    assert baseline.model_input is not None
    assert changed.model_input is not None
    first = baseline.model_input.work_identity
    second = changed.model_input.work_identity
    assert first is not None and second is not None
    assert first.work_key == second.work_key == "RV-051"
    assert first.intent_fingerprint != second.intent_fingerprint
    assert compare_work_identity(
        first.projection,
        second.projection,
    ) == WorkIdentityRelation.SAME_WORK_CHANGED_INTENT
    assert first.binding_fingerprint != second.binding_fingerprint


def test_different_rv_rows_are_different_work_even_with_same_material_intent():
    first = rv_gateway(ref="REQUIREMENTS:RV-051", row_id="RV-051").admit(
        state(), request(event_id="EVENT-RV-051")
    )
    second = rv_gateway(ref="REQUIREMENTS:RV-052", row_id="RV-052").admit(
        state(), request(event_id="EVENT-RV-052")
    )

    assert first.allowed is True
    assert second.allowed is True
    assert first.model_input is not None
    assert second.model_input is not None
    left = first.model_input.work_identity
    right = second.model_input.work_identity
    assert left is not None and right is not None
    assert left.intent_fingerprint == right.intent_fingerprint
    assert compare_work_identity(
        left.projection,
        right.projection,
    ) == WorkIdentityRelation.DIFFERENT_WORK


def test_rv_row_and_source_ref_mismatch_fails_closed_before_model():
    admission = rv_gateway(
        ref="REQUIREMENTS:RV-051",
        row_id="RV-052",
    ).admit(state(), request())

    assert admission.allowed is False
    assert admission.code == "WORK_IDENTITY_REQUIREMENT_VERIFICATION_SOURCE_MISMATCH"
    assert admission.model_input is None


def test_rv_identity_requires_current_direct_hao_event_identity():
    admission = rv_gateway().admit(state(), request(event_id=""))

    assert admission.allowed is False
    assert admission.code == "PRE_MODEL_RV_IDENTITY_DIRECT_HAO_INTENT_REQUIRED"
    assert admission.model_input is None


def test_non_rv_current_semantics_remain_backward_compatible_without_work_identity():
    ref = "CURRENT:PROJECT_HAO"
    item = AdmittedContextItem(
        ref=ref,
        kind="CURRENT_CONTROL",
        summary="Current requires Project/Current-first reasoning before material action.",
        source_version="CURRENT-v7",
        project_scope="PROJECT_HAO",
        applicability="APPLICABLE",
        disposition="APPLY",
    )
    gateway = ContextBoundPreModelGateway(
        PreModelContextGateway(StructuralResolver(ref)),
        SemanticResolver(item),
    )

    admission = gateway.admit(state(), request(event_id="EVENT-NON-RV"))

    assert admission.allowed is True
    assert admission.model_input is not None
    assert admission.model_input.work_identity is None
