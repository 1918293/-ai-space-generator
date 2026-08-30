from dataclasses import fields

from src.action_catalog import ActionBinding, ActionCatalog, ModelActionIntent
from src.control_gateway import (
    ControlPlaneGateway,
    ModelIngressRequest,
    TaskExecutionPolicy,
)
from src.execution_control import (
    ActionArchetype,
    ActionExternality,
    AuthorityStamp,
    FailureStage,
    Mode,
    admit_action,
)
from src.operational_state import ActiveOperationalState


class PolicyProvider:
    def resolve(self, state):
        assert state.task == "Exact edit"
        return TaskExecutionPolicy(
            goal_valid=True,
            acceptance_criteria=("edit only inside mask", "preserve outside mask"),
            required_acceptance_gate_ids=("MASK_SCOPE", "OUTSIDE_MASK_ZERO"),
            hao_acceptance_required=True,
            authority_refs=("SOURCE-ORIGINAL",),
            authority_stamps=(AuthorityStamp("SOURCE-ORIGINAL", "sha256:abc"),),
            required_action_authority_refs=("SOURCE-ORIGINAL",),
            required_action_tags=("LOCAL_EDIT", "PRESERVE_OUTSIDE_MASK"),
            forbidden_action_tags=("FULL_GENERATION",),
        )


def catalog():
    return ActionCatalog(
        [
            ActionBinding(
                "image.local_edit",
                "image_edit",
                "image-editor",
                "masked_edit",
                ActionArchetype.MUTATE,
                ActionExternality.PRIVATE_REVERSIBLE,
                assurance_tags=("LOCAL_EDIT", "PRESERVE_OUTSIDE_MASK"),
                rollback_available=True,
            ),
            ActionBinding(
                "image.full_generate",
                "image_generation",
                "image-model",
                "generate",
                ActionArchetype.MUTATE,
                ActionExternality.PRIVATE_REVERSIBLE,
                assurance_tags=("FULL_GENERATION",),
            ),
        ]
    )


def state():
    return ActiveOperationalState(Mode.EXP, "Exact edit", 9, "USER-EVENT")


def test_model_ingress_schema_cannot_supply_mode_task_authority_or_acceptance_policy():
    names = {field.name for field in fields(ModelIngressRequest)}
    assert names == {"run_id", "sequence", "intent"}


def test_gateway_builds_run_from_runtime_state_and_trusted_policy():
    gateway = ControlPlaneGateway(catalog(), PolicyProvider())
    prepared = gateway.prepare(
        state(),
        ModelIngressRequest(
            "RUN-GW",
            1,
            ModelActionIntent(
                "INTENT-1",
                "image_edit",
                "image.local_edit",
                expected_state_delta="bounded edit",
            ),
        ),
    )
    record = prepared.record
    proposal = prepared.resolution.proposal
    assert record is not None
    assert proposal is not None
    assert record.mode == Mode.EXP
    assert record.task == "Exact edit"
    assert record.hao_acceptance_required is True
    assert record.required_acceptance_gate_ids == ("MASK_SCOPE", "OUTSIDE_MASK_ZERO")
    assert record.required_action_authority_refs == ("SOURCE-ORIGINAL",)
    assert proposal.externality == ActionExternality.PRIVATE_REVERSIBLE
    assert set(proposal.assurance_tags) == {"LOCAL_EDIT", "PRESERVE_OUTSIDE_MASK"}
    admitted, decision = admit_action(record, proposal)
    assert decision.allowed is True
    assert admitted.action == proposal


def test_gateway_cannot_make_wrong_full_generation_binding_fit_exact_edit_policy():
    gateway = ControlPlaneGateway(catalog(), PolicyProvider())
    prepared = gateway.prepare(
        state(),
        ModelIngressRequest(
            "RUN-GW-WRONG",
            1,
            ModelActionIntent(
                "INTENT-1",
                "image_generation",
                "image.full_generate",
                expected_state_delta="replace image",
            ),
        ),
    )
    record = prepared.record
    proposal = prepared.resolution.proposal
    assert record is not None and proposal is not None
    blocked, decision = admit_action(record, proposal)
    assert decision.allowed is False
    assert decision.failed_at == FailureStage.POLICY
    assert blocked.failure_code.startswith("MISSING_REQUIRED_ACTION_TAGS:")


def test_unregistered_model_binding_never_produces_execution_record_pair():
    gateway = ControlPlaneGateway(catalog(), PolicyProvider())
    prepared = gateway.prepare(
        state(),
        ModelIngressRequest(
            "RUN-GW-UNKNOWN",
            1,
            ModelActionIntent("INTENT-1", "image_edit", "unknown.binding"),
        ),
    )
    assert prepared.record is None
    assert prepared.resolution.proposal is None
    assert prepared.resolution.decision.failed_at == FailureStage.BINDING
