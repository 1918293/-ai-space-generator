from __future__ import annotations

from contextlib import contextmanager
from hashlib import sha256
import json
from typing import Any, Callable, Iterator

from .controlled_runner import ToolOutcome
from .execution_control import ActionProposal, FailureStage
from .reconciliation import (
    ReconciliationCase,
    ReconciliationDisposition,
    ReconciliationEvidence,
    ReconciliationEvidenceKind,
    ReconciliationKind,
    ReconciliationPhase,
    validate_case,
)


ConnectionFactory = Callable[[], Any]
_TERMINAL_PHASES = frozenset(
    {ReconciliationPhase.RESOLVED, ReconciliationPhase.PERMANENT_UNRESOLVED}
)


def _default_connect_factory(database_url: str) -> ConnectionFactory:
    def connect() -> Any:
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:  # pragma: no cover - deployment dependency guard
            raise RuntimeError("PSYCOPG_REQUIRED_FOR_RECONCILIATION") from exc
        return psycopg.connect(database_url, autocommit=True, row_factory=dict_row)

    return connect


def _value(row: Any, key: str, index: int) -> Any:
    try:
        return row[key]
    except (TypeError, KeyError, IndexError):
        return row[index]


def _run_id(action_id: str) -> str:
    marker = ":A"
    if marker not in action_id:
        raise ValueError("RUNTIME_ACTION_ID_REQUIRED_FOR_RECONCILIATION")
    run_id = action_id.split(marker, 1)[0].strip()
    if not run_id:
        raise ValueError("RUN_ID_UNRESOLVED_FROM_ACTION")
    return run_id


def reconciliation_case_id(action_id: str) -> str:
    action_id = action_id.strip()
    if not action_id:
        raise ValueError("ACTION_ID_REQUIRED")
    return "RECON-" + sha256(action_id.encode("utf-8")).hexdigest()[:32]


