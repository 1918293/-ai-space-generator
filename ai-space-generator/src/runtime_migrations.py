from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Callable


ConnectionFactory = Callable[[], Any]
CURRENT_RUNTIME_SCHEMA_VERSION = 2
MIGRATION_ADVISORY_LOCK_ID = 0x48414F52  # "HAOR"


@dataclass(frozen=True)
class MigrationResult:
    from_version: int
    to_version: int
    applied_versions: tuple[int, ...]


_MIGRATION_1 = (
    """
    CREATE TABLE IF NOT EXISTS operational_state (
        singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
        mode TEXT NOT NULL,
        task TEXT NOT NULL,
        version INTEGER NOT NULL,
        last_event_id TEXT NOT NULL DEFAULT ''
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS operational_events (
        event_id TEXT PRIMARY KEY,
        actor TEXT NOT NULL,
        command_text TEXT NOT NULL,
        resulting_version INTEGER NOT NULL,
        applied INTEGER NOT NULL,
        code TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS idempotency_records (
        key TEXT PRIMARY KEY,
        action_fingerprint TEXT NOT NULL,
        state TEXT NOT NULL,
        receipt_id TEXT NOT NULL DEFAULT '',
        source TEXT NOT NULL DEFAULT '',
        error_code TEXT NOT NULL DEFAULT ''
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS authoritative_completions (
        run_id TEXT PRIMARY KEY,
        action_id TEXT NOT NULL,
        task TEXT NOT NULL,
        mode TEXT NOT NULL,
        operational_version INTEGER NOT NULL,
        authority_snapshot_fingerprint TEXT NOT NULL,
        evidence_digest TEXT NOT NULL,
        issued_at TEXT NOT NULL,
        signature TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS mcp_control_runs (
        workflow_id TEXT PRIMARY KEY,
        owner_subject TEXT NOT NULL,
        operational_version INTEGER NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS finalization_issue_times (
        workflow_id TEXT PRIMARY KEY,
        issued_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS reconciliation_cases (
        case_id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL,
        action_id TEXT NOT NULL UNIQUE,
        kind TEXT NOT NULL,
        effect_may_have_occurred INTEGER NOT NULL,
        evidence_json TEXT NOT NULL DEFAULT '[]',
        phase TEXT NOT NULL,
        disposition TEXT NOT NULL DEFAULT '',
        resolution_code TEXT NOT NULL DEFAULT '',
        trigger_error_code TEXT NOT NULL DEFAULT ''
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS parent_tasks (
        task_run_id TEXT PRIMARY KEY,
        plan_id TEXT NOT NULL,
        task TEXT NOT NULL,
        mode TEXT NOT NULL,
        admitted_operational_version INTEGER NOT NULL,
        required_action_ids_json TEXT NOT NULL,
        required_gate_ids_json TEXT NOT NULL,
        hao_acceptance_required INTEGER NOT NULL,
        authority_snapshot_fingerprint TEXT NOT NULL DEFAULT '',
        child_outcomes_json TEXT NOT NULL DEFAULT '[]',
        passed_gate_ids_json TEXT NOT NULL DEFAULT '[]',
        hao_accepted INTEGER NOT NULL DEFAULT 0,
        phase TEXT NOT NULL,
        failure_code TEXT NOT NULL DEFAULT ''
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS parent_task_children (
        task_run_id TEXT NOT NULL,
        slot_index INTEGER NOT NULL,
        slot_id TEXT NOT NULL,
        binding_id TEXT NOT NULL,
        action_id TEXT NOT NULL UNIQUE,
        workflow_id TEXT NOT NULL DEFAULT '',
        operational_version INTEGER NOT NULL DEFAULT 0,
        finalized INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY(task_run_id, slot_index)
    )
    """,
)

_MIGRATION_2 = (
    "ALTER TABLE parent_tasks ADD COLUMN IF NOT EXISTS row_revision INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE parent_task_children ADD COLUMN IF NOT EXISTS depends_on_slots_json TEXT NOT NULL DEFAULT '[]'",
    """
    CREATE TABLE IF NOT EXISTS reconciliation_retry_reservations (
        case_id TEXT PRIMARY KEY,
        owner_ref TEXT NOT NULL,
        request_fingerprint TEXT NOT NULL,
        retry_run_id TEXT NOT NULL UNIQUE
    )
    """,
)

MIGRATIONS: dict[int, tuple[str, ...]] = {1: _MIGRATION_1, 2: _MIGRATION_2}

_REQUIRED_TABLES = (
    "operational_state",
    "operational_events",
    "idempotency_records",
    "authoritative_completions",
    "mcp_control_runs",
    "finalization_issue_times",
    "reconciliation_cases",
    "reconciliation_retry_reservations",
    "parent_tasks",
    "parent_task_children",
)


