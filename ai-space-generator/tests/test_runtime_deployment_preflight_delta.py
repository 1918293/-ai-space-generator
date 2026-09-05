import sqlite3
from pathlib import Path

from src.authoritative_completion import CompletionAttestor
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
from src.postgres_persistence import build_postgres_persistence
from src.runtime_migrations import CURRENT_RUNTIME_SCHEMA_VERSION, run_postgres_migrations


OLD_SECRET = b"old-completion-signing-secret-32-bytes-minimum"
NEW_SECRET = b"new-completion-signing-secret-32-bytes-minimum"


class Row(dict):
    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self.values())[key]
        return super().__getitem__(key)


class Cursor:
    def __init__(self, row=None, rowcount=0):
        self._row = row
        self.rowcount = rowcount

    def fetchone(self):
        return self._row


class FakeMigrationConnection:
    def __init__(self, *, initial_version: int):
        self.version = initial_version
        self.calls: list[tuple[str, tuple[object, ...]]] = []
        self.closed = False

    def execute(self, sql, params=()):
        normalized = " ".join(sql.split())
        self.calls.append((normalized, tuple(params)))
        if "SELECT COALESCE(MAX(version), 0) AS version" in normalized:
            return Cursor(Row(version=self.version))
        if normalized.startswith("INSERT INTO runtime_schema_migrations"):
            self.version = int(params[0])
            return Cursor(rowcount=1)
        return Cursor(rowcount=1)

    def close(self):
        self.closed = True


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


def _closed_record() -> ExecutionRecord:
    action = ActionProposal(
        action_id="RUN-ROTATION:A0001:read",
        archetype=ActionArchetype.READ,
        externality=ActionExternality.READ_ONLY,
        capability="authority_read",
        provider="drive",
        action_name="read",
    )
    evidence = (
        EvidenceReceipt(
            "VERIFY-ROTATION",
            EvidenceKind.VERIFICATION_PASS,
            True,
            "runtime-verifier",
            claim_scope=action.action_id,
            origin=EvidenceOrigin.VERIFIER,
        ),
        EvidenceReceipt(
            "GATE-ROTATION",
            EvidenceKind.ACCEPTANCE_GATE_PASS,
            True,
            "runtime-verifier",
            claim_scope=action.action_id,
            origin=EvidenceOrigin.VERIFIER,
            gate_id="ROTATION_ACCEPTANCE",
        ),
    )
    return ExecutionRecord(
        run_id="RUN-ROTATION",
        task="Completion signing rotation",
        mode=Mode.EXP,
        goal_valid=True,
        acceptance_criteria=("completion remains verifiable through bounded rotation",),
        required_acceptance_gate_ids=("ROTATION_ACCEPTANCE",),
        phase=RunPhase.CLOSED,
        action=action,
        evidence=evidence,
    )


def test_schema_two_upgrades_only_through_key_id_migration_three():
    conn = FakeMigrationConnection(initial_version=2)
    result = run_postgres_migrations(
        "postgresql://runtime/test",
        release_id="runtime-v2-key-rotation",
        connect_factory=lambda: conn,
    )
    assert CURRENT_RUNTIME_SCHEMA_VERSION == 3
    assert result.from_version == 2
    assert result.to_version == 3
    assert result.applied_versions == (3,)
    migration_sql = [sql for sql, _ in conn.calls]
    assert any(
        "ALTER TABLE authoritative_completions ADD COLUMN IF NOT EXISTS key_id" in sql
        for sql in migration_sql
    )
    assert conn.closed is True


def test_postgres_completion_replay_survives_restart_and_bounded_key_rotation(tmp_path):
    database_path = tmp_path / "rotation-postgres-compat.sqlite3"
    factory = lambda: SqlitePostgresCompatConnection(database_path)
    record = _closed_record()

    runtime = build_postgres_persistence(
        "postgresql://runtime-v2/test",
        connect_factory=factory,
    )
    old_attestor = CompletionAttestor(OLD_SECRET, key_id="completion-signing-v1")
    old_attestation = old_attestor.issue(
        record,
        operational_version=7,
        issued_at="2026-09-05T15:45:00+08:00",
    )
    committed = runtime.completion.commit(
        old_attestation,
        record,
        operational_version=7,
        attestor=old_attestor,
    )
    assert committed.committed is True
    assert committed.code == "AUTHORITATIVE_COMPLETION_COMMITTED"

    with sqlite3.connect(database_path) as conn:
        row = conn.execute(
            "SELECT key_id, signature FROM authoritative_completions WHERE run_id = ?",
            (record.run_id,),
        ).fetchone()
    assert row is not None
    assert row[0] == "completion-signing-v1"
    assert row[1] == old_attestation.signature

    restarted = build_postgres_persistence(
        "postgresql://runtime-v2/test",
        connect_factory=factory,
    )
    rotated_attestor = CompletionAttestor(
        NEW_SECRET,
        key_id="completion-signing-v2",
        verification_keys={"completion-signing-v1": OLD_SECRET},
    )
    replay = restarted.completion.commit(
        old_attestation,
        record,
        operational_version=7,
        attestor=rotated_attestor,
    )
    assert replay.committed is True
    assert replay.code == "ATTESTATION_ALREADY_COMMITTED"

    revoked_attestor = CompletionAttestor(
        NEW_SECRET,
        key_id="completion-signing-v2",
    )
    revoked = restarted.completion.commit(
        old_attestation,
        record,
        operational_version=7,
        attestor=revoked_attestor,
    )
    assert revoked.committed is False
    assert revoked.code == "INVALID_CONTROL_PLANE_ATTESTATION"


def test_production_env_example_tracks_schema_bootstrap_and_secret_rotation_contract():
    env_path = Path(__file__).resolve().parents[1] / "deploy" / "runtime-production.env.example"
    body = env_path.read_text(encoding="utf-8")
    assert f"HAO_DATABASE_SCHEMA_VERSION={CURRENT_RUNTIME_SCHEMA_VERSION}" in body
    assert "HAO_INITIAL_MODE=EXP" in body
    assert "HAO_INITIAL_TASK=<deployment-seeded-task>" in body
    assert "HAO_ATTESTATION_PREVIOUS_KEYS_JSON={}" in body
    assert "add HAO_ATTESTATION_PREVIOUS_KEYS_JSON to the numeric binding map" in body
    assert "HAO_DATABASE_URL=postgresql://<database-user>@<cloud-sql>/runtime" in body
    assert "If a password is embedded in HAO_DATABASE_URL" in body
