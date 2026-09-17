from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
import re

from .active_work_signal import ActiveWorkSlot, ActiveWorkStatus
from .active_work_transition import ActiveWorkTransition, classify_active_work_transition


_INTENT_FINGERPRINT = re.compile(r"^sha256:[0-9a-f]{64}$")


class ActiveWorkReleaseReason(StrEnum):
    COMPLETE = "COMPLETE"
    EXPIRE = "EXPIRE"
    RECONCILE = "RECONCILE"


@dataclass(frozen=True)
class ActiveWorkMutationPlan:
    """Pure planned slot delta; never performs Google Docs persistence.

    `expected_generation` and `expected_ref` are preconditions for a later writer.
    Google Docs revision-CAS remains an additional document-level precondition;
    this object does not replace it or claim provider success.
    """

    transition: ActiveWorkTransition
    slot_id: str
    expected_generation: int
    after: ActiveWorkSlot
    expected_ref: str = ""
    release_reason: ActiveWorkReleaseReason | None = None


def _required_value(value: str, field: str) -> str:
    candidate = value.strip()
    if not candidate or candidate != value or candidate == "NONE":
        raise ValueError(f"ACTIVE_WORK_PLAN_{field}_REQUIRED")
    if "\n" in candidate or "\r" in candidate:
        raise ValueError(f"ACTIVE_WORK_PLAN_{field}_LINE_BREAK_INVALID")
    return candidate


def _required_identity(work_key: str, intent_fingerprint: str) -> tuple[str, str]:
    key = _required_value(work_key, "WORK_KEY")
    fingerprint = _required_value(intent_fingerprint, "INTENT_FINGERPRINT")
    if _INTENT_FINGERPRINT.fullmatch(fingerprint) is None:
        raise ValueError("ACTIVE_WORK_PLAN_INTENT_FINGERPRINT_INVALID")
    return key, fingerprint


def _require_aware(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"ACTIVE_WORK_PLAN_{field}_TIMEZONE_REQUIRED")


def _require_generation(before: ActiveWorkSlot, expected_generation: int) -> None:
    if expected_generation < 0 or before.generation != expected_generation:
        raise ValueError("ACTIVE_WORK_PLAN_STALE_GENERATION")


def _require_active_ref(before: ActiveWorkSlot, expected_ref: str) -> str:
    if before.status != ActiveWorkStatus.ACTIVE:
        raise ValueError("ACTIVE_WORK_PLAN_ACTIVE_SLOT_REQUIRED")
    candidate = expected_ref.strip()
    if not candidate or candidate != expected_ref:
        raise ValueError("ACTIVE_WORK_PLAN_EXPECTED_REF_REQUIRED")
    if before.ref != candidate:
        raise ValueError("ACTIVE_WORK_PLAN_STALE_REF")
    return candidate


def plan_active_work_claim(
    before: ActiveWorkSlot,
    *,
    expected_generation: int,
    run_key: str,
    objective: str,
    target: str,
    execution_lane: str,
    work_state: str,
    started_at: datetime,
    expires_at: datetime,
    expected_delta: str,
    readback_state: str,
    owner: str,
    work_key: str,
    intent_fingerprint: str,
) -> ActiveWorkMutationPlan:
    """Plan EMPTY(g) -> ACTIVE(g+1) with an atomic semantic identity pair."""

    _require_generation(before, expected_generation)
    if before.status != ActiveWorkStatus.EMPTY:
        raise ValueError("ACTIVE_WORK_PLAN_CLAIM_REQUIRES_EMPTY")

    _require_aware(started_at, "STARTED_AT")
    _require_aware(expires_at, "EXPIRES_AT")
    if started_at >= expires_at:
        raise ValueError("ACTIVE_WORK_PLAN_CLAIM_TIMELINE_INVALID")
    key, fingerprint = _required_identity(work_key, intent_fingerprint)

    after = ActiveWorkSlot(
        slot_id=before.slot_id,
        status=ActiveWorkStatus.ACTIVE,
        generation=before.generation + 1,
        run_key=_required_value(run_key, "RUN_KEY"),
        objective=_required_value(objective, "OBJECTIVE"),
        target=_required_value(target, "TARGET"),
        execution_lane=_required_value(execution_lane, "EXECUTION_LANE"),
        work_state=_required_value(work_state, "WORK_STATE"),
        started_at=started_at,
        updated_at=started_at,
        expires_at=expires_at,
        expected_delta=_required_value(expected_delta, "EXPECTED_DELTA"),
        readback_state=_required_value(readback_state, "READBACK_STATE"),
        owner=_required_value(owner, "OWNER"),
        work_key=key,
        intent_fingerprint=fingerprint,
    )
    transition = classify_active_work_transition(before, after)
    if transition != ActiveWorkTransition.CLAIM:
        raise ValueError("ACTIVE_WORK_PLAN_CLAIM_TRANSITION_INVALID")
    return ActiveWorkMutationPlan(
        transition=transition,
        slot_id=before.slot_id,
        expected_generation=expected_generation,
        after=after,
    )


