from __future__ import annotations

import asyncio
from dataclasses import dataclass
from hashlib import sha256
import json
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

    def _mutate_sync(self, command: DriveMutationCommand) -> DriveMutationReceipt:
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

    async def mutate(self, command: DriveMutationCommand) -> DriveMutationReceipt:
        return await asyncio.to_thread(self._mutate_sync, command)

    def _readback_sync(self, command: DriveMutationCommand) -> DriveStateReadback:
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
