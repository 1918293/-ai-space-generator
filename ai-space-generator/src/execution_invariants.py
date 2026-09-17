from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import re

from .control_gateway import PreModelContextReceipt
from .execution_control import CompletionClaim, ExecutionRecord, RunPhase, can_claim
from .operational_state import ActiveOperationalState


_WHITESPACE = re.compile(r"\s+")


def _normalized_text(value: str, *, field: str) -> str:
    normalized = _WHITESPACE.sub(" ", value.strip())
    if not normalized:
        raise ValueError(f"WORK_IDENTITY_{field.upper()}_REQUIRED")
    return normalized


def _normalized_set(values: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(sorted({_WHITESPACE.sub(" ", value.strip()) for value in values if value.strip()}))


@dataclass(frozen=True)
class ResolvedWorkIdentity:
    """Semantic identity after Current/intent resolution, never from raw prompt text.

    Attempt/runtime details such as provider, run key, lease epoch and execution
    lane are deliberately excluded. They identify an attempt, not the work.
    """

    owner_scope: str
    target: str
    objective: str
    deliverable_class: str
    acceptance_criteria: tuple[str, ...]
    authority_scope: tuple[str, ...] = ()


def work_fingerprint(identity: ResolvedWorkIdentity) -> str:
    acceptance = _normalized_set(identity.acceptance_criteria)
    if not acceptance:
        raise ValueError("WORK_IDENTITY_ACCEPTANCE_CRITERIA_REQUIRED")
    payload = {
        "owner_scope": _normalized_text(identity.owner_scope, field="owner_scope"),
        "target": _normalized_text(identity.target, field="target"),
        "objective": _normalized_text(identity.objective, field="objective"),
        "deliverable_class": _normalized_text(
            identity.deliverable_class, field="deliverable_class"
        ),
        "acceptance_criteria": acceptance,
        "authority_scope": _normalized_set(identity.authority_scope),
    }
    material = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "WORK:" + sha256(material).hexdigest()


def context_receipt_is_current(
    receipt: PreModelContextReceipt,
    state: ActiveOperationalState,
) -> bool:
    """A material Current change invalidates an already-minted context receipt."""

    return (
        receipt.mode == state.mode
        and receipt.task == state.task
        and receipt.operational_version == state.version
    )


def terminal_completion_renderable(record: ExecutionRecord) -> bool:
    """Final prose may say COMPLETED only from a closed, evidence-valid record."""

    return (
        record.phase == RunPhase.CLOSED
        and can_claim(record, CompletionClaim.COMPLETED).allowed
    )
