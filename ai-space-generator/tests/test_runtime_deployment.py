import pytest

from src.execution_control import ActionArchetype, ActionExternality
from src.runtime_deployment import (
    FORMAL_SHEETS_ASSURANCE_TAGS,
    action_catalog_for_targets,
    load_sheets_targets,
    load_task_policies,
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
