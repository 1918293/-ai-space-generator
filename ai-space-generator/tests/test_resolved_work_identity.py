from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.hao_authority_resolver import (
    HaoAuthorityRoutes,
    HaoCanonicalCurrent,
    HaoDriveCanonicalAuthoritySource,
    HaoExistingWorkResult,
    HaoLookupResult,
)
from src.resolved_work_identity import (
    CanonicalWorkIdentitySeed,
    current_context_fingerprint,
    resolve_work_identity_projection,
)


AUTHORITY_REFS = (
    "CURRENT:PROJECT",
    "CURRENT:TARGET",
    "CURRENT:OBJECTIVE",
    "CURRENT:DELIVERABLE",
    "REQUIREMENTS:R-036",
)
FIELD_SOURCES = (
    ("project_scope", "CURRENT:PROJECT"),
    ("logical_target", "CURRENT:TARGET"),
    ("objective", "CURRENT:OBJECTIVE"),
    ("deliverable_identity", "CURRENT:DELIVERABLE"),
    ("acceptance_identity", "REQUIREMENTS:R-036"),
)
SEED = CanonicalWorkIdentitySeed(
    project_scope="PROJECT_HAO",
    logical_target="runtime-v2/active-work",
    objective="ACTIVE_WORK_CANONICAL_IDENTITY",
    deliverable_identity="BOUNDED_ENGINEERING_VALIDATION",
    acceptance_identity="SAME_WAIT_JOIN|COMPLETE_NOOP|UNKNOWN_FAIL_CLOSED",
    field_sources=FIELD_SOURCES,
)


def projection(*, version=7, objective=None):
    seed = SEED if objective is None else CanonicalWorkIdentitySeed(
        project_scope=SEED.project_scope,
        logical_target=SEED.logical_target,
        objective=objective,
        deliverable_identity=SEED.deliverable_identity,
        acceptance_identity=SEED.acceptance_identity,
        field_sources=SEED.field_sources,
    )
    return resolve_work_identity_projection(
        seed,
        checkpoint_id="R200",
        task="Validate Active Work identity",
        operational_version=version,
        authority_refs=AUTHORITY_REFS,
    )


def test_semantic_identity_is_separate_from_current_binding():
    first = projection(version=7)
    later_current = projection(version=8)

    assert first.semantic_fingerprint == later_current.semantic_fingerprint
    assert first.current_fingerprint != later_current.current_fingerprint
    assert first.binding_fingerprint != later_current.binding_fingerprint


def test_material_semantic_delta_changes_semantic_identity():
    baseline = projection()
    changed = projection(objective="ACTIVE_WORK_MUTATION_WRITER")

    assert baseline.semantic_fingerprint != changed.semantic_fingerprint
    assert baseline.binding_fingerprint != changed.binding_fingerprint


def test_material_deliverable_delta_changes_semantic_identity():
    baseline = projection()
    changed_seed = CanonicalWorkIdentitySeed(
        project_scope=SEED.project_scope,
        logical_target=SEED.logical_target,
        objective=SEED.objective,
        deliverable_identity="PRODUCTION_ACTIVE_WORK_WIRING",
        acceptance_identity=SEED.acceptance_identity,
        field_sources=SEED.field_sources,
    )
    changed = resolve_work_identity_projection(
        changed_seed,
        checkpoint_id="R200",
        task="Validate Active Work identity",
        operational_version=7,
        authority_refs=AUTHORITY_REFS,
    )
    assert baseline.semantic_fingerprint != changed.semantic_fingerprint


def test_current_fingerprint_is_order_invariant_for_authority_refs():
    first = current_context_fingerprint(
        checkpoint_id="R200",
        task="Task",
        operational_version=3,
        authority_refs=("B", "A"),
    )
    second = current_context_fingerprint(
        checkpoint_id="R200",
        task="Task",
        operational_version=3,
        authority_refs=("A", "B"),
    )
    assert first == second


def test_blank_or_duplicate_identity_inputs_fail_closed():
    with pytest.raises(ValueError, match="WORK_IDENTITY_OBJECTIVE_REQUIRED"):
        resolve_work_identity_projection(
            CanonicalWorkIdentitySeed(
                "PROJECT_HAO",
                "target",
                " ",
                "deliverable",
                "acceptance",
                FIELD_SOURCES,
            ),
            checkpoint_id="R1",
            task="Task",
            operational_version=1,
            authority_refs=AUTHORITY_REFS,
        )

    with pytest.raises(ValueError, match="WORK_IDENTITY_AUTHORITY_REFS_DUPLICATE"):
        current_context_fingerprint(
            checkpoint_id="R1",
            task="Task",
            operational_version=1,
            authority_refs=("CURRENT", "CURRENT"),
        )


