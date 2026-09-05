from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Protocol

from .execution_control import (
    ActionProposal,
    CompletionClaim,
    ControlDecision,
    EvidenceKind,
    EvidenceReceipt,
    ExecutionRecord,
    FailureStage,
    RunPhase,
    action_requires_persistence_evidence,
    add_evidence,
    admit_action,
    block_run,
    can_claim,
    close_run,
    mark_unsynced,
    record_failure,
    transition,
)


@dataclass(frozen=True)
class AuthorityPreflightOutcome:
    passed: bool
    current_fingerprint: str = ""
    source: str = ""
    error_code: str = ""


@dataclass(frozen=True)
class ToolOutcome:
    success: bool
    receipt_id: str = ""
    source: str = ""
    error_code: str = ""
    failure_stage: FailureStage = FailureStage.TOOL_EXECUTION


@dataclass(frozen=True)
class VerificationOutcome:
    passed: bool
    receipts: tuple[EvidenceReceipt, ...] = ()
    error_code: str = ""
    failure_stage: FailureStage = FailureStage.VERIFICATION


@dataclass(frozen=True)
class ControlledRunResult:
    record: ExecutionRecord
    admission: ControlDecision
    completion: ControlDecision


class AuthorityPreflight(Protocol):
    def verify_current(self, proposal: ActionProposal) -> AuthorityPreflightOutcome: ...


class ToolExecutor(Protocol):
    def execute(self, proposal: ActionProposal) -> ToolOutcome: ...


class OutcomeVerifier(Protocol):
    def verify(
        self,
        record: ExecutionRecord,
        proposal: ActionProposal,
        tool_outcome: ToolOutcome,
    ) -> VerificationOutcome: ...


def apply_authority_preflight(
    admitted: ExecutionRecord,
    proposal: ActionProposal,
    preflight: AuthorityPreflightOutcome,
) -> tuple[ExecutionRecord, ControlDecision]:
    if admitted.phase != RunPhase.ADMITTED:
        return admitted, ControlDecision(False, "AUTHORITY_PREFLIGHT_REQUIRES_ADMITTED", FailureStage.PLAN)
    expected = proposal.authority_snapshot_fingerprint.strip()
    if not expected:
        return admitted, ControlDecision(True, "AUTHORITY_PREFLIGHT_NOT_REQUIRED")
    if not preflight.passed:
        blocked = block_run(
            admitted,
            stage=FailureStage.AUTHORITY,
            code=preflight.error_code or "AUTHORITY_PREFLIGHT_FAILED",
        )
        return blocked, ControlDecision(False, blocked.failure_code, FailureStage.AUTHORITY)
    if not preflight.current_fingerprint.strip() or preflight.current_fingerprint != expected:
        blocked = block_run(
            admitted,
            stage=FailureStage.AUTHORITY,
            code="AUTHORITY_CHANGED_SINCE_ADMISSION",
        )
        return blocked, ControlDecision(False, blocked.failure_code, FailureStage.AUTHORITY)
    if not preflight.source.strip():
        blocked = block_run(
            admitted,
            stage=FailureStage.AUTHORITY,
            code="AUTHORITY_PREFLIGHT_WITHOUT_SOURCE",
        )
        return blocked, ControlDecision(False, blocked.failure_code, FailureStage.AUTHORITY)

    checked = add_evidence(
        admitted,
        EvidenceReceipt(
            evidence_id=f"AUTHORITY-PREFLIGHT:{proposal.action_id}",
            kind=EvidenceKind.AUTHORITY_SNAPSHOT,
            passed=True,
            source=preflight.source,
            claim_scope=proposal.action_id,
        ),
    )
    return checked, ControlDecision(True, "AUTHORITY_CURRENT_AT_EXECUTION")


def _post_effect_failure(
    record: ExecutionRecord,
    proposal: ActionProposal,
    *,
    stage: FailureStage,
    code: str,
    mechanism: str,
) -> ExecutionRecord:
    if action_requires_persistence_evidence(proposal):
        return mark_unsynced(
            record,
            stage=stage,
            code=code,
            mechanism=mechanism,
        )
    return record_failure(
        record,
        stage=stage,
        code=code,
        mechanism=mechanism,
    )


