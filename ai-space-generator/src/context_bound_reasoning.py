from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Protocol

from .action_catalog import ModelActionIntent
from .control_gateway import (
    ControlPlaneGateway,
    ModelIngressRequest,
    PreModelContextGateway,
    PreModelContextReceipt,
    PreModelContextRequest,
    PreparedControlledAction,
)
from .operational_state import ActiveOperationalState


_ALLOWED_CONTEXT_KINDS = frozenset(
    {
        "CURRENT_CONTROL",
        "CONTINUATION",
        "EXISTING_WORK",
        "PRIOR_ATTEMPT",
        "REGRESSION",
    }
)
_ALLOWED_APPLICABILITY = frozenset({"APPLICABLE", "REFERENCE_ONLY"})
_ALLOWED_DISPOSITIONS = frozenset(
    {
        "APPLY",
        "REUSE",
        "REFERENCE_ONLY",
        "DO_NOT_REPEAT",
        "NO_ACTION",
        "BLOCK",
    }
)
_MAX_ITEM_SUMMARY_CHARS = 2000
_MAX_TOTAL_SUMMARY_CHARS = 12000


@dataclass(frozen=True)
class AdmittedContextItem:
    """One transient, source-bound semantic item admitted for the first model call.

    This object is not a persistence layer or Authority owner. It is a bounded
    runtime projection of already-resolved canonical evidence. `ref` must bind to
    the structural pre-model receipt; `source_version` records the source state
    inspected by the trusted resolver; `applicability` and `disposition` make the
    use/no-use decision explicit instead of treating retrieval as use.
    """

    ref: str
    kind: str
    summary: str
    source_version: str
    project_scope: str = ""
    applicability: str = "APPLICABLE"
    disposition: str = "APPLY"
    binding_id: str = ""


@dataclass(frozen=True)
class ContextBoundModelInput:
    receipt: PreModelContextReceipt
    admitted_context: tuple[AdmittedContextItem, ...]
    semantic_fingerprint: str
    user_text: str


@dataclass(frozen=True)
class ContextBoundAdmission:
    allowed: bool
    code: str
    model_input: ContextBoundModelInput | None = None


@dataclass(frozen=True)
class ContextBoundReasoningResult:
    admission: ContextBoundAdmission
    intent: ModelActionIntent | None = None
    prepared: PreparedControlledAction | None = None
    code: str = ""


class ContextSemanticsResolver(Protocol):
    """Resolve bounded model-usable semantics from already-selected canonical refs."""

    def resolve(
        self,
        state: ActiveOperationalState,
        request: PreModelContextRequest,
        receipt: PreModelContextReceipt,
    ) -> tuple[AdmittedContextItem, ...] | None: ...


class ContextBoundIntentModel(Protocol):
    """First-model boundary that may return only a non-authoritative action intent."""

    def invoke(self, model_input: ContextBoundModelInput) -> ModelActionIntent: ...


def _normalize_context_item(item: AdmittedContextItem) -> AdmittedContextItem:
    ref = item.ref.strip()
    kind = item.kind.strip().upper()
    summary = item.summary.strip()
    source_version = item.source_version.strip()
    project_scope = item.project_scope.strip()
    applicability = item.applicability.strip().upper()
    disposition = item.disposition.strip().upper()
    binding_id = item.binding_id.strip()

    if not ref:
        raise ValueError("PRE_MODEL_SEMANTIC_REF_REQUIRED")
    if kind not in _ALLOWED_CONTEXT_KINDS:
        raise ValueError("PRE_MODEL_SEMANTIC_KIND_INVALID")
    if not summary:
        raise ValueError("PRE_MODEL_SEMANTIC_SUMMARY_REQUIRED")
    if len(summary) > _MAX_ITEM_SUMMARY_CHARS:
        raise ValueError("PRE_MODEL_SEMANTIC_ITEM_TOO_LARGE")
    if not source_version:
        raise ValueError("PRE_MODEL_SEMANTIC_SOURCE_VERSION_REQUIRED")
    if applicability not in _ALLOWED_APPLICABILITY:
        if applicability == "STALE":
            raise ValueError("PRE_MODEL_SEMANTIC_STALE")
        if applicability == "OUT_OF_SCOPE":
            raise ValueError("PRE_MODEL_SEMANTIC_OUT_OF_SCOPE")
        if applicability == "SUPERSEDED":
            raise ValueError("PRE_MODEL_SEMANTIC_SUPERSEDED")
        raise ValueError("PRE_MODEL_SEMANTIC_APPLICABILITY_INVALID")
    if disposition not in _ALLOWED_DISPOSITIONS:
        raise ValueError("PRE_MODEL_SEMANTIC_DISPOSITION_INVALID")
    if disposition == "DO_NOT_REPEAT" and not binding_id:
        raise ValueError("PRE_MODEL_DO_NOT_REPEAT_BINDING_REQUIRED")

    return AdmittedContextItem(
        ref=ref,
        kind=kind,
        summary=summary,
        source_version=source_version,
        project_scope=project_scope,
        applicability=applicability,
        disposition=disposition,
        binding_id=binding_id,
    )


