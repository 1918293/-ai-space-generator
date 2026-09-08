from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Iterable, Mapping, Protocol

from .context_bound_reasoning import AdmittedContextItem, ContextSemanticsResolver
from .control_gateway import PreModelContextReceipt, PreModelContextRequest
from .operational_state import ActiveOperationalState


_ALLOWED_SOURCE_KINDS = frozenset(
    {
        "CURRENT_CONTROL",
        "CONTINUATION",
        "EXISTING_WORK",
        "PRIOR_ATTEMPT",
        "REGRESSION",
    }
)
_MAX_CANONICAL_SUMMARY_CHARS = 1800


@dataclass(frozen=True)
class CanonicalSemanticRangeSource:
    """Deployment routing metadata for one existing canonical source range.

    The source content remains owned by the configured Google Sheet. This object
    only says where a receipt ref can be fresh-read and how trusted Runtime
    should classify that already-canonical evidence for first-model admission.
    """

    ref: str
    kind: str
    spreadsheet_id: str
    range_a1: str
    source_file_id: str
    project_scope: str = ""
    applicability: str = "APPLICABLE"
    disposition: str = "APPLY"
    binding_id: str = ""


class CanonicalRangeReader(Protocol):
    def read_range(self, spreadsheet_id: str, range_a1: str) -> object | None: ...

    def source_version(self, file_id: str) -> str: ...


def _source_from_mapping(item: Mapping[str, object]) -> CanonicalSemanticRangeSource:
    source = CanonicalSemanticRangeSource(
        ref=str(item.get("ref", "")).strip(),
        kind=str(item.get("kind", "")).strip().upper(),
        spreadsheet_id=str(item.get("spreadsheet_id", "")).strip(),
        range_a1=str(item.get("range_a1", "")).strip(),
        source_file_id=str(item.get("source_file_id", "")).strip(),
        project_scope=str(item.get("project_scope", "")).strip(),
        applicability=str(item.get("applicability", "APPLICABLE")).strip().upper(),
        disposition=str(item.get("disposition", "APPLY")).strip().upper(),
        binding_id=str(item.get("binding_id", "")).strip(),
    )
    if not source.ref:
        raise ValueError("CANONICAL_SEMANTIC_SOURCE_REF_REQUIRED")
    if source.kind not in _ALLOWED_SOURCE_KINDS:
        raise ValueError("CANONICAL_SEMANTIC_SOURCE_KIND_INVALID")
    if not source.spreadsheet_id or not source.range_a1 or not source.source_file_id:
        raise ValueError("CANONICAL_SEMANTIC_SOURCE_LOCATION_REQUIRED")
    return source


def load_canonical_semantic_sources(raw: object) -> tuple[CanonicalSemanticRangeSource, ...]:
    """Parse deployment config without storing semantic content in config."""

    if not isinstance(raw, list):
        raise ValueError("CANONICAL_SEMANTIC_SOURCES_LIST_REQUIRED")
    sources: list[CanonicalSemanticRangeSource] = []
    seen: set[tuple[str, str]] = set()
    for raw_item in raw:
        if not isinstance(raw_item, Mapping):
            raise ValueError("CANONICAL_SEMANTIC_SOURCE_OBJECT_REQUIRED")
        source = _source_from_mapping(raw_item)
        identity = (source.kind, source.ref)
        if identity in seen:
            raise ValueError("CANONICAL_SEMANTIC_SOURCE_DUPLICATE")
        seen.add(identity)
        sources.append(source)
    if not sources:
        raise ValueError("CANONICAL_SEMANTIC_SOURCES_REQUIRED")
    return tuple(sources)


def load_canonical_semantic_sources_json(raw: str) -> tuple[CanonicalSemanticRangeSource, ...]:
    value = raw.strip()
    if not value:
        raise ValueError("CANONICAL_SEMANTIC_SOURCES_JSON_REQUIRED")
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError("CANONICAL_SEMANTIC_SOURCES_JSON_INVALID") from exc
    return load_canonical_semantic_sources(decoded)


def _normalized_range_summary(values: object) -> str:
    """Create a deterministic bounded model-usable projection of exact cells.

    No truncation is allowed: if the configured exact range is too broad, the
    admission fails closed and deployment must narrow that existing-source range.
    """

    if not isinstance(values, list) or not values:
        raise ValueError("CANONICAL_SEMANTIC_RANGE_EMPTY")
    rows: list[list[object]] = []
    for raw_row in values:
        if not isinstance(raw_row, list):
            raise ValueError("CANONICAL_SEMANTIC_RANGE_ROWS_INVALID")
        row = list(raw_row)
        while row and row[-1] in (None, ""):
            row.pop()
        rows.append(row)
    while rows and not rows[-1]:
        rows.pop()
    if not rows:
        raise ValueError("CANONICAL_SEMANTIC_RANGE_EMPTY")
    summary = json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if len(summary) > _MAX_CANONICAL_SUMMARY_CHARS:
        raise ValueError("CANONICAL_SEMANTIC_RANGE_TOO_LARGE")
    return summary


