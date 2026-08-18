from src.runtime_validation import run_integrity_validation


ALLOWED_INTAKE_STATUSES = {
    "DETECTED",
    "STORED_PENDING_VERIFICATION",
    "CLOSED",
    "BLOCKED",
}

ALLOWED_INTAKE_PRIMARY_CLASSES = {
    "IDEA",
    "REQUIREMENT",
    "DECISION",
    "TASK",
    "OBSERVATION",
    "QUESTION",
    "EVIDENCE",
    "SYSTEM_DESIGN",
}

ALLOWED_INTAKE_CONFIDENCES = {"LOW", "MEDIUM", "HIGH"}


def test_runtime_summary_passes_when_all_contracts_pass():
    result = run_integrity_validation(
        events=[{"event_id": "EVT-001"}, {"event_id": "EVT-002"}],
        relations=[{"relation_id": "REL-001", "subject_id": "INTAKE-001"}],
        valid_subject_ids={"INTAKE-001"},
    )
    assert result["schema_version"] == "0.2"
    assert result["authority_integrity_pass"] is True
    assert result["failed_contracts"] == []


def test_runtime_summary_reports_both_live_style_integrity_failures():
    result = run_integrity_validation(
        events=[{"event_id": "EVT-R50"}, {"event_id": "EVT-R50"}],
        relations=[
            {"relation_id": "REL-QV3-01", "subject_id": "INTAKE-QV3"},
            {"relation_id": "REL-QV3-01", "subject_id": "INTAKE-QV3"},
        ],
        valid_subject_ids={"INTAKE-QV3"},
    )
    assert result["authority_integrity_pass"] is False
    assert result["failed_contracts"] == [
        "EVENT_ID_UNIQUE",
        "RELATION_INTEGRITY",
    ]
    assert result["contracts"]["EVENT_ID_UNIQUE"]["duplicate_event_ids"] == [
        "EVT-R50"
    ]
    assert result["contracts"]["RELATION_INTEGRITY"]["duplicate_relation_ids"] == [
        "REL-QV3-01"
    ]


def test_runtime_summary_keeps_invalid_subject_separate_from_duplicate_identity():
    result = run_integrity_validation(
        events=[{"event_id": "EVT-001"}],
        relations=[{"relation_id": "REL-001", "subject_id": "INTAKE-MISSING"}],
        valid_subject_ids={"INTAKE-001"},
    )
    relation = result["contracts"]["RELATION_INTEGRITY"]
    assert relation["duplicate_relation_ids"] == []
    assert relation["invalid_subject_rows"] == [
        {"row": 0, "subject_id": "INTAKE-MISSING"}
    ]


def test_runtime_summary_includes_intake_domain_supersedes_and_temporal_contracts():
    intake = [
        {
            "record_id": "INTAKE-001",
            "captured_at": "2026-08-16T01:28:00+08:00",
            "status": "CLOSED",
            "supersedes": "",
        },
        {
            "record_id": "INTAKE-002",
            "captured_at": "2026-08-16T01:26:02+08:00",
            "status": "CURRENT",
            "supersedes": "INTAKE-001",
        },
        {
            "record_id": "INTAKE-003",
            "captured_at": "2026-08-16T02:01:00+08:00",
            "status": "CLOSED",
            "supersedes": "INTAKE-MISSING",
        },
        {
            "record_id": "INTAKE-003",
            "captured_at": "2026-08-16T02:02:00+08:00",
            "status": "CLOSED",
            "supersedes": "",
        },
    ]
    result = run_integrity_validation(
        events=[{"event_id": "EVT-001"}],
        relations=[{"relation_id": "REL-001", "subject_id": "INTAKE-001"}],
        valid_subject_ids={"INTAKE-001", "INTAKE-002", "INTAKE-003"},
        intake_records=intake,
        allowed_intake_statuses=ALLOWED_INTAKE_STATUSES,
        source_versions_before={"intake": "rev-1", "events": "rev-1"},
        source_versions_after={"intake": "rev-1", "events": "rev-1"},
    )
    assert result["authority_integrity_pass"] is False
    assert result["failed_contracts"] == [
        "INTAKE_ID_UNIQUE",
        "SUPERSEDES_INTEGRITY",
        "SUPERSEDES_TEMPORAL_ORDER",
        "INTAKE_STATUS_DOMAIN",
    ]
    assert result["contracts"]["INTAKE_ID_UNIQUE"]["duplicate_record_ids"] == [
        "INTAKE-003"
    ]
    assert result["contracts"]["SUPERSEDES_INTEGRITY"]["orphan_supersedes"] == [
        {"row": 2, "record_id": "INTAKE-003", "supersedes": "INTAKE-MISSING"}
    ]
    assert result["contracts"]["SUPERSEDES_TEMPORAL_ORDER"]["temporal_inversions"][0][
        "delta_seconds"
    ] == 118.0
    assert result["contracts"]["INTAKE_STATUS_DOMAIN"]["invalid_status_rows"] == [
        {"row": 1, "record_id": "INTAKE-002", "status": "CURRENT"}
    ]
    assert result["contracts"]["AUTHORITY_SNAPSHOT_STABLE"]["hard_gate_pass"] is True


