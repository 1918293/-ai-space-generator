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
    WorkIdentityRelation,
    compare_work_identity,
    current_context_fingerprint,
    resolve_work_identity_projection,
)


AUTHORITY_REFS = (
    "CURRENT:WORK_KEY",
    "CURRENT:PROJECT",
    "CURRENT:TARGET",
    "CURRENT:DELIVERABLE",
    "REQUIREMENTS:R-036",
)
INTENT_REFS = ("HAO_INTENT:EVENT-200",)
FIELD_SOURCES = (
    ("work_key", "CURRENT_AUTHORITY", "CURRENT:WORK_KEY"),
    ("project_scope", "CURRENT_AUTHORITY", "CURRENT:PROJECT"),
    ("logical_target", "CURRENT_AUTHORITY", "CURRENT:TARGET"),
    ("objective", "HAO_INTENT", "HAO_INTENT:EVENT-200"),
    ("deliverable_identity", "HAO_INTENT", "HAO_INTENT:EVENT-200"),
    ("acceptance_identity", "CURRENT_AUTHORITY", "REQUIREMENTS:R-036"),
)
SEED = CanonicalWorkIdentitySeed(
    work_key="PROJECT_HAO:ACTIVE_WORK_COORDINATION",
    project_scope="PROJECT_HAO",
    logical_target="runtime-v2/active-work",
    objective="ACTIVE_WORK_CANONICAL_IDENTITY",
    deliverable_identity="BOUNDED_ENGINEERING_VALIDATION",
    acceptance_identity="SAME_WAIT_JOIN|COMPLETE_NOOP|UNKNOWN_FAIL_CLOSED",
    field_sources=FIELD_SOURCES,
)


def projection(*, version=7, seed=SEED, intent_refs=INTENT_REFS):
    return resolve_work_identity_projection(
        seed,
        checkpoint_id="R200",
        task="Validate Active Work identity",
        operational_version=version,
        authority_refs=AUTHORITY_REFS,
        intent_refs=intent_refs,
    )


def with_seed(**changes):
    values = {
        "work_key": SEED.work_key,
        "project_scope": SEED.project_scope,
        "logical_target": SEED.logical_target,
        "objective": SEED.objective,
        "deliverable_identity": SEED.deliverable_identity,
        "acceptance_identity": SEED.acceptance_identity,
        "field_sources": SEED.field_sources,
    }
    values.update(changes)
    return CanonicalWorkIdentitySeed(**values)


def test_stable_work_key_and_intent_fingerprint_are_separate_from_current_binding():
    first = projection(version=7)
    later_current = projection(version=8)

    assert first.work_key == later_current.work_key
    assert first.intent_fingerprint == later_current.intent_fingerprint
    assert first.semantic_fingerprint == first.intent_fingerprint
    assert first.current_fingerprint != later_current.current_fingerprint
    assert first.binding_fingerprint != later_current.binding_fingerprint
    assert compare_work_identity(first, later_current) == WorkIdentityRelation.SAME_WORK_SAME_INTENT


def test_same_work_key_with_changed_acceptance_is_changed_intent_not_new_work():
    baseline = projection()
    changed = projection(seed=with_seed(acceptance_identity="DEPLOYED_FIELD_PASS_REQUIRED"))

    assert baseline.work_key == changed.work_key
    assert baseline.intent_fingerprint != changed.intent_fingerprint
    assert compare_work_identity(baseline, changed) == WorkIdentityRelation.SAME_WORK_CHANGED_INTENT


def test_different_work_key_with_same_target_is_different_work():
    baseline = projection()
    changed = projection(seed=with_seed(work_key="PROJECT_HAO:ACTIVE_WORK_WRITER"))

    assert baseline.logical_target == changed.logical_target
    assert baseline.intent_fingerprint == changed.intent_fingerprint
    assert compare_work_identity(baseline, changed) == WorkIdentityRelation.DIFFERENT_WORK


def test_unknown_identity_never_auto_coalesces():
    assert compare_work_identity(None, projection()) == WorkIdentityRelation.UNKNOWN
    assert compare_work_identity(projection(), None) == WorkIdentityRelation.UNKNOWN


def test_typed_provenance_accepts_direct_hao_intent_and_current_authority_together():
    result = projection()

    assert ("objective", "HAO_INTENT", "HAO_INTENT:EVENT-200") in result.field_sources
    assert ("logical_target", "CURRENT_AUTHORITY", "CURRENT:TARGET") in result.field_sources


def test_model_or_unknown_source_class_is_rejected():
    invalid_sources = tuple(
        (field, "MODEL_OUTPUT", ref) if field == "objective" else (field, source_class, ref)
        for field, source_class, ref in FIELD_SOURCES
    )
    with pytest.raises(ValueError, match="WORK_IDENTITY_SOURCE_CLASS_INVALID"):
        projection(seed=with_seed(field_sources=invalid_sources))


def test_direct_intent_source_must_be_admitted_by_trusted_intent_path():
    with pytest.raises(ValueError, match="WORK_IDENTITY_SOURCE_NOT_HAO_INTENT:objective"):
        projection(intent_refs=("HAO_INTENT:DIFFERENT_EVENT",))


def test_current_authority_source_must_still_be_in_verified_current_refs():
    stale_sources = tuple(
        (field, source_class, "SUPERSEDED:TARGET")
        if field == "logical_target"
        else (field, source_class, ref)
        for field, source_class, ref in FIELD_SOURCES
    )
    with pytest.raises(
        ValueError,
        match="WORK_IDENTITY_SOURCE_NOT_CURRENT_AUTHORITY:logical_target",
    ):
        projection(seed=with_seed(field_sources=stale_sources))


