from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .controlled_runner import (
    AuthorityPreflightOutcome,
    ToolOutcome,
    VerificationOutcome,
)
from .execution_control import (
    ActionArchetype,
    ActionProposal,
    EvidenceKind,
    EvidenceOrigin,
    EvidenceReceipt,
    ExecutionRecord,
    FailureStage,
)


@dataclass(frozen=True)
class DriveMutationCommand:
    """Trusted, application-owned command for one exact Drive mutation.

    The model never supplies this command directly. A trusted resolver binds the
    model's admitted ActionProposal to an exact target, authorization evidence,
    expected state and immutable provider payload.
    """

    action_id: str
    provider: str
    action_name: str
    target_ref: str
    expected_state_delta: str
    expected_state_digest: str
    authorization_scope: str
    authorization_ref: str
    authority_snapshot_fingerprint: str
    payload: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class DriveMutationReceipt:
    success: bool
    receipt_id: str = ""
    source: str = ""
    error_code: str = ""
    no_effect_confirmed: bool = False


@dataclass(frozen=True)
class DriveStateReadback:
    state_digest: str
    source: str
    matched: bool
    error_code: str = ""


@dataclass(frozen=True)
class DriveAuthorityReadback:
    current_fingerprint: str
    source: str
    passed: bool
    error_code: str = ""


class DriveCommandResolver(Protocol):
    def resolve(self, proposal: ActionProposal) -> DriveMutationCommand | None: ...


class AsyncDriveMutationClient(Protocol):
    async def preflight_authority(self, command: DriveMutationCommand) -> DriveAuthorityReadback: ...

    async def mutate(self, command: DriveMutationCommand) -> DriveMutationReceipt: ...

    async def readback(self, command: DriveMutationCommand) -> DriveStateReadback: ...


class DriveEffectUnknown(RuntimeError):
    """Raised only when a mutation may have occurred and must not auto-replay."""


def _command_error(proposal: ActionProposal, command: DriveMutationCommand | None) -> str:
    if command is None:
        return "DRIVE_TRUSTED_COMMAND_UNRESOLVED"
    if proposal.archetype not in {ActionArchetype.MUTATE, ActionArchetype.PUBLISH}:
        return "DRIVE_MUTATION_ARCHETYPE_REQUIRED"
    if command.action_id != proposal.action_id:
        return "DRIVE_ACTION_ID_MISMATCH"
    if command.provider != proposal.provider or command.action_name != proposal.action_name:
        return "DRIVE_PROVIDER_BINDING_MISMATCH"
    if command.expected_state_delta != proposal.expected_state_delta:
        return "DRIVE_EXPECTED_DELTA_MISMATCH"
    if command.authorization_scope != proposal.authorization_scope:
        return "DRIVE_AUTHORIZATION_SCOPE_MISMATCH"
    if not command.authorization_scope.strip() or not command.authorization_ref.strip():
        return "DRIVE_EXACT_AUTHORIZATION_REQUIRED"
    if command.authority_snapshot_fingerprint != proposal.authority_snapshot_fingerprint:
        return "DRIVE_AUTHORITY_SNAPSHOT_MISMATCH"
    if not command.target_ref.strip():
        return "DRIVE_TARGET_REF_REQUIRED"
    if not command.expected_state_digest.strip():
        return "DRIVE_EXPECTED_STATE_DIGEST_REQUIRED"
    return ""


def _resolve_command(
    resolver: DriveCommandResolver,
    proposal: ActionProposal,
) -> tuple[DriveMutationCommand | None, str]:
    command = resolver.resolve(proposal)
    return command, _command_error(proposal, command)


class ControlledGoogleDriveProvider:
    """Raw Drive mutation provider intended to run behind IdempotentAsyncBroker.

    A provider exception or an acknowledged failure that cannot prove no effect
    raises DriveEffectUnknown. The broker then records UNKNOWN_EFFECT and refuses
    automatic replay. A successful provider receipt still does not complete the
    task; exact resulting-state readback belongs to the verifier below.
    """

    def __init__(
        self,
        resolver: DriveCommandResolver,
        client: AsyncDriveMutationClient,
    ) -> None:
        self._resolver = resolver
        self._client = client

    async def execute(self, proposal: ActionProposal) -> ToolOutcome:
        command, error = _resolve_command(self._resolver, proposal)
        if error:
            return ToolOutcome(False, error_code=error, failure_stage=FailureStage.BINDING)
        assert command is not None

        try:
            receipt = await self._client.mutate(command)
        except Exception as exc:
            raise DriveEffectUnknown("DRIVE_MUTATION_EXCEPTION_EFFECT_UNKNOWN") from exc

        if not receipt.success:
            if receipt.no_effect_confirmed:
                return ToolOutcome(
                    False,
                    error_code=receipt.error_code or "DRIVE_MUTATION_FAILED_NO_EFFECT",
                    failure_stage=FailureStage.TOOL_EXECUTION,
                )
            raise DriveEffectUnknown(
                receipt.error_code or "DRIVE_MUTATION_FAILURE_EFFECT_UNKNOWN"
            )

        if not receipt.receipt_id.strip() or not receipt.source.strip():
            raise DriveEffectUnknown("DRIVE_MUTATION_SUCCESS_WITHOUT_RECEIPT_EFFECT_UNKNOWN")

        return ToolOutcome(True, receipt.receipt_id, receipt.source)


