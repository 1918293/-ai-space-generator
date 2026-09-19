from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
from typing import Awaitable, Callable, Protocol

from .execution_control import ActionProposal
from .google_drive_control import (
    AsyncDriveMutationClient,
    DriveCommandResolver,
    DriveStateReadback,
    resolve_drive_mutation_command,
)
from .idempotent_broker import IdempotencyRecord, IdempotencyState
from .reconciliation import (
    ReconciliationCase,
    ReconciliationEvidence,
    ReconciliationEvidenceKind,
    ReconciliationKind,
    ReconciliationPhase,
    add_reconciliation_evidence,
)


class ProviderEffectState(StrEnum):
    """Claim-facing effect classification projected from authoritative runtime state."""

    KNOWN_APPLIED = "KNOWN_APPLIED"
    KNOWN_NOT_APPLIED = "KNOWN_NOT_APPLIED"
    UNKNOWN_EFFECT = "UNKNOWN_EFFECT"
    UNSYNCED = "UNSYNCED"


def classify_idempotency_effect(
    record: IdempotencyRecord,
    *,
    unsynced: bool = False,
) -> ProviderEffectState:
    """Project broker persistence into the effect vocabulary used by reconciliation.

    This is intentionally a projection only; IdempotencyRecord remains the single
    durable broker truth. RESERVED is conservatively UNKNOWN_EFFECT after restart
    because the process may have crashed after dispatching the provider call.
    """

    if unsynced:
        return ProviderEffectState.UNSYNCED
    if record.state == IdempotencyState.SUCCEEDED:
        return ProviderEffectState.KNOWN_APPLIED
    if record.state == IdempotencyState.FAILED_NO_EFFECT:
        return ProviderEffectState.KNOWN_NOT_APPLIED
    return ProviderEffectState.UNKNOWN_EFFECT


@dataclass(frozen=True)
class ReadbackRetryPolicy:
    """Bounded retry policy for read-only reconciliation inspection only."""

    max_attempts: int = 3
    backoff_seconds: tuple[float, ...] = (0.05, 0.2)

    def __post_init__(self) -> None:
        if self.max_attempts < 1 or self.max_attempts > 3:
            raise ValueError("READBACK_RETRY_ATTEMPTS_OUT_OF_RANGE")
        if any(delay < 0 or delay > 30 for delay in self.backoff_seconds):
            raise ValueError("READBACK_RETRY_BACKOFF_OUT_OF_RANGE")


@dataclass(frozen=True)
class ProviderReconciliationInspection:
    case: ReconciliationCase
    effect_state: ProviderEffectState
    code: str
    attempts: int
    readback_source: str = ""
    observed_state_digest: str = ""


class ReconciliationCaseStore(Protocol):
    def get(self, case_id: str) -> ReconciliationCase | None: ...

    def save(self, case: ReconciliationCase) -> ReconciliationCase: ...


def _evidence_suffix(case: ReconciliationCase, readback: DriveStateReadback) -> str:
    material = "\x1f".join(
        (
            case.case_id,
            case.action_id,
            readback.source.strip(),
            readback.state_digest.strip(),
            "1" if readback.matched else "0",
            readback.error_code.strip(),
        )
    ).encode("utf-8")
    return sha256(material).hexdigest()[:24]