def _expected_kind_refs(receipt: PreModelContextReceipt) -> dict[str, frozenset[str]]:
    return {
        "CURRENT_CONTROL": frozenset(receipt.authority_refs),
        "CONTINUATION": frozenset(receipt.authority_refs),
        "EXISTING_WORK": frozenset(receipt.existing_work_refs),
        "PRIOR_ATTEMPT": frozenset(receipt.prior_attempt_refs),
        "REGRESSION": frozenset(receipt.regression_refs),
    }


def _normalize_context_items(
    receipt: PreModelContextReceipt,
    items: tuple[AdmittedContextItem, ...],
) -> tuple[AdmittedContextItem, ...]:
    normalized = tuple(_normalize_context_item(item) for item in items)
    if not normalized:
        raise ValueError("PRE_MODEL_SEMANTIC_CONTEXT_REQUIRED")

    total_summary_chars = sum(len(item.summary) for item in normalized)
    if total_summary_chars > _MAX_TOTAL_SUMMARY_CHARS:
        raise ValueError("PRE_MODEL_SEMANTIC_CONTEXT_TOO_LARGE")

    seen: set[tuple[str, str]] = set()
    allowed_by_kind = _expected_kind_refs(receipt)
    for item in normalized:
        identity = (item.kind, item.ref)
        if identity in seen:
            raise ValueError("DUPLICATE_PRE_MODEL_SEMANTIC_REFERENCE")
        seen.add(identity)
        if item.ref not in allowed_by_kind[item.kind]:
            raise ValueError("PRE_MODEL_SEMANTIC_REF_UNBOUND")

    # Structural lookup cannot count as semantic hydration. Every ref selected by
    # existing-work/prior-attempt/regression lookup must have a model-usable item.
    for kind, refs in (
        ("EXISTING_WORK", receipt.existing_work_refs),
        ("PRIOR_ATTEMPT", receipt.prior_attempt_refs),
        ("REGRESSION", receipt.regression_refs),
    ):
        represented = {item.ref for item in normalized if item.kind == kind}
        if set(refs) != represented:
            raise ValueError(f"PRE_MODEL_{kind}_SEMANTIC_COVERAGE_REQUIRED")

    # At least one Authority/Current semantic must be usable before reasoning.
    authority_semantics = {
        item.ref
        for item in normalized
        if item.kind in {"CURRENT_CONTROL", "CONTINUATION"}
    }
    if not authority_semantics:
        raise ValueError("PRE_MODEL_CURRENT_SEMANTIC_REQUIRED")
    if not authority_semantics.issubset(set(receipt.authority_refs)):
        raise ValueError("PRE_MODEL_CURRENT_SEMANTIC_REF_UNBOUND")

    return normalized


def semantic_context_fingerprint(
    receipt: PreModelContextReceipt,
    items: tuple[AdmittedContextItem, ...],
) -> str:
    payload = {
        "structural_context_fingerprint": receipt.context_fingerprint,
        "items": [
            {
                "ref": item.ref,
                "kind": item.kind,
                "summary": item.summary,
                "source_version": item.source_version,
                "project_scope": item.project_scope,
                "applicability": item.applicability,
                "disposition": item.disposition,
                "binding_id": item.binding_id,
            }
            for item in items
        ],
    }
    material = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + sha256(material).hexdigest()