def plan_active_work_refresh(
    before: ActiveWorkSlot,
    *,
    expected_generation: int,
    expected_ref: str,
    updated_at: datetime,
    expires_at: datetime,
    work_state: str | None = None,
    readback_state: str | None = None,
) -> ActiveWorkMutationPlan:
    """Plan same-generation lease/progress refresh without rebinding identity."""

    _require_generation(before, expected_generation)
    active_ref = _require_active_ref(before, expected_ref)
    _require_aware(updated_at, "UPDATED_AT")
    _require_aware(expires_at, "EXPIRES_AT")
    if updated_at >= expires_at:
        raise ValueError("ACTIVE_WORK_PLAN_REFRESH_TIMELINE_INVALID")

    next_work_state = before.work_state
    if work_state is not None:
        next_work_state = _required_value(work_state, "WORK_STATE")
    next_readback_state = before.readback_state
    if readback_state is not None:
        next_readback_state = _required_value(readback_state, "READBACK_STATE")

    after = replace(
        before,
        work_state=next_work_state,
        readback_state=next_readback_state,
        updated_at=updated_at,
        expires_at=expires_at,
    )
    transition = classify_active_work_transition(before, after)
    if transition != ActiveWorkTransition.REFRESH:
        raise ValueError("ACTIVE_WORK_PLAN_REFRESH_TRANSITION_INVALID")
    return ActiveWorkMutationPlan(
        transition=transition,
        slot_id=before.slot_id,
        expected_generation=expected_generation,
        expected_ref=active_ref,
        after=after,
    )


def plan_active_work_release(
    before: ActiveWorkSlot,
    *,
    expected_generation: int,
    expected_ref: str,
    reason: ActiveWorkReleaseReason,
) -> ActiveWorkMutationPlan:
    """Plan ACTIVE(g) -> EMPTY(g+1), atomically clearing all payload and identity."""

    _require_generation(before, expected_generation)
    active_ref = _require_active_ref(before, expected_ref)
    if not isinstance(reason, ActiveWorkReleaseReason):
        raise ValueError("ACTIVE_WORK_PLAN_RELEASE_REASON_INVALID")

    after = ActiveWorkSlot(
        slot_id=before.slot_id,
        status=ActiveWorkStatus.EMPTY,
        generation=before.generation + 1,
        run_key="NONE",
        objective="NONE",
        target="NONE",
        execution_lane="NONE",
        work_state="NONE",
        started_at=None,
        updated_at=None,
        expires_at=None,
        expected_delta="NONE",
        readback_state="NONE",
        owner="NONE",
        work_key=None,
        intent_fingerprint=None,
    )
    transition = classify_active_work_transition(before, after)
    if transition != ActiveWorkTransition.RELEASE:
        raise ValueError("ACTIVE_WORK_PLAN_RELEASE_TRANSITION_INVALID")
    return ActiveWorkMutationPlan(
        transition=transition,
        slot_id=before.slot_id,
        expected_generation=expected_generation,
        expected_ref=active_ref,
        after=after,
        release_reason=reason,
    )
