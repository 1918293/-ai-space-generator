from src.controlled_runner import ToolOutcome, VerificationOutcome, run_controlled_action
from src.execution_control import (
    ActionArchetype,
    ActionExternality,
    ActionProposal,
    CompletionClaim,
    EvidenceKind,
    EvidenceReceipt,
    ExecutionRecord,
    FailureStage,
    Mode,
    RunPhase,
    add_evidence,
    admit_action,
    can_claim,
    can_retry,
    mark_unsynced,
    render_header,
    transition,
)


def record(**overrides):
    values = dict(
        run_id="RUN-REGRESSION",
        task="Historical regression gate",
        mode=Mode.EXP,
        goal_valid=True,
        acceptance_criteria=("invalid transition must be impossible",),
        authority_refs=("CURRENT-AUTHORITY",),
    )
    values.update(overrides)
    return ExecutionRecord(**values)


def proposal(**overrides):
    values = dict(
        action_id="ACT-REGRESSION",
        archetype=ActionArchetype.READ,
        externality=ActionExternality.READ_ONLY,
        capability="read",
        provider="fake",
        action_name="read",
        required_authority_refs=("CURRENT-AUTHORITY",),
    )
    values.update(overrides)
    return ActionProposal(**values)


def mutation(**overrides):
    values = dict(
        action_id="ACT-MUTATION",
        archetype=ActionArchetype.MUTATE,
        externality=ActionExternality.PRIVATE_REVERSIBLE,
        capability="persist",
        provider="fake",
        action_name="write",
        expected_state_delta="one bounded state change",
        required_authority_refs=("CURRENT-AUTHORITY",),
        idempotency_key="RUN-REGRESSION:ACT-MUTATION",
    )
    values.update(overrides)
    return ActionProposal(**values)


class CountingExecutor:
    def __init__(self):
        self.calls = 0

    def execute(self, action):
        self.calls += 1
        return ToolOutcome(True, "TOOL-R", "fake")


class PassingVerifier:
    def verify(self, current, action, tool_outcome):
        return VerificationOutcome(
            True,
            receipts=(
                EvidenceReceipt(
                    "VERIFY-R",
                    EvidenceKind.VERIFICATION_PASS,
                    True,
                    "verifier",
                    claim_scope=action.action_id,
                ),
                EvidenceReceipt(
                    "GATE-R",
                    EvidenceKind.ACCEPTANCE_GATE_PASS,
                    True,
                    "verifier",
                    claim_scope=action.action_id,
                ),
            ),
        )


def test_regression_mode_render_cannot_diverge_from_control_state():
    current = record(mode=Mode.EXP, task="Stable task")
    header = render_header(current, date="2026-08-30", time_with_offset="09:45+08:00")
    assert header.startswith("[MODE=EXP][TASK=Stable task]")
    assert "MODE=SYS" not in header


def test_regression_record_intent_cannot_route_to_memory_when_formal_persistence_required():
    current = record(
        required_action_tags=("FORMAL_HAO_PERSISTENCE",),
        forbidden_action_tags=("CHATGPT_MEMORY",),
    )
    memory_action = mutation(
        provider="chatgpt-memory",
        action_name="remember",
        assurance_tags=("CHATGPT_MEMORY",),
    )
    updated, decision = admit_action(current, memory_action)
    assert decision.allowed is False
    assert updated.phase == RunPhase.BLOCKED


def test_regression_exact_local_edit_cannot_silently_become_full_generation():
    current = record(
        required_action_tags=("LOCAL_EDIT", "PRESERVE_OUTSIDE_MASK"),
        forbidden_action_tags=("FULL_GENERATION",),
    )
    full_generation = mutation(
        capability="image_generation",
        provider="image-model",
        action_name="generate",
        assurance_tags=("FULL_GENERATION",),
    )
    _, decision = admit_action(current, full_generation)
    assert decision.allowed is False
    assert decision.failed_at == FailureStage.POLICY


