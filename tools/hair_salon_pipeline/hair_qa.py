#!/usr/bin/env python3
import argparse
import json
from pathlib import Path

import cv2
import numpy as np
from PIL import Image


def rgb(path: Path) -> np.ndarray:
    return np.asarray(Image.open(path).convert("RGB"))


def matte_u01(path: Path, size) -> np.ndarray:
    im = Image.open(path).convert("L")
    if im.size != size:
        raise ValueError(f"matte size {im.size} != image size {size}")
    return np.asarray(im).astype(np.float32) / 255.0


def grad(a: np.ndarray) -> np.ndarray:
    g = cv2.cvtColor(a, cv2.COLOR_RGB2GRAY).astype(np.float32)
    gx = cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3)
    return np.sqrt(gx * gx + gy * gy)


def corr(x: np.ndarray, y: np.ndarray) -> float:
    if x.size < 100 or float(np.std(x)) < 1e-6 or float(np.std(y)) < 1e-6:
        return 1.0
    return float(np.corrcoef(x, y)[0, 1])


def main():
    ap = argparse.ArgumentParser(description="Hair-focused fidelity QA for salon portrait candidates")
    ap.add_argument("--authority", required=True, help="Source/authority image used as fidelity reference")
    ap.add_argument("--candidate", required=True)
    ap.add_argument("--matte", required=True)
    ap.add_argument("--output", required=True)
    args = ap.parse_args()

    authority_p = Path(args.authority)
    candidate_p = Path(args.candidate)
    a = rgb(authority_p)
    c = rgb(candidate_p)
    if a.shape != c.shape:
        raise ValueError(f"authority shape {a.shape} != candidate shape {c.shape}")
    h, w = a.shape[:2]
    m = matte_u01(Path(args.matte), (w, h))

    core = m >= 0.65
    if not np.any(core):
        raise ValueError("subject core is empty")
    ys, xs = np.where(core)
    x0, x1 = int(xs.min()), int(xs.max()) + 1
    y0, y1 = int(ys.min()), int(ys.max()) + 1

    # Without a dedicated hair segmentation mask, use the upper 50% of the
    # high-confidence subject as a conservative hair/face proxy and label it as such.
    # This intentionally avoids letting hands/lower-shirt highlights dominate hair QA.
    hair_y1 = y0 + max(1, int((y1 - y0) * 0.50))
    yy = np.arange(h)[:, None]
    hair_proxy = core & (yy >= y0) & (yy < hair_y1)
    if np.count_nonzero(hair_proxy) < 1000:
        hair_proxy = core

    ga = grad(a)
    gc = grad(c)
    g_corr = corr(ga[hair_proxy], gc[hair_proxy])
    edge_ratio = float(np.mean(gc[hair_proxy]) / max(float(np.mean(ga[hair_proxy])), 1e-6))

    ha = cv2.cvtColor(a, cv2.COLOR_RGB2HSV).astype(np.float32)
    hc = cv2.cvtColor(c, cv2.COLOR_RGB2HSV).astype(np.float32)
    chroma = hair_proxy & (ha[:, :, 1] >= 35) & (ha[:, :, 2] >= 25)
    if np.any(chroma):
        hd = np.abs(hc[:, :, 0] - ha[:, :, 0])
        hd = np.minimum(hd, 180 - hd) * 2.0
        sd = np.abs(hc[:, :, 1] - ha[:, :, 1])
        hue_med = float(np.median(hd[chroma]))
        hue_p95 = float(np.percentile(hd[chroma], 95))
        sat_med = float(np.median(sd[chroma]))
    else:
        hue_med = hue_p95 = sat_med = 0.0

    auth_clip_core = float(np.mean(np.any(a >= 255, axis=2)[core]))
    cand_clip_core = float(np.mean(np.any(c >= 255, axis=2)[core]))
    auth_clip_hair = float(np.mean(np.any(a >= 255, axis=2)[hair_proxy]))
    cand_clip_hair = float(np.mean(np.any(c >= 255, axis=2)[hair_proxy]))

    # Transition-band diagnostics are informative only; intentional bokeh can change them.
    transition = (m >= 0.20) & (m < 0.65)
    diff = np.mean(np.abs(c.astype(np.int16) - a.astype(np.int16)), axis=2)
    transition_mae = float(np.mean(diff[transition])) if np.any(transition) else 0.0
    transition_p95 = float(np.percentile(diff[transition], 95)) if np.any(transition) else 0.0

    gates = {
        "hair_proxy_structure": g_corr >= 0.995,
        "hair_proxy_edge_integrity": 0.90 <= edge_ratio <= 1.15,
        "hair_color_median_hue": hue_med <= 2.0,
        "hair_color_p95_hue": hue_p95 <= 6.0,
        "hair_color_saturation": sat_med <= 4.0,
        "hair_proxy_highlight_clip_within_limit": cand_clip_hair <= 0.015,
    }
    hard_pass = all(gates.values())

    report = {
        "status": "PASS_HAIR_FIDELITY" if hard_pass else "REVIEW_HAIR_FIDELITY",
        "authority": str(authority_p),
        "candidate": str(candidate_p),
        "mask_authority": {
            "type": "MODNet upper-subject proxy",
            "dedicated_hair_mask": False,
            "note": "Hair-specific metrics are conservative proxies until a dedicated hair segmentation mask is supplied.",
            "subject_bbox_xyxy": [x0, y0, x1, y1],
            "hair_proxy_y_range": [y0, hair_y1],
            "hair_proxy_pixels": int(np.count_nonzero(hair_proxy)),
        },
        "gates": gates,
        "metrics": {
            "hair_proxy_gradient_correlation": g_corr,
            "hair_proxy_edge_energy_ratio_candidate_over_authority": edge_ratio,
            "median_hue_shift_degrees": hue_med,
            "p95_hue_shift_degrees": hue_p95,
            "median_saturation_shift_8bit": sat_med,
            "authority_subject_core_highlight_clip_fraction": auth_clip_core,
            "candidate_subject_core_highlight_clip_fraction": cand_clip_core,
            "authority_hair_proxy_highlight_clip_fraction": auth_clip_hair,
            "candidate_hair_proxy_highlight_clip_fraction": cand_clip_hair,
            "transition_band_mean_abs_error": transition_mae,
            "transition_band_p95_abs_error": transition_p95,
        },
        "thresholds": {
            "gradient_correlation_min": 0.995,
            "edge_energy_ratio_min": 0.90,
            "edge_energy_ratio_max": 1.15,
            "median_hue_shift_degrees_max": 2.0,
            "p95_hue_shift_degrees_max": 6.0,
            "median_saturation_shift_8bit_max": 4.0,
            "hair_proxy_highlight_clip_fraction_max": 0.015,
        },
        "policy": {
            "transition_band_is_diagnostic_not_hard_gate": True,
            "subject_core_highlight_is_diagnostic_not_hair_gate": True,
            "candidate_cannot_be_promoted_by_metrics_alone": True,
            "visual_hair_edge_review_required_for_final_acceptance": True,
        },
    }

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    if not hard_pass:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
