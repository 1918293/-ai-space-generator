import asyncio

from src.action_catalog import ActionBinding, ActionCatalog, ModelActionIntent, resolve_model_intent
from src.execution_control import (
    ActionArchetype,
    ActionExternality,
    AuthorityStamp,
    ExecutionRecord,
    Mode,
    authority_snapshot_fingerprint,
)
from src.google_workspace_adapter import (
    AuthorityFileSource,
    ConfiguredSheetsCommandResolver,
    GoogleWorkspaceSheetsClient,
    SheetsMutationTarget,
    canonical_values_digest,
)


def record():
    return ExecutionRecord(
        run_id="RUN-GWORKSPACE",
        task="Controlled Sheets write",
        mode=Mode.EXP,
        goal_valid=True,
        acceptance_criteria=("exact range matches",),
        authority_refs=("AUTH:MAIN",),
        authority_stamps=(AuthorityStamp("AUTH:MAIN", "17"),),
        required_action_authority_refs=("AUTH:MAIN",),
    )


def binding():
    return ActionBinding(
        binding_id="drive.formal_cells.update",
        capability="formal_persistence",
        provider="google-drive",
        action_name="update_cells",
        archetype=ActionArchetype.MUTATE,
        externality=ActionExternality.PRIVATE_REVERSIBLE,
        authorization_scope_prefix="HAO_DRIVE_WRITE",
        rollback_available=True,
        allowed_argument_keys=("values_json",),
        required_argument_keys=("values_json",),
    )


def proposal():
    result = resolve_model_intent(
        record(),
        ModelActionIntent(
            "INTENT-1",
            "formal_persistence",
            "drive.formal_cells.update",
            expected_state_delta="replace exact configured range",
            authorization_target="formal-record",
            arguments=(("values_json", '[["alpha",1],["beta",2]]'),),
        ),
        ActionCatalog((binding(),)),
        sequence=1,
    )
    assert result.proposal is not None
    return result.proposal


def test_model_arguments_are_allowlisted_and_required_by_trusted_binding():
    allowed = proposal()
    assert allowed.arguments == (("values_json", '[["alpha",1],["beta",2]]'),)

    unknown = resolve_model_intent(
        record(),
        ModelActionIntent(
            "INTENT-2",
            "formal_persistence",
            "drive.formal_cells.update",
            expected_state_delta="replace exact configured range",
            arguments=(("spreadsheet_id", "attacker-selected"),),
        ),
        ActionCatalog((binding(),)),
        sequence=1,
    )
    assert unknown.proposal is None
    assert unknown.decision.code == "MODEL_ARGUMENT_NOT_ALLOWED:spreadsheet_id"

    missing = resolve_model_intent(
        record(),
        ModelActionIntent(
            "INTENT-3",
            "formal_persistence",
            "drive.formal_cells.update",
            expected_state_delta="replace exact configured range",
        ),
        ActionCatalog((binding(),)),
        sequence=1,
    )
    assert missing.proposal is None
    assert missing.decision.code == "MODEL_ARGUMENT_REQUIRED:values_json"


def test_configured_resolver_owns_target_and_authority_sources():
    resolver = ConfiguredSheetsCommandResolver(
        (
            SheetsMutationTarget(
                binding_id="drive.formal_cells.update",
                spreadsheet_id="trusted-sheet",
                range_a1="01_Intake!A10:B11",
                authority_sources=(AuthorityFileSource("AUTH:MAIN", "trusted-sheet"),),
            ),
        )
    )
    command = resolver.resolve(proposal())
    assert command is not None
    payload = dict(command.payload)
    assert payload["spreadsheet_id"] == "trusted-sheet"
    assert payload["range_a1"] == "01_Intake!A10:B11"
    assert command.expected_state_digest == canonical_values_digest(
        [["alpha", 1], ["beta", 2]]
    )
    assert "attacker-selected" not in command.target_ref


