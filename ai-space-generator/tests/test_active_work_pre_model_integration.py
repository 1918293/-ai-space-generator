import json
from datetime import datetime

from src.active_work_identity_admission import FreshActiveWorkIdentityAdmissionResolver
from src.context_bound_reasoning import (
    AdmittedContextItem,
    ContextBoundPreModelGateway,
    ContextBoundReasoningIngress,
)
from src.control_gateway import (
    PreModelContextGateway,
    PreModelContextRequest,
    PreModelContextResolution,
)
from src.execution_control import Mode
from src.operational_state import ActiveOperationalState, CommandActor


TASK = "Requirement Verification identity projection"
NOW = datetime.fromisoformat("2026-09-17T11:30:00+08:00")
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


def state():
    return ActiveOperationalState(Mode.EXP, TASK, 7, "EVENT-7")


def request(event_id="EVENT-RV-051"):
    return PreModelContextRequest(
        "Auto > continue bounded Requirement Verification",
        CommandActor.USER,
        event_id,
    )


def rv_summary(objective="Bind applicable controls before material action dispatch."):
    return json.dumps(
        [[
            "RV-051",
            "R-051",
            "N-302",
            "FUNCTIONAL",
            objective,
            "Runtime v2 / action-admission control",
            "BYPASS + FAIL-CLOSED REGRESSION",
            "Attempt bypasses and verify fail-closed behavior before dispatch.",
            "0 sampled consequential hard-control bypasses.",
        ]],
        ensure_ascii=False,
        separators=(",", ":"),
    )


class StructuralResolver:
    def resolve(self, current_state, current_request, checkpoint_cue):
        assert current_state.task == TASK
        assert current_request.actor == CommandActor.USER
        assert checkpoint_cue == ""
        return PreModelContextResolution(
            checkpoint_id="R7",
            task=TASK,
            operational_version=7,
            authority_refs=("REQUIREMENTS:RV-051",),
            existing_work_lookup_complete=True,
            prior_attempt_lookup_complete=True,
            regression_lookup_complete=True,
            reuse_disposition="ADMIT",
        )


class SemanticResolver:
    def __init__(self, objective="Bind applicable controls before material action dispatch."):
        self.objective = objective

    def resolve(self, current_state, current_request, receipt):
        return (
            AdmittedContextItem(
                ref="REQUIREMENTS:RV-051",
                kind="CURRENT_CONTROL",
                summary=rv_summary(self.objective),
                source_version="requirements-baseline-v1",
                project_scope="PROJECT_HAO",
                applicability="APPLICABLE",
                disposition="APPLY",
            ),
        )


class Source:
    def __init__(self, text):
        self.text = text
        self.calls = 0

    def read_text(self):
        self.calls += 1
        return self.text


class CountingModel:
    def __init__(self):
        self.calls = 0

    def invoke(self, model_input):
        self.calls += 1
        raise AssertionError("ACTIVE_WORK_BLOCK_MUST_PREVENT_FIRST_MODEL")


class ForbiddenControlPlane:
    def prepare(self, state, request):
        raise AssertionError("ACTIVE_WORK_BLOCK_MUST_PREVENT_CONTROL_PLANE")


class ForbiddenActiveWorkResolver:
    def resolve(self, *, work_key, intent_fingerprint):
        raise AssertionError("NON_RV_PATH_MUST_NOT_QUERY_ACTIVE_WORK_IDENTITY")


def empty_slot(index, generation=0):
    return f"""S{index}_STATUS=EMPTY
S{index}_GENERATION={generation}
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


def active_signal(intent_fingerprint=None, *, include_identity=True, work_key="RV-051"):
    identity = ""
    if include_identity:
        identity = (
            f"S1_WORK_KEY={work_key}\n"
            f"S1_INTENT_FINGERPRINT={intent_fingerprint}\n"
        )
    s1 = f"""S1_STATUS=ACTIVE
