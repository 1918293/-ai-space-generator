from src.control_gateway import (
    PreModelContextGateway,
    PreModelContextRequest,
    invoke_after_pre_model_admission,
)
from src.execution_control import Mode
from src.hao_authority_resolver import (
    HaoAuthorityRoutes,
    HaoCanonicalCurrent,
    HaoCanonicalPreModelResolver,
    HaoDriveCanonicalAuthoritySource,
    HaoExistingWorkResult,
    HaoLookupResult,
)
from src.operational_state import ActiveOperationalState, CommandActor


ROUTES = HaoAuthorityRoutes(
    current_owner="config://hao/current",
    requirements_owner="config://hao/requirements",
    continuation_projection="config://hao/handoff",
    regression_owner="config://hao/regressions",
    prior_attempt_owner="config://hao/prior-attempts",
)


def state():
    return ActiveOperationalState(Mode.EXP, "Runtime v2 Bundle A", 31, "EVENT-31")


class Reader:
    def __init__(
        self,
        *,
        verified=True,
        version=31,
        authority_refs=("CURRENT:CONFIG", "REQUIREMENTS:RV-016"),
        existing_complete=True,
        prior_complete=True,
        regression_complete=True,
    ):
        self.verified = verified
        self.version = version
        self.authority_refs = authority_refs
        self.existing_complete = existing_complete
        self.prior_complete = prior_complete
        self.regression_complete = regression_complete
        self.current_calls = 0
        self.lookup_calls = []
        self.last_checkpoint_cue = None

    def resolve_current(self, routes, current_state, request, checkpoint_cue):
        assert routes == ROUTES
        assert current_state.task == "Runtime v2 Bundle A"
        self.current_calls += 1
        self.last_checkpoint_cue = checkpoint_cue
        return HaoCanonicalCurrent(
            checkpoint_id="R131",
            task=current_state.task,
            operational_version=self.version,
            authority_refs=self.authority_refs,
            verified=self.verified,
        )

    def lookup_existing_work(self, routes, current_state, request):
        self.lookup_calls.append("existing")
        return HaoExistingWorkResult(
            refs=("PR17:CURRENT",),
            complete=self.existing_complete,
            reuse_disposition="REUSE",
        )

    def lookup_prior_attempts(self, routes, current_state, request):
        self.lookup_calls.append("prior")
        return HaoLookupResult(
            refs=("INTAKE:A5055", "A633:TOOL_HISTORY"),
            complete=self.prior_complete,
        )

    def lookup_regressions(self, routes, current_state, request):
        self.lookup_calls.append("regression")
        return HaoLookupResult(
            refs=("REG:F1", "REG:F3"),
            complete=self.regression_complete,
        )


class Model:
    def __init__(self):
        self.calls = 0
        self.inputs = []

    def invoke(self, model_input):
        self.calls += 1
        self.inputs.append(model_input)
        return "ok"


def gateway(reader):
    source = HaoDriveCanonicalAuthoritySource(reader, ROUTES)
    return PreModelContextGateway(HaoCanonicalPreModelResolver(source))


def test_verified_canonical_source_hydrates_prior_attempts_before_first_model_call():
    reader = Reader()
    model = Model()
    result = invoke_after_pre_model_admission(
        gateway(reader),
        state(),
        PreModelContextRequest("Auto > continue Runtime v2", CommandActor.USER),
        model,
    )

    assert result.admission.allowed is True
    assert model.calls == 1
    receipt = model.inputs[0].receipt
    assert receipt.checkpoint_id == "R131"
    assert receipt.mode == Mode.EXP
    assert receipt.existing_work_refs == ("PR17:CURRENT",)
    assert receipt.prior_attempt_refs == ("INTAKE:A5055", "A633:TOOL_HISTORY")
    assert receipt.regression_refs == ("REG:F1", "REG:F3")
    assert reader.lookup_calls == ["existing", "prior", "regression"]


def test_explicit_checkpoint_cue_is_forwarded_and_mismatch_remains_fail_closed():
    reader = Reader()
    model = Model()
    result = invoke_after_pre_model_admission(
        gateway(reader),
        state(),
        PreModelContextRequest("Auto Exp > R130", CommandActor.USER),
        model,
    )

    assert reader.last_checkpoint_cue == "R130"
    assert result.admission.allowed is False
    assert result.admission.code == "PRE_MODEL_CHECKPOINT_MISMATCH"
    assert model.calls == 0


def test_unverified_continuation_projection_cannot_become_authority():
    reader = Reader(verified=False)
    model = Model()
    result = invoke_after_pre_model_admission(
        gateway(reader),
        state(),
        PreModelContextRequest("Auto > continue", CommandActor.USER),
        model,
    )

    assert result.admission.allowed is False
    assert result.admission.code == "PRE_MODEL_CURRENT_UNRESOLVED"
    assert reader.lookup_calls == []
    assert model.calls == 0


def test_prior_attempt_lookup_incomplete_blocks_before_model_call():
    reader = Reader(prior_complete=False)
    model = Model()
    result = invoke_after_pre_model_admission(
        gateway(reader),
        state(),
        PreModelContextRequest("Auto > continue", CommandActor.USER),
        model,
    )

    assert result.admission.allowed is False
    assert result.admission.code == "PRE_MODEL_PRIOR_ATTEMPT_LOOKUP_REQUIRED"
    assert model.calls == 0


def test_stale_authority_snapshot_version_blocks_before_model_call():
    reader = Reader(version=30)
    model = Model()
    result = invoke_after_pre_model_admission(
        gateway(reader),
        state(),
        PreModelContextRequest("Auto > continue", CommandActor.USER),
        model,
    )

    assert result.admission.allowed is False
    assert result.admission.code == "PRE_MODEL_STALE_OPERATIONAL_CONTEXT"
    assert model.calls == 0


def test_verified_projection_without_canonical_authority_refs_still_blocks():
    reader = Reader(authority_refs=())
    model = Model()
    result = invoke_after_pre_model_admission(
        gateway(reader),
        state(),
        PreModelContextRequest("Auto > continue", CommandActor.USER),
        model,
    )

    assert result.admission.allowed is False
    assert result.admission.code == "PRE_MODEL_AUTHORITY_REFS_REQUIRED"
    assert model.calls == 0
