from dataclasses import replace
import sqlite3

from src.authoritative_completion import CompletionAttestor, SQLiteAuthoritativeCompletionStore
from src.execution_control import (
    ActionArchetype,
    ActionExternality,
    ActionProposal,
    EvidenceKind,
    EvidenceOrigin,
    EvidenceReceipt,
    ExecutionRecord,
    Mode,
    RunPhase,
)
from src.runtime_migrations import (
    CURRENT_RUNTIME_SCHEMA_VERSION,
    MIGRATIONS,
    compatibility_epoch_for_version,
)


SECRET = b"decision-provenance-phase-b-test-secret-32bytes"


def _closed_provenance_record() -> ExecutionRecord:
    action = ActionProposal(
        action_id="RUN-PROVENANCE:A0001:drive.persist",
        archetype=ActionArchetype.MUTATE,
        externality=ActionExternality.PRIVATE_REVERSIBLE,
        capability="formal_persistence",
        provider="google_drive",
        action_name="update",
        authority_snapshot_fingerprint="sha256:authority-snapshot-v1",
        idempotency_key="RUN-PROVENANCE:A0001:drive.persist",
    )
    evidence = (
        EvidenceReceipt(
            "VERIFY-1",
            EvidenceKind.VERIFICATION_PASS,
            True,
            "hao-verifier",
            claim_scope=action.action_id,
            origin=EvidenceOrigin.VERIFIER,
        ),
        EvidenceReceipt(
            "GATE-1",
            EvidenceKind.ACCEPTANCE_GATE_PASS,
            True,
            "hao-verifier",
            claim_scope=action.action_id,
            origin=EvidenceOrigin.VERIFIER,
        ),
    )
    return ExecutionRecord(
        run_id="RUN-PROVENANCE",
        task="Decision provenance Phase B",
        mode=Mode.EXP,
        goal_valid=True,
        acceptance_criteria=("completion remains bound to one runtime decision",),
        policy_fingerprint="sha256:trusted-policy-v1",
        decision_id="DECISION:runtime-owned-v1",
        phase=RunPhase.CLOSED,
        action=action,
        evidence=evidence,
    )


def test_completion_signature_binds_runtime_owned_decision_identity():
    record = _closed_provenance_record()
    attestor = CompletionAttestor(SECRET, key_id="completion-v4")
    receipt = attestor.issue(
        record,
        operational_version=11,
        issued_at="2026-09-06T18:00:00+08:00",
    )

    assert receipt.policy_fingerprint == record.policy_fingerprint
    assert receipt.decision_id == record.decision_id
    assert attestor.verify(receipt, record, operational_version=11) is True
    assert attestor.verify(
        replace(receipt, decision_id="DECISION:forged"),
        record,
        operational_version=11,
    ) is False
    assert attestor.verify(
        receipt,
        replace(record, policy_fingerprint="sha256:forged-policy"),
        operational_version=11,
    ) is False


def test_incomplete_runtime_decision_provenance_fails_closed():
    record = _closed_provenance_record()
    attestor = CompletionAttestor(SECRET, key_id="completion-v4")

    for incomplete in (
        replace(record, policy_fingerprint=""),
        replace(record, decision_id=""),
    ):
        try:
            attestor.issue(
                incomplete,
                operational_version=11,
                issued_at="2026-09-06T18:00:00+08:00",
            )
        except ValueError as exc:
            assert str(exc) == "DECISION_PROVENANCE_INCOMPLETE"
        else:
            raise AssertionError("incomplete decision provenance must fail closed")


def test_reference_authoritative_store_persists_decision_identity(tmp_path):
    record = _closed_provenance_record()
    attestor = CompletionAttestor(SECRET, key_id="completion-v4")
    receipt = attestor.issue(
        record,
        operational_version=11,
        issued_at="2026-09-06T18:00:00+08:00",
    )
    path = tmp_path / "decision-provenance.sqlite"
    store = SQLiteAuthoritativeCompletionStore(str(path))

    result = store.commit(receipt, record, operational_version=11, attestor=attestor)
    assert result.committed is True

    with sqlite3.connect(path) as conn:
        row = conn.execute(
            "SELECT policy_fingerprint, decision_id FROM authoritative_completions WHERE run_id = ?",
            (record.run_id,),
        ).fetchone()
    assert row == (record.policy_fingerprint, record.decision_id)


def test_schema_v4_is_additive_expand_in_compatibility_epoch_one():
    assert CURRENT_RUNTIME_SCHEMA_VERSION == 4
    assert compatibility_epoch_for_version(3) == 1
    assert compatibility_epoch_for_version(4) == 1
    statements = "\n".join(MIGRATIONS[4])
    assert "policy_fingerprint" in statements
    assert "decision_id" in statements
    assert "DROP " not in statements.upper()
