import importlib.util
from pathlib import Path

import cv2
import numpy as np
import pytest

TOOL = (
    Path(__file__).resolve().parents[1]
    / "tools"
    / "verify_multiview_donor_admission.py"
)
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
    cv2.line(img, (20, 70), (450, 90), (255, 255, 255), 2)
    cv2.line(img, (30, 250), (440, 230), (200, 200, 200), 2)
    return img


def _strong_pair_metrics():
    return {
        "good_matches": 120,
        "inliers": 110,
        "inlier_ratio": 110 / 120,
        "median_reprojection_px": 0.3,
        "inlier_coverage": 0.20,
    }


def test_planar_estimator_passes_high_overlap_visible_only_control():
    target = _textured_scene()
    H = np.array(
        [[1, 0.01, 5], [-0.01, 1, 4], [0, 0, 1]],
        dtype=np.float64,
    )
    donor = cv2.warpPerspective(
        target,
        np.linalg.inv(H),
        (target.shape[1], target.shape[0]),
    )
    visible = np.ones(target.shape[:2], dtype=bool)
    visible[120:190, 190:290] = False

    metrics = mod.estimate_planar_geometry(
        target,
        donor,
        visible_mask=visible,
    )
    qa = mod.assess_pair(metrics)

    assert qa["pass"] is True
    assert metrics["inliers"] >= 40
    assert metrics["median_reprojection_px"] <= 1.0


def test_planar_estimator_fails_closed_on_featureless_inputs():
    target = np.zeros((200, 300, 3), dtype=np.uint8)
    donor = target.copy()

    metrics = mod.estimate_planar_geometry(target, donor)

    assert metrics["status"] == "REJECT"
    assert mod.assess_pair(metrics)["pass"] is False


def test_region_requires_multiple_independently_good_donors():
    region = {
        "donors": [_strong_pair_metrics()],
        "consensus_ssim": 0.95,
        "consensus_mae": 4.0,
        "visible_ring_ssim": 0.95,
    }

    qa = mod.assess_region(region)

    assert qa["pass"] is False
    assert "INSUFFICIENT_DONOR_COUNT" in qa["reasons"]
    assert qa["final_delivery_authorized"] is False


def test_region_rejects_disagreeing_donors_even_with_good_geometry():
    region = {
        "donors": [_strong_pair_metrics(), _strong_pair_metrics()],
        "consensus_ssim": 0.40,
        "consensus_mae": 48.0,
        "visible_ring_ssim": 0.94,
    }

    qa = mod.assess_region(region)

    assert qa["pass"] is False
    assert "DONOR_CONSENSUS_SSIM_LOW" in qa["reasons"]
    assert "DONOR_CONSENSUS_MAE_HIGH" in qa["reasons"]


def test_region_can_only_allow_candidate_not_final_delivery():
    region = {
        "donors": [_strong_pair_metrics(), _strong_pair_metrics()],
        "consensus_ssim": 0.92,
        "consensus_mae": 7.0,
        "visible_ring_ssim": 0.91,
    }

    qa = mod.assess_region(region)

    assert qa["decision"] == "ALLOW_RECONSTRUCTION_CANDIDATE"
    assert qa["pass"] is True
    assert qa["semantic_or_naturalness_verified"] is False
    assert qa["final_delivery_authorized"] is False


def test_current_385_p0_summary_is_hold_not_promotion():
    summary = {
        "held_out_precision": 8 / 11,
        "fresh_holdout_admissions": 0,
        "fresh_holdout_false_positives": 0,
    }

    qa = mod.assess_experiment_summary(summary)

    assert qa["decision"] == "HOLD_AUTONOMOUS_DONOR_FAIL_CLOSED"
    assert qa["pass"] is False
    assert "HELD_OUT_PRECISION_BELOW_PROMOTION_MIN" in qa["reasons"]
    assert "NO_FRESH_HOLDOUT_ADMISSION_EVIDENCE" in qa["reasons"]
    assert qa["workflow_promotion_authorized"] is False


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -float("inf")])
def test_nonfinite_metrics_fail_closed(bad):
    region = {
        "donors": [_strong_pair_metrics(), _strong_pair_metrics()],
        "consensus_ssim": bad,
        "consensus_mae": 5.0,
        "visible_ring_ssim": 0.9,
    }

    with pytest.raises(ValueError, match="finite"):
        mod.assess_region(region)
