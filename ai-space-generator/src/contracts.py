from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from typing import Any


def _duplicates(values: Iterable[str]) -> list[str]:
    counts = Counter(values)
    return sorted(value for value, count in counts.items() if count > 1)


def validate_event_id_unique(events: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Validate that every non-empty event_id appears exactly once."""
    ids = [str(row.get("event_id", "")).strip() for row in events]
    missing = [index for index, value in enumerate(ids) if not value]
    duplicates = _duplicates(value for value in ids if value)
    return {
        "hard_gate_pass": not missing and not duplicates,
        "missing_event_id_rows": missing,
        "duplicate_event_ids": duplicates,
    }


def validate_relation_integrity(
    relations: Iterable[Mapping[str, Any]],
    valid_subject_ids: Iterable[str],
) -> dict[str, Any]:
    """Validate relation identity and subject referential integrity.

    This intentionally checks only deterministic structure. It does not decide
    whether a semantically valid relation type is the right interpretation.
    """
    relation_rows = list(relations)
    relation_ids = [str(row.get("relation_id", "")).strip() for row in relation_rows]
    missing_relation_ids = [index for index, value in enumerate(relation_ids) if not value]
    duplicate_relation_ids = _duplicates(value for value in relation_ids if value)

    subjects = {str(value).strip() for value in valid_subject_ids if str(value).strip()}
    missing_subject_rows: list[dict[str, Any]] = []
    for index, row in enumerate(relation_rows):
        subject_id = str(row.get("subject_id", "")).strip()
        if not subject_id or subject_id not in subjects:
            missing_subject_rows.append({"row": index, "subject_id": subject_id})

    return {
        "hard_gate_pass": (
            not missing_relation_ids
            and not duplicate_relation_ids
            and not missing_subject_rows
        ),
        "missing_relation_id_rows": missing_relation_ids,
        "duplicate_relation_ids": duplicate_relation_ids,
        "invalid_subject_rows": missing_subject_rows,
    }
