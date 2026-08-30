from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .action_catalog import ActionCatalog, ActionResolution, ModelActionIntent, resolve_model_intent
from .execution_control import AuthorityStamp, ExecutionRecord
from .operational_state import ActiveOperationalState, execution_record_from_operational_state


@dataclass(frozen=True)
class TaskExecutionPolicy:
    """Trusted execution requirements resolved outside model output."""

    goal_valid: bool
    acceptance_criteria: tuple[str, ...]
    required_acceptance_gate_ids: tuple[str, ...] = ()
    hao_acceptance_required: bool = False
    authority_refs: tuple[str, ...] = ()
    authority_stamps: tuple[AuthorityStamp, ...] = ()
    required_action_authority_refs: tuple[str, ...] = ()
    required_action_tags: tuple[str, ...] = ()
    forbidden_action_tags: tuple[str, ...] = ()


class TaskPolicyProvider(Protocol):
    def resolve(self, state: ActiveOperationalState) -> TaskExecutionPolicy: ...


@dataclass(frozen=True)
class ModelIngressRequest:
    run_id: str
    sequence: int
    intent: ModelActionIntent


@dataclass(frozen=True)
class PreparedControlledAction:
    record: ExecutionRecord | None
    resolution: ActionResolution


class ControlPlaneGateway:
    """The model-facing ingress boundary.

    The model supplies only `ModelActionIntent`. Mode/TASK come from durable
    operational state; acceptance, authority and action invariants come from a
    trusted policy provider; tool safety metadata comes from ActionCatalog.
    """

    def __init__(self, catalog: ActionCatalog, policy_provider: TaskPolicyProvider) -> None:
        self._catalog = catalog
        self._policy_provider = policy_provider

    def prepare(
        self,
        state: ActiveOperationalState,
        request: ModelIngressRequest,
    ) -> PreparedControlledAction:
        if not request.run_id.strip():
            raise ValueError("RUN_ID_REQUIRED")

        policy = self._policy_provider.resolve(state)
        record = execution_record_from_operational_state(
            state,
            run_id=request.run_id,
            goal_valid=policy.goal_valid,
            acceptance_criteria=policy.acceptance_criteria,
            required_acceptance_gate_ids=policy.required_acceptance_gate_ids,
            hao_acceptance_required=policy.hao_acceptance_required,
            authority_refs=policy.authority_refs,
            authority_stamps=policy.authority_stamps,
            required_action_authority_refs=policy.required_action_authority_refs,
            required_action_tags=policy.required_action_tags,
            forbidden_action_tags=policy.forbidden_action_tags,
        )
        resolution = resolve_model_intent(
            record,
            request.intent,
            self._catalog,
            sequence=request.sequence,
        )
        if resolution.proposal is None:
            return PreparedControlledAction(None, resolution)
        return PreparedControlledAction(record, resolution)
