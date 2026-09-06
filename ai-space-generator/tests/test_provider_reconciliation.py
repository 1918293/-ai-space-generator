import asyncio
import sqlite3
from pathlib import Path

import pytest

from src.controlled_runner import ToolOutcome
from src.execution_control import (
    ActionArchetype,
    ActionExternality,
    ActionProposal,
    ExecutionRecord,
    Mode,
)
from src.google_drive_control import (
    ControlledGoogleDriveProvider,
    GoogleDriveOutcomeVerifier,
    DriveMutationCommand,
    DriveMutationReceipt,
    DriveStateReadback,
)
from src.idempotent_broker import (
    IdempotencyRecord,
    IdempotencyState,
    IdempotentAsyncBroker,
    SQLiteIdempotencyStore,
)
from src.provider_reconciliation import (
    ProviderEffectState,
    ReadbackRetryPolicy,
    TrustedDriveReconciliationInspector,
    classify_idempotency_effect,
)
from src.reconciliation import (
    ReconciliationDisposition,
    ReconciliationEvidenceKind,
    ReconciliationPhase,
    apply_reconciliation,
)
from src.reconciliation_persistence import (
    PostgresReconciliationStore,
    ReconciliationAwareBroker,
)


def proposal(**overrides):
    values = dict(
        action_id="RUN-D-RECON:A0001:formal.intake.append",
        archetype=ActionArchetype.MUTATE,
        externality=ActionExternality.PRIVATE_REVERSIBLE,
        capability="formal_persistence",
        provider="google-drive",
        action_name="update_cells",
        expected_state_delta="write exact configured row",
        authority_snapshot_fingerprint="AUTH-FP-1",
        authorization_scope="HAO_DRIVE_WRITE:Hao System Intake",
        idempotency_key="RUN-D-RECON:A0001:formal.intake.append",
        rollback_available=True,
    )
    values.update(overrides)
    return ActionProposal(**values)


def command(**overrides):
    values = dict(
        action_id="RUN-D-RECON:A0001:formal.intake.append",
        provider="google-drive",
        action_name="update_cells",
        target_ref="google-sheets:sheet-main:01_Intake!A10:B10",
        expected_state_delta="write exact configured row",
        expected_state_digest="sha256:expected",
        authorization_scope="HAO_DRIVE_WRITE:Hao System Intake",
        authorization_ref="AUTHORIZATION:REC-1",
        authority_snapshot_fingerprint="AUTH-FP-1",
        payload=(("spreadsheet_id", "sheet-main"), ("range", "01_Intake!A10:B10")),
    )
    values.update(overrides)
    return DriveMutationCommand(**values)


class Resolver:
    def __init__(self, resolved=None):
        self.resolved = resolved or command()
        self.calls = 0

    def resolve(self, current_proposal):
        self.calls += 1
        return self.resolved


class FailureInjectionClient:
    def __init__(self):
        self.mode = "success"
        self.state_digest = "sha256:expected"
        self.mutate_calls = 0
        self.readback_calls = 0
        self.readback_failures_remaining = 0

    async def preflight_authority(self, current_command):
        raise AssertionError("not used by these reconciliation tests")

    async def mutate(self, current_command):
        self.mutate_calls += 1
        if self.mode == "timeout_before_commit":
            raise TimeoutError("timeout before provider commit")
        if self.mode == "network_loss_after_effect":
            self.state_digest = current_command.expected_state_digest
            raise ConnectionError("response lost after provider commit")
        if self.mode == "partial_mutation":
            self.state_digest = "sha256:partial"
            raise ConnectionError("response lost after partial provider effect")
        return DriveMutationReceipt(True, f"RECEIPT-{self.mutate_calls}", "fake:sheets:update")

    async def readback(self, current_command):
        self.readback_calls += 1
        if self.readback_failures_remaining:
            self.readback_failures_remaining -= 1
            raise TimeoutError("transient readback timeout")
        if self.mode == "empty_readback":
            return DriveStateReadback("", "fake:sheets:get", False, "READBACK_UNAVAILABLE")
        matched = self.state_digest == current_command.expected_state_digest
        return DriveStateReadback(
            self.state_digest,
            "fake:sheets:get",
            matched,
            "" if matched else "GOOGLE_SHEETS_READBACK_MISMATCH",
        )


