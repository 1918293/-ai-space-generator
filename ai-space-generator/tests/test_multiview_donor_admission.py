import importlib.util
from pathlib import Path

import cv2
import numpy as np
import pytest

TOOL = Path(__file__).resolve().parents[1] / "tools" / "verify_multiview_donor_admission.py"
spec = importlib.util.spec_from_file_location("donor_admission", TOOL)
mod = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(mod)


def _textured_scene(seed=385):
    rng = np.random.default_rng(seed)
    img = np.zeros((320, 480, 3), dtype=np.uint8)
    for _ in range(180):
        x, y = rng.integers(10, 470), rng.integers(10, 310)
        r = int(rng.integers(2, 7))
        color = tuple(int(v) for v in rng.integers(50, 255, 3))
        cv2.circle(img, (int(x), int(y)), r, color, -1)
    return img


def _strong_pair_metrics():
    return {
        "good_matches": 120,
        "inliers": 110,
        "inlier_ratio": 110 / 120,
        "median_reprojection_px": 0.3,
        "inlier_coverage": 0.20,
        "visible_ring_ssim": 0.95,
    }


def _strong_camera():
    return {"incremental_pnp_inlier_ratio": 0.83}


def _strong_region():
    return {"target_camera": _strong_camera(), "donors": [_strong_pair_metrics(), _strong_pair_metrics()], "consensus_ssim": 0.92, "consensus_mae": 7.0, "visible_ring_ssim": 0.91, "visible_ring_structural_ssim": 0.95}


def test_planar_estimator_passes_high_overlap_visible_only_control():
    target = _textured_scene()
    H = np.array([[1,0.01,5],[-0.01,1,4],[0,0,1]], dtype=np.float64)
    donor = cv2.warpPerspective(target, np.linalg.inv(H), (target.shape[1], target.shape[0]))
    visible = np.ones(target.shape[:2], dtype=bool); visible[120:190,190:290] = False
    metrics = mod.estimate_planar_geometry(target, donor, visible_mask=visible)
    assert mod.assess_pair(metrics)["pass"] is True
    assert metrics["inliers"] >= 40


def test_planar_estimator_fails_closed_on_featureless_inputs():
    target = np.zeros((200,300,3), dtype=np.uint8)
    metrics = mod.estimate_planar_geometry(target, target.copy())
    assert metrics["status"] == "REJECT"
    assert mod.assess_pair(metrics)["pass"] is False


def test_region_requires_target_camera_quality_receipt():
    region = _strong_region(); region.pop("target_camera")
    qa = mod.assess_region(region)
    assert qa["pass"] is False
    assert "TARGET_CAMERA_QUALITY_MISSING" in qa["reasons"]


def test_weak_target_camera_rejects_even_when_two_donors_agree():
    region = _strong_region(); region["target_camera"]={"incremental_pnp_inlier_ratio": 0.4642857142857143}
    qa = mod.assess_region(region)
    assert qa["pass"] is False
    assert "TARGET_CAMERA_PNP_RATIO_BELOW_MIN" in qa["reasons"]
    assert qa["final_delivery_authorized"] is False


def test_strong_target_camera_can_reach_candidate_only():
    qa = mod.assess_region(_strong_region())
    assert qa["decision"] == "ALLOW_RECONSTRUCTION_CANDIDATE"
    assert qa["pass"] is True
    assert qa["semantic_or_naturalness_verified"] is False
    assert qa["final_delivery_authorized"] is False


def test_region_requires_multiple_independently_good_donors():
    region = _strong_region(); region["donors"]=[_strong_pair_metrics()]
    qa=mod.assess_region(region)
    assert qa["pass"] is False
    assert "INSUFFICIENT_DONOR_COUNT" in qa["reasons"]


def test_frozen_gate_v3_consensus_defaults_match_field_contract():
    assert mod.DEFAULT_THRESHOLDS["min_consensus_ssim"] == 0.90
    assert mod.DEFAULT_THRESHOLDS["max_consensus_mae"] == 8.0


