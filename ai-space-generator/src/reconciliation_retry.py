from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Any, Callable, Iterator, Mapping

from .reconciliation import (
    ReconciliationCase,
    ReconciliationDisposition,
    ReconciliationPhase,
)


ConnectionFactory = Callable[[], Any]


def _default_connect_factory(database_url: str) -> ConnectionFactory:
    def connect() -> Any:
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:  # pragma: no cover - deployment dependency guard
            raise RuntimeError("PSYCOPG_REQUIRED_FOR_RECONCILIATION_RETRY") from exc
        return psycopg.connect(database_url, autocommit=True, row_factory=dict_row)

    return connect


def _value(row: Any, key: str, index: int) -> Any:
    try:
        return row[key]
    except (TypeError, KeyError, IndexError):
        return row[index]


@dataclass(frozen=True)
class ReconciliationRetryReservation:
    case_id: str
    owner_ref: str
    request_fingerprint: str
    retry_run_id: str
    created: bool


def retry_request_fingerprint(
    *,
    case: ReconciliationCase,
    expected_state_delta: str,
    authorization_target: str = "",
    arguments: Mapping[str, str] | None = None,
) -> str:
    """Fingerprint one retry request without trusting provider identity from callers."""

    delta = expected_state_delta.strip()
    if not delta:
        raise ValueError("RECONCILIATION_RETRY_DELTA_REQUIRED")
    material = json.dumps(
        {
            "case_id": case.case_id,
            "action_id": case.action_id,
            "expected_state_delta": delta,
            "authorization_target": authorization_target.strip(),
            "arguments": dict(sorted((arguments or {}).items())),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(material).hexdigest()


class PostgresReconciliationRetryStore:
    """Single-owner durable retry reservation for one resolved reconciliation case.

    A duplicate request with exactly the same owner and fingerprint reuses the
    same retry_run_id after restart. A changed request cannot consume the same
    resolved case again. This store does not authorize retries; it only persists
    the stable identity after the existing control layer has established owner,
    disposition and changed-delta safety.
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
                CREATE TABLE IF NOT EXISTS reconciliation_retry_reservations (
                    case_id TEXT PRIMARY KEY,
                    owner_ref TEXT NOT NULL,
                    request_fingerprint TEXT NOT NULL,
                    retry_run_id TEXT NOT NULL UNIQUE
                )
                """
            )

    @staticmethod
    def _assert_retry_safe_case(case: ReconciliationCase) -> None:
        if case.phase != ReconciliationPhase.RESOLVED:
            raise PermissionError("RECONCILIATION_NOT_RETRY_SAFE")
        if case.disposition not in {
            ReconciliationDisposition.ADOPT_VERIFIED_STATE,
            ReconciliationDisposition.COMPENSATE_VERIFIED,
        }:
            raise PermissionError("RECONCILIATION_DISPOSITION_NOT_RETRY_SAFE")

    def reserve(
        self,
        *,
        case: ReconciliationCase,
        owner_ref: str,
        request_fingerprint: str,
    ) -> ReconciliationRetryReservation:
        self._assert_retry_safe_case(case)
        owner_ref = owner_ref.strip()
        request_fingerprint = request_fingerprint.strip()
        if not owner_ref or not request_fingerprint:
            raise ValueError("RECONCILIATION_RETRY_IDENTITY_REQUIRED")

        retry_run_id = "RUN-RECON-" + sha256(
            f"{case.case_id}\x1f{owner_ref}\x1f{request_fingerprint}".encode("utf-8")
        ).hexdigest()[:32]

        with self._transaction() as conn:
            cursor = conn.execute(
                """
                INSERT INTO reconciliation_retry_reservations(
                    case_id, owner_ref, request_fingerprint, retry_run_id
                ) VALUES (%s, %s, %s, %s)
                ON CONFLICT (case_id) DO NOTHING
                """,
                (case.case_id, owner_ref, request_fingerprint, retry_run_id),
            )
            created = cursor.rowcount == 1
            row = conn.execute(
                """
                SELECT owner_ref, request_fingerprint, retry_run_id
                FROM reconciliation_retry_reservations
                WHERE case_id = %s FOR UPDATE
                """,
                (case.case_id,),
            ).fetchone()
            if row is None:
                raise RuntimeError("RECONCILIATION_RETRY_RESERVATION_LOST")

            existing_owner = str(_value(row, "owner_ref", 0))
            existing_fingerprint = str(_value(row, "request_fingerprint", 1))
            existing_run_id = str(_value(row, "retry_run_id", 2))
            if existing_owner != owner_ref:
                raise PermissionError("RECONCILIATION_RETRY_OWNER_MISMATCH")
            if existing_fingerprint != request_fingerprint:
                raise PermissionError("RECONCILIATION_RETRY_ALREADY_RESERVED")
            if existing_run_id != retry_run_id:
                raise RuntimeError("RECONCILIATION_RETRY_IDENTITY_CONFLICT")

        return ReconciliationRetryReservation(
            case.case_id,
            owner_ref,
            request_fingerprint,
            retry_run_id,
            created,
        )