def _ambiguous_effect_from_executing(
    executing: ExecutionRecord,
    *,
    code: str,
    mechanism: str,
) -> ExecutionRecord:
    observed_unknown = transition(executing, RunPhase.OBSERVED)
    return mark_unsynced(
        observed_unknown,
        stage=FailureStage.PERSISTENCE,
        code=code,
        mechanism=mechanism,
    )


def apply_tool_outcome(
    executing: ExecutionRecord,
    proposal: ActionProposal,
    tool_outcome: ToolOutcome,
) -> tuple[ExecutionRecord, ControlDecision]:
    if executing.phase != RunPhase.EXECUTING:
        return executing, ControlDecision(False, "TOOL_OUTCOME_REQUIRES_EXECUTING", FailureStage.PLAN)

    if not tool_outcome.success:
        code = tool_outcome.error_code or "TOOL_EXECUTION_FAILED"
        mechanism = code.lower()
        if action_requires_persistence_evidence(proposal) and tool_outcome.failure_stage == FailureStage.PERSISTENCE:
            unsynced = _ambiguous_effect_from_executing(
                executing,
                code=code,
                mechanism=mechanism,
            )
            return unsynced, ControlDecision(False, unsynced.failure_code, unsynced.failure_stage)
        failed = record_failure(
            executing,
            stage=tool_outcome.failure_stage,
            code=code,
            mechanism=mechanism,
        )
        return failed, ControlDecision(False, failed.failure_code, failed.failure_stage)

    if not tool_outcome.receipt_id.strip() or not tool_outcome.source.strip():
        if action_requires_persistence_evidence(proposal):
            unsynced = _ambiguous_effect_from_executing(
                executing,
                code="TOOL_SUCCESS_WITHOUT_RECEIPT_EFFECT_UNKNOWN",
                mechanism="missing-tool-receipt-effect-unknown",
            )
            return unsynced, ControlDecision(False, unsynced.failure_code, unsynced.failure_stage)
        failed = record_failure(
            executing,
            stage=FailureStage.TOOL_OUTPUT,
            code="TOOL_SUCCESS_WITHOUT_RECEIPT",
            mechanism="missing-tool-receipt",
        )
        return failed, ControlDecision(False, failed.failure_code, failed.failure_stage)

    observed = add_evidence(
        executing,
        EvidenceReceipt(
            evidence_id=tool_outcome.receipt_id,
            kind=EvidenceKind.TOOL_RECEIPT,
            passed=True,
            source=tool_outcome.source,
            claim_scope=proposal.action_id,
        ),
    )
    observed = transition(observed, RunPhase.OBSERVED)
    return observed, ControlDecision(True, "TOOL_OUTCOME_OBSERVED")


def apply_verification_outcome(
    observed: ExecutionRecord,
    proposal: ActionProposal,
    verification: VerificationOutcome,
) -> tuple[ExecutionRecord, ControlDecision]:
    if observed.phase != RunPhase.OBSERVED:
        return observed, ControlDecision(False, "VERIFICATION_REQUIRES_OBSERVED", FailureStage.PLAN)

    if not verification.passed:
        failed = _post_effect_failure(
            observed,
            proposal,
            stage=verification.failure_stage,
            code=verification.error_code or "VERIFICATION_FAILED",
            mechanism=(verification.error_code or "verification-failed").lower(),
        )
        return failed, ControlDecision(False, failed.failure_code, failed.failure_stage)

    verified = observed
    for receipt in verification.receipts:
        verified = add_evidence(verified, receipt)

    verification_claim = can_claim(verified, CompletionClaim.VERIFIED)
    if not verification_claim.allowed:
        failed = _post_effect_failure(
            verified,
            proposal,
            stage=FailureStage.VERIFICATION,
            code="VERIFIER_DID_NOT_PRODUCE_ACTION_SCOPED_VERIFICATION_RECEIPT",
            mechanism="missing-action-scoped-verification-receipt",
        )
        return failed, ControlDecision(False, failed.failure_code, failed.failure_stage)

    if action_requires_persistence_evidence(proposal):
        persistence_claim = can_claim(verified, CompletionClaim.PERSISTED)
        if not persistence_claim.allowed:
            unsynced = mark_unsynced(
                verified,
                stage=FailureStage.PERSISTENCE,
                code="MUTATION_WITHOUT_ACTION_SCOPED_STATE_READBACK",
                mechanism="missing-action-scoped-state-readback",
            )
            return unsynced, ControlDecision(False, unsynced.failure_code, unsynced.failure_stage)

    verified = transition(verified, RunPhase.VERIFIED)
    return verified, ControlDecision(True, "VERIFIED")


