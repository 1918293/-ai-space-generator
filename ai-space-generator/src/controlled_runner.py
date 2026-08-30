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
    add_evidence,
    admit_action,
    can_claim,
    close_run,
    record_failure,
    transition,
)


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


@dataclass(frozen=True)
class ControlledRunResult:
    record: ExecutionRecord
    admission: ControlDecision
    completion: ControlDecision


class ToolExecutor(Protocol):
    def execute(self, proposal: ActionProposal) -> ToolOutcome: ...


class OutcomeVerifier(Protocol):
    def verify(
        self,
        record: ExecutionRecord,
        proposal: ActionProposal,
        tool_outcome: ToolOutcome,
    ) -> VerificationOutcome: ...


def run_controlled_action(
    record: ExecutionRecord,
    proposal: ActionProposal,
    *,
    executor: ToolExecutor,
    verifier: OutcomeVerifier,
    hao_authorized_scopes: Iterable[str] = (),
    close_when_complete: bool = True,
) -> ControlledRunResult:
    """Run exactly one bounded action through deterministic control ownership.

    The model may propose an action, but it cannot advance phases, convert a tool
    response into a verified result, or emit CLOSED. Those transitions happen
    here from executable evidence only.
    """
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

    executing = transition(admitted, RunPhase.EXECUTING)

    try:
        tool_outcome = executor.execute(proposal)
    except Exception as exc:  # runtime boundary: exceptions become typed failures
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

    if not tool_outcome.success:
        failed = record_failure(
            executing,
            stage=tool_outcome.failure_stage,
            code=tool_outcome.error_code or "TOOL_EXECUTION_FAILED",
            mechanism=(tool_outcome.error_code or "tool-execution-failed").lower(),
        )
        return ControlledRunResult(
            failed,
            admission,
            ControlDecision(False, failed.failure_code, failed.failure_stage),
        )

    if not tool_outcome.receipt_id.strip() or not tool_outcome.source.strip():
        failed = record_failure(
            executing,
            stage=FailureStage.TOOL_OUTPUT,
            code="TOOL_SUCCESS_WITHOUT_RECEIPT",
            mechanism="missing-tool-receipt",
        )
        return ControlledRunResult(
            failed,
            admission,
            ControlDecision(False, failed.failure_code, failed.failure_stage),
        )

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

    try:
        verification = verifier.verify(observed, proposal, tool_outcome)
    except Exception as exc:
        failed = record_failure(
            observed,
            stage=FailureStage.VERIFICATION,
            code=f"VERIFIER_EXCEPTION:{type(exc).__name__}",
            mechanism="verifier-exception",
        )
        return ControlledRunResult(
            failed,
            admission,
            ControlDecision(False, failed.failure_code, failed.failure_stage),
        )

    if not verification.passed:
        failed = record_failure(
            observed,
            stage=FailureStage.VERIFICATION,
            code=verification.error_code or "VERIFICATION_FAILED",
            mechanism=(verification.error_code or "verification-failed").lower(),
        )
        return ControlledRunResult(
            failed,
            admission,
            ControlDecision(False, failed.failure_code, failed.failure_stage),
        )

    verified = observed
    for receipt in verification.receipts:
        verified = add_evidence(verified, receipt)

    passing_kinds = {receipt.kind for receipt in verified.evidence if receipt.passed}
    if EvidenceKind.VERIFICATION_PASS not in passing_kinds:
        failed = record_failure(
            verified,
            stage=FailureStage.VERIFICATION,
            code="VERIFIER_DID_NOT_PRODUCE_VERIFICATION_RECEIPT",
            mechanism="missing-verification-receipt",
        )
        return ControlledRunResult(
            failed,
            admission,
            ControlDecision(False, failed.failure_code, failed.failure_stage),
        )

    verified = transition(verified, RunPhase.VERIFIED)
    completion = can_claim(verified, CompletionClaim.COMPLETED)

    if close_when_complete and completion.allowed:
        verified = close_run(verified)

    return ControlledRunResult(verified, admission, completion)
