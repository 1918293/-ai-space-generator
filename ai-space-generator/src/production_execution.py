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


class DurableWorkflowRunner(Protocol):
    async def run(self, run_input: DurableRunInput) -> DurableRunResult: ...


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
    """Classify out-of-band side effects without upgrading them to completion.

    Native ChatGPT tools, manually executed provider calls, or any other path
    that bypasses the controlled workflow may still have real-world effects.
    Those effects are not ignored, but they cannot mint Hao System completion.
    They must be reconciled by a new controlled read/verification workflow.
    """
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

    The service accepts only a non-authoritative model intent. It resolves
    operational state and task policy through the trusted gateway, delegates the
    action to durable orchestration, and mints authoritative completion only
    after the workflow returns CLOSED with all evidence floors satisfied.
    """

    def __init__(
        self,
        *,
        gateway: ControlPlaneGateway,
        runner: DurableWorkflowRunner,
        attestor: CompletionAttestor,
        completion_store: SQLiteAuthoritativeCompletionStore,
    ) -> None:
        self._gateway = gateway
        self._runner = runner
        self._attestor = attestor
        self._completion_store = completion_store

    async def execute(
        self,
        state: ActiveOperationalState,
        request: ModelIngressRequest,
        *,
        issued_at: str,
    ) -> ProductionExecutionResult:
        prepared = self._gateway.prepare(state, request)
        if prepared.record is None or prepared.resolution.proposal is None:
            return ProductionExecutionResult(
                None,
                False,
                prepared.resolution.decision.code,
            )

        durable_result = await self._runner.run(
            DurableRunInput(prepared.record, prepared.resolution.proposal)
        )
        record = durable_result.record
        if record.phase != RunPhase.CLOSED:
            return ProductionExecutionResult(
                record,
                False,
                "CONTROLLED_RUN_NOT_CLOSED:" + record.phase.value,
            )

        attestation = self._attestor.issue(
            record,
            operational_version=state.version,
            issued_at=issued_at,
        )
        commit = self._completion_store.commit(
            attestation,
            record,
            operational_version=state.version,
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