S1_GENERATION=1
{identity}S1_RUN_KEY=RUN-RV051-OLD
S1_OBJECTIVE=LEGACY_OBJECTIVE_MUST_NOT_DEFINE_IDENTITY
S1_TARGET=LEGACY_TARGET_MUST_NOT_DEFINE_IDENTITY
S1_EXECUTION_LANE=CHATGPT_PRIVATE_LANE
S1_WORK_STATE=VERIFYING
S1_STARTED_AT=2026-09-17T11:22:28+08:00
S1_UPDATED_AT=2026-09-17T11:22:28+08:00
S1_EXPIRES_AT=2026-09-17T11:42:28+08:00
S1_EXPECTED_DELTA=BOUNDED_VERIFICATION
S1_READBACK_STATE=PENDING
S1_OWNER=CHATGPT_CURRENT_CHAT
"""
    return CONTROL + s1 + empty_slot(2) + empty_slot(3) + empty_slot(4)


def gateway(*, objective=None, active_work_resolver=None):
    return ContextBoundPreModelGateway(
        PreModelContextGateway(StructuralResolver()),
        SemanticResolver(
            objective or "Bind applicable controls before material action dispatch."
        ),
        active_work_resolver=active_work_resolver,
    )


def projected_identity(*, objective=None):
    admission = gateway(objective=objective).admit(state(), request())
    assert admission.allowed is True
    assert admission.model_input is not None
    assert admission.model_input.work_identity is not None
    return admission.model_input.work_identity


def fresh_resolver(text):
    return FreshActiveWorkIdentityAdmissionResolver(Source(text), now=lambda: NOW)


def test_same_work_same_intent_blocks_at_existing_pre_model_gateway():
    identity = projected_identity()
    admission = gateway(
        active_work_resolver=fresh_resolver(
            active_signal(identity.intent_fingerprint)
        )
    ).admit(state(), request())

    assert admission.allowed is False
    assert admission.code == "ACTIVE_WORK_SAME_INTENT_WAIT_OR_JOIN"
    assert admission.model_input is None


def test_same_work_changed_intent_blocks_for_reconciliation_before_first_model():
    old_identity = projected_identity()
    admission = gateway(
        objective="Revalidate dynamic tool delegation before enablement.",
        active_work_resolver=fresh_resolver(
            active_signal(old_identity.intent_fingerprint)
        ),
    ).admit(state(), request())

    assert admission.allowed is False
    assert admission.code == "ACTIVE_WORK_CHANGED_INTENT_RECONCILIATION_REQUIRED"
    assert admission.model_input is None


def test_identityless_legacy_active_work_fails_closed_without_objective_fallback():
    admission = gateway(
        active_work_resolver=fresh_resolver(
            active_signal(include_identity=False)
        )
    ).admit(state(), request())

    assert admission.allowed is False
    assert admission.code == "ACTIVE_WORK_IDENTITY_VISIBILITY_UNKNOWN"


def test_different_known_work_does_not_block_identity_layer():
    identity = projected_identity()
    admission = gateway(
        active_work_resolver=fresh_resolver(
            active_signal(identity.intent_fingerprint, work_key="RV-052")
        )
    ).admit(state(), request())

    assert admission.allowed is True
    assert admission.model_input is not None
    assert admission.model_input.work_identity is not None
    assert admission.model_input.work_identity.work_key == "RV-051"


def test_active_work_block_prevents_first_model_and_control_plane():
    identity = projected_identity()
    model = CountingModel()
    ingress = ContextBoundReasoningIngress(
        pre_model=gateway(
            active_work_resolver=fresh_resolver(
                active_signal(identity.intent_fingerprint)
            )
        ),
        model=model,
        control_plane=ForbiddenControlPlane(),
    )

    result = ingress.prepare(state(), request(), run_id="RUN-NEW")

    assert result.code == "ACTIVE_WORK_SAME_INTENT_WAIT_OR_JOIN"
    assert result.admission.allowed is False
    assert result.intent is None
    assert result.prepared is None
    assert model.calls == 0


def test_non_rv_semantics_remain_backward_compatible_and_skip_identity_resolver():
    ref = "CURRENT:PROJECT_HAO"

    class NonRvStructuralResolver:
        def resolve(self, current_state, current_request, checkpoint_cue):
            return PreModelContextResolution(
                checkpoint_id="R7",
                task=TASK,
                operational_version=7,
                authority_refs=(ref,),
                existing_work_lookup_complete=True,
                prior_attempt_lookup_complete=True,
                regression_lookup_complete=True,
                reuse_disposition="ADMIT",
            )

    class NonRvSemanticResolver:
        def resolve(self, current_state, current_request, receipt):
            return (
                AdmittedContextItem(
                    ref=ref,
                    kind="CURRENT_CONTROL",
                    summary="Current requires fail-closed continuation semantics.",
                    source_version="CURRENT-v7",
                    project_scope="PROJECT_HAO",
                ),
            )

    admission = ContextBoundPreModelGateway(
        PreModelContextGateway(NonRvStructuralResolver()),
        NonRvSemanticResolver(),
        active_work_resolver=ForbiddenActiveWorkResolver(),
    ).admit(state(), request(event_id="EVENT-NON-RV"))

    assert admission.allowed is True
    assert admission.model_input is not None
    assert admission.model_input.work_identity is None
