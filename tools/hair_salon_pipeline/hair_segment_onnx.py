#!/usr/bin/env python3
import argparse
import json
from pathlib import Path

import cv2
import numpy as np
import onnxruntime as ort
from PIL import Image, ImageDraw

HAIR_CLASS = 17


def main():
    ap = argparse.ArgumentParser(description="Generate a dedicated source-derived hair mask with BiSeNet ONNX")
    ap.add_argument("--source", required=True)
    ap.add_argument("--matte", required=True, help="Permissive subject matte used only for crop location/background suppression")
    ap.add_argument("--model", required=True)
    ap.add_argument("--output-mask", required=True)
    ap.add_argument("--output-report", required=True)
    ap.add_argument("--output-qa")
    args = ap.parse_args()

    src = np.asarray(Image.open(args.source).convert("RGB"))
    matte = np.asarray(Image.open(args.matte).convert("L")).astype(np.float32) / 255.0
    h, w = src.shape[:2]
    if matte.shape != (h, w):
        raise ValueError(f"matte shape {matte.shape} != source {(h, w)}")

    subject = matte >= 0.20
    if np.count_nonzero(subject) < 1000:
        raise ValueError("subject matte is empty")
    ys, xs = np.where(subject)
    sx0, sx1 = int(xs.min()), int(xs.max()) + 1
    sy0, sy1 = int(ys.min()), int(ys.max()) + 1
    sw, sh = sx1 - sx0, sy1 - sy0

    # Face parsing still needs enough context for shoulder/chest-length salon hair.
    cy0 = max(0, sy0 - int(0.06 * sh))
    cy1 = min(h, sy0 + int(0.76 * sh))
    cx0 = max(0, sx0 - int(0.08 * sw))
    cx1 = min(w, sx1 + int(0.08 * sw))
    crop = src[cy0:cy1, cx0:cx1]
    ch, cw = crop.shape[:2]
    side = max(ch, cw)
    canvas = np.full((side, side, 3), 127, dtype=np.uint8)
    ox, oy = (side - cw) // 2, (side - ch) // 2
    canvas[oy:oy + ch, ox:ox + cw] = crop

    inp = cv2.resize(canvas, (512, 512), interpolation=cv2.INTER_LINEAR).astype(np.float32) / 255.0
    mean = np.array([0.485, 0.456, 0.406], dtype=np.float32)
    std = np.array([0.229, 0.224, 0.225], dtype=np.float32)
    inp = np.transpose((inp - mean) / std, (2, 0, 1))[None].astype(np.float32)

    sess = ort.InferenceSession(args.model, providers=["CPUExecutionProvider"])
    logits = sess.run([o.name for o in sess.get_outputs()], {sess.get_inputs()[0].name: inp})[0]
    labels = logits.squeeze(0).argmax(0).astype(np.uint8)
    labels = cv2.resize(labels, (side, side), interpolation=cv2.INTER_NEAREST)[oy:oy + ch, ox:ox + cw]

    hair = np.zeros((h, w), dtype=np.uint8)
    hair[cy0:cy1, cx0:cx1] = (labels == HAIR_CLASS).astype(np.uint8) * 255
    # The dedicated model decides hair; MODNet only suppresses obvious non-subject background.
    hair[matte < 0.05] = 0
    hb = hair > 0
    area = int(np.count_nonzero(hb))
    subject_area = int(np.count_nonzero(subject))
    area_fraction = float(area / max(subject_area, 1))
    core_overlap = float(np.mean(matte[hb] >= 0.65)) if area else 0.0

    n, _, stats, _ = cv2.connectedComponentsWithStats(hb.astype(np.uint8), 8)
    comps = sorted([int(stats[i, cv2.CC_STAT_AREA]) for i in range(1, n)], reverse=True)
    largest_fraction = float(comps[0] / area) if comps and area else 0.0

    sanity = {
        "nonempty": area >= 5000,
        "area_fraction_reasonable": 0.05 <= area_fraction <= 0.55,
        "core_overlap": core_overlap >= 0.80,
        "dominant_component": largest_fraction >= 0.70,
    }
    hard_pass = all(sanity.values())

    out_mask = Path(args.output_mask)
    out_mask.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(hair).save(out_mask)

    report = {
        "status": "PASS_DEDICATED_HAIR_MASK" if hard_pass else "REVIEW_DEDICATED_HAIR_MASK",
        "model": "yakhyo/face-parsing ResNet18 ONNX",
        "license": "MIT",
        "training_dataset": "CelebAMask-HQ",
        "hair_class": HAIR_CLASS,
        "source_derived": True,
        "crop_xyxy": [cx0, cy0, cx1, cy1],
        "sanity_gates": sanity,
        "metrics": {
            "hair_pixels": area,
            "hair_over_subject_fraction": area_fraction,
            "hair_pixels_in_modnet_core_fraction": core_overlap,
            "largest_connected_component_fraction": largest_fraction,
        },
    }
    Path(args.output_report).write_text(json.dumps(report, indent=2), encoding="utf-8")

    if args.output_qa:
        overlay = src.astype(np.float32)
        tint = np.array([255, 40, 180], dtype=np.float32)
        overlay[hb] = 0.62 * overlay[hb] + 0.38 * tint
        qa = Image.fromarray(np.clip(overlay, 0, 255).astype(np.uint8))
        draw = ImageDraw.Draw(qa)
        draw.rectangle((cx0, cy0, cx1 - 1, cy1 - 1), outline=(0, 255, 255), width=5)
        Path(args.output_qa).parent.mkdir(parents=True, exist_ok=True)
        qa.save(args.output_qa, quality=95)

    print(json.dumps(report, indent=2))
    if not hard_pass:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
