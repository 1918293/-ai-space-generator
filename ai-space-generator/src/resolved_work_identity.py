from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Iterable


@dataclass(frozen=True)
class CanonicalWorkIdentitySeed:
    """Authority-supplied semantic work identity fields.

    The Runtime does not infer these fields from raw TASK text, model output,
    embeddings, providers, or tools. They must already be resolved by the
    trusted Current/Intent authority path.
    """

    project_scope: str
    logical_target: str
    objective: str
    deliverable_class: str
    acceptance_identity: str


@dataclass(frozen=True)
class ResolvedWorkIdentityProjection:
    """Immutable semantic identity plus its exact Current binding.

    `semantic_fingerprint` identifies the work semantics. `current_fingerprint`
    identifies the verified Current snapshot. `binding_fingerprint` binds both
    without redefining either one as the other.
    """

    project_scope: str
    logical_target: str
    objective: str
    deliverable_class: str
    acceptance_identity: str
    semantic_fingerprint: str
    current_fingerprint: str
    binding_fingerprint: str

    def receipt_payload(self) -> dict[str, str]:
        return {
            "project_scope": self.project_scope,
            "logical_target": self.logical_target,
            "objective": self.objective,
            "deliverable_class": self.deliverable_class,
            "acceptance_identity": self.acceptance_identity,
            "semantic_fingerprint": self.semantic_fingerprint,
            "current_fingerprint": self.current_fingerprint,
            "binding_fingerprint": self.binding_fingerprint,
        }


def _required_text(value: str, code: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(code)
    return normalized


def _sha256_payload(payload: dict[str, object]) -> str:
    material = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(material).hexdigest()


def normalized_authority_refs(values: Iterable[str]) -> tuple[str, ...]:
    refs = tuple(sorted(value.strip() for value in values if value.strip()))
    if not refs:
        raise ValueError("WORK_IDENTITY_AUTHORITY_REFS_REQUIRED")
    if len(set(refs)) != len(refs):
        raise ValueError("WORK_IDENTITY_AUTHORITY_REFS_DUPLICATE")
    return refs


def current_context_fingerprint(
    *,
    checkpoint_id: str,
    task: str,
    operational_version: int,
    authority_refs: Iterable[str],
) -> str:
    checkpoint = _required_text(checkpoint_id, "WORK_IDENTITY_CHECKPOINT_REQUIRED").upper()
    current_task = _required_text(task, "WORK_IDENTITY_TASK_REQUIRED")
    if operational_version < 1:
        raise ValueError("WORK_IDENTITY_OPERATIONAL_VERSION_INVALID")
    refs = normalized_authority_refs(authority_refs)
    return _sha256_payload(
        {
            "checkpoint_id": checkpoint,
            "task": current_task,
            "operational_version": operational_version,
            "authority_refs": refs,
        }
    )


def resolve_work_identity_projection(
    seed: CanonicalWorkIdentitySeed,
    *,
    checkpoint_id: str,
    task: str,
    operational_version: int,
    authority_refs: Iterable[str],
) -> ResolvedWorkIdentityProjection:
    project_scope = _required_text(seed.project_scope, "WORK_IDENTITY_PROJECT_SCOPE_REQUIRED")
    logical_target = _required_text(seed.logical_target, "WORK_IDENTITY_LOGICAL_TARGET_REQUIRED")
    objective = _required_text(seed.objective, "WORK_IDENTITY_OBJECTIVE_REQUIRED")
    deliverable_class = _required_text(seed.deliverable_class, "WORK_IDENTITY_DELIVERABLE_CLASS_REQUIRED")
    acceptance_identity = _required_text(seed.acceptance_identity, "WORK_IDENTITY_ACCEPTANCE_REQUIRED")

    semantic_fingerprint = _sha256_payload(
        {
            "project_scope": project_scope,
            "logical_target": logical_target,
            "objective": objective,
            "deliverable_class": deliverable_class,
            "acceptance_identity": acceptance_identity,
        }
    )
    current_fingerprint = current_context_fingerprint(
        checkpoint_id=checkpoint_id,
        task=task,
        operational_version=operational_version,
        authority_refs=authority_refs,
    )
    binding_fingerprint = _sha256_payload(
        {
            "semantic_fingerprint": semantic_fingerprint,
            "current_fingerprint": current_fingerprint,
        }
    )
    return ResolvedWorkIdentityProjection(
        project_scope=project_scope,
        logical_target=logical_target,
        objective=objective,
        deliverable_class=deliverable_class,
        acceptance_identity=acceptance_identity,
        semantic_fingerprint=semantic_fingerprint,
        current_fingerprint=current_fingerprint,
        binding_fingerprint=binding_fingerprint,
    )