class SqlitePostgresCompatConnection:
    def __init__(self, path: Path):
        self._conn = sqlite3.connect(path, timeout=30, isolation_level=None)
        self._conn.row_factory = sqlite3.Row

    def execute(self, sql, params=()):
        normalized = sql.strip()
        if normalized == "BEGIN ISOLATION LEVEL SERIALIZABLE":
            return self._conn.execute("BEGIN IMMEDIATE")
        normalized = normalized.replace(" FOR UPDATE", "").replace("%s", "?")
        return self._conn.execute(normalized, params)

    def close(self):
        self._conn.close()


def reconciliation_store(tmp_path):
    db = tmp_path / "reconciliation.sqlite3"
    return PostgresReconciliationStore(
        "postgresql://runtime-v2/test",
        connect_factory=lambda: SqlitePostgresCompatConnection(db),
    )


def idempotency_store(tmp_path):
    return SQLiteIdempotencyStore(str(tmp_path / "idempotency.sqlite3"))


@pytest.mark.asyncio
async def test_network_loss_after_effect_direct_readback_reconciles_without_blind_replay(tmp_path):
    resolver = Resolver()
    client = FailureInjectionClient()
    client.mode = "network_loss_after_effect"
    idem = idempotency_store(tmp_path)
    cases = reconciliation_store(tmp_path)
    broker = ReconciliationAwareBroker(
        IdempotentAsyncBroker(ControlledGoogleDriveProvider(resolver, client), idem),
        cases,
    )

    first = await broker.execute(proposal())
    assert first.success is False
    assert first.error_code == "PROVIDER_EXCEPTION_EFFECT_UNKNOWN"
    assert client.mutate_calls == 1
    assert idem.get(proposal().idempotency_key).state == IdempotencyState.UNKNOWN_EFFECT

    client.mode = "success"
    second = await broker.execute(proposal())
    assert second.success is False
    assert second.error_code == "IDEMPOTENCY_EFFECT_UNKNOWN"
    assert client.mutate_calls == 1

    opened = cases.get_by_action(proposal().action_id)
    assert opened is not None
    inspector = TrustedDriveReconciliationInspector(
        resolver=resolver,
        client=client,
        store=cases,
        retry_policy=ReadbackRetryPolicy(max_attempts=2, backoff_seconds=(0.0,)),
    )
    inspection = await inspector.inspect(case_id=opened.case_id, proposal=proposal())
    assert inspection.effect_state == ProviderEffectState.KNOWN_APPLIED
    assert inspection.code == "TRUSTED_DIRECT_READBACK_VERIFIED_APPLIED"
    assert client.mutate_calls == 1
    kinds = {e.kind for e in inspection.case.evidence if e.passed}
    assert ReconciliationEvidenceKind.STATE_READBACK in kinds
    assert ReconciliationEvidenceKind.VERIFICATION_PASS in kinds
    assert inspection.case.phase == ReconciliationPhase.OPEN

    resolved = apply_reconciliation(
        inspection.case,
        ReconciliationDisposition.ADOPT_VERIFIED_STATE,
    )
    saved = cases.save(resolved)
    assert saved.phase == ReconciliationPhase.RESOLVED

    third = await broker.execute(proposal())
    assert third.success is False
    assert third.error_code == "IDEMPOTENCY_EFFECT_UNKNOWN"
    assert client.mutate_calls == 1


