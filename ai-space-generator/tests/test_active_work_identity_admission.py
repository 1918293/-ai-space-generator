from datetime import datetime, timedelta

from src.active_work_identity_admission import (
    ActiveWorkIdentityDisposition,
    resolve_active_work_identity_admission,
)
from src.active_work_signal import parse_active_work_signal


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
NOW = datetime.fromisoformat("2026-09-17T11:30:00+08:00")


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


def active_slot(
    index: int,
    *,
    work_key: str | None,
    intent_fingerprint: str | None,
    objective: str = "VERIFY_R051",
    target: str = "REQUIREMENTS:RV-051",
    generation: int = 1,
    run_key: str | None = None,
    expires_at: datetime | None = None,
) -> str:
    run = run_key or f"RUN-S{index}"
    expiry = expires_at or datetime.fromisoformat("2026-09-17T11:42:28+08:00")
    identity = ""
    if work_key is not None or intent_fingerprint is not None:
        identity = (
            f"S{index}_WORK_KEY={work_key if work_key is not None else 'NONE'}\n"
            f"S{index}_INTENT_FINGERPRINT={intent_fingerprint if intent_fingerprint is not None else 'NONE'}\n"
        )
    return f"""S{index}_STATUS=ACTIVE
S{index}_GENERATION={generation}
{identity}S{index}_RUN_KEY={run}
S{index}_OBJECTIVE={objective}
S{index}_TARGET={target}
S{index}_EXECUTION_LANE=CHATGPT_PRIVATE_LANE
S{index}_WORK_STATE=VERIFYING
S{index}_STARTED_AT=2026-09-17T11:22:28+08:00
S{index}_UPDATED_AT=2026-09-17T11:22:28+08:00
S{index}_EXPIRES_AT={expiry.isoformat()}
S{index}_EXPECTED_DELTA=BOUNDED_VERIFICATION
S{index}_READBACK_STATE=PENDING
S{index}_OWNER=CHATGPT_CURRENT_CHAT
"""


def signal(*slots: str) -> str:
    material = list(slots)
    while len(material) < 4:
        material.append(empty_slot(len(material) + 1))
    return CONTROL + "".join(material)


def resolve(text: str, *, work_key: str = "RV-051", intent: str = FP_A):
    return resolve_active_work_identity_admission(
        parse_active_work_signal(text),
        work_key=work_key,
        intent_fingerprint=intent,
        now=NOW,
    )


def test_no_live_work_is_clear():
    decision = resolve(signal())
    assert decision.allowed is True
    assert decision.code == "ACTIVE_WORK_IDENTITY_CLEAR"
    assert decision.disposition == ActiveWorkIdentityDisposition.CLEAR


def test_known_different_work_is_independent_at_identity_layer():
    decision = resolve(signal(active_slot(1, work_key="RV-052", intent_fingerprint=FP_A)))
    assert decision.allowed is True
    assert decision.code == "ACTIVE_WORK_IDENTITY_INDEPENDENT"
    assert decision.disposition == ActiveWorkIdentityDisposition.INDEPENDENT


def test_same_work_same_intent_waits_or_joins_without_duplicate_attempt():
    decision = resolve(signal(active_slot(1, work_key="RV-051", intent_fingerprint=FP_A)))
    assert decision.allowed is False
    assert decision.code == "ACTIVE_WORK_SAME_INTENT_WAIT_OR_JOIN"
    assert decision.disposition == ActiveWorkIdentityDisposition.WAIT_OR_JOIN
    assert decision.blocking_refs == ("ACTIVE_WORK:S1:G1:RUN-S1",)


def test_same_work_changed_intent_requires_reconciliation_not_refresh_or_join():
    decision = resolve(
        signal(active_slot(1, work_key="RV-051", intent_fingerprint=FP_A)),
        intent=FP_B,
    )
    assert decision.allowed is False
    assert decision.code == "ACTIVE_WORK_CHANGED_INTENT_RECONCILIATION_REQUIRED"
    assert decision.disposition == ActiveWorkIdentityDisposition.RECONCILE_CHANGED_INTENT
    assert decision.blocking_refs == ("ACTIVE_WORK:S1:G1:RUN-S1",)


def test_legacy_identityless_live_slot_fails_closed_to_unknown_even_if_objective_matches():
    decision = resolve(
        signal(
            active_slot(
                1,
                work_key=None,
                intent_fingerprint=None,
                objective="VERIFY_R051",
                target="REQUIREMENTS:RV-051",
            )
        )
    )
    assert decision.allowed is False
    assert decision.code == "ACTIVE_WORK_IDENTITY_VISIBILITY_UNKNOWN"
    assert decision.disposition == ActiveWorkIdentityDisposition.VISIBILITY_UNKNOWN


def test_expired_slot_does_not_block_new_attempt():
    decision = resolve(
        signal(
            active_slot(
                1,
                work_key="RV-051",
                intent_fingerprint=FP_A,
                expires_at=NOW - timedelta(seconds=1),
            )
        )
    )
    assert decision.allowed is True
    assert decision.disposition == ActiveWorkIdentityDisposition.CLEAR


def test_changed_intent_block_takes_precedence_over_unrelated_live_work():
    decision = resolve(
        signal(
            active_slot(1, work_key="RV-052", intent_fingerprint=FP_A),
            active_slot(2, work_key="RV-051", intent_fingerprint=FP_A),
        ),
        intent=FP_B,
    )
    assert decision.allowed is False
    assert decision.disposition == ActiveWorkIdentityDisposition.RECONCILE_CHANGED_INTENT
    assert decision.blocking_refs == ("ACTIVE_WORK:S2:G1:RUN-S2",)


def test_identity_layer_does_not_treat_objective_or_target_as_work_identity():
    decision = resolve(
        signal(
            active_slot(
                1,
                work_key="RV-999",
                intent_fingerprint=FP_A,
                objective="VERIFY_R051",
                target="REQUIREMENTS:RV-051",
            )
        )
    )
    assert decision.allowed is True
    assert decision.disposition == ActiveWorkIdentityDisposition.INDEPENDENT
