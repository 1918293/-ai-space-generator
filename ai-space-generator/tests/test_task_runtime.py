from src.execution_control import Mode, RunPhase
from src.task_runtime import (
    ChildActionOutcome,
    ParentTaskPhase,
    ParentTaskRecord,
    close_parent_task,
    evaluate_parent_completion,
    record_child_outcome,
    record_hao_task_acceptance,
    record_task_gate_pass,
)


def task(**kwargs):
    values = dict(
        task_run_id="TASK-RUN-1",
        task="Multi-action Hao task",
        mode=Mode.EXP,
        admitted_operational_version=7,
        required_action_ids=("A1", "A2"),
        required_gate_ids=("READBACK", "VERIFY"),
        hao_acceptance_required=True,
    )
    values.update(kwargs)
    return ParentTaskRecord(**values)


def closed(action_id: str, authoritative: bool = True):
    return ChildActionOutcome(action_id, RunPhase.CLOSED, authoritative)


def test_one_successful_child_cannot_close_parent_task():
    record = record_child_outcome(task(), closed("A1"))
    decision = evaluate_parent_completion(record, 7)
    assert decision.allowed is False
    assert decision.code == "REQUIRED_CHILD_OUTCOME_MISSING:A2"
    assert decision.next_phase == ParentTaskPhase.RUNNING


def test_unsynced_child_forces_parent_reconciliation():
    record = record_child_outcome(
        task(),
        ChildActionOutcome("A1", RunPhase.UNSYNCED, False, "UNKNOWN_EFFECT"),
    )
    assert record.phase == ParentTaskPhase.RECONCILIATION_REQUIRED
    decision = evaluate_parent_completion(record, 7)
    assert decision.allowed is False
    assert decision.code == "CHILD_RECONCILIATION_REQUIRED:A1"


def test_non_authoritative_child_cannot_satisfy_parent_completion():
    record = record_child_outcome(task(), closed("A1"))
    record = record_child_outcome(record, closed("A2", authoritative=False))
    decision = evaluate_parent_completion(record, 7)
    assert decision.allowed is False
    assert decision.code == "CHILD_NOT_AUTHORITATIVE:A2"


def test_named_task_gates_are_independent():
    record = record_child_outcome(task(), closed("A1"))
    record = record_child_outcome(record, closed("A2"))
    record = record_task_gate_pass(record, "READBACK")
    decision = evaluate_parent_completion(record, 7)
    assert decision.allowed is False
    assert decision.code == "TASK_GATE_MISSING:VERIFY"
    assert decision.next_phase == ParentTaskPhase.VERIFIED


def test_hao_acceptance_is_required_when_policy_says_so():
    record = record_child_outcome(task(), closed("A1"))
    record = record_child_outcome(record, closed("A2"))
    record = record_task_gate_pass(record, "READBACK")
    record = record_task_gate_pass(record, "VERIFY")
    decision = evaluate_parent_completion(record, 7)
    assert decision.allowed is False
    assert decision.code == "HAO_TASK_ACCEPTANCE_REQUIRED"
    assert decision.next_phase == ParentTaskPhase.AWAITING_HAO


def test_stale_operational_context_cannot_close_current_task():
    record = record_child_outcome(task(), closed("A1"))
    record = record_child_outcome(record, closed("A2"))
    record = record_task_gate_pass(record, "READBACK")
    record = record_task_gate_pass(record, "VERIFY")
    record = record_hao_task_acceptance(record, True)
    decision = evaluate_parent_completion(record, 8)
    assert decision.allowed is False
    assert decision.code == "STALE_OPERATIONAL_CONTEXT"
    assert decision.next_phase == ParentTaskPhase.RECONCILIATION_REQUIRED


def test_parent_task_closes_only_after_all_children_gates_and_hao_acceptance():
    record = record_child_outcome(task(), closed("A1"))
    record = record_child_outcome(record, closed("A2"))
    record = record_task_gate_pass(record, "READBACK")
    record = record_task_gate_pass(record, "VERIFY")
    record = record_hao_task_acceptance(record, True)
    closed_record = close_parent_task(record, 7)
    assert closed_record.phase == ParentTaskPhase.CLOSED
    assert closed_record.failure_code == ""


def test_conflicting_duplicate_child_outcome_is_rejected():
    import pytest

    record = record_child_outcome(task(), closed("A1"))
    with pytest.raises(ValueError, match="CONFLICTING_CHILD_OUTCOME:A1"):
        record_child_outcome(
            record,
            ChildActionOutcome("A1", RunPhase.FAILED, False, "FAILED_LATER"),
        )