@pytest.mark.asyncio
async def test_partial_mutation_readback_is_unsynced_and_cannot_be_adopted(tmp_path):
    resolver = Resolver()
    client = FailureInjectionClient()
    client.mode = "partial_mutation"
    idem = idempotency_store(tmp_path)
    cases = reconciliation_store(tmp_path)
    broker = ReconciliationAwareBroker(
        IdempotentAsyncBroker(ControlledGoogleDriveProvider(resolver, client), idem),
        cases,
    )
    await broker.execute(proposal())
    opened = cases.get_by_action(proposal().action_id)
    assert opened is not None

    inspection = await TrustedDriveReconciliationInspector(
        resolver=resolver,
        client=client,
        store=cases,
    ).inspect(case_id=opened.case_id, proposal=proposal())
    assert inspection.effect_state == ProviderEffectState.UNSYNCED
    assert inspection.case.phase == ReconciliationPhase.OPEN
    verification = [
        e for e in inspection.case.evidence
        if e.kind == ReconciliationEvidenceKind.VERIFICATION_PASS
    ]
    assert len(verification) == 1
    assert verification[0].passed is False

    rejected = apply_reconciliation(
        inspection.case,
        ReconciliationDisposition.ADOPT_VERIFIED_STATE,
    )
    assert rejected.phase == ReconciliationPhase.OPEN
    assert rejected.resolution_code == "ADOPTION_REQUIRES_VERIFIED_READBACK"


@pytest.mark.asyncio
async def test_readback_retry_is_bounded_and_read_only(tmp_path):
    resolver = Resolver()
    client = FailureInjectionClient()
    cases = reconciliation_store(tmp_path)
    opened = cases.open_unknown_effect(
        proposal(), error_code="PROVIDER_EXCEPTION_EFFECT_UNKNOWN"
    )
    client.readback_failures_remaining = 2
    sleeps = []

    async def sleeper(delay):
        sleeps.append(delay)

    inspection = await TrustedDriveReconciliationInspector(
        resolver=resolver,
        client=client,
        store=cases,
        retry_policy=ReadbackRetryPolicy(max_attempts=3, backoff_seconds=(0.01, 0.02)),
        sleeper=sleeper,
    ).inspect(case_id=opened.case_id, proposal=proposal())
    assert inspection.effect_state == ProviderEffectState.KNOWN_APPLIED
    assert inspection.attempts == 3
    assert client.readback_calls == 3
    assert client.mutate_calls == 0
    assert sleeps == [0.01, 0.02]


@pytest.mark.asyncio
async def test_readback_without_observed_state_exhausts_loudly_without_evidence(tmp_path):
    resolver = Resolver()
    client = FailureInjectionClient()
    client.mode = "empty_readback"
    cases = reconciliation_store(tmp_path)
    opened = cases.open_unknown_effect(
        proposal(), error_code="PROVIDER_EXCEPTION_EFFECT_UNKNOWN"
    )
    inspection = await TrustedDriveReconciliationInspector(
        resolver=resolver,
        client=client,
        store=cases,
        retry_policy=ReadbackRetryPolicy(max_attempts=2, backoff_seconds=(0.0,)),
    ).inspect(case_id=opened.case_id, proposal=proposal())
    assert inspection.effect_state == ProviderEffectState.UNKNOWN_EFFECT
    assert inspection.code == "READBACK_UNAVAILABLE"
    assert inspection.attempts == 2
    assert inspection.case.evidence == ()
    assert client.mutate_calls == 0


def test_effect_state_projection_preserves_unknown_and_unsynced_semantics():
    def record(state):
        return IdempotencyRecord("k", "fp", state)

    assert classify_idempotency_effect(record(IdempotencyState.SUCCEEDED)) == ProviderEffectState.KNOWN_APPLIED
    assert classify_idempotency_effect(record(IdempotencyState.FAILED_NO_EFFECT)) == ProviderEffectState.KNOWN_NOT_APPLIED
    assert classify_idempotency_effect(record(IdempotencyState.UNKNOWN_EFFECT)) == ProviderEffectState.UNKNOWN_EFFECT
    assert classify_idempotency_effect(record(IdempotencyState.RESERVED)) == ProviderEffectState.UNKNOWN_EFFECT
    assert classify_idempotency_effect(record(IdempotencyState.SUCCEEDED), unsynced=True) == ProviderEffectState.UNSYNCED


