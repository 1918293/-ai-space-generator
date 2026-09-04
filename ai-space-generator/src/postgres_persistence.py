from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Callable, Iterator

from .authoritative_completion import (
    CompletionAttestor,
    CompletionCommitResult,
    ExecutionAttestation,
)
from .execution_control import ExecutionRecord, Mode
from .idempotent_broker import IdempotencyRecord, IdempotencyState
from .operational_state import (
    ActiveOperationalState,
    CommandActor,
    OperationalCommand,
    OperationalUpdate,
    explicit_user_mode,
)


ConnectionFactory = Callable[[], Any]


def _value(row: Any, key: str, index: int) -> Any:
    try:
        return row[key]
    except (TypeError, KeyError, IndexError):
        return row[index]


def _default_connect_factory(database_url: str) -> ConnectionFactory:
    def connect() -> Any:
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:  # pragma: no cover - deployment dependency guard
            raise RuntimeError("PSYCOPG_REQUIRED_FOR_POSTGRES_RUNTIME") from exc
        return psycopg.connect(database_url, autocommit=True, row_factory=dict_row)

    return connect


class _PostgresRuntimeDatabase:
    """One Postgres database/transaction boundary for Runtime v2 durable state.

    Operational state, broker idempotency, and authoritative completion are
    intentionally initialized through one database owner. This avoids three
    unrelated production stores drifting away from the single Postgres contract
    required by RuntimeSettings.
    """

    def __init__(
        self,
        database_url: str,
        *,
        connect_factory: ConnectionFactory | None = None,
        initialize_schema: bool = True,
    ) -> None:
        if not database_url.startswith(("postgresql://", "postgres://")):
            raise ValueError("POSTGRES_DATABASE_URL_REQUIRED")
        self.database_url = database_url
        self._connect_factory = connect_factory or _default_connect_factory(database_url)
        if initialize_schema:
            self.initialize_schema()

    @contextmanager
    def transaction(self) -> Iterator[Any]:
        conn = self._connect_factory()
        try:
            conn.execute("BEGIN ISOLATION LEVEL SERIALIZABLE")
            yield conn
            conn.execute("COMMIT")
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
            raise
        finally:
            conn.close()

    def initialize_schema(self) -> None:
        with self.transaction() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS operational_state (
                    singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
                    mode TEXT NOT NULL,
                    task TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    last_event_id TEXT NOT NULL DEFAULT ''
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS operational_events (
                    event_id TEXT PRIMARY KEY,
                    actor TEXT NOT NULL,
                    command_text TEXT NOT NULL,
                    resulting_version INTEGER NOT NULL,
                    applied INTEGER NOT NULL,
                    code TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS idempotency_records (
                    key TEXT PRIMARY KEY,
                    action_fingerprint TEXT NOT NULL,
                    state TEXT NOT NULL,
                    receipt_id TEXT NOT NULL DEFAULT '',
                    source TEXT NOT NULL DEFAULT '',
                    error_code TEXT NOT NULL DEFAULT ''
                )
                """
            )
            conn.execute(
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
                """
            )


class PostgresOperationalStateStore:
    def __init__(self, database: _PostgresRuntimeDatabase) -> None:
        self._database = database

    @staticmethod
    def _state(row: Any) -> ActiveOperationalState:
        return ActiveOperationalState(
            Mode(_value(row, "mode", 0)),
            _value(row, "task", 1),
            int(_value(row, "version", 2)),
            _value(row, "last_event_id", 3),
        )

    def initialize(self, *, mode: Mode, task: str) -> ActiveOperationalState:
        task = task.strip()
        if not task:
            raise ValueError("TASK_REQUIRED")
        with self._database.transaction() as conn:
            conn.execute(
                """
                INSERT INTO operational_state(singleton, mode, task, version)
                VALUES (1, %s, %s, 1)
                ON CONFLICT (singleton) DO NOTHING
                """,
                (mode.value, task),
            )
            row = conn.execute(
                "SELECT mode, task, version, last_event_id FROM operational_state WHERE singleton = 1 FOR UPDATE"
            ).fetchone()
            if row is None:
                raise RuntimeError("OPERATIONAL_STATE_INITIALIZATION_FAILED")
            return self._state(row)

    def get(self) -> ActiveOperationalState:
        with self._database.transaction() as conn:
            row = conn.execute(
                "SELECT mode, task, version, last_event_id FROM operational_state WHERE singleton = 1"
            ).fetchone()
        if row is None:
            raise ValueError("OPERATIONAL_STATE_NOT_INITIALIZED")
        return self._state(row)

    def apply(self, command: OperationalCommand) -> OperationalUpdate:
        event_id = command.event_id.strip()
        if not event_id:
            raise ValueError("EVENT_ID_REQUIRED")

        with self._database.transaction() as conn:
            prior_event = conn.execute(
                "SELECT resulting_version, applied, code FROM operational_events WHERE event_id = %s",
                (event_id,),
            ).fetchone()
            if prior_event is not None:
                row = conn.execute(
                    "SELECT mode, task, version, last_event_id FROM operational_state WHERE singleton = 1 FOR UPDATE"
                ).fetchone()
                if row is None:
                    raise ValueError("OPERATIONAL_STATE_NOT_INITIALIZED")
                return OperationalUpdate(self._state(row), bool(_value(prior_event, "applied", 1)), "EVENT_ALREADY_APPLIED")

            row = conn.execute(
                "SELECT mode, task, version, last_event_id FROM operational_state WHERE singleton = 1 FOR UPDATE"
            ).fetchone()
            if row is None:
                raise ValueError("OPERATIONAL_STATE_NOT_INITIALIZED")
            current = self._state(row)
            if command.expected_version is not None and command.expected_version != current.version:
                return OperationalUpdate(current, False, "STALE_OPERATIONAL_STATE")

            next_mode = current.mode
            next_task = current.task
            code = "NO_OPERATIONAL_CHANGE"
            if command.actor == CommandActor.USER:
                requested = explicit_user_mode(command.text)
                if requested is not None:
                    next_mode = requested
                    code = "USER_MODE_COMMAND_APPLIED"
                if command.explicit_task.strip():
                    next_task = command.explicit_task.strip()
                    code = "USER_TASK_COMMAND_APPLIED" if requested is None else "USER_MODE_AND_TASK_APPLIED"
            elif command.explicit_task.strip():
                code = "NON_USER_TASK_CHANGE_IGNORED"

            changed = next_mode != current.mode or next_task != current.task
            next_version = current.version + 1 if changed else current.version
            if changed:
                cursor = conn.execute(
                    """
                    UPDATE operational_state
                    SET mode = %s, task = %s, version = %s, last_event_id = %s
                    WHERE singleton = 1 AND version = %s
                    """,
                    (next_mode.value, next_task, next_version, event_id, current.version),
                )
                if cursor.rowcount != 1:
                    raise RuntimeError("OPERATIONAL_STATE_CAS_FAILED")

            conn.execute(
                """
                INSERT INTO operational_events(
                    event_id, actor, command_text, resulting_version, applied, code
                ) VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (event_id, command.actor.value, command.text, next_version, int(changed), code),
            )
            return OperationalUpdate(
                ActiveOperationalState(
                    next_mode,
                    next_task,
                    next_version,
                    event_id if changed else current.last_event_id,
                ),
                changed,
                code,
            )


class PostgresIdempotencyStore:
    def __init__(self, database: _PostgresRuntimeDatabase) -> None:
        self._database = database

    @staticmethod
    def _record(row: Any) -> IdempotencyRecord:
        return IdempotencyRecord(
            key=_value(row, "key", 0),
            action_fingerprint=_value(row, "action_fingerprint", 1),
            state=IdempotencyState(_value(row, "state", 2)),
            receipt_id=_value(row, "receipt_id", 3),
            source=_value(row, "source", 4),
            error_code=_value(row, "error_code", 5),
        )

    def get(self, key: str) -> IdempotencyRecord | None:
        with self._database.transaction() as conn:
            row = conn.execute(
                "SELECT key, action_fingerprint, state, receipt_id, source, error_code FROM idempotency_records WHERE key = %s",
                (key,),
            ).fetchone()
        return None if row is None else self._record(row)

    def reserve(self, key: str, fingerprint: str) -> tuple[bool, IdempotencyRecord]:
        with self._database.transaction() as conn:
            cursor = conn.execute(
                """
                INSERT INTO idempotency_records(key, action_fingerprint, state)
                VALUES (%s, %s, %s)
                ON CONFLICT (key) DO NOTHING
                """,
                (key, fingerprint, IdempotencyState.RESERVED.value),
            )
            if cursor.rowcount == 1:
                return True, IdempotencyRecord(key, fingerprint, IdempotencyState.RESERVED)
            row = conn.execute(
                "SELECT key, action_fingerprint, state, receipt_id, source, error_code FROM idempotency_records WHERE key = %s FOR UPDATE",
                (key,),
            ).fetchone()
            if row is None:
                raise RuntimeError("IDEMPOTENCY_RESERVATION_LOST")
            return False, self._record(row)

    def set_state(
        self,
        key: str,
        fingerprint: str,
        state: IdempotencyState,
        *,
        receipt_id: str = "",
        source: str = "",
        error_code: str = "",
    ) -> None:
        with self._database.transaction() as conn:
            cursor = conn.execute(
                """
                UPDATE idempotency_records
                SET state = %s, receipt_id = %s, source = %s, error_code = %s
                WHERE key = %s AND action_fingerprint = %s
                """,
                (state.value, receipt_id, source, error_code, key, fingerprint),
            )
            if cursor.rowcount != 1:
                raise ValueError("IDEMPOTENCY_RECORD_CHANGED_OR_MISSING")


class PostgresAuthoritativeCompletionStore:
    def __init__(self, database: _PostgresRuntimeDatabase) -> None:
        self._database = database

    def commit(
        self,
        attestation: ExecutionAttestation,
        record: ExecutionRecord,
        *,
        operational_version: int,
        attestor: CompletionAttestor,
    ) -> CompletionCommitResult:
        if not attestor.verify(attestation, record, operational_version=operational_version):
            return CompletionCommitResult(False, "INVALID_CONTROL_PLANE_ATTESTATION")

        with self._database.transaction() as conn:
            existing = conn.execute(
                "SELECT signature FROM authoritative_completions WHERE run_id = %s FOR UPDATE",
                (attestation.run_id,),
            ).fetchone()
            if existing is not None:
                import hmac

                existing_signature = _value(existing, "signature", 0)
                if hmac.compare_digest(existing_signature, attestation.signature):
                    return CompletionCommitResult(True, "ATTESTATION_ALREADY_COMMITTED")
                return CompletionCommitResult(False, "AUTHORITATIVE_COMPLETION_CONFLICT")

            conn.execute(
                """
                INSERT INTO authoritative_completions(
                    run_id, action_id, task, mode, operational_version,
                    authority_snapshot_fingerprint, evidence_digest, issued_at, signature
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    attestation.run_id,
                    attestation.action_id,
                    attestation.task,
                    attestation.mode,
                    attestation.operational_version,
                    attestation.authority_snapshot_fingerprint,
                    attestation.evidence_digest,
                    attestation.issued_at,
                    attestation.signature,
                ),
            )
        return CompletionCommitResult(True, "AUTHORITATIVE_COMPLETION_COMMITTED")


@dataclass(frozen=True)
class PostgresPersistenceBundle:
    operational_state: PostgresOperationalStateStore
    idempotency: PostgresIdempotencyStore
    completion: PostgresAuthoritativeCompletionStore


def build_postgres_persistence(
    database_url: str,
    *,
    connect_factory: ConnectionFactory | None = None,
    initialize_schema: bool = True,
) -> PostgresPersistenceBundle:
    database = _PostgresRuntimeDatabase(
        database_url,
        connect_factory=connect_factory,
        initialize_schema=initialize_schema,
    )
    return PostgresPersistenceBundle(
        operational_state=PostgresOperationalStateStore(database),
        idempotency=PostgresIdempotencyStore(database),
        completion=PostgresAuthoritativeCompletionStore(database),
    )