class Request:
    def __init__(self, result):
        self.result = result

    def execute(self):
        return self.result


class FilesAPI:
    def __init__(self, versions):
        self.versions = versions
        self.calls = []

    def get(self, *, fileId, fields):
        self.calls.append((fileId, fields))
        return Request({"id": fileId, "version": self.versions[fileId]})


class DriveService:
    def __init__(self, versions):
        self.api = FilesAPI(versions)

    def files(self):
        return self.api


class ValuesAPI:
    def __init__(self):
        self.update_calls = []
        self.get_calls = []
        self.values = [["alpha", 1], ["beta", 2]]

    def update(self, **kwargs):
        self.update_calls.append(kwargs)
        return Request(
            {
                "updatedRange": kwargs["range"],
                "updatedCells": 4,
                "updatedData": {"values": kwargs["body"]["values"]},
            }
        )

    def get(self, **kwargs):
        self.get_calls.append(kwargs)
        return Request({"range": kwargs["range"], "values": self.values})


class SpreadsheetsAPI:
    def __init__(self, values_api):
        self.values_api = values_api
        self.batch_update_calls = []
        self.get_calls = []
        self.sheets = [{"properties": {"sheetId": 123, "title": "01_Intake"}}]

    def values(self):
        return self.values_api

    def get(self, **kwargs):
        self.get_calls.append(kwargs)
        return Request({"sheets": self.sheets})

    def batchUpdate(self, **kwargs):
        self.batch_update_calls.append(kwargs)
        return Request({"replies": [{}, {}]})


class SheetsService:
    def __init__(self):
        self.values_api = ValuesAPI()
        self.spreadsheets_api = SpreadsheetsAPI(self.values_api)

    def spreadsheets(self):
        return self.spreadsheets_api


def client_with_fakes():
    client = object.__new__(GoogleWorkspaceSheetsClient)
    client._drive = DriveService({"trusted-sheet": "17"})
    client._sheets = SheetsService()
    return client


def test_real_adapter_preflight_mutation_and_exact_readback_use_configured_target():
    current_proposal = proposal()
    resolver = ConfiguredSheetsCommandResolver(
        (
            SheetsMutationTarget(
                binding_id="drive.formal_cells.update",
                spreadsheet_id="trusted-sheet",
                range_a1="01_Intake!A10:B11",
                authority_sources=(AuthorityFileSource("AUTH:MAIN", "trusted-sheet"),),
            ),
        )
    )
    command = resolver.resolve(current_proposal)
    assert command is not None
    client = client_with_fakes()

    preflight = asyncio.run(client.preflight_authority(command))
    assert preflight.passed is True
    assert preflight.current_fingerprint == authority_snapshot_fingerprint(
        (AuthorityStamp("AUTH:MAIN", "17"),),
        ("AUTH:MAIN",),
    )

    mutation = asyncio.run(client.mutate(command))
    assert mutation.success is True
    assert mutation.receipt_id.startswith("GSHEETS-")
    update = client._sheets.values_api.update_calls[0]
    assert update["spreadsheetId"] == "trusted-sheet"
    assert update["range"] == "01_Intake!A10:B11"
    assert update["includeValuesInResponse"] is True
    assert update["body"] == {"values": [["alpha", 1], ["beta", 2]]}

    readback = asyncio.run(client.readback(command))
    assert readback.matched is True
    assert readback.state_digest == command.expected_state_digest
    get_call = client._sheets.values_api.get_calls[0]
    assert get_call["spreadsheetId"] == "trusted-sheet"
    assert get_call["range"] == "01_Intake!A10:B11"


