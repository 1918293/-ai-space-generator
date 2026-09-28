#!/usr/bin/env python3
"""Hosted Qwen-Image reconstruction runner for Salon experiments.

Runs on ordinary CPU hosts because inference is remote. Requires DASHSCOPE_API_KEY.
Private portrait/control assets are read from PACK_DIR and are never committed here.
"""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import Any

import requests

ROOT = Path(os.environ.get("PACK_DIR", ".")).resolve()
OUT = Path(os.environ.get("OUT_DIR", ROOT / "outputs_qwencloud")).resolve()
OUT.mkdir(parents=True, exist_ok=True)

MODEL = os.environ.get("QWEN_IMAGE_MODEL", "qwen-image-3.0-pro")
BASE_URL = os.environ.get("QWEN_IMAGE_BASE_URL", "https://dashscope-intl.aliyuncs.com/api/v1").rstrip("/")
API_KEY = os.environ.get("DASHSCOPE_API_KEY", "").strip()
if not API_KEY:
    raise SystemExit("DASHSCOPE_API_KEY is required")

SOURCE = ROOT / os.environ.get("SOURCE_IMAGE", "01_Source_Original.png")
GARMENT = ROOT / os.environ.get("GARMENT_IMAGE", "02_Garment_Reference_CALIFORNIA.png")
STRUCTURE = ROOT / os.environ.get("STRUCTURE_IMAGE", "03_Target_Keypoints_UpperBody_Strong.png")
PROFILE = Path(os.environ.get(
    "PROFILE_JSON",
    Path(__file__).resolve().parent / "profiles" / "upper_body_strong_slim_v01.json",
))

for p in (SOURCE, GARMENT, STRUCTURE, PROFILE):
    if not p.exists():
        raise SystemExit(f"Missing required input: {p}")

profile: dict[str, Any] = json.loads(PROFILE.read_text(encoding="utf-8"))

prompt = f"""
Reconstruct the same person from Image 1 according to profile {profile['id']}.
The primary objective is: {profile['primary_objective']}

Image authority:
- Image 1: identity, face, hair, hands, phone, lighting and salon-scene authority.
- Image 2: black washed CALIFORNIA / LONG BEACH / SUNSET T-shirt design, print, material and garment authority.
- Image 3: target upper-body geometry authority.

Rebuild the shoulders, upper-arm volume, ribcage/torso width, waist/abdomen visual width,
sleeves, arm-to-torso spacing and T-shirt drape as a coherent new body-and-garment result.
Do not merely squeeze, liquify or warp the source pixels. The result must read clearly slimmer
at first glance. Preserve the same person, face, hairstyle, hair color, phone, hand identity,
shirt identity and readable print, and retain a believable visual relationship to the salon scene.
Naturalness is a final QA gate, not a reason to make the slimming subtle.
Return one normal photorealistic portrait only; no collage, labels, borders or infographic.
""".strip()

negative = (
    "different person, changed face, changed hairstyle, deformed hands, extra fingers, "
    "warped phone, melted sleeves, distorted shirt lettering, unreadable CALIFORNIA text, "
    "horizontal squeeze, liquify artifacts, stretched background, plastic AI skin, collage, labels"
)


def data_uri(path: Path) -> str:
    ext = path.suffix.lower()
    mime = "image/jpeg" if ext in {".jpg", ".jpeg"} else "image/png"
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"

content = [
    {"image": data_uri(SOURCE)},
    {"image": data_uri(GARMENT)},
    {"image": data_uri(STRUCTURE)},
    {"text": prompt},
]
payload = {
    "model": MODEL,
    "input": {"messages": [{"role": "user", "content": content}]},
    "parameters": {
        "n": 1,
        "negative_prompt": negative,
        "prompt_extend": True,
        "prompt_extend_mode": "direct",
        "enable_thinking": True,
        "watermark": False,
        "seed": int(os.environ.get("SEED", "47")),
    },
}

if os.environ.get("DRY_RUN") == "1":
    safe = json.loads(json.dumps(payload))
    for part in safe["input"]["messages"][0]["content"]:
        if "image" in part:
            part["image"] = f"<data-uri {len(part['image'])} chars>"
    print(json.dumps(safe, ensure_ascii=False, indent=2))
    raise SystemExit(0)

url = BASE_URL + "/services/aigc/multimodal-generation/generation"
r = requests.post(
    url,
    headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
    json=payload,
    timeout=300,
)
try:
    data = r.json()
except ValueError as exc:
    raise RuntimeError(f"Non-JSON response ({r.status_code}): {r.text[:500]}") from exc
if r.status_code >= 400 or data.get("code"):
    raise RuntimeError(f"Qwen-Image failed ({r.status_code}): {data.get('code')} {data.get('message')}")

urls: list[str] = []
for choice in (data.get("output") or {}).get("choices") or []:
    for part in ((choice.get("message") or {}).get("content") or []):
        if isinstance(part, dict) and part.get("image"):
            urls.append(part["image"])
if not urls:
    raise RuntimeError(f"No image URL returned: {data}")

for i, image_url in enumerate(urls, 1):
    image = requests.get(image_url, timeout=90)
    image.raise_for_status()
    target = OUT / f"qwencloud_upper_body_strong_{i}.png"
    target.write_bytes(image.content)
    print(target)
