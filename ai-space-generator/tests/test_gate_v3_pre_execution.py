import hashlib
import json

from src.execution_control import (
    ActionArchetype,
    ActionExternality,
    ActionProposal,
    EvidenceKind,
    ExecutionRecord,
    Mode,
)
from src.gate_v3_pre_execution import (
    GATE_ID,
    GateV3ExpectedProvenance,
    GateV3SourceBoundPreExecutionVerifier,
)


GATE_SHA = "0cdc9a7ba4b5c8a214f4aeabf0620d78e2fd2825449748b7aa35c2edf0c26545"
SCORER_SHA = "4e61e3f87eb2a99dffb08417d027ac31a903c02858cce1b1d4ba1e06eec5269b"
RESULT_SHA = "7e87edbe0410a1958a130112b9555cc3e35d23cbf32882a67b354097b97a8ef2"
CORE_SHA = "c87700e2ef633c56320e3f852cc958b0ea1f8a06b7c32f3d2e7a01562bada25a"
TARGET_SHA = "a595a7009821ef31a15797ad3f41843e41b6bea85d4a7f1e89242daadf634647"
DONOR1_SHA = "e7ae00bab362064a248ce6a88c1f286774c6d3ef04f3c0780a885475b0cd06a2"
DONOR2_SHA = "a09f237adb5b0c3119adfcb83b65747cbd18910aa56d275f879ababdfa7504ee"


def action():
    return ActionProposal(
        action_id="RUN-BONSAI:A0001:donor.reconstruct",
        archetype=ActionArchetype.ANALYZE,
        externality=ActionExternality.READ_ONLY,
        capability="donor_reconstruction_candidate",
        provider="gate-v3-test",
        action_name="reconstruct_candidate",
    )


def record():
    return ExecutionRecord(
        run_id="RUN-BONSAI",
        task="Gate-v3 source-bound P0",
        mode=Mode.EXP,
        goal_valid=True,
        acceptance_criteria=("source-bound gate",),
        required_pre_execution_gate_ids=(GATE_ID,),
    )


def receipt_dict():
    return {
        "schema": "hao.385.gate-v3-pre-execution.v1",
        "frozen_gate_sha256": GATE_SHA,
        "canonical_scorer_sha256": SCORER_SHA,
        "source_result_sha256": RESULT_SHA,
        "core_model_sha256": CORE_SHA,
        "target": {
            "name": "DSCF5600.JPG",
            "sha256": TARGET_SHA,
            "pnp_ratio": 0.9434618137204784,
        },
        "donors": [
            {"name": "DSCF5601.JPG", "sha256": DONOR1_SHA},
            {"name": "DSCF5599.JPG", "sha256": DONOR2_SHA},
        ],
        "patch": {
            "center": [340, 420],
            "bbox": [304, 384, 376, 456],
            "plane_support": 82,
            "plane_median_dist": 0.004526377142830995,
            "plane_threshold": 0.009531054562342087,
            "plane_scale": 0.5295030312412271,
            "plane_normalized_median_dist": 0.008548349821946348,
            "support_span": [429.8949189840954, 361.8205424185286],
            "candidate_donors": [
                {
                    "donor": "DSCF5601.JPG",
                    "ring_mae": 1.7822265625,
                    "ring_ssim": 0.9707149575583017,
                    "shared_plane": 75,
                    "valid_fraction": 1.0,
                },
                {
                    "donor": "DSCF5599.JPG",
                    "ring_mae": 8.32591869212963,
                    "ring_ssim": 0.9462611586479178,
                    "shared_plane": 47,
                    "valid_fraction": 1.0,
                },
            ],
            "consensus": {
                "donors": ["DSCF5601.JPG", "DSCF5599.JPG"],
                "ssim": 0.9607476344378965,
                "mae": 7.560313786008231,
            },
            "outside_changed_pixels": 0,
        },
        "admitted": True,
    }


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def expected(raw, **changes):
    values = dict(
        receipt_sha256=hashlib.sha256(raw).hexdigest(),
        frozen_gate_sha256=GATE_SHA,
        canonical_scorer_sha256=SCORER_SHA,
        source_result_sha256=RESULT_SHA,
        core_model_sha256=CORE_SHA,
        target_name="DSCF5600.JPG",
        target_sha256=TARGET_SHA,
        donor_sha256s=(
            ("DSCF5601.JPG", DONOR1_SHA),
            ("DSCF5599.JPG", DONOR2_SHA),
        ),
    )
    values.update(changes)
    return GateV3ExpectedProvenance(**values)


