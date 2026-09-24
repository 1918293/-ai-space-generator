from src.contracts import (
    validate_event_id_unique,
    validate_intake_id_unique,
    validate_intake_status_domain,
    validate_relation_integrity,
    validate_snapshot_stability,
    validate_supersedes_integrity,
    validate_supersedes_temporal_order,
)
from src.runtime_validation import run_integrity_validation


def test_event_ids_pass_when_unique():
    result = validate_event_id_unique([
        {"event_id": "EVT-001"},
        {"event_id": "EVT-002"},
    ])
    assert result["hard_gate_pass"] is True
    assert result["duplicate_event_ids"] == []


def test_event_ids_fail_on_duplicate():
    result = validate_event_id_unique([
        {"event_id": "EVT-001"},
        {"event_id": "EVT-001"},
    ])
    assert result["hard_gate_pass"] is False
    assert result["duplicate_event_ids"] == ["EVT-001"]


def test_relation_integrity_passes_with_unique_ids_and_existing_subjects():
    result = validate_relation_integrity(
        [
            {"relation_id": "REL-001", "subject_id": "INTAKE-001"},
            {"relation_id": "REL-002", "subject_id": "INTAKE-002"},
        ],
        valid_subject_ids={"INTAKE-001", "INTAKE-002"},
    )
    assert result["hard_gate_pass"] is True
    assert result["duplicate_relation_ids"] == []
    assert result["invalid_subject_rows"] == []
    assert result["invalid_object_rows"] == []


def test_relation_integrity_fails_on_duplicate_relation_id():
    result = validate_relation_integrity(
        [
            {"relation_id": "REL-001", "subject_id": "INTAKE-001"},
            {"relation_id": "REL-001", "subject_id": "INTAKE-001"},
        ],
        valid_subject_ids={"INTAKE-001"},
    )
    assert result["hard_gate_pass"] is False
    assert result["duplicate_relation_ids"] == ["REL-001"]


def test_relation_integrity_fails_when_subject_does_not_exist():
    result = validate_relation_integrity(
        [{"relation_id": "REL-001", "subject_id": "INTAKE-MISSING"}],
        valid_subject_ids={"INTAKE-001"},
    )
    assert result["hard_gate_pass"] is False
    assert result["invalid_subject_rows"] == [
        {"row": 0, "subject_id": "INTAKE-MISSING"}
    ]


def test_relation_integrity_can_validate_object_endpoint_when_supplied():
    result = validate_relation_integrity(
        [
            {
                "relation_id": "REL-001",
                "subject_id": "INTAKE-001",
                "object_id": "INTAKE-MISSING",
            }
        ],
        valid_subject_ids={"INTAKE-001"},
        valid_object_ids={"INTAKE-001", "INTAKE-002"},
    )
    assert result["hard_gate_pass"] is False
    assert result["invalid_subject_rows"] == []
    assert result["invalid_object_rows"] == [
        {"row": 0, "object_id": "INTAKE-MISSING"}
    ]


def test_relation_integrity_keeps_subject_only_compatibility_without_object_domain():
    result = validate_relation_integrity(
        [
            {
                "relation_id": "REL-001",
                "subject_id": "INTAKE-001",
                "object_id": "INTAKE-MISSING",
            }
        ],
        valid_subject_ids={"INTAKE-001"},
    )
    assert result["hard_gate_pass"] is True
    assert result["invalid_object_rows"] == []


def test_runtime_adapter_forwards_optional_object_domain():
    result = run_integrity_validation(
        events=[{"event_id": "EVT-001"}],
        relations=[
            {
                "relation_id": "REL-001",
                "subject_id": "INTAKE-001",
                "object_id": "INTAKE-MISSING",
            }
        ],
        valid_subject_ids={"INTAKE-001"},
        valid_object_ids={"INTAKE-001"},
    )
    assert result["authority_integrity_pass"] is False
    assert result["failed_contracts"] == ["RELATION_INTEGRITY"]
    assert result["contracts"]["RELATION_INTEGRITY"]["invalid_object_rows"] == [
        {"row": 0, "object_id": "INTAKE-MISSING"}
    ]


def test_intake_ids_fail_on_duplicate():
    result = validate_intake_id_unique([
        {"record_id": "INTAKE-001"},
        {"record_id": "INTAKE-001"},
    ])
    assert result["hard_gate_pass"] is False
    assert result["duplicate_record_ids"] == ["INTAKE-001"]


