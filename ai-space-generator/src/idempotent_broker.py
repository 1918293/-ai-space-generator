from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
from hashlib import sha256
import json
import sqlite3
from typing import Callable, Protocol

from .controlled_runner import ToolOutcome
from .execution_control import (
    ActionArchetype,
    ActionProposal,
    FailureStage,
)


class IdempotencyState(StrEnum):
    RESERVED = "RESERVED"
    SUCCEEDED = "SUCCEEDED"
    FAILED_NO_EFFECT = "FAILED_NO_EFFECT"
    UNKNOWN_EFFECT = "UNKNOWN_EFFECT"


@dataclass(frozen=True)
class IdempotencyRecord:
    key: str
    action_fingerprint: str
    state: IdempotencyState
    receipt_id: str = ""
    source: str = ""
    error_code: str = ""


class AsyncRawProvider(Protocol):
    async def execute(self, proposal: ActionProposal) -> ToolOutcome: ...


class IdempotencyStore(Protocol):
    """Durable broker claim store independent of the backing database."""

    def get(self, key: str) -> IdempotencyRecord | None: ...

    def reserve(self, key: str, fingerprint: str) -> tuple[bool, IdempotencyRecord]: ...

    def release(self, key: str, fingerprint: str) -> None: ...

    def set_state(
        self,
        key: str,
        fingerprint: str,
        state: IdempotencyState,
        *,
        receipt_id: str = "",
        source: str = "",
        error_code: str = "",
    ) -> None: ...


