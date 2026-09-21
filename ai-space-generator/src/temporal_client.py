from __future__ import annotations

from typing import Any

from .execution_control import ExecutionRecord
from .temporal_control import (
    ApprovalSignal,
    DurableRunInput,
    DurableRunResult,
    HaoExecutionControlWorkflow,
)


class TemporalControlledRunHandle:
    def __init__(self, handle: Any, workflow_id: str) -> None:
        self._handle = handle
        self._workflow_id = workflow_id

    @property
    def workflow_id(self) -> str:
        return self._workflow_id

    async def result(self) -> DurableRunResult:
        return await self._handle.result()

    async def authorize(self, scope: str, approved: bool, reason: str = "") -> None:
        if not scope.strip():
            raise ValueError("AUTHORIZATION_SCOPE_REQUIRED")
        await self._handle.signal(
            HaoExecutionControlWorkflow.authorization,
            ApprovalSignal(scope.strip(), approved, reason.strip()),
        )

    async def current_state(self) -> ExecutionRecord | None:
        return await self._handle.query(HaoExecutionControlWorkflow.current_state)


class TemporalWorkflowStarter:
    """Production adapter from the control facade to Temporal Client.

    Handles are reconstructable from workflow IDs so MCP/HTTP calls do not need
    to retain an in-memory Python object between submit, approval, status, and
    finalization requests.
    """

    def __init__(self, client: Any, *, task_queue: str) -> None:
        if not task_queue.strip():
            raise ValueError("TEMPORAL_TASK_QUEUE_REQUIRED")
        self._client = client
        self._task_queue = task_queue.strip()

    async def start(self, run_input: DurableRunInput) -> TemporalControlledRunHandle:
        workflow_id = run_input.record.run_id.strip()
        if not workflow_id:
            raise ValueError("WORKFLOW_RUN_ID_REQUIRED")
        handle = await self._client.start_workflow(
            HaoExecutionControlWorkflow.run,
            run_input,
            id=workflow_id,
            task_queue=self._task_queue,
        )
        return TemporalControlledRunHandle(handle, workflow_id)

    async def attach(self, workflow_id: str) -> TemporalControlledRunHandle:
        workflow_id = workflow_id.strip()
        if not workflow_id:
            raise ValueError("WORKFLOW_RUN_ID_REQUIRED")
        # String-ID attachment loses the workflow method's return annotation unless
        # the result type is supplied explicitly. Without it, a process/API restart
        # can deserialize DurableRunResult as a plain dict and break finalization.
        handle = self._client.get_workflow_handle(
            workflow_id,
            result_type=DurableRunResult,
        )
        return TemporalControlledRunHandle(handle, workflow_id)
