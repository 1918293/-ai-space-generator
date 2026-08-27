#!/usr/bin/env python3
"""Free hosted Salon reconstruction through a Hugging Face Zero GPU Space.

Reads private source/garment/structure images from PACK_DIR, sends them as base64
in-memory inputs to a Qwen-Image-Edit-2511 fast Zero GPU Space, and writes the
returned PNG locally. No local GPU or DashScope API key is required.
"""
from __future__ import annotations

import base64
import io
import json
import os
from pathlib import Path

from PIL import Image
from gradio_client import Client

ROOT = Path(os.environ.get("PACK_DIR", ".")).resolve()
OUT = Path(os.environ.get("OUT_DIR", ROOT / "outputs_hf_zero")).resolve()
OUT.mkdir(parents=True, exist_ok=True)

SPACE = os.environ.get(
    "HF_ZERO_SPACE",
    "prithivMLmods/Qwen-Image-Edit-2511-LoRAs-Fast",
)
LORA = os.environ.get("HF_ZERO_LORA", "Studio-DeLight")
STEPS = int(os.environ.get("HF_ZERO_STEPS", "4"))
GUIDANCE = float(os.environ.get("HF_ZERO_GUIDANCE", "1.0"))
SEED = int(os.environ.get("SEED", "47"))

SOURCE = ROOT / os.environ.get("SOURCE_IMAGE", "01_Source_Original.png")
GARMENT = ROOT / os.environ.get(
    "GARMENT_IMAGE", "02_Garment_Reference_CALIFORNIA.png"
)
STRUCTURE = ROOT / os.environ.get(
    "STRUCTURE_IMAGE", "03_Target_Keypoints_UpperBody_Strong.png"
)
PROFILE = Path(
    os.environ.get(
        "PROFILE_JSON",
        Path(__file__).resolve().parent / "profiles" / "upper_body_strong_slim_v01.json",
    )
)

for path in (SOURCE, GARMENT, STRUCTURE, PROFILE):
    if not path.exists():
        raise SystemExit(f"Missing required input: {path}")

profile = json.loads(PROFILE.read_text(encoding="utf-8"))

prompt = f"""
Reconstruct the same person from Image 1 according to profile {profile['id']}.
Primary objective: {profile['primary_objective']}

Image 1 is identity, face, hairstyle, hair color, hands, phone, lighting and salon-scene authority.
Image 2 is the black washed CALIFORNIA / LONG BEACH / SUNSET T-shirt design, print,
material and garment authority.
Image 3 is target upper-body geometry authority.

Create a clearly and substantially slimmer upper-body result at first glance. Rebuild the
shoulder width, upper-arm volume, ribcage/torso width, waist/abdomen visual width, sleeves,
arm-to-torso spacing and oversized T-shirt drape as a coherent body-and-garment reconstruction.
Do not simply squeeze, liquify or horizontally warp the source. Keep the same person and face,
the same brown hairstyle, the same phone and hand identity, and the same readable shirt identity.
The shirt may re-drape naturally over the slimmer body and should lose unnecessary boxy bulk while
remaining recognizably oversized/washed. Reconstruct occlusions and newly exposed areas naturally.
Naturalness is a final QA gate, not a reason to make the slimming subtle. Return one normal
photorealistic salon portrait only, with no collage, labels, borders or infographic.
""".strip()


def image_to_data_uri(path: Path) -> str:
    image = Image.open(path).convert("RGB")
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


images_json = json.dumps(
    [
        image_to_data_uri(SOURCE),
        image_to_data_uri(GARMENT),
        image_to_data_uri(STRUCTURE),
    ]
)

client = Client(SPACE, verbose=False)
api = client.view_api(return_format="dict")
named = api.get("named_endpoints") or {}
endpoint = None
for candidate in ("/edit_image", "edit_image"):
    if candidate in named:
        endpoint = candidate
        break
if endpoint is None:
    endpoint = next((key for key in named if "edit_image" in key), None)
if endpoint is None:
    raise RuntimeError(f"No edit_image endpoint found on {SPACE}: {list(named)}")

result = client.predict(
    images_json,
    prompt,
    LORA,
    SEED,
    False,
    GUIDANCE,
    STEPS,
    api_name=endpoint,
)

# Current Server API returns a dict containing a base64 PNG data URL.
if not isinstance(result, dict) or not result.get("image"):
    raise RuntimeError(f"Unexpected Zero GPU result: {type(result)} {result}")

image_data = result["image"]
if image_data.startswith("data:image"):
    _, image_data = image_data.split(",", 1)
raw = base64.b64decode(image_data)

target = OUT / "hf_zero_upper_body_strong.png"
target.write_bytes(raw)
print(target)
print(f"seed={result.get('seed', SEED)} space={SPACE} lora={LORA} steps={STEPS}")
