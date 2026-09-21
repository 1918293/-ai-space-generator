from datetime import datetime, timedelta, timezone

import pytest

from src.action_catalog import ActionBinding, ActionCatalog, ModelActionIntent
from src.context_bound_reasoning import (
    AdmittedContextItem,
    ContextBoundPreModelGateway,
    ContextBoundReasoningIngress,
)
from src.control_gateway import (
    ControlPlaneGateway,
    PreModelContextGateway,
    PreModelContextRequest,
    PreModelContextResolution,
    TaskExecutionPolicy,
)
from src.controlled_runner import (
    ToolOutcome,
    VerificationOutcome,
    run_controlled_action,
)
from src.execution_control import (
    ActionArchetype,
    ActionExternality,
    EvidenceKind,
    EvidenceReceipt,
    Mode,
    RunPhase,
    render_header,
)
from src.operational_state import ActiveOperationalState, CommandActor


TASK = "2026-09-21 Auto tool recovery bounded replay"
HEAVY_BINDING = "drive.search.heavy"
METADATA_BINDING = "drive.search.metadata"


def state():
    return ActiveOperationalState(Mode.EXP, TASK, 483, "EVENT-20260921-REPLAY")


def request():
    return PreModelContextRequest(
        "Auto > 繼續",
        CommandActor.USER,
        "EVENT-20260921-REPLAY",
    )


class StructuralResolver:
    def resolve(self, current_state, current_request, checkpoint_cue):
        assert current_state.mode == Mode.EXP
        assert current_state.task == TASK
        assert current_request.actor == CommandActor.USER
        assert checkpoint_cue == ""
        return PreModelContextResolution(
            checkpoint_id="R483",
            task=TASK,
            operational_version=483,
            authority_refs=("CURRENT:HAO_SYSTEM",),
            existing_work_refs=("PR17:CURRENT_HEAD",),
            prior_attempt_refs=("INCIDENT:20260921:DRIVE_NO_RESULT",),
            regression_refs=("REG-FAMILY-20260828-F4-EFFICIENCY-RESPONSIBILITY",),
            existing_work_lookup_complete=True,
            prior_attempt_lookup_complete=True,
            regression_lookup_complete=True,
            reuse_disposition="REUSE",
        )


class SemanticResolver:
    def resolve(self, current_state, current_request, receipt):
        assert receipt.mode == Mode.EXP
        return (
            AdmittedContextItem(
                ref="CURRENT:HAO_SYSTEM",
                kind="CURRENT_CONTROL",
                summary=(
                    "Auto/Continue is execution continuation. A materially required "
                    "available tool must be dispatched; no-result recovery must not "
                    "blindly repeat the same heavy route."
                ),
                source_version="06_Config:2026-09-21T22:04:24+08:00",
                project_scope="HAO_SYSTEM",
                disposition="APPLY",
            ),
            AdmittedContextItem(
                ref="PR17:CURRENT_HEAD",
                kind="EXISTING_WORK",
                summary="Reuse the existing Runtime v2 ControlPlane; do not build a parallel executor.",
                source_version="0e4daea14f769e39068b4869ca2295e03d4cfa62",
                project_scope="HAO_SYSTEM",
                disposition="REUSE",
            ),
            AdmittedContextItem(
                ref="INCIDENT:20260921:DRIVE_NO_RESULT",
                kind="PRIOR_ATTEMPT",
                summary=(
                    "The heavy best-effort/content-hydration Drive route returned no "
                    "usable result. Retry must downgrade to metadata-only/smaller scope."
                ),
                source_version="2026-09-21",
                project_scope="HAO_SYSTEM",
                disposition="DO_NOT_REPEAT",
                binding_id=HEAVY_BINDING,
            ),
            AdmittedContextItem(
                ref="REG-FAMILY-20260828-F4-EFFICIENCY-RESPONSIBILITY",
                kind="REGRESSION",
                summary=(
                    "Same-condition failure must not be retried and assistant-owned "
                    "tool execution must not be delegated back to Hao."
                ),
                source_version="2026-08-28",
                project_scope="HAO_SYSTEM",
                disposition="APPLY",
            ),
        )


class PolicyProvider:
    def resolve(self, current_state):
        assert current_state.task == TASK
        return TaskExecutionPolicy(
            goal_valid=True,
            acceptance_criteria=("dispatch the bounded trusted Drive retrieval route",),
        )


def catalog():
    return ActionCatalog(
        (
            ActionBinding(
                HEAVY_BINDING,
                "drive_search",
                "google-drive",
                "search_best_effort_content",
                ActionArchetype.RETRIEVE,
                ActionExternality.READ_ONLY,
            ),
            ActionBinding(
                METADATA_BINDING,
                "drive_search",
                "google-drive",
                "search_metadata_only",
                ActionArchetype.RETRIEVE,
                ActionExternality.READ_ONLY,
            ),
        )
    )


class IntentModel:
    def __init__(self, binding_id):
        self.binding_id = binding_id
        self.calls = 0

    def invoke(self, model_input):
        self.calls += 1
        return ModelActionIntent(
            intent_id="INTENT-20260921-" + self.binding_id,
            requested_capability="drive_search",
            binding_id=self.binding_id,
            expected_state_delta="bounded Drive retrieval",
            model_reported_used_refs=(
                "CURRENT:HAO_SYSTEM",
                "INCIDENT:20260921:DRIVE_NO_RESULT",
            ),
        )


