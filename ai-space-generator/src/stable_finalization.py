from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Callable, Iterator

from .production_execution import ProductionExecutionResult


ConnectionFactory = Callable[[], Any]


def _default_connect_factory(database_url: str) -> ConnectionFactory:
    def connect() -> Any:
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("PSYCOPG_REQUIRED_FOR_STABLE_FINALIZATION") from exc
        return psycopg.connect(database_url, autocommit=True, row_factory=dict_row)

    return connect


def _value(row: Any, key: str, index: int) -> Any:
    try:
        return row[key]
    except (TypeError, KeyError, IndexError):
        return row[index]


class PostgresFinalizationIssueStore:
    """Reserve one immutable attestation issuance time for each workflow.

    Completion signatures include `issued_at`. Without a durable reservation,
    a retry after the completion row commits but before the caller receives the
    response can mint a different signature for the same run and look like a
    conflict. This store makes retries/restarts reuse the original issuance time.
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
        self._connect_factory = connect_factory or _default_connect_factory(database_url)
        if initialize_schema:
            self.initialize_schema()

    @contextmanager
    def _transaction(self) -> Iterator[Any]:
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
        with self._transaction() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS finalization_issue_times (
                    workflow_id TEXT PRIMARY KEY,
                    issued_at TEXT NOT NULL
                )
                """
            )

    def reserve(self, workflow_id: str, issued_at: str) -> str:
        workflow_id = workflow_id.strip()
        issued_at = issued_at.strip()
        if not workflow_id or not issued_at:
            raise ValueError("WORKFLOW_ID_AND_ISSUED_AT_REQUIRED")
        with self._transaction() as conn:
            conn.execute(
                """
                INSERT INTO finalization_issue_times(workflow_id, issued_at)
                VALUES (%s, %s)
                ON CONFLICT (workflow_id) DO NOTHING
                """,
                (workflow_id, issued_at),
            )
            row = conn.execute(
                "SELECT issued_at FROM finalization_issue_times WHERE workflow_id = %s FOR UPDATE",
                (workflow_id,),
            ).fetchone()
        if row is None:
            raise RuntimeError("FINALIZATION_ISSUE_TIME_RESERVATION_LOST")
        return str(_value(row, "issued_at", 0))


class StableFinalizationProductionService:
    """Transparent decorator that makes ProductionExecutionService.finalize retry-safe."""

    def __init__(self, production: Any, issue_store: PostgresFinalizationIssueStore) -> None:
        self._production = production
        self._issue_store = issue_store

    async def submit(self, state: Any, request: Any) -> Any:
        return await self._production.submit(state, request)

    async def resume(self, workflow_id: str, *, operational_version: int) -> Any:
        return await self._production.resume(
            workflow_id, operational_version=operational_version
        )

    async def authorize(
        self,
        pending: Any,
        *,
        scope: str,
        approved: bool,
        reason: str = "",
    ) -> None:
        await self._production.authorize(
            pending,
            scope=scope,
            approved=approved,
            reason=reason,
        )

    async def current_state(self, pending: Any) -> Any:
        return await self._production.current_state(pending)

    async def finalize(self, pending: Any, *, issued_at: str) -> ProductionExecutionResult:
        workflow_id = str(pending.handle.workflow_id).strip()
        stable_issued_at = self._issue_store.reserve(workflow_id, issued_at)
        return await self._production.finalize(
            pending,
            issued_at=stable_issued_at,
        )

    async def execute(
        self,
        state: Any,
        request: Any,
        *,
        issued_at: str,
    ) -> ProductionExecutionResult:
        submission = await self.submit(state, request)
        if not submission.accepted or submission.pending is None:
            return ProductionExecutionResult(
                submission.record,
                False,
                submission.code,
            )
        return await self.finalize(submission.pending, issued_at=issued_at)
