from dataclasses import replace

from src.control_gateway import PreModelContextReceipt
from src.execution_control import Mode
from src.execution_invariants import (
    ResolvedWorkIdentity,
    context_receipt_is_current,
    work_fingerprint,
)
from src.operational_state import ActiveOperationalState


def work(**overrides):
    values = dict(
        owner_scope="PROJECT_HAO",
        target="Runtime v2 / PR17",
        objective="Eliminate recurrent execution binding failures",
        deliverable_class="bounded engineering validation",
        acceptance_criteria=(
            "no false semantic coalesce",
            "no unsupported terminal completion",
        ),
        authority_scope=("HAO_SYSTEM", "PROJECT_HAO"),
    )
    values.update(overrides)
    return ResolvedWorkIdentity(**values)


def test_work_fingerprint_is_order_and_whitespace_stable():
    first = work()
    equivalent = work(
        owner_scope="  PROJECT_HAO ",
        target="Runtime   v2 / PR17",
        objective="Eliminate recurrent   execution binding failures",
        acceptance_criteria=tuple(reversed(first.acceptance_criteria)),
        authority_scope=tuple(reversed(first.authority_scope)),
    )
    assert work_fingerprint(first) == work_fingerprint(equivalent)


def test_every_material_work_dimension_changes_identity():
    baseline = work_fingerprint(work())
    mutants = (
        work(owner_scope="PROJECT_LOFTY"),
        work(target="Runtime v2 / different target"),
        work(objective="Different objective"),
        work(deliverable_class="different deliverable"),
        work(acceptance_criteria=("different acceptance",)),
        work(authority_scope=("PROJECT_HAO", "DIFFERENT_AUTHORITY_SCOPE")),
    )
    assert all(work_fingerprint(candidate) != baseline for candidate in mutants)


def test_work_fingerprint_rejects_under_specified_identity():
    for candidate in (
        work(owner_scope=""),
        work(target=""),
        work(objective=""),
        work(deliverable_class=""),
        work(acceptance_criteria=()),
    ):
        try:
            work_fingerprint(candidate)
        except ValueError:
            pass
        else:
            raise AssertionError("under-specified semantic identity must fail closed")


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