def test_terminal_reconciliation_case_cannot_be_overwritten_by_stale_open_copy(tmp_path):
    cases = reconciliation_store(tmp_path)
    opened = cases.open_unknown_effect(
        proposal(), error_code="PROVIDER_EXCEPTION_EFFECT_UNKNOWN"
    )
    from src.reconciliation import ReconciliationEvidence, add_reconciliation_evidence

    enriched = add_reconciliation_evidence(
        opened,
        ReconciliationEvidence(
            "RB-1",
            ReconciliationEvidenceKind.STATE_READBACK,
            True,
            "fake:sheets:get",
        ),
    )
    enriched = add_reconciliation_evidence(
        enriched,
        ReconciliationEvidence(
            "VERIFY-1",
            ReconciliationEvidenceKind.VERIFICATION_PASS,
            True,
            "runtime:trusted-drive-reconciliation",
        ),
    )
    resolved = cases.save(
        apply_reconciliation(enriched, ReconciliationDisposition.ADOPT_VERIFIED_STATE)
    )
    assert resolved.phase == ReconciliationPhase.RESOLVED
    assert cases.save(resolved) == resolved
    with pytest.raises(ValueError, match="RECONCILIATION_CASE_TERMINAL"):
        cases.save(opened)


@pytest.mark.asyncio
async def test_timeout_before_commit_is_conservatively_unknown_and_never_replayed(tmp_path):
    resolver = Resolver()
    client = FailureInjectionClient()
    client.state_digest = "sha256:before"
    client.mode = "timeout_before_commit"
    cases = reconciliation_store(tmp_path)
    broker = ReconciliationAwareBroker(
        IdempotentAsyncBroker(
            ControlledGoogleDriveProvider(resolver, client),
            idempotency_store(tmp_path),
        ),
        cases,
    )

    first = await broker.execute(proposal())
    assert first.success is False
    assert first.error_code == "PROVIDER_EXCEPTION_EFFECT_UNKNOWN"
    assert client.mutate_calls == 1

    duplicate = await broker.execute(proposal())
    assert duplicate.success is False
    assert duplicate.error_code == "IDEMPOTENCY_EFFECT_UNKNOWN"
    assert client.mutate_calls == 1

    opened = cases.get_by_action(proposal().action_id)
    assert opened is not None
    client.mode = "success"
    inspection = await TrustedDriveReconciliationInspector(
        resolver=resolver,
        client=client,
        store=cases,
        retry_policy=ReadbackRetryPolicy(max_attempts=1, backoff_seconds=()),
    ).inspect(case_id=opened.case_id, proposal=proposal())
    assert inspection.effect_state == ProviderEffectState.UNSYNCED
    assert inspection.observed_state_digest == "sha256:before"
    assert client.mutate_calls == 1


@pytest.mark.asyncio
async def test_duplicate_request_after_verified_provider_success_reuses_receipt_without_second_mutation(tmp_path):
    resolver = Resolver()
    client = FailureInjectionClient()
    durable = idempotency_store(tmp_path)
    broker = IdempotentAsyncBroker(ControlledGoogleDriveProvider(resolver, client), durable)

    first = await broker.execute(proposal())
    duplicate = await broker.execute(proposal())

    assert first.success is duplicate.success is True
    assert duplicate.receipt_id == first.receipt_id
    assert client.mutate_calls == 1
    assert durable.get(proposal().idempotency_key).state == IdempotencyState.SUCCEEDED


@pytest.mark.asyncio
async def test_provider_receipt_followed_by_direct_readback_mismatch_is_not_verification_success(tmp_path):
    resolver = Resolver()
    client = FailureInjectionClient()
    durable = idempotency_store(tmp_path)
    broker = IdempotentAsyncBroker(ControlledGoogleDriveProvider(resolver, client), durable)
    outcome = await broker.execute(proposal())
    assert outcome.success is True

    client.state_digest = "sha256:mismatch-after-receipt"
    verifier = GoogleDriveOutcomeVerifier(resolver, client)
    record = ExecutionRecord(
        run_id="RUN-D-RECON",
        task="Lane D provider reconciliation",
        mode=Mode.EXP,
        goal_valid=True,
        acceptance_criteria=("exact configured row",),
    )
    verified = await verifier.verify(record, proposal(), ToolOutcome(True, outcome.receipt_id, outcome.source))
    assert verified.passed is False
    assert verified.error_code == "GOOGLE_SHEETS_READBACK_MISMATCH"
    assert client.mutate_calls == 1
    assert client.readback_calls == 1
