# Salon Whole-Person Reconstruction — Executable Pack v0.4

## Purpose
Turn strong body-shape targets into true reconstruction experiments rather than another 2D warp.

## Privacy boundary
Do **not** commit portrait/source/control images to this public repository. Supply them at runtime from a private/local `PACK_DIR`, or through a private presigned package URL used only as a GitHub Actions secret.

## Profiles
- `profiles/upper_body_strong_slim_v01.json`: for cropped/upper-body salon portraits where visible slimming depends mainly on shoulder width, upper-arm volume, torso width and garment drape.

## Preferred run order
1. **Free hosted first:** `run_hf_zero_fast_salon.py` using `prithivMLmods/Qwen-Image-Edit-2511-LoRAs-Fast`. This route needs no local GPU, no DashScope API key and no Picsart credits. A real 3-image one-step synthetic inference has already passed on GitHub Actions Zero GPU.
2. **Free private GitHub Actions path:** `.github/workflows/salon-hf-zero.yml`. It downloads the private control pack through `SALON_PACK_URL`, runs the free Zero GPU route, uploads the generated PNG only to private `SALON_RESULT_UPLOAD_URL`, then deletes private assets from the runner.
3. **Hosted paid/fallback:** `run_qwencloud_salon.py` with `qwen-image-3.0-pro`. No local GPU required, but it requires `DASHSCOPE_API_KEY`.
4. **QwenCloud GitHub Actions fallback:** `.github/workflows/salon-qwencloud.yml`.
5. **Self-hosted low-cost:** `run_qwen_lightx2v_fp8.sh` when LightX2V/Qwen-2511 FP8 assets and a compatible GPU are available.
6. **Self-hosted full comparator:** `run_qwen_salon.py`.
7. **Garment-fit comparator:** `run_fitvton_salon.sh` for `slim + medium-tall` and `slim + tall` garment-fit targets.
8. Do not promote any result until visual readback checks identity, anatomy, shirt text, hands/phone, garment drape, background and naturalness.

## Free Hugging Face Zero example

```bash
export PACK_DIR='/private/path/Salon_UpperBody_ControlPack_v01'
python experiments/salon-reconstruction/run_hf_zero_fast_salon.py
```

Useful overrides:

```bash
HF_ZERO_LORA='Studio-DeLight' \
HF_ZERO_STEPS=4 \
SEED=47 \
PACK_DIR='/private/path/Salon_UpperBody_ControlPack_v01' \
python experiments/salon-reconstruction/run_hf_zero_fast_salon.py
```

Expected default local asset names:
- `01_Source_Original.png`
- `02_Garment_Reference_CALIFORNIA.png`
- `03_Target_Keypoints_UpperBody_Strong.png`

## Free Zero GitHub Actions private execution
`.github/workflows/salon-hf-zero.yml` is intentionally `workflow_dispatch` only and requires two private transport secrets:

- `SALON_PACK_URL`: short-lived private GET URL for a ZIP containing the runtime image assets.
- `SALON_RESULT_UPLOAD_URL`: short-lived private PUT URL that receives the generated PNG.

No model/API credential is required for the current free Zero route. The workflow deliberately does **not** upload the portrait as a GitHub Actions artifact because this repository is public.

## QwenCloud fallback

```bash
DASHSCOPE_API_KEY='...' \
PACK_DIR='/private/path/Salon_UpperBody_ControlPack_v01' \
python experiments/salon-reconstruction/run_qwencloud_salon.py
```

## Validation evidence
- `Salon Runner Validation`: GitHub-hosted `ubuntu-latest` dry-run and compile validation PASS.
- Official `Qwen/Qwen-Image-Edit-2511` Space API contract probe PASS, but actual generation is blocked by its current Zero GPU duration policy (`requested GPU duration 360s > maximum allowed`).
- Faster `prithivMLmods/Qwen-Image-Edit-2511-LoRAs-Fast`: real 3-image, 1-step synthetic Zero GPU inference PASS from GitHub Actions.
- The current portrait itself has **not** yet been sent through this Zero runner because private input/output transport is not configured in GitHub Secrets.

## Current gate
The model/runtime/credit gate has been removed for the preferred route. The remaining gate is only safe private transport: `SALON_PACK_URL` + `SALON_RESULT_UPLOAD_URL`, or executing `run_hf_zero_fast_salon.py` directly in any internet-connected environment that can access the private local pack.
