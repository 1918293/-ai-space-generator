import sqlite3
from pathlib import Path

import pytest

from src.reconciliation import (
    ReconciliationCase,
    ReconciliationDisposition,
    ReconciliationEvidence,
    ReconciliationEvidenceKind,
    ReconciliationKind,
    add_reconciliation_evidence,
    apply_reconciliation,
)
from src.reconciliation_retry import (
    PostgresReconciliationRetryStore,
    retry_request_fingerprint,
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


def store(db: Path):
    return PostgresReconciliationRetryStore(
        "postgresql://runtime-v2/test",
        connect_factory=lambda: SqlitePostgresCompatConnection(db),
    )


def open_case():
    return ReconciliationCase(
        case_id="RECON-1",
        run_id="RUN-1",
        action_id="RUN-1:A0001:formal.intake.append",
        kind=ReconciliationKind.UNKNOWN_EFFECT,
        effect_may_have_occurred=True,
    )


def resolved_adopt_case():
    case = add_reconciliation_evidence(
        open_case(),
        ReconciliationEvidence(
            "RB-1",
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
            "runtime:trusted-drive-reconciliation",
        ),
    )
    return apply_reconciliation(case, ReconciliationDisposition.ADOPT_VERIFIED_STATE)


def test_duplicate_retry_request_reuses_stable_run_identity_after_restart(tmp_path):
    db = tmp_path / "retry.sqlite3"
    case = resolved_adopt_case()
    fingerprint = retry_request_fingerprint(
        case=case,
        expected_state_delta="write corrected row",
        authorization_target="Hao System Intake",
        arguments={"values_json": '[["corrected"]]'},
    )

    first_store = store(db)
    first = first_store.reserve(
        case=case,
        owner_ref="hao-sub",
        request_fingerprint=fingerprint,
    )
    assert first.created is True
    assert first.retry_run_id.startswith("RUN-RECON-")

    restarted_store = store(db)
    duplicate = restarted_store.reserve(
        case=case,
        owner_ref="hao-sub",
        request_fingerprint=fingerprint,
    )
    assert duplicate.created is False
    assert duplicate.retry_run_id == first.retry_run_id


def test_single_retry_owner_and_single_request_fingerprint_are_enforced(tmp_path):
    db = tmp_path / "retry.sqlite3"
    case = resolved_adopt_case()
    first_fingerprint = retry_request_fingerprint(
        case=case,
        expected_state_delta="write corrected row",
    )
    retry_store = store(db)
    retry_store.reserve(
        case=case,
        owner_ref="hao-sub",
        request_fingerprint=first_fingerprint,
    )

    with pytest.raises(PermissionError, match="RECONCILIATION_RETRY_OWNER_MISMATCH"):
        retry_store.reserve(
            case=case,
            owner_ref="other-subject",
            request_fingerprint=first_fingerprint,
        )

    second_fingerprint = retry_request_fingerprint(
        case=case,
        expected_state_delta="write a different correction",
    )
    with pytest.raises(PermissionError, match="RECONCILIATION_RETRY_ALREADY_RESERVED"):
        retry_store.reserve(
            case=case,
            owner_ref="hao-sub",
            request_fingerprint=second_fingerprint,
        )


def test_retry_reservation_rejects_unresolved_or_unsafe_disposition(tmp_path):
    retry_store = store(tmp_path / "retry.sqlite3")
    unresolved = open_case()
    fingerprint = retry_request_fingerprint(
        case=unresolved,
        expected_state_delta="changed delta",
    )
    with pytest.raises(PermissionError, match="RECONCILIATION_NOT_RETRY_SAFE"):
        retry_store.reserve(
            case=unresolved,
            owner_ref="hao-sub",
            request_fingerprint=fingerprint,
        )

    unsafe = ReconciliationCase(
        case_id="RECON-2",
        run_id="RUN-2",
        action_id="RUN-2:A0001:formal.intake.append",
        kind=ReconciliationKind.UNKNOWN_EFFECT,
        effect_may_have_occurred=True,
        phase=resolved_adopt_case().phase,
        disposition=ReconciliationDisposition.SUPERSEDE,
        resolution_code="STALE_ACTION_SUPERSEDED",
    )
    unsafe_fp = retry_request_fingerprint(
        case=unsafe,
        expected_state_delta="changed delta",
    )
    with pytest.raises(PermissionError, match="RECONCILIATION_DISPOSITION_NOT_RETRY_SAFE"):
        retry_store.reserve(
            case=unsafe,
            owner_ref="hao-sub",
            request_fingerprint=unsafe_fp,
        )


def test_retry_fingerprint_is_stable_and_sensitive_to_changed_delta():
    case = resolved_adopt_case()
    first = retry_request_fingerprint(
        case=case,
        expected_state_delta="delta-2",
        authorization_target="target",
        arguments={"b": "2", "a": "1"},
    )
    duplicate = retry_request_fingerprint(
        case=case,
        expected_state_delta="delta-2",
        authorization_target="target",
        arguments={"a": "1", "b": "2"},
    )
    changed = retry_request_fingerprint(
        case=case,
        expected_state_delta="delta-3",
        authorization_target="target",
        arguments={"a": "1", "b": "2"},
    )
    assert first == duplicate
    assert changed != first
