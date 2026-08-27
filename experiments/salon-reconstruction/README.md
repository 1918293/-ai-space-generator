# Salon Whole-Person Reconstruction — Executable Pack v0.1

## Purpose
Turn the current strong whole-person target into a true reconstruction experiment rather than another 2D warp.

## Privacy boundary
Do **not** commit portrait/source/control images to this public repository. Supply them at runtime from a private/local pack.

Expected local inputs beside the runners:
- `01_Source_Original.png`
- `02_Garment_Reference_CALIFORNIA.png`
- `03_Target_Keypoints_Strong.png`
- `04_Target_Silhouette_Strong.png`
- `05_Target_Edge_Strong.png`
- `06_Target_Depth_PROXY_Strong.png`

## Run order
1. Run `run_fitvton_salon.sh` to test `slim + medium-tall` and `slim + tall` garment-fit targets.
2. Run `run_qwen_lightx2v_fp8.sh` first when LightX2V/Qwen-2511 FP8 assets are available.
3. Use `run_qwen_salon.py` as the full Qwen-Image-Edit-2511 comparator.
4. Do not promote any result until visual readback checks identity, anatomy, shirt text, hands/phone, garment drape, background and naturalness.

## Evidence boundary
These runners are executable preparation. They do **not** mean FitVTON, Qwen, MHR or InstantHMR inference has already executed.

## Runtime gate
A compatible GPU and model assets are still required. The repository currently has no GPU/self-hosted GitHub Actions runner configured.