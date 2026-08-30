from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .authoritative_completion import (
    CompletionAttestor,
    ExecutionAttestation,
    SQLiteAuthoritativeCompletionStore,
)
from .control_gateway import ControlPlaneGateway, ModelIngressRequest
from .execution_control import ExecutionRecord, RunPhase
from .operational_state import ActiveOperationalState
from .temporal_control import DurableRunInput, DurableRunResult


class ControlledWorkflowHandle(Protocol):
    @property
    def workflow_id(self) -> str: ...

    async def result(self) -> DurableRunResult: ...

    async def authorize(self, scope: str, approved: bool, reason: str = "") -> None: ...

    async def current_state(self) -> ExecutionRecord | None: ...


class DurableWorkflowStarter(Protocol):
    async def start(self, run_input: DurableRunInput) -> ControlledWorkflowHandle: ...


@dataclass(frozen=True)
class PendingControlledRun:
    handle: ControlledWorkflowHandle
    operational_version: int


@dataclass(frozen=True)
class ProductionSubmissionResult:
    pending: PendingControlledRun | None
    record: ExecutionRecord | None
    accepted: bool
    code: str


@dataclass(frozen=True)
class ProductionExecutionResult:
    record: ExecutionRecord | None
    authoritative: bool
    code: str
    attestation: ExecutionAttestation | None = None


@dataclass(frozen=True)
class UncontrolledEffectReport:
    source: str
    receipt_id: str
    description: str = ""


@dataclass(frozen=True)
class UncontrolledEffectDisposition:
    authoritative: bool
    code: str
    requires_reconciliation: bool


def quarantine_uncontrolled_effect(
    report: UncontrolledEffectReport,
) -> UncontrolledEffectDisposition:
    """Classify out-of-band side effects without upgrading them to completion."""
    if not report.source.strip() or not report.receipt_id.strip():
        return UncontrolledEffectDisposition(
            False,
            "UNCONTROLLED_EFFECT_UNVERIFIABLE",
            True,
        )
    return UncontrolledEffectDisposition(
        False,
        "UNCONTROLLED_EFFECT_REQUIRES_RECONCILIATION",
        True,
    )


class ProductionExecutionService:
    """Single authoritative application path for controlled Hao System work.

    Submission is separated from finalization because approval waits and provider
    recovery can outlive one chat/HTTP request. Temporal owns that waiting state;
    the caller receives a durable handle rather than holding an open request.
    """

    def __init__(
        self,
        *,
        gateway: ControlPlaneGateway,
        starter: DurableWorkflowStarter,
        attestor: CompletionAttestor,
        completion_store: SQLiteAuthoritativeCompletionStore,
    ) -> None:
        self._gateway = gateway
        self._starter = starter
        self._attestor = attestor
        self._completion_store = completion_store

    async def submit(
        self,
        state: ActiveOperationalState,
        request: ModelIngressRequest,
    ) -> ProductionSubmissionResult:
        prepared = self._gateway.prepare(state, request)
        if prepared.record is None or prepared.resolution.proposal is None:
            return ProductionSubmissionResult(
                None,
                None,
                False,
                prepared.resolution.decision.code,
            )

        handle = await self._starter.start(
            DurableRunInput(prepared.record, prepared.resolution.proposal)
        )
        return ProductionSubmissionResult(
            PendingControlledRun(handle, state.version),
            prepared.record,
            True,
            "CONTROLLED_RUN_SUBMITTED",
        )

    async def authorize(
        self,
        pending: PendingControlledRun,
        *,
        scope: str,
        approved: bool,
        reason: str = "",
    ) -> None:
        await pending.handle.authorize(scope, approved, reason)

    async def current_state(
        self,
        pending: PendingControlledRun,
    ) -> ExecutionRecord | None:
        return await pending.handle.current_state()

    async def finalize(
        self,
        pending: PendingControlledRun,
        *,
        issued_at: str,
    ) -> ProductionExecutionResult:
        durable_result = await pending.handle.result()
        record = durable_result.record
        if record.phase != RunPhase.CLOSED:
            return ProductionExecutionResult(
                record,
                False,
                "CONTROLLED_RUN_NOT_CLOSED:" + record.phase.value,
            )

        attestation = self._attestor.issue(
            record,
            operational_version=pending.operational_version,
            issued_at=issued_at,
        )
        commit = self._completion_store.commit(
            attestation,
            record,
            operational_version=pending.operational_version,
            attestor=self._attestor,
        )
        if not commit.committed:
            return ProductionExecutionResult(
                record,
                False,
                commit.code,
                attestation,
            )

        return ProductionExecutionResult(
            record,
            True,
            commit.code,
            attestation,
        )

    async def execute(
        self,
        state: ActiveOperationalState,
        request: ModelIngressRequest,
        *,
        issued_at: str,
    ) -> ProductionExecutionResult:
        """Convenience path for workflows that do not require an external signal."""
        submission = await self.submit(state, request)
        if not submission.accepted or submission.pending is None:
            return ProductionExecutionResult(
                submission.record,
                False,
                submission.code,
            )
        return await self.finalize(submission.pending, issued_at=issued_at)
