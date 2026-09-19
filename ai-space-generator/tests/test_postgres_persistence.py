import asyncio
import sqlite3
from pathlib import Path

import pytest

from src.authoritative_completion import CompletionAttestor
from src.controlled_runner import ToolOutcome
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
from src.idempotent_broker import IdempotentAsyncBroker, IdempotencyState
from src.operational_state import CommandActor, OperationalCommand, TaskChangeAuthority
from src.postgres_persistence import build_postgres_persistence


SECRET = b"hao-postgres-persistence-test-secret-32-bytes"
TASK_CHANGE_SECRET = b"hao-postgres-task-change-authority-key-32-bytes"


class SqlitePostgresCompatConnection:
    """Exercise Postgres store semantics without provisioning a real database."""

    def __init__(self, path: Path):
        self._conn = sqlite3.connect(path, timeout=30, isolation_level=None)
        self._conn.row_factory = sqlite3.Row

    def execute(self, sql, params=()):
        normalized = sql.strip()
        if normalized == "BEGIN ISOLATION LEVEL SERIALIZABLE":
            return self._conn.execute("BEGIN IMMEDIATE")
        if normalized.startswith(
            "ALTER TABLE operational_events ADD COLUMN IF NOT EXISTS "
            "task_change_receipt_fingerprint"
        ):
            # Fresh compatibility databases already receive the v5 column in
            # CREATE TABLE. Real Postgres uses this idempotent ALTER for an
            # existing pre-v5 table; SQLite lacks the IF NOT EXISTS form.
            return self._conn.execute("SELECT 1")
        normalized = normalized.replace(" FOR UPDATE", "").replace("%s", "?")
        return self._conn.execute(normalized, params)

    def close(self):
        self._conn.close()


def factory(path: Path):
    return lambda: SqlitePostgresCompatConnection(path)


def bundle(tmp_path: Path, *, task_change_authority=None):
    return build_postgres_persistence(
        "postgresql://runtime-v2/test",
        connect_factory=factory(tmp_path / "runtime-postgres-compat.sqlite3"),
        task_change_authority=task_change_authority,
    )


def mutation():
    return ActionProposal(
        action_id="RUN-PG:A0001:drive.update",
        archetype=ActionArchetype.MUTATE,
        externality=ActionExternality.PRIVATE_REVERSIBLE,
        capability="formal_persistence",
        provider="drive",
        action_name="update",
        expected_state_delta="write one row",
        idempotency_key="RUN-PG:A0001:drive.update",
    )


class Provider:
    def __init__(self):
        self.calls = 0

    async def execute(self, proposal):
        self.calls += 1
        return ToolOutcome(True, "RECEIPT-PG-1", "drive")


def closed_record():
    action = ActionProposal(
        action_id="RUN-COMPLETE:A0001:read",
        archetype=ActionArchetype.READ,
        externality=ActionExternality.READ_ONLY,
        capability="authority_read",
        provider="drive",
        action_name="read",
    )
    evidence = (
        EvidenceReceipt(
            "VERIFY-PG",
            EvidenceKind.VERIFICATION_PASS,
            True,
            "runtime-verifier",
            claim_scope=action.action_id,
            origin=EvidenceOrigin.VERIFIER,
        ),
        EvidenceReceipt(
            "GATE-PG",
            EvidenceKind.ACCEPTANCE_GATE_PASS,
            True,
            "runtime-verifier",
            claim_scope=action.action_id,
            origin=EvidenceOrigin.VERIFIER,
            gate_id="PG_ACCEPTANCE",
        ),
    )
    return ExecutionRecord(
        run_id="RUN-COMPLETE",
        task="Postgres persistence",
        mode=Mode.EXP,
        goal_valid=True,
        acceptance_criteria=("persistence is authoritative",),
        required_acceptance_gate_ids=("PG_ACCEPTANCE",),
        phase=RunPhase.CLOSED,
        action=action,
        evidence=evidence,
    )


def test_bundle_rejects_non_postgres_production_database_url(tmp_path):
    with pytest.raises(ValueError, match="POSTGRES_DATABASE_URL_REQUIRED"):
        build_postgres_persistence(
            "sqlite:///runtime.sqlite3",
            connect_factory=factory(tmp_path / "unused.sqlite3"),
        )


