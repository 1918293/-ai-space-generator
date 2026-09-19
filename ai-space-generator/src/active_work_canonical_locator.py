from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Callable, Mapping, Protocol

from .canonical_semantic_reader import CanonicalRangeReader
from .google_docs_active_work_source import GoogleDocsActiveWorkSignalTextSource


ACTIVE_WORK_INDEX_ID = "IDX-055"
ACTIVE_WORK_TITLE = "Hao System｜Active Work Signal｜EXP_NON_AUTHORITY｜v0.1"
ACTIVE_WORK_DOMAIN = "RUNTIME_COORDINATION"
ACTIVE_WORK_ROLE = "PERSISTENT_ACTIVE_WORK_SIGNAL"
ACTIVE_WORK_AUTHORITY = "NON_AUTHORITY"
ACTIVE_WORK_LIFECYCLE = "ACTIVE"
ACTIVE_WORK_RECORD_TYPE = "DERIVED_RECORD"
ACTIVE_WORK_RETENTION = "KEEP_ACTIVE"


@dataclass(frozen=True)
class ActiveWorkIndexRangeSource:
    """Deployment locator for the canonical System Index row, not the Signal Doc.

    The deployment may say where to fresh-read the existing canonical IDX-055 row;
    it may not directly assert the Active Work document id. The document id is
    accepted only after the row identity and lifecycle contract validate.
    """

    spreadsheet_id: str
    range_a1: str
    source_file_id: str


class ActiveWorkDocumentTextSource(Protocol):
    def read_text(self) -> str | None: ...


def load_active_work_index_range_source(raw: object) -> ActiveWorkIndexRangeSource:
    if not isinstance(raw, Mapping):
        raise ValueError("ACTIVE_WORK_INDEX_SOURCE_OBJECT_REQUIRED")
    allowed = {"spreadsheet_id", "range_a1", "source_file_id"}
    unknown = sorted(str(key) for key in set(raw) - allowed)
    if unknown:
        raise ValueError("ACTIVE_WORK_INDEX_SOURCE_UNKNOWN_FIELD:" + ",".join(unknown))

    source = ActiveWorkIndexRangeSource(
        spreadsheet_id=str(raw.get("spreadsheet_id", "")).strip(),
        range_a1=str(raw.get("range_a1", "")).strip(),
        source_file_id=str(raw.get("source_file_id", "")).strip(),
    )
    if not source.spreadsheet_id or not source.range_a1 or not source.source_file_id:
        raise ValueError("ACTIVE_WORK_INDEX_SOURCE_LOCATION_REQUIRED")
    return source


def load_active_work_index_range_source_json(raw: str) -> ActiveWorkIndexRangeSource:
    value = raw.strip()
    if not value:
        raise ValueError("ACTIVE_WORK_INDEX_SOURCE_JSON_REQUIRED")
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError("ACTIVE_WORK_INDEX_SOURCE_JSON_INVALID") from exc
    return load_active_work_index_range_source(decoded)


def _required_cell(row: list[object], index: int, code: str) -> str:
    if index >= len(row):
        raise ValueError(code)
    value = str(row[index]).strip()
    if not value:
        raise ValueError(code)
    return value


def active_work_document_id_from_index_values(values: object) -> str:
    """Return the Signal Doc id only from an exact valid canonical IDX-055 row."""

    if not isinstance(values, list) or len(values) != 1 or not isinstance(values[0], list):
        raise ValueError("ACTIVE_WORK_INDEX_EXACT_SINGLE_ROW_REQUIRED")
    row = values[0]

    expected = (
        (0, ACTIVE_WORK_INDEX_ID, "ACTIVE_WORK_INDEX_ID_MISMATCH"),
        (1, ACTIVE_WORK_TITLE, "ACTIVE_WORK_INDEX_TITLE_MISMATCH"),
        (3, ACTIVE_WORK_DOMAIN, "ACTIVE_WORK_INDEX_DOMAIN_MISMATCH"),
        (4, ACTIVE_WORK_ROLE, "ACTIVE_WORK_INDEX_ROLE_MISMATCH"),
        (5, ACTIVE_WORK_AUTHORITY, "ACTIVE_WORK_INDEX_AUTHORITY_MISMATCH"),
        (6, ACTIVE_WORK_LIFECYCLE, "ACTIVE_WORK_INDEX_LIFECYCLE_MISMATCH"),
        (7, ACTIVE_WORK_RECORD_TYPE, "ACTIVE_WORK_INDEX_RECORD_TYPE_MISMATCH"),
        (8, ACTIVE_WORK_RETENTION, "ACTIVE_WORK_INDEX_RETENTION_MISMATCH"),
    )
    for index, required, code in expected:
        if _required_cell(row, index, code) != required:
            raise ValueError(code)

    document_id = _required_cell(row, 2, "ACTIVE_WORK_INDEX_DOCUMENT_ID_REQUIRED")
    if document_id == "NONE" or any(character.isspace() for character in document_id):
        raise ValueError("ACTIVE_WORK_INDEX_DOCUMENT_ID_INVALID")

    if len(row) > 9 and str(row[9]).strip():
        url = str(row[9]).strip()
        if f"/document/d/{document_id}/" not in url:
            raise ValueError("ACTIVE_WORK_INDEX_URL_DOCUMENT_ID_MISMATCH")
    return document_id


class CanonicalActiveWorkSignalTextSource:
    """Fresh resolve IDX-055, then fresh-read the resolved Google Doc.

    This remains a read-only locator/source adapter. It does not cache a resolved
    document id, create a registry, own Authority, mint work identity, or mutate
    the Signal. A stale/missing/malformed System Index row returns None so the
    existing Active Work admission resolver fails closed to UNKNOWN visibility.
    """

    def __init__(
        self,
        reader: CanonicalRangeReader,
        index_source: ActiveWorkIndexRangeSource,
        *,
        document_source_factory: Callable[[str], ActiveWorkDocumentTextSource] | None = None,
    ) -> None:
        self._reader = reader
        self._index_source = index_source
        self._document_source_factory = (
            document_source_factory or (lambda document_id: GoogleDocsActiveWorkSignalTextSource(document_id))
        )

    def read_text(self) -> str | None:
        try:
            version = self._reader.source_version(self._index_source.source_file_id).strip()
            if not version:
                return None
            values = self._reader.read_range(
                self._index_source.spreadsheet_id,
                self._index_source.range_a1,
            )
            document_id = active_work_document_id_from_index_values(values)
            return self._document_source_factory(document_id).read_text()
        except (TypeError, ValueError):
            return None
