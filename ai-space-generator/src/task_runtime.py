from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum

from .execution_control import Mode, RunPhase


class ParentTaskPhase(StrEnum):
    OPEN = "OPEN"
    RUNNING = "RUNNING"
    AWAITING_HAO = "AWAITING_HAO"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"
    VERIFIED = "VERIFIED"
    CLOSED = "CLOSED"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"


@dataclass(frozen=True)
class ChildActionOutcome:
    action_id: str
    phase: RunPhase
    authoritative: bool
    failure_code: str = ""


@dataclass(frozen=True)
class ParentTaskRecord:
    task_run_id: str
    task: str
    mode: Mode
    admitted_operational_version: int
    required_action_ids: tuple[str, ...] = ()
    required_gate_ids: tuple[str, ...] = ()
    hao_acceptance_required: bool = False
    authority_snapshot_fingerprint: str = ""
    child_outcomes: tuple[ChildActionOutcome, ...] = ()
    passed_gate_ids: tuple[str, ...] = ()
    hao_accepted: bool = False
    phase: ParentTaskPhase = ParentTaskPhase.OPEN
    failure_code: str = ""


@dataclass(frozen=True)
class ParentTaskDecision:
    allowed: bool
    code: str
    next_phase: ParentTaskPhase


def _normalized_unique(values: tuple[str, ...], *, field: str) -> tuple[str, ...]:
    normalized = tuple(value.strip() for value in values if value.strip())
    if len(set(normalized)) != len(normalized):
        raise ValueError(f"DUPLICATE_{field}")
    return normalized


def validate_parent_task(record: ParentTaskRecord) -> ParentTaskRecord:
    if not record.task_run_id.strip():
        raise ValueError("TASK_RUN_ID_REQUIRED")
    if not record.task.strip():
        raise ValueError("TASK_REQUIRED")
    if record.admitted_operational_version < 1:
        raise ValueError("ADMITTED_OPERATIONAL_VERSION_REQUIRED")
    required_actions = _normalized_unique(record.required_action_ids, field="REQUIRED_ACTION_ID")
    required_gates = _normalized_unique(record.required_gate_ids, field="REQUIRED_GATE_ID")
    if record.phase == ParentTaskPhase.CLOSED:
        decision = evaluate_parent_completion(record, record.admitted_operational_version)
        if not decision.allowed:
            raise ValueError("INVALID_CLOSED_PARENT_TASK:" + decision.code)
    return replace(
        record,
        task_run_id=record.task_run_id.strip(),
        task=record.task.strip(),
        required_action_ids=required_actions,
        required_gate_ids=required_gates,
    )


def record_child_outcome(
    record: ParentTaskRecord,
    outcome: ChildActionOutcome,
) -> ParentTaskRecord:
    action_id = outcome.action_id.strip()
    if not action_id:
        raise ValueError("CHILD_ACTION_ID_REQUIRED")
    if record.phase == ParentTaskPhase.CLOSED:
        raise ValueError("PARENT_TASK_ALREADY_CLOSED")
    if record.required_action_ids and action_id not in set(record.required_action_ids):
        raise ValueError("UNEXPECTED_CHILD_ACTION:" + action_id)

    existing = {item.action_id: item for item in record.child_outcomes}
    prior = existing.get(action_id)
    normalized = ChildActionOutcome(
        action_id=action_id,
        phase=outcome.phase,
        authoritative=outcome.authoritative,
        failure_code=outcome.failure_code.strip(),
    )
    if prior is not None:
        if prior == normalized:
            return record
        raise ValueError("CONFLICTING_CHILD_OUTCOME:" + action_id)

    next_outcomes = record.child_outcomes + (normalized,)
    next_phase = ParentTaskPhase.RUNNING
    failure_code = ""

    if normalized.phase == RunPhase.AWAITING_HAO:
        next_phase = ParentTaskPhase.AWAITING_HAO
    elif normalized.phase == RunPhase.UNSYNCED:
        next_phase = ParentTaskPhase.RECONCILIATION_REQUIRED
        failure_code = normalized.failure_code or "CHILD_UNSYNCED"
    elif normalized.phase == RunPhase.FAILED:
        next_phase = ParentTaskPhase.FAILED
        failure_code = normalized.failure_code or "CHILD_FAILED"
    elif normalized.phase == RunPhase.BLOCKED:
        next_phase = ParentTaskPhase.BLOCKED
        failure_code = normalized.failure_code or "CHILD_BLOCKED"

    return replace(
        record,
        child_outcomes=next_outcomes,
        phase=next_phase,
        failure_code=failure_code,
    )