def test_at_least_one_material_intent_field_requires_direct_hao_intent_binding():
    authority_only_sources = tuple(
        (field, "CURRENT_AUTHORITY", "REQUIREMENTS:R-036")
        if source_class == "HAO_INTENT"
        else (field, source_class, ref)
        for field, source_class, ref in FIELD_SOURCES
    )
    with pytest.raises(ValueError, match="WORK_IDENTITY_DIRECT_HAO_INTENT_BINDING_REQUIRED"):
        projection(seed=with_seed(field_sources=authority_only_sources))


def test_provenance_location_change_does_not_change_intent_semantics():
    baseline = projection()
    moved_sources = tuple(
        (field, source_class, "HAO_INTENT:EVENT-201")
        if field == "objective"
        else (field, source_class, ref)
        for field, source_class, ref in FIELD_SOURCES
    )
    moved = projection(
        version=8,
        seed=with_seed(field_sources=moved_sources),
        intent_refs=("HAO_INTENT:EVENT-201",),
    )

    assert baseline.work_key == moved.work_key
    assert baseline.intent_fingerprint == moved.intent_fingerprint
    assert baseline.current_fingerprint != moved.current_fingerprint
    assert compare_work_identity(baseline, moved) == WorkIdentityRelation.SAME_WORK_SAME_INTENT


def test_current_fingerprint_is_order_invariant_for_authority_and_intent_refs():
    first = current_context_fingerprint(
        checkpoint_id="R200",
        task="Task",
        operational_version=3,
        authority_refs=("B", "A"),
        intent_refs=("INTENT:B", "INTENT:A"),
    )
    second = current_context_fingerprint(
        checkpoint_id="R200",
        task="Task",
        operational_version=3,
        authority_refs=("A", "B"),
        intent_refs=("INTENT:A", "INTENT:B"),
    )
    assert first == second


def test_blank_work_key_or_duplicate_current_inputs_fail_closed():
    with pytest.raises(ValueError, match="WORK_IDENTITY_WORK_KEY_REQUIRED"):
        projection(seed=with_seed(work_key=" "))

    with pytest.raises(ValueError, match="WORK_IDENTITY_AUTHORITY_REFS_DUPLICATE"):
        current_context_fingerprint(
            checkpoint_id="R1",
            task="Task",
            operational_version=1,
            authority_refs=("CURRENT", "CURRENT"),
        )

    with pytest.raises(ValueError, match="WORK_IDENTITY_INTENT_REFS_DUPLICATE"):
        current_context_fingerprint(
            checkpoint_id="R1",
            task="Task",
            operational_version=1,
            authority_refs=("CURRENT",),
            intent_refs=("INTENT", "INTENT"),
        )


class Reader:
    def __init__(self, seed, *, intent_refs=INTENT_REFS):
        self.seed = seed
        self.intent_refs = intent_refs

    def resolve_current(self, routes, state, request, checkpoint_cue):
        return HaoCanonicalCurrent(
            checkpoint_id="R200",
            task=state.task,
            operational_version=state.version,
            authority_refs=AUTHORITY_REFS,
            verified=True,
            intent_refs=self.intent_refs,
            work_identity_seed=self.seed,
        )

    def lookup_existing_work(self, routes, state, request):
        return HaoExistingWorkResult((), True, "ADMIT")

    def lookup_prior_attempts(self, routes, state, request):
        return HaoLookupResult((), True)

    def lookup_regressions(self, routes, state, request):
        return HaoLookupResult((), True)


ROUTES = HaoAuthorityRoutes("current", "requirements", "handoff", "regressions", "prior")


def test_verified_composite_current_can_project_identity_without_task_inference():
    source = HaoDriveCanonicalAuthoritySource(Reader(SEED), ROUTES)
    state = SimpleNamespace(task="Natural language TASK text", version=7)
    snapshot = source.read_context(state, SimpleNamespace(), "R200")

    assert snapshot is not None
    assert snapshot.work_identity is not None
    assert snapshot.work_identity.work_key == "PROJECT_HAO:ACTIVE_WORK_COORDINATION"
    assert snapshot.work_identity.objective == "ACTIVE_WORK_CANONICAL_IDENTITY"
    assert snapshot.work_identity.objective != state.task


def test_drive_only_seed_without_direct_intent_binding_fails_closed():
    source = HaoDriveCanonicalAuthoritySource(Reader(SEED, intent_refs=()), ROUTES)
    state = SimpleNamespace(task="ACTIVE_WORK_CANONICAL_IDENTITY", version=7)

    assert source.read_context(state, SimpleNamespace(), "R200") is None


def test_missing_identity_seed_does_not_infer_identity_from_task():
    source = HaoDriveCanonicalAuthoritySource(Reader(None), ROUTES)
    state = SimpleNamespace(task="ACTIVE_WORK_CANONICAL_IDENTITY", version=7)
    snapshot = source.read_context(state, SimpleNamespace(), "R200")

    assert snapshot is not None
    assert snapshot.work_identity is None


def test_invalid_composite_seed_fails_closed():
    invalid = with_seed(objective="")
    source = HaoDriveCanonicalAuthoritySource(Reader(invalid), ROUTES)
    state = SimpleNamespace(task="Task", version=7)

    assert source.read_context(state, SimpleNamespace(), "R200") is None
