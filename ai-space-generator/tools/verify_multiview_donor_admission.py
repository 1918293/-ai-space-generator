#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

import cv2
import numpy as np

DEFAULT_THRESHOLDS = {
    "min_good_matches": 40,
    "min_inliers": 40,
    "min_inlier_ratio": 0.50,
    "max_median_reprojection_px": 1.0,
    "min_inlier_coverage": 0.05,
    "min_donor_count": 2,
    "min_consensus_ssim": 0.85,
    "max_consensus_mae": 18.0,
    "min_visible_ring_ssim": 0.80,
    "min_held_out_precision": 0.99,
    "min_fresh_holdout_admissions": 1,
}


def _finite_number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite number")
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite")
    return value


def _binary_mask(mask: np.ndarray | None, shape: tuple[int, int]) -> np.ndarray | None:
    if mask is None:
        return None
    arr = np.asarray(mask)
    if arr.shape != shape:
        raise ValueError(f"visible mask shape {arr.shape} != image shape {shape}")
    if arr.ndim != 2:
        raise ValueError("visible mask must be 2D")
    if np.issubdtype(arr.dtype, np.floating) and not np.isfinite(arr).all():
        raise ValueError("visible mask must be finite")
    return np.where(arr, 255, 0).astype(np.uint8)


def _coverage(points: np.ndarray, image_shape: tuple[int, int]) -> float:
    if len(points) < 3:
        return 0.0
    hull = cv2.convexHull(points.astype(np.float32))
    area = float(cv2.contourArea(hull))
    h, w = image_shape
    return max(0.0, min(1.0, area / float(h * w)))


def estimate_planar_geometry(
    target: np.ndarray,
    donor: np.ndarray,
    *,
    visible_mask: np.ndarray | None = None,
    ratio_test: float = 0.75,
    ransac_reproj_threshold: float = 3.0,
) -> dict[str, Any]:
    """Estimate donor->target planar geometry using only visible target pixels.

    This is an admission diagnostic, not a final-edit generator. Hidden/edited
    target pixels can be excluded through visible_mask so they cannot influence
    feature detection or the homography estimate.
    """
    target = np.asarray(target)
    donor = np.asarray(donor)
    if target.shape[:2] != donor.shape[:2]:
        raise ValueError("target and donor must share image dimensions")
    if target.ndim not in (2, 3) or donor.ndim not in (2, 3):
        raise ValueError("target and donor must be grayscale or color images")
    if target.size == 0 or donor.size == 0:
        raise ValueError("images must be non-empty")

    target_gray = cv2.cvtColor(target, cv2.COLOR_BGR2GRAY) if target.ndim == 3 else target
    donor_gray = cv2.cvtColor(donor, cv2.COLOR_BGR2GRAY) if donor.ndim == 3 else donor
    mask = _binary_mask(visible_mask, target_gray.shape)

    sift = cv2.SIFT_create()
    kp_t, desc_t = sift.detectAndCompute(target_gray, mask)
    kp_d, desc_d = sift.detectAndCompute(donor_gray, None)
    if desc_t is None or desc_d is None or len(kp_t) < 4 or len(kp_d) < 4:
        return {
            "status": "REJECT",
            "reason": "INSUFFICIENT_FEATURES",
            "good_matches": 0,
            "inliers": 0,
            "inlier_ratio": 0.0,
            "median_reprojection_px": None,
            "inlier_coverage": 0.0,
        }

    matcher = cv2.BFMatcher(cv2.NORM_L2)
    knn = matcher.knnMatch(desc_d, desc_t, k=2)
    good = [m for m, n in knn if m.distance < ratio_test * n.distance]
    if len(good) < 4:
        return {
            "status": "REJECT",
            "reason": "INSUFFICIENT_MATCHES",
            "good_matches": len(good),
            "inliers": 0,
            "inlier_ratio": 0.0,
            "median_reprojection_px": None,
            "inlier_coverage": 0.0,
        }

    donor_pts = np.float32([kp_d[m.queryIdx].pt for m in good])
    target_pts = np.float32([kp_t[m.trainIdx].pt for m in good])
    H, inlier_mask = cv2.findHomography(
        donor_pts, target_pts, cv2.RANSAC, ransac_reproj_threshold
    )
    if H is None or inlier_mask is None:
        return {
            "status": "REJECT",
            "reason": "HOMOGRAPHY_FAILED",
            "good_matches": len(good),
            "inliers": 0,
            "inlier_ratio": 0.0,
            "median_reprojection_px": None,
            "inlier_coverage": 0.0,
        }

    keep = inlier_mask.ravel().astype(bool)
    inliers = int(np.count_nonzero(keep))
    projected = cv2.perspectiveTransform(donor_pts.reshape(-1, 1, 2), H).reshape(-1, 2)
    errors = np.linalg.norm(projected - target_pts, axis=1)
    median_reproj = float(np.median(errors[keep])) if inliers else float("inf")
    coverage = _coverage(target_pts[keep], target_gray.shape)
    return {
        "status": "MEASURED",
        "reason": None,
        "good_matches": len(good),
        "inliers": inliers,
        "inlier_ratio": inliers / len(good),
        "median_reprojection_px": median_reproj,
        "inlier_coverage": coverage,
        "homography": H.tolist(),
    }


