from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .execution_control import (
    ActionArchetype,
    ActionExternality,
    ActionProposal,
    ControlDecision,
    ExecutionRecord,
    FailureStage,
    authority_snapshot_fingerprint,
)


@dataclass(frozen=True)
class ActionBinding:
    """Trusted runtime metadata for one provider action.

    The model never authors archetype, externality, assurance tags, rollback
    semantics, or whether an action needs an idempotency key. Those facts belong
    to the control-plane catalog and are reviewed/tested like code.
    """

    binding_id: str
    capability: str
    provider: str
    action_name: str
    archetype: ActionArchetype
    externality: ActionExternality
    assurance_tags: tuple[str, ...] = ()
    authorization_scope_prefix: str = ""
    rollback_available: bool = False


@dataclass(frozen=True)
class ModelActionIntent:
    """The maximum authority granted to a reasoning model.

    This is a request for a trusted binding, not an executable tool call. Safety
    classification and authorization are deliberately absent from this schema.
    """

    intent_id: str
    requested_capability: str
    binding_id: str
    expected_state_delta: str = ""
    authorization_target: str = ""


@dataclass(frozen=True)
class ActionResolution:
    proposal: ActionProposal | None
    decision: ControlDecision


class ActionCatalog:
    def __init__(self, bindings: Iterable[ActionBinding]) -> None:
        by_id: dict[str, ActionBinding] = {}
        for binding in bindings:
            key = binding.binding_id.strip()
            if not key:
                raise ValueError("BINDING_ID_REQUIRED")
            if key in by_id:
                raise ValueError(f"DUPLICATE_BINDING_ID:{key}")
            if not binding.capability.strip() or not binding.provider.strip() or not binding.action_name.strip():
                raise ValueError(f"INCOMPLETE_BINDING:{key}")
            by_id[key] = binding
        self._by_id = by_id

    def get(self, binding_id: str) -> ActionBinding | None:
        return self._by_id.get(binding_id.strip())

    def binding_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._by_id))


def _authorization_scope(binding: ActionBinding, target: str) -> str:
    prefix = binding.authorization_scope_prefix.strip()
    if not prefix:
        return ""
    target = target.strip()
    return f"{prefix}:{target}" if target else prefix


def resolve_model_intent(
    record: ExecutionRecord,
    intent: ModelActionIntent,
    catalog: ActionCatalog,
    *,
    sequence: int,
) -> ActionResolution:
    """Compile a non-authoritative model intent into a trusted ActionProposal."""
    if not intent.intent_id.strip():
        return ActionResolution(
            None,
            ControlDecision(False, "MODEL_INTENT_ID_REQUIRED", FailureStage.PLAN),
        )
    if sequence < 1:
        return ActionResolution(
            None,
            ControlDecision(False, "ACTION_SEQUENCE_MUST_BE_POSITIVE", FailureStage.PLAN),
        )

    binding = catalog.get(intent.binding_id)
    if binding is None:
        return ActionResolution(
            None,
            ControlDecision(False, "UNREGISTERED_ACTION_BINDING", FailureStage.BINDING),
        )

    if intent.requested_capability.strip() != binding.capability:
        return ActionResolution(
            None,
            ControlDecision(False, "CAPABILITY_BINDING_MISMATCH", FailureStage.ROUTING),
        )

    snapshot = authority_snapshot_fingerprint(
        record.authority_stamps,
        record.required_action_authority_refs,
    )
    if record.required_action_authority_refs and not snapshot:
        return ActionResolution(
            None,
            ControlDecision(False, "AUTHORITY_VERSION_UNRESOLVED", FailureStage.AUTHORITY),
        )

    action_id = f"{record.run_id}:A{sequence:04d}:{binding.binding_id}"
    idempotency_key = ""
    if binding.archetype in {ActionArchetype.MUTATE, ActionArchetype.PUBLISH}:
        idempotency_key = action_id

    proposal = ActionProposal(
        action_id=action_id,
        archetype=binding.archetype,
        externality=binding.externality,
        capability=binding.capability,
        provider=binding.provider,
        action_name=binding.action_name,
        expected_state_delta=intent.expected_state_delta,
        required_authority_refs=record.required_action_authority_refs,
        authority_snapshot_fingerprint=snapshot,
        authorization_scope=_authorization_scope(binding, intent.authorization_target),
        idempotency_key=idempotency_key,
        rollback_available=binding.rollback_available,
        assurance_tags=binding.assurance_tags,
    )
    return ActionResolution(
        proposal,
        ControlDecision(True, "MODEL_INTENT_RESOLVED_TO_TRUSTED_BINDING"),
    )
