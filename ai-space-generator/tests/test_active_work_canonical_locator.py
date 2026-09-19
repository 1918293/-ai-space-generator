import json

import pytest

from src.active_work_canonical_locator import (
    ACTIVE_WORK_INDEX_ID,
    ACTIVE_WORK_TITLE,
    ActiveWorkIndexRangeSource,
    CanonicalActiveWorkSignalTextSource,
    active_work_document_id_from_index_values,
    load_active_work_index_range_source_json,
)


DOCUMENT_ID = "1bOnUQkIiFnjzJ7DNaL6OOSpYgV_LaSrrP9WfLS1z8qM"


def index_row(*, overrides=None):
    row = [
        ACTIVE_WORK_INDEX_ID,
        ACTIVE_WORK_TITLE,
        DOCUMENT_ID,
        "RUNTIME_COORDINATION",
        "PERSISTENT_ACTIVE_WORK_SIGNAL",
        "NON_AUTHORITY",
        "ACTIVE",
        "DERIVED_RECORD",
        "KEEP_ACTIVE",
        f"https://docs.google.com/document/d/{DOCUMENT_ID}/edit",
        "coordination only",
        "2026-09-17T10:51:26+08:00",
    ]
    for index, value in (overrides or {}).items():
        row[index] = value
    return [row]


class Reader:
    def __init__(self, *values, version="system-index-v1"):
        self.values = list(values)
        self.version = version
        self.read_calls = []
        self.version_calls = []

    def read_range(self, spreadsheet_id, range_a1):
        self.read_calls.append((spreadsheet_id, range_a1))
        return self.values.pop(0)

    def source_version(self, file_id):
        self.version_calls.append(file_id)
        return self.version


class DocSource:
    def __init__(self, text):
        self.text = text
        self.calls = 0

    def read_text(self):
        self.calls += 1
        return self.text


class Factory:
    def __init__(self, *texts):
        self.texts = list(texts)
        self.ids = []
        self.sources = []

    def __call__(self, document_id):
        self.ids.append(document_id)
        source = DocSource(self.texts.pop(0))
        self.sources.append(source)
        return source


def source():
    return ActiveWorkIndexRangeSource(
        spreadsheet_id="hao-system-sheet",
        range_a1="07_System_Index!A55:L55",
        source_file_id="hao-system-sheet",
    )


def test_exact_canonical_idx055_row_resolves_document_id():
    assert active_work_document_id_from_index_values(index_row()) == DOCUMENT_ID


@pytest.mark.parametrize(
    ("column", "value", "code"),
    [
        (0, "IDX-999", "ACTIVE_WORK_INDEX_ID_MISMATCH"),
        (1, "Wrong title", "ACTIVE_WORK_INDEX_TITLE_MISMATCH"),
        (3, "OTHER_DOMAIN", "ACTIVE_WORK_INDEX_DOMAIN_MISMATCH"),
        (4, "OTHER_ROLE", "ACTIVE_WORK_INDEX_ROLE_MISMATCH"),
        (5, "FORMAL_AUTHORITY", "ACTIVE_WORK_INDEX_AUTHORITY_MISMATCH"),
        (6, "ARCHIVED", "ACTIVE_WORK_INDEX_LIFECYCLE_MISMATCH"),
        (7, "PRIMARY", "ACTIVE_WORK_INDEX_RECORD_TYPE_MISMATCH"),
        (8, "ARCHIVE", "ACTIVE_WORK_INDEX_RETENTION_MISMATCH"),
    ],
)
def test_canonical_identity_or_lifecycle_mismatch_fails_closed(column, value, code):
    with pytest.raises(ValueError, match=code):
        active_work_document_id_from_index_values(index_row(overrides={column: value}))


def test_row_url_must_bind_to_same_document_id():
    with pytest.raises(ValueError, match="ACTIVE_WORK_INDEX_URL_DOCUMENT_ID_MISMATCH"):
        active_work_document_id_from_index_values(
            index_row(overrides={9: "https://docs.google.com/document/d/other/edit"})
        )


def test_multiple_or_missing_rows_are_not_accepted():
    with pytest.raises(ValueError, match="ACTIVE_WORK_INDEX_EXACT_SINGLE_ROW_REQUIRED"):
        active_work_document_id_from_index_values([])
    with pytest.raises(ValueError, match="ACTIVE_WORK_INDEX_EXACT_SINGLE_ROW_REQUIRED"):
        active_work_document_id_from_index_values(index_row() + index_row())


def test_locator_config_contains_only_index_location_not_document_id():
    parsed = load_active_work_index_range_source_json(
        json.dumps(
            {
                "spreadsheet_id": "hao-system-sheet",
                "range_a1": "07_System_Index!A55:L55",
                "source_file_id": "hao-system-sheet",
            }
        )
    )
    assert parsed == source()

    with pytest.raises(ValueError, match="ACTIVE_WORK_INDEX_SOURCE_UNKNOWN_FIELD:document_id"):
        load_active_work_index_range_source_json(
            json.dumps(
                {
                    "spreadsheet_id": "hao-system-sheet",
                    "range_a1": "07_System_Index!A55:L55",
                    "source_file_id": "hao-system-sheet",
                    "document_id": DOCUMENT_ID,
                }
            )
        )


def test_canonical_source_fresh_resolves_index_before_each_document_read():
    reader = Reader(index_row(), index_row())
    factory = Factory("SIGNAL-V1", "SIGNAL-V2")
    locator = CanonicalActiveWorkSignalTextSource(
        reader,
        source(),
        document_source_factory=factory,
    )

    assert locator.read_text() == "SIGNAL-V1"
    assert locator.read_text() == "SIGNAL-V2"
    assert reader.read_calls == [
        ("hao-system-sheet", "07_System_Index!A55:L55"),
        ("hao-system-sheet", "07_System_Index!A55:L55"),
    ]
    assert reader.version_calls == ["hao-system-sheet", "hao-system-sheet"]
    assert factory.ids == [DOCUMENT_ID, DOCUMENT_ID]


def test_canonical_source_fails_closed_without_source_version():
    reader = Reader(index_row(), version="")
    factory = Factory("SHOULD-NOT-READ")
    locator = CanonicalActiveWorkSignalTextSource(
        reader,
        source(),
        document_source_factory=factory,
    )

    assert locator.read_text() is None
    assert factory.ids == []


def test_canonical_source_fails_closed_on_mismatched_index_without_doc_read():
    reader = Reader(index_row(overrides={5: "FORMAL_AUTHORITY"}))
    factory = Factory("SHOULD-NOT-READ")
    locator = CanonicalActiveWorkSignalTextSource(
        reader,
        source(),
        document_source_factory=factory,
    )

    assert locator.read_text() is None
    assert factory.ids == []