def test_operational_state_is_durable_idempotent_stale_safe_and_task_receipt_bound(tmp_path):
    authority = TaskChangeAuthority(TASK_CHANGE_SECRET)
    first = bundle(tmp_path, task_change_authority=authority)
    initial = first.operational_state.initialize(mode=Mode.EXP, task="Stable task")

    change_text = "SYS > TASK: New task"
    receipt = authority.issue(
        initial,
        event_id="EVENT-PG-1",
        actor=CommandActor.USER,
        text=change_text,
    )
    assert receipt is not None
    changed = first.operational_state.apply(
        OperationalCommand(
            "EVENT-PG-1",
            CommandActor.USER,
            change_text,
            task_change_receipt=receipt,
            expected_version=initial.version,
        )
    )
    duplicate = first.operational_state.apply(
        OperationalCommand(
            "EVENT-PG-1",
            CommandActor.USER,
            change_text,
            task_change_receipt=receipt,
        )
    )
    reactive_upload = first.operational_state.apply(
        OperationalCommand(
            "EVENT-PG-REACTIVE-IMAGE",
            CommandActor.USER,
            "這張屋頂圖片只是回應上一個錯誤生成結果",
            expected_version=changed.state.version,
        )
    )
    stale = first.operational_state.apply(
        OperationalCommand(
            "EVENT-PG-2",
            CommandActor.USER,
            "EXE > stale",
            expected_version=initial.version,
        )
    )

    assert changed.code == "USER_MODE_AND_TASK_RECEIPT_APPLIED"
    assert changed.state.mode == Mode.SYS
    assert changed.state.task == "New task"
    assert changed.state.version == 2
    assert duplicate.code == "EVENT_ALREADY_APPLIED"
    assert duplicate.state.version == 2
    assert reactive_upload.applied is False
    assert reactive_upload.code == "NO_OPERATIONAL_CHANGE"
    assert reactive_upload.state.task == "New task"
    assert stale.applied is False
    assert stale.code == "STALE_OPERATIONAL_STATE"
    assert stale.state.mode == Mode.SYS

    with sqlite3.connect(tmp_path / "runtime-postgres-compat.sqlite3") as conn:
        fingerprint = conn.execute(
            "SELECT task_change_receipt_fingerprint FROM operational_events WHERE event_id = ?",
            ("EVENT-PG-1",),
        ).fetchone()[0]
    assert fingerprint == receipt.receipt_fingerprint

    restarted = bundle(tmp_path, task_change_authority=authority)
    restored = restarted.operational_state.get()
    assert restored.mode == Mode.SYS
    assert restored.task == "New task"
    assert restored.version == 2


def test_postgres_task_directive_without_runtime_receipt_fails_closed(tmp_path):
    authority = TaskChangeAuthority(TASK_CHANGE_SECRET)
    runtime = bundle(tmp_path, task_change_authority=authority)
    initial = runtime.operational_state.initialize(mode=Mode.EXP, task="Stable task")
    blocked = runtime.operational_state.apply(
        OperationalCommand(
            "EVENT-PG-NO-RECEIPT",
            CommandActor.USER,
            "TASK: Caller invented task",
            expected_version=initial.version,
        )
    )
    assert blocked.applied is False
    assert blocked.code == "USER_TASK_CHANGE_RECEIPT_REQUIRED"
    assert blocked.state.task == "Stable task"


def test_broker_replays_success_from_postgres_store_without_second_provider_call(tmp_path):
    runtime = bundle(tmp_path)
    first_provider = Provider()
    first = asyncio.run(
        IdempotentAsyncBroker(first_provider, runtime.idempotency).execute(mutation())
    )
    assert first.success is True
    assert first_provider.calls == 1
    assert runtime.idempotency.get(mutation().idempotency_key).state == IdempotencyState.SUCCEEDED

    second_provider = Provider()
    restarted = bundle(tmp_path)
    replay = asyncio.run(
        IdempotentAsyncBroker(second_provider, restarted.idempotency).execute(mutation())
    )
    assert replay.success is True
    assert replay.receipt_id == "RECEIPT-PG-1"
    assert second_provider.calls == 0


def test_authoritative_completion_is_idempotent_and_conflict_safe_across_restart(tmp_path):
    runtime = bundle(tmp_path)
    record = closed_record()
    attestor = CompletionAttestor(SECRET)
    first_attestation = attestor.issue(
        record,
        operational_version=7,
        issued_at="2026-09-05T02:40:00+08:00",
    )
    first = runtime.completion.commit(
        first_attestation,
        record,
        operational_version=7,
        attestor=attestor,
    )
    assert first.committed is True
    assert first.code == "AUTHORITATIVE_COMPLETION_COMMITTED"

    restarted = bundle(tmp_path)
    replay = restarted.completion.commit(
        first_attestation,
        record,
        operational_version=7,
        attestor=attestor,
    )
    assert replay.committed is True
    assert replay.code == "ATTESTATION_ALREADY_COMMITTED"

    conflicting_attestation = attestor.issue(
        record,
        operational_version=7,
        issued_at="2026-09-05T02:41:00+08:00",
    )
    conflict = restarted.completion.commit(
        conflicting_attestation,
        record,
        operational_version=7,
        attestor=attestor,
    )
    assert conflict.committed is False
    assert conflict.code == "AUTHORITATIVE_COMPLETION_CONFLICT"


def test_mcp_run_ownership_is_shared_durable_and_identity_bound(tmp_path):
    runtime = bundle(tmp_path)
    first = runtime.run_registry.register(
        workflow_id="WORKFLOW-1",
        owner_subject="hao-subject",
        operational_version=9,
    )
    assert first.workflow_id == "WORKFLOW-1"
    assert first.operational_version == 9

    restarted = bundle(tmp_path)
    owned = restarted.run_registry.require_owned(
        workflow_id="WORKFLOW-1",
        owner_subject="hao-subject",
    )
    assert owned == first

    duplicate = restarted.run_registry.register(
        workflow_id="WORKFLOW-1",
        owner_subject="hao-subject",
        operational_version=9,
    )
    assert duplicate == first

    with pytest.raises(PermissionError, match="CONTROLLED_RUN_OWNER_MISMATCH"):
        restarted.run_registry.require_owned(
            workflow_id="WORKFLOW-1",
            owner_subject="other-subject",
        )

    with pytest.raises(ValueError, match="MCP_RUN_IDENTITY_CONFLICT"):
        restarted.run_registry.register(
            workflow_id="WORKFLOW-1",
            owner_subject="hao-subject",
            operational_version=10,
        )
