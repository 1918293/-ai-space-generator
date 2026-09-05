from __future__ import annotations

import asyncio
from dataclasses import dataclass
from hashlib import sha256
import json
import re
from typing import Any, Iterable, Mapping

from .execution_control import ActionProposal, AuthorityStamp, authority_snapshot_fingerprint
from .google_drive_control import (
    DriveAuthorityReadback,
    DriveCommandResolver,
    DriveMutationCommand,
    DriveMutationReceipt,
    DriveStateReadback,
)


_GOOGLE_SCOPES = (
    "https://www.googleapis.com/auth/drive.metadata.readonly",
    "https://www.googleapis.com/auth/spreadsheets",
)


def _normalized_sheet_values(values: object) -> object:
    if not isinstance(values, list):
        return values
    rows: list[list[Any]] = []
    for raw_row in values:
        if not isinstance(raw_row, list):
            return values
        row = list(raw_row)
        while row and row[-1] in (None, ""):
            row.pop()
        rows.append(row)
    while rows and not rows[-1]:
        rows.pop()
    return rows


def canonical_values_digest(values: object) -> str:
    normalized = _normalized_sheet_values(values)
    payload = json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + sha256(payload.encode("utf-8")).hexdigest()


def _argument_map(proposal: ActionProposal) -> dict[str, str]:
    return {key: value for key, value in proposal.arguments}


def _binding_id(proposal: ActionProposal) -> str:
    parts = proposal.action_id.split(":", 2)
    if len(parts) != 3 or not parts[1].startswith("A"):
        return ""
    return parts[2]


def _validated_values_json(raw: str) -> tuple[list[list[Any]] | None, str]:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return None, "SHEETS_VALUES_JSON_INVALID"
    if not isinstance(value, list):
        return None, "SHEETS_VALUES_MUST_BE_ROWS"
    rows: list[list[Any]] = []
    for row in value:
        if not isinstance(row, list):
            return None, "SHEETS_VALUES_ROW_MUST_BE_LIST"
        normalized: list[Any] = []
        for item in row:
            if item is None or isinstance(item, (str, int, float, bool)):
                normalized.append(item)
            else:
                return None, "SHEETS_VALUE_SCALAR_REQUIRED"
        rows.append(normalized)
    return rows, ""


@dataclass(frozen=True)
class AuthorityFileSource:
    ref: str
    file_id: str


@dataclass(frozen=True)
class SheetsMutationTarget:
    binding_id: str
    spreadsheet_id: str
    range_a1: str
    value_input_option: str = "RAW"
    authority_sources: tuple[AuthorityFileSource, ...] = ()
    mutation_mode: str = "fixed_range"
    sheet_id: int | None = None
    unique_key_column: str = ""


_FULL_COLUMN_RANGE = re.compile(
    r"^(?P<sheet>'(?:[^']|'')+'|[^!]+)!(?P<start>[A-Z]+):(?P<end>[A-Z]+)$"
)


def _column_index(column: str) -> int:
    if not column or not column.isalpha() or not column.isupper():
        raise ValueError("SHEETS_UNIQUE_KEY_COLUMN_INVALID")
    result = 0
    for character in column:
        result = result * 26 + ord(character) - ord("A") + 1
    return result - 1


def _logical_append_columns(range_a1: str, unique_key_column: str) -> tuple[str, int, int, int]:
    match = _FULL_COLUMN_RANGE.fullmatch(range_a1)
    if match is None:
        raise ValueError("SHEETS_LOGICAL_APPEND_RANGE_INVALID")
    start = _column_index(match.group("start"))
    end = _column_index(match.group("end"))
    key = _column_index(unique_key_column)
    if end < start or not start <= key <= end:
        raise ValueError("SHEETS_UNIQUE_KEY_COLUMN_OUTSIDE_RANGE")
    return match.group("sheet"), start, end, key - start


