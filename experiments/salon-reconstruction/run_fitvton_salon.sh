#!/usr/bin/env bash
set -euo pipefail

FITVTON_DIR="${FITVTON_DIR:-$HOME/FitVTON}"
PACK_DIR="${PACK_DIR:-$(cd "$(dirname "$0")" && pwd)}"
OUT_DIR="${OUT_DIR:-$PACK_DIR/outputs_fitvton}"

cd "$FITVTON_DIR"
mkdir -p "$OUT_DIR"

python inference_demo.py \
  --person_image "$PACK_DIR/01_Source_Original.png" \
  --reference_image "$PACK_DIR/02_Garment_Reference_CALIFORNIA.png" \
  --gender female \
  --shape slim \
  --height medium-tall \
  --length long-length \
  --garment_type upper \
  --style untucked \
  --output_dir "$OUT_DIR/A_strong_medium_tall" \
  --output_prefix salon_A \
  --num_inference_steps 30 \
  --guidance_scale 1.0 \
  --seed 41

python inference_demo.py \
  --person_image "$PACK_DIR/01_Source_Original.png" \
  --reference_image "$PACK_DIR/02_Garment_Reference_CALIFORNIA.png" \
  --gender female \
  --shape slim \
  --height tall \
  --length long-length \
  --garment_type upper \
  --style untucked \
  --output_dir "$OUT_DIR/B_tall_model" \
  --output_prefix salon_B \
  --num_inference_steps 30 \
  --guidance_scale 1.0 \
  --seed 43

echo "FitVTON outputs: $OUT_DIR"