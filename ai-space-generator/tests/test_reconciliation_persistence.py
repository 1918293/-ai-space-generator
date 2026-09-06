import asyncio
import sqlite3
from pathlib import Path

from src.controlled_runner import ToolOutcome
from src.execution_control import (
    ActionArchetype,
    ActionExternality,
    ActionProposal,
    FailureStage,
)
from src.reconciliation import (
    ReconciliationDisposition,
    ReconciliationEvidence,
    ReconciliationEvidenceKind,
    ReconciliationPhase,
    add_reconciliation_evidence,
    apply_reconciliation,
)
from src.reconciliation_persistence import (
    PostgresReconciliationStore,
    ReconciliationAwareBroker,
    reconciliation_case_id,
)


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


def store(tmp_path: Path):
    db = tmp_path / "reconciliation.sqlite3"
    return PostgresReconciliationStore(
        "postgresql://runtime-v2/test",
        connect_factory=lambda: SqlitePostgresCompatConnection(db),
    )


def proposal():
    return ActionProposal(
        action_id="RUN-REC:A0001:formal.intake.append",
        archetype=ActionArchetype.MUTATE,
        externality=ActionExternality.PRIVATE_REVERSIBLE,
        capability="formal_persistence",
        provider="google-drive",
        action_name="update_cells",
        expected_state_delta="write formal row",
        idempotency_key="RUN-REC:A0001:formal.intake.append",
    )


def test_unknown_effect_case_id_is_deterministic_and_open_is_idempotent(tmp_path):
    persistence = store(tmp_path)
    first = persistence.open_unknown_effect(proposal(), error_code="PROVIDER_EXCEPTION_EFFECT_UNKNOWN")
    second = persistence.open_unknown_effect(proposal(), error_code="IDEMPOTENCY_EFFECT_UNKNOWN")

    assert first.case_id == reconciliation_case_id(proposal().action_id)
    assert second.case_id == first.case_id
    assert second.run_id == "RUN-REC"
    assert second.action_id == proposal().action_id
    assert second.effect_may_have_occurred is True
    assert second.phase == ReconciliationPhase.OPEN


def test_resolved_reconciliation_case_survives_restart(tmp_path):
    db = tmp_path / "reconciliation.sqlite3"
    factory = lambda: SqlitePostgresCompatConnection(db)
    persistence = PostgresReconciliationStore(
        "postgresql://runtime-v2/test", connect_factory=factory
    )
    case = persistence.open_unknown_effect(proposal(), error_code="PROVIDER_EXCEPTION_EFFECT_UNKNOWN")
    case = add_reconciliation_evidence(
        case,
        ReconciliationEvidence(
            "READBACK-1",
            ReconciliationEvidenceKind.STATE_READBACK,
            True,
            "google-sheets:values.get",
        ),
    )
    case = add_reconciliation_evidence(
        case,
        ReconciliationEvidence(
            "VERIFY-1",
            ReconciliationEvidenceKind.VERIFICATION_PASS,
            True,
            "runtime-verifier",
        ),
    )
    case = apply_reconciliation(case, ReconciliationDisposition.ADOPT_VERIFIED_STATE)
    saved = persistence.save(case)

    restarted = PostgresReconciliationStore(
        "postgresql://runtime-v2/test", connect_factory=factory
    )
    restored = restarted.get(saved.case_id)
    assert restored == saved
    assert restored.phase == ReconciliationPhase.RESOLVED
    assert restored.disposition == ReconciliationDisposition.ADOPT_VERIFIED_STATE


class AmbiguousBroker:
    async def execute(self, proposal):
        return ToolOutcome(
            False,
            error_code="PROVIDER_EXCEPTION_EFFECT_UNKNOWN",
            failure_stage=FailureStage.PERSISTENCE,
        )


def test_reconciliation_aware_broker_opens_case_without_replaying_effect(tmp_path):
    persistence = store(tmp_path)
    broker = ReconciliationAwareBroker(AmbiguousBroker(), persistence)

    outcome = asyncio.run(broker.execute(proposal()))
    opened = persistence.get_by_action(proposal().action_id)

    assert outcome.success is False
    assert outcome.error_code == "PROVIDER_EXCEPTION_EFFECT_UNKNOWN"
    assert opened is not None
    assert opened.effect_may_have_occurred is True


class KnownNoEffectBroker:
    async def execute(self, proposal):
        return ToolOutcome(
            False,
            error_code="GOOGLE_SHEETS_REJECTED:403",
            failure_stage=FailureStage.TOOL_EXECUTION,
        )


def test_known_no_effect_failure_does_not_open_reconciliation_case(tmp_path):
    persistence = store(tmp_path)
    broker = ReconciliationAwareBroker(KnownNoEffectBroker(), persistence)

    outcome = asyncio.run(broker.execute(proposal()))
    assert outcome.success is False
    assert persistence.get_by_action(proposal().action_id) is None
