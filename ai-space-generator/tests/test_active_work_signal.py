from datetime import datetime

import pytest

from src.active_work_signal import ActiveWorkStatus, parse_active_work_signal


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


def historical_claim_signal() -> str:
    # Reproduces the material S1 values observed in Drive revision 10.
    s1 = """S1_STATUS=ACTIVE
S1_GENERATION=1
S1_RUN_KEY=HAO_FULL_CHAT_CROSSCHAT_CLOSEOUT_20260917_01
S1_OBJECTIVE=FULL_CHAT_DELETION_GRADE_DISTILLATION_AND_CROSSCHAT_HANDOFF
S1_TARGET=HAO_SYSTEM_INTAKE+VERIFICATION+HANDOFF
S1_EXECUTION_LANE=SINGLE_WRITE_GATEWAY
S1_WORK_STATE=FORMAL_CLOSEOUT
S1_STARTED_AT=2026-09-17T11:22:28+08:00
S1_UPDATED_AT=2026-09-17T11:22:28+08:00
S1_EXPIRES_AT=2026-09-17T11:42:28+08:00
S1_EXPECTED_DELTA=RAW_EVIDENCE+MATERIAL_CLOSEOUT+HANDOFF+VERIFY
S1_READBACK_STATE=PENDING
S1_OWNER=CHATGPT_CURRENT_CHAT
"""
    return CONTROL + s1 + empty_slot(2) + empty_slot(3) + empty_slot(4)


def historical_cleared_signal() -> str:
    # Reproduces the material S1 values observed in Drive revision 11.
    return CONTROL + empty_slot(1, generation=2) + empty_slot(2) + empty_slot(3) + empty_slot(4)


def test_historical_claim_is_live_only_for_exact_objective_before_ttl():
    signal = parse_active_work_signal(historical_claim_signal())
    slot = signal.slots[0]

    assert slot.status == ActiveWorkStatus.ACTIVE
    assert slot.generation == 1
    assert slot.readback_state == "PENDING"
    assert slot.owner == "CHATGPT_CURRENT_CHAT"
    assert slot.ref == (
        "ACTIVE_WORK:S1:G1:HAO_FULL_CHAT_CROSSCHAT_CLOSEOUT_20260917_01"
    )

    now = datetime.fromisoformat("2026-09-17T11:30:00+08:00")
    matches = signal.live_same_objective(
        "FULL_CHAT_DELETION_GRADE_DISTILLATION_AND_CROSSCHAT_HANDOFF",
        now=now,
    )
    assert matches == (slot,)
    assert signal.live_same_objective(
        "FULL_CHAT_DELETION_GRADE_DISTILLATION_AND_CROSSCHAT_HANDOFF_V2",
        now=now,
    ) == ()


def test_historical_claim_is_not_live_after_expiry():
    signal = parse_active_work_signal(historical_claim_signal())
    assert signal.live_same_objective(
        "FULL_CHAT_DELETION_GRADE_DISTILLATION_AND_CROSSCHAT_HANDOFF",
        now=datetime.fromisoformat("2026-09-17T11:42:28+08:00"),
    ) == ()


def test_historical_clear_preserves_generation_fence_without_live_work():
    signal = parse_active_work_signal(historical_cleared_signal())
    slot = signal.slots[0]
    assert slot.status == ActiveWorkStatus.EMPTY
    assert slot.generation == 2
    assert signal.live_same_objective(
        "FULL_CHAT_DELETION_GRADE_DISTILLATION_AND_CROSSCHAT_HANDOFF",
        now=datetime.fromisoformat("2026-09-17T12:00:00+08:00"),
    ) == ()


def test_signal_control_boundary_fails_closed():
    malformed = historical_claim_signal().replace(
        "TASK_DATABASE=NO", "TASK_DATABASE=YES", 1
    )
    with pytest.raises(ValueError, match="ACTIVE_WORK_SIGNAL_CONTROL_INVALID:TASK_DATABASE"):
        parse_active_work_signal(malformed)


def test_active_slot_requires_complete_owner_and_ttl_fields():
    malformed = historical_claim_signal().replace(
        "S1_OWNER=CHATGPT_CURRENT_CHAT", "S1_OWNER=NONE", 1
    )
    with pytest.raises(ValueError, match="ACTIVE_WORK_ACTIVE_SLOT_INCOMPLETE:S1"):
        parse_active_work_signal(malformed)


def test_naive_query_time_fails_closed():
    signal = parse_active_work_signal(historical_claim_signal())
    with pytest.raises(ValueError, match="ACTIVE_WORK_NOW_TIMEZONE_REQUIRED"):
        signal.live_same_objective(
            "FULL_CHAT_DELETION_GRADE_DISTILLATION_AND_CROSSCHAT_HANDOFF",
            now=datetime(2026, 9, 17, 11, 30),
        )