def test_frozen_gate_v3_rejects_consensus_boundary_previously_allowed_by_generic_defaults():
    region = _strong_region()
    region["consensus_ssim"] = 0.87
    region["consensus_mae"] = 10.0
    qa = mod.assess_region(region)
    assert qa["pass"] is False
    assert "DONOR_CONSENSUS_SSIM_LOW" in qa["reasons"]
    assert "DONOR_CONSENSUS_MAE_HIGH" in qa["reasons"]


def test_region_rejects_disagreeing_donors_even_with_good_geometry():
    region=_strong_region(); region["consensus_ssim"]=0.40; region["consensus_mae"]=48.0
    qa=mod.assess_region(region)
    assert qa["pass"] is False
    assert "DONOR_CONSENSUS_SSIM_LOW" in qa["reasons"]
    assert "DONOR_CONSENSUS_MAE_HIGH" in qa["reasons"]


def test_low_donor_ring_structure_rejects_even_when_consensus_is_good():
    region = _strong_region()
    region["donors"][1] = {**_strong_pair_metrics(), "visible_ring_ssim": 0.84}
    qa = mod.assess_region(region)
    assert qa["pass"] is False
    assert "DONOR_VISIBLE_RING_SSIM_BELOW_MIN" in qa["reasons"]
    assert qa["final_delivery_authorized"] is False


def test_missing_donor_ring_structure_fails_closed():
    region = _strong_region()
    region["donors"][0].pop("visible_ring_ssim")
    qa = mod.assess_region(region)
    assert qa["pass"] is False
    assert "DONOR_VISIBLE_RING_SSIM_BELOW_MIN" in qa["reasons"]



def test_v8_low_coverage_summary_remains_hold():
    summary={"held_out_precision":1.0,"fresh_holdout_admissions":1,"fresh_holdout_targets_with_admissions":1,"fresh_holdout_false_positives":0}
    qa=mod.assess_experiment_summary(summary)
    assert qa["decision"] == "HOLD_AUTONOMOUS_DONOR_FAIL_CLOSED"
    assert "INSUFFICIENT_FRESH_HOLDOUT_ADMISSIONS" in qa["reasons"]
    assert "INSUFFICIENT_INDEPENDENT_FRESH_TARGETS" in qa["reasons"]
    assert qa["workflow_promotion_authorized"] is False


def test_multiple_independent_fresh_admissions_only_become_review_eligible():
    summary={"held_out_precision":1.0,"fresh_holdout_admissions":4,"fresh_holdout_targets_with_admissions":2,"fresh_holdout_false_positives":0}
    qa=mod.assess_experiment_summary(summary)
    assert qa["decision"] == "ELIGIBLE_FOR_FURTHER_REVIEW"
    assert qa["pass"] is True
    assert qa["workflow_promotion_authorized"] is False


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -float("inf")])
def test_nonfinite_camera_quality_fails_closed(bad):
    with pytest.raises(ValueError, match="finite"):
        mod.assess_target_camera({"incremental_pnp_inlier_ratio": bad})


def test_gate_v3_rejects_weak_visible_ring_structure():
    region = _strong_region()
    region["visible_ring_structural_ssim"] = 0.88
    qa = mod.assess_region(region)
    assert qa["pass"] is False
    assert "VISIBLE_RING_STRUCTURAL_SSIM_LOW" in qa["reasons"]


def test_gate_v3_component2_false_positive_keeps_experiment_hold():
    summary = {
        "held_out_precision": 0.8,
        "fresh_holdout_admissions": 5,
        "fresh_holdout_targets_with_admissions": 3,
        "fresh_holdout_false_positives": 1,
    }
    qa = mod.assess_experiment_summary(summary)
    assert qa["decision"] == "HOLD_AUTONOMOUS_DONOR_FAIL_CLOSED"
    assert "FRESH_HOLDOUT_FALSE_POSITIVE" in qa["reasons"]
    assert qa["workflow_promotion_authorized"] is False
