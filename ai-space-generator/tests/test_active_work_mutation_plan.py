from datetime import datetime, timedelta

import pytest

from src.active_work_mutation_plan import (
    ActiveWorkReleaseReason,
    plan_active_work_claim,
    plan_active_work_refresh,
    plan_active_work_release,
)
from src.active_work_signal import ActiveWorkStatus, parse_active_work_signal
from src.active_work_transition import ActiveWorkTransition


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
START = datetime.fromisoformat("2026-09-17T11:22:28+08:00")
EXPIRY = datetime.fromisoformat("2026-09-17T11:42:28+08:00")


def empty_slot(index=1, generation=3):
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


def active_slot(index=1, generation=4):
    return f"""S{index}_STATUS=ACTIVE
S{index}_GENERATION={generation}
S{index}_WORK_KEY=RV-051
S{index}_INTENT_FINGERPRINT={FP_A}
S{index}_RUN_KEY=RUN-RV051-G4
S{index}_OBJECTIVE=VERIFY_R051
S{index}_TARGET=REQUIREMENTS:RV-051
S{index}_EXECUTION_LANE=SINGLE_WRITE_GATEWAY
S{index}_WORK_STATE=VERIFYING
S{index}_STARTED_AT={START.isoformat()}
S{index}_UPDATED_AT={START.isoformat()}
S{index}_EXPIRES_AT={EXPIRY.isoformat()}
S{index}_EXPECTED_DELTA=BOUNDED_VERIFICATION
S{index}_READBACK_STATE=PENDING
S{index}_OWNER=CHATGPT_CURRENT_CHAT
"""


def signal(s1):
    return CONTROL + s1 + empty_slot(2, 0) + empty_slot(3, 0) + empty_slot(4, 0)


def empty():
    return parse_active_work_signal(signal(empty_slot())).slots[0]


def active():
    return parse_active_work_signal(signal(active_slot())).slots[0]


def claim(before=None, **overrides):
    before = before or empty()
    values = dict(
        expected_generation=before.generation,
        run_key="RUN-RV051-G4",
        objective="VERIFY_R051",
        target="REQUIREMENTS:RV-051",
        execution_lane="SINGLE_WRITE_GATEWAY",
        work_state="VERIFYING",
        started_at=START,
        expires_at=EXPIRY,
        expected_delta="BOUNDED_VERIFICATION",
        readback_state="PENDING",
        owner="CHATGPT_CURRENT_CHAT",
        work_key="RV-051",
        intent_fingerprint=FP_A,
    )
    values.update(overrides)
    return plan_active_work_claim(before, **values)


def test_claim_atomically_emits_identity_pair_and_increments_generation():
    before = empty()
    plan = claim(before)

    assert plan.transition == ActiveWorkTransition.CLAIM
    assert plan.expected_generation == 3
    assert plan.expected_ref == ""
    assert plan.after.status == ActiveWorkStatus.ACTIVE
    assert plan.after.generation == 4
    assert plan.after.work_key == "RV-051"
    assert plan.after.intent_fingerprint == FP_A
    assert plan.after.run_key == "RUN-RV051-G4"
    assert plan.after.updated_at == START


def test_claim_requires_both_trusted_identity_values():
    with pytest.raises(ValueError, match="ACTIVE_WORK_PLAN_WORK_KEY_REQUIRED"):
        claim(work_key="NONE")
    with pytest.raises(ValueError, match="ACTIVE_WORK_PLAN_INTENT_FINGERPRINT_INVALID"):
        claim(intent_fingerprint="sha256:broken")


def test_claim_fails_closed_on_stale_generation():
    with pytest.raises(ValueError, match="ACTIVE_WORK_PLAN_STALE_GENERATION"):
        claim(expected_generation=2)


def test_claim_cannot_reuse_active_slot():
    before = active()
    with pytest.raises(ValueError, match="ACTIVE_WORK_PLAN_CLAIM_REQUIRES_EMPTY"):
        claim(before, expected_generation=before.generation)


