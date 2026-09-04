from __future__ import annotations

from typing import Any

from .runtime_observability import RuntimeTelemetry


class ObservableMCPControlBridge:
    """Telemetry decorator around MCPControlBridge without changing control semantics."""

    def __init__(self, bridge: Any, telemetry: RuntimeTelemetry) -> None:
        self._bridge = bridge
        self._telemetry = telemetry

    def operational_context(self, principal: Any) -> dict[str, object]:
        return self._bridge.operational_context(principal)

    async def submit(self, principal: Any, **kwargs: Any) -> Any:
        result = await self._bridge.submit(principal, **kwargs)
        self._telemetry.record_run_event(
            "submitted" if result.workflow_id else "submit_rejected",
            run_id=result.workflow_id,
            phase=result.phase,
            failure_stage="" if result.workflow_id else "ADMISSION",
            failure_code="" if result.workflow_id else result.code,
        )
        return result

    async def status(self, principal: Any, *, workflow_id: str) -> Any:
        result = await self._bridge.status(principal, workflow_id=workflow_id)
        self._telemetry.record_run_event(
            "status",
            run_id=workflow_id,
            phase=result.phase,
            failure_stage=result.failure_stage,
            failure_code=result.failure_code,
        )
        return result

    async def authorize_after_human_confirmation(
        self,
        principal: Any,
        **kwargs: Any,
    ) -> Any:
        result = await self._bridge.authorize_after_human_confirmation(principal, **kwargs)
        self._telemetry.record_run_event(
            "hao_authorization",
            run_id=result.workflow_id,
            phase=result.phase,
            failure_stage=result.failure_stage,
            failure_code=result.failure_code,
        )
        return result

    async def finalize(self, principal: Any, *, workflow_id: str) -> Any:
        result = await self._bridge.finalize(principal, workflow_id=workflow_id)
        self._telemetry.record_run_event(
            "finalized",
            run_id=workflow_id,
            phase=result.phase,
            failure_stage="" if result.authoritative else "COMPLETION",
            failure_code="" if result.authoritative else result.code,
        )
        if result.authoritative:
            self._telemetry.record_authoritative_completion()
        return result


class ObservableReconciliationBroker:
    """Observe a newly opened durable reconciliation case exactly once per action."""

    def __init__(
        self,
        broker: Any,
        reconciliation_store: Any,
        telemetry: RuntimeTelemetry,
    ) -> None:
        self._broker = broker
        self._store = reconciliation_store
        self._telemetry = telemetry

    async def execute(self, proposal: Any) -> Any:
        prior = self._store.get_by_action(proposal.action_id)
        outcome = await self._broker.execute(proposal)
        current = self._store.get_by_action(proposal.action_id)
        if prior is None and current is not None:
            self._telemetry.record_reconciliation(
                kind=current.kind.value,
                run_id=current.run_id,
                error_code=outcome.error_code,
            )
        self._telemetry.record_run_event(
            "provider_outcome",
            run_id=current.run_id if current is not None else "",
            phase="UNSYNCED" if current is not None else ("OBSERVED" if outcome.success else "FAILED"),
            provider=proposal.provider,
            failure_stage=(outcome.failure_stage.value if outcome.failure_stage else ""),
            failure_code=outcome.error_code,
        )
        return outcome
