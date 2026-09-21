from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from src.active_work_signal import parse_active_work_signal
from src.active_work_transition import (
    ActiveWorkTransition,
    classify_active_work_transition,
)


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
FP_A = "sha256:" + "a" * 64
FP_B = "sha256:" + "b" * 64


def empty_slot(index: int, generation: int = 0) -> str:
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


def active_slot(index: int = 1, generation: int = 1) -> str:
    return f"""S{index}_STATUS=ACTIVE
S{index}_GENERATION={generation}
S{index}_WORK_KEY=RV-051
S{index}_INTENT_FINGERPRINT={FP_A}
S{index}_RUN_KEY=RUN-RV051-1
S{index}_OBJECTIVE=VERIFY_R051
S{index}_TARGET=REQUIREMENTS:RV-051
S{index}_EXECUTION_LANE=CHATGPT_PRIVATE_LANE
S{index}_WORK_STATE=VERIFYING
S{index}_STARTED_AT=2026-09-17T11:22:28+08:00
S{index}_UPDATED_AT=2026-09-17T11:22:28+08:00
S{index}_EXPIRES_AT=2026-09-17T11:42:28+08:00
S{index}_EXPECTED_DELTA=BOUNDED_VERIFICATION
S{index}_READBACK_STATE=PENDING
S{index}_OWNER=CHATGPT_CURRENT_CHAT
"""


def signal_with_s1(s1: str) -> str:
    return CONTROL + s1 + empty_slot(2) + empty_slot(3) + empty_slot(4)


def test_historical_generation_pattern_validates_claim():
    before = parse_active_work_signal(
        signal_with_s1(empty_slot(1, generation=0))
    ).slots[0]
    after = parse_active_work_signal(signal_with_s1(active_slot())).slots[0]
    assert classify_active_work_transition(before, after) == ActiveWorkTransition.CLAIM


def test_historical_generation_pattern_validates_release_and_clears_identity():
    before = parse_active_work_signal(signal_with_s1(active_slot())).slots[0]
    after = parse_active_work_signal(
        signal_with_s1(empty_slot(1, generation=2))
    ).slots[0]
    assert classify_active_work_transition(before, after) == ActiveWorkTransition.RELEASE
    assert after.work_key is None
    assert after.intent_fingerprint is None


def test_refresh_keeps_generation_and_material_binding_stable():
    before = parse_active_work_signal(signal_with_s1(active_slot())).slots[0]
    after = replace(
        before,
        updated_at=before.updated_at + timedelta(minutes=5),
        expires_at=before.expires_at + timedelta(minutes=5),
        work_state="READBACK",
        readback_state="READY",
    )
    assert classify_active_work_transition(before, after) == ActiveWorkTransition.REFRESH


def test_refresh_cannot_silently_change_intent_in_same_run_and_generation():
    before = parse_active_work_signal(signal_with_s1(active_slot())).slots[0]
    after = replace(
        before,
        intent_fingerprint=FP_B,
        updated_at=before.updated_at + timedelta(minutes=5),
        expires_at=before.expires_at + timedelta(minutes=5),
    )
    with pytest.raises(
        ValueError,
        match="ACTIVE_WORK_REFRESH_STABLE_BINDING_CHANGED:intent_fingerprint",
    ):
        classify_active_work_transition(before, after)


def test_refresh_cannot_change_run_key_or_owner_without_new_generation():
    before = parse_active_work_signal(signal_with_s1(active_slot())).slots[0]
    after = replace(
        before,
        run_key="RUN-RV051-2",
        owner="OTHER_WORKER",
        updated_at=before.updated_at + timedelta(minutes=5),
        expires_at=before.expires_at + timedelta(minutes=5),
    )
    with pytest.raises(ValueError, match="ACTIVE_WORK_REFRESH_STABLE_BINDING_CHANGED"):
        classify_active_work_transition(before, after)


def test_stale_generation_refresh_fails_closed():
    before = parse_active_work_signal(signal_with_s1(active_slot())).slots[0]
    after = replace(
        before,
        generation=2,
        updated_at=before.updated_at + timedelta(minutes=5),
        expires_at=before.expires_at + timedelta(minutes=5),
    )
    with pytest.raises(ValueError, match="ACTIVE_WORK_REFRESH_GENERATION_FENCE_INVALID"):
        classify_active_work_transition(before, after)


def test_refresh_requires_real_lease_extension():
    before = parse_active_work_signal(signal_with_s1(active_slot())).slots[0]
    after = replace(
        before,
        updated_at=before.updated_at + timedelta(minutes=1),
        expires_at=before.expires_at,
    )
    with pytest.raises(ValueError, match="ACTIVE_WORK_REFRESH_EXPIRY_NOT_EXTENDED"):
        classify_active_work_transition(before, after)


def test_identical_snapshot_is_no_change():
    slot = parse_active_work_signal(signal_with_s1(active_slot())).slots[0]
    assert classify_active_work_transition(slot, slot) == ActiveWorkTransition.NO_CHANGE
