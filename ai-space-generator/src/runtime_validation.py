from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime
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


def _clean(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _parse_iso_datetime(value: Any) -> datetime | None:
    text = _clean(value)
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _validate_intake_field_domain(
    intake_records: Iterable[Mapping[str, Any]],
    *,
    field: str,
    allowed_values: Iterable[str],
    invalid_key: str,
) -> dict[str, Any]:
    """Validate one Intake field against a caller-supplied Authority domain."""
    rows = list(intake_records)
    allowed = {_clean(value) for value in allowed_values if _clean(value)}
    invalid_rows: list[dict[str, Any]] = []

    for index, row in enumerate(rows):
        value = _clean(row.get(field))
        if value not in allowed:
            invalid_rows.append(
                {
                    "row": index,
                    "record_id": _clean(row.get("record_id")),
                    field: value,
                }
            )

    return {
        "hard_gate_pass": not invalid_rows,
        "allowed_values": sorted(allowed),
        invalid_key: invalid_rows,
    }


def _scope_intake_rows(
    intake_records: Iterable[Mapping[str, Any]],
    *,
    effective_from: str,
) -> dict[str, Any]:
    """Select lifecycle rows at/after a caller-supplied cutover.

    The cutover comes from Drive/Config Authority. GitHub does not hardcode it.
    Missing or unparseable timestamps fail closed because scope is unresolved.
    """
    rows = list(intake_records)
    threshold = _parse_iso_datetime(effective_from)
    if threshold is None:
        return {
            "hard_gate_pass": False,
            "effective_from": effective_from,
            "scoped_rows": [],
            "scoped_source_indexes": [],
            "excluded_legacy_rows": [],
            "scope_unknown_rows": [],
            "scope_error": "INVALID_EFFECTIVE_FROM",
        }

    scoped_rows: list[Mapping[str, Any]] = []
    scoped_source_indexes: list[int] = []
    excluded_legacy_rows: list[dict[str, Any]] = []
    scope_unknown_rows: list[dict[str, Any]] = []

    for index, row in enumerate(rows):
        captured_raw = row.get("captured_at")
        captured_at = _parse_iso_datetime(captured_raw)
        if captured_at is None:
            scope_unknown_rows.append(
                {
                    "row": index,
                    "record_id": _clean(row.get("record_id")),
                    "captured_at": _clean(captured_raw),
                }
            )
            continue

        try:
            is_legacy = captured_at < threshold
        except TypeError:
            scope_unknown_rows.append(
                {
                    "row": index,
                    "record_id": _clean(row.get("record_id")),
                    "captured_at": _clean(captured_raw),
                }
            )
            continue

        if is_legacy:
            excluded_legacy_rows.append(
                {
                    "row": index,
                    "record_id": _clean(row.get("record_id")),
                    "captured_at": _clean(captured_raw),
                }
            )
            continue

        scoped_rows.append(row)
        scoped_source_indexes.append(index)

    return {
        "hard_gate_pass": not scope_unknown_rows,
        "effective_from": effective_from,
        "scoped_rows": scoped_rows,
        "scoped_source_indexes": scoped_source_indexes,
        "excluded_legacy_rows": excluded_legacy_rows,
        "scope_unknown_rows": scope_unknown_rows,
        "scope_error": "",
    }


def _remap_invalid_status_rows(
    status_result: dict[str, Any],
    source_indexes: list[int],
) -> dict[str, Any]:
    remapped = dict(status_result)
    invalid_rows: list[dict[str, Any]] = []
    for item in status_result.get("invalid_status_rows", []):
        local_index = item["row"]
        updated = dict(item)
        updated["row"] = source_indexes[local_index]
        invalid_rows.append(updated)
    remapped["invalid_status_rows"] = invalid_rows
    return remapped


def run_integrity_validation(
    events: Iterable[Mapping[str, Any]],
    relations: Iterable[Mapping[str, Any]],
    valid_subject_ids: Iterable[str],
    *,
    valid_object_ids: Iterable[str] | None = None,
    intake_records: Iterable[Mapping[str, Any]] | None = None,
    allowed_intake_statuses: Iterable[str] | None = None,
    intake_status_effective_from: str | None = None,
    allowed_intake_primary_classes: Iterable[str] | None = None,
    allowed_intake_confidences: Iterable[str] | None = None,
    source_versions_before: Mapping[str, Any] | None = None,
    source_versions_after: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Run deterministic integrity contracts and return a stable summary.

    This adapter intentionally receives already-resolved records and source
    version tokens. It has no connector credentials and no knowledge of private
    Google Drive contents.

    Allowed domains and lifecycle cutovers are caller-supplied from current
    Authority. GitHub never defines or expands those vocabularies itself.
    Optional object endpoint validation is also caller-supplied so existing
    subject-only relation checks remain backward compatible.

    Additional Intake and freshness contracts are enabled only when their
    required inputs are supplied, preserving backward compatibility with the
    original EVENT_ID_UNIQUE / RELATION_INTEGRITY runtime.
    """
    event_result = validate_event_id_unique(events)
    relation_result = validate_relation_integrity(
        relations,
        valid_subject_ids,
        valid_object_ids,
    )

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
            if intake_status_effective_from is not None:
                scope = _scope_intake_rows(
                    intake_rows,
                    effective_from=intake_status_effective_from,
                )
                contracts["INTAKE_STATUS_SCOPE"] = {
                    key: value
                    for key, value in scope.items()
                    if key not in {"scoped_rows", "scoped_source_indexes"}
                }

                if scope["hard_gate_pass"]:
                    status_result = validate_intake_status_domain(
                        scope["scoped_rows"],
                        allowed_intake_statuses,
                    )
                    contracts["INTAKE_STATUS_DOMAIN"] = _remap_invalid_status_rows(
                        status_result,
                        scope["scoped_source_indexes"],
                    )
            else:
                contracts["INTAKE_STATUS_DOMAIN"] = validate_intake_status_domain(
                    intake_rows,
                    allowed_intake_statuses,
                )

        if allowed_intake_primary_classes is not None:
            contracts["INTAKE_PRIMARY_CLASS_DOMAIN"] = _validate_intake_field_domain(
                intake_rows,
                field="primary_class",
                allowed_values=allowed_intake_primary_classes,
                invalid_key="invalid_primary_class_rows",
            )

        if allowed_intake_confidences is not None:
            contracts["INTAKE_CONFIDENCE_DOMAIN"] = _validate_intake_field_domain(
                intake_rows,
                field="confidence",
                allowed_values=allowed_intake_confidences,
                invalid_key="invalid_confidence_rows",
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
