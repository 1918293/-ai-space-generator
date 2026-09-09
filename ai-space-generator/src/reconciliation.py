from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Iterable


class ReconciliationKind(StrEnum):
    UNKNOWN_EFFECT = "UNKNOWN_EFFECT"
    UNSYNCED = "UNSYNCED"
    UNCONTROLLED_EFFECT = "UNCONTROLLED_EFFECT"
    STALE_OPERATIONAL_CONTEXT = "STALE_OPERATIONAL_CONTEXT"
    STALE_AUTHORITY = "STALE_AUTHORITY"
    READBACK_MISMATCH = "READBACK_MISMATCH"
    PARTIAL_TASK = "PARTIAL_TASK"


class ReconciliationPhase(StrEnum):
    OPEN = "OPEN"
    AWAITING_HAO = "AWAITING_HAO"
    RESOLVED = "RESOLVED"
    PERMANENT_UNRESOLVED = "PERMANENT_UNRESOLVED"


class ReconciliationDisposition(StrEnum):
    ADOPT_VERIFIED_STATE = "ADOPT_VERIFIED_STATE"
    COMPENSATE_VERIFIED = "COMPENSATE_VERIFIED"
    SUPERSEDE = "SUPERSEDE"
    REQUIRE_HAO = "REQUIRE_HAO"
    PERMANENT_UNRESOLVED = "PERMANENT_UNRESOLVED"


class ReconciliationEvidenceKind(StrEnum):
    STATE_READBACK = "STATE_READBACK"
    VERIFICATION_PASS = "VERIFICATION_PASS"
    COMPENSATION_RECEIPT = "COMPENSATION_RECEIPT"
    SUPERSEDING_ACTION = "SUPERSEDING_ACTION"
    HAO_DECISION = "HAO_DECISION"


@dataclass(frozen=True)
class ReconciliationEvidence:
    evidence_id: str
    kind: ReconciliationEvidenceKind
    passed: bool
    source: str


@dataclass(frozen=True)
class ReconciliationCase:
    case_id: str
    run_id: str
    action_id: str
    kind: ReconciliationKind
    effect_may_have_occurred: bool
    evidence: tuple[ReconciliationEvidence, ...] = ()
    phase: ReconciliationPhase = ReconciliationPhase.OPEN
    disposition: ReconciliationDisposition | None = None
    resolution_code: str = ""


@dataclass(frozen=True)
class ReconciliationDecision:
    allowed: bool
    code: str
    phase: ReconciliationPhase


def validate_case(case: ReconciliationCase) -> ReconciliationCase:
    if not case.case_id.strip() or not case.run_id.strip() or not case.action_id.strip():
        raise ValueError("RECONCILIATION_IDENTITY_REQUIRED")
    ids = [item.evidence_id.strip() for item in case.evidence]
    if any(not item for item in ids) or len(set(ids)) != len(ids):
        raise ValueError("INVALID_RECONCILIATION_EVIDENCE_IDS")
    return replace(
        case,
        case_id=case.case_id.strip(),
        run_id=case.run_id.strip(),
        action_id=case.action_id.strip(),
    )


def add_reconciliation_evidence(
    case: ReconciliationCase,
    evidence: ReconciliationEvidence,
) -> ReconciliationCase:
    if case.phase in {ReconciliationPhase.RESOLVED, ReconciliationPhase.PERMANENT_UNRESOLVED}:
        raise ValueError("RECONCILIATION_CASE_TERMINAL")
    if not evidence.evidence_id.strip() or not evidence.source.strip():
        raise ValueError("INVALID_RECONCILIATION_EVIDENCE")
    for existing in case.evidence:
        if existing.evidence_id != evidence.evidence_id:
            continue
        if existing == evidence:
            return case
        raise ValueError("RECONCILIATION_EVIDENCE_CONFLICT")
    return replace(case, evidence=case.evidence + (evidence,))


def automatic_replay_allowed(case: ReconciliationCase) -> bool:
    """Ambiguous effects are never eligible for automatic side-effect replay."""
    return not case.effect_may_have_occurred


def _passing(case: ReconciliationCase) -> set[ReconciliationEvidenceKind]:
    return {item.kind for item in case.evidence if item.passed}


def evaluate_reconciliation(
    case: ReconciliationCase,
    disposition: ReconciliationDisposition,
) -> ReconciliationDecision:
    case = validate_case(case)
    passing = _passing(case)

    if disposition == ReconciliationDisposition.REQUIRE_HAO:
        return ReconciliationDecision(True, "RECONCILIATION_AWAITING_HAO", ReconciliationPhase.AWAITING_HAO)

    if disposition == ReconciliationDisposition.PERMANENT_UNRESOLVED:
        return ReconciliationDecision(
            True,
            "RECONCILIATION_MARKED_PERMANENT_UNRESOLVED",
            ReconciliationPhase.PERMANENT_UNRESOLVED,
        )

    if disposition == ReconciliationDisposition.ADOPT_VERIFIED_STATE:
        required = {
            ReconciliationEvidenceKind.STATE_READBACK,
            ReconciliationEvidenceKind.VERIFICATION_PASS,
        }
        if not required.issubset(passing):
            return ReconciliationDecision(False, "ADOPTION_REQUIRES_VERIFIED_READBACK", case.phase)
        return ReconciliationDecision(True, "VERIFIED_EXTERNAL_STATE_ADOPTED", ReconciliationPhase.RESOLVED)

    if disposition == ReconciliationDisposition.COMPENSATE_VERIFIED:
        required = {
            ReconciliationEvidenceKind.COMPENSATION_RECEIPT,
            ReconciliationEvidenceKind.STATE_READBACK,
            ReconciliationEvidenceKind.VERIFICATION_PASS,
        }
        if not required.issubset(passing):
            return ReconciliationDecision(False, "COMPENSATION_REQUIRES_RECEIPT_AND_VERIFIED_READBACK", case.phase)
        return ReconciliationDecision(True, "COMPENSATION_VERIFIED", ReconciliationPhase.RESOLVED)

    if disposition == ReconciliationDisposition.SUPERSEDE:
        required = {
            ReconciliationEvidenceKind.SUPERSEDING_ACTION,
            ReconciliationEvidenceKind.VERIFICATION_PASS,
        }
        if not required.issubset(passing):
            return ReconciliationDecision(False, "SUPERSEDE_REQUIRES_VERIFIED_NEW_ACTION", case.phase)
        return ReconciliationDecision(True, "STALE_ACTION_SUPERSEDED", ReconciliationPhase.RESOLVED)

    return ReconciliationDecision(False, "RECONCILIATION_DISPOSITION_UNSUPPORTED", case.phase)


def apply_reconciliation(
    case: ReconciliationCase,
    disposition: ReconciliationDisposition,
) -> ReconciliationCase:
    decision = evaluate_reconciliation(case, disposition)
    if not decision.allowed:
        return replace(case, resolution_code=decision.code)
    return replace(
        case,
        phase=decision.phase,
        disposition=disposition,
        resolution_code=decision.code,
    )


def reconciliation_case_for_unknown_effect(
    *,
    case_id: str,
    run_id: str,
    action_id: str,
) -> ReconciliationCase:
    return validate_case(
        ReconciliationCase(
            case_id=case_id,
            run_id=run_id,
            action_id=action_id,
            kind=ReconciliationKind.UNKNOWN_EFFECT,
            effect_may_have_occurred=True,
        )
    )
