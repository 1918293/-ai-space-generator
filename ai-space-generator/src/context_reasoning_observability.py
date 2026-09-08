from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .context_bound_reasoning import ContextBoundReasoningIngress, ContextBoundReasoningResult
from .control_gateway import PreModelContextRequest
from .operational_state import ActiveOperationalState


@dataclass(frozen=True)
class ContextReasoningObservation:
    """Content-free stage evidence for one context-bound reasoning attempt.

    Counts/fingerprints/codes are deliberately separated from canonical cell
    content and Hao user text. `presented_to_model` is not called `used`.
    `model_reported_used_count` is a validated but non-authoritative model
    self-report and remains distinct from `deterministic_used_count`, which is
    only incremented where trusted Runtime logic itself consumed a disposition
    to stop/block an action.
    """

    run_id: str
    code: str
    structural_ref_count: int
    admitted_ref_count: int
    presented_to_model: bool
    model_reported_used_count: int
    deterministic_used_count: int
    action_selected: bool
    structural_fingerprint: str = ""
    semantic_fingerprint: str = ""


class ContextReasoningObservationSink(Protocol):
    def record(self, observation: ContextReasoningObservation) -> None: ...


def observation_from_result(
    result: ContextBoundReasoningResult,
    *,
    run_id: str,
) -> ContextReasoningObservation:
    model_input = result.admission.model_input
    structural_ref_count = 0
    admitted_ref_count = 0
    structural_fingerprint = ""
    semantic_fingerprint = ""
    model_reported_used_count = 0
    deterministic_used_count = 0

    if model_input is not None:
        receipt = model_input.receipt
        structural_ref_count = len(
            receipt.authority_refs
            + receipt.existing_work_refs
            + receipt.prior_attempt_refs
            + receipt.regression_refs
        )
        admitted_ref_count = len(model_input.admitted_context)
        structural_fingerprint = receipt.context_fingerprint
        semantic_fingerprint = model_input.semantic_fingerprint

        if (
            result.intent is not None
            and not result.code.startswith("PRE_MODEL_REPORTED_USED_")
        ):
            model_reported_used_count = len(result.intent.model_reported_used_refs)

        if result.code == "PRE_MODEL_CONTEXT_NO_ACTION":
            deterministic_used_count = sum(
                1
                for item in model_input.admitted_context
                if item.disposition == "NO_ACTION" and not item.binding_id
            )
        elif result.code == "PRE_MODEL_KNOWN_FAILURE_REPEAT_BLOCKED" and result.intent is not None:
            binding_id = result.intent.binding_id.strip()
            deterministic_used_count = sum(
                1
                for item in model_input.admitted_context
                if item.binding_id == binding_id
                and item.disposition in {"DO_NOT_REPEAT", "BLOCK"}
            )

    presented_to_model = result.intent is not None or result.code.startswith(
        "PRE_MODEL_INTENT_INVALID:"
    )
    action_selected = bool(
        result.prepared is not None
        and result.prepared.resolution.proposal is not None
    )
    return ContextReasoningObservation(
        run_id=run_id.strip(),
        code=result.code,
        structural_ref_count=structural_ref_count,
        admitted_ref_count=admitted_ref_count,
        presented_to_model=presented_to_model,
        model_reported_used_count=model_reported_used_count,
        deterministic_used_count=deterministic_used_count,
        action_selected=action_selected,
        structural_fingerprint=structural_fingerprint,
        semantic_fingerprint=semantic_fingerprint,
    )


class ObservableContextBoundReasoningIngress:
    """Observe the existing ingress without changing action admission semantics."""

    def __init__(
        self,
        ingress: ContextBoundReasoningIngress,
        sink: ContextReasoningObservationSink,
    ) -> None:
        self._ingress = ingress
        self._sink = sink

    def prepare(
        self,
        state: ActiveOperationalState,
        request: PreModelContextRequest,
        *,
        run_id: str,
        sequence: int = 1,
    ) -> ContextBoundReasoningResult:
        result = self._ingress.prepare(
            state,
            request,
            run_id=run_id,
            sequence=sequence,
        )
        self._sink.record(observation_from_result(result, run_id=run_id))
        return result