def run_controlled_action(
    record: ExecutionRecord,
    proposal: ActionProposal,
    *,
    executor: ToolExecutor,
    verifier: OutcomeVerifier,
    authority_preflight: AuthorityPreflight | None = None,
    hao_authorized_scopes: Iterable[str] = (),
    close_when_complete: bool = True,
) -> ControlledRunResult:
    admitted, admission = admit_action(
        record,
        proposal,
        hao_authorized_scopes=hao_authorized_scopes,
    )
    if not admission.allowed:
        return ControlledRunResult(
            admitted,
            admission,
            ControlDecision(False, "ACTION_NOT_ADMITTED", admission.failed_at),
        )

    if proposal.authority_snapshot_fingerprint:
        if authority_preflight is None:
            blocked = block_run(
                admitted,
                stage=FailureStage.AUTHORITY,
                code="AUTHORITY_PREFLIGHT_REQUIRED",
            )
            return ControlledRunResult(
                blocked,
                admission,
                ControlDecision(False, blocked.failure_code, FailureStage.AUTHORITY),
            )
        try:
            preflight = authority_preflight.verify_current(proposal)
        except Exception as exc:
            blocked = block_run(
                admitted,
                stage=FailureStage.AUTHORITY,
                code=f"AUTHORITY_PREFLIGHT_EXCEPTION:{type(exc).__name__}",
            )
            return ControlledRunResult(
                blocked,
                admission,
                ControlDecision(False, blocked.failure_code, FailureStage.AUTHORITY),
            )
        admitted, current_decision = apply_authority_preflight(admitted, proposal, preflight)
        if not current_decision.allowed:
            return ControlledRunResult(admitted, admission, current_decision)

    executing = transition(admitted, RunPhase.EXECUTING)

    try:
        tool_outcome = executor.execute(proposal)
    except Exception as exc:
        failed = record_failure(
            executing,
            stage=FailureStage.TOOL_EXECUTION,
            code=f"EXECUTOR_EXCEPTION:{type(exc).__name__}",
            mechanism="executor-exception",
        )
        return ControlledRunResult(
            failed,
            admission,
            ControlDecision(False, failed.failure_code, failed.failure_stage),
        )

    observed, tool_decision = apply_tool_outcome(executing, proposal, tool_outcome)
    if not tool_decision.allowed:
        return ControlledRunResult(observed, admission, tool_decision)

    try:
        verification = verifier.verify(observed, proposal, tool_outcome)
    except Exception as exc:
        failed = _post_effect_failure(
            observed,
            proposal,
            stage=FailureStage.VERIFICATION,
            code=f"VERIFIER_EXCEPTION:{type(exc).__name__}",
            mechanism="verifier-exception",
        )
        return ControlledRunResult(
            failed,
            admission,
            ControlDecision(False, failed.failure_code, failed.failure_stage),
        )

    verified, verification_decision = apply_verification_outcome(
        observed,
        proposal,
        verification,
    )
    if not verification_decision.allowed:
        return ControlledRunResult(verified, admission, verification_decision)

    completion = can_claim(verified, CompletionClaim.COMPLETED)
    if close_when_complete and completion.allowed:
        verified = close_run(verified)

    return ControlledRunResult(verified, admission, completion)
