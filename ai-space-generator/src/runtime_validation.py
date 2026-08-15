from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from .contracts import (
    validate_event_id_unique,
    validate_intake_id_unique,
    validate_intake_status_domain,
    validate_relation_integrity,
    validate_snapshot_stability,
    validate_supersedes_integrity,
    validate_supersedes_temporal_order,
)


def run_integrity_validation(
    events: Iterable[Mapping[str, Any]],
    relations: Iterable[Mapping[str, Any]],
    valid_subject_ids: Iterable[str],
    *,
    intake_records: Iterable[Mapping[str, Any]] | None = None,
    allowed_intake_statuses: Iterable[str] | None = None,
    source_versions_before: Mapping[str, Any] | None = None,
    source_versions_after: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Run deterministic integrity contracts and return a stable summary.

    This adapter intentionally receives already-resolved records and source
    version tokens. It has no connector credentials and no knowledge of private
    Google Drive contents.

    Additional Intake and freshness contracts are enabled only when their
    required inputs are supplied, preserving backward compatibility with the
    original EVENT_ID_UNIQUE / RELATION_INTEGRITY runtime.
    """
    event_result = validate_event_id_unique(events)
    relation_result = validate_relation_integrity(relations, valid_subject_ids)

    contracts: dict[str, dict[str, Any]] = {
        "EVENT_ID_UNIQUE": event_result,
        "RELATION_INTEGRITY": relation_result,
    }

    if intake_records is not None:
        intake_rows = list(intake_records)
        contracts["INTAKE_ID_UNIQUE"] = validate_intake_id_unique(intake_rows)
        contracts["SUPERSEDES_INTEGRITY"] = validate_supersedes_integrity(intake_rows)
        contracts["SUPERSEDES_TEMPORAL_ORDER"] = validate_supersedes_temporal_order(
            intake_rows
        )
        if allowed_intake_statuses is not None:
            contracts["INTAKE_STATUS_DOMAIN"] = validate_intake_status_domain(
                intake_rows,
                allowed_intake_statuses,
            )

    if source_versions_before is not None or source_versions_after is not None:
        contracts["AUTHORITY_SNAPSHOT_STABLE"] = validate_snapshot_stability(
            source_versions_before or {},
            source_versions_after or {},
        )

    failed_contracts = [
        name for name, result in contracts.items() if not result["hard_gate_pass"]
    ]

    return {
        "schema_version": "0.2",
        "authority_integrity_pass": not failed_contracts,
        "failed_contracts": failed_contracts,
        "contracts": contracts,
    }
