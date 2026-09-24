#!/usr/bin/env bash
set -euo pipefail

PACK_DIR="${PACK_DIR:-$(cd "$(dirname "$0")" && pwd)}"
LIGHTX2V_DIR="${LIGHTX2V_DIR:-$HOME/LightX2V}"
QWEN2511_MODEL="${QWEN2511_MODEL:-$HOME/models/Qwen-Image-Edit-2511}"
OUTPUT="${OUTPUT:-$PACK_DIR/outputs_lightx2v/Salon_Qwen2511_FP8_Strong.png}"

mkdir -p "$(dirname "$OUTPUT")"
cd "$LIGHTX2V_DIR"

export CUDA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES:-0}"
source "$LIGHTX2V_DIR/scripts/base/base.sh"

PROMPT='Reconstruct the same woman from Image 1 with a clearly slimmer and taller whole-person body. Image 1 is identity, face, hair, phone and salon-scene authority. Image 2 is the black CALIFORNIA / LONG BEACH / SUNSET T-shirt design, texture and garment authority. Image 3 is target body-structure authority: narrower shoulders and ribcage, slimmer upper arms, distinctly slimmer torso and waist, longer coherent proportions. Rebuild body volume, sleeves, arm-to-torso spacing, loose T-shirt drape, occlusions and newly exposed areas as if the person naturally had this body when photographed. Preserve the same person, hairstyle, phone and readable shirt graphic. Do not merely squeeze or liquify pixels. Output one photorealistic portrait only.'

python -m lightx2v.infer \
  --model_cls qwen_image \
  --task i2i \
  --model_path "$QWEN2511_MODEL" \
  --config_json "$LIGHTX2V_DIR/configs/qwen_image/qwen_image_i2i_2511_distill_fp8.json" \
  --prompt "$PROMPT" \
  --negative_prompt "different person, changed face, deformed hands, extra fingers, melted clothes, distorted shirt lettering, warped phone, liquify artifacts, infographic, collage, labels, plastic AI skin" \
  --image_path "$PACK_DIR/01_Source_Original.png,$PACK_DIR/02_Garment_Reference_CALIFORNIA.png,$PACK_DIR/03_Target_Keypoints_Strong.png" \
  --save_result_path "$OUTPUT" \
  --seed 47

echo "Saved: $OUTPUT"