class TrustedDriveReconciliationInspector:
    """Populate a durable reconciliation case using only direct provider readback.

    The caller supplies the already-admitted ActionProposal identity, but cannot
    supply provider evidence. Target resolution is delegated to the same trusted
    DriveCommandResolver used by the controlled mutation path. This service never
    invokes mutate(), never auto-resolves the case, and never marks task completion.
    """

    def __init__(
        self,
        *,
        resolver: DriveCommandResolver,
        client: AsyncDriveMutationClient,
        store: ReconciliationCaseStore,
        retry_policy: ReadbackRetryPolicy = ReadbackRetryPolicy(),
        sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._resolver = resolver
        self._client = client
        self._store = store
        self._retry_policy = retry_policy
        self._sleeper = sleeper

    async def inspect(
        self,
        *,
        case_id: str,
        proposal: ActionProposal,
    ) -> ProviderReconciliationInspection:
        case = self._store.get(case_id.strip())
        if case is None:
            raise ValueError("RECONCILIATION_CASE_NOT_FOUND")
        if case.phase in {
            ReconciliationPhase.RESOLVED,
            ReconciliationPhase.PERMANENT_UNRESOLVED,
        }:
            raise ValueError("RECONCILIATION_CASE_TERMINAL")
        if case.action_id != proposal.action_id:
            raise ValueError("RECONCILIATION_ACTION_IDENTITY_MISMATCH")
        if case.kind not in {
            ReconciliationKind.UNKNOWN_EFFECT,
            ReconciliationKind.READBACK_MISMATCH,
            ReconciliationKind.UNSYNCED,
        }:
            raise ValueError("RECONCILIATION_KIND_NOT_PROVIDER_READBACK_ELIGIBLE")

        command, command_error = resolve_drive_mutation_command(self._resolver, proposal)
        if command_error:
            raise ValueError(command_error)
        assert command is not None

        attempts = 0
        readback: DriveStateReadback | None = None
        while attempts < self._retry_policy.max_attempts:
            attempts += 1
            try:
                current = await self._client.readback(command)
            except Exception as exc:
                if attempts >= self._retry_policy.max_attempts:
                    return ProviderReconciliationInspection(
                        case,
                        ProviderEffectState.UNKNOWN_EFFECT,
                        f"TRUSTED_READBACK_EXCEPTION:{type(exc).__name__}",
                        attempts,
                    )
                await self._backoff(attempts)
                continue

            # A valid direct state observation is terminal for this inspection.
            # An empty digest means no provider state was actually obtained, so a
            # bounded retry is safe because readback itself is non-mutating.
            if current.source.strip() and current.state_digest.strip():
                readback = current
                break
            if attempts >= self._retry_policy.max_attempts:
                return ProviderReconciliationInspection(
                    case,
                    ProviderEffectState.UNKNOWN_EFFECT,
                    current.error_code or "TRUSTED_READBACK_STATE_UNAVAILABLE",
                    attempts,
                    current.source,
                    current.state_digest,
                )
            await self._backoff(attempts)

        assert readback is not None
        suffix = _evidence_suffix(case, readback)
        updated = add_reconciliation_evidence(
            case,
            ReconciliationEvidence(
                evidence_id=f"PROVIDER-READBACK:{suffix}",
                kind=ReconciliationEvidenceKind.STATE_READBACK,
                passed=True,
                source=readback.source,
            ),
        )
        verified = bool(
            readback.matched
            and readback.state_digest == command.expected_state_digest
        )
        updated = add_reconciliation_evidence(
            updated,
            ReconciliationEvidence(
                evidence_id=f"RUNTIME-VERIFY:{suffix}",
                kind=ReconciliationEvidenceKind.VERIFICATION_PASS,
                passed=verified,
                source="runtime:trusted-drive-reconciliation",
            ),
        )
        saved = self._store.save(updated)
        if verified:
            return ProviderReconciliationInspection(
                saved,
                ProviderEffectState.KNOWN_APPLIED,
                "TRUSTED_DIRECT_READBACK_VERIFIED_APPLIED",
                attempts,
                readback.source,
                readback.state_digest,
            )
        return ProviderReconciliationInspection(
            saved,
            ProviderEffectState.UNSYNCED,
            readback.error_code or "TRUSTED_DIRECT_READBACK_MISMATCH",
            attempts,
            readback.source,
            readback.state_digest,
        )

    async def _backoff(self, attempts: int) -> None:
        if not self._retry_policy.backoff_seconds:
            return
        index = min(attempts - 1, len(self._retry_policy.backoff_seconds) - 1)
        await self._sleeper(self._retry_policy.backoff_seconds[index])
