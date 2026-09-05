from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.hao_authority_resolver import (
    HaoAuthorityRoutes,
    HaoCanonicalCurrent,
    HaoDriveCanonicalAuthoritySource,
    HaoExistingWorkResult,
    HaoLookupResult,
)
from src.mcp_control_bridge import HaoMCPIdentityPolicy, MCPPrincipal
from src.operational_state import (
    CommandActor,
    Mode,
    OperationalCommand,
    SQLiteOperationalStateStore,
)
from src.runtime_deployment import (
    load_parent_task_plans,
    load_sheets_targets,
    load_task_policies,
)
from src.runtime_policy import ConfiguredTaskPolicySpec, GoogleAuthorityTaskPolicyProvider


class FakeAuthorityReader:
    def __init__(self, *, verified=True):
        self.verified = verified

    def resolve_current(self, routes, state, request, checkpoint_cue):
        return HaoCanonicalCurrent(
            checkpoint_id="R200",
            task=state.task,
            operational_version=state.version,
            authority_refs=("canonical:current",),
            verified=self.verified,
        )

    def lookup_existing_work(self, routes, state, request):
        return HaoExistingWorkResult(("canonical:existing",), True, "REUSE")

    def lookup_prior_attempts(self, routes, state, request):
        return HaoLookupResult(("canonical:prior",), True)

    def lookup_regressions(self, routes, state, request):
        return HaoLookupResult(("canonical:regression",), True)


class NeverCalledPolicyClient:
    def current_authority_stamps_sync(self, sources):
        raise AssertionError("missing TaskPolicy must fail before authority client access")


def test_expected_hao_subject_and_per_operation_scope_fail_closed():
    policy = HaoMCPIdentityPolicy("hao-subject")
    with pytest.raises(PermissionError, match="HAO_IDENTITY_REQUIRED"):
        policy.require(MCPPrincipal("other", frozenset({"hao:read"})), "hao:read")
    with pytest.raises(PermissionError, match="MISSING_SCOPE:hao:execute"):
        policy.require(MCPPrincipal("hao-subject", frozenset({"hao:read"})), "hao:execute")
    policy.require(MCPPrincipal("hao-subject", frozenset({"hao:execute"})), "hao:execute")


def test_mode_and_task_mutation_remain_user_authoritative(tmp_path):
    store = SQLiteOperationalStateStore(str(tmp_path / "state.sqlite"))
    original = store.initialize(mode=Mode.EXP, task="Original task")

    model = store.apply(
        OperationalCommand(
            event_id="EVT-MODEL",
            actor=CommandActor.MODEL,
            text="EXE > switch",
            explicit_task="Model task",
            expected_version=original.version,
        )
    )
    assert model.applied is False
    assert model.state.mode == Mode.EXP
    assert model.state.task == "Original task"

    projection = store.apply(
        OperationalCommand(
            event_id="EVT-PROJECTION",
            actor=CommandActor.PROJECTION,
            text="SYS > projected",
            explicit_task="Projected task",
            expected_version=original.version,
        )
    )
    assert projection.applied is False
    assert projection.state.mode == Mode.EXP
    assert projection.state.task == "Original task"

    user = store.apply(
        OperationalCommand(
            event_id="EVT-USER",
            actor=CommandActor.USER,
            text="EXE > explicit Hao command",
            explicit_task="Hao task",
            expected_version=original.version,
        )
    )
    assert user.applied is True
    assert user.state.mode == Mode.EXE
    assert user.state.task == "Hao task"


def test_missing_task_policy_has_no_model_or_default_fallback():
    provider = GoogleAuthorityTaskPolicyProvider(
        NeverCalledPolicyClient(),
        (
            ConfiguredTaskPolicySpec(
                task="Configured task",
                acceptance_criteria=("verified",),
                authority_sources=(SimpleNamespace(ref="authority", file_id="file"),),
            ),
        ),
    )
    state = SimpleNamespace(task="Unconfigured task")
    with pytest.raises(ValueError, match="TASK_POLICY_UNRESOLVED"):
        provider.resolve(state)


def test_projection_route_is_not_promoted_into_semantic_authority():
    routes = HaoAuthorityRoutes(
        current_owner="canonical-current",
        requirements_owner="canonical-requirements",
        continuation_projection="handoff-projection",
        regression_owner="canonical-regression",
        prior_attempt_owner="canonical-prior",
    )
    source = HaoDriveCanonicalAuthoritySource(FakeAuthorityReader(verified=True), routes)
    state = SimpleNamespace(task="Task", version=4)
    snapshot = source.read_context(state, SimpleNamespace(), "R200")
    assert snapshot is not None
    assert snapshot.authority_refs == ("canonical:current",)
    assert "handoff-projection" not in snapshot.authority_refs

    unverified = HaoDriveCanonicalAuthoritySource(FakeAuthorityReader(verified=False), routes)
    assert unverified.read_context(state, SimpleNamespace(), "R200") is None


@pytest.mark.parametrize(
    "loader,key",
    [
        (load_sheets_targets, "HAO_SHEETS_TARGETS_JSON"),
        (load_task_policies, "HAO_TASK_POLICIES_JSON"),
        (load_parent_task_plans, "HAO_PARENT_TASK_PLANS_JSON"),
    ],
)
def test_deployment_owned_authority_contracts_fail_closed(loader, key):
    with pytest.raises(ValueError, match=f"MISSING_CONFIG:{key}"):
        loader({})
    with pytest.raises(ValueError, match=f"INVALID_JSON_CONFIG:{key}"):
        loader({key: "not-json"})