def ingress(binding_id):
    return ContextBoundReasoningIngress(
        pre_model=ContextBoundPreModelGateway(
            PreModelContextGateway(StructuralResolver()),
            SemanticResolver(),
        ),
        model=IntentModel(binding_id),
        control_plane=ControlPlaneGateway(catalog(), PolicyProvider()),
    )


class CapturingExecutor:
    def __init__(self):
        self.calls = []

    def execute(self, proposal):
        self.calls.append(proposal)
        return ToolOutcome(
            True,
            receipt_id="TOOL-20260921-METADATA",
            source="google-drive:bounded-replay",
        )


class PassingVerifier:
    def verify(self, record, proposal, tool_outcome):
        return VerificationOutcome(
            True,
            receipts=(
                EvidenceReceipt(
                    evidence_id="VERIFY-20260921-METADATA",
                    kind=EvidenceKind.VERIFICATION_PASS,
                    passed=True,
                    source="runtime-v2:bounded-replay",
                    claim_scope=proposal.action_id,
                ),
                EvidenceReceipt(
                    evidence_id="ACCEPT-20260921-METADATA",
                    kind=EvidenceKind.ACCEPTANCE_GATE_PASS,
                    passed=True,
                    source="runtime-v2:bounded-replay",
                    claim_scope=proposal.action_id,
                    gate_id="BOUNDED_TOOL_DISPATCH_REPLAY",
                ),
            ),
        )


def test_auto_continue_dispatches_trusted_google_drive_tool_route():
    prepared = ingress(METADATA_BINDING).prepare(
        state(),
        request(),
        run_id="RUN-20260921-AUTO-DISPATCH",
    )

    assert prepared.prepared is not None
    assert prepared.prepared.record is not None
    proposal = prepared.prepared.resolution.proposal
    assert proposal is not None
    assert prepared.prepared.record.mode == Mode.EXP
    assert prepared.prepared.record.task == TASK
    assert proposal.provider == "google-drive"
    assert proposal.action_name == "search_metadata_only"

    executor = CapturingExecutor()
    executed = run_controlled_action(
        prepared.prepared.record,
        proposal,
        executor=executor,
        verifier=PassingVerifier(),
    )

    assert len(executor.calls) == 1
    assert executor.calls[0].action_id == proposal.action_id
    assert executed.admission.allowed is True
    assert executed.completion.allowed is True
    assert executed.record.phase == RunPhase.CLOSED


def test_no_result_recovery_allows_metadata_downgrade_route():
    result = ingress(METADATA_BINDING).prepare(
        state(),
        request(),
        run_id="RUN-20260921-DOWNGRADE",
    )

    assert result.code == "MODEL_INTENT_RESOLVED_TO_TRUSTED_BINDING"
    assert result.prepared is not None
    proposal = result.prepared.resolution.proposal
    assert proposal is not None
    assert proposal.action_name == "search_metadata_only"
    assert proposal.action_name != "search_best_effort_content"


def test_same_failed_heavy_route_is_denied_before_tool_dispatch():
    result = ingress(HEAVY_BINDING).prepare(
        state(),
        request(),
        run_id="RUN-20260921-SAME-ROUTE",
    )

    assert result.code == "PRE_MODEL_KNOWN_FAILURE_REPEAT_BLOCKED"
    assert result.prepared is None


def test_fresh_header_output_guard_derives_date_and_time_from_one_source():
    plus_8 = timezone(timedelta(hours=8))
    observed = datetime(2026, 9, 21, 23, 1, 5, tzinfo=plus_8)
    now = datetime(2026, 9, 21, 23, 1, 20, tzinfo=plus_8)

    assert render_header(state_record(), observed_at=observed, trusted_now=now) == (
        f"[MODE=EXP][TASK={TASK}]\n"
        "[DATE=2026-09-21][TIME=23:01+08:00]"
    )


def test_header_output_guard_rejects_stale_or_wrong_timezone_source():
    plus_8 = timezone(timedelta(hours=8))
    utc = timezone.utc
    now = datetime(2026, 9, 21, 23, 3, 0, tzinfo=plus_8)

    with pytest.raises(ValueError, match="HEADER_TIMESTAMP_STALE"):
        render_header(
            state_record(),
            observed_at=now - timedelta(minutes=3),
            trusted_now=now,
        )

    with pytest.raises(ValueError, match="HEADER_LOCAL_OFFSET_REQUIRED"):
        render_header(
            state_record(),
            observed_at=datetime(2026, 9, 21, 15, 3, 0, tzinfo=utc),
            trusted_now=now,
        )


def test_header_output_guard_rejects_missing_timezone():
    plus_8 = timezone(timedelta(hours=8))
    with pytest.raises(ValueError, match="HEADER_OBSERVED_TIMEZONE_REQUIRED"):
        render_header(
            state_record(),
            observed_at=datetime(2026, 9, 21, 23, 1, 5),
            trusted_now=datetime(2026, 9, 21, 23, 1, 20, tzinfo=plus_8),
        )


def state_record():
    result = ingress(METADATA_BINDING).prepare(
        state(),
        request(),
        run_id="RUN-20260921-HEADER",
    )
    assert result.prepared is not None
    assert result.prepared.record is not None
    return result.prepared.record
