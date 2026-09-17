from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from .active_work_signal import ActiveWorkSignal
from .resolved_work_identity import WorkIdentityRelation


class ActiveWorkIdentityDisposition(StrEnum):
    CLEAR = "CLEAR"
    INDEPENDENT = "INDEPENDENT"
    WAIT_OR_JOIN = "WAIT_OR_JOIN"
    RECONCILE_CHANGED_INTENT = "RECONCILE_CHANGED_INTENT"
    VISIBILITY_UNKNOWN = "VISIBILITY_UNKNOWN"


@dataclass(frozen=True)
class ActiveWorkIdentityAdmission:
    """Read-only identity coordination result for one already-hydrated work item.

    This object is not Authority, a writer, a lease, or a second Gateway. It only
    interprets the existing Active Work Signal against a trusted post-semantic
    `(work_key, intent_fingerprint)` pair. OBJECTIVE, TARGET and RUN_KEY are never
    used to manufacture semantic identity here.
    """

    allowed: bool
    code: str
    disposition: ActiveWorkIdentityDisposition
    blocking_refs: tuple[str, ...] = ()


def resolve_active_work_identity_admission(
    signal: ActiveWorkSignal,
    *,
    work_key: str,
    intent_fingerprint: str,
    now: datetime,
) -> ActiveWorkIdentityAdmission:
    """Resolve duplicate/conflict visibility without mutating Active Work state.

    Current Hao controls already require duplicate active work to wait/join,
    unknown consequential visibility to fail closed, and stale Current/control
    bindings to reconcile before consequential continuation. Consequently:

    * same work + same intent cannot start a duplicate attempt;
    * same work + changed material intent cannot join/refresh the old attempt;
      it must reconcile/release/expire before a new generation may proceed;
    * identity-less legacy active work remains UNKNOWN and fails closed;
    * only known different work is semantically independent at this layer.

    Same-target/upstream-dependency coordination remains an existing separate
    Active Work concern; DIFFERENT_WORK here does not waive those controls.
    """

    relations = signal.live_identity_relations(
        work_key=work_key,
        intent_fingerprint=intent_fingerprint,
        now=now,
    )
    if not relations:
        return ActiveWorkIdentityAdmission(
            True,
            "ACTIVE_WORK_IDENTITY_CLEAR",
            ActiveWorkIdentityDisposition.CLEAR,
        )

    def refs_for(relation: WorkIdentityRelation) -> tuple[str, ...]:
        return tuple(slot.ref for slot, observed in relations if observed == relation)

    changed = refs_for(WorkIdentityRelation.SAME_WORK_CHANGED_INTENT)
    if changed:
        return ActiveWorkIdentityAdmission(
            False,
            "ACTIVE_WORK_CHANGED_INTENT_RECONCILIATION_REQUIRED",
            ActiveWorkIdentityDisposition.RECONCILE_CHANGED_INTENT,
            changed,
        )

    same = refs_for(WorkIdentityRelation.SAME_WORK_SAME_INTENT)
    if same:
        return ActiveWorkIdentityAdmission(
            False,
            "ACTIVE_WORK_SAME_INTENT_WAIT_OR_JOIN",
            ActiveWorkIdentityDisposition.WAIT_OR_JOIN,
            same,
        )

    unknown = refs_for(WorkIdentityRelation.UNKNOWN)
    if unknown:
        return ActiveWorkIdentityAdmission(
            False,
            "ACTIVE_WORK_IDENTITY_VISIBILITY_UNKNOWN",
            ActiveWorkIdentityDisposition.VISIBILITY_UNKNOWN,
            unknown,
        )

    return ActiveWorkIdentityAdmission(
        True,
        "ACTIVE_WORK_IDENTITY_INDEPENDENT",
        ActiveWorkIdentityDisposition.INDEPENDENT,
    )