def logical_append_command(values_json='[["REC-2","new"]]'):
    current_proposal = proposal()
    current_proposal = type(current_proposal)(
        **{
            **current_proposal.__dict__,
            "arguments": (("values_json", values_json),),
        }
    )
    resolver = ConfiguredSheetsCommandResolver(
        (
            SheetsMutationTarget(
                binding_id="drive.formal_cells.update",
                spreadsheet_id="trusted-sheet",
                range_a1="01_Intake!A:B",
                authority_sources=(AuthorityFileSource("AUTH:MAIN", "trusted-sheet"),),
                mutation_mode="logical_append",
                sheet_id=123,
                unique_key_column="A",
            ),
        )
    )
    command = resolver.resolve(current_proposal)
    assert command is not None
    return command


def test_logical_append_resolves_fresh_tail_and_uses_one_atomic_batch():
    command = logical_append_command()
    client = client_with_fakes()
    client._sheets.values_api.values = [["record_id", "value"], ["REC-1", "old"]]

    mutation = asyncio.run(client.mutate(command))

    assert mutation.success is True
    assert mutation.source == "google-sheets:spreadsheets.batchUpdate"
    assert client._sheets.values_api.update_calls == []
    assert client._sheets.values_api.get_calls[0]["range"] == "01_Intake!A:B"
    batch = client._sheets.spreadsheets_api.batch_update_calls[0]
    assert batch["spreadsheetId"] == "trusted-sheet"
    assert batch["body"] == {
        "requests": [
            {
                "insertDimension": {
                    "range": {
                        "sheetId": 123,
                        "dimension": "ROWS",
                        "startIndex": 2,
                        "endIndex": 3,
                    },
                    "inheritFromBefore": True,
                }
            },
            {
                "updateCells": {
                    "range": {
                        "sheetId": 123,
                        "startRowIndex": 2,
                        "endRowIndex": 3,
                        "startColumnIndex": 0,
                        "endColumnIndex": 2,
                    },
                    "rows": [
                        {
                            "values": [
                                {"userEnteredValue": {"stringValue": "REC-2"}},
                                {"userEnteredValue": {"stringValue": "new"}},
                            ]
                        }
                    ],
                    "fields": "userEnteredValue",
                }
            },
        ]
    }


def test_logical_append_blocks_blank_duplicate_and_multiple_rows_before_mutation():
    client = client_with_fakes()

    blank = asyncio.run(client.mutate(logical_append_command('[["","new"]]')))
    assert blank.success is False
    assert blank.error_code == "SHEETS_LOGICAL_APPEND_KEY_REQUIRED"
    assert blank.no_effect_confirmed is True

    client._sheets.values_api.values = [["record_id", "value"], ["REC-2", "old"]]
    duplicate = asyncio.run(client.mutate(logical_append_command()))
    assert duplicate.success is False
    assert duplicate.error_code == "SHEETS_LOGICAL_APPEND_KEY_DUPLICATE"
    assert duplicate.no_effect_confirmed is True

    try:
        asyncio.run(client.mutate(logical_append_command('[["REC-2"],["REC-3"]]')))
    except ValueError as exc:
        assert str(exc) == "SHEETS_LOGICAL_APPEND_ONE_RAW_ROW_REQUIRED"
    else:
        raise AssertionError("multiple append rows must fail closed")
    assert client._sheets.spreadsheets_api.batch_update_calls == []


def test_logical_append_readback_requires_one_key_and_exact_canonical_row_from_one_snapshot():
    command = logical_append_command()
    client = client_with_fakes()
    client._sheets.values_api.values = [["record_id", "value"], ["REC-2", "new"]]

    readback = asyncio.run(client.readback(command))
    assert readback.matched is True
    assert len(client._sheets.values_api.get_calls) == 1
    assert client._sheets.values_api.get_calls[0]["range"] == "01_Intake!A:B"

    client = client_with_fakes()
    client._sheets.values_api.values = [["REC-2", "new"], ["REC-2", "new"]]
    duplicate = asyncio.run(client.readback(command))
    assert duplicate.matched is False
    assert duplicate.error_code == "GOOGLE_SHEETS_APPEND_KEY_NOT_EXACTLY_ONCE"

    client = client_with_fakes()
    client._sheets.values_api.values = [["REC-2", "wrong"]]
    mismatch = asyncio.run(client.readback(command))
    assert mismatch.matched is False
    assert mismatch.error_code == "GOOGLE_SHEETS_READBACK_MISMATCH"


