from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Protocol

from .action_catalog import ActionCatalog, ActionResolution, ModelActionIntent, resolve_model_intent
from .execution_control import AuthorityStamp, ExecutionRecord
from .operational_state import (
    ActiveOperationalState,
    CommandActor,
    execution_record_from_operational_state,
)


@dataclass(frozen=True)
class TaskExecutionPolicy:
    """Trusted execution requirements resolved outside model output."""

    goal_valid: bool
    acceptance_criteria: tuple[str, ...]
    required_acceptance_gate_ids: tuple[str, ...] = ()
    hao_acceptance_required: bool = False
    authority_refs: tuple[str, ...] = ()
    authority_stamps: tuple[AuthorityStamp, ...] = ()
    required_action_authority_refs: tuple[str, ...] = ()
    required_action_tags: tuple[str, ...] = ()
    forbidden_action_tags: tuple[str, ...] = ()


class TaskPolicyProvider(Protocol):
    def resolve(self, state: ActiveOperationalState) -> TaskExecutionPolicy: ...


@dataclass(frozen=True)
class ModelIngressRequest:
    run_id: str
    sequence: int
    intent: ModelActionIntent


@dataclass(frozen=True)
class PreparedControlledAction:
    record: ExecutionRecord | None
    resolution: ActionResolution


class ControlPlaneGateway:
    """The model-facing action ingress boundary.

    The model supplies only `ModelActionIntent`. Mode/TASK come from durable
    operational state; acceptance, authority and action invariants come from a
    trusted policy provider; tool safety metadata comes from ActionCatalog.

    This gateway intentionally owns the post-model-intent seam. Pre-model
    Current/checkpoint binding is handled by `PreModelContextGateway` below so a
    model cannot begin reasoning on an unverified continuation context and then
    rely on action admission to repair that earlier referent error.
    """

    def __init__(self, catalog: ActionCatalog, policy_provider: TaskPolicyProvider) -> None:
        self._catalog = catalog
        self._policy_provider = policy_provider

    def prepare(
        self,
        state: ActiveOperationalState,
        request: ModelIngressRequest,
    ) -> PreparedControlledAction:
        if not request.run_id.strip():
            raise ValueError("RUN_ID_REQUIRED")

        policy = self._policy_provider.resolve(state)
        record = execution_record_from_operational_state(
            state,
            run_id=request.run_id,
            goal_valid=policy.goal_valid,
            acceptance_criteria=policy.acceptance_criteria,
            required_acceptance_gate_ids=policy.required_acceptance_gate_ids,
            hao_acceptance_required=policy.hao_acceptance_required,
            authority_refs=policy.authority_refs,
            authority_stamps=policy.authority_stamps,
            required_action_authority_refs=policy.required_action_authority_refs,
            required_action_tags=policy.required_action_tags,
            forbidden_action_tags=policy.forbidden_action_tags,
        )
        resolution = resolve_model_intent(
            record,
            request.intent,
            self._catalog,
            sequence=request.sequence,
        )
        if resolution.proposal is None:
            return PreparedControlledAction(None, resolution)
        return PreparedControlledAction(record, resolution)


_CHECKPOINT_CUE = re.compile(r"(?<![A-Z0-9_])R(\d+)(?![A-Z0-9_])", re.IGNORECASE)
_ALLOWED_REUSE_DISPOSITIONS = frozenset(
    {
        "REUSE",
        "CROSSWALK_DELTA",
        "REFERENCE_ONLY",
        "NO_REUSABLE_ASSET",
        "ADMIT",
    }
)


@dataclass(frozen=True)
class PreModelContextRequest:
    """Raw interaction-boundary input before any model call.

    Deliberately contains no receipt, Mode, TASK, Authority, or verification
    fields that a caller/model could self-assert.
    """

    user_text: str
    actor: CommandActor
    event_id: str = ""


@dataclass(frozen=True)
class PreModelContextResolution:
    """Trusted resolver output for the exact Current/checkpoint context."""

    checkpoint_id: str
    task: str
    operational_version: int
    authority_refs: tuple[str, ...]
    regression_refs: tuple[str, ...] = ()
    existing_work_lookup_complete: bool = False
    regression_lookup_complete: bool = False
    reuse_disposition: str = ""


class PreModelContextResolver(Protocol):
    def resolve(
        self,
        state: ActiveOperationalState,
        request: PreModelContextRequest,
        checkpoint_cue: str,
    ) -> PreModelContextResolution | None: ...


@dataclass(frozen=True)
class PreModelContextReceipt:
    checkpoint_id: str
    task: str
    operational_version: int
    authority_refs: tuple[str, ...]
    regression_refs: tuple[str, ...]
    reuse_disposition: str
    context_fingerprint: str


@dataclass(frozen=True)
class VerifiedModelInput:
    """Structured first-model input with trusted context separated from Hao text.

    A concrete Agents/Responses adapter must map `receipt` to its trusted model
    context/instructions channel and `user_text` to the user channel. Keeping
    these fields separate avoids reducing the boundary back to one spoofable
    concatenated prompt string.
    """

    receipt: PreModelContextReceipt
    user_text: str


@dataclass(frozen=True)
class PreModelAdmission:
    allowed: bool
    code: str
    receipt: PreModelContextReceipt | None = None
    model_input: VerifiedModelInput | None = None


@dataclass(frozen=True)
class PreModelInvocationResult:
    admission: PreModelAdmission
    model_output: object | None = None


