from src.action_catalog import ActionBinding, ActionCatalog, ModelActionIntent, resolve_model_intent
from src.execution_control import (
    ActionArchetype,
    ActionExternality,
    AuthorityStamp,
    ExecutionRecord,
    Mode,
)


def catalog():
    return ActionCatalog(
        [
            ActionBinding(
                binding_id="read.one",
                capability="read",
                provider="fake",
                action_name="read",
                archetype=ActionArchetype.READ,
                externality=ActionExternality.READ_ONLY,
            )
        ]
    )


def test_no_required_authority_means_no_snapshot_and_no_preflight_contract():
    record = ExecutionRecord(
        run_id="RUN-NO-AUTH",
        task="No authority preflight",
        mode=Mode.EXP,
        goal_valid=True,
        acceptance_criteria=("read",),
    )
    resolution = resolve_model_intent(
        record,
        ModelActionIntent("I-1", "read", "read.one"),
        catalog(),
        sequence=1,
    )
    assert resolution.proposal is not None
    assert resolution.proposal.required_authority_refs == ()
    assert resolution.proposal.authority_snapshot_fingerprint == ""


def test_required_authority_produces_bound_snapshot_fingerprint():
    record = ExecutionRecord(
        run_id="RUN-AUTH",
        task="Authority preflight",
        mode=Mode.EXP,
        goal_valid=True,
        acceptance_criteria=("read",),
        authority_refs=("AUTH-1",),
        authority_stamps=(AuthorityStamp("AUTH-1", "rev-7"),),
        required_action_authority_refs=("AUTH-1",),
    )
    resolution = resolve_model_intent(
        record,
        ModelActionIntent("I-1", "read", "read.one"),
        catalog(),
        sequence=1,
    )
    assert resolution.proposal is not None
    assert resolution.proposal.required_authority_refs == ("AUTH-1",)
    assert len(resolution.proposal.authority_snapshot_fingerprint) == 64
