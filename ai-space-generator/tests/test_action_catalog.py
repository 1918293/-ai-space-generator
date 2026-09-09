from dataclasses import fields

from src.action_catalog import (
    ActionBinding,
    ActionCatalog,
    ModelActionIntent,
    resolve_model_intent,
)
from src.execution_control import (
    ActionArchetype,
    ActionExternality,
    AuthorityStamp,
    ExecutionRecord,
    FailureStage,
    Mode,
    RunPhase,
    admit_action,
    authority_snapshot_fingerprint,
)


def record(**overrides):
    values = dict(
        run_id="RUN-CATALOG",
        task="Trusted action catalog",
        mode=Mode.EXP,
        goal_valid=True,
        acceptance_criteria=("model cannot self-classify tool safety",),
        authority_refs=("AUTH-CURRENT",),
        authority_stamps=(AuthorityStamp("AUTH-CURRENT", "rev-17"),),
        required_action_authority_refs=("AUTH-CURRENT",),
    )
    values.update(overrides)
    return ExecutionRecord(**values)


def catalog():
    return ActionCatalog(
        [
            ActionBinding(
                binding_id="image.local_mask_edit",
                capability="image_edit",
                provider="image-editor",
                action_name="masked_edit",
                archetype=ActionArchetype.MUTATE,
                externality=ActionExternality.PRIVATE_REVERSIBLE,
                assurance_tags=("LOCAL_EDIT", "PRESERVE_OUTSIDE_MASK"),
                rollback_available=True,
            ),
            ActionBinding(
                binding_id="image.full_generate",
                capability="image_generation",
                provider="image-model",
                action_name="generate",
                archetype=ActionArchetype.MUTATE,
                externality=ActionExternality.PRIVATE_REVERSIBLE,
                assurance_tags=("FULL_GENERATION",),
            ),
            ActionBinding(
                binding_id="gmail.send",
                capability="external_message",
                provider="gmail",
                action_name="send",
                archetype=ActionArchetype.PUBLISH,
                externality=ActionExternality.EXTERNAL_IRREVERSIBLE,
                assurance_tags=("EXTERNAL_MESSAGE",),
                authorization_scope_prefix="SEND_EXTERNAL",
            ),
        ]
    )


def test_model_intent_schema_has_no_safety_or_authorization_assertion_fields():
    names = {field.name for field in fields(ModelActionIntent)}
    assert "externality" not in names
    assert "archetype" not in names
    assert "assurance_tags" not in names
    assert "authorization_scope" not in names
    assert "hao_authorized_scopes" not in names


def test_unregistered_binding_is_rejected_before_action_proposal_exists():
    resolution = resolve_model_intent(
        record(),
        ModelActionIntent("I-1", "image_edit", "made.up.binding"),
        catalog(),
        sequence=1,
    )
    assert resolution.proposal is None
    assert resolution.decision.allowed is False
    assert resolution.decision.failed_at == FailureStage.BINDING


def test_model_capability_claim_must_match_trusted_binding_metadata():
    resolution = resolve_model_intent(
        record(),
        ModelActionIntent("I-1", "image_edit", "image.full_generate"),
        catalog(),
        sequence=1,
    )
    assert resolution.proposal is None
    assert resolution.decision.code == "CAPABILITY_BINDING_MISMATCH"


def test_safety_metadata_and_authority_snapshot_come_from_runtime():
    current = record()
    resolution = resolve_model_intent(
        current,
        ModelActionIntent(
            "I-1",
            "image_edit",
            "image.local_mask_edit",
            expected_state_delta="change only inside mask",
        ),
        catalog(),
        sequence=7,
    )
    proposal = resolution.proposal
    assert proposal is not None
    assert proposal.action_id == "RUN-CATALOG:A0007:image.local_mask_edit"
    assert proposal.idempotency_key == proposal.action_id
    assert proposal.externality == ActionExternality.PRIVATE_REVERSIBLE
    assert proposal.archetype == ActionArchetype.MUTATE
    assert set(proposal.assurance_tags) == {"LOCAL_EDIT", "PRESERVE_OUTSIDE_MASK"}
    assert proposal.required_authority_refs == ("AUTH-CURRENT",)
    assert proposal.authority_snapshot_fingerprint == authority_snapshot_fingerprint(
        current.authority_stamps,
        current.required_action_authority_refs,
    )