def test_logical_append_target_coordinates_are_deployment_owned():
    command = logical_append_command()
    payload = dict(command.payload)
    assert payload["spreadsheet_id"] == "trusted-sheet"
    assert payload["range_a1"] == "01_Intake!A:B"
    assert payload["sheet_id"] == "123"
    assert payload["unique_key_column"] == "A"

    try:
        ConfiguredSheetsCommandResolver(
            (
                SheetsMutationTarget(
                    binding_id="drive.formal_cells.update",
                    spreadsheet_id="trusted-sheet",
                    range_a1="01_Intake!A:B",
                    mutation_mode="logical_append",
                    sheet_id=123,
                    unique_key_column="C",
                ),
            )
        )
    except ValueError as exc:
        assert str(exc) == "SHEETS_UNIQUE_KEY_COLUMN_OUTSIDE_RANGE"
    else:
        raise AssertionError("key coordinates outside the trusted range must be rejected")



def test_logical_append_uniqueness_key_is_stable_across_runs_and_payload_conflicts():
    resolver = ConfiguredSheetsCommandResolver(
        (
            SheetsMutationTarget(
                binding_id="drive.formal_cells.update",
                spreadsheet_id="trusted-sheet",
                range_a1="01_Intake!A:B",
                mutation_mode="logical_append",
                sheet_id=123,
                unique_key_column="A",
            ),
        )
    )
    base = proposal()

    def for_run(run_id, values_json):
        return type(base)(
            **{
                **base.__dict__,
                "action_id": f"{run_id}:A0001:drive.formal_cells.update",
                "idempotency_key": f"{run_id}:A0001:drive.formal_cells.update",
                "arguments": (("values_json", values_json),),
            }
        )

    first = for_run("RUN-A", '[["REC-X","one"]]')
    same_key_different_payload = for_run("RUN-B", '[["REC-X","two"]]')
    different_key = for_run("RUN-C", '[["REC-Y","one"]]')

    first_key = resolver.logical_append_uniqueness_key(first)
    assert first_key.startswith("GSHEET-UNIQUE:")
    assert resolver.logical_append_uniqueness_key(same_key_different_payload) == first_key
    assert resolver.logical_append_uniqueness_key(different_key) != first_key


def test_fixed_range_has_no_secondary_uniqueness_key():
    resolver = ConfiguredSheetsCommandResolver(
        (
            SheetsMutationTarget(
                binding_id="drive.formal_cells.update",
                spreadsheet_id="trusted-sheet",
                range_a1="01_Intake!A10:B11",
            ),
        )
    )
    assert resolver.logical_append_uniqueness_key(proposal()) == ""



def test_logical_append_rejects_sheet_id_title_mismatch_before_mutation():
    command = logical_append_command()
    client = client_with_fakes()
    client._sheets.spreadsheets_api.sheets = [
        {"properties": {"sheetId": 123, "title": "Wrong_Tab"}},
        {"properties": {"sheetId": 999, "title": "01_Intake"}},
    ]
    client._sheets.values_api.values = [["record_id", "value"]]

    mutation = asyncio.run(client.mutate(command))

    assert mutation.success is False
    assert mutation.error_code == "SHEETS_LOGICAL_APPEND_SHEET_ID_MISMATCH"
    assert mutation.no_effect_confirmed is True
    assert len(client._sheets.spreadsheets_api.get_calls) == 1
    assert client._sheets.values_api.get_calls == []
    assert client._sheets.spreadsheets_api.batch_update_calls == []


