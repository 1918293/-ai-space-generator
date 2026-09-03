from dataclasses import fields

from src.action_catalog import ActionBinding, ActionCatalog, ModelActionIntent
from src.control_gateway import (
    ControlPlaneGateway,
    ModelIngressRequest,
    PreModelContextGateway,
    PreModelContextRequest,
    PreModelContextResolution,
    TaskExecutionPolicy,
    explicit_checkpoint_cue,
    invoke_after_pre_model_admission,
)
from src.execution_control import (
    ActionArchetype,
    ActionExternality,
    AuthorityStamp,
    FailureStage,
    Mode,
    admit_action,
)
from src.operational_state import ActiveOperationalState, CommandActor


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


class StaticPreModelResolver:
    def __init__(self, resolution):
        self.resolution = resolution
        self.calls = 0
        self.last_checkpoint_cue = ""

    def resolve(self, state, request, checkpoint_cue):
        self.calls += 1
        self.last_checkpoint_cue = checkpoint_cue
        return self.resolution


class CountingModelBoundary:
    """Simulates a model that would immediately start public-web research."""

    def __init__(self):
        self.model_calls = 0
        self.web_calls = 0
        self.inputs = []

    def invoke(self, model_input):
        self.model_calls += 1
        self.web_calls += 1
        self.inputs.append(model_input)
        return "model-result"


def pre_model_state():
    return ActiveOperationalState(
        Mode.EXP,
        "Knowledge-to-Execution preflight prospective field validation",
        98,
        "HAO-R98-FIELD",
    )


def valid_r98_resolution(**overrides):
    values = dict(
        checkpoint_id="R98",
        task="Knowledge-to-Execution preflight prospective field validation",
        operational_version=98,
        authority_refs=("HANDOFF:R98", "REQUIREMENTS:RV-011"),
        regression_refs=("RUN-20260904-PREFLIGHT-FIELD-R1",),
        existing_work_lookup_complete=True,
        regression_lookup_complete=True,
        reuse_disposition="REUSE",
    )
    values.update(overrides)
    return PreModelContextResolution(**values)


def test_pre_model_request_cannot_supply_receipt_mode_task_or_authority():
    names = {field.name for field in fields(PreModelContextRequest)}
    assert names == {"user_text", "actor", "event_id"}


def test_explicit_checkpoint_cue_is_resolved_from_natural_r98_input():
    assert explicit_checkpoint_cue("Auto Exp > R98") == "R98"
    assert explicit_checkpoint_cue("continue without explicit checkpoint") == ""


def test_pre_model_unresolved_current_blocks_before_model_or_web_call():
    resolver = StaticPreModelResolver(None)
    gateway = PreModelContextGateway(resolver)
    model = CountingModelBoundary()

    result = invoke_after_pre_model_admission(
        gateway,
        pre_model_state(),
        PreModelContextRequest("Auto Exp > R98", CommandActor.USER, "EVENT-R98"),
        model,
    )

    assert result.admission.allowed is False
    assert result.admission.code == "PRE_MODEL_CURRENT_UNRESOLVED"
    assert resolver.last_checkpoint_cue == "R98"
    assert model.model_calls == 0
    assert model.web_calls == 0


def test_pre_model_checkpoint_mismatch_blocks_before_model_or_web_call():
    resolver = StaticPreModelResolver(valid_r98_resolution(checkpoint_id="R100"))
    gateway = PreModelContextGateway(resolver)
    model = CountingModelBoundary()

    result = invoke_after_pre_model_admission(
        gateway,
        pre_model_state(),
        PreModelContextRequest("Auto Exp > R98", CommandActor.USER),
        model,
    )

    assert result.admission.allowed is False
    assert result.admission.code == "PRE_MODEL_CHECKPOINT_MISMATCH"
    assert model.model_calls == 0
    assert model.web_calls == 0


def test_r98_public_roadmap_referent_regression_is_blocked_before_research_starts():
    resolver = StaticPreModelResolver(
        valid_r98_resolution(task="Public GitHub R98 roadmap research")
    )
    gateway = PreModelContextGateway(resolver)
    model = CountingModelBoundary()

    result = invoke_after_pre_model_admission(
        gateway,
        pre_model_state(),
        PreModelContextRequest("Auto Exp > R98", CommandActor.USER),
        model,
    )

    assert result.admission.allowed is False
    assert result.admission.code == "PRE_MODEL_TASK_MISMATCH"
    assert model.model_calls == 0
    assert model.web_calls == 0


def test_pre_model_missing_regression_lookup_blocks_before_first_model_call():
    resolver = StaticPreModelResolver(
        valid_r98_resolution(regression_lookup_complete=False)
    )
    gateway = PreModelContextGateway(resolver)
    model = CountingModelBoundary()

    result = invoke_after_pre_model_admission(
        gateway,
        pre_model_state(),
        PreModelContextRequest("Auto Exp > R98", CommandActor.USER),
        model,
    )

    assert result.admission.allowed is False
    assert result.admission.code == "PRE_MODEL_REGRESSION_LOOKUP_REQUIRED"
    assert model.model_calls == 0
    assert model.web_calls == 0


def test_pre_model_verified_current_is_hydrated_before_first_model_call():
    resolver = StaticPreModelResolver(valid_r98_resolution())
    gateway = PreModelContextGateway(resolver)
    model = CountingModelBoundary()

    result = invoke_after_pre_model_admission(
        gateway,
        pre_model_state(),
        PreModelContextRequest("Auto Exp > R98", CommandActor.USER, "EVENT-R98"),
        model,
    )

    assert result.admission.allowed is True
    assert result.admission.code == "PRE_MODEL_CONTEXT_ADMITTED"
    assert result.admission.receipt is not None
    assert result.admission.receipt.checkpoint_id == "R98"
    assert result.admission.receipt.operational_version == 98
    assert result.admission.receipt.context_fingerprint
    assert model.model_calls == 1
    assert model.web_calls == 1
    first_input = model.inputs[0]
    assert first_input.startswith("HAO_VERIFIED_PRE_MODEL_CONTEXT\n")
    assert '"checkpoint_id": "R98"' in first_input
    assert '"operational_version": 98' in first_input
    assert "HANDOFF:R98" in first_input
    assert "RUN-20260904-PREFLIGHT-FIELD-R1" in first_input
    assert first_input.endswith("HAO_USER_INPUT\nAuto Exp > R98")


def test_pre_model_non_user_actor_cannot_self_author_context_receipt():
    resolver = StaticPreModelResolver(valid_r98_resolution())
    gateway = PreModelContextGateway(resolver)
    model = CountingModelBoundary()

    result = invoke_after_pre_model_admission(
        gateway,
        pre_model_state(),
        PreModelContextRequest("Auto Exp > R98", CommandActor.MODEL),
        model,
    )

    assert result.admission.allowed is False
    assert result.admission.code == "PRE_MODEL_USER_ACTOR_REQUIRED"
    assert resolver.calls == 0
    assert model.model_calls == 0
    assert model.web_calls == 0
