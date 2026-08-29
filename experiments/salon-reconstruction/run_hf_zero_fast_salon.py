#!/usr/bin/env python3
"""Hosted Salon reconstruction through fast Hugging Face Zero-GPU Qwen backends.

This is the single runner authority for the free Zero-GPU lane.
Private portrait/control assets stay outside the public repository and are read from
PACK_DIR at runtime.

Backends:
- lora_fast: prithivMLmods/Qwen-Image-Edit-2511-LoRAs-Fast
- lightning: LPX55/Qwen-Image-Edit-2511-Turbo-Lightning

The tested Lightning backend is execution-proven on the current Salon portrait, but its
outputs showed garment/hand/phone preservation failures. Treat it as an exploration
backend, not an accepted production result generator.
"""
from __future__ import annotations

import base64
import io
import json
import os
from pathlib import Path
from typing import Iterable

from PIL import Image
from gradio_client import Client, handle_file

ROOT = Path(os.environ.get("PACK_DIR", ".")).resolve()
OUT = Path(os.environ.get("OUT_DIR", ROOT / "outputs_hf_zero")).resolve()
OUT.mkdir(parents=True, exist_ok=True)

BACKEND = os.environ.get("HF_ZERO_BACKEND", "lora_fast").strip().lower()
INPUT_MODE = os.environ.get("HF_ZERO_INPUT_MODE", "source_garment_structure").strip().lower()
VARIANT = os.environ.get("HF_ZERO_VARIANT", "preserve_garment").strip().lower()
LORA = os.environ.get("HF_ZERO_LORA", "Studio-DeLight")
STEPS = int(os.environ.get("HF_ZERO_STEPS", "4"))
GUIDANCE = float(os.environ.get("HF_ZERO_GUIDANCE", "1.0"))
SEED = int(os.environ.get("SEED", "47"))
HEIGHT = int(os.environ.get("HF_ZERO_HEIGHT", "1024"))
WIDTH = int(os.environ.get("HF_ZERO_WIDTH", "576"))

SOURCE = ROOT / os.environ.get("SOURCE_IMAGE", "01_Source_Original.png")
GARMENT = ROOT / os.environ.get("GARMENT_IMAGE", "02_Garment_Reference_CALIFORNIA.png")
STRUCTURE = ROOT / os.environ.get(
    "STRUCTURE_IMAGE", "03_Target_Keypoints_UpperBody_Strong.png"
)
PROFILE = Path(
    os.environ.get(
        "PROFILE_JSON",
        Path(__file__).resolve().parent / "profiles" / "upper_body_strong_slim_v01.json",
    )
)

BACKENDS = {
    "lora_fast": {
        "space": "prithivMLmods/Qwen-Image-Edit-2511-LoRAs-Fast",
        "role": "garment-aware LoRA comparator",
    },
    "lightning": {
        "space": "LPX55/Qwen-Image-Edit-2511-Turbo-Lightning",
        "role": "execution-proven strong reconstruction explorer",
    },
}

if BACKEND not in BACKENDS:
    raise SystemExit(f"Unknown HF_ZERO_BACKEND={BACKEND!r}; choose {sorted(BACKENDS)}")
if INPUT_MODE not in {"source_only", "source_garment", "source_garment_structure"}:
    raise SystemExit(
        "HF_ZERO_INPUT_MODE must be source_only, source_garment, or source_garment_structure"
    )

required = [SOURCE, PROFILE]
if INPUT_MODE in {"source_garment", "source_garment_structure"}:
    required.append(GARMENT)
if INPUT_MODE == "source_garment_structure":
    required.append(STRUCTURE)
for path in required:
    if not path.exists():
        raise SystemExit(f"Missing required input: {path}")

profile = json.loads(PROFILE.read_text(encoding="utf-8"))

COMMON_PROMPT = f"""
Reconstruct the same person from Image 1 according to profile {profile['id']}.
Primary objective: {profile['primary_objective']}

Image 1 is the dominant authority for identity, face, hairstyle, hair color, downward gaze,
hands, phone, lighting and salon-scene relationship. Make the visible upper body clearly and
substantially slimmer at first glance by coherently rebuilding shoulder width, upper-arm volume,
ribcage/torso width, waist/abdomen visual width, arm-to-torso spacing and clothing drape.
Do not simply squeeze, liquify or horizontally warp the source pixels.

Garment hard constraints: preserve the same black washed CALIFORNIA / LONG BEACH / SUNSET
T-shirt identity, crew neckline, both covered shoulders, short sleeves, loose untucked length
covering the waist/abdomen and recognizably readable graphic. Do not convert it into a crop top,
tank, sleeveless top, cold-shoulder top or exposed-midriff garment. Keep hands anatomically
plausible and close to source size. Keep the phone recognizable and close to source pose/location.
Naturalness is a final QA gate, not a reason to make the slimming subtle. Return one normal
photorealistic salon portrait only, with no collage, labels, borders or infographic.
""".strip()