class ModelBoundary(Protocol):
    def invoke(self, model_input: VerifiedModelInput) -> object: ...


def explicit_checkpoint_cue(text: str) -> str:
    """Return one normalized explicit `R<number>` cue or fail on ambiguity."""

    cues = {f"R{int(match)}" for match in _CHECKPOINT_CUE.findall(text or "")}
    if len(cues) > 1:
        raise ValueError("AMBIGUOUS_CHECKPOINT_CUE")
    return next(iter(cues), "")


def _normalized_refs(values: tuple[str, ...]) -> tuple[str, ...]:
    normalized = tuple(value.strip() for value in values if value.strip())
    if len(set(normalized)) != len(normalized):
        raise ValueError("DUPLICATE_PRE_MODEL_REFERENCE")
    return normalized


def _mint_pre_model_receipt(
    resolution: PreModelContextResolution,
) -> PreModelContextReceipt:
    authority_refs = _normalized_refs(resolution.authority_refs)
    regression_refs = _normalized_refs(resolution.regression_refs)
    payload = {
        "checkpoint_id": resolution.checkpoint_id.strip().upper(),
        "task": resolution.task.strip(),
        "operational_version": resolution.operational_version,
        "authority_refs": authority_refs,
        "regression_refs": regression_refs,
        "reuse_disposition": resolution.reuse_disposition.strip().upper(),
    }
    fingerprint = hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return PreModelContextReceipt(
        checkpoint_id=payload["checkpoint_id"],
        task=payload["task"],
        operational_version=payload["operational_version"],
        authority_refs=authority_refs,
        regression_refs=regression_refs,
        reuse_disposition=payload["reuse_disposition"],
        context_fingerprint=fingerprint,
    )


def _hydrate_model_input(
    receipt: PreModelContextReceipt,
    user_text: str,
) -> VerifiedModelInput:
    return VerifiedModelInput(receipt=receipt, user_text=user_text.strip())


class PreModelContextGateway:
    """Fail-closed context admission before the first model invocation.

    The resolver is trusted application/runtime code responsible for reading the
    canonical Current/checkpoint and completing bounded existing-work and
    regression lookup. The model cannot author the receipt because admission
    accepts only raw Hao input plus runtime-owned operational state.
    """

    def __init__(self, resolver: PreModelContextResolver) -> None:
        self._resolver = resolver

    def admit(
        self,
        state: ActiveOperationalState,
        request: PreModelContextRequest,
    ) -> PreModelAdmission:
        if request.actor != CommandActor.USER:
            return PreModelAdmission(False, "PRE_MODEL_USER_ACTOR_REQUIRED")
        if not request.user_text.strip():
            return PreModelAdmission(False, "PRE_MODEL_USER_TEXT_REQUIRED")

        try:
            checkpoint_cue = explicit_checkpoint_cue(request.user_text)
        except ValueError as exc:
            return PreModelAdmission(False, str(exc))

        resolution = self._resolver.resolve(state, request, checkpoint_cue)
        if resolution is None:
            return PreModelAdmission(False, "PRE_MODEL_CURRENT_UNRESOLVED")

        resolved_checkpoint = resolution.checkpoint_id.strip().upper()
        if checkpoint_cue and resolved_checkpoint != checkpoint_cue:
            return PreModelAdmission(False, "PRE_MODEL_CHECKPOINT_MISMATCH")
        if resolution.task.strip() != state.task:
            return PreModelAdmission(False, "PRE_MODEL_TASK_MISMATCH")
        if resolution.operational_version != state.version:
            return PreModelAdmission(False, "PRE_MODEL_STALE_OPERATIONAL_CONTEXT")

        try:
            authority_refs = _normalized_refs(resolution.authority_refs)
            _normalized_refs(resolution.regression_refs)
        except ValueError as exc:
            return PreModelAdmission(False, str(exc))
        if not authority_refs:
            return PreModelAdmission(False, "PRE_MODEL_AUTHORITY_REFS_REQUIRED")
        if not resolution.existing_work_lookup_complete:
            return PreModelAdmission(False, "PRE_MODEL_EXISTING_WORK_LOOKUP_REQUIRED")
        if not resolution.regression_lookup_complete:
            return PreModelAdmission(False, "PRE_MODEL_REGRESSION_LOOKUP_REQUIRED")

        reuse_disposition = resolution.reuse_disposition.strip().upper()
        if reuse_disposition not in _ALLOWED_REUSE_DISPOSITIONS:
            return PreModelAdmission(False, "PRE_MODEL_REUSE_DISPOSITION_INVALID")

        try:
            receipt = _mint_pre_model_receipt(resolution)
        except ValueError as exc:
            return PreModelAdmission(False, str(exc))
        return PreModelAdmission(
            True,
            "PRE_MODEL_CONTEXT_ADMITTED",
            receipt,
            _hydrate_model_input(receipt, request.user_text),
        )


def invoke_after_pre_model_admission(
    gateway: PreModelContextGateway,
    state: ActiveOperationalState,
    request: PreModelContextRequest,
    model: ModelBoundary,
) -> PreModelInvocationResult:
    """Only cross the first-model-call boundary after verified context admission."""

    admission = gateway.admit(state, request)
    if not admission.allowed:
        return PreModelInvocationResult(admission)
    if admission.model_input is None:
        return PreModelInvocationResult(
            PreModelAdmission(False, "PRE_MODEL_HYDRATION_MISSING")
        )
    return PreModelInvocationResult(admission, model.invoke(admission.model_input))
