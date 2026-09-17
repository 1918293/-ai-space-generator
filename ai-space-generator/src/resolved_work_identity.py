from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import json
from typing import Iterable


_SOURCE_HAO_INTENT = "HAO_INTENT"
_SOURCE_CURRENT_AUTHORITY = "CURRENT_AUTHORITY"
_ALLOWED_SOURCE_CLASSES = frozenset({_SOURCE_HAO_INTENT, _SOURCE_CURRENT_AUTHORITY})
_PROVENANCE_FIELDS = (
    "work_key",
    "project_scope",
    "logical_target",
    "objective",
    "deliverable_identity",
    "acceptance_identity",
)
_HAO_INTENT_REQUIRED_FIELDS = frozenset(
    {"objective", "deliverable_identity", "acceptance_identity"}
)


@dataclass(frozen=True)
class CanonicalWorkIdentitySeed:
    """Source-backed business work key plus material intent identity.

    `work_key` is the stable business/work-instance identity used for
    coordination. The five material fields describe the current intent bound to
    that work. The Runtime never derives either from TASK equality, model output,
    embeddings, provider/tool identity, RUN_KEY, or mutation identity.

    Each field source is `(field, source_class, source_ref)` where source_class is
    either `HAO_INTENT` or `CURRENT_AUTHORITY`. `HAO_INTENT` refs must have been
    admitted by the trusted current-interaction path; `CURRENT_AUTHORITY` refs
    must belong to the verified Current authority set.
    """

    work_key: str
    project_scope: str
    logical_target: str
    objective: str
    deliverable_identity: str
    acceptance_identity: str
    field_sources: tuple[tuple[str, str, str], ...]


@dataclass(frozen=True)
class ResolvedWorkIdentityProjection:
    """Stable work identity + mutable intent fingerprint + Current binding.

    `work_key` answers "which work instance?". `intent_fingerprint` answers
    "which material intent for that work?". `current_fingerprint` identifies the
    exact trusted Current/direct-Hao-intent snapshot. `binding_fingerprint` binds
    all three without collapsing their distinct semantics.
    """

    work_key: str
    project_scope: str
    logical_target: str
    objective: str
    deliverable_identity: str
    acceptance_identity: str
    field_sources: tuple[tuple[str, str, str], ...]
    intent_fingerprint: str
    current_fingerprint: str
    binding_fingerprint: str

    @property
    def semantic_fingerprint(self) -> str:
        """Compatibility alias; new code should use `intent_fingerprint`."""

        return self.intent_fingerprint

    def receipt_payload(self) -> dict[str, object]:
        return {
            "work_key": self.work_key,
            "project_scope": self.project_scope,
            "logical_target": self.logical_target,
            "objective": self.objective,
            "deliverable_identity": self.deliverable_identity,
            "acceptance_identity": self.acceptance_identity,
            "field_sources": self.field_sources,
            "intent_fingerprint": self.intent_fingerprint,
            "semantic_fingerprint": self.intent_fingerprint,
            "current_fingerprint": self.current_fingerprint,
            "binding_fingerprint": self.binding_fingerprint,
        }


class WorkIdentityRelation(str, Enum):
    UNKNOWN = "UNKNOWN"
    DIFFERENT_WORK = "DIFFERENT_WORK"
    SAME_WORK_SAME_INTENT = "SAME_WORK_SAME_INTENT"
    SAME_WORK_CHANGED_INTENT = "SAME_WORK_CHANGED_INTENT"


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


def _normalized_refs(
    values: Iterable[str],
    *,
    required_code: str,
    duplicate_code: str,
    required: bool,
) -> tuple[str, ...]:
    refs = tuple(sorted(value.strip() for value in values if value.strip()))
    if required and not refs:
        raise ValueError(required_code)
    if len(set(refs)) != len(refs):
        raise ValueError(duplicate_code)
    return refs


def normalized_authority_refs(values: Iterable[str]) -> tuple[str, ...]:
    return _normalized_refs(
        values,
        required_code="WORK_IDENTITY_AUTHORITY_REFS_REQUIRED",
        duplicate_code="WORK_IDENTITY_AUTHORITY_REFS_DUPLICATE",
        required=True,
    )


def normalized_intent_refs(values: Iterable[str]) -> tuple[str, ...]:
    return _normalized_refs(
        values,
        required_code="WORK_IDENTITY_INTENT_REFS_REQUIRED",
        duplicate_code="WORK_IDENTITY_INTENT_REFS_DUPLICATE",
        required=False,
    )