VARIANT_PROMPTS = {
    "preserve_garment": (
        " If Image 2 is present, use it only as exact garment identity/material/print authority."
        " Re-drape that same oversized shirt naturally over the slimmer body instead of shrinking"
        " it like skin. If Image 3 is present, use it only as a directional body-geometry hint;"
        " do not copy its colors, background, line style or abstract appearance into the photo."
    ),
    "strong_keypoints": (
        " If Image 3 is present, use its pose/body proportions as a strong directional geometry"
        " reference while keeping Image 1 dominant for appearance and Image 2 dominant for garment."
    ),
    "identity_first": (
        " Prioritize same-person face/hair/hands/phone and exact garment construction over following"
        " abstract structure literally; slimming must still remain obvious at first glance."
    ),
}
PROMPT = COMMON_PROMPT + VARIANT_PROMPTS.get(VARIANT, VARIANT_PROMPTS["preserve_garment"])


def selected_paths() -> list[Path]:
    paths = [SOURCE]
    if INPUT_MODE in {"source_garment", "source_garment_structure"}:
        paths.append(GARMENT)
    if INPUT_MODE == "source_garment_structure":
        paths.append(STRUCTURE)
    return paths


def image_to_data_uri(path: Path) -> str:
    image = Image.open(path).convert("RGB")
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def save_normalized_image(raw: bytes, target: Path) -> None:
    image = Image.open(io.BytesIO(raw)).convert("RGB")
    image.save(target, format="PNG")
    with Image.open(target) as check:
        check.verify()


def run_lora_fast(paths: Iterable[Path]) -> bytes:
    space = os.environ.get("HF_ZERO_SPACE", BACKENDS["lora_fast"]["space"])
    images_json = json.dumps([image_to_data_uri(path) for path in paths])
    client = Client(space, verbose=False)
    api = client.view_api(return_format="dict")
    named = api.get("named_endpoints") or {}
    endpoint = "/edit_image" if "/edit_image" in named else next(
        (key for key in named if "edit_image" in key), None
    )
    if endpoint is None:
        raise RuntimeError(f"No edit_image endpoint found on {space}: {list(named)}")
    result = client.predict(
        images_json,
        PROMPT,
        LORA,
        SEED,
        False,
        GUIDANCE,
        STEPS,
        api_name=endpoint,
    )
    if not isinstance(result, dict) or not result.get("image"):
        raise RuntimeError(f"Unexpected lora_fast result: {type(result)} {result}")
    image_data = result["image"]
    if image_data.startswith("data:image"):
        _, image_data = image_data.split(",", 1)
    return base64.b64decode(image_data)


def run_lightning(paths: Iterable[Path]) -> bytes:
    space = os.environ.get("HF_ZERO_SPACE", BACKENDS["lightning"]["space"])
    gallery = [
        {"image": handle_file(str(path)), "caption": None}
        for path in paths
    ]
    client = Client(space, verbose=False)
    result = client.predict(
        images=gallery,
        prompt=PROMPT,
        seed=SEED,
        randomize_seed=False,
        true_guidance_scale=GUIDANCE,
        num_inference_steps=STEPS,
        height=HEIGHT,
        width=WIDTH,
        rewrite_prompt=False,
        num_images_per_prompt=1,
        api_name="/infer",
    )
    outputs = result[0] if isinstance(result, tuple) else result
    if not outputs:
        raise RuntimeError("Lightning backend returned no gallery output")
    first = outputs[0]
    image = first.get("image", first) if isinstance(first, dict) else first
    path = image if isinstance(image, str) else image.get("path") if isinstance(image, dict) else None
    if not path:
        raise RuntimeError(f"No Lightning output path: {first}")
    source = Path(path)
    if not source.exists() or source.stat().st_size == 0:
        raise RuntimeError(f"Lightning output missing/empty: {source}")
    return source.read_bytes()


paths = selected_paths()
raw = run_lora_fast(paths) if BACKEND == "lora_fast" else run_lightning(paths)
target = OUT / f"hf_zero_{BACKEND}_{VARIANT}.png"
save_normalized_image(raw, target)
print(target)
print(
    f"backend={BACKEND} role={BACKENDS[BACKEND]['role']} input_mode={INPUT_MODE} "
    f"variant={VARIANT} seed={SEED} steps={STEPS}"
)
