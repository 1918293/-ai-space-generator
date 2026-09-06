from src.reconciliation import (
    ReconciliationDisposition,
    ReconciliationEvidence,
    ReconciliationEvidenceKind,
    ReconciliationPhase,
    add_reconciliation_evidence,
    apply_reconciliation,
    automatic_replay_allowed,
    reconciliation_case_for_unknown_effect,
)


def case():
    return reconciliation_case_for_unknown_effect(
        case_id="RECON-1",
        run_id="RUN-1",
        action_id="RUN-1:A0001:drive.update",
    )


def evidence(evidence_id, kind):
    return ReconciliationEvidence(evidence_id, kind, True, "runtime-verifier")


def test_unknown_effect_can_never_be_automatically_replayed():
    assert automatic_replay_allowed(case()) is False


def test_unknown_effect_cannot_be_adopted_without_verified_state_readback():
    unresolved = apply_reconciliation(case(), ReconciliationDisposition.ADOPT_VERIFIED_STATE)
    assert unresolved.phase == ReconciliationPhase.OPEN
    assert unresolved.resolution_code == "ADOPTION_REQUIRES_VERIFIED_READBACK"


def test_verified_external_state_can_be_adopted_without_replaying_side_effect():
    current = add_reconciliation_evidence(
        case(), evidence("READBACK-1", ReconciliationEvidenceKind.STATE_READBACK)
    )
    current = add_reconciliation_evidence(
        current, evidence("VERIFY-1", ReconciliationEvidenceKind.VERIFICATION_PASS)
    )
    resolved = apply_reconciliation(current, ReconciliationDisposition.ADOPT_VERIFIED_STATE)
    assert resolved.phase == ReconciliationPhase.RESOLVED
    assert resolved.resolution_code == "VERIFIED_EXTERNAL_STATE_ADOPTED"
    assert automatic_replay_allowed(resolved) is False


def test_compensation_requires_receipt_and_verified_post_compensation_readback():
    current = add_reconciliation_evidence(
        case(), evidence("COMP-1", ReconciliationEvidenceKind.COMPENSATION_RECEIPT)
    )
    incomplete = apply_reconciliation(current, ReconciliationDisposition.COMPENSATE_VERIFIED)
    assert incomplete.phase == ReconciliationPhase.OPEN

    current = add_reconciliation_evidence(
        current, evidence("READBACK-1", ReconciliationEvidenceKind.STATE_READBACK)
    )
    current = add_reconciliation_evidence(
        current, evidence("VERIFY-1", ReconciliationEvidenceKind.VERIFICATION_PASS)
    )
    resolved = apply_reconciliation(current, ReconciliationDisposition.COMPENSATE_VERIFIED)
    assert resolved.phase == ReconciliationPhase.RESOLVED
    assert resolved.resolution_code == "COMPENSATION_VERIFIED"


def test_require_hao_and_permanent_unresolved_are_explicit_states():
    waiting = apply_reconciliation(case(), ReconciliationDisposition.REQUIRE_HAO)
    assert waiting.phase == ReconciliationPhase.AWAITING_HAO

    permanent = apply_reconciliation(case(), ReconciliationDisposition.PERMANENT_UNRESOLVED)
    assert permanent.phase == ReconciliationPhase.PERMANENT_UNRESOLVED
