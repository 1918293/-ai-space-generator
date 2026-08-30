from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Iterable


class Mode(StrEnum):
    EXP = "EXP"
    FAM = "FAM"
    EXE = "EXE"
    INT = "INT"
    SYS = "SYS"
    UNSPECIFIED = "未指定"


class RunPhase(StrEnum):
    RESOLVED = "RESOLVED"
    ADMITTED = "ADMITTED"
    EXECUTING = "EXECUTING"
    OBSERVED = "OBSERVED"
    VERIFIED = "VERIFIED"
    COMMITTED = "COMMITTED"
    CLOSED = "CLOSED"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"
    UNSYNCED = "UNSYNCED"
    AWAITING_HAO = "AWAITING_HAO"


class ActionArchetype(StrEnum):
    READ = "READ"
    RETRIEVE = "RETRIEVE"
    ANALYZE = "ANALYZE"
    MUTATE = "MUTATE"
    PUBLISH = "PUBLISH"
    VERIFY = "VERIFY"
    RECOVER = "RECOVER"


class ActionExternality(StrEnum):
    READ_ONLY = "READ_ONLY"
    PRIVATE_REVERSIBLE = "PRIVATE_REVERSIBLE"
    PRIVATE_IRREVERSIBLE = "PRIVATE_IRREVERSIBLE"
    EXTERNAL_REVERSIBLE = "EXTERNAL_REVERSIBLE"
    EXTERNAL_IRREVERSIBLE = "EXTERNAL_IRREVERSIBLE"
    FINANCIAL_PERMISSION_OR_SECURITY = "FINANCIAL_PERMISSION_OR_SECURITY"


class FailureStage(StrEnum):
    INTENT = "INTENT"
    AUTHORITY = "AUTHORITY"
    PLAN = "PLAN"
    POLICY = "POLICY"
    ROUTING = "ROUTING"
    BINDING = "BINDING"
    TOOL_INPUT = "TOOL_INPUT"
    TOOL_EXECUTION = "TOOL_EXECUTION"
    TOOL_OUTPUT = "TOOL_OUTPUT"
    SEMANTIC = "SEMANTIC"
    MUTATION = "MUTATION"
    VERIFICATION = "VERIFICATION"
    COMPLETION = "COMPLETION"
    PERSISTENCE = "PERSISTENCE"
    PROJECTION = "PROJECTION"


class EvidenceKind(StrEnum):
    AUTHORITY_SNAPSHOT = "AUTHORITY_SNAPSHOT"
    TOOL_RECEIPT = "TOOL_RECEIPT"
    STATE_READBACK = "STATE_READBACK"
    VERIFICATION_PASS = "VERIFICATION_PASS"
    ACCEPTANCE_GATE_PASS = "ACCEPTANCE_GATE_PASS"
    HAO_ACCEPTANCE = "HAO_ACCEPTANCE"


class CompletionClaim(StrEnum):
    EXECUTED = "EXECUTED"
    PERSISTED = "PERSISTED"
    VERIFIED = "VERIFIED"
    ACCEPTED = "ACCEPTED"
    COMPLETED = "COMPLETED"


@dataclass(frozen=True)
class EvidenceReceipt:
    evidence_id: str
    kind: EvidenceKind
    passed: bool
    source: str
    claim_scope: str = ""


@dataclass(frozen=True)
class ActionProposal:
    action_id: str
    archetype: ActionArchetype
    externality: ActionExternality
    capability: str
    provider: str
    action_name: str
    expected_state_delta: str = ""
    required_authority_refs: tuple[str, ...] = ()
    authorization_scope: str = ""
    idempotency_key: str = ""
    rollback_available: bool = False


@dataclass(frozen=True)
class ExecutionRecord:
    run_id: str
    task: str
    mode: Mode
    goal_valid: bool
    acceptance_criteria: tuple[str, ...]
    authority_refs: tuple[str, ...] = ()
    phase: RunPhase = RunPhase.RESOLVED
    action: ActionProposal | None = None
    evidence: tuple[EvidenceReceipt, ...] = ()
    failure_stage: FailureStage | None = None
    failure_code: str = ""
    last_failure_mechanism: str = ""
    last_retry_basis: str = ""


