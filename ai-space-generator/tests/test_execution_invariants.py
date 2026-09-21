from dataclasses import replace

from src.control_gateway import PreModelContextReceipt
from src.execution_control import Mode
from src.execution_invariants import context_receipt_is_current
from src.operational_state import ActiveOperationalState


def receipt():
    return PreModelContextReceipt(
        checkpoint_id="R200",
        mode=Mode.EXP,
        task="Semantic invariant validation",
        operational_version=200,
        authority_refs=("CURRENT:PROJECT_HAO",),
        existing_work_refs=("PR17:CURRENT",),
        prior_attempt_refs=(),
        regression_refs=("REG:R98",),
        reuse_disposition="REUSE",
        context_fingerprint="sha256:test-context",
    )


def state():
    return ActiveOperationalState(
        Mode.EXP,
        "Semantic invariant validation",
        200,
        "EVENT-200",
    )


def test_context_receipt_invalidates_on_each_material_current_change():
    current = state()
    current_receipt = receipt()
    assert context_receipt_is_current(current_receipt, current) is True
    assert context_receipt_is_current(
        current_receipt, replace(current, mode=Mode.SYS)
    ) is False
    assert context_receipt_is_current(
        current_receipt, replace(current, task="Changed task")
    ) is False
    assert context_receipt_is_current(
        current_receipt, replace(current, version=201)
    ) is False
