from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from datetime import datetime
from typing import Any


def _clean(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _duplicates(values: Iterable[str]) -> list[str]:
    counts = Counter(values)
    return sorted(value for value, count in counts.items() if count > 1)


def _split_refs(value: Any) -> list[str]:
    text = _clean(value)
    if not text:
        return []
    return [part.strip() for part in text.split(";") if part.strip()]


def _parse_iso_datetime(value: Any) -> datetime | None:
    text = _clean(value)
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _is_timezone_aware(value: datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


def validate_event_id_unique(events: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Validate that every non-empty event_id appears exactly once."""
    ids = [_clean(row.get("event_id")) for row in events]
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
    valid_object_ids: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Validate relation identity and deterministic endpoint integrity.

    Subject referential integrity is always checked. Object referential integrity
    is checked only when the caller supplies an explicit valid_object_ids domain,
    preserving the original subject-only behavior for existing callers.

    This intentionally checks only deterministic structure. It does not decide
    whether a semantically valid relation type is the right interpretation.
    """
    relation_rows = list(relations)
    relation_ids = [_clean(row.get("relation_id")) for row in relation_rows]
    missing_relation_ids = [index for index, value in enumerate(relation_ids) if not value]
    duplicate_relation_ids = _duplicates(value for value in relation_ids if value)

    subjects = {_clean(value) for value in valid_subject_ids if _clean(value)}
    missing_subject_rows: list[dict[str, Any]] = []
    for index, row in enumerate(relation_rows):
        subject_id = _clean(row.get("subject_id"))
        if not subject_id or subject_id not in subjects:
            missing_subject_rows.append({"row": index, "subject_id": subject_id})

    invalid_object_rows: list[dict[str, Any]] = []
    if valid_object_ids is not None:
        objects = {_clean(value) for value in valid_object_ids if _clean(value)}
        for index, row in enumerate(relation_rows):
            object_id = _clean(row.get("object_id"))
            if not object_id or object_id not in objects:
                invalid_object_rows.append({"row": index, "object_id": object_id})

    return {
        "hard_gate_pass": (
            not missing_relation_ids
            and not duplicate_relation_ids
            and not missing_subject_rows
            and not invalid_object_rows
        ),
        "missing_relation_id_rows": missing_relation_ids,
        "duplicate_relation_ids": duplicate_relation_ids,
        "invalid_subject_rows": missing_subject_rows,
        "invalid_object_rows": invalid_object_rows,
    }


def validate_intake_id_unique(
    intake_records: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Validate that every Intake record_id is present and unique."""
    rows = list(intake_records)
    ids = [_clean(row.get("record_id")) for row in rows]
    missing = [index for index, value in enumerate(ids) if not value]
    duplicates = _duplicates(value for value in ids if value)
    return {
        "hard_gate_pass": not missing and not duplicates,
        "missing_record_id_rows": missing,
        "duplicate_record_ids": duplicates,
    }


def validate_intake_status_domain(
    intake_records: Iterable[Mapping[str, Any]],
    allowed_statuses: Iterable[str],
) -> dict[str, Any]:
    """Validate Intake lifecycle status against an explicit allowed domain."""
    allowed = {_clean(value) for value in allowed_statuses if _clean(value)}
    invalid_rows: list[dict[str, Any]] = []
    for index, row in enumerate(intake_records):
        status = _clean(row.get("status"))
        if status not in allowed:
            invalid_rows.append(
                {
                    "row": index,
                    "record_id": _clean(row.get("record_id")),
                    "status": status,
                }
            )
    return {
        "hard_gate_pass": not invalid_rows,
        "allowed_statuses": sorted(allowed),
        "invalid_status_rows": invalid_rows,
    }


def validate_supersedes_integrity(
    intake_records: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Validate that every supersedes target resolves to an Intake record_id."""
    rows = list(intake_records)
    record_ids = {_clean(row.get("record_id")) for row in rows if _clean(row.get("record_id"))}
    orphan_edges: list[dict[str, Any]] = []

    for index, row in enumerate(rows):
        source_id = _clean(row.get("record_id"))
        for target_id in _split_refs(row.get("supersedes")):
            if target_id not in record_ids:
                orphan_edges.append(
                    {
                        "row": index,
                        "record_id": source_id,
                        "supersedes": target_id,
                    }
                )

    return {
        "hard_gate_pass": not orphan_edges,
        "orphan_supersedes": orphan_edges,
    }


def validate_supersedes_temporal_order(
    intake_records: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Flag unresolved timestamps, timezone mismatches, and temporal inversions.

    This is a structural anomaly detector. A flagged inversion may be intentional
    correction evidence, but it must remain explicit rather than silently passing.
    Mixed timezone-awareness also fails closed rather than guessing a timezone or
    raising a TypeError during comparison.
    """
    rows = list(intake_records)
    by_id: dict[str, tuple[int, Mapping[str, Any], datetime | None]] = {}
    invalid_timestamp_rows: list[dict[str, Any]] = []

    for index, row in enumerate(rows):
        record_id = _clean(row.get("record_id"))
        captured_raw = row.get("captured_at")
        captured_at = _parse_iso_datetime(captured_raw)
        if record_id:
            by_id[record_id] = (index, row, captured_at)
            if captured_at is None:
                invalid_timestamp_rows.append(
                    {
                        "row": index,
                        "record_id": record_id,
                        "captured_at": _clean(captured_raw),
                    }
                )

    timezone_mismatch_rows: list[dict[str, Any]] = []
    inversions: list[dict[str, Any]] = []

    for index, row in enumerate(rows):
        source_id = _clean(row.get("record_id"))
        source_raw = row.get("captured_at")
        source_time = _parse_iso_datetime(source_raw)
        if source_id and source_time is None:
            continue

        for target_id in _split_refs(row.get("supersedes")):
            target_entry = by_id.get(target_id)
            if target_entry is None:
                continue

            _, target, target_time = target_entry
            target_raw = target.get("captured_at")
            if target_time is None:
                continue
            if source_time is None:
                continue

            if _is_timezone_aware(source_time) != _is_timezone_aware(target_time):
                timezone_mismatch_rows.append(
                    {
                        "row": index,
                        "record_id": source_id,
                        "captured_at": _clean(source_raw),
                        "supersedes": target_id,
                        "target_captured_at": _clean(target_raw),
                    }
                )
                continue

            if source_time < target_time:
                inversions.append(
                    {
                        "row": index,
                        "record_id": source_id,
                        "captured_at": _clean(source_raw),
                        "supersedes": target_id,
                        "target_captured_at": _clean(target_raw),
                        "delta_seconds": (target_time - source_time).total_seconds(),
                    }
                )

    return {
        "hard_gate_pass": (
            not invalid_timestamp_rows
            and not timezone_mismatch_rows
            and not inversions
        ),
        "invalid_timestamp_rows": invalid_timestamp_rows,
        "timezone_mismatch_rows": timezone_mismatch_rows,
        "temporal_inversions": inversions,
    }


def validate_snapshot_stability(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate that source version tokens remain unchanged during one read window."""
    before_clean = {str(key): _clean(value) for key, value in before.items()}
    after_clean = {str(key): _clean(value) for key, value in after.items()}
    keys = sorted(set(before_clean) | set(after_clean))
    changed = [
        {
            "source": key,
            "before": before_clean.get(key, ""),
            "after": after_clean.get(key, ""),
        }
        for key in keys
        if before_clean.get(key, "") != after_clean.get(key, "")
    ]
    return {
        "hard_gate_pass": not changed,
        "changed_sources": changed,
    }
