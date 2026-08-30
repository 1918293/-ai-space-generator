from dataclasses import replace

import pytest

from src.authoritative_completion import (
    CompletionAttestor,
    ExecutionAttestation,
    SQLiteAuthoritativeCompletionStore,
)
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


SECRET = b"hao-control-plane-test-secret-32bytes-minimum"


def closed_record():
    action = ActionProposal(
        action_id="RUN-CUTOVER:A0001:drive.persist",
        archetype=ActionArchetype.MUTATE,
        externality=ActionExternality.PRIVATE_REVERSIBLE,
        capability="formal_persistence",
        provider="google_drive",
        action_name="update",
        expected_state_delta="persist one verified delta",
        idempotency_key="RUN-CUTOVER:A0001:drive.persist",
    )
    evidence = (
        EvidenceReceipt(
            "TOOL-1",
            EvidenceKind.TOOL_RECEIPT,
            True,
            "google-drive",
            claim_scope=action.action_id,
            origin=EvidenceOrigin.PROVIDER,
        ),
        EvidenceReceipt(
            "READBACK-1",
            EvidenceKind.STATE_READBACK,
            True,
            "google-drive-readback",
            claim_scope=action.action_id,
            origin=EvidenceOrigin.PROVIDER,
        ),
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
        run_id="RUN-CUTOVER",
        task="Production cutover",
        mode=Mode.EXP,
        goal_valid=True,
        acceptance_criteria=("verified controlled completion",),
        phase=RunPhase.CLOSED,
        action=action,
        evidence=evidence,
    )


def test_only_closed_evidence_complete_record_can_receive_runtime_attestation():
    attestor = CompletionAttestor(SECRET)
    record = closed_record()
    attestation = attestor.issue(
        record,
        operational_version=7,
        issued_at="2026-08-30T14:10:00+08:00",
    )
    assert attestor.verify(attestation, record, operational_version=7) is True

    with pytest.raises(ValueError, match="ATTESTATION_REQUIRES_CLOSED_STATE"):
        attestor.issue(
            replace(record, phase=RunPhase.VERIFIED),
            operational_version=7,
            issued_at="2026-08-30T14:10:00+08:00",
        )


def test_native_or_direct_tool_result_cannot_forge_authoritative_completion():
    attestor = CompletionAttestor(SECRET)
    record = closed_record()
    forged = ExecutionAttestation(
        run_id=record.run_id,
        action_id=record.action.action_id,
        task=record.task,
        mode=record.mode.value,
        operational_version=7,
        authority_snapshot_fingerprint="",
        evidence_digest="pretend-tool-success",
        issued_at="2026-08-30T14:10:00+08:00",
        signature="native-chatgpt-or-tool-result",
    )
    assert attestor.verify(forged, record, operational_version=7) is False


def test_attestation_is_bound_to_operational_version_task_action_and_evidence():
    attestor = CompletionAttestor(SECRET)
    record = closed_record()
    attestation = attestor.issue(
        record,
        operational_version=7,
        issued_at="2026-08-30T14:10:00+08:00",
    )
    assert attestor.verify(attestation, record, operational_version=8) is False
    assert attestor.verify(
        attestation,
        replace(record, task="Different task"),
        operational_version=7,
    ) is False

    changed_evidence = replace(
        record,
        evidence=record.evidence
        + (
            EvidenceReceipt(
                "EXTRA",
                EvidenceKind.VERIFICATION_PASS,
                True,
                "other-verifier",
                claim_scope=record.action.action_id,
                origin=EvidenceOrigin.VERIFIER,
            ),
        ),
    )
    assert attestor.verify(attestation, changed_evidence, operational_version=7) is False


def test_authoritative_store_rejects_unsigned_bypass_and_accepts_runtime_receipt(tmp_path):
    record = closed_record()
    attestor = CompletionAttestor(SECRET)
    store = SQLiteAuthoritativeCompletionStore(str(tmp_path / "completion.sqlite"))

    forged = ExecutionAttestation(
        run_id=record.run_id,
        action_id=record.action.action_id,
        task=record.task,
        mode=record.mode.value,
        operational_version=7,
        authority_snapshot_fingerprint="",
        evidence_digest="fake",
        issued_at="2026-08-30T14:10:00+08:00",
        signature="fake",
    )
    rejected = store.commit(
        forged,
        record,
        operational_version=7,
        attestor=attestor,
    )
    assert rejected.committed is False
    assert rejected.code == "INVALID_CONTROL_PLANE_ATTESTATION"

    valid = attestor.issue(
        record,
        operational_version=7,
        issued_at="2026-08-30T14:10:00+08:00",
    )
    first = store.commit(valid, record, operational_version=7, attestor=attestor)
    second = store.commit(valid, record, operational_version=7, attestor=attestor)
    assert first.code == "AUTHORITATIVE_COMPLETION_COMMITTED"
    assert second.code == "ATTESTATION_ALREADY_COMMITTED"


def test_same_run_cannot_be_rewritten_with_different_valid_completion_attestation(tmp_path):
    record = closed_record()
    attestor = CompletionAttestor(SECRET)
    store = SQLiteAuthoritativeCompletionStore(str(tmp_path / "completion.sqlite"))
    first = attestor.issue(
        record,
        operational_version=7,
        issued_at="2026-08-30T14:10:00+08:00",
    )
    later = attestor.issue(
        record,
        operational_version=7,
        issued_at="2026-08-30T14:11:00+08:00",
    )
    assert store.commit(first, record, operational_version=7, attestor=attestor).committed is True
    conflict = store.commit(later, record, operational_version=7, attestor=attestor)
    assert conflict.committed is False
    assert conflict.code == "AUTHORITATIVE_COMPLETION_CONFLICT"
