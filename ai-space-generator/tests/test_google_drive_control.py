import asyncio
from pathlib import Path

from src.controlled_runner import ToolOutcome
from src.execution_control import (
    ActionArchetype,
    ActionExternality,
    ActionProposal,
    EvidenceKind,
    ExecutionRecord,
    FailureStage,
    Mode,
)
from src.google_drive_control import (
    ControlledGoogleDriveProvider,
    DriveAuthorityReadback,
    DriveMutationCommand,
    DriveMutationReceipt,
    DriveStateReadback,
    GoogleDriveAuthorityGuard,
    GoogleDriveOutcomeVerifier,
)
from src.idempotent_broker import (
    IdempotencyState,
    IdempotentAsyncBroker,
    SQLiteIdempotencyStore,
)


def proposal(**overrides):
    values = dict(
        action_id="RUN-DRIVE:A0001:drive.update",
        archetype=ActionArchetype.MUTATE,
        externality=ActionExternality.PRIVATE_REVERSIBLE,
        capability="formal_persistence",
        provider="google-drive",
        action_name="update_cells",
        expected_state_delta="update exact authorized cells",
        authority_snapshot_fingerprint="AUTH-FP-1",
        authorization_scope="HAO_DRIVE_WRITE:formal-record",
        idempotency_key="RUN-DRIVE:A0001:drive.update",
        rollback_available=True,
    )
    values.update(overrides)
    return ActionProposal(**values)


def command(**overrides):
    values = dict(
        action_id="RUN-DRIVE:A0001:drive.update",
        provider="google-drive",
        action_name="update_cells",
        target_ref="drive://formal-record/exact-range",
        expected_state_delta="update exact authorized cells",
        expected_state_digest="sha256:expected-state",
        authorization_scope="HAO_DRIVE_WRITE:formal-record",
        authorization_ref="AUTHORIZATION:REC-1",
        authority_snapshot_fingerprint="AUTH-FP-1",
        payload=(("range", "A10:B10"), ("value", "verified")),
    )
    values.update(overrides)
    return DriveMutationCommand(**values)


class Resolver:
    def __init__(self, resolved=None):
        self.resolved = resolved if resolved is not None else command()
        self.calls = 0

    def resolve(self, current_proposal):
        self.calls += 1
        return self.resolved


class Client:
    def __init__(
        self,
        *,
        mutation=None,
        readback=None,
        authority=None,
        mutation_exc=None,
        readback_exc=None,
    ):
        self.mutation = mutation or DriveMutationReceipt(True, "DRIVE-RECEIPT-1", "drive-api")
        self.readback_result = readback or DriveStateReadback(
            "sha256:expected-state", "drive-readback", True
        )
        self.authority = authority or DriveAuthorityReadback(
            "AUTH-FP-1", "drive-authority", True
        )
        self.mutation_exc = mutation_exc
        self.readback_exc = readback_exc
        self.mutate_calls = 0
        self.readback_calls = 0
        self.authority_calls = 0

    async def preflight_authority(self, current_command):
        self.authority_calls += 1
        return self.authority

    async def mutate(self, current_command):
        self.mutate_calls += 1
        if self.mutation_exc is not None:
            raise self.mutation_exc
        return self.mutation

    async def readback(self, current_command):
        self.readback_calls += 1
        if self.readback_exc is not None:
            raise self.readback_exc
        return self.readback_result


def store(tmp_path: Path):
    return SQLiteIdempotencyStore(str(tmp_path / "drive-idempotency.sqlite3"))


def record():
    return ExecutionRecord(
        run_id="RUN-DRIVE",
        task="Controlled Drive mutation",
        mode=Mode.EXP,
        goal_valid=True,
        acceptance_criteria=("exact resulting state",),
    )


def test_successful_mutation_is_idempotently_replayed_without_second_drive_call(tmp_path):
    client = Client()
    durable_store = store(tmp_path)
    first = asyncio.run(
        IdempotentAsyncBroker(
            ControlledGoogleDriveProvider(Resolver(), client),
            durable_store,
        ).execute(proposal())
    )
    assert first.success is True
    assert first.receipt_id == "DRIVE-RECEIPT-1"
    assert client.mutate_calls == 1
    assert durable_store.get(proposal().idempotency_key).state == IdempotencyState.SUCCEEDED

    second_client = Client()
    replay = asyncio.run(
        IdempotentAsyncBroker(
            ControlledGoogleDriveProvider(Resolver(), second_client),
            durable_store,
        ).execute(proposal())
    )
    assert replay.success is True
    assert replay.receipt_id == "DRIVE-RECEIPT-1"
    assert second_client.mutate_calls == 0