def test_exact_edit_task_accepts_trusted_local_edit_binding():
    current = record(
        required_action_tags=("LOCAL_EDIT", "PRESERVE_OUTSIDE_MASK"),
        forbidden_action_tags=("FULL_GENERATION",),
    )
    resolution = resolve_model_intent(
        current,
        ModelActionIntent(
            "I-1",
            "image_edit",
            "image.local_mask_edit",
            expected_state_delta="bounded mask delta",
        ),
        catalog(),
        sequence=1,
    )
    admitted, decision = admit_action(current, resolution.proposal)
    assert decision.allowed is True
    assert admitted.phase == RunPhase.ADMITTED


def test_exact_edit_task_cannot_be_reclassified_as_full_generation_by_model():
    current = record(
        required_action_tags=("LOCAL_EDIT", "PRESERVE_OUTSIDE_MASK"),
        forbidden_action_tags=("FULL_GENERATION",),
    )
    resolution = resolve_model_intent(
        current,
        ModelActionIntent(
            "I-1",
            "image_generation",
            "image.full_generate",
            expected_state_delta="new image",
        ),
        catalog(),
        sequence=1,
    )
    assert resolution.proposal is not None
    blocked, decision = admit_action(current, resolution.proposal)
    assert decision.allowed is False
    assert decision.failed_at == FailureStage.POLICY
    assert blocked.phase == RunPhase.BLOCKED


def test_external_authorization_scope_is_derived_from_trusted_binding_and_target():
    current = record()
    resolution = resolve_model_intent(
        current,
        ModelActionIntent(
            "I-1",
            "external_message",
            "gmail.send",
            expected_state_delta="send one message",
            authorization_target="recipient-123",
        ),
        catalog(),
        sequence=1,
    )
    proposal = resolution.proposal
    assert proposal is not None
    assert proposal.externality == ActionExternality.EXTERNAL_IRREVERSIBLE
    assert proposal.authorization_scope == "SEND_EXTERNAL:recipient-123"
    waiting, decision = admit_action(current, proposal)
    assert decision.allowed is False
    assert decision.requires_hao_authorization is True
    assert waiting.phase == RunPhase.AWAITING_HAO


def test_missing_authority_version_blocks_intent_resolution_before_proposal():
    current = record(
        authority_stamps=(),
        required_action_authority_refs=("AUTH-CURRENT",),
    )
    resolution = resolve_model_intent(
        current,
        ModelActionIntent(
            "I-1",
            "image_edit",
            "image.local_mask_edit",
            expected_state_delta="bounded mask delta",
        ),
        catalog(),
        sequence=1,
    )
    assert resolution.proposal is None
    assert resolution.decision.allowed is False
    assert resolution.decision.code == "AUTHORITY_VERSION_UNRESOLVED"
    assert resolution.decision.failed_at == FailureStage.AUTHORITY


def test_tampered_action_snapshot_is_blocked_at_admission():
    current = record()
    resolution = resolve_model_intent(
        current,
        ModelActionIntent(
            "I-1",
            "image_edit",
            "image.local_mask_edit",
            expected_state_delta="bounded mask delta",
        ),
        catalog(),
        sequence=1,
    )
    proposal = resolution.proposal
    tampered = proposal.__class__(
        **{**proposal.__dict__, "authority_snapshot_fingerprint": "stale-or-forged"}
    )
    blocked, decision = admit_action(current, tampered)
    assert decision.allowed is False
    assert decision.code == "AUTHORITY_SNAPSHOT_MISMATCH"
    assert blocked.phase == RunPhase.BLOCKED