def test_regression_stale_authority_reference_blocks_before_provider_call():
    current = record(authority_refs=("CURRENT-AUTHORITY",))
    stale = proposal(required_authority_refs=("STALE-HANDOFF",))
    _, decision = admit_action(current, stale)
    assert decision.allowed is False
    assert decision.failed_at == FailureStage.AUTHORITY


def test_regression_index_search_miss_cannot_be_used_as_content_absence_proof():
    current = record(
        required_action_tags=("DIRECT_CONTENT_READBACK",),
        forbidden_action_tags=("INDEX_ONLY_ABSENCE",),
    )
    index_only = proposal(
        capability="search",
        action_name="code_search",
        assurance_tags=("INDEX_ONLY_ABSENCE",),
    )
    _, decision = admit_action(current, index_only)
    assert decision.allowed is False


def test_regression_old_verification_receipts_cannot_complete_new_action():
    current_action = mutation()
    current = record(action=current_action, phase=RunPhase.VERIFIED)
    for kind in (
        EvidenceKind.TOOL_RECEIPT,
        EvidenceKind.STATE_READBACK,
        EvidenceKind.VERIFICATION_PASS,
        EvidenceKind.ACCEPTANCE_GATE_PASS,
    ):
        current = add_evidence(
            current,
            EvidenceReceipt(
                f"OLD-{kind.value}",
                kind,
                True,
                "old-run",
                claim_scope="PREVIOUS-ACTION",
            ),
        )
    assert can_claim(current, CompletionClaim.COMPLETED).allowed is False


def test_regression_api_success_without_readback_cannot_be_persisted_or_completed():
    action = mutation()
    current = record(action=action, phase=RunPhase.OBSERVED)
    current = add_evidence(
        current,
        EvidenceReceipt(
            "TOOL-ONLY",
            EvidenceKind.TOOL_RECEIPT,
            True,
            "provider",
            claim_scope=action.action_id,
        ),
    )
    assert can_claim(current, CompletionClaim.PERSISTED).allowed is False
    assert can_claim(current, CompletionClaim.COMPLETED).allowed is False


def test_regression_side_effect_success_then_readback_failure_becomes_unsynced():
    action = mutation()
    current = record(action=action, phase=RunPhase.OBSERVED)
    unsynced = mark_unsynced(
        current,
        stage=FailureStage.PERSISTENCE,
        code="READBACK_MISMATCH",
        mechanism="wrong-target-state",
    )
    assert unsynced.phase == RunPhase.UNSYNCED
    assert can_claim(unsynced, CompletionClaim.COMPLETED).allowed is False


def test_regression_same_failure_cannot_be_retried_by_rewording_without_material_delta():
    action = mutation()
    failed = mark_unsynced(
        record(action=action, phase=RunPhase.OBSERVED),
        code="READBACK_MISMATCH",
        mechanism="wrong-target-state",
    )
    decision = can_retry(
        failed,
        mechanism="wrong-target-state",
        material_delta=False,
        retry_basis="changed wording only",
    )
    assert decision.allowed is False
    assert decision.code == "NO_DELTA_RETRY_BLOCKED"


def test_regression_external_action_without_exact_scope_never_reaches_executor():
    external = mutation(
        archetype=ActionArchetype.PUBLISH,
        externality=ActionExternality.EXTERNAL_REVERSIBLE,
        authorization_scope="SEND_EXTERNAL",
    )
    executor = CountingExecutor()
    result = run_controlled_action(
        record(),
        external,
        executor=executor,
        verifier=PassingVerifier(),
    )
    assert executor.calls == 0
    assert result.record.phase == RunPhase.AWAITING_HAO


def test_regression_execution_cannot_skip_observation_to_self_verify():
    admitted, decision = admit_action(record(), proposal())
    assert decision.allowed is True
    executing = transition(admitted, RunPhase.EXECUTING)
    try:
        transition(executing, RunPhase.VERIFIED)
    except ValueError as exc:
        assert "ILLEGAL_TRANSITION" in str(exc)
    else:
        raise AssertionError("execution was allowed to self-verify without observation")
