from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import hmac
import json
import sqlite3
from typing import Mapping, Protocol

from .execution_control import CompletionClaim, ExecutionRecord, RunPhase, can_claim


@dataclass(frozen=True)
class ExecutionAttestation:
    run_id: str
    action_id: str
    task: str
    mode: str
    operational_version: int
    authority_snapshot_fingerprint: str
    evidence_digest: str
    issued_at: str
    signature: str
    key_id: str = ""


def evidence_digest(record: ExecutionRecord) -> str:
    rows = [
        {
            "evidence_id": item.evidence_id,
            "kind": item.kind.value,
            "passed": item.passed,
            "source": item.source,
            "claim_scope": item.claim_scope,
            "origin": item.origin.value,
            "gate_id": item.gate_id,
        }
        for item in sorted(record.evidence, key=lambda item: item.evidence_id)
    ]
    payload = json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(payload.encode("utf-8")).hexdigest()


def _payload_dict(
    record: ExecutionRecord,
    *,
    operational_version: int,
    issued_at: str,
    key_id: str = "",
) -> dict[str, object]:
    if record.action is None:
        raise ValueError("ATTESTATION_REQUIRES_ACTION")
    payload: dict[str, object] = {
        "run_id": record.run_id,
        "action_id": record.action.action_id,
        "task": record.task,
        "mode": record.mode.value,
        "operational_version": operational_version,
        "authority_snapshot_fingerprint": record.action.authority_snapshot_fingerprint,
        "evidence_digest": evidence_digest(record),
        "issued_at": issued_at,
    }
    # Preserve verification compatibility for pre-key-id engineering receipts.
    # Production Runtime v2 always supplies a non-empty key id and therefore
    # cryptographically binds that identity into the signed payload.
    if key_id:
        payload["key_id"] = key_id
    return payload