def test_runtime_summary_flags_stale_authority_window():
    result = run_integrity_validation(
        events=[{"event_id": "EVT-001"}],
        relations=[{"relation_id": "REL-001", "subject_id": "INTAKE-001"}],
        valid_subject_ids={"INTAKE-001"},
        source_versions_before={"intake": "rev-1", "events": "rev-1"},
        source_versions_after={"intake": "rev-2", "events": "rev-1"},
    )
    assert result["authority_integrity_pass"] is False
    assert result["failed_contracts"] == ["AUTHORITY_SNAPSHOT_STABLE"]
    assert result["contracts"]["AUTHORITY_SNAPSHOT_STABLE"]["changed_sources"] == [
        {"source": "intake", "before": "rev-1", "after": "rev-2"}
    ]


def test_runtime_summary_flags_primary_class_and_confidence_domain_drift():
    intake = [
        {
            "record_id": "INTAKE-001",
            "captured_at": "2026-08-18T12:17:00+08:00",
            "status": "CLOSED",
            "primary_class": "RESEARCH_FINDING",
            "confidence": "MEDIUM_HIGH",
            "supersedes": "",
        }
    ]
    result = run_integrity_validation(
        events=[{"event_id": "EVT-001"}],
        relations=[{"relation_id": "REL-001", "subject_id": "INTAKE-001"}],
        valid_subject_ids={"INTAKE-001"},
        intake_records=intake,
        allowed_intake_primary_classes=ALLOWED_INTAKE_PRIMARY_CLASSES,
        allowed_intake_confidences=ALLOWED_INTAKE_CONFIDENCES,
    )

    assert result["authority_integrity_pass"] is False
    assert result["failed_contracts"] == [
        "INTAKE_PRIMARY_CLASS_DOMAIN",
        "INTAKE_CONFIDENCE_DOMAIN",
    ]
    assert result["contracts"]["INTAKE_PRIMARY_CLASS_DOMAIN"][
        "invalid_primary_class_rows"
    ] == [
        {
            "row": 0,
            "record_id": "INTAKE-001",
            "primary_class": "RESEARCH_FINDING",
        }
    ]
    assert result["contracts"]["INTAKE_CONFIDENCE_DOMAIN"][
        "invalid_confidence_rows"
    ] == [
        {
            "row": 0,
            "record_id": "INTAKE-001",
            "confidence": "MEDIUM_HIGH",
        }
    ]


def test_runtime_status_effective_scope_excludes_legacy_rows():
    intake = [
        {
            "record_id": "INTAKE-LEGACY",
            "captured_at": "2026-08-15T22:17:00+08:00",
            "status": "CURRENT",
            "supersedes": "",
        },
        {
            "record_id": "INTAKE-CURRENT",
            "captured_at": "2026-08-16T10:45:00+08:00",
            "status": "CLOSED",
            "supersedes": "",
        },
    ]
    result = run_integrity_validation(
        events=[{"event_id": "EVT-001"}],
        relations=[{"relation_id": "REL-001", "subject_id": "INTAKE-CURRENT"}],
        valid_subject_ids={"INTAKE-LEGACY", "INTAKE-CURRENT"},
        intake_records=intake,
        allowed_intake_statuses=ALLOWED_INTAKE_STATUSES,
        intake_status_effective_from="2026-08-16T10:45:00+08:00",
    )

    assert result["authority_integrity_pass"] is True
    assert result["failed_contracts"] == []
    scope = result["contracts"]["INTAKE_STATUS_SCOPE"]
    assert scope["hard_gate_pass"] is True
    assert scope["excluded_legacy_rows"] == [
        {
            "row": 0,
            "record_id": "INTAKE-LEGACY",
            "captured_at": "2026-08-15T22:17:00+08:00",
        }
    ]
    assert result["contracts"]["INTAKE_STATUS_DOMAIN"]["invalid_status_rows"] == []


def test_runtime_status_effective_scope_fails_closed_when_timestamp_is_unknown():
    intake = [
        {
            "record_id": "INTAKE-UNKNOWN",
            "captured_at": "",
            "status": "CLOSED",
            "supersedes": "",
        }
    ]
    result = run_integrity_validation(
        events=[{"event_id": "EVT-001"}],
        relations=[{"relation_id": "REL-001", "subject_id": "INTAKE-UNKNOWN"}],
        valid_subject_ids={"INTAKE-UNKNOWN"},
        intake_records=intake,
        allowed_intake_statuses=ALLOWED_INTAKE_STATUSES,
        intake_status_effective_from="2026-08-16T10:45:00+08:00",
    )

    assert result["authority_integrity_pass"] is False
    assert result["failed_contracts"] == [
        "SUPERSEDES_TEMPORAL_ORDER",
        "INTAKE_STATUS_SCOPE",
    ]
    assert result["contracts"]["INTAKE_STATUS_SCOPE"]["scope_unknown_rows"] == [
        {
            "row": 0,
            "record_id": "INTAKE-UNKNOWN",
            "captured_at": "",
        }
    ]
    assert "INTAKE_STATUS_DOMAIN" not in result["contracts"]