class ContextBoundPreModelGateway:
    """Second-stage fail-closed semantic hydration after structural admission."""

    def __init__(
        self,
        structural_gateway: PreModelContextGateway,
        semantic_resolver: ContextSemanticsResolver,
    ) -> None:
        self._structural_gateway = structural_gateway
        self._semantic_resolver = semantic_resolver

    def admit(
        self,
        state: ActiveOperationalState,
        request: PreModelContextRequest,
    ) -> ContextBoundAdmission:
        structural = self._structural_gateway.admit(state, request)
        if not structural.allowed:
            return ContextBoundAdmission(False, structural.code)
        if structural.receipt is None:
            return ContextBoundAdmission(False, "PRE_MODEL_STRUCTURAL_RECEIPT_MISSING")

        raw_items = self._semantic_resolver.resolve(state, request, structural.receipt)
        if raw_items is None:
            return ContextBoundAdmission(False, "PRE_MODEL_SEMANTICS_UNRESOLVED")
        try:
            items = _normalize_context_items(structural.receipt, tuple(raw_items))
        except ValueError as exc:
            return ContextBoundAdmission(False, str(exc))

        fingerprint = semantic_context_fingerprint(structural.receipt, items)
        return ContextBoundAdmission(
            True,
            "PRE_MODEL_SEMANTIC_CONTEXT_ADMITTED",
            ContextBoundModelInput(
                receipt=structural.receipt,
                admitted_context=items,
                semantic_fingerprint=fingerprint,
                user_text=request.user_text.strip(),
            ),
        )


def _validate_model_reported_usage(
    model_input: ContextBoundModelInput,
    intent: ModelActionIntent,
) -> str:
    raw_refs = intent.model_reported_used_refs
    if not raw_refs:
        return "PRE_MODEL_REPORTED_USED_REFS_REQUIRED"

    admitted = {item.ref for item in model_input.admitted_context}
    seen: set[str] = set()
    for raw_ref in raw_refs:
        if not isinstance(raw_ref, str):
            return "PRE_MODEL_REPORTED_USED_REF_STRING_REQUIRED"
        ref = raw_ref.strip()
        if not ref or ref != raw_ref:
            return "PRE_MODEL_REPORTED_USED_REF_EXACT_REQUIRED"
        if ref in seen:
            return "PRE_MODEL_REPORTED_USED_REF_DUPLICATE:" + ref
        if ref not in admitted:
            return "PRE_MODEL_REPORTED_USED_REF_UNADMITTED:" + ref
        seen.add(ref)
    return ""


class ContextBoundReasoningIngress:
    """Connect raw Hao input to the existing post-model ControlPlaneGateway.

    No provider side effect happens here. The first model receives only admitted
    semantics and may propose only `ModelActionIntent`; the existing trusted
    ControlPlane remains responsible for binding, policy, Authority and action
    admission. A model-driven action must carry a bounded non-authoritative usage
    report whose refs are revalidated against the admitted semantic set before
    the ControlPlane is reached.
    """

    def __init__(
        self,
        *,
        pre_model: ContextBoundPreModelGateway,
        model: ContextBoundIntentModel,
        control_plane: ControlPlaneGateway,
    ) -> None:
        self._pre_model = pre_model
        self._model = model
        self._control_plane = control_plane

    def prepare(
        self,
        state: ActiveOperationalState,
        request: PreModelContextRequest,
        *,
        run_id: str,
        sequence: int = 1,
    ) -> ContextBoundReasoningResult:
        admission = self._pre_model.admit(state, request)
        if not admission.allowed or admission.model_input is None:
            return ContextBoundReasoningResult(
                admission=admission,
                code=admission.code,
            )

        # A trusted semantic no-action disposition stops before the first model
        # call; this is a real use outcome, not merely a stored note.
        if any(
            item.disposition == "NO_ACTION" and not item.binding_id
            for item in admission.model_input.admitted_context
        ):
            return ContextBoundReasoningResult(
                admission=admission,
                code="PRE_MODEL_CONTEXT_NO_ACTION",
            )

        try:
            intent = self._model.invoke(admission.model_input)
        except ValueError as exc:
            return ContextBoundReasoningResult(
                admission=admission,
                code=f"PRE_MODEL_INTENT_INVALID:{exc}",
            )

        usage_error = _validate_model_reported_usage(admission.model_input, intent)
        if usage_error:
            return ContextBoundReasoningResult(
                admission=admission,
                intent=intent,
                code=usage_error,
            )

        blocked_bindings = {
            item.binding_id
            for item in admission.model_input.admitted_context
            if item.disposition in {"DO_NOT_REPEAT", "BLOCK"} and item.binding_id
        }
        if intent.binding_id.strip() in blocked_bindings:
            return ContextBoundReasoningResult(
                admission=admission,
                intent=intent,
                code="PRE_MODEL_KNOWN_FAILURE_REPEAT_BLOCKED",
            )

        prepared = self._control_plane.prepare(
            state,
            ModelIngressRequest(
                run_id=run_id,
                sequence=sequence,
                intent=intent,
            ),
        )
        return ContextBoundReasoningResult(
            admission=admission,
            intent=intent,
            prepared=prepared,
            code=prepared.resolution.decision.code,
        )