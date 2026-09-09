from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .context_bound_reasoning import ContextBoundReasoningResult
from .control_gateway import PreModelContextRequest
from .operational_state import ActiveOperationalState, CommandActor


class ContextReasoningIngress(Protocol):
    def prepare(
        self,
        state: ActiveOperationalState,
        request: PreModelContextRequest,
        *,
        run_id: str,
        sequence: int = 1,
    ) -> ContextBoundReasoningResult: ...


class OperationalStateSource(Protocol):
    def get(self) -> ActiveOperationalState: ...


@dataclass(frozen=True)
class ContextReasoningConsumerResult:
    """Safe interaction-facing projection of one pre-execution reasoning attempt."""

    code: str
    action_selected: bool
    decision_id: str = ""
    action_id: str = ""
    authorization_scope: str = ""


class ContextBoundReasoningConsumer:
    """Force one raw Hao user turn through the context-bound first-model seam.

    The consumer accepts no caller-supplied Mode, TASK, Authority refs, context
    receipt, model intent, provider, action metadata or completion fields. Mode
    and TASK are read from Runtime operational state; context is resolved by the
    ingress; the model may only propose a non-authoritative intent; and the
    existing ControlPlane decides whether an action can be selected.

    This service performs no external provider mutation. It produces a prepared
    controlled action projection for the existing execution path.
    """

    def __init__(
        self,
        *,
        state_source: OperationalStateSource,
        ingress: ContextReasoningIngress,
    ) -> None:
        self._state_source = state_source
        self._ingress = ingress

    def prepare_user_turn(
        self,
        user_text: str,
        *,
        run_id: str,
        event_id: str = "",
        sequence: int = 1,
    ) -> ContextReasoningConsumerResult:
        if not run_id.strip():
            raise ValueError("RUN_ID_REQUIRED")
        if sequence < 1:
            raise ValueError("ACTION_SEQUENCE_MUST_BE_POSITIVE")

        state = self._state_source.get()
        result = self._ingress.prepare(
            state,
            PreModelContextRequest(
                user_text=user_text,
                actor=CommandActor.USER,
                event_id=event_id.strip(),
            ),
            run_id=run_id.strip(),
            sequence=sequence,
        )

        prepared = result.prepared
        proposal = None if prepared is None else prepared.resolution.proposal
        record = None if prepared is None else prepared.record
        return ContextReasoningConsumerResult(
            code=result.code,
            action_selected=proposal is not None,
            decision_id="" if record is None else record.decision_id,
            action_id="" if proposal is None else proposal.action_id,
            authorization_scope="" if proposal is None else proposal.authorization_scope,
        )