def test_refresh_preserves_generation_run_key_and_identity_pair():
    before = active()
    plan = plan_active_work_refresh(
        before,
        expected_generation=4,
        expected_ref=before.ref,
        updated_at=START + timedelta(minutes=5),
        expires_at=EXPIRY + timedelta(minutes=5),
        work_state="READBACK",
        readback_state="READY",
    )

    assert plan.transition == ActiveWorkTransition.REFRESH
    assert plan.expected_ref == before.ref
    assert plan.after.generation == before.generation
    assert plan.after.run_key == before.run_key
    assert plan.after.work_key == before.work_key
    assert plan.after.intent_fingerprint == before.intent_fingerprint
    assert plan.after.work_state == "READBACK"
    assert plan.after.readback_state == "READY"


def test_refresh_fails_closed_on_stale_ref_or_generation():
    before = active()
    with pytest.raises(ValueError, match="ACTIVE_WORK_PLAN_STALE_REF"):
        plan_active_work_refresh(
            before,
            expected_generation=4,
            expected_ref="ACTIVE_WORK:S1:G4:OTHER-RUN",
            updated_at=START + timedelta(minutes=5),
            expires_at=EXPIRY + timedelta(minutes=5),
        )
    with pytest.raises(ValueError, match="ACTIVE_WORK_PLAN_STALE_GENERATION"):
        plan_active_work_refresh(
            before,
            expected_generation=3,
            expected_ref=before.ref,
            updated_at=START + timedelta(minutes=5),
            expires_at=EXPIRY + timedelta(minutes=5),
        )


def test_refresh_requires_monotonic_lease_extension_and_valid_timeline():
    before = active()
    with pytest.raises(ValueError, match="ACTIVE_WORK_REFRESH_EXPIRY_NOT_EXTENDED"):
        plan_active_work_refresh(
            before,
            expected_generation=4,
            expected_ref=before.ref,
            updated_at=START + timedelta(minutes=1),
            expires_at=EXPIRY,
        )
    with pytest.raises(ValueError, match="ACTIVE_WORK_PLAN_REFRESH_TIMELINE_INVALID"):
        plan_active_work_refresh(
            before,
            expected_generation=4,
            expected_ref=before.ref,
            updated_at=EXPIRY + timedelta(minutes=10),
            expires_at=EXPIRY + timedelta(minutes=5),
        )


def test_release_for_complete_increments_generation_and_clears_identity_atomically():
    before = active()
    plan = plan_active_work_release(
        before,
        expected_generation=4,
        expected_ref=before.ref,
        reason=ActiveWorkReleaseReason.COMPLETE,
    )

    assert plan.transition == ActiveWorkTransition.RELEASE
    assert plan.release_reason == ActiveWorkReleaseReason.COMPLETE
    assert plan.expected_ref == before.ref
    assert plan.after.status == ActiveWorkStatus.EMPTY
    assert plan.after.generation == 5
    assert plan.after.run_key == "NONE"
    assert plan.after.work_key is None
    assert plan.after.intent_fingerprint is None
    assert plan.after.started_at is None
    assert plan.after.expires_at is None


def test_release_for_expire_and_reconcile_use_same_generation_fence():
    before = active()
    for reason in (ActiveWorkReleaseReason.EXPIRE, ActiveWorkReleaseReason.RECONCILE):
        plan = plan_active_work_release(
            before,
            expected_generation=4,
            expected_ref=before.ref,
            reason=reason,
        )
        assert plan.after.generation == 5
        assert plan.release_reason == reason
        assert plan.after.work_key is None
        assert plan.after.intent_fingerprint is None


def test_release_fails_closed_for_stale_attempt():
    before = active()
    with pytest.raises(ValueError, match="ACTIVE_WORK_PLAN_STALE_REF"):
        plan_active_work_release(
            before,
            expected_generation=4,
            expected_ref="ACTIVE_WORK:S1:G4:STALE-RUN",
            reason=ActiveWorkReleaseReason.COMPLETE,
        )


def test_mutation_plan_has_no_path_to_refresh_changed_intent():
    before = active()
    plan = plan_active_work_refresh(
        before,
        expected_generation=4,
        expected_ref=before.ref,
        updated_at=START + timedelta(minutes=5),
        expires_at=EXPIRY + timedelta(minutes=5),
    )
    assert plan.after.intent_fingerprint == FP_A
    assert FP_B != plan.after.intent_fingerprint
