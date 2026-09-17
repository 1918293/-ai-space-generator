from datetime import datetime

import pytest

from src.active_work_signal import ActiveWorkStatus, parse_active_work_signal
from src.resolved_work_identity import WorkIdentityRelation


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


def identity_claim_signal(
    *,
    work_key: str = "RV-051",
    intent_fingerprint: str = FP_A,
) -> str:
    return historical_claim_signal().replace(
        "S1_GENERATION=1\n",
        (
            "S1_GENERATION=1\n"
            f"S1_WORK_KEY={work_key}\n"
            f"S1_INTENT_FINGERPRINT={intent_fingerprint}\n"
        ),
        1,
    )


def test_historical_claim_is_live_only_for_exact_objective_before_ttl():
    signal = parse_active_work_signal(historical_claim_signal())
    slot = signal.slots[0]

    assert slot.status == ActiveWorkStatus.ACTIVE
    assert slot.generation == 1
    assert slot.readback_state == "PENDING"
    assert slot.owner == "CHATGPT_CURRENT_CHAT"
    assert slot.work_key is None
    assert slot.intent_fingerprint is None
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


def test_reader_accepts_optional_identity_pair_without_changing_legacy_fields():
    signal = parse_active_work_signal(identity_claim_signal())
    slot = signal.slots[0]
    assert slot.work_key == "RV-051"
    assert slot.intent_fingerprint == FP_A
    assert slot.objective == "FULL_CHAT_DELETION_GRADE_DISTILLATION_AND_CROSSCHAT_HANDOFF"
    assert slot.run_key == "HAO_FULL_CHAT_CROSSCHAT_CLOSEOUT_20260917_01"


def test_same_work_same_intent_relation_uses_identity_pair_not_objective():
    signal = parse_active_work_signal(identity_claim_signal())
    now = datetime.fromisoformat("2026-09-17T11:30:00+08:00")
    relations = signal.live_identity_relations(
        work_key="RV-051",
        intent_fingerprint=FP_A,
        now=now,
    )
    assert relations == ((signal.slots[0], WorkIdentityRelation.SAME_WORK_SAME_INTENT),)


def test_same_work_changed_intent_relation_preserves_stable_work_key():
    signal = parse_active_work_signal(identity_claim_signal())
    now = datetime.fromisoformat("2026-09-17T11:30:00+08:00")
    relations = signal.live_identity_relations(
        work_key="RV-051",
        intent_fingerprint=FP_B,
        now=now,
    )
    assert relations == ((signal.slots[0], WorkIdentityRelation.SAME_WORK_CHANGED_INTENT),)


def test_different_work_relation_does_not_coalesce_similar_objective():
    signal = parse_active_work_signal(identity_claim_signal(work_key="RV-052"))
    now = datetime.fromisoformat("2026-09-17T11:30:00+08:00")
    relations = signal.live_identity_relations(
        work_key="RV-051",
        intent_fingerprint=FP_A,
        now=now,
    )
    assert relations == ((signal.slots[0], WorkIdentityRelation.DIFFERENT_WORK),)


def test_legacy_live_slot_without_identity_fails_closed_to_unknown_relation():
    signal = parse_active_work_signal(historical_claim_signal())
    now = datetime.fromisoformat("2026-09-17T11:30:00+08:00")
    relations = signal.live_identity_relations(
        work_key="RV-051",
        intent_fingerprint=FP_A,
        now=now,
    )
    assert relations == ((signal.slots[0], WorkIdentityRelation.UNKNOWN),)


def test_historical_claim_is_not_live_after_expiry():
    signal = parse_active_work_signal(historical_claim_signal())
    expiry = datetime.fromisoformat("2026-09-17T11:42:28+08:00")
    assert signal.live_same_objective(
        "FULL_CHAT_DELETION_GRADE_DISTILLATION_AND_CROSSCHAT_HANDOFF",
        now=expiry,
    ) == ()
    assert signal.live_identity_relations(
        work_key="RV-051",
        intent_fingerprint=FP_A,
        now=expiry,
    ) == ()


def test_historical_clear_preserves_generation_fence_without_live_work():
    claimed = parse_active_work_signal(historical_claim_signal())
    old_ref = claimed.slots[0].ref
    assert claimed.has_live_ref(
        old_ref,
        now=datetime.fromisoformat("2026-09-17T11:30:00+08:00"),
    ) is True

    cleared = parse_active_work_signal(historical_cleared_signal())
    slot = cleared.slots[0]
    assert slot.status == ActiveWorkStatus.EMPTY
    assert slot.generation == 2
    assert cleared.has_live_ref(
        old_ref,
        now=datetime.fromisoformat("2026-09-17T12:00:00+08:00"),
    ) is False
    assert cleared.live_same_objective(
        "FULL_CHAT_DELETION_GRADE_DISTILLATION_AND_CROSSCHAT_HANDOFF",
        now=datetime.fromisoformat("2026-09-17T12:00:00+08:00"),
    ) == ()


def test_optional_identity_pair_is_atomic_at_read_boundary():
    malformed = historical_claim_signal().replace(
        "S1_GENERATION=1\n",
        "S1_GENERATION=1\nS1_WORK_KEY=RV-051\n",
        1,
    )
    with pytest.raises(ValueError, match="ACTIVE_WORK_IDENTITY_PAIR_INCOMPLETE:S1"):
        parse_active_work_signal(malformed)


def test_malformed_intent_fingerprint_fails_closed():
    malformed = identity_claim_signal(intent_fingerprint="sha256:not-a-valid-digest")
    with pytest.raises(ValueError, match="ACTIVE_WORK_INTENT_FINGERPRINT_INVALID:S1"):
        parse_active_work_signal(malformed)


def test_empty_slot_cannot_retain_identity_payload():
    malformed = historical_cleared_signal().replace(
        "S1_GENERATION=2\n",
        (
            "S1_GENERATION=2\n"
            "S1_WORK_KEY=RV-051\n"
            f"S1_INTENT_FINGERPRINT={FP_A}\n"
        ),
        1,
    )
    with pytest.raises(ValueError, match="ACTIVE_WORK_EMPTY_SLOT_NOT_CLEARED:S1"):
        parse_active_work_signal(malformed)


def test_unknown_extension_field_still_fails_closed():
    malformed = historical_claim_signal().replace(
        "S1_GENERATION=1\n",
        "S1_GENERATION=1\nS1_FAKE_IDENTITY=RV-051\n",
        1,
    )
    with pytest.raises(ValueError, match="ACTIVE_WORK_SIGNAL_UNEXPECTED_FIELD"):
        parse_active_work_signal(malformed)


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
