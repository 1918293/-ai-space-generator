#!/usr/bin/env python3
import argparse
import json
import os
from pathlib import Path

import cv2
import numpy as np
from PIL import Image


def load_rgb(path: Path) -> np.ndarray:
    return np.asarray(Image.open(path).convert("RGB"))


def load_matte(path: Path, size) -> np.ndarray:
    matte = Image.open(path).convert("L")
    if matte.size != size:
        raise ValueError(f"matte size {matte.size} != source size {size}")
    return np.asarray(matte).astype(np.float32) / 255.0


def edge_energy(rgb: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY).astype(np.float32)
    gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    return np.sqrt(gx * gx + gy * gy)


def main():
    ap = argparse.ArgumentParser(description="Defect-driven preflight for salon portrait production")
    ap.add_argument("--source", required=True)
    ap.add_argument("--matte")
    ap.add_argument("--output", required=True)
    ap.add_argument("--short-edge-target", type=int, default=1600)
    ap.add_argument("--bokeh-review-ratio", type=float, default=0.35)
    ap.add_argument(
        "--object-removal-requested",
        action="store_true",
        help="Declare that object removal is an actual job intent. Routing alone never infers this intent.",
    )
    args = ap.parse_args()

    src_path = Path(args.source)
    src = load_rgb(src_path)
    h, w = src.shape[:2]
    short_edge = min(w, h)
    mp = (w * h) / 1_000_000.0

    highlight_clip = float(np.mean(np.any(src >= 255, axis=2)))
    black_clip = float(np.mean(np.all(src <= 0, axis=2)))
    edges = edge_energy(src)
    lap_var = float(cv2.Laplacian(cv2.cvtColor(src, cv2.COLOR_RGB2GRAY), cv2.CV_64F).var())

    matte = None
    subject_edge = None
    background_edge = None
    edge_ratio = None
    bokeh_review = "UNKNOWN_NO_MATTE"
    if args.matte:
        matte = load_matte(Path(args.matte), (w, h))
        subject = matte >= 0.65
        background = matte < 0.20
        if np.any(subject) and np.any(background):
            subject_edge = float(np.mean(edges[subject]))
            background_edge = float(np.mean(edges[background]))
            edge_ratio = float(background_edge / max(subject_edge, 1e-6))
            # Review only: a relatively busy background may benefit from separation.
            # This is a routing heuristic, not an automatic visual decision.
            bokeh_review = "REVIEW" if edge_ratio > args.bokeh_review_ratio else "SKIP"

    needs_sr = short_edge < args.short_edge_target
    tone_review = highlight_clip > 0.010 or black_clip > 0.005
    object_removal = "MANUAL_DEFECT_MASK_REQUIRED" if args.object_removal_requested else "NOT_REQUESTED"

    route = []
    route.append("SR" if needs_sr else "SKIP_SR")
    route.append("DEFECT_MASK_REVIEW" if args.object_removal_requested else "SKIP_OBJECT_REMOVAL")
    route.append("BOKEH_REVIEW" if bokeh_review == "REVIEW" else "SKIP_BOKEH" if bokeh_review == "SKIP" else "BOKEH_UNKNOWN")
    route.append("TONE_REVIEW" if tone_review else "SKIP_TONE_CORRECTION")
    route.append("HAIR_QA")

    report = {
        "status": "PREFLIGHT_COMPLETE",
        "source": str(src_path),
        "image": {
            "width": w,
            "height": h,
            "megapixels": mp,
            "short_edge": short_edge,
            "laplacian_variance": lap_var,
            "highlight_clip_fraction": highlight_clip,
            "black_clip_fraction": black_clip,
        },
        "intent": {
            "object_removal_requested": bool(args.object_removal_requested),
        },
        "decisions": {
            "sr": "RUN" if needs_sr else "SKIP",
            "object_removal": object_removal,
            "bokeh": bokeh_review,
            "tone": "REVIEW" if tone_review else "SKIP",
        },
        "diagnostics": {
            "subject_edge_energy": subject_edge,
            "background_edge_energy": background_edge,
            "background_to_subject_edge_ratio": edge_ratio,
            "bokeh_review_ratio_threshold": args.bokeh_review_ratio,
            "matte_available": matte is not None,
        },
        "route": route,
        "policy": {
            "object_removal_intent_is_never_inferred": True,
            "no_automatic_inpainting_without_explicit_defect_mask": True,
            "skip_unneeded_expensive_stages": True,
            "preflight_is_a_router_not_a_visual_acceptance_gate": True,
            "explicit_visual_direction_can_override_router": True,
        },
    }

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))

    gh_out = os.getenv("GITHUB_OUTPUT")
    if gh_out:
        with open(gh_out, "a", encoding="utf-8") as f:
            f.write(f"needs_sr={'true' if needs_sr else 'false'}\n")
            f.write(f"object_removal_requested={'true' if args.object_removal_requested else 'false'}\n")
            f.write(f"bokeh_review={bokeh_review}\n")
            f.write(f"tone_review={'true' if tone_review else 'false'}\n")
            f.write("route=" + "->".join(route) + "\n")


if __name__ == "__main__":
    main()