def action_fingerprint(proposal: ActionProposal) -> str:
    payload = json.dumps(
        asdict(proposal),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return sha256(payload).hexdigest()


class SQLiteIdempotencyStore:
    """Small durable store for broker execution claims.

    SQLite is a concrete reference implementation for tests/single-node use. A
    production multi-worker deployment can replace it with a transactional
    database while preserving these semantics.
    """

    def __init__(self, path: str) -> None:
        self._path = path
        with self._connect() as conn:
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

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        return conn

    def get(self, key: str) -> IdempotencyRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM idempotency_records WHERE key = ?",
                (key,),
            ).fetchone()
        if row is None:
            return None
        return IdempotencyRecord(
            key=row["key"],
            action_fingerprint=row["action_fingerprint"],
            state=IdempotencyState(row["state"]),
            receipt_id=row["receipt_id"],
            source=row["source"],
            error_code=row["error_code"],
        )

    def reserve(self, key: str, fingerprint: str) -> tuple[bool, IdempotencyRecord]:
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM idempotency_records WHERE key = ?",
                (key,),
            ).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO idempotency_records(key, action_fingerprint, state) VALUES (?, ?, ?)",
                    (key, fingerprint, IdempotencyState.RESERVED.value),
                )
                conn.execute("COMMIT")
                return True, IdempotencyRecord(key, fingerprint, IdempotencyState.RESERVED)
            conn.execute("COMMIT")
            existing = IdempotencyRecord(
                key=row["key"],
                action_fingerprint=row["action_fingerprint"],
                state=IdempotencyState(row["state"]),
                receipt_id=row["receipt_id"],
                source=row["source"],
                error_code=row["error_code"],
            )
            return False, existing
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
        finally:
            conn.close()

    def release(self, key: str, fingerprint: str) -> None:
        with self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM idempotency_records "
                "WHERE key = ? AND action_fingerprint = ? AND state = ?",
                (key, fingerprint, IdempotencyState.RESERVED.value),
            )
            if cursor.rowcount != 1:
                raise ValueError("IDEMPOTENCY_RESERVATION_CHANGED_OR_MISSING")

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
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE idempotency_records
                SET state = ?, receipt_id = ?, source = ?, error_code = ?
                WHERE key = ? AND action_fingerprint = ?
                """,
                (state.value, receipt_id, source, error_code, key, fingerprint),
            )
            if cursor.rowcount != 1:
                raise ValueError("IDEMPOTENCY_RECORD_CHANGED_OR_MISSING")


class IdempotentAsyncBroker:
    """Broker wrapper that never repeats an ambiguous side effect.

    Contract for raw providers:
    - `success=True` means an effect/result exists and must include a receipt.
    - `success=False` means the provider can affirm no side effect occurred.
    - exceptions are treated conservatively as UNKNOWN_EFFECT.
    """

    def __init__(
        self,
        provider: AsyncRawProvider,
        store: IdempotencyStore,
        *,
        uniqueness_key_resolver: Callable[[ActionProposal], str] | None = None,
    ) -> None:
        self._provider = provider
        self._store = store
        self._uniqueness_key_resolver = uniqueness_key_resolver

    async def execute(self, proposal: ActionProposal) -> ToolOutcome:
        if proposal.archetype not in {ActionArchetype.MUTATE, ActionArchetype.PUBLISH}:
            return await self._provider.execute(proposal)

        key = proposal.idempotency_key.strip()
        if not key:
            return ToolOutcome(
                False,
                error_code="BROKER_REQUIRES_IDEMPOTENCY_KEY",
                failure_stage=FailureStage.POLICY,
            )

        fingerprint = action_fingerprint(proposal)
        reserved, existing = self._store.reserve(key, fingerprint)
        if not reserved:
            if existing.action_fingerprint != fingerprint:
                return ToolOutcome(
                    False,
                    error_code="IDEMPOTENCY_KEY_CONFLICT",
                    failure_stage=FailureStage.POLICY,
                )
            if existing.state == IdempotencyState.SUCCEEDED:
                return ToolOutcome(True, existing.receipt_id, existing.source)
            if existing.state in {IdempotencyState.RESERVED, IdempotencyState.UNKNOWN_EFFECT}:
                return ToolOutcome(
                    False,
                    error_code="IDEMPOTENCY_EFFECT_UNKNOWN",
                    failure_stage=FailureStage.PERSISTENCE,
                )
            return ToolOutcome(
                False,
                error_code=existing.error_code or "PREVIOUS_KNOWN_FAILURE_REQUIRES_NEW_CONTROL_DECISION",
                failure_stage=FailureStage.TOOL_EXECUTION,
            )

        uniqueness_key = ""
        uniqueness_reserved = False
        if self._uniqueness_key_resolver is not None:
            try:
                uniqueness_key = str(self._uniqueness_key_resolver(proposal) or "").strip()
            except Exception as exc:
                code = f"IDEMPOTENCY_UNIQUENESS_KEY_RESOLUTION_FAILED:{type(exc).__name__}"
                self._store.set_state(
                    key,
                    fingerprint,
                    IdempotencyState.FAILED_NO_EFFECT,
                    error_code=code,
                )
                return ToolOutcome(False, error_code=code, failure_stage=FailureStage.BINDING)

        if uniqueness_key and uniqueness_key != key:
            uniqueness_reserved, _ = self._store.reserve(uniqueness_key, fingerprint)
            if not uniqueness_reserved:
                code = "IDEMPOTENCY_UNIQUENESS_CONFLICT"
                self._store.set_state(
                    key,
                    fingerprint,
                    IdempotencyState.FAILED_NO_EFFECT,
                    error_code=code,
                )
                return ToolOutcome(False, error_code=code, failure_stage=FailureStage.POLICY)

        try:
            outcome = await self._provider.execute(proposal)
        except Exception:
            if uniqueness_reserved:
                self._store.set_state(
                    uniqueness_key,
                    fingerprint,
                    IdempotencyState.UNKNOWN_EFFECT,
                    error_code="PROVIDER_EXCEPTION_EFFECT_UNKNOWN",
                )
            self._store.set_state(
                key,
                fingerprint,
                IdempotencyState.UNKNOWN_EFFECT,
                error_code="PROVIDER_EXCEPTION_EFFECT_UNKNOWN",
            )
            return ToolOutcome(
                False,
                error_code="PROVIDER_EXCEPTION_EFFECT_UNKNOWN",
                failure_stage=FailureStage.PERSISTENCE,
            )

        if outcome.success:
            if not outcome.receipt_id.strip() or not outcome.source.strip():
                if uniqueness_reserved:
                    self._store.set_state(
                        uniqueness_key,
                        fingerprint,
                        IdempotencyState.UNKNOWN_EFFECT,
                        error_code="PROVIDER_SUCCESS_WITHOUT_RECEIPT_EFFECT_UNKNOWN",
                    )
                self._store.set_state(
                    key,
                    fingerprint,
                    IdempotencyState.UNKNOWN_EFFECT,
                    error_code="PROVIDER_SUCCESS_WITHOUT_RECEIPT_EFFECT_UNKNOWN",
                )
                return ToolOutcome(
                    False,
                    error_code="PROVIDER_SUCCESS_WITHOUT_RECEIPT_EFFECT_UNKNOWN",
                    failure_stage=FailureStage.PERSISTENCE,
                )
            if uniqueness_reserved:
                self._store.set_state(
                    uniqueness_key,
                    fingerprint,
                    IdempotencyState.SUCCEEDED,
                    receipt_id=outcome.receipt_id,
                    source=outcome.source,
                )
            self._store.set_state(
                key,
                fingerprint,
                IdempotencyState.SUCCEEDED,
                receipt_id=outcome.receipt_id,
                source=outcome.source,
            )
            return outcome

        if uniqueness_reserved:
            try:
                self._store.release(uniqueness_key, fingerprint)
            except Exception:
                code = "IDEMPOTENCY_UNIQUENESS_RELEASE_FAILED"
                self._store.set_state(
                    key,
                    fingerprint,
                    IdempotencyState.FAILED_NO_EFFECT,
                    error_code=code,
                )
                return ToolOutcome(False, error_code=code, failure_stage=FailureStage.PERSISTENCE)
        self._store.set_state(
            key,
            fingerprint,
            IdempotencyState.FAILED_NO_EFFECT,
            error_code=outcome.error_code or "PROVIDER_FAILED_NO_EFFECT",
        )
        return outcome