class ConfiguredSheetsCommandResolver(DriveCommandResolver):
    """Bind one trusted ActionBinding to one exact configured Sheets target."""

    def __init__(self, targets: Iterable[SheetsMutationTarget]) -> None:
        by_binding: dict[str, SheetsMutationTarget] = {}
        for target in targets:
            key = target.binding_id.strip()
            if not key or key in by_binding:
                raise ValueError("INVALID_OR_DUPLICATE_SHEETS_BINDING")
            if not target.spreadsheet_id.strip() or not target.range_a1.strip():
                raise ValueError("SHEETS_TARGET_REQUIRED")
            if target.value_input_option not in {"RAW", "USER_ENTERED"}:
                raise ValueError("SHEETS_VALUE_INPUT_OPTION_INVALID")
            if target.mutation_mode not in {"fixed_range", "logical_append"}:
                raise ValueError("SHEETS_MUTATION_MODE_INVALID")
            if target.mutation_mode == "logical_append":
                if target.sheet_id is None or target.sheet_id < 0:
                    raise ValueError("SHEETS_LOGICAL_APPEND_SHEET_ID_REQUIRED")
                if target.value_input_option != "RAW":
                    raise ValueError("SHEETS_LOGICAL_APPEND_RAW_REQUIRED")
                _logical_append_columns(target.range_a1.strip(), target.unique_key_column.strip())
            by_binding[key] = target
        self._by_binding = by_binding

    def resolve(self, proposal: ActionProposal) -> DriveMutationCommand | None:
        binding_id = _binding_id(proposal)
        target = self._by_binding.get(binding_id)
        if target is None:
            return None
        arguments = _argument_map(proposal)
        values, error = _validated_values_json(arguments.get("values_json", ""))
        if error or values is None:
            return None
        expected_digest = canonical_values_digest(values)
        trusted_payload = {
            "binding_id": binding_id,
            "spreadsheet_id": target.spreadsheet_id.strip(),
            "range_a1": target.range_a1.strip(),
            "value_input_option": target.value_input_option,
            "mutation_mode": target.mutation_mode,
            "values_json": json.dumps(values, ensure_ascii=False, separators=(",", ":")),
            "authority_sources_json": json.dumps(
                [
                    {"ref": source.ref.strip(), "file_id": source.file_id.strip()}
                    for source in target.authority_sources
                    if source.ref.strip() and source.file_id.strip()
                ],
                ensure_ascii=False,
                separators=(",", ":"),
            ),
        }
        if target.mutation_mode == "logical_append":
            trusted_payload["sheet_id"] = str(target.sheet_id)
            trusted_payload["unique_key_column"] = target.unique_key_column.strip()
        return DriveMutationCommand(
            action_id=proposal.action_id,
            provider=proposal.provider,
            action_name=proposal.action_name,
            target_ref=f"google-sheets:{target.spreadsheet_id.strip()}:{target.range_a1.strip()}",
            expected_state_delta=proposal.expected_state_delta,
            expected_state_digest=expected_digest,
            authorization_scope=proposal.authorization_scope,
            authorization_ref=f"runtime-bound-scope:{proposal.authorization_scope}",
            authority_snapshot_fingerprint=proposal.authority_snapshot_fingerprint,
            payload=tuple(sorted(trusted_payload.items())),
        )


def _payload(command: DriveMutationCommand) -> dict[str, str]:
    return {key: value for key, value in command.payload}