@dataclass(frozen=True)
class ControlDecision:
    allowed: bool
    code: str
    failed_at: FailureStage | None = None
    requires_hao_authorization: bool = False


_ALLOWED_TRANSITIONS: dict[RunPhase, set[RunPhase]] = {
    RunPhase.RESOLVED: {RunPhase.ADMITTED, RunPhase.BLOCKED, RunPhase.AWAITING_HAO},
    RunPhase.ADMITTED: {RunPhase.EXECUTING, RunPhase.BLOCKED, RunPhase.FAILED},
    RunPhase.EXECUTING: {RunPhase.OBSERVED, RunPhase.FAILED},
    RunPhase.OBSERVED: {RunPhase.VERIFIED, RunPhase.FAILED, RunPhase.BLOCKED, RunPhase.UNSYNCED},
    RunPhase.VERIFIED: {RunPhase.COMMITTED, RunPhase.CLOSED, RunPhase.FAILED, RunPhase.UNSYNCED},
    RunPhase.COMMITTED: {RunPhase.CLOSED, RunPhase.FAILED, RunPhase.UNSYNCED},
    RunPhase.UNSYNCED: {RunPhase.RESOLVED, RunPhase.BLOCKED, RunPhase.AWAITING_HAO, RunPhase.FAILED},
    RunPhase.BLOCKED: {RunPhase.RESOLVED, RunPhase.AWAITING_HAO},
    RunPhase.AWAITING_HAO: {RunPhase.RESOLVED, RunPhase.ADMITTED, RunPhase.BLOCKED},
    RunPhase.FAILED: {RunPhase.RESOLVED, RunPhase.BLOCKED, RunPhase.AWAITING_HAO},
    RunPhase.CLOSED: set(),
}


_COMPLETION_FLOORS: dict[CompletionClaim, set[EvidenceKind]] = {
    CompletionClaim.EXECUTED: {EvidenceKind.TOOL_RECEIPT},
    CompletionClaim.PERSISTED: {
        EvidenceKind.TOOL_RECEIPT,
        EvidenceKind.STATE_READBACK,
        EvidenceKind.VERIFICATION_PASS,
    },
    CompletionClaim.VERIFIED: {EvidenceKind.VERIFICATION_PASS},
    CompletionClaim.ACCEPTED: {EvidenceKind.HAO_ACCEPTANCE},
    CompletionClaim.COMPLETED: {
        EvidenceKind.VERIFICATION_PASS,
        EvidenceKind.ACCEPTANCE_GATE_PASS,
    },
}


_EXPLICIT_HAO_AUTHORIZATION_REQUIRED = {
    ActionExternality.PRIVATE_IRREVERSIBLE,
    ActionExternality.EXTERNAL_REVERSIBLE,
    ActionExternality.EXTERNAL_IRREVERSIBLE,
    ActionExternality.FINANCIAL_PERMISSION_OR_SECURITY,
}


_PERSISTENCE_ACTIONS = {ActionArchetype.MUTATE, ActionArchetype.PUBLISH}


def _passing_evidence_kinds(receipts: Iterable[EvidenceReceipt]) -> set[EvidenceKind]:
    return {receipt.kind for receipt in receipts if receipt.passed}


def requires_hao_authorization(externality: ActionExternality) -> bool:
    return externality in _EXPLICIT_HAO_AUTHORIZATION_REQUIRED


def action_requires_persistence_evidence(proposal: ActionProposal | None) -> bool:
    return proposal is not None and proposal.archetype in _PERSISTENCE_ACTIONS