class GoogleDriveAuthorityGuard:
    """Fresh authority check immediately before the durable workflow mutates."""

    def __init__(self, resolver: DriveCommandResolver, client: AsyncDriveMutationClient) -> None:
        self._resolver = resolver
        self._client = client

    async def verify_current(self, proposal: ActionProposal) -> AuthorityPreflightOutcome:
        command, error = _resolve_command(self._resolver, proposal)
        if error:
            return AuthorityPreflightOutcome(False, error_code=error)
        assert command is not None
        try:
            readback = await self._client.preflight_authority(command)
        except Exception as exc:
            return AuthorityPreflightOutcome(
                False,
                error_code=f"DRIVE_AUTHORITY_READ_EXCEPTION:{type(exc).__name__}",
            )
        return AuthorityPreflightOutcome(
            readback.passed,
            current_fingerprint=readback.current_fingerprint,
            source=readback.source,
            error_code=readback.error_code,
        )


class GoogleDriveOutcomeVerifier:
    """Exact resulting-state readback for a previously acknowledged Drive effect."""

    def __init__(self, resolver: DriveCommandResolver, client: AsyncDriveMutationClient) -> None:
        self._resolver = resolver
        self._client = client

    async def verify(
        self,
        record: ExecutionRecord,
        proposal: ActionProposal,
        tool_outcome: ToolOutcome,
    ) -> VerificationOutcome:
        command, error = _resolve_command(self._resolver, proposal)
        if error:
            return VerificationOutcome(False, error_code=error, failure_stage=FailureStage.BINDING)
        assert command is not None
        if not tool_outcome.success or not tool_outcome.receipt_id.strip():
            return VerificationOutcome(
                False,
                error_code="DRIVE_VERIFICATION_REQUIRES_PROVIDER_RECEIPT",
                failure_stage=FailureStage.VERIFICATION,
            )

        try:
            readback = await self._client.readback(command)
        except Exception as exc:
            return VerificationOutcome(
                False,
                error_code=f"DRIVE_READBACK_EXCEPTION:{type(exc).__name__}",
                failure_stage=FailureStage.PERSISTENCE,
            )

        if not readback.source.strip():
            return VerificationOutcome(
                False,
                error_code="DRIVE_READBACK_SOURCE_REQUIRED",
                failure_stage=FailureStage.PERSISTENCE,
            )
        if not readback.matched or readback.state_digest != command.expected_state_digest:
            return VerificationOutcome(
                False,
                error_code=readback.error_code or "DRIVE_RESULTING_STATE_MISMATCH",
                failure_stage=FailureStage.PERSISTENCE,
            )

        scope = proposal.action_id
        return VerificationOutcome(
            True,
            receipts=(
                EvidenceReceipt(
                    evidence_id=f"DRIVE-READBACK:{tool_outcome.receipt_id}",
                    kind=EvidenceKind.STATE_READBACK,
                    passed=True,
                    source=readback.source,
                    claim_scope=scope,
                    origin=EvidenceOrigin.PROVIDER,
                ),
                EvidenceReceipt(
                    evidence_id=f"DRIVE-VERIFY:{tool_outcome.receipt_id}",
                    kind=EvidenceKind.VERIFICATION_PASS,
                    passed=True,
                    source=readback.source,
                    claim_scope=scope,
                    origin=EvidenceOrigin.VERIFIER,
                ),
                EvidenceReceipt(
                    evidence_id=f"DRIVE-GATE:{tool_outcome.receipt_id}",
                    kind=EvidenceKind.ACCEPTANCE_GATE_PASS,
                    passed=True,
                    source=readback.source,
                    claim_scope=scope,
                    origin=EvidenceOrigin.VERIFIER,
                    gate_id="DRIVE_EXPECTED_STATE_MATCH",
                ),
            ),
        )
