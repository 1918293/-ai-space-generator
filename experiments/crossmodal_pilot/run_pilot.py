import csv
import json
import time
from pathlib import Path

import cv2
import numpy as np

OUT = Path("pilot_output")
OUT.mkdir(exist_ok=True)


def make_scene(seed: int, label: str) -> np.ndarray:
    rng = np.random.default_rng(seed)
    img = np.full((640, 960, 3), 245, np.uint8)
    for _ in range(28):
        x1 = int(rng.integers(40, 800))
        y1 = int(rng.integers(40, 520))
        x2 = x1 + int(rng.integers(25, 120))
        y2 = y1 + int(rng.integers(25, 120))
        color = tuple(int(v) for v in rng.integers(20, 220, size=3))
        if rng.random() < 0.5:
            cv2.rectangle(img, (x1, y1), (min(x2, 930), min(y2, 610)), color, 2)
        else:
            cv2.circle(img, (x1, y1), int(rng.integers(10, 45)), color, 2)
    cv2.putText(img, label, (70, 600), cv2.FONT_HERSHEY_SIMPLEX, 1.5, (20, 20, 20), 3, cv2.LINE_AA)
    return img


def transform(img: np.ndarray, rotate_deg: float, scale: float, tx: float, ty: float) -> np.ndarray:
    h, w = img.shape[:2]
    M = cv2.getRotationMatrix2D((w / 2, h / 2), rotate_deg, scale)
    M[:, 2] += [tx, ty]
    return cv2.warpAffine(img, M, (w, h), flags=cv2.INTER_LINEAR, borderValue=(255, 255, 255))


def extract_sift(sift, img):
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    kp, des = sift.detectAndCompute(gray, None)
    return kp, des


def match_pair(kp1, des1, kp2, des2):
    if des1 is None or des2 is None or len(des1) < 2 or len(des2) < 2:
        return {"good": 0, "inliers": 0, "inlier_ratio": 0.0, "median_reproj": None}
    bf = cv2.BFMatcher(cv2.NORM_L2)
    knn = bf.knnMatch(des1, des2, k=2)
    good = [m for m, n in knn if m.distance < 0.72 * n.distance]
    if len(good) < 4:
        return {"good": len(good), "inliers": 0, "inlier_ratio": 0.0, "median_reproj": None}
    src = np.float32([kp1[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
    dst = np.float32([kp2[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
    H, mask = cv2.findHomography(src, dst, cv2.RANSAC, 4.0)
    if H is None or mask is None:
        return {"good": len(good), "inliers": 0, "inlier_ratio": 0.0, "median_reproj": None}
    mask = mask.ravel().astype(bool)
    inliers = int(mask.sum())
    reproj = cv2.perspectiveTransform(src, H)
    err = np.linalg.norm(reproj[:, 0, :] - dst[:, 0, :], axis=1)
    med = float(np.median(err[mask])) if inliers else None
    return {
        "good": len(good),
        "inliers": inliers,
        "inlier_ratio": round(inliers / len(good), 4),
        "median_reproj": None if med is None else round(med, 4),
    }


def main():
    sift = cv2.SIFT_create(nfeatures=1500)

    base_a = make_scene(7, "POSITIVE-A")
    base_b = make_scene(19, "POSITIVE-B")
    # Hard negative: same visible topic label but independently generated geometry.
    # It must not reuse source pixels; otherwise it is a real edited-variant positive.
    hard_negative = make_scene(31, "POSITIVE-A")
    cv2.putText(hard_negative, "DIFFERENT ARTIFACT", (250, 80), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (20, 20, 20), 2, cv2.LINE_AA)

    images = {
        "a_source": base_a,
        "a_target": transform(base_a, 4.0, 0.82, 35, -15),
        "b_source": base_b,
        "b_target": transform(base_b, -3.0, 0.88, -28, 22),
        "hard_negative": hard_negative,
        "unrelated_1": make_scene(100, "UNRELATED-1"),
        "unrelated_2": make_scene(101, "UNRELATED-2"),
    }

    for name, img in images.items():
        cv2.imwrite(str(OUT / f"{name}.jpg"), img)

    # One-time descriptor cache benchmark.
    t0 = time.perf_counter()
    cache = {name: extract_sift(sift, img) for name, img in images.items()}
    extraction_ms = (time.perf_counter() - t0) * 1000

    tests = [
        ("positive_a", "a_source", "a_target", "MATCH"),
        ("positive_b", "b_source", "b_target", "MATCH"),
        ("hard_negative", "a_source", "hard_negative", "NO_MATCH"),
        ("unrelated_1", "a_source", "unrelated_1", "NO_MATCH"),
        ("unrelated_2", "b_source", "unrelated_2", "NO_MATCH"),
    ]

    rows = []
    t1 = time.perf_counter()
    for test_name, q, t, expected in tests:
        metrics = match_pair(*cache[q], *cache[t])
        # Conservative synthetic pilot gate only; not a universal research threshold.
        predicted = "MATCH" if metrics["inliers"] >= 12 and metrics["inlier_ratio"] >= 0.35 else "NO_MATCH"
        rows.append({
            "test": test_name,
            "query": q,
            "target": t,
            "expected": expected,
            "predicted": predicted,
            **metrics,
            "pass": predicted == expected,
        })
    cached_match_ms = (time.perf_counter() - t1) * 1000

    # Compare one representative pair with and without descriptor reuse.
    repeats = 8
    t2 = time.perf_counter()
    for _ in range(repeats):
        kp1, des1 = extract_sift(sift, images["a_source"])
        kp2, des2 = extract_sift(sift, images["a_target"])
        match_pair(kp1, des1, kp2, des2)
    recompute_ms_per_pair = ((time.perf_counter() - t2) * 1000) / repeats

    kp1, des1 = cache["a_source"]
    kp2, des2 = cache["a_target"]
    t3 = time.perf_counter()
    for _ in range(repeats):
        match_pair(kp1, des1, kp2, des2)
    cached_ms_per_pair = ((time.perf_counter() - t3) * 1000) / repeats

    summary = {
        "opencv_version": cv2.__version__,
        "image_count": len(images),
        "test_count": len(tests),
        "all_regressions_pass": all(r["pass"] for r in rows),
        "descriptor_extraction_total_ms": round(extraction_ms, 3),
        "descriptor_extraction_avg_ms_per_image": round(extraction_ms / len(images), 3),
        "cached_matching_total_ms": round(cached_match_ms, 3),
        "cached_matching_avg_ms_per_test_pair": round(cached_match_ms / len(tests), 3),
        "recompute_ms_per_pair": round(recompute_ms_per_pair, 3),
        "cached_ms_per_pair": round(cached_ms_per_pair, 3),
        "pair_stage_speedup_x": round(recompute_ms_per_pair / max(cached_ms_per_pair, 1e-9), 2),
        "guardrail": "Scores propose candidates only; terminal identity still requires external temporal/referent/manual verification.",
    }

    with open(OUT / "pilot_results.json", "w", encoding="utf-8") as f:
        json.dump({"summary": summary, "tests": rows}, f, ensure_ascii=False, indent=2)

    with open(OUT / "pilot_results.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    print(json.dumps({"summary": summary, "tests": rows}, ensure_ascii=False, indent=2))
    if not summary["all_regressions_pass"]:
        raise SystemExit("Regression gate failed")


if __name__ == "__main__":
    main()