def test_each_semantic_field_requires_current_authority_provenance():
    missing = CanonicalWorkIdentitySeed(
        project_scope=SEED.project_scope,
        logical_target=SEED.logical_target,
        objective=SEED.objective,
        deliverable_identity=SEED.deliverable_identity,
        acceptance_identity=SEED.acceptance_identity,
        field_sources=FIELD_SOURCES[:-1],
    )
    with pytest.raises(ValueError, match="WORK_IDENTITY_SOURCE_FIELD_MISSING"):
        resolve_work_identity_projection(
            missing,
            checkpoint_id="R200",
            task="Task",
            operational_version=7,
            authority_refs=AUTHORITY_REFS,
        )

    stale_source = CanonicalWorkIdentitySeed(
        project_scope=SEED.project_scope,
        logical_target=SEED.logical_target,
        objective=SEED.objective,
        deliverable_identity=SEED.deliverable_identity,
        acceptance_identity=SEED.acceptance_identity,
        field_sources=tuple(
            (field, "SUPERSEDED:OBJECTIVE") if field == "objective" else (field, ref)
            for field, ref in FIELD_SOURCES
        ),
    )
    with pytest.raises(
        ValueError,
        match="WORK_IDENTITY_SOURCE_NOT_CURRENT_AUTHORITY:objective",
    ):
        resolve_work_identity_projection(
            stale_source,
            checkpoint_id="R200",
            task="Task",
            operational_version=7,
            authority_refs=AUTHORITY_REFS,
        )


def test_field_source_location_is_provenance_not_semantic_identity():
    baseline = projection()
    moved_seed = CanonicalWorkIdentitySeed(
        project_scope=SEED.project_scope,
        logical_target=SEED.logical_target,
        objective=SEED.objective,
        deliverable_identity=SEED.deliverable_identity,
        acceptance_identity=SEED.acceptance_identity,
        field_sources=tuple(
            (field, "CURRENT:OBJECTIVE_V2") if field == "objective" else (field, ref)
            for field, ref in FIELD_SOURCES
        ),
    )
    moved = resolve_work_identity_projection(
        moved_seed,
        checkpoint_id="R200",
        task="Validate Active Work identity",
        operational_version=8,
        authority_refs=AUTHORITY_REFS + ("CURRENT:OBJECTIVE_V2",),
    )

    assert baseline.semantic_fingerprint == moved.semantic_fingerprint
    assert baseline.current_fingerprint != moved.current_fingerprint
    assert baseline.binding_fingerprint != moved.binding_fingerprint


class Reader:
    def __init__(self, seed):
        self.seed = seed

    def resolve_current(self, routes, state, request, checkpoint_cue):
        return HaoCanonicalCurrent(
            checkpoint_id="R200",
            task=state.task,
            operational_version=state.version,
            authority_refs=AUTHORITY_REFS,
            verified=True,
            work_identity_seed=self.seed,
        )

    def lookup_existing_work(self, routes, state, request):
        return HaoExistingWorkResult((), True, "ADMIT")

    def lookup_prior_attempts(self, routes, state, request):
        return HaoLookupResult((), True)

    def lookup_regressions(self, routes, state, request):
        return HaoLookupResult((), True)


ROUTES = HaoAuthorityRoutes("current", "requirements", "handoff", "regressions", "prior")


def test_verified_current_can_project_authority_supplied_identity_without_task_inference():
    source = HaoDriveCanonicalAuthoritySource(Reader(SEED), ROUTES)
    state = SimpleNamespace(task="Natural language TASK text", version=7)
    snapshot = source.read_context(state, SimpleNamespace(), "R200")

    assert snapshot is not None
    assert snapshot.work_identity is not None
    assert snapshot.work_identity.objective == "ACTIVE_WORK_CANONICAL_IDENTITY"
    assert snapshot.work_identity.logical_target == "runtime-v2/active-work"
    assert snapshot.work_identity.deliverable_identity == "BOUNDED_ENGINEERING_VALIDATION"
    assert snapshot.work_identity.objective != state.task
    assert snapshot.work_identity.field_sources == FIELD_SOURCES


def test_missing_identity_seed_does_not_infer_identity_from_task():
    source = HaoDriveCanonicalAuthoritySource(Reader(None), ROUTES)
    state = SimpleNamespace(task="ACTIVE_WORK_CANONICAL_IDENTITY", version=7)
    snapshot = source.read_context(state, SimpleNamespace(), "R200")

    assert snapshot is not None
    assert snapshot.work_identity is None


def test_invalid_authority_supplied_identity_fails_closed():
    invalid = CanonicalWorkIdentitySeed(
        project_scope="PROJECT_HAO",
        logical_target="runtime-v2/active-work",
        objective="",
        deliverable_identity="BOUNDED_ENGINEERING_VALIDATION",
        acceptance_identity="acceptance",
        field_sources=FIELD_SOURCES,
    )
    source = HaoDriveCanonicalAuthoritySource(Reader(invalid), ROUTES)
    state = SimpleNamespace(task="Task", version=7)

    assert source.read_context(state, SimpleNamespace(), "R200") is None