def validate_action_contract(proposal: ActionProposal) -> ControlDecision:
    if not proposal.action_id.strip():
        return ControlDecision(False, "MISSING_ACTION_ID", FailureStage.PLAN)
    if not proposal.capability.strip() or not proposal.provider.strip() or not proposal.action_name.strip():
        return ControlDecision(False, "INCOMPLETE_TOOL_BINDING", FailureStage.BINDING)

    if proposal.archetype in _PERSISTENCE_ACTIONS:
        if not proposal.expected_state_delta.strip():
            return ControlDecision(False, "MISSING_EXPECTED_STATE_DELTA", FailureStage.PLAN)
        if not proposal.idempotency_key.strip():
            return ControlDecision(False, "MISSING_IDEMPOTENCY_KEY", FailureStage.POLICY)

    if proposal.archetype == ActionArchetype.PUBLISH and proposal.externality == ActionExternality.READ_ONLY:
        return ControlDecision(False, "PUBLISH_CANNOT_BE_READ_ONLY", FailureStage.POLICY)

    if proposal.archetype in {ActionArchetype.READ, ActionArchetype.RETRIEVE} and proposal.externality not in {
        ActionExternality.READ_ONLY,
        ActionExternality.PRIVATE_REVERSIBLE,
    }:
        return ControlDecision(False, "READ_EXTERNALITY_MISMATCH", FailureStage.POLICY)

    return ControlDecision(True, "ACTION_CONTRACT_VALID")


def admit_action(
    record: ExecutionRecord,
    proposal: ActionProposal,
    *,
    hao_authorized_scopes: Iterable[str] = (),
) -> tuple[ExecutionRecord, ControlDecision]:
    if record.phase != RunPhase.RESOLVED:
        return record, ControlDecision(False, "RUN_NOT_RESOLVED", FailureStage.PLAN)
    if not record.goal_valid:
        blocked = replace(record, phase=RunPhase.BLOCKED, failure_stage=FailureStage.INTENT, failure_code="GOAL_INVALID")
        return blocked, ControlDecision(False, "GOAL_INVALID", FailureStage.INTENT)

    contract = validate_action_contract(proposal)
    if not contract.allowed:
        blocked = replace(record, phase=RunPhase.BLOCKED, action=proposal, failure_stage=contract.failed_at, failure_code=contract.code)
        return blocked, contract

    missing_authority = [ref for ref in proposal.required_authority_refs if ref not in record.authority_refs]
    if missing_authority:
        blocked = replace(record, phase=RunPhase.BLOCKED, action=proposal, failure_stage=FailureStage.AUTHORITY, failure_code="AUTHORITY_REF_UNRESOLVED")
        return blocked, ControlDecision(False, "AUTHORITY_REF_UNRESOLVED", FailureStage.AUTHORITY)

    if requires_hao_authorization(proposal.externality):
        allowed_scopes = {scope.strip() for scope in hao_authorized_scopes if scope.strip()}
        if not proposal.authorization_scope.strip() or proposal.authorization_scope not in allowed_scopes:
            waiting = replace(record, phase=RunPhase.AWAITING_HAO, action=proposal, failure_stage=FailureStage.POLICY, failure_code="HAO_AUTHORIZATION_REQUIRED")
            return waiting, ControlDecision(
                False,
                "HAO_AUTHORIZATION_REQUIRED",
                FailureStage.POLICY,
                requires_hao_authorization=True,
            )

    admitted = replace(record, phase=RunPhase.ADMITTED, action=proposal, failure_stage=None, failure_code="")
    return admitted, ControlDecision(True, "ADMITTED")


def transition(record: ExecutionRecord, target: RunPhase) -> ExecutionRecord:
    if target not in _ALLOWED_TRANSITIONS[record.phase]:
        raise ValueError(f"ILLEGAL_TRANSITION:{record.phase}->{target}")
    return replace(record, phase=target)


def add_evidence(record: ExecutionRecord, receipt: EvidenceReceipt) -> ExecutionRecord:
    if not receipt.evidence_id.strip() or not receipt.source.strip():
        raise ValueError("INVALID_EVIDENCE_RECEIPT")
    return replace(record, evidence=record.evidence + (receipt,))


def required_evidence(record: ExecutionRecord, claim: CompletionClaim) -> set[EvidenceKind]:
    required = set(_COMPLETION_FLOORS[claim])
    if claim == CompletionClaim.COMPLETED and action_requires_persistence_evidence(record.action):
        required.update(
            {
                EvidenceKind.TOOL_RECEIPT,
                EvidenceKind.STATE_READBACK,
                EvidenceKind.VERIFICATION_PASS,
            }
        )
    return required


