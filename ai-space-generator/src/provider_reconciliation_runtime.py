from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
import asyncio
import json
import sqlite3
from typing import Any, Awaitable, Callable, Mapping, Protocol


class EffectState(StrEnum):
    KNOWN_APPLIED = "KNOWN_APPLIED"
    KNOWN_NOT_APPLIED = "KNOWN_NOT_APPLIED"
    UNKNOWN_EFFECT = "UNKNOWN_EFFECT"


class ReconciliationState(StrEnum):
    OPEN = "OPEN"
    VERIFIED_APPLIED = "VERIFIED_APPLIED"
    VERIFIED_NOT_APPLIED = "VERIFIED_NOT_APPLIED"
    PERMANENT_UNRESOLVED = "PERMANENT_UNRESOLVED"


@dataclass(frozen=True)
class ProviderBinding:
    binding_id: str
    provider: str
    target_ref: str


class ProviderBindingCatalog:
    def __init__(self, bindings: list[ProviderBinding]) -> None:
        self._bindings: dict[str, ProviderBinding] = {}
        for binding in bindings:
            key = binding.binding_id.strip()
            if not key or key in self._bindings or not binding.provider.strip() or not binding.target_ref.strip():
                raise ValueError("INVALID_OR_DUPLICATE_PROVIDER_BINDING")
            self._bindings[key] = ProviderBinding(key, binding.provider.strip(), binding.target_ref.strip())

    def resolve(self, binding_id: str) -> ProviderBinding:
        binding = self._bindings.get(binding_id.strip())
        if binding is None:
            raise PermissionError("PROVIDER_BINDING_NOT_CONFIGURED")
        return binding


