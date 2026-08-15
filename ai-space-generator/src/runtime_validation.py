from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from .contracts import validate_event_id_unique, validate_relation_integrity


def run_integrity_validation(
    events: Iterable[Mapping[str, Any]],
    relations: Iterable[Mapping[str, Any]],
    valid_subject_ids: Iterable[str],
) -> dict[str, Any]:
    """Run deterministic integrity contracts and return a stable summary.

    This adapter intentionally receives already-resolved records. It has no
    connector credentials and no knowledge of private Google Drive contents.
    """
    event_result = validate_event_id_unique(events)
    relation_result = validate_relation_integrity(relations, valid_subject_ids)

    contracts = {
        "EVENT_ID_UNIQUE": event_result,
        "RELATION_INTEGRITY": relation_result,
    }
    failed_contracts = [
        name for name, result in contracts.items() if not result["hard_gate_pass"]
    ]

    return {
        "schema_version": "0.1",
        "authority_integrity_pass": not failed_contracts,
        "failed_contracts": failed_contracts,
        "contracts": contracts,
    }
