from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Iterable


_SEMANTIC_FIELDS = (
    "project_scope",
    "logical_target",
    "objective",
    "deliverable_identity",
    "acceptance_identity",
)


@dataclass(frozen=True)
class CanonicalWorkIdentitySeed:
    """Authority-supplied semantic work identity fields plus field provenance.

    The Runtime does not infer these fields from raw TASK text, model output,
    embeddings, providers, tools, RUN_KEYs, mutation identity, or a private
    deliverable taxonomy. Each field must already be resolved by the trusted
    Current/Intent authority path and must name the verified Current authority
    ref that supplied it.
    """

    project_scope: str
    logical_target: str
    objective: str
    deliverable_identity: str
    acceptance_identity: str
    field_sources: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class ResolvedWorkIdentityProjection:
    """Immutable semantic identity plus exact Current binding and provenance.

    `semantic_fingerprint` identifies work semantics only. `current_fingerprint`
    identifies the verified Current snapshot. `binding_fingerprint` binds both
    without redefining either one as the other. `field_sources` records which
    verified Current authority supplied each semantic field without making that
    source location part of semantic identity.
    """

    project_scope: str
    logical_target: str
    objective: str
    deliverable_identity: str
    acceptance_identity: str
    field_sources: tuple[tuple[str, str], ...]
    semantic_fingerprint: str
    current_fingerprint: str
    binding_fingerprint: str

    def receipt_payload(self) -> dict[str, object]:
        return {
            "project_scope": self.project_scope,
            "logical_target": self.logical_target,
            "objective": self.objective,
            "deliverable_identity": self.deliverable_identity,
            "acceptance_identity": self.acceptance_identity,
            "field_sources": self.field_sources,
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


def _normalized_field_sources(
    values: Iterable[tuple[str, str]],
    *,
    authority_refs: tuple[str, ...],
) -> tuple[tuple[str, str], ...]:
    by_field: dict[str, str] = {}
    for raw_field, raw_ref in values:
        field = raw_field.strip()
        ref = raw_ref.strip()
        if field not in _SEMANTIC_FIELDS:
            raise ValueError("WORK_IDENTITY_SOURCE_FIELD_INVALID")
        if field in by_field:
            raise ValueError("WORK_IDENTITY_SOURCE_FIELD_DUPLICATE")
        if not ref:
            raise ValueError("WORK_IDENTITY_SOURCE_REF_REQUIRED")
        by_field[field] = ref

    missing = [field for field in _SEMANTIC_FIELDS if field not in by_field]
    if missing:
        raise ValueError("WORK_IDENTITY_SOURCE_FIELD_MISSING")

    current_refs = set(authority_refs)
    for field in _SEMANTIC_FIELDS:
        if by_field[field] not in current_refs:
            raise ValueError(f"WORK_IDENTITY_SOURCE_NOT_CURRENT_AUTHORITY:{field}")
    return tuple((field, by_field[field]) for field in _SEMANTIC_FIELDS)


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
    deliverable_identity = _required_text(
        seed.deliverable_identity,
        "WORK_IDENTITY_DELIVERABLE_REQUIRED",
    )
    acceptance_identity = _required_text(seed.acceptance_identity, "WORK_IDENTITY_ACCEPTANCE_REQUIRED")
    refs = normalized_authority_refs(authority_refs)
    field_sources = _normalized_field_sources(seed.field_sources, authority_refs=refs)

    semantic_fingerprint = _sha256_payload(
        {
            "project_scope": project_scope,
            "logical_target": logical_target,
            "objective": objective,
            "deliverable_identity": deliverable_identity,
            "acceptance_identity": acceptance_identity,
        }
    )
    current_fingerprint = current_context_fingerprint(
        checkpoint_id=checkpoint_id,
        task=task,
        operational_version=operational_version,
        authority_refs=refs,
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
        deliverable_identity=deliverable_identity,
        acceptance_identity=acceptance_identity,
        field_sources=field_sources,
        semantic_fingerprint=semantic_fingerprint,
        current_fingerprint=current_fingerprint,
        binding_fingerprint=binding_fingerprint,
    )
