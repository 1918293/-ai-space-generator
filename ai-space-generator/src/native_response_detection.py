from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ClientSurface(StrEnum):
    IOS = "ios"
    WEB = "web"
    WORK = "work"
    CONTROLLED_RUNTIME = "controlled_runtime"
    UNKNOWN = "unknown"


class ToolEffectState(StrEnum):
    NONE = "none"
    VERIFIED = "verified"
    UNKNOWN = "unknown"


class NativeErrorClass(StrEnum):
    NONE = "none"
    STOPPED_THINKING = "stopped_thinking"
    STREAM_INTERRUPTED = "stream_interrupted"
    TIMEOUT = "timeout"
    OTHER = "other"


class DetectionOutcome(StrEnum):
    COMPLETE_VERIFIED = "COMPLETE_VERIFIED"
    AMBIGUOUS_DELIVERY = "AMBIGUOUS_DELIVERY"
    EXECUTION_FAILED = "EXECUTION_FAILED"
    UNKNOWN_EFFECT = "UNKNOWN_EFFECT"
    # Reporting-only bounded positive label. The turn classifier must never
    # emit this as a fallback for unmatched or contradictory evidence.
    SCOPED_POSITIVE = "SCOPED_POSITIVE"


@dataclass(frozen=True)
class NativeContinuationEvidence:
    task_id: str
    mode: str
    client_surface: ClientSurface
    observed_at: str
    execution_started: bool
    tool_dispatch_observed: bool
    tool_effect_state: ToolEffectState
    readback_observed: bool
    terminal_message_observed: bool
    native_error_class: NativeErrorClass = NativeErrorClass.NONE
    conversation_id: str = ""
    public_status_snapshot: str = "unknown"
    evidence_refs: tuple[str, ...] = ()
    failure_signature: str = ""
    environment_fingerprint: str = ""
    provider_trace_available: bool = False


@dataclass(frozen=True)
class DiagnosticAdmission:
    allowed: bool
    code: str
    basis: tuple[str, ...] = ()


def classify_native_outcome(
    evidence: NativeContinuationEvidence,
    *,
    persistence_required: bool = False,
) -> DetectionOutcome:
    # Consequential effect ambiguity dominates delivery state. Never use a
    # missing terminal message as permission to replay an unknown side effect.
    if evidence.tool_effect_state == ToolEffectState.UNKNOWN:
        return DetectionOutcome.UNKNOWN_EFFECT

    # Persistence-required work is not verified until its required readback is
    # observed. If execution already started, fail closed rather than minting a
    # positive outcome from a terminal message alone.
    if persistence_required and evidence.execution_started and not evidence.readback_observed:
        return DetectionOutcome.UNKNOWN_EFFECT

    required_readback_ok = not persistence_required or evidence.readback_observed

    # Positive completion is whitelist-only: verified terminal delivery, no
    # native error signal, and every required readback satisfied.
    if (
        evidence.execution_started
        and evidence.terminal_message_observed
        and evidence.native_error_class == NativeErrorClass.NONE
        and required_readback_ok
    ):
        return DetectionOutcome.COMPLETE_VERIFIED

    # Once execution has started, every non-whitelisted terminal state is
    # ambiguous delivery. This includes missing terminal evidence, absent UI
    # error labels, and contradictory terminal+error observations.
    if evidence.execution_started:
        return DetectionOutcome.AMBIGUOUS_DELIVERY

    # A continuation that never demonstrably started is not positive evidence.
    return DetectionOutcome.EXECUTION_FAILED


def admit_diagnostic_retest(
    previous: NativeContinuationEvidence,
    proposed: NativeContinuationEvidence,
    *,
    natural_use_recurrence: bool = False,
) -> DiagnosticAdmission:
    if natural_use_recurrence:
        return DiagnosticAdmission(True, "NATURAL_USE_EVIDENCE_ADMITTED", ("natural_use_recurrence",))

    delta: list[str] = []
    if proposed.client_surface != previous.client_surface:
        delta.append("client_surface")
    if proposed.environment_fingerprint != previous.environment_fingerprint:
        delta.append("environment")
    if proposed.failure_signature != previous.failure_signature:
        delta.append("failure_signature")
    if proposed.provider_trace_available and not previous.provider_trace_available:
        delta.append("provider_trace")
    if proposed.public_status_snapshot != previous.public_status_snapshot:
        delta.append("public_status")
    if (
        previous.tool_effect_state == ToolEffectState.UNKNOWN
        and proposed.tool_effect_state != ToolEffectState.UNKNOWN
    ):
        delta.append("effect_reconciliation")

    if not delta:
        return DiagnosticAdmission(False, "NO_MATERIAL_DELTA_DO_NOT_RETEST")

    return DiagnosticAdmission(True, "MATERIAL_DELTA_DIAGNOSTIC_ADMITTED", tuple(delta))
