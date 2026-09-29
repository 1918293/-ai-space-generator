from __future__ import annotations

from collections.abc import Mapping
from typing import Any


_ALLOWED_EXTERNALITIES = {
    "READ_ONLY",
    "PRIVATE_REVERSIBLE_WRITE",
    "EXTERNAL_OR_IRREVERSIBLE",
}

_CLAIM_EVIDENCE_FLOORS: dict[str, tuple[str, ...]] = {
    "EXECUTED": ("action_executed", "direct_output_observed"),
    "PERSISTED": ("action_executed", "readback_ok", "verification_pass"),
    "VERIFIED": ("direct_output_observed", "verification_pass"),
    "ACCEPTED": ("explicit_user_acceptance",),
}

_EVIDENCE_STAGE = {
    "action_executed": "EXECUTE",
    "direct_output_observed": "OBSERVE",
    "readback_ok": "VERIFY",
    "verification_pass": "VERIFY",
    "explicit_user_acceptance": "CLOSE",
}

_STAGE_ORDER = {
    "RESOLVE": 0,
    "ADMIT": 1,
    "EXECUTE": 2,
    "OBSERVE": 3,
    "VERIFY": 4,
    "CLOSE": 5,
}


def _clean(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _is_true(value: Any) -> bool:
    return value is True


def _first_stage(stages: list[str]) -> str:
    if not stages:
        return ""
    return min(stages, key=lambda stage: _STAGE_ORDER[stage])


def evaluate_execution_transition(
    action: Mapping[str, Any],
    *,
    claim: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate one bounded action/claim transition deterministically.

    This prototype intentionally does not infer policy from prose or provider
    names. The caller supplies an already-resolved action identity and
    externality classification. Unknown externality or claim types fail closed.

    Externality semantics:
    - READ_ONLY: no mutation authorization is required by this contract.
    - PRIVATE_REVERSIBLE_WRITE: requires current scope authorization; explicit
      authorization also satisfies that requirement.
    - EXTERNAL_OR_IRREVERSIBLE: requires explicit authorization.

    Completion evidence semantics:
    - EXECUTED requires actual execution plus directly observed output.
    - PERSISTED requires execution plus readback plus verification.
    - VERIFIED requires directly observed evidence plus verification pass.
    - ACCEPTED requires explicit user acceptance and does not imply technical
      verification of any other claim.

    Tool/API success is deliberately not an accepted evidence substitute.
    """
    action_name = _clean(action.get("name"))
    externality = _clean(action.get("externality"))
    scope_authorized = _is_true(action.get("scope_authorized"))
    explicit_authorization = _is_true(action.get("explicit_authorization"))

    claim_type = ""
    evidence: Mapping[str, Any] = {}
    if claim is not None:
        claim_type = _clean(claim.get("type"))
        raw_evidence = claim.get("evidence")
        if isinstance(raw_evidence, Mapping):
            evidence = raw_evidence

    issues: list[dict[str, Any]] = []
    failure_stages: list[str] = []

    if not action_name:
        issues.append({"code": "ACTION_IDENTITY_UNRESOLVED", "stage": "RESOLVE"})
        failure_stages.append("RESOLVE")

    if externality not in _ALLOWED_EXTERNALITIES:
        issues.append(
            {
                "code": "ACTION_EXTERNALITY_UNRESOLVED",
                "stage": "RESOLVE",
                "externality": externality,
            }
        )
        failure_stages.append("RESOLVE")

    if claim is not None and claim_type not in _CLAIM_EVIDENCE_FLOORS:
        issues.append(
            {
                "code": "COMPLETION_CLAIM_TYPE_UNRESOLVED",
                "stage": "RESOLVE",
                "claim_type": claim_type,
            }
        )
        failure_stages.append("RESOLVE")

    # Do not continue policy evaluation when the action/claim contract itself
    # cannot be resolved deterministically.
    if failure_stages:
        return {
            "schema_version": "0.1",
            "transition_pass": False,
            "failed_at": _first_stage(failure_stages),
            "action_name": action_name,
            "action_externality": externality,
            "claim_type": claim_type,
            "required_evidence": [],
            "missing_evidence": [],
            "issues": issues,
        }

    if externality == "PRIVATE_REVERSIBLE_WRITE" and not (
        scope_authorized or explicit_authorization
    ):
        issues.append(
            {
                "code": "ACTION_SCOPE_NOT_AUTHORIZED",
                "stage": "ADMIT",
                "externality": externality,
            }
        )
        failure_stages.append("ADMIT")

    if externality == "EXTERNAL_OR_IRREVERSIBLE" and not explicit_authorization:
        issues.append(
            {
                "code": "EXPLICIT_AUTHORIZATION_REQUIRED",
                "stage": "ADMIT",
                "externality": externality,
            }
        )
        failure_stages.append("ADMIT")

    required_evidence = list(_CLAIM_EVIDENCE_FLOORS.get(claim_type, ()))
    missing_evidence = [
        field for field in required_evidence if not _is_true(evidence.get(field))
    ]

    for field in missing_evidence:
        stage = _EVIDENCE_STAGE[field]
        issues.append(
            {
                "code": "COMPLETION_EVIDENCE_MISSING",
                "stage": stage,
                "evidence": field,
                "claim_type": claim_type,
            }
        )
        failure_stages.append(stage)

    return {
        "schema_version": "0.1",
        "transition_pass": not issues,
        "failed_at": _first_stage(failure_stages),
        "action_name": action_name,
        "action_externality": externality,
        "claim_type": claim_type,
        "required_evidence": required_evidence,
        "missing_evidence": missing_evidence,
        "issues": issues,
    }
