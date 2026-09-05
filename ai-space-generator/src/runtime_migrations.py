from __future__ import annotations

from typing import Any, Callable


ConnectionFactory = Callable[[], Any]
LATEST_RUNTIME_SCHEMA_VERSION = 1
_MIGRATION_LOCK_CLASS = 121315
_MIGRATION_LOCK_OBJECT = 2


_BASELINE_SCHEMA = (
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
    """
    CREATE TABLE IF NOT EXISTS finalization_issue_times (
        workflow_id TEXT PRIMARY KEY,
        issued_at TEXT NOT NULL
    )
    """,
)

_MIGRATIONS = ((1, "runtime-v2-baseline", _BASELINE_SCHEMA),)


def _default_connect_factory(database_url: str) -> ConnectionFactory:
    def connect() -> Any:
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("PSYCOPG_REQUIRED_FOR_RUNTIME_MIGRATIONS") from exc
        return psycopg.connect(database_url, autocommit=True, row_factory=dict_row)

    return connect


def _version(row: Any) -> int:
    try:
        return int(row["version"])
    except (TypeError, KeyError, IndexError):
        return int(row[0])


def apply_runtime_migrations(
    database_url: str,
    *,
    connect_factory: ConnectionFactory | None = None,
) -> tuple[int, ...]:
    """Apply Runtime v2 Postgres migrations under one transaction-scoped lock.

    This is intentionally an upgrade-only gate. A binary that sees a newer
    database schema refuses to start instead of guessing at compatibility.
    """
    if not database_url.startswith(("postgresql://", "postgres://")):
        raise ValueError("POSTGRES_DATABASE_URL_REQUIRED")
    factory = connect_factory or _default_connect_factory(database_url)
    conn = factory()
    applied_now: list[int] = []
    try:
        conn.execute("BEGIN ISOLATION LEVEL SERIALIZABLE")
        conn.execute(
            "SELECT pg_advisory_xact_lock(%s, %s)",
            (_MIGRATION_LOCK_CLASS, _MIGRATION_LOCK_OBJECT),
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS hao_runtime_schema_migrations (
                version INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        rows = conn.execute(
            "SELECT version FROM hao_runtime_schema_migrations ORDER BY version"
        ).fetchall()
        applied = {_version(row) for row in rows}
        if any(version > LATEST_RUNTIME_SCHEMA_VERSION for version in applied):
            raise RuntimeError("DATABASE_SCHEMA_NEWER_THAN_RUNTIME")

        for version, name, statements in _MIGRATIONS:
            if version in applied:
                continue
            for statement in statements:
                conn.execute(statement)
            conn.execute(
                "INSERT INTO hao_runtime_schema_migrations(version, name) VALUES (%s, %s)",
                (version, name),
            )
            applied_now.append(version)
        conn.execute("COMMIT")
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        raise
    finally:
        conn.close()
    return tuple(applied_now)