def _evidence_json(evidence: tuple[ReconciliationEvidence, ...]) -> str:
    return json.dumps(
        [
            {
                "evidence_id": item.evidence_id,
                "kind": item.kind.value,
                "passed": bool(item.passed),
                "source": item.source,
            }
            for item in evidence
        ],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _parse_evidence(raw: str) -> tuple[ReconciliationEvidence, ...]:
    try:
        items = json.loads(raw or "[]")
    except json.JSONDecodeError as exc:
        raise ValueError("RECONCILIATION_EVIDENCE_JSON_INVALID") from exc
    if not isinstance(items, list):
        raise ValueError("RECONCILIATION_EVIDENCE_LIST_REQUIRED")
    result: list[ReconciliationEvidence] = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("RECONCILIATION_EVIDENCE_OBJECT_REQUIRED")
        result.append(
            ReconciliationEvidence(
                evidence_id=str(item.get("evidence_id", "")),
                kind=ReconciliationEvidenceKind(str(item.get("kind", ""))),
                passed=bool(item.get("passed", False)),
                source=str(item.get("source", "")),
            )
        )
    return tuple(result)


class PostgresReconciliationStore:
    """Durable reconciliation case store using the Runtime v2 Postgres boundary.

    One action may have at most one case. Opening an UNKNOWN_EFFECT case is
    deterministic and idempotent, so a restart or repeated status check cannot
    create duplicate incident records. Terminal cases are immutable at the store
    boundary so concurrent/stale reconcilers cannot reopen or overwrite them.
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
                """
            )

    @staticmethod
    def _case(row: Any) -> ReconciliationCase:
        disposition_raw = str(_value(row, "disposition", 7) or "").strip()
        return validate_case(
            ReconciliationCase(
                case_id=str(_value(row, "case_id", 0)),
                run_id=str(_value(row, "run_id", 1)),
                action_id=str(_value(row, "action_id", 2)),
                kind=ReconciliationKind(str(_value(row, "kind", 3))),
                effect_may_have_occurred=bool(_value(row, "effect_may_have_occurred", 4)),
                evidence=_parse_evidence(str(_value(row, "evidence_json", 5))),
                phase=ReconciliationPhase(str(_value(row, "phase", 6))),
                disposition=(
                    ReconciliationDisposition(disposition_raw) if disposition_raw else None
                ),
                resolution_code=str(_value(row, "resolution_code", 8) or ""),
            )
        )

    @staticmethod
    def _select_case_sql(*, for_update: bool = False) -> str:
        suffix = " FOR UPDATE" if for_update else ""
        return (
            "SELECT case_id, run_id, action_id, kind, effect_may_have_occurred, "
            "evidence_json, phase, disposition, resolution_code, trigger_error_code "
            "FROM reconciliation_cases WHERE case_id = %s" + suffix
        )

    def get(self, case_id: str) -> ReconciliationCase | None:
        with self._transaction() as conn:
            row = conn.execute(
                self._select_case_sql(),
                (case_id.strip(),),
            ).fetchone()
        return None if row is None else self._case(row)

    def get_by_action(self, action_id: str) -> ReconciliationCase | None:
        with self._transaction() as conn:
            row = conn.execute(
                """
                SELECT case_id, run_id, action_id, kind, effect_may_have_occurred,
                       evidence_json, phase, disposition, resolution_code, trigger_error_code
                FROM reconciliation_cases WHERE action_id = %s
                """,
                (action_id.strip(),),
            ).fetchone()
        return None if row is None else self._case(row)

    def open_unknown_effect(
        self,
        proposal: ActionProposal,
        *,
        error_code: str,
    ) -> ReconciliationCase:
        action_id = proposal.action_id.strip()
        run_id = _run_id(action_id)
        case_id = reconciliation_case_id(action_id)
        case = ReconciliationCase(
            case_id=case_id,
            run_id=run_id,
            action_id=action_id,
            kind=ReconciliationKind.UNKNOWN_EFFECT,
            effect_may_have_occurred=True,
        )
        validate_case(case)
        with self._transaction() as conn:
            conn.execute(
                """
                INSERT INTO reconciliation_cases(
                    case_id, run_id, action_id, kind, effect_may_have_occurred,
                    evidence_json, phase, disposition, resolution_code, trigger_error_code
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, '', '', %s)
                ON CONFLICT (action_id) DO NOTHING
                """,
                (
                    case.case_id,
                    case.run_id,
                    case.action_id,
                    case.kind.value,
                    int(case.effect_may_have_occurred),
                    _evidence_json(case.evidence),
                    case.phase.value,
                    error_code.strip(),
                ),
            )
            row = conn.execute(
                """
                SELECT case_id, run_id, action_id, kind, effect_may_have_occurred,
                       evidence_json, phase, disposition, resolution_code, trigger_error_code
                FROM reconciliation_cases WHERE action_id = %s FOR UPDATE
                """,
                (action_id,),
            ).fetchone()
        if row is None:
            raise RuntimeError("RECONCILIATION_CASE_OPEN_FAILED")
        existing = self._case(row)
        if existing.kind != ReconciliationKind.UNKNOWN_EFFECT:
            raise ValueError("RECONCILIATION_KIND_CONFLICT")
        return existing

    def save(self, case: ReconciliationCase) -> ReconciliationCase:
        case = validate_case(case)
        with self._transaction() as conn:
            row = conn.execute(
                self._select_case_sql(for_update=True),
                (case.case_id,),
            ).fetchone()
            if row is None:
                raise ValueError("RECONCILIATION_CASE_NOT_FOUND")
            existing = self._case(row)
            if existing.run_id != case.run_id or existing.action_id != case.action_id:
                raise ValueError("RECONCILIATION_CASE_IDENTITY_CONFLICT")

            if existing.phase in _TERMINAL_PHASES:
                if existing == case:
                    return existing
                raise ValueError("RECONCILIATION_CASE_TERMINAL")
            if (
                existing.phase == ReconciliationPhase.AWAITING_HAO
                and case.phase == ReconciliationPhase.OPEN
            ):
                raise ValueError("RECONCILIATION_PHASE_REGRESSION")

            cursor = conn.execute(
                """
                UPDATE reconciliation_cases
                SET kind = %s,
                    effect_may_have_occurred = %s,
                    evidence_json = %s,
                    phase = %s,
                    disposition = %s,
                    resolution_code = %s
                WHERE case_id = %s AND phase = %s
                """,
                (
                    case.kind.value,
                    int(case.effect_may_have_occurred),
                    _evidence_json(case.evidence),
                    case.phase.value,
                    case.disposition.value if case.disposition else "",
                    case.resolution_code,
                    case.case_id,
                    existing.phase.value,
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError("RECONCILIATION_SAVE_RACE")

        saved = self.get(case.case_id)
        if saved is None:
            raise RuntimeError("RECONCILIATION_CASE_SAVE_LOST")
        return saved


class ReconciliationAwareBroker:
    """Open a durable reconciliation case whenever broker outcome is ambiguous."""

    def __init__(self, broker: Any, store: PostgresReconciliationStore) -> None:
        self._broker = broker
        self._store = store

    async def execute(self, proposal: ActionProposal) -> ToolOutcome:
        outcome = await self._broker.execute(proposal)
        if outcome.success:
            return outcome
        if outcome.failure_stage != FailureStage.PERSISTENCE:
            return outcome
        if "EFFECT_UNKNOWN" not in outcome.error_code:
            return outcome
        try:
            self._store.open_unknown_effect(proposal, error_code=outcome.error_code)
        except Exception as exc:
            return ToolOutcome(
                False,
                error_code=f"RECONCILIATION_OPEN_FAILED:{type(exc).__name__}",
                failure_stage=FailureStage.PERSISTENCE,
            )
        return outcome
