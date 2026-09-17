from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.control_gateway import PreModelContextRequest
from src.hao_authority_resolver import (
    HaoAuthorityRoutes,
    HaoCanonicalCurrent,
    HaoDriveCanonicalAuthoritySource,
    HaoExistingWorkResult,
    HaoLookupResult,
)
from src.operational_state import CommandActor
from src.resolved_work_identity import (
    CanonicalWorkIdentitySeed,
    WorkIdentityRelation,
    compare_work_identity,
    current_context_fingerprint,
    direct_hao_intent_ref,
    requirement_verification_work_key,
    resolve_work_identity_projection,
)


INTENT_TEXT = "Auto > 根據上述執行任務"
INTENT_REF = direct_hao_intent_ref(
    user_text=INTENT_TEXT,
    actor=CommandActor.USER,
    event_id="EVENT-200",
)
INTENT_REFS = (INTENT_REF,)
AUTHORITY_REFS = (
    "CURRENT:WORK_KEY",
    "CURRENT:PROJECT",
    "CURRENT:TARGET",
    "CURRENT:OBJECTIVE",
    "CURRENT:DELIVERABLE",
    "REQUIREMENTS:R-036",
)
FIELD_SOURCES = (
    ("work_key", "CURRENT_AUTHORITY", "CURRENT:WORK_KEY"),
    ("project_scope", "CURRENT_AUTHORITY", "CURRENT:PROJECT"),
    ("logical_target", "CURRENT_AUTHORITY", "CURRENT:TARGET"),
    ("objective", "HAO_INTENT", INTENT_REF),
    ("deliverable_identity", "HAO_INTENT", INTENT_REF),
    ("acceptance_identity", "CURRENT_AUTHORITY", "REQUIREMENTS:R-036"),
)
AUTHORITY_ONLY_FIELD_SOURCES = (
    ("work_key", "CURRENT_AUTHORITY", "CURRENT:WORK_KEY"),
    ("project_scope", "CURRENT_AUTHORITY", "CURRENT:PROJECT"),
    ("logical_target", "CURRENT_AUTHORITY", "CURRENT:TARGET"),
    ("objective", "CURRENT_AUTHORITY", "CURRENT:OBJECTIVE"),
    ("deliverable_identity", "CURRENT_AUTHORITY", "CURRENT:DELIVERABLE"),
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


def intent_request(
    *,
    event_id="EVENT-200",
    text=INTENT_TEXT,
    actor=CommandActor.USER,
):
    return PreModelContextRequest(user_text=text, actor=actor, event_id=event_id)


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


def requirement_verification_projection(
    *,
    row_id="RV-051",
    objective="VERIFY_ACTION_ADMISSION",
    event_id="EVENT-RV-051",
):
    row_ref = f"REQUIREMENTS:{row_id}"
    authority_refs = (
        row_ref,
        "CURRENT:PROJECT",
        "CURRENT:TARGET",
        "CURRENT:DELIVERABLE",
        "CURRENT:ACCEPTANCE",
    )
    work_key = requirement_verification_work_key(
        row_id,
        source_ref=row_ref,
        authority_refs=authority_refs,
    )
    seed = CanonicalWorkIdentitySeed(
        work_key=work_key,
        project_scope="PROJECT_HAO",
        logical_target="requirements/07_Requirement_Verification",
        objective=objective,
        deliverable_identity="BOUNDED_VERIFICATION_EVIDENCE",
        acceptance_identity="VERIFICATION_CONTRACT_SATISFIED",
        field_sources=(
            ("work_key", "CURRENT_AUTHORITY", row_ref),
            ("project_scope", "CURRENT_AUTHORITY", "CURRENT:PROJECT"),
            ("logical_target", "CURRENT_AUTHORITY", "CURRENT:TARGET"),
            ("objective", "CURRENT_AUTHORITY", row_ref),
            ("deliverable_identity", "CURRENT_AUTHORITY", "CURRENT:DELIVERABLE"),
            ("acceptance_identity", "CURRENT_AUTHORITY", "CURRENT:ACCEPTANCE"),
        ),
    )
    intent_ref = direct_hao_intent_ref(
        user_text=INTENT_TEXT,
        actor=CommandActor.USER,
        event_id=event_id,
    )
    return resolve_work_identity_projection(
        seed,
        checkpoint_id="R200",
        task="Continue Requirement Verification",
        operational_version=7,
        authority_refs=authority_refs,
        intent_refs=(intent_ref,),
    )


def test_direct_hao_intent_ref_is_bound_to_user_event_and_raw_text():
    assert INTENT_REF.startswith("HAO_INTENT:")
    assert INTENT_REF == direct_hao_intent_ref(
        user_text=INTENT_TEXT,
        actor=CommandActor.USER,
        event_id="EVENT-200",
    )
    assert INTENT_REF != direct_hao_intent_ref(
        user_text=INTENT_TEXT + " now",
        actor=CommandActor.USER,
        event_id="EVENT-200",
    )
    assert INTENT_REF != direct_hao_intent_ref(
        user_text=INTENT_TEXT,
        actor=CommandActor.USER,
        event_id="EVENT-201",
    )
    assert direct_hao_intent_ref(
        user_text=INTENT_TEXT,
        actor=CommandActor.MODEL,
        event_id="EVENT-200",
    ) == ""
    assert direct_hao_intent_ref(
        user_text=INTENT_TEXT,
        actor=CommandActor.USER,
        event_id="",
    ) == ""


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


def test_requirement_verification_row_id_is_stable_across_attempt_events():
    first = requirement_verification_projection(event_id="EVENT-RV-051-A")
    second = requirement_verification_projection(event_id="EVENT-RV-051-B")

    assert first.work_key == "RV-051"
    assert second.work_key == "RV-051"
    assert first.current_fingerprint != second.current_fingerprint
    assert compare_work_identity(first, second) == WorkIdentityRelation.SAME_WORK_SAME_INTENT


def test_requirement_verification_same_row_changed_intent_is_not_false_coalesced():
    baseline = requirement_verification_projection()
    changed = requirement_verification_projection(objective="VERIFY_DYNAMIC_TOOL_REVALIDATION")

    assert baseline.work_key == changed.work_key == "RV-051"
    assert baseline.intent_fingerprint != changed.intent_fingerprint
    assert compare_work_identity(baseline, changed) == WorkIdentityRelation.SAME_WORK_CHANGED_INTENT


def test_requirement_verification_different_rows_are_different_work():
    first = requirement_verification_projection(row_id="RV-051")
    second = requirement_verification_projection(row_id="RV-052")

    assert first.intent_fingerprint == second.intent_fingerprint
    assert compare_work_identity(first, second) == WorkIdentityRelation.DIFFERENT_WORK


def test_requirement_verification_accepts_existing_zero_padded_row_ids():
    assert requirement_verification_work_key(
        "rv-009",
        source_ref="REQUIREMENTS:RV-009",
        authority_refs=("REQUIREMENTS:RV-009",),
    ) == "RV-009"


@pytest.mark.parametrize(
    "invalid_id",
    ["R-051", "RUN-20260918-01", "PROJECT_HAO", "51", "RV-000"],
)
def test_requirement_verification_rejects_non_rv_identity_substitutes(invalid_id):
    ref = f"REQUIREMENTS:{invalid_id}"
    with pytest.raises(ValueError, match="WORK_IDENTITY_REQUIREMENT_VERIFICATION_ID_INVALID"):
        requirement_verification_work_key(
            invalid_id,
            source_ref=ref,
            authority_refs=(ref,),
        )


def test_requirement_verification_source_must_be_in_verified_current_authority():
    with pytest.raises(
        ValueError,
        match="WORK_IDENTITY_REQUIREMENT_VERIFICATION_SOURCE_NOT_CURRENT_AUTHORITY",
    ):
        requirement_verification_work_key(
            "RV-051",
            source_ref="REQUIREMENTS:RV-051",
            authority_refs=("REQUIREMENTS:RV-052",),
        )


def test_requirement_verification_source_must_bind_the_same_row_identity():
    with pytest.raises(
        ValueError,
        match="WORK_IDENTITY_REQUIREMENT_VERIFICATION_SOURCE_MISMATCH",
    ):
        requirement_verification_work_key(
            "RV-051",
            source_ref="REQUIREMENTS:RV-052",
            authority_refs=("REQUIREMENTS:RV-052",),
        )


def test_unknown_identity_never_auto_coalesces():
    assert compare_work_identity(None, projection()) == WorkIdentityRelation.UNKNOWN
    assert compare_work_identity(projection(), None) == WorkIdentityRelation.UNKNOWN


def test_typed_provenance_accepts_direct_hao_intent_and_current_authority_together():
    result = projection()

    assert ("objective", "HAO_INTENT", INTENT_REF) in result.field_sources
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


def test_continuation_can_inherit_semantic_fields_from_current_authority():
    result = projection(seed=with_seed(field_sources=AUTHORITY_ONLY_FIELD_SOURCES))

    assert result.objective == SEED.objective
    assert result.deliverable_identity == SEED.deliverable_identity
    assert result.acceptance_identity == SEED.acceptance_identity
    assert all(source_class == "CURRENT_AUTHORITY" for _, source_class, _ in result.field_sources)


def test_direct_hao_interaction_provenance_is_required_even_when_semantics_are_current():
    with pytest.raises(ValueError, match="WORK_IDENTITY_DIRECT_HAO_INTENT_BINDING_REQUIRED"):
        projection(
            seed=with_seed(field_sources=AUTHORITY_ONLY_FIELD_SOURCES),
            intent_refs=(),
        )


def test_provenance_location_change_does_not_change_intent_semantics():
    baseline = projection()
    moved_intent_ref = direct_hao_intent_ref(
        user_text=INTENT_TEXT,
        actor=CommandActor.USER,
        event_id="EVENT-201",
    )
    moved_sources = tuple(
        (field, source_class, moved_intent_ref)
        if source_class == "HAO_INTENT"
        else (field, source_class, ref)
        for field, source_class, ref in FIELD_SOURCES
    )
    moved = projection(
        version=8,
        seed=with_seed(field_sources=moved_sources),
        intent_refs=(moved_intent_ref,),
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


def test_verified_composite_current_can_project_identity_from_interaction_boundary():
    source = HaoDriveCanonicalAuthoritySource(Reader(SEED), ROUTES)
    state = SimpleNamespace(task="Natural language TASK text", version=7)
    snapshot = source.read_context(state, intent_request(), "R200")

    assert snapshot is not None
    assert snapshot.work_identity is not None
    assert snapshot.work_identity.work_key == "PROJECT_HAO:ACTIVE_WORK_COORDINATION"
    assert snapshot.work_identity.objective == "ACTIVE_WORK_CANONICAL_IDENTITY"
    assert snapshot.work_identity.objective != state.task


def test_continuation_instruction_can_keep_semantics_source_backed_by_current():
    authority_only_seed = with_seed(field_sources=AUTHORITY_ONLY_FIELD_SOURCES)
    source = HaoDriveCanonicalAuthoritySource(Reader(authority_only_seed), ROUTES)
    state = SimpleNamespace(task="Natural language TASK text", version=7)
    snapshot = source.read_context(state, intent_request(), "R200")

    assert snapshot is not None
    assert snapshot.work_identity is not None
    assert all(
        source_class == "CURRENT_AUTHORITY"
        for _, source_class, _ in snapshot.work_identity.field_sources
    )


def test_identity_seed_without_current_direct_interaction_provenance_fails_closed():
    source = HaoDriveCanonicalAuthoritySource(Reader(SEED), ROUTES)
    state = SimpleNamespace(task="ACTIVE_WORK_CANONICAL_IDENTITY", version=7)

    assert source.read_context(state, intent_request(event_id=""), "R200") is None
    assert source.read_context(
        state,
        intent_request(actor=CommandActor.MODEL),
        "R200",
    ) is None


def test_missing_identity_seed_does_not_infer_identity_from_task_or_require_event_id():
    source = HaoDriveCanonicalAuthoritySource(Reader(None), ROUTES)
    state = SimpleNamespace(task="ACTIVE_WORK_CANONICAL_IDENTITY", version=7)
    snapshot = source.read_context(state, intent_request(event_id=""), "R200")

    assert snapshot is not None
    assert snapshot.work_identity is None


def test_invalid_composite_seed_fails_closed():
    invalid = with_seed(objective="")
    source = HaoDriveCanonicalAuthoritySource(Reader(invalid), ROUTES)
    state = SimpleNamespace(task="Task", version=7)

    assert source.read_context(state, intent_request(), "R200") is None
