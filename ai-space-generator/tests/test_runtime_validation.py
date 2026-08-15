from src.runtime_validation import run_integrity_validation


def test_runtime_summary_passes_when_all_contracts_pass():
    result = run_integrity_validation(
        events=[{"event_id": "EVT-001"}, {"event_id": "EVT-002"}],
        relations=[{"relation_id": "REL-001", "subject_id": "INTAKE-001"}],
        valid_subject_ids={"INTAKE-001"},
    )
    assert result["schema_version"] == "0.1"
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
