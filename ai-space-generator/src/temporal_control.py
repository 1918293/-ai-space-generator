from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Protocol

from temporalio import activity, workflow
from temporalio.common import RetryPolicy

from .controlled_runner import (
    ToolOutcome,
    VerificationOutcome,
    apply_tool_outcome,
    apply_verification_outcome,
)
from .execution_control import (
    ActionProposal,
    CompletionClaim,
    ControlDecision,
    ExecutionRecord,
    FailureStage,
    RunPhase,
    admit_action,
    block_run,
    can_claim,
    close_run,
    record_failure,
    transition,
)


@dataclass(frozen=True)
class ApprovalSignal:
    scope: str
    approved: bool
    reason: str = ""


@dataclass(frozen=True)
class DurableRunInput:
    record: ExecutionRecord
    proposal: ActionProposal
    initial_authorized_scopes: tuple[str, ...] = ()


@dataclass(frozen=True)
class VerificationRequest:
    record: ExecutionRecord
    proposal: ActionProposal
    tool_outcome: ToolOutcome


@dataclass(frozen=True)
class DurableRunResult:
    record: ExecutionRecord
    admission: ControlDecision
    completion: ControlDecision


class AsyncToolBroker(Protocol):
    async def execute(self, proposal: ActionProposal) -> ToolOutcome: ...


class AsyncOutcomeVerifier(Protocol):
    async def verify(
        self,
        record: ExecutionRecord,
        proposal: ActionProposal,
        tool_outcome: ToolOutcome,
    ) -> VerificationOutcome: ...


class ExecutionActivities:
    """Worker-side bridge to real providers.

    The workflow never holds provider credentials and never performs network I/O.
    Side effects happen only in these Activities through an injected broker.
    """

    def __init__(self, broker: AsyncToolBroker, verifier: AsyncOutcomeVerifier) -> None:
        self._broker = broker
        self._verifier = verifier

    @activity.defn(name="hao_execute_tool")
    async def execute_tool(self, proposal: ActionProposal) -> ToolOutcome:
        return await self._broker.execute(proposal)

    @activity.defn(name="hao_verify_outcome")
    async def verify_outcome(self, request: VerificationRequest) -> VerificationOutcome:
        return await self._verifier.verify(
            request.record,
            request.proposal,
            request.tool_outcome,
        )


@workflow.defn(name="HaoExecutionControlWorkflow")
class HaoExecutionControlWorkflow:
    """Durable owner of one controlled Hao System action.

    Temporal owns the long-lived transition state. Provider I/O is isolated in
    Activities. Automatic Activity retries are disabled at this layer because
    Hao System retry admission must first classify the prior failure and prove a
    material delta; idempotent provider-specific retry can be added explicitly by
    the broker after that decision.
    """

    def __init__(self) -> None:
        self._record: ExecutionRecord | None = None
        self._proposal: ActionProposal | None = None
        self._approved_scopes: set[str] = set()
        self._rejected_scopes: set[str] = set()
        self._rejection_reasons: dict[str, str] = {}

    @workflow.query
    def current_state(self) -> ExecutionRecord | None:
        return self._record

    @workflow.signal
    def authorization(self, decision: ApprovalSignal) -> None:
        scope = decision.scope.strip()
        if not scope:
            return
        if decision.approved:
            self._approved_scopes.add(scope)
            self._rejected_scopes.discard(scope)
            self._rejection_reasons.pop(scope, None)
        else:
            self._rejected_scopes.add(scope)
            self._approved_scopes.discard(scope)
            self._rejection_reasons[scope] = decision.reason.strip()

    @workflow.run
    async def run(self, run_input: DurableRunInput) -> DurableRunResult:
        self._record = run_input.record
        self._proposal = run_input.proposal
        self._approved_scopes.update(run_input.initial_authorized_scopes)

        admitted, admission = admit_action(
            self._record,
            self._proposal,
            hao_authorized_scopes=self._approved_scopes,
        )
        self._record = admitted

        if admission.requires_hao_authorization:
            scope = self._proposal.authorization_scope
            await workflow.wait_condition(
                lambda: scope in self._approved_scopes or scope in self._rejected_scopes
            )
            if scope in self._rejected_scopes:
                self._record = block_run(
                    self._record,
                    stage=FailureStage.POLICY,
                    code="HAO_AUTHORIZATION_REJECTED",
                )
                return DurableRunResult(
                    self._record,
                    ControlDecision(
                        False,
                        "HAO_AUTHORIZATION_REJECTED",
                        FailureStage.POLICY,
                    ),
                    ControlDecision(
                        False,
                        "ACTION_NOT_ADMITTED",
                        FailureStage.POLICY,
                    ),
                )

            self._record = transition(self._record, RunPhase.RESOLVED)
            admitted, admission = admit_action(
                self._record,
                self._proposal,
                hao_authorized_scopes=self._approved_scopes,
            )
            self._record = admitted

        if not admission.allowed:
            return DurableRunResult(
                self._record,
                admission,
                ControlDecision(False, "ACTION_NOT_ADMITTED", admission.failed_at),
            )

        self._record = transition(self._record, RunPhase.EXECUTING)
        no_automatic_retry = RetryPolicy(maximum_attempts=1)

        try:
            tool_outcome = await workflow.execute_activity(
                "hao_execute_tool",
                self._proposal,
                start_to_close_timeout=timedelta(minutes=10),
                retry_policy=no_automatic_retry,
                result_type=ToolOutcome,
            )
        except Exception as exc:
            self._record = record_failure(
                self._record,
                stage=FailureStage.TOOL_EXECUTION,
                code=f"ACTIVITY_EXCEPTION:{type(exc).__name__}",
                mechanism="temporal-tool-activity-exception",
            )
            return DurableRunResult(
                self._record,
                admission,
                ControlDecision(False, self._record.failure_code, self._record.failure_stage),
            )

        self._record, tool_decision = apply_tool_outcome(
            self._record,
            self._proposal,
            tool_outcome,
        )
        if not tool_decision.allowed:
            return DurableRunResult(self._record, admission, tool_decision)

        try:
            verification = await workflow.execute_activity(
                "hao_verify_outcome",
                VerificationRequest(self._record, self._proposal, tool_outcome),
                start_to_close_timeout=timedelta(minutes=10),
                retry_policy=no_automatic_retry,
                result_type=VerificationOutcome,
            )
        except Exception as exc:
            verification = VerificationOutcome(
                False,
                error_code=f"VERIFICATION_ACTIVITY_EXCEPTION:{type(exc).__name__}",
                failure_stage=FailureStage.VERIFICATION,
            )

        self._record, verification_decision = apply_verification_outcome(
            self._record,
            self._proposal,
            verification,
        )
        if not verification_decision.allowed:
            return DurableRunResult(self._record, admission, verification_decision)

        completion = can_claim(self._record, CompletionClaim.COMPLETED)
        if completion.allowed:
            self._record = close_run(self._record)

        return DurableRunResult(self._record, admission, completion)