def _required_source_identities(receipt: PreModelContextReceipt) -> tuple[set[tuple[str, str]], set[str]]:
    exact: set[tuple[str, str]] = set()
    for ref in receipt.existing_work_refs:
        exact.add(("EXISTING_WORK", ref))
    for ref in receipt.prior_attempt_refs:
        exact.add(("PRIOR_ATTEMPT", ref))
    for ref in receipt.regression_refs:
        exact.add(("REGRESSION", ref))
    return exact, set(receipt.authority_refs)


class ConfiguredCanonicalSemanticsResolver(ContextSemanticsResolver):
    """Fresh-read exact canonical ranges selected by the structural receipt.

    Retrieval routing metadata is configured, but semantic content and source
    version are fresh-read from the existing canonical provider on every call.
    The resolver never falls back to Handoff/model summaries/caches and never
    persists a second copy of the semantic content.
    """

    def __init__(
        self,
        reader: CanonicalRangeReader,
        sources: Iterable[CanonicalSemanticRangeSource],
    ) -> None:
        by_identity: dict[tuple[str, str], CanonicalSemanticRangeSource] = {}
        for source in tuple(sources):
            identity = (source.kind.strip().upper(), source.ref.strip())
            if identity in by_identity:
                raise ValueError("CANONICAL_SEMANTIC_SOURCE_DUPLICATE")
            by_identity[identity] = source
        if not by_identity:
            raise ValueError("CANONICAL_SEMANTIC_SOURCES_REQUIRED")
        self._reader = reader
        self._by_identity = by_identity

    def resolve(
        self,
        state: ActiveOperationalState,
        request: PreModelContextRequest,
        receipt: PreModelContextReceipt,
    ) -> tuple[AdmittedContextItem, ...] | None:
        del request
        if receipt.task != state.task or receipt.operational_version != state.version:
            return None

        exact_required, authority_refs = _required_source_identities(receipt)
        missing_exact = sorted(identity for identity in exact_required if identity not in self._by_identity)
        if missing_exact:
            return None

        authority_sources = [
            source
            for (kind, ref), source in self._by_identity.items()
            if kind in {"CURRENT_CONTROL", "CONTINUATION"} and ref in authority_refs
        ]
        if not authority_sources:
            return None

        selected: list[CanonicalSemanticRangeSource] = []
        selected.extend(authority_sources)
        for identity in sorted(exact_required):
            selected.append(self._by_identity[identity])

        admitted: list[AdmittedContextItem] = []
        try:
            for source in selected:
                values = self._reader.read_range(source.spreadsheet_id, source.range_a1)
                if values is None:
                    return None
                summary = _normalized_range_summary(values)
                version = self._reader.source_version(source.source_file_id).strip()
                if not version:
                    return None
                admitted.append(
                    AdmittedContextItem(
                        ref=source.ref,
                        kind=source.kind,
                        summary=summary,
                        source_version=version,
                        project_scope=source.project_scope,
                        applicability=source.applicability,
                        disposition=source.disposition,
                        binding_id=source.binding_id,
                    )
                )
        except (TypeError, ValueError):
            return None
        return tuple(admitted)


class GoogleWorkspaceCanonicalRangeReader:
    """ADC-backed read-only adapter for canonical Google Sheets semantics."""

    _SCOPES = (
        "https://www.googleapis.com/auth/drive.metadata.readonly",
        "https://www.googleapis.com/auth/spreadsheets.readonly",
    )

    def __init__(self, *, credentials: Any | None = None) -> None:
        if credentials is None:
            import google.auth

            credentials, _ = google.auth.default(scopes=list(self._SCOPES))
        from googleapiclient.discovery import build

        self._drive = build("drive", "v3", credentials=credentials, cache_discovery=False)
        self._sheets = build("sheets", "v4", credentials=credentials, cache_discovery=False)

    def read_range(self, spreadsheet_id: str, range_a1: str) -> object | None:
        try:
            result = (
                self._sheets.spreadsheets()
                .values()
                .get(
                    spreadsheetId=spreadsheet_id,
                    range=range_a1,
                    valueRenderOption="UNFORMATTED_VALUE",
                    dateTimeRenderOption="FORMATTED_STRING",
                )
                .execute()
            )
        except Exception:
            return None
        return result.get("values")

    def source_version(self, file_id: str) -> str:
        try:
            metadata = (
                self._drive.files()
                .get(fileId=file_id, fields="id,version,modifiedTime")
                .execute()
            )
        except Exception:
            return ""
        return str(metadata.get("version") or metadata.get("modifiedTime") or "").strip()
