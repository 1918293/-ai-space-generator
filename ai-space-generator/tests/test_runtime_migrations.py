import pytest

from src.runtime_migrations import (
    CURRENT_RUNTIME_SCHEMA_VERSION,
    MIGRATION_ADVISORY_LOCK_ID,
    run_postgres_migrations,
    verify_postgres_schema,
)


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


class FakePostgresConnection:
    def __init__(self, *, initial_version=0, missing_table=""):
        self.version = initial_version
        self.missing_table = missing_table
        self.calls = []
        self.closed = False

    def execute(self, sql, params=()):
        normalized = " ".join(sql.split())
        self.calls.append((normalized, tuple(params)))
        if "SELECT COALESCE(MAX(version), 0) AS version" in normalized:
            return Cursor(Row(version=self.version))
        if normalized.startswith("INSERT INTO runtime_schema_migrations"):
            self.version = int(params[0])
            return Cursor(rowcount=1)
        if normalized.startswith("SELECT to_regclass"):
            table = str(params[0])
            return Cursor(Row(table_name=None if table == self.missing_table else table))
        return Cursor(rowcount=1)

    def close(self):
        self.closed = True


def factory(conn):
    return lambda: conn


def test_migration_uses_serializable_transaction_and_advisory_lock():
    conn = FakePostgresConnection()
    result = run_postgres_migrations(
        "postgresql://runtime/test",
        release_id="runtime-v2.0.0-rc1",
        connect_factory=factory(conn),
    )
    assert result.from_version == 0
    assert result.to_version == CURRENT_RUNTIME_SCHEMA_VERSION
    assert result.applied_versions == tuple(range(1, CURRENT_RUNTIME_SCHEMA_VERSION + 1))
    assert conn.version == CURRENT_RUNTIME_SCHEMA_VERSION
    assert (
        "SELECT pg_advisory_xact_lock(%s)",
        (MIGRATION_ADVISORY_LOCK_ID,),
    ) in conn.calls
    assert conn.calls[0][0] == "BEGIN ISOLATION LEVEL SERIALIZABLE"
    assert conn.calls[-1][0] == "COMMIT"
    assert conn.closed is True


def test_migration_is_idempotent_when_target_version_already_applied():
    conn = FakePostgresConnection(initial_version=CURRENT_RUNTIME_SCHEMA_VERSION)
    result = run_postgres_migrations(
        "postgresql://runtime/test",
        connect_factory=factory(conn),
    )
    assert result.applied_versions == ()
    assert result.from_version == CURRENT_RUNTIME_SCHEMA_VERSION
    assert result.to_version == CURRENT_RUNTIME_SCHEMA_VERSION


def test_migration_upgrades_prior_schema_one_to_current():
    conn = FakePostgresConnection(initial_version=1)
    result = run_postgres_migrations(
        "postgresql://runtime/test",
        connect_factory=factory(conn),
    )
    assert result.from_version == 1
    assert result.to_version == CURRENT_RUNTIME_SCHEMA_VERSION
    assert result.applied_versions == tuple(range(2, CURRENT_RUNTIME_SCHEMA_VERSION + 1))


def test_runtime_refuses_database_newer_than_code():
    conn = FakePostgresConnection(initial_version=CURRENT_RUNTIME_SCHEMA_VERSION + 1)
    with pytest.raises(RuntimeError, match="DATABASE_SCHEMA_NEWER_THAN_RUNTIME"):
        run_postgres_migrations(
            "postgresql://runtime/test",
            target_version=CURRENT_RUNTIME_SCHEMA_VERSION,
            connect_factory=factory(conn),
        )
    assert any(call[0] == "ROLLBACK" for call in conn.calls)


def test_schema_readiness_rejects_config_binary_version_drift_before_database_access():
    conn = FakePostgresConnection(initial_version=CURRENT_RUNTIME_SCHEMA_VERSION - 1)
    with pytest.raises(RuntimeError, match="RUNTIME_BINARY_SCHEMA_VERSION_MISMATCH"):
        verify_postgres_schema(
            "postgresql://runtime/test",
            expected_version=CURRENT_RUNTIME_SCHEMA_VERSION - 1,
            connect_factory=factory(conn),
        )
    assert conn.calls == []
    assert conn.closed is False


def test_schema_readiness_requires_exact_version_and_every_runtime_table():
    conn = FakePostgresConnection(initial_version=CURRENT_RUNTIME_SCHEMA_VERSION)
    assert verify_postgres_schema(
        "postgresql://runtime/test",
        expected_version=CURRENT_RUNTIME_SCHEMA_VERSION,
        connect_factory=factory(conn),
    ) == CURRENT_RUNTIME_SCHEMA_VERSION

    mismatch = FakePostgresConnection(initial_version=CURRENT_RUNTIME_SCHEMA_VERSION - 1)
    with pytest.raises(RuntimeError, match="RUNTIME_SCHEMA_VERSION_MISMATCH"):
        verify_postgres_schema(
            "postgresql://runtime/test",
            expected_version=CURRENT_RUNTIME_SCHEMA_VERSION,
            connect_factory=factory(mismatch),
        )

    missing = FakePostgresConnection(
        initial_version=CURRENT_RUNTIME_SCHEMA_VERSION,
        missing_table="parent_tasks",
    )
    with pytest.raises(RuntimeError, match="RUNTIME_SCHEMA_TABLE_MISSING:parent_tasks"):
        verify_postgres_schema(
            "postgresql://runtime/test",
            expected_version=CURRENT_RUNTIME_SCHEMA_VERSION,
            connect_factory=factory(missing),
        )
