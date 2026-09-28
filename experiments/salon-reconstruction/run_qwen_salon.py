#!/usr/bin/env python3
from pathlib import Path
import os
import torch
from PIL import Image
from diffusers import QwenImageEditPlusPipeline

ROOT = Path(os.environ.get("PACK_DIR", Path(__file__).resolve().parent))
OUT = Path(os.environ.get("OUT_DIR", ROOT / "outputs_qwen"))
OUT.mkdir(parents=True, exist_ok=True)

source = Image.open(ROOT / "01_Source_Original.png").convert("RGB")
garment = Image.open(ROOT / "02_Garment_Reference_CALIFORNIA.png").convert("RGB")
keypoints = Image.open(ROOT / "03_Target_Keypoints_Strong.png").convert("RGB")
silhouette = Image.open(ROOT / "04_Target_Silhouette_Strong.png").convert("RGB")

prompt = """
Reconstruct the same woman from the first image as a visibly and substantially slimmer,
taller, fashion-model-proportioned version while keeping a strong visual relationship to
the source portrait. Image 1 is identity/face/hair/phone/salon-scene authority. Image 2
is the black oversized CALIFORNIA / LONG BEACH / SUNSET T-shirt identity, print, material
and garment-reference authority. Image 3 is target whole-person geometry authority.
Rebuild shoulders, ribcage, waist, upper arms, sleeves, arm-to-torso spacing, body volume,
loose T-shirt drape, occlusions and newly exposed areas as though the person naturally had
this slimmer body when photographed. Do not merely liquify or horizontally squeeze source
pixels. Preserve the same face, hairstyle, hair color, phone, hand identity and readable
shirt graphic. Output one normal photorealistic portrait only; no comparison layout,
infographic, labels, borders or added text.
""".strip()

model_id = os.environ.get("QWEN_EDIT_MODEL", "Qwen/Qwen-Image-Edit-2511")
pipe = QwenImageEditPlusPipeline.from_pretrained(model_id, torch_dtype=torch.bfloat16)
pipe.to("cuda")
pipe.set_progress_bar_config(disable=None)

common = {
    "prompt": prompt,
    "true_cfg_scale": 4.0,
    "negative_prompt": (
        "different person, changed face, changed hairstyle, deformed hands, extra fingers, "
        "melted clothing, distorted shirt lettering, warped phone, stretched background, "
        "liquify artifacts, infographic, collage, labels, plastic AI skin"
    ),
    "num_inference_steps": 40,
    "guidance_scale": 1.0,
    "num_images_per_prompt": 1,
}

with torch.inference_mode():
    result = pipe(
        image=[source, garment, keypoints],
        generator=torch.Generator(device="cuda").manual_seed(47),
        **common,
    ).images[0]
result.save(OUT / "Q1_source_garment_keypoints.png")

with torch.inference_mode():
    result2 = pipe(
        image=[source, garment, silhouette],
        generator=torch.Generator(device="cuda").manual_seed(53),
        **common,
    ).images[0]
result2.save(OUT / "Q2_source_garment_silhouette.png")

print(f"Saved: {OUT}")