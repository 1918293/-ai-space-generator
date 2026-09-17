from __future__ import annotations

from enum import StrEnum

from .active_work_signal import ActiveWorkSlot, ActiveWorkStatus


class ActiveWorkTransition(StrEnum):
    NO_CHANGE = "NO_CHANGE"
    CLAIM = "CLAIM"
    REFRESH = "REFRESH"
    RELEASE = "RELEASE"


_REFRESH_STABLE_FIELDS = (
    "run_key",
    "objective",
    "target",
    "execution_lane",
    "started_at",
    "expected_delta",
    "owner",
    "work_key",
    "intent_fingerprint",
)


def classify_active_work_transition(
    before: ActiveWorkSlot,
    after: ActiveWorkSlot,
) -> ActiveWorkTransition:
    """Validate one slot transition without performing persistence.

    This is a bounded post-read validator for the existing Active Work Signal.
    It does not write Drive state, mint identity, or replace revision-CAS.
    """

    if before.slot_id != after.slot_id:
        raise ValueError("ACTIVE_WORK_TRANSITION_SLOT_MISMATCH")

    if before == after:
        return ActiveWorkTransition.NO_CHANGE

    if before.status == ActiveWorkStatus.EMPTY:
        if after.status != ActiveWorkStatus.ACTIVE:
            raise ValueError("ACTIVE_WORK_TRANSITION_EMPTY_INVALID")
        if after.generation != before.generation + 1:
            raise ValueError("ACTIVE_WORK_CLAIM_GENERATION_FENCE_INVALID")
        return ActiveWorkTransition.CLAIM

    if before.status != ActiveWorkStatus.ACTIVE:
        raise ValueError("ACTIVE_WORK_TRANSITION_BEFORE_STATUS_INVALID")

    if after.status == ActiveWorkStatus.EMPTY:
        if after.generation != before.generation + 1:
            raise ValueError("ACTIVE_WORK_RELEASE_GENERATION_FENCE_INVALID")
        return ActiveWorkTransition.RELEASE

    if after.status != ActiveWorkStatus.ACTIVE:
        raise ValueError("ACTIVE_WORK_TRANSITION_AFTER_STATUS_INVALID")
    if after.generation != before.generation:
        raise ValueError("ACTIVE_WORK_REFRESH_GENERATION_FENCE_INVALID")

    changed_stable = tuple(
        field
        for field in _REFRESH_STABLE_FIELDS
        if getattr(before, field) != getattr(after, field)
    )
    if changed_stable:
        raise ValueError(
            "ACTIVE_WORK_REFRESH_STABLE_BINDING_CHANGED:" + ",".join(changed_stable)
        )

    if before.updated_at is None or after.updated_at is None:
        raise ValueError("ACTIVE_WORK_REFRESH_UPDATED_AT_REQUIRED")
    if before.expires_at is None or after.expires_at is None:
        raise ValueError("ACTIVE_WORK_REFRESH_EXPIRES_AT_REQUIRED")
    if after.updated_at <= before.updated_at:
        raise ValueError("ACTIVE_WORK_REFRESH_UPDATED_AT_NOT_ADVANCED")
    if after.expires_at <= before.expires_at:
        raise ValueError("ACTIVE_WORK_REFRESH_EXPIRY_NOT_EXTENDED")

    return ActiveWorkTransition.REFRESH