def _canonical_bytes(payload: dict[str, object]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


class CompletionAttestor:
    """Runtime-only authority for minting and verifying completion receipts.

    `key_id` selects the current signing key. `verification_keys` may retain
    previous keys for verification-only use during bounded rotation windows.
    Removing a previous key makes receipts carrying that key id unverifiable by
    the new runtime, which is the explicit revocation behavior.
    """

    def __init__(
        self,
        secret: bytes,
        *,
        key_id: str = "",
        verification_keys: Mapping[str, bytes] | None = None,
    ) -> None:
        if len(secret) < 32:
            raise ValueError("ATTESTATION_SECRET_MIN_32_BYTES")
        self._current_key_id = key_id.strip()
        keys: dict[str, bytes] = {self._current_key_id: secret}
        for prior_key_id, prior_secret in (verification_keys or {}).items():
            prior_key_id = str(prior_key_id).strip()
            if not prior_key_id:
                raise ValueError("ATTESTATION_VERIFICATION_KEY_ID_REQUIRED")
            if len(prior_secret) < 32:
                raise ValueError("ATTESTATION_VERIFICATION_SECRET_MIN_32_BYTES")
            existing = keys.get(prior_key_id)
            if existing is not None and existing != prior_secret:
                raise ValueError("ATTESTATION_KEY_ID_CONFLICT")
            keys[prior_key_id] = prior_secret
        self._verification_keys = keys

    @property
    def current_key_id(self) -> str:
        return self._current_key_id

    def _sign_payload(self, payload: dict[str, object], secret: bytes) -> str:
        return hmac.new(secret, _canonical_bytes(payload), sha256).hexdigest()

    def issue(
        self,
        record: ExecutionRecord,
        *,
        operational_version: int,
        issued_at: str,
    ) -> ExecutionAttestation:
        if record.phase != RunPhase.CLOSED:
            raise ValueError("ATTESTATION_REQUIRES_CLOSED_STATE")
        if operational_version < 1:
            raise ValueError("OPERATIONAL_VERSION_REQUIRED")
        if not issued_at.strip():
            raise ValueError("ISSUED_AT_REQUIRED")
        completion = can_claim(record, CompletionClaim.COMPLETED)
        if not completion.allowed:
            raise ValueError("ATTESTATION_REQUIRES_COMPLETION_EVIDENCE:" + completion.code)

        payload = _payload_dict(
            record,
            operational_version=operational_version,
            issued_at=issued_at.strip(),
            key_id=self._current_key_id,
        )
        return ExecutionAttestation(
            **payload,
            signature=self._sign_payload(payload, self._verification_keys[self._current_key_id]),
        )

    def verify(
        self,
        attestation: ExecutionAttestation,
        record: ExecutionRecord,
        *,
        operational_version: int,
    ) -> bool:
        if record.phase != RunPhase.CLOSED or record.action is None:
            return False
        if not can_claim(record, CompletionClaim.COMPLETED).allowed:
            return False
        verification_secret = self._verification_keys.get(attestation.key_id)
        if verification_secret is None:
            return False
        expected_payload = _payload_dict(
            record,
            operational_version=operational_version,
            issued_at=attestation.issued_at,
            key_id=attestation.key_id,
        )
        supplied_payload = asdict(attestation)
        supplied_signature = supplied_payload.pop("signature")
        if not attestation.key_id:
            supplied_payload.pop("key_id", None)
        if supplied_payload != expected_payload:
            return False
        expected_signature = self._sign_payload(expected_payload, verification_secret)
        return hmac.compare_digest(supplied_signature, expected_signature)


@dataclass(frozen=True)
class CompletionCommitResult:
    committed: bool
    code: str


class AuthoritativeCompletionStore(Protocol):
    """Durable completion sink independent of the backing database."""

    def commit(
        self,
        attestation: ExecutionAttestation,
        record: ExecutionRecord,
        *,
        operational_version: int,
        attestor: CompletionAttestor,
    ) -> CompletionCommitResult: ...


class SQLiteAuthoritativeCompletionStore:
    """Reference sink for authoritative completion state.

    Only verified control-plane attestations can create rows. The store is
    idempotent for an identical attestation and fails closed on run-id conflicts.
    """

    def __init__(self, path: str) -> None:
        self._path = path
        with sqlite3.connect(self._path) as conn:
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
                    signature TEXT NOT NULL,
                    key_id TEXT NOT NULL DEFAULT ''
                )
                """
            )
            columns = {
                str(row[1])
                for row in conn.execute("PRAGMA table_info(authoritative_completions)")
            }
            if "key_id" not in columns:
                conn.execute(
                    "ALTER TABLE authoritative_completions "
                    "ADD COLUMN key_id TEXT NOT NULL DEFAULT ''"
                )

    def commit(
        self,
        attestation: ExecutionAttestation,
        record: ExecutionRecord,
        *,
        operational_version: int,
        attestor: CompletionAttestor,
    ) -> CompletionCommitResult:
        if not attestor.verify(
            attestation,
            record,
            operational_version=operational_version,
        ):
            return CompletionCommitResult(False, "INVALID_CONTROL_PLANE_ATTESTATION")

        with sqlite3.connect(self._path, isolation_level=None) as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT key_id, signature FROM authoritative_completions WHERE run_id = ?",
                (attestation.run_id,),
            ).fetchone()
            if existing is not None:
                existing_key_id, existing_signature = existing
                conn.execute("COMMIT")
                if (
                    existing_key_id == attestation.key_id
                    and hmac.compare_digest(existing_signature, attestation.signature)
                ):
                    return CompletionCommitResult(True, "ATTESTATION_ALREADY_COMMITTED")
                return CompletionCommitResult(False, "AUTHORITATIVE_COMPLETION_CONFLICT")

            conn.execute(
                """
                INSERT INTO authoritative_completions(
                    run_id, action_id, task, mode, operational_version,
                    authority_snapshot_fingerprint, evidence_digest, issued_at,
                    key_id, signature
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    attestation.key_id,
                    attestation.signature,
                ),
            )
            conn.execute("COMMIT")
        return CompletionCommitResult(True, "AUTHORITATIVE_COMPLETION_COMMITTED")
