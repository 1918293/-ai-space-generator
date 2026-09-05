import asyncio
import sqlite3
from types import SimpleNamespace

import pytest

from src.execution_control import ActionArchetype, ActionExternality
from src.production_execution import ProductionExecutionResult
from src.runtime_deployment import (
    FORMAL_SHEETS_ASSURANCE_TAGS,
    action_catalog_for_targets,
    load_sheets_targets,
    load_task_policies,
)
from src.stable_finalization import (
    PostgresFinalizationIssueStore,
    StableFinalizationProductionService,
)


def deployment_values():
    return {
        "HAO_SHEETS_TARGETS_JSON": """[
          {
            "binding_id": "formal.intake.append",
            "spreadsheet_id": "sheet-main",
            "range_a1": "01_Intake!A1:V1",
            "value_input_option": "RAW",
            "authority_sources": [
              {"ref": "AUTH-MAIN", "file_id": "drive-main"}
            ]
          }
        ]""",
        "HAO_TASK_POLICIES_JSON": """[
          {
            "task": "Runtime v2 formal persistence",
            "acceptance_criteria": ["exact readback"],
            "authority_sources": [
              {"ref": "AUTH-MAIN", "file_id": "drive-main"}
            ],
            "required_acceptance_gate_ids": ["FORMAL_RECORD_WRITTEN"],
            "required_action_tags": ["TASK_BOUND"],
            "forbidden_action_tags": ["FULL_GENERATION"]
          }
        ]""",
    }


def test_deployment_target_compiles_to_trusted_private_reversible_binding():
    values = deployment_values()
    targets = load_sheets_targets(values)
    catalog = action_catalog_for_targets(targets)
    binding = catalog.get("formal.intake.append")

    assert binding is not None
    assert binding.provider == "google-drive"
    assert binding.action_name == "update_cells"
    assert binding.archetype == ActionArchetype.MUTATE
    assert binding.externality == ActionExternality.PRIVATE_REVERSIBLE
    assert binding.allowed_argument_keys == ("values_json",)
    assert binding.required_argument_keys == ("values_json",)
    assert set(FORMAL_SHEETS_ASSURANCE_TAGS).issubset(set(binding.assurance_tags))


def test_target_identity_and_authority_are_deployment_owned_not_model_arguments():
    target = load_sheets_targets(deployment_values())[0]
    assert target.spreadsheet_id == "sheet-main"
    assert target.range_a1 == "01_Intake!A1:V1"
    assert target.authority_sources[0].ref == "AUTH-MAIN"
    assert target.authority_sources[0].file_id == "drive-main"


def test_task_policy_forces_direct_readback_gate_and_formal_assurance_tags():
    spec = load_task_policies(deployment_values())[0]
    assert "DRIVE_EXPECTED_STATE_MATCH" in spec.required_acceptance_gate_ids
    assert "FORMAL_RECORD_WRITTEN" in spec.required_acceptance_gate_ids
    assert set(FORMAL_SHEETS_ASSURANCE_TAGS).issubset(set(spec.required_action_tags))
    assert "TASK_BOUND" in spec.required_action_tags
    assert spec.forbidden_action_tags == ("FULL_GENERATION",)


def test_empty_or_malformed_deployment_targets_fail_closed():
    values = deployment_values()
    values["HAO_SHEETS_TARGETS_JSON"] = "[]"
    with pytest.raises(ValueError, match="HAO_SHEETS_TARGETS_REQUIRED"):
        load_sheets_targets(values)

    values["HAO_SHEETS_TARGETS_JSON"] = "not-json"
    with pytest.raises(ValueError, match="INVALID_JSON_CONFIG:HAO_SHEETS_TARGETS_JSON"):
        load_sheets_targets(values)


def test_authority_source_is_required_for_every_deployment_target():
    values = deployment_values()
    values["HAO_SHEETS_TARGETS_JSON"] = """[
      {
        "binding_id": "formal.intake.append",
        "spreadsheet_id": "sheet-main",
        "range_a1": "01_Intake!A1:V1",
        "authority_sources": []
      }
    ]"""
    with pytest.raises(ValueError, match="AUTHORITY_SOURCES_REQUIRED"):
        load_sheets_targets(values)


class SqlitePostgresCompatConnection:
    def __init__(self, path):
        self._conn = sqlite3.connect(path, timeout=30, isolation_level=None)
        self._conn.row_factory = sqlite3.Row

    def execute(self, sql, params=()):
        normalized = sql.strip()
        if normalized == "BEGIN ISOLATION LEVEL SERIALIZABLE":
            return self._conn.execute("BEGIN IMMEDIATE")
        normalized = normalized.replace(" FOR UPDATE", "").replace("%s", "?")
        return self._conn.execute(normalized, params)

    def close(self):
        self._conn.close()


class CrashWindowProduction:
    def __init__(self, observed_issued_at, *, lose_response):
        self._observed_issued_at = observed_issued_at
        self._lose_response = lose_response

    async def finalize(self, pending, *, issued_at):
        del pending
        self._observed_issued_at.append(issued_at)
        if self._lose_response:
            raise ConnectionError("RESPONSE_LOST_AFTER_AUTHORITATIVE_COMMIT")
        return ProductionExecutionResult(
            None,
            True,
            "AUTHORITATIVE_COMPLETION_ALREADY_COMMITTED",
        )


def test_stable_finalization_reuses_issuance_after_response_loss_and_restart(tmp_path):
    database_path = tmp_path / "stable-finalization.sqlite3"
    factory = lambda: SqlitePostgresCompatConnection(database_path)
    pending = SimpleNamespace(handle=SimpleNamespace(workflow_id="RUN-STABLE-1"))
    observed_issued_at = []

    first_store = PostgresFinalizationIssueStore(
        "postgresql://runtime-v2/test",
        connect_factory=factory,
    )
    first_process = StableFinalizationProductionService(
        CrashWindowProduction(observed_issued_at, lose_response=True),
        first_store,
    )

    with pytest.raises(ConnectionError, match="RESPONSE_LOST_AFTER_AUTHORITATIVE_COMMIT"):
        asyncio.run(
            first_process.finalize(
                pending,
                issued_at="2026-09-05T09:00:00+08:00",
            )
        )

    restarted_store = PostgresFinalizationIssueStore(
        "postgresql://runtime-v2/test",
        connect_factory=factory,
    )
    restarted_process = StableFinalizationProductionService(
        CrashWindowProduction(observed_issued_at, lose_response=False),
        restarted_store,
    )
    result = asyncio.run(
        restarted_process.finalize(
            pending,
            issued_at="2026-09-05T09:05:00+08:00",
        )
    )

    assert result.authoritative is True
    assert observed_issued_at == [
        "2026-09-05T09:00:00+08:00",
        "2026-09-05T09:00:00+08:00",
    ]