def test_ambiguous_drive_exception_becomes_unknown_effect_and_never_auto_replays(tmp_path):
    durable_store = store(tmp_path)
    first_client = Client(mutation_exc=ConnectionError("connection dropped"))
    first = asyncio.run(
        IdempotentAsyncBroker(
            ControlledGoogleDriveProvider(Resolver(), first_client),
            durable_store,
        ).execute(proposal())
    )
    assert first.success is False
    assert first.error_code == "PROVIDER_EXCEPTION_EFFECT_UNKNOWN"
    assert durable_store.get(proposal().idempotency_key).state == IdempotencyState.UNKNOWN_EFFECT

    second_client = Client()
    second = asyncio.run(
        IdempotentAsyncBroker(
            ControlledGoogleDriveProvider(Resolver(), second_client),
            durable_store,
        ).execute(proposal())
    )
    assert second.success is False
    assert second.error_code == "IDEMPOTENCY_EFFECT_UNKNOWN"
    assert second_client.mutate_calls == 0


def test_provider_confirmed_no_effect_is_remembered_as_known_failure(tmp_path):
    client = Client(
        mutation=DriveMutationReceipt(
            False,
            error_code="DRIVE_PERMISSION_DENIED",
            no_effect_confirmed=True,
        )
    )
    durable_store = store(tmp_path)
    result = asyncio.run(
        IdempotentAsyncBroker(
            ControlledGoogleDriveProvider(Resolver(), client),
            durable_store,
        ).execute(proposal())
    )
    assert result.success is False
    assert result.error_code == "DRIVE_PERMISSION_DENIED"
    assert durable_store.get(proposal().idempotency_key).state == IdempotencyState.FAILED_NO_EFFECT


def test_missing_exact_authorization_is_blocked_before_drive_mutation(tmp_path):
    client = Client()
    result = asyncio.run(
        IdempotentAsyncBroker(
            ControlledGoogleDriveProvider(
                Resolver(command(authorization_ref="")),
                client,
            ),
            store(tmp_path),
        ).execute(proposal())
    )
    assert result.success is False
    assert result.error_code == "DRIVE_EXACT_AUTHORIZATION_REQUIRED"
    assert result.failure_stage == FailureStage.BINDING
    assert client.mutate_calls == 0


def test_authority_guard_returns_fresh_source_fingerprint_for_runtime_comparison():
    client = Client()
    outcome = asyncio.run(GoogleDriveAuthorityGuard(Resolver(), client).verify_current(proposal()))
    assert outcome.passed is True
    assert outcome.current_fingerprint == "AUTH-FP-1"
    assert outcome.source == "drive-authority"
    assert client.authority_calls == 1


def test_exact_resulting_state_readback_mints_action_scoped_verification_evidence():
    client = Client()
    verifier = GoogleDriveOutcomeVerifier(Resolver(), client)
    result = asyncio.run(
        verifier.verify(
            record(),
            proposal(),
            ToolOutcome(True, "DRIVE-RECEIPT-1", "drive-api"),
        )
    )
    assert result.passed is True
    kinds = {receipt.kind for receipt in result.receipts}
    assert kinds == {
        EvidenceKind.STATE_READBACK,
        EvidenceKind.VERIFICATION_PASS,
        EvidenceKind.ACCEPTANCE_GATE_PASS,
    }
    assert {receipt.claim_scope for receipt in result.receipts} == {proposal().action_id}
    gate = [r for r in result.receipts if r.kind == EvidenceKind.ACCEPTANCE_GATE_PASS][0]
    assert gate.gate_id == "DRIVE_EXPECTED_STATE_MATCH"
    assert client.readback_calls == 1


def test_resulting_state_mismatch_is_persistence_failure_not_success():
    client = Client(
        readback=DriveStateReadback(
            "sha256:unexpected-state",
            "drive-readback",
            False,
            "DRIVE_CELL_READBACK_MISMATCH",
        )
    )
    result = asyncio.run(
        GoogleDriveOutcomeVerifier(Resolver(), client).verify(
            record(),
            proposal(),
            ToolOutcome(True, "DRIVE-RECEIPT-1", "drive-api"),
        )
    )
    assert result.passed is False
    assert result.error_code == "DRIVE_CELL_READBACK_MISMATCH"
    assert result.failure_stage == FailureStage.PERSISTENCE
