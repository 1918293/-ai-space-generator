from __future__ import annotations

from .control_gateway import PreModelContextReceipt
from .operational_state import ActiveOperationalState


def context_receipt_is_current(
    receipt: PreModelContextReceipt,
    state: ActiveOperationalState,
) -> bool:
    """A material Runtime Current change invalidates an already-minted receipt."""

    return (
        receipt.mode == state.mode
        and receipt.task == state.task
        and receipt.operational_version == state.version
    )
