from __future__ import annotations

import pytest

from src import runtime_migrations


class Row(dict):
    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self.values())[key]
        return super().__getitem__(key)


class Cursor:
    def __init__(self, row=None):
        self._row = row

    def fetchone(self):
        return self._row


class CompatibilityConnection:
    def __init__(self, physical_version: int):
        self.physical_version = physical_version
        self.closed = False

    def execute(self, sql, params=()):
        normalized = " ".join(sql.split())
        if "SELECT COALESCE(MAX(version), 0) AS version" in normalized:
            return Cursor(Row(version=self.physical_version))
        if "SELECT to_regclass" in normalized:
            return Cursor(Row(table_name=str(params[0])))
        return Cursor()

    def close(self):
        self.closed = True


def _factory(version: int):
    conn = CompatibilityConnection(version)
    return conn, lambda: conn


def test_current_schema_has_explicit_compatibility_epoch():
    current = runtime_migrations.current_storage_compatibility()
    assert current.physical_version == runtime_migrations.CURRENT_RUNTIME_SCHEMA_VERSION
    assert current.compatibility_epoch == 1


def test_additive_newer_physical_version_can_remain_compatible(monkeypatch):
    monkeypatch.setitem(runtime_migrations.MIGRATION_COMPATIBILITY_EPOCH, 4, 1)
    conn, factory = _factory(4)
    verified = runtime_migrations.verify_postgres_schema(
        "postgresql://runtime/test",
        minimum_version=3,
        supported_compatibility_epochs=(1,),
        connect_factory=factory,
    )
    assert verified == 4
    assert conn.closed is True


def test_binary_needing_new_expand_migration_rejects_older_database():
    conn, factory = _factory(3)
    with pytest.raises(RuntimeError, match="RUNTIME_SCHEMA_TOO_OLD:3<4"):
        runtime_migrations.verify_postgres_schema(
            "postgresql://runtime/test",
            minimum_version=4,
            supported_compatibility_epochs=(1,),
            connect_factory=factory,
        )
    assert conn.closed is True


def test_contract_epoch_change_blocks_old_binary(monkeypatch):
    monkeypatch.setitem(runtime_migrations.MIGRATION_COMPATIBILITY_EPOCH, 4, 2)
    conn, factory = _factory(4)
    with pytest.raises(
        RuntimeError,
        match=r"RUNTIME_STORAGE_COMPATIBILITY_EPOCH_MISMATCH:2 not in \[1\]",
    ):
        runtime_migrations.verify_postgres_schema(
            "postgresql://runtime/test",
            minimum_version=3,
            supported_compatibility_epochs=(1,),
            connect_factory=factory,
        )
    assert conn.closed is True


def test_new_binary_can_explicitly_support_old_and_new_epochs(monkeypatch):
    monkeypatch.setitem(runtime_migrations.MIGRATION_COMPATIBILITY_EPOCH, 4, 2)
    conn, factory = _factory(4)
    verified = runtime_migrations.verify_postgres_schema(
        "postgresql://runtime/test",
        minimum_version=4,
        supported_compatibility_epochs=(1, 2),
        connect_factory=factory,
    )
    assert verified == 4


def test_exact_verification_remains_available_for_migration_job():
    conn, factory = _factory(runtime_migrations.CURRENT_RUNTIME_SCHEMA_VERSION)
    verified = runtime_migrations.verify_postgres_schema(
        "postgresql://runtime/test",
        expected_version=runtime_migrations.CURRENT_RUNTIME_SCHEMA_VERSION,
        connect_factory=factory,
    )
    assert verified == runtime_migrations.CURRENT_RUNTIME_SCHEMA_VERSION


def test_exact_and_compatibility_modes_cannot_be_mixed():
    with pytest.raises(ValueError, match="STORAGE_VERIFICATION_MODE_CONFLICT"):
        runtime_migrations.verify_postgres_schema(
            "postgresql://runtime/test",
            expected_version=3,
            minimum_version=3,
            supported_compatibility_epochs=(1,),
        )
