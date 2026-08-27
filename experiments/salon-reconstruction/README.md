# Salon Whole-Person Reconstruction — Executable Pack v0.3

## Purpose
Turn strong body-shape targets into true reconstruction experiments rather than another 2D warp.

## Privacy boundary
Do **not** commit portrait/source/control images to this public repository. Supply them at runtime from a private/local `PACK_DIR`, or through a private presigned package URL used only as a GitHub Actions secret.

## Profiles
- `profiles/upper_body_strong_slim_v01.json`: for cropped/upper-body salon portraits where visible slimming depends mainly on shoulder width, upper-arm volume, torso width and garment drape.

## Preferred run order
1. **Hosted first:** `run_qwencloud_salon.py` with `qwen-image-3.0-pro`. This is a CPU-side client calling hosted inference, so no local GPU is required. It accepts source + garment reference + structure control. Requires `DASHSCOPE_API_KEY`.
2. **GitHub Actions hosted path:** `.github/workflows/salon-qwencloud.yml`. It runs on `ubuntu-latest`, downloads the private control pack through `SALON_PACK_URL`, calls QwenCloud, uploads the result only to a private presigned `SALON_RESULT_UPLOAD_URL`, then deletes private assets from the runner. It does not publish portrait artifacts to the public repository.
3. **Self-hosted low-cost:** `run_qwen_lightx2v_fp8.sh` when LightX2V/Qwen-2511 FP8 assets and a compatible GPU are available.
4. **Self-hosted full comparator:** `run_qwen_salon.py`.
5. **Garment-fit comparator:** `run_fitvton_salon.sh` for `slim + medium-tall` and `slim + tall` garment-fit targets.
6. Do not promote any result until visual readback checks identity, anatomy, shirt text, hands/phone, garment drape, background and naturalness.

## Hosted QwenCloud example

```bash
export DASHSCOPE_API_KEY='...'
export PACK_DIR='/private/path/Salon_UpperBody_ControlPack_v01'
python experiments/salon-reconstruction/run_qwencloud_salon.py
```

Dry-run payload validation without spending inference:

```bash
DRY_RUN=1 \
DASHSCOPE_API_KEY='placeholder' \
PACK_DIR='/private/path/Salon_UpperBody_ControlPack_v01' \
python experiments/salon-reconstruction/run_qwencloud_salon.py
```

Expected default local asset names for the upper-body profile:
- `01_Source_Original.png`
- `02_Garment_Reference_CALIFORNIA.png`
- `03_Target_Keypoints_UpperBody_Strong.png`

`SOURCE_IMAGE`, `GARMENT_IMAGE`, `STRUCTURE_IMAGE`, `PROFILE_JSON`, `OUT_DIR`, `SEED`, `QWEN_IMAGE_MODEL` and `QWEN_IMAGE_BASE_URL` may be overridden with environment variables.

## GitHub Actions private hosted execution
The workflow is intentionally `workflow_dispatch` only. It requires three repository/environment secrets:

- `DASHSCOPE_API_KEY`: QwenCloud / DashScope API key.
- `SALON_PACK_URL`: short-lived private GET URL for a ZIP containing the three runtime image assets.
- `SALON_RESULT_UPLOAD_URL`: short-lived private PUT URL that receives the generated PNG.

The workflow deliberately does **not** upload the portrait result as a GitHub Actions artifact, because this repository is public.

## Evidence boundary
- QwenCloud runner: Python compile + real-asset dry-run payload validation passed locally.
- GitHub hosted workflow: committed as an execution path; it has not run real inference because the required secrets are not available through the current connector.
- These runners do **not** mean QwenCloud, FitVTON, Qwen self-hosted, MHR or InstantHMR inference has already executed.

## Current gate
The hosted path removes the previous GPU requirement. The immediate execution gate is now credential/input transport: `DASHSCOPE_API_KEY` plus private pack/result presigned URLs. Self-hosted routes remain fallbacks rather than the preferred first test.
