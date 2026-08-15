from src.contracts import validate_event_id_unique, validate_relation_integrity


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
