from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from hashlib import sha256
from typing import Protocol

from .authoritative_completion import (
    AuthoritativeCompletionStore,
    CompletionAttestor,
    ExecutionAttestation,
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

    async def attach(self, workflow_id: str) -> ControlledWorkflowHandle: ...


class DecisionTelemetrySink(Protocol):
    def record_decision_event(
        self,
        event: str,
        record: ExecutionRecord,
        *,
        admission_code: str = "",
        completion_code: str = "",
        authoritative: bool | None = None,
    ) -> None: ...


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


class TerminalDeliveryStatus(StrEnum):
    PENDING = "PENDING"
    ACKNOWLEDGED = "ACKNOWLEDGED"


@dataclass(frozen=True)
class TerminalDeliveryReceipt:
    """Consumer-scoped terminal delivery evidence, separate from completion authority.

    A runtime consumer acknowledgement proves only that the controllable consumer
    accepted this terminal result. It never upgrades itself into native ChatGPT
    field acceptance or any other external presentation guarantee.
    """

    delivery_id: str
    run_id: str
    status: TerminalDeliveryStatus
    scope: str = "CONTROLLED_RUNTIME_CONSUMER_ONLY"
    consumer: str = ""
    consumer_receipt_id: str = ""
    field_acceptance_proven: bool = False


@dataclass(frozen=True)
class ProductionExecutionResult:
    record: ExecutionRecord | None
    authoritative: bool
    code: str
    attestation: ExecutionAttestation | None = None
    terminal_delivery: TerminalDeliveryReceipt | None = None

    @property
    def terminal_delivered(self) -> bool:
        return (
            self.terminal_delivery is not None
            and self.terminal_delivery.status == TerminalDeliveryStatus.ACKNOWLEDGED
        )


def _pending_terminal_delivery(
    attestation: ExecutionAttestation,
) -> TerminalDeliveryReceipt:
    digest = sha256(
        f"{attestation.run_id}:{attestation.signature}".encode("utf-8")
    ).hexdigest()
    return TerminalDeliveryReceipt(
        delivery_id="TERMINAL:" + digest,
        run_id=attestation.run_id,
        status=TerminalDeliveryStatus.PENDING,
    )


def acknowledge_terminal_delivery(
    result: ProductionExecutionResult,
    *,
    consumer: str,
    receipt_id: str,
) -> ProductionExecutionResult:
    """Acknowledge delivery by one controllable Runtime consumer.

    This acknowledgement is intentionally conservative and consumer-scoped. It
    does not claim that the native ChatGPT renderer, mobile app, or any other
    external field actually displayed the terminal output.
    """

    consumer = consumer.strip()
    receipt_id = receipt_id.strip()
    if (
        not result.authoritative
        or result.attestation is None
        or result.terminal_delivery is None
    ):
        raise ValueError("AUTHORITATIVE_COMPLETION_REQUIRED_FOR_TERMINAL_DELIVERY")
    if not consumer or not receipt_id:
        raise ValueError("TERMINAL_DELIVERY_CONSUMER_AND_RECEIPT_REQUIRED")

    current = result.terminal_delivery
    if current.status == TerminalDeliveryStatus.ACKNOWLEDGED:
        if current.consumer == consumer and current.consumer_receipt_id == receipt_id:
            return result
        raise ValueError("TERMINAL_DELIVERY_ACK_CONFLICT")

    acknowledged = replace(
        current,
        status=TerminalDeliveryStatus.ACKNOWLEDGED,
        consumer=consumer,
        consumer_receipt_id=receipt_id,
        field_acceptance_proven=False,
    )
    return replace(result, terminal_delivery=acknowledged)


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
    callers can reconstruct a handle from durable workflow identity instead of
    trusting client-side conversational memory.
    """

    def __init__(
        self,
        *,
        gateway: ControlPlaneGateway,
        starter: DurableWorkflowStarter,
        attestor: CompletionAttestor,
        completion_store: AuthoritativeCompletionStore,
        telemetry: DecisionTelemetrySink | None = None,
    ) -> None:
        self._gateway = gateway
        self._starter = starter
        self._attestor = attestor
        self._completion_store = completion_store
        self._telemetry = telemetry

    def _record_decision_event(
        self,
        event: str,
        record: ExecutionRecord,
        *,
        admission_code: str = "",
        completion_code: str = "",
        authoritative: bool | None = None,
    ) -> None:
        if self._telemetry is None:
            return
        self._telemetry.record_decision_event(
            event,
            record,
            admission_code=admission_code,
            completion_code=completion_code,
            authoritative=authoritative,
        )

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

    async def resume(
        self,
        workflow_id: str,
        *,
        operational_version: int,
    ) -> PendingControlledRun:
        if operational_version < 1:
            raise ValueError("OPERATIONAL_VERSION_REQUIRED")
        handle = await self._starter.attach(workflow_id)
        return PendingControlledRun(handle, operational_version)

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

        # These are outcome observations of the durable workflow, emitted outside
        # Temporal workflow code so tracing cannot affect replay determinism.
        self._record_decision_event(
            "admission",
            record,
            admission_code=durable_result.admission.code,
        )
        self._record_decision_event("provider_readback", record)
        self._record_decision_event("verification", record)

        if record.phase != RunPhase.CLOSED:
            code = "CONTROLLED_RUN_NOT_CLOSED:" + record.phase.value
            self._record_decision_event(
                "finalization",
                record,
                completion_code=code,
                authoritative=False,
            )
            return ProductionExecutionResult(
                record,
                False,
                code,
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
        self._record_decision_event(
            "finalization",
            record,
            completion_code=commit.code,
            authoritative=commit.committed,
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
            terminal_delivery=_pending_terminal_delivery(attestation),
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