def _normalized_field_sources(
    values: Iterable[tuple[str, str, str]],
    *,
    authority_refs: tuple[str, ...],
    intent_refs: tuple[str, ...],
) -> tuple[tuple[str, str, str], ...]:
    by_field: dict[str, tuple[str, str]] = {}
    for raw_field, raw_source_class, raw_ref in values:
        field = raw_field.strip()
        source_class = raw_source_class.strip()
        ref = raw_ref.strip()
        if field not in _PROVENANCE_FIELDS:
            raise ValueError("WORK_IDENTITY_SOURCE_FIELD_INVALID")
        if field in by_field:
            raise ValueError("WORK_IDENTITY_SOURCE_FIELD_DUPLICATE")
        if source_class not in _ALLOWED_SOURCE_CLASSES:
            raise ValueError("WORK_IDENTITY_SOURCE_CLASS_INVALID")
        if not ref:
            raise ValueError("WORK_IDENTITY_SOURCE_REF_REQUIRED")
        by_field[field] = (source_class, ref)

    missing = [field for field in _PROVENANCE_FIELDS if field not in by_field]
    if missing:
        raise ValueError("WORK_IDENTITY_SOURCE_FIELD_MISSING")

    authority_set = set(authority_refs)
    intent_set = set(intent_refs)
    for field in _PROVENANCE_FIELDS:
        source_class, ref = by_field[field]
        if source_class == _SOURCE_CURRENT_AUTHORITY and ref not in authority_set:
            raise ValueError(f"WORK_IDENTITY_SOURCE_NOT_CURRENT_AUTHORITY:{field}")
        if source_class == _SOURCE_HAO_INTENT and ref not in intent_set:
            raise ValueError(f"WORK_IDENTITY_SOURCE_NOT_HAO_INTENT:{field}")

    if not any(
        by_field[field][0] == _SOURCE_HAO_INTENT
        for field in _HAO_INTENT_REQUIRED_FIELDS
    ):
        raise ValueError("WORK_IDENTITY_DIRECT_HAO_INTENT_BINDING_REQUIRED")

    return tuple(
        (field, by_field[field][0], by_field[field][1])
        for field in _PROVENANCE_FIELDS
    )


def current_context_fingerprint(
    *,
    checkpoint_id: str,
    task: str,
    operational_version: int,
    authority_refs: Iterable[str],
    intent_refs: Iterable[str] = (),
) -> str:
    checkpoint = _required_text(checkpoint_id, "WORK_IDENTITY_CHECKPOINT_REQUIRED").upper()
    current_task = _required_text(task, "WORK_IDENTITY_TASK_REQUIRED")
    if operational_version < 1:
        raise ValueError("WORK_IDENTITY_OPERATIONAL_VERSION_INVALID")
    refs = normalized_authority_refs(authority_refs)
    direct_intent_refs = normalized_intent_refs(intent_refs)
    return _sha256_payload(
        {
            "checkpoint_id": checkpoint,
            "task": current_task,
            "operational_version": operational_version,
            "authority_refs": refs,
            "intent_refs": direct_intent_refs,
        }
    )


def resolve_work_identity_projection(
    seed: CanonicalWorkIdentitySeed,
    *,
    checkpoint_id: str,
    task: str,
    operational_version: int,
    authority_refs: Iterable[str],
    intent_refs: Iterable[str],
) -> ResolvedWorkIdentityProjection:
    work_key = _required_text(seed.work_key, "WORK_IDENTITY_WORK_KEY_REQUIRED")
    project_scope = _required_text(seed.project_scope, "WORK_IDENTITY_PROJECT_SCOPE_REQUIRED")
    logical_target = _required_text(seed.logical_target, "WORK_IDENTITY_LOGICAL_TARGET_REQUIRED")
    objective = _required_text(seed.objective, "WORK_IDENTITY_OBJECTIVE_REQUIRED")
    deliverable_identity = _required_text(
        seed.deliverable_identity,
        "WORK_IDENTITY_DELIVERABLE_REQUIRED",
    )
    acceptance_identity = _required_text(seed.acceptance_identity, "WORK_IDENTITY_ACCEPTANCE_REQUIRED")
    refs = normalized_authority_refs(authority_refs)
    direct_intent_refs = normalized_intent_refs(intent_refs)
    field_sources = _normalized_field_sources(
        seed.field_sources,
        authority_refs=refs,
        intent_refs=direct_intent_refs,
    )

    intent_fingerprint = _sha256_payload(
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
        intent_refs=direct_intent_refs,
    )
    binding_fingerprint = _sha256_payload(
        {
            "work_key": work_key,
            "intent_fingerprint": intent_fingerprint,
            "current_fingerprint": current_fingerprint,
        }
    )
    return ResolvedWorkIdentityProjection(
        work_key=work_key,
        project_scope=project_scope,
        logical_target=logical_target,
        objective=objective,
        deliverable_identity=deliverable_identity,
        acceptance_identity=acceptance_identity,
        field_sources=field_sources,
        intent_fingerprint=intent_fingerprint,
        current_fingerprint=current_fingerprint,
        binding_fingerprint=binding_fingerprint,
    )


def compare_work_identity(
    left: ResolvedWorkIdentityProjection | None,
    right: ResolvedWorkIdentityProjection | None,
) -> WorkIdentityRelation:
    if left is None or right is None:
        return WorkIdentityRelation.UNKNOWN
    if left.work_key != right.work_key:
        return WorkIdentityRelation.DIFFERENT_WORK
    if left.intent_fingerprint == right.intent_fingerprint:
        return WorkIdentityRelation.SAME_WORK_SAME_INTENT
    return WorkIdentityRelation.SAME_WORK_CHANGED_INTENT