def test_logical_append_numeric_keys_share_one_canonical_uniqueness_identity():
    resolver = ConfiguredSheetsCommandResolver(
        (
            SheetsMutationTarget(
                binding_id="drive.formal_cells.update",
                spreadsheet_id="trusted-sheet",
                range_a1="01_Intake!A:B",
                mutation_mode="logical_append",
                sheet_id=123,
                unique_key_column="A",
            ),
        )
    )
    base = proposal()

    def for_run(run_id, values_json):
        return type(base)(
            **{
                **base.__dict__,
                "action_id": f"{run_id}:A0001:drive.formal_cells.update",
                "idempotency_key": f"{run_id}:A0001:drive.formal_cells.update",
                "arguments": (("values_json", values_json),),
            }
        )

    integer_key = resolver.logical_append_uniqueness_key(for_run("RUN-I", '[[1,"one"]]'))
    float_key = resolver.logical_append_uniqueness_key(for_run("RUN-F", '[[1.0,"two"]]'))
    bool_key = resolver.logical_append_uniqueness_key(for_run("RUN-B", '[[true,"three"]]'))

    assert integer_key == float_key
    assert bool_key != integer_key


def test_logical_append_numeric_duplicate_preflight_uses_canonical_key_semantics():
    command = logical_append_command('[[1,"new"]]')
    client = client_with_fakes()
    client._sheets.values_api.values = [["record_id", "value"], [1.0, "old"]]

    duplicate = asyncio.run(client.mutate(command))

    assert duplicate.success is False
    assert duplicate.error_code == "SHEETS_LOGICAL_APPEND_KEY_DUPLICATE"
    assert duplicate.no_effect_confirmed is True
    assert client._sheets.spreadsheets_api.batch_update_calls == []



def test_logical_append_keys_use_provider_double_precision():
    resolver = ConfiguredSheetsCommandResolver(
        (
            SheetsMutationTarget(
                binding_id="drive.formal_cells.update",
                spreadsheet_id="trusted-sheet",
                range_a1="01_Intake!A:B",
                mutation_mode="logical_append",
                sheet_id=123,
                unique_key_column="A",
            ),
        )
    )
    base = proposal()

    def for_run(run_id, values_json):
        return type(base)(
            **{
                **base.__dict__,
                "action_id": f"{run_id}:A0001:drive.formal_cells.update",
                "idempotency_key": f"{run_id}:A0001:drive.formal_cells.update",
                "arguments": (("values_json", values_json),),
            }
        )

    lower = resolver.logical_append_uniqueness_key(
        for_run("RUN-LOW", '[[9007199254740992,"one"]]')
    )
    rounded_same = resolver.logical_append_uniqueness_key(
        for_run("RUN-HIGH", '[[9007199254740993,"two"]]')
    )
    next_double = resolver.logical_append_uniqueness_key(
        for_run("RUN-NEXT", '[[9007199254740994,"three"]]')
    )

    assert lower == rounded_same
    assert next_double != lower


def test_logical_append_duplicate_preflight_uses_provider_double_precision():
    command = logical_append_command('[[9007199254740993,"new"]]')
    client = client_with_fakes()
    client._sheets.values_api.values = [
        ["record_id", "value"],
        [9007199254740992, "old"],
    ]

    duplicate = asyncio.run(client.mutate(command))

    assert duplicate.success is False
    assert duplicate.error_code == "SHEETS_LOGICAL_APPEND_KEY_DUPLICATE"
    assert duplicate.no_effect_confirmed is True
    assert client._sheets.spreadsheets_api.batch_update_calls == []


def test_logical_append_readback_digest_normalizes_sheets_numbers():
    command = logical_append_command('[["REC-N",1.0]]')
    client = client_with_fakes()
    client._sheets.values_api.values = [["REC-N", 1]]

    readback = asyncio.run(client.readback(command))

    assert readback.matched is True
    assert readback.state_digest == command.expected_state_digest


def test_fixed_range_digest_semantics_remain_unchanged():
    assert canonical_values_digest([[1]]) != canonical_values_digest([[1.0]])