def verify(value=None, *, expected_changes=None):
    raw = encoded(receipt_dict() if value is None else value)
    verifier = GateV3SourceBoundPreExecutionVerifier(
        raw,
        expected=expected(raw, **(expected_changes or {})),
        source="library:HAO_385_Bonsai_GateV3_Heldout_20261023.zip",
    )
    return verifier.verify(record(), action())


def test_real_bonsai_patch_metrics_admit_and_mint_only_pre_execution_evidence():
    result = verify()
    assert result.passed is True
    assert result.error_code == ""
    assert len(result.receipts) == 1
    evidence = result.receipts[0]
    assert evidence.kind == EvidenceKind.PRE_EXECUTION_GATE_PASS
    assert evidence.gate_id == GATE_ID
    assert evidence.claim_scope == action().action_id


def test_receipt_fixity_mismatch_fails_closed():
    raw = encoded(receipt_dict())
    verifier = GateV3SourceBoundPreExecutionVerifier(
        raw,
        expected=expected(raw, receipt_sha256="0" * 64),
        source="library:test",
    )
    result = verifier.verify(record(), action())
    assert result.passed is False
    assert result.error_code.endswith("PROVENANCE_MISMATCH:receipt_sha256")


def test_source_identity_mismatch_fails_closed():
    result = verify(expected_changes={"target_sha256": "0" * 64})
    assert result.passed is False
    assert result.error_code.endswith("PROVENANCE_MISMATCH:target.sha256")


def test_ground_truth_and_abstract_ring_fields_are_rejected():
    for key in (
        "ground_truth_score",
        "ground_truth_success",
        "visible_ring_ssim",
        "visible_ring_structural_ssim",
    ):
        value = receipt_dict()
        value["patch"][key] = {} if "score" in key else True
        result = verify(value)
        assert result.passed is False
        assert "FORBIDDEN_FIELD:" in result.error_code


def test_unknown_or_missing_schema_fields_fail_closed():
    extra = receipt_dict()
    extra["patch"]["invented_metric"] = 1
    assert verify(extra).passed is False

    missing = receipt_dict()
    del missing["patch"]["plane_support"]
    assert verify(missing).passed is False


def test_frozen_threshold_rejects_without_minting_gate_receipt():
    value = receipt_dict()
    value["patch"]["candidate_donors"][0]["ring_ssim"] = 0.90
    value["admitted"] = False
    result = verify(value)
    assert result.passed is False
    assert result.receipts == ()
    assert "DSCF5601.JPG:RING_SSIM_BELOW_MIN" in result.error_code


def test_claimed_admission_must_equal_deterministic_recomputed_decision():
    value = receipt_dict()
    value["patch"]["outside_changed_pixels"] = 1
    # Deliberately leave admitted=True to prove self-assertion cannot override recomputation.
    result = verify(value)
    assert result.passed is False
    assert result.error_code.endswith("ADMISSION_DECISION_MISMATCH")


def test_consensus_identity_must_match_source_bound_candidate_donors():
    value = receipt_dict()
    value["patch"]["consensus"]["donors"].reverse()
    result = verify(value)
    assert result.passed is False
    assert result.error_code.endswith("CONSENSUS_DONOR_IDENTITY_MISMATCH")