@dataclass(frozen=True)
class MutationIntent:
    run_id: str
    owner_subject: str
    binding_id: str
    idempotency_key: str
    expected_state_delta: str
    payload: Mapping[str, Any]

    def fingerprint(self, binding: ProviderBinding) -> str:
        material = json.dumps(
            {
                "run_id": self.run_id,
                "owner_subject": self.owner_subject,
                "binding_id": binding.binding_id,
                "provider": binding.provider,
                "target_ref": binding.target_ref,
                "expected_state_delta": self.expected_state_delta,
                "payload": self.payload,
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            default=str,
        ).encode("utf-8")
        return sha256(material).hexdigest()


@dataclass(frozen=True)
class ProviderReceipt:
    success: bool
    receipt_id: str = ""
    source: str = ""
    no_effect_confirmed: bool = False
    retryable: bool = False
    error_code: str = ""


@dataclass(frozen=True)
class DirectReadback:
    matched: bool
    observed_digest: str
    source: str
    error_code: str = ""


class ControlledProvider(Protocol):
    async def mutate(self, binding: ProviderBinding, intent: MutationIntent) -> ProviderReceipt: ...
    async def readback(self, binding: ProviderBinding, intent: MutationIntent) -> DirectReadback: ...


@dataclass(frozen=True)
class ProviderExecution:
    run_id: str
    owner_subject: str
    effect_state: EffectState
    provider_receipt: str
    readback_source: str
    verification_passed: bool
    task_completed: bool
    code: str
    reconciliation_case_id: str = ""
    attempts: int = 0


@dataclass(frozen=True)
class ReconciliationCase:
    case_id: str
    run_id: str
    owner_subject: str
    binding_id: str
    idempotency_key: str
    expected_state_delta: str
    effect_state: EffectState
    state: ReconciliationState
    trusted_evidence_digest: str = ""
    resolution_code: str = ""


@dataclass(frozen=True)
class RetryPlan:
    prior_case_id: str
    new_run_id: str
    owner_subject: str
    binding_id: str
    new_idempotency_key: str
    new_expected_state_delta: str


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 2
    backoff_seconds: tuple[float, ...] = (0.0,)

    def __post_init__(self) -> None:
        if self.max_attempts < 1 or self.max_attempts > 3:
            raise ValueError("RETRY_ATTEMPTS_OUT_OF_RANGE")
        if any(value < 0 or value > 30 for value in self.backoff_seconds):
            raise ValueError("RETRY_BACKOFF_OUT_OF_RANGE")


class SQLiteProviderRuntimeStore:
    def __init__(self, path: str) -> None:
        self._path = path
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS effects (
                    idempotency_key TEXT PRIMARY KEY,
                    fingerprint TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    owner_subject TEXT NOT NULL,
                    state TEXT NOT NULL,
                    receipt_id TEXT NOT NULL DEFAULT '',
                    source TEXT NOT NULL DEFAULT '',
                    code TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS reconciliation_cases (
                    case_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    owner_subject TEXT NOT NULL,
                    binding_id TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    expected_state_delta TEXT NOT NULL,
                    effect_state TEXT NOT NULL,
                    state TEXT NOT NULL,
                    trusted_evidence_digest TEXT NOT NULL DEFAULT '',
                    resolution_code TEXT NOT NULL DEFAULT ''
                );
                """
            )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path)
        conn.row_factory = sqlite3.Row
        return conn

    def reserve_effect(self, intent: MutationIntent, fingerprint: str) -> tuple[bool, sqlite3.Row | None]:
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM effects WHERE idempotency_key = ?", (intent.idempotency_key,)).fetchone()
            if row is not None:
                conn.commit()
                return False, row
            conn.execute(
                "INSERT INTO effects(idempotency_key, fingerprint, run_id, owner_subject, state) VALUES (?, ?, ?, ?, ?)",
                (intent.idempotency_key, fingerprint, intent.run_id, intent.owner_subject, EffectState.UNKNOWN_EFFECT.value),
            )
            conn.commit()
            return True, None
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def update_effect(self, intent: MutationIntent, fingerprint: str, state: EffectState, *, receipt_id="", source="", code="") -> None:
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE effects SET state=?, receipt_id=?, source=?, code=? WHERE idempotency_key=? AND fingerprint=? AND owner_subject=?",
                (state.value, receipt_id, source, code, intent.idempotency_key, fingerprint, intent.owner_subject),
            )
            if cur.rowcount != 1:
                raise PermissionError("EFFECT_RESERVATION_OWNERSHIP_CHANGED")

    def get_effect(self, idempotency_key: str) -> sqlite3.Row | None:
        with self._connect() as conn:
            return conn.execute("SELECT * FROM effects WHERE idempotency_key = ?", (idempotency_key.strip(),)).fetchone()

    def create_case(self, case: ReconciliationCase) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO reconciliation_cases VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (case.case_id, case.run_id, case.owner_subject, case.binding_id, case.idempotency_key, case.expected_state_delta, case.effect_state.value, case.state.value, case.trusted_evidence_digest, case.resolution_code),
            )

    def get_case(self, case_id: str, owner_subject: str) -> ReconciliationCase:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM reconciliation_cases WHERE case_id = ?", (case_id.strip(),)).fetchone()
        if row is None:
            raise PermissionError("RECONCILIATION_CASE_NOT_FOUND")
        if row["owner_subject"] != owner_subject.strip():
            raise PermissionError("RECONCILIATION_OWNER_MISMATCH")
        return ReconciliationCase(
            row["case_id"], row["run_id"], row["owner_subject"], row["binding_id"], row["idempotency_key"], row["expected_state_delta"],
            EffectState(row["effect_state"]), ReconciliationState(row["state"]), row["trusted_evidence_digest"], row["resolution_code"]
        )

    def resolve_case(self, case_id: str, owner_subject: str, *, state: ReconciliationState, evidence_digest: str, code: str) -> ReconciliationCase:
        case = self.get_case(case_id, owner_subject)
        if case.state != ReconciliationState.OPEN:
            raise PermissionError("RECONCILIATION_CASE_TERMINAL")
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE reconciliation_cases SET state=?, trusted_evidence_digest=?, resolution_code=? WHERE case_id=? AND owner_subject=? AND state=?",
                (state.value, evidence_digest, code, case_id, owner_subject, ReconciliationState.OPEN.value),
            )
            if cur.rowcount != 1:
                raise PermissionError("RECONCILIATION_RESOLUTION_RACE")
        return self.get_case(case_id, owner_subject)


class ProviderReconciliationRuntime:
    def __init__(
        self,
        *,
        catalog: ProviderBindingCatalog,
        provider: ControlledProvider,
        store: SQLiteProviderRuntimeStore,
        retry_policy: RetryPolicy = RetryPolicy(),
        sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._catalog = catalog
        self._provider = provider
        self._store = store
        self._retry_policy = retry_policy
        self._sleeper = sleeper

    def _validate_intent(self, intent: MutationIntent) -> ProviderBinding:
        if any(not str(value).strip() for value in (intent.run_id, intent.owner_subject, intent.binding_id, intent.idempotency_key, intent.expected_state_delta)):
            raise ValueError("CONTROLLED_PROVIDER_INTENT_FIELDS_REQUIRED")
        return self._catalog.resolve(intent.binding_id)

    async def execute(self, intent: MutationIntent) -> ProviderExecution:
        binding = self._validate_intent(intent)
        fingerprint = intent.fingerprint(binding)
        reserved, existing = self._store.reserve_effect(intent, fingerprint)
        if not reserved:
            if existing["owner_subject"] != intent.owner_subject:
                raise PermissionError("IDEMPOTENCY_OWNER_MISMATCH")
            if existing["fingerprint"] != fingerprint:
                raise PermissionError("IDEMPOTENCY_KEY_CONFLICT")
            state = EffectState(existing["state"])
            if state == EffectState.KNOWN_APPLIED:
                return ProviderExecution(intent.run_id, intent.owner_subject, state, existing["receipt_id"], existing["source"], True, False, "IDEMPOTENT_KNOWN_APPLIED", attempts=0)
            if state == EffectState.KNOWN_NOT_APPLIED:
                return ProviderExecution(intent.run_id, intent.owner_subject, state, "", existing["source"], False, False, existing["code"] or "KNOWN_NOT_APPLIED_REQUIRES_NEW_CONTROL_DECISION", attempts=0)
            return self._unknown_execution(intent, binding, "IDEMPOTENCY_EFFECT_UNKNOWN", attempts=0)

        attempts = 0
        while True:
            attempts += 1
            try:
                receipt = await self._provider.mutate(binding, intent)
            except Exception as exc:
                code = f"PROVIDER_EXCEPTION_EFFECT_UNKNOWN:{type(exc).__name__}"
                self._store.update_effect(intent, fingerprint, EffectState.UNKNOWN_EFFECT, code=code)
                return self._unknown_execution(intent, binding, code, attempts=attempts)

            if receipt.success:
                if not receipt.receipt_id.strip() or not receipt.source.strip():
                    code = "PROVIDER_SUCCESS_WITHOUT_RECEIPT_EFFECT_UNKNOWN"
                    self._store.update_effect(intent, fingerprint, EffectState.UNKNOWN_EFFECT, code=code)
                    return self._unknown_execution(intent, binding, code, attempts=attempts)
                readback = await self._provider.readback(binding, intent)
                if not readback.matched:
                    code = readback.error_code or "DIRECT_READBACK_MISMATCH"
                    self._store.update_effect(intent, fingerprint, EffectState.UNKNOWN_EFFECT, receipt_id=receipt.receipt_id, source=readback.source, code=code)
                    return self._unknown_execution(intent, binding, code, attempts=attempts)
                self._store.update_effect(intent, fingerprint, EffectState.KNOWN_APPLIED, receipt_id=receipt.receipt_id, source=readback.source, code="PROVIDER_EFFECT_VERIFIED")
                return ProviderExecution(intent.run_id, intent.owner_subject, EffectState.KNOWN_APPLIED, receipt.receipt_id, readback.source, True, False, "PROVIDER_EFFECT_VERIFIED", attempts=attempts)

            if not receipt.no_effect_confirmed:
                code = receipt.error_code or "PROVIDER_FAILURE_EFFECT_UNKNOWN"
                self._store.update_effect(intent, fingerprint, EffectState.UNKNOWN_EFFECT, source=receipt.source, code=code)
                return self._unknown_execution(intent, binding, code, attempts=attempts)

            if receipt.retryable and attempts < self._retry_policy.max_attempts:
                delay_index = min(attempts - 1, len(self._retry_policy.backoff_seconds) - 1)
                delay = self._retry_policy.backoff_seconds[delay_index] if self._retry_policy.backoff_seconds else 0.0
                await self._sleeper(delay)
                continue

            code = receipt.error_code or "PROVIDER_KNOWN_NOT_APPLIED"
            self._store.update_effect(intent, fingerprint, EffectState.KNOWN_NOT_APPLIED, source=receipt.source, code=code)
            return ProviderExecution(intent.run_id, intent.owner_subject, EffectState.KNOWN_NOT_APPLIED, "", receipt.source, False, False, code, attempts=attempts)

    def _unknown_execution(self, intent: MutationIntent, binding: ProviderBinding, code: str, *, attempts: int) -> ProviderExecution:
        case_id = "RECON-" + sha256(f"{intent.run_id}:{intent.idempotency_key}:{binding.binding_id}".encode()).hexdigest()[:24]
        try:
            self._store.create_case(ReconciliationCase(case_id, intent.run_id, intent.owner_subject, binding.binding_id, intent.idempotency_key, intent.expected_state_delta, EffectState.UNKNOWN_EFFECT, ReconciliationState.OPEN))
        except sqlite3.IntegrityError:
            pass
        return ProviderExecution(intent.run_id, intent.owner_subject, EffectState.UNKNOWN_EFFECT, "", "", False, False, code, case_id, attempts)

    def inspect_reconciliation(self, case_id: str, owner_subject: str) -> ReconciliationCase:
        return self._store.get_case(case_id, owner_subject)

    async def reconcile_from_direct_provider_readback(self, case_id: str, owner_subject: str, intent: MutationIntent) -> ReconciliationCase:
        case = self._store.get_case(case_id, owner_subject)
        if case.run_id != intent.run_id or case.binding_id != intent.binding_id or case.idempotency_key != intent.idempotency_key:
            raise PermissionError("RECONCILIATION_INTENT_IDENTITY_MISMATCH")
        binding = self._catalog.resolve(case.binding_id)
        readback = await self._provider.readback(binding, intent)
        evidence_material = json.dumps({"source": readback.source, "digest": readback.observed_digest, "matched": readback.matched}, sort_keys=True).encode()
        evidence_digest = "sha256:" + sha256(evidence_material).hexdigest()
        if readback.matched:
            return self._store.resolve_case(case_id, owner_subject, state=ReconciliationState.VERIFIED_APPLIED, evidence_digest=evidence_digest, code="DIRECT_PROVIDER_READBACK_VERIFIED_APPLIED")
        return self._store.resolve_case(case_id, owner_subject, state=ReconciliationState.PERMANENT_UNRESOLVED, evidence_digest=evidence_digest, code=readback.error_code or "DIRECT_PROVIDER_READBACK_UNRESOLVED")

    def retry_with_changed_delta(self, case_id: str, owner_subject: str, *, new_run_id: str, new_idempotency_key: str, new_expected_state_delta: str) -> RetryPlan:
        case = self._store.get_case(case_id, owner_subject)
        if case.state not in {ReconciliationState.VERIFIED_APPLIED, ReconciliationState.VERIFIED_NOT_APPLIED}:
            raise PermissionError("RECONCILIATION_NOT_RETRY_SAFE")
        if not new_run_id.strip() or new_run_id == case.run_id:
            raise ValueError("NEW_CONTROLLED_RUN_REQUIRED")
        if not new_idempotency_key.strip() or new_idempotency_key == case.idempotency_key:
            raise ValueError("NEW_IDEMPOTENCY_KEY_REQUIRED")
        if not new_expected_state_delta.strip() or new_expected_state_delta == case.expected_state_delta:
            raise ValueError("CHANGED_EXPECTED_STATE_DELTA_REQUIRED")
        return RetryPlan(case.case_id, new_run_id.strip(), owner_subject.strip(), case.binding_id, new_idempotency_key.strip(), new_expected_state_delta.strip())