def test_intake_status_domain_flags_out_of_enum_values():
    result = validate_intake_status_domain(
        [
            {"record_id": "INTAKE-001", "status": "CLOSED"},
            {"record_id": "INTAKE-002", "status": "CURRENT"},
        ],
        allowed_statuses={"DETECTED", "STORED_PENDING_VERIFICATION", "CLOSED", "BLOCKED"},
    )
    assert result["hard_gate_pass"] is False
    assert result["invalid_status_rows"] == [
        {"row": 1, "record_id": "INTAKE-002", "status": "CURRENT"}
    ]


def test_supersedes_integrity_flags_missing_target():
    result = validate_supersedes_integrity([
        {"record_id": "INTAKE-001", "supersedes": ""},
        {"record_id": "INTAKE-002", "supersedes": "INTAKE-MISSING"},
    ])
    assert result["hard_gate_pass"] is False
    assert result["orphan_supersedes"] == [
        {"row": 1, "record_id": "INTAKE-002", "supersedes": "INTAKE-MISSING"}
    ]


def test_supersedes_integrity_supports_multiple_semicolon_targets():
    result = validate_supersedes_integrity([
        {"record_id": "INTAKE-001", "supersedes": ""},
        {"record_id": "INTAKE-002", "supersedes": ""},
        {"record_id": "INTAKE-003", "supersedes": "INTAKE-001; INTAKE-002"},
    ])
    assert result["hard_gate_pass"] is True
    assert result["orphan_supersedes"] == []


def test_temporal_order_flags_superseding_record_earlier_than_target():
    result = validate_supersedes_temporal_order([
        {
            "record_id": "INTAKE-001",
            "captured_at": "2026-08-16T01:28:00+08:00",
            "supersedes": "",
        },
        {
            "record_id": "INTAKE-002",
            "captured_at": "2026-08-16T01:26:02+08:00",
            "supersedes": "INTAKE-001",
        },
    ])
    assert result["hard_gate_pass"] is False
    assert result["timezone_mismatch_rows"] == []
    assert result["temporal_inversions"][0]["record_id"] == "INTAKE-002"
    assert result["temporal_inversions"][0]["supersedes"] == "INTAKE-001"
    assert result["temporal_inversions"][0]["delta_seconds"] == 118.0


def test_temporal_order_fails_closed_on_mixed_timezone_awareness():
    result = validate_supersedes_temporal_order([
        {
            "record_id": "INTAKE-001",
            "captured_at": "2026-08-16T01:28:00+08:00",
            "supersedes": "",
        },
        {
            "record_id": "INTAKE-002",
            "captured_at": "2026-08-16T01:29:00",
            "supersedes": "INTAKE-001",
        },
    ])
    assert result["hard_gate_pass"] is False
    assert result["temporal_inversions"] == []
    assert result["timezone_mismatch_rows"] == [
        {
            "row": 1,
            "record_id": "INTAKE-002",
            "captured_at": "2026-08-16T01:29:00",
            "supersedes": "INTAKE-001",
            "target_captured_at": "2026-08-16T01:28:00+08:00",
        }
    ]


def test_temporal_order_reports_invalid_target_timestamp_once_at_target_row():
    result = validate_supersedes_temporal_order([
        {
            "record_id": "INTAKE-001",
            "captured_at": "not-a-time",
            "supersedes": "",
        },
        {
            "record_id": "INTAKE-002",
            "captured_at": "2026-08-16T01:29:00+08:00",
            "supersedes": "INTAKE-001",
        },
    ])
    assert result["hard_gate_pass"] is False
    assert result["invalid_timestamp_rows"] == [
        {"row": 0, "record_id": "INTAKE-001", "captured_at": "not-a-time"}
    ]
    assert result["timezone_mismatch_rows"] == []
    assert result["temporal_inversions"] == []


def test_snapshot_stability_passes_when_tokens_do_not_change():
    result = validate_snapshot_stability(
        {"intake": "rev-1", "events": "rev-2"},
        {"intake": "rev-1", "events": "rev-2"},
    )
    assert result["hard_gate_pass"] is True
    assert result["changed_sources"] == []


def test_snapshot_stability_flags_changed_source():
    result = validate_snapshot_stability(
        {"intake": "rev-1"},
        {"intake": "rev-2"},
    )
    assert result["hard_gate_pass"] is False
    assert result["changed_sources"] == [
        {"source": "intake", "before": "rev-1", "after": "rev-2"}
    ]