def can_claim(record: ExecutionRecord, claim: CompletionClaim) -> ControlDecision:
    required = required_evidence(record, claim)
    present = _passing_evidence_kinds(record.evidence)
    missing = sorted(kind.value for kind in required - present)
    if missing:
        return ControlDecision(False, "MISSING_EVIDENCE:" + ",".join(missing), FailureStage.COMPLETION)
    return ControlDecision(True, f"CLAIM_ALLOWED:{claim}")


def close_run(record: ExecutionRecord) -> ExecutionRecord:
    if record.phase not in {RunPhase.VERIFIED, RunPhase.COMMITTED}:
        raise ValueError(f"CLOSE_REQUIRES_VERIFIED_STATE:{record.phase}")
    decision = can_claim(record, CompletionClaim.COMPLETED)
    if not decision.allowed:
        raise ValueError(decision.code)
    return replace(record, phase=RunPhase.CLOSED)


def block_run(record: ExecutionRecord, *, stage: FailureStage, code: str) -> ExecutionRecord:
    if RunPhase.BLOCKED not in _ALLOWED_TRANSITIONS[record.phase]:
        raise ValueError(f"BLOCK_NOT_ALLOWED_FROM:{record.phase}")
    return replace(record, phase=RunPhase.BLOCKED, failure_stage=stage, failure_code=code)


def mark_unsynced(
    record: ExecutionRecord,
    *,
    code: str,
    mechanism: str,
    stage: FailureStage = FailureStage.PERSISTENCE,
) -> ExecutionRecord:
    if RunPhase.UNSYNCED not in _ALLOWED_TRANSITIONS[record.phase]:
        raise ValueError(f"UNSYNCED_NOT_ALLOWED_FROM:{record.phase}")
    if not code.strip() or not mechanism.strip():
        raise ValueError("UNSYNCED_REQUIRES_CODE_AND_MECHANISM")
    return replace(
        record,
        phase=RunPhase.UNSYNCED,
        failure_stage=stage,
        failure_code=code,
        last_failure_mechanism=mechanism,
    )


def record_failure(
    record: ExecutionRecord,
    *,
    stage: FailureStage,
    code: str,
    mechanism: str,
    retry_basis: str = "",
) -> ExecutionRecord:
    if not code.strip() or not mechanism.strip():
        raise ValueError("FAILURE_REQUIRES_CODE_AND_MECHANISM")
    return replace(
        record,
        phase=RunPhase.FAILED,
        failure_stage=stage,
        failure_code=code,
        last_failure_mechanism=mechanism,
        last_retry_basis=retry_basis,
    )


def can_retry(
    record: ExecutionRecord,
    *,
    mechanism: str,
    material_delta: bool,
    retry_basis: str,
) -> ControlDecision:
    if record.phase not in {RunPhase.FAILED, RunPhase.UNSYNCED}:
        return ControlDecision(False, "RETRY_REQUIRES_FAILED_OR_UNSYNCED_STATE", FailureStage.PLAN)
    if not mechanism.strip():
        return ControlDecision(False, "MISSING_FAILURE_MECHANISM", FailureStage.PLAN)
    if mechanism == record.last_failure_mechanism and not material_delta:
        return ControlDecision(False, "NO_DELTA_RETRY_BLOCKED", record.failure_stage)
    if material_delta and not retry_basis.strip():
        return ControlDecision(False, "MATERIAL_DELTA_REQUIRES_BASIS", FailureStage.PLAN)
    return ControlDecision(True, "RETRY_ALLOWED")


def render_header(record: ExecutionRecord, *, date: str, time_with_offset: str) -> str:
    if not date.strip() or not time_with_offset.strip():
        raise ValueError("DATE_AND_TIME_REQUIRED")
    return (
        f"[MODE={record.mode.value}][TASK={record.task}]\n"
        f"[DATE={date}][TIME={time_with_offset}]"
    )