def _default_connect_factory(database_url: str) -> ConnectionFactory:
    def connect() -> Any:
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("PSYCOPG_REQUIRED_FOR_RUNTIME_MIGRATIONS") from exc
        return psycopg.connect(database_url, autocommit=True, row_factory=dict_row)

    return connect


def _database_url(value: str) -> str:
    value = value.strip()
    if not value.startswith(("postgresql://", "postgres://")):
        raise ValueError("POSTGRES_DATABASE_URL_REQUIRED")
    return value


def _scalar(row: Any, key: str, index: int = 0) -> Any:
    try:
        return row[key]
    except (TypeError, KeyError, IndexError):
        return row[index]


def _current_version(conn: Any) -> int:
    row = conn.execute(
        "SELECT COALESCE(MAX(version), 0) AS version FROM runtime_schema_migrations"
    ).fetchone()
    return int(_scalar(row, "version")) if row is not None else 0


def run_postgres_migrations(
    database_url: str,
    *,
    target_version: int = CURRENT_RUNTIME_SCHEMA_VERSION,
    release_id: str = "",
    connect_factory: ConnectionFactory | None = None,
    enforce_advisory_lock: bool = True,
) -> MigrationResult:
    """Apply ordered Runtime v2 migrations under one serializable advisory lock."""
    _database_url(database_url)
    if target_version < 1 or target_version > CURRENT_RUNTIME_SCHEMA_VERSION:
        raise ValueError("UNSUPPORTED_RUNTIME_SCHEMA_VERSION")
    connect = connect_factory or _default_connect_factory(database_url)
    conn = connect()
    try:
        conn.execute("BEGIN ISOLATION LEVEL SERIALIZABLE")
        if enforce_advisory_lock:
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (MIGRATION_ADVISORY_LOCK_ID,))
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS runtime_schema_migrations (
                version INTEGER PRIMARY KEY,
                release_id TEXT NOT NULL DEFAULT '',
                applied_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        before = _current_version(conn)
        if before > target_version:
            raise RuntimeError("DATABASE_SCHEMA_NEWER_THAN_RUNTIME")
        applied: list[int] = []
        for version in range(before + 1, target_version + 1):
            statements = MIGRATIONS.get(version)
            if statements is None:
                raise RuntimeError(f"MISSING_MIGRATION:{version}")
            for statement in statements:
                conn.execute(statement)
            conn.execute(
                """
                INSERT INTO runtime_schema_migrations(version, release_id)
                VALUES (%s, %s)
                """,
                (version, release_id.strip()),
            )
            applied.append(version)
        conn.execute("COMMIT")
        return MigrationResult(before, target_version, tuple(applied))
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        raise
    finally:
        conn.close()


def verify_postgres_schema(
    database_url: str,
    *,
    expected_version: int = CURRENT_RUNTIME_SCHEMA_VERSION,
    connect_factory: ConnectionFactory | None = None,
    verify_tables: bool = True,
) -> int:
    """Fail closed unless config, binary and database share one exact schema version."""
    _database_url(database_url)
    if expected_version != CURRENT_RUNTIME_SCHEMA_VERSION:
        raise RuntimeError(
            "RUNTIME_BINARY_SCHEMA_VERSION_MISMATCH:"
            f"{expected_version}!={CURRENT_RUNTIME_SCHEMA_VERSION}"
        )
    connect = connect_factory or _default_connect_factory(database_url)
    conn = connect()
    try:
        row = conn.execute(
            "SELECT COALESCE(MAX(version), 0) AS version FROM runtime_schema_migrations"
        ).fetchone()
        if row is None:
            raise RuntimeError("RUNTIME_SCHEMA_MIGRATIONS_MISSING")
        current = int(_scalar(row, "version"))
        if current != expected_version:
            raise RuntimeError(
                f"RUNTIME_SCHEMA_VERSION_MISMATCH:{current}!={expected_version}"
            )
        if verify_tables:
            for table in _REQUIRED_TABLES:
                row = conn.execute("SELECT to_regclass(%s) AS table_name", (table,)).fetchone()
                if row is None or not _scalar(row, "table_name"):
                    raise RuntimeError(f"RUNTIME_SCHEMA_TABLE_MISSING:{table}")
        return current
    finally:
        conn.close()


def main() -> None:
    database_url = os.environ.get("HAO_DATABASE_URL", "")
    release_id = os.environ.get("HAO_RELEASE_ID", "")
    raw_version = os.environ.get(
        "HAO_DATABASE_SCHEMA_VERSION", str(CURRENT_RUNTIME_SCHEMA_VERSION)
    )
    try:
        target_version = int(raw_version)
    except ValueError as exc:
        raise ValueError("INVALID_INTEGER_CONFIG:HAO_DATABASE_SCHEMA_VERSION") from exc
    run_postgres_migrations(
        database_url,
        target_version=target_version,
        release_id=release_id,
    )


if __name__ == "__main__":
    main()