class GoogleWorkspaceSheetsClient:
    """ADC-backed real Google Drive/Sheets provider for Runtime v2."""

    def __init__(self, *, credentials: Any | None = None) -> None:
        if credentials is None:
            import google.auth

            credentials, _ = google.auth.default(scopes=list(_GOOGLE_SCOPES))
        from googleapiclient.discovery import build

        self._drive = build("drive", "v3", credentials=credentials, cache_discovery=False)
        self._sheets = build("sheets", "v4", credentials=credentials, cache_discovery=False)

    @staticmethod
    def _authority_sources(command: DriveMutationCommand) -> tuple[AuthorityFileSource, ...]:
        raw = _payload(command).get("authority_sources_json", "[]")
        try:
            items = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("AUTHORITY_SOURCES_JSON_INVALID") from exc
        if not isinstance(items, list):
            raise ValueError("AUTHORITY_SOURCES_LIST_REQUIRED")
        result: list[AuthorityFileSource] = []
        for item in items:
            if not isinstance(item, Mapping):
                raise ValueError("AUTHORITY_SOURCE_OBJECT_REQUIRED")
            ref = str(item.get("ref", "")).strip()
            file_id = str(item.get("file_id", "")).strip()
            if not ref or not file_id:
                raise ValueError("AUTHORITY_SOURCE_FIELDS_REQUIRED")
            result.append(AuthorityFileSource(ref, file_id))
        return tuple(result)

    def current_authority_stamps_sync(
        self, sources: Iterable[AuthorityFileSource]
    ) -> tuple[AuthorityStamp, ...]:
        stamps: list[AuthorityStamp] = []
        for source in tuple(sources):
            metadata = (
                self._drive.files()
                .get(fileId=source.file_id, fields="id,version,modifiedTime")
                .execute()
            )
            version = str(metadata.get("version") or metadata.get("modifiedTime") or "").strip()
            if not version:
                raise ValueError("GOOGLE_DRIVE_AUTHORITY_VERSION_UNRESOLVED")
            stamps.append(AuthorityStamp(source.ref, version))
        return tuple(stamps)

    async def current_authority_stamps(
        self, sources: Iterable[AuthorityFileSource]
    ) -> tuple[AuthorityStamp, ...]:
        return await asyncio.to_thread(self.current_authority_stamps_sync, tuple(sources))

    async def preflight_authority(self, command: DriveMutationCommand) -> DriveAuthorityReadback:
        try:
            sources = self._authority_sources(command)
            if not sources:
                return DriveAuthorityReadback(
                    "", "google-drive:files.get", False, "AUTHORITY_SOURCES_REQUIRED"
                )
            stamps = await self.current_authority_stamps(sources)
            fingerprint = authority_snapshot_fingerprint(
                stamps,
                tuple(source.ref for source in sources),
            )
            return DriveAuthorityReadback(
                fingerprint,
                "google-drive:files.get",
                bool(fingerprint),
                "" if fingerprint else "AUTHORITY_FINGERPRINT_UNRESOLVED",
            )
        except Exception as exc:
            return DriveAuthorityReadback(
                "",
                "google-drive:files.get",
                False,
                f"GOOGLE_DRIVE_AUTHORITY_READ_FAILED:{type(exc).__name__}",
            )

    @staticmethod
    def _mutation_input(command: DriveMutationCommand) -> tuple[str, str, str, list[list[Any]]]:
        payload = _payload(command)
        spreadsheet_id = payload.get("spreadsheet_id", "").strip()
        range_a1 = payload.get("range_a1", "").strip()
        value_input_option = payload.get("value_input_option", "RAW").strip()
        values, error = _validated_values_json(payload.get("values_json", ""))
        if not spreadsheet_id or not range_a1 or error or values is None:
            raise ValueError(error or "SHEETS_TRUSTED_TARGET_INVALID")
        return spreadsheet_id, range_a1, value_input_option, values

    @staticmethod
    def _logical_append_input(
        command: DriveMutationCommand,
    ) -> tuple[str, str, int, str, int, int, int, list[Any]] | None:
        payload = _payload(command)
        if payload.get("mutation_mode", "fixed_range") == "fixed_range":
            return None
        if payload.get("mutation_mode") != "logical_append":
            raise ValueError("SHEETS_MUTATION_MODE_INVALID")
        spreadsheet_id, range_a1, value_input_option, values = GoogleWorkspaceSheetsClient._mutation_input(
            command
        )
        if value_input_option != "RAW" or len(values) != 1:
            raise ValueError("SHEETS_LOGICAL_APPEND_ONE_RAW_ROW_REQUIRED")
        try:
            sheet_id = int(payload.get("sheet_id", ""))
        except ValueError as exc:
            raise ValueError("SHEETS_LOGICAL_APPEND_SHEET_ID_INVALID") from exc
        sheet, start_column, end_column, key_offset = _logical_append_columns(
            range_a1, payload.get("unique_key_column", "").strip()
        )
        row = values[0]
        if len(row) > end_column - start_column + 1:
            raise ValueError("SHEETS_LOGICAL_APPEND_ROW_TOO_WIDE")
        return spreadsheet_id, range_a1, sheet_id, sheet, start_column, end_column, key_offset, row

    @staticmethod
    def _cell_data(value: Any) -> dict[str, Any]:
        if value is None:
            return {}
        if isinstance(value, bool):
            return {"userEnteredValue": {"boolValue": value}}
        if isinstance(value, (int, float)):
            return {"userEnteredValue": {"numberValue": value}}
        return {"userEnteredValue": {"stringValue": value}}

    def _sheet_values(self, spreadsheet_id: str, range_a1: str) -> list[list[Any]]:
        result = (
            self._sheets.spreadsheets()
            .values()
            .get(
                spreadsheetId=spreadsheet_id,
                range=range_a1,
                valueRenderOption="UNFORMATTED_VALUE",
                dateTimeRenderOption="SERIAL_NUMBER",
            )
            .execute()
        )
        values = result.get("values", [])
        if not isinstance(values, list) or any(not isinstance(row, list) for row in values):
            raise ValueError("GOOGLE_SHEETS_VALUES_INVALID")
        return values

    def _mutate_sync(self, command: DriveMutationCommand) -> DriveMutationReceipt:
        logical_append = self._logical_append_input(command)
        if logical_append is not None:
            return self._logical_append_sync(command, logical_append)
        spreadsheet_id, range_a1, value_input_option, values = self._mutation_input(command)
        try:
            result = (
                self._sheets.spreadsheets()
                .values()
                .update(
                    spreadsheetId=spreadsheet_id,
                    range=range_a1,
                    valueInputOption=value_input_option,
                    includeValuesInResponse=True,
                    body={"values": values},
                )
                .execute()
            )
        except Exception as exc:
            try:
                from googleapiclient.errors import HttpError

                if isinstance(exc, HttpError) and int(getattr(exc.resp, "status", 0)) in {
                    400,
                    401,
                    403,
                    404,
                }:
                    return DriveMutationReceipt(
                        False,
                        error_code=f"GOOGLE_SHEETS_REJECTED:{int(exc.resp.status)}",
                        no_effect_confirmed=True,
                    )
            except Exception:
                pass
            raise

        updated_range = str(result.get("updatedRange", range_a1))
        updated_cells = int(result.get("updatedCells", 0) or 0)
        receipt_material = f"{spreadsheet_id}:{updated_range}:{updated_cells}:{command.action_id}"
        receipt_id = "GSHEETS-" + sha256(receipt_material.encode("utf-8")).hexdigest()[:32]
        return DriveMutationReceipt(True, receipt_id, "google-sheets:spreadsheets.values.update")

    def _logical_append_sync(
        self,
        command: DriveMutationCommand,
        mutation: tuple[str, str, int, str, int, int, int, list[Any]],
    ) -> DriveMutationReceipt:
        spreadsheet_id, range_a1, sheet_id, _, start_column, _, key_offset, row = mutation
        key = row[key_offset] if key_offset < len(row) else None
        if key is None or (isinstance(key, str) and not key.strip()):
            return DriveMutationReceipt(
                False, error_code="SHEETS_LOGICAL_APPEND_KEY_REQUIRED", no_effect_confirmed=True
            )
        try:
            current_values = self._sheet_values(spreadsheet_id, range_a1)
        except Exception as exc:
            return DriveMutationReceipt(
                False,
                error_code=f"GOOGLE_SHEETS_APPEND_PREFLIGHT_FAILED:{type(exc).__name__}",
                no_effect_confirmed=True,
            )
        existing_keys = [
            existing[key_offset]
            for existing in current_values
            if key_offset < len(existing)
        ]
        if key in existing_keys:
            return DriveMutationReceipt(
                False, error_code="SHEETS_LOGICAL_APPEND_KEY_DUPLICATE", no_effect_confirmed=True
            )
        row_index = len(_normalized_sheet_values(current_values))
        body = {
            "requests": [
                {
                    "insertDimension": {
                        "range": {
                            "sheetId": sheet_id,
                            "dimension": "ROWS",
                            "startIndex": row_index,
                            "endIndex": row_index + 1,
                        },
                        "inheritFromBefore": row_index > 0,
                    }
                },
                {
                    "updateCells": {
                        "range": {
                            "sheetId": sheet_id,
                            "startRowIndex": row_index,
                            "endRowIndex": row_index + 1,
                            "startColumnIndex": start_column,
                            "endColumnIndex": start_column + len(row),
                        },
                        "rows": [{"values": [self._cell_data(value) for value in row]}],
                        "fields": "userEnteredValue",
                    }
                },
            ]
        }
        try:
            self._sheets.spreadsheets().batchUpdate(
                spreadsheetId=spreadsheet_id, body=body
            ).execute()
        except Exception:
            # A transport failure can hide a successful atomic mutation. The caller must
            # reconcile by stable key and must not blindly replay this append.
            raise
        receipt_material = f"{spreadsheet_id}:{sheet_id}:{row_index}:{key}:{command.action_id}"
        receipt_id = "GSHEETS-" + sha256(receipt_material.encode("utf-8")).hexdigest()[:32]
        return DriveMutationReceipt(True, receipt_id, "google-sheets:spreadsheets.batchUpdate")

    async def mutate(self, command: DriveMutationCommand) -> DriveMutationReceipt:
        return await asyncio.to_thread(self._mutate_sync, command)

    def _readback_sync(self, command: DriveMutationCommand) -> DriveStateReadback:
        logical_append = self._logical_append_input(command)
        if logical_append is not None:
            return self._logical_append_readback(command, logical_append)
        spreadsheet_id, range_a1, _, _ = self._mutation_input(command)
        result = (
            self._sheets.spreadsheets()
            .values()
            .get(
                spreadsheetId=spreadsheet_id,
                range=range_a1,
                valueRenderOption="UNFORMATTED_VALUE",
                dateTimeRenderOption="SERIAL_NUMBER",
            )
            .execute()
        )
        values = result.get("values", [])
        digest = canonical_values_digest(values)
        return DriveStateReadback(
            digest,
            "google-sheets:spreadsheets.values.get",
            digest == command.expected_state_digest,
            "" if digest == command.expected_state_digest else "GOOGLE_SHEETS_READBACK_MISMATCH",
        )

    def _logical_append_readback(
        self,
        command: DriveMutationCommand,
        mutation: tuple[str, str, int, str, int, int, int, list[Any]],
    ) -> DriveStateReadback:
        spreadsheet_id, range_a1, _, sheet, start_column, end_column, key_offset, expected_row = mutation
        key = expected_row[key_offset] if key_offset < len(expected_row) else None
        values = self._sheet_values(spreadsheet_id, range_a1)
        matching_rows = [
            index
            for index, row in enumerate(values)
            if key_offset < len(row) and row[key_offset] == key
        ]
        if len(matching_rows) != 1:
            return DriveStateReadback(
                "",
                "google-sheets:spreadsheets.values.get",
                False,
                "GOOGLE_SHEETS_APPEND_KEY_NOT_EXACTLY_ONCE",
            )
        row_number = matching_rows[0] + 1
        start_name = _payload(command)["range_a1"].split("!", 1)[1].split(":", 1)[0]
        end_name = _payload(command)["range_a1"].rsplit(":", 1)[1]
        exact_range = f"{sheet}!{start_name}{row_number}:{end_name}{row_number}"
        exact_values = self._sheet_values(spreadsheet_id, exact_range)
        digest = canonical_values_digest(exact_values)
        return DriveStateReadback(
            digest,
            "google-sheets:spreadsheets.values.get",
            digest == command.expected_state_digest,
            "" if digest == command.expected_state_digest else "GOOGLE_SHEETS_READBACK_MISMATCH",
        )

    async def readback(self, command: DriveMutationCommand) -> DriveStateReadback:
        try:
            return await asyncio.to_thread(self._readback_sync, command)
        except Exception as exc:
            return DriveStateReadback(
                "",
                "google-sheets:spreadsheets.values.get",
                False,
                f"GOOGLE_SHEETS_READBACK_FAILED:{type(exc).__name__}",
            )