def assess_pair(metrics: dict[str, Any], thresholds: dict[str, float] | None = None) -> dict[str, Any]:
    t = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    reasons: list[str] = []
    good = int(_finite_number(metrics.get("good_matches", 0), "good_matches"))
    inliers = int(_finite_number(metrics.get("inliers", 0), "inliers"))
    ratio = _finite_number(metrics.get("inlier_ratio", 0), "inlier_ratio")
    coverage = _finite_number(metrics.get("inlier_coverage", 0), "inlier_coverage")
    reproj_raw = metrics.get("median_reprojection_px")
    reproj = float("inf") if reproj_raw is None else _finite_number(reproj_raw, "median_reprojection_px")

    if good < t["min_good_matches"]:
        reasons.append("GOOD_MATCHES_BELOW_MIN")
    if inliers < t["min_inliers"]:
        reasons.append("INLIERS_BELOW_MIN")
    if ratio < t["min_inlier_ratio"]:
        reasons.append("INLIER_RATIO_BELOW_MIN")
    if coverage < t["min_inlier_coverage"]:
        reasons.append("INLIER_COVERAGE_BELOW_MIN")
    if reproj > t["max_median_reprojection_px"]:
        reasons.append("REPROJECTION_ABOVE_MAX")
    return {"pass": not reasons, "reasons": reasons}


def assess_region(region: dict[str, Any], thresholds: dict[str, float] | None = None) -> dict[str, Any]:
    """Fail-closed visible-only admission for one hidden target region.

    A region can only become a reconstruction candidate; this function never
    declares semantic/naturalness success and never authorizes final delivery.
    """
    t = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    donors = region.get("donors")
    if not isinstance(donors, list):
        raise ValueError("region.donors must be a list")
    pair_results = [assess_pair(d, t) for d in donors]
    reasons: list[str] = []
    if len(donors) < t["min_donor_count"]:
        reasons.append("INSUFFICIENT_DONOR_COUNT")
    if any(not r["pass"] for r in pair_results):
        reasons.append("DONOR_GEOMETRY_REJECTED")

    consensus_ssim = _finite_number(region.get("consensus_ssim", 0), "consensus_ssim")
    consensus_mae = _finite_number(region.get("consensus_mae", float("inf")), "consensus_mae")
    ring_ssim = _finite_number(region.get("visible_ring_ssim", 0), "visible_ring_ssim")
    if consensus_ssim < t["min_consensus_ssim"]:
        reasons.append("DONOR_CONSENSUS_SSIM_LOW")
    if consensus_mae > t["max_consensus_mae"]:
        reasons.append("DONOR_CONSENSUS_MAE_HIGH")
    if ring_ssim < t["min_visible_ring_ssim"]:
        reasons.append("VISIBLE_RING_SSIM_LOW")

    return {
        "decision": "ALLOW_RECONSTRUCTION_CANDIDATE" if not reasons else "HOLD",
        "pass": not reasons,
        "reasons": reasons,
        "pair_results": pair_results,
        "semantic_or_naturalness_verified": False,
        "final_delivery_authorized": False,
    }


def assess_experiment_summary(summary: dict[str, Any], thresholds: dict[str, float] | None = None) -> dict[str, Any]:
    """Promotion gate for the autonomous donor route itself."""
    t = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    precision = _finite_number(summary.get("held_out_precision", 0), "held_out_precision")
    fresh_admissions = int(_finite_number(summary.get("fresh_holdout_admissions", 0), "fresh_holdout_admissions"))
    fresh_false_positives = int(_finite_number(summary.get("fresh_holdout_false_positives", 0), "fresh_holdout_false_positives"))
    reasons: list[str] = []
    if precision < t["min_held_out_precision"]:
        reasons.append("HELD_OUT_PRECISION_BELOW_PROMOTION_MIN")
    if fresh_admissions < t["min_fresh_holdout_admissions"]:
        reasons.append("NO_FRESH_HOLDOUT_ADMISSION_EVIDENCE")
    if fresh_false_positives != 0:
        reasons.append("FRESH_HOLDOUT_FALSE_POSITIVE")
    return {
        "decision": "ELIGIBLE_FOR_FURTHER_REVIEW" if not reasons else "HOLD_AUTONOMOUS_DONOR_FAIL_CLOSED",
        "pass": not reasons,
        "reasons": reasons,
        "workflow_promotion_authorized": False,
    }


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Fail-closed verifier for multi-view donor admission evidence."
    )
    ap.add_argument("manifest", type=Path, help="JSON manifest with regions and/or experiment_summary")
    ap.add_argument("--output", type=Path)
    args = ap.parse_args()
    payload = json.loads(args.manifest.read_text(encoding="utf-8"))
    result: dict[str, Any] = {"schema": "hao.multiview-donor-admission-result.v1"}
    try:
        if "regions" in payload:
            result["regions"] = [
                assess_region(x, payload.get("thresholds")) for x in payload["regions"]
            ]
        if "experiment_summary" in payload:
            result["experiment"] = assess_experiment_summary(
                payload["experiment_summary"], payload.get("thresholds")
            )
        result["pass"] = bool(result.get("experiment", {}).get("pass", False))
    except Exception as exc:
        result = {
            **result,
            "pass": False,
            "error": f"{type(exc).__name__}: {exc}",
        }

    encoded = json.dumps(result, indent=2, sort_keys=True)
    if args.output:
        args.output.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)
    return 0 if result["pass"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