def record_task_gate_pass(record: ParentTaskRecord, gate_id: str) -> ParentTaskRecord:
    gate_id = gate_id.strip()
    if not gate_id:
        raise ValueError("TASK_GATE_ID_REQUIRED")
    if record.required_gate_ids and gate_id not in set(record.required_gate_ids):
        raise ValueError("UNEXPECTED_TASK_GATE:" + gate_id)
    if gate_id in set(record.passed_gate_ids):
        return record
    return replace(record, passed_gate_ids=record.passed_gate_ids + (gate_id,))


def record_hao_task_acceptance(record: ParentTaskRecord, accepted: bool) -> ParentTaskRecord:
    if not accepted:
        return replace(record, hao_accepted=False, phase=ParentTaskPhase.AWAITING_HAO)
    return replace(record, hao_accepted=True)


def evaluate_parent_completion(
    record: ParentTaskRecord,
    current_operational_version: int,
) -> ParentTaskDecision:
    if current_operational_version < 1:
        return ParentTaskDecision(
            False,
            "CURRENT_OPERATIONAL_VERSION_REQUIRED",
            ParentTaskPhase.BLOCKED,
        )

    if current_operational_version != record.admitted_operational_version:
        return ParentTaskDecision(
            False,
            "STALE_OPERATIONAL_CONTEXT",
            ParentTaskPhase.RECONCILIATION_REQUIRED,
        )

    outcomes = {item.action_id: item for item in record.child_outcomes}
    for action_id in record.required_action_ids:
        outcome = outcomes.get(action_id)
        if outcome is None:
            return ParentTaskDecision(
                False,
                "REQUIRED_CHILD_OUTCOME_MISSING:" + action_id,
                ParentTaskPhase.RUNNING,
            )
        if outcome.phase == RunPhase.UNSYNCED:
            return ParentTaskDecision(
                False,
                "CHILD_RECONCILIATION_REQUIRED:" + action_id,
                ParentTaskPhase.RECONCILIATION_REQUIRED,
            )
        if outcome.phase == RunPhase.AWAITING_HAO:
            return ParentTaskDecision(
                False,
                "CHILD_AWAITING_HAO:" + action_id,
                ParentTaskPhase.AWAITING_HAO,
            )
        if outcome.phase == RunPhase.BLOCKED:
            return ParentTaskDecision(
                False,
                "CHILD_BLOCKED:" + action_id,
                ParentTaskPhase.BLOCKED,
            )
        if outcome.phase == RunPhase.FAILED:
            return ParentTaskDecision(
                False,
                "CHILD_FAILED:" + action_id,
                ParentTaskPhase.FAILED,
            )
        if outcome.phase != RunPhase.CLOSED:
            return ParentTaskDecision(
                False,
                "CHILD_NOT_CLOSED:" + action_id + ":" + outcome.phase.value,
                ParentTaskPhase.RUNNING,
            )
        if not outcome.authoritative:
            return ParentTaskDecision(
                False,
                "CHILD_NOT_AUTHORITATIVE:" + action_id,
                ParentTaskPhase.BLOCKED,
            )

    passed_gates = set(record.passed_gate_ids)
    for gate_id in record.required_gate_ids:
        if gate_id not in passed_gates:
            return ParentTaskDecision(
                False,
                "TASK_GATE_MISSING:" + gate_id,
                ParentTaskPhase.VERIFIED,
            )

    if record.hao_acceptance_required and not record.hao_accepted:
        return ParentTaskDecision(
            False,
            "HAO_TASK_ACCEPTANCE_REQUIRED",
            ParentTaskPhase.AWAITING_HAO,
        )

    return ParentTaskDecision(True, "PARENT_TASK_COMPLETION_ALLOWED", ParentTaskPhase.CLOSED)


def close_parent_task(
    record: ParentTaskRecord,
    current_operational_version: int,
) -> ParentTaskRecord:
    decision = evaluate_parent_completion(record, current_operational_version)
    if not decision.allowed:
        return replace(record, phase=decision.next_phase, failure_code=decision.code)
    return replace(record, phase=ParentTaskPhase.CLOSED, failure_code="